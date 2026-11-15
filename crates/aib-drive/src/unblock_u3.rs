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
        }
    }
    pub fn to_json(&self) -> Value {
        json!({
            "code": self.code, "key": self.key, "subject": self.subject,
            "evidence": self.evidence, "owner_role": self.owner_role, "auto": self.auto,
        })
    }
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
            out.push(Blocker::new(
                "U3",
                format!("{} offline", h.name),
                ev,
                "維運",
            ));
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
            out.push(Blocker::new("U3", format!("{} disk", h.name), ev, "維運"));
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
        out.push(Blocker::new("U9", "swap".into(), ev, "各席"));
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
            out.push(Blocker::new("U9", format!("@{seat} open"), ev, "各席"));
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
/// [`U19_STUCK_SECS`], nothing running it; or done without a `HEAD-END` line and not a
/// `REFUSED` (a refusal is a result).
pub fn u19_receipts(receipts: &[Receipt]) -> Vec<Blocker> {
    let mut out = Vec::new();
    for r in receipts {
        if !r.done && !r.running && r.age_secs >= U19_STUCK_SECS {
            out.push(Blocker::new(
                "U19",
                format!("receipt {}", r.rid),
                vec![format!(
                    "{}.out {} min old, no .done, no process",
                    r.rid,
                    r.age_secs / 60
                )],
                "收據擁有席",
            ));
        } else if r.done
            && !r.text.lines().any(|l| l.starts_with("HEAD-END "))
            && !r.text.lines().any(|l| l.starts_with("REFUSED"))
        {
            out.push(Blocker::new(
                "U19",
                format!("receipt {}", r.rid),
                vec![format!("{}: done but no HEAD-END line", r.rid)],
                "收據擁有席",
            ));
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
        )],
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
            out.push(Blocker::new(
                "U20",
                format!("knife {name} in {rid}"),
                vec![format!("{rid}: {l}")],
                "刀作者",
            ));
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

/// Parse `aib-lease hosts` JSON into this round's machines.
pub fn hosts_from_json(v: &Value) -> Vec<HostSnap> {
    v.get("hosts")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(|h| {
                    Some(HostSnap {
                        name: h.get("name")?.as_str()?.to_owned(),
                        state: h.get("state")?.as_str()?.to_owned(),
                        disk_free_kb: h.get("disk_free_kb").and_then(Value::as_u64),
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

/// Keep the newest [`HISTORY_KEEP`] rounds of the history text, then add `round`.
pub fn history_with(text: &str, round: &Round) -> (Vec<Round>, String) {
    let mut rounds: Vec<Round> = text
        .lines()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
        .filter_map(|v| Round::from_json(&v))
        .collect();
    rounds.push(round.clone());
    let skip = rounds.len().saturating_sub(HISTORY_KEEP);
    let rounds: Vec<Round> = rounds.into_iter().skip(skip).collect();
    let mut out = String::new();
    for r in &rounds {
        out.push_str(&r.to_json().to_string());
        out.push('\n');
    }
    (rounds, out)
}

/// Run a program and return its stdout when it exits 0.
fn read_cmd(prog: &str, args: &[&str]) -> Option<String> {
    let o = std::process::Command::new(prog).args(args).output().ok()?;
    o.status
        .success()
        .then(|| String::from_utf8_lossy(&o.stdout).into_owned())
}

/// This round's facts from the machine (each source that cannot be read is named).
fn gather(root: &Path, now: u64, unreadable: &mut Vec<String>) -> Round {
    let lease_hosts = root.join("bin/aib-lease-hosts");
    let hosts = match read_cmd(&lease_hosts.to_string_lossy(), &["hosts"])
        .and_then(|s| serde_json::from_str::<Value>(&s).ok())
    {
        Some(v) => hosts_from_json(&v),
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

/// This machine's lease; the pid is checked here (`kill -0`).
fn lease(root: &Path, unreadable: &mut Vec<String>) -> Option<Lease> {
    let aib = root.join("bin/aib");
    let prog = if aib.exists() {
        aib.to_string_lossy().into_owned()
    } else {
        "aib".to_owned()
    };
    let Some(v) = read_cmd(&prog, &["lease", "status", "--json"])
        .and_then(|s| serde_json::from_str::<Value>(&s).ok())
    else {
        unreadable.push("aib lease status".into());
        return None;
    };
    let l = v.get("lease")?;
    let pid = l
        .get("pid")
        .and_then(Value::as_u64)
        .and_then(|p| u32::try_from(p).ok());
    let pid_alive = pid.map(|p| {
        std::process::Command::new("kill")
            .args(["-0", &p.to_string()])
            .stderr(std::process::Stdio::null())
            .status()
            .is_ok_and(|s| s.success())
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
    "usage: aib-drive unblock-u3 [--record] [--json]   (read-only; --record appends this round to drive/u3-history.jsonl)\n";

/// `aib-drive unblock-u3`: rc 0 (rows or none), 2 usage / no state root.
pub fn run(args: &[String], root: Option<&Path>, now: u64) -> (i32, String) {
    let mut record = false;
    let mut as_json = false;
    for a in args {
        match a.as_str() {
            "--record" => record = true,
            "--json" => as_json = true,
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
    let (history, text) = history_with(&old, &round);
    // without --record the round is judged but not kept: the streaks only grow on record
    if record {
        if let Err(e) = std::fs::create_dir_all(root.join("drive"))
            .and_then(|()| std::fs::write(&hist_path, text))
        {
            unreadable.push(format!("history not written: {e}"));
        }
    }
    let rec = receipts(root, now);
    let l = lease(root, &mut unreadable);
    let rows = blockers(&history, &rec, l.as_ref());
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
        let (rounds, out) = history_with(&text, &round(999, vec![]));
        assert_eq!(rounds.len(), HISTORY_KEEP);
        assert_eq!(rounds.last().map(|r| r.as_of), Some(999));
        assert_eq!(rounds[0].as_of, 6);
        assert_eq!(out.lines().count(), HISTORY_KEEP);
        let back = Round::from_json(&serde_json::from_str(out.lines().next().unwrap()).unwrap());
        assert_eq!(back, Some(rounds[0].clone()));
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
