// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host edge domain preflight <name> --ip <our ipv4>` and
//! `edge domain add | rm <site> <name> [--ip <our ipv4>] --plan` (4.4.1 §6.2 / §12).
//! Looks the name up with `dig` (A, AAAA, CAA), reads the edge configuration and the
//! site's records, asks `aib_cloud::edge` and prints. **Nothing is changed**: `add` / `rm`
//! refuse without `--plan`, as applying needs the operation journal and the host lock.
//!
//! A lookup that fails (no `dig`, a timeout, a resolver error) is "could not look", never
//! "no such record": the domain is then not ready.
//!
//! `edge cf-only --plan | --check [--builtin] [--accept-diff] [--fw <file>]` (E6 C3, sheet H8): 80 / 443 of
//! the edge only from Cloudflare. Fetches Cloudflare's IPv4 list (or takes the one in the
//! code with `--builtin`), checks it with `aib_cloud::cfonly`, and prints the edge CT's
//! firewall file as it would be with the set and the two rules put in, plus what the file
//! on disk says now, with the edge's network cards (`pct config`) and the cluster switch.
//! A fetched list that differs from the built-in one gives no steps (rc 2) without
//! `--accept-diff`. `--check` reads back only: rc 0 when the file holds exactly that and
//! the firewall applies to every card, 1 when not. **Nothing is written**: the file is
//! changed by a sign sheet.

use std::fs;
use std::process::Command;
use std::time::Duration;

use aib_cloud::cfonly;
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

/// Gets a page; the host's is `curl`, tests hand in their own (no network in tests).
pub trait Fetch {
    fn get(&mut self, url: &str) -> Result<String, String>;
}

/// The real host: `curl` over https only, failing on an HTTP error.
pub struct Curl;

impl Fetch for Curl {
    fn get(&mut self, url: &str) -> Result<String, String> {
        let mut cmd = Command::new("curl");
        cmd.args(["-fsS", "--proto", "=https", "--max-time", "20", url])
            .env("LC_ALL", "C");
        run_capture(&mut cmd, Duration::from_secs(30))
    }
}

/// What `edge cf-only` reads on the host; `None` where it could not be read.
pub struct EdgeFacts {
    /// The edge CT's firewall file.
    pub fw: Option<String>,
    /// `pct config <edge CT>`: its network cards.
    pub pct_config: Option<String>,
    /// The cluster firewall file.
    pub cluster_fw: Option<String>,
}

/// The switches of `edge cf-only`.
#[derive(Debug, Clone, Copy, Default)]
pub struct CfOpts {
    /// Take the list in the code, fetch nothing.
    pub builtin: bool,
    /// Read back only.
    pub check: bool,
    /// A fetched list that differs from the built-in one has been looked at.
    pub accept_diff: bool,
}

/// What the person who signs H8 must know; printed with every plan.
fn plan_notes() -> Vec<String> {
    vec![
        format!(
            "after the sign sheet: run `pve-firewall compile` and confirm PVEFW-{}-{} is in its output, then `edge cf-only --check` must give rc 0",
            aib_cloud::site::EDGE_CT,
            cfonly::IPSET
        ),
        "IPv6 is blocked whole: the drop has no source and the set is IPv4 only (the edge has no public IPv6 path; a proxied AAAA would need ips-v6 first)".to_owned(),
        "the drop also stops hosts and CTs on the internal bridge calling the edge on 80 / 443; our own calls enter the edge with pct exec to 127.0.0.1".to_owned(),
        "nic_firewall_off / fw_* / cluster_* in found_now are not fixed by this file: fix them first, or the rules do not apply".to_owned(),
    ]
}

/// `edge cf-only`: the plan (or the read-back) from what was read on the host. A fetched
/// list that fails the check is refused, never replaced by the built-in one behind the
/// person's back; one that differs from the built-in one gives no steps (rc 2) until
/// `--accept-diff` says it was looked at (c477a L1). The read-back counts the cards and
/// the cluster switch too (c477a M1): a clean file behind a card without the firewall is
/// not "only Cloudflare".
pub fn cf_only(
    fetch: &mut dyn Fetch,
    opts: CfOpts,
    facts: &EdgeFacts,
) -> Result<(Value, u8), String> {
    let known = cfonly::builtin_ranges().map_err(|e| e.to_string())?;
    let ranges = if opts.builtin {
        known.clone()
    } else {
        let text = fetch.get(cfonly::SOURCE_URL)?;
        cfonly::parse_ranges(&text).map_err(|e| format!("{}: {e}", cfonly::SOURCE_URL))?
    };
    let (added, dropped) = cfonly::difference(&known, &ranges);
    let source = if opts.builtin {
        "builtin"
    } else {
        cfonly::SOURCE_URL
    };
    let fw = facts.fw.as_deref();
    let mut now =
        cfonly::firewall_applies(facts.pct_config.as_deref(), fw, facts.cluster_fw.as_deref());
    now.extend(cfonly::readback(fw.unwrap_or_default(), &ranges));
    if opts.check {
        let rc = u8::from(!now.is_empty());
        let doc = json!({
            "check": "edge cf-only", "ok": now.is_empty(), "found": now, "source": source,
            "added_to_builtin": added, "dropped_from_builtin": dropped,
        });
        return Ok((doc, rc));
    }
    // c477a N2: the plan edits the edge's own file; it does not make one
    let fw = fw.ok_or(
        "the edge CT's firewall file could not be read: this plan only edits an existing file",
    )?;
    let text = cfonly::apply(fw, &ranges);
    let after = cfonly::readback(&text, &ranges);
    if !after.is_empty() {
        return Err(format!(
            "the planned file would not read back: {}",
            after.join(", ")
        ));
    }
    let needs_ack = !added.is_empty() || !dropped.is_empty();
    let mut doc = json!({
        "plan": "edge cf-only",
        "level": "L2",
        "source": source,
        "ranges": ranges.len(),
        "added_to_builtin": added,
        "dropped_from_builtin": dropped,
        "needs_ack": needs_ack,
        "found_now": now,
        "notes": plan_notes(),
    });
    if needs_ack && !opts.accept_diff {
        doc["refused"] = json!("the fetched list differs from the built-in one: look at added_to_builtin / dropped_from_builtin, then run again with --accept-diff (and update BUILTIN_V4 in a PR)");
        return Ok((doc, 2));
    }
    doc["steps"] = json!([{ "do": "write_edge_firewall", "text": text }, { "do": "read_back" }]);
    Ok((doc, 0))
}

fn cmd_cf_only(args: &[String]) -> Result<u8, String> {
    let usage = "edge cf-only --plan | --check [--builtin] [--accept-diff] [--fw <file>]";
    let mut fw_path = format!("/etc/pve/firewall/{}.fw", aib_cloud::site::EDGE_CT);
    let (mut plan, mut opts) = (false, CfOpts::default());
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--plan" => plan = true,
            "--check" => opts.check = true,
            "--builtin" => opts.builtin = true,
            "--accept-diff" => opts.accept_diff = true,
            "--fw" => fw_path = it.next().ok_or("--fw needs a file")?.clone(),
            _ => return Err(usage.to_owned()),
        }
    }
    if plan == opts.check {
        return Err(usage.to_owned());
    }
    let edge_ct = aib_cloud::site::EDGE_CT.to_string();
    let mut pct = Command::new("pct");
    pct.args(["config", edge_ct.as_str()]).env("LC_ALL", "C");
    let facts = EdgeFacts {
        fw: fs::read_to_string(&fw_path).ok(),
        pct_config: run_capture(&mut pct, Duration::from_secs(10)).ok(),
        cluster_fw: fs::read_to_string("/etc/pve/firewall/cluster.fw").ok(),
    };
    let (doc, rc) = cf_only(&mut Curl, opts, &facts)?;
    println!(
        "{}",
        serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?
    );
    Ok(rc)
}

pub fn cmd(args: &[String]) -> Result<u8, String> {
    let usage = "edge domain preflight <name> --ip <our ipv4> | edge domain add|rm <site> <name> [--ip <our ipv4>] --plan | edge cf-only --plan | --check [--builtin] [--accept-diff] [--fw <file>]";
    if args.first().map(String::as_str) == Some("cf-only") {
        return cmd_cf_only(&args[1..]);
    }
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

    /// A fetch that answers from a string and counts its calls.
    struct Fake(Result<String, String>, usize);

    impl Fetch for Fake {
        fn get(&mut self, url: &str) -> Result<String, String> {
            assert_eq!(url, cfonly::SOURCE_URL);
            self.1 += 1;
            self.0.clone()
        }
    }

    /// A list in the documentation ranges: tests never carry a real address.
    const SAMPLE: &str = "192.0.2.0/24\n198.51.100.0/24\n";
    const EDGE_FW: &str = "[OPTIONS]\nenable: 1\n\n[RULES]\nGROUP ez2-edge-v1\n";

    const EDGE_CFG: &str =
        "hostname: edge\nnet0: name=eth0,bridge=vmbr1,firewall=1\nunprivileged: 1\n";
    const CLUSTER: &str = "[OPTIONS]\nenable: 1\npolicy_in: DROP\n";

    fn facts(fw: &str) -> EdgeFacts {
        EdgeFacts {
            fw: Some(fw.to_owned()),
            pct_config: Some(EDGE_CFG.to_owned()),
            cluster_fw: Some(CLUSTER.to_owned()),
        }
    }

    const PLAN: CfOpts = CfOpts {
        builtin: false,
        check: false,
        accept_diff: true,
    };
    const CHECK: CfOpts = CfOpts {
        builtin: false,
        check: true,
        accept_diff: false,
    };

    #[test]
    fn a_fetched_list_is_checked_and_its_change_shown() {
        let mut f = Fake(Ok(SAMPLE.into()), 0);
        let (doc, rc) = cf_only(&mut f, PLAN, &facts(EDGE_FW)).unwrap();
        assert_eq!((rc, f.1), (0, 1));
        assert_eq!(
            doc["added_to_builtin"],
            json!(["192.0.2.0/24", "198.51.100.0/24"])
        );
        assert_eq!(doc["dropped_from_builtin"].as_array().unwrap().len(), 15);
        assert_eq!(doc["found_now"], json!(["set_missing", "rules_not_first"]));
        assert!(doc["notes"][0]
            .as_str()
            .unwrap()
            .contains("PVEFW-101-ez2-cloudflare-v4"));
        let text = doc["steps"][0]["text"].as_str().unwrap();
        // the planned file read back with --check is clean
        let mut again = Fake(Ok(SAMPLE.into()), 0);
        assert_eq!(cf_only(&mut again, CHECK, &facts(text)).unwrap().1, 0);
        assert_eq!(cf_only(&mut again, CHECK, &facts(EDGE_FW)).unwrap().1, 1);
        // a clean file behind a card without the firewall is not ok (c477a M1)
        let mut open = facts(text);
        open.pct_config = Some(format!("{EDGE_CFG}net1: name=eth1,bridge=vmbr0\n"));
        let (doc, rc) = cf_only(&mut again, CHECK, &open).unwrap();
        assert_eq!((rc, doc["found"].clone()), (1, json!(["nic_firewall_off"])));
        // the built-in list never fetches
        let mut none = Fake(Err("offline".into()), 0);
        let builtin = CfOpts {
            builtin: true,
            ..CfOpts::default()
        };
        assert_eq!(cf_only(&mut none, builtin, &facts(EDGE_FW)).unwrap().1, 0);
        assert_eq!(none.1, 0);
        // no firewall file of the edge: no plan, a reason
        let mut gone = facts(EDGE_FW);
        gone.fw = None;
        assert!(cf_only(&mut none, builtin, &gone).is_err());
    }

    #[test]
    fn a_changed_list_needs_accept_diff() {
        let mut f = Fake(Ok(SAMPLE.into()), 0);
        let (doc, rc) = cf_only(&mut f, CfOpts::default(), &facts(EDGE_FW)).unwrap();
        assert_eq!(rc, 2);
        assert_eq!(doc["needs_ack"], json!(true));
        assert!(doc.get("steps").is_none() && doc["refused"].is_string());
        // the built-in list has nothing to accept
        let builtin = CfOpts {
            builtin: true,
            ..CfOpts::default()
        };
        let (doc, rc) = cf_only(&mut f, builtin, &facts(EDGE_FW)).unwrap();
        assert_eq!((rc, doc["needs_ack"].clone()), (0, json!(false)));
        assert!(doc["steps"].is_array());
    }

    #[test]
    fn a_bad_or_missing_fetch_is_refused_not_replaced() {
        for answer in [
            Ok(String::new()),
            Ok("0.0.0.0/0\n".into()),
            Ok("<html>".into()),
            Err("timeout".into()),
        ] {
            let mut f = Fake(answer, 0);
            assert!(cf_only(&mut f, PLAN, &facts(EDGE_FW)).is_err());
        }
    }

    #[test]
    fn dig_output() {
        assert_eq!(dig_lines("203.0.113.10\n"), Some(vec!["203.0.113.10"]));
        assert_eq!(dig_lines(""), Some(Vec::new()));
        // a resolver error is not an empty answer
        assert_eq!(
            dig_lines(";; communications error to 1.1.1.1#53: timed out\n"),
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
