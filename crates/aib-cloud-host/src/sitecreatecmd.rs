// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host site create <domain> [--blank] [--tenant <id>] --plan` — the read-only half of opening a
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
//! The plan carries the customer's quota, so `--apply` must name the same customer
//! (`--tenant`) as the plan did: without it the plan made again would stop at
//! `quota_unknown`; a different one is refused as `tenant_mismatch` (c477a, E7 N2).
//!
//! `--continue <code>` (B2) carries an opening on from its record: limits and network,
//! the firewall file, the start — each checked after it is done, the record's `step` and
//! `at` moved on after each. These three are still undone on failure (stop, unlock,
//! destroy, drop the record).
//! B3 carries on with the install and the identity ([`crate::siteidentity`]): the
//! reviewed installer, the site software's scheduler on a timer, keys and salts anew, the
//! address and title, the first administrators with passwords kept on the host only.
//! `--continue` runs up to the identity; the licences and after are the next slices.
//! `op status` lists every opening record with the step it reached, so a half-made
//! container is never hidden behind a `verified` clone (c477a, B1 L1).
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
use std::path::{Path, PathBuf};
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
/// The records of sites whose opening has begun and not ended: written before the clone,
/// so the number and the address are taken even while the new container's configuration
/// does not name the address yet (the network is set by the step after the clone).
/// They also count against the customer's quota (E7); a missing directory is none.
pub const CREATING: &str = "/var/lib/ez2-cloud/sites-creating";
/// The opening records of the sites that opened, kept beside their full records (B4).
pub const OPENED: &str = "/var/lib/ez2-cloud/sites-opened";
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
    let quota = quota::judge(
        seen.tenant.as_deref(),
        seen.records.as_deref(),
        seen.creating.as_deref(),
    );
    // a stop of this command comes before the order's own answer
    let own_stop: Option<&'static str> = if taken.is_some() {
        Some("domain_taken")
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
    /// The same with `input` on the program's standard input: what must never be in an
    /// argument list (a password) or is a whole file (a unit file). A runner that cannot
    /// feed a program says so, and the step stops.
    fn run_input(&mut self, argv: &[String], input: &[u8]) -> Result<String, String> {
        let _ = (argv, input);
        Err("this runner cannot feed a program".to_owned())
    }
    /// `n` random bytes, from the system's source.
    fn random(&mut self, n: usize) -> Result<Vec<u8>, String> {
        let mut buf = vec![0u8; n];
        let mut f = fs::File::open("/dev/urandom").map_err(|e| format!("/dev/urandom: {e}"))?;
        std::io::Read::read_exact(&mut f, &mut buf).map_err(|e| format!("/dev/urandom: {e}"))?;
        Ok(buf)
    }
}

/// What a program inside a container may say before it is not believed: the container is
/// the customer's, and so is whatever answers there (ebb36, wpress r2 M1).
pub const CT_OUT_MAX: usize = 64 * 1024;

/// The real host: the program with a time limit, its whole process group killed on timeout.
pub struct HostRunner;

impl HostRunner {
    /// A full clone copies the template's disk and the installer sets up a whole site;
    /// everything else is short.
    fn seconds(argv: &[String]) -> u64 {
        let long = argv.get(1).is_some_and(|a| a == "clone")
            || argv.iter().any(|a| a == crate::siteidentity::SETUP_IN_CT);
        if long {
            900
        } else {
            60
        }
    }
}

impl Runner for HostRunner {
    fn run(&mut self, argv: &[String]) -> Result<String, String> {
        let (program, args) = argv.split_first().ok_or("an empty command")?;
        let mut cmd = Command::new(program);
        cmd.args(args);
        let timeout = Duration::from_secs(Self::seconds(argv));
        if args.first().is_some_and(|a| a == "exec") {
            // inside the container: read with a cap, no input
            return crate::probe::run_bounded_input(&mut cmd, timeout, CT_OUT_MAX, b"");
        }
        run_capture(&mut cmd, timeout)
    }

    fn run_input(&mut self, argv: &[String], input: &[u8]) -> Result<String, String> {
        let (program, args) = argv.split_first().ok_or("an empty command")?;
        let mut cmd = Command::new(program);
        cmd.args(args);
        let timeout = Duration::from_secs(Self::seconds(argv));
        crate::probe::run_bounded_input(&mut cmd, timeout, CT_OUT_MAX, input)
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
    /// The customer the plan was made for (`quota.tenant_id`), if it named one.
    pub tenant: Option<String>,
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
        tenant: plan["quota"]["tenant_id"].as_str().map(str::to_owned),
    })
}

/// `--apply` names the customer the plan was made for (c477a, E7 N2): the plan is made
/// again for that customer, so a missing `--tenant` would only end in `plan_changed`, and a
/// different one must never apply a plan judged for someone else's quota. Fixed words:
/// `tenant_missing`, `tenant_mismatch`.
pub fn tenant_matches(g: &Given, tenant: Option<&str>) -> Result<(), String> {
    match (g.tenant.as_deref(), tenant) {
        (_, None) => Err(
            "tenant_missing: --apply needs --tenant, the customer the plan was made for"
                .to_owned(),
        ),
        (Some(planned), Some(t)) if planned == t => Ok(()),
        _ => Err(
            "tenant_mismatch: --tenant is not the customer the plan was made for; make a new plan"
                .to_owned(),
        ),
    }
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
        "started_at": crate::probe::unix_now(),
        "at": crate::probe::unix_now(),
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
    Err(undo_opening(runner, creating, &input.code, ct, &failure))
}

/// Undo a failed opening step (clone, limits, firewall, start: all
/// [`AfterFailure::Undo`]); the answer is the whole error.
fn undo_opening(
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
            // a container left running (B2) cannot be destroyed, and a clone cut off by its
            // time limit leaves `lock: create` on it (c477a, B1 N): both are cleared first;
            // their own failures do not matter, the destroy and the look after it decide
            let n = ct.to_string();
            let _ = runner.run(&argv(&["pct", "stop", &n]));
            let _ = runner.run(&argv(&["pct", "unlock", &n]));
            let destroyed = runner.run(&argv(&["pct", "destroy", &n, "--purge", "1"]));
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

// ------------------------------------------------------------------ continue (B2)

/// Where B2 reads and writes on the host; tests hand in scratch directories.
pub struct HostPaths {
    /// The opening records ([`CREATING`]).
    pub creating: PathBuf,
    /// The containers' configuration files (`/etc/pve/lxc`).
    pub lxc_conf: PathBuf,
    /// The containers' firewall files (`/etc/pve/firewall`).
    pub firewall: PathBuf,
    /// Where each site's own secrets live on the host (`/etc/ez2-cloud/sites`): B3 writes
    /// the first administrators' passwords there, root only.
    pub secrets: PathBuf,
    /// The first administrators' addresses ([`SITE_ADMINS`]).
    pub site_admins: PathBuf,
    /// The reviewed site installer on the host, pushed into the container for the install.
    pub setup: PathBuf,
    /// The site records (`/var/lib/ez2-cloud/sites`): B4 writes the new site's there, and
    /// asks every other site in them through the entry.
    pub records: PathBuf,
    /// The export list ([`crate::sitelive::SITES_LIST`]).
    pub sites_list: PathBuf,
    /// The template's paid plugin keys ([`crate::sitelive::KEYS`]), read into memory only.
    pub keys: PathBuf,
    /// The opening records of opened sites ([`OPENED`]).
    pub opened: PathBuf,
}

impl HostPaths {
    pub fn host() -> Self {
        HostPaths {
            creating: PathBuf::from(CREATING),
            lxc_conf: PathBuf::from(LXC_CONFIGS),
            firewall: PathBuf::from("/etc/pve/firewall"),
            secrets: PathBuf::from(crate::siteidentity::SECRETS),
            site_admins: PathBuf::from(SITE_ADMINS),
            setup: PathBuf::from(crate::siteidentity::SETUP_ON_HOST),
            records: PathBuf::from(RECORDS),
            sites_list: PathBuf::from(crate::sitelive::SITES_LIST),
            keys: PathBuf::from(crate::sitelive::KEYS),
            opened: PathBuf::from(OPENED),
        }
    }
}

/// Every step `--continue` runs, in order: B2's three, B3's install and identity — the
/// last two that are still undone on failure — and B4's licences, entry and health, from
/// which a failure keeps the site for a person.
pub const CONTINUE_STEPS: [CreateStep; 8] = [
    CreateStep::Limits,
    CreateStep::Firewall,
    CreateStep::Start,
    CreateStep::Install,
    CreateStep::Identity,
    CreateStep::Licenses,
    CreateStep::Edge,
    CreateStep::Health,
];

/// The steps still to do after the step an opening record reached, up to and with
/// `last`. A record at the clone has every one ahead; at `last` or past it, none. Any
/// other word is not a step this command carries on from.
pub fn steps_after(reached: &str, last: CreateStep) -> Result<Vec<CreateStep>, String> {
    let upto: Vec<CreateStep> = CONTINUE_STEPS
        .iter()
        .copied()
        .filter(|s| *s <= last)
        .collect();
    if reached == CreateStep::Clone.as_str() {
        return Ok(upto);
    }
    match CONTINUE_STEPS.iter().position(|s| s.as_str() == reached) {
        Some(i) => Ok(upto.into_iter().skip(i + 1).collect()),
        None => Err(format!(
            "the opening record is at {reached:?}: not a step this command carries on from"
        )),
    }
}

/// An opening record read back whole: what B2 needs from it.
pub struct Opening {
    pub record: Value,
    pub spec: aib_cloud::site::SiteSpec,
}

/// Read `creating/<code>.json` for `domain`. The domain must be the record's first name:
/// a code typed for another domain's opening is refused.
pub fn read_opening(creating: &Path, code: &str, domain: &str) -> Result<Opening, String> {
    if !aib_cloud::site::valid_code(code) {
        return Err(format!("{code:?} is not a site code"));
    }
    let path = creating.join(format!("{code}.json"));
    let text = fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
    let record: Value =
        serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))?;
    if record.get("site").and_then(Value::as_str) != Some(code) {
        return Err(format!("{}: the record is not {code}'s", path.display()));
    }
    let hosts = record
        .get("hosts")
        .and_then(Value::as_str)
        .ok_or("the opening record has no hosts")?;
    if hosts.split(',').next().map(str::trim) != Some(domain) {
        return Err(format!("{code} is the opening of {hosts}, not of {domain}"));
    }
    let (ct, ip) = record_numbers(&record).ok_or("the opening record has no numbers")?;
    let blank = record
        .get("blank")
        .and_then(Value::as_bool)
        .ok_or("the opening record has no blank")?;
    let spec = aib_cloud::site::SiteSpec::new(
        code,
        ct,
        ip,
        &[domain],
        !blank,
        "zh_TW",
        aib_cloud::site::Limits::START,
    )
    .map_err(|e| format!("{e:?}"))?;
    Ok(Opening { record, spec })
}

/// `text` (a container's configuration) with exactly one `lxc.cgroup2.pids.max` line, set
/// to `n`, in the main section: `pct set` does not take raw `lxc.*` keys, so the line is
/// written into the file (the snapshot sections after the first `[` are left alone).
pub fn with_pids(text: &str, n: u32) -> String {
    let mut main = Vec::new();
    let mut rest = Vec::new();
    for line in text.lines() {
        if !rest.is_empty() || line.starts_with('[') {
            rest.push(line);
        } else if !line.starts_with("lxc.cgroup2.pids.max:") {
            main.push(line);
        }
    }
    while main.last().is_some_and(|l| l.trim().is_empty()) {
        main.pop();
    }
    let mut out = String::new();
    for line in &main {
        out.push_str(line);
        out.push('\n');
    }
    out.push_str(&format!("lxc.cgroup2.pids.max: {n}\n"));
    if !rest.is_empty() {
        out.push('\n');
        for line in rest {
            out.push_str(line);
            out.push('\n');
        }
    }
    out
}

/// Limits and network: `pct set` from the provisioning plan, the pids line, then
/// `pct config` must show every limit key as declared.
fn limits_step(
    runner: &mut impl Runner,
    paths: &HostPaths,
    spec: &aib_cloud::site::SiteSpec,
) -> Result<(), String> {
    let set = aib_cloud::site::plan(spec)
        .into_iter()
        .find(|s| s.id == "set")
        .ok_or("the provisioning plan has no set")?;
    runner
        .run(&set.argv)
        .map_err(|e| format!("limits_failed: pct set: {e}"))?;
    let conf = paths.lxc_conf.join(format!("{}.conf", spec.ct));
    let text =
        fs::read_to_string(&conf).map_err(|e| format!("limits_failed: {}: {e}", conf.display()))?;
    write_via_tmp(&conf, &with_pids(&text, spec.limits.pids_max))
        .map_err(|e| format!("limits_failed: {e}"))?;
    // TODO(ct-mask2): the container settings of the host-information mask
    // (aib_cloud::mask, branch feat/aib-cloud-ct-mask2) are written here, in the limits
    // step, once that branch is in main
    let shown = runner
        .run(&argv(&["pct", "config", &spec.ct.to_string()]))
        .map_err(|e| format!("limits_unchecked: pct config: {e}"))?;
    let differ = aib_cloud::site::limits_differ(spec, &shown)
        .map_err(|e| format!("limits_unchecked: {e:?}"))?;
    if differ.is_empty() {
        Ok(())
    } else {
        let keys: Vec<&str> = differ.iter().map(|d| d.key.as_str()).collect();
        Err(format!("limits_not_applied: {}", keys.join(",")))
    }
}

/// Write `text` to `path` whole: a temporary file beside it, then renamed over it, so
/// nobody reads half a file (pmxcfs under /etc/pve takes a rename; c477a B2 N2). PVE's own
/// configuration lock is not taken: the host lock holds our commands back, not the GUI.
fn write_via_tmp(path: &Path, text: &str) -> Result<(), String> {
    let name = path
        .file_name()
        .and_then(|n| n.to_str())
        .ok_or_else(|| format!("{}: no file name", path.display()))?;
    let tmp = path.with_file_name(format!(".{name}.tmp"));
    fs::write(&tmp, text)
        .and_then(|()| fs::rename(&tmp, path))
        .map_err(|e| format!("{}: {e}", path.display()))
}

/// The firewall file, always written (a clone carries the template's) and read back.
///
/// Written is not in force: pve-firewall compiles the file into rules on its own round
/// (about every ten seconds), so the first seconds after the start may still run the
/// template's rules. The container is only on the inner bridge until the Edge step, and
/// the Edge step must not begin before the container's compiled rules are read back
/// (`pve-firewall compile` / `status`; [`CreateStep::Edge`], c477a B2 L1). How long it
/// takes on this host is a host test.
fn firewall_step(paths: &HostPaths, spec: &aib_cloud::site::SiteSpec) -> Result<(), String> {
    let want = aib_cloud::site::desired_firewall(&spec.fw_group, spec.ssh_port);
    let fw = paths.firewall.join(format!("{}.fw", spec.ct));
    write_via_tmp(&fw, &want).map_err(|e| format!("firewall_failed: {e}"))?;
    match fs::read_to_string(&fw) {
        Ok(back) if back == want => Ok(()),
        Ok(_) => Err(format!(
            "firewall_not_written: {} reads back otherwise",
            fw.display()
        )),
        Err(e) => Err(format!("firewall_unchecked: {}: {e}", fw.display())),
    }
}

/// Whether `mac` (`aa:bb:cc:dd:ee:ff`) is a locally administered unicast address: in the
/// first byte the 0x02 bit is set and the 0x01 bit is clear. A platform's prefix (the
/// hosting software's own range) is neither, and a hardware address seen once is seen
/// for good, so the first start must already carry a local one.
/// TODO(ct-mask2): the shared check of the host-information mask replaces this once that
/// branch is in main.
pub fn mac_is_local(mac: &str) -> bool {
    let bytes: Vec<Option<u8>> = mac
        .split(':')
        .map(|b| {
            if b.len() == 2 && b.bytes().all(|c| c.is_ascii_hexdigit()) {
                u8::from_str_radix(b, 16).ok()
            } else {
                None
            }
        })
        .collect();
    let Some(Some(first)) = bytes.first().copied() else {
        return false;
    };
    bytes.len() == 6 && bytes.iter().all(Option::is_some) && first & 0x02 != 0 && first & 0x01 == 0
}

/// The `hwaddr=` of `net0` in `pct config` output (main section only).
pub fn net0_hwaddr(config: &str) -> Option<String> {
    config
        .lines()
        .take_while(|l| !l.starts_with('['))
        .find_map(|l| l.strip_prefix("net0:"))?
        .split(',')
        .find_map(|p| p.trim().strip_prefix("hwaddr="))
        .map(str::to_owned)
}

/// Before the first start: `net0` must carry a locally administered address (c477a B2
/// L2). Until the host-information mask writes one in the limits step, the template's
/// platform address is refused here: nothing is undone and the opening stays at the
/// firewall, to be carried on once the address is set.
fn ready_to_start(runner: &mut impl Runner, ct: u32) -> Result<(), String> {
    let shown = runner
        .run(&argv(&["pct", "config", &ct.to_string()]))
        .map_err(|e| format!("hwaddr_unchecked: pct config {ct}: {e}"))?;
    match net0_hwaddr(&shown) {
        Some(mac) if mac_is_local(&mac) => Ok(()),
        Some(mac) => Err(format!(
            "hwaddr_not_local: CT {ct} net0 hwaddr {mac} is not a locally administered address; a first start would show it outside the container (the host-information mask is not in yet), so CT {ct} is not started and the opening stays at firewall"
        )),
        None => Err(format!(
            "hwaddr_unchecked: CT {ct} has no net0 hwaddr in pct config; not started, the opening stays at firewall"
        )),
    }
}

/// The start: `pct start`, then `pct status` must say running. A start that reports an
/// error on a container that is running anyway counts by the status.
fn start_step(runner: &mut impl Runner, ct: u32) -> Result<(), String> {
    let n = ct.to_string();
    let started = runner.run(&argv(&["pct", "start", &n]));
    match runner.run(&argv(&["pct", "status", &n])) {
        Ok(out) if out.lines().any(|l| l.trim() == "status: running") => Ok(()),
        Ok(out) => Err(format!(
            "start_failed: CT {ct} is {:?} after pct start{}",
            out.trim(),
            started.err().map(|e| format!(" ({e})")).unwrap_or_default()
        )),
        Err(e) => Err(format!("start_unchecked: pct status {ct}: {e}")),
    }
}

/// B2–B4: the steps after the record's, each checked, the record moved on after each. A
/// failure up to the identity is undone like the clone's; from the licences on the site
/// is kept for a person ([`keep`]), and a healthy site leaves its opening record. One
/// thing holds instead of undoing: a container without a local hardware address is not
/// started ([`ready_to_start`]); the answer then carries `held` with the reason and the
/// record stays at the firewall.
pub fn continue_steps(
    runner: &mut impl Runner,
    paths: &HostPaths,
    opening: Opening,
    steps: &[CreateStep],
) -> Result<Value, String> {
    let Opening { mut record, spec } = opening;
    for &step in steps {
        if step == CreateStep::Start {
            if let Err(why) = ready_to_start(runner, spec.ct) {
                record["held"] = json!(why);
                return Ok(record);
            }
        }
        let done = match step {
            CreateStep::Limits => limits_step(runner, paths, &spec),
            CreateStep::Firewall => firewall_step(paths, &spec),
            CreateStep::Start => start_step(runner, spec.ct),
            CreateStep::Install => crate::siteidentity::install_step(runner, paths, &spec, &record),
            CreateStep::Identity => {
                crate::siteidentity::identity_step(runner, paths, &spec, &mut record)
            }
            CreateStep::Licenses => {
                crate::sitelive::licences_step(runner, paths, &spec, &mut record)
            }
            CreateStep::Edge => crate::sitelive::edge_step(runner, paths, &spec),
            CreateStep::Health => {
                // the opening becomes a site: its record moves to the opened ones as it is
                // after this step, and nothing is written back to the openings
                let mut opened = record.clone();
                opened["step"] = json!(step.as_str());
                opened["at"] = json!(crate::probe::unix_now());
                if let Err(why) = crate::sitelive::health_step(runner, paths, &spec, &opened) {
                    return Err(keep(paths, &spec, &mut record, step, &why));
                }
                return Ok(opened);
            }
            other => Err(format!("{} is not a step of --continue", other.as_str())),
        };
        if sitecreate::after_failure(step) == AfterFailure::Keep {
            // from the licences on nothing is undone: a failure, or a record that cannot
            // be written after the step, keeps the site for a person
            let saved = done.and_then(|()| {
                record["step"] = json!(step.as_str());
                record["at"] = json!(crate::probe::unix_now());
                write_record(&paths.creating, &spec.code, &record)
            });
            if let Err(why) = saved {
                return Err(keep(paths, &spec, &mut record, step, &why));
            }
            continue;
        }
        let moved = done.and_then(|()| {
            record["step"] = json!(step.as_str());
            record["at"] = json!(crate::probe::unix_now());
            if step == CreateStep::Firewall {
                record["fw_group"] = json!(spec.fw_group);
            }
            write_record(&paths.creating, &spec.code, &record)
        });
        if let Err(failure) = moved {
            // passwords written for a site that is undone go with it
            let failure = match crate::siteidentity::drop_admin_secrets(&paths.secrets, &spec.code)
            {
                Ok(()) => failure,
                Err(e) => format!("{failure}; {e}"),
            };
            return Err(undo_opening(
                runner,
                &paths.creating,
                &spec.code,
                spec.ct,
                &failure,
            ));
        }
    }
    Ok(record)
}

/// A failure from the licences on: the site is kept as it is, the opening record says at
/// which step and why, and a person looks. Nothing is removed (an activation at a vendor
/// is used up, and the site may be reachable).
fn keep(
    paths: &HostPaths,
    spec: &aib_cloud::site::SiteSpec,
    record: &mut Value,
    step: CreateStep,
    why: &str,
) -> String {
    record["kept"] = json!({
        "step": step.as_str(),
        "why": why,
        "at": crate::probe::unix_now(),
    });
    let saved = match write_record(&paths.creating, &spec.code, record) {
        Ok(()) => String::new(),
        Err(e) => format!("; the opening record could not say so: {e}"),
    };
    format!(
        "kept_for_a_person: {} failed: {why}; the site {} is kept as it is, nothing was removed{saved}",
        step.as_str(),
        spec.code
    )
}

/// `--continue <code>`: under the host lock and as a new operation on the domain's
/// target, read the opening record again and run the B2 steps it has not reached.
/// Nothing left to do is no operation at all.
pub fn continue_apply(
    place: &opstore::OpPlace,
    domain: &str,
    code: &str,
    paths: &HostPaths,
    runner: &mut impl Runner,
    last: CreateStep,
) -> Result<Option<Value>, String> {
    // the same target as the clone's, so an unfinished clone holds this back
    let on = target(domain);
    opstore::run_op_prepared(
        place,
        COMMAND,
        &on,
        opstore::BY_LOCAL_ROOT,
        crate::probe::unix_now(),
        || {
            let opening = read_opening(&paths.creating, code, domain)?;
            let reached = opening
                .record
                .get("step")
                .and_then(Value::as_str)
                .ok_or("the opening record has no step")?;
            let steps = steps_after(reached, last)?;
            if steps.is_empty() {
                return Ok(None);
            }
            let plan = json!({
                "command": COMMAND,
                "continue": code,
                "domain": domain,
                "ct": opening.spec.ct,
                "from": reached,
                "steps": steps.iter().map(|s| s.as_str()).collect::<Vec<_>>(),
            });
            Ok(Some((plan, (opening, steps))))
        },
        |_, (opening, steps): (Opening, Vec<CreateStep>)| {
            continue_steps(runner, paths, opening, &steps)
        },
    )
}

/// Every opening record under `dir` with the step it reached, for `op status`: a
/// container a `verified` clone left half made shows here. A directory that cannot be
/// read says so.
pub fn opening_report(dir: &Path) -> Value {
    match read_records(dir) {
        Some(list) => Value::Array(
            list.iter()
                .map(|r| {
                    json!({
                        "site": r.get("site"),
                        "ct": r.get("ct"),
                        "hosts": r.get("hosts"),
                        "op": r.get("op"),
                        "step": r.get("step"),
                        "at": r.get("at"),
                        "what_to_run": format!(
                            "site create {} --continue {}",
                            r.get("hosts").and_then(Value::as_str).and_then(|h| h.split(',').next()).unwrap_or("?").trim(),
                            r.get("site").and_then(Value::as_str).unwrap_or("?")
                        ),
                    })
                })
                .collect(),
        ),
        None => json!({ "error": format!("{} could not be read", dir.display()) }),
    }
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
    let mut apply_file: Option<String> = None;
    let mut continue_code: Option<String> = None;
    let mut tenant = None;
    let mut it = args[1..].iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--plan" => plan_only = true,
            "--blank" => blank = true,
            "--apply" => apply_file = Some(it.next().ok_or("--apply needs a plan file")?.clone()),
            "--continue" => {
                continue_code = Some(it.next().ok_or("--continue needs a site code")?.clone());
            }
            "--tenant" => tenant = Some(it.next().ok_or("--tenant needs a customer id")?.clone()),
            "--records" => records = it.next().ok_or("--records needs a dir")?.clone(),
            other => return Err(format!("unknown argument {other}")),
        }
    }
    if let Some(code) = continue_code {
        if plan_only || blank || apply_file.is_some() {
            return Err("--continue <code> stands alone".to_owned());
        }
        return continue_cmd(&domain, &code);
    }
    match (plan_only, apply_file) {
        (true, None) => {}
        (false, Some(file)) => {
            if blank {
                return Err(
                    "--blank belongs to --plan: the plan given to --apply says it".to_owned(),
                );
            }
            return apply_cmd(&domain, &file, &records, tenant);
        }
        (true, Some(_)) => return Err("--plan or --apply, not both".to_owned()),
        (false, None) => {
            return Err(
                "needs --plan, --apply <plan.json> with a plan this command printed, or --continue <code>".to_owned(),
            )
        }
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

/// `--apply <file>` on this host. Writing: run only as a reviewed, approved operation
/// (see [`opstore::BY_LOCAL_ROOT`]: until the remote CLI's one-time approvals exist, a
/// write on the host goes through a signed script a person approved).
fn apply_cmd(
    domain: &str,
    file: &str,
    records: &str,
    tenant: Option<String>,
) -> Result<u8, String> {
    let text = fs::read_to_string(file).map_err(|e| format!("{file}: {e}"))?;
    let plan: Value = serde_json::from_str(&text).map_err(|e| format!("{file}: {e}"))?;
    tenant_matches(&given(&plan, domain)?, tenant.as_deref())?;
    let place = opstore::OpPlace::host()?;
    let record = apply(
        &place,
        &plan,
        domain,
        Path::new(CREATING),
        || see(domain, records, tenant),
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

/// `--continue <code>` on this host. Writing, like `--apply`: run only as a reviewed,
/// approved operation (see [`opstore::BY_LOCAL_ROOT`]).
///
/// There is no plan file and no hash: the approval is for "carry on s-xxxx", and every
/// value comes from the root-owned opening record B1 wrote under an approved plan. The
/// approval sheet must therefore show the `opening` row of `op status` for that code (ct,
/// hosts, step), so the person sees what is carried on (c477a B2 N1).
///
/// A container whose `.fw` stays after `pct destroy --purge` is overwritten whole when the
/// number is used again, but `site verify` may report a `.fw` without a container as a
/// leftover (c477a B2 N3; whether destroy removes it is a host test).
///
/// Exit 0 when the steps ran, 1 when the start was held (`held` says why).
fn continue_cmd(domain: &str, code: &str) -> Result<u8, String> {
    let place = opstore::OpPlace::host()?;
    let done = continue_apply(
        &place,
        domain,
        code,
        &HostPaths::host(),
        &mut HostRunner,
        CreateStep::Health,
    )?;
    let held = done
        .as_ref()
        .is_some_and(|r| r.get("held").is_some_and(Value::is_string));
    let doc = match done {
        Some(record) => {
            let open =
                record.get("step").and_then(Value::as_str) == Some(CreateStep::Health.as_str());
            json!({
                "applied": record.get("step"),
                "record": record,
                "open": open,
                // from the record: what B3 left for later and the licences not done
                "pending": crate::sitelive::pending_of(&record),
            })
        }
        None => json!({ "applied": null, "why": "the opening has done every step of this slice" }),
    };
    println!(
        "{}",
        serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?
    );
    Ok(u8::from(held))
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
        // the same plan again: refused at the journal's first line, nothing run
        // (c477a, B1 N: the refusal is named, not only "some error")
        let mut again = fake(healthy);
        let err = apply(
            &place,
            &plan,
            "new.example.test",
            &creating,
            host,
            random_from(0),
            &mut again,
        )
        .unwrap_err();
        assert!(err.contains("journal refused"), "{err}");
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

    // ------------------------------------------------------------------ continue (B2)

    const CODE: &str = "s-b2test";

    /// A host after a clone: the opening record at `step`, the container's configuration
    /// as the template left it, no firewall file yet.
    fn opened(name: &str, step: &str) -> (opstore::OpPlace, HostPaths) {
        let place = place(name);
        let root = place.ops_dir.parent().unwrap().to_path_buf();
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
        };
        for d in [
            &paths.creating,
            &paths.lxc_conf,
            &paths.firewall,
            &paths.secrets,
        ] {
            fs::create_dir_all(d).unwrap();
        }
        let record = json!({
            "site": CODE, "ct": 131, "ip": "10.77.0.41", "hosts": "new.example.test",
            "blank": false, "op": OP, "step": step, "started_at": 1, "at": 1,
        });
        fs::write(
            paths.creating.join(format!("{CODE}.json")),
            record.to_string(),
        )
        .unwrap();
        fs::write(
            paths.lxc_conf.join("131.conf"),
            "arch: amd64\nhostname: s-b2test\nlxc.cgroup2.pids.max: 99\nmemory: 512\n\n[snap1]\nmemory: 512\n",
        )
        .unwrap();
        (place, paths)
    }

    /// The spec the record of [`opened`] stands for (built here, not read: the tests run
    /// side by side and must not share a scratch directory).
    fn spec131() -> aib_cloud::site::SiteSpec {
        aib_cloud::site::SiteSpec::new(
            CODE,
            131,
            41,
            &["new.example.test"],
            true,
            "zh_TW",
            aib_cloud::site::Limits::START,
        )
        .unwrap()
    }

    /// `pct config` as the limits step should leave it, with a locally administered
    /// hardware address on net0 (what the host-information mask will write).
    fn shown_config() -> String {
        aib_cloud::site::desired(&spec131())
            .iter()
            .map(|(k, v)| {
                if *k == "net0" {
                    format!("{k}: {v},hwaddr=02:5A:00:12:34:56,type=veth\n")
                } else {
                    format!("{k}: {v}\n")
                }
            })
            .collect()
    }

    /// A host where every B2 command works and the container runs once started.
    fn b2_host(config: String) -> impl FnMut(&[String]) -> Result<String, String> {
        let mut started = false;
        let mut destroyed = false;
        move |argv: &[String]| match argv[1].as_str() {
            "config" => Ok(config.clone()),
            "start" => {
                started = true;
                Ok(String::new())
            }
            "destroy" => {
                destroyed = true;
                Ok(String::new())
            }
            "status" if destroyed => Err(GONE.to_owned()),
            "status" if started => Ok("status: running\n".to_owned()),
            "status" => Ok("status: stopped\n".to_owned()),
            _ => Ok(String::new()),
        }
    }

    fn record_of(paths: &HostPaths) -> Value {
        serde_json::from_str(
            &fs::read_to_string(paths.creating.join(format!("{CODE}.json"))).unwrap(),
        )
        .unwrap()
    }

    fn last_state(place: &opstore::OpPlace) -> Option<State> {
        opstore::last_entries(&place.ops_dir)
            .unwrap()
            .iter()
            .find(|e| e.target == "domain:new.example.test")
            .map(|e| e.state)
    }

    #[test]
    fn an_opening_is_carried_on_through_limits_firewall_and_start() {
        let (place, paths) = opened("b2run", "clone");
        let mut runner = fake(b2_host(shown_config()));
        let rec = continue_apply(
            &place,
            "new.example.test",
            CODE,
            &paths,
            &mut runner,
            CreateStep::Start,
        )
        .unwrap()
        .unwrap();
        assert_eq!(rec["step"], "start");
        assert_eq!(rec["fw_group"], aib_cloud::site::FW_GROUP);
        assert_eq!(record_of(&paths), rec, "the record on disk moved on");
        assert!(rec["at"].as_u64().unwrap() > 1);
        let set = aib_cloud::site::plan(&spec131())
            .into_iter()
            .find(|s| s.id == "set")
            .unwrap()
            .argv
            .join(" ");
        assert_eq!(
            runner.calls,
            [
                set,
                "pct config 131".to_owned(),
                "pct config 131".to_owned(),
                "pct start 131".to_owned(),
                "pct status 131".to_owned(),
            ]
        );
        assert!(rec.get("held").is_none());
        // one pids line, in the main section, the snapshot left as it was
        let conf = fs::read_to_string(paths.lxc_conf.join("131.conf")).unwrap();
        assert_eq!(conf.matches("lxc.cgroup2.pids.max").count(), 1);
        assert!(
            conf.contains("lxc.cgroup2.pids.max: 2048\n\n[snap1]\nmemory: 512\n"),
            "{conf}"
        );
        assert_eq!(
            fs::read_to_string(paths.firewall.join("131.fw")).unwrap(),
            aib_cloud::site::desired_firewall(aib_cloud::site::FW_GROUP, None)
        );
        assert_eq!(last_state(&place), Some(State::Verified));
        assert!(!place.lock.lock.exists(), "the lock is given back");
    }

    #[test]
    fn an_opening_carries_on_from_the_step_it_reached() {
        let (place, paths) = opened("b2from", "limits");
        let mut runner = fake(b2_host(shown_config()));
        continue_apply(
            &place,
            "new.example.test",
            CODE,
            &paths,
            &mut runner,
            CreateStep::Start,
        )
        .unwrap()
        .unwrap();
        assert_eq!(
            runner.calls,
            ["pct config 131", "pct start 131", "pct status 131"]
        );
        // done with this slice: nothing runs and nothing is journaled
        let (place, paths) = opened("b2done", "start");
        // B2 alone (the tests above stop at the start): nothing past it
        let mut runner = fake(b2_host(shown_config()));
        assert_eq!(
            continue_apply(
                &place,
                "new.example.test",
                CODE,
                &paths,
                &mut runner,
                CreateStep::Start
            )
            .unwrap(),
            None
        );
        assert!(runner.calls.is_empty());
        assert_eq!(last_state(&place), None);
        assert_eq!(steps_after("identity", CreateStep::Identity).unwrap(), []);
        // past the last step asked for: nothing; a word that is no step: refused
        assert_eq!(steps_after("licenses", CreateStep::Identity).unwrap(), []);
        assert!(steps_after("dns", CreateStep::Health).is_err());
        assert_eq!(
            steps_after("identity", CreateStep::Health).unwrap(),
            [CreateStep::Licenses, CreateStep::Edge, CreateStep::Health]
        );
        assert_eq!(
            steps_after("firewall", CreateStep::Start).unwrap(),
            [CreateStep::Start]
        );
        assert_eq!(
            steps_after("start", CreateStep::Identity).unwrap(),
            [CreateStep::Install, CreateStep::Identity]
        );
        assert_eq!(
            steps_after("clone", CreateStep::Start).unwrap(),
            [CreateStep::Limits, CreateStep::Firewall, CreateStep::Start]
        );
    }

    #[test]
    fn a_failure_from_the_licences_on_keeps_the_site_and_removes_nothing() {
        let (_place, paths) = opened("b4keep", "identity");
        // no key file: the licence step cannot know, so it stops
        let opening = read_opening(&paths.creating, CODE, "new.example.test").unwrap();
        let mut runner = fake(|_| Err("the host says no".to_owned()));
        let e = continue_steps(&mut runner, &paths, opening, &[CreateStep::Licenses]).unwrap_err();
        assert!(e.starts_with("kept_for_a_person: licenses failed"), "{e}");
        assert!(runner
            .calls
            .iter()
            .all(|c| !c.contains("destroy") && !c.contains("stop")));
        let rec: Value = serde_json::from_str(
            &fs::read_to_string(paths.creating.join(format!("{CODE}.json"))).unwrap(),
        )
        .unwrap();
        assert_eq!(rec["kept"]["step"], "licenses");
        assert_eq!(rec["step"], "identity");
    }

    #[test]
    fn limits_that_did_not_take_are_undone() {
        let (place, paths) = opened("b2limits", "clone");
        let wrong = shown_config().replace("memory: ", "memory: 9");
        let mut runner = fake(b2_host(wrong));
        let err = continue_apply(
            &place,
            "new.example.test",
            CODE,
            &paths,
            &mut runner,
            CreateStep::Start,
        )
        .unwrap_err();
        assert!(err.starts_with("limits_not_applied: memory"), "{err}");
        assert!(err.contains("undone: CT 131 destroyed"), "{err}");
        for c in [
            "pct stop 131",
            "pct unlock 131",
            "pct destroy 131 --purge 1",
        ] {
            assert!(
                runner.calls.contains(&c.to_owned()),
                "{c}: {:?}",
                runner.calls
            );
        }
        assert!(!runner.calls.iter().any(|c| c.contains("start")));
        assert!(
            fs::read_dir(&paths.creating).unwrap().next().is_none(),
            "the record is removed"
        );
        assert!(!paths.firewall.join("131.fw").exists());
        assert_eq!(last_state(&place), Some(State::Failed));
    }

    /// An opening that is undone keeps no administrator password on the host (B3 writes
    /// them before a later step can fail).
    #[test]
    fn an_undone_opening_drops_its_passwords() {
        let (place, paths) = opened("b3drop", "clone");
        fs::write(paths.secrets.join(format!("{CODE}.admin.ops")), "x").unwrap();
        fs::write(paths.secrets.join("s-other.admin.ops"), "y").unwrap();
        let wrong = shown_config().replace("memory: ", "memory: 9");
        let mut runner = fake(b2_host(wrong));
        let err = continue_apply(
            &place,
            "new.example.test",
            CODE,
            &paths,
            &mut runner,
            CreateStep::Start,
        )
        .unwrap_err();
        assert!(err.contains("undone: CT 131 destroyed"), "{err}");
        assert!(!paths.secrets.join(format!("{CODE}.admin.ops")).exists());
        assert!(paths.secrets.join("s-other.admin.ops").exists());
    }

    #[test]
    fn a_container_that_does_not_run_is_undone() {
        let (place, paths) = opened("b2start", "firewall");
        let mut destroyed = false;
        let config = shown_config();
        let mut runner = fake(move |argv: &[String]| match argv[1].as_str() {
            "config" => Ok(config.clone()),
            "start" => Err("startup for container '131' failed".to_owned()),
            "destroy" => {
                destroyed = true;
                Ok(String::new())
            }
            "status" if destroyed => Err(GONE.to_owned()),
            "status" => Ok("status: stopped\n".to_owned()),
            _ => Ok(String::new()),
        });
        let err = continue_apply(
            &place,
            "new.example.test",
            CODE,
            &paths,
            &mut runner,
            CreateStep::Start,
        )
        .unwrap_err();
        assert!(err.starts_with("start_failed"), "{err}");
        assert!(err.contains("undone: CT 131 destroyed"), "{err}");
        assert!(fs::read_dir(&paths.creating).unwrap().next().is_none());
    }

    #[test]
    fn a_container_whose_state_cannot_be_told_is_left_for_a_person() {
        let (place, paths) = opened("b2unknown", "firewall");
        let config = shown_config();
        let mut runner = fake(move |argv: &[String]| match argv[1].as_str() {
            "config" => Ok(config.clone()),
            "start" => Ok(String::new()),
            _ => Err("pct: timed out".to_owned()),
        });
        let err = continue_apply(
            &place,
            "new.example.test",
            CODE,
            &paths,
            &mut runner,
            CreateStep::Start,
        )
        .unwrap_err();
        assert!(err.starts_with("start_unchecked"), "{err}");
        assert!(err.contains("left for a person"), "{err}");
        assert!(!runner.calls.iter().any(|c| c.contains("destroy")));
        assert_eq!(record_of(&paths)["step"], "firewall", "the record stays");
    }

    #[test]
    fn an_opening_of_another_domain_is_not_carried_on() {
        let (place, paths) = opened("b2other", "clone");
        let mut runner = fake(b2_host(shown_config()));
        let err = continue_apply(
            &place,
            "else.example.test",
            CODE,
            &paths,
            &mut runner,
            CreateStep::Start,
        )
        .unwrap_err();
        assert!(err.contains("not of else.example.test"), "{err}");
        assert!(runner.calls.is_empty());
        assert!(continue_apply(
            &place,
            "new.example.test",
            "s-nothere",
            &paths,
            &mut runner,
            CreateStep::Start
        )
        .is_err());
        assert!(continue_apply(
            &place,
            "new.example.test",
            "../x",
            &paths,
            &mut runner,
            CreateStep::Start
        )
        .is_err());
        assert!(runner.calls.is_empty());
        assert_eq!(last_state(&place), None);
    }

    #[test]
    fn the_pids_line_is_one_and_in_the_main_section() {
        assert_eq!(
            with_pids("a: 1\n", 2048),
            "a: 1\nlxc.cgroup2.pids.max: 2048\n"
        );
        assert_eq!(
            with_pids(
                "a: 1\nlxc.cgroup2.pids.max: 9\nb: 2\n\n[s]\nlxc.cgroup2.pids.max: 9\n",
                4096
            ),
            "a: 1\nb: 2\nlxc.cgroup2.pids.max: 4096\n\n[s]\nlxc.cgroup2.pids.max: 9\n"
        );
    }

    /// `op status` shows a container a verified clone left half made (c477a, B1 L1).
    #[test]
    fn an_opening_shows_with_the_step_it_reached() {
        let (_, paths) = opened("b2report", "limits");
        let report = opening_report(&paths.creating);
        assert_eq!(report[0]["site"], CODE);
        assert_eq!(report[0]["step"], "limits");
        assert_eq!(report[0]["ct"], 131);
        assert_eq!(
            report[0]["what_to_run"],
            format!("site create new.example.test --continue {CODE}")
        );
        fs::write(paths.creating.join("cut.json"), "{").unwrap();
        assert!(opening_report(&paths.creating)["error"].is_string());
    }

    /// A container still on the platform's hardware address is not started: nothing is
    /// undone, the opening stays at the firewall and says why (c477a B2 L2), and the
    /// install and identity after it do not run.
    #[test]
    fn a_platform_hardware_address_holds_the_start() {
        let (place, paths) = opened("b2mac", "firewall");
        let platform =
            shown_config().replace("hwaddr=02:5A:00:12:34:56", "hwaddr=BC:24:11:AA:BB:CC");
        let mut runner = fake(b2_host(platform));
        let rec = continue_apply(
            &place,
            "new.example.test",
            CODE,
            &paths,
            &mut runner,
            CreateStep::Identity,
        )
        .unwrap()
        .unwrap();
        let held = rec["held"].as_str().unwrap();
        assert!(held.starts_with("hwaddr_not_local"), "{held}");
        assert!(held.contains("stays at firewall"), "{held}");
        assert_eq!(runner.calls, ["pct config 131"]);
        assert_eq!(record_of(&paths)["step"], "firewall", "the record stays");
        assert!(
            record_of(&paths).get("held").is_none(),
            "held is the answer's, not the record's"
        );
        // no hardware address shown at all: held the same way
        let (place, paths) = opened("b2nomac", "firewall");
        let none: String = shown_config().replace(",hwaddr=02:5A:00:12:34:56", "");
        let mut runner = fake(b2_host(none));
        let rec = continue_apply(
            &place,
            "new.example.test",
            CODE,
            &paths,
            &mut runner,
            CreateStep::Start,
        )
        .unwrap()
        .unwrap();
        assert!(rec["held"]
            .as_str()
            .unwrap()
            .starts_with("hwaddr_unchecked"));
        assert!(!runner
            .calls
            .iter()
            .any(|c| c.contains("start") || c.contains("destroy")));
    }

    #[test]
    fn a_local_hardware_address_is_told_from_a_platform_one() {
        assert!(mac_is_local("02:5a:00:12:34:56"));
        assert!(mac_is_local("0E:00:00:00:00:01"));
        // a platform's own range: not local
        assert!(!mac_is_local("BC:24:11:AA:BB:CC"));
        // local but multicast
        assert!(!mac_is_local("03:00:00:00:00:01"));
        for bad in [
            "",
            "+2:5a:00:12:34:56",
            "02:5a",
            "02:5a:00:12:34:56:78",
            "2:5a:00:12:34:56",
            "zz:5a:00:12:34:56",
            "02:5a:00:12:34:5g",
        ] {
            assert!(!mac_is_local(bad), "{bad}");
        }
        assert_eq!(
            net0_hwaddr("net0: name=eth0,hwaddr=02:5A:00:12:34:56,ip=10.77.0.41/24\n[s]\nnet0: hwaddr=BC:24:11:00:00:01\n").as_deref(),
            Some("02:5A:00:12:34:56")
        );
        assert_eq!(net0_hwaddr("[s]\nnet0: hwaddr=02:5A:00:12:34:56\n"), None);
    }

    /// The files under /etc/pve are written beside and renamed: no temporary file stays.
    #[test]
    fn the_container_files_leave_no_temporary_file() {
        let (place, paths) = opened("b2tmp", "clone");
        let mut runner = fake(b2_host(shown_config()));
        continue_apply(
            &place,
            "new.example.test",
            CODE,
            &paths,
            &mut runner,
            CreateStep::Start,
        )
        .unwrap()
        .unwrap();
        for dir in [&paths.lxc_conf, &paths.firewall] {
            let names: Vec<String> = fs::read_dir(dir)
                .unwrap()
                .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
                .collect();
            assert!(names.iter().all(|n| !n.ends_with(".tmp")), "{names:?}");
        }
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

    /// E7 N2 (c477a): `--apply` must name the customer the plan was made for; none or
    /// another is refused with a fixed word before anything is read.
    #[test]
    fn apply_needs_the_tenant_the_plan_was_made_for() {
        let mut doc = plan_doc("new.example.test", false, &host());
        crate::quarantinecmd::seal_with_op(
            &mut doc,
            OP,
            &"f".repeat(64),
            env!("CARGO_PKG_VERSION"),
        )
        .unwrap();
        let g = given(&doc, "new.example.test").unwrap();
        assert_eq!(g.tenant.as_deref(), Some("acme"));
        assert!(tenant_matches(&g, Some("acme")).is_ok());
        let missing = tenant_matches(&g, None).unwrap_err();
        assert!(missing.starts_with("tenant_missing"), "{missing}");
        let other = tenant_matches(&g, Some("other")).unwrap_err();
        assert!(other.starts_with("tenant_mismatch"), "{other}");
        // a plan that named nobody is never applied for a customer
        let mut nobody = g;
        nobody.tenant = None;
        let none = tenant_matches(&nobody, Some("acme")).unwrap_err();
        assert!(none.starts_with("tenant_mismatch"), "{none}");
    }
}
