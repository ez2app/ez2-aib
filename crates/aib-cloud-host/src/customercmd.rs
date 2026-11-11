// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host customer --site <code> | --tenant <id> --roles <role,…>` — the one
//! command a customer's key reaches. A key is bound to one site or to a customer (all of
//! that tenant's sites, or with `--sites a,b` the listed ones of them); its roles are
//! Ez2.PAGE's (`aib_cloud::customer::Role`). The lines for a customer's keys are written
//! from that customer's roster (`aib_cloud::roster`).
//!
//! Originally:
//! (4.4.1 §1; request grammar: `aib_cloud::customer`). It is meant to be the forced
//! command of that key's entry, with the site code written there by us:
//!
//! ```text
//! command="/usr/local/sbin/aib-cloud-host customer --site site2 --roles editor,publisher,analyst",restrict ssh-ed25519 AAAA… customer
//! ```
//!
//! What the customer typed arrives in `SSH_ORIGINAL_COMMAND`. It is parsed as data, never
//! run; the site is the one on our side of the entry, never one the customer names.
//!
//! Available now: `status` (the site's own checks, no host checks; only the checks and
//! codes `aib_cloud::customer::status_view` names are shown, the rest is one
//! `provider_check` row) and `logs` (bounded, masked). `php-reload`, `restart` and `backup` parse but answer "not available yet":
//! the first two change something and wait for the operation journal and the host lock.
//!
//! Exit status: as `site verify` for `status`; 0 for `logs`; 2 a refused request;
//! 4 not available yet; 3 could not be done. `logs` for a quarantined site is not run
//! inside the CT: it answers the neutral word `provider_check` and exits 3, the same as
//! when the site's state cannot be read (Andy 11-10: neutral, as `status`).

use std::process::Command;
use std::time::Duration;

use aib_cloud::customer::{
    parse_owner_request, parse_request, parse_roles, OwnerRequest, Request, Role,
};
use aib_cloud::site::spec_from_record;
use aib_cloud::svc;
use aib_cloud::verify;
use serde_json::{json, Value};

use crate::probe::run_capture_bounded;
use crate::quarantinecmd::load_spec;

const RECORDS: &str = "/var/lib/ez2-cloud/sites";
const FIREWALL_DIR: &str = "/etc/pve/firewall";

/// Whether a customer's `logs` request may enter their CT.
#[derive(Debug, PartialEq, Eq)]
enum LogsGate {
    /// The site is in normal service.
    Go,
    /// The site is quarantined: its logs are read by us from the host, not handed out
    /// by running a program inside a CT that may be under someone else's control
    /// (33e28, #631 seg. 5 L2). Andy 11-10: the customer is told in neutral words, the
    /// same as `status` — `provider_check`, nothing about a quarantine.
    Quarantined,
    /// The CT's firewall file could not be read, so which of the two is not known. That
    /// is not a reason to go in.
    Unknown,
}

fn logs_gate(ct_fw: Option<&str>) -> LogsGate {
    match ct_fw {
        None => LogsGate::Unknown,
        Some(fw) if aib_cloud::quarantine::is_quarantined(fw) => LogsGate::Quarantined,
        Some(_) => LogsGate::Go,
    }
}

/// Answer one request for one site. `line` is what the customer sent.
pub fn answer(site: &str, records: &str, line: &str, roles: &[Role]) -> u8 {
    let request = match parse_request(line) {
        Ok(r) => r,
        Err(why) => {
            // a fixed word: what was sent is not echoed
            eprintln!("refused: {}", why.as_str());
            // only what is offered is listed (Andy 11-10: the rest stays hidden)
            eprintln!("requests: status | logs <nginx|php|db> [lines N] [minutes N]");
            return 2;
        }
    };
    serve(site, records, request, roles)
}

/// Whether any of the key's roles may do what the request is (the table is Ez2.PAGE's:
/// `aib_cloud::customer::Role::may`).
fn permitted(roles: &[Role], request: Request) -> bool {
    roles.iter().any(|r| r.may(request.operation()))
}

/// Answer one parsed request for one site, for a key with these roles.
fn serve(site: &str, records: &str, request: Request, roles: &[Role]) -> u8 {
    if !permitted(roles, request) {
        eprintln!("refused: role_forbidden");
        return 2;
    }
    match request {
        Request::Status => {
            let path = std::path::PathBuf::from(format!("{records}/{site}.json"));
            let (name, _, results) = crate::siteverify::site_checks(&path);
            // the customer's view: a whitelist of checks and codes (aib_cloud::customer)
            println!(
                "{}",
                aib_cloud::customer::status_view(Some(&name), &results)
            );
            verify::exit_code(&results)
        }
        Request::Logs {
            source,
            lines,
            minutes,
        } => {
            let Ok(spec) = load_spec(site, records) else {
                eprintln!("could not read the site record");
                return 3;
            };
            // nothing is run inside a quarantined CT for its customer (Andy 11-10)
            let ct_fw = std::fs::read_to_string(format!("{FIREWALL_DIR}/{}.fw", spec.ct)).ok();
            // Either way the customer is told the same neutral word `status` uses, with
            // the same exit status: a quarantine is not announced to whoever holds the key.
            if logs_gate(ct_fw.as_deref()) != LogsGate::Go {
                eprintln!("provider_check");
                return 3;
            }
            let Ok(argv) = svc::logs_argv(&spec, source, lines, minutes) else {
                eprintln!("could not read the site record");
                return 3;
            };
            let mut cmd = Command::new(&argv[0]);
            cmd.args(&argv[1..]).env("LC_ALL", "C");
            // the host counts bytes and lines itself: what prints the log runs in the CT
            let wanted = usize::try_from(lines).unwrap_or(usize::MAX);
            match run_capture_bounded(&mut cmd, Duration::from_secs(30), svc::log_bytes_max(lines))
            {
                Ok((out, more)) => {
                    for l in out.lines().take(wanted) {
                        println!("{}", svc::mask_line(svc::clip_line(l)));
                    }
                    if more || out.lines().count() > wanted {
                        eprintln!("output cut at the limit");
                    }
                    0
                }
                Err(_) => {
                    eprintln!("could not read the log");
                    3
                }
            }
        }
        Request::PhpReload | Request::Restart | Request::Backup => {
            eprintln!("not available yet");
            4
        }
    }
}

/// The sites whose record names `tenant` outright, as (site code, record); with a `scope`
/// (the key's `--sites` list), only those of them the list names. The record decides, the
/// list only narrows: a code in the list whose record names another tenant, or none, is
/// not this key's, so a mistyped entry cannot reach someone else's site. An unreadable
/// directory or record is an error: a list that silently leaves a site out would hide it
/// from its owner.
fn tenant_sites(
    tenant: &str,
    scope: Option<&[&str]>,
    records: &str,
) -> Result<Vec<(String, Value)>, String> {
    let mut out = Vec::new();
    for entry in std::fs::read_dir(records).map_err(|e| e.to_string())? {
        let path = entry.map_err(|e| e.to_string())?.path();
        if path.extension().is_none_or(|x| x != "json") {
            continue;
        }
        let text = std::fs::read_to_string(&path).map_err(|e| e.to_string())?;
        let rec: Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
        // only a tenant the record names outright: a record without one has no tenant
        if aib_cloud::site::record_tenant(&rec) == Some(tenant) {
            let spec = spec_from_record(&rec).map_err(|e| e.to_string())?;
            if scope.is_none_or(|list| list.contains(&spec.code.as_str())) {
                out.push((spec.code, rec));
            }
        }
    }
    out.sort_by(|a, b| a.0.cmp(&b.0));
    Ok(out)
}

/// The key's `--sites a,b`: site codes, at least one, none twice. `None` for anything else —
/// a wrong entry is an error, never "every site".
fn parse_scope(list: &str) -> Option<Vec<&str>> {
    let codes: Vec<&str> = list.split(',').collect();
    let ok = codes.iter().all(|c| aib_cloud::site::valid_code(c))
        && codes
            .iter()
            .enumerate()
            .all(|(i, c)| !codes[..i].contains(c));
    ok.then_some(codes)
}

/// Whether a line starts with a word only a customer key's grammar has.
fn is_owner_word(line: &str) -> bool {
    matches!(
        line.split(' ').find(|w| !w.is_empty()),
        Some("sites" | "usage" | "site")
    )
}

/// Answer one request of a **customer** key: every site of that tenant (or, with a scope,
/// the listed ones of them), and no other. A site that does not exist, a site that is
/// someone else's and a site outside the scope get the same answer.
pub fn answer_tenant(
    tenant: &str,
    scope: Option<&[&str]>,
    records: &str,
    line: &str,
    roles: &[Role],
) -> u8 {
    // a list of one site: that member need not name it (4.5 section 4). The line is still
    // a customer key's line afterwards, so the record is asked whose the site is.
    let named;
    let line = match scope {
        Some([only]) if !is_owner_word(line) && !line.trim().is_empty() => {
            named = format!("site {only} {line}");
            named.as_str()
        }
        _ => line,
    };
    let request = match parse_owner_request(line) {
        Ok(r) => r,
        Err(why) => {
            eprintln!("refused: {}", why.as_str());
            eprintln!("requests: sites | usage | site <code> status | site <code> logs <nginx|php|db> [lines N] [minutes N]");
            // `site create <name>` and `site <code> domain check|add <name>` are not listed
            // until the host can do them (Andy 11-10: what is not open is not shown)
            return 2;
        }
    };
    if let OwnerRequest::Site { site, request } = request {
        // the role is checked first: a key that may not ask learns nothing about the site
        if !permitted(roles, request) {
            eprintln!("refused: role_forbidden");
            return 2;
        }
        let Ok(sites) = tenant_sites(tenant, scope, records) else {
            eprintln!("could not read the site records");
            return 3;
        };
        if !sites.iter().any(|(code, _)| code == site) {
            eprintln!("refused: tenant_mismatch");
            return 2;
        }
        return serve(site, records, request, roles);
    }
    if !roles.iter().any(|r| r.may(request.operation())) {
        eprintln!("refused: role_forbidden");
        return 2;
    }
    // opening a site is for a key that reaches every site of the customer: a member's
    // key for some of the sites, whatever its roles, would open a site it cannot see on
    // the owner's plan (33e28, #652 M1). The same answer as a role that may not.
    let scoped_create = matches!(request, OwnerRequest::Create { .. }) && scope.is_some();
    if scoped_create {
        eprintln!("refused: role_forbidden");
        return 2;
    }
    // opening a site and a site's domains parse and are checked against the roles, and
    // nothing is done yet (PLAN-customer-site-create: the journal and the host lock come
    // with the part that does them). The role came first: a key that may not ask learns
    // nothing, not even whether the site named is the customer's.
    let coming_later = matches!(
        request,
        OwnerRequest::Create { .. } | OwnerRequest::Domain { .. }
    );
    if coming_later {
        eprintln!("not available yet");
        return 4;
    }
    let Ok(sites) = tenant_sites(tenant, scope, records) else {
        eprintln!("could not read the site records");
        return 3;
    };
    match request {
        OwnerRequest::Sites => {
            let list: Vec<Value> = sites
                .iter()
                .map(|(code, rec)| json!({ "site": code, "hosts": rec.get("hosts") }))
                .collect();
            println!("{}", json!({ "tenant_id": tenant, "sites": list }));
            0
        }
        OwnerRequest::Usage => {
            // what the sites are allowed, added up against the plan; what they really use
            // is `site usage` on the host and is not measured for this answer yet
            let mut held = (0u64, 0u64, 0u64);
            for (_, rec) in &sites {
                if let Ok(spec) = spec_from_record(rec) {
                    held.0 += u64::from(spec.limits.cpulimit_x100);
                    held.1 += u64::from(spec.limits.memory_mb);
                }
                held.2 += rec.get("disk_gb").and_then(Value::as_u64).unwrap_or(0);
            }
            let plan = aib_cloud::arc::PLAN;
            println!(
                "{}",
                json!({
                    "tenant_id": tenant, "sites": sites.len(), "measured": false,
                    "plan": { "cores": plan.cores, "memory_mb": plan.memory_mb, "disk_gb": plan.disk_gb },
                    "limits_total": { "cpu_x100": held.0, "memory_mb": held.1, "disk_gb": held.2 },
                })
            );
            0
        }
        OwnerRequest::Site { .. } | OwnerRequest::Create { .. } | OwnerRequest::Domain { .. } => 3,
    }
}

/// Whether the records directory named on the command line is taken: any, when run
/// directly (tests, an operator); through sudo only our own.
fn records_taken(under_sudo: bool, records: &str) -> bool {
    !under_sudo || records == RECORDS
}

/// What the customer sent, out of the environment sshd leaves: `SSH_ORIGINAL_COMMAND`, or
/// nothing when they sent no command (an interactive login; refused later as empty).
///
/// Through `sudo` the environment is reset, and the entry's sudoers rule has to keep
/// `SSH_ORIGINAL_COMMAND` and `SSH_CONNECTION`. If it does not, every request would arrive
/// empty and read as "you typed nothing" (33e28, #642 M1). `SSH_CONNECTION` is there for
/// every ssh session, so under sudo its absence says the variables were not kept: `None`.
fn request_line(
    original: Option<String>,
    under_sudo: bool,
    has_connection: bool,
) -> Option<String> {
    match original {
        Some(line) => Some(line),
        None if under_sudo && !has_connection => None,
        None => Some(String::new()),
    }
}

/// `customer --site <code> --roles <r,…>` or `customer --tenant <code> [--sites a,b] --roles <r,…>`:
/// the forced command of a key's entry. Which of the two, the code and the roles are
/// written there by us; a wrong entry is an error here, never a wider right.
pub fn cmd(args: &[String]) -> Result<u8, String> {
    let (mut site, mut tenant, mut roles) = (None::<&str>, None::<&str>, None::<&str>);
    let mut scope = None::<&str>;
    let mut records = RECORDS.to_owned();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--site" => site = Some(it.next().ok_or("--site needs a site code")?.as_str()),
            "--tenant" => tenant = Some(it.next().ok_or("--tenant needs a tenant id")?.as_str()),
            "--sites" => scope = Some(it.next().ok_or("--sites needs a list")?.as_str()),
            "--roles" => roles = Some(it.next().ok_or("--roles needs a list")?.as_str()),
            "--records" => records = it.next().ok_or("--records needs a dir")?.clone(),
            // anything else on our side of the entry is a mistake in the entry itself
            other => return Err(format!("unknown argument {other}")),
        }
    }
    let roles = roles
        .and_then(parse_roles)
        .ok_or("customer needs --roles <role,…> (set in the key's entry)")?;
    let under_sudo = std::env::var_os("SUDO_UID").is_some();
    // the records directory is ours to name, never the caller's: through sudo this runs as
    // root, and a directory of someone's own making would hand them any CT (33e28, #642 L2)
    if !records_taken(under_sudo, &records) {
        return Err("--records is not taken through sudo".to_owned());
    }
    let Some(line) = request_line(
        std::env::var("SSH_ORIGINAL_COMMAND").ok(),
        under_sudo,
        std::env::var_os("SSH_CONNECTION").is_some(),
    ) else {
        // a fixed word: this is our set-up, not something the customer typed
        eprintln!("entry is not set up");
        return Ok(3);
    };
    let scope = match scope {
        None => None,
        Some(_) if tenant.is_none() => return Err("--sites goes with --tenant".to_owned()),
        Some(list) => Some(parse_scope(list).ok_or("--sites is not a list of site codes")?),
    };
    match (site, tenant) {
        (Some(code), None) if aib_cloud::site::valid_code(code) => {
            Ok(answer(code, &records, &line, &roles))
        }
        (None, Some(code)) if aib_cloud::site::valid_code(code) => Ok(answer_tenant(
            code,
            scope.as_deref(),
            &records,
            &line,
            &roles,
        )),
        (Some(_), Some(_)) => Err("customer takes --site or --tenant, not both".to_owned()),
        (None, None) => Err(
            "customer needs --site <code> or --tenant <id> (set in the key's entry, not by the customer)"
                .to_owned(),
        ),
        _ => Err("the code in the key's entry is not valid".to_owned()),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    const ALL: &[Role] = &[Role::Owner];

    fn record(dir: &std::path::Path, site: &str, ct: u32, tenant: Option<&str>) {
        let mut rec = json!({
            "site": site, "ct": ct, "ip": format!("10.77.0.{}", ct - 100),
            "hosts": format!("{site}.example.net"), "wp": 1, "locale": "zh_TW",
            "created": "2026-11-10T01:00:00Z",
        });
        if let Some(t) = tenant {
            rec["tenant_id"] = json!(t);
        }
        std::fs::write(dir.join(format!("{site}.json")), rec.to_string()).unwrap();
    }

    /// Andy 11-10 / 11-11: a key bound to a customer reaches all of that customer's
    /// sites and nobody else's; roles are Ez2.PAGE's.
    #[test]
    fn a_tenant_key_reaches_its_own_sites_only() {
        let dir = std::env::temp_dir().join(format!("aib-tenant-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        record(&dir, "site2", 112, Some("t-aaa"));
        record(&dir, "site3", 113, Some("t-aaa"));
        record(&dir, "site4", 114, Some("t-bbb"));
        let d = dir.to_str().unwrap();
        let mine = tenant_sites("t-aaa", None, d).unwrap();
        let codes: Vec<&str> = mine.iter().map(|(c, _)| c.as_str()).collect();
        assert_eq!(codes, ["site2", "site3"]);
        // a site whose record names no tenant belongs to no customer key, whatever the key is called
        record(&dir, "site5", 115, None);
        assert!(tenant_sites("site5", None, d).unwrap().is_empty());
        assert_eq!(answer_tenant("site5", None, d, "site site5 status", ALL), 2);
        assert_eq!(answer_tenant("t-aaa", None, d, "sites", ALL), 0);
        assert_eq!(answer_tenant("t-aaa", None, d, "usage", ALL), 0);
        // someone else's site and a site that does not exist: the same refusal
        assert_eq!(answer_tenant("t-aaa", None, d, "site site4 status", ALL), 2);
        assert_eq!(answer_tenant("t-aaa", None, d, "site site9 status", ALL), 2);
        // a site key's words are not a tenant key's, and the other way round
        assert_eq!(answer_tenant("t-aaa", None, d, "status", ALL), 2);
        assert_eq!(answer("site2", d, "sites", ALL), 2);
        // roles: a viewer may list, not read usage; an editor may not restart or read logs
        assert_eq!(answer_tenant("t-aaa", None, d, "sites", &[Role::Viewer]), 0);
        assert_eq!(answer_tenant("t-aaa", None, d, "usage", &[Role::Viewer]), 2);
        assert_eq!(answer("site2", d, "restart", &[Role::Editor]), 2);
        assert_eq!(answer("site2", d, "logs nginx", &[Role::Editor]), 2);
        // (the status is named so that the knife on `serve`'s own `4` finds one line)
        let not_yet = 4;
        assert_eq!(
            answer("site2", d, "restart", &[Role::Editor, Role::Publisher]),
            not_yet
        );
        // the role is asked before the site: no way to probe for sites without the right
        assert_eq!(
            answer_tenant("t-aaa", None, d, "site site4 logs nginx", &[Role::Viewer]),
            2
        );
        // records that cannot be read are "could not", never an empty list
        assert_eq!(
            answer_tenant("t-aaa", None, "/nonexistent/aib-tenant", "sites", ALL),
            3
        );
        // Andy 11-11: opening a site and a site's domains. They parse, the role is asked,
        // and nothing is done yet: the same "not available yet" as a site's restart
        let coming = not_yet;
        let open = "site create www.example.com";
        assert_eq!(answer_tenant("t-aaa", None, d, open, ALL), coming);
        assert_eq!(
            answer_tenant("t-aaa", None, d, open, &[Role::Owner]),
            coming
        );
        // opening a site is the owner's: nobody else's key gets as far as "not yet"
        for role in [Role::Editor, Role::Publisher, Role::Analyst, Role::Viewer] {
            assert_eq!(answer_tenant("t-aaa", None, d, open, &[role]), 2);
        }
        let bind = "site site2 domain add shop.example.com";
        assert_eq!(
            answer_tenant("t-aaa", None, d, bind, &[Role::Publisher]),
            coming
        );
        assert_eq!(answer_tenant("t-aaa", None, d, bind, &[Role::Editor]), 2);
        assert_eq!(
            answer_tenant(
                "t-aaa",
                None,
                d,
                "site site2 domain check shop.example.com",
                ALL
            ),
            coming
        );
        let blank = "site create example.com blank";
        assert_eq!(answer_tenant("t-aaa", None, d, blank, ALL), coming);
        // a key for some of the customer's sites opens none, whatever its roles (33e28)
        assert_eq!(answer_tenant("t-aaa", Some(&["site2"]), d, open, ALL), 2);
        assert_eq!(
            answer_tenant("t-aaa", Some(&["site2", "site3"]), d, open, ALL),
            2
        );
        // a site is not opened without its address, and nothing else is the customer's to choose
        assert_eq!(answer_tenant("t-aaa", None, d, "site create", ALL), 2);
        assert_eq!(answer_tenant("t-aaa", None, d, "site create site9", ALL), 2);
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// c477a: one member's key may be for some of the customer's sites. The list only
    /// narrows what the records already give.
    #[test]
    fn a_site_list_narrows_a_tenant_key_and_never_widens_it() {
        let dir = std::env::temp_dir().join(format!("aib-scope-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        record(&dir, "site2", 112, Some("t-aaa"));
        record(&dir, "site3", 113, Some("t-aaa"));
        record(&dir, "site4", 114, Some("t-bbb"));
        record(&dir, "site5", 115, None);
        let d = dir.to_str().unwrap();
        let codes = |scope: &[&str]| -> Vec<String> {
            let sites = tenant_sites("t-aaa", Some(scope), d).unwrap();
            sites.into_iter().map(|(c, _)| c).collect()
        };
        assert_eq!(codes(&["site3"]), ["site3"]);
        // someone else's site, a site with no tenant and a site that does not exist, written
        // into the list by mistake: the record decides, the key does not reach them
        assert_eq!(codes(&["site3", "site4", "site5", "site9"]), ["site3"]);
        assert!(codes(&["site4"]).is_empty());
        // outside the list is refused like someone else's, without entering anything
        let scope: &[&str] = &["site3"];
        assert_eq!(answer_tenant("t-aaa", Some(scope), d, "sites", ALL), 0);
        assert_eq!(
            answer_tenant("t-aaa", Some(scope), d, "site site2 status", ALL),
            2
        );
        assert_eq!(
            answer_tenant("t-aaa", Some(scope), d, "site site4 status", ALL),
            2
        );
        // a list of one site: its member asks without naming it, and still reaches no other.
        // (`status` itself enters the site, so the words used here are ones refused before.)
        assert_eq!(answer_tenant("t-aaa", Some(scope), d, "restart", ALL), 4);
        assert_eq!(answer_tenant("t-aaa", Some(scope), d, "stop", ALL), 2);
        assert_eq!(answer_tenant("t-aaa", Some(scope), d, "", ALL), 2);
        // the one site is someone else's: the same refusal as any site not yours
        let theirs: &[&str] = &["site4"];
        assert_eq!(answer_tenant("t-aaa", Some(theirs), d, "restart", ALL), 2);
        // with the whole tenant, or a longer list, the site has to be named
        assert_eq!(answer_tenant("t-aaa", None, d, "restart", ALL), 2);
        let two: &[&str] = &["site2", "site3"];
        assert_eq!(answer_tenant("t-aaa", Some(two), d, "restart", ALL), 2);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_site_list_of_an_entry_is_codes_or_an_error() {
        assert_eq!(parse_scope("site2"), Some(vec!["site2"]));
        assert_eq!(parse_scope("site2,site3"), Some(vec!["site2", "site3"]));
        // empty, an empty item, the same site twice, not a code: an error, never "every site"
        for bad in [
            "",
            "site2,",
            ",site2",
            "site2,site2",
            "site2, site3",
            "*",
            "../site2",
        ] {
            assert_eq!(parse_scope(bad), None, "{bad:?}");
        }
        let args = |a: &[&str]| a.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>();
        // a list without a customer, and a list that is not one, stop the entry
        assert!(cmd(&args(&[
            "--site", "site2", "--sites", "site2", "--roles", "owner"
        ]))
        .is_err());
        assert!(cmd(&args(&[
            "--tenant", "t-aaa", "--sites", "", "--roles", "owner"
        ]))
        .is_err());
    }

    #[test]
    fn refused_and_not_yet() {
        let dir = "/nonexistent/aib-customer";
        // nothing sent (an interactive login), shell syntax, another site, an unknown word
        for line in [
            "",
            "bash",
            "status; reboot",
            "status site3",
            "logs nginx | nc x 1",
            "stop",
        ] {
            assert_eq!(answer("site2", dir, line, ALL), 2, "{line:?}");
        }
        for line in ["php-reload", "restart", "backup"] {
            assert_eq!(answer("site2", dir, line, ALL), 4, "{line:?}");
        }
        // a request that is fine for a site whose record cannot be read: not done, not green
        assert_eq!(answer("site2", dir, "logs nginx", ALL), 3);
        assert_eq!(answer("site2", dir, "status", ALL), 1);
    }

    /// 33e28, #631 seg. 5 M1: what prints the log runs inside the customer's container.
    #[test]
    fn output_that_does_not_end_is_cut_on_the_host() {
        let sh = |script: &str| {
            let mut cmd = Command::new("sh");
            cmd.args(["-c", script]);
            run_capture_bounded(&mut cmd, Duration::from_secs(20), 10_000)
        };
        // three megabytes where ten thousand bytes were asked for
        let (out, more) = sh("head -c 3000000 /dev/zero | tr '\\0' a").unwrap();
        assert_eq!(out.len(), 10_000);
        assert!(more);
        // a program that never stops is stopped, long before the time is up
        let t0 = std::time::Instant::now();
        let (out, more) = sh("while :; do echo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; done").unwrap();
        assert_eq!(out.len(), 10_000);
        assert!(more);
        assert!(t0.elapsed() < Duration::from_secs(10));
        // output under the cap arrives whole; a program that fails is an error
        assert_eq!(
            sh("printf 'a\\nb\\n'").unwrap(),
            ("a\nb\n".to_owned(), false)
        );
        assert!(sh("echo x; exit 3").is_err());
    }

    /// 33e28, #631 seg. 5 L2; Andy 11-10.
    #[test]
    fn a_quarantined_site_is_not_entered_for_its_logs() {
        let quarantined = aib_cloud::quarantine::firewall();
        assert_eq!(logs_gate(Some(&quarantined)), LogsGate::Quarantined);
        let normal = "[OPTIONS]\nenable: 1\npolicy_in: DROP\n\n[RULES]\nGROUP ez2-site-v2\n";
        assert_eq!(logs_gate(Some(normal)), LogsGate::Go);
        // a state that cannot be read is not a reason to go in
        assert_eq!(logs_gate(None), LogsGate::Unknown);
    }

    #[test]
    fn the_site_comes_from_our_side_only() {
        let args = |a: &[&str]| a.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>();
        assert!(cmd(&args(&[])).is_err());
        assert!(cmd(&args(&["--site", "../etc", "--roles", "owner"])).is_err());
        assert!(cmd(&args(&["--site", "site2", "--roles", "owner", "status"])).is_err());
        assert!(cmd(&args(&["status"])).is_err());
        // an entry without roles, with a role that is not one, or with both scopes gives nothing
        assert!(cmd(&args(&["--site", "site2"])).is_err());
        assert!(cmd(&args(&["--site", "site2", "--roles", "root"])).is_err());
        assert!(cmd(&args(&[
            "--tenant", "t-1", "--site", "site2", "--roles", "owner"
        ]))
        .is_err());
        assert!(cmd(&args(&["--tenant", "../x", "--roles", "owner"])).is_err());
    }
    /// 33e28, #642 seg. 4 M1: the entry runs through sudo, which resets the environment.
    #[test]
    fn an_entry_whose_environment_was_not_kept_is_not_an_empty_request() {
        let sent = |s: &str| Some(s.to_owned());
        // what was sent is what is answered, sudo or not
        assert_eq!(request_line(sent("status"), true, true), sent("status"));
        assert_eq!(request_line(sent("status"), false, true), sent("status"));
        // nothing sent (an interactive login): an empty request, to be refused as such
        assert_eq!(request_line(None, true, true), sent(""));
        assert_eq!(request_line(None, false, false), sent(""));
        // through sudo with not even the connection variable: the rule does not keep them
        assert_eq!(request_line(None, true, false), None);
    }

    /// 33e28, #642 seg. 4 L2: through sudo the records are where we say, not where the caller says.
    #[test]
    fn through_sudo_the_records_directory_is_ours_only() {
        assert!(records_taken(false, "/tmp/any"));
        assert!(records_taken(true, RECORDS));
        assert!(!records_taken(true, "/tmp/any"));
        assert!(!records_taken(true, "/var/lib/ez2-cloud/sites/"));
    }
}
