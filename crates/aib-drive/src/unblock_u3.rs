//! u-3 of `aib-drive unblock` (DECISION-council-unblock-519d0-1115 r1 §2 / §5, ebb36):
//! U3 machines, U9 seat resources, U19 receipts without a result / leases left hanging,
//! U20 knives that "bit" without a red test.
//!
//! Read-only (§6): nothing here takes a lock, releases a lease, kills, removes or calls gh.
//! The rules are pure functions over parsed facts, so each one has its own test and knife;
//! [`run`] only gathers the facts and keeps the round history (`drive/u3-history.jsonl`)
//! that "two rounds in a row" / "three rounds in a row" are judged on.
//!
//! Until u-1 (the framework and `unblock.json`) lands this is `aib-drive unblock-u3`;
//! u-1 calls [`blockers`] instead.

use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::Path;

/// U3: a machine counts as unusable only after this many rounds in a row (a single ssh
/// timeout is usually the network; DECISION §2).
pub const U3_ROUNDS: usize = 2;
/// U3: less free space than this in the home folder (KiB) is "full" (S82: 15 GB).
pub const U3_DISK_MIN_KB: u64 = 15 * 1024 * 1024;
/// U9: a seat resource is listed only after this many rounds in a row (f1fae, DECISION §2).
pub const U9_ROUNDS: usize = 3;
/// U9: swap use above this percent.
pub const U9_SWAP_PCT: u8 = 90;
/// U9: a seat open longer than this many hours should hand over and reopen.
pub const U9_OPEN_HOURS: u64 = 24;
/// U19a: a receipt `.out` with no `.done` this long (seconds) and no process is stuck.
pub const U19_STUCK_SECS: u64 = 90 * 60;
/// Rounds kept in the history file.
pub const HISTORY_KEEP: usize = 50;
/// Rounds closer than this (seconds) to the last kept one are not recorded (1ced7 u-3 N1).
pub const ROUND_MIN_GAP_SECS: u64 = 300;

/// One blocker row (the shape u-1 takes over).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Blocker {
    pub code: &'static str,
    /// Stable identity (code + subject): the same key with the same evidence is not news.
    pub key: String,
    pub subject: String,
    pub evidence: Vec<String>,
    pub owner_role: &'static str,
    /// Always false in u-3: every rule here only tells.
    pub auto: bool,
    /// What the v-2 fingerprint is made of: the state only, never the time-stamped evidence
    /// (1ced7 u-3b M1: evidence carries `@ as_of`, so hashing it changed every round).
    pub state: String,
}

impl Blocker {
    fn new(code: &'static str, subject: String, evidence: Vec<String>, role: &'static str) -> Self {
        Blocker {
            code,
            key: format!("{code}:{subject}"),
            subject,
            evidence,
            owner_role: role,
            auto: false,
            state: String::new(),
        }
    }
    /// The state the fingerprint follows (see [`Blocker::state`]).
    fn with_state(mut self, state: String) -> Self {
        self.state = state;
        self
    }
    pub fn to_json(&self) -> Value {
        json!({
            "code": self.code, "key": self.key, "subject": self.subject,
            "evidence": self.evidence, "owner_role": self.owner_role, "auto": self.auto,
        })
    }
}

/// v-2 source schema (IFACE-unblock-json-v2-a9136-1115).
pub const SRC_SCHEMA: &str = "aib-drive.unblock.v2";
/// v-2 source name of this file's rows.
pub const SRC_NAME: &str = "u-3";

/// v-2 `subject`: lower-case letters, digits and `-` only (never a colon), so `code:subject`
/// is a stable id. "andymini offline" → "andymini-offline", "@56cda open" → "56cda-open".
pub fn slug(s: &str) -> String {
    let mut out = String::new();
    for c in s.chars().flat_map(char::to_lowercase) {
        if c.is_ascii_alphanumeric() {
            out.push(c);
        } else if !out.is_empty() && !out.ends_with('-') {
            out.push('-');
        }
    }
    while out.ends_with('-') {
        out.pop();
    }
    out
}

/// v-2 `fingerprint`: changes only when the state does (FNV-1a over it), no colon. The state
/// is what the rule saw (offline / a disk or swap grade / open / stuck), not when it saw it.
pub fn fingerprint(code: &str, state: &str) -> String {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in state.bytes() {
        h ^= u64::from(b);
        h = h.wrapping_mul(0x0100_0000_01b3);
    }
    format!("{}-{:08x}", code.to_ascii_lowercase(), h & 0xffff_ffff)
}

/// One v-2 source row. `since_ms` comes from the last file when the id was there already;
/// `blocks` is never written here (aib-drive computes it). The owner is not a seat u-3 can
/// name (a machine, a receipt), so it is "" and the role goes into `what`.
pub fn v2_row(b: &Blocker, now_ms: u64, since_ms: Option<u64>) -> Value {
    let subject = slug(&b.subject);
    json!({
        "id": format!("{}:{subject}", b.code),
        "code": b.code,
        "subject": subject,
        "what": format!("{} {}(負責角色:{})", b.code, b.subject, b.owner_role),
        "fingerprint": fingerprint(b.code, &b.state),
        "evidence": b.evidence.join(";"),
        "owner": "",
        "since_ms": since_ms.unwrap_or(now_ms),
        "as_of_ms": now_ms,
        "source": SRC_NAME,
    })
}

/// v-2 r3 `covers`: the blocker codes a part that could not be read hides (a row of one of
/// these codes that disappears is "unknown", not "solved"). Anything not known here covers
/// every u-3 code — the safe side.
pub fn covers_of(text: &str) -> Vec<&'static str> {
    let has = |p: &str| text.starts_with(p);
    if has("remote unfinished receipts") {
        vec!["U19", "U20"]
    } else if has("disk of ") || has("aib-lease hosts") {
        vec!["U3"]
    } else if has("swap") || has("tmux panes") {
        vec!["U9"]
    } else if has("ps ") || has("aib lease status") {
        vec!["U19"]
    } else if has("round not recorded") || has("history not written") {
        vec!["U3", "U9"]
    } else {
        vec!["U3", "U9", "U19", "U20"]
    }
}

/// v-2 r1 `unreadable` entry: the name is the text before " (" or ": ", the why is all of it;
/// r3 adds `covers` ([`covers_of`]).
pub fn unreadable_entry(text: &str) -> Value {
    let cut = [" (", ": "]
        .iter()
        .filter_map(|m| text.find(m))
        .min()
        .unwrap_or(text.len());
    json!({"name": &text[..cut], "why": text, "covers": covers_of(text)})
}

/// The whole `unblock-src/u-3.json`, keeping each id's first-seen time from `old`; what could
/// not be read goes in `unreadable` (v-2 r1), never silently dropped.
pub fn src_doc(rows: &[Blocker], now_ms: u64, old: Option<&Value>, unreadable: &[String]) -> Value {
    let seen = |id: &str| -> Option<u64> {
        old?.get("rows")?
            .as_array()?
            .iter()
            .find(|r| r.get("id").and_then(Value::as_str) == Some(id))?
            .get("since_ms")?
            .as_u64()
    };
    let out: Vec<Value> = rows
        .iter()
        .map(|b| {
            let id = format!("{}:{}", b.code, slug(&b.subject));
            v2_row(b, now_ms, seen(&id))
        })
        .collect();
    let un: Vec<Value> = unreadable.iter().map(|t| unreadable_entry(t)).collect();
    json!({"schema": SRC_SCHEMA, "as_of_ms": now_ms, "rows": out, "unreadable": un})
}

/// One machine in one round, as `aib-lease hosts` reported it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HostSnap {
    pub name: String,
    /// free / busy / queued / closed / offline
    pub state: String,
    pub disk_free_kb: Option<u64>,
}

/// One round of facts.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Round {
    pub as_of: u64,
    pub hosts: Vec<HostSnap>,
    /// Swap use of this machine in percent; `None` = not read.
    pub swap_pct: Option<u8>,
    /// Seat code → hours its pane has been open; a seat not read is absent.
    pub seat_open_hours: BTreeMap<String, u64>,
}

impl Round {
    pub fn to_json(&self) -> Value {
        json!({
            "as_of": self.as_of,
            "hosts": self.hosts.iter().map(|h| json!({
                "name": h.name, "state": h.state, "disk_free_kb": h.disk_free_kb,
            })).collect::<Vec<_>>(),
            "swap_pct": self.swap_pct,
            "seat_open_hours": self.seat_open_hours,
        })
    }
    /// A history line back; `None` for a line that is not a round.
    pub fn from_json(v: &Value) -> Option<Round> {
        let hosts = v
            .get("hosts")?
            .as_array()?
            .iter()
            .filter_map(|h| {
                Some(HostSnap {
                    name: h.get("name")?.as_str()?.to_owned(),
                    state: h.get("state")?.as_str()?.to_owned(),
                    disk_free_kb: h.get("disk_free_kb").and_then(Value::as_u64),
                })
            })
            .collect();
        Some(Round {
            as_of: v.get("as_of")?.as_u64()?,
            hosts,
            swap_pct: v
                .get("swap_pct")
                .and_then(Value::as_u64)
                .and_then(|n| u8::try_from(n).ok()),
            seat_open_hours: v
                .get("seat_open_hours")
                .and_then(Value::as_object)
                .map(|o| {
                    o.iter()
                        .filter_map(|(k, n)| Some((k.clone(), n.as_u64()?)))
                        .collect()
                })
                .unwrap_or_default(),
        })
    }
}

/// The last `n` rounds, oldest first; `None` when there are fewer than `n`.
fn last(history: &[Round], n: usize) -> Option<&[Round]> {
    history.len().checked_sub(n).map(|i| &history[i..])
}

/// U3: a machine offline, or short of disk, in each of the last [`U3_ROUNDS`] rounds.
/// A machine missing from any of those rounds is not judged.
pub fn u3(history: &[Round]) -> Vec<Blocker> {
    let Some(rounds) = last(history, U3_ROUNDS) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    let Some(newest) = rounds.last() else {
        return out;
    };
    for h in &newest.hosts {
        let seen: Vec<&HostSnap> = rounds
            .iter()
            .filter_map(|r| r.hosts.iter().find(|x| x.name == h.name))
            .collect();
        if seen.len() != rounds.len() {
            continue;
        }
        if seen.iter().all(|x| x.state == "offline") {
            let ev = rounds
                .iter()
                .map(|r| format!("{} offline @ {}", h.name, r.as_of))
                .collect();
            out.push(
                Blocker::new("U3", format!("{} offline", h.name), ev, "維運")
                    .with_state("offline".into()),
            );
        } else if seen
            .iter()
            .all(|x| x.disk_free_kb.is_some_and(|kb| kb < U3_DISK_MIN_KB))
        {
            let ev = rounds
                .iter()
                .zip(&seen)
                .map(|(r, x)| {
                    format!(
                        "{} free {} GB @ {}",
                        h.name,
                        x.disk_free_kb.unwrap_or(0) / (1024 * 1024),
                        r.as_of
                    )
                })
                .collect();
            // grade: whole 5 GB steps of what the newest round had free
            let grade = h.disk_free_kb.unwrap_or(0) / (5 * 1024 * 1024) * 5;
            out.push(
                Blocker::new("U3", format!("{} disk", h.name), ev, "維運")
                    .with_state(format!("disk under {} GB", grade + 5)),
            );
        }
    }
    out
}

/// U9: swap above [`U9_SWAP_PCT`], or a seat open over [`U9_OPEN_HOURS`], in each of the
/// last [`U9_ROUNDS`] rounds. Not read in any of them = not judged.
pub fn u9(history: &[Round]) -> Vec<Blocker> {
    let Some(rounds) = last(history, U9_ROUNDS) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    if rounds
        .iter()
        .all(|r| r.swap_pct.is_some_and(|p| p > U9_SWAP_PCT))
    {
        let ev = rounds
            .iter()
            .map(|r| format!("swap {}% @ {}", r.swap_pct.unwrap_or(0), r.as_of))
            .collect();
        let pct = rounds.last().and_then(|r| r.swap_pct).unwrap_or(0);
        out.push(
            Blocker::new("U9", "swap".into(), ev, "各席")
                .with_state(format!("swap {}%+", pct / 5 * 5)),
        );
    }
    let Some(newest) = rounds.last() else {
        return out;
    };
    for seat in newest.seat_open_hours.keys() {
        let hours: Vec<u64> = rounds
            .iter()
            .filter_map(|r| r.seat_open_hours.get(seat).copied())
            .collect();
        if hours.len() == rounds.len() && hours.iter().all(|&h| h > U9_OPEN_HOURS) {
            let ev = rounds
                .iter()
                .zip(&hours)
                .map(|(r, h)| format!("@{seat} open {h} h @ {}", r.as_of))
                .collect();
            out.push(
                Blocker::new("U9", format!("@{seat} open"), ev, "各席").with_state("open".into()),
            );
        }
    }
    out
}

/// A receipt as found on this machine.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Receipt {
    /// run-id (file name without `.out`)
    pub rid: String,
    pub text: String,
    /// seconds since the `.out` was last written
    pub age_secs: u64,
    /// a `.done` sits next to it (fetched copies in pending/ count as done)
    pub done: bool,
    /// a process names this run-id
    pub running: bool,
}

/// U19a: a receipt that will not give a result — `.out` with no `.done`, older than
/// [`U19_STUCK_SECS`], nothing running it; or done without a result line. A result line is
/// `HEAD-END` (receipt-run), `END rc=` (the FALLBACK cell receipts, 1ced7 u-3 L1) or a
/// `REFUSED` (a refusal is a result).
pub fn has_result(text: &str) -> bool {
    text.lines()
        .any(|l| l.starts_with("HEAD-END ") || l.starts_with("END rc=") || l.starts_with("REFUSED"))
}

pub fn u19_receipts(receipts: &[Receipt]) -> Vec<Blocker> {
    let mut out = Vec::new();
    for r in receipts {
        if !r.done && !r.running && r.age_secs >= U19_STUCK_SECS {
            out.push(
                Blocker::new(
                    "U19",
                    format!("receipt {}", r.rid),
                    vec![format!(
                        "{}.out {} min old, no .done, no process",
                        r.rid,
                        r.age_secs / 60
                    )],
                    "收據擁有席",
                )
                .with_state("stuck".into()),
            );
        } else if r.done && !has_result(&r.text) {
            out.push(
                Blocker::new(
                    "U19",
                    format!("receipt {}", r.rid),
                    vec![format!("{}: done but no HEAD-END / END rc= line", r.rid)],
                    "收據擁有席",
                )
                .with_state("no result".into()),
            );
        }
    }
    out
}

/// This machine's lease as `aib lease status --json` gives it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Lease {
    pub run_id: String,
    pub owner: String,
    pub pid: Option<u32>,
    /// `Some(false)` only when the pid was checked on this machine and is gone.
    pub pid_alive: Option<bool>,
}

/// U19b: a lease whose holder process is gone. A pid that could not be checked (another
/// machine, no pid) is unknown and not listed (DECISION §2: unreadable is not "dead").
pub fn u19_lease(lease: Option<&Lease>) -> Vec<Blocker> {
    match lease {
        Some(l) if l.pid_alive == Some(false) => vec![Blocker::new(
            "U19",
            format!("lease {}", l.run_id),
            vec![format!(
                "lease {} of @{} held, pid {} is gone",
                l.run_id,
                l.owner,
                l.pid.map_or_else(|| "?".to_owned(), |p| p.to_string())
            )],
            "租約持有席",
        )
        .with_state("pid gone".into())],
        _ => Vec::new(),
    }
}

/// U20: knives a receipt counts as bitten without a red test: `RC knife_X=<non-0>` whose
/// own section (the lines since the previous `RC knife_` line) has no `test result: FAILED`
/// and no `panicked`, or that says INVALID. `=0` (did not bite) is a miss, not a fake.
pub fn u20(rid: &str, text: &str) -> Vec<Blocker> {
    let mut out = Vec::new();
    let mut section: Vec<&str> = Vec::new();
    for l in text.lines() {
        let Some(rest) = l.strip_prefix("RC knife_") else {
            section.push(l);
            continue;
        };
        let (name, val) = rest.split_once('=').unwrap_or((rest, ""));
        let code = val.split_whitespace().next().unwrap_or("");
        let red = section
            .iter()
            .any(|s| s.contains("test result: FAILED") || s.contains("panicked"));
        if name != "crate" && code != "0" && !code.is_empty() && (l.contains("INVALID") || !red) {
            out.push(
                Blocker::new(
                    "U20",
                    format!("knife {name} in {rid}"),
                    vec![format!("{rid}: {l}")],
                    "刀作者",
                )
                .with_state(l.to_owned()),
            );
        }
        section.clear();
    }
    out
}

/// Every u-3 rule over one set of facts, in code order.
pub fn blockers(history: &[Round], receipts: &[Receipt], lease: Option<&Lease>) -> Vec<Blocker> {
    let mut v = u3(history);
    v.extend(u9(history));
    v.extend(u19_receipts(receipts));
    v.extend(u19_lease(lease));
    for r in receipts.iter().filter(|r| r.done) {
        v.extend(u20(&r.rid, &r.text));
    }
    v
}

/// Parse `aib-lease hosts` JSON into this round's machines. The tool prints
/// `disk_free_gb` (whole GB) and `low_disk` (hosts.rs `view_json`, 1ced7 u-3 M1); a
/// `low_disk: true` with no number counts as below the line, `disk_free_kb` is read too.
pub fn hosts_from_json(v: &Value) -> Vec<HostSnap> {
    v.get("hosts")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(|h| {
                    let kb = h
                        .get("disk_free_kb")
                        .and_then(Value::as_u64)
                        .or_else(|| {
                            h.get("disk_free_gb")
                                .and_then(Value::as_u64)
                                .map(|g| g.saturating_mul(1024 * 1024))
                        })
                        .or_else(|| {
                            (h.get("low_disk").and_then(Value::as_bool) == Some(true)).then_some(0)
                        });
                    Some(HostSnap {
                        name: h.get("name")?.as_str()?.to_owned(),
                        state: h.get("state")?.as_str()?.to_owned(),
                        disk_free_kb: kb,
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Swap use in percent from `sysctl vm.swapusage` (`total = 18432.00M  used = 17635.50M …`).
pub fn swap_pct(sysctl: &str) -> Option<u8> {
    let num = |k: &str| -> Option<f64> {
        let rest = sysctl.split(k).nth(1)?;
        let tok = rest
            .trim_start_matches([' ', '='])
            .split_whitespace()
            .next()?;
        tok.trim_end_matches('M').parse::<f64>().ok()
    };
    let (total, used) = (num("total")?, num("used")?);
    if total <= 0.0 {
        return None;
    }
    let p = (used / total * 100.0).round();
    if (0.0..=100.0).contains(&p) {
        // the range check above makes the cast exact
        Some(p as u8)
    } else {
        None
    }
}

/// `ps -o etime=` (`[[dd-]hh:]mm:ss`) in whole hours.
pub fn etime_hours(s: &str) -> Option<u64> {
    let s = s.trim();
    let (days, rest) = match s.split_once('-') {
        Some((d, r)) => (d.parse::<u64>().ok()?, r),
        None => (0, s),
    };
    let parts: Vec<u64> = rest
        .split(':')
        .map(|p| p.parse::<u64>().ok())
        .collect::<Option<_>>()?;
    let hours = match parts.as_slice() {
        [h, _, _] => *h,
        [_, _] => 0,
        _ => return None,
    };
    Some(days * 24 + hours)
}

/// Keep the newest [`HISTORY_KEEP`] rounds of the history text, then add `round` — only
/// when it is at least [`ROUND_MIN_GAP_SECS`] after the last kept one (1ced7 u-3 N1: two
/// records seconds apart are not two rounds). The bool says whether it was added.
pub fn history_with(text: &str, round: &Round) -> (Vec<Round>, String, bool) {
    let mut rounds: Vec<Round> = text
        .lines()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
        .filter_map(|v| Round::from_json(&v))
        .collect();
    let added = rounds
        .last()
        .is_none_or(|l| round.as_of >= l.as_of.saturating_add(ROUND_MIN_GAP_SECS));
    if added {
        rounds.push(round.clone());
    }
    let skip = rounds.len().saturating_sub(HISTORY_KEEP);
    let rounds: Vec<Round> = rounds.into_iter().skip(skip).collect();
    let mut out = String::new();
    for r in &rounds {
        out.push_str(&r.to_json().to_string());
        out.push('\n');
    }
    (rounds, out, added)
}

/// Run a program and return its stdout when it exits 0.
fn read_cmd(prog: &str, args: &[&str]) -> Option<String> {
    let o = std::process::Command::new(prog).args(args).output().ok()?;
    o.status
        .success()
        .then(|| String::from_utf8_lossy(&o.stdout).into_owned())
}

/// Run a program whose answer is JSON on stdout whatever its exit code (`aib lease status
/// --json` exits 4 while a lease is held, 1ced7 u-3 M2).
fn read_json_any_rc(prog: &str, args: &[&str]) -> Option<Value> {
    let o = std::process::Command::new(prog).args(args).output().ok()?;
    json_whatever_rc(o.status.code(), &o.stdout)
}

/// The JSON on stdout, read whatever the exit code was (1ced7 u-3 rev3 L2: testable without
/// a process). Only a stdout that is not JSON is None.
pub fn json_whatever_rc(_rc: Option<i32>, stdout: &[u8]) -> Option<Value> {
    serde_json::from_slice::<Value>(stdout).ok()
}

/// Any machine in `aib-lease hosts` with none of the disk fields (an old build): its disk is
/// not judged, which must be said (1ced7 u-3 rev3 L1).
pub fn hosts_without_disk(v: &Value) -> Vec<String> {
    v.get("hosts")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter(|h| {
                    ["disk_free_kb", "disk_free_gb", "low_disk"]
                        .iter()
                        .all(|k| h.get(*k).is_none())
                })
                .filter_map(|h| h.get("name").and_then(Value::as_str).map(str::to_owned))
                .collect()
        })
        .unwrap_or_default()
}

/// `kill -0` stderr → alive? `Operation not permitted` means the pid exists but is someone
/// else's (1ced7 u-3 N2): unknown, never "gone".
pub fn kill0_alive(success: bool, stderr: &str) -> Option<bool> {
    if success {
        Some(true)
    } else if stderr.contains("not permitted") {
        None
    } else {
        Some(false)
    }
}

/// The run-id a receipt file name names: `<rid>.out` in ~/wt.noindex.
fn rid_of_out(name: &str) -> Option<&str> {
    name.strip_suffix(".out").filter(|r| {
        !r.is_empty()
            && r.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
    })
}

/// This round's facts from the machine (each source that cannot be read is named).
fn gather(root: &Path, now: u64, unreadable: &mut Vec<String>) -> Round {
    let lease_hosts = root.join("bin/aib-lease-hosts");
    let hosts = match read_cmd(&lease_hosts.to_string_lossy(), &["hosts"])
        .and_then(|s| serde_json::from_str::<Value>(&s).ok())
    {
        Some(v) => {
            let blind = hosts_without_disk(&v);
            if !blind.is_empty() {
                unreadable.push(format!(
                    "disk of {} (aib-lease hosts has no disk field)",
                    blind.join(",")
                ));
            }
            hosts_from_json(&v)
        }
        None => {
            unreadable.push("aib-lease hosts".into());
            Vec::new()
        }
    };
    let swap = read_cmd("sysctl", &["vm.swapusage"]).and_then(|s| swap_pct(&s));
    if swap.is_none() {
        unreadable.push("swap".into());
    }
    // seat panes: the window name is the seat code; the pane's process age is how long it is open
    let mut seat_open_hours = BTreeMap::new();
    match read_cmd(
        "tmux",
        &["list-panes", "-a", "-F", "#{window_name} #{pane_pid}"],
    ) {
        Some(s) => {
            for l in s.lines() {
                let mut it = l.split_whitespace();
                let (Some(w), Some(pid)) = (it.next(), it.next()) else {
                    continue;
                };
                let seat = w.trim_start_matches('@');
                if seat.len() == 5 && seat.bytes().all(|b| b.is_ascii_hexdigit()) {
                    if let Some(h) =
                        read_cmd("ps", &["-o", "etime=", "-p", pid]).and_then(|e| etime_hours(&e))
                    {
                        seat_open_hours.insert(seat.to_owned(), h);
                    }
                }
            }
        }
        None => unreadable.push("tmux panes".into()),
    }
    Round {
        as_of: now,
        hosts,
        swap_pct: swap,
        seat_open_hours,
    }
}

/// Fetched receipts in pending/ written in the last day (each is done: it was fetched).
fn receipts(root: &Path, now: u64) -> Vec<Receipt> {
    let Ok(rd) = std::fs::read_dir(root.join("pending")) else {
        return Vec::new();
    };
    let mut v = Vec::new();
    for e in rd.flatten() {
        let name = e.file_name().to_string_lossy().into_owned();
        let Some(rid) = name
            .strip_prefix("RECEIPT-")
            .and_then(|n| n.strip_suffix(".out"))
        else {
            continue;
        };
        let age = e
            .metadata()
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map_or(u64::MAX, |d| now.saturating_sub(d.as_secs()));
        if age > 24 * 60 * 60 {
            continue;
        }
        if let Ok(text) = std::fs::read_to_string(e.path()) {
            v.push(Receipt {
                rid: rid.to_owned(),
                text,
                age_secs: age,
                done: true,
                running: false,
            });
        }
    }
    v.sort_by(|a, b| a.rid.cmp(&b.rid));
    v
}

/// Receipts run on this machine: `~/wt.noindex/<rid>.out` of the last day, with `.done`
/// beside it or not, and whether a process names the run-id (`ps -axo command`). Receipts
/// on the test machines are not read here (1ced7 u-3 M3): [`run`] says so in `unreadable`.
fn local_receipts(wt: &Path, now: u64, unreadable: &mut Vec<String>) -> Vec<Receipt> {
    let Ok(rd) = std::fs::read_dir(wt) else {
        return Vec::new();
    };
    let ps = read_cmd("ps", &["-axo", "command"]);
    if ps.is_none() {
        unreadable.push("ps (local receipts' processes)".into());
    }
    let mut v = Vec::new();
    for e in rd.flatten() {
        let name = e.file_name().to_string_lossy().into_owned();
        let Some(rid) = rid_of_out(&name) else {
            continue;
        };
        let age = e
            .metadata()
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map_or(u64::MAX, |d| now.saturating_sub(d.as_secs()));
        if age > 24 * 60 * 60 {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(e.path()) else {
            continue;
        };
        // ps not read ⇒ assume running: unknown is never "stuck"
        let running = ps
            .as_deref()
            .is_none_or(|p| p.lines().any(|l| l.contains(rid)));
        v.push(Receipt {
            rid: rid.to_owned(),
            text,
            age_secs: age,
            done: wt.join(format!("{rid}.done")).exists(),
            running,
        });
    }
    v.sort_by(|a, b| a.rid.cmp(&b.rid));
    v
}

/// This machine's lease; the pid is checked here (`kill -0`).
fn lease(root: &Path, unreadable: &mut Vec<String>) -> Option<Lease> {
    let aib = root.join("bin/aib");
    let prog = if aib.exists() {
        aib.to_string_lossy().into_owned()
    } else {
        "aib".to_owned()
    };
    let Some(v) = read_json_any_rc(&prog, &["lease", "status", "--json"]) else {
        unreadable.push("aib lease status".into());
        return None;
    };
    let l = v.get("lease").filter(|l| !l.is_null())?;
    let pid = l
        .get("pid")
        .and_then(Value::as_u64)
        .and_then(|p| u32::try_from(p).ok());
    let pid_alive = pid.and_then(|p| {
        let o = std::process::Command::new("kill")
            .args(["-0", &p.to_string()])
            .output()
            .ok()?;
        kill0_alive(o.status.success(), &String::from_utf8_lossy(&o.stderr))
    });
    Some(Lease {
        run_id: l.get("run_id")?.as_str()?.to_owned(),
        owner: l
            .get("owner")
            .and_then(Value::as_str)
            .unwrap_or("?")
            .to_owned(),
        pid,
        pid_alive,
    })
}

pub const USAGE: &str =
    "usage: aib-drive unblock-u3 [--record] [--json] [--write-src]   (read-only; --record appends this round to drive/u3-history.jsonl; --write-src writes drive/unblock-src/u-3.json, the v-2 source)\n";

/// `aib-drive unblock-u3`: rc 0 (rows or none), 2 usage / no state root.
pub fn run(args: &[String], root: Option<&Path>, now: u64) -> (i32, String) {
    let mut record = false;
    let mut as_json = false;
    let mut write_src = false;
    for a in args {
        match a.as_str() {
            "--record" => record = true,
            "--json" => as_json = true,
            "--write-src" => write_src = true,
            _ => return (2, USAGE.to_owned()),
        }
    }
    let Some(root) = root else {
        return (2, "aib-drive unblock-u3: no state root\n".to_owned());
    };
    let mut unreadable = Vec::new();
    let round = gather(root, now, &mut unreadable);
    let hist_path = root.join("drive/u3-history.jsonl");
    let old = std::fs::read_to_string(&hist_path).unwrap_or_default();
    let (history, text, added) = history_with(&old, &round);
    // without --record the round is judged but not kept: the streaks only grow on record
    if record && !added {
        unreadable.push(format!(
            "round not recorded: under {ROUND_MIN_GAP_SECS}s since the last one"
        ));
    }
    if record && added {
        if let Err(e) = std::fs::create_dir_all(root.join("drive"))
            .and_then(|()| std::fs::write(&hist_path, text))
        {
            unreadable.push(format!("history not written: {e}"));
        }
    }
    let mut rec = receipts(root, now);
    if let Some(home) = std::env::var_os("HOME") {
        let local = local_receipts(&Path::new(&home).join("wt.noindex"), now, &mut unreadable);
        rec.retain(|r| !local.iter().any(|l| l.rid == r.rid));
        rec.extend(local);
    }
    unreadable.push("remote unfinished receipts not read (test machines' ~/wt.noindex)".into());
    let l = lease(root, &mut unreadable);
    let rows = blockers(&history, &rec, l.as_ref());
    if write_src {
        let dir = root.join("drive/unblock-src");
        let path = dir.join("u-3.json");
        let old = std::fs::read_to_string(&path)
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok());
        let doc = src_doc(&rows, now.saturating_mul(1000), old.as_ref(), &unreadable);
        let tmp = dir.join(format!(".u-3.json.{}", std::process::id()));
        if let Err(e) = std::fs::create_dir_all(&dir)
            .and_then(|()| std::fs::write(&tmp, format!("{doc}\n")))
            .and_then(|()| std::fs::rename(&tmp, &path))
        {
            let _ = std::fs::remove_file(&tmp);
            unreadable.push(format!("unblock-src/u-3.json not written: {e}"));
        }
    }
    if as_json {
        let j = json!({
            "as_of": now,
            "rows": rows.iter().map(Blocker::to_json).collect::<Vec<_>>(),
            "unreadable": unreadable,
            "recorded": record,
        });
        return (0, format!("{j}\n"));
    }
    let mut out = String::new();
    for b in &rows {
        out.push_str(&format!(
            "{} {} → {}:{}\n",
            b.code,
            b.subject,
            b.owner_role,
            b.evidence.join(";")
        ));
    }
    if rows.is_empty() {
        out.push_str("u-3:沒有卡點\n");
    }
    if !unreadable.is_empty() {
        out.push_str(&format!("讀不到(不算沒卡點):{}\n", unreadable.join("、")));
    }
    (0, out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn host(name: &str, state: &str, gb: Option<u64>) -> HostSnap {
        HostSnap {
            name: name.into(),
            state: state.into(),
            disk_free_kb: gb.map(|g| g * 1024 * 1024),
        }
    }
    fn round(t: u64, hosts: Vec<HostSnap>) -> Round {
        Round {
            as_of: t,
            hosts,
            ..Round::default()
        }
    }

    #[test]
    fn a_machine_offline_two_rounds_in_a_row_is_u3_one_round_is_not() {
        let one = vec![
            round(1, vec![host("mini", "busy", Some(40))]),
            round(2, vec![host("mini", "offline", None)]),
        ];
        assert!(u3(&one).is_empty(), "reason: one_timeout_listed");
        let two = vec![
            round(2, vec![host("mini", "offline", None)]),
            round(3, vec![host("mini", "offline", None)]),
        ];
        let b = u3(&two);
        assert_eq!(b.len(), 1, "{b:?}");
        assert_eq!(b[0].key, "U3:mini offline");
        assert!(!b[0].auto);
        assert!(
            u3(&two[..1]).is_empty(),
            "reason: one_round_of_history_judged"
        );
    }

    #[test]
    fn a_machine_short_of_disk_two_rounds_is_u3_and_unknown_disk_is_not() {
        let low = vec![
            round(1, vec![host("pro13", "busy", Some(10))]),
            round(2, vec![host("pro13", "free", Some(9))]),
        ];
        let b = u3(&low);
        assert_eq!(b.len(), 1, "{b:?}");
        assert_eq!(b[0].key, "U3:pro13 disk");
        let unknown = vec![
            round(1, vec![host("pro13", "busy", Some(10))]),
            round(2, vec![host("pro13", "free", None)]),
        ];
        assert!(u3(&unknown).is_empty(), "reason: unread_disk_counted_full");
        let ok = vec![
            round(1, vec![host("pro13", "busy", Some(16))]),
            round(2, vec![host("pro13", "free", Some(20))]),
        ];
        assert!(u3(&ok).is_empty(), "reason: enough_disk_listed");
    }

    #[test]
    fn a_machine_missing_from_a_round_is_not_judged() {
        let h = vec![
            round(1, vec![]),
            round(2, vec![host("mini", "offline", None)]),
        ];
        assert!(u3(&h).is_empty());
    }

    #[test]
    fn swap_or_a_long_open_seat_three_rounds_in_a_row_is_u9() {
        let mk = |t: u64, swap: Option<u8>, open: Option<u64>| Round {
            as_of: t,
            swap_pct: swap,
            seat_open_hours: open
                .map(|h| BTreeMap::from([("ebb36".to_owned(), h)]))
                .unwrap_or_default(),
            ..Round::default()
        };
        let two = vec![mk(1, Some(95), Some(30)), mk(2, Some(96), Some(31))];
        assert!(u9(&two).is_empty(), "reason: two_rounds_listed");
        let three = vec![
            mk(1, Some(95), Some(30)),
            mk(2, Some(96), Some(31)),
            mk(3, Some(97), Some(32)),
        ];
        let keys: Vec<String> = u9(&three).into_iter().map(|b| b.key).collect();
        assert_eq!(keys, ["U9:swap", "U9:@ebb36 open"]);
        let dip = vec![
            mk(1, Some(95), Some(30)),
            mk(2, Some(80), Some(10)),
            mk(3, Some(97), Some(32)),
        ];
        assert!(u9(&dip).is_empty(), "reason: a_dip_did_not_reset");
        let unread = vec![
            mk(1, Some(95), None),
            mk(2, None, Some(31)),
            mk(3, Some(97), Some(32)),
        ];
        assert!(u9(&unread).is_empty(), "reason: unread_counted");
    }

    fn rc(rid: &str, text: &str, age: u64, done: bool, running: bool) -> Receipt {
        Receipt {
            rid: rid.into(),
            text: text.into(),
            age_secs: age,
            done,
            running,
        }
    }

    #[test]
    fn a_receipt_with_no_result_is_u19_and_a_running_or_finished_one_is_not() {
        let stuck = rc("r1", "START\n", U19_STUCK_SECS, false, false);
        let young = rc("r2", "START\n", U19_STUCK_SECS - 1, false, false);
        let running = rc("r3", "START\n", U19_STUCK_SECS * 3, false, true);
        let fine = rc(
            "r4",
            "RC test=0\nHEAD-END abc\n",
            U19_STUCK_SECS * 3,
            true,
            false,
        );
        let headless = rc("r5", "RC fetch=1\nEND\n", 10, true, false);
        let refused = rc("r6", "REFUSED bundle 不存在\n", 10, true, false);
        let keys: Vec<String> = u19_receipts(&[stuck, young, running, fine, headless, refused])
            .into_iter()
            .map(|b| b.key)
            .collect();
        assert_eq!(keys, ["U19:receipt r1", "U19:receipt r5"]);
    }

    #[test]
    fn a_lease_whose_pid_is_gone_is_u19_and_an_unchecked_pid_is_not() {
        let l = |alive: Option<bool>| Lease {
            run_id: "rid-1".into(),
            owner: "ebb36".into(),
            pid: Some(4242),
            pid_alive: alive,
        };
        assert_eq!(u19_lease(Some(&l(Some(false))))[0].key, "U19:lease rid-1");
        assert!(u19_lease(Some(&l(Some(true)))).is_empty());
        assert!(
            u19_lease(Some(&l(None))).is_empty(),
            "reason: unknown_pid_counted_dead"
        );
        assert!(u19_lease(None).is_empty());
    }

    #[test]
    fn a_knife_that_bit_without_a_red_test_is_u20() {
        let text = "\
KNIVES-EXPECTED 4
anchor crates/a/src/lib.rs 1
test result: FAILED. 0 passed; 1 failed
test a::x ... FAILED
RC knife_K1=101 (want non-0)
anchor crates/a/src/lib.rs 1
INVALID:編譯錯 error[E0425]: cannot find value `q`
RC knife_K2=101 (want non-0)
anchor crates/a/src/lib.rs 1
RC knife_K3=101 INVALID(沒有 test result FAILED / panicked 行)
anchor crates/a/src/lib.rs 1
test result: ok. 3 passed
RC knife_K4=0 (want non-0)
RC knife_crate=INVALID crate「x」不在 workspace
";
        let keys: Vec<String> = u20("rid-9", text).into_iter().map(|b| b.key).collect();
        assert_eq!(keys, ["U20:knife K2 in rid-9", "U20:knife K3 in rid-9"]);
    }

    #[test]
    fn a_red_test_in_the_previous_knife_does_not_cover_the_next_one() {
        let text = "\
test result: FAILED. 0 passed; 1 failed
RC knife_A=101 (want non-0)
anchor x 1
RC knife_B=101 (want non-0)
";
        let keys: Vec<String> = u20("r", text).into_iter().map(|b| b.key).collect();
        assert_eq!(keys, ["U20:knife B in r"]);
    }

    #[test]
    fn the_sources_parse_as_the_tools_print_them() {
        assert_eq!(
            swap_pct(
                "vm.swapusage: total = 18432.00M  used = 17635.50M  free = 796.50M  (encrypted)"
            ),
            Some(96)
        );
        assert_eq!(
            swap_pct("vm.swapusage: total = 0.00M  used = 0.00M  free = 0.00M"),
            None
        );
        assert_eq!(etime_hours("2-03:04:05"), Some(51));
        assert_eq!(etime_hours("  25:00:01\n"), Some(25));
        assert_eq!(etime_hours("04:05"), Some(0));
        assert_eq!(etime_hours("x"), None);
        let v: Value = serde_json::from_str(
            r#"{"hosts":[{"name":"mini","state":"offline","disk_free_kb":null},{"name":"p","state":"busy","disk_free_kb":123}]}"#,
        )
        .unwrap();
        assert_eq!(
            hosts_from_json(&v),
            vec![
                host("mini", "offline", None),
                HostSnap {
                    name: "p".into(),
                    state: "busy".into(),
                    disk_free_kb: Some(123)
                }
            ]
        );
    }

    #[test]
    fn the_history_keeps_the_newest_rounds_and_skips_bad_lines() {
        let mut text = String::from("not json\n{\"as_of\":\"x\"}\n");
        for t in 0..(HISTORY_KEEP as u64 + 5) {
            text.push_str(
                &round(t, vec![host("m", "busy", None)])
                    .to_json()
                    .to_string(),
            );
            text.push('\n');
        }
        let (rounds, out, added) = history_with(&text, &round(999, vec![]));
        assert!(added);
        assert_eq!(rounds.len(), HISTORY_KEEP);
        assert_eq!(rounds.last().map(|r| r.as_of), Some(999));
        assert_eq!(rounds[0].as_of, 6);
        assert_eq!(out.lines().count(), HISTORY_KEEP);
        let back = Round::from_json(&serde_json::from_str(out.lines().next().unwrap()).unwrap());
        assert_eq!(back, Some(rounds[0].clone()));
    }

    #[test]
    fn a_round_too_close_to_the_last_is_not_recorded() {
        let text = format!("{}\n", round(1000, vec![]).to_json());
        let (rounds, out, added) =
            history_with(&text, &round(1000 + ROUND_MIN_GAP_SECS - 1, vec![]));
        assert!(!added);
        assert_eq!(rounds.len(), 1);
        assert_eq!(out.lines().count(), 1);
        let (rounds, _, added) = history_with(&text, &round(1000 + ROUND_MIN_GAP_SECS, vec![]));
        assert!(added);
        assert_eq!(rounds.len(), 2);
    }

    #[test]
    fn hosts_json_reads_disk_free_gb_and_low_disk() {
        let v: Value = serde_json::from_str(
            r#"{"hosts":[{"name":"a","state":"free","disk_free_gb":3},{"name":"b","state":"free","low_disk":true},{"name":"c","state":"free","disk_free_kb":7},{"name":"d","state":"free","low_disk":false}]}"#,
        )
        .unwrap();
        let h = hosts_from_json(&v);
        assert_eq!(h[0].disk_free_kb, Some(3 * 1024 * 1024));
        assert_eq!(h[1].disk_free_kb, Some(0));
        assert_eq!(h[2].disk_free_kb, Some(7));
        assert_eq!(h[3].disk_free_kb, None);
    }

    #[test]
    fn a_fallback_receipt_with_end_rc_has_a_result() {
        assert!(has_result("x\nEND rc=0\n"));
        assert!(has_result("HEAD-END abc\n"));
        assert!(!has_result("RC knife_X=1\n"));
        let rows = u19_receipts(&[rc("f", "FMT-OK\nEND rc=0\n", 99_999, true, false)]);
        assert!(rows.is_empty(), "{rows:?}");
    }

    #[test]
    fn kill0_eperm_is_unknown() {
        assert_eq!(kill0_alive(true, ""), Some(true));
        assert_eq!(kill0_alive(false, "kill: 1: Operation not permitted"), None);
        assert_eq!(kill0_alive(false, "kill: 1: No such process"), Some(false));
    }

    #[test]
    fn only_rid_out_names_are_receipts() {
        assert_eq!(
            rid_of_out("u3-ebb36-efa7fbd86.out"),
            Some("u3-ebb36-efa7fbd86")
        );
        assert_eq!(rid_of_out(".out"), None);
        assert_eq!(rid_of_out("a b.out"), None);
        assert_eq!(rid_of_out("x.done"), None);
    }

    #[test]
    fn v2_subject_is_a_colon_free_slug() {
        assert_eq!(slug("andymini offline"), "andymini-offline");
        assert_eq!(slug("@56cda open"), "56cda-open");
        assert_eq!(
            slug("receipt u3-ebb36-6877da8b0"),
            "receipt-u3-ebb36-6877da8b0"
        );
        assert_eq!(slug("knife X:1 in r"), "knife-x-1-in-r");
    }

    #[test]
    fn v2_fingerprint_changes_only_with_the_evidence() {
        let a = fingerprint("U3", "offline");
        assert_eq!(a, fingerprint("U3", "offline"));
        assert_ne!(a, fingerprint("U3", "disk under 5 GB"));
        assert!(a.starts_with("u3-") && !a.contains(':'), "{a}");
    }

    #[test]
    fn the_same_state_two_rounds_on_keeps_its_fingerprint() {
        let fp = |rows: Vec<Blocker>| -> Vec<String> {
            rows.iter().map(|b| fingerprint(b.code, &b.state)).collect()
        };
        let off = |t| round(t, vec![host("m", "offline", None)]);
        let a = fp(u3(&[off(1), off(2)]));
        let b = fp(u3(&[off(1), off(2), off(3)]));
        assert_eq!(a.len(), 1);
        assert_eq!(a, b, "the window slid, the machine is still offline");
        let low = |t, gb| round(t, vec![host("m", "busy", Some(gb))]);
        let d1 = fp(u3(&[low(1, 12), low(2, 11)]));
        let d2 = fp(u3(&[low(1, 12), low(2, 11), low(3, 11)]));
        let d3 = fp(u3(&[low(2, 11), low(3, 3)]));
        assert_eq!(d1, d2);
        assert_ne!(d1, d3, "a lower disk grade is news");
        let open = |t, h| Round {
            as_of: t,
            seat_open_hours: [("56cda".to_string(), h)].into_iter().collect(),
            ..Round::default()
        };
        assert_eq!(
            fp(u9(&[open(1, 30), open(2, 31), open(3, 32)])),
            fp(u9(&[open(2, 31), open(3, 32), open(4, 33)]))
        );
    }

    #[test]
    fn v2_source_keeps_first_seen_and_never_writes_blocks() {
        let b = Blocker::new("U3", "andymini offline".into(), vec!["e".into()], "維運");
        let old = json!({"rows": [{"id": "U3:andymini-offline", "since_ms": 1000}]});
        let doc = src_doc(std::slice::from_ref(&b), 9000, Some(&old), &[]);
        assert_eq!(doc["schema"], SRC_SCHEMA);
        let r = &doc["rows"][0];
        assert_eq!(r["id"], "U3:andymini-offline");
        assert_eq!(r["since_ms"], 1000);
        assert_eq!(r["as_of_ms"], 9000);
        assert_eq!(r["source"], "u-3");
        assert!(r.get("blocks").is_none());
        let fresh = src_doc(&[b], 9000, None, &["aib-lease hosts".into()]);
        assert_eq!(fresh["rows"][0]["since_ms"], 9000);
        assert_eq!(fresh["unreadable"][0]["name"], "aib-lease hosts");
    }

    #[test]
    fn unreadable_is_named_by_its_head_and_kept_whole() {
        let e = unreadable_entry("disk of mini (aib-lease hosts has no disk field)");
        assert_eq!(e["name"], "disk of mini");
        assert_eq!(e["why"], "disk of mini (aib-lease hosts has no disk field)");
        let e = unreadable_entry("history not written: denied");
        assert_eq!(e["name"], "history not written");
        assert_eq!(unreadable_entry("ps")["name"], "ps");
    }

    #[test]
    fn unreadable_covers_only_the_codes_it_hides() {
        let c = |t: &str| unreadable_entry(t)["covers"].clone();
        assert_eq!(
            c("remote unfinished receipts not read (test machines' ~/wt.noindex)"),
            json!(["U19", "U20"])
        );
        assert_eq!(c("aib-lease hosts"), json!(["U3"]));
        assert_eq!(
            c("disk of mini (aib-lease hosts has no disk field)"),
            json!(["U3"])
        );
        assert_eq!(c("tmux panes"), json!(["U9"]));
        assert_eq!(c("aib lease status"), json!(["U19"]));
        assert_eq!(c("something new"), json!(["U3", "U9", "U19", "U20"]));
    }

    #[test]
    fn json_is_read_whatever_the_exit_code() {
        assert_eq!(
            json_whatever_rc(Some(4), br#"{"state":"held"}"#).map(|v| v["state"].clone()),
            Some(json!("held"))
        );
        assert!(json_whatever_rc(Some(0), b"not json").is_none());
    }

    #[test]
    fn a_host_with_no_disk_field_is_named() {
        let v = json!({"hosts": [{"name": "old", "state": "free"}, {"name": "new", "state": "free", "disk_free_gb": 9}]});
        assert_eq!(hosts_without_disk(&v), vec!["old".to_string()]);
    }

    #[test]
    fn local_receipts_read_out_and_done_from_the_folder() {
        let d = std::env::temp_dir().join(format!("u3b-local-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        std::fs::write(d.join("fin-0123abcde.out"), "HEAD-END x\n").unwrap();
        std::fs::write(d.join("fin-0123abcde.done"), "").unwrap();
        std::fs::write(d.join("run-0123abcdf.out"), "START\n").unwrap();
        std::fs::write(d.join("notes.txt"), "x").unwrap();
        let mut un = Vec::new();
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let r = local_receipts(&d, now, &mut un);
        let _ = std::fs::remove_dir_all(&d);
        let got: Vec<(String, bool)> = r.iter().map(|x| (x.rid.clone(), x.done)).collect();
        assert_eq!(
            got,
            vec![
                ("fin-0123abcde".into(), true),
                ("run-0123abcdf".into(), false)
            ]
        );
    }

    #[test]
    fn every_row_only_tells() {
        let h = vec![
            round(1, vec![host("m", "offline", None)]),
            round(2, vec![host("m", "offline", None)]),
        ];
        let rows = blockers(
            &h,
            &[rc(
                "r",
                "RC knife_X=101 (want non-0)\nHEAD-END abc\n",
                1,
                true,
                false,
            )],
            None,
        );
        assert_eq!(rows.len(), 2, "{rows:?}");
        assert!(rows.iter().all(|b| !b.auto));
        assert!(
            blockers(
                &h,
                &[rc("r", "RC knife_X=101 (want non-0)\n", 1, false, true)],
                None
            )
            .iter()
            .all(|b| b.code != "U20"),
            "reason: unfinished_receipt_judged_for_knives"
        );
    }
}
