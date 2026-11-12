// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host edge domain preflight <name> --ip <our ipv4>` and
//! `edge domain add | rm <site> <name> [--ip <our ipv4>] --plan` (4.4.1 §6.2 / §12).
//! Looks the name up with `dig` (A, AAAA, CAA), reads the edge configuration and the
//! site's records, asks `aib_cloud::edge` and prints. **Nothing is changed**: `add` / `rm`
//! refuse without `--plan`, as applying needs the operation journal and the host lock.
//!
//! A lookup that fails (no `dig`, a timeout, a resolver error) is "could not look", never
//! "no such record": the domain is then not ready.

use std::fs;
use std::process::Command;
use std::time::Duration;

use aib_cloud::edge::{self, Action, Dns, NotReady};
use aib_cloud::quarantine;
use aib_cloud::site::SiteSpec;
use serde_json::{json, Value};

use crate::probe::run_capture;
use crate::quarantinecmd::{load_record, load_spec};

const RECORDS: &str = "/var/lib/ez2-cloud/sites";
const QUARANTINE_RECORDS: &str = "/var/lib/ez2-cloud/quarantine";
/// The certificate authority the edge uses, as CAA records name it.
const CA: &str = "letsencrypt.org";

/// The answer lines of `dig +short`: `None` when the output carries a resolver error
/// (lines starting with `;`), otherwise the non-empty lines.
pub fn dig_lines(out: &str) -> Option<Vec<&str>> {
    let lines: Vec<&str> = out
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .collect();
    if lines.iter().any(|l| l.starts_with(';')) {
        return None;
    }
    Some(lines)
}

/// Addresses out of `dig +short A|AAAA` lines: a CNAME chain prints its names (ending in
/// a dot) before the addresses; those are dropped.
pub fn addresses(lines: &[&str]) -> Vec<String> {
    lines
        .iter()
        .filter(|l| !l.ends_with('.'))
        .map(|l| (*l).to_owned())
        .collect()
}

/// CAA records out of `dig +short CAA` lines (`0 issue "letsencrypt.org"`) as
/// `<tag> <value>`.
pub fn caa_records(lines: &[&str]) -> Vec<String> {
    lines
        .iter()
        .filter_map(|l| {
            let mut parts = l.splitn(3, ' ');
            let _flags = parts.next()?;
            Some(format!("{} {}", parts.next()?, parts.next()?))
        })
        .collect()
}

/// Where CAA is looked for: the name, then each parent down to the registrable-looking
/// two labels. The closest set that exists is the one that counts.
pub fn caa_candidates(name: &str) -> Vec<String> {
    let labels: Vec<&str> = name.split('.').collect();
    (0..labels.len().saturating_sub(1))
        .map(|i| labels[i..].join("."))
        .collect()
}

fn dig(kind: &str, name: &str) -> Option<String> {
    let mut cmd = Command::new("dig");
    cmd.args(["+short", "+time=3", "+tries=2", kind, name])
        .env("LC_ALL", "C");
    run_capture(&mut cmd, Duration::from_secs(10)).ok()
}

/// Look a name up. Every part that could not be looked up is `None`.
fn lookup(name: &str) -> Dns {
    // only a name that is a domain name is handed to the resolver
    if !edge::valid_domain(name) {
        return Dns {
            a: None,
            aaaa: None,
            caa: None,
        };
    }
    let answer =
        |kind: &str, n: &str| dig(kind, n).and_then(|o| dig_lines(&o).map(|l| addresses(&l)));
    let mut caa: Option<Vec<String>> = Some(Vec::new());
    for candidate in caa_candidates(name) {
        match dig("CAA", &candidate).as_deref().and_then(dig_lines) {
            None => {
                caa = None;
                break;
            }
            Some(lines) if !lines.is_empty() => {
                caa = Some(caa_records(&lines));
                break;
            }
            Some(_) => {}
        }
    }
    Dns {
        a: answer("A", name),
        aaaa: answer("AAAA", name),
        caa,
    }
}

fn not_ready_code(n: NotReady) -> &'static str {
    match n {
        NotReady::Name => "not_a_domain_name",
        NotReady::LookupFailed => "lookup_failed",
        NotReady::NoA => "no_a_record",
        NotReady::AElsewhere => "a_record_elsewhere",
        NotReady::HasAaaa => "has_aaaa_record",
        NotReady::CaaForbids => "caa_forbids_our_ca",
    }
}

/// One action of an add / rm plan as JSON; the edge configuration is shown as this
/// site's block only.
pub fn action_json(spec: &SiteSpec, action: &Action) -> Value {
    match action {
        Action::WriteCaddyfile { text } => json!({
            "do": "write_edge_block",
            "block": quarantine::caddy_block_text(text, &spec.code).ok(),
        }),
        Action::WriteRecordHosts(hosts) => json!({ "do": "write_record_hosts", "hosts": hosts }),
        Action::VerifyServes(name) => json!({ "do": "verify_serves", "name": name }),
        Action::VerifyGone(name) => json!({ "do": "verify_gone", "name": name }),
        Action::CertificateStays(name) => json!({ "do": "certificate_stays", "name": name }),
    }
}

fn valid_ipv4(ip: &str) -> bool {
    ip.parse::<std::net::Ipv4Addr>().is_ok()
}

pub fn cmd(args: &[String]) -> Result<u8, String> {
    let usage = "edge domain preflight <name> --ip <our ipv4> | edge domain add|rm <site> <name> [--ip <our ipv4>] --plan";
    if args.first().map(String::as_str) != Some("domain") {
        return Err(usage.to_owned());
    }
    let sub = args.get(1).map(String::as_str).ok_or(usage)?;
    let mut positional: Vec<&str> = Vec::new();
    let mut ip: Option<&str> = None;
    let mut plan_only = false;
    let mut records = RECORDS.to_owned();
    let mut qdir = QUARANTINE_RECORDS.to_owned();
    let mut it = args[2..].iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--ip" => {
                ip = Some(
                    it.next()
                        .ok_or("--ip needs the edge's public IPv4")?
                        .as_str(),
                )
            }
            "--plan" => plan_only = true,
            "--records" => records = it.next().ok_or("--records needs a dir")?.clone(),
            "--quarantine-records" => {
                qdir = it.next().ok_or("--quarantine-records needs a dir")?.clone();
            }
            p if !p.starts_with('-') => positional.push(p),
            other => return Err(format!("unknown argument {other}")),
        }
    }
    if ip.is_some_and(|i| !valid_ipv4(i)) {
        return Err("--ip is not an IPv4 address".to_owned());
    }
    match (sub, positional.as_slice()) {
        ("preflight", [name]) => {
            let ip = ip.ok_or("preflight needs --ip <the edge's public IPv4>")?;
            let found = edge::preflight(name, &lookup(name), ip, CA);
            let codes: Vec<&str> = found.iter().map(|n| not_ready_code(*n)).collect();
            println!(
                "{}",
                json!({ "domain": name, "ready": found.is_empty(), "not_ready": codes })
            );
            Ok(u8::from(!found.is_empty()))
        }
        ("add" | "rm", [code, name]) => {
            if !plan_only {
                return Err(
                    "only --plan is available: applying needs the operation journal and the host lock, which are not wired yet"
                        .to_owned(),
                );
            }
            let spec = load_spec(code, &records)?;
            let caddyfile = crate::probe::edge_caddyfile()?;
            let actions = if sub == "add" {
                let ip = ip.ok_or("add needs --ip <the edge's public IPv4> for the preflight")?;
                let ready = edge::preflight(name, &lookup(name), ip, CA);
                if !ready.is_empty() {
                    let codes: Vec<&str> = ready.iter().map(|n| not_ready_code(*n)).collect();
                    return Err(format!("domain {name} is not ready: {}", codes.join(", ")));
                }
                let ct_fw = match fs::read_to_string(format!("/etc/pve/firewall/{}.fw", spec.ct)) {
                    Ok(t) => t,
                    Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(),
                    Err(e) => return Err(format!("firewall file of CT {}: {e}", spec.ct)),
                };
                let record = load_record(code, &qdir)?;
                edge::add_plan(&spec, name, &ready, &caddyfile, &ct_fw, record.as_ref())
            } else {
                edge::rm_plan(&spec, name, &caddyfile)
            }
            .map_err(|e| e.to_string())?;
            let doc = json!({
                "plan": format!("edge domain {sub}"),
                "site": spec.code,
                "domain": name,
                "level": "L2",
                "steps": actions.iter().map(|a| action_json(&spec, a)).collect::<Vec<_>>(),
            });
            println!(
                "{}",
                serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?
            );
            Ok(0)
        }
        _ => Err(usage.to_owned()),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    #[test]
    fn dig_output() {
        assert_eq!(dig_lines("203.0.113.10\n"), Some(vec!["203.0.113.10"]));
        assert_eq!(dig_lines(""), Some(Vec::new()));
        // a resolver error is not an empty answer
        assert_eq!(
            dig_lines(";; communications error to 192.0.2.53#53: timed out\n"),
            None
        );
        assert_eq!(
            dig_lines("203.0.113.10\n;; Truncated, retrying in TCP mode.\n"),
            None
        );
        // a CNAME chain: names first, then the addresses
        let lines = dig_lines("shops.example.net.\nedge.example.net.\n203.0.113.10\n").unwrap();
        assert_eq!(addresses(&lines), ["203.0.113.10"]);
        // a CNAME that leads nowhere is "no A record"
        assert!(addresses(&dig_lines("gone.example.net.\n").unwrap()).is_empty());
    }

    #[test]
    fn caa_output_and_where_it_is_looked_for() {
        let lines = dig_lines(
            "0 issue \"letsencrypt.org\"\n0 iodef \"mailto:x@example.test\"\n128 issuewild \";\"\n",
        )
        .unwrap();
        assert_eq!(
            caa_records(&lines),
            [
                "issue \"letsencrypt.org\"",
                "iodef \"mailto:x@example.test\"",
                "issuewild \";\""
            ]
        );
        assert_eq!(
            caa_candidates("shop.a.example.test"),
            ["shop.a.example.test", "a.example.test", "example.test"]
        );
        assert_eq!(caa_candidates("example.test"), ["example.test"]);
        // what the pure check makes of it
        let dns = Dns {
            a: Some(vec!["203.0.113.10".to_owned()]),
            aaaa: Some(Vec::new()),
            caa: Some(caa_records(&lines)),
        };
        assert!(edge::preflight("shop.example.test", &dns, "203.0.113.10", CA).is_empty());
    }

    #[test]
    fn usage_and_gates() {
        let args = |a: &[&str]| a.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>();
        assert!(cmd(&args(&[])).is_err());
        assert!(cmd(&args(&["domain"])).is_err());
        assert!(cmd(&args(&["domain", "preflight", "shop.example.test"]))
            .unwrap_err()
            .contains("--ip"));
        assert!(cmd(&args(&[
            "domain",
            "preflight",
            "x.test",
            "--ip",
            "not-an-ip"
        ]))
        .is_err());
        // add / rm change nothing: without --plan they refuse before reading anything
        for sub in ["add", "rm"] {
            let err = cmd(&args(&[
                "domain",
                sub,
                "site2",
                "shop.example.test",
                "--ip",
                "203.0.113.10",
            ]))
            .unwrap_err();
            assert!(err.contains("only --plan"), "{err}");
        }
        assert!(cmd(&args(&["domain", "add", "site2", "--plan"])).is_err());
        assert!(cmd(&args(&["domain", "purge", "site2", "x.test", "--plan"])).is_err());
        // a name that is not a domain name is not ready, whatever DNS says (no lookup made
        // can turn it into one)
        assert_eq!(
            cmd(&args(&[
                "domain",
                "preflight",
                "*.example.test",
                "--ip",
                "203.0.113.10"
            ])),
            Ok(1)
        );
    }
}
