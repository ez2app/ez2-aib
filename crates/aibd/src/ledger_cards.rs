//! The 1.x ledger's other open work as event cards (N-ws-evcards-rest, 519d0 11-19 06:28 / 06:31
//! ruling; NOTE-import-regroup §7): only what still needs a hand, on its plan card, opening the
//! file, gone once it is closed (no history).
//!
//! | source | open while | slot | card id |
//! |---|---|---|---|
//! | `pending/REVIEW-*.md` (§20 first line 「結論: 不通過 頭: …」) | the newest review of the same slice by the same author fails, within [`REVIEW_DAYS`] | 5 可推進 | `revfail:<file>` |
//! | `state/measure/receipt-runs.jsonl` | the newest run of a receipt name is red, within [`RECEIPT_HOURS`], and no full green receipt came after it on the same branch (its `BRANCH` line; no branch ⇒ the same seat) | 2 注意 | `rcpred:<file>` |
//! | `pending/HANDOFF-*.md` 交件單 | no later 交件單 supersedes it, within [`HANDOFF_DAYS`] | 4 卡住 | `handoff:<file>` |
//!
//! A seat's own session handoff (`HANDOFF-<seat>-MMDD-HHMM*.md`) is not a card (519d0 06:31).
//! Only the cabin's live seats' work shows; `open` is `{"doc": <file>}` (the page opens it with
//! the documents API).

use crate::import_regroup::{card_of, Plan};
use crate::slots::{actions, Card, NAMES};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::io::{BufRead, Read};
use std::path::Path;

/// Days a failed review stays a card.
pub const REVIEW_DAYS: i64 = 3;
/// Hours a red receipt stays a card.
pub const RECEIPT_HOURS: i64 = 24;
/// Days an untaken 交件單 stays a card.
pub const HANDOFF_DAYS: i64 = 3;
/// A full receipt has at least this many steps (fmt, anchors, clippy, build, winclippy, test…);
/// a compile-only one has fewer.
pub const FULL_STEPS: i64 = 8;
/// The longest line a card shows.
pub const ITEM_MAX: usize = 80;
/// Bytes read from the top of a file to find its header lines.
const HEAD_BYTES: u64 = 4096;
/// Bytes of a receipt read to find its `BRANCH` line.
const RECEIPT_BYTES: u64 = 4 << 20;

fn hex5(s: &str) -> bool {
    s.len() == 5
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn head_hex(s: &str) -> bool {
    (7..=40).contains(&s.len())
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn round_mark(s: &str) -> bool {
    s.len() > 1 && s.starts_with('r') && s[1..].bytes().all(|b| b.is_ascii_digit())
}

fn cut(s: &str) -> String {
    s.trim().chars().take(ITEM_MAX).collect()
}

fn mtime(p: &Path) -> Option<i64> {
    let t = std::fs::metadata(p)
        .ok()?
        .modified()
        .ok()?
        .duration_since(std::time::UNIX_EPOCH)
        .ok()?;
    i64::try_from(t.as_secs()).ok()
}

fn top_of(p: &Path) -> String {
    let mut b = Vec::new();
    if let Ok(f) = std::fs::File::open(p) {
        let _ = f.take(HEAD_BYTES).read_to_end(&mut b);
    }
    String::from_utf8_lossy(&b).into_owned()
}

/// The files in `dir` named `<prefix>…<suffix>` (one piece, no path) changed at or after
/// `since`: (name, mtime).
fn recent(dir: &Path, prefix: &str, suffix: &str, since: i64) -> Vec<(String, i64)> {
    let Ok(rd) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut out: Vec<(String, i64)> = rd
        .flatten()
        .filter_map(|e| {
            let n = e.file_name().into_string().ok()?;
            if !(n.starts_with(prefix) && n.ends_with(suffix)) || n.contains(['/', '\\']) {
                return None;
            }
            let t = mtime(&e.path())?;
            (t >= since).then_some((n, t))
        })
        .collect();
    out.sort();
    out
}

// one place fills a card the same way for all three kinds (as ctl.rs does for its rows)
#[allow(clippy::too_many_arguments)]
fn card(
    slot: u8,
    kind: &str,
    id: String,
    seat: &str,
    at: i64,
    item: &str,
    state: &str,
    plan_card: Option<String>,
    file: &str,
) -> Card {
    Card {
        card_id: id,
        slot,
        name: NAMES[usize::from(slot - 1)],
        kind: kind.into(),
        // a review that does not say its author has no seat (the page shows it unowned)
        seat_id: (!seat.is_empty()).then(|| seat.to_owned()),
        session_id: None,
        at: crate::sessions::rfc3339_of_unix(at),
        global_seq: None,
        item: Some(cut(item)),
        why: None,
        pct: None,
        card: plan_card,
        rec: None,
        state: Some(state.to_owned()),
        src: None,
        tag: None,
        open: Some(json!({ "doc": file })),
        actions: actions(slot),
    }
}

/// A review file's (slice, author, failed, first line, second line) by §20: the first line
/// 「結論: <verdict> 頭: <head> …」; the author is the first seat code after 「審查(」 on the
/// second line, else the first seat code in the name; the slice is the name before that code.
/// What [`review_of`] reads: (slice, author, failed, first line, second line).
pub type ReviewHead = (String, String, bool, String, String);
/// The newest review of a (slice, author): (mtime, file, failed, first line, second line).
type Newest = (i64, String, bool, String, String);

pub fn review_of(name: &str, text: &str) -> Option<ReviewHead> {
    let mut lines = text.lines();
    let first = lines.next()?.trim();
    let second = lines.next().unwrap_or("").trim();
    let verdict = first.strip_prefix("結論: ")?.split_whitespace().next()?;
    if !first.contains("頭: ") {
        return None;
    }
    let toks: Vec<&str> = name
        .strip_prefix("REVIEW-")?
        .strip_suffix(".md")?
        .split('-')
        .collect();
    let at = toks.iter().position(|t| hex5(t))?;
    let slice = toks[..at].join("-");
    // the author as the review says it (「作者 b4297」, 「審查(7b81e …」), else the first of two
    // seat codes in the name; one code alone is often the reviewer's, so the author is unknown ("")
    let said = |key: &str| {
        text.lines().take(6).find_map(|l| {
            l.split_once(key).and_then(|(_, rest)| {
                rest.split(|c: char| !c.is_ascii_alphanumeric())
                    .next()
                    .filter(|w| hex5(w))
            })
        })
    };
    let codes: Vec<&str> = toks.iter().copied().filter(|t| hex5(t)).collect();
    let author = said("作者 ")
        .or_else(|| said("審查("))
        .or_else(|| (codes.len() >= 2).then(|| codes[0]))
        .unwrap_or("");
    Some((
        slice,
        author.to_owned(),
        verdict == "不通過",
        first.to_owned(),
        second.to_owned(),
    ))
}

/// Failed reviews still open: the newest review of each (slice, author) within the window.
pub fn review_cards(pending: &Path, live: &[String], plan: &Plan, now: i64) -> Vec<Card> {
    let mut newest: BTreeMap<(String, String), Newest> = BTreeMap::new();
    for (name, t) in recent(pending, "REVIEW-", ".md", now - REVIEW_DAYS * 86_400) {
        let Some((slice, author, failed, first, second)) =
            review_of(&name, &top_of(&pending.join(&name)))
        else {
            continue;
        };
        let e = newest.entry((slice, author)).or_insert((
            i64::MIN,
            String::new(),
            false,
            String::new(),
            String::new(),
        ));
        if (t, &name) > (e.0, &e.1) {
            *e = (t, name, failed, first, second);
        }
    }
    // a review that did not say its author is closed by any newer review of its slice (0eed7
    // VERIFY-evcards 93d26f1b3 L1: the old REVIEW-nnow3 had no 「作者 」, the passing one did)
    let latest: BTreeMap<String, (i64, String)> =
        newest
            .iter()
            .fold(BTreeMap::new(), |mut m, ((slice, _), v)| {
                let e = m.entry(slice.clone()).or_insert((i64::MIN, String::new()));
                if (v.0, &v.1) > (e.0, &e.1) {
                    *e = (v.0, v.1.clone());
                }
                m
            });
    // the newest review of each slice that names no author
    let unowned: BTreeMap<String, (i64, String)> = newest
        .iter()
        .filter(|((_, a), _)| a.is_empty())
        .map(|((slice, _), v)| (slice.clone(), (v.0, v.1.clone())))
        .collect();
    newest
        .into_iter()
        .filter(|((slice, author), v)| {
            // either way (0eed7 r1 N): an unowned review is closed by any newer review of its
            // slice, an owned one also by a newer unowned review
            let unowned_after = unowned
                .get(slice)
                .is_some_and(|w| (w.0, &w.1) > (v.0, &v.1));
            if author.is_empty() {
                latest.get(slice).is_some_and(|l| l.1 == v.1)
            } else {
                !unowned_after
            }
        })
        .filter(|((_, author), v)| v.2 && (author.is_empty() || live.iter().any(|s| s == author)))
        .map(|((_, author), (t, name, _, first, second))| {
            card(
                5,
                "ledger.review",
                format!("revfail:{name}"),
                &author,
                t,
                &first,
                "審查不通過、之後沒有新的審查",
                card_of(&second, plan).map(|(c, _)| c),
                &name,
            )
        })
        .collect()
}

/// A receipt run's (seat, name): its invocation id from the first seat code on, without the
/// head and round marks at the end (`lin-macmini-7ebca-dmready-239dad1a0` ⇒ `7ebca`,
/// `7ebca-dmready`).
pub fn receipt_key(invocation: &str) -> Option<(String, String)> {
    let parts: Vec<&str> = invocation.split('-').collect();
    let at = parts.iter().position(|p| hex5(p))?;
    let mut keep = &parts[at..];
    while keep.len() > 1 && keep.last().is_some_and(|p| head_hex(p) || round_mark(p)) {
        keep = &keep[..keep.len() - 1];
    }
    Some((keep[0].to_owned(), keep.join("-")))
}

fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|f| f != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(_) => true,
    }
}

fn int(v: &Value, k: &str) -> i64 {
    v.get(k).and_then(Value::as_i64).unwrap_or(0)
}

/// A receipt's `BRANCH <name>` line (none when the file is not a plain receipt name or has none).
pub fn branch_of(pending: &Path, file: &str) -> Option<String> {
    if !file.starts_with("RECEIPT-") || file.contains(['/', '\\']) || file.contains("..") {
        return None;
    }
    let f = std::fs::File::open(pending.join(file)).ok()?;
    std::io::BufReader::new(f.take(RECEIPT_BYTES))
        .lines()
        .map_while(Result::ok)
        .find_map(|l| l.strip_prefix("BRANCH ").map(|b| b.trim().to_owned()))
        .filter(|b| !b.is_empty())
}

/// Red receipts still open (see the module table).
pub fn receipt_cards(
    pending: &Path,
    runs: &str,
    live: &[String],
    plan: &Plan,
    now: i64,
) -> Vec<Card> {
    let rows: Vec<(String, String, i64, Value)> = runs
        .lines()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
        .filter_map(|v| {
            let (seat, name) = receipt_key(v.get("invocation_id")?.as_str()?)?;
            let at = int(&v, "at_ms") / 1000;
            Some((seat, name, at, v))
        })
        .collect();
    let red = |v: &Value| int(v, "steps_bad") > 0 || truthy(v.get("cell_red"));
    let green_full = |v: &Value| {
        int(v, "steps") >= FULL_STEPS && int(v, "steps_bad") == 0 && !truthy(v.get("cell_red"))
    };
    let file = |v: &Value| {
        v.get("file")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_owned()
    };
    let mut newest: BTreeMap<&str, usize> = BTreeMap::new();
    // a compile-only green says nothing of the tests: it neither closes a red nor stands for
    // the newest of its name (29ba25e6c's receipt: the test below never ran before)
    for (i, (_, name, at, v)) in rows.iter().enumerate() {
        if !red(v) && !green_full(v) {
            continue;
        }
        let e = newest.entry(name.as_str()).or_insert(i);
        if *at >= rows[*e].2 {
            *e = i;
        }
    }
    let mut out = Vec::new();
    for &i in newest.values() {
        let (seat, name, at, v) = &rows[i];
        if !red(v) || *at < now - RECEIPT_HOURS * 3600 || !live.iter().any(|s| s == seat) {
            continue;
        }
        let f = file(v);
        let branch = branch_of(pending, &f);
        let healed = rows.iter().any(|(s2, _, at2, v2)| {
            *at2 > *at
                && green_full(v2)
                && match &branch {
                    Some(b) => branch_of(pending, &file(v2)).as_ref() == Some(b),
                    None => s2 == seat,
                }
        });
        if healed {
            continue;
        }
        let what = format!("收據紅 {name}(壞 {} 步)", int(v, "steps_bad"));
        out.push(card(
            2,
            "ledger.receipt",
            format!("rcpred:{f}"),
            seat,
            *at,
            &what,
            "收據紅、之後同分支沒有綠的整套收據",
            branch
                .as_deref()
                .and_then(|b| card_of(b, plan))
                .map(|(c, _)| c),
            &f,
        ));
    }
    out
}

/// A seat's own session handoff: `HANDOFF-<seat>-MMDD-HHMM…`.
pub fn session_handoff(name: &str) -> bool {
    let mut p = name.trim_start_matches("HANDOFF-").split('-');
    let four =
        |s: Option<&str>| s.is_some_and(|s| s.len() == 4 && s.bytes().all(|b| b.is_ascii_digit()));
    p.next().is_some_and(hex5) && four(p.next()) && four(p.next().map(|s| s.get(..4).unwrap_or(s)))
}

/// The header fields of a 交件單: (owner, what it supersedes).
pub fn delivery_of(text: &str) -> Option<(Option<String>, Option<String>)> {
    let status = text
        .lines()
        .find(|l| l.trim_start().starts_with("status:"))?;
    if !status.contains("交件單") {
        return None;
    }
    let owner = status.split_once("owner: ").and_then(|(_, r)| {
        r.split(|c: char| !c.is_ascii_alphanumeric())
            .next()
            .filter(|w| hex5(w))
            .map(str::to_owned)
    });
    let sup = status.split_once("supersedes: ").and_then(|(_, r)| {
        r.split(|c: char| c.is_whitespace() || "((·".contains(c))
            .next()
            .filter(|w| w.starts_with("HANDOFF-") && w.ends_with(".md"))
            .map(str::to_owned)
    });
    Some((owner, sup))
}

/// 交件單 no later one supersedes.
pub fn handoff_cards(pending: &Path, live: &[String], plan: &Plan, now: i64) -> Vec<Card> {
    let mut all = Vec::new();
    for (name, t) in recent(pending, "HANDOFF-", ".md", now - HANDOFF_DAYS * 86_400) {
        if session_handoff(&name) {
            continue;
        }
        let text = top_of(&pending.join(&name));
        if let Some((owner, sup)) = delivery_of(&text) {
            let title = text
                .lines()
                .find(|l| l.starts_with("# "))
                .map(|l| l.trim_start_matches("# ").to_owned())
                .unwrap_or_else(|| name.clone());
            all.push((name, t, owner, sup, title, text));
        }
    }
    let taken: Vec<String> = all.iter().filter_map(|a| a.3.clone()).collect();
    all.iter()
        .filter(|(name, ..)| !taken.contains(name))
        .filter_map(|(name, t, owner, _, title, text)| {
            let owner = owner.as_deref().filter(|o| live.iter().any(|s| s == o))?;
            let node = text
                .lines()
                .find(|l| l.trim_start().starts_with("status:"))
                .and_then(|s| s.split_once("node: "))
                .map(|(_, r)| r.to_owned())
                .unwrap_or_default();
            Some(card(
                4,
                "ledger.handoff",
                format!("handoff:{name}"),
                owner,
                *t,
                title,
                "交件單還沒有人接(沒有新的一份取代它)",
                card_of(&node, plan)
                    .or_else(|| card_of(title, plan))
                    .map(|(c, _)| c),
                name,
            ))
        })
        .collect()
}

/// Hours a merge stays a slot-6 card.
pub const MERGED_HOURS: i64 = 24;

/// aib-drive's `merged` events (its merge watch, a9136; 519d0 11-19 07:39) within
/// [`MERGED_HOURS`] as slot-6 cards (已合): `{kind: "merged", at, pr, head, node?, branch?,
/// author?}` (`seat` is the writer); one card per head. `events` is the text of aib-drive's month files.
pub fn merged_cards(events: &str, plan: &Plan, now: i64) -> Vec<Card> {
    let since = crate::sessions::rfc3339_of_unix(now - MERGED_HOURS * 3600);
    let mut by_head: BTreeMap<String, Card> = BTreeMap::new();
    for v in events
        .lines()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
        .filter(|v| v.get("kind").and_then(Value::as_str) == Some("merged"))
    {
        let s = |k: &str| v.get(k).and_then(Value::as_str).map(str::to_owned);
        let (Some(at), Some(head)) = (s("at"), s("head").filter(|h| head_hex(h))) else {
            continue;
        };
        if at.as_str() < since.as_str() {
            continue;
        }
        let pr = v.get("pr").and_then(Value::as_u64);
        let node = s("node").filter(|n| plan.ids.contains(n));
        let short: String = head.chars().take(9).collect();
        let item = match pr {
            Some(n) => format!("已合 #{n}({short})"),
            None => format!("已合 {short}"),
        };
        // the author: `seat` is the seat that wrote the event (aib-ledger), not whose PR it was
        let seat = s("author").filter(|x| hex5(x));
        by_head.insert(
            head.clone(),
            Card {
                card_id: format!("merged:{head}"),
                slot: 6,
                name: NAMES[5],
                kind: "drive.merged".into(),
                seat_id: seat.clone(),
                session_id: None,
                at,
                global_seq: None,
                item: Some(item),
                why: None,
                pct: None,
                card: node.or_else(|| s("branch").and_then(|b| card_of(&b, plan)).map(|(c, _)| c)),
                rec: None,
                state: None,
                src: seat,
                tag: None,
                open: None,
                actions: actions(6),
            },
        );
    }
    by_head.into_values().collect()
}

/// aib-drive's events of the months [`MERGED_HOURS`] back and now (`events/YYYY-MM.jsonl`).
fn drive_events(state: &Path, now: i64) -> String {
    let dir = state.join("aib-drive").join("events");
    let mut text = String::new();
    let month = |t: i64| -> String {
        crate::sessions::rfc3339_of_unix(t)
            .chars()
            .take(7)
            .collect()
    };
    let (then, this) = (month(now - MERGED_HOURS * 3600), month(now));
    // a day back is this month or the last one; never the same file twice
    let months = if then == this {
        vec![this]
    } else {
        vec![then, this]
    };
    for m in months {
        if let Ok(s) = std::fs::read_to_string(dir.join(format!("{m}.jsonl"))) {
            text.push_str(&s);
            text.push('\n');
        }
    }
    text
}

/// All three kinds from the folders given.
pub fn cards_in(pending: &Path, state: &Path, live: &[String], plan: &Plan, now: i64) -> Vec<Card> {
    let runs = std::fs::read_to_string(state.join("measure").join("receipt-runs.jsonl"))
        .unwrap_or_default();
    let mut out = review_cards(pending, live, plan, now);
    out.extend(receipt_cards(pending, &runs, live, plan, now));
    out.extend(handoff_cards(pending, live, plan, now));
    out.extend(merged_cards(&drive_events(state, now), plan, now));
    out
}

/// The cabin's live 1.x seats' open ledger work (none unless `legacy_cabin` is set).
pub fn cards(now: i64) -> Vec<Card> {
    let cfg = crate::bridge_cfg::now();
    let Some(cabin) = cfg.legacy_cabin.clone() else {
        return Vec::new();
    };
    let Some(queue) = crate::bridge_cfg::queue_root(&cfg, |k| std::env::var(k).ok()) else {
        return Vec::new();
    };
    let Some(state) = queue.parent() else {
        return Vec::new();
    };
    let Some(home) = state.parent() else {
        return Vec::new();
    };
    let Some(pending) = crate::docs::root() else {
        return Vec::new();
    };
    let live = crate::legacy_send::cabin_seats(&home.join("instances"), &cabin);
    if live.is_empty() {
        return Vec::new();
    }
    let plan = crate::dm_cards::plan_of(crate::workspace::current_tree().as_ref());
    cards_in(&pending, state, &live, &plan, now)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn live() -> Vec<String> {
        ["7b81e", "7ebca", "b4297"]
            .iter()
            .map(|s| (*s).to_owned())
            .collect()
    }

    fn put(dir: &Path, name: &str, text: &str) {
        std::fs::write(dir.join(name), text).unwrap();
    }

    fn now() -> i64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| i64::try_from(d.as_secs()).unwrap_or(0))
    }

    /// Shapes copied from real files (REVIEW-touchid-7b81e-5d33a-8ec571dc.md,
    /// REVIEW-B1dm-header-5d33a-a6c5e663.md): the author is on the second line.
    #[test]
    fn a_review_is_read_by_its_first_two_lines() {
        let r = review_of(
            "REVIEW-touchid-7b81e-5d33a-8ec571dc.md",
            "結論: 不通過 頭: 8ec571dcd883ca04f14d83abb8f375e280bcac20 H/M/L/N: 0/1/1/1\nstatus: 審查(7b81e 配對改指紋兩片)· 卡 N-ws-oneweb\n",
        )
        .unwrap();
        assert_eq!(
            (r.0.as_str(), r.1.as_str(), r.2),
            ("touchid", "7b81e", true)
        );
        let r = review_of(
            "REVIEW-B1dm-header-5d33a-a6c5e663.md",
            "結論: 通過 頭: a6c5e663 H/M/L/N: 0/0/0/0\nstatus: 審查(7ebca B1dm 信頭)\n",
        )
        .unwrap();
        assert_eq!(
            (r.1.as_str(), r.2),
            ("7ebca", false),
            "reason: reviewer_as_author"
        );
        // one seat code in the name is the reviewer's: 「作者 」 names the author, else unknown
        let r = review_of(
            "REVIEW-B1dm-header-5d33a-a6c5e663.md",
            "結論: 不通過 頭: a6c5e663\nstatus: 審查(已上鏈換上後補審)· 作者 b4297\n",
        )
        .unwrap();
        assert_eq!(r.1, "b4297", "reason: reviewer_as_author");
        let r = review_of(
            "REVIEW-S89-dgrs-5d33a-29e68425.md",
            "結論: 不通過 頭: 29e68425\n不通過的原因只有一條\n",
        )
        .unwrap();
        assert_eq!(r.1, "", "reason: reviewer_as_author");
        assert!(review_of("REVIEW-x-7ebca-1.md", "# 舊式\n").is_none());
    }

    #[test]
    fn only_the_newest_failed_review_of_a_slice_is_a_card() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path();
        let plan = crate::dm_cards::plan_of(Some(&json!({"nodes": [{"id": "N-ws-oneweb"}]})));
        put(
            p,
            "REVIEW-touchid-7b81e-5d33a-1.md",
            "結論: 不通過 頭: 1111111\nstatus: 審查(7b81e)· 卡 N-ws-oneweb\n",
        );
        put(
            p,
            "REVIEW-s89-7ebca-5d33a-2.md",
            "結論: 不通過 頭: 2222222\nstatus: 審查(7ebca)\n",
        );
        put(
            p,
            "REVIEW-gone-f00d0-5d33a-3.md",
            "結論: 不通過 頭: 3333333\nstatus: 審查(f00d0)\n",
        );
        let c = review_cards(p, &live(), &plan, now());
        assert_eq!(c.len(), 2, "reason: retired_or_missing {c:?}");
        let t = c
            .iter()
            .find(|c| c.seat_id.as_deref() == Some("7b81e"))
            .unwrap();
        assert_eq!((t.slot, t.card.as_deref()), (5, Some("N-ws-oneweb")));
        assert_eq!(
            t.open,
            Some(json!({"doc": "REVIEW-touchid-7b81e-5d33a-1.md"}))
        );
        // a later pass of the same slice by the same author closes it
        let older = std::time::SystemTime::now() - std::time::Duration::from_secs(120);
        std::fs::File::options()
            .write(true)
            .open(p.join("REVIEW-touchid-7b81e-5d33a-1.md"))
            .unwrap()
            .set_modified(older)
            .unwrap();
        put(
            p,
            "REVIEW-touchid-7b81e-5d33a-4.md",
            "結論: 通過 頭: 4444444\nstatus: 審查(7b81e)\n",
        );
        let c = review_cards(p, &live(), &plan, now());
        assert!(
            c.iter().all(|c| c.seat_id.as_deref() != Some("7b81e")),
            "reason: passed_slice_kept"
        );
        // 0eed7 L1: an older failed review that did not name its author is closed by a newer
        // review of the same slice that does
        put(
            p,
            "REVIEW-nnow3-5d33a-5.md",
            "結論: 不通過 頭: 5555555\n舊式第二行\n",
        );
        let c = review_cards(p, &live(), &plan, now());
        assert!(
            c.iter()
                .any(|c| c.card_id == "revfail:REVIEW-nnow3-5d33a-5.md"),
            "reason: unowned_fail_missing"
        );
        let older = std::time::SystemTime::now() - std::time::Duration::from_secs(60);
        std::fs::File::options()
            .write(true)
            .open(p.join("REVIEW-nnow3-5d33a-5.md"))
            .unwrap()
            .set_modified(older)
            .unwrap();
        put(
            p,
            "REVIEW-nnow3-5d33a-6.md",
            "結論: 通過 頭: 6666666\n作者 7ebca · status: 審查\n",
        );
        let c = review_cards(p, &live(), &plan, now());
        assert!(
            c.iter()
                .all(|c| !c.card_id.starts_with("revfail:REVIEW-nnow3")),
            "reason: unowned_fail_kept_after_pass {c:?}"
        );
        // and the other way: an owned fail, then a newer pass that names no author
        put(
            p,
            "REVIEW-s99-5d33a-7.md",
            "結論: 不通過 頭: 7777777\n作者 7ebca · status: 審查\n",
        );
        std::fs::File::options()
            .write(true)
            .open(p.join("REVIEW-s99-5d33a-7.md"))
            .unwrap()
            .set_modified(older)
            .unwrap();
        put(p, "REVIEW-s99-5d33a-8.md", "結論: 通過 頭: 8888888\n舊式\n");
        let c = review_cards(p, &live(), &plan, now());
        assert!(
            c.iter()
                .all(|c| !c.card_id.starts_with("revfail:REVIEW-s99")),
            "reason: owned_fail_kept_after_unowned_pass {c:?}"
        );
    }

    #[test]
    fn a_merge_in_the_last_day_is_a_done_card_once_per_head() {
        let plan = crate::dm_cards::plan_of(Some(&json!({"nodes": [{"id": "N-web-dmbridge"}]})));
        let n = now();
        let at = |ago: i64| crate::sessions::rfc3339_of_unix(n - ago);
        let h1 = "ca92a42efd3dc3638e7ca1212465af0f8461380f";
        let h2 = "5e778fb603a15218e30a92f4a6046af110a5b4cb";
        let evs = [
            json!({"kind": "merged", "at": at(600), "pr": 901, "head": h1, "node": "N-web-dmbridge", "author": "7ebca", "seat": "a9136"}),
            json!({"kind": "merged", "at": at(300), "pr": 901, "head": h1, "node": "N-web-dmbridge"}),
            json!({"kind": "merged", "at": at(2 * 86_400), "pr": 880, "head": h2}),
            json!({"kind": "merged", "at": at(60), "pr": 902, "head": "not-hex"}),
            json!({"kind": "drive_send", "at": at(60), "head": h2}),
        ]
        .iter()
        .map(Value::to_string)
        .collect::<Vec<_>>()
        .join("\n");
        let c = merged_cards(&evs, &plan, n);
        assert_eq!(c.len(), 1, "reason: old_dup_or_bad_head {c:?}");
        assert_eq!(c[0].card_id, format!("merged:{h1}"));
        assert_eq!(
            (c[0].slot, c[0].card.as_deref()),
            (6, Some("N-web-dmbridge"))
        );
        assert_eq!(c[0].item.as_deref(), Some("已合 #901(ca92a42ef)"));
        // its author (not the writer in `seat`) is the card's seat
        let one = json!({"kind": "merged", "at": at(60), "pr": 903, "head": h2,
            "branch": "feat/x", "author": "7ebca", "seat": "a9136", "outcome": "seen"})
        .to_string();
        let c = merged_cards(&one, &plan, n);
        assert_eq!(
            c[0].seat_id.as_deref(),
            Some("7ebca"),
            "reason: writer_as_author"
        );
    }

    #[test]
    fn a_receipts_name_drops_its_host_head_and_round() {
        assert_eq!(
            receipt_key("lin-macmini-7ebca-dmready-239dad1a0"),
            Some(("7ebca".into(), "7ebca-dmready".into()))
        );
        assert_eq!(
            receipt_key("100.116.110.31-7ebca-wsco-2017cdd5f-r2"),
            Some(("7ebca".into(), "7ebca-wsco".into()))
        );
        assert_eq!(receipt_key("andymini-nohex"), None);
    }

    #[test]
    fn a_red_receipt_is_a_card_until_its_branch_has_a_full_green() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path();
        let plan = Plan::of(None);
        let n = now();
        let row = |inv: &str, file: &str, at: i64, steps: i64, bad: i64| {
            json!({"invocation_id": inv, "file": file, "at_ms": at * 1000, "steps": steps,
                "steps_bad": bad, "cell_red": null})
            .to_string()
        };
        put(
            p,
            "RECEIPT-lin-7ebca-a-1111111.out",
            "HEAD x\nBRANCH feat/a\n",
        );
        put(p, "RECEIPT-lin-7ebca-a2-2222222.out", "BRANCH feat/a\n");
        put(p, "RECEIPT-lin-7b81e-b-3333333.out", "BRANCH feat/b\n");
        let runs = [
            row(
                "lin-7ebca-a-1111111",
                "RECEIPT-lin-7ebca-a-1111111.out",
                n - 600,
                8,
                1,
            ),
            row(
                "lin-7b81e-b-3333333",
                "RECEIPT-lin-7b81e-b-3333333.out",
                n - 600,
                6,
                2,
            ),
            row(
                "lin-b4297-c-4444444",
                "RECEIPT-lin-b4297-c-4444444.out",
                n - 2 * 86_400,
                8,
                1,
            ),
        ]
        .join("\n");
        let c = receipt_cards(p, &runs, &live(), &plan, n);
        let ids: Vec<&str> = c.iter().map(|c| c.card_id.as_str()).collect();
        assert_eq!(
            ids,
            [
                "rcpred:RECEIPT-lin-7b81e-b-3333333.out",
                "rcpred:RECEIPT-lin-7ebca-a-1111111.out"
            ],
            "reason: old_or_missing"
        );
        assert_eq!(c[0].slot, 2);
        // a full green under another name on the same branch closes it; a compile-only one does not
        let more = format!(
            "{runs}\n{}\n{}",
            row(
                "lin-7ebca-a2-2222222",
                "RECEIPT-lin-7ebca-a2-2222222.out",
                n - 60,
                8,
                0
            ),
            row(
                "lin-7b81e-b-5555555",
                "RECEIPT-lin-7b81e-b-5555555.out",
                n - 60,
                6,
                0
            )
        );
        let c = receipt_cards(p, &more, &live(), &plan, n);
        let ids: Vec<&str> = c.iter().map(|c| c.card_id.as_str()).collect();
        assert_eq!(
            ids,
            ["rcpred:RECEIPT-lin-7b81e-b-3333333.out"],
            "reason: healed_kept_or_compile_only_heals"
        );
    }

    #[test]
    fn a_delivery_note_is_a_card_until_a_later_one_supersedes_it() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path();
        let plan = crate::dm_cards::plan_of(Some(&json!({"nodes": [{"id": "N-workspace"}]})));
        put(p, "HANDOFF-fastbin-8843ef5f-b4297-1118.md",
            "---\nstatus: 交件單 · rev: r0 · node: N-workspace · owner: b4297 · 11-18\n---\n# 快修包 8843ef5f\n");
        put(p, "HANDOFF-fastbin-51ac92d9-b4297-1118.md",
            "---\nstatus: 交件單 · rev: r0 · supersedes: HANDOFF-fastbin-8843ef5f-b4297-1118.md(已換上)· node: N-workspace · owner: b4297 · 11-18\n---\n# 快修包 51ac92d9\n");
        put(
            p,
            "HANDOFF-7ebca-1118-1227-2.md",
            "status: 交件單 · owner: 7ebca\n",
        );
        let c = handoff_cards(p, &live(), &plan, now());
        assert_eq!(c.len(), 1, "reason: superseded_or_session_shown {c:?}");
        assert_eq!(
            c[0].card_id,
            "handoff:HANDOFF-fastbin-51ac92d9-b4297-1118.md"
        );
        assert_eq!((c[0].slot, c[0].card.as_deref()), (4, Some("N-workspace")));
        assert_eq!(c[0].item.as_deref(), Some("快修包 51ac92d9"));
        assert!(session_handoff("HANDOFF-519d0-1118-1227-2.md"));
        assert!(!session_handoff("HANDOFF-fastbin-51ac92d9-b4297-1118.md"));
    }
}
