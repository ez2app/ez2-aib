// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host site create <domain> [--blank] --plan` — the read-only half of opening a
//! site. It checks the domain, reads every number in use on this host and every removed
//! site's numbers, looks for a site that already serves the domain, reads who the first
//! administrators are, the host lock and the unfinished operations, probes the nine steps
//! of [`aib_cloud::sitecreate`] and prints what would be done next, sealed like every other
//! plan. **Nothing is changed**: without `--plan` the command refuses.
//!
//! Every judgement is the rule crate's: the order and the next step
//! ([`sitecreate::next`]), what a failure leaves ([`sitecreate::after_failure`]), the
//! numbers ([`sitecreate::next_numbers`]), the title and the login names
//! ([`aib_cloud::sitedefaults`]). This file only reads. Whatever could not be read is
//! "could not tell" and stops the plan: a list that could not be read is not an empty list.
//!
//! The customer's quota (E7, [`aib_cloud::quota`]) is judged for the tenant named by
//! `--tenant`: the customer's live records and the opening records ([`CREATING`]) against the
//! plan. A plan that cannot be told stops at `quota_unknown`, a plan with no room for one
//! more site on the starting tier at `quota_exceeded`.
//!
//! The administrators' mail addresses are host configuration ([`SITE_ADMINS`], one address
//! per line, `#` for comments); they are never written into this repository. The plan
//! prints the login names only.

use std::fs;
use std::path::Path;
use std::process::Command;
use std::time::Duration;

use aib_cloud::edge::valid_domain;
use aib_cloud::quota::{self, Verdict};
use aib_cloud::sitecreate::{self, AfterFailure, CreateStep, Next, Probe, STEPS};
use aib_cloud::sitedefaults::{admin_login, site_title};
use serde_json::{json, Value};

use crate::opstore;
use crate::probe::run_capture;

const RECORDS: &str = "/var/lib/ez2-cloud/sites";
const REMOVED_RECORDS: &str = "/var/lib/ez2-cloud/sites-removed";
/// The records of sites whose opening has begun and not ended (written by the opening
/// itself, B4). They count against the customer's quota; a missing directory is none.
pub const CREATING: &str = "/var/lib/ez2-cloud/sites-creating";
/// The configuration of every container and every virtual machine of this host.
const LXC_CONFIGS: &str = "/etc/pve/lxc";
const VM_CONFIGS: &str = "/etc/pve/qemu-server";
/// The first administrators of every new site: one mail address per line.
pub const SITE_ADMINS: &str = "/etc/ez2-cloud/site-admins";
/// The sites' internal network; a site's address is this plus one last octet.
const NET: &str = "10.77.0.";
/// What the code of a new site will be. No generator exists yet: the code is made when the
/// site is created, so the plan names the rule, not a code.
const CODE_RULE: &str = "generated at creation: passes site::valid_code (not a reserved word) and is not the code of any live or removed site";

/// What was read from the host. `None` is "could not be read".
#[derive(Default)]
pub struct Seen {
    /// `pct list`.
    pub container_list: Option<String>,
    /// `qm list`: virtual machines share the containers' numbers.
    pub vm_list: Option<String>,
    /// The last octets of the internal addresses in every guest's configuration.
    pub guest_ips: Option<Vec<u8>>,
    pub records: Option<Vec<Value>>,
    pub removed: Option<Vec<Value>>,
    /// The opening records ([`CREATING`]).
    pub creating: Option<Vec<Value>>,
    /// The customer the site is opened for (`--tenant`).
    pub tenant: Option<String>,
    pub entry_config: Option<String>,
    /// The text of [`SITE_ADMINS`].
    pub admins: Option<String>,
    /// The host lock as found, see [`lock_state`].
    pub lock: Value,
    /// The unfinished operations on this domain's target: `Some([])` when there are none.
    pub unfinished: Option<Vec<Value>>,
}

/// The domain as typed. Lower case only: what is typed is what is matched against the
/// records and the entry, so an upper-case letter is refused, not folded.
pub fn domain_arg(name: &str) -> Result<String, String> {
    if !valid_domain(name) {
        return Err(format!(
            "{name:?} is not a domain: lower-case letters, digits, inner hyphens, at least two labels"
        ));
    }
    Ok(name.to_owned())
}

/// The target of opening the site at `domain` in the operation journal.
pub fn target(domain: &str) -> String {
    format!("domain:{domain}")
}

/// The longest target a journal line holds (`aib_cloud::op`).
const TARGET_MAX: usize = 80;

/// The numbers of `pct list` or `qm list`. The list is only passed here when the command
/// ended with 0 ([`run_capture`] gives nothing otherwise). An empty output is no guests:
/// a host without any virtual machine prints not even the header (`qm list` on the host,
/// 33e28 11-11 M1). Anything else must open with the header and every other line with a
/// number, or it is `None`.
pub fn guest_ids(list: &str) -> Option<Vec<u32>> {
    let mut lines = list.lines().filter(|l| !l.trim().is_empty());
    let Some(header) = lines.next() else {
        return Some(Vec::new());
    };
    if !header.trim_start().starts_with("VMID") {
        return None;
    }
    lines
        .map(|l| l.split_whitespace().next().and_then(|w| w.parse().ok()))
        .collect()
}

/// The last octets of the sites' network named by `ip=` in a guest configuration (every
/// network line, snapshots included: an older number still counts).
pub fn ips_in_config(text: &str) -> Vec<u8> {
    text.lines()
        .flat_map(|l| l.split([',', ' ']))
        .filter_map(|w| w.strip_prefix("ip=")?.strip_prefix(NET))
        .filter_map(|rest| rest.split('/').next()?.parse().ok())
        .collect()
}

/// The container number and the address's last octet of one record. `None` when either is
/// missing or not one.
fn record_numbers(rec: &Value) -> Option<(u32, u8)> {
    let ct = u32::try_from(rec.get("ct")?.as_u64()?).ok()?;
    let ip = rec.get("ip")?.as_str()?.strip_prefix(NET)?.parse().ok()?;
    Some((ct, ip))
}

/// The container number and address for the new site, from every guest (containers and
/// virtual machines), every guest configuration, every live record and every removed
/// record. Anything not read, or a record without its numbers, is `numbers_unknown`.
pub fn numbers(seen: &Seen) -> Result<(u32, u8), &'static str> {
    let mut live_cts = Vec::new();
    for list in [seen.container_list.as_deref(), seen.vm_list.as_deref()] {
        let ids = list.and_then(guest_ids).ok_or("numbers_unknown")?;
        live_cts.extend(ids);
    }
    let mut live_ips = seen.guest_ips.clone().ok_or("numbers_unknown")?;
    let records = seen.records.as_ref().ok_or("numbers_unknown")?;
    for rec in records {
        let (ct, ip) = record_numbers(rec).ok_or("numbers_unknown")?;
        live_cts.push(ct);
        live_ips.push(ip);
    }
    let (mut removed_cts, mut removed_ips) = (Vec::new(), Vec::new());
    for rec in seen.removed.as_ref().ok_or("numbers_unknown")? {
        let (gone_ct, gone_ip) = record_numbers(rec).ok_or("numbers_unknown")?;
        removed_cts.push(gone_ct);
        removed_ips.push(gone_ip);
    }
    sitecreate::next_numbers(&live_cts, &removed_cts, &live_ips, &removed_ips)
}

/// The names that count as the same domain: the name itself and its `www` twin.
fn same_names(domain: &str) -> [String; 2] {
    let bare = domain.strip_prefix("www.").unwrap_or(domain);
    [bare.to_owned(), format!("www.{bare}")]
}

/// The site that already serves `domain` (or its `www` twin): a live record naming it, or
/// a block of the entry opening with it. The entry's block is named by the `# site:<code>`
/// line above it, or `unmarked` when there is none.
pub fn taken_by(domain: &str, records: &[Value], entry_config: Option<&str>) -> Option<String> {
    let names = same_names(domain);
    // an address of the entry may carry a scheme and a port (`https://x`, `x:443`)
    let bare = |n: &str| {
        let n = n
            .strip_prefix("https://")
            .or_else(|| n.strip_prefix("http://"))
            .unwrap_or(n);
        n.split_once(':').map_or(n, |(host, _)| host).to_owned()
    };
    let named = |list: &str| {
        list.split(|c: char| c == ',' || c.is_whitespace())
            .any(|n| names.iter().any(|m| *m == bare(n)))
    };
    for rec in records {
        if rec.get("hosts").and_then(Value::as_str).is_some_and(named) {
            let code = rec.get("site").and_then(Value::as_str).unwrap_or("unnamed");
            return Some(code.to_owned());
        }
    }
    let mut marker: Option<&str> = None;
    for line in entry_config.unwrap_or("").lines() {
        let line = line.trim_end();
        if let Some(code) = line.strip_prefix("# site:") {
            marker = Some(code.trim());
        } else if let Some(opening) = line.strip_suffix('{') {
            if named(opening) {
                return Some(marker.unwrap_or("unmarked").to_owned());
            }
            marker = None;
        }
    }
    None
}

/// The login names of the first administrators, in the order of [`SITE_ADMINS`]. `Err`
/// with a fixed word: the file was not read, holds no address, or an address leaves no
/// usable name.
pub fn admin_logins(text: Option<&str>) -> Result<Vec<String>, &'static str> {
    let text = text.ok_or("admins_unknown")?;
    let mut logins: Vec<String> = Vec::new();
    for line in text.lines().map(str::trim) {
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let taken: Vec<&str> = logins.iter().map(String::as_str).collect();
        let login = admin_login(line, &taken).ok_or("admin_login_unusable")?;
        logins.push(login);
    }
    if logins.is_empty() {
        return Err("admins_unknown");
    }
    Ok(logins)
}

/// The host lock as found, read only. Not there ⇒ free; there and a holder ⇒ held, with
/// whether the holder still runs; there and not a holder ⇒ damaged; not readable ⇒ unknown.
pub fn lock_state(lock: &Path, proc_root: &Path) -> Value {
    match fs::read_to_string(lock) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => json!({ "state": "free" }),
        Err(e) => json!({ "state": "unknown", "error": e.to_string() }),
        Ok(text) => match opstore::holder_from_text(&text) {
            None => json!({ "state": "damaged" }),
            Some(h) => json!({
                "state": "held",
                "op": h.op,
                "pid": h.pid,
                "liveness": format!("{:?}", opstore::liveness(proc_root, &h)).to_lowercase(),
            }),
        },
    }
}

/// One probe per step, in [`STEPS`] order. Nothing of the new site exists before it is
/// cloned, so every step is "not done" once the host could be read; the clone is "could
/// not tell" when the numbers could not be read (the new number cannot be shown free),
/// the entry when its configuration could not be read.
pub fn probes(seen: &Seen) -> [Probe; 9] {
    let mut p = [Probe::NotDone; 9];
    if numbers(seen).is_err() {
        p[0] = Probe::Unknown;
    }
    if seen.entry_config.as_deref().is_none_or(str::is_empty) {
        p[7] = Probe::Unknown;
    }
    p
}

fn probe_word(p: Probe) -> &'static str {
    match p {
        Probe::Done => "done",
        Probe::NotDone => "not_done",
        Probe::Unknown => "unknown",
    }
}

fn failure_word(step: CreateStep) -> &'static str {
    match sitecreate::after_failure(step) {
        AfterFailure::Undo => "undo",
        AfterFailure::Keep => "keep",
    }
}

/// The plan document. Separate from [`cmd`] so it can be tested without a host.
pub fn plan_doc(domain: &str, blank: bool, seen: &Seen) -> Value {
    let probes = probes(seen);
    let numbers = numbers(seen);
    let logins = admin_logins(seen.admins.as_deref());
    let taken = taken_by(
        domain,
        seen.records.as_deref().unwrap_or_default(),
        seen.entry_config.as_deref(),
    );
    let next = sitecreate::next(&probes, blank);
    let quota = quota::judge(
        seen.tenant.as_deref(),
        seen.records.as_deref(),
        seen.creating.as_deref(),
    );
    // a stop of this command comes before the order's own answer
    let own_stop: Option<&'static str> = if taken.is_some() {
        Some("domain_taken")
    } else if target(domain).len() > TARGET_MAX {
        // the opening could not be written in the journal (33e28 11-11 N2)
        Some("domain_too_long_for_journal")
    } else if seen.unfinished.as_ref().is_some_and(|u| !u.is_empty()) {
        Some("unfinished_operation")
    } else if seen.unfinished.is_none() {
        Some("journal_unreadable")
    } else if let Err(w) = &logins {
        Some(*w)
    } else if let Some(w) = quota.stop() {
        Some(w)
    } else {
        numbers.err()
    };
    let (next_word, next_step, stop) = match (own_stop, next) {
        (Some(w), _) => ("stop", Some(CreateStep::Clone), Some(w)),
        (None, Next::Run(s)) => ("run", Some(s), None),
        (None, Next::Finished) => ("finished", None, None),
        (None, Next::Stop(w, s)) => ("stop", Some(s), Some(w)),
    };
    // calling the opening off leaves nothing behind only while the step to run would be
    // undone on failure; a plan that stops promises nothing
    let may_call_off = next_word == "run"
        && next_step.is_some_and(|s| sitecreate::after_failure(s) == AfterFailure::Undo);
    json!({
        "plan": "site-create",
        "domain": domain,
        "blank": blank,
        "level": "L2",
        "site_code": CODE_RULE,
        "ct": numbers.ok().map(|(ct, _)| ct),
        "ip": numbers.ok().map(|(_, ip)| format!("{NET}{ip}")),
        "numbers": numbers.err().unwrap_or("read"),
        "title": site_title(domain),
        "admins": logins.as_ref().ok(),
        "admins_from": SITE_ADMINS,
        "taken_by": taken,
        "target": target(domain),
        "unfinished": seen.unfinished,
        "lock": seen.lock,
        "quota": quota_doc(seen.tenant.as_deref(), &quota),
        "new_tenant": seen.tenant.as_deref().map(|t| !quota::seen_before(
            t,
            &[
                seen.records.as_deref().unwrap_or_default(),
                seen.creating.as_deref().unwrap_or_default(),
                seen.removed.as_deref().unwrap_or_default(),
            ],
        )),
        "steps": STEPS.iter().zip(probes).map(|(s, p)| json!({
            "step": s.as_str(),
            "applies": s.applies(blank),
            "now": probe_word(p),
            "on_failure": failure_word(*s),
        })).collect::<Vec<_>>(),
        "next": next_word,
        "next_step": next_step.map(CreateStep::as_str),
        "stop": stop,
        "may_call_off": may_call_off,
        "not_looked_at": ["DNS of the domain", "the licences left at the vendors"],
    })
}

/// The quota as the plan prints it.
fn quota_doc(tenant: Option<&str>, verdict: &Verdict) -> Value {
    let plan = quota::plan_of(tenant)
        .ok()
        .map(|p| json!({ "cores": p.cores, "memory_mb": p.memory_mb, "disk_gb": p.disk_gb }));
    let counted = |c: &quota::Count, allowed: u32| {
        json!({
            "live": c.live,
            "opening": c.opening,
            "allowed": allowed,
            "held": { "cpu_x100": c.held.cpu_x100, "memory_mb": c.held.memory_mb, "disk_gb": c.held.disk_gb },
        })
    };
    let (word, sites, short, why) = match verdict {
        Verdict::Fits { count, allowed } => ("fits", counted(count, *allowed), None, None),
        Verdict::Exceeded {
            count,
            allowed,
            short,
        } => (
            "quota_exceeded",
            counted(count, *allowed),
            Some(match short {
                aib_cloud::arc::Resource::Cpu => "cpu",
                aib_cloud::arc::Resource::Memory => "memory",
                aib_cloud::arc::Resource::Disk => "disk",
            }),
            None,
        ),
        Verdict::Unknown(u) => ("quota_unknown", Value::Null, None, Some(u.as_str())),
    };
    json!({
        "tenant_id": tenant,
        "verdict": word,
        "plan": plan,
        "sites": sites,
        "short_of": short,
        "unknown": why,
        "new_site": "Limits::START",
    })
}

/// Put the operation id in and seal. A host whose machine id cannot be read gets no plan:
/// a hash over a stand-in would match every host that cannot be read.
pub fn seal_plan(doc: &mut Value, op: &str, machine_id: &Path) -> Result<(), String> {
    let fingerprint = opstore::host_fingerprint(machine_id)?;
    crate::quarantinecmd::seal_with_op(doc, op, &fingerprint, env!("CARGO_PKG_VERSION"))
}

fn capture(program: &str, args: &[&str], seconds: u64) -> Option<String> {
    let mut cmd = Command::new(program);
    cmd.args(args);
    run_capture(&mut cmd, Duration::from_secs(seconds)).ok()
}

/// Every `*.json` record under `dir`, parsed. A directory that is not there holds no
/// record (it is made with the first one): unlike everything else here, absent is read as
/// empty, not as "could not tell". The guest lists still count every number in use, so a
/// missing records directory cannot hand out a number a container has (33e28 11-11 N1).
/// Any other failure is `None`.
pub fn read_records(dir: &Path) -> Option<Vec<Value>> {
    let rd = match fs::read_dir(dir) {
        Ok(rd) => rd,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Some(Vec::new()),
        Err(_) => return None,
    };
    let mut out = Vec::new();
    for item in rd {
        let path = item.ok()?.path();
        if path.extension().and_then(|x| x.to_str()) != Some("json") {
            continue;
        }
        out.push(serde_json::from_str(&fs::read_to_string(&path).ok()?).ok()?);
    }
    Some(out)
}

/// The sites' addresses in every `*.conf` under the given directories. Any directory or
/// file that cannot be read is `None`.
pub fn read_guest_ips(dirs: &[&Path]) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    for dir in dirs {
        for item in fs::read_dir(dir).ok()? {
            let path = item.ok()?.path();
            if path.extension().and_then(|x| x.to_str()) == Some("conf") {
                out.extend(ips_in_config(&fs::read_to_string(&path).ok()?));
            }
        }
    }
    Some(out)
}

/// The unfinished operations on `domain` and on its `www` twin in the journals under
/// `ops`: the twin is the same site ([`taken_by`]), so an opening of either holds both.
pub fn unfinished_on(ops: &Path, domain: &str) -> Option<Vec<Value>> {
    let lasts = opstore::last_entries(ops).ok()?;
    let report = opstore::unfinished_report(&lasts);
    let mut out = Vec::new();
    for name in same_names(domain) {
        if let Some(list) = report.get(target(&name)).and_then(Value::as_array) {
            out.extend(list.iter().cloned());
        }
    }
    Some(out)
}

fn see(domain: &str, records: &str, tenant: Option<String>) -> Seen {
    let proc_root = Path::new("/proc");
    Seen {
        container_list: capture("pct", &["list"], 20),
        vm_list: capture("qm", &["list"], 20),
        guest_ips: read_guest_ips(&[Path::new(LXC_CONFIGS), Path::new(VM_CONFIGS)]),
        records: read_records(Path::new(records)),
        removed: read_records(Path::new(REMOVED_RECORDS)),
        creating: read_records(Path::new(CREATING)),
        tenant,
        entry_config: crate::probe::edge_caddyfile().ok(),
        admins: fs::read_to_string(SITE_ADMINS).ok(),
        lock: lock_state(Path::new(opstore::LOCK_FILE), proc_root),
        unfinished: unfinished_on(Path::new(opstore::OPS_DIR), domain),
    }
}

pub fn cmd(args: &[String]) -> Result<u8, String> {
    let domain = args
        .first()
        .filter(|a| !a.starts_with("--"))
        .ok_or("needs <domain>")?;
    let domain = domain_arg(domain)?;
    let mut records = RECORDS.to_owned();
    let mut plan_only = false;
    let mut blank = false;
    let mut tenant = None;
    let mut it = args[1..].iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--plan" => plan_only = true,
            "--blank" => blank = true,
            "--tenant" => tenant = Some(it.next().ok_or("--tenant needs a customer id")?.clone()),
            "--records" => records = it.next().ok_or("--records needs a dir")?.clone(),
            other => return Err(format!("unknown argument {other}")),
        }
    }
    if !plan_only {
        return Err(
            "only --plan is available: opening a site needs the operation journal, the host lock and an approval, which are not wired to this command yet"
                .to_owned(),
        );
    }
    let seen = see(&domain, &records, tenant);
    let mut doc = plan_doc(&domain, blank, &seen);
    let op = opstore::op_id(crate::probe::unix_now(), opstore::random4()?);
    seal_plan(&mut doc, &op, Path::new(opstore::MACHINE_ID))?;
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
    use aib_cloud::op::{Entry, State};
    use std::path::PathBuf;

    const PCT: &str = "VMID       Status     Lock         Name\n101        running                 edge\n112        running                 site2\n900        stopped                 template\n";
    const QM: &str = "      VMID NAME                 STATUS     MEM(MB)    BOOTDISK(GB) PID\n       130 runner               running    4096              32.00 4242\n";
    const ADMINS: &str = "# first administrators\nops@example.test\n\nOps@other.example.test\n";

    fn rec(code: &str, ct: u32, ip: u8, hosts: &str) -> Value {
        json!({ "site": code, "ct": ct, "ip": format!("10.77.0.{ip}"), "hosts": hosts })
    }

    fn host() -> Seen {
        Seen {
            container_list: Some(PCT.to_owned()),
            vm_list: Some(QM.to_owned()),
            guest_ips: Some(vec![2, 12, 40]),
            records: Some(vec![rec("site2", 112, 12, "shop.example.test")]),
            removed: Some(vec![rec("site3", 125, 25, "old.example.test")]),
            creating: Some(Vec::new()),
            tenant: Some("acme".to_owned()),
            entry_config: Some("# site:site2\nshop.example.test {\n}\n".to_owned()),
            admins: Some(ADMINS.to_owned()),
            lock: json!({ "state": "free" }),
            unfinished: Some(Vec::new()),
        }
    }

    fn scratch(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("aib-sitecreate-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    /// A domain nobody serves on a host that could be read: clone first, with the numbers
    /// one above every guest, every record and every removed record.
    #[test]
    fn a_fresh_domain_plans_the_clone_with_the_next_numbers() {
        let doc = plan_doc("www.new.example.test", false, &host());
        assert_eq!(doc["next"], "run");
        assert_eq!(doc["next_step"], "clone");
        assert_eq!(doc["stop"], Value::Null);
        assert_eq!(doc["may_call_off"], true);
        // a virtual machine's number counts like a container's; 900 is the template's
        assert_eq!(doc["ct"], 131);
        // the removed site's address is above the live ones and is never handed out again
        assert_eq!(doc["ip"], "10.77.0.41");
        assert_eq!(doc["numbers"], "read");
        assert_eq!(doc["title"], "new");
        assert_eq!(doc["admins"], json!(["ops", "ops2"]));
        assert_eq!(doc["target"], "domain:www.new.example.test");
        assert_eq!(doc["quota"]["verdict"], "fits");
        assert_eq!(doc["quota"]["tenant_id"], "acme");
        assert_eq!(doc["quota"]["sites"]["allowed"], 4);
        assert_eq!(doc["quota"]["plan"]["memory_mb"], 16_384);
        // no record names acme: a customer never seen before is marked, not refused
        assert_eq!(doc["new_tenant"], true);
        assert_eq!(doc["site_code"], CODE_RULE);
        assert_eq!(doc["taken_by"], Value::Null);
        let steps = doc["steps"].as_array().unwrap();
        assert_eq!(steps.len(), 9);
        assert!(steps.iter().all(|s| s["now"] == "not_done"));
        // a failed identity is undone, a failed licence is kept
        assert_eq!(steps[5]["on_failure"], "undo");
        assert_eq!(steps[6]["on_failure"], "keep");
        // the mail addresses themselves are not in the plan
        assert!(!doc.to_string().contains("@"));
    }

    #[test]
    fn a_blank_site_passes_over_the_licences() {
        let doc = plan_doc("blank.example.test", true, &host());
        assert_eq!(doc["steps"][6]["step"], "licenses");
        assert_eq!(doc["steps"][6]["applies"], false);
        assert_eq!(doc["steps"][5]["applies"], true);
        assert_eq!(doc["next_step"], "clone");
    }

    /// Any list of numbers that could not be read stops the plan: it is not an empty list.
    #[test]
    fn an_unreadable_number_stops_the_plan() {
        type Break = Box<dyn Fn(&mut Seen)>;
        let cases: Vec<Break> = vec![
            Box::new(|s| s.container_list = None),
            Box::new(|s| s.vm_list = None),
            Box::new(|s| s.vm_list = Some("no such command\n".to_owned())),
            Box::new(|s| s.guest_ips = None),
            Box::new(|s| s.records = None),
            Box::new(|s| s.removed = None),
            Box::new(|s| s.removed = Some(vec![json!({ "site": "site3", "ip": "10.77.0.25" })])),
            Box::new(|s| s.records = Some(vec![json!({ "site": "site2", "ct": 112 })])),
        ];
        for (i, f) in cases.iter().enumerate() {
            let mut seen = host();
            f(&mut seen);
            assert_eq!(numbers(&seen), Err("numbers_unknown"), "case {i}");
            let doc = plan_doc("new.example.test", false, &seen);
            assert_eq!(doc["next"], "stop", "case {i}");
            assert_eq!(doc["may_call_off"], false, "case {i}");
            assert_eq!(doc["ct"], Value::Null, "case {i}");
            assert_eq!(doc["steps"][0]["now"], "unknown", "case {i}");
        }
    }

    #[test]
    fn numbers_come_from_lists_and_configurations() {
        assert_eq!(guest_ids(PCT), Some(vec![101, 112, 900]));
        assert_eq!(guest_ids(QM), Some(vec![130]));
        assert_eq!(guest_ids("VMID Status\n"), Some(Vec::new()));
        // a host without virtual machines: `qm list` prints nothing and ends with 0
        assert_eq!(guest_ids(""), Some(Vec::new()));
        assert_eq!(guest_ids("\n"), Some(Vec::new()));
        let mut seen = host();
        seen.vm_list = Some(String::new());
        assert_eq!(numbers(&seen), Ok((126, 41)));
        assert_eq!(guest_ids("130 runner running\n"), None);
        assert_eq!(guest_ids("VMID Status\nbusy running\n"), None);
        let conf = "net0: name=eth0,bridge=vmbr1,ip=10.77.0.12/24,gw=10.77.0.1,firewall=1\n[snap]\nnet0: name=eth0,ip=10.77.0.9/24\nipconfig0: ip=10.77.0.60/24,gw=10.77.0.1\nnet1: ip=192.0.2.5/24\n";
        assert_eq!(ips_in_config(conf), vec![12, 9, 60]);
        let d = scratch("guests");
        fs::write(d.join("112.conf"), conf).unwrap();
        fs::write(d.join("note.txt"), "ip=10.77.0.200/24").unwrap();
        assert_eq!(read_guest_ips(&[d.as_path()]), Some(vec![12, 9, 60]));
        assert_eq!(read_guest_ips(&[d.join("missing").as_path()]), None);
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn records_are_read_whole_or_not_at_all() {
        let d = scratch("records");
        assert_eq!(read_records(&d.join("missing")), Some(Vec::new()));
        fs::write(
            d.join("site2.json"),
            rec("site2", 112, 12, "a.example.test").to_string(),
        )
        .unwrap();
        fs::write(d.join("site2.json.bak"), "not json").unwrap();
        assert_eq!(read_records(&d).unwrap().len(), 1);
        fs::write(d.join("site4.json"), "{ cut").unwrap();
        assert_eq!(read_records(&d), None);
        let _ = fs::remove_dir_all(&d);
    }

    /// A record or an entry block for the domain, or for its `www` twin, stops the plan and
    /// names the site.
    #[test]
    fn an_existing_domain_stops_the_plan() {
        for (domain, site) in [
            ("shop.example.test", "site2"),
            ("www.shop.example.test", "site2"),
        ] {
            let doc = plan_doc(domain, false, &host());
            assert_eq!(doc["next"], "stop", "{domain}");
            assert_eq!(doc["stop"], "domain_taken", "{domain}");
            assert_eq!(doc["taken_by"], site, "{domain}");
            assert_eq!(doc["may_call_off"], false, "{domain}");
        }
        // in a record's list of names, not the first one
        let mut seen = host();
        seen.records = Some(vec![rec(
            "site2",
            112,
            12,
            "shop.example.test, extra.example.test",
        )]);
        assert_eq!(
            plan_doc("extra.example.test", false, &seen)["taken_by"],
            "site2"
        );
        // only the entry knows it: named by the marker, or unmarked
        let mut seen = host();
        seen.records = Some(Vec::new());
        seen.entry_config =
            Some("# site:site7\nside.example.test, www.side.example.test {\n}\nloose.example.test {\n}\n".to_owned());
        assert_eq!(
            plan_doc("side.example.test", false, &seen)["taken_by"],
            "site7"
        );
        assert_eq!(
            plan_doc("loose.example.test", false, &seen)["taken_by"],
            "unmarked"
        );
        // an entry address written with a scheme or a port is the same name
        let mut seen = host();
        seen.records = Some(Vec::new());
        seen.entry_config = Some(
            "# site:site8\nhttps://www.port.example.test:443 {\n}\n# site:site9\nplain.example.test:8443 {\n}\n"
                .to_owned(),
        );
        assert_eq!(
            plan_doc("port.example.test", false, &seen)["taken_by"],
            "site8"
        );
        assert_eq!(
            plan_doc("plain.example.test", false, &seen)["taken_by"],
            "site9"
        );
        // a removed site's domain is free again
        assert_eq!(plan_doc("old.example.test", false, &host())["next"], "run");
    }

    #[test]
    fn an_unreadable_entry_stops_at_the_entry_step() {
        let mut seen = host();
        seen.entry_config = None;
        let doc = plan_doc("new.example.test", false, &seen);
        assert_eq!(doc["steps"][7]["now"], "unknown");
        assert_eq!(doc["stop"], "probe_unknown");
        assert_eq!(doc["next_step"], "edge");
        assert_eq!(doc["may_call_off"], false);
    }

    /// A domain whose journal target would not fit a journal line stops the plan.
    #[test]
    fn a_domain_too_long_for_the_journal_stops_the_plan() {
        let long = format!("{}.example.test", "a".repeat(63));
        assert!(target(&long).len() > TARGET_MAX);
        let doc = plan_doc(&long, false, &host());
        assert_eq!(doc["stop"], "domain_too_long_for_journal");
        assert_eq!(doc["may_call_off"], false);
        let fits = format!("{}.example.test", "a".repeat(40));
        assert_eq!(plan_doc(&fits, false, &host())["next"], "run");
    }

    #[test]
    fn an_uppercase_domain_is_refused() {
        assert!(domain_arg("Shop.Example.Test").is_err());
        assert!(domain_arg("shop.example.test.").is_err());
        assert!(domain_arg("localhost").is_err());
        assert_eq!(
            domain_arg("shop.example.test").unwrap(),
            "shop.example.test"
        );
    }

    #[test]
    fn admins_come_from_the_host_file_or_stop_the_plan() {
        let mut seen = host();
        seen.admins = None;
        let doc = plan_doc("new.example.test", false, &seen);
        assert_eq!(doc["stop"], "admins_unknown");
        assert_eq!(doc["admins"], Value::Null);
        assert_eq!(admin_logins(Some("# none yet\n\n")), Err("admins_unknown"));
        assert_eq!(
            admin_logins(Some("@example.test\n")),
            Err("admin_login_unusable")
        );
        let mut seen = host();
        seen.admins = Some("-@example.test\n".to_owned());
        assert_eq!(
            plan_doc("new.example.test", false, &seen)["stop"],
            "admin_login_unusable"
        );
    }

    #[test]
    fn an_unfinished_operation_on_the_domain_stops_the_plan() {
        let d = scratch("ops");
        // the opening was asked for the www twin
        let target = target("www.new.example.test");
        assert_eq!(unfinished_on(&d, "new.example.test"), Some(Vec::new()));
        let line = |seq, state, approval: &str| Entry {
            op: "20261111T120000Z-ab12cd34".to_owned(),
            seq,
            at: 1_794_300_000,
            state,
            command: "site.create".to_owned(),
            target: target.clone(),
            plan_sha256: "a".repeat(64),
            code: "ok".to_owned(),
            approval: approval.to_owned(),
        };
        opstore::append_entry(&d, &line(1, State::Planned, "")).unwrap();
        opstore::append_entry(&d, &line(2, State::Approved, "n1")).unwrap();
        opstore::append_entry(&d, &line(3, State::Running, "n1")).unwrap();
        let found = unfinished_on(&d, "new.example.test").unwrap();
        assert_eq!(found.len(), 1);
        assert_eq!(unfinished_on(&d, "www.new.example.test").unwrap().len(), 1);
        // another domain is not held by it
        assert_eq!(unfinished_on(&d, "other.example.test"), Some(Vec::new()));
        let mut seen = host();
        seen.unfinished = Some(found);
        let doc = plan_doc("new.example.test", false, &seen);
        assert_eq!(doc["stop"], "unfinished_operation");
        assert_eq!(doc["may_call_off"], false);
        // a journal that does not read holds every target
        fs::write(
            d.join("20261111T120001Z-cd34ef56.jsonl"),
            "not a journal line\nnor this\n",
        )
        .unwrap();
        assert_eq!(unfinished_on(&d, "new.example.test"), None);
        seen.unfinished = None;
        assert_eq!(
            plan_doc("new.example.test", false, &seen)["stop"],
            "journal_unreadable"
        );
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn the_lock_is_read_and_left_alone() {
        let d = scratch("lock");
        let lock = d.join("host.lock");
        assert_eq!(lock_state(&lock, &d)["state"], "free");
        fs::write(&lock, "{ cut").unwrap();
        assert_eq!(lock_state(&lock, &d)["state"], "damaged");
        fs::write(
            &lock,
            r#"{"op":"20261111T120000Z-ab12cd34","pid":4242,"start_time":7,"boot_id":"b"}"#,
        )
        .unwrap();
        let held = lock_state(&lock, &d);
        assert_eq!(held["state"], "held");
        // no /proc entry for the pid under this root: the holder is gone
        assert_eq!(held["liveness"], "gone");
        assert!(lock.exists(), "reading the lock does not remove it");
        let _ = fs::remove_dir_all(&d);
    }

    /// The plan is sealed over the host's machine id; a host without one gets no plan.
    #[test]
    fn a_plan_without_machine_id_is_not_sealed() {
        let d = scratch("seal");
        let mut doc = plan_doc("new.example.test", false, &host());
        assert!(seal_plan(&mut doc, "20261111T120000Z-ab12cd34", &d.join("machine-id")).is_err());
        assert!(doc.get("plan_sha256").is_none());
        fs::write(d.join("machine-id"), "0123456789abcdef0123456789abcdef\n").unwrap();
        let mut doc = plan_doc("new.example.test", false, &host());
        seal_plan(&mut doc, "20261111T120000Z-ab12cd34", &d.join("machine-id")).unwrap();
        assert_eq!(doc["op"], "20261111T120000Z-ab12cd34");
        assert_eq!(doc["plan_sha256"].as_str().unwrap().len(), 64);
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn nothing_but_a_plan() {
        let args = |a: &[&str]| a.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>();
        assert!(cmd(&args(&["new.example.test"]))
            .unwrap_err()
            .contains("only --plan"));
        assert!(cmd(&args(&["New.example.test", "--plan"])).is_err());
        assert!(cmd(&args(&["--plan"])).is_err());
        assert!(cmd(&args(&["new.example.test", "--plan", "--force"])).is_err());
    }

    /// E7: no customer named, or records that could not be read, is "could not tell";
    /// the plan stops before the clone and says why.
    #[test]
    fn a_quota_that_cannot_be_told_stops_at_quota_unknown() {
        let mut seen = host();
        seen.tenant = None;
        let doc = plan_doc("www.new.example.test", false, &seen);
        assert_eq!(doc["stop"], "quota_unknown");
        assert_eq!(doc["next"], "stop");
        assert_eq!(doc["may_call_off"], false);
        assert_eq!(doc["quota"]["unknown"], "no_tenant");
        let mut seen = host();
        seen.creating = None;
        let doc = plan_doc("www.new.example.test", false, &seen);
        assert_eq!(doc["stop"], "quota_unknown");
        assert_eq!(doc["quota"]["unknown"], "records_unreadable");
    }

    /// E7: a customer whose sites (live and opening) leave no room for one more on the
    /// starting tier stops at quota_exceeded; removed sites are not counted.
    #[test]
    fn a_full_plan_stops_at_quota_exceeded_and_removed_sites_do_not_count() {
        let mine = |code: &str, ct: u32, ip: u8| {
            let mut r = rec(code, ct, ip, &format!("{code}.example.test"));
            r["tenant_id"] = json!("acme");
            r["limits"] = json!({
                "cores": 2, "cpulimit_x100": 200, "memory_mb": 1024, "swap_mb": 512, "pids_max": 2048
            });
            r
        };
        let mut seen = host();
        seen.records = Some(vec![
            mine("sa", 113, 13),
            mine("sb", 114, 14),
            mine("sc", 115, 15),
        ]);
        // a removed site of the customer holds nothing
        seen.removed = Some(vec![mine("sr", 116, 16)]);
        let doc = plan_doc("www.new.example.test", false, &seen);
        assert_eq!(doc["stop"], Value::Null, "{doc}");
        assert_eq!(doc["quota"]["sites"]["live"], 3);
        assert_eq!(doc["new_tenant"], false);
        // an opening under way holds the last room
        let opening = json!({
            "site": "sd", "ct": 117, "ip": "10.77.0.17", "hosts": "sd.example.test", "step": "clone"
        });
        seen.creating = Some(vec![opening]);
        let doc = plan_doc("www.new.example.test", false, &seen);
        assert_eq!(doc["stop"], "quota_exceeded");
        assert_eq!(doc["next"], "stop");
        assert_eq!(doc["quota"]["verdict"], "quota_exceeded");
        assert_eq!(doc["quota"]["short_of"], "cpu");
        assert_eq!(doc["quota"]["sites"]["opening"], 1);
        assert_eq!(doc["quota"]["sites"]["allowed"], 4);
    }

    #[test]
    fn tenant_is_an_argument_with_a_value() {
        let args = ["shop.example.test", "--plan", "--tenant"].map(String::from);
        assert!(cmd(&args).is_err());
    }
}
