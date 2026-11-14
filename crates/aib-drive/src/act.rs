//! `aib-drive act` / `aib-drive switch` (S79 n-3, IFACE-s79-drive-519d0-1115 §3).
//!
//! act turns the `next` list into at most three kinds of action, each behind its own switch:
//! `nudge` (a dm still queued after 5 minutes), `open_review` (a layer-3 row: a review
//! dispatch dm until aib-review's `open` exists) and `dispatch` (a layer-5 / 6 row). It never
//! merges, pushes, deletes, rebases, reruns or cancels CI, or notifies the whole fleet.
//!
//! **This slice ships `act --once --dry-run` only**: it reads the next and seats JSON (local
//! structs written from IFACE §1 / §2), works out this round's actions and writes `would`
//! lines to `<root>/drive/act-dryrun.jsonl`. It sends nothing. The comparison period is exactly
//! this file against the coordinator's real `派工:` first lines. Sending, and the intents
//! ledger (aib-ledger) that sending needs, come with the next slice.
//!
//! Rules applied (IFACE §3, DEV-S79 §2.2):
//! - the switch file `<root>/drive/act.json` defaults to all off; `switch` flips one and
//!   records who and why in `switches.jsonl`. A dry run works out every action whatever the
//!   switches say and records whether that switch was on.
//! - one holder: `<root>/drive/act.lock` = `<pid> <expires ms>`, TTL 5 minutes; not taken ⇒ rc 5.
//! - dedupe key = action + PR or node + head + seat; a key already in the journal is not
//!   done again.
//! - throttle: one dm per seat per 30 minutes; several rows for one seat in one round go in
//!   one dm (the first is the work, the rest are "其後"); unread > 10 ⇒ not sent; `on_deck`
//!   ⇒ not sent; the row's size must fit the seat's size; only `dispatchable: yes` dev seats.
//! - wrap up first: a seat whose `first` list is not empty gets only 「先做收尾:…」.
//! - nudge: once per dm; after that the sender and the coordinator are told instead, once.
//!
//! Exit codes (IFACE §0): 0 done · 2 usage · 3 a source could not be read · 4 paused · 5 the
//! lock was not taken, or a state file is broken.

use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashSet};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

pub const LOCK_TTL_MS: i64 = 5 * 60 * 1000;
pub const NUDGE_AFTER_MIN: i64 = 5;
pub const SEAT_GAP_MIN: i64 = 30;
pub const UNREAD_MAX: u64 = 10;
/// The coordinator seat: told about undelivered dms, never handed development work.
pub const COORDINATOR: &str = "519d0";

pub const RC_OK: u8 = 0;
pub const RC_USAGE: u8 = 2;
pub const RC_SOURCE: u8 = 3;
pub const RC_PAUSED: u8 = 4;
pub const RC_LOCK: u8 = 5;

// ---- inputs (local structs; the integration takes next.rs / seats.rs types) ----

/// A field that is null reads as its default (IFACE r4 #4: sig and next write null when a
/// source could not be read; `#[serde(default)]` alone only covers a missing field).
fn nd<'de, D, T>(d: D) -> Result<T, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Deserialize<'de> + Default,
{
    Ok(Option::<T>::deserialize(d)?.unwrap_or_default())
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct NextOut {
    #[serde(default, deserialize_with = "nd")]
    pub complete: bool,
    #[serde(default, deserialize_with = "nd")]
    pub rows: Vec<Row>,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Row {
    #[serde(default, deserialize_with = "nd")]
    pub rank: u64,
    #[serde(default, deserialize_with = "nd")]
    pub layer: u8,
    #[serde(default, deserialize_with = "nd")]
    pub what: String,
    #[serde(default, deserialize_with = "nd")]
    pub pr: Option<u64>,
    #[serde(default, deserialize_with = "nd")]
    pub node: Option<String>,
    /// The PR head (40 hex) when the row is about a PR; part of the dedupe key.
    #[serde(default, deserialize_with = "nd")]
    pub head: Option<String>,
    #[serde(default, deserialize_with = "nd")]
    pub to: Option<String>,
    #[serde(default, deserialize_with = "nd")]
    pub why_him: String,
    #[serde(default, deserialize_with = "nd")]
    pub why_now: String,
    #[serde(default, deserialize_with = "nd")]
    pub needs: String,
    #[serde(default, deserialize_with = "nd")]
    pub size: String,
    #[serde(default, deserialize_with = "nd")]
    pub data_at: String,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct SeatsOut {
    #[serde(default, deserialize_with = "nd")]
    pub seats: Vec<Seat>,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Fact {
    #[serde(default, deserialize_with = "nd")]
    pub v: String,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Capacity {
    #[serde(default, deserialize_with = "nd")]
    pub unread: Option<u64>,
    #[serde(default, deserialize_with = "nd")]
    /// null = not known (IFACE r4 #4): not taken as on deck, but the dm carries a note.
    pub on_deck: Option<bool>,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Seat {
    pub seat: String,
    #[serde(default, deserialize_with = "nd")]
    pub role: String,
    #[serde(default, deserialize_with = "nd")]
    pub alive: Fact,
    #[serde(default, deserialize_with = "nd")]
    pub capacity: Capacity,
    #[serde(default, deserialize_with = "nd")]
    pub dispatchable: String,
    #[serde(default, deserialize_with = "nd")]
    pub size: String,
    #[serde(default, deserialize_with = "nd")]
    pub first: Vec<String>,
}

/// One dm waiting in a queue (the dry run reads a list of these from `--dm`).
#[derive(Debug, Clone, Deserialize, Default)]
pub struct Waiting {
    pub id: String,
    pub to: String,
    #[serde(default, deserialize_with = "nd")]
    pub from: String,
    #[serde(default, deserialize_with = "nd")]
    pub state: String,
    /// Minutes since it was queued.
    #[serde(default, deserialize_with = "nd")]
    pub queued_min: i64,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Switches {
    pub paused: bool,
    pub nudge: bool,
    pub open_review: bool,
    pub dispatch: bool,
}

impl Switches {
    pub const NAMES: [&'static str; 4] = ["paused", "nudge", "open_review", "dispatch"];

    pub fn parse(text: &str) -> Result<Switches, String> {
        let v: Value = serde_json::from_str(text).map_err(|e| format!("act.json: {e}"))?;
        let o = v.as_object().ok_or("act.json: not an object")?;
        let mut s = Switches::default();
        for (k, val) in o {
            let b = val
                .as_bool()
                .ok_or_else(|| format!("act.json: {k} is not true / false"))?;
            match k.as_str() {
                "paused" => s.paused = b,
                "nudge" => s.nudge = b,
                "open_review" => s.open_review = b,
                "dispatch" => s.dispatch = b,
                _ => return Err(format!("act.json: unknown switch {k}")),
            }
        }
        Ok(s)
    }

    pub fn get(&self, name: &str) -> bool {
        match name {
            "paused" => self.paused,
            "nudge" | "tell_sender" => self.nudge,
            "open_review" => self.open_review,
            "dispatch" | "wrap_up" => self.dispatch,
            _ => false,
        }
    }

    pub fn set(&mut self, name: &str, on: bool) -> bool {
        match name {
            "paused" => self.paused = on,
            "nudge" => self.nudge = on,
            "open_review" => self.open_review = on,
            "dispatch" => self.dispatch = on,
            _ => return false,
        }
        true
    }

    pub fn to_json(self) -> Value {
        json!({"paused": self.paused, "nudge": self.nudge,
               "open_review": self.open_review, "dispatch": self.dispatch})
    }
}

/// What earlier rounds did (from the journal): keys already handled and when each seat was
/// last sent a dm.
#[derive(Debug, Clone, Default)]
pub struct History {
    pub keys: HashSet<String>,
    pub last_sent_ms: BTreeMap<String, i64>,
}

impl History {
    /// Read the `would` lines of a journal. A line that is not a JSON object is an error
    /// (the state file is broken: rc 5), never skipped.
    pub fn parse(text: &str) -> Result<History, String> {
        let mut h = History::default();
        for (n, line) in text.lines().enumerate() {
            if line.trim().is_empty() {
                continue;
            }
            let v: Value =
                serde_json::from_str(line).map_err(|e| format!("journal line {}: {e}", n + 1))?;
            let o = v
                .as_object()
                .ok_or_else(|| format!("journal line {}: not an object", n + 1))?;
            if o.get("kind").and_then(Value::as_str) != Some("would") {
                continue;
            }
            for k in o
                .get("keys")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                if let Some(k) = k.as_str() {
                    h.keys.insert(k.to_owned());
                }
            }
            let to = o.get("to").and_then(Value::as_str);
            let at = o.get("at_ms").and_then(Value::as_i64);
            let counts = o.get("throttled").and_then(Value::as_bool).unwrap_or(false);
            if let (Some(to), Some(at), true) = (to, at, counts) {
                let e = h.last_sent_ms.entry(to.to_owned()).or_insert(at);
                *e = (*e).max(at);
            }
        }
        Ok(h)
    }
}

// ---- the plan ----

/// One dm act would send.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Would {
    /// dispatch / open_review / wrap_up / nudge / tell_sender
    pub action: String,
    pub to: String,
    pub first_line: String,
    pub body: Vec<String>,
    pub keys: Vec<String>,
    /// Counts toward the one-dm-per-seat-per-30-minutes limit.
    pub throttled: bool,
    pub ranks: Vec<u64>,
}

/// Why a row was not acted on this round.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Skip {
    pub rank: u64,
    pub why: String,
}

fn size_rank(s: &str) -> Option<u8> {
    match s {
        "review" => Some(1),
        "slice" => Some(2),
        "mainline" => Some(3),
        _ => None,
    }
}

fn hex40(s: &str) -> bool {
    s.len() == 40 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// `action:<PR or node>:<head or ->:<seat>`
pub fn key(action: &str, row: &Row, seat: &str) -> String {
    let what = match (row.pr, row.node.as_deref()) {
        (Some(pr), _) => format!("#{pr}"),
        (None, Some(n)) => n.to_owned(),
        (None, None) => format!("rank{}", row.rank),
    };
    let head = row.head.as_deref().filter(|h| hex40(h)).unwrap_or("-");
    format!("{action}:{what}:{head}:{seat}")
}

fn action_of(layer: u8) -> Option<&'static str> {
    match layer {
        3 => Some("open_review"),
        5 | 6 => Some("dispatch"),
        _ => None,
    }
}

fn row_line(action: &str, row: &Row, seat: &str) -> String {
    match action {
        // next already writes some review rows as 「審 …」: one 審, not 「審 審」 (a9136 F26)
        "open_review" if !row.what.starts_with('審') => format!("派工: 審 {} → {seat}", row.what),
        _ => format!("派工: {} → {seat}", row.what),
    }
}

fn row_body(row: &Row) -> Vec<String> {
    let mut b = Vec::new();
    if let Some(n) = &row.node {
        b.push(format!("節點:{n}"));
    }
    if let Some(pr) = row.pr {
        b.push(format!("PR:#{pr}"));
    }
    if let Some(h) = &row.head {
        b.push(format!("頭:{h}"));
    }
    if !row.why_him.is_empty() {
        b.push(format!("為什麼是你:{}", row.why_him));
    }
    if !row.why_now.is_empty() {
        b.push(format!("為什麼是現在:{}", row.why_now));
    }
    if !row.data_at.is_empty() {
        b.push(format!("資料時間:{}", row.data_at));
    }
    b
}

/// This round's actions, in the order of `next`, and the rows not acted on.
pub fn plan(
    next: &NextOut,
    seats: &SeatsOut,
    waiting: &[Waiting],
    hist: &History,
    now_ms: i64,
) -> (Vec<Would>, Vec<Skip>) {
    let mut out: Vec<Would> = Vec::new();
    let mut skips = Vec::new();
    // seats that got a dm this round (one dm per seat: later rows join it)
    let mut this_round: BTreeMap<String, usize> = BTreeMap::new();
    let mut rows: Vec<&Row> = next.rows.iter().collect();
    rows.sort_by_key(|r| r.rank);
    for row in rows {
        let mut skip = |why: String| {
            skips.push(Skip {
                rank: row.rank,
                why,
            })
        };
        let Some(action) = action_of(row.layer) else {
            skip(format!("layer {} is not an act action", row.layer));
            continue;
        };
        if row.needs != "none" {
            skip(format!(
                "needs {}",
                if row.needs.is_empty() {
                    "?"
                } else {
                    row.needs.as_str()
                }
            ));
            continue;
        }
        let Some(to) = row.to.as_deref().filter(|t| !t.is_empty()) else {
            skip("no seat (to)".into());
            continue;
        };
        let Some(seat) = seats.seats.iter().find(|s| s.seat == to) else {
            skip(format!("{to}: no seat facts"));
            continue;
        };
        if seat.role != "dev" || to == COORDINATOR {
            skip(format!("{to}: not a dev seat"));
            continue;
        }
        if seat.alive.v != "yes" {
            skip(format!("{to}: alive {}", seat.alive.v));
            continue;
        }
        if seat.dispatchable != "yes" {
            skip(format!("{to}: dispatchable {}", seat.dispatchable));
            continue;
        }
        let deck_note = match seat.capacity.on_deck {
            Some(true) => {
                skip(format!("{to}: on deck in the merge queue"));
                continue;
            }
            Some(false) => None,
            None => Some(format!("註:{to} 是不是合併隊下一張讀不到(on_deck null)")),
        };
        match seat.capacity.unread {
            Some(u) if u <= UNREAD_MAX => {}
            Some(u) => {
                skip(format!("{to}: {u} unread"));
                continue;
            }
            None => {
                skip(format!("{to}: unread unknown"));
                continue;
            }
        }
        match (size_rank(&row.size), size_rank(&seat.size)) {
            (Some(r), Some(s)) if r <= s => {}
            _ => {
                skip(format!(
                    "{to}: size {} does not fit {}",
                    row.size, seat.size
                ));
                continue;
            }
        }
        let k = key(action, row, to);
        if hist.keys.contains(&k) {
            skip(format!("already done ({k})"));
            continue;
        }
        if let Some(&i) = this_round.get(to) {
            // one dm per seat per round: this row is 「其後」 in the first dm (unless that dm
            // is a wrap-up, which carries no new work)
            let w = &mut out[i];
            if w.action == "wrap_up" {
                skip(format!("{to}: wrap up first"));
            } else {
                w.body.push(format!("其後:{}", row_line(action, row, to)));
                w.keys.push(k);
                w.ranks.push(row.rank);
            }
            continue;
        }
        if let Some(&last) = hist.last_sent_ms.get(to) {
            if now_ms - last < SEAT_GAP_MIN * 60_000 {
                skip(format!(
                    "{to}: sent {} min ago (one per {SEAT_GAP_MIN} min)",
                    (now_ms - last) / 60_000
                ));
                continue;
            }
        }
        if !seat.first.is_empty() {
            // wrap up first: no new work in this dm, and the row comes back next round
            this_round.insert(to.to_owned(), out.len());
            out.push(Would {
                action: "wrap_up".into(),
                to: to.to_owned(),
                first_line: format!("派工: 先做收尾 → {to}"),
                body: vec![format!("先做收尾:{}", seat.first.join("、"))],
                keys: Vec::new(),
                throttled: true,
                ranks: vec![row.rank],
            });
            skip(format!("{to}: wrap up first"));
            continue;
        }
        let mut body = row_body(row);
        if let Some(n) = &deck_note {
            body.push(n.clone());
        }
        this_round.insert(to.to_owned(), out.len());
        out.push(Would {
            action: action.into(),
            to: to.to_owned(),
            first_line: row_line(action, row, to),
            body,
            keys: vec![k],
            throttled: true,
            ranks: vec![row.rank],
        });
    }
    for w in waiting {
        // the queue file's status (dm-ctl's delivery stage name was sort-queued)
        if w.state != "queued" || w.queued_min < NUDGE_AFTER_MIN {
            continue;
        }
        let nk = format!("nudge:{}:-:{}", w.id, w.to);
        let tk = format!("tell_sender:{}:-:{}", w.id, w.from);
        if !hist.keys.contains(&nk) {
            out.push(Would {
                action: "nudge".into(),
                to: w.to.clone(),
                first_line: format!("提醒: 你有一封還沒收的信 {} → {}", w.id, w.to),
                body: vec![format!("寄件人 {},已排隊 {} 分", w.from, w.queued_min)],
                keys: vec![nk],
                throttled: false,
                ranks: Vec::new(),
            });
        } else if !hist.keys.contains(&tk) {
            let mut to = vec![w.from.clone()];
            if w.from != COORDINATOR {
                to.push(COORDINATOR.to_owned());
            }
            out.push(Would {
                action: "tell_sender".into(),
                to: to.join(","),
                first_line: format!(
                    "未送達: {} 給 {} 的信已排隊 {} 分,提醒過一次",
                    w.id, w.to, w.queued_min
                ),
                body: vec!["要不要換人請統籌席決定".into()],
                keys: vec![tk],
                throttled: false,
                ranks: Vec::new(),
            });
        }
    }
    (out, skips)
}

// ---- time ----

/// `2026-11-15T00:20:00.000Z` for milliseconds since 1970 (UTC).
pub fn rfc3339_ms(ms: i64) -> String {
    let secs = ms.div_euclid(1000);
    let milli = ms.rem_euclid(1000);
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}.{milli:03}Z",
        rem / 3600,
        rem % 3600 / 60,
        rem % 60
    )
}

/// Would this dm go out if act were live: its switch is on, and a pause holds back only new
/// work (dispatch / open_review); wrap-ups, nudges and telling the sender still go out.
pub fn would_send(sw: Switches, action: &str) -> bool {
    let new_work = matches!(action, "dispatch" | "open_review");
    sw.get(action) && !(sw.paused && new_work)
}

/// The journal line of one `would`.
pub fn would_line(w: &Would, now_ms: i64, sw: Switches) -> String {
    json!({
        "v": 1, "kind": "would", "at": rfc3339_ms(now_ms), "at_ms": now_ms,
        "action": w.action, "to": w.to, "first_line": w.first_line, "body": w.body,
        "keys": w.keys, "throttled": w.throttled, "ranks": w.ranks,
        "switch_on": sw.get(&w.action), "paused": sw.paused,
        "would_send": would_send(sw, &w.action),
    })
    .to_string()
}

/// The journal line that closes one round (what was seen and why rows were skipped), so a
/// disagreement in the comparison period can be traced.
pub fn round_line(now_ms: i64, rows: usize, n: usize, skips: &[Skip], complete: bool) -> String {
    json!({
        "v": 1, "kind": "round", "at": rfc3339_ms(now_ms), "at_ms": now_ms,
        "next_complete": complete, "rows": rows, "would": n,
        "skipped": skips.iter().map(|s| json!({"rank": s.rank, "why": s.why})).collect::<Vec<_>>(),
    })
    .to_string()
}

// ---- files: root, lock, journal, switches ----

fn nonempty(k: &str) -> Option<String> {
    std::env::var(k).ok().filter(|v| !v.is_empty())
}

/// `<root>/drive`, root in `observe::state_root`'s order: `EZ2_AIB_STATE_ROOT`, then
/// `EZ2_AIB_HOME`, then `~/.ez2-aib` (IFACE r4 #3; the integration calls observe::state_root).
pub fn drive_dir() -> Option<PathBuf> {
    let root = match nonempty("EZ2_AIB_STATE_ROOT").or_else(|| nonempty("EZ2_AIB_HOME")) {
        Some(h) => PathBuf::from(h),
        None => {
            #[cfg(windows)]
            let home = nonempty("USERPROFILE").or_else(|| nonempty("HOME"));
            #[cfg(not(windows))]
            let home = nonempty("HOME").or_else(|| nonempty("USERPROFILE"));
            PathBuf::from(home?).join(".ez2-aib")
        }
    };
    Some(root.join("drive"))
}

pub fn now_ms() -> i64 {
    if let Some(n) = nonempty("AIB_DRIVE_NOW_MS").and_then(|v| v.parse().ok()) {
        return n;
    }
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_millis()).unwrap_or(i64::MAX))
}

/// The act lock: `<pid> <expires ms>`; removed on drop if it is still ours.
#[derive(Debug)]
pub struct Lock {
    path: PathBuf,
    mine: String,
}

impl Drop for Lock {
    fn drop(&mut self) {
        if fs::read_to_string(&self.path).ok().as_deref() == Some(self.mine.as_str()) {
            let _ = fs::remove_file(&self.path);
        }
    }
}

fn parse_lock(text: &str) -> Option<(u32, i64)> {
    let mut it = text.split_whitespace();
    let pid = it.next()?.parse().ok()?;
    let exp = it.next()?.parse().ok()?;
    it.next().is_none().then_some((pid, exp))
}

/// Take the lock, or say who holds it. An expired lock is moved aside (rename, so only one
/// taker wins) and taken; a lock file that does not parse is a broken state file.
pub fn take_lock(dir: &Path, now_ms: i64) -> Result<Lock, String> {
    let path = dir.join("act.lock");
    let mine = format!("{} {}", std::process::id(), now_ms + LOCK_TTL_MS);
    for _ in 0..2 {
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(mut f) => {
                f.write_all(mine.as_bytes())
                    .and_then(|()| f.sync_all())
                    .map_err(|e| format!("act.lock: {e}"))?;
                return Ok(Lock { path, mine });
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(e) => return Err(format!("act.lock: {e}")),
        }
        let held = fs::read_to_string(&path).map_err(|e| format!("act.lock: {e}"))?;
        let (pid, exp) =
            parse_lock(&held).ok_or_else(|| format!("act.lock is broken: {:?}", held.trim()))?;
        if exp > now_ms {
            return Err(format!(
                "act.lock held by pid {pid} for {} more s",
                (exp - now_ms) / 1000
            ));
        }
        let aside = dir.join(format!("act.lock.expired-{}", std::process::id()));
        if fs::rename(&path, &aside).is_ok() {
            let moved = fs::read_to_string(&aside).unwrap_or_default();
            if moved != held {
                // another taker got there first and this moved its new lock: put it back.
                // hard_link fails if a lock is there again; then leave the aside file for a
                // person and say so (never two holders)
                return Err(match fs::hard_link(&aside, &path) {
                    Ok(()) => {
                        let _ = fs::remove_file(&aside);
                        "act.lock changed while it was being taken over".into()
                    }
                    Err(e) => format!(
                        "act.lock changed while it was being taken over and could not be put back ({e}); see {}",
                        aside.display()
                    ),
                });
            }
            let _ = fs::remove_file(&aside);
        }
    }
    Err("act.lock: not taken".into())
}

/// Append lines to a journal file in one write, then sync.
pub fn append(path: &Path, lines: &[String]) -> Result<(), String> {
    if lines.is_empty() {
        return Ok(());
    }
    let mut text = lines.join("\n");
    text.push('\n');
    let mut f = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|e| format!("{}: {e}", path.display()))?;
    f.write_all(text.as_bytes())
        .and_then(|()| f.sync_all())
        .map_err(|e| format!("{}: {e}", path.display()))
}

fn read_opt(path: &Path) -> Result<Option<String>, String> {
    match fs::read_to_string(path) {
        Ok(t) => Ok(Some(t)),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(format!("{}: {e}", path.display())),
    }
}

pub fn read_switches(dir: &Path) -> Result<Switches, String> {
    match read_opt(&dir.join("act.json"))? {
        None => Ok(Switches::default()),
        Some(t) => Switches::parse(&t),
    }
}

/// Write act.json through a new file and a rename.
pub fn write_switches(dir: &Path, s: Switches) -> Result<(), String> {
    let tmp = dir.join(format!("act.json.new-{}", std::process::id()));
    fs::write(&tmp, format!("{}\n", s.to_json())).map_err(|e| format!("act.json: {e}"))?;
    fs::rename(&tmp, dir.join("act.json")).map_err(|e| format!("act.json: {e}"))
}

// ---- command lines ----

struct Flags {
    pairs: Vec<(String, Option<String>)>,
}

impl Flags {
    fn parse(raw: &[String], bools: &[&str], known: &[&str]) -> Result<Flags, String> {
        let mut pairs: Vec<(String, Option<String>)> = Vec::new();
        let mut i = 0;
        while i < raw.len() {
            let name = raw[i]
                .strip_prefix("--")
                .ok_or_else(|| format!("unexpected word {}", raw[i]))?;
            if !known.contains(&name) {
                return Err(format!("unknown flag --{name}"));
            }
            if pairs.iter().any(|(k, _)| k == name) {
                return Err(format!("--{name} given twice"));
            }
            if bools.contains(&name) {
                pairs.push((name.to_owned(), None));
            } else {
                let v = raw
                    .get(i + 1)
                    .filter(|v| !v.starts_with("--") && !v.is_empty())
                    .ok_or_else(|| format!("--{name} takes a value"))?;
                pairs.push((name.to_owned(), Some(v.clone())));
                i += 1;
            }
            i += 1;
        }
        Ok(Flags { pairs })
    }
    fn has(&self, k: &str) -> bool {
        self.pairs.iter().any(|(x, _)| x == k)
    }
    fn get(&self, k: &str) -> Option<&str> {
        self.pairs
            .iter()
            .find(|(x, _)| x == k)
            .and_then(|(_, v)| v.as_deref())
    }
}

pub const ACT_USAGE: &str = "\
aib-drive act --once --dry-run --next <next.json> --seats <seats.json> [--dm <waiting.json>] [--event <drive/event> [--by <名>]]
aib-drive switch <nudge|open_review|dispatch|paused> <on|off> --by <席 5 碼> --why \"<為什麼>\"
(這一版只有 --dry-run:算出這一輪會發的信,寫進 <root>/drive/act-dryrun.jsonl,不發任何信)";

fn read_json<T: for<'de> Deserialize<'de>>(path: &str) -> Result<T, String> {
    let t = fs::read_to_string(path).map_err(|e| format!("{path}: {e}"))?;
    serde_json::from_str(&t).map_err(|e| format!("{path}: {e}"))
}

/// The name act answers to when neither `--by` nor `AIB_DRIVE_BY` gives one: an event this
/// program writes carries it as `by` (wake: `--by`, else `AIB_DRIVE_BY`).
pub const ACT_BY: &str = "act";

/// A5 (IFACE §5 「自己發的信不觸發自己」): the id of the event that woke this round when act
/// itself wrote it (`wake::is_own`), so its own dms cannot start it again in a loop. No
/// event, an unreadable one or someone else's = `None` (an event only wakes; the round goes on).
pub fn own_event(event_text: Option<&str>, me: &str) -> Option<String> {
    let ev = crate::wake::parse(event_text?)?;
    crate::wake::is_own(&ev, me).then_some(ev.id)
}

/// `aib-drive act …` (the words after `act`). Prints what it would send.
pub fn cli_act(raw: &[String]) -> u8 {
    let f = match Flags::parse(
        raw,
        &["once", "dry-run"],
        &["once", "dry-run", "next", "seats", "dm", "event", "by"],
    ) {
        Ok(f) => f,
        Err(e) => {
            eprintln!("aib-drive act: {e}\n{ACT_USAGE}");
            return RC_USAGE;
        }
    };
    if !f.has("once") || !f.has("dry-run") {
        eprintln!(
            "aib-drive act: this version runs only --once --dry-run (sending comes with the intents ledger)\n{ACT_USAGE}"
        );
        return RC_USAGE;
    }
    let (Some(next_p), Some(seats_p)) = (f.get("next"), f.get("seats")) else {
        eprintln!("aib-drive act: --next and --seats are required\n{ACT_USAGE}");
        return RC_USAGE;
    };
    let me = f
        .get("by")
        .map(str::to_owned)
        .or_else(|| std::env::var("AIB_DRIVE_BY").ok().filter(|b| !b.is_empty()))
        .unwrap_or_else(|| ACT_BY.to_owned());
    if let Some(id) = f
        .get("event")
        .and_then(|p| own_event(fs::read_to_string(p).ok().as_deref(), &me))
    {
        println!("act dry-run: event {id} was written by {me} itself, no round");
        return RC_OK;
    }
    let next: NextOut = match read_json(next_p) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("aib-drive act: next: {e}");
            return RC_SOURCE;
        }
    };
    let seats: SeatsOut = match read_json(seats_p) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("aib-drive act: seats: {e}");
            return RC_SOURCE;
        }
    };
    let waiting: Vec<Waiting> = match f.get("dm").map(read_json::<Vec<Waiting>>) {
        None => Vec::new(),
        Some(Ok(v)) => v,
        Some(Err(e)) => {
            eprintln!("aib-drive act: dm: {e}");
            return RC_SOURCE;
        }
    };
    let Some(dir) = drive_dir() else {
        eprintln!("aib-drive act: no EZ2_AIB_HOME and no home folder");
        return RC_USAGE;
    };
    if let Err(e) = fs::create_dir_all(&dir) {
        eprintln!("aib-drive act: {}: {e}", dir.display());
        return RC_LOCK;
    }
    let now = now_ms();
    let _lock = match take_lock(&dir, now) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("aib-drive act: {e}");
            return RC_LOCK;
        }
    };
    let sw = match read_switches(&dir) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("aib-drive act: {e}");
            return RC_LOCK;
        }
    };
    let journal = dir.join("act-dryrun.jsonl");
    let hist = match read_opt(&journal).and_then(|t| History::parse(&t.unwrap_or_default())) {
        Ok(h) => h,
        Err(e) => {
            eprintln!("aib-drive act: act-dryrun.jsonl: {e}");
            return RC_LOCK;
        }
    };
    let (would, skips) = plan(&next, &seats, &waiting, &hist, now);
    let mut lines: Vec<String> = would.iter().map(|w| would_line(w, now, sw)).collect();
    lines.push(round_line(
        now,
        next.rows.len(),
        would.len(),
        &skips,
        next.complete,
    ));
    if let Err(e) = append(&journal, &lines) {
        eprintln!("aib-drive act: {e}");
        return RC_LOCK;
    }
    for w in &would {
        println!(
            "would {} [{}] {}",
            w.action,
            if sw.get(&w.action) { "開" } else { "關" },
            w.first_line
        );
    }
    println!(
        "act dry-run: {} would, {} skipped, rows {}{} → {}",
        would.len(),
        skips.len(),
        next.rows.len(),
        if sw.paused { ", paused" } else { "" },
        journal.display()
    );
    RC_OK
}

fn seat_ok(s: &str) -> bool {
    s.len() == 5 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// `aib-drive switch <name> <on|off> --by <seat> --why "…"`.
pub fn cli_switch(raw: &[String]) -> u8 {
    let (name, on, rest) = match raw {
        [n, v, rest @ ..] if Switches::NAMES.contains(&n.as_str()) && (v == "on" || v == "off") => {
            (n.as_str(), v == "on", rest)
        }
        _ => {
            eprintln!(
                "aib-drive switch: <nudge|open_review|dispatch|paused> <on|off>\n{ACT_USAGE}"
            );
            return RC_USAGE;
        }
    };
    let f = match Flags::parse(rest, &[], &["by", "why"]) {
        Ok(f) => f,
        Err(e) => {
            eprintln!("aib-drive switch: {e}");
            return RC_USAGE;
        }
    };
    let (Some(by), Some(why)) = (f.get("by"), f.get("why")) else {
        eprintln!("aib-drive switch: --by and --why are required");
        return RC_USAGE;
    };
    if !seat_ok(by) || why.trim().is_empty() {
        eprintln!("aib-drive switch: --by is a 5-hex seat and --why is not empty");
        return RC_USAGE;
    }
    let Some(dir) = drive_dir() else {
        eprintln!("aib-drive switch: no EZ2_AIB_HOME and no home folder");
        return RC_USAGE;
    };
    if let Err(e) = fs::create_dir_all(&dir) {
        eprintln!("aib-drive switch: {e}");
        return RC_LOCK;
    }
    let now = now_ms();
    let _lock = match take_lock(&dir, now) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("aib-drive switch: {e}");
            return RC_LOCK;
        }
    };
    let mut sw = match read_switches(&dir) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("aib-drive switch: {e}");
            return RC_LOCK;
        }
    };
    let was = sw.get(name);
    sw.set(name, on);
    let rec = json!({"v": 1, "at": rfc3339_ms(now), "at_ms": now, "by": by, "why": why,
                     "switch": name, "from": was, "to": on})
    .to_string();
    // the record first: a flip nobody can explain is worse than a record of a flip that failed
    if let Err(e) = append(&dir.join("switches.jsonl"), &[rec]) {
        eprintln!("aib-drive switch: {e}");
        return RC_LOCK;
    }
    if let Err(e) = write_switches(&dir, sw) {
        eprintln!("aib-drive switch: {e}");
        return RC_LOCK;
    }
    println!(
        "{name}: {} → {}",
        if was { "on" } else { "off" },
        if on { "on" } else { "off" }
    );
    RC_OK
}

#[cfg(test)]
mod tests {
    use super::*;

    const H: &str = "1b0d079b58bf33909f6b8302e82b543d40043581";
    const NOW: i64 = 1_794_700_800_000; // 2026-11-15T00:00:00.000Z

    /// A fresh folder under the system temp folder, removed on drop (no tempfile dependency:
    /// this slice does not touch Cargo.toml).
    pub struct Scratch(PathBuf);

    impl Scratch {
        pub fn new(tag: &str) -> Scratch {
            let nanos = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(0, |d| d.as_nanos());
            let p = std::env::temp_dir().join(format!(
                "aib-drive-act-{tag}-{}-{nanos}",
                std::process::id()
            ));
            fs::create_dir_all(&p).expect("mkdir scratch");
            Scratch(p)
        }
        pub fn path(&self) -> &Path {
            &self.0
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn seat(code: &str) -> Seat {
        Seat {
            seat: code.into(),
            role: "dev".into(),
            alive: Fact { v: "yes".into() },
            capacity: Capacity {
                unread: Some(2),
                on_deck: Some(false),
            },
            dispatchable: "yes".into(),
            size: "mainline".into(),
            first: Vec::new(),
        }
    }

    fn row(rank: u64, layer: u8, to: &str) -> Row {
        Row {
            rank,
            layer,
            what: format!("件{rank}"),
            pr: Some(700 + rank),
            node: Some(format!("S{rank}")),
            head: Some(H.into()),
            to: Some(to.into()),
            why_him: "交換審查".into(),
            why_now: "收據綠".into(),
            needs: "none".into(),
            size: "review".into(),
            data_at: "2026-11-15T00:00:00.000Z".into(),
        }
    }

    fn next(rows: Vec<Row>) -> NextOut {
        NextOut {
            complete: true,
            rows,
        }
    }

    fn seats(v: Vec<Seat>) -> SeatsOut {
        SeatsOut { seats: v }
    }

    #[test]
    fn a_review_row_becomes_a_review_dispatch_and_a_mainline_row_a_dispatch() {
        let n = next(vec![
            row(1, 3, "a9136"),
            row(2, 5, "ebb36"),
            row(3, 1, "f1fae"),
        ]);
        let (w, skips) = plan(
            &n,
            &seats(vec![seat("a9136"), seat("ebb36")]),
            &[],
            &History::default(),
            NOW,
        );
        assert_eq!(w.len(), 2, "{w:?}");
        assert_eq!(w[0].first_line, "派工: 審 件1 → a9136");
        assert_eq!(w[0].action, "open_review");
        assert_eq!(w[0].keys, vec![format!("open_review:#701:{H}:a9136")]);
        assert_eq!(w[1].first_line, "派工: 件2 → ebb36");
        assert!(w[1].body.contains(&"節點:S2".to_owned()), "{:?}", w[1].body);
        assert!(w[1].body.contains(&"為什麼是現在:收據綠".to_owned()));
        // E7 (IFACE r4 #5): the evidence is copied from the next row, never made up here
        assert!(w[1].body.contains(&"為什麼是你:交換審查".to_owned()));
        assert!(w[1].body.contains(&format!("頭:{H}")), "{:?}", w[1].body);
        assert_eq!(
            skips,
            vec![Skip {
                rank: 3,
                why: "layer 1 is not an act action".into()
            }]
        );
    }

    /// a9136 F26: next writes some review rows as 「審 …」; the first line has one 審.
    #[test]
    fn a_review_row_that_already_says_review_gets_one_review_word() {
        let mut r = row(1, 3, "a9136");
        r.what = "審 feat/x-1ced7-1115".into();
        let (w, _) = plan(
            &next(vec![r]),
            &seats(vec![seat("a9136")]),
            &[],
            &History::default(),
            NOW,
        );
        assert_eq!(w.len(), 1, "{w:?}");
        assert_eq!(w[0].first_line, "派工: 審 feat/x-1ced7-1115 → a9136");
    }

    /// A5: an event act wrote itself starts no round; someone else's, none or a broken one do.
    #[test]
    fn an_event_act_wrote_itself_is_its_own_and_no_other_is() {
        let ev = |by: &str| {
            format!(r#"{{"id":"e1","kind":"dm_done","ref":"dm/q_1","at":1794700800,"by":"{by}"}}"#)
        };
        assert_eq!(own_event(Some(&ev("act")), "act"), Some("e1".to_owned()));
        assert_eq!(own_event(Some(&ev("1ced7")), "act"), None);
        assert_eq!(own_event(Some(&ev("act")), "a9136"), None);
        assert_eq!(own_event(None, "act"), None);
        assert_eq!(own_event(Some("not json"), "act"), None);
    }

    #[test]
    fn rows_for_one_seat_in_one_round_go_in_one_dm() {
        let n = next(vec![row(2, 5, "a9136"), row(1, 3, "a9136")]);
        let (w, _) = plan(
            &n,
            &seats(vec![seat("a9136")]),
            &[],
            &History::default(),
            NOW,
        );
        assert_eq!(w.len(), 1);
        assert_eq!(
            w[0].first_line, "派工: 審 件1 → a9136",
            "rank order, not file order"
        );
        assert_eq!(
            w[0].body.last().map(String::as_str),
            Some("其後:派工: 件2 → a9136")
        );
        assert_eq!(w[0].keys.len(), 2);
    }

    #[test]
    fn a_seat_that_is_busy_lost_full_or_on_deck_is_not_sent() {
        let mut lost = seat("c477a");
        lost.alive.v = "no".into();
        let mut busy = seat("b433b");
        busy.dispatchable = "no".into();
        let mut full = seat("2240e");
        full.capacity.unread = Some(11);
        let mut deck = seat("7b81e");
        deck.capacity.on_deck = Some(true);
        let mut small = seat("ebb36");
        small.size = "review".into();
        let mut unknown = seat("1ced7");
        unknown.capacity.unread = None;
        let mut big = row(6, 5, "ebb36");
        big.size = "slice".into();
        let mut human = row(7, 5, "a9136");
        human.needs = "needs_human".into();
        let n = next(vec![
            row(1, 3, "c477a"),
            row(2, 3, "b433b"),
            row(3, 3, "2240e"),
            row(4, 3, "7b81e"),
            row(5, 3, COORDINATOR),
            big,
            human,
            row(8, 3, "1ced7"),
        ]);
        let mut coord = seat(COORDINATOR);
        coord.role = "coordinator".into();
        let all = seats(vec![
            lost,
            busy,
            full,
            deck,
            small,
            unknown,
            coord,
            seat("a9136"),
        ]);
        let (w, skips) = plan(&n, &all, &[], &History::default(), NOW);
        assert!(w.is_empty(), "{w:?}");
        let why: Vec<&str> = skips.iter().map(|s| s.why.as_str()).collect();
        assert_eq!(
            why,
            vec![
                "c477a: alive no",
                "b433b: dispatchable no",
                "2240e: 11 unread",
                "7b81e: on deck in the merge queue",
                "519d0: not a dev seat",
                "ebb36: size slice does not fit review",
                "needs needs_human",
                "1ced7: unread unknown",
            ]
        );
    }

    #[test]
    fn a_seat_with_wrap_up_left_gets_only_the_wrap_up() {
        let mut s = seat("a9136");
        s.first = vec!["plan_node".into(), "handoff".into()];
        let n = next(vec![row(1, 3, "a9136"), row(2, 5, "a9136")]);
        let (w, skips) = plan(&n, &seats(vec![s]), &[], &History::default(), NOW);
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].first_line, "派工: 先做收尾 → a9136");
        assert_eq!(w[0].body, vec!["先做收尾:plan_node、handoff"]);
        assert!(w[0].keys.is_empty(), "the rows come back next round");
        assert_eq!(skips.len(), 2);
    }

    #[test]
    fn done_keys_and_the_thirty_minute_gap_hold_across_rounds() {
        let n = next(vec![row(1, 3, "a9136")]);
        let all = seats(vec![seat("a9136")]);
        let (w, _) = plan(&n, &all, &[], &History::default(), NOW);
        let journal = format!("{}\n", would_line(&w[0], NOW, Switches::default()));
        let hist = History::parse(&journal).expect("journal");
        // the same row again: already done
        let (w2, skips) = plan(&n, &all, &[], &hist, NOW + 60_000);
        assert!(w2.is_empty());
        assert!(
            skips[0].why.starts_with("already done (open_review:#701:"),
            "{skips:?}"
        );
        // a new row for the same seat 10 minutes later: the gap holds; 31 minutes: sent
        let n2 = next(vec![row(2, 3, "a9136")]);
        let (w3, skips) = plan(&n2, &all, &[], &hist, NOW + 10 * 60_000);
        assert!(w3.is_empty());
        assert_eq!(skips[0].why, "a9136: sent 10 min ago (one per 30 min)");
        let (w4, _) = plan(&n2, &all, &[], &hist, NOW + 31 * 60_000);
        assert_eq!(w4.len(), 1);
        // a new head is a new key
        let mut moved = row(1, 3, "a9136");
        moved.head = Some("2".repeat(40));
        let (w5, _) = plan(&next(vec![moved]), &all, &[], &hist, NOW + 31 * 60_000);
        assert_eq!(w5.len(), 1);
    }

    #[test]
    fn a_queued_dm_is_nudged_once_then_the_sender_is_told_once() {
        let q = vec![
            Waiting {
                id: "q_1".into(),
                to: "a9136".into(),
                from: "ebb36".into(),
                state: "queued".into(),
                queued_min: 6,
            },
            Waiting {
                id: "q_2".into(),
                to: "a9136".into(),
                from: "ebb36".into(),
                state: "queued".into(),
                queued_min: 4,
            },
            Waiting {
                id: "q_3".into(),
                to: "a9136".into(),
                from: "ebb36".into(),
                state: "delivered".into(),
                queued_min: 60,
            },
        ];
        let n = next(vec![]);
        let none = seats(vec![]);
        let (w, _) = plan(&n, &none, &q, &History::default(), NOW);
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].action, "nudge");
        assert!(!w[0].throttled, "a nudge is not a dispatch");
        let hist = History::parse(&format!(
            "{}\n",
            would_line(&w[0], NOW, Switches::default())
        ))
        .expect("h");
        let (w2, _) = plan(&n, &none, &q, &hist, NOW);
        assert_eq!(w2.len(), 1);
        assert_eq!(w2[0].action, "tell_sender");
        assert_eq!(w2[0].to, format!("ebb36,{COORDINATOR}"));
        let mut j = format!("{}\n", would_line(&w[0], NOW, Switches::default()));
        j.push_str(&format!(
            "{}\n",
            would_line(&w2[0], NOW, Switches::default())
        ));
        let (w3, _) = plan(&n, &none, &q, &History::parse(&j).expect("h"), NOW);
        assert!(w3.is_empty());
    }

    #[test]
    fn switches_default_off_and_a_broken_file_is_an_error() {
        assert_eq!(Switches::default(), Switches::parse("{}").expect("empty"));
        let s = Switches::parse(
            r#"{"paused":false,"nudge":true,"open_review":false,"dispatch":false}"#,
        )
        .expect("s");
        assert!(s.nudge && !s.dispatch && s.get("tell_sender"));
        assert!(Switches::parse(r#"{"nudge":"yes"}"#).is_err());
        assert!(Switches::parse(r#"{"merge":true}"#).is_err());
        assert!(Switches::parse("not json").is_err());
        assert!(History::parse("{\"kind\":\"would\"}\nnot json\n").is_err());
    }

    #[test]
    fn the_time_is_rfc3339_utc_milliseconds() {
        assert_eq!(rfc3339_ms(NOW), "2026-11-15T00:00:00.000Z");
        assert_eq!(rfc3339_ms(0), "1970-01-01T00:00:00.000Z");
        assert_eq!(rfc3339_ms(951_782_400_123), "2000-02-29T00:00:00.123Z");
    }

    #[test]
    fn the_lock_is_one_holder_until_it_expires() {
        let tmp = Scratch::new("lock");
        let a = take_lock(tmp.path(), NOW).expect("first");
        let e = take_lock(tmp.path(), NOW + 1000).expect_err("held");
        assert!(e.starts_with("act.lock held by pid"), "{e}");
        // past the TTL the lock is taken over
        let b = take_lock(tmp.path(), NOW + LOCK_TTL_MS + 1).expect("taken over");
        drop(a); // the old holder's drop must not remove the new holder's lock
        assert!(tmp.path().join("act.lock").exists());
        drop(b);
        assert!(!tmp.path().join("act.lock").exists());
        fs::write(tmp.path().join("act.lock"), "garbage").expect("w");
        assert!(take_lock(tmp.path(), NOW)
            .expect_err("broken")
            .starts_with("act.lock is broken"));
    }

    #[test]
    fn nulls_from_sig_and_next_read_as_not_known() {
        // sig 99cd24c9 to_json and next r4 #1 as they are written today: null = not read
        let s: SeatsOut = serde_json::from_str(
            r#"{"schema":"aib-drive.seats.v1","seats":[
  {"seat":"a9136","cabin":null,"role":"dev","alive":{"v":"yes","why":"x"},
   "hands":{"v":"free","free_since_min":12,"why":"x"},
   "capacity":{"usage_pct":null,"unread":2,"wip_unpushed":null,"open_prs":null,"on_deck":null},
   "waiting_on":{"v":"none","eta_min":null},"dispatchable":"yes","size":"mainline","first":[],
   "reasons":[],"data_at":null}]}"#,
        )
        .expect("sig output");
        assert_eq!(s.seats[0].capacity.on_deck, None);
        let n: NextOut = serde_json::from_str(
            r#"{"complete":true,"rows":[{"rank":1,"layer":5,"what":"S79 下一件","pr":null,
  "head":null,"node":"S79","to":"a9136","why_him":null,"why_now":"主軸","needs":"none",
  "size":"slice","data_at":null}]}"#,
        )
        .expect("next output");
        assert_eq!(n.rows[0].why_him, "");
        let (w, skips) = plan(&n, &s, &[], &History::default(), NOW);
        assert!(skips.is_empty(), "{skips:?}");
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].first_line, "派工: S79 下一件 → a9136");
        assert!(
            w[0].body.iter().any(|b| b.contains("on_deck null")),
            "{:?}",
            w[0].body
        );
    }

    #[test]
    fn a_pause_holds_back_only_new_work() {
        let all_on = Switches {
            paused: false,
            nudge: true,
            open_review: true,
            dispatch: true,
        };
        let paused = Switches {
            paused: true,
            ..all_on
        };
        for a in ["dispatch", "open_review", "wrap_up", "nudge", "tell_sender"] {
            assert!(would_send(all_on, a), "{a} goes out when nothing is paused");
            assert!(
                !would_send(Switches::default(), a),
                "{a}: every switch starts off"
            );
        }
        assert!(!would_send(paused, "dispatch"));
        assert!(!would_send(paused, "open_review"));
        for a in ["wrap_up", "nudge", "tell_sender"] {
            assert!(would_send(paused, a), "a pause does not hold back {a}");
        }
        let n = next(vec![row(1, 3, "a9136")]);
        let (w, _) = plan(
            &n,
            &seats(vec![seat("a9136")]),
            &[],
            &History::default(),
            NOW,
        );
        let line: Value = serde_json::from_str(&would_line(&w[0], NOW, paused)).expect("json");
        assert_eq!(line["would_send"], json!(false));
        assert_eq!(line["paused"], json!(true));
    }

    #[test]
    fn act_runs_no_outside_program() {
        // act only reads files and writes its own state: no merge, push, delete of branches or
        // trees, rebase, CI rerun / cancel or fleet-wide notice can come from here
        let src = include_str!("act.rs");
        for bad in [
            concat!("Comm", "and::"),
            concat!("process::", "Command"),
            concat!("process::", "exit"),
        ] {
            assert!(!src.contains(bad), "act.rs must not contain {bad}");
        }
    }
}
