// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host site remove <code> --address <main address> --plan` — the read-only
//! half of removing a site. It reads the site's record, checks that the code and the main
//! address typed are the record's, probes each of the ten steps of
//! [`aib_cloud::siteremove`] on this host and prints what would be done next, sealed like
//! every other plan. `--plan` changes nothing.
//!
//! `--apply <plan.json>` carries the removal out on this host: under the host lock the
//! plan is made again and must be the very plan given (same hash, same document), every
//! other site is measured once, then each step the plan's rule names next is done and
//! looked at again, as operation `op` of the plan, until only the DNS step is left (done
//! where the DNS key is) or nothing is. After every step every other site must answer as
//! it did at the start. It is written to agree with the reviewed hand-run program
//! `tools/generic/site-remove.sh` of the ez2-cloud-site-manual skill.
//!
//! What it does not look at yet is said in the plan, not guessed: whether the site's paid
//! plugins are still activated at their vendors is answered inside the site, and that is
//! not wired here. The licence step is therefore "could not tell" unless the person
//! passes `--licences-left-at-vendor`, which is written into the plan.
//!
//! The DNS step is not probed on this host either: the key that manages DNS zones is not
//! kept here. The step is "not asked here": the steps before it are planned as usual and
//! the plan stops only when the DNS step would be next, saying why. It is never asked
//! from here; the part that deletes records runs where that key is, over every address
//! of the site's record (listed in the plan).
//!
//! The archive of a site is looked for under [`ARCHIVES`]. That directory is outside the
//! one the daily backup rotates, and the archive tool must be able to enter it as the
//! container's own root, which is an unprivileged id on the host: a first removal by hand
//! stopped at exactly that, with the archive placed where only the host's root could go.
//!
//! Once the record is moved, `--apply` removes what the site left on the host (step 10
//! of the hand-run program, [`clean_leftovers`]) and looks again. Its answer's
//! `left_on_host` is what that second look found, so it is always `[]` when `--apply`
//! answers at all: anything still there stops it with the paths instead. That step is
//! not one of the plan's steps and runs after the record is moved, so a new plan has
//! nothing left for `--apply` to run: the stop says which paths to remove by hand, and
//! `--plan` for the removed site then lists what is still there as `left_on_host`.

use std::fs;
use std::path::Path;
use std::process::Command;
use std::time::{Duration, SystemTime};

use aib_cloud::site::{customer_ssh_port, SiteSpec};
use aib_cloud::sitecreate::Probe;
use aib_cloud::siteremove::{self, DnsProbe, Next, RemoveStep, STEPS};
use serde_json::{json, Value};

use crate::opstore;
use crate::probe::run_capture;

const RECORDS: &str = "/var/lib/ez2-cloud/sites";
const REMOVED_RECORDS: &str = "/var/lib/ez2-cloud/sites-removed";
/// Where the archives of removed sites are kept.
pub const ARCHIVES: &str = "/var/lib/vz/ez2-removed";
const EXPORT_LIST: &str = "/etc/ez2-cloud/sites.list";
const NETWORK_FILE: &str = "/etc/network/interfaces";
/// How old an archive may be and still count while the container is there.
pub const FRESH_SECS: u64 = 24 * 3600;
/// Why the DNS step is not probed by this command.
pub const DNS_NOT_HERE: &str =
    "not available on this host: the key that manages DNS zones is not kept here";

/// What was read from the host for one site. `None` is "could not be read".
#[derive(Default)]
pub struct Seen {
    pub entry_config: Option<String>,
    pub container_list: Option<String>,
    /// `Some(true)` running, `Some(false)` stopped; only asked when the container is listed.
    pub running: Option<bool>,
    /// Whether a disk of the container is still in the storage.
    pub disk: Option<bool>,
    pub archive: Option<Probe>,
    /// The file name of the archive [`Seen::archive`] found good, when it did.
    pub archive_name: Option<String>,
    pub live_rules: Option<String>,
    pub network_file: Option<String>,
    pub firewall_file: Option<bool>,
    pub export_list: Option<String>,
    /// Whether the record is still in the records directory.
    pub record_in_place: Option<bool>,
}

fn known(answer: Option<bool>, done_when: bool) -> Probe {
    match answer {
        None => Probe::Unknown,
        Some(v) if v == done_when => Probe::Done,
        Some(_) => Probe::NotDone,
    }
}

/// One probe per step, in [`STEPS`] order. Separate from [`cmd`] so it can be tested
/// without a host.
pub fn probes(
    spec: &SiteSpec,
    seen: &Seen,
    licences_wired: Option<Probe>,
    dns: DnsProbe,
) -> [Probe; 10] {
    let listed = siteremove::container_listed(seen.container_list.as_deref(), spec.ct);
    let access = match customer_ssh_port(spec.ct) {
        Some(port) => siteremove::access_probe(
            seen.live_rules.as_deref(),
            seen.network_file.as_deref(),
            port,
            &spec.ip(),
        ),
        None => Probe::Unknown,
    };
    let stopped = match listed {
        None => Probe::Unknown,
        Some(false) => Probe::Done,
        Some(true) => known(seen.running, false),
    };
    let destroyed = match (listed, seen.disk) {
        (Some(false), Some(false)) => Probe::Done,
        (Some(true), Some(_)) | (Some(false), Some(true)) => Probe::NotDone,
        _ => Probe::Unknown,
    };
    [
        siteremove::unserve_probe(seen.entry_config.as_deref(), &spec.code, spec.primary()),
        seen.archive.unwrap_or(Probe::Unknown),
        licences_wired.unwrap_or(Probe::Unknown),
        access,
        stopped,
        destroyed,
        known(seen.firewall_file, false),
        siteremove::lists_probe(seen.export_list.as_deref(), &spec.code),
        dns.probe(),
        known(seen.record_in_place, false),
    ]
}

fn probe_word(p: Probe) -> &'static str {
    match p {
        Probe::Done => "done",
        Probe::NotDone => "not_done",
        Probe::Unknown => "unknown",
    }
}

/// The plan document. Separate from [`cmd`] so it can be tested without a host.
pub fn plan_doc(
    spec: &SiteSpec,
    probes: &[Probe; 10],
    licences_left_at_vendor: bool,
    dns: DnsProbe,
) -> Value {
    let next = next_here(probes, licences_left_at_vendor, dns);
    let (next_word, next_step, stop) = match next {
        Next::Run(s) => ("run", Some(s), None),
        Next::Finished => ("finished", None, None),
        Next::Stop(why, s) => ("stop", Some(s), Some(why)),
    };
    json!({
        "plan": "site-remove",
        "site": spec.code,
        "ct": spec.ct,
        "address": spec.primary(),
        "level": "L2",
        "licences_left_at_vendor": licences_left_at_vendor,
        "steps": STEPS.iter().zip(probes).map(|(s, p)| json!({
            "step": s.as_str(),
            "now": probe_word(*p),
            "needs_archive": siteremove::needs_archive(*s),
        })).collect::<Vec<_>>(),
        "next": next_word,
        "next_step": next_step.map(RemoveStep::as_str),
        "stop": stop,
        "may_call_off": siteremove::may_call_off_now(next),
        // steps found done behind the next one: calling off leaves them as they are
        "done_early": siteremove::done_early(probes, next).into_iter().map(RemoveStep::as_str).collect::<Vec<_>>(),
        "dns": dns.as_str(),
        "dns_note": if dns == DnsProbe::NotAskedHere { Value::from(DNS_NOT_HERE) } else { Value::Null },
        "dns_names": spec.hosts,
        "archives": ARCHIVES,
        "on_failure": "stop where it failed; every step is safe to do again; after the licence step the only way is forward",
        "not_looked_at": ["licences at the vendors (answered inside the site; not wired to this command yet)", "DNS records of the site's addresses (the key is not on this host)", "the id ranges given to the container"],
    })
}

fn capture(program: &str, args: &[&str], seconds: u64) -> Option<String> {
    let mut cmd = Command::new(program);
    cmd.args(args);
    run_capture(&mut cmd, Duration::from_secs(seconds)).ok()
}

fn read_text(path: &str) -> Option<String> {
    fs::read_to_string(path).ok()
}

/// `Some(false)` only when the path is known not to be there.
fn exists(path: &str) -> Option<bool> {
    match fs::symlink_metadata(path) {
        Ok(_) => Some(true),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Some(false),
        Err(_) => None,
    }
}

/// The newest archive of container `ct` under `dir`, checked: the compressed file is
/// whole and holds the container's own configuration. No archive is "not done"; an
/// archive that does not check out, or a directory that cannot be read, is "could not
/// tell" and is left for a person. The name of the archive is given back when it is good.
///
/// `fresh_only`: only an archive made less than [`FRESH_SECS`] before `now` counts. That
/// is the case while the container is there (or could not be listed): an older archive may
/// predate changes to the site and is passed over, never reused. Once the container is
/// gone, whatever archive is left of it is the only one there can be: any age counts
/// (the same rule as the reviewed hand-run program `site-remove.sh`, c477a L1 / L4).
pub fn archive_probe(
    dir: &Path,
    ct: u32,
    fresh_only: bool,
    now: SystemTime,
) -> (Probe, Option<String>) {
    let entries = match fs::read_dir(dir) {
        Ok(e) => e,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return (Probe::NotDone, None),
        Err(_) => return (Probe::Unknown, None),
    };
    let prefix = format!("vzdump-lxc-{ct}-");
    let mut names: Vec<String> = Vec::new();
    for entry in entries {
        let Ok(entry) = entry else {
            return (Probe::Unknown, None);
        };
        let name = entry.file_name().to_string_lossy().into_owned();
        if !(name.starts_with(&prefix) && name.ends_with(".tar.zst")) {
            continue;
        }
        if fresh_only {
            let Ok(made) = entry.metadata().and_then(|m| m.modified()) else {
                return (Probe::Unknown, None);
            };
            // a time ahead of now is not fresh either: the clock or the file is off
            match now.duration_since(made) {
                Ok(age) if age.as_secs() < FRESH_SECS => {}
                _ => continue,
            }
        }
        names.push(name);
    }
    names.sort();
    let Some(newest) = names.last() else {
        return (Probe::NotDone, None);
    };
    let path = dir.join(newest);
    let Some(path) = path.to_str() else {
        return (Probe::Unknown, None);
    };
    let whole = capture("zstd", &["-tq", path], 600).is_some();
    let own = capture(
        "tar",
        &["--zstd", "-tf", path, "./etc/vzdump/pct.conf"],
        600,
    )
    .is_some();
    if whole && own {
        (Probe::Done, Some(newest.clone()))
    } else {
        (Probe::Unknown, None)
    }
}

fn see(spec: &SiteSpec, records: &str) -> Seen {
    let ct = format!("{}", spec.ct);
    let container_list = capture("pct", &["list"], 20);
    let listed = siteremove::container_listed(container_list.as_deref(), spec.ct);
    let running = if listed == Some(true) {
        match capture("pct", &["status", &ct], 20)
            .as_deref()
            .map(str::trim)
        {
            Some("status: running") => Some(true),
            Some("status: stopped") => Some(false),
            _ => None,
        }
    } else {
        None
    };
    let disk = capture("pvesm", &["list", "local-lvm", "--vmid", &ct], 30).and_then(|t| {
        t.lines()
            .next()
            .is_some_and(|h| h.starts_with("Volid"))
            .then(|| t.contains(&format!("vm-{ct}-disk")))
    });
    // while the container is there, or could not be listed, only a fresh archive counts
    let archive = archive_probe(
        &Path::new(ARCHIVES).join(&spec.code),
        spec.ct,
        listed != Some(false),
        SystemTime::now(),
    );
    Seen {
        entry_config: crate::probe::edge_caddyfile().ok(),
        container_list,
        running,
        disk,
        archive: Some(archive.0),
        archive_name: archive.1,
        live_rules: capture("iptables", &["-w", "-t", "nat", "-S", "PREROUTING"], 20),
        network_file: read_text(NETWORK_FILE),
        firewall_file: exists(&format!("/etc/pve/firewall/{ct}.fw")),
        export_list: read_text(EXPORT_LIST),
        record_in_place: exists(&format!("{records}/{}.json", spec.code)),
    }
}

/// The record of a removed site: the site's record as it was, with the time of the
/// removal, the archive's name (or none), the plugins whose activations are left at their
/// vendors because no program can give them back, and what the DNS step found. The
/// record step of `--apply` writes this.
pub fn removed_record(
    record: &Value,
    removed_at: &str,
    archive: Option<&str>,
    left_at_vendor: &[&str],
    dns: DnsProbe,
) -> Result<Value, String> {
    let mut out = record.clone();
    let obj = out
        .as_object_mut()
        .ok_or("the site's record is not an object")?;
    obj.insert("removed".to_owned(), json!(removed_at));
    obj.insert("archive".to_owned(), json!(archive));
    obj.insert("left_at_vendor".to_owned(), json!(left_at_vendor));
    obj.insert("dns".to_owned(), json!(dns.as_str()));
    Ok(out)
}

// ------------------------------------------------------------------ apply

/// The command name in the operation journal.
pub const COMMAND: &str = "site.remove";
/// What the removed record lists as left at the vendors when the licence step was passed
/// over on the person's word: no vendor was asked from this host.
pub const LEFT_ON_WORD: &str = "all:not-asked(licences-left-at-vendor)";
/// The files where Proxmox keeps backup schedules: `jobs.cfg` since PVE 7.1, `vzdump.cron`
/// before. `pct destroy --purge` takes the container out of them, so destroying must
/// leave both as they were. Our own daily backup is not scheduled in either (4542e
/// looked on the first host, 11-11: no `jobs.cfg`, a `vzdump.cron` with no backup line):
/// this guards against a schedule someone adds in Proxmox, not ours.
pub const BACKUP_SCHEDULES: [&str; 2] = ["/etc/pve/jobs.cfg", "/etc/pve/vzdump.cron"];

/// The backup schedules as one text: each file's path and content, or that it is not
/// there. A file that is not there is an answer (the host has no such schedule); a file
/// that is there and cannot be read is not: `None`.
pub fn schedule_text(paths: &[&Path]) -> Option<String> {
    let mut out = String::new();
    for p in paths {
        match fs::read_to_string(p) {
            Ok(t) => out.push_str(&format!("== {}\n{t}\n", p.display())),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                out.push_str(&format!("== {} absent\n", p.display()));
            }
            Err(_) => return None,
        }
    }
    Some(out)
}

/// Where copies of the host files this command rewrites are kept.
const KEEP: &str = "/root/ez2-removed";

/// What removing a site does on the host, one call per change. The host's is
/// [`HostRemover`]; tests hand in a made-up host that remembers every call. Every call
/// that changes something only changes it: whether it took is judged by looking again
/// ([`RemoveHost::see`]) and probing, the same way the plan looks.
pub trait RemoveHost {
    /// Everything the probes read, read again.
    fn see(&mut self) -> Seen;
    /// Every other site's main address and what it answers through the entry; `None`:
    /// it could not be asked.
    fn others(&mut self) -> Vec<(String, Option<u16>)>;
    /// The entry's configuration replaced by `text`: kept, validated, reloaded; put back
    /// when it does not validate or reload.
    fn write_entry(&mut self, text: &str) -> Result<(), String>;
    /// An archive of the whole container made under [`ARCHIVES`]`/<code>`.
    fn archive(&mut self) -> Result<(), String>;
    /// These forwarding rules deleted (each is the argument list of `iptables -t nat`),
    /// and the host's network file replaced by `network_file` when it is `Some`.
    fn close_access(
        &mut self,
        rules: &[Vec<String>],
        network_file: Option<&str>,
    ) -> Result<(), String>;
    fn stop(&mut self) -> Result<(), String>;
    fn destroy(&mut self) -> Result<(), String>;
    /// The host's backup schedules as text ([`schedule_text`] of [`BACKUP_SCHEDULES`]):
    /// destroying must not change them. `None`: one could not be read.
    fn backup_schedule(&mut self) -> Option<String>;
    fn remove_firewall(&mut self) -> Result<(), String>;
    fn write_export_list(&mut self, text: &str) -> Result<(), String>;
    fn read_record(&mut self) -> Result<Value, String>;
    /// The removed record written under the removed records (0600), then the record taken
    /// out of the records.
    fn move_record(&mut self, removed: &Value) -> Result<(), String>;
    /// The names in `dir`; `None`: there is no such directory.
    fn list_dir(&mut self, dir: &str) -> Result<Option<Vec<String>>, String>;
    /// Whether `path` is there (a file or a directory).
    fn exists(&mut self, path: &str) -> Result<bool, String>;
    /// `path` removed: a file, or a directory with all it holds.
    fn remove_path(&mut self, path: &str) -> Result<(), String>;
}

/// The entry's configuration without this site's block. The block must be the site's one
/// block, closed, not running into the next site's, and opening with the site's main
/// address; a blank line left doubled where it was is taken out. The result must probe as
/// "taken off" ([`siteremove::unserve_probe`]).
pub fn entry_without(caddyfile: &str, code: &str, main_address: &str) -> Result<String, String> {
    let block =
        aib_cloud::quarantine::caddy_block_text(caddyfile, code).map_err(|e| format!("{e:?}"))?;
    let opens = block
        .lines()
        .nth(1)
        .and_then(|l| l.trim_end().strip_suffix('{'))
        .is_some_and(|names| names.split_whitespace().next() == Some(main_address));
    if !opens {
        return Err(format!(
            "the entry's block for {code} does not open with {main_address}"
        ));
    }
    let marker = aib_cloud::site::caddy_marker(code);
    let lines: Vec<&str> = caddyfile.lines().collect();
    let start = lines
        .iter()
        .position(|l| l.trim_end() == marker)
        .ok_or("the block's marker went missing")?;
    let len = block.lines().count();
    let mut out: Vec<&str> = lines[..start].to_vec();
    out.extend_from_slice(&lines[start + len..]);
    if start > 0
        && start < out.len()
        && out[start - 1].trim().is_empty()
        && out[start].trim().is_empty()
    {
        out.remove(start);
    }
    let mut text = out.join("\n");
    if caddyfile.ends_with('\n') {
        text.push('\n');
    }
    if siteremove::unserve_probe(Some(&text), code, main_address) != Probe::Done {
        return Err(format!(
            "without its block the entry still names {main_address}: not touched"
        ));
    }
    Ok(text)
}

/// Whether a rule line forwards the site's port to the site (the match of
/// [`siteremove::access_probe`]).
fn forwards_to(line: &str, port: u16, ip: &str) -> bool {
    let l = line.trim_end();
    let to = format!("--to-destination {ip}:22");
    l.contains(&format!("--dport {port} ")) && (l.ends_with(&to) || l.contains(&format!("{to} ")))
}

/// The `iptables -t nat` arguments that delete each live rule forwarding the site's
/// port to it: the listed `-A` line with `-D` in its place. The line is split on white
/// space: a rule carrying a quoted comment with spaces would be split wrong, `-D` would
/// then fail and the step stop (nothing wrong is deleted). Our forwards carry no comment
/// (33e28 N2).
pub fn rules_to_delete(live_rules: &str, port: u16, ip: &str) -> Vec<Vec<String>> {
    live_rules
        .lines()
        .filter(|l| l.starts_with("-A ") && forwards_to(l, port, ip))
        .map(|l| {
            let mut args: Vec<String> = l.split_whitespace().map(str::to_owned).collect();
            args[0] = "-D".to_owned();
            args
        })
        .collect()
}

/// The host's network file without the lines forwarding the site's port to it, and how
/// many lines went.
pub fn network_file_without(text: &str, port: u16, ip: &str) -> (String, usize) {
    let mut gone = 0;
    let mut out = String::new();
    for l in text.lines() {
        if forwards_to(l, port, ip) {
            gone += 1;
        } else {
            out.push_str(l);
            out.push('\n');
        }
    }
    (out, gone)
}

/// The export list without the site's line (the match of [`siteremove::lists_probe`]).
pub fn export_list_without(text: &str, code: &str) -> String {
    let mut out = String::new();
    for l in text.lines() {
        let comment = l.trim_start().starts_with('#');
        if comment || l.split_whitespace().next() != Some(code) {
            out.push_str(l);
            out.push('\n');
        }
    }
    out
}

/// Every other site answers now as it did before the start. A site that could not be
/// asked, then or now, is not "the same": it stops the work. Codes are compared one for
/// one, so a site whose answer moves between 200, 302 and 503 stops a removal half way;
/// the removal then goes on from where it stopped with a new plan (33e28 N4).
pub fn same_as_base(
    base: &[(String, Option<u16>)],
    now: &[(String, Option<u16>)],
) -> Result<(), String> {
    if let Some((h, _)) = base.iter().chain(now).find(|(_, c)| c.is_none()) {
        return Err(format!("another site could not be asked: {h}"));
    }
    if base != now {
        let show = |v: &[(String, Option<u16>)]| {
            v.iter()
                .map(|(h, c)| format!("{h}={}", c.unwrap_or(0)))
                .collect::<Vec<_>>()
                .join(" ")
        };
        return Err(format!(
            "another site does not answer as it did before the start: before {} now {}",
            show(base),
            show(now)
        ));
    }
    Ok(())
}

/// What the plan given to `--apply` says, checked before anything is read.
pub struct Given {
    pub op: String,
    pub sha: String,
    pub left: bool,
    /// The plan as given, whole: every field must be what this host makes again.
    pub plan: Value,
}

/// Read the plan given to `--apply` for the site `code` at `address`.
pub fn given(plan: &Value, code: &str, address: &str) -> Result<Given, String> {
    if plan.get("plan").and_then(Value::as_str) != Some("site-remove") {
        return Err("not a site-remove plan".to_owned());
    }
    if plan.get("site").and_then(Value::as_str) != Some(code)
        || plan.get("address").and_then(Value::as_str) != Some(address)
    {
        return Err("the plan is not for this site code and main address".to_owned());
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
        left: plan
            .get("licences_left_at_vendor")
            .and_then(Value::as_bool)
            .ok_or("the plan has no licences_left_at_vendor")?,
        plan: plan.clone(),
    })
}

/// The plan as this host makes it now, under the lock, checked against the plan given:
/// the same hash and the same whole document, and its next move is a step to run.
/// Returns the plan as journaled (unsealed, with its op).
pub fn recheck(
    spec: &SiteSpec,
    g: &Given,
    seen: &Seen,
    fingerprint: &str,
) -> Result<Value, String> {
    let p = probes(spec, seen, None, DnsProbe::NotAskedHere);
    let mut doc = plan_doc(spec, &p, g.left, DnsProbe::NotAskedHere);
    crate::quarantinecmd::seal_with_op(&mut doc, &g.op, fingerprint, env!("CARGO_PKG_VERSION"))?;
    if doc.get("plan_sha256").and_then(Value::as_str) != Some(g.sha.as_str()) || doc != g.plan {
        return Err(
            "plan_changed: this host is not as the plan saw it (or the plan was edited); make a new plan"
                .to_owned(),
        );
    }
    if doc["next"] != "run" {
        return Err(format!(
            "nothing to run: the plan says {} {} {}",
            doc["next"], doc["next_step"], doc["stop"]
        ));
    }
    Ok(opstore::unsealed(&doc))
}

/// Do one step on the host from what was just seen.
fn do_step(
    host: &mut impl RemoveHost,
    spec: &SiteSpec,
    step: RemoveStep,
    seen: &Seen,
    left: bool,
    archive_name: Option<&str>,
    removed_at: &str,
) -> Result<(), String> {
    match step {
        RemoveStep::Unserve => {
            let entry = seen
                .entry_config
                .as_deref()
                .ok_or("the entry's configuration could not be read")?;
            host.write_entry(&entry_without(entry, &spec.code, spec.primary())?)
        }
        RemoveStep::Archive => host.archive(),
        RemoveStep::Access => {
            let port = customer_ssh_port(spec.ct).ok_or("the site has no shell port")?;
            let ip = spec.ip();
            let rules = rules_to_delete(
                seen.live_rules
                    .as_deref()
                    .ok_or("the forwarding rules could not be listed")?,
                port,
                &ip,
            );
            let (file, gone) = network_file_without(
                seen.network_file
                    .as_deref()
                    .ok_or("the network file could not be read")?,
                port,
                &ip,
            );
            host.close_access(&rules, (gone > 0).then_some(file.as_str()))
        }
        RemoveStep::Stop => host.stop(),
        RemoveStep::Destroy => {
            let before = host
                .backup_schedule()
                .ok_or("the backup schedule could not be read: nothing destroyed")?;
            host.destroy()?;
            if host.backup_schedule().as_deref() != Some(before.as_str()) {
                return Err("the backup schedule changed while destroying".to_owned());
            }
            Ok(())
        }
        RemoveStep::Firewall => host.remove_firewall(),
        RemoveStep::Lists => host.write_export_list(&export_list_without(
            seen.export_list
                .as_deref()
                .ok_or("the export list could not be read")?,
            &spec.code,
        )),
        RemoveStep::Record => {
            let record = host.read_record()?;
            let left_list: &[&str] = if left { &[LEFT_ON_WORD] } else { &[] };
            let removed = removed_record(
                &record,
                removed_at,
                archive_name,
                left_list,
                DnsProbe::NotAskedHere,
            )?;
            host.move_record(&removed)
        }
        // the licences are answered inside the site and the DNS key is not on this host:
        // the plan passes the first over only on the person's word and stops before the
        // second, so neither is ever run from here
        RemoveStep::Licenses | RemoveStep::Dns => {
            Err(format!("{} is not done from this host", step.as_str()))
        }
    }
}

/// Whether, with the DNS step held for where the DNS key is, the record step is the one
/// left: every step before the DNS step is done and the record is not yet moved.
fn record_is_last_here(probes: &[Probe; 10], left: bool) -> bool {
    let mut q = *probes;
    if let Some(at) = STEPS.iter().position(|s| *s == RemoveStep::Dns) {
        q[at] = Probe::Done;
    }
    siteremove::next(&q, left) == Next::Run(RemoveStep::Record)
}

/// What is next on this host: [`siteremove::next_with_dns`], except that once only the
/// record and the DNS step are left while DNS is not asked here, the record is next. The
/// record step comes after DNS in the order, but on this host the record is moved aside
/// all the same, saying the DNS step was not asked here (as the hand-run program does: its
/// record step comes before DNS, which is a program of its own); the names for the DNS
/// step stay in the moved record. `--plan` and `--apply` both ask this, so a host found
/// with only those two left (an apply cut off after the export list, say) gets a plan
/// that `--apply` takes (33e28 M1).
pub fn next_here(probes: &[Probe; 10], left: bool, dns: DnsProbe) -> Next {
    let next = siteremove::next_with_dns(probes, left, dns);
    if next == siteremove::DNS_NOT_ASKED_HERE && record_is_last_here(probes, left) {
        return Next::Run(RemoveStep::Record);
    }
    next
}

/// Where step 10 of the hand-run program looks for what a removed site left on the host:
/// its own files under `/etc/ez2-cloud/sites`, the local copies of the daily backup of
/// its container, and what restores left behind. The archive under [`ARCHIVES`] is not
/// among them: how long it stays is a rule of its own.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Leftover {
    /// Every name in `dir` that starts with `prefix`.
    Prefixed { dir: String, prefix: String },
    /// The one path `dir/name`.
    Exact { dir: String, name: String },
}

/// The four places, made only from the site code and the container number, both checked
/// first: a code that is not a site code or a container number of the entry, the template
/// or outside the numbers a site gets gives no place at all, so nothing is removed.
pub fn leftover_places(spec: &SiteSpec) -> Result<Vec<Leftover>, String> {
    use aib_cloud::site::{valid_code, EDGE_CT, TEMPLATE_CT};
    if !valid_code(&spec.code) {
        return Err(format!("{:?} is not a site code", spec.code));
    }
    let ct = spec.ct;
    if !(100..=999_999_999).contains(&ct) || ct == EDGE_CT || ct == TEMPLATE_CT {
        return Err(format!("{ct} is not the container number of a site"));
    }
    Ok(vec![
        Leftover::Prefixed {
            dir: "/etc/ez2-cloud/sites".to_owned(),
            prefix: format!("{}.", spec.code),
        },
        Leftover::Prefixed {
            dir: "/var/lib/vz/dump".to_owned(),
            prefix: format!("vzdump-lxc-{ct}-"),
        },
        Leftover::Exact {
            dir: "/root/ez2-restore".to_owned(),
            name: spec.code.clone(),
        },
        Leftover::Exact {
            dir: "/var/lib/vz/ez2-prerestore".to_owned(),
            name: spec.code.clone(),
        },
    ])
}

/// `dir/name`, only when `name` is one plain name: no slash, not `.` or `..`, not empty.
fn under(dir: &str, name: &str) -> Option<String> {
    let plain = !name.is_empty() && name != "." && name != ".." && !name.contains('/');
    plain.then(|| format!("{dir}/{name}"))
}

/// What of this site is on the host now, place by place, every path checked to be one
/// name right under its place's directory. A directory that is not there holds nothing;
/// one that cannot be read is an error, not "nothing".
pub fn leftovers_found(
    host: &mut impl RemoveHost,
    places: &[Leftover],
) -> Result<Vec<String>, String> {
    let mut out = Vec::new();
    for place in places {
        match place {
            Leftover::Prefixed { dir, prefix } => {
                let mut names = host.list_dir(dir)?.unwrap_or_default();
                names.sort();
                for name in names {
                    if !name.starts_with(prefix.as_str()) {
                        continue;
                    }
                    out.push(
                        under(dir, &name)
                            .ok_or_else(|| format!("{dir}: {name:?} is not one plain name"))?,
                    );
                }
            }
            Leftover::Exact { dir, name } => {
                let path =
                    under(dir, name).ok_or_else(|| format!("{name:?} is not one plain name"))?;
                if host.exists(&path)? {
                    out.push(path);
                }
            }
        }
    }
    Ok(out)
}

/// Step 10 of the hand-run program: what this site left on the host removed, then looked
/// for again. Anything still there stops with its paths (as the hand-run program's
/// count does). Answers the paths removed.
pub fn clean_leftovers(host: &mut impl RemoveHost, spec: &SiteSpec) -> Result<Vec<String>, String> {
    let places = leftover_places(spec)?;
    let found = leftovers_found(host, &places)?;
    // one path that cannot be removed does not keep the others: all are tried, then counted
    let mut failed = Vec::new();
    for path in &found {
        if let Err(e) = host.remove_path(path) {
            failed.push(e);
        }
    }
    let still = leftovers_found(host, &places)?;
    if !still.is_empty() || !failed.is_empty() {
        return Err(format!(
            "{} path(s) of this site are still on the host: {still:?}; not removed: {failed:?}",
            still.len()
        ));
    }
    Ok(found)
}

/// How a step 10 that stopped is said. The record is already moved, so a new plan has
/// nothing for `--apply` to run: the person removes the paths by hand and checks with the
/// plan of the removed site, which lists what is still there.
fn leftovers_failed(spec: &SiteSpec, e: &str, applied: &[&str]) -> String {
    format!(
        "leftovers: {e}; the record is already moved, so --apply has nothing left to run: \
         remove these paths by hand as root, then run \
         `aib-cloud-host site remove {} --address {} --plan` and check that left_on_host is []; \
         done so far: {applied:?}",
        spec.code,
        spec.primary()
    )
}

/// `--plan` for a site whose record was moved aside: no step is probed, but what the
/// site left on the host is looked for (step 10, read only), so a step 10 that stopped
/// can be checked again once the paths are removed by hand. `spec` is read from the moved
/// record; the paths are listed only when the code and the main address typed are its.
pub fn removed_doc(
    code: &str,
    address: &str,
    spec: Result<SiteSpec, String>,
    look: impl FnOnce(&SiteSpec) -> Result<Vec<String>, String>,
) -> Value {
    let (left, note) = match spec {
        Ok(s) if !siteremove::confirmed(&s.code, s.primary(), code, address) => (
            Value::Null,
            Value::from("the site code and the main address given are not those of one site"),
        ),
        Ok(s) => match look(&s) {
            Ok(found) => (Value::from(found), Value::Null),
            Err(e) => (Value::Null, Value::from(e)),
        },
        Err(e) => (Value::Null, Value::from(format!("the moved record: {e}"))),
    };
    json!({
        "plan": "site-remove",
        "site": code,
        "already_removed": true,
        "probed": false,
        "left_on_host": left,
        "left_on_host_note": note,
    })
}

/// Carry the removal out from where the host is: look, ask [`next_here`] what is next
/// (the same rule as the plan), do that step, check every other site still
/// answers as in `base`, and look again. A step that is still not done when looked at
/// again stops the work; so does any stop of the rule, except the DNS step, which is done
/// where the DNS key is (the answer then says so, with the names).
pub fn carry_out(
    host: &mut impl RemoveHost,
    spec: &SiteSpec,
    left: bool,
    base: &[(String, Option<u16>)],
    removed_at: &str,
) -> Result<Value, String> {
    let mut applied: Vec<RemoveStep> = Vec::new();
    let mut archive_name: Option<String> = None;
    let words = |a: &[RemoveStep]| a.iter().map(|s| s.as_str()).collect::<Vec<_>>();
    for _ in 0..=STEPS.len() {
        let seen = host.see();
        if seen.archive_name.is_some() {
            archive_name.clone_from(&seen.archive_name);
        }
        let p = probes(spec, &seen, None, DnsProbe::NotAskedHere);
        let next = next_here(&p, left, DnsProbe::NotAskedHere);
        let (state, why) = match next {
            Next::Run(step) => {
                if applied.contains(&step) {
                    return Err(format!(
                        "{}: done, but not done when looked at again; done so far: {:?}",
                        step.as_str(),
                        words(&applied)
                    ));
                }
                do_step(
                    host,
                    spec,
                    step,
                    &seen,
                    left,
                    archive_name.as_deref(),
                    removed_at,
                )
                .map_err(|e| {
                    format!(
                        "{} failed: {e}; done so far: {:?}",
                        step.as_str(),
                        words(&applied)
                    )
                })?;
                applied.push(step);
                same_as_base(base, &host.others()).map_err(|e| {
                    format!(
                        "after {}: {e}; done so far: {:?}",
                        step.as_str(),
                        words(&applied)
                    )
                })?;
                continue;
            }
            Next::Finished => ("finished", None),
            // the DNS step is done where the key is: everything else is done here
            Next::Stop(why, RemoveStep::Dns) if next == siteremove::DNS_NOT_ASKED_HERE => {
                ("dns_left", Some(why))
            }
            Next::Stop(why, step) => {
                return Err(format!(
                    "stopped at {}: {why}; done so far: {:?}",
                    step.as_str(),
                    words(&applied)
                ))
            }
        };
        // the record is moved (finished, or only the DNS step is left): step 10
        let removed = clean_leftovers(host, spec)
            .map_err(|e| leftovers_failed(spec, &e, &words(&applied)))?;
        // what is found now: always [] here, anything still there stopped just above
        let left_on_host = leftovers_found(host, &leftover_places(spec)?)?;
        return Ok(json!({
            "applied": words(&applied),
            "state": state,
            "stop": why,
            "archive": archive_name,
            "dns_names": spec.hosts,
            "dns_note": if why.is_some() { Value::from(DNS_NOT_HERE) } else { Value::Null },
            "removed_from_host": removed,
            "left_on_host": left_on_host,
        }));
    }
    Err(format!(
        "more steps than there are; done so far: {:?}",
        words(&applied)
    ))
}

/// `--apply`: the plan given is recomputed under the host lock and must be the same,
/// every other site is measured once (`base`), then the steps run as operation `op` of
/// the plan. Separate from [`cmd`] so it runs against a scratch place and a made-up host.
pub fn apply(
    place: &opstore::OpPlace,
    plan: &Value,
    spec: &SiteSpec,
    address: &str,
    host: &mut impl RemoveHost,
    removed_at: &str,
) -> Result<Value, String> {
    let g = given(plan, &spec.code, address)?;
    let host = std::cell::RefCell::new(host);
    let done = opstore::run_op_prepared_as(
        place,
        &g.op,
        COMMAND,
        &format!("site:{}", spec.code),
        opstore::BY_LOCAL_ROOT,
        || {
            let mut h = host.borrow_mut();
            let seen = h.see();
            let doc = recheck(spec, &g, &seen, &place.fingerprint)?;
            let base = h.others();
            same_as_base(&base, &base)
                .map_err(|e| format!("before starting: {e}; nothing was changed"))?;
            Ok(Some((doc, base)))
        },
        |_op, base: Vec<(String, Option<u16>)>| {
            let mut h = host.borrow_mut();
            carry_out(&mut **h, spec, g.left, &base, removed_at)
        },
    )?;
    done.ok_or_else(|| "nothing to do".to_owned())
}

/// The real host.
pub struct HostRemover {
    pub spec: SiteSpec,
    pub records: String,
    pub stamp: String,
}

fn run(program: &str, args: &[&str], seconds: u64) -> Result<String, String> {
    let mut cmd = Command::new(program);
    cmd.args(args);
    run_capture(&mut cmd, Duration::from_secs(seconds)).map_err(|e| format!("{program}: {e}"))
}

/// Write `text` to `path` whole: a temporary file beside it, synced, then renamed.
fn write_whole(path: &Path, text: &str, mode: u32) -> Result<(), String> {
    use std::io::Write as _;
    use std::os::unix::fs::OpenOptionsExt as _;
    let name = path
        .file_name()
        .ok_or("no file name")?
        .to_string_lossy()
        .into_owned();
    let tmp = path.with_file_name(format!(".{name}.tmp-{}", std::process::id()));
    let _ = fs::remove_file(&tmp);
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(mode)
        .open(&tmp)
        .map_err(|e| format!("{}: {e}", tmp.display()))?;
    f.write_all(text.as_bytes())
        .and_then(|()| f.sync_all())
        .and_then(|()| fs::rename(&tmp, path))
        .map_err(|e| format!("{}: {e}", path.display()))
}

/// A copy of a host file before it is rewritten, kept under [`KEEP`].
fn keep_copy(path: &str, label: &str, stamp: &str) -> Result<(), String> {
    fs::create_dir_all(KEEP).map_err(|e| format!("{KEEP}: {e}"))?;
    let to = format!("{KEEP}/{label}.before-{stamp}");
    fs::copy(path, &to)
        .map(|_| ())
        .map_err(|e| format!("{to}: {e}"))
}

impl RemoveHost for HostRemover {
    fn see(&mut self) -> Seen {
        see(&self.spec, &self.records)
    }

    fn others(&mut self) -> Vec<(String, Option<u16>)> {
        let mut out = Vec::new();
        let Ok(entries) = fs::read_dir(&self.records) else {
            return vec![(format!("(the records in {})", self.records), None)];
        };
        let mut addresses = Vec::new();
        for e in entries.flatten() {
            let name = e.file_name().to_string_lossy().into_owned();
            if !name.ends_with(".json") || name == format!("{}.json", self.spec.code) {
                continue;
            }
            let first = fs::read_to_string(e.path())
                .ok()
                .and_then(|t| serde_json::from_str::<Value>(&t).ok())
                .and_then(|v| {
                    v.get("hosts").and_then(Value::as_str).and_then(|h| {
                        h.split(|c: char| c == ',' || c.is_whitespace())
                            .find(|x| !x.is_empty())
                            .map(str::to_owned)
                    })
                });
            addresses.push(first.unwrap_or_else(|| format!("(record {name})")));
        }
        addresses.sort();
        for h in addresses {
            let code = if aib_cloud::edge::valid_domain(&h) {
                let resolve = format!("{h}:443:127.0.0.1");
                let url = format!("https://{h}/");
                run(
                    "pct",
                    &[
                        "exec",
                        crate::probe::EDGE_CT,
                        "--",
                        "curl",
                        "-s",
                        "-o",
                        "/dev/null",
                        "-m",
                        "10",
                        "-w",
                        "%{http_code}",
                        "--resolve",
                        &resolve,
                        &url,
                    ],
                    20,
                )
                .ok()
                .and_then(|c| c.trim().parse::<u16>().ok())
                .filter(|c| *c != 0)
            } else {
                None
            };
            out.push((h, code));
        }
        out
    }

    fn write_entry(&mut self, text: &str) -> Result<(), String> {
        crate::probe::edge_apply_caddyfile(text).map(|_| ())
    }

    fn archive(&mut self) -> Result<(), String> {
        let dir = Path::new(ARCHIVES).join(&self.spec.code);
        fs::create_dir_all(&dir).map_err(|e| format!("{}: {e}", dir.display()))?;
        let dir = dir.to_str().ok_or("the archive directory is not text")?;
        let ct = self.spec.ct.to_string();
        run(
            "vzdump",
            &[
                &ct,
                "--mode",
                "snapshot",
                "--compress",
                "zstd",
                "--dumpdir",
                dir,
            ],
            3600,
        )
        .map(|_| ())
    }

    // The live rules go first, then the file; a file that does not parse is put back, the
    // live rules stay deleted (towards closed). The new file was made from the text read
    // when the step began: a change made to it by hand in between is overwritten (the
    // host lock holds back only our own tools; 33e28 N3).
    fn close_access(
        &mut self,
        rules: &[Vec<String>],
        network_file: Option<&str>,
    ) -> Result<(), String> {
        for r in rules {
            let mut args = vec!["-w", "-t", "nat"];
            args.extend(r.iter().map(String::as_str));
            run("iptables", &args, 20)?;
        }
        if let Some(text) = network_file {
            keep_copy(
                NETWORK_FILE,
                &format!("interfaces-{}", self.spec.code),
                &self.stamp,
            )?;
            let before =
                fs::read_to_string(NETWORK_FILE).map_err(|e| format!("{NETWORK_FILE}: {e}"))?;
            write_whole(Path::new(NETWORK_FILE), text, 0o644)?;
            if let Err(e) = run("ifquery", &["-a"], 30) {
                write_whole(Path::new(NETWORK_FILE), &before, 0o644)?;
                return Err(format!(
                    "the new network file did not parse, the old one was put back: {e}"
                ));
            }
        }
        Ok(())
    }

    fn stop(&mut self) -> Result<(), String> {
        run("pct", &["stop", &self.spec.ct.to_string()], 180).map(|_| ())
    }

    fn destroy(&mut self) -> Result<(), String> {
        let ct = self.spec.ct.to_string();
        run(
            "pct",
            &[
                "destroy",
                &ct,
                "--purge",
                "1",
                "--destroy-unreferenced-disks",
                "1",
            ],
            600,
        )
        .map(|_| ())
    }

    fn backup_schedule(&mut self) -> Option<String> {
        schedule_text(&BACKUP_SCHEDULES.map(Path::new))
    }

    fn remove_firewall(&mut self) -> Result<(), String> {
        let path = format!("/etc/pve/firewall/{}.fw", self.spec.ct);
        match fs::remove_file(&path) {
            Ok(()) => Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(e) => Err(format!("{path}: {e}")),
        }
    }

    fn write_export_list(&mut self, text: &str) -> Result<(), String> {
        keep_copy(
            EXPORT_LIST,
            &format!("sites.list-{}", self.spec.code),
            &self.stamp,
        )?;
        write_whole(Path::new(EXPORT_LIST), text, 0o644)
    }

    fn read_record(&mut self) -> Result<Value, String> {
        let path = format!("{}/{}.json", self.records, self.spec.code);
        let text = fs::read_to_string(&path).map_err(|e| format!("{path}: {e}"))?;
        serde_json::from_str(&text).map_err(|e| format!("{path}: {e}"))
    }

    fn move_record(&mut self, removed: &Value) -> Result<(), String> {
        fs::create_dir_all(REMOVED_RECORDS).map_err(|e| format!("{REMOVED_RECORDS}: {e}"))?;
        let to = format!("{REMOVED_RECORDS}/{}.json", self.spec.code);
        let text = serde_json::to_string_pretty(removed).map_err(|e| e.to_string())? + "\n";
        write_whole(Path::new(&to), &text, 0o600)?;
        let from = format!("{}/{}.json", self.records, self.spec.code);
        fs::remove_file(&from).map_err(|e| format!("{from}: {e}"))
    }

    fn list_dir(&mut self, dir: &str) -> Result<Option<Vec<String>>, String> {
        let rd = match fs::read_dir(dir) {
            Ok(rd) => rd,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(e) => return Err(format!("{dir}: {e}")),
        };
        let mut names = Vec::new();
        for e in rd {
            let e = e.map_err(|e| format!("{dir}: {e}"))?;
            names.push(
                e.file_name()
                    .into_string()
                    .map_err(|n| format!("{dir}: {n:?} is not text"))?,
            );
        }
        Ok(Some(names))
    }

    fn exists(&mut self, path: &str) -> Result<bool, String> {
        match fs::symlink_metadata(path) {
            Ok(_) => Ok(true),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(false),
            Err(e) => Err(format!("{path}: {e}")),
        }
    }

    // a link is removed as itself, never followed
    fn remove_path(&mut self, path: &str) -> Result<(), String> {
        let meta = fs::symlink_metadata(path).map_err(|e| format!("{path}: {e}"))?;
        if meta.is_dir() {
            fs::remove_dir_all(path)
        } else {
            fs::remove_file(path)
        }
        .map_err(|e| format!("{path}: {e}"))
    }
}

/// `--apply <file>` on this host. Writing: run only as a reviewed, approved operation
/// (see [`opstore::BY_LOCAL_ROOT`]: until the remote CLI's one-time approvals exist, a
/// write on the host goes through a signed script a person approved).
fn apply_cmd(code: &str, address: &str, file: &str, records: &str) -> Result<u8, String> {
    let text = fs::read_to_string(file).map_err(|e| format!("{file}: {e}"))?;
    let plan: Value = serde_json::from_str(&text).map_err(|e| format!("{file}: {e}"))?;
    let spec = crate::quarantinecmd::load_spec(code, records)?;
    if !siteremove::confirmed(&spec.code, spec.primary(), code, address) {
        return Err(
            "the site code and the main address given are not those of one site".to_owned(),
        );
    }
    let place = opstore::OpPlace::host()?;
    let stamp = opstore::op_id(crate::probe::unix_now(), [0; 4])
        .split('-')
        .next()
        .unwrap_or_default()
        .to_owned();
    let mut host = HostRemover {
        spec: spec.clone(),
        records: records.to_owned(),
        stamp: stamp.clone(),
    };
    let done = apply(&place, &plan, &spec, address, &mut host, &stamp)?;
    println!(
        "{}",
        serde_json::to_string_pretty(&done).map_err(|e| e.to_string())?
    );
    Ok(0)
}

pub fn cmd(args: &[String]) -> Result<u8, String> {
    let code = args
        .first()
        .filter(|a| !a.starts_with("--"))
        .ok_or("needs <site code>")?
        .clone();
    let mut records = RECORDS.to_owned();
    let mut address: Option<String> = None;
    let mut plan_only = false;
    let mut apply_file: Option<String> = None;
    let mut left = false;
    let mut it = args[1..].iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--plan" => plan_only = true,
            "--apply" => apply_file = Some(it.next().ok_or("--apply needs a plan file")?.clone()),
            "--licences-left-at-vendor" => left = true,
            "--records" => records = it.next().ok_or("--records needs a dir")?.clone(),
            "--address" => {
                address = Some(
                    it.next()
                        .ok_or("--address needs the site's main address")?
                        .clone(),
                );
            }
            other => return Err(format!("unknown argument {other}")),
        }
    }
    match (plan_only, &apply_file) {
        (true, None) | (false, Some(_)) => {}
        (true, Some(_)) => return Err("--plan or --apply, not both".to_owned()),
        (false, None) => {
            return Err(
                "needs --plan, or --apply <plan.json> with a plan this command printed".to_owned(),
            )
        }
    }
    let address = address.ok_or("removing a site needs --address <its main address>")?;
    if let Some(file) = apply_file {
        if left {
            return Err(
                "--licences-left-at-vendor belongs to --plan: the plan given to --apply says it"
                    .to_owned(),
            );
        }
        return apply_cmd(&code, &address, &file, &records);
    }
    if exists(&format!("{records}/{code}.json")) == Some(false)
        && exists(&format!("{REMOVED_RECORDS}/{code}.json")) == Some(true)
    {
        // the record was moved aside: said as found, nothing probed but what step 10 removes
        let spec = crate::quarantinecmd::load_spec(&code, REMOVED_RECORDS);
        let doc = removed_doc(&code, &address, spec, |s| {
            let mut host = HostRemover {
                spec: s.clone(),
                records: records.clone(),
                stamp: String::new(),
            };
            leftovers_found(&mut host, &leftover_places(s)?)
        });
        println!("{doc}");
        return Ok(0);
    }
    let spec = crate::quarantinecmd::load_spec(&code, &records)?;
    if !siteremove::confirmed(&spec.code, spec.primary(), &code, &address) {
        // the same words whether the code or the address is the one that is off
        return Err(
            "the site code and the main address given are not those of one site".to_owned(),
        );
    }
    let seen = see(&spec, &records);
    let p = probes(&spec, &seen, None, DnsProbe::NotAskedHere);
    let mut doc = plan_doc(&spec, &p, left, DnsProbe::NotAskedHere);
    let op = opstore::op_id(crate::probe::unix_now(), opstore::random4()?);
    let fingerprint = opstore::host_fingerprint(Path::new(opstore::MACHINE_ID))?;
    crate::quarantinecmd::seal_with_op(&mut doc, &op, &fingerprint, env!("CARGO_PKG_VERSION"))?;
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
    use aib_cloud::site::spec_from_record;

    fn spec() -> SiteSpec {
        spec_from_record(&json!({
            "site": "site9", "ct": 119, "ip": "10.77.0.19", "hosts": "www.example.com",
            "wp": 1, "locale": "en_US", "created": "2026-01-01T00:00:00Z", "template": 900,
            "limits": { "cores": 2, "cpulimit_x100": 200, "memory_mb": 1024, "swap_mb": 512, "pids_max": 512 }
        }))
        .unwrap()
    }

    const LIST_WITH: &str =
        "VMID       Status     Lock         Name\n119        running                 site9\n";
    const LIST_WITHOUT: &str =
        "VMID       Status     Lock         Name\n101        running                 edge\n";
    const RULES_WITH: &str = "-P PREROUTING ACCEPT\n-A PREROUTING -i eth9 -p tcp -m tcp --dport 2119 -j DNAT --to-destination 10.77.0.19:22\n";
    const RULES_WITHOUT: &str = "-P PREROUTING ACCEPT\n";

    fn untouched() -> Seen {
        Seen {
            entry_config: Some("# site:site9\nwww.example.com {\n}\n".to_owned()),
            container_list: Some(LIST_WITH.to_owned()),
            running: Some(true),
            disk: Some(true),
            archive: Some(Probe::NotDone),
            archive_name: None,
            live_rules: Some(RULES_WITH.to_owned()),
            network_file: Some("iface x\n".to_owned()),
            firewall_file: Some(true),
            export_list: Some("site9 119 /var/www/site9\n".to_owned()),
            record_in_place: Some(true),
        }
    }

    fn gone() -> Seen {
        Seen {
            entry_config: Some("# site:other\nother.example.com {\n}\n".to_owned()),
            container_list: Some(LIST_WITHOUT.to_owned()),
            running: None,
            disk: Some(false),
            archive: Some(Probe::Done),
            archive_name: Some("vzdump-lxc-119-2026_01_01-00_00_00.tar.zst".to_owned()),
            live_rules: Some(RULES_WITHOUT.to_owned()),
            network_file: Some("iface x\n".to_owned()),
            firewall_file: Some(false),
            export_list: Some("other 101 /var/www/other\n".to_owned()),
            record_in_place: Some(false),
        }
    }

    #[test]
    fn an_untouched_site_has_nothing_done_and_starts_at_the_entry() {
        let p = probes(
            &spec(),
            &untouched(),
            Some(Probe::NotDone),
            DnsProbe::NotDone,
        );
        assert!(p.iter().all(|x| *x == Probe::NotDone), "{p:?}");
        let doc = plan_doc(&spec(), &p, false, DnsProbe::NotDone);
        assert_eq!(doc["next"], "run");
        assert_eq!(doc["next_step"], "unserve");
        assert_eq!(doc["may_call_off"], true);
        assert_eq!(doc["address"], "www.example.com");
    }

    #[test]
    fn a_plan_that_stops_never_says_it_can_be_called_off() {
        // the entry could not be read while the container is already gone
        let mut seen = gone();
        seen.entry_config = None;
        let p = probes(&spec(), &seen, Some(Probe::Done), DnsProbe::Done);
        let doc = plan_doc(&spec(), &p, false, DnsProbe::Done);
        assert_eq!(doc["next"], "stop");
        assert_eq!(doc["next_step"], "unserve");
        assert_eq!(doc["may_call_off"], false);
        // steps out of order: nothing is archived and the firewall file is already gone
        let mut seen = untouched();
        seen.firewall_file = Some(false);
        let p = probes(&spec(), &seen, Some(Probe::NotDone), DnsProbe::NotDone);
        let doc = plan_doc(&spec(), &p, false, DnsProbe::NotDone);
        assert_eq!(doc["next"], "stop");
        assert_eq!(doc["stop"], "steps_out_of_order");
        assert_eq!(doc["may_call_off"], false);
    }

    #[test]
    fn a_site_whose_port_was_never_forwarded_starts_at_the_entry() {
        let mut seen = untouched();
        seen.live_rules = gone().live_rules;
        let p = probes(&spec(), &seen, Some(Probe::NotDone), DnsProbe::NotDone);
        let doc = plan_doc(&spec(), &p, false, DnsProbe::NotDone);
        assert_eq!(doc["next"], "run");
        assert_eq!(doc["next_step"], "unserve");
        assert_eq!(doc["may_call_off"], true);
    }

    #[test]
    fn a_removed_site_has_everything_done() {
        let p = probes(&spec(), &gone(), Some(Probe::Done), DnsProbe::Done);
        assert!(p.iter().all(|x| *x == Probe::Done), "{p:?}");
        assert_eq!(
            plan_doc(&spec(), &p, false, DnsProbe::Done)["next"],
            "finished"
        );
    }

    #[test]
    fn the_licences_are_not_guessed() {
        // not wired: unknown, and the plan stops there once the archive is done
        let mut seen = untouched();
        seen.entry_config = gone().entry_config;
        seen.archive = Some(Probe::Done);
        // (the DNS step as the command passes it: not asked here)
        let p = probes(&spec(), &seen, None, DnsProbe::NotAskedHere);
        let doc = plan_doc(&spec(), &p, false, DnsProbe::NotAskedHere);
        assert_eq!(doc["next"], "stop");
        assert_eq!(doc["next_step"], "licenses");
        assert_eq!(doc["stop"], "probe_unknown");
        // the person's explicit word passes it over, and the plan says so
        let doc = plan_doc(&spec(), &p, true, DnsProbe::NotAskedHere);
        assert_eq!(doc["next"], "run");
        assert_eq!(doc["next_step"], "access");
        assert_eq!(doc["licences_left_at_vendor"], true);
        assert_eq!(doc["may_call_off"], false);
    }

    #[test]
    fn what_could_not_be_read_is_unknown_not_absent() {
        let at = |s: RemoveStep| STEPS.iter().position(|x| *x == s).unwrap();
        let mut seen = gone();
        seen.container_list = None;
        let p = probes(&spec(), &seen, Some(Probe::Done), DnsProbe::Done);
        assert_eq!(p[at(RemoveStep::Stop)], Probe::Unknown);
        assert_eq!(p[at(RemoveStep::Destroy)], Probe::Unknown);
        let mut seen = gone();
        seen.disk = None;
        assert_eq!(
            probes(&spec(), &seen, Some(Probe::Done), DnsProbe::Done)[at(RemoveStep::Destroy)],
            Probe::Unknown
        );
        let mut seen = gone();
        seen.firewall_file = None;
        assert_eq!(
            probes(&spec(), &seen, Some(Probe::Done), DnsProbe::Done)[at(RemoveStep::Firewall)],
            Probe::Unknown
        );
        let mut seen = gone();
        seen.record_in_place = None;
        assert_eq!(
            probes(&spec(), &seen, Some(Probe::Done), DnsProbe::Done)[at(RemoveStep::Record)],
            Probe::Unknown
        );
        let mut seen = gone();
        seen.archive = None;
        assert_eq!(
            probes(&spec(), &seen, Some(Probe::Done), DnsProbe::Done)[at(RemoveStep::Archive)],
            Probe::Unknown
        );
        // a listed container whose state was not answered
        let mut seen = untouched();
        seen.running = None;
        assert_eq!(
            probes(&spec(), &seen, Some(Probe::Done), DnsProbe::Done)[at(RemoveStep::Stop)],
            Probe::Unknown
        );
        // the container is gone and its disk is still there: not destroyed
        let mut seen = gone();
        seen.disk = Some(true);
        assert_eq!(
            probes(&spec(), &seen, Some(Probe::Done), DnsProbe::Done)[at(RemoveStep::Destroy)],
            Probe::NotDone
        );
    }

    #[test]
    fn an_archive_is_looked_for_by_the_containers_number() {
        let dir = std::env::temp_dir().join(format!(
            "aib-rm-{}-{}",
            std::process::id(),
            crate::probe::unix_now()
        ));
        // asked at each look: a file written after `now` would be one from the future
        let look = |fresh| archive_probe(&dir, 119, fresh, SystemTime::now()).0;
        // no directory, then an empty one, then another container's archive: not done
        assert_eq!(look(true), Probe::NotDone);
        fs::create_dir_all(&dir).unwrap();
        assert_eq!(look(true), Probe::NotDone);
        fs::write(
            dir.join("vzdump-lxc-1190-2026_01_01-00_00_00.tar.zst"),
            b"x",
        )
        .unwrap();
        assert_eq!(look(true), Probe::NotDone);
        // a file of this container that is not a whole archive: could not tell, never done
        let mine = dir.join("vzdump-lxc-119-2026_01_01-00_00_00.tar.zst");
        fs::write(&mine, b"not an archive").unwrap();
        assert_eq!(look(true), Probe::Unknown);
        assert_eq!(archive_probe(&dir, 119, true, SystemTime::now()).1, None);
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn an_archive_older_than_a_day_counts_only_once_the_container_is_gone() {
        let dir = std::env::temp_dir().join(format!(
            "aib-rm-old-{}-{}",
            std::process::id(),
            crate::probe::unix_now()
        ));
        fs::create_dir_all(&dir).unwrap();
        let old = dir.join("vzdump-lxc-119-2026_01_01-00_00_00.tar.zst");
        fs::write(&old, b"not an archive").unwrap();
        let now = SystemTime::now();
        let two_days = now - Duration::from_secs(2 * FRESH_SECS);
        fs::File::options()
            .write(true)
            .open(&old)
            .unwrap()
            .set_modified(two_days)
            .unwrap();
        // the container is there: an archive two days old is passed over, as if none
        assert_eq!(archive_probe(&dir, 119, true, now).0, Probe::NotDone);
        // the container is gone: it is looked at (and here it is no archive at all)
        assert_eq!(archive_probe(&dir, 119, false, now).0, Probe::Unknown);
        // one made a moment ago counts while the container is there
        assert_eq!(
            archive_probe(&dir, 119, true, two_days + Duration::from_secs(60)).0,
            Probe::Unknown
        );
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn nothing_but_a_plan() {
        let args: Vec<String> = ["site9", "--address", "www.example.com"]
            .iter()
            .map(|s| (*s).to_owned())
            .collect();
        assert!(cmd(&args).unwrap_err().contains("needs --plan"));
        let args: Vec<String> = ["site9", "--plan"]
            .iter()
            .map(|s| (*s).to_owned())
            .collect();
        assert!(cmd(&args).unwrap_err().contains("--address"));
    }

    #[test]
    fn the_dns_step_is_not_asked_on_this_host_and_the_plan_says_why() {
        let at = |s: RemoveStep| STEPS.iter().position(|x| *x == s).unwrap();
        // what the command itself passes: not asked here
        let p = probes(&spec(), &gone(), Some(Probe::Done), DnsProbe::NotAskedHere);
        assert_eq!(p[at(RemoveStep::Dns)], Probe::NotDone);
        // everything else done, the record moved: the plan stops at DNS and says why
        let doc = plan_doc(&spec(), &p, false, DnsProbe::NotAskedHere);
        assert_eq!(doc["next"], "stop");
        assert_eq!(doc["next_step"], "dns");
        assert_eq!(doc["stop"], "dns_not_asked_here");
        assert_eq!(doc["dns"], "not_asked_here");
        // only the record and DNS left: on this host the record is next (33e28 M1)
        let mut p2 = p;
        p2[at(RemoveStep::Record)] = Probe::NotDone;
        let doc = plan_doc(&spec(), &p2, false, DnsProbe::NotAskedHere);
        assert_eq!(doc["next"], "run");
        assert_eq!(doc["next_step"], "record");
        assert_eq!(doc["dns_note"], DNS_NOT_HERE);
        assert_eq!(doc["dns_names"], json!(["www.example.com"]));
        // a zone that is not ours is passed over and said
        let p = probes(&spec(), &gone(), Some(Probe::Done), DnsProbe::Skipped);
        let doc = plan_doc(&spec(), &p, false, DnsProbe::Skipped);
        assert_eq!(doc["next"], "finished");
        assert_eq!(doc["dns"], "skipped_zone_not_ours");
        assert!(doc["dns_note"].is_null());
        // an untouched site in a zone that is not ours starts at the entry, as always
        let p = probes(
            &spec(),
            &untouched(),
            Some(Probe::NotDone),
            DnsProbe::Skipped,
        );
        let doc = plan_doc(&spec(), &p, false, DnsProbe::Skipped);
        assert_eq!(doc["next_step"], "unserve");
        assert_eq!(doc["may_call_off"], true);
        // a record of ours is left once everything before it is done
        let p = probes(&spec(), &gone(), Some(Probe::Done), DnsProbe::NotDone);
        let mut p2 = p;
        p2[at(RemoveStep::Record)] = Probe::NotDone;
        let doc = plan_doc(&spec(), &p2, false, DnsProbe::NotDone);
        assert_eq!(doc["next_step"], "dns");
        assert_eq!(doc["may_call_off"], false);
    }

    #[test]
    fn what_the_command_passes_lets_an_untouched_site_start_at_the_entry() {
        // exactly what `cmd` passes: licences not wired, DNS not asked here
        let p = probes(&spec(), &untouched(), None, DnsProbe::NotAskedHere);
        let doc = plan_doc(&spec(), &p, true, DnsProbe::NotAskedHere);
        assert_eq!(doc["next"], "run");
        assert_eq!(doc["next_step"], "unserve");
        assert_eq!(doc["may_call_off"], true);
        assert_eq!(doc["dns"], "not_asked_here");
        assert_eq!(doc["done_early"], json!([]));
        // a site whose port was never forwarded: still the entry, and the plan says what is early
        let mut seen = untouched();
        seen.live_rules = gone().live_rules;
        let p = probes(&spec(), &seen, None, DnsProbe::NotAskedHere);
        let doc = plan_doc(&spec(), &p, true, DnsProbe::NotAskedHere);
        assert_eq!(doc["next_step"], "unserve");
        assert_eq!(doc["may_call_off"], true);
        assert_eq!(doc["done_early"], json!(["access"]));
    }

    #[test]
    fn the_removed_record_keeps_what_was_left_at_vendors_and_what_dns_found() {
        let rec = json!({ "site": "site9", "ct": 119, "hosts": "www.example.com" });
        let out = removed_record(
            &rec,
            "20260101T000000Z",
            None,
            &["blocks", "theme"],
            DnsProbe::DoneForeignLeft,
        )
        .unwrap();
        assert_eq!(out["site"], "site9");
        assert_eq!(out["removed"], "20260101T000000Z");
        assert!(out["archive"].is_null());
        assert_eq!(out["left_at_vendor"], json!(["blocks", "theme"]));
        assert_eq!(out["dns"], "done_records_without_our_marker_left");
        let out = removed_record(&rec, "t", Some("a.tar.zst"), &[], DnsProbe::Skipped).unwrap();
        assert_eq!(out["archive"], "a.tar.zst");
        assert_eq!(out["left_at_vendor"], json!([]));
        assert_eq!(out["dns"], "skipped_zone_not_ours");
        assert!(removed_record(&json!([]), "t", None, &[], DnsProbe::Done).is_err());
    }

    // ------------------------------------------------------------------ apply

    use aib_cloud::op::State;

    const OP: &str = "20261111T120000Z-ab12cd34";
    const ENTRY: &str = "{\n\temail ops@example.test\n}\n\n# site:site9\nwww.example.com {\n\treverse_proxy 10.77.0.19:80\n}\n\n# site:other\nother.example.com {\n\treverse_proxy 10.77.0.20:80\n}\n";
    const NETFILE: &str = "iface vmbr0 inet static\n\tpost-up iptables -t nat -A PREROUTING -i eth9 -p tcp -m tcp --dport 2119 -j DNAT --to-destination 10.77.0.19:22\n\tpost-up iptables -t nat -A PREROUTING -i eth9 -p tcp -m tcp --dport 2120 -j DNAT --to-destination 10.77.0.20:22\n";
    const ARCH: &str = "vzdump-lxc-119-2026_11_11-12_00_00.tar.zst";

    fn scratch(name: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("aib-siteremove-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

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

    /// A made-up host that changes as it is told and remembers every change asked.
    struct FakeHost {
        entry: String,
        listed: bool,
        running: bool,
        disk: bool,
        archive: Option<String>,
        rules: String,
        netfile: String,
        fw: bool,
        export: String,
        record_in_place: bool,
        schedule: String,
        others: Vec<(String, Option<u16>)>,
        others_asked: usize,
        /// From this many `others` calls on, another site answers differently.
        others_change_at: Option<usize>,
        /// A step whose call answers Ok without changing anything.
        no_effect: Option<&'static str>,
        /// Destroying also rewrites the backup schedule.
        destroy_touches_schedule: bool,
        moved: Option<Value>,
        /// Every path on the made-up host's disk that step 10 looks at.
        files: Vec<String>,
        /// A path that `remove_path` answers Ok for without removing.
        sticky: Option<&'static str>,
        /// A path that `remove_path` refuses.
        refused: Option<&'static str>,
        calls: Vec<String>,
    }

    fn fake() -> FakeHost {
        FakeHost {
            entry: ENTRY.to_owned(),
            listed: true,
            running: true,
            disk: true,
            archive: None,
            rules: RULES_WITH.to_owned(),
            netfile: NETFILE.to_owned(),
            fw: true,
            export: "site9 119 /var/www/site9\nother 120 /var/www/other\n".to_owned(),
            record_in_place: true,
            schedule: "0 2 * * * root vzdump --all 1\n".to_owned(),
            others: vec![("other.example.com".to_owned(), Some(200))],
            others_asked: 0,
            others_change_at: None,
            no_effect: None,
            destroy_touches_schedule: false,
            moved: None,
            files: FILES.iter().map(|f| (*f).to_owned()).collect(),
            sticky: None,
            refused: None,
            calls: Vec::new(),
        }
    }

    /// The made-up host's disk: this site's (site9, ct 119) leftovers among others'.
    const FILES: [&str; 9] = [
        "/etc/ez2-cloud/sites/site9.secret",
        "/etc/ez2-cloud/sites/site9.admin.anna",
        "/etc/ez2-cloud/sites/site99.secret",
        "/etc/ez2-cloud/sites/other.secret",
        "/var/lib/vz/dump/vzdump-lxc-119-2026_11_10-02_00_00.tar.zst",
        "/var/lib/vz/dump/vzdump-lxc-1190-2026_11_10-02_00_00.tar.zst",
        "/var/lib/vz/dump/vzdump-lxc-120-2026_11_10-02_00_00.tar.zst",
        "/root/ez2-restore/site9",
        "/var/lib/vz/ez2-prerestore/site99",
    ];

    impl FakeHost {
        fn did(&mut self, what: &'static str) -> bool {
            self.calls.push(what.to_owned());
            self.no_effect != Some(what)
        }
    }

    impl RemoveHost for FakeHost {
        fn see(&mut self) -> Seen {
            Seen {
                entry_config: Some(self.entry.clone()),
                container_list: Some(if self.listed { LIST_WITH } else { LIST_WITHOUT }.to_owned()),
                running: self.listed.then_some(self.running),
                disk: Some(self.disk),
                archive: Some(if self.archive.is_some() {
                    Probe::Done
                } else {
                    Probe::NotDone
                }),
                archive_name: self.archive.clone(),
                live_rules: Some(self.rules.clone()),
                network_file: Some(self.netfile.clone()),
                firewall_file: Some(self.fw),
                export_list: Some(self.export.clone()),
                record_in_place: Some(self.record_in_place),
            }
        }
        fn others(&mut self) -> Vec<(String, Option<u16>)> {
            self.others_asked += 1;
            if self.others_change_at.is_some_and(|n| self.others_asked > n) {
                return vec![("other.example.com".to_owned(), Some(502))];
            }
            self.others.clone()
        }
        fn write_entry(&mut self, text: &str) -> Result<(), String> {
            if self.did("write_entry") {
                self.entry = text.to_owned();
            }
            Ok(())
        }
        fn archive(&mut self) -> Result<(), String> {
            if self.did("archive") {
                self.archive = Some(ARCH.to_owned());
            }
            Ok(())
        }
        fn close_access(
            &mut self,
            rules: &[Vec<String>],
            file: Option<&str>,
        ) -> Result<(), String> {
            self.calls
                .push(format!("close_access {} {}", rules.len(), file.is_some()));
            for r in rules {
                let line = format!("-A {}", r[1..].join(" "));
                self.rules = self
                    .rules
                    .lines()
                    .filter(|l| *l != line)
                    .map(|l| format!("{l}\n"))
                    .collect();
            }
            if let Some(f) = file {
                self.netfile = f.to_owned();
            }
            Ok(())
        }
        fn stop(&mut self) -> Result<(), String> {
            if self.did("stop") {
                self.running = false;
            }
            Ok(())
        }
        fn destroy(&mut self) -> Result<(), String> {
            if self.did("destroy") {
                self.listed = false;
                self.disk = false;
                if self.destroy_touches_schedule {
                    self.schedule.push_str("# changed\n");
                }
            }
            Ok(())
        }
        fn backup_schedule(&mut self) -> Option<String> {
            Some(self.schedule.clone())
        }
        fn remove_firewall(&mut self) -> Result<(), String> {
            if self.did("remove_firewall") {
                self.fw = false;
            }
            Ok(())
        }
        fn write_export_list(&mut self, text: &str) -> Result<(), String> {
            if self.did("write_export_list") {
                self.export = text.to_owned();
            }
            Ok(())
        }
        fn read_record(&mut self) -> Result<Value, String> {
            Ok(
                json!({ "site": "site9", "ct": 119, "ip": "10.77.0.19", "hosts": "www.example.com" }),
            )
        }
        fn move_record(&mut self, removed: &Value) -> Result<(), String> {
            if self.did("move_record") {
                self.moved = Some(removed.clone());
                self.record_in_place = false;
            }
            Ok(())
        }
        fn list_dir(&mut self, dir: &str) -> Result<Option<Vec<String>>, String> {
            let names: Vec<String> = self
                .files
                .iter()
                .filter_map(|f| f.strip_prefix(&format!("{dir}/")))
                .filter(|n| !n.contains('/'))
                .map(str::to_owned)
                .collect();
            Ok(Some(names))
        }
        fn exists(&mut self, path: &str) -> Result<bool, String> {
            Ok(self.files.iter().any(|f| f == path))
        }
        fn remove_path(&mut self, path: &str) -> Result<(), String> {
            self.calls.push(format!("remove_path {path}"));
            if self.refused == Some(path) {
                return Err(format!("{path}: refused"));
            }
            if self.sticky != Some(path) {
                self.files.retain(|f| f != path);
            }
            Ok(())
        }
    }

    /// The plan `--plan` would print for `host`, sealed for `place`.
    fn sealed(place: &opstore::OpPlace, host: &mut FakeHost, left: bool) -> Value {
        let seen = host.see();
        let p = probes(&spec(), &seen, None, DnsProbe::NotAskedHere);
        let mut doc = plan_doc(&spec(), &p, left, DnsProbe::NotAskedHere);
        crate::quarantinecmd::seal_with_op(
            &mut doc,
            OP,
            &place.fingerprint,
            env!("CARGO_PKG_VERSION"),
        )
        .unwrap();
        doc
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

    const WRITES: [&str; 8] = [
        "write_entry",
        "archive",
        "close_access",
        "stop",
        "destroy",
        "remove_firewall",
        "write_export_list",
        "move_record",
    ];

    fn writes(h: &FakeHost) -> Vec<String> {
        h.calls
            .iter()
            .filter(|c| WRITES.iter().any(|w| c.starts_with(w)))
            .cloned()
            .collect()
    }

    #[test]
    fn step_ten_has_no_place_for_a_bad_code_or_a_container_that_is_not_a_sites() {
        assert_eq!(leftover_places(&spec()).map(|p| p.len()), Ok(4));
        for ct in [
            aib_cloud::site::EDGE_CT,
            aib_cloud::site::TEMPLATE_CT,
            99,
            0,
        ] {
            let mut s = spec();
            s.ct = ct;
            assert!(leftover_places(&s).is_err(), "ct {ct}");
        }
        for code in ["", "a", "../etc", "site9/x", "Site9", "-x"] {
            let mut s = spec();
            s.code = code.to_owned();
            assert!(leftover_places(&s).is_err(), "code {code:?}");
        }
    }

    #[test]
    fn step_ten_only_joins_one_plain_name_to_its_place() {
        assert_eq!(
            under("/root/ez2-restore", "site9").as_deref(),
            Some("/root/ez2-restore/site9")
        );
        for bad in ["", ".", "..", "a/b", "../x"] {
            assert_eq!(under("/root/ez2-restore", bad), None, "{bad:?}");
        }
    }

    #[test]
    fn a_leftover_still_there_after_its_removal_stops_with_its_path() {
        let mut h = fake();
        h.sticky = Some("/etc/ez2-cloud/sites/site9.secret");
        let e = clean_leftovers(&mut h, &spec()).unwrap_err();
        assert!(e.contains("1 path(s)") && e.contains("site9.secret"), "{e}");
        // the removal ran through apply stops there too, with what was done
        let pl = place("sticky");
        let mut h = fake();
        h.sticky = Some("/root/ez2-restore/site9");
        let plan = sealed(&pl, &mut h, true);
        let e = apply(
            &pl,
            &plan,
            &spec(),
            "www.example.com",
            &mut h,
            "20261111T120000Z",
        )
        .unwrap_err();
        assert!(
            e.contains("leftovers") && e.contains("/root/ez2-restore/site9"),
            "{e}"
        );
        // the record is moved, so the stop says what to remove by hand and how to check
        assert!(
            e.contains("by hand")
                && e.contains("aib-cloud-host site remove site9 --address www.example.com --plan"),
            "{e}"
        );
    }

    #[test]
    fn a_path_that_cannot_be_removed_does_not_keep_the_others_and_is_listed() {
        let mut h = fake();
        h.refused = Some("/etc/ez2-cloud/sites/site9.admin.anna");
        let e = clean_leftovers(&mut h, &spec()).unwrap_err();
        assert!(e.contains("1 path(s)") && e.contains("site9.admin.anna: refused"), "{e}");
        // every other path of the site was still removed
        let left: Vec<&str> = h
            .files
            .iter()
            .map(String::as_str)
            .filter(|f| f.contains("site9") && !f.contains("site99"))
            .collect();
        assert_eq!(left, vec!["/etc/ez2-cloud/sites/site9.admin.anna"]);
        assert!(!h.files.iter().any(|f| f.contains("vzdump-lxc-119-")));
    }

    #[test]
    fn the_plan_of_a_removed_site_lists_what_it_left_only_for_its_code_and_address() {
        fn never(_: &SiteSpec) -> Result<Vec<String>, String> {
            panic!("looked at")
        }
        let look = |s: &SiteSpec| {
            let mut h = fake();
            leftovers_found(&mut h, &leftover_places(s)?)
        };
        let doc = removed_doc("site9", "www.example.com", Ok(spec()), look);
        assert_eq!(doc["already_removed"], true);
        assert_eq!(doc["probed"], false);
        assert_eq!(
            doc["left_on_host"],
            json!([
                "/etc/ez2-cloud/sites/site9.admin.anna",
                "/etc/ez2-cloud/sites/site9.secret",
                "/var/lib/vz/dump/vzdump-lxc-119-2026_11_10-02_00_00.tar.zst",
                "/root/ez2-restore/site9"
            ])
        );
        assert_eq!(doc["left_on_host_note"], Value::Null);
        // another address: nothing is looked at
        let doc = removed_doc("site9", "www.example.org", Ok(spec()), never);
        assert_eq!(doc["left_on_host"], Value::Null);
        assert!(doc["left_on_host_note"].as_str().unwrap().contains("not those"));
        // no moved record that can be read: said, not guessed
        let doc = removed_doc("site9", "www.example.com", Err("gone".to_owned()), never);
        assert_eq!(doc["left_on_host"], Value::Null);
        assert!(doc["left_on_host_note"].as_str().unwrap().contains("gone"));
    }

    #[test]
    fn a_removal_runs_every_host_step_in_order_and_leaves_the_dns_step() {
        let pl = place("full");
        let mut h = fake();
        let plan = sealed(&pl, &mut h, true);
        let out = apply(
            &pl,
            &plan,
            &spec(),
            "www.example.com",
            &mut h,
            "20261111T120000Z",
        )
        .unwrap();
        assert_eq!(
            out["applied"],
            json!([
                "unserve", "archive", "access", "stop", "destroy", "firewall", "lists", "record"
            ])
        );
        assert_eq!(out["state"], "dns_left");
        assert_eq!(out["stop"], "dns_not_asked_here");
        assert_eq!(out["dns_names"], json!(["www.example.com"]));
        assert_eq!(out["archive"], ARCH);
        assert_eq!(
            writes(&h),
            [
                "write_entry",
                "archive",
                "close_access 1 true",
                "stop",
                "destroy",
                "remove_firewall",
                "write_export_list",
                "move_record"
            ]
        );
        // the entry lost this site's block and nothing else
        assert!(!h.entry.contains("site9") && !h.entry.contains("www.example.com"));
        assert!(h.entry.contains("# site:other\nother.example.com {"));
        assert!(!h.entry.contains("\n\n\n"));
        // only this site's forward went, from the live rules and from the file
        assert!(!h.rules.contains("2119") && h.rules.starts_with("-P PREROUTING"));
        assert!(!h.netfile.contains("2119") && h.netfile.contains("--dport 2120"));
        assert_eq!(h.export, "other 120 /var/www/other\n");
        let rec = h.moved.clone().unwrap();
        assert_eq!(rec["removed"], "20261111T120000Z");
        assert_eq!(rec["archive"], ARCH);
        assert_eq!(rec["left_at_vendor"], json!([LEFT_ON_WORD]));
        assert_eq!(rec["dns"], "not_asked_here");
        // step 10: this site's leftovers went, nobody else's (site99, ct 1190, ct 120)
        assert_eq!(
            out["removed_from_host"],
            json!([
                "/etc/ez2-cloud/sites/site9.admin.anna",
                "/etc/ez2-cloud/sites/site9.secret",
                "/var/lib/vz/dump/vzdump-lxc-119-2026_11_10-02_00_00.tar.zst",
                "/root/ez2-restore/site9"
            ])
        );
        assert_eq!(out["left_on_host"], json!([]));
        assert_eq!(
            h.files,
            [
                "/etc/ez2-cloud/sites/site99.secret",
                "/etc/ez2-cloud/sites/other.secret",
                "/var/lib/vz/dump/vzdump-lxc-1190-2026_11_10-02_00_00.tar.zst",
                "/var/lib/vz/dump/vzdump-lxc-120-2026_11_10-02_00_00.tar.zst",
                "/var/lib/vz/ez2-prerestore/site99"
            ]
        );
        assert_eq!(
            states(&pl),
            [
                State::Planned,
                State::Approved,
                State::Running,
                State::Verified
            ]
        );
        // the same plan cannot be applied twice
        let mut h2 = fake();
        assert!(apply(&pl, &plan, &spec(), "www.example.com", &mut h2, "t").is_err());
        assert!(writes(&h2).is_empty());
    }

    #[test]
    fn a_removal_found_half_way_goes_on_from_the_first_step_not_done() {
        let pl = place("half");
        let mut h = fake();
        h.entry = "# site:other\nother.example.com {\n}\n".to_owned();
        h.archive = Some(ARCH.to_owned());
        h.rules = RULES_WITHOUT.to_owned();
        h.netfile = "iface vmbr0 inet static\n".to_owned();
        let plan = sealed(&pl, &mut h, true);
        assert_eq!(plan["next_step"], "stop");
        let out = apply(&pl, &plan, &spec(), "www.example.com", &mut h, "t").unwrap();
        assert_eq!(
            out["applied"],
            json!(["stop", "destroy", "firewall", "lists", "record"])
        );
        assert!(!writes(&h)
            .iter()
            .any(|c| c == "write_entry" || c == "archive"));
    }

    #[test]
    fn a_host_that_changed_or_a_plan_that_was_edited_is_refused_before_anything() {
        let pl = place("changed");
        let mut h = fake();
        let plan = sealed(&pl, &mut h, true);
        h.fw = false;
        let e = apply(&pl, &plan, &spec(), "www.example.com", &mut h, "t").unwrap_err();
        assert!(e.contains("plan_changed"), "{e}");
        assert!(writes(&h).is_empty());
        let pl = place("edited");
        let mut h = fake();
        let mut plan = sealed(&pl, &mut h, true);
        plan["may_call_off"] = json!(false);
        let e = apply(&pl, &plan, &spec(), "www.example.com", &mut h, "t").unwrap_err();
        assert!(e.contains("plan_changed"), "{e}");
        assert!(writes(&h).is_empty());
        // another site's address, or another site's plan
        let e = apply(&pl, &plan, &spec(), "other.example.com", &mut fake(), "t").unwrap_err();
        assert!(e.contains("not for this site"), "{e}");
    }

    #[test]
    fn without_the_persons_word_on_the_licences_nothing_runs_from_this_host() {
        let pl = place("noword");
        let mut h = fake();
        let plan = sealed(&pl, &mut h, false);
        assert_eq!(plan["next"], "stop");
        let e = apply(&pl, &plan, &spec(), "www.example.com", &mut h, "t").unwrap_err();
        assert!(e.contains("nothing to run"), "{e}");
        assert!(writes(&h).is_empty());
    }

    #[test]
    fn another_site_that_could_not_be_asked_at_the_start_refuses_the_removal() {
        let pl = place("noask");
        let mut h = fake();
        h.others = vec![("other.example.com".to_owned(), None)];
        let plan = sealed(&pl, &mut h, true);
        let e = apply(&pl, &plan, &spec(), "www.example.com", &mut h, "t").unwrap_err();
        assert!(
            e.contains("before starting") && e.contains("nothing was changed"),
            "{e}"
        );
        assert!(writes(&h).is_empty());
    }

    #[test]
    fn another_site_answering_differently_stops_the_work_after_that_step() {
        let pl = place("others");
        let mut h = fake();
        h.others_change_at = Some(1);
        let plan = sealed(&pl, &mut h, true);
        let e = apply(&pl, &plan, &spec(), "www.example.com", &mut h, "t").unwrap_err();
        assert!(
            e.contains("after unserve") && e.contains("does not answer as it did"),
            "{e}"
        );
        assert_eq!(writes(&h), ["write_entry"]);
        assert_eq!(states(&pl).last(), Some(&State::Failed));
    }

    #[test]
    fn a_step_that_does_not_take_stops_the_work_and_nothing_after_it_runs() {
        let pl = place("notake");
        let mut h = fake();
        h.no_effect = Some("stop");
        let plan = sealed(&pl, &mut h, true);
        let e = apply(&pl, &plan, &spec(), "www.example.com", &mut h, "t").unwrap_err();
        assert!(
            e.contains("stop: done, but not done when looked at again"),
            "{e}"
        );
        assert!(!writes(&h).iter().any(|c| c == "destroy"));
    }

    #[test]
    fn destroying_must_leave_the_backup_schedule_as_it_was() {
        let pl = place("schedule");
        let mut h = fake();
        h.destroy_touches_schedule = true;
        let plan = sealed(&pl, &mut h, true);
        let e = apply(&pl, &plan, &spec(), "www.example.com", &mut h, "t").unwrap_err();
        assert!(
            e.contains("destroy failed") && e.contains("backup schedule"),
            "{e}"
        );
        assert!(!writes(&h).iter().any(|c| c == "remove_firewall"));
    }

    #[test]
    fn the_entry_loses_only_this_sites_block() {
        let out = entry_without(ENTRY, "site9", "www.example.com").unwrap();
        assert_eq!(
            out,
            "{\n\temail ops@example.test\n}\n\n# site:other\nother.example.com {\n\treverse_proxy 10.77.0.20:80\n}\n"
        );
        // a block opening with another address is not this site's: not touched
        assert!(entry_without(ENTRY, "site9", "shop.example.com").is_err());
        // no block, or a block twice
        assert!(entry_without(
            "# site:other\nother.example.com {\n}\n",
            "site9",
            "www.example.com"
        )
        .is_err());
        let twice = format!("{ENTRY}# site:site9\nwww.example.com {{\n}}\n");
        assert!(entry_without(&twice, "site9", "www.example.com").is_err());
        // the address is still opened by another block: refused, not half done
        let again = format!("{ENTRY}\n# site:site10\nwww.example.com {{\n}}\n");
        assert!(entry_without(&again, "site9", "www.example.com").is_err());
    }

    #[test]
    fn only_this_sites_forward_is_closed() {
        let rules = format!("{RULES_WITH}-A PREROUTING -i eth9 -p tcp -m tcp --dport 2120 -j DNAT --to-destination 10.77.0.20:22\n");
        let del = rules_to_delete(&rules, 2119, "10.77.0.19");
        assert_eq!(del.len(), 1);
        assert_eq!(del[0][0], "-D");
        assert!(del[0].contains(&"2119".to_owned()));
        assert!(rules_to_delete(RULES_WITHOUT, 2119, "10.77.0.19").is_empty());
        let (file, gone) = network_file_without(NETFILE, 2119, "10.77.0.19");
        assert_eq!(gone, 1);
        assert!(file.contains("--dport 2120") && !file.contains("--dport 2119"));
        // port 21190 is not port 2119
        let (_, gone) = network_file_without(
            "post-up x --dport 21190 -j DNAT --to-destination 10.77.0.19:22\n",
            2119,
            "10.77.0.19",
        );
        assert_eq!(gone, 0);
    }

    #[test]
    fn the_export_list_loses_only_this_sites_line() {
        let text = "# code ct path\nsite9 119 /var/www/site9\nsite99 199 /var/www/site99\n";
        assert_eq!(
            export_list_without(text, "site9"),
            "# code ct path\nsite99 199 /var/www/site99\n"
        );
    }

    #[test]
    fn other_sites_are_compared_with_how_they_answered_at_the_start() {
        let a = |c: Option<u16>| vec![("x.example.com".to_owned(), c)];
        assert!(same_as_base(&a(Some(200)), &a(Some(200))).is_ok());
        // a site down before the start that is still down does not stop the work
        assert!(same_as_base(&a(Some(503)), &a(Some(503))).is_ok());
        assert!(same_as_base(&a(Some(200)), &a(Some(502))).is_err());
        // could not be asked, then or now: never "the same"
        assert!(same_as_base(&a(None), &a(None)).is_err());
        assert!(same_as_base(&a(Some(200)), &a(None)).is_err());
        assert!(same_as_base(&[], &[]).is_ok());
    }

    #[test]
    fn a_host_with_only_the_record_and_dns_left_moves_the_record_and_nothing_else() {
        let pl = place("recordonly");
        let mut h = fake();
        h.entry = "# site:other\nother.example.com {\n}\n".to_owned();
        h.archive = Some(ARCH.to_owned());
        h.rules = RULES_WITHOUT.to_owned();
        h.netfile = "iface vmbr0 inet static\n".to_owned();
        h.listed = false;
        h.running = false;
        h.disk = false;
        h.fw = false;
        h.export = "other 120 /var/www/other\n".to_owned();
        let plan = sealed(&pl, &mut h, true);
        assert_eq!(plan["next"], "run");
        assert_eq!(plan["next_step"], "record");
        let out = apply(&pl, &plan, &spec(), "www.example.com", &mut h, "t").unwrap();
        assert_eq!(out["applied"], json!(["record"]));
        assert_eq!(out["state"], "dns_left");
        assert_eq!(writes(&h), ["move_record"]);
    }

    #[test]
    fn a_record_that_is_still_in_place_after_its_step_stops_the_work() {
        let pl = place("recordstays");
        let mut h = fake();
        h.no_effect = Some("move_record");
        let plan = sealed(&pl, &mut h, true);
        let e = apply(&pl, &plan, &spec(), "www.example.com", &mut h, "t").unwrap_err();
        assert!(
            e.contains("record: done, but not done when looked at again"),
            "{e}"
        );
        assert_eq!(states(&pl).last(), Some(&State::Failed));
    }

    #[test]
    fn a_schedule_file_that_is_not_there_is_an_answer_one_that_cannot_be_read_is_not() {
        let d = scratch("schedule-files");
        let there = d.join("vzdump.cron");
        fs::write(&there, "# cluster wide vzdump cron schedule\n").unwrap();
        let absent = d.join("jobs.cfg");
        let a = schedule_text(&[absent.as_path(), there.as_path()]).unwrap();
        assert!(
            a.contains("jobs.cfg absent") && a.contains("cluster wide"),
            "{a}"
        );
        // the same files, the same answer; a schedule added, another answer
        assert_eq!(
            schedule_text(&[absent.as_path(), there.as_path()]),
            Some(a.clone())
        );
        fs::write(&absent, "vzdump: backup-1\n\tschedule 02:00\n").unwrap();
        assert_ne!(schedule_text(&[absent.as_path(), there.as_path()]), Some(a));
        // a directory where the file should be: could not be read
        let dir = d.join("dir");
        fs::create_dir_all(&dir).unwrap();
        assert_eq!(schedule_text(&[dir.as_path()]), None);
        fs::remove_dir_all(&d).unwrap();
    }
}
