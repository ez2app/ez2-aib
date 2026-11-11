// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host site create <domain> [--blank] --plan` — the read-only half of opening a
//! site. It checks the domain, reads every number in use on this host and every removed
//! site's numbers, looks for a site that already serves the domain, reads who the first
//! administrators are, the host lock and the unfinished operations, probes the nine steps
//! of [`aib_cloud::sitecreate`] and prints what would be done next, sealed like every other
//! plan. `--plan` changes nothing.
//!
//! `--apply <plan.json>` (B1) is the first writing step: under the host lock it makes the
//! plan again, which must hash the same as the plan given (same host, same version, same
//! operation id), then runs the clone as that operation and nothing after it. The numbers
//! are held by a record under [`CREATING`] from before the clone; a failed clone is undone.
//! The later steps (limits and network onwards) are the next slices.
//!
//! Every judgement is the rule crate's: the order and the next step
//! ([`sitecreate::next`]), what a failure leaves ([`sitecreate::after_failure`]), the
//! numbers ([`sitecreate::next_numbers`]), the title and the login names
//! ([`aib_cloud::sitedefaults`]). This file only reads. Whatever could not be read is
//! "could not tell" and stops the plan: a list that could not be read is not an empty list.
//!
//! The administrators' mail addresses are host configuration ([`SITE_ADMINS`], one address
//! per line, `#` for comments); they are never written into this repository. The plan
//! prints the login names only.

use std::fs;
use std::path::Path;
use std::process::Command;
use std::time::Duration;

use aib_cloud::edge::valid_domain;
use aib_cloud::sitecreate::{self, AfterFailure, CreateStep, Next, Probe, STEPS};
use aib_cloud::sitedefaults::{admin_login, site_title};
use serde_json::{json, Value};

use crate::opstore;
use crate::probe::run_capture;

const RECORDS: &str = "/var/lib/ez2-cloud/sites";
const REMOVED_RECORDS: &str = "/var/lib/ez2-cloud/sites-removed";
/// The records of sites whose opening has begun and not ended: written before the clone,
/// so the number and the address are taken even while the new container's configuration
/// does not name the address yet (the network is set by the step after the clone).
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
    /// The records of sites being opened ([`CREATING`]): their numbers are taken from the
    /// moment the clone is decided, before any guest configuration names the address.
    pub creating: Option<Vec<Value>>,
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

/// The longest target the operation journal takes ([`aib_cloud::op::Entry::validate`]).
const TARGET_MAX: usize = 80;

/// The target of opening the site at `domain` in the operation journal: `domain:<domain>`
/// while that fits the journal's 80 characters, else `domain:fnv64-<16 hex>` of the domain.
///
/// Why not the site code: it is made at creation, so two openings of the same domain would
/// have two targets and the journal could not hold the second back while the first is
/// unfinished. Why a hash and not the first 73 characters: two long domains sharing their
/// start would always share a target. The hash is FNV-1a 64 (no hashing dependency in this
/// crate, and `sha256sum` would make the plan fail where it is missing); a collision only
/// holds back a second opening while the first is unfinished, it lets nothing through.
/// A domain always has a dot, `fnv64-…` never does: the two forms cannot meet.
pub fn target(domain: &str) -> String {
    let plain = format!("domain:{domain}");
    if plain.len() <= TARGET_MAX {
        return plain;
    }
    format!("domain:fnv64-{}", fnv1a64_hex(domain.as_bytes()))
}

/// FNV-1a, 64 bits, as 16 lower hex digits.
fn fnv1a64_hex(bytes: &[u8]) -> String {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in bytes {
        h ^= u64::from(*b);
        h = h.wrapping_mul(0x0000_0100_0000_01b3);
    }
    format!("{h:016x}")
}

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
    let creating = seen.creating.as_ref().ok_or("numbers_unknown")?;
    for rec in records.iter().chain(creating) {
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
    // a site being opened serves nobody yet, but its domain is taken all the same
    let live: Vec<Value> = seen
        .records
        .iter()
        .chain(&seen.creating)
        .flatten()
        .cloned()
        .collect();
    let taken = taken_by(domain, &live, seen.entry_config.as_deref());
    let next = sitecreate::next(&probes, blank);
    // a stop of this command comes before the order's own answer
    let own_stop: Option<&'static str> = if taken.is_some() {
        Some("domain_taken")
    } else if seen.unfinished.as_ref().is_some_and(|u| !u.is_empty()) {
        Some("unfinished_operation")
    } else if seen.unfinished.is_none() {
        Some("journal_unreadable")
    } else if let Err(w) = &logins {
        Some(*w)
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
        "quota": "not checked yet",
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
        "not_looked_at": ["the customer's quota of sites (no rule for it yet)", "DNS of the domain", "the licences left at the vendors"],
    })
}

/// Put the operation id in and seal. A host whose machine id cannot be read gets no plan:
/// a hash over a stand-in would match every host that cannot be read.
pub fn seal_plan(doc: &mut Value, op: &str, machine_id: &Path) -> Result<(), String> {
    let fingerprint = opstore::host_fingerprint(machine_id)?;
    crate::quarantinecmd::seal_with_op(doc, op, &fingerprint, env!("CARGO_PKG_VERSION"))
}

// ------------------------------------------------------------------ apply (B1: the clone)

/// The command name in the operation journal.
pub const COMMAND: &str = "site.create";
/// What a code of a new site looks like: `s-` and six of these.
const CODE_ALPHABET: &[u8; 36] = b"abcdefghijklmnopqrstuvwxyz0123456789";
/// How many random codes are tried before giving up (each is checked against every live,
/// removed and opening site).
const CODE_TRIES: usize = 8;

/// Runs one host program. The host's is [`HostRunner`]; tests hand in a recorder.
pub trait Runner {
    /// The program's standard output, or why it did not succeed.
    fn run(&mut self, argv: &[String]) -> Result<String, String>;
}

/// The real host: the program with a time limit, its whole process group killed on timeout.
pub struct HostRunner;

impl Runner for HostRunner {
    fn run(&mut self, argv: &[String]) -> Result<String, String> {
        let (program, args) = argv.split_first().ok_or("an empty command")?;
        let mut cmd = Command::new(program);
        cmd.args(args);
        // a full clone copies the template's disk
        let seconds = if args.first().is_some_and(|a| a == "clone") {
            900
        } else {
            60
        };
        run_capture(&mut cmd, Duration::from_secs(seconds))
    }
}

/// A code for a new site: `s-` and six letters or digits from four random bytes.
pub fn code_from(random: [u8; 4]) -> String {
    let mut n = u32::from_le_bytes(random);
    let mut out = String::from("s-");
    for _ in 0..6 {
        out.push(char::from(CODE_ALPHABET[(n % 36) as usize]));
        n /= 36;
    }
    out
}

/// The codes in use: live, removed and opening sites. A code is never given twice.
fn codes_in_use(seen: &Seen) -> Vec<String> {
    [&seen.records, &seen.removed, &seen.creating]
        .into_iter()
        .flatten()
        .flatten()
        .filter_map(|r| r.get("site").and_then(Value::as_str).map(str::to_owned))
        .collect()
}

/// A new code that passes [`aib_cloud::site::valid_code`] and is not in `taken`.
pub fn new_code(
    taken: &[String],
    mut random: impl FnMut() -> Result<[u8; 4], String>,
) -> Result<String, String> {
    for _ in 0..CODE_TRIES {
        let code = code_from(random()?);
        if aib_cloud::site::valid_code(&code) && !taken.contains(&code) {
            return Ok(code);
        }
    }
    Err("code_unavailable: no free site code after several tries".to_owned())
}

/// What the plan given to `--apply` says, checked before anything is read.
pub struct Given {
    pub op: String,
    pub sha: String,
    pub blank: bool,
    /// The host lock as the plan saw it. Under the apply the lock is this operation's own,
    /// so the lock is put back as the plan had it before the hash is recomputed; the lock
    /// stops nothing in the plan (it is only shown), the apply takes it for real.
    pub lock: Value,
    /// The plan as given, whole: every field must be what this host makes again, not only
    /// the hash (an edited field under the old hash is refused too).
    pub plan: Value,
}

/// Read the plan given to `--apply` for `domain`.
pub fn given(plan: &Value, domain: &str) -> Result<Given, String> {
    if plan.get("plan").and_then(Value::as_str) != Some("site-create") {
        return Err("not a site-create plan".to_owned());
    }
    if plan.get("domain").and_then(Value::as_str) != Some(domain) {
        return Err(format!("the plan is for another domain than {domain}"));
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
        blank: plan
            .get("blank")
            .and_then(Value::as_bool)
            .ok_or("the plan has no blank")?,
        lock: plan.get("lock").cloned().ok_or("the plan has no lock")?,
        plan: plan.clone(),
    })
}

/// What the prepare hands the clone.
pub struct CloneInput {
    pub code: String,
    pub ct: u32,
    pub ip: u8,
    pub blank: bool,
}

/// The plan as this host makes it now, under the lock, checked against the plan given:
/// the same hash, and its next step is the clone. Returns the plan as journaled (unsealed,
/// with its op) and what the clone needs.
pub fn recheck(
    domain: &str,
    g: &Given,
    seen: &Seen,
    fingerprint: &str,
    random: impl FnMut() -> Result<[u8; 4], String>,
) -> Result<(Value, CloneInput), String> {
    let mut doc = plan_doc(domain, g.blank, seen);
    if let Some(obj) = doc.as_object_mut() {
        obj.insert("lock".to_owned(), g.lock.clone());
    }
    crate::quarantinecmd::seal_with_op(&mut doc, &g.op, fingerprint, env!("CARGO_PKG_VERSION"))?;
    if doc.get("plan_sha256").and_then(Value::as_str) != Some(g.sha.as_str()) || doc != g.plan {
        return Err(
            "plan_changed: this host is not as the plan saw it (or the plan was edited); make a new plan"
                .to_owned(),
        );
    }
    if doc["next"] != "run" || doc["next_step"] != "clone" {
        return Err(format!(
            "this apply only clones; the plan says {} {}",
            doc["next"], doc["next_step"]
        ));
    }
    let ct = doc["ct"]
        .as_u64()
        .and_then(|n| u32::try_from(n).ok())
        .ok_or("the plan has no container number")?;
    let ip = doc["ip"]
        .as_str()
        .and_then(|a| a.strip_prefix(NET))
        .and_then(|o| o.parse().ok())
        .ok_or("the plan has no address")?;
    let code = new_code(&codes_in_use(seen), random)?;
    Ok((
        opstore::unsealed(&doc),
        CloneInput {
            code,
            ct,
            ip,
            blank: g.blank,
        },
    ))
}

/// Whether the container `ct` exists: `Some(false)` only when `pct status` says its
/// configuration does not exist; any other failure is "could not tell".
fn ct_exists(runner: &mut impl Runner, ct: u32) -> Option<bool> {
    match runner.run(&argv(&["pct", "status", &ct.to_string()])) {
        Ok(_) => Some(true),
        Err(e) if e.contains("does not exist") => Some(false),
        Err(_) => None,
    }
}

fn argv(parts: &[&str]) -> Vec<String> {
    parts.iter().map(|s| (*s).to_owned()).collect()
}

/// Write `doc` to `dir/<code>.json` whole: a temporary file, synced, then renamed.
fn write_record(dir: &Path, code: &str, doc: &Value) -> Result<(), String> {
    fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    let path = dir.join(format!("{code}.json"));
    let tmp = dir.join(format!(".{code}.json.tmp"));
    let text = serde_json::to_string_pretty(doc).map_err(|e| e.to_string())?;
    fs::write(&tmp, text + "\n")
        .and_then(|()| fs::File::open(&tmp)?.sync_all())
        .and_then(|()| fs::rename(&tmp, &path))
        .map_err(|e| format!("{}: {e}", path.display()))
}

/// The clone, the first of the nine steps (B1). In this order: the number must still be
/// free in both guest lists (a number taken by something else is not ours: nothing is made
/// and nothing removed); the opening's record is written, so the number and the address
/// stay taken whatever happens next; `pct clone`; the container must then answer
/// `pct status`. A failure after the record is undone ([`sitecreate::after_failure`] of
/// the clone is `Undo`): the container is destroyed only when it exists — this opening saw
/// the number free under the lock — and must then be gone; the record is removed last.
/// An undo that could not finish keeps the record and says so: a person looks.
pub fn clone_step(
    runner: &mut impl Runner,
    creating: &Path,
    domain: &str,
    op: &str,
    input: &CloneInput,
) -> Result<Value, String> {
    let ct = input.ct;
    for list in ["pct", "qm"] {
        let ids = runner
            .run(&argv(&[list, "list"]))
            .ok()
            .as_deref()
            .and_then(guest_ids)
            .ok_or("numbers_unknown: a guest list could not be read; nothing was made")?;
        if ids.contains(&ct) {
            return Err(format!(
                "number_taken: guest {ct} exists; nothing was made, nothing removed"
            ));
        }
    }
    let spec = aib_cloud::site::SiteSpec::new(
        &input.code,
        ct,
        input.ip,
        &[domain],
        !input.blank,
        "zh_TW",
        aib_cloud::site::Limits::START,
    )
    .map_err(|e| format!("{e:?}"))?;
    let record = json!({
        "site": input.code,
        "ct": ct,
        "ip": format!("{NET}{}", input.ip),
        "hosts": domain,
        "blank": input.blank,
        "op": op,
        "step": CreateStep::Clone.as_str(),
    });
    write_record(creating, &input.code, &record)?;
    let clone = aib_cloud::site::plan(&spec)
        .into_iter()
        .find(|s| s.id == "clone")
        .ok_or("the provisioning plan has no clone")?;
    let failure = match runner.run(&clone.argv) {
        Ok(_) => match ct_exists(runner, ct) {
            Some(true) => return Ok(record),
            _ => format!("clone_failed: CT {ct} does not answer after the clone"),
        },
        Err(e) => format!("clone_failed: {e}"),
    };
    Err(undo_clone(runner, creating, &input.code, ct, &failure))
}

/// Undo a failed clone; the answer is the whole error.
fn undo_clone(
    runner: &mut impl Runner,
    creating: &Path,
    code: &str,
    ct: u32,
    failure: &str,
) -> String {
    let record = creating.join(format!("{code}.json"));
    let drop_record = || fs::remove_file(&record).map_err(|e| format!("{}: {e}", record.display()));
    match ct_exists(runner, ct) {
        Some(false) => match drop_record() {
            Ok(()) => format!("{failure}; undone: nothing was made"),
            Err(e) => format!("{failure}; nothing was made, but the opening record stays: {e}"),
        },
        None => format!(
            "{failure}; undo stopped: whether CT {ct} exists could not be told, it and {} are left for a person",
            record.display()
        ),
        Some(true) => {
            let destroyed = runner.run(&argv(&["pct", "destroy", &ct.to_string(), "--purge", "1"]));
            match (destroyed, ct_exists(runner, ct)) {
                (Ok(_), Some(false)) => match drop_record() {
                    Ok(()) => format!("{failure}; undone: CT {ct} destroyed"),
                    Err(e) => format!("{failure}; CT {ct} destroyed, but the opening record stays: {e}"),
                },
                (Err(e), _) => format!(
                    "{failure}; undo failed: pct destroy {ct}: {e}; CT {ct} and {} are left for a person",
                    record.display()
                ),
                (Ok(_), _) => format!(
                    "{failure}; undo failed: CT {ct} still answers after pct destroy; it and {} are left for a person",
                    record.display()
                ),
            }
        }
    }
}

/// `--apply`: the plan given is recomputed under the host lock and must hash the same,
/// then the clone runs as operation `op` of the plan. Separate from [`cmd`] so it runs
/// against a scratch place, a made-up host and a recording runner.
pub fn apply(
    place: &opstore::OpPlace,
    plan: &Value,
    domain: &str,
    creating: &Path,
    see: impl FnOnce() -> Seen,
    random: impl FnMut() -> Result<[u8; 4], String>,
    runner: &mut impl Runner,
) -> Result<Value, String> {
    let g = given(plan, domain)?;
    let done = opstore::run_op_prepared_as(
        place,
        &g.op,
        COMMAND,
        &target(domain),
        opstore::BY_LOCAL_ROOT,
        || {
            let seen = see();
            recheck(domain, &g, &seen, &place.fingerprint, random).map(Some)
        },
        |op, input: CloneInput| clone_step(runner, creating, domain, op, &input),
    )?;
    done.ok_or_else(|| "nothing to do".to_owned())
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

fn see(domain: &str, records: &str) -> Seen {
    let proc_root = Path::new("/proc");
    Seen {
        container_list: capture("pct", &["list"], 20),
        vm_list: capture("qm", &["list"], 20),
        guest_ips: read_guest_ips(&[Path::new(LXC_CONFIGS), Path::new(VM_CONFIGS)]),
        records: read_records(Path::new(records)),
        removed: read_records(Path::new(REMOVED_RECORDS)),
        creating: read_records(Path::new(CREATING)),
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
    let mut apply_file: Option<String> = None;
    let mut it = args[1..].iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--plan" => plan_only = true,
            "--blank" => blank = true,
            "--apply" => apply_file = Some(it.next().ok_or("--apply needs a plan file")?.clone()),
            "--records" => records = it.next().ok_or("--records needs a dir")?.clone(),
            other => return Err(format!("unknown argument {other}")),
        }
    }
    match (plan_only, apply_file) {
        (true, None) => {}
        (false, Some(file)) => {
            if blank {
                return Err(
                    "--blank belongs to --plan: the plan given to --apply says it".to_owned(),
                );
            }
            return apply_cmd(&domain, &file, &records);
        }
        (true, Some(_)) => return Err("--plan or --apply, not both".to_owned()),
        (false, None) => {
            return Err(
                "needs --plan, or --apply <plan.json> with a plan this command printed".to_owned(),
            )
        }
    }
    let seen = see(&domain, &records);
    let mut doc = plan_doc(&domain, blank, &seen);
    let op = opstore::op_id(crate::probe::unix_now(), opstore::random4()?);
    seal_plan(&mut doc, &op, Path::new(opstore::MACHINE_ID))?;
    println!(
        "{}",
        serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?
    );
    Ok(0)
}

/// `--apply <file>` on this host. Writing: run only as a reviewed, approved operation
/// (see [`opstore::BY_LOCAL_ROOT`]: until the remote CLI's one-time approvals exist, a
/// write on the host goes through a signed script a person approved).
fn apply_cmd(domain: &str, file: &str, records: &str) -> Result<u8, String> {
    let text = fs::read_to_string(file).map_err(|e| format!("{file}: {e}"))?;
    let plan: Value = serde_json::from_str(&text).map_err(|e| format!("{file}: {e}"))?;
    let place = opstore::OpPlace::host()?;
    let record = apply(
        &place,
        &plan,
        domain,
        Path::new(CREATING),
        || see(domain, records),
        opstore::random4,
        &mut HostRunner,
    )?;
    println!(
        "{}",
        serde_json::to_string_pretty(&json!({
            "applied": "clone",
            "record": record,
            "next_step": CreateStep::Limits.as_str(),
        }))
        .map_err(|e| e.to_string())?
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
        assert_eq!(doc["quota"], "not checked yet");
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

    /// A domain whose plain target would not fit a journal line is planned under its
    /// hashed target, not stopped (33e28 11-11 N2, one target rule with the apply).
    #[test]
    fn a_long_domain_is_planned_under_its_hashed_target() {
        let long = format!("{}.example.test", "a".repeat(63));
        assert!(format!("domain:{long}").len() > TARGET_MAX);
        let doc = plan_doc(&long, false, &host());
        assert_eq!(doc["next"], "run");
        assert_eq!(doc["stop"], Value::Null);
        let t = doc["target"].as_str().unwrap();
        assert!(
            t.starts_with("domain:fnv64-") && t.len() <= TARGET_MAX,
            "{t}"
        );
        let fits = format!("{}.example.test", "a".repeat(40));
        let doc = plan_doc(&fits, false, &host());
        assert_eq!(doc["next"], "run");
        assert_eq!(doc["target"], format!("domain:{fits}"));
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
            .contains("needs --plan"));
        assert!(cmd(&args(&["New.example.test", "--plan"])).is_err());
        assert!(cmd(&args(&["--plan"])).is_err());
        assert!(cmd(&args(&["new.example.test", "--plan", "--force"])).is_err());
    }

    // ------------------------------------------------------------------ apply (B1)

    const OP: &str = "20261111T120000Z-ab12cd34";

    /// A scratch operation place: its own lock, boot id and a `/proc` that knows this test.
    fn place(name: &str) -> opstore::OpPlace {
        let d = scratch(name);
        fs::write(d.join("boot_id"), "0f0e0d0c-0b0a-4908-8706-050403020100\n").unwrap();
        let me = d.join("proc").join(std::process::id().to_string());
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
        fs::create_dir_all(d.join("ops")).unwrap();
        opstore::OpPlace {
            ops_dir: d.join("ops"),
            lock: opstore::LockPaths {
                lock: d.join("host.lock"),
                proc_root: d.join("proc"),
                boot_id: d.join("boot_id"),
            },
            fingerprint: "f".repeat(64),
        }
    }

    /// The plan `--plan` would print on `seen`, sealed for `place`.
    fn sealed(place: &opstore::OpPlace, domain: &str, seen: &Seen) -> Value {
        let mut doc = plan_doc(domain, false, seen);
        crate::quarantinecmd::seal_with_op(
            &mut doc,
            OP,
            &place.fingerprint,
            env!("CARGO_PKG_VERSION"),
        )
        .unwrap();
        doc
    }

    /// A host that answers from a script and remembers every command.
    struct Fake<F: FnMut(&[String]) -> Result<String, String>> {
        calls: Vec<String>,
        answer: F,
    }

    impl<F: FnMut(&[String]) -> Result<String, String>> Runner for Fake<F> {
        fn run(&mut self, argv: &[String]) -> Result<String, String> {
            self.calls.push(argv.join(" "));
            (self.answer)(argv)
        }
    }

    fn fake<F: FnMut(&[String]) -> Result<String, String>>(answer: F) -> Fake<F> {
        Fake {
            calls: Vec::new(),
            answer,
        }
    }

    const GONE: &str = "Configuration file 'nodes/x/lxc/126.conf' does not exist";

    /// The usual host: both lists read and without the new number, the clone works and
    /// the new container answers.
    fn healthy(argv: &[String]) -> Result<String, String> {
        match argv[1].as_str() {
            "list" if argv[0] == "pct" => Ok(PCT.to_owned()),
            "list" => Ok(QM.to_owned()),
            _ => Ok("status: stopped\n".to_owned()),
        }
    }

    fn states(place: &opstore::OpPlace) -> Vec<State> {
        let path = opstore::journal_path(&place.ops_dir, OP).unwrap();
        opstore::read_journal(&path)
            .unwrap()
            .0
            .iter()
            .map(|e| e.state)
            .collect()
    }

    fn random_from(seed: u8) -> impl FnMut() -> Result<[u8; 4], String> {
        let mut n = seed;
        move || {
            n = n.wrapping_add(1);
            Ok([n, 7, 9, 11])
        }
    }

    #[test]
    fn a_long_domain_gets_a_hashed_target_that_fits_the_journal() {
        assert_eq!(target("new.example.test"), "domain:new.example.test");
        let long = format!("{}.example.test", "a".repeat(70));
        let other = format!("{}.example.test", "b".repeat(70));
        let t = target(&long);
        assert!(
            t.starts_with("domain:fnv64-") && t.len() <= TARGET_MAX,
            "{t}"
        );
        assert_eq!(t, target(&long));
        assert_ne!(t, target(&other));
        // the journal takes it
        let e = Entry {
            op: OP.to_owned(),
            seq: 1,
            at: 1,
            state: State::Planned,
            command: COMMAND.to_owned(),
            target: t,
            plan_sha256: "a".repeat(64),
            code: "ok".to_owned(),
            approval: String::new(),
        };
        assert!(e.validate().is_ok());
        // exactly at the edge the plain form is kept
        let edge = format!("{}.test", "c".repeat(TARGET_MAX - "domain:".len() - 5));
        assert_eq!(target(&edge), format!("domain:{edge}"));
    }

    #[test]
    fn a_code_is_new_and_valid() {
        let c = code_from([1, 2, 3, 4]);
        assert!(c.starts_with("s-") && c.len() == 8 && aib_cloud::site::valid_code(&c));
        let taken = vec![code_from([1, 7, 9, 11])];
        let mut tries = 0;
        let code = new_code(&taken, || {
            tries += 1;
            Ok([tries, 7, 9, 11])
        })
        .unwrap();
        assert_ne!(code, taken[0]);
        assert!(new_code(&taken, || Ok([1, 7, 9, 11]))
            .unwrap_err()
            .starts_with("code_unavailable"));
    }

    /// A site being opened holds its numbers and its domain.
    #[test]
    fn an_opening_holds_its_numbers_and_its_domain() {
        let free = plan_doc("new.example.test", false, &host());
        let seen = Seen {
            creating: Some(vec![rec("s-abc123", 131, 41, "new.example.test")]),
            ..host()
        };
        let doc = plan_doc("other.example.test", false, &seen);
        assert_ne!(doc["ct"], free["ct"]);
        assert_ne!(doc["ip"], free["ip"]);
        let doc = plan_doc("new.example.test", false, &seen);
        assert_eq!(doc["stop"], "domain_taken");
        assert_eq!(doc["taken_by"], "s-abc123");
        let unread = Seen {
            creating: None,
            ..host()
        };
        assert_eq!(
            plan_doc("other.example.test", false, &unread)["stop"],
            "numbers_unknown"
        );
    }

    #[test]
    fn an_approved_plan_clones_once() {
        let place = place("apply");
        let creating = place.ops_dir.with_file_name("creating");
        let plan = sealed(&place, "new.example.test", &host());
        let ct = plan["ct"].as_u64().unwrap();
        let mut runner = fake(healthy);
        let rec = apply(
            &place,
            &plan,
            "new.example.test",
            &creating,
            host,
            random_from(0),
            &mut runner,
        )
        .unwrap();
        assert_eq!(rec["ct"], ct);
        assert_eq!(rec["ip"], plan["ip"]);
        assert_eq!(rec["op"], OP);
        let code = rec["site"].as_str().unwrap().to_owned();
        assert_eq!(
            runner.calls,
            [
                "pct list".to_owned(),
                "qm list".to_owned(),
                format!("pct clone 900 {ct} --hostname {code} --full 1"),
                format!("pct status {ct}"),
            ]
        );
        let on_disk: Value = serde_json::from_str(
            &fs::read_to_string(creating.join(format!("{code}.json"))).unwrap(),
        )
        .unwrap();
        assert_eq!(on_disk, rec);
        assert_eq!(
            states(&place),
            [
                State::Planned,
                State::Approved,
                State::Running,
                State::Verified
            ]
        );
        // the journal's plan hash is the plan's
        let path = opstore::journal_path(&place.ops_dir, OP).unwrap();
        let first = &opstore::read_journal(&path).unwrap().0[0];
        assert_eq!(first.plan_sha256, plan["plan_sha256"].as_str().unwrap());
        assert_eq!(first.target, "domain:new.example.test");
        // the same plan again: refused, nothing run
        let mut again = fake(healthy);
        assert!(apply(
            &place,
            &plan,
            "new.example.test",
            &creating,
            host,
            random_from(0),
            &mut again
        )
        .is_err());
        assert!(again.calls.is_empty());
        assert!(!place.lock.lock.exists(), "the lock is given back");
    }

    #[test]
    fn a_host_that_changed_refuses_the_plan() {
        let place = place("changed");
        let creating = place.ops_dir.with_file_name("creating");
        let plan = sealed(&place, "new.example.test", &host());
        let later = || Seen {
            records: Some(vec![
                rec("site2", 112, 12, "shop.example.test"),
                rec("site9", 140, 41, "late.example.test"),
            ]),
            ..host()
        };
        let mut runner = fake(healthy);
        let err = apply(
            &place,
            &plan,
            "new.example.test",
            &creating,
            later,
            random_from(0),
            &mut runner,
        )
        .unwrap_err();
        assert!(err.starts_with("plan_changed"), "{err}");
        assert!(runner.calls.is_empty());
        assert!(!opstore::journal_path(&place.ops_dir, OP).unwrap().exists());
        // an edited plan is refused the same way
        let mut edited = plan.clone();
        edited["ct"] = json!(150);
        let err = apply(
            &place,
            &edited,
            "new.example.test",
            &creating,
            host,
            random_from(0),
            &mut runner,
        )
        .unwrap_err();
        assert!(err.starts_with("plan_changed"), "{err}");
        // a plan for another domain is not this one's
        assert!(apply(
            &place,
            &plan,
            "else.example.test",
            &creating,
            host,
            random_from(0),
            &mut runner
        )
        .is_err());
        assert!(runner.calls.is_empty());
    }

    #[test]
    fn a_number_taken_since_makes_and_removes_nothing() {
        let place = place("taken");
        let creating = place.ops_dir.with_file_name("creating");
        let plan = sealed(&place, "new.example.test", &host());
        let ct = plan["ct"].as_u64().unwrap();
        let mut runner = fake(
            |argv: &[String]| match (argv[0].as_str(), argv[1].as_str()) {
                ("pct", "list") => Ok(format!(
                    "{PCT}{ct}        stopped                 intruder\n"
                )),
                ("qm", "list") => Ok(QM.to_owned()),
                _ => Ok(String::new()),
            },
        );
        let err = apply(
            &place,
            &plan,
            "new.example.test",
            &creating,
            host,
            random_from(0),
            &mut runner,
        )
        .unwrap_err();
        assert!(err.starts_with("number_taken"), "{err}");
        assert_eq!(runner.calls, ["pct list"]);
        assert!(!creating.exists() || fs::read_dir(&creating).unwrap().next().is_none());
        assert_eq!(states(&place).last(), Some(&State::Failed));
    }

    #[test]
    fn a_failed_clone_is_undone() {
        let place = place("undo");
        let creating = place.ops_dir.with_file_name("creating");
        let plan = sealed(&place, "new.example.test", &host());
        let ct = plan["ct"].as_u64().unwrap();
        // half a clone: it fails, the container answers, is destroyed, then is gone
        let mut destroyed = false;
        let mut runner = fake(move |argv: &[String]| match argv[1].as_str() {
            "list" if argv[0] == "pct" => Ok(PCT.to_owned()),
            "list" => Ok(QM.to_owned()),
            "clone" => Err("pct clone: storage full".to_owned()),
            "destroy" => {
                destroyed = true;
                Ok(String::new())
            }
            _ if destroyed => Err(GONE.to_owned()),
            _ => Ok("status: stopped\n".to_owned()),
        });
        let err = apply(
            &place,
            &plan,
            "new.example.test",
            &creating,
            host,
            random_from(0),
            &mut runner,
        )
        .unwrap_err();
        assert!(err.contains(&format!("undone: CT {ct} destroyed")), "{err}");
        assert!(runner
            .calls
            .contains(&format!("pct destroy {ct} --purge 1")));
        assert!(
            fs::read_dir(&creating).unwrap().next().is_none(),
            "the record is removed"
        );
        assert_eq!(states(&place).last(), Some(&State::Failed));
    }

    #[test]
    fn an_undo_that_cannot_tell_leaves_everything_for_a_person() {
        let place = place("unknown");
        let creating = place.ops_dir.with_file_name("creating");
        let plan = sealed(&place, "new.example.test", &host());
        let mut runner = fake(|argv: &[String]| match argv[1].as_str() {
            "list" if argv[0] == "pct" => Ok(PCT.to_owned()),
            "list" => Ok(QM.to_owned()),
            "clone" => Err("pct clone: timed out".to_owned()),
            _ => Err("pct status: timed out".to_owned()),
        });
        let err = apply(
            &place,
            &plan,
            "new.example.test",
            &creating,
            host,
            random_from(0),
            &mut runner,
        )
        .unwrap_err();
        assert!(err.contains("left for a person"), "{err}");
        assert!(!runner.calls.iter().any(|c| c.contains("destroy")));
        assert_eq!(
            fs::read_dir(&creating).unwrap().count(),
            1,
            "the record stays"
        );
    }

    #[test]
    fn a_clone_that_made_nothing_only_drops_the_record() {
        let place = place("nothing");
        let creating = place.ops_dir.with_file_name("creating");
        let plan = sealed(&place, "new.example.test", &host());
        let mut runner = fake(|argv: &[String]| match argv[1].as_str() {
            "list" if argv[0] == "pct" => Ok(PCT.to_owned()),
            "list" => Ok(QM.to_owned()),
            "clone" => Err("pct clone: template locked".to_owned()),
            _ => Err(GONE.to_owned()),
        });
        let err = apply(
            &place,
            &plan,
            "new.example.test",
            &creating,
            host,
            random_from(0),
            &mut runner,
        )
        .unwrap_err();
        assert!(err.contains("undone: nothing was made"), "{err}");
        assert!(!runner.calls.iter().any(|c| c.contains("destroy")));
        assert!(fs::read_dir(&creating).unwrap().next().is_none());
    }
}
