// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host site create <domain> --abandon <code> --plan | --apply <plan.json>` —
//! call off an opening that stopped half way. `site remove` reads only the site records and
//! cannot see an opening; this takes down what the opening made, in the order of
//! [`aib_cloud::siteabandon`], under the host lock and as one operation of the journal.
//!
//! * `--plan` reads only and prints a sealed plan: the opening record, the container under
//!   its number (and its hostname), the firewall file, whether the opening got as far as
//!   its DNS, the site's passwords on the host, the unfinished operations on the domain.
//! * `--apply` makes the plan again under the lock; it must be the same, word for word, or
//!   nothing is done. Then each step runs, is looked at again, and is written into the
//!   opening record (`abandon`), so a stop half way is carried on by a new plan.
//! * The record ends under `sites-creating/abandoned/` ([`abandoned_dir`]): its numbers and
//!   its code stay counted as used ([`crate::sitecreatecmd::see`] reads them with the
//!   removed sites').
//! * The DNS records are only those carrying the site's mark, in the customer's zone
//!   ([`crate::cfdns::remove_marked`]).
//! * A step that fails stops the operation as failed: like every failed operation it holds
//!   the domain until a person has looked (`op recovered <op>`); a new plan then carries
//!   on from what the record says is done. An opening whose own `site create` failed is
//!   held the same way, so its failure is looked at before it is abandoned.
//! * A customer's own opening of the code (`sites-asked/<code>.json`) still going stops the
//!   plan (`customer_run_going`); the last step marks that ask stopped, `why: abandoned`,
//!   and keeps it, so its code stays given (c477a abandon L1).
//! * Whatever cannot be read — the record, `pct config`, a directory, the journal — stops
//!   the plan; nothing is guessed.
//!
//! Exit status: 0 printed / done; 2 refused; 3 could not be read.

use std::fs;
use std::path::Path;

use aib_cloud::siteabandon::{self, AbandonStep, Guard, Next, STEPS};
use aib_cloud::sitecreate::Probe;
use serde_json::{json, Value};

use crate::opstore;
use crate::sitecreatecmd::{target, write_record, HostPaths, Runner};
use crate::sitecreaterun::Places;

/// The command name in the operation journal.
pub const COMMAND: &str = "site.abandon";

/// Neither an opening nor an abandoned one under this code.
pub const NO_OPENING: &str = "no_opening";
/// The journal could not be read: whether an operation on the domain is still running is
/// not known.
pub const JOURNAL_UNREADABLE: &str = "journal_unreadable";
/// The customer's own opening of this code is still going (its run waited for or alive):
/// it is not taken down under it (c477a abandon L1).
pub const CUSTOMER_RUN_GOING: &str = "customer_run_going";
/// An operation on the domain did not finish: a person looks at it first (`op recovered`).
pub const UNFINISHED_OPERATION: &str = "unfinished_operation";

/// Where abandoned openings' records go: `abandoned/` under the openings' own directory,
/// which the openings' listing does not read.
pub fn abandoned_dir(creating: &Path) -> std::path::PathBuf {
    creating.join("abandoned")
}

/// What the host showed, read only.
pub struct Seen {
    /// The opening record: `Ok(None)` when there is none.
    pub opening: Result<Option<Value>, String>,
    /// Whether the code was abandoned already; `None` could not tell.
    pub abandoned: Option<bool>,
    /// Whether the code has a full or an opened site record; `None` could not tell.
    pub site_record: Option<bool>,
    /// `pct config` of the record's container: `Some(None)` there is none.
    pub ct_config: Option<Option<String>>,
    /// The container numbers the site records name.
    pub live_cts: Option<Vec<u32>>,
    /// Whether the container's firewall file is there.
    pub fw: Option<bool>,
    /// Whether any of the site's passwords are left on the host.
    pub secrets_left: Option<bool>,
    /// The unfinished operations on the domain.
    pub unfinished: Option<Vec<Value>>,
    /// The customer's ask under the code: `Some(None)` there is none, `Some(Some(state))`
    /// its state now, `None` could not be read.
    pub customer_ask: Option<Option<&'static str>>,
}

fn exists(path: &Path) -> Option<bool> {
    match fs::metadata(path) {
        Ok(_) => Some(true),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Some(false),
        Err(_) => None,
    }
}

fn read_opening(creating: &Path, code: &str) -> Result<Option<Value>, String> {
    let path = creating.join(format!("{code}.json"));
    match fs::read_to_string(&path) {
        Ok(text) => serde_json::from_str(&text)
            .map(Some)
            .map_err(|e| format!("{}: {e}", path.display())),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(format!("{}: {e}", path.display())),
    }
}

fn record_ct(rec: &Value) -> Option<u32> {
    rec.get("ct")
        .and_then(Value::as_u64)
        .and_then(|n| u32::try_from(n).ok())
}

/// The site's names in its opening record, first one first.
fn hosts(rec: &Value) -> Vec<String> {
    rec.get("hosts")
        .and_then(Value::as_str)
        .map(|h| {
            h.split(',')
                .map(|n| n.trim().to_owned())
                .filter(|n| !n.is_empty())
                .collect()
        })
        .unwrap_or_default()
}

/// `pct config <ct>`: `Some(None)` only when the configuration does not exist.
fn ct_config(runner: &mut impl Runner, ct: u32) -> Option<Option<String>> {
    match runner.run(&["pct".to_owned(), "config".to_owned(), ct.to_string()]) {
        Ok(text) => Some(Some(text)),
        Err(e) if e.contains("does not exist") => Some(None),
        Err(_) => None,
    }
}

/// The `hostname:` of a container's configuration (main section); empty when it has none.
pub fn hostname(config: &str) -> &str {
    config
        .lines()
        .take_while(|l| !l.starts_with('['))
        .find_map(|l| l.strip_prefix("hostname:"))
        .map_or("", str::trim)
}

fn secrets_left(dir: &Path, code: &str) -> Option<bool> {
    let list = match fs::read_dir(dir) {
        Ok(l) => l,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Some(false),
        Err(_) => return None,
    };
    let mine = format!("{code}.admin.");
    let tmp = format!(".{code}.admin.");
    for entry in list {
        let name = entry.ok()?.file_name().to_string_lossy().into_owned();
        if name.starts_with(&mine) || name.starts_with(&tmp) {
            return Some(true);
        }
    }
    Some(false)
}

/// Look at the host for abandoning `code`.
pub fn see(
    runner: &mut impl Runner,
    paths: &HostPaths,
    asks: &Places,
    ops_dir: &Path,
    domain: &str,
    code: &str,
) -> Seen {
    let opening = read_opening(&paths.creating, code);
    let ct = opening
        .as_ref()
        .ok()
        .and_then(Option::as_ref)
        .and_then(record_ct);
    let site_record = match (
        exists(&paths.records.join(format!("{code}.json"))),
        exists(&paths.opened.join(format!("{code}.json"))),
    ) {
        (Some(a), Some(b)) => Some(a || b),
        _ => None,
    };
    Seen {
        abandoned: exists(&abandoned_dir(&paths.creating).join(format!("{code}.json"))),
        site_record,
        ct_config: ct.and_then(|ct| ct_config(runner, ct)),
        live_cts: crate::sitecreatecmd::read_records(&paths.records)
            .map(|list| list.iter().filter_map(record_ct).collect()),
        fw: ct.and_then(|ct| exists(&paths.firewall.join(format!("{ct}.fw")))),
        secrets_left: secrets_left(&paths.secrets, code),
        unfinished: crate::sitecreatecmd::unfinished_on(ops_dir, domain),
        customer_ask: crate::sitecreaterun::ask_now(asks, code, crate::probe::unix_now()).ok(),
        opening,
    }
}

fn probe(found: Option<bool>) -> Probe {
    match found {
        Some(true) => Probe::NotDone,
        Some(false) => Probe::Done,
        None => Probe::Unknown,
    }
}

/// Whether the opening got as far as its DNS and it has not been taken away yet.
fn dns_left(rec: &Value) -> bool {
    let dns = &rec["dns"];
    let touched = dns.get("adding").is_some() || dns.get("done").is_some();
    touched && rec["abandon"]["dns_removed"] != true
}

/// One probe per step, in [`STEPS`] order.
pub fn probes(seen: &Seen) -> [Probe; STEPS.len()] {
    let opening = seen.opening.as_ref().ok().and_then(Option::as_ref);
    STEPS.map(|step| match (step, opening) {
        (_, None) => Probe::Unknown,
        (AbandonStep::Container, Some(_)) => match &seen.ct_config {
            Some(None) => Probe::Done,
            Some(Some(_)) => Probe::NotDone,
            None => Probe::Unknown,
        },
        (AbandonStep::Firewall, Some(_)) => probe(seen.fw),
        (AbandonStep::Dns, Some(rec)) => probe(Some(dns_left(rec))),
        (AbandonStep::Secrets, Some(_)) => probe(seen.secrets_left),
        (AbandonStep::Record, Some(_)) => Probe::NotDone,
    })
}

fn probe_word(p: Probe) -> &'static str {
    match p {
        Probe::Done => "done",
        Probe::NotDone => "not_done",
        Probe::Unknown => "unknown",
    }
}

/// The plan document. Separate from [`cmd`] so it can be tested without a host.
pub fn plan_doc(domain: &str, code: &str, seen: &Seen) -> Value {
    let opening = seen.opening.as_ref().ok().and_then(Option::as_ref);
    let ct = opening.and_then(record_ct);
    let matches = opening.is_some_and(|r| {
        r.get("site").and_then(Value::as_str) == Some(code)
            && hosts(r).first().map(String::as_str) == Some(domain)
    });
    let ct_hostname: Option<Option<&str>> = match &seen.ct_config {
        Some(Some(c)) => Some(Some(hostname(c))),
        Some(None) => Some(None),
        None => None,
    };
    let guard = Guard {
        site_record: seen.site_record,
        opening_matches: matches,
        ct_hostname,
        ct_of_a_site: match (&seen.live_cts, ct) {
            (Some(list), Some(ct)) => Some(list.contains(&ct)),
            (Some(_), None) => Some(false),
            (None, _) => None,
        },
    };
    let probes = probes(seen);
    let own_stop: Option<&'static str> = match (&seen.opening, seen.abandoned) {
        (Err(_), _) | (_, None) => Some(siteabandon::PROBE_UNKNOWN),
        (Ok(None), Some(true)) => None,
        (Ok(None), Some(false)) => Some(NO_OPENING),
        _ if seen.unfinished.is_none() => Some(JOURNAL_UNREADABLE),
        _ if seen.unfinished.as_ref().is_some_and(|u| !u.is_empty()) => Some(UNFINISHED_OPERATION),
        _ if seen.customer_ask.is_none() => Some(siteabandon::PROBE_UNKNOWN),
        _ if seen.customer_ask == Some(Some("opening")) => Some(CUSTOMER_RUN_GOING),
        _ => None,
    };
    let next = match (&seen.opening, seen.abandoned, own_stop) {
        (_, _, Some(w)) => Next::Stop(w),
        (Ok(None), Some(true), None) => Next::Finished,
        _ => siteabandon::next(code, &guard, &probes),
    };
    let (word, step, stop) = match next {
        Next::Run(s) => ("run", Some(s.as_str()), None),
        Next::Finished => ("finished", None, None),
        Next::Stop(w) => ("stop", None, Some(w)),
    };
    json!({
        "plan": "site-abandon",
        "domain": domain,
        "site": code,
        "level": "L2",
        "ct": ct,
        "ct_hostname": ct_hostname.map(|h| h.map(str::to_owned)),
        "hosts": opening.map(hosts),
        "tenant_id": opening.and_then(|r| r.get("tenant_id")),
        "dns": opening.map(|r| r["dns"].clone()),
        "target": target(domain),
        "unfinished": seen.unfinished,
        "customer_ask": seen.customer_ask,
        "steps": STEPS.iter().zip(probes).map(|(s, p)| json!({
            "step": s.as_str(),
            "now": probe_word(p),
        })).collect::<Vec<_>>(),
        "next": word,
        "next_step": step,
        "stop": stop,
        "numbers": "kept: the number, the address and the code are never given out again",
    })
}

/// What the plan given to `--apply` says.
struct Given {
    op: String,
    sha: String,
    plan: Value,
}

fn given(plan: &Value, domain: &str, code: &str) -> Result<Given, String> {
    if plan.get("plan").and_then(Value::as_str) != Some("site-abandon") {
        return Err("not a site-abandon plan".to_owned());
    }
    if plan.get("domain").and_then(Value::as_str) != Some(domain)
        || plan.get("site").and_then(Value::as_str) != Some(code)
    {
        return Err(format!("the plan is not for {code} of {domain}"));
    }
    let text = |k: &str| {
        plan.get(k)
            .and_then(Value::as_str)
            .map(str::to_owned)
            .ok_or_else(|| format!("the plan has no {k}"))
    };
    Ok(Given {
        op: text("op")?,
        sha: text("plan_sha256")?,
        plan: plan.clone(),
    })
}

/// What the prepare hands the steps.
pub struct Input {
    record: Value,
    steps: Vec<AbandonStep>,
}

/// The plan made again under the lock, checked against the plan given word for word.
pub fn recheck(
    domain: &str,
    code: &str,
    plan: &Value,
    seen: &Seen,
    fingerprint: &str,
) -> Result<(Value, Input), String> {
    let g = given(plan, domain, code)?;
    let mut doc = plan_doc(domain, code, seen);
    crate::quarantinecmd::seal_with_op(&mut doc, &g.op, fingerprint, env!("CARGO_PKG_VERSION"))?;
    if doc.get("plan_sha256").and_then(Value::as_str) != Some(g.sha.as_str()) || doc != g.plan {
        return Err(
            "plan_changed: this host is not as the plan saw it (or the plan was edited); make a new plan"
                .to_owned(),
        );
    }
    if doc["next"] != "run" {
        return Err(format!(
            "nothing to apply: the plan says {} {}",
            doc["next"], doc["stop"]
        ));
    }
    let record = seen
        .opening
        .as_ref()
        .ok()
        .and_then(Option::as_ref)
        .cloned()
        .ok_or("the opening record is gone")?;
    let steps = STEPS
        .iter()
        .zip(probes(seen))
        .filter(|(_, p)| *p == Probe::NotDone)
        .map(|(s, _)| *s)
        .collect();
    Ok((opstore::unsealed(&doc), Input { record, steps }))
}

fn container_step(runner: &mut impl Runner, ct: u32, code: &str) -> Result<(), String> {
    let n = ct.to_string();
    let pct = |w: &str| vec!["pct".to_owned(), w.to_owned(), n.clone()];
    // the name is looked at again right before: the plan's look may be minutes old
    match ct_config(runner, ct) {
        Some(Some(c)) if hostname(&c) == code => {}
        Some(None) => return Ok(()),
        Some(Some(_)) => return Err(format!("{}: CT {ct}", siteabandon::CT_NOT_THIS_OPENING)),
        None => return Err(format!("{}: CT {ct}", siteabandon::PROBE_UNKNOWN)),
    }
    // a running or locked container cannot be destroyed; their own failures do not matter,
    // the destroy and the look after it decide
    let _ = runner.run(&pct("stop"));
    let _ = runner.run(&pct("unlock"));
    let mut destroy = pct("destroy");
    destroy.extend(["--purge".to_owned(), "1".to_owned()]);
    let destroyed = runner.run(&destroy);
    match (destroyed, ct_config(runner, ct)) {
        (_, Some(None)) => Ok(()),
        (Err(e), _) => Err(format!("pct destroy {ct}: {e}")),
        (Ok(_), _) => Err(format!("CT {ct} still answers after pct destroy")),
    }
}

fn firewall_step(paths: &HostPaths, ct: u32) -> Result<(), String> {
    let fw = paths.firewall.join(format!("{ct}.fw"));
    match fs::remove_file(&fw) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(format!("{}: {e}", fw.display())),
    }
    match exists(&fw) {
        Some(false) => Ok(()),
        _ => Err(format!("{} is still there", fw.display())),
    }
}

fn record_step(paths: &HostPaths, asks: &Places, code: &str, record: &Value) -> Result<(), String> {
    // the customer reads why their opening stopped; the ask stays, so the code stays given
    crate::sitecreaterun::stop_ask_abandoned(asks, code, crate::probe::unix_now())?;
    let to = abandoned_dir(&paths.creating);
    write_record(&to, code, record)?;
    let from = paths.creating.join(format!("{code}.json"));
    match fs::remove_file(&from) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(format!("{}: {e}", from.display())),
    }
}

/// The steps, in order, each written into the opening record when done.
pub fn run_steps(
    runner: &mut impl Runner,
    paths: &HostPaths,
    asks: &Places,
    domain: &str,
    code: &str,
    op: &str,
    input: Input,
) -> Result<Value, String> {
    let Input { mut record, steps } = input;
    let ct = record_ct(&record).ok_or("the opening record has no container number")?;
    let names = hosts(&record);
    let names: Vec<&str> = names.iter().map(String::as_str).collect();
    // a carried-on abandon keeps what the one before it did
    if record["abandon"]["op"] != op {
        let before = record["abandon"].take();
        let done = match &before["done"] {
            Value::Array(list) => Value::Array(list.clone()),
            _ => json!([]),
        };
        record["abandon"] = json!({
            "op": op,
            "done": done,
            "dns_removed": before["dns_removed"] == true,
        });
    }
    for step in steps {
        let result = match step {
            AbandonStep::Container => container_step(runner, ct, code),
            AbandonStep::Firewall => firewall_step(paths, ct),
            AbandonStep::Dns => {
                crate::cfdns::remove_marked(runner, paths, code, domain, &names, &record)
            }
            AbandonStep::Secrets => crate::siteidentity::drop_admin_secrets(&paths.secrets, code),
            AbandonStep::Record => {
                record["abandon"]["at"] = json!(crate::probe::unix_now());
                if let Some(list) = record["abandon"]["done"].as_array_mut() {
                    list.push(json!(step.as_str()));
                }
                record_step(paths, asks, code, &record)
            }
        };
        if let Err(why) = result {
            return Err(format!(
                "abandon_stopped: {} failed: {why}; what is done is in the opening record of {code}, a new plan carries on",
                step.as_str()
            ));
        }
        if step == AbandonStep::Record {
            return Ok(record);
        }
        if step == AbandonStep::Dns {
            record["abandon"]["dns_removed"] = json!(true);
        }
        if let Some(list) = record["abandon"]["done"].as_array_mut() {
            list.push(json!(step.as_str()));
        }
        write_record(&paths.creating, code, &record).map_err(|e| {
            format!(
                "abandon_stopped: {} was done but the opening record could not say so: {e}",
                step.as_str()
            )
        })?;
    }
    Ok(record)
}

/// `--apply`: under the lock and as operation `op` of the plan.
pub fn apply(
    place: &opstore::OpPlace,
    plan: &Value,
    domain: &str,
    code: &str,
    paths: &HostPaths,
    asks: &Places,
    runner: &mut impl Runner,
) -> Result<Value, String> {
    let g = given(plan, domain, code)?;
    // the look under the lock and the steps after it both run programs
    let runner = std::cell::RefCell::new(runner);
    let done = opstore::run_op_prepared_as(
        place,
        &g.op,
        COMMAND,
        &target(domain),
        opstore::BY_LOCAL_ROOT,
        || {
            let seen = see(
                &mut **runner.borrow_mut(),
                paths,
                asks,
                &place.ops_dir,
                domain,
                code,
            );
            recheck(domain, code, plan, &seen, &place.fingerprint).map(Some)
        },
        |op, input: Input| {
            run_steps(
                &mut **runner.borrow_mut(),
                paths,
                asks,
                domain,
                code,
                op,
                input,
            )
        },
    )?;
    done.ok_or_else(|| "nothing to do".to_owned())
}

/// `site create <domain> --abandon <code> --plan | --apply <file>` on this host.
pub fn cmd(domain: &str, code: &str, apply_file: Option<&str>) -> Result<u8, String> {
    if !aib_cloud::site::valid_code(code) {
        return Err(format!("{code:?} is not a site code"));
    }
    let paths = HostPaths::host();
    let asks = Places::host()?;
    let mut runner = crate::sitecreatecmd::HostRunner;
    let doc = match apply_file {
        None => {
            let seen = see(
                &mut runner,
                &paths,
                &asks,
                Path::new(opstore::OPS_DIR),
                domain,
                code,
            );
            let mut doc = plan_doc(domain, code, &seen);
            let op = opstore::op_id(crate::probe::unix_now(), opstore::random4()?);
            crate::sitecreatecmd::seal_plan(&mut doc, &op, Path::new(opstore::MACHINE_ID))?;
            doc
        }
        Some(file) => {
            let text = fs::read_to_string(file).map_err(|e| format!("{file}: {e}"))?;
            let plan: Value = serde_json::from_str(&text).map_err(|e| format!("{file}: {e}"))?;
            let place = opstore::OpPlace::host()?;
            let record = apply(&place, &plan, domain, code, &paths, &asks, &mut runner)?;
            json!({ "abandoned": code, "record": record })
        }
    };
    println!(
        "{}",
        serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?
    );
    Ok(0)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    const CODE: &str = "s-aban01";
    const DOMAIN: &str = "half.example.com";
    const OP: &str = "20261111T120000Z-ab12cd34";
    const GONE: &str = "Configuration file 'nodes/x/lxc/131.conf' does not exist";

    fn scratch(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("aib-abandon-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    fn place(root: &Path) -> opstore::OpPlace {
        fs::write(
            root.join("boot_id"),
            "0f0e0d0c-0b0a-4908-8706-050403020100\n",
        )
        .unwrap();
        let me = root.join("proc").join(std::process::id().to_string());
        fs::create_dir_all(&me).unwrap();
        let filler = (3..22).map(|i| i.to_string()).collect::<Vec<_>>().join(" ");
        fs::write(
            me.join("stat"),
            format!(
                "{} (aib-cloud-host) {filler} 4242 0 0\n",
                std::process::id()
            ),
        )
        .unwrap();
        fs::create_dir_all(root.join("ops")).unwrap();
        opstore::OpPlace {
            ops_dir: root.join("ops"),
            lock: opstore::LockPaths {
                lock: root.join("host.lock"),
                proc_root: root.join("proc"),
                boot_id: root.join("boot_id"),
            },
            fingerprint: "f".repeat(64),
        }
    }

    /// A half opening: its record (cloned, firewall file written, a password kept), the
    /// container under its code.
    fn host(name: &str, dns: Value) -> (PathBuf, HostPaths) {
        let root = scratch(name);
        let paths = HostPaths {
            creating: root.join("creating"),
            lxc_conf: root.join("lxc"),
            firewall: root.join("firewall"),
            secrets: root.join("secrets"),
            site_admins: root.join("site-admins"),
            setup: root.join("ez2-site-setup"),
            records: root.join("records"),
            sites_list: root.join("sites.list"),
            keys: root.join("keys.env"),
            opened: root.join("opened"),
            cloudflare: root.join("cloudflare.env"),
            rosters: root.join("rosters"),
            cf_ledger: root.join("cloudflare.jsonl"),
        };
        for d in [
            &paths.firewall,
            &paths.secrets,
            &paths.records,
            &paths.opened,
        ] {
            fs::create_dir_all(d).unwrap();
        }
        let mut rec = json!({
            "site": CODE, "ct": 131, "ip": "10.77.0.41", "hosts": format!("{DOMAIN},www.{DOMAIN}"),
            "tenant_id": "t-aaa", "step": "identity",
        });
        if !dns.is_null() {
            rec["dns"] = dns;
        }
        write_record(&paths.creating, CODE, &rec).unwrap();
        fs::write(paths.firewall.join("131.fw"), "[OPTIONS]\nenable: 1\n").unwrap();
        fs::write(paths.secrets.join(format!("{CODE}.admin.pw")), "x").unwrap();
        fs::write(paths.secrets.join("s-other1.admin.pw"), "x").unwrap();
        (root, paths)
    }

    /// `pct` as the host answers it: the container under `hostname`, gone after a destroy
    /// that works.
    struct Pct {
        calls: Vec<String>,
        there: bool,
        hostname: String,
        destroy_works: bool,
        config_fails: bool,
    }

    impl Pct {
        fn new() -> Self {
            Pct {
                calls: Vec::new(),
                there: true,
                hostname: CODE.to_owned(),
                destroy_works: true,
                config_fails: false,
            }
        }
        fn destroys(&self) -> usize {
            self.calls
                .iter()
                .filter(|c| c.starts_with("pct destroy"))
                .count()
        }
    }

    impl Runner for Pct {
        fn run(&mut self, argv: &[String]) -> Result<String, String> {
            self.calls.push(argv.join(" "));
            match (argv[0].as_str(), argv[1].as_str()) {
                ("pct", "config") if self.config_fails => Err("ipcc_send_rec failed".to_owned()),
                ("pct", "config") if self.there => Ok(format!(
                    "arch: amd64\nhostname: {}\nmemory: 2048\n\n[snap]\nhostname: old\n",
                    self.hostname
                )),
                ("pct", "config") => Err(GONE.to_owned()),
                ("pct", "destroy") => {
                    if self.destroy_works {
                        self.there = false;
                    }
                    Ok(String::new())
                }
                ("pct", _) => Ok(String::new()),
                _ => Err("not pct".to_owned()),
            }
        }
    }

    /// The customer's asks beside the scratch host.
    fn asks(root: &Path, paths: &HostPaths) -> Places {
        Places {
            asked: root.join("asked"),
            creating: paths.creating.clone(),
            opened: paths.opened.clone(),
            records: paths.records.clone(),
            removed: root.join("removed"),
            platform: root.join("platform-domains"),
            lock: opstore::LockPaths {
                lock: root.join("host.lock"),
                proc_root: root.join("proc"),
                boot_id: root.join("boot_id"),
            },
        }
    }

    fn look(pct: &mut Pct, paths: &HostPaths, root: &Path) -> Seen {
        fs::create_dir_all(root.join("ops")).unwrap();
        see(
            pct,
            paths,
            &asks(root, paths),
            &root.join("ops"),
            DOMAIN,
            CODE,
        )
    }

    fn sealed(pct: &mut Pct, paths: &HostPaths, root: &Path) -> Value {
        let mut doc = plan_doc(DOMAIN, CODE, &look(pct, paths, root));
        crate::quarantinecmd::seal_with_op(
            &mut doc,
            OP,
            &"f".repeat(64),
            env!("CARGO_PKG_VERSION"),
        )
        .unwrap();
        doc
    }

    fn opening(paths: &HostPaths) -> Option<Value> {
        read_opening(&paths.creating, CODE).unwrap()
    }

    #[test]
    fn the_hostname_is_the_main_sections() {
        assert_eq!(
            hostname("arch: amd64\nhostname: s-x\n[snap]\nhostname: y\n"),
            "s-x"
        );
        assert_eq!(hostname("[snap]\nhostname: y\n"), "");
        assert_eq!(hostname("arch: amd64\n"), "");
    }

    #[test]
    fn a_half_opening_plans_the_container_first_and_every_step() {
        let (root, paths) = host("plan", json!({"adding": {"name": DOMAIN}}));
        let doc = plan_doc(DOMAIN, CODE, &look(&mut Pct::new(), &paths, &root));
        assert_eq!(doc["plan"], "site-abandon");
        assert_eq!(doc["next"], "run");
        assert_eq!(doc["next_step"], "container");
        assert_eq!(doc["ct"], 131);
        assert_eq!(doc["ct_hostname"], CODE);
        assert_eq!(doc["target"], format!("domain:{DOMAIN}"));
        let now: Vec<&str> = doc["steps"]
            .as_array()
            .unwrap()
            .iter()
            .map(|s| s["now"].as_str().unwrap())
            .collect();
        assert_eq!(now, ["not_done"; 5]);
        // the plan only reads
        assert!(opening(&paths).is_some());
        assert!(paths.firewall.join("131.fw").exists());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn an_opening_that_never_reached_dns_has_no_dns_to_remove() {
        let (root, paths) = host("nodns", Value::Null);
        let doc = plan_doc(DOMAIN, CODE, &look(&mut Pct::new(), &paths, &root));
        assert_eq!(doc["steps"][2]["step"], "dns");
        assert_eq!(doc["steps"][2]["now"], "done");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn applied_everything_goes_and_the_record_moves_to_the_abandoned() {
        let (root, paths) = host("apply", Value::Null);
        let place = place(&root);
        let mut pct = Pct::new();
        let plan = sealed(&mut pct, &paths, &root);
        let done = apply(
            &place,
            &plan,
            DOMAIN,
            CODE,
            &paths,
            &asks(&root, &paths),
            &mut pct,
        )
        .unwrap();
        assert!(pct.calls.iter().any(|c| c == "pct destroy 131 --purge 1"));
        assert!(!pct.there);
        assert!(!paths.firewall.join("131.fw").exists());
        assert!(!paths.secrets.join(format!("{CODE}.admin.pw")).exists());
        // another site's password is not this opening's
        assert!(paths.secrets.join("s-other1.admin.pw").exists());
        assert!(opening(&paths).is_none());
        let kept: Value = serde_json::from_str(
            &fs::read_to_string(abandoned_dir(&paths.creating).join(format!("{CODE}.json")))
                .unwrap(),
        )
        .unwrap();
        assert_eq!(kept["abandon"]["op"], OP);
        assert_eq!(
            kept["abandon"]["done"],
            json!(["container", "firewall", "secrets", "record"])
        );
        assert_eq!(kept["ct"], 131);
        assert_eq!(done, kept);
        // asked again: finished, nothing to apply
        let again = plan_doc(DOMAIN, CODE, &look(&mut pct, &paths, &root));
        assert_eq!(again["next"], "finished");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_stop_half_way_is_written_and_a_new_plan_carries_on() {
        // the opening reached its DNS and there is no Cloudflare key: the DNS step stops
        let (root, paths) = host("resume", json!({"done": 1_794_400_000_u64}));
        let place = place(&root);
        let mut pct = Pct::new();
        let plan = sealed(&mut pct, &paths, &root);
        let err = apply(
            &place,
            &plan,
            DOMAIN,
            CODE,
            &paths,
            &asks(&root, &paths),
            &mut pct,
        )
        .unwrap_err();
        assert!(err.contains("abandon_stopped: dns"), "{err}");
        let rec = opening(&paths).unwrap();
        assert_eq!(rec["abandon"]["done"], json!(["container", "firewall"]));
        assert_eq!(rec["abandon"]["dns_removed"], false);
        // the stopped operation holds the domain until a person has looked
        let held = plan_doc(DOMAIN, CODE, &look(&mut pct, &paths, &root));
        assert_eq!(held["stop"], UNFINISHED_OPERATION);
        opstore::mark_recovered(&place, OP, 1_794_400_100).unwrap();
        // the new plan starts at the DNS, the two done steps are seen done
        let next = plan_doc(DOMAIN, CODE, &look(&mut pct, &paths, &root));
        assert_eq!(next["next"], "run");
        assert_eq!(next["next_step"], "dns");
        assert_eq!(next["steps"][0]["now"], "done");
        assert_eq!(next["steps"][1]["now"], "done");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn dns_already_removed_is_done() {
        let (root, paths) = host("dnsdone", json!({"done": 1_794_400_000_u64}));
        let mut rec = opening(&paths).unwrap();
        rec["abandon"] = json!({"op": OP, "done": ["dns"], "dns_removed": true});
        write_record(&paths.creating, CODE, &rec).unwrap();
        let doc = plan_doc(DOMAIN, CODE, &look(&mut Pct::new(), &paths, &root));
        assert_eq!(doc["steps"][2]["now"], "done");
        let _ = fs::remove_dir_all(&root);
    }

    fn write_ask(root: &Path, asked_at: u64) {
        fs::create_dir_all(root.join("asked")).unwrap();
        fs::write(
            root.join("asked").join(format!("{CODE}.json")),
            json!({"site": CODE, "domain": DOMAIN, "tenant_id": "t-aaa", "state": "waiting", "asked_at": asked_at})
                .to_string(),
        )
        .unwrap();
    }

    /// The customer's own run of the code still going: nothing is taken down under it. A
    /// run no longer waited for is not.
    #[test]
    fn a_customer_opening_still_going_is_not_abandoned() {
        let (root, paths) = host("asking", Value::Null);
        write_ask(&root, crate::probe::unix_now());
        let doc = plan_doc(DOMAIN, CODE, &look(&mut Pct::new(), &paths, &root));
        assert_eq!(doc["stop"], CUSTOMER_RUN_GOING);
        write_ask(&root, 0);
        let doc = plan_doc(DOMAIN, CODE, &look(&mut Pct::new(), &paths, &root));
        assert_eq!(doc["next"], "run");
        assert_eq!(doc["customer_ask"], "stopped");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn an_ask_that_cannot_be_read_stops() {
        let (root, paths) = host("askbad", Value::Null);
        fs::create_dir_all(root.join("asked")).unwrap();
        fs::write(root.join("asked").join(format!("{CODE}.json")), "{").unwrap();
        let doc = plan_doc(DOMAIN, CODE, &look(&mut Pct::new(), &paths, &root));
        assert_eq!(doc["stop"], siteabandon::PROBE_UNKNOWN);
        let _ = fs::remove_dir_all(&root);
    }

    /// The customer's ask of an abandoned opening reads stopped, why abandoned, and stays.
    #[test]
    fn the_customers_ask_is_stopped_as_abandoned_and_kept() {
        let (root, paths) = host("askstop", Value::Null);
        write_ask(&root, 0);
        let place = place(&root);
        let mut pct = Pct::new();
        let plan = sealed(&mut pct, &paths, &root);
        apply(
            &place,
            &plan,
            DOMAIN,
            CODE,
            &paths,
            &asks(&root, &paths),
            &mut pct,
        )
        .unwrap();
        let ask: Value = serde_json::from_str(
            &fs::read_to_string(root.join("asked").join(format!("{CODE}.json"))).unwrap(),
        )
        .unwrap();
        assert_eq!(ask["state"], "stopped");
        assert_eq!(ask["why"], "abandoned");
        assert_eq!(ask["domain"], DOMAIN);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_site_is_never_abandoned() {
        for dir in ["records", "opened"] {
            let (root, paths) = host(&format!("site-{dir}"), Value::Null);
            write_record(&root.join(dir), CODE, &json!({"site": CODE})).unwrap();
            let doc = plan_doc(DOMAIN, CODE, &look(&mut Pct::new(), &paths, &root));
            assert_eq!(doc["next"], "stop");
            assert_eq!(doc["stop"], siteabandon::ALREADY_A_SITE);
            let _ = fs::remove_dir_all(&root);
        }
    }

    #[test]
    fn a_container_under_another_name_or_of_a_site_is_not_touched() {
        let (root, paths) = host("othername", Value::Null);
        let place = place(&root);
        let mut pct = Pct::new();
        pct.hostname = "s-other1".to_owned();
        let doc = plan_doc(DOMAIN, CODE, &look(&mut pct, &paths, &root));
        assert_eq!(doc["stop"], siteabandon::CT_NOT_THIS_OPENING);
        let mut sealed_doc = doc;
        crate::quarantinecmd::seal_with_op(
            &mut sealed_doc,
            OP,
            &"f".repeat(64),
            env!("CARGO_PKG_VERSION"),
        )
        .unwrap();
        assert!(apply(
            &place,
            &sealed_doc,
            DOMAIN,
            CODE,
            &paths,
            &asks(&root, &paths),
            &mut pct
        )
        .is_err());
        assert_eq!(pct.destroys(), 0);
        let _ = fs::remove_dir_all(&root);

        // a live site's container, though named for the code: nothing is destroyed
        let (root, paths) = host("ofasite", Value::Null);
        let place = self::place(&root);
        write_record(
            &paths.records,
            "s-live01",
            &json!({"site": "s-live01", "ct": 131}),
        )
        .unwrap();
        let mut pct = Pct::new();
        let plan = sealed(&mut pct, &paths, &root);
        assert_eq!(plan["stop"], siteabandon::CT_OF_A_SITE);
        assert!(apply(
            &place,
            &plan,
            DOMAIN,
            CODE,
            &paths,
            &asks(&root, &paths),
            &mut pct
        )
        .is_err());
        assert_eq!(pct.destroys(), 0);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn the_opening_must_be_of_this_domain() {
        let (root, paths) = host("otherdomain", Value::Null);
        let doc = plan_doc(
            "else.example.com",
            CODE,
            &look(&mut Pct::new(), &paths, &root),
        );
        assert_eq!(doc["stop"], siteabandon::NOT_THIS_OPENING);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn what_cannot_be_read_stops_everything() {
        let (root, paths) = host("unknown", Value::Null);
        let mut pct = Pct::new();
        pct.config_fails = true;
        let doc = plan_doc(DOMAIN, CODE, &look(&mut pct, &paths, &root));
        assert_eq!(doc["stop"], siteabandon::PROBE_UNKNOWN);
        let mut seen = look(&mut Pct::new(), &paths, &root);
        seen.unfinished = None;
        assert_eq!(plan_doc(DOMAIN, CODE, &seen)["stop"], JOURNAL_UNREADABLE);
        let mut seen = look(&mut Pct::new(), &paths, &root);
        seen.unfinished = Some(vec![json!({"op": OP})]);
        assert_eq!(plan_doc(DOMAIN, CODE, &seen)["stop"], UNFINISHED_OPERATION);
        let mut seen = look(&mut Pct::new(), &paths, &root);
        seen.secrets_left = None;
        assert_eq!(
            plan_doc(DOMAIN, CODE, &seen)["stop"],
            siteabandon::PROBE_UNKNOWN
        );
        let mut seen = look(&mut Pct::new(), &paths, &root);
        seen.site_record = None;
        assert_eq!(
            plan_doc(DOMAIN, CODE, &seen)["stop"],
            siteabandon::PROBE_UNKNOWN
        );
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn no_opening_is_no_opening_and_an_abandoned_one_is_finished() {
        let (root, paths) = host("none", Value::Null);
        fs::remove_file(paths.creating.join(format!("{CODE}.json"))).unwrap();
        let doc = plan_doc(DOMAIN, CODE, &look(&mut Pct::new(), &paths, &root));
        assert_eq!(doc["stop"], NO_OPENING);
        write_record(
            &abandoned_dir(&paths.creating),
            CODE,
            &json!({"site": CODE}),
        )
        .unwrap();
        let doc = plan_doc(DOMAIN, CODE, &look(&mut Pct::new(), &paths, &root));
        assert_eq!(doc["next"], "finished");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_host_that_changed_after_the_plan_is_left_alone() {
        let (root, paths) = host("changed", Value::Null);
        let place = place(&root);
        let mut pct = Pct::new();
        let plan = sealed(&mut pct, &paths, &root);
        fs::remove_file(paths.firewall.join("131.fw")).unwrap();
        let err = apply(
            &place,
            &plan,
            DOMAIN,
            CODE,
            &paths,
            &asks(&root, &paths),
            &mut pct,
        )
        .unwrap_err();
        assert!(err.contains("plan_changed"), "{err}");
        assert_eq!(pct.destroys(), 0);
        assert!(opening(&paths).is_some());
        let _ = fs::remove_dir_all(&root);
    }

    /// The step looks at the container's name again right before: the plan's look may be
    /// minutes old.
    #[test]
    fn the_container_is_looked_at_again_right_before_it_goes() {
        let mut pct = Pct::new();
        pct.hostname = "s-other1".to_owned();
        let err = container_step(&mut pct, 131, CODE).unwrap_err();
        assert!(err.starts_with(siteabandon::CT_NOT_THIS_OPENING), "{err}");
        assert_eq!(pct.destroys(), 0);
        let mut gone = Pct::new();
        gone.there = false;
        container_step(&mut gone, 131, CODE).unwrap();
        assert_eq!(gone.destroys(), 0);
    }

    #[test]
    fn a_container_that_is_still_there_after_destroy_stops() {
        let (root, paths) = host("stays", Value::Null);
        let place = place(&root);
        let mut pct = Pct::new();
        pct.destroy_works = false;
        let plan = sealed(&mut pct, &paths, &root);
        let err = apply(
            &place,
            &plan,
            DOMAIN,
            CODE,
            &paths,
            &asks(&root, &paths),
            &mut pct,
        )
        .unwrap_err();
        assert!(err.contains("abandon_stopped: container"), "{err}");
        // nothing after it ran: the firewall file and the record are where they were
        assert!(paths.firewall.join("131.fw").exists());
        assert!(opening(&paths).unwrap()["abandon"].is_null());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn an_abandoned_openings_numbers_are_never_given_again() {
        let (root, paths) = host("numbers", Value::Null);
        let removed = root.join("removed");
        write_record(
            &removed,
            "s-rm0001",
            &json!({"site": "s-rm0001", "ct": 120, "ip": "10.77.0.20"}),
        )
        .unwrap();
        write_record(
            &abandoned_dir(&paths.creating),
            "s-gone01",
            &json!({"site": "s-gone01", "ct": 140, "ip": "10.77.0.50"}),
        )
        .unwrap();
        let gone = crate::sitecreatecmd::gone_records(&removed, &paths.creating).unwrap();
        let codes: Vec<&str> = gone.iter().map(|r| r["site"].as_str().unwrap()).collect();
        assert!(codes.contains(&"s-rm0001") && codes.contains(&"s-gone01"));
        // the abandoned directory is not read as openings
        let opening_codes: Vec<Value> =
            crate::sitecreatecmd::read_records(&paths.creating).unwrap();
        assert_eq!(opening_codes.len(), 1);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn the_plan_and_the_apply_name_this_code_and_domain() {
        let (root, paths) = host("given", Value::Null);
        let place = place(&root);
        let mut pct = Pct::new();
        let plan = sealed(&mut pct, &paths, &root);
        assert!(apply(
            &place,
            &plan,
            DOMAIN,
            "s-other1",
            &paths,
            &asks(&root, &paths),
            &mut pct
        )
        .is_err());
        assert!(apply(
            &place,
            &plan,
            "else.example.com",
            CODE,
            &paths,
            &asks(&root, &paths),
            &mut pct
        )
        .is_err());
        assert_eq!(pct.destroys(), 0);
        let _ = fs::remove_dir_all(&root);
    }
}
