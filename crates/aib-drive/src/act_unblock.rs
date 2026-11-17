//! `unblock_notify` (S79-unblock u-5, DECISION-council-unblock-519d0-1115 §3): act turns
//! the rows of `<root>/drive/unblock.json` (written by `aib-drive unblock`, u-1) into
//! 「卡點: <碼> <事> → <席>」 dms.
//!
//! - act only reads the file, it never detects anything itself; a file whose `as_of_ms` is
//!   older than [`FRESH_MS`] (or missing) holds back every row of the round.
//! - the dedupe key is `unblock_notify:<code>:<subject>:<fingerprint>:<owner>`: the same evidence is
//!   told once, new evidence (a new fingerprint) is told again; time alone never repeats it.
//! - the seat rules of act apply (alive, unread ≤ [`crate::act::UNREAD_MAX`], not on deck);
//!   several rows for one seat in one round go in one dm.
//! - the switch `unblock_notify` (off by default) lets it out and a pause holds it back.
//! - dry run first: `unblock_notify` is not in [`crate::act::SENDABLE`], so it is only
//!   journaled. Until it is, a would here never counts toward the 30-minute seat gap and
//!   never rides along in another dm, so a dry row cannot hold back or leak into real work.

use crate::act::{History, SeatsOut, Skip, Would, UNREAD_MAX};
use serde::Deserialize;

/// The action (and switch) name.
pub const ACTION: &str = "unblock_notify";
/// The file act reads, under `<root>/drive/`.
pub const FILE: &str = "unblock.json";
/// unblock.json older than this (two of next's 10-minute rounds) is not acted on.
pub const FRESH_MS: i64 = 20 * 60 * 1000;
/// The rank skips of this file carry (next's rows start at 1).
pub const SKIP_RANK: u64 = 0;

#[derive(Debug, Clone, Deserialize, Default)]
pub struct UnblockOut {
    #[serde(default, deserialize_with = "crate::act::nd")]
    pub schema: String,
    #[serde(default, deserialize_with = "crate::act::nd")]
    pub as_of_ms: Option<i64>,
    #[serde(default, deserialize_with = "crate::act::nd")]
    pub rows: Vec<UnblockRow>,
}

/// One row (IFACE-unblock-json-v2-a9136-1115 r0).
#[derive(Debug, Clone, Deserialize, Default)]
pub struct UnblockRow {
    /// `<code>:<subject>`, the same whatever the evidence; when given it must say exactly that.
    #[serde(default, deserialize_with = "crate::act::nd")]
    pub id: String,
    /// `U1` … `U22`
    #[serde(default, deserialize_with = "crate::act::nd")]
    pub code: String,
    /// One line: what is stuck.
    #[serde(default, deserialize_with = "crate::act::nd")]
    pub what: String,
    /// The seat that is to clear it now (5 hex), the one told; `""` = not known (not told).
    /// `seat` was the v1 name.
    #[serde(default, alias = "seat", deserialize_with = "crate::act::nd")]
    pub owner: String,
    /// What it is about: `pr-785`, `host-lin`, `node-S79-p06`.
    #[serde(default, deserialize_with = "crate::act::nd")]
    pub subject: String,
    /// Changes when the evidence changes.
    #[serde(default, deserialize_with = "crate::act::nd")]
    pub fingerprint: String,
    #[serde(default, deserialize_with = "crate::act::nd")]
    pub evidence: String,
    /// How many nodes / PRs it holds up (aib-drive works it out): the bigger goes first.
    #[serde(default, deserialize_with = "crate::act::nd")]
    pub blocks: u32,
    /// The seats that may take it over if it is escalated: shown, never sent to (escalating
    /// is v-2b's, not act's).
    #[serde(default, deserialize_with = "crate::act::nd")]
    pub candidates: Vec<String>,
    /// When this row's facts were read; older than [`FRESH_MS`] = stale, not told.
    #[serde(default, deserialize_with = "crate::act::nd")]
    pub as_of_ms: Option<i64>,
}

fn candidates_line(c: &[String]) -> Option<String> {
    let ok: Vec<&str> = c
        .iter()
        .map(String::as_str)
        .filter(|s| seat_ok(s))
        .collect();
    if ok.is_empty() {
        None
    } else {
        Some(format!("升級時可接(只列出,不是派工):{}", ok.join("、")))
    }
}

/// Whether this version really sends `unblock_notify` with `--send`.
pub fn sends() -> bool {
    crate::act::SENDABLE.contains(&ACTION)
}

fn code_ok(c: &str) -> bool {
    c.len() >= 2 && c.len() <= 4 && c.starts_with('U') && c[1..].bytes().all(|b| b.is_ascii_digit())
}

fn seat_ok(s: &str) -> bool {
    s.len() == 5 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// A key part: not empty, one line, no `:` (the key is split on it) and no `@` (a key sent
/// to several seats is `key@seat`).
fn part_ok(s: &str) -> bool {
    !s.trim().is_empty() && !s.contains([':', '@', '\n', '\r'])
}

pub fn key(r: &UnblockRow) -> String {
    format!(
        "{ACTION}:{}:{}:{}:{}",
        r.code, r.subject, r.fingerprint, r.owner
    )
}

fn one_line(s: &str) -> String {
    s.split(['\n', '\r'])
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

fn first_line(r: &UnblockRow) -> String {
    format!("卡點: {} {} → {}", r.code, one_line(&r.what), r.owner)
}

/// This round's `unblock_notify` dms, added to `out` (after [`crate::act::plan`]); rows not
/// told go to `skips` with rank [`SKIP_RANK`]. `None` = no unblock.json: nothing to do.
pub fn plan(
    u: Option<&UnblockOut>,
    seats: &SeatsOut,
    hist: &History,
    now_ms: i64,
    out: &mut Vec<Would>,
    skips: &mut Vec<Skip>,
) {
    let Some(u) = u else {
        return;
    };
    let mut skip = |why: String| {
        skips.push(Skip {
            rank: SKIP_RANK,
            why: format!("{ACTION}: {why}"),
        })
    };
    let age = u.as_of_ms.map(|t| now_ms.saturating_sub(t));
    match age {
        Some(a) if (0..=FRESH_MS).contains(&a) => {}
        Some(a) if a < 0 => {
            skip(format!("{FILE} as_of is {} s in the future", -a / 1000));
            return;
        }
        Some(a) => {
            skip(format!(
                "{FILE} is {} min old (over {} min), {} rows held back",
                a / 60_000,
                FRESH_MS / 60_000,
                u.rows.len()
            ));
            return;
        }
        None => {
            skip(format!(
                "{FILE} has no as_of_ms, {} rows held back",
                u.rows.len()
            ));
            return;
        }
    }
    let live = sends();
    // the row that holds up more goes first (stable: equal ones keep the file's order)
    let mut rows: Vec<&UnblockRow> = u.rows.iter().collect();
    rows.sort_by_key(|r| std::cmp::Reverse(r.blocks));
    for r in rows {
        if !code_ok(&r.code) || !part_ok(&r.subject) || !part_ok(&r.fingerprint) {
            skip(format!(
                "a row is not code U<n> / subject / fingerprint without : @ ({} {})",
                r.code, r.subject
            ));
            continue;
        }
        let id = format!("{}:{}", r.code, r.subject);
        if !r.id.is_empty() && r.id != id {
            skip(format!("id {} is not {id}", r.id));
            continue;
        }
        if r.owner.is_empty() {
            skip(format!("{id}: no owner"));
            continue;
        }
        if !seat_ok(&r.owner) {
            skip(format!("{id}: owner {} is not a 5-hex seat", r.owner));
            continue;
        }
        // IFACE v2: every row carries its own time; a source that stopped writing goes stale
        // row by row, even inside a fresh file
        match r.as_of_ms.map(|t| now_ms.saturating_sub(t)) {
            Some(age) if (0..=FRESH_MS).contains(&age) => {}
            Some(age) => {
                skip(format!("{id}: row data {} min old (stale)", age / 60_000));
                continue;
            }
            None => {
                skip(format!("{id}: row has no as_of_ms"));
                continue;
            }
        }
        let to = r.owner.as_str();
        let Some(seat) = seats.seats.iter().find(|s| s.seat == to) else {
            skip(format!("{to}: no seat facts"));
            continue;
        };
        if seat.alive.v != "yes" {
            skip(format!("{to}: alive {}", seat.alive.v));
            continue;
        }
        if seat.capacity.on_deck == Some(true) {
            skip(format!("{to}: on deck in the merge queue"));
            continue;
        }
        match seat.capacity.unread {
            Some(n) if n <= UNREAD_MAX => {}
            Some(n) => {
                skip(format!("{to}: {n} unread"));
                continue;
            }
            None => {
                skip(format!("{to}: unread unknown"));
                continue;
            }
        }
        let k = key(r);
        // live: told = the intents ledger has it done (a switch-off round does not count);
        // dry: the journal's would line is enough, so a dry row is journaled once
        let told = if live {
            hist.told(&k)
        } else {
            hist.keys.contains(&k)
        };
        if told {
            skip(format!("already told ({k})"));
            continue;
        }
        let mut body = vec![format!("對象:{}", r.subject)];
        if !r.evidence.trim().is_empty() {
            body.push(format!("證據:{}", one_line(&r.evidence)));
        }
        if r.blocks > 0 {
            body.push(format!("擋住:{} 個節點 / PR", r.blocks));
        }
        if let Some(n) = candidates_line(&r.candidates) {
            body.push(n);
        }
        // one dm per seat per round: a second row for the seat goes in as 「其後」, in the
        // seat's unblock dm, or (live only) in its work dm of this round
        if let Some(w) = out.iter_mut().find(|w| {
            w.to == to
                && (w.action == ACTION || (live && crate::act::WORK.contains(&w.action.as_str())))
        }) {
            w.body.push(format!("其後:{}", first_line(r)));
            w.keys.push(k);
            continue;
        }
        if live {
            if let Some(&last) = hist.last_sent_ms.get(to) {
                if now_ms - last < crate::act::SEAT_GAP_MIN * 60_000 {
                    skip(format!(
                        "{to}: sent {} min ago (one per {} min)",
                        (now_ms - last) / 60_000,
                        crate::act::SEAT_GAP_MIN
                    ));
                    continue;
                }
            }
        }
        out.push(Would {
            action: ACTION.into(),
            to: to.to_owned(),
            first_line: first_line(r),
            body,
            send_key: k.clone(),
            keys: vec![k],
            throttled: live,
            ranks: Vec::new(),
            dm: None,
            pr_checks: None,
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::act::{Capacity, Fact, Seat};

    const NOW: i64 = 1_794_700_800_000;

    fn seat(s: &str) -> Seat {
        Seat {
            seat: s.into(),
            cabin: "dab9".into(),
            role: "dev".into(),
            alive: Fact { v: "yes".into() },
            capacity: Capacity {
                unread: Some(0),
                on_deck: Some(false),
            },
            dispatchable: "yes".into(),
            size: "slice".into(),
            first: Vec::new(),
        }
    }

    fn row(code: &str, seat: &str, subject: &str, fp: &str) -> UnblockRow {
        UnblockRow {
            code: code.into(),
            what: "next 不完整:refresh 失敗".into(),
            owner: seat.into(),
            subject: subject.into(),
            fingerprint: fp.into(),
            evidence: "drive/refresh.log".into(),
            blocks: 0,
            candidates: Vec::new(),
            as_of_ms: Some(NOW),
            id: String::new(),
        }
    }

    fn file(rows: Vec<UnblockRow>, age_ms: i64) -> UnblockOut {
        UnblockOut {
            schema: "aib-drive.unblock.v1".into(),
            as_of_ms: Some(NOW - age_ms),
            rows,
        }
    }

    fn run(u: &UnblockOut, seats: Vec<Seat>, hist: &History) -> (Vec<Would>, Vec<Skip>) {
        let (mut w, mut s) = (Vec::new(), Vec::new());
        plan(
            Some(u),
            &SeatsOut {
                seats,
                complete: true,
            },
            hist,
            NOW,
            &mut w,
            &mut s,
        );
        (w, s)
    }

    #[test]
    fn a_fresh_row_is_one_dm_with_the_first_line_and_the_evidence() {
        let u = file(vec![row("U1", "a9136", "S79-unblock", "ab12")], 60_000);
        let (w, s) = run(&u, vec![seat("a9136")], &History::default());
        assert!(s.is_empty(), "{s:?}");
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].action, ACTION);
        assert_eq!(w[0].first_line, "卡點: U1 next 不完整:refresh 失敗 → a9136");
        assert_eq!(w[0].keys, vec!["unblock_notify:U1:S79-unblock:ab12:a9136"]);
        assert!(w[0].body.iter().any(|b| b == "證據:drive/refresh.log"));
    }

    #[test]
    fn a_stale_or_undated_file_holds_back_every_row() {
        let rows = vec![row("U1", "a9136", "x", "1"), row("U4", "f1fae", "y", "2")];
        let seats = vec![seat("a9136"), seat("f1fae")];
        let (w, s) = run(
            &file(rows.clone(), FRESH_MS + 1),
            seats.clone(),
            &History::default(),
        );
        assert!(w.is_empty());
        assert_eq!(s.len(), 1, "one skip for the whole file: {s:?}");
        assert!(s[0].why.contains("2 rows held back"), "{s:?}");
        let mut undated = file(rows, 0);
        undated.as_of_ms = None;
        let (w, s) = run(&undated, seats, &History::default());
        assert!(w.is_empty());
        assert!(s[0].why.contains("no as_of_ms"), "{s:?}");
        // exactly at the limit is still fresh
        let (w, _) = run(
            &file(vec![row("U1", "a9136", "x", "1")], FRESH_MS),
            vec![seat("a9136")],
            &History::default(),
        );
        assert_eq!(w.len(), 1);
    }

    #[test]
    fn the_same_evidence_is_told_once_and_new_evidence_again() {
        let seats = vec![seat("a9136")];
        let (w, _) = run(
            &file(vec![row("U1", "a9136", "x", "1")], 0),
            seats.clone(),
            &History::default(),
        );
        let mut hist = History::default();
        hist.keys.extend(w[0].keys.iter().cloned());
        let (w2, s2) = run(
            &file(vec![row("U1", "a9136", "x", "1")], 0),
            seats.clone(),
            &hist,
        );
        assert!(w2.is_empty());
        assert!(s2[0].why.contains("already told"), "{s2:?}");
        let (w3, _) = run(&file(vec![row("U1", "a9136", "x", "2")], 0), seats, &hist);
        assert_eq!(w3.len(), 1, "a new fingerprint is told again");
    }

    #[test]
    fn rows_for_one_seat_go_in_one_dm() {
        let u = file(
            vec![row("U1", "a9136", "x", "1"), row("U22", "a9136", "y", "2")],
            0,
        );
        let (w, _) = run(&u, vec![seat("a9136")], &History::default());
        assert_eq!(w.len(), 1);
        assert_eq!(w[0].keys.len(), 2);
        assert!(
            w[0].body.iter().any(|b| b.starts_with("其後:卡點: U22")),
            "{:?}",
            w[0].body
        );
    }

    #[test]
    fn the_row_that_holds_up_more_goes_first_and_candidates_are_only_listed() {
        let mut small = row("U4", "a9136", "x", "1");
        small.blocks = 1;
        let mut big = row("U13", "519d0", "node-S79-p04", "2");
        big.blocks = 5;
        big.candidates = vec!["043dd".into(), "a9136".into(), "not a seat".into()];
        let (w, _) = run(
            &file(vec![small, big], 0),
            vec![seat("a9136"), seat("519d0")],
            &History::default(),
        );
        assert_eq!(w[0].to, "519d0", "5 before 1: {w:?}");
        assert!(
            w[0].body.iter().any(|b| b == "擋住:5 個節點 / PR"),
            "{:?}",
            w[0].body
        );
        assert!(
            w[0].body
                .iter()
                .any(|b| b == "升級時可接(只列出,不是派工):043dd、a9136"),
            "{:?}",
            w[0].body
        );
        assert_eq!(w.len(), 2, "candidates are never sent to: {w:?}");
        assert_eq!(
            w[0].action, ACTION,
            "a candidate list is a notice, never a dispatch"
        );
    }

    #[test]
    fn a_v2_row_needs_its_own_fresh_time_an_owner_and_a_matching_id() {
        let fresh = row("U14", "a9136", "pr-785", "dirty-1");
        let mut stale = row("U14", "a9136", "pr-786", "dirty-2");
        stale.as_of_ms = Some(NOW - FRESH_MS - 1);
        let mut undated = row("U14", "a9136", "pr-787", "dirty-3");
        undated.as_of_ms = None;
        let mut no_owner = row("U14", "a9136", "pr-788", "dirty-4");
        no_owner.owner = String::new();
        let mut bad_id = row("U14", "a9136", "pr-789", "dirty-5");
        bad_id.id = "U14:pr-000".into();
        let mut good_id = row("U14", "a9136", "pr-790", "dirty-6");
        good_id.id = "U14:pr-790".into();
        let (w, s) = run(
            &file(vec![fresh, stale, undated, no_owner, bad_id, good_id], 0),
            vec![seat("a9136")],
            &History::default(),
        );
        assert_eq!(w.len(), 1, "{s:?}");
        assert_eq!(
            w[0].keys.len(),
            2,
            "the fresh row and the one with its id: {w:?}"
        );
        let why: Vec<&str> = s.iter().map(|s| s.why.as_str()).collect();
        assert!(why[0].contains("stale"), "{why:?}");
        assert!(why[1].contains("no as_of_ms"), "{why:?}");
        assert!(why[2].contains("no owner"), "{why:?}");
        assert!(why[3].contains("is not U14:pr-789"), "{why:?}");
        // v1 files named the owner `seat`
        let v1: UnblockRow =
            serde_json::from_str(r#"{"code":"U1","seat":"a9136","subject":"x","fingerprint":"1"}"#)
                .expect("v1 row");
        assert_eq!(v1.owner, "a9136");
    }

    #[test]
    fn the_seat_rules_of_act_apply() {
        let mut busy = seat("aaaa1");
        busy.capacity.unread = Some(UNREAD_MAX + 1);
        let mut deck = seat("aaaa2");
        deck.capacity.on_deck = Some(true);
        let mut dead = seat("aaaa3");
        dead.alive.v = "no".into();
        let mut unknown = seat("aaaa4");
        unknown.capacity.unread = None;
        let rows = ["aaaa1", "aaaa2", "aaaa3", "aaaa4", "aaaa5"]
            .iter()
            .map(|s| row("U9", s, "x", "1"))
            .collect();
        let (w, s) = run(
            &file(rows, 0),
            vec![busy, deck, dead, unknown],
            &History::default(),
        );
        assert!(w.is_empty(), "{w:?}");
        let why: Vec<&str> = s.iter().map(|s| s.why.as_str()).collect();
        assert!(why[0].contains("unread"), "{why:?}");
        assert!(why[1].contains("on deck"), "{why:?}");
        assert!(why[2].contains("alive no"), "{why:?}");
        assert!(why[3].contains("unread unknown"), "{why:?}");
        assert!(why[4].contains("no seat facts"), "{why:?}");
    }

    #[test]
    fn a_row_whose_key_would_not_split_is_refused() {
        let rows = vec![
            row("U1", "a9136", "a:b", "1"),
            row("U1", "a9136", "x", "f@g"),
            row("X1", "a9136", "x", "1"),
            row("U1", "A9136", "x", "1"),
            row("U1", "a9136", " ", "1"),
        ];
        let (w, s) = run(&file(rows, 0), vec![seat("a9136")], &History::default());
        assert!(w.is_empty(), "{w:?}");
        assert_eq!(s.len(), 5, "{s:?}");
    }

    #[test]
    fn a_dry_row_never_counts_toward_the_seat_gap_nor_rides_in_a_work_dm() {
        assert!(
            !sends(),
            "unblock_notify is dry until its comparison passes"
        );
        let work = Would {
            action: "dispatch".into(),
            to: "a9136".into(),
            first_line: "派工: x → a9136".into(),
            body: Vec::new(),
            keys: vec!["dispatch:#1:-:a9136".into()],
            throttled: true,
            ranks: vec![1],
            send_key: "dispatch:#1:-:a9136".into(),
            dm: None,
            pr_checks: None,
        };
        let mut w = vec![work.clone()];
        let mut s = Vec::new();
        let mut hist = History::default();
        hist.last_sent_ms.insert("a9136".into(), NOW - 60_000);
        plan(
            Some(&file(vec![row("U1", "a9136", "x", "1")], 0)),
            &SeatsOut {
                seats: vec![seat("a9136")],
                complete: true,
            },
            &hist,
            NOW,
            &mut w,
            &mut s,
        );
        assert_eq!(w[0], work, "the work dm is untouched");
        assert_eq!(w.len(), 2, "{s:?}");
        assert!(!w[1].throttled);
    }

    #[test]
    fn no_file_is_nothing_to_do() {
        let (mut w, mut s) = (Vec::new(), Vec::new());
        plan(
            None,
            &SeatsOut::default(),
            &History::default(),
            NOW,
            &mut w,
            &mut s,
        );
        assert!(w.is_empty() && s.is_empty());
    }

    #[test]
    fn u18_a_switch_this_version_never_sends_for_is_named() {
        use crate::act::Switches;
        assert!(
            Switches::sends("nudge") && Switches::sends("dispatch") && Switches::sends("paused")
        );
        assert!(!Switches::sends(ACTION), "dry until the comparison passes");
        let sw = Switches::parse(r#"{"nudge":true,"unblock_notify":true}"#).expect("sw");
        assert_eq!(sw.on_but_dry(), vec![ACTION]);
        assert!(Switches::parse(r#"{"nudge":true}"#)
            .expect("sw")
            .on_but_dry()
            .is_empty());
        assert_eq!(
            Switches::parse(&sw.to_json().to_string()).expect("round trip"),
            sw
        );
    }

    #[test]
    fn an_unblock_dm_to_a_seat_that_is_not_there_is_final_not_retried() {
        use crate::act::{after_send, Sent};
        let u = file(vec![row("U1", "c477a", "x", "1")], 0);
        let (w, _) = run(&u, vec![seat("c477a")], &History::default());
        let sent = vec![Sent {
            key: w[0].send_key.clone(),
            base_key: w[0].send_key.clone(),
            to: "c477a".into(),
            outcome: Ok(crate::intents::Outcome::Dropped(
                "aib dm stage resolve, no such seat".into(),
            )),
            stage: None,
            dm_id: None,
        }];
        let lines = after_send(&w, &sent, false, NOW);
        assert_eq!(lines.len(), 1, "{lines:?}");
        assert!(lines[0].contains("\"kind\":\"gone\""), "{lines:?}");
    }

    #[test]
    fn a_pause_holds_back_unblock_dms() {
        use crate::act::{would_send, Switches};
        let mut sw = Switches {
            unblock_notify: true,
            ..Switches::default()
        };
        assert!(would_send(sw, ACTION));
        sw.paused = true;
        assert!(!would_send(sw, ACTION));
        sw.nudge = true;
        assert!(would_send(sw, "nudge"), "nudges still go out in a pause");
    }
}
