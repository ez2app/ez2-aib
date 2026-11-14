// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host site dns --adopt <code> --plan|--apply|--check` (E6 S12): put a site
//! opened by hand behind the Cloudflare proxy. Its records were made before sites carried
//! marks, so they are "DNS only" and unmarked, and the DNS step and `site remove` leave
//! them alone; anyone who looks the name up gets the host's own address.
//!
//! * **Plan** (read only): the site's names; every address record under them with its
//!   type, content, proxied and comment; the zone's SSL mode and Always Use HTTPS; the ACME
//!   challenges the Edge's Caddy uses for each name, from its merged configuration
//!   (`caddy adapt`), or `unknown`. Only when the SSL mode is `full` or `strict` and no
//!   name is proved by TLS-ALPN alone ([`aib_cloud::sitedns::adopt_gate`]) are the steps
//!   listed; otherwise exit 2 with the reason and no steps.
//! * **Apply**: the same gate, then for each A record to this host's own address
//!   (`CF_ORIGIN_IPV4`) under the site's names one edit: proxied, the comment the site's
//!   mark followed by the old comment ([`aib_cloud::sitedns::adopt`]). Mail and text
//!   records, records pointing anywhere else and records with another site's mark are
//!   never touched. Asked again, a record that is proxied and marked is "無變更". Read
//!   back after: S15's judgement over the new listing.
//! * **Check**: S15 alone ([`crate::cfdns::proxied_names`]), read only.
//!
//! Every Cloudflare call goes through [`crate::cfdns::Cf`]: on the allowed list, one
//! ledger line each, the key on curl's standard input.
//!
//! Exit: 0 done / nothing to do / all proxied; 1 a name is still not proxied or an edit
//! failed; 2 the gate (or the zone) says not to; 3 could not tell.

use std::fmt::Write as _;
use std::fs;

use aib_cloud::site::spec_from_record;
use aib_cloud::sitedns::{self, Adopt, CfCall, Proxied, Record};
use serde_json::Value;

use crate::cfdns::{creds_from, customer_accounts, customer_zones, listing, Cf, KEY_UNSET};
use crate::probe::{CADDYFILE, EDGE_CT};
use crate::sitecreatecmd::{HostPaths, Runner};

/// Which of the three.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Plan,
    Apply,
    Check,
}

/// The Edge's merged configuration: what its Caddy runs, imports and snippets filled in.
fn edge_adapted(runner: &mut impl Runner) -> Option<Value> {
    let argv: Vec<String> = [
        "pct",
        "exec",
        EDGE_CT,
        "--",
        "caddy",
        "adapt",
        "--config",
        CADDYFILE,
        "--adapter",
        "caddyfile",
    ]
    .iter()
    .map(|s| (*s).to_owned())
    .collect();
    let out = runner.run(&argv).ok()?;
    serde_json::from_str(&out).ok()
}

fn record_line(out: &mut String, r: &Record) {
    let _ = writeln!(
        out,
        "record {} {} {} proxied={} comment={}",
        r.kind,
        r.name,
        r.content,
        r.proxied,
        Value::String(r.comment.clone())
    );
}

/// `site dns`.
pub fn cmd(args: &[String]) -> Result<u8, String> {
    let mut code: Option<String> = None;
    let mut mode: Option<Mode> = None;
    let mut paths = HostPaths::host();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--adopt" => {
                let c = it.next().ok_or("--adopt needs a site code")?;
                if !aib_cloud::site::valid_code(c) {
                    return Err(format!("site code {c:?} is not valid"));
                }
                code = Some(c.clone());
            }
            "--plan" | "--apply" | "--check" if mode.is_none() => {
                mode = Some(match a.as_str() {
                    "--plan" => Mode::Plan,
                    "--apply" => Mode::Apply,
                    _ => Mode::Check,
                });
            }
            "--records" => paths.records = it.next().ok_or("--records needs a dir")?.into(),
            other => return Err(format!("unknown argument {other}")),
        }
    }
    let (Some(code), Some(mode)) = (code, mode) else {
        return Err("site dns --adopt <code> --plan|--apply|--check".to_owned());
    };
    let mut out = String::new();
    let rc = match adopt(
        &mut crate::sitecreatecmd::HostRunner,
        &paths,
        &code,
        mode,
        &mut out,
    ) {
        Ok(rc) => rc,
        Err(why) => {
            let _ = writeln!(out, "could not tell: {why}");
            3
        }
    };
    print!("{out}");
    Ok(rc)
}

/// The command for site `code`, its report written to `out`; the exit code.
pub fn adopt(
    runner: &mut impl Runner,
    paths: &HostPaths,
    code: &str,
    mode: Mode,
    out: &mut String,
) -> Result<u8, String> {
    let file = paths.records.join(format!("{code}.json"));
    let rec: Value = fs::read_to_string(&file)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .ok_or_else(|| format!("{}: no readable record", file.display()))?;
    let spec = spec_from_record(&rec).map_err(|e| format!("{}: {e}", file.display()))?;
    if spec.code != code {
        return Err(format!(
            "{}: the record is of {}",
            file.display(),
            spec.code
        ));
    }
    let names: Vec<&str> = spec.hosts.iter().map(String::as_str).collect();
    if mode == Mode::Check {
        let found = crate::cfdns::proxied_names(runner, paths, &spec, &rec)?;
        return Ok(s15_line(out, &found));
    }
    let _ = writeln!(
        out,
        "site {code} dns adopt {}",
        if mode == Mode::Plan { "plan" } else { "apply" }
    );
    for n in &names {
        let _ = writeln!(out, "name {n}");
    }
    let edge = edge_adapted(runner);
    let text = fs::read_to_string(&paths.cloudflare)
        .map_err(|e| format!("{KEY_UNSET}: {}: {}", paths.cloudflare.display(), e.kind()))?;
    let (creds, origin) = creds_from(&text)?;
    let origin = origin
        .filter(|o| sitedns::valid_origin(o))
        .ok_or(sitedns::ORIGIN_UNSET)?;
    let domain = *names.first().ok_or("the site has no name")?;
    let accounts = customer_accounts(paths, &rec)?;
    let mut cf = Cf::new(runner, creds, &paths.cf_ledger, &spec.code);
    let zones = customer_zones(&mut cf, &accounts, domain)?;
    let zone = sitedns::pick_zone(domain, Some(accounts.as_slice()), Some(zones.as_slice()))
        .map_err(|w| format!("{w}: {domain}"))?;
    let _ = writeln!(out, "zone {}", zone.name);
    if names.iter().any(|n| !sitedns::in_zone(n, &zone.name)) {
        let _ = writeln!(out, "stop: {}", sitedns::MULTI_ZONE);
        return Ok(2);
    }

    let (http, answer) = cf.call(
        &CfCall::SslMode {
            zone: zone.id.clone(),
        },
        None,
    )?;
    let ssl = if http == 200 {
        sitedns::ssl_mode(&answer)
    } else {
        Err(sitedns::failure_word(http, false))
    };
    let value = |a: &Value| {
        a.pointer("/result/value")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .to_owned()
    };
    let _ = writeln!(out, "ssl_mode {}", value(&answer));
    let (http, answer) = cf.call(
        &CfCall::AlwaysHttps {
            zone: zone.id.clone(),
        },
        None,
    )?;
    let https = if http == 200 {
        value(&answer)
    } else {
        "unknown".to_owned()
    };
    let _ = writeln!(out, "always_use_https {https}");
    let acme: Vec<Option<Vec<&'static str>>> = names
        .iter()
        .map(|n| edge.as_ref().and_then(|e| sitedns::acme_challenges(e, n)))
        .collect();
    for (n, a) in names.iter().zip(&acme) {
        let word = match a {
            None => "unknown".to_owned(),
            Some(v) if v.is_empty() => "none".to_owned(),
            Some(v) => v.join(" "),
        };
        let _ = writeln!(out, "acme {n} {word}");
    }
    let all = listing(&mut cf, &zone.id)?
        .ok_or_else(|| format!("{}: records listing", sitedns::UNREADABLE))?;
    let rows = sitedns::adopt(&zone.id, &names, &all, &origin, &spec.code);
    for (r, _) in &rows {
        record_line(out, r);
    }
    if let Err(why) = sitedns::adopt_gate(ssl, &acme) {
        let _ = writeln!(out, "stop: {why}");
        return Ok(2);
    }

    let mut edits = Vec::new();
    for (r, action) in &rows {
        let what = match action {
            Adopt::Edit(e) => {
                edits.push(e.clone());
                format!(
                    "edit proxied=true comment={}",
                    Value::String(e.comment().to_owned())
                )
            }
            Adopt::Unchanged => "unchanged (無變更)".to_owned(),
            Adopt::Elsewhere => "not touched: points elsewhere".to_owned(),
            Adopt::OtherSite => "not touched: another site's mark".to_owned(),
            Adopt::Refused => "not touched: not an id".to_owned(),
        };
        let _ = writeln!(out, "step {} {} {what}", r.kind, r.name);
    }
    if edits.is_empty() {
        let _ = writeln!(out, "無變更");
    }
    if mode == Mode::Plan {
        let _ = writeln!(out, "plan ok: {} edit(s)", edits.len());
        return Ok(0);
    }

    let mut failed = 0;
    for e in &edits {
        let (http, answer) = cf.call(&CfCall::Edit(e.clone()), Some(e.body().as_str()))?;
        if http != 200 || answer.get("success") != Some(&Value::Bool(true)) {
            failed += 1;
            let _ = writeln!(out, "edit failed: {} (http {http})", e.record());
        }
    }
    let after = listing(&mut cf, &zone.id)?;
    let left = after.as_deref().map_or(edits.len(), |a| {
        sitedns::adopt(&zone.id, &names, a, &origin, &spec.code)
            .iter()
            .filter(|(_, x)| matches!(x, Adopt::Edit(_)))
            .count()
    });
    if left > 0 {
        let _ = writeln!(
            out,
            "{}: {left} record(s) not proxied and marked",
            sitedns::NOT_CONFIRMED
        );
        failed += 1;
    }
    let rc = s15_line(out, &sitedns::proxied(&names, after.as_deref()));
    Ok(if failed > 0 { rc.max(1) } else { rc })
}

/// S15's word for what was found, and its exit code.
fn s15_line(out: &mut String, found: &Proxied) -> u8 {
    match found {
        Proxied::All => {
            let _ = writeln!(out, "check all names proxied");
            0
        }
        Proxied::Grey(names) => {
            let _ = writeln!(out, "check not proxied: {}", names.join(" "));
            1
        }
        Proxied::Unchecked(why) => {
            let _ = writeln!(out, "check unchecked: {why}");
            3
        }
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::path::{Path, PathBuf};

    const ACC: &str = "0123456789abcdef0123456789abcdef";
    const ZID: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const GREY: &str = "d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1";
    const AWAY: &str = "d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d2";
    const MAIL: &str = "d3d3d3d3d3d3d3d3d3d3d3d3d3d3d3d3";
    const KEY: &str = "0123456789abcdef0123456789abcdef01234";
    const ORIGIN: &str = "192.0.2.10";
    const ELSEWHERE: &str = "198.51.100.7";
    const CODE: &str = "s-adopt1";
    const DOMAIN: &str = "shop.example.com";

    /// A Cloudflare that keeps its records and does the edits it is sent; and an Edge
    /// whose `caddy adapt` answers `edge`.
    struct Fake {
        ssl: &'static str,
        records: Vec<Value>,
        edge: Option<Value>,
        patches: Vec<String>,
    }

    fn config_value<'a>(config: &'a str, key: &str) -> Option<&'a str> {
        config.lines().find_map(|l| {
            l.strip_prefix(key)?
                .strip_prefix(" = \"")?
                .strip_suffix('"')
        })
    }

    impl Runner for Fake {
        fn run(&mut self, argv: &[String]) -> Result<String, String> {
            match (argv.get(4).map(String::as_str), &self.edge) {
                (Some("caddy"), Some(e)) => Ok(e.to_string()),
                _ => Err("no edge".to_owned()),
            }
        }
        fn run_input(&mut self, _argv: &[String], input: &[u8]) -> Result<String, String> {
            let config = String::from_utf8(input.to_vec()).unwrap();
            let url = config_value(&config, "url").unwrap();
            let path = url.split("/client/v4").nth(1).unwrap().to_owned();
            let method = config_value(&config, "request").unwrap();
            let body = if path.starts_with("/zones?") {
                let list = if path.contains(&format!("account.id={ACC}&name=example.com")) {
                    vec![
                        json!({"id": ZID, "name": "example.com", "status": "active", "account": {"id": ACC}}),
                    ]
                } else {
                    vec![]
                };
                json!({"success": true, "result": list})
            } else if path.ends_with("/settings/ssl") {
                json!({"success": true, "result": {"id": "ssl", "value": self.ssl}})
            } else if path.ends_with("/settings/always_use_https") {
                json!({"success": true, "result": {"id": "always_use_https", "value": "on"}})
            } else if method == "PATCH" {
                let id = path.rsplit('/').next().unwrap().to_owned();
                self.patches.push(id.clone());
                let data = config_value(&config, "data").unwrap().replace("\\\"", "\"");
                let edit: Value = serde_json::from_str(&data).unwrap();
                for r in &mut self.records {
                    if r["id"] == id.as_str() {
                        r["proxied"] = edit["proxied"].clone();
                        r["comment"] = edit["comment"].clone();
                    }
                }
                json!({"success": true, "result": {"id": id}})
            } else {
                let n = self.records.len();
                json!({"success": true, "result": self.records,
                    "result_info": {"page": 1, "total_pages": 1, "total_count": n}})
            };
            Ok(format!("{body}\n200"))
        }
    }

    fn fake() -> Fake {
        Fake {
            ssl: "strict",
            records: vec![
                json!({"id": GREY, "type": "A", "name": DOMAIN, "content": ORIGIN, "proxied": false, "comment": "made by hand"}),
                json!({"id": AWAY, "type": "A", "name": format!("www.{DOMAIN}"), "content": ELSEWHERE, "proxied": false, "comment": null}),
                json!({"id": MAIL, "type": "MX", "name": DOMAIN, "content": "mail.example.com", "proxied": false, "comment": null}),
            ],
            edge: Some(json!({"apps": {"http": {"servers": {}}}})),
            patches: Vec::new(),
        }
    }

    fn scratch(name: &str) -> HostPaths {
        let d = std::env::temp_dir().join(format!("aib-sitedns-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        let mut p = HostPaths::host();
        let at = |s: &str| -> PathBuf { d.join(s) };
        p.records = at("records");
        p.rosters = at("rosters");
        p.cloudflare = at("cloudflare.env");
        p.cf_ledger = at("log/cloudflare.jsonl");
        fs::create_dir_all(&p.records).unwrap();
        fs::create_dir_all(&p.rosters).unwrap();
        fs::write(
            &p.cloudflare,
            format!(
                "CF_AUTH_EMAIL=ops@example.test\nCF_GLOBAL_KEY={KEY}\nCF_ORIGIN_IPV4={ORIGIN}\n"
            ),
        )
        .unwrap();
        fs::write(
            p.rosters.join("t-aaa.json"),
            json!({"tenant_id": "t-aaa", "cloudflare_accounts": [ACC], "members": []}).to_string(),
        )
        .unwrap();
        let www = format!("www.{DOMAIN}");
        let spec = aib_cloud::site::SiteSpec::new(
            CODE,
            131,
            41,
            &[DOMAIN, www.as_str()],
            true,
            "zh_TW",
            aib_cloud::site::Limits::START,
        )
        .unwrap();
        let mut rec = aib_cloud::site::record(&spec, "2026-11-01T00:00:00Z");
        rec["tenant_id"] = json!("t-aaa");
        fs::write(p.records.join(format!("{CODE}.json")), rec.to_string()).unwrap();
        p
    }

    fn ledger_methods(p: &Path) -> Vec<String> {
        fs::read_to_string(p)
            .unwrap_or_default()
            .lines()
            .map(|l| {
                serde_json::from_str::<Value>(l).unwrap()["method"]
                    .as_str()
                    .unwrap()
                    .to_owned()
            })
            .collect()
    }

    fn run(cf: &mut Fake, p: &HostPaths, mode: Mode) -> (u8, String) {
        let mut out = String::new();
        let rc = adopt(cf, p, CODE, mode, &mut out).unwrap();
        (rc, out)
    }

    fn record<'a>(cf: &'a Fake, id: &str) -> &'a Value {
        cf.records.iter().find(|r| r["id"] == id).unwrap()
    }

    #[test]
    fn adopt_apply_makes_a_grey_record_proxied_and_marked() {
        let p = scratch("grey");
        let mut cf = fake();
        let (rc, out) = run(&mut cf, &p, Mode::Plan);
        assert_eq!(rc, 0, "{out}");
        assert!(cf.patches.is_empty(), "the plan edits nothing");
        assert!(!ledger_methods(&p.cf_ledger).iter().any(|m| m != "GET"));
        let (rc, out) = run(&mut cf, &p, Mode::Apply);
        // www still points elsewhere and stays grey: S15 says so
        assert_eq!(rc, 1, "{out}");
        assert_eq!(cf.patches, [GREY]);
        let grey = record(&cf, GREY);
        assert_eq!(grey["proxied"], true);
        assert_eq!(grey["comment"], format!("ez2:site:{CODE} made by hand"));
        assert!(
            out.contains(&format!("check not proxied: www.{DOMAIN}")),
            "{out}"
        );
    }

    #[test]
    fn adopt_apply_leaves_a_record_pointing_elsewhere_alone() {
        let p = scratch("away");
        let mut cf = fake();
        let (_, out) = run(&mut cf, &p, Mode::Apply);
        assert!(
            !cf.patches.iter().any(|id| id == AWAY || id == MAIL),
            "{out}"
        );
        let away = record(&cf, AWAY);
        assert_eq!(
            (away["proxied"].clone(), away["comment"].clone()),
            (json!(false), Value::Null)
        );
        assert!(
            out.contains(&format!(
                "step A www.{DOMAIN} not touched: points elsewhere"
            )),
            "{out}"
        );
        assert!(!out.contains("MX"), "mail records are not looked at: {out}");
    }

    #[test]
    fn adopt_plan_stops_when_ssl_is_flexible_or_off() {
        for mode in ["flexible", "off"] {
            let p = scratch(mode);
            let mut cf = fake();
            cf.ssl = mode;
            let (rc, out) = run(&mut cf, &p, Mode::Plan);
            assert_eq!(rc, 2, "{out}");
            assert!(
                out.contains(&format!("stop: {}", sitedns::SSL_MODE_NOT_FULL)),
                "{out}"
            );
            assert!(!out.contains("step "), "no steps: {out}");
            let (rc, _) = run(&mut cf, &p, Mode::Apply);
            assert_eq!(rc, 2);
            assert!(cf.patches.is_empty());
        }
        // an Edge proved by TLS-ALPN alone, and an Edge that cannot be read, stop too
        let p = scratch("alpn");
        let mut cf = fake();
        cf.edge = Some(
            json!({"apps": {"tls": {"automation": {"policies": [{"issuers": [
            {"module": "acme", "challenges": {"http": {"disabled": true}}}]}]}}}}),
        );
        let (rc, out) = run(&mut cf, &p, Mode::Plan);
        assert_eq!(
            (rc, out.contains(sitedns::ACME_ALPN_ONLY)),
            (2, true),
            "{out}"
        );
        cf.edge = None;
        let (rc, out) = run(&mut cf, &p, Mode::Plan);
        assert_eq!(
            (rc, out.contains(sitedns::ACME_UNKNOWN)),
            (2, true),
            "{out}"
        );
        assert!(out.contains(&format!("acme {DOMAIN} unknown")), "{out}");
    }

    #[test]
    fn adopt_rerun_on_a_proxied_marked_record_changes_nothing() {
        let p = scratch("again");
        let mut cf = fake();
        let www = format!("www.{DOMAIN}");
        for r in &mut cf.records {
            if r["id"] == AWAY {
                *r = json!({"id": AWAY, "type": "A", "name": www, "content": ORIGIN,
                    "proxied": true, "comment": format!("ez2:site:{CODE}")});
            }
        }
        let (rc, out) = run(&mut cf, &p, Mode::Apply);
        assert_eq!(rc, 0, "{out}");
        assert_eq!(cf.patches, [GREY]);
        assert!(out.contains(&format!("step A {www} unchanged")), "{out}");
        let (rc, out) = run(&mut cf, &p, Mode::Apply);
        assert_eq!(cf.patches.len(), 1, "nothing edited twice: {out}");
        assert_eq!(rc, 0, "{out}");
        assert!(out.contains("無變更"), "{out}");
        assert!(out.contains(&format!("step A {DOMAIN} unchanged")), "{out}");
        assert_eq!(
            record(&cf, GREY)["comment"],
            format!("ez2:site:{CODE} made by hand")
        );
        assert_eq!(record(&cf, AWAY)["comment"], format!("ez2:site:{CODE}"));
    }

    #[test]
    fn adopt_check_is_s15() {
        let p = scratch("check");
        let mut cf = fake();
        let (rc, out) = run(&mut cf, &p, Mode::Check);
        assert_eq!(rc, 1, "{out}");
        assert!(cf.patches.is_empty());
        for r in &mut cf.records {
            r["proxied"] = json!(true);
        }
        let (rc, out) = run(&mut cf, &p, Mode::Check);
        assert_eq!((rc, out.as_str()), (0, "check all names proxied\n"));
    }
}
