//! `aib-drive act` / `aib-drive switch` (S79 n-3, IFACE-s79-drive-519d0-1115 §3).
//!
//! act turns the `next` list into at most three kinds of action, each behind its own switch:
//! `nudge` (a dm still queued after 5 minutes), `open_review` (a layer-3 row: a review
//! dispatch dm until aib-review's `open` exists) and `dispatch` (a layer-5 / 6 row). It never
//! merges, pushes, deletes, rebases, reruns or cancels CI, or notifies the whole fleet.
//!
//! `act --once --dry-run` reads the next and seats JSON (local structs written from IFACE
//! §1 / §2), works out this round's actions and writes `would` lines to
//! `<root>/drive/act-dryrun.jsonl`. It sends nothing. The comparison period is exactly this
//! file against the coordinator's real `派工:` first lines.
//!
//! `act --once --send` (E5 / E6 / E7 / E9 / E10) does the same, then sends the nudges,
//! telling the sender, wrap-ups, review dispatches, dispatches and fix requests (notify_fix)
//! whose switch is on, each through the intents ledger (`intents::carry_out`: intent →
//! recheck → sending → `aib dm` → done / abandoned; a run killed while sending is not sent
//! again).
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
//!   ⇒ not sent; the row's size must fit the seat's size; only `dispatchable: yes` dev seats,
//!   or a seat in its turn (`no`) on a row next marks [`BUSY`] (S79-busy: sent all the same,
//!   its size not judged).
//! - wrap up first: a seat whose `first` list is not empty gets only 「先做收尾:…」.
//! - nudge: once per dm; after that the sender and the coordinator are told instead, once.
//!
//! Exit codes (IFACE §0): 0 done · 2 usage · 3 a source could not be read · 4 paused · 5 the
//! lock was not taken, or a state file is broken.

use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

pub const LOCK_TTL_MS: i64 = 5 * 60 * 1000;
pub const NUDGE_AFTER_MIN: i64 = 5;
/// A nudge dm's first words: a queued item that starts with them is a nudge itself and is not
/// nudged again (f1fae 11-20: nudges of nudges piled up five deep).
pub const NUDGE_HEAD: &str = "提醒: 你有一封還沒收的信";
pub const SEAT_GAP_MIN: i64 = 30;
pub const UNREAD_MAX: u64 = 10;
/// next's `needs` for a row whose seat is in its turn (dispatchable `no`, alive). Such a row
/// is sent all the same: the dm waits in the seat's queue until the turn ends (S79-busy,
/// Andy 11-16 07:0x「同意,在忙也發,30 分鐘最多一封」); [`SEAT_GAP_MIN`], unread and alive
/// still hold it back.
pub const BUSY: &str = "seat_busy";
/// The coordinator seat: told about undelivered dms, never handed development work.
pub const COORDINATOR: &str = "519d0";
/// The merge queue seat: a red that is infrastructure (not the author's) goes here to be rerun.
pub const MERGE_QUEUE: &str = "f1fae";

pub const RC_OK: u8 = 0;
pub const RC_USAGE: u8 = 2;
pub const RC_SOURCE: u8 = 3;
pub const RC_PAUSED: u8 = 4;
pub const RC_LOCK: u8 = 5;

// ---- inputs (local structs; the integration takes next.rs / seats.rs types) ----

/// A field that is null reads as its default (IFACE r4 #4: sig and next write null when a
/// source could not be read; `#[serde(default)]` alone only covers a missing field).
pub(crate) fn nd<'de, D, T>(d: D) -> Result<T, D::Error>
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
    /// The branch, when next knows it: the key of a row with no PR and no node.
    #[serde(default, deserialize_with = "nd")]
    pub branch: Option<String>,
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
    /// next's row kind (`fix`, `receipt`, `review`, …): layer 2 is acted on only for `fix`.
    #[serde(default, deserialize_with = "nd")]
    pub kind: String,
    /// Why a `fix` row is red when it is not the author's to fix: `infra` (the job died in
    /// Set up job / Set up runner, IFACE r11 4) goes to the merge queue, not the author.
    #[serde(default, deserialize_with = "nd")]
    pub cause: Option<String>,
    /// From a scan that was not complete, not named by its errors (next's `partial`, 519d0
    /// 11-15 20:28): with next not complete only an open_review / notify_fix of it goes out,
    /// after a live recheck of the PR ([`PARTIAL_ACTIONS`]).
    #[serde(default, deserialize_with = "nd")]
    pub partial: bool,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct SeatsOut {
    #[serde(default, deserialize_with = "nd")]
    pub seats: Vec<Seat>,
    /// The roster, alive and hands were all read (seats.rs `complete`). Only then is a seat
    /// with no cabin in the facts really not there ([`after_send`]); missing = false.
    #[serde(default)]
    pub complete: bool,
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
    /// The cabin the seat sits in (a dm goes to `<cabin>/<seat>`).
    #[serde(default, deserialize_with = "nd")]
    pub cabin: String,
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
    /// The queue item's `sender` text when it names no seat (S79-sender: 「自動推進」, the
    /// 1.x sender check's plain name for act's own shell), shown instead of 不明.
    #[serde(default, deserialize_with = "nd")]
    pub from_name: String,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Switches {
    pub paused: bool,
    pub nudge: bool,
    pub open_review: bool,
    pub dispatch: bool,
    /// IFACE r10 1: tell the author of a layer-2 fix row (default off)
    pub notify_fix: bool,
    /// S79-unblock u-5: 「卡點:」 dms from unblock.json (default off; dry until it is in
    /// [`SENDABLE`]).
    pub unblock_notify: bool,
}

impl Switches {
    pub const NAMES: [&'static str; 6] = [
        "paused",
        "nudge",
        "open_review",
        "dispatch",
        "notify_fix",
        "unblock_notify",
    ];

    /// The actions each switch lets out (U18: a switch none of whose actions this version
    /// sends only ever journals would lines). `paused` lets nothing out.
    pub fn actions(name: &str) -> &'static [&'static str] {
        match name {
            "nudge" => &["nudge", "tell_sender"],
            "open_review" => &["open_review"],
            "dispatch" => &["dispatch", "wrap_up"],
            "notify_fix" => &["notify_fix"],
            "unblock_notify" => &["unblock_notify"],
            _ => &[],
        }
    }

    /// U18 (DECISION-council-unblock §2): whether turning this switch on makes this version
    /// send anything. `paused` holds back, it never sends: always true.
    pub fn sends(name: &str) -> bool {
        name == "paused" || Self::actions(name).iter().any(|a| SENDABLE.contains(a))
    }

    /// The switches that are on although this version sends none of their actions (U18).
    pub fn on_but_dry(self) -> Vec<&'static str> {
        Self::NAMES
            .iter()
            .copied()
            .filter(|n| self.get(n) && !Self::sends(n))
            .collect()
    }

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
                "notify_fix" => s.notify_fix = b,
                "unblock_notify" => s.unblock_notify = b,
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
            "notify_fix" => self.notify_fix,
            "unblock_notify" => self.unblock_notify,
            _ => false,
        }
    }

    pub fn set(&mut self, name: &str, on: bool) -> bool {
        match name {
            "paused" => self.paused = on,
            "nudge" => self.nudge = on,
            "open_review" => self.open_review = on,
            "dispatch" => self.dispatch = on,
            "notify_fix" => self.notify_fix = on,
            "unblock_notify" => self.unblock_notify = on,
            _ => return false,
        }
        true
    }

    pub fn to_json(self) -> Value {
        json!({"paused": self.paused, "nudge": self.nudge,
               "open_review": self.open_review, "dispatch": self.dispatch,
               "notify_fix": self.notify_fix,
               "unblock_notify": self.unblock_notify})
    }
}

/// What earlier rounds did (from the journal): keys already handled and when each seat was
/// last sent a dm.
#[derive(Debug, Clone, Default)]
pub struct History {
    pub keys: HashSet<String>,
    pub last_sent_ms: BTreeMap<String, i64>,
    /// Keys whose send did not go out [`RETRY_MAX`] times: no longer tried (shown each
    /// round for a person to look at).
    pub gave_up: BTreeSet<String>,
    /// Keys whose send did not go out and that wait out [`RETRY_AFTER_MS`]: not planned, and
    /// nothing that follows them (telling the sender after a nudge) is planned either
    /// (5d33a d3ad28dc r2 2).
    pub backing_off: BTreeSet<String>,
    /// Keys already marked as not taken up (E10 `mark` lines): marked once.
    pub marked: BTreeSet<String>,
    /// Work rows by their key without the seat (action:PR or node:head) → the seat it went
    /// to and when (E10): kept with that seat for [`crate::track::FOLLOW_FOR_MS`], not
    /// handed to another.
    pub went_to: BTreeMap<String, (String, i64)>,
    /// With `--send`: the nudge / telling-the-sender keys the intents ledger has `done`
    /// ([`History::with_ledger`]); `None` (a dry run) reads them from the journal's `would`
    /// lines as before.
    pub sent: Option<BTreeSet<String>>,
    /// Nudge keys whose seat is not there ([`seat_gone`], `gone` lines): never nudged again;
    /// the sender is told that the seat is not there.
    pub gone: BTreeSet<String>,
    /// Keys of `would` lines whose round did not send them (switch off, paused, or a dry
    /// run), with their action: a later round that would not send that action either keeps
    /// them planned once ([`History::hold_unsent`]); a round that sends it does not.
    pub unsent: BTreeMap<String, String>,
}

/// A work key without its seat (`action:PR or node:head`), and its seat.
pub fn split_seat(key: &str) -> Option<(&str, &str)> {
    key.rsplit_once(':')
}

/// After a send that did not go out, its key is planned again only this long after
/// (the per-seat throttle; 7b81e d3ad28dc L1).
pub const RETRY_AFTER_MS: i64 = 30 * 60 * 1000;
/// A key whose send did not go out this many times is given up.
pub const RETRY_MAX: u32 = 3;

impl History {
    /// 519d0 11-15 (87167 on the live journal): with `--send`, whether a dm was nudged or its
    /// sender told is read from the intents ledger only: a key counts when its line is
    /// `done` (sent: delivered or queued). A `would` line of a round whose switch was off,
    /// or of a dry run, never counts, so the first real send is the nudge, not telling the
    /// sender it was nudged. A `done` intent that carried several dms (one dm for one seat,
    /// E8) counts for every key on its `keys` field; a key sent to several seats
    /// (`key@seat`) counts as its key.
    pub fn with_ledger(mut self, ledger: &str) -> History {
        let (states, _) = crate::intents::load(ledger);
        let mut carried: BTreeMap<String, Vec<String>> = BTreeMap::new();
        for v in ledger
            .lines()
            .filter_map(|l| serde_json::from_str::<Value>(l).ok())
            .filter(|v| v.get("kind").and_then(Value::as_str) == Some("intent"))
        {
            if let (Some(k), Some(ks)) = (
                v.get("key").and_then(Value::as_str),
                v.get("keys").and_then(Value::as_array),
            ) {
                let ks = ks.iter().filter_map(Value::as_str).map(str::to_owned);
                carried.insert(k.to_owned(), ks.collect());
            }
        }
        let mut sent = BTreeSet::new();
        for (k, st) in &states {
            if *st == crate::intents::State::Done {
                sent.insert(k.split('@').next().unwrap_or(k).to_owned());
                sent.extend(carried.get(k).into_iter().flatten().cloned());
            }
        }
        self.sent = Some(sent);
        self
    }

    /// A round that will not send `action` (`sends(action)` false: a dry run, or its switch
    /// off / paused) plans an unsent key once, as before, so a dry run lists each dm once;
    /// a round that sends it plans it again (519d0 11-15 23:55).
    pub fn hold_unsent(mut self, sends: impl Fn(&str) -> bool) -> History {
        for (k, a) in std::mem::take(&mut self.unsent) {
            if !sends(&a) {
                self.keys.insert(k);
            }
        }
        self
    }

    /// Whether a nudge / telling-the-sender key was handled (see [`History::with_ledger`]).
    pub fn told(&self, k: &str) -> bool {
        match &self.sent {
            Some(sent) => sent.contains(k),
            None => self.keys.contains(k),
        }
    }

    /// Read the `would` lines of a journal. A line that is not a JSON object is an error
    /// (the state file is broken: rc 5), never skipped.
    pub fn parse(text: &str) -> Result<History, String> {
        Self::parse_at(text, i64::MAX)
    }

    /// [`History::parse`] at `now_ms`: a `retry` line forgets its key only once
    /// [`RETRY_AFTER_MS`] have passed (a line with `backoff: false`, written when the round
    /// did not send at all, at once and without counting); after [`RETRY_MAX`] counted
    /// retries the key stays handled and is listed in `gave_up`.
    pub fn parse_at(text: &str, now_ms: i64) -> Result<History, String> {
        let mut h = History::default();
        let mut tries: BTreeMap<String, u32> = BTreeMap::new();
        for (n, line) in text.lines().enumerate() {
            if line.trim().is_empty() {
                continue;
            }
            let v: Value =
                serde_json::from_str(line).map_err(|e| format!("journal line {}: {e}", n + 1))?;
            let o = v
                .as_object()
                .ok_or_else(|| format!("journal line {}: not an object", n + 1))?;
            match o.get("kind").and_then(Value::as_str) {
                // a would whose round did not send it (switch off, paused, or a dry run) is not
                // a send: its keys, its seat and its 30-minute gap stay free (519d0 11-15 23:55:
                // a dry run's would held notify_fix / open_review for good once switched on)
                Some("would") => {
                    // a line written before `send_round` existed counts as sent (never sent twice)
                    let flag = |f: &str| o.get(f).and_then(Value::as_bool).unwrap_or(true);
                    let sent_line = flag("would_send") && flag("send_round");
                    if !sent_line {
                        let a = o.get("action").and_then(Value::as_str).unwrap_or("");
                        for k in o
                            .get("keys")
                            .and_then(Value::as_array)
                            .into_iter()
                            .flatten()
                            .filter_map(Value::as_str)
                        {
                            h.unsent.insert(k.to_owned(), a.to_owned());
                        }
                        continue;
                    }
                }
                // a send that did not go out (7b81e E4 M1): its key is planned again
                Some("retry") => {
                    let Some(k) = o.get("key").and_then(Value::as_str) else {
                        continue;
                    };
                    if o.get("backoff").and_then(Value::as_bool) == Some(false) {
                        h.keys.remove(k);
                        h.backing_off.remove(k);
                        continue;
                    }
                    // a work dm that did not go out did not reach its seat
                    if let Some((base, seat)) = split_seat(k) {
                        if h.went_to.get(base).is_some_and(|(s, _)| s == seat) {
                            h.went_to.remove(base);
                        }
                    }
                    let n = tries.entry(k.to_owned()).or_insert(0);
                    *n += 1;
                    let at = o.get("at_ms").and_then(Value::as_i64).unwrap_or(i64::MAX);
                    h.backing_off.remove(k);
                    if *n >= RETRY_MAX {
                        h.gave_up.insert(k.to_owned());
                    } else if now_ms.saturating_sub(at) >= RETRY_AFTER_MS {
                        h.keys.remove(k);
                    } else {
                        h.backing_off.insert(k.to_owned());
                    }
                    continue;
                }
                // a nudge whose seat is not there: handled for good, the sender is told
                Some("gone") => {
                    if let Some(k) = o.get("key").and_then(Value::as_str) {
                        h.gone.insert(k.to_owned());
                        h.backing_off.remove(k);
                    }
                    continue;
                }
                Some("mark") => {
                    if let Some(k) = o.get("key").and_then(Value::as_str) {
                        h.marked.insert(k.to_owned());
                    }
                    continue;
                }
                _ => continue,
            }
            let work = o
                .get("action")
                .and_then(Value::as_str)
                .is_some_and(|a| WORK.contains(&a));
            let at_ms = o.get("at_ms").and_then(Value::as_i64).unwrap_or(0);
            for k in o
                .get("keys")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .filter(|_| work)
            {
                if let Some((base, seat)) = split_seat(k) {
                    h.went_to.insert(base.to_owned(), (seat.to_owned(), at_ms));
                }
            }
            for k in o
                .get("keys")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                if let Some(k) = k.as_str() {
                    h.keys.insert(k.to_owned());
                    h.backing_off.remove(k);
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
#[derive(Debug, Clone, Default, PartialEq, Eq)]
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
    /// The intents key a real send goes under (E4): the row key, the nudge key, or for a
    /// wrap-up the day + seat + its list (the same list once a day).
    pub send_key: String,
    /// For a nudge / telling the sender: the dm (id, its recipient) the recheck looks up.
    pub dm: Option<(String, String)>,
    /// Every row in it is `partial` (519d0 11-15 20:28): the (PR, head) of each, which the
    /// recheck reads live before it goes out (open and still at that head); `None` = not all
    /// partial, held while next is not complete.
    pub pr_checks: Option<Vec<(u64, String)>>,
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
    // a branch row keeps its key when its rank moves (519d0 11-16 S79-anc 1); the rank only
    // when next names neither (a branch name has no ':', git refuses it)
    let branch = row
        .branch
        .as_deref()
        .filter(|b| !b.is_empty() && !b.contains(':'));
    let what = match (row.pr, row.node.as_deref(), branch) {
        (Some(pr), _, _) => format!("#{pr}"),
        (None, Some(n), _) => n.to_owned(),
        (None, None, Some(b)) => format!("branch={b}"),
        (None, None, None) => format!("rank{}", row.rank),
    };
    let head = row.head.as_deref().filter(|h| hex40(h)).unwrap_or("-");
    format!("{action}:{what}:{head}:{seat}")
}

fn action_of(row: &Row) -> Option<&'static str> {
    // IFACE r10 1: a layer-2 fix row (conflict, a real red, a medium review not fixed)
    if row.layer == 2 && row.kind == "fix" {
        return Some("notify_fix");
    }
    match row.layer {
        3 => Some("open_review"),
        5 | 6 => Some("dispatch"),
        _ => None,
    }
}

/// The seat a branch belongs to: its last `-` / `/` part that is a 5-hex seat code
/// (`feat/drive-explain-a9136-1117` ⇒ `a9136`). None when no part is one.
pub fn branch_seat(branch: &str) -> Option<&str> {
    branch
        .split(['-', '/'])
        .filter(|p| p.len() == 5 && p.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')))
        .rfind(|p| p.bytes().any(|b| b.is_ascii_alphabetic()))
}

/// Who wrote a row's work: the seat in its branch name; a fix row without one goes to its
/// author, so its `to`. An open_review row's `to` is the reviewer, never the author.
pub fn row_author(r: &Row) -> Option<String> {
    if let Some(s) = r.branch.as_deref().and_then(branch_seat) {
        return Some(s.to_owned());
    }
    (r.layer == 2 && r.kind == "fix")
        .then(|| r.to.clone())
        .flatten()
}

/// Rows act does not plan at all (519d0 11-17 03:0x, from 5d33a's trial count, rows 54 / 6):
/// a notify_fix / open_review row with no 40-hex head, or any work row about a PR with none
/// (the dm could name no head: X7); and a review row whose head already has a `REVIEW-*.md`
/// bound to it in `pending` written by a seat that is not the row's author (X5; an author's
/// own review is no review, 5d33a r1 M1). The rest of next, as it was, and why each dropped
/// row was dropped.
pub fn screen_rows(next: &NextOut, pending: Option<&Path>) -> (NextOut, Vec<Skip>) {
    screen_rows_at(next, pending, &|_| None)
}

/// What a branch points at now, for [`screen_rows_at`]: `None` = cannot tell (no clone),
/// `Some(None)` = no such branch, `Some(Some(head))` = its head.
pub type BranchHead<'a> = &'a dyn Fn(&str) -> Option<Option<String>>;

/// [`screen_rows`], and also (5d33a CHECK-drive-nohist M1): a notify_fix / open_review row
/// with a branch and no PR whose branch has moved off the row's head, or is gone, is not
/// planned: its facts (a receipt, a review) were about an older head. A branch that cannot
/// be looked up keeps the row (the other rules still hold).
pub fn screen_rows_at(
    next: &NextOut,
    pending: Option<&Path>,
    branch_head: BranchHead<'_>,
) -> (NextOut, Vec<Skip>) {
    let mut skips = Vec::new();
    let mut rows = Vec::new();
    for r in &next.rows {
        let action = action_of(r);
        let head = r
            .head
            .as_deref()
            .filter(|h| h.len() == 40 && h.bytes().all(|b| b.is_ascii_hexdigit()));
        let needs_head = matches!(action, Some("notify_fix" | "open_review"))
            || (action.is_some() && r.pr.is_some());
        if needs_head && head.is_none() {
            let what =
                r.pr.map_or_else(|| format!("rank {}", r.rank), |n| format!("#{n}"));
            skips.push(Skip {
                rank: r.rank,
                why: format!("{what}: no 40-hex head on the row (a work dm names its head)"),
            });
            continue;
        }
        if let (Some("notify_fix" | "open_review"), None, Some(b), Some(h)) =
            (action, r.pr, r.branch.as_deref(), head)
        {
            let moved = match branch_head(b) {
                Some(Some(now)) if !now.eq_ignore_ascii_case(h) => Some(format!(
                    "branch {b} moved off the row's head {} to {}: the facts are older than the branch",
                    h.get(..8).unwrap_or(h),
                    now.get(..8).unwrap_or(&now)
                )),
                Some(None) => Some(format!("branch {b} is gone")),
                _ => None,
            };
            if let Some(why) = moved {
                skips.push(Skip { rank: r.rank, why });
                continue;
            }
        }
        if let (Some("open_review"), Some(h), Some(p)) = (action, head, pending) {
            let author = row_author(r);
            let have = reviews_at_head(p, h);
            let by_other = have.iter().find(|f| {
                review_seat(f).is_some_and(|s| author.as_deref().is_some_and(|a| a != s))
            });
            if let Some(f) = by_other {
                skips.push(Skip {
                    rank: r.rank,
                    why: format!("review already at {}: {f}", h.get(..8).unwrap_or(h)),
                });
                continue;
            }
        }
        rows.push(r.clone());
    }
    (
        NextOut {
            complete: next.complete,
            rows,
        },
        skips,
    )
}

/// A red the author cannot fix (IFACE r11 4): it goes to the merge queue to be rerun.
fn is_infra(row: &Row) -> bool {
    row.cause.as_deref() == Some("infra")
}

fn row_line(action: &str, row: &Row, seat: &str) -> String {
    // next writes a fix row as 「<分支或 PR> 修:<原因>」
    let fix = || {
        row.what
            .split_once(" 修:")
            .unwrap_or((row.what.as_str(), "?"))
    };
    match action {
        "notify_fix" if is_infra(row) => {
            let (target, why) = fix();
            format!("派工: 重跑 {target}:{why} → {seat}")
        }
        "notify_fix" => {
            let (target, why) = fix();
            format!("派工: 修 {target}:{why} → {seat}")
        }
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
    // E7 (IFACE r4 #5): the evidence is the next row's PR, head and data time, copied
    let mut ev = Vec::new();
    if let Some(pr) = row.pr {
        ev.push(format!("PR #{pr}"));
    }
    if let Some(h) = &row.head {
        ev.push(format!("頭 {h}"));
    }
    if !row.data_at.is_empty() {
        ev.push(format!("資料時間 {}", row.data_at));
    }
    if !ev.is_empty() {
        b.push(format!("證據:{}", ev.join(" · ")));
    }
    if !row.why_him.is_empty() {
        b.push(format!("為什麼是你:{}", row.why_him));
    }
    if !row.why_now.is_empty() {
        b.push(format!("為什麼是現在:{}", row.why_now));
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
    plan_sw(next, seats, waiting, hist, now_ms, true)
}

/// [`plan`], told whether a wrap-up would go out (`wrap_out`: [`goes_out`] of `wrap_up` were
/// the round live). A wrap-up that cannot go out (its switch, dispatch, off) is not planned:
/// it no longer takes the seat's one dm and holds back its fix request (519d0 11-16 00:32:
/// 043dd / 1ced7 fix rows skipped 「wrap up first」 with dispatch off, notify_fix on).
pub fn plan_sw(
    next: &NextOut,
    seats: &SeatsOut,
    waiting: &[Waiting],
    hist: &History,
    now_ms: i64,
    wrap_out: bool,
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
        let Some(action) = action_of(row) else {
            skip(format!("layer {} is not an act action", row.layer));
            continue;
        };
        // an infrastructure red goes to the merge queue to be rerun, not to the author
        let infra = action == "notify_fix" && is_infra(row);
        let busy = row.needs == BUSY;
        if row.needs != "none" && !busy {
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
        let author = row.to.as_deref().filter(|t| !t.is_empty());
        let Some(to) = (if infra { Some(MERGE_QUEUE) } else { author }) else {
            skip("no seat (to)".into());
            continue;
        };
        let Some(seat) = seats.seats.iter().find(|s| s.seat == to) else {
            skip(format!("{to}: no seat facts"));
            continue;
        };
        if !infra && (seat.role != "dev" || to == COORDINATOR) {
            skip(format!("{to}: not a dev seat"));
            continue;
        }
        if seat.alive.v != "yes" {
            skip(format!("{to}: alive {}", seat.alive.v));
            continue;
        }
        // a seat in its turn is `no` with next's needs seat_busy; both must say so (a `no`
        // next did not see, or `unknown`, still holds the row back)
        let in_turn = busy && seat.dispatchable == "no";
        if seat.dispatchable != "yes" && !in_turn {
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
            Some(u) if u < UNREAD_MAX => {}
            Some(u) => {
                skip(format!("{to}: {u} unread"));
                continue;
            }
            None => {
                skip(format!("{to}: unread unknown"));
                continue;
            }
        }
        // a seat in its turn has size none (no free capacity now); its row is queued for after
        // the turn, so the size is not judged on this round's facts
        match (size_rank(&row.size), size_rank(&seat.size)) {
            _ if in_turn => {}
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
        // E10: work sent to a seat stays with it (not taken up = marked, never handed on)
        if let Some((other, at)) = split_seat(&k).and_then(|(base, _)| hist.went_to.get(base)) {
            if other != to && now_ms.saturating_sub(*at) < crate::track::FOLLOW_FOR_MS {
                skip(format!(
                    "{to}: went to {other} {} min ago (stays with it)",
                    now_ms.saturating_sub(*at) / 60_000
                ));
                continue;
            }
        }
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
                // one row not partial (or not checkable) ⇒ the dm is not all partial
                w.pr_checks = match (w.pr_checks.take(), partial_check(row)) {
                    (Some(mut cs), Some(c)) => {
                        cs.push(c);
                        Some(cs)
                    }
                    _ => None,
                };
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
        if !seat.first.is_empty() && wrap_out {
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
                send_key: format!(
                    "wrap_up:{}:-:{to}:{}",
                    rfc3339_ms(now_ms).get(..10).unwrap_or("-"),
                    seat.first.join("|")
                ),
                dm: None,
                pr_checks: None,
            });
            skip(format!("{to}: wrap up first"));
            continue;
        }
        let mut body = row_body(row);
        if let Some(n) = &deck_note {
            body.push(n.clone());
        }
        if infra {
            body.push(format!(
                "作者:{}(基礎設施失敗,不是作者要修的;請重跑)",
                author.unwrap_or("?")
            ));
        }
        this_round.insert(to.to_owned(), out.len());
        out.push(Would {
            action: action.into(),
            to: to.to_owned(),
            first_line: row_line(action, row, to),
            body,
            send_key: k.clone(),
            keys: vec![k],
            throttled: true,
            ranks: vec![row.rank],
            dm: None,
            pr_checks: partial_check(row).map(|c| vec![c]),
        });
    }
    let first_dm = out.len();
    for w in waiting {
        // the queue file's status (dm-ctl's delivery stage name was sort-queued)
        if w.state != "queued" || w.queued_min < NUDGE_AFTER_MIN {
            continue;
        }
        let nk = format!("nudge:{}:-:{}", w.id, w.to);
        let tk = format!("tell_sender:{}:-:{}", w.id, w.from);
        // a queue item with no sender_seat (5d33a 624d4c96 L1: 18 of 28 on this Mac): the
        // sender is not named and only the coordinator is told
        let from = if !w.from.is_empty() {
            w.from.as_str()
        } else if !w.from_name.is_empty() {
            w.from_name.as_str()
        } else {
            "不明"
        };
        let gone = hist.gone.contains(&nk);
        if !gone
            && !hist.told(&nk)
            && !hist.backing_off.contains(&nk)
            && !hist.gave_up.contains(&nk)
        {
            out.push(Would {
                action: "nudge".into(),
                to: w.to.clone(),
                first_line: format!("{NUDGE_HEAD} {} → {}", w.id, w.to),
                body: vec![format!("寄件人 {from},已排隊 {} 分", w.queued_min)],
                send_key: nk.clone(),
                keys: vec![nk],
                throttled: false,
                ranks: Vec::new(),
                dm: Some((w.id.clone(), w.to.clone())),
                pr_checks: None,
            });
        } else if hist.backing_off.contains(&nk) {
            // the nudge did not go out and waits to be tried again: the sender is not told
            // it was nudged (5d33a d3ad28dc r2 2)
            continue;
        } else if !hist.told(&tk) {
            let mut to: Vec<String> = Some(w.from.clone())
                .filter(|f| !f.is_empty())
                .into_iter()
                .collect();
            if w.from != COORDINATOR {
                to.push(COORDINATOR.to_owned());
            }
            out.push(Would {
                action: "tell_sender".into(),
                to: to.join(","),
                first_line: if gone {
                    format!(
                        "未送達: {} 給 {} 的信已排隊 {} 分,收件席不在(找不到艙),沒有提醒",
                        w.id, w.to, w.queued_min
                    )
                } else {
                    format!(
                        "未送達: {} 給 {} 的信已排隊 {} 分,提醒過一次",
                        w.id, w.to, w.queued_min
                    )
                },
                body: vec![format!("寄件人 {from};要不要換人請統籌席決定")],
                send_key: tk.clone(),
                keys: vec![tk],
                throttled: false,
                ranks: Vec::new(),
                dm: Some((w.id.clone(), w.to.clone())),
                pr_checks: None,
            });
        }
    }
    let dms = merge_dms(out.split_off(first_dm));
    out.extend(dms);
    (out, skips)
}

/// E8「同批併一封」for the dm rounds (519d0 11-15: 7b81e and b433b each got two nudges in
/// the first real round): nudges, or tellings, to the same seat(s) in one round go out as
/// one dm that lists every dm; its keys are all of theirs (the intent line carries them,
/// [`History::with_ledger`]). One alone is left as it was.
fn merge_dms(dms: Vec<Would>) -> Vec<Would> {
    let mut groups: Vec<Vec<Would>> = Vec::new();
    for w in dms {
        match groups
            .iter_mut()
            .find(|g| g[0].action == w.action && g[0].to == w.to)
        {
            Some(g) => g.push(w),
            None => groups.push(vec![w]),
        }
    }
    groups
        .into_iter()
        .filter_map(|mut g| {
            if g.len() < 2 {
                return g.pop();
            }
            let mut one = g[0].clone();
            one.first_line = if one.action == "nudge" {
                format!("提醒: 你有 {} 封還沒收的信 → {}", g.len(), one.to)
            } else {
                format!("未送達: {} 封信還在排隊", g.len())
            };
            one.body = g
                .iter()
                .map(|w| format!("{}({})", w.first_line, w.body.join(";")))
                .collect();
            one.keys = g.iter().flat_map(|w| w.keys.clone()).collect();
            Some(one)
        })
        .collect()
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
/// work (dispatch / open_review / notify_fix); wrap-ups, nudges and telling the sender still
/// go out.
pub fn would_send(sw: Switches, action: &str) -> bool {
    // a pause also holds back 「卡點:」 dms (unblock_notify): a pause often means drive itself
    // is wrong, and the stuck-point finding may be the wrong part
    let new_work = matches!(
        action,
        "dispatch" | "open_review" | "notify_fix" | "unblock_notify"
    );
    sw.get(action) && !(sw.paused && new_work)
}

/// Whether this round really sends `action`: it runs with `--send` (`sending`), this version
/// sends that action ([`SENDABLE`]), and its switch lets it out ([`would_send`]). The same
/// test as [`send_round`]'s (5d33a 7919bb14 N2).
pub fn goes_out(sw: Switches, action: &str, sending: bool) -> bool {
    sending && SENDABLE.contains(&action) && would_send(sw, action)
}

/// What this version really sends with `--send` (E5 nudge and telling the sender, E9 the
/// wrap-up), review dispatch (E6 open_review) and dispatch (E7), each only with its switch
/// on (all off by default; IFACE r11: not turned on before the comparison table passes),
/// and the fix request (IFACE r10 1: notify_fix, to the author or, for an infrastructure
/// red, to the merge queue) behind its own `notify_fix` switch.
/// Every [`WORK`] action is sendable.
pub const SENDABLE: [&str; 6] = ["nudge", "tell_sender", "wrap_up", WORK[0], WORK[1], WORK[2]];
/// The row actions that hand a seat work (E10: a row sent to one seat stays with it).
pub const WORK: [&str; 3] = ["notify_fix", "open_review", "dispatch"];

/// One dm of a sending round and what became of it (E10: the delivery stage is kept apart
/// from read / taken, which act does not decide).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Sent {
    /// The intents key (`<send key>@<seat>` when the dm goes to several seats).
    pub key: String,
    /// The would's own key, the one the journal dedupes on.
    pub base_key: String,
    pub to: String,
    pub outcome: Result<crate::intents::Outcome, String>,
    /// What `aib dm` answered when it was sent: the stage and the q_ id (S107-product slice 3,
    /// the drive_send event); `None` when nothing was sent.
    pub stage: Option<String>,
    pub dm_id: Option<String>,
}

/// The recheck before a send: the seat has a cabin to send to, and a nudge's dm is still
/// queued for its recipient (a wrap-up rests on this round's seat facts).
/// Why a dm could not be addressed: the seat has no cabin in the seat facts.
pub const NO_CABIN: &str = "no cabin in the seat facts";

/// 519d0 11-15 (87167 ACCEPT-LIVE N2, a dm to the closed seat c477a): the recipient seat is
/// not there — `aib dm` itself could not resolve the address. Not a busy moment: a nudge to
/// it is final (no backing off) and the sender is told once. No cabin in the seat facts is
/// gone only when the roster was read whole ([`after_send`], 87167 18:0x): a round whose seat
/// facts are incomplete would mark a live seat gone and tell its sender a wrong 「收件席不在」
/// (5d33a 0a39f00a L1), so there it is retried as before.
pub fn seat_gone(why: &str) -> bool {
    why.starts_with("aib dm stage resolve,")
}

/// The journal line for a nudge whose seat is not there ([`seat_gone`]): never tried again,
/// the next round tells the sender.
pub fn gone_line(now_ms: i64, key: &str, why: &str) -> String {
    json!({"v": 1, "kind": "gone", "at": rfc3339_ms(now_ms), "at_ms": now_ms, "key": key,
           "why": why})
    .to_string()
}

fn recheck(
    w: &Would,
    addr: Option<&str>,
    lookup: &dyn crate::source::DmLookup,
) -> Result<(), String> {
    if addr.is_none() {
        return Err(NO_CABIN.into());
    }
    // rows from a partial scan: the PR read live, still open and at the row's head (519d0
    // 11-15 20:28); a PR that cannot be read is not sent (tried again later)
    for (n, head) in w.pr_checks.iter().flatten() {
        let v = lookup
            .pr(*n)
            .map_err(|e| format!("{PR_UNREAD} #{n}: {e}"))?;
        let open = v["state"].as_str() == Some("open");
        let at = v["head"]["sha"].as_str() == Some(head.as_str());
        if !(open && at) {
            return Err(format!(
                "#{n} is no longer open at {} (partial row)",
                head.get(..8).unwrap_or(head)
            ));
        }
    }
    let Some((id, rcpt)) = &w.dm else {
        return Ok(());
    };
    let v = lookup.all(rcpt)?;
    let queued = v.as_array().is_some_and(|a| {
        a.iter().any(|d| {
            d["id"].as_str() == Some(id.as_str()) && d["status"].as_str() == Some("queued")
        })
    });
    if queued {
        Ok(())
    } else {
        Err(format!("{id} is no longer queued for {rcpt}"))
    }
}

/// `--send` (E5): each would whose action is in [`SENDABLE`] and whose switch lets it out
/// goes through the intents ledger (intent → recheck → sending → send → done / abandoned),
/// one dm per recipient. Nothing else is sent.
pub fn send_round(
    dir: &Path,
    would: &[Would],
    sw: Switches,
    seats: &SeatsOut,
    lookup: &dyn crate::source::DmLookup,
    sender: &dyn crate::source::Sender,
    now_ms: i64,
) -> Vec<Sent> {
    let mut out = Vec::new();
    for w in would {
        if !SENDABLE.contains(&w.action.as_str()) || !would_send(sw, &w.action) {
            continue;
        }
        let many = w.to.contains(',');
        for to in w.to.split(',') {
            let key = if many {
                format!("{}@{to}", w.send_key)
            } else {
                w.send_key.clone()
            };
            let addr = seats
                .seats
                .iter()
                .find(|s| s.seat == to && !s.cabin.is_empty())
                .map(|s| format!("{}/{}", s.cabin, s.seat));
            let text = std::iter::once(w.first_line.as_str())
                .chain(w.body.iter().map(String::as_str))
                .chain(std::iter::once(sent_by(dir, &key).as_str()))
                .collect::<Vec<_>>()
                .join("\n");
            let mut fields = serde_json::Map::new();
            fields.insert("action".into(), json!(w.action));
            fields.insert("to".into(), json!(to));
            fields.insert("first_line".into(), json!(w.first_line));
            // one dm for several (E8): every key it carries, read back by with_ledger
            if w.keys.len() > 1 {
                fields.insert("keys".into(), json!(w.keys));
            }
            let answer: std::cell::RefCell<Option<(String, String)>> = Default::default();
            let outcome = crate::intents::carry_out(
                dir,
                &key,
                &fields,
                now_ms,
                || recheck(w, addr.as_deref(), lookup),
                || {
                    let a = addr
                        .as_deref()
                        .ok_or_else(|| crate::intents::SendError::NotSent(NO_CABIN.into()))?;
                    sender.send(a, &text).map(|(stage, id)| {
                        *answer.borrow_mut() = Some((stage.clone(), id.clone()));
                        let mut r = serde_json::Map::new();
                        r.insert("stage".into(), json!(stage));
                        r.insert("dm_id".into(), json!(id));
                        r
                    })
                },
            );
            let (stage, dm_id) = answer.into_inner().unzip();
            out.push(Sent {
                key,
                base_key: w.send_key.clone(),
                to: to.to_owned(),
                outcome,
                stage,
                dm_id: dm_id.filter(|i| crate::send_events::real_dm_id(i)),
            });
        }
    }
    out
}

/// The last line of every dm act sends (S79-sender, 519d0 11-16 08:46, a9136): who turned the
/// switches on, as `switches.jsonl` says (self-reported, so said so), and the intent key, which
/// the recipient can look up in `intents.jsonl` for the dm id. Last, not first: a dispatch's
/// first line stays 「派工: 事 → 席」 (S79 r1), which act's own lookups and the recipients read.
pub fn sent_by(dir: &Path, key: &str) -> String {
    let by = std::fs::read_to_string(dir.join("switches.jsonl"))
        .ok()
        .and_then(|t| {
            t.lines()
                .rev()
                .filter_map(|l| serde_json::from_str::<Value>(l).ok())
                .find_map(|v| v.get("by").and_then(Value::as_str).map(str::to_owned))
        })
        .filter(|b| crate::source::is_seat(b))
        .map_or_else(|| "?".to_owned(), |b| format!("{b}(自報)"));
    format!("— 代發:自動推進,開關 {by} · 意圖 {key}(核對 drive/intents.jsonl)")
}

/// What reads only the dm queue and the seat facts, never next's list (519d0 11-15): sent
/// even when next is not complete.
pub const DM_ACTIONS: [&str; 2] = ["nudge", "tell_sender"];

/// What may go out for rows from a partial scan while next is not complete (519d0 11-15
/// 20:28): opening a review and a fix notice, each after a live recheck of the PR. A
/// dispatch still needs next complete.
pub const PARTIAL_ACTIONS: [&str; 2] = ["open_review", "notify_fix"];

/// How a partial row's recheck says the PR could not be read at all (not closed, not moved
/// on): the retry it gets has no back-off and does not count (5d33a 942c8382 L1).
pub const PR_UNREAD: &str = "PR not read";

/// A partial row's (PR, head) for the live recheck; not partial, not one of
/// [`PARTIAL_ACTIONS`], or no PR or head to check ⇒ `None` (held like any row while next is
/// not complete; in a bundle it holds the whole dm, so a dispatch never rides along).
fn partial_check(row: &Row) -> Option<(u64, String)> {
    if !(row.partial && action_of(row).is_some_and(|a| PARTIAL_ACTIONS.contains(&a))) {
        return None;
    }
    Some((row.pr?, row.head.clone().filter(|h| !h.is_empty())?))
}

/// A list that is not complete (IFACE §1: rc 3) holds back the work it drives (review
/// dispatch, dispatch, fix requests, wrap-ups); nudges and tellings ([`DM_ACTIONS`]) go out
/// anyway (519d0 11-15), and so do a review or fix notice whose rows all come from a
/// partial scan that did not name them ([`PARTIAL_ACTIONS`], each PR rechecked live before
/// it goes, 519d0 11-15 20:28). What it held back that would have gone out is given back as
/// `retry` lines with no back-off, so it is planned again at once (7b81e d3ad28dc M2: the
/// would lines made those keys handled). A complete list holds nothing back.
pub fn hold_for_incomplete(
    would: &[Would],
    sw: Switches,
    complete: bool,
    now_ms: i64,
) -> (Vec<Would>, Vec<String>) {
    if complete {
        return (would.to_vec(), Vec::new());
    }
    let (out, held): (Vec<Would>, Vec<Would>) = would.iter().cloned().partition(|w| {
        DM_ACTIONS.contains(&w.action.as_str())
            || (PARTIAL_ACTIONS.contains(&w.action.as_str()) && w.pr_checks.is_some())
    });
    let back = held
        .iter()
        .filter(|w| SENDABLE.contains(&w.action.as_str()) && would_send(sw, &w.action))
        .map(|w| retry_line(now_ms, &w.send_key, "next not complete", false))
        .collect();
    (out, back)
}

/// The journal lines after a send round: for each send that did not go out, a `retry` line
/// for every dm its would carried (E8), or a `gone` line when it was a nudge whose seat is
/// not there ([`seat_gone`]: final, the sender is told next round). Each line once.
pub fn after_send(
    would: &[Would],
    sent: &[Sent],
    roster_complete: bool,
    now_ms: i64,
) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for s in sent {
        let Ok(crate::intents::Outcome::Dropped(why) | crate::intents::Outcome::SendFailed(why)) =
            &s.outcome
        else {
            continue;
        };
        let keys = would
            .iter()
            .find(|w| w.send_key == s.base_key)
            .map_or_else(|| vec![s.base_key.clone()], |w| w.keys.clone());
        // no cabin in the facts is gone only when the roster was read whole (87167 18:0x:
        // f88fa backed off for ever; 5d33a 0a39f00a L1: an incomplete round is not proof);
        // a nudge or a 「卡點:」 dm to a seat that is not there is final (an unblock row is not
        // retried into a new U8 stuck point)
        let not_there = seat_gone(why) || (roster_complete && why == NO_CABIN);
        let gone = (s.base_key.starts_with("nudge:")
            || s.base_key.starts_with(crate::act_unblock::ACTION))
            && not_there;
        for k in &keys {
            let line = if gone {
                gone_line(now_ms, k, why)
            } else {
                // a PR that could not be read (gh down) is not the row's fault: tried again
                // at once and not counted toward giving up (5d33a 942c8382 L1)
                retry_line(now_ms, k, why, !why.starts_with(PR_UNREAD))
            };
            if !out.contains(&line) {
                out.push(line);
            }
        }
    }
    out
}

/// The journal line of one `would` of a `--send` round (see [`would_line_in`]).
pub fn would_line(w: &Would, now_ms: i64, sw: Switches) -> String {
    would_line_in(w, now_ms, sw, true)
}

/// The journal line of one `would`. `would_send` = it would go out were the round live
/// (what the comparison table reads: [`SENDABLE`] and its switch); `send_round` = this round
/// ran with `--send`. Only a line with both is a send ([`History::parse_at`]): a dry run with
/// the switch on no longer holds the key from the first real round.
pub fn would_line_in(w: &Would, now_ms: i64, sw: Switches, sending: bool) -> String {
    json!({
        "v": 1, "kind": "would", "at": rfc3339_ms(now_ms), "at_ms": now_ms,
        "action": w.action, "to": w.to, "first_line": w.first_line, "body": w.body,
        "keys": w.keys, "throttled": w.throttled, "ranks": w.ranks,
        "switch_on": sw.get(&w.action), "paused": sw.paused,
        "would_send": goes_out(sw, &w.action, true),
        "send_round": sending,
    })
    .to_string()
}

/// `<drive>/basis.jsonl`: one line per dm act plans, with what it was planned on (METHOD-drive-
/// hitrate-5d33a r1 columns 2–8, a9136 11-17). Joined to its dm id and send time by `key`
/// on `intents.jsonl`'s `done` line (column 1).
pub const BASIS_FILE: &str = "basis.jsonl";

/// `YYYY-MM-DDTHH:MM:SS[.fff]Z` (what next and act write) as ms since the epoch; anything
/// else = None (an offset other than Z is not guessed).
pub fn rfc3339_to_ms(s: &str) -> Option<i64> {
    let b = s.as_bytes();
    if b.len() < 20 || b[4] != b'-' || b[7] != b'-' || b[10] != b'T' || !s.ends_with('Z') {
        return None;
    }
    let n = |r: std::ops::Range<usize>| s.get(r)?.parse::<i64>().ok();
    let (y, mo, d) = (n(0..4)?, n(5..7)?, n(8..10)?);
    let (h, mi, se) = (n(11..13)?, n(14..16)?, n(17..19)?);
    let frac = match s.get(19..s.len() - 1)? {
        "" => 0,
        f if f.starts_with('.') && f.len() >= 2 => {
            let digits = f.get(1..)?;
            let ms3: String = digits.chars().chain("000".chars()).take(3).collect();
            ms3.parse::<i64>().ok()?
        }
        _ => return None,
    };
    if !(1..=12).contains(&mo) || !(1..=31).contains(&d) || h > 23 || mi > 59 || se > 60 {
        return None;
    }
    // days from civil (Howard Hinnant)
    let y2 = if mo <= 2 { y - 1 } else { y };
    let era = y2.div_euclid(400);
    let yoe = y2 - era * 400;
    let mp = if mo > 2 { mo - 3 } else { mo + 9 };
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    Some(((days * 86_400 + h * 3600 + mi * 60 + se) * 1000) + frac)
}

/// One `basis.jsonl` line for a planned dm: the rows it carries (kind / layer / reason /
/// branch / head 40 / PR / why), how old each row's facts were, the recipient's seat facts
/// this round, and the last dm act sent that seat and the last time this work went to anyone.
pub fn basis_line(
    w: &Would,
    next: &NextOut,
    seats: &SeatsOut,
    hist: &History,
    now_ms: i64,
    sending: bool,
    pending: Option<&Path>,
) -> String {
    let rows: Vec<Value> = next
        .rows
        .iter()
        .filter(|r| w.ranks.contains(&r.rank))
        .map(|r| {
            let age = rfc3339_to_ms(&r.data_at).map(|t| now_ms.saturating_sub(t) / 60_000);
            json!({
                "rank": r.rank, "layer": r.layer, "kind": r.kind, "what": r.what,
                "pr": r.pr, "head": r.head, "branch": r.branch, "node": r.node,
                "author": row_author(r), "why_him": r.why_him, "why_now": r.why_now,
                "cause": r.cause, "partial": r.partial,
                "data_at": r.data_at, "facts_age_min": age,
                // 5d33a 03:07 (X5): review files already bound to this head
                "review_at_head": match (pending, r.head.as_deref()) {
                    (Some(p), Some(h)) => json!(reviews_at_head(p, h)),
                    _ => Value::Null,
                },
            })
        })
        .collect();
    let seat = |to: &str| {
        seats.seats.iter().find(|s| s.seat == to).map(|s| {
            json!({
                "seat": s.seat, "role": s.role, "alive": s.alive.v,
                "dispatchable": s.dispatchable, "size": s.size,
                "unread": s.capacity.unread, "on_deck": s.capacity.on_deck, "first": s.first,
            })
        })
    };
    let to: Vec<&str> = w.to.split(',').collect();
    let last_to: Value = to
        .iter()
        .map(|t| {
            (
                t.to_string(),
                hist.last_sent_ms.get(*t).map(|m| rfc3339_ms(*m)),
            )
        })
        .map(|(t, m)| json!({"seat": t, "last_sent": m}))
        .collect();
    let same: Vec<Value> = w
        .keys
        .iter()
        .filter_map(|k| {
            let base = split_seat(k).map_or(k.as_str(), |(b, _)| b);
            hist.went_to
                .get(base)
                .map(|(s, at)| json!({"key": k, "went_to": s, "at": rfc3339_ms(*at)}))
        })
        .collect();
    json!({
        "v": 1, "kind": "basis", "at": rfc3339_ms(now_ms), "at_ms": now_ms,
        "key": w.send_key, "keys": w.keys, "action": w.action, "to": w.to,
        "first_line": w.first_line, "send_round": sending,
        "rows": rows,
        "seats": to.iter().copied().map(seat).collect::<Vec<_>>(),
        "last_sent_to_seat": last_to,
        "same_work_before": same,
    })
    .to_string()
}

/// The reviewer seat in a review file name `REVIEW-<what>-<seat>-<head8>.md`: the part
/// before the last.
pub fn review_seat(file: &str) -> Option<&str> {
    let stem = file.strip_suffix(".md")?;
    let mut parts = stem.rsplit('-');
    parts.next()?;
    parts
        .next()
        .filter(|p| p.len() == 5 && p.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')))
}

/// File name starts of a review bound to a head: a review, a check of an integration stack
/// and its recheck (5d33a CHECK-drive-nohist M3: #827 / #831 have CHECK- / RECHECK- files).
pub const REVIEW_PREFIXES: [&str; 3] = ["REVIEW-", "CHECK-", "RECHECK-"];

/// Review files ([`REVIEW_PREFIXES`]) in `pending` whose first line binds `head` (RULES §20
/// `頭: <40>`), sorted; a folder that cannot be read = none.
pub fn reviews_at_head(pending: &Path, head: &str) -> Vec<String> {
    let want = format!("頭: {head}");
    let mut out: Vec<String> = fs::read_dir(pending)
        .into_iter()
        .flatten()
        .flatten()
        .filter_map(|e| e.file_name().into_string().ok())
        .filter(|n| REVIEW_PREFIXES.iter().any(|p| n.starts_with(p)) && n.ends_with(".md"))
        .filter(|n| {
            fs::read_to_string(pending.join(n))
                .ok()
                .and_then(|t| t.lines().next().map(|l| l.contains(&want)))
                .unwrap_or(false)
        })
        .collect();
    out.sort();
    out
}

/// One `basis.jsonl` line for a row [`screen_rows`] dropped: its rank and why.
pub fn screened_line(s: &Skip, now_ms: i64) -> String {
    json!({
        "v": 1, "kind": "screened", "at": rfc3339_ms(now_ms), "at_ms": now_ms,
        "rank": s.rank, "why": s.why,
    })
    .to_string()
}

/// One `basis.jsonl` line written right before a work dm goes out (5d33a 03:07, X4): each PR
/// the dm carries as GitHub has it at send time, beside the head the rows were planned on.
pub fn basis_send_line(
    w: &Would,
    next: &NextOut,
    lookup: &dyn crate::source::DmLookup,
    now_ms: i64,
) -> String {
    let prs: Vec<Value> = next
        .rows
        .iter()
        .filter(|r| r.pr.is_some() && w.ranks.contains(&r.rank))
        .filter_map(|r| r.pr.map(|n| (n, r.head.clone())))
        .map(|(n, row_head)| match lookup.pr(n) {
            Ok(v) => {
                let live = v["head"]["sha"].as_str().map(str::to_owned);
                json!({
                    "pr": n, "row_head": row_head, "pr_head_at_send": live,
                    "state": v["state"], "same": live.is_some() && live == row_head,
                })
            }
            Err(e) => json!({"pr": n, "row_head": row_head, "pr_head_at_send": null,
                             "unknown": e}),
        })
        .collect();
    json!({
        "v": 1, "kind": "basis_send", "at": rfc3339_ms(now_ms), "at_ms": now_ms,
        "key": w.send_key, "action": w.action, "to": w.to, "prs": prs,
    })
    .to_string()
}

/// The journal line after a send that did not go out (the recheck said no, or `aib dm`
/// failed): [`History::parse`] forgets the key so the next round plans it again.
/// `backoff: false` = the round sent nothing at all (next not complete): planned again at
/// once and not counted toward [`RETRY_MAX`].
pub fn retry_line(now_ms: i64, key: &str, why: &str, backoff: bool) -> String {
    json!({"v": 1, "kind": "retry", "at": rfc3339_ms(now_ms), "at_ms": now_ms, "key": key,
           "why": why, "backoff": backoff})
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

/// The live dm queue, `<root>/state/dm-queue` (dm_queue.py's queue_dir), beside `drive/`.
pub fn queue_dir() -> Option<PathBuf> {
    drive_dir()?
        .parent()
        .map(|r| r.join("state").join("dm-queue"))
}

/// A queue item's sender seat: `sender_seat`, else the `@xxxxx` that starts `sender_label`
/// (87167 18:0x: 17 items from provider-launch carry the seat only in the label); none ⇒
/// empty (the sender is 不明 and only the coordinator is told).
pub fn sender_of(v: &Value) -> String {
    let text = |k: &str| v.get(k).and_then(Value::as_str).unwrap_or_default();
    if !text("sender_seat").is_empty() {
        return text("sender_seat").to_owned();
    }
    text("sender_label")
        .strip_prefix('@')
        .and_then(|l| l.get(..5))
        .filter(|s| crate::source::is_seat(s))
        .unwrap_or_default()
        .to_owned()
}

/// A queue item's `sender_label` when it is a plain name, not a seat (1.x writes the origin's
/// plain name there, e.g. 自動推進 for act's own shell, S79-sender; the queue file has no
/// `sender` field, 5d33a 352f8776 M1); `@…`, any `kind:name` label (`agent:offhub`,
/// `unattested:…`), empty or over 40 characters ⇒ empty (不明).
pub fn name_of(v: &Value) -> String {
    let t = v
        .get("sender_label")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim();
    if t.is_empty() || t.starts_with('@') || t.contains(':') || t.chars().count() > 40 {
        return String::new();
    }
    t.to_owned()
}

/// The dms still `queued` in the live queue, read only (519d0 11-15: act's `--dm` had no
/// command that printed it): `<dir>/<seat>/q_*.json`, one item each (aib-dm-queue-item/v2).
/// `to` is the item's `seat` (else the folder's name), `from` its sender ([`sender_of`]),
/// `queued_min` the whole minutes since `sent_at` (else `ts`). Only 5-hex seat folders are
/// read; no such folder ⇒ empty. An item that cannot be read or parsed is skipped and named
/// in the second list (one bad file must not stop every nudge); a folder that exists but
/// cannot be listed is `Err`.
pub fn waiting_from_queue(dir: &Path, now_s: i64) -> Result<(Vec<Waiting>, Vec<String>), String> {
    let (mut out, mut bad) = (Vec::new(), Vec::new());
    let seats = match fs::read_dir(dir) {
        Ok(rd) => rd,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok((out, bad)),
        Err(e) => return Err(format!("{}: {e}", dir.display())),
    };
    let mut seat_dirs: Vec<(String, PathBuf)> = seats
        .flatten()
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().into_owned();
            (crate::source::is_seat(&name) && e.path().is_dir()).then(|| (name, e.path()))
        })
        .collect();
    seat_dirs.sort();
    for (seat, sd) in seat_dirs {
        let items = fs::read_dir(&sd).map_err(|e| format!("{}: {e}", sd.display()))?;
        let mut files: Vec<PathBuf> = items
            .flatten()
            .map(|e| e.path())
            .filter(|p| {
                p.file_name()
                    .and_then(|n| n.to_str())
                    .is_some_and(|n| n.starts_with("q_") && n.ends_with(".json"))
            })
            .collect();
        files.sort();
        for p in files {
            let v: Value = match fs::read(&p)
                .map_err(|e| e.to_string())
                .and_then(|b| serde_json::from_slice(&b).map_err(|e| e.to_string()))
            {
                Ok(v) => v,
                Err(e) => {
                    bad.push(format!("{}: {e}", p.display()));
                    continue;
                }
            };
            let text = |k: &str| v.get(k).and_then(Value::as_str).unwrap_or_default();
            if text("status") != "queued" {
                continue;
            }
            // an item a test sent (origin test) is never nudged (519d0 11-15 on 5d33a N2)
            if text("origin") == "test" {
                continue;
            }
            // a nudge waiting to be read is not nudged in turn (f1fae 11-20)
            if text("msg").starts_with(NUDGE_HEAD) {
                continue;
            }
            let at = v
                .get("sent_at")
                .and_then(Value::as_i64)
                .or_else(|| v.get("ts").and_then(Value::as_i64))
                .unwrap_or(now_s);
            let to = Some(text("seat"))
                .filter(|s| !s.is_empty())
                .unwrap_or(seat.as_str());
            out.push(Waiting {
                id: text("id").to_owned(),
                to: to.to_owned(),
                from: sender_of(&v),
                state: "queued".to_owned(),
                queued_min: (now_s - at).max(0) / 60,
                from_name: name_of(&v),
            });
        }
    }
    Ok((out, bad))
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
aib-drive act --once (--dry-run|--send) --next <next.json> --seats <seats.json> [--dm <waiting.json>] [--event <drive/event> [--by <名>]]
aib-drive act --auto
aib-drive switch <nudge|open_review|dispatch|notify_fix|unblock_notify|paused> <on|off> --by <席 5 碼> --why \"<為什麼>\"
(--dry-run:算出這一輪會發的信,寫進 <root>/drive/act-dryrun.jsonl,不發任何信;
 --send:再把開關開著的提醒 / 告知寄件人 / 收尾 / 開審查 / 派工 / 修正通知經意圖帳 <root>/drive/intents.jsonl 用 aib dm 發出,
 每種只在自己的開關開著時發;
 兩種都會跟進過去一天發出的工作:送達 / 已讀 / 接單 / 開工分開印，2 小時沒接單只寫 mark 行，不改派;
 不給 --dm:唯讀 <root>/state/dm-queue,只看 status=queued、origin 不是 test 的信;
 next 不完整時只發提醒 / 告知寄件人，其他留到下一輪;
 卡點信(unblock_notify):讀 <root>/drive/unblock.json(超過 20 分不發),同證據只發一次;這一版只試跑;
 開關開著但這一版不送的(U18)每輪印出來;
 --auto:當場算 next 與席位事實寫進 <root>/drive/act-next.json、act-seats.json,再跑一次 --once --send(開關全關 = 只記不發))";

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
/// The clone aib-slice scans: `AIB_DRIVE_REPO_DIR`, else the path in `drive/refresh-repo`
/// (the file refresh --auto reads); none = branches are not looked up.
pub fn repo_dir(drive: &Path) -> Option<PathBuf> {
    let p = nonempty("AIB_DRIVE_REPO_DIR").or_else(|| {
        fs::read_to_string(drive.join("refresh-repo"))
            .ok()
            .map(|t| t.trim().to_owned())
            .filter(|t| !t.is_empty())
    })?;
    let p = PathBuf::from(p);
    p.is_dir().then_some(p)
}

pub fn cli_act(raw: &[String]) -> u8 {
    let f = match Flags::parse(
        raw,
        &["once", "dry-run", "send"],
        &[
            "once", "dry-run", "send", "next", "seats", "dm", "event", "by",
        ],
    ) {
        Ok(f) => f,
        Err(e) => {
            eprintln!("aib-drive act: {e}\n{ACT_USAGE}");
            return RC_USAGE;
        }
    };
    if !f.has("once") || f.has("dry-run") == f.has("send") {
        eprintln!("aib-drive act: one round only (--once), with exactly one of --dry-run / --send\n{ACT_USAGE}");
        return RC_USAGE;
    }
    let (Some(next_p), Some(seats_p)) = (f.get("next"), f.get("seats")) else {
        eprintln!("aib-drive act: --next and --seats are required\n{ACT_USAGE}");
        return RC_USAGE;
    };
    // --by names act only to tell its own events apart: without --event it would do nothing
    if f.has("by") && !f.has("event") {
        eprintln!("aib-drive act: --by goes with --event\n{ACT_USAGE}");
        return RC_USAGE;
    }
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
    // 519d0 11-15: no --dm ⇒ the live queue, read only (no command printed this list)
    let waiting: Vec<Waiting> = match f.get("dm") {
        Some(p) => match read_json::<Vec<Waiting>>(p) {
            Ok(v) => v,
            Err(e) => {
                eprintln!("aib-drive act: dm: {e}");
                return RC_SOURCE;
            }
        },
        None => match queue_dir().map(|q| waiting_from_queue(&q, now_ms() / 1000)) {
            None => Vec::new(),
            Some(Ok((v, bad))) => {
                for b in bad {
                    eprintln!("aib-drive act: dm-queue: {b} (skipped)");
                }
                v
            }
            Some(Err(e)) => {
                eprintln!("aib-drive act: dm-queue: {e}");
                return RC_SOURCE;
            }
        },
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
    let hist = match read_opt(&journal).and_then(|t| History::parse_at(&t.unwrap_or_default(), now))
    {
        Ok(h) => h,
        Err(e) => {
            eprintln!("aib-drive act: act-dryrun.jsonl: {e}");
            return RC_LOCK;
        }
    };
    // nudged / sender told is the ledger's done only, in a dry run too, so the dry run shows
    // what a real round would send (519d0 11-15; 87167 18:0x: the dry run thought three
    // nudges done that the first real round then sent)
    let hist = match read_opt(&dir.join(crate::intents::FILE)) {
        Ok(t) => hist.with_ledger(&t.unwrap_or_default()),
        Err(e) => {
            eprintln!("aib-drive act: {}: {e}", crate::intents::FILE);
            return RC_LOCK;
        }
    };
    let sending = f.has("send");
    let hist = hist.hold_unsent(|a| goes_out(sw, a, sending));
    // the wrap-up as a live round would see it, so the dry run plans what --send sends
    let wrap_out = goes_out(sw, "wrap_up", true);
    let pending = dir.parent().map(|r| r.join("pending"));
    let repo = repo_dir(&dir);
    let lookup = |b: &str| crate::source::branch_head_in(repo.as_deref()?, b);
    let (next, screened) = screen_rows_at(&next, pending.as_deref(), &lookup);
    let (mut would, mut skips) = plan_sw(&next, &seats, &waiting, &hist, now, wrap_out);
    skips.extend(screened.iter().cloned());
    // u-5: the 「卡點:」 rows of unblock.json. It is optional (u-1 writes it): missing = none;
    // a file that cannot be read is said and skipped, never stops the round
    let unblock = match read_opt(&dir.join(crate::act_unblock::FILE)) {
        Ok(None) => None,
        Ok(Some(t)) => match serde_json::from_str::<crate::act_unblock::UnblockOut>(&t) {
            Ok(u) => Some(u),
            Err(e) => {
                eprintln!(
                    "aib-drive act: {}: {e} (no 卡點 dms)",
                    crate::act_unblock::FILE
                );
                None
            }
        },
        Err(e) => {
            eprintln!(
                "aib-drive act: {}: {e} (no 卡點 dms)",
                crate::act_unblock::FILE
            );
            None
        }
    };
    crate::act_unblock::plan(unblock.as_ref(), &seats, &hist, now, &mut would, &mut skips);
    for n in sw.on_but_dry() {
        println!("act: 開關 {n} 開著,但這一版不送 {n}(U18,只試跑)");
    }
    let mut lines: Vec<String> = would
        .iter()
        .map(|w| would_line_in(w, now, sw, sending))
        .collect();
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
    // what each dm was planned on (5d33a's hit-rate columns): a log, so a write that fails
    // is said and the round goes on
    let mut basis: Vec<String> = would
        .iter()
        .map(|w| basis_line(w, &next, &seats, &hist, now, sending, pending.as_deref()))
        .collect();
    // rows screened out before planning (5d33a r1 N1): X5 / X7 are counted from these
    basis.extend(screened.iter().map(|s| screened_line(s, now)));
    if let Err(e) = append(&dir.join(BASIS_FILE), &basis) {
        eprintln!("aib-drive act: {BASIS_FILE} not written: {e}");
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
    // E10: follow the work sent before (both modes: it only reads and marks)
    if let Err(e) = follow_round(&dir, &journal, &hist, now) {
        eprintln!("aib-drive act: {e}");
        return RC_LOCK;
    }
    if !f.has("send") {
        return RC_OK;
    }
    // a list that is not complete is not acted on (IFACE §1: rc 3 = not complete, not
    // nothing to do): the round is journaled as a dry run, nothing is sent
    // nudges and tellings read only the dm queue and the seat facts: they go out even when
    // next is not complete; the work next's list drives waits (519d0 11-15)
    let (would_out, back) = hold_for_incomplete(&would, sw, next.complete, now);
    if !next.complete {
        println!("act send: next is not complete, only nudges, tellings and partial rows rechecked live go out");
        if !back.is_empty() {
            if let Err(e) = append(&journal, &back) {
                eprintln!("aib-drive act: {e}");
                return RC_LOCK;
            }
        }
    }
    let live = crate::source::LiveLookup::new();
    // each work dm's PRs as GitHub has them now, beside the planned head (5d33a 03:07, X4)
    let at_send: Vec<String> = would_out
        .iter()
        .filter(|w| WORK.contains(&w.action.as_str()) && would_send(sw, &w.action))
        .map(|w| basis_send_line(w, &next, &live, now))
        .collect();
    if !at_send.is_empty() {
        if let Err(e) = append(&dir.join(BASIS_FILE), &at_send) {
            eprintln!("aib-drive act: {BASIS_FILE} not written: {e}");
        }
    }
    let sent = send_round(
        &dir,
        &would_out,
        sw,
        &seats,
        &live,
        &crate::source::AibDm::new(),
        now,
    );
    // S107-product slice 3: each dm tried and each one held by its switch, as events (a write
    // that fails is said and changes nothing)
    if let Some(root) = dir.parent() {
        let run = crate::send_events::Run::load(root);
        let evs = crate::send_events::drop_held_written(
            crate::send_events::send_events(&run, &would_out, &sent, sw, now),
            &crate::send_events::held_today(root, now),
        );
        crate::send_events::write_all(root, &evs, now);
    }
    let mut rc = RC_OK;
    for k in &hist.gave_up {
        println!("act send: gave up on {k} after {RETRY_MAX} tries that did not go out");
    }
    for s in &sent {
        match &s.outcome {
            Ok(o) => println!("send {} → {}: {o:?}", s.key, s.to),
            Err(e) => {
                eprintln!("aib-drive act: send {} → {}: {e}", s.key, s.to);
                rc = RC_LOCK;
            }
        }
    }
    let retry = after_send(&would, &sent, seats.complete, now);
    if !retry.is_empty() {
        if let Err(e) = append(&journal, &retry) {
            eprintln!("aib-drive act: {e}");
            rc = RC_LOCK;
        }
    }
    println!("act send: {} through the intents ledger", sent.len());
    rc
}

/// E10: print the four steps of each piece of work sent in the last day and journal a
/// `mark` for work not taken up in time. No ledger = nothing sent yet = nothing to follow
/// (no outside call).
fn follow_round(dir: &Path, journal: &Path, hist: &History, now: i64) -> Result<(), String> {
    let Some(ledger) = read_opt(&dir.join(crate::intents::FILE))? else {
        return Ok(());
    };
    let root = dir.parent().unwrap_or(dir).to_path_buf();
    let with_status = crate::track::follow_with_status(
        &ledger,
        now,
        &crate::source::AibQueue::new(),
        &crate::track::FsSeatNow { root: root.clone() },
    );
    // S107-product slice 3: a status not written before, as a drive_send_after event
    let run = crate::send_events::Run::load(&root);
    let (seen, cards) = crate::send_events::written(&root, now);
    let st: Vec<_> = with_status
        .iter()
        .map(|(w, _, st)| (w.clone(), st.clone()))
        .collect();
    let evs = crate::send_events::after_events(&run, &st, &seen, &cards, now);
    crate::send_events::write_all(&root, &evs, now);
    let followed: Vec<_> = with_status.into_iter().map(|(w, s, _)| (w, s)).collect();
    for (w, s) in &followed {
        println!("{}", crate::track::line(w, *s));
    }
    let marks = crate::track::due_marks(&followed, &hist.marked, now);
    for m in &marks {
        println!("mark: {m}");
    }
    append(journal, &marks)
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
                "aib-drive switch: <nudge|open_review|dispatch|notify_fix|unblock_notify|paused> <on|off>\n{ACT_USAGE}"
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
    // U18 (DECISION-council-unblock §2): a switch turned on that this version never sends
    // for is said out loud and recorded, so nobody waits for dms that cannot come
    let sends = Switches::sends(name);
    if on && !sends {
        eprintln!(
            "aib-drive switch: 注意(U18):這一版 act 不送 {name}(只試跑,不在 SENDABLE),開了也只記 would"
        );
    }
    let rec = json!({"v": 1, "at": rfc3339_ms(now), "at_ms": now, "by": by, "why": why,
                     "switch": name, "from": was, "to": on, "sends": sends})
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
pub(crate) mod tests {
    use super::*;

    const H: &str = "1b0d079b58bf33909f6b8302e82b543d40043581";
    const NOW: i64 = 1_794_700_800_000; // 2026-11-15T00:00:00.000Z

    /// A fresh folder under the system temp folder, removed on drop (no tempfile dependency:
    /// this slice does not touch Cargo.toml).
    pub struct Scratch(PathBuf);

    impl Scratch {
        pub fn new(tag: &str) -> Scratch {
            // macOS gives the clock in microseconds: two tests with the same tag starting in the
            // same microsecond on parallel threads got the same folder and read each other's
            // events (send_events tests, receipts a9136-dsf* 11-17, 7b81e-wsd-c0dd890bb 11-19).
            // A number per call keeps every folder its own.
            static SEQ: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
            let seq = SEQ.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            let nanos = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(0, |d| d.as_nanos());
            let p = std::env::temp_dir().join(format!(
                "aib-drive-act-{tag}-{}-{nanos}-{seq}",
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
            cabin: "dab9".into(),
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
            branch: None,
            head: Some(H.into()),
            to: Some(to.into()),
            why_him: "交換審查".into(),
            why_now: "收據綠".into(),
            needs: "none".into(),
            size: "review".into(),
            data_at: "2026-11-15T00:00:00.000Z".into(),
            kind: String::new(),
            cause: None,
            partial: false,
        }
    }

    fn next(rows: Vec<Row>) -> NextOut {
        NextOut {
            complete: true,
            rows,
        }
    }

    fn seats(v: Vec<Seat>) -> SeatsOut {
        SeatsOut {
            seats: v,
            complete: false,
        }
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
        assert_eq!(
            w[1].body,
            [
                "節點:S2".to_owned(),
                format!("證據:PR #702 · 頭 {H} · 資料時間 2026-11-15T00:00:00.000Z"),
                "為什麼是你:交換審查".to_owned(),
                "為什麼是現在:收據綠".to_owned(),
            ]
        );
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

    fn fix_row(rank: u64, what: &str, to: &str) -> Row {
        let mut r = row(rank, 2, to);
        r.kind = "fix".into();
        r.what = what.into();
        r
    }

    /// IFACE r10 1: a layer-2 fix row tells its author in one line; a layer-2 row of another
    /// kind (a receipt to run) is not an act action.
    #[test]
    fn a_fix_row_tells_the_author_and_a_receipt_row_is_not_acted_on() {
        let mut receipt = row(2, 2, "ebb36");
        receipt.kind = "receipt".into();
        let n = next(vec![fix_row(1, "#757 修:跟 main 衝突", "a9136"), receipt]);
        let (w, skips) = plan(
            &n,
            &seats(vec![seat("a9136"), seat("ebb36")]),
            &[],
            &History::default(),
            NOW,
        );
        assert_eq!(w.len(), 1, "{w:?}");
        assert_eq!(w[0].action, "notify_fix");
        assert_eq!(w[0].to, "a9136");
        assert_eq!(w[0].first_line, "派工: 修 #757:跟 main 衝突 → a9136");
        assert_eq!(w[0].keys, vec![format!("notify_fix:#701:{H}:a9136")]);
        assert_eq!(
            skips,
            vec![Skip {
                rank: 2,
                why: "layer 2 is not an act action".into()
            }]
        );
        // the same head again: already told
        let hist = History {
            keys: w[0].keys.iter().cloned().collect(),
            ..History::default()
        };
        let (w2, _) = plan(&n, &seats(vec![seat("a9136")]), &[], &hist, NOW);
        assert!(w2.is_empty(), "{w2:?}");
    }

    /// IFACE r11 4: an infrastructure red (the job died setting up the runner) is not the
    /// author's: it goes to the merge queue to be rerun, with the author named in the body.
    #[test]
    fn an_infra_red_goes_to_the_merge_queue_not_the_author() {
        let mut r = fix_row(1, "#748 修:macOS 格 Set up runner 失敗", "043dd");
        r.cause = Some("infra".into());
        let mut mq = seat(MERGE_QUEUE);
        mq.role = "ops".into();
        let (w, _) = plan(
            &next(vec![r]),
            &seats(vec![seat("043dd"), mq]),
            &[],
            &History::default(),
            NOW,
        );
        assert_eq!(w.len(), 1, "{w:?}");
        assert_eq!(w[0].to, MERGE_QUEUE);
        assert_eq!(
            w[0].first_line,
            format!("派工: 重跑 #748:macOS 格 Set up runner 失敗 → {MERGE_QUEUE}")
        );
        assert!(
            w[0].body.iter().any(|b| b.starts_with("作者:043dd")),
            "{:?}",
            w[0].body
        );
    }

    #[test]
    fn the_notify_fix_switch_starts_off_and_is_read() {
        assert!(!Switches::default().notify_fix);
        let s = Switches::parse(r#"{"notify_fix":true}"#).expect("s");
        assert!(s.get("notify_fix") && !s.get("dispatch"));
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
        full.capacity.unread = Some(UNREAD_MAX);
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
                "2240e: 10 unread",
                "7b81e: on deck in the merge queue",
                "519d0: not a dev seat",
                "ebb36: size slice does not fit review",
                "needs needs_human",
                "1ced7: unread unknown",
            ]
        );
    }

    /// S79-busy (Andy 11-16 07:0x「同意,在忙也發,30 分鐘最多一封」): a row next marks
    /// seat_busy goes to a seat in its turn; one per seat per 30 minutes, unread over 10,
    /// a seat not alive, or a seat next and the facts disagree on, still hold it back.
    #[test]
    fn a_seat_in_its_turn_still_gets_its_row_at_most_once_per_30_minutes() {
        let in_turn = |code: &str| {
            let mut s = seat(code);
            s.dispatchable = "no".into();
            s.size = "none".into();
            s
        };
        let busy_row = |rank: u64, to: &str| {
            let mut r = row(rank, 3, to);
            r.needs = BUSY.into();
            r.size = "slice".into();
            r
        };
        // sent: the size of a seat in its turn is not judged
        let n = next(vec![busy_row(1, "b433b")]);
        let (w, skips) = plan(
            &n,
            &seats(vec![in_turn("b433b")]),
            &[],
            &History::default(),
            NOW,
        );
        assert!(skips.is_empty(), "{skips:?}");
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].to, "b433b");
        // a dm to it 10 minutes ago: skipped with the reason
        let mut hist = History::default();
        hist.last_sent_ms.insert("b433b".into(), NOW - 10 * 60_000);
        let (w, skips) = plan(&n, &seats(vec![in_turn("b433b")]), &[], &hist, NOW);
        assert!(w.is_empty(), "{w:?}");
        assert_eq!(skips[0].why, "b433b: sent 10 min ago (one per 30 min)");
        // 31 minutes ago: sent again
        hist.last_sent_ms.insert("b433b".into(), NOW - 31 * 60_000);
        let (w, _) = plan(&n, &seats(vec![in_turn("b433b")]), &[], &hist, NOW);
        assert_eq!(w.len(), 1);
        // unread over 10, not alive, dispatchable unknown: still held back
        let mut full = in_turn("2240e");
        full.capacity.unread = Some(11);
        let mut lost = in_turn("c477a");
        lost.alive.v = "no".into();
        let mut unsure = in_turn("1ced7");
        unsure.dispatchable = "unknown".into();
        let n = next(vec![
            busy_row(1, "2240e"),
            busy_row(2, "c477a"),
            busy_row(3, "1ced7"),
        ]);
        let (w, skips) = plan(
            &n,
            &seats(vec![full, lost, unsure]),
            &[],
            &History::default(),
            NOW,
        );
        assert!(w.is_empty(), "{w:?}");
        let why: Vec<&str> = skips.iter().map(|s| s.why.as_str()).collect();
        assert_eq!(
            why,
            vec![
                "2240e: 11 unread",
                "c477a: alive no",
                "1ced7: dispatchable unknown"
            ]
        );
    }

    /// 519d0 11-16 S79-anc 1: a row with no PR and no node is keyed by its branch, so a rank
    /// that moves (d3cb6126 rank 1 → 2) does not send it again; with no branch either, the
    /// rank as before.
    #[test]
    fn a_branch_row_keeps_its_key_when_its_rank_moves() {
        let mut a = row(1, 2, "56cda");
        a.pr = None;
        a.node = None;
        a.branch = Some("feat/aib-slice-parts-56cda-1115".into());
        let mut b = a.clone();
        b.rank = 2;
        assert_eq!(
            key("notify_fix", &a, "56cda"),
            key("notify_fix", &b, "56cda")
        );
        assert_eq!(
            key("notify_fix", &a, "56cda"),
            format!("notify_fix:branch=feat/aib-slice-parts-56cda-1115:{H}:56cda")
        );
        a.branch = None;
        b.branch = None;
        assert_ne!(
            key("notify_fix", &a, "56cda"),
            key("notify_fix", &b, "56cda")
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
        let journal = format!("{}\n", would_line(&w[0], NOW, sw_on()));
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

    /// 519d0 11-15 23:55: a would written while its switch was off (or by a dry run) is not
    /// a send: the next round with the switch on still sends it, and the seat's 30-minute gap
    /// is not used up by it.
    #[test]
    fn a_dry_run_would_is_not_a_send() {
        let n = next(vec![row(1, 3, "a9136")]);
        let all = seats(vec![seat("a9136")]);
        let (w, _) = plan(&n, &all, &[], &History::default(), NOW);
        assert_eq!(w.len(), 1);
        let journal = format!("{}\n", would_line(&w[0], NOW, Switches::default()));
        let hist = History::parse_at(&journal, NOW + 60_000)
            .expect("journal")
            .with_ledger("");
        assert!(hist.keys.is_empty(), "{:?}", hist.keys);
        assert!(hist.last_sent_ms.is_empty(), "{:?}", hist.last_sent_ms);
        assert!(hist.went_to.is_empty(), "{:?}", hist.went_to);
        // a round that sends open_review plans it again: the dry run did not send it
        let live = hist.clone().hold_unsent(|a| a == "open_review");
        let (w2, skips) = plan(&n, &all, &[], &live, NOW + 60_000);
        assert_eq!(w2.len(), 1, "{skips:?}");
        assert_eq!(w2[0].keys, w[0].keys);
        // another dry run (or the switch still off) lists it once, not every round
        let dry = hist.hold_unsent(|_| false);
        let (w3, _) = plan(&n, &all, &[], &dry, NOW + 60_000);
        assert!(w3.is_empty(), "{w3:?}");
    }

    /// 5d33a 7919bb14: a dry run with the switch on journals `would_send: true`, but its
    /// round did not send: the first `--send` round still sends it. A line from before
    /// `send_round` existed counts as sent, so nothing is sent twice.
    #[test]
    fn a_dry_run_with_the_switch_on_is_not_a_send() {
        let n = next(vec![row(1, 3, "a9136")]);
        let all = seats(vec![seat("a9136")]);
        let (w, _) = plan(&n, &all, &[], &History::default(), NOW);
        let line = would_line_in(&w[0], NOW, sw_on(), false);
        let v: Value = serde_json::from_str(&line).expect("json");
        assert_eq!(v["would_send"], json!(true), "what a live round would do");
        assert_eq!(v["send_round"], json!(false));
        let hist = History::parse_at(&format!("{line}\n"), NOW + 60_000).expect("journal");
        assert!(hist.keys.is_empty(), "{:?}", hist.keys);
        let live = hist.clone().hold_unsent(|a| goes_out(sw_on(), a, true));
        assert_eq!(plan(&n, &all, &[], &live, NOW + 60_000).0.len(), 1);
        let dry = hist.hold_unsent(|a| goes_out(sw_on(), a, false));
        assert!(plan(&n, &all, &[], &dry, NOW + 60_000).0.is_empty());
        let mut old = v.clone();
        old.as_object_mut().expect("object").remove("send_round");
        let hist = History::parse_at(&format!("{old}\n"), NOW + 60_000).expect("journal");
        assert!(hist.keys.contains(&w[0].keys[0]), "{:?}", hist.keys);
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
                ..Default::default()
            },
            Waiting {
                id: "q_2".into(),
                to: "a9136".into(),
                from: "ebb36".into(),
                state: "queued".into(),
                queued_min: 4,
                ..Default::default()
            },
            Waiting {
                id: "q_3".into(),
                to: "a9136".into(),
                from: "ebb36".into(),
                state: "delivered".into(),
                queued_min: 60,
                ..Default::default()
            },
        ];
        let n = next(vec![]);
        let none = seats(vec![]);
        let (w, _) = plan(&n, &none, &q, &History::default(), NOW);
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].action, "nudge");
        // only q_1: q_2 (4 minutes) is too new and q_3 is not queued; a one-dm count alone
        // does not show it, since nudges to one seat are one dm (E8; a9136 A13 MISS)
        assert_eq!(w[0].keys, ["nudge:q_1:-:a9136"], "{w:?}");
        assert!(!w[0].throttled, "a nudge is not a dispatch");
        let hist = History::parse(&format!("{}\n", would_line(&w[0], NOW, sw_on()))).expect("h");
        let (w2, _) = plan(&n, &none, &q, &hist, NOW);
        assert_eq!(w2.len(), 1);
        assert_eq!(w2[0].action, "tell_sender");
        assert_eq!(w2[0].to, format!("ebb36,{COORDINATOR}"));
        let mut j = format!("{}\n", would_line(&w[0], NOW, sw_on()));
        j.push_str(&format!("{}\n", would_line(&w2[0], NOW, sw_on())));
        let (w3, _) = plan(&n, &none, &q, &History::parse(&j).expect("h"), NOW);
        assert!(w3.is_empty());
    }

    /// 5d33a 624d4c96 L1: a queue item with no sender_seat names no sender and tells only
    /// the coordinator (not ",519d0" / "寄件人 ,").
    #[test]
    fn a_dm_with_no_sender_is_nudged_and_only_the_coordinator_is_told() {
        let q = vec![waiting("q_1", "a9136", "")];
        let (n, none) = (next(vec![]), seats(vec![]));
        let (w, _) = plan(&n, &none, &q, &History::default(), NOW);
        assert_eq!(w[0].action, "nudge");
        assert_eq!(w[0].body, vec!["寄件人 不明,已排隊 9 分".to_owned()]);
        let hist = History::parse(&format!("{}\n", would_line(&w[0], NOW, sw_on()))).expect("h");
        let (w2, _) = plan(&n, &none, &q, &hist, NOW);
        assert_eq!(w2[0].action, "tell_sender");
        assert_eq!(w2[0].to, COORDINATOR);
        assert_eq!(
            w2[0].body,
            vec!["寄件人 不明;要不要換人請統籌席決定".to_owned()]
        );
        // S79-sender: a sender with a plain name but no seat (act's own shell) is named, not 不明
        let mut named = waiting("q_2", "a9136", "");
        named.from_name = "自動推進".into();
        let (w, _) = plan(&n, &none, &[named], &History::default(), NOW);
        assert_eq!(w[0].body, vec!["寄件人 自動推進,已排隊 9 分".to_owned()]);
        // the queue file's sender fields as written (b433b/q_9621d015f4353846)
        let real = json!({"id": "q_9621d015f4353846", "seat": "b433b", "sender_kind": "unknown",
            "sender_label": "自動推進", "sender_origin": "auto-find-work-dm", "sender_seat": null});
        assert_eq!(name_of(&real), "自動推進");
        assert_eq!(sender_of(&real), "");
        for s in ["@1ced7(x)", "agent:offhub", "unattested:hub-lobby", " ", ""] {
            assert_eq!(name_of(&json!({"sender_label": s})), "", "{s}");
        }
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
            notify_fix: true,
            unblock_notify: true,
        };
        let paused = Switches {
            paused: true,
            ..all_on
        };
        for a in [
            "dispatch",
            "open_review",
            "notify_fix",
            "wrap_up",
            "nudge",
            "tell_sender",
        ] {
            assert!(would_send(all_on, a), "{a} goes out when nothing is paused");
            assert!(
                !would_send(Switches::default(), a),
                "{a}: every switch starts off"
            );
        }
        assert!(!would_send(paused, "dispatch"));
        assert!(!would_send(paused, "open_review"));
        assert!(
            !would_send(paused, "notify_fix"),
            "a fix request is new work too"
        );
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

    /// 7b81e E4 M1 at act's level: a key whose send did not go out is planned again.
    #[test]
    fn a_retry_line_lets_the_next_round_plan_the_key_again() {
        let ws = [waiting("q_aaaa0001", "a9136", "1ced7")];
        let all = seats(vec![seat("a9136")]);
        let (w, _) = plan(&next(Vec::new()), &all, &ws, &History::default(), NOW);
        let journal = format!(
            "{}\n{}\n",
            would_line(&w[0], NOW, sw_on()),
            retry_line(NOW, &w[0].send_key, "aib dm stage preflight", true)
        );
        // 7b81e d3ad28dc L1: not before RETRY_AFTER_MS
        let h = History::parse_at(&journal, NOW + RETRY_AFTER_MS - 1).expect("journal");
        let (soon, _) = plan(&next(Vec::new()), &all, &ws, &h, NOW);
        // 5d33a r2 2: while the nudge backs off, nothing is planned (not telling the sender)
        assert!(soon.is_empty(), "still backing off: {soon:?}");
        assert!(h_soon_backing(&journal), "the key is backing off");
        let h = History::parse_at(&journal, NOW + RETRY_AFTER_MS).expect("journal");
        let (again, _) = plan(&next(Vec::new()), &all, &ws, &h, NOW);
        assert_eq!(again[0].action, "nudge", "{again:?}");
        // without the retry line the nudge is done and the sender is told instead
        let h = History::parse(&format!("{}\n", would_line(&w[0], NOW, sw_on()))).expect("j");
        let (after, _) = plan(&next(Vec::new()), &all, &ws, &h, NOW);
        assert_eq!(after[0].action, "tell_sender");
    }

    fn h_soon_backing(journal: &str) -> bool {
        History::parse_at(journal, NOW + RETRY_AFTER_MS - 1)
            .map(|h| h.backing_off.contains("nudge:q_aaaa0001:-:a9136"))
            .unwrap_or(false)
    }

    /// A PR as GitHub has it now, for the recheck of a partial row.
    struct FakePr(Value);

    impl crate::source::DmLookup for FakePr {
        fn all(&self, _seat: &str) -> Result<Value, String> {
            Ok(json!([]))
        }
        fn pr(&self, _n: u64) -> Result<Value, String> {
            Ok(self.0.clone())
        }
    }

    /// 519d0 11-15 20:28: with next not complete, a review or fix notice whose rows came
    /// from a partial scan (not named by its errors) goes out after a live recheck; a
    /// dispatch, a row of a whole source and a bundle that carries a dispatch are held.
    #[test]
    fn a_partial_row_goes_out_only_as_a_review_or_fix_and_only_at_its_head() {
        let part = |mut r: Row| {
            r.partial = true;
            r
        };
        let all = seats(vec![
            seat("a9136"),
            seat("1ced7"),
            seat("ebb36"),
            seat("f1fae"),
        ]);
        let rows = vec![
            part(row(1, 3, "a9136")),          // partial review: out
            part(row(2, 5, "1ced7")),          // partial dispatch: held
            row(3, 3, "ebb36"),                // review of a source not partial: held
            part(row(4, 3, "f1fae")),          // partial review ...
            part(row(5, 5, "f1fae")),          // ... carrying a dispatch: held
            part(fix_row(6, "衝突", "a9136")), // partial fix: rides in a9136's dm
        ];
        let (w, _) = plan(&next(rows), &all, &[], &History::default(), NOW);
        assert_eq!(w.len(), 4, "{w:?}");
        assert_eq!(w[0].pr_checks, Some(vec![(701, H.into()), (706, H.into())]));
        assert_eq!(w[1].pr_checks, None, "a dispatch is never a partial send");
        assert_eq!(w[2].pr_checks, None, "not partial");
        assert_eq!(w[3].pr_checks, None, "a bundle with a dispatch in it");
        let (out, back) = hold_for_incomplete(&w, sw_on(), false, NOW);
        assert_eq!(out.len(), 1, "{out:?}");
        assert_eq!(out[0].to, "a9136");
        assert_eq!(back.len(), 3, "{back:?}");
        // a complete list holds nothing back
        assert_eq!(hold_for_incomplete(&w, sw_on(), true, NOW).0.len(), 4);
        // still open at the row's head: sent
        let d = Scratch::new("partial-at-head");
        let dm = FakeDm::new("delivered");
        let at = FakePr(json!({"state": "open", "head": {"sha": H}}));
        let sent = send_round(d.path(), &out, sw_on(), &all, &at, &dm, NOW);
        assert_eq!(sent[0].outcome, Ok(crate::intents::Outcome::Sent));
        assert_eq!(dm.sent.borrow().len(), 1);
        // the head moved on, or the PR closed, or it cannot be read: not sent
        for pr in [
            json!({"state": "open", "head": {"sha": "0".repeat(40)}}),
            json!({"state": "closed", "head": {"sha": H}}),
        ] {
            let d = Scratch::new("partial-moved");
            let dm = FakeDm::new("delivered");
            let sent = send_round(d.path(), &out, sw_on(), &all, &FakePr(pr), &dm, NOW);
            assert!(
                matches!(&sent[0].outcome, Ok(crate::intents::Outcome::Dropped(why)) if why.contains("#701 is no longer open")),
                "{sent:?}"
            );
            // tried again later, both rows of the dm, backed off and counted
            let lines = after_send(&out, &sent, true, NOW);
            assert_eq!(lines.len(), 2);
            let line: Value = serde_json::from_str(&lines[0]).expect("line");
            assert_eq!(line["backoff"], true, "{line}");
            assert!(dm.sent.borrow().is_empty());
        }
        let d = Scratch::new("partial-unread");
        let dm = FakeDm::new("delivered");
        let sent = send_round(d.path(), &out, sw_on(), &all, &queued("q_x"), &dm, NOW);
        assert!(
            matches!(&sent[0].outcome, Ok(crate::intents::Outcome::Dropped(why)) if why.contains("no PR lookup")),
            "{sent:?}"
        );
        assert!(dm.sent.borrow().is_empty());
        // gh down is retried at once and never counts toward giving up; a moved head does
        let line: Value =
            serde_json::from_str(&after_send(&out, &sent, true, NOW)[0]).expect("line");
        assert_eq!(line["backoff"], false, "{line}");
    }

    /// 7b81e d3ad28dc L1: after RETRY_MAX tries that did not go out the key is given up;
    /// a retry written because the round sent nothing does not count.
    #[test]
    fn a_key_is_given_up_after_three_tries_that_did_not_go_out() {
        let k = "nudge:q_aaaa0001:-:a9136";
        let mut j = String::new();
        for _ in 0..(RETRY_MAX - 1) {
            j.push_str(&retry_line(NOW, k, "busy", true));
            j.push('\n');
            j.push_str(&retry_line(NOW, k, "next not complete", false));
            j.push('\n');
        }
        let late = NOW + RETRY_AFTER_MS;
        let h = History::parse_at(&j, late).expect("j");
        assert!(h.gave_up.is_empty(), "{:?}", h.gave_up);
        j.push_str(&retry_line(NOW, k, "busy", true));
        j.push('\n');
        let h = History::parse_at(&j, late).expect("j");
        assert_eq!(h.gave_up.iter().collect::<Vec<_>>(), [k]);
    }

    /// E10: a row sent to one seat stays with it for a day — next picking another seat
    /// for the same row (same head) does not hand it on; after a day it may.
    #[test]
    fn a_row_sent_to_one_seat_is_not_handed_to_another() {
        let all = seats(vec![seat("a9136"), seat("1ced7")]);
        let (w, _) = plan(
            &next(vec![row(1, 3, "a9136")]),
            &all,
            &[],
            &History::default(),
            NOW,
        );
        assert_eq!(w[0].keys, vec![format!("open_review:#701:{H}:a9136")]);
        let j = format!("{}\n", would_line(&w[0], NOW, sw_on()));
        let h = History::parse_at(&j, NOW + 60_000).expect("j");
        let (w2, skips) = plan(&next(vec![row(1, 3, "1ced7")]), &all, &[], &h, NOW + 60_000);
        assert!(w2.is_empty(), "{w2:?}");
        assert_eq!(
            skips[0].why,
            "1ced7: went to a9136 1 min ago (stays with it)"
        );
        // the same seat again is the old rule (already done), not this one
        let (_, s3) = plan(&next(vec![row(1, 3, "a9136")]), &all, &[], &h, NOW + 60_000);
        assert!(s3[0].why.starts_with("already done"), "{s3:?}");
        // a new head is new work: anyone
        let mut r = row(1, 3, "1ced7");
        r.head = Some("2".repeat(40));
        let (w4, _) = plan(&next(vec![r]), &all, &[], &h, NOW + 60_000);
        assert_eq!(w4.len(), 1, "{w4:?}");
        // after a day it may go to another seat
        let late = NOW + crate::track::FOLLOW_FOR_MS;
        let h = History::parse_at(&j, late).expect("j");
        let (w5, _) = plan(&next(vec![row(1, 3, "1ced7")]), &all, &[], &h, late);
        assert_eq!(w5[0].to, "1ced7");
    }

    /// E10: a work dm that did not go out (retry line) did not reach its seat: the row is
    /// free again; a mark line is read as marked and changes no key.
    #[test]
    fn a_retry_frees_the_row_and_a_mark_is_only_a_mark() {
        let all = seats(vec![seat("a9136"), seat("1ced7")]);
        let (w, _) = plan(
            &next(vec![row(1, 3, "a9136")]),
            &all,
            &[],
            &History::default(),
            NOW,
        );
        let k = w[0].send_key.clone();
        let j = format!(
            "{}\n{}\n",
            would_line(&w[0], NOW, sw_on()),
            retry_line(NOW, &k, "aib dm stage preflight", true)
        );
        let h = History::parse_at(&j, NOW + 60_000).expect("j");
        assert!(h.went_to.is_empty(), "{:?}", h.went_to);
        let (w2, _) = plan(&next(vec![row(1, 3, "1ced7")]), &all, &[], &h, NOW + 60_000);
        assert_eq!(w2[0].to, "1ced7");
        let m = format!(
            "{}\n{}\n",
            would_line(&w[0], NOW, sw_on()),
            json!({"v":1,"kind":"mark","key":k,"to":"a9136","at_ms":NOW})
        );
        let h = History::parse_at(&m, NOW).expect("m");
        assert!(h.marked.contains(&k));
        assert!(h.keys.contains(&k));
        assert_eq!(h.went_to.len(), 1);
    }

    #[test]
    fn act_runs_no_outside_program() {
        // act reads files, writes its own state and sends dms only through source::Sender: no merge, push, delete of branches or
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

    // ---- E5 / E9 / E10: sending through the intents ledger (fakes only) ----

    struct FakeDm {
        sent: std::cell::RefCell<Vec<(String, String)>>,
        stage: &'static str,
        fail: bool,
    }

    impl FakeDm {
        fn new(stage: &'static str) -> FakeDm {
            FakeDm {
                sent: std::cell::RefCell::new(Vec::new()),
                stage,
                fail: false,
            }
        }
    }

    impl crate::source::Sender for FakeDm {
        fn send(
            &self,
            addr: &str,
            text: &str,
        ) -> Result<(String, String), crate::intents::SendError> {
            if self.fail {
                return Err(crate::intents::SendError::NotSent(
                    "aib dm stage preflight".into(),
                ));
            }
            // an aib dm that cannot resolve the seat (closed), as source.rs words it
            if self.stage == "resolve" {
                return Err(crate::intents::SendError::NotSent(
                    "aib dm stage resolve, exit Some(2)".into(),
                ));
            }
            let mut v = self.sent.borrow_mut();
            v.push((addr.to_owned(), text.to_owned()));
            Ok((self.stage.to_owned(), format!("q_{:08x}", v.len())))
        }
    }

    /// The `--tab all` list every recipient's lookup returns.
    struct FakeQueue(Value);

    impl crate::source::DmLookup for FakeQueue {
        fn all(&self, _seat: &str) -> Result<Value, String> {
            Ok(self.0.clone())
        }
    }

    fn queued(id: &str) -> FakeQueue {
        FakeQueue(json!([{"id": id, "status": "queued"}]))
    }

    fn waiting(id: &str, to: &str, from: &str) -> Waiting {
        Waiting {
            id: id.into(),
            to: to.into(),
            from: from.into(),
            state: "queued".into(),
            queued_min: 9,
            from_name: String::new(),
        }
    }

    fn sw_on() -> Switches {
        Switches {
            paused: false,
            nudge: true,
            open_review: true,
            dispatch: true,
            notify_fix: true,
            unblock_notify: true,
        }
    }

    fn ledger(dir: &Path) -> Vec<Value> {
        fs::read_to_string(dir.join(crate::intents::FILE))
            .unwrap_or_default()
            .lines()
            .map(|l| serde_json::from_str(l).expect("ledger line"))
            .collect()
    }

    /// 519d0 11-15 (87167 on the live journal): a dry run's `would` (switch off) is not a
    /// nudge; the first real send is the nudge, and only the ledger's `done` makes the
    /// next round tell the sender.
    #[test]
    fn a_dry_run_would_is_not_a_nudge_so_the_first_send_is_the_nudge() {
        let d = Scratch::new("dry-then-send");
        let ws = [waiting("q_aaaa0001", "a9136", "1ced7")];
        let (n, st) = (next(Vec::new()), seats(vec![seat("a9136")]));
        // a dry run, every switch off, journals the nudge it would send
        let (w, _) = plan(&n, &st, &ws, &History::default(), NOW);
        let journal = format!("{}\n", would_line(&w[0], NOW, Switches::default()));
        // the first --send round: nothing in the ledger ⇒ the nudge
        let hist = History::parse(&journal).expect("h").with_ledger("");
        let (w1, _) = plan(&n, &st, &ws, &hist, NOW + 600_000);
        assert_eq!(w1.len(), 1, "{w1:?}");
        assert_eq!(w1[0].action, "nudge", "{w1:?}");
        let (dm, q) = (FakeDm::new("delivered"), queued("q_aaaa0001"));
        let sent = send_round(d.path(), &w1, sw_on(), &st, &q, &dm, NOW + 600_000);
        assert_eq!(sent[0].outcome, Ok(crate::intents::Outcome::Sent));
        // the round after: the ledger has the nudge done ⇒ the sender is told
        let ledger = fs::read_to_string(d.path().join(crate::intents::FILE)).expect("ledger");
        let hist2 = History::parse(&journal).expect("h").with_ledger(&ledger);
        let (w2, _) = plan(&n, &st, &ws, &hist2, NOW + 1_200_000);
        assert_eq!(w2.len(), 1, "{w2:?}");
        assert_eq!(w2[0].action, "tell_sender", "{w2:?}");
    }

    /// 519d0 11-15 (87167 ACCEPT-LIVE N2): a nudge to a seat that is not there (closed, like
    /// c477a) is final, not backed off round after round: the next round tells the sender
    /// once that the seat is not there, and nothing after that.
    #[test]
    fn a_nudge_to_a_seat_that_is_not_there_is_final_and_the_sender_is_told_once() {
        let d = Scratch::new("seat-gone");
        let ws = [waiting("q_aaaa0001", "c477a", "1ced7")];
        // 5d33a 0a39f00a L1: a seat missing from the seat facts (an incomplete round) is not
        // gone, only retried
        let (n, partial) = (next(Vec::new()), seats(vec![seat("1ced7")]));
        let (w0, _) = plan(&n, &partial, &ws, &History::default().with_ledger(""), NOW);
        let (live, q) = (FakeDm::new("delivered"), queued("q_aaaa0001"));
        let sent0 = send_round(d.path(), &w0, sw_on(), &partial, &q, &live, NOW);
        let lines0 = after_send(&w0, &sent0, partial.complete, NOW);
        assert_eq!(lines0.len(), 1, "{lines0:?}");
        let retry: Value = serde_json::from_str(&lines0[0]).expect("line");
        assert_eq!(retry["kind"], "retry", "{retry}");
        assert!(!seat_gone(NO_CABIN));
        // 87167 18:0x (f88fa backed off for ever): with the roster read whole, no cabin in the
        // facts is gone like a resolve failure, so the sender is told once
        let whole = after_send(&w0, &sent0, true, NOW);
        let gone0: Value = serde_json::from_str(&whole[0]).expect("line");
        assert_eq!(gone0["kind"], "gone", "{gone0}");
        // aib dm cannot resolve the seat (closed, like c477a): gone
        let d = Scratch::new("seat-gone-2");
        let st = seats(vec![seat("c477a"), seat("1ced7"), seat(COORDINATOR)]);
        let (w, _) = plan(&n, &st, &ws, &History::default().with_ledger(""), NOW);
        assert_eq!(w[0].action, "nudge", "{w:?}");
        let closed = FakeDm::new("resolve");
        let sent = send_round(d.path(), &w, sw_on(), &st, &q, &closed, NOW);
        let lines = after_send(&w, &sent, false, NOW);
        assert_eq!(lines.len(), 1, "{lines:?}");
        let gone: Value = serde_json::from_str(&lines[0]).expect("line");
        assert_eq!(gone["kind"], "gone", "{gone}");
        assert!(seat_gone("aib dm stage resolve, exit Some(2)"));
        assert!(!seat_gone("aib dm stage preflight, exit Some(2)"));
        let dm = live;
        let mut journal = format!("{}\n", would_line(&w[0], NOW, sw_on()));
        journal.push_str(&format!("{}\n", lines[0]));
        let ledger = fs::read_to_string(d.path().join(crate::intents::FILE)).expect("ledger");
        let later = NOW + 600_000;
        let hist = History::parse_at(&journal, later)
            .expect("h")
            .with_ledger(&ledger);
        assert!(hist.backing_off.is_empty(), "{hist:?}");
        let (w2, _) = plan(&n, &st, &ws, &hist, later);
        assert_eq!(w2.len(), 1, "{w2:?}");
        assert_eq!(w2[0].action, "tell_sender");
        assert_eq!(w2[0].to, format!("1ced7,{COORDINATOR}"));
        assert!(w2[0].first_line.contains("收件席不在"), "{w2:?}");
        // told once: the round after the telling is done plans nothing
        let sent2 = send_round(d.path(), &w2, sw_on(), &st, &q, &dm, later);
        assert!(
            sent2
                .iter()
                .all(|s| s.outcome == Ok(crate::intents::Outcome::Sent)),
            "{sent2:?}"
        );
        let ledger2 = fs::read_to_string(d.path().join(crate::intents::FILE)).expect("ledger");
        let hist3 = History::parse_at(&journal, later + 600_000)
            .expect("h")
            .with_ledger(&ledger2);
        let (w3, _) = plan(&n, &st, &ws, &hist3, later + 600_000);
        assert!(w3.is_empty(), "{w3:?}");
    }

    /// E8 (519d0 11-15: 7b81e and b433b each got two nudges in one round): nudges to one
    /// seat in one round are one dm, and every dm it carried counts as nudged.
    #[test]
    fn two_nudges_to_one_seat_in_one_round_are_one_dm_and_both_count_as_sent() {
        let d = Scratch::new("merge-nudges");
        let ws = [
            waiting("q_aaaa0001", "a9136", "1ced7"),
            waiting("q_aaaa0002", "a9136", "b433b"),
        ];
        let (n, st) = (next(Vec::new()), seats(vec![seat("a9136")]));
        let (w, _) = plan(&n, &st, &ws, &History::default().with_ledger(""), NOW);
        assert_eq!(w.len(), 1, "{w:?}");
        assert_eq!(w[0].first_line, "提醒: 你有 2 封還沒收的信 → a9136");
        assert_eq!(
            w[0].keys,
            ["nudge:q_aaaa0001:-:a9136", "nudge:q_aaaa0002:-:a9136"]
        );
        assert_eq!(w[0].body.len(), 2, "{w:?}");
        let (dm, q) = (FakeDm::new("delivered"), queued("q_aaaa0001"));
        send_round(d.path(), &w, sw_on(), &st, &q, &dm, NOW);
        assert_eq!(dm.sent.borrow().len(), 1);
        let ledger = fs::read_to_string(d.path().join(crate::intents::FILE)).expect("ledger");
        let (w2, _) = plan(&n, &st, &ws, &History::default().with_ledger(&ledger), NOW);
        // both nudged ⇒ each sender told (two senders, so two tellings)
        assert_eq!(w2.len(), 2, "{w2:?}");
        assert!(w2.iter().all(|w| w.action == "tell_sender"), "{w2:?}");
    }

    #[test]
    fn a_nudge_is_sent_once_through_the_ledger() {
        let d = Scratch::new("send-once");
        let (w, _) = plan(
            &next(Vec::new()),
            &seats(vec![seat("a9136")]),
            &[waiting("q_aaaa0001", "a9136", "1ced7")],
            &History::default(),
            NOW,
        );
        let dm = FakeDm::new("delivered");
        let q = queued("q_aaaa0001");
        let sent = send_round(
            d.path(),
            &w,
            sw_on(),
            &seats(vec![seat("a9136")]),
            &q,
            &dm,
            NOW,
        );
        assert_eq!(sent.len(), 1);
        assert_eq!(sent[0].outcome, Ok(crate::intents::Outcome::Sent));
        assert_eq!(sent[0].key, "nudge:q_aaaa0001:-:a9136");
        {
            let s = dm.sent.borrow();
            assert_eq!(s.len(), 1);
            assert_eq!(s[0].0, "dab9/a9136");
            assert!(s[0].1.starts_with("提醒: "), "{}", s[0].1);
        }
        // the same round again (act killed after sending, before its journal): not sent twice
        let again = send_round(
            d.path(),
            &w,
            sw_on(),
            &seats(vec![seat("a9136")]),
            &q,
            &dm,
            NOW,
        );
        assert_eq!(again[0].outcome, Ok(crate::intents::Outcome::AlreadyDone));
        assert_eq!(dm.sent.borrow().len(), 1);
    }

    #[test]
    fn a_switch_that_is_off_sends_nothing() {
        let d = Scratch::new("send-off");
        let (w, _) = plan(
            &next(Vec::new()),
            &seats(vec![seat("a9136")]),
            &[waiting("q_aaaa0001", "a9136", "1ced7")],
            &History::default(),
            NOW,
        );
        let dm = FakeDm::new("delivered");
        let sent = send_round(
            d.path(),
            &w,
            Switches::default(),
            &seats(vec![seat("a9136")]),
            &queued("q_aaaa0001"),
            &dm,
            NOW,
        );
        assert!(sent.is_empty());
        assert!(dm.sent.borrow().is_empty());
        assert!(ledger(d.path()).is_empty(), "no intent written");
    }

    /// S79-sender (519d0 11-16 08:46): every dm ends with who turned the switches on (the last
    /// `by` in switches.jsonl, self-reported) and the intent key; no ledger or a `by` that is
    /// not a seat code says `?`, never a name it did not read.
    #[test]
    fn every_dm_ends_with_the_seat_that_turned_act_on_and_the_intent_key() {
        let d = Scratch::new("sent-by");
        assert_eq!(
            sent_by(d.path(), "nudge:q_1:-:a9136"),
            "— 代發:自動推進,開關 ? · 意圖 nudge:q_1:-:a9136(核對 drive/intents.jsonl)"
        );
        fs::write(
            d.path().join("switches.jsonl"),
            "{\"by\":\"1ced7\",\"name\":\"nudge\"}\nnot json\n{\"by\":\"519d0\",\"name\":\"paused\"}\n",
        )
        .expect("write");
        assert!(sent_by(d.path(), "k").contains("開關 519d0(自報)"));
        fs::write(d.path().join("switches.jsonl"), "{\"by\":\"Andy\"}\n").expect("write");
        assert!(sent_by(d.path(), "k").contains("開關 ?"));
    }

    #[test]
    fn review_dispatch_dispatch_and_fix_requests_are_sent() {
        let d = Scratch::new("send-e67");
        let fix = fix_row(3, "#757 修:跟 main 衝突", "ebb36");
        let all = seats(vec![seat("a9136"), seat("ebb36"), seat("c477a")]);
        let (w, _) = plan(
            &next(vec![row(1, 3, "a9136"), row(2, 5, "c477a"), fix]),
            &all,
            &[],
            &History::default(),
            NOW,
        );
        let mut actions: Vec<&str> = w.iter().map(|x| x.action.as_str()).collect();
        actions.sort_unstable();
        assert_eq!(actions, ["dispatch", "notify_fix", "open_review"]);
        let dm = FakeDm::new("delivered");
        let sent = send_round(d.path(), &w, sw_on(), &all, &FakeQueue(json!([])), &dm, NOW);
        let keys: Vec<&str> = sent.iter().map(|s| s.key.as_str()).collect();
        assert_eq!(
            keys,
            [
                format!("open_review:#701:{H}:a9136"),
                format!("dispatch:#702:{H}:c477a"),
                format!("notify_fix:#703:{H}:ebb36")
            ]
        );
        assert!(sent
            .iter()
            .all(|s| s.outcome == Ok(crate::intents::Outcome::Sent)));
        let s = dm.sent.borrow();
        assert_eq!(s[0].0, "dab9/a9136");
        assert!(s[0].1.starts_with("派工: 審 件1 → a9136\n"), "{}", s[0].1);
        // E7: first line 「派工: 事 → 席」, then the node, the evidence, why him, why now
        let (body, last) = s[1].1.rsplit_once('\n').expect("a last line");
        assert_eq!(
            body,
            format!(
                "派工: 件2 → c477a\n節點:S2\n證據:PR #702 · 頭 {H} · 資料時間 2026-11-15T00:00:00.000Z\n為什麼是你:交換審查\n為什麼是現在:收據綠"
            )
        );
        assert_eq!(
            last,
            sent_by(d.path(), &sent[1].key),
            "S79-sender: the last line"
        );
        // IFACE r10 1: the fix request goes to the author, first line 「派工: 修 <PR>:<原因> → 席」
        assert_eq!(s[2].0, "dab9/ebb36");
        assert!(
            s[2].1.starts_with("派工: 修 #757:跟 main 衝突 → ebb36\n"),
            "{}",
            s[2].1
        );
    }

    /// IFACE r10 1: a fix request needs the notify_fix switch (dispatch or open_review on is
    /// not enough), the pause holds it back, an infrastructure red goes to the merge queue,
    /// and what the dry run journals as would_send is exactly what --send sends.
    #[test]
    fn a_fix_request_goes_out_only_with_its_switch_and_as_the_dry_run_says() {
        let mut infra = fix_row(2, "#748 修:macOS 格 Set up runner 失敗", "043dd");
        infra.cause = Some("infra".into());
        let mut mq = seat(MERGE_QUEUE);
        mq.role = "ops".into();
        let all = seats(vec![seat("ebb36"), seat("043dd"), mq]);
        let (w, _) = plan(
            &next(vec![fix_row(1, "#757 修:跟 main 衝突", "ebb36"), infra]),
            &all,
            &[],
            &History::default(),
            NOW,
        );
        assert_eq!(w.len(), 2, "{w:?}");
        let only_fix = Switches {
            notify_fix: true,
            ..Switches::default()
        };
        let all_but_fix = Switches {
            notify_fix: false,
            ..sw_on()
        };
        let paused = Switches {
            paused: true,
            ..sw_on()
        };
        for (sw, want) in [
            (Switches::default(), 0),
            (all_but_fix, 0),
            (paused, 0),
            (only_fix, 2),
            (sw_on(), 2),
        ] {
            let d = Scratch::new("send-fix-sw");
            let dm = FakeDm::new("delivered");
            let sent = send_round(d.path(), &w, sw, &all, &FakeQueue(json!([])), &dm, NOW);
            let says: Vec<String> = w
                .iter()
                .filter(|x| {
                    let v: Value = serde_json::from_str(&would_line(x, NOW, sw)).expect("json");
                    v["would_send"] == json!(true)
                })
                .map(|x| x.send_key.clone())
                .collect();
            let went: Vec<String> = sent.iter().map(|s| s.key.clone()).collect();
            assert_eq!(went.len(), want, "{sw:?}: {went:?}");
            assert_eq!(went, says, "{sw:?}");
            if want == 2 {
                let s = dm.sent.borrow();
                assert_eq!(s[0].0, "dab9/ebb36");
                assert_eq!(s[1].0, format!("dab9/{MERGE_QUEUE}"));
                assert!(
                    s[1].1.starts_with(&format!(
                        "派工: 重跑 #748:macOS 格 Set up runner 失敗 → {MERGE_QUEUE}\n"
                    )),
                    "{}",
                    s[1].1
                );
            }
        }
    }

    /// E6 / E7: each needs its own switch, the pause holds both back, and what the dry run
    /// journals as would_send is exactly what --send sends.
    #[test]
    fn review_dispatch_and_dispatch_go_out_only_as_the_dry_run_says() {
        let all = seats(vec![seat("a9136"), seat("c477a")]);
        let (w, _) = plan(
            &next(vec![row(1, 3, "a9136"), row(2, 5, "c477a")]),
            &all,
            &[],
            &History::default(),
            NOW,
        );
        let only_dispatch = Switches {
            dispatch: true,
            ..Switches::default()
        };
        let paused = Switches {
            paused: true,
            ..sw_on()
        };
        for (sw, want) in [
            (Switches::default(), 0),
            (only_dispatch, 1),
            (paused, 0),
            (sw_on(), 2),
        ] {
            let d = Scratch::new("send-e67-sw");
            let dm = FakeDm::new("delivered");
            let sent = send_round(d.path(), &w, sw, &all, &FakeQueue(json!([])), &dm, NOW);
            let says: Vec<String> = w
                .iter()
                .filter(|x| {
                    let v: Value = serde_json::from_str(&would_line(x, NOW, sw)).expect("json");
                    v["would_send"] == json!(true)
                })
                .map(|x| x.send_key.clone())
                .collect();
            let went: Vec<String> = sent.iter().map(|s| s.key.clone()).collect();
            assert_eq!(went.len(), want, "{sw:?}: {went:?}");
            assert_eq!(went, says, "{sw:?}");
        }
    }

    #[test]
    fn a_nudge_whose_dm_was_taken_meanwhile_is_dropped() {
        let d = Scratch::new("send-recheck");
        let (w, _) = plan(
            &next(Vec::new()),
            &seats(vec![seat("a9136")]),
            &[waiting("q_aaaa0001", "a9136", "1ced7")],
            &History::default(),
            NOW,
        );
        let dm = FakeDm::new("delivered");
        let taken = FakeQueue(json!([{"id": "q_aaaa0001", "status": "read"}]));
        let sent = send_round(
            d.path(),
            &w,
            sw_on(),
            &seats(vec![seat("a9136")]),
            &taken,
            &dm,
            NOW,
        );
        assert!(
            matches!(&sent[0].outcome, Ok(crate::intents::Outcome::Dropped(why)) if why.contains("no longer queued")),
            "{:?}",
            sent[0].outcome
        );
        assert!(dm.sent.borrow().is_empty());
        // a seat with no cabin is dropped the same way, never sent to a bare seat code
        let mut bare = seat("a9136");
        bare.cabin = String::new();
        let d2 = Scratch::new("send-nocabin");
        let sent = send_round(
            d2.path(),
            &w,
            sw_on(),
            &seats(vec![bare]),
            &queued("q_aaaa0001"),
            &dm,
            NOW,
        );
        assert!(matches!(
            &sent[0].outcome,
            Ok(crate::intents::Outcome::Dropped(why)) if why.contains("no cabin")
        ));
        assert!(dm.sent.borrow().is_empty());
    }

    #[test]
    fn a_seat_with_a_wrap_up_list_gets_only_the_wrap_up() {
        // E9: first is not empty ⇒ the wrap-up goes out, the new work does not
        let d = Scratch::new("send-wrap");
        let mut busy = seat("c477a");
        busy.first = vec!["#760 收尾".into()];
        let all = seats(vec![busy]);
        let (w, _) = plan(
            &next(vec![row(2, 5, "c477a")]),
            &all,
            &[],
            &History::default(),
            NOW,
        );
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].action, "wrap_up");
        let dm = FakeDm::new("delivered");
        let sent = send_round(d.path(), &w, sw_on(), &all, &FakeQueue(json!([])), &dm, NOW);
        assert_eq!(sent[0].outcome, Ok(crate::intents::Outcome::Sent));
        let s = dm.sent.borrow();
        assert_eq!(s.len(), 1);
        assert!(
            s[0].1
                .starts_with("派工: 先做收尾 → c477a\n先做收尾:#760 收尾"),
            "{}",
            s[0].1
        );
    }

    /// 519d0 11-16 00:32: dispatch off (so no wrap-up goes out), notify_fix on, the seat has
    /// wrap-up left ⇒ its fix request still goes out, alone.
    #[test]
    fn a_wrap_up_that_cannot_go_out_does_not_hold_back_the_fix_request() {
        let d = Scratch::new("send-wrap-off");
        let mut busy = seat("043dd");
        busy.first = vec!["plan_node".into(), "memory_tidy".into()];
        let all = seats(vec![busy]);
        let only_fix = Switches {
            notify_fix: true,
            ..Switches::default()
        };
        let n = next(vec![fix_row(1, "#757 修:跟 main 衝突", "043dd")]);
        let wrap_out = goes_out(only_fix, "wrap_up", true);
        assert!(!wrap_out, "wrap_up rides the dispatch switch");
        let (w, skips) = plan_sw(&n, &all, &[], &History::default(), NOW, wrap_out);
        assert_eq!(w.len(), 1, "{w:?} {skips:?}");
        assert_eq!(w[0].action, "notify_fix");
        let dm = FakeDm::new("delivered");
        let sent = send_round(
            d.path(),
            &w,
            only_fix,
            &all,
            &FakeQueue(json!([])),
            &dm,
            NOW,
        );
        assert_eq!(sent.len(), 1, "{sent:?}");
        assert_eq!(dm.sent.borrow()[0].0, "dab9/043dd");
        // with dispatch on the wrap-up goes out first, as before
        let (w, _) = plan_sw(&n, &all, &[], &History::default(), NOW, true);
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].action, "wrap_up");
    }

    #[test]
    fn the_done_line_keeps_the_delivery_stage_apart_from_read_and_taken() {
        // E10: act records what aib dm said (delivered / sort = queued) and the dm id; read
        // and taken are the recipient's, not written here
        let d = Scratch::new("send-stage");
        let (w, _) = plan(
            &next(Vec::new()),
            &seats(vec![seat("a9136")]),
            &[waiting("q_aaaa0001", "a9136", "1ced7")],
            &History::default(),
            NOW,
        );
        let dm = FakeDm::new("sort-queued");
        send_round(
            d.path(),
            &w,
            sw_on(),
            &seats(vec![seat("a9136")]),
            &queued("q_aaaa0001"),
            &dm,
            NOW,
        );
        let l = ledger(d.path());
        let done = l
            .iter()
            .find(|v| v["kind"] == json!("done"))
            .expect("done line");
        assert_eq!(done["stage"], json!("sort-queued"));
        assert_eq!(done["dm_id"], json!("q_00000001"));
        assert!(done.get("read").is_none() && done.get("taken").is_none());
        let intent = l
            .iter()
            .find(|v| v["kind"] == json!("intent"))
            .expect("intent line");
        assert_eq!(intent["action"], json!("nudge"));
        assert_eq!(intent["to"], json!("a9136"));
    }

    #[test]
    fn telling_the_sender_goes_once_per_recipient() {
        let d = Scratch::new("send-tell");
        let mut hist = History::default();
        hist.keys.insert("nudge:q_aaaa0001:-:a9136".into());
        let all = seats(vec![seat("a9136"), seat("1ced7"), seat(COORDINATOR)]);
        let (w, _) = plan(
            &next(Vec::new()),
            &all,
            &[waiting("q_aaaa0001", "a9136", "1ced7")],
            &hist,
            NOW,
        );
        assert_eq!(w[0].action, "tell_sender");
        let dm = FakeDm::new("delivered");
        let sent = send_round(d.path(), &w, sw_on(), &all, &queued("q_aaaa0001"), &dm, NOW);
        let keys: Vec<String> = sent.iter().map(|s| s.key.clone()).collect();
        assert_eq!(
            keys,
            [
                "tell_sender:q_aaaa0001:-:1ced7@1ced7".to_owned(),
                format!("tell_sender:q_aaaa0001:-:1ced7@{COORDINATOR}")
            ]
        );
        let addrs: Vec<String> = dm.sent.borrow().iter().map(|s| s.0.clone()).collect();
        assert_eq!(
            addrs,
            ["dab9/1ced7".to_owned(), format!("dab9/{COORDINATOR}")]
        );
        // a failed send is abandoned, not done, and its key is not sent again
        let d2 = Scratch::new("send-fail");
        let mut broken = FakeDm::new("delivered");
        broken.fail = true;
        let sent = send_round(
            d2.path(),
            &w,
            sw_on(),
            &all,
            &queued("q_aaaa0001"),
            &broken,
            NOW,
        );
        assert!(sent
            .iter()
            .all(|s| matches!(&s.outcome, Ok(crate::intents::Outcome::SendFailed(_)))));
    }
}
