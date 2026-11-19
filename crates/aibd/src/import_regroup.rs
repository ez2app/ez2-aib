//! Andy 11-19 04:3x 「為什麼你匯入了的資訊變成一大封,右邊的 事件小卡 沒有增加?」: the old records
//! put by what they are about, not by who wrote to whom (519d0 NOTE-import-regroup-519d0-1119
//! points 1, 2, 4).
//!
//! - `aibd import regroup [--dry-run] [--cards N] [--undo]`, read only on the sources (the same
//!   ones as `dm` / `shadow` / `council` and the `SUMMARY-` / `HANDOFF-` / `DEEPREVIEW-` files).
//! - Each record goes to one card of the plan, by the first rule that finds one, and says which
//!   (`card_rule`, 043dd's names): `card_text` its text names the card · `pr` a head / PR number / branch only
//!   that card's own fields write · `file` it names a document's file and that document has a card · `reply` (mail) the mail it answers or replaces had a card ·
//!   `context` (mail) the mail before it between the same two seats, at most 30 minutes before, had
//!   one · `unassigned` nothing: the sender seat's bucket 「未歸卡 · <席>(主項目 …)」 under its
//!   main item then (Andy 11-19 a50-2), else the thread 「未歸卡 · <日期>」; older than
//!   [`RECENT_DAYS`] a record without a card is not imported (a50-1).
//! - A record with a card is posted into that card's own thread (the one the page opens for the
//!   item), so the card's conversation shows it.
//! - A document (定稿, 總結, 交接, 深度回顧) is one short post: its title, its first lines and the
//!   file to open; never the whole text.
//! - `--dry-run` prints the table (how many each rule put, how many are without a card, the
//!   biggest threads) and writes nothing; `--cards N` writes only the records of the N cards
//!   with the most records (the sample for Andy).
//! - Keys `rg:<kind>:<id>` under the owner `import`, so a run again adds nothing and `--undo`
//!   takes this import back without touching the kind imports (`aibd import dm` …).
use crate::import::{Counts, Done, OWNER};
use crate::import_more::{self as im, Item, Roots};
use aib_plan::mainline::ON_TRACK_MIN;
use aib_store::{Inserted, MessageDb, Opened, ThreadRow};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

/// The idem key prefix of this import.
pub const KEY_PREFIX: &str = "rg:";
/// How close the mail before has to be for rule `context` in [`card_of_mail`] (a new letter of
/// the mirror); the import takes the same Taipei day instead ([`route`], 519d0 05:12).
pub const NEAR_SECS: i64 = 30 * 60;
/// The import's rule `context` (519d0 08:11, T13 2,930 / S15 1,435 put by context chains
/// across seats and days): a letter takes the card of its pair's anchor — a letter carded by
/// its own text, work, document or reply — only the same Taipei day, at most this far from it…
pub const CONTEXT_SECS: i64 = 2 * 60 * 60;
/// …and at most this many of the pair's letters after (or before) it; past that it stays
/// without a card (better none than a wrong one).
pub const CONTEXT_MAX: usize = 20;
/// Rule `file` reads a named document's card from this many of its first lines that are not
/// blank (519d0 08:18).
pub const DOC_HEAD_LINES: usize = 8;
/// Andy 11-19 a50-1: the last this many days come in whole; before that only what found a card.
pub const RECENT_DAYS: i64 = 14;
/// The documents posted short: (file prefix, kind).
pub const DOC_KINDS: [(&str, &str); 4] = [
    ("DECISION-council-", "council"),
    ("SUMMARY-", "summary"),
    ("HANDOFF-", "handoff"),
    ("DEEPREVIEW-", "review"),
];
/// How many first lines a document's post keeps.
pub const DOC_LINES: usize = 4;

/// Which rule found the card.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum By {
    Text,
    Work,
    File,
    Reply,
    Near,
    SeatDay,
    System,
    None,
}

impl By {
    pub fn word(self) -> &'static str {
        match self {
            By::Text => "card_text",
            By::Work => "pr",
            By::File => "file",
            By::Reply => "reply",
            By::Near => "context",
            By::SeatDay => "seat_day",
            By::System => "system",
            By::None => "unassigned",
        }
    }
}

/// One record and where it goes.
#[derive(Debug, Clone)]
pub struct Routed {
    pub item: Item,
    pub by: By,
}

/// A head as people write it: 7–40 lower hex with a letter and a digit in it.
pub fn is_head(w: &str) -> bool {
    (7..=40).contains(&w.len())
        && w.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        && w.bytes().any(|b| b.is_ascii_digit())
        && w.bytes().any(|b| b.is_ascii_alphabetic())
}

const BRANCHES: [&str; 7] = [
    "feat/", "fix/", "integ/", "merge/", "docs/", "test/", "diag/",
];

/// Whether `text` names a PR number or a branch (rule `context` B, 519d0 08:32): a letter whose
/// own work no card has is about something else. Heads are not counted (519d0 08:39: nearly
/// every letter writes heads, and a card keeps only its newest two, so 6,669 letters with only
/// heads fell out).
pub fn names_other_work(text: &str) -> bool {
    work_words(text).iter().any(|w| !is_head(w))
}

/// The words in `text` that name a piece of work: a head, a PR number (`#` and 3–5 digits),
/// a branch (`feat/…`, `fix/…`, …).
pub fn work_words(text: &str) -> Vec<String> {
    text.split(|c: char| !(c.is_ascii_alphanumeric() || matches!(c, '#' | '/' | '-' | '_' | '.')))
        .filter_map(|w| {
            let w = w.trim_end_matches(['.', '-', '_']);
            if is_head(w) {
                return Some(w.to_owned());
            }
            if let Some(d) = w.strip_prefix('#') {
                let pr = (3..=5).contains(&d.len()) && d.bytes().all(|b| b.is_ascii_digit());
                return pr.then(|| w.to_owned());
            }
            BRANCHES
                .iter()
                .any(|p| w.len() > p.len() && w.starts_with(p))
                .then(|| w.to_owned())
        })
        .collect()
}

/// What the plan says names one of its cards.
#[derive(Debug, Default)]
pub struct Plan {
    /// card ids a text may name (one letter ones are left out: 「V」 is a word too)
    pub ids: BTreeSet<String>,
    pub titles: BTreeMap<String, String>,
    /// a PR number or branch only one card writes
    pub work: BTreeMap<String, String>,
    /// a head only one card writes
    pub heads: Vec<(String, String)>,
    /// a document's file name → the card the document went to (rule `file`, NOTE r1 §4 5)
    pub files: BTreeMap<String, String>,
    /// seat → (time, card) of each mark it left on a card: a plan tree note
    /// (`plan-events.jsonl` `by` / `id` / `ts`) or a status line naming one (rule `seat_day`)
    pub seat_marks: BTreeMap<String, Vec<(i64, String)>>,
}

/// An id that reads like something else when it stands alone in a text (519d0 05:1x: card:M1
/// had 3,801 posts): a review grade and number (`H1` `M2` `L1` `N3`), a round (`R4`), anything
/// of two characters or less.
pub fn looks_like_other(id: &str) -> bool {
    let b = id.as_bytes();
    let numbered =
        |lead: &[u8]| b.len() >= 2 && lead.contains(&b[0]) && b[1..].iter().all(u8::is_ascii_digit);
    id.chars().count() <= 2 || numbered(b"HMLN") || numbered(b"R")
}

/// Rule `card_text`: the first card the text names. An id that [`looks_like_other`] counts only
/// where a card is said on purpose: after 「卡 」 / 「卡:」 (a dispatch's 「(卡 X)」 too) or
/// `node:` — not on a first line (519d0 05:12: 1,011 first lines had M1 as a review number).
pub fn card_in_text(text: &str, plan: &Plan) -> Option<String> {
    let words = |t: &str| -> Vec<String> {
        t.split(|c: char| !(c.is_ascii_alphanumeric() || c == '-' || c == '_'))
            .filter(|w| !w.is_empty() && plan.ids.contains(*w))
            .map(str::to_owned)
            .collect()
    };
    let said = |id: &str| {
        ["卡 ", "卡:", "卡:", "node: ", "node:"]
            .iter()
            .any(|p| text.contains(&format!("{p}{id}")))
    };
    words(text)
        .into_iter()
        .find(|id| !looks_like_other(id) || said(id))
}

impl Plan {
    pub fn of(tree: Option<&Value>) -> Plan {
        let mut p = Plan::default();
        let mut by: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
        let nodes = tree
            .and_then(|t| t.get("nodes"))
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        for n in &nodes {
            let Some(id) = n.get("id").and_then(Value::as_str) else {
                continue;
            };
            if id.chars().count() > 1 {
                p.ids.insert(id.to_owned());
            }
            let title = n.get("title").and_then(Value::as_str).unwrap_or(id);
            p.titles.insert(id.to_owned(), title.to_owned());
            for k in ["head", "head_prev", "evidence", "why", "why_note"] {
                for w in n
                    .get(k)
                    .and_then(Value::as_str)
                    .map(work_words)
                    .unwrap_or_default()
                {
                    by.entry(w).or_default().insert(id.to_owned());
                }
            }
        }
        for (w, cards) in by {
            // a word two cards write names neither
            if cards.len() != 1 {
                continue;
            }
            let Some(card) = cards.into_iter().next() else {
                continue;
            };
            if is_head(&w) {
                p.heads.push((w, card));
            } else {
                p.work.insert(w, card);
            }
        }
        p
    }

    /// Rule `pr`: the card the first work word of `text` names.
    pub fn card_of_work(&self, text: &str) -> Option<String> {
        work_words(text).into_iter().find_map(|w| {
            if is_head(&w) {
                self.heads
                    .iter()
                    .find(|(h, _)| h.starts_with(&w) || w.starts_with(h.as_str()))
                    .map(|(_, c)| c.clone())
            } else {
                self.work.get(&w).cloned()
            }
        })
    }
}

/// Rules `card_text` and `pr` (any record): the card its own text names. The one call the
/// mirror and the ledger's event cards use too (7ebca 04:3x), so the rules live once.
pub fn card_of(text: &str, plan: &Plan) -> Option<(String, By)> {
    card_in_text(text, plan)
        .map(|c| (c, By::Text))
        .or_else(|| plan.card_of_work(text).map(|c| (c, By::Work)))
        .or_else(|| card_of_file(text, plan).map(|c| (c, By::File)))
}

/// Rule `file`: the card of the first document `text` names by its file name.
pub fn card_of_file(text: &str, plan: &Plan) -> Option<String> {
    doc_names(text)
        .into_iter()
        .find_map(|w| plan.files.get(&w).cloned())
}

/// The document file names in `text`: `REVIEW-x-a9136-1119.md` and the like (an upper-case
/// kind, `-`, ending `.md` / `.txt`), also glued to Chinese text or a path; no RELAY (its
/// words are already the letter's).
pub fn doc_names(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    for w in text
        .split(|c: char| c.is_whitespace() || "()()「」『』,,:::;;、。/\\\"'`<>[]【】".contains(c))
    {
        let Some(start) = w.find(|c: char| c.is_ascii_uppercase()) else {
            continue;
        };
        let w = &w[start..];
        let Some(end) = [".md", ".txt"]
            .iter()
            .filter_map(|e| w.find(e).map(|i| i + e.len()))
            .min()
        else {
            continue;
        };
        let name = &w[..end];
        let kind = name.split('-').next().unwrap_or("");
        let ok = name.contains('-')
            && kind.len() >= 2
            && kind.bytes().all(|b| b.is_ascii_uppercase())
            && kind != "RELAY"
            && name
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b));
        if ok && !out.iter().any(|n| n == name) {
            out.push(name.to_owned());
        }
    }
    out
}

/// More of [`Plan::files`] (519d0 07:29: rule `file` was 0): each document a record names
/// that is in the documents folder (the folder walls of [`crate::docs::resolve_in`]), on the
/// card its own text names. At most 64 KB of each is read.
pub fn named_files(
    items: &[Item],
    pending: &std::path::Path,
    plan: &Plan,
) -> BTreeMap<String, String> {
    let mut seen: BTreeSet<String> = BTreeSet::new();
    let mut out = BTreeMap::new();
    for name in items.iter().flat_map(|i| doc_names(&i.body)) {
        if plan.files.contains_key(&name) || !seen.insert(name.clone()) {
            continue;
        }
        let Ok(path) = crate::docs::resolve_in(pending, &name) else {
            continue;
        };
        let Ok(bytes) = std::fs::read(&path) else {
            continue;
        };
        let text = String::from_utf8_lossy(&bytes[..bytes.len().min(64 * 1024)]).into_owned();
        // the card the document is about is in its opening lines (title, node:, first
        // sentence); one named in passing further down is not (519d0 08:18: 「T13 +88」)
        let head: String = text
            .lines()
            .filter(|l| !l.trim().is_empty())
            .take(DOC_HEAD_LINES)
            .collect::<Vec<_>>()
            .join("\n");
        if let Some(c) = card_in_text(&head, plan).or_else(|| plan.card_of_work(&head)) {
            out.insert(name, c);
        }
    }
    out
}

/// The documents' file names and the cards they went to (by their own text).
pub fn files_of(items: &[Item]) -> BTreeMap<String, String> {
    items
        .iter()
        .filter(|i| i.doc.is_some())
        .filter_map(|i| {
            let file = i.source.get("id").and_then(Value::as_str)?;
            Some((file.to_owned(), i.card.clone()?))
        })
        .collect()
}

/// All five rules for one letter: `answered` is the card of the mail it answers or replaces
/// (rule `reply`), `before` the time and card of the mail before it between the same two seats
/// (rule `context`, when at most [`NEAR_SECS`] before `at`).
pub fn card_of_mail(
    text: &str,
    plan: &Plan,
    answered: Option<&str>,
    before: Option<(i64, &str)>,
    at: i64,
) -> (Option<String>, By) {
    let found = card_of(text, plan)
        .or_else(|| answered.map(|c| (c.to_owned(), By::Reply)))
        .or_else(|| {
            before
                .filter(|(t, _)| at - t <= NEAR_SECS)
                .map(|(_, c)| (c.to_owned(), By::Near))
        });
    match found {
        Some((c, b)) => (Some(c), b),
        None => (None, By::None),
    }
}

/// Rule `seat_day` (519d0 05:12, 06:00): the card the sender (else the receiver) last left a
/// mark on that Taipei day, at or before the letter.
pub fn seat_day_card(plan: &Plan, seats: &[String], at: i64) -> Option<String> {
    let day = tpe_day(at);
    seats.iter().find_map(|seat| {
        plan.seat_marks
            .get(seat)?
            .iter()
            .rev()
            .find(|(t, _)| *t <= at && tpe_day(*t) == day)
            .map(|(_, c)| c.clone())
    })
}

/// The marks of [`Plan::seat_marks`]: status lines naming a card, and the plan tree's notes.
pub fn seat_marks_of(
    logs: &[(String, Vec<aib_plan::mainline::Entry>)],
    plan_events: &str,
    plan: &Plan,
) -> BTreeMap<String, Vec<(i64, String)>> {
    let mut m: BTreeMap<String, Vec<(i64, String)>> = BTreeMap::new();
    for (seat, log) in logs {
        for e in log {
            if let Some((c, _)) = card_of(&e.item, plan) {
                m.entry(seat.clone()).or_default().push((e.seen, c));
            }
        }
    }
    for v in plan_events
        .lines()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
    {
        let s = |k: &str| v.get(k).and_then(Value::as_str).unwrap_or("");
        let (Some(seat), Some(ts)) = (im::seat_code(s("by")), v.get("ts").and_then(Value::as_i64))
        else {
            continue;
        };
        if plan.ids.contains(s("id")) {
            m.entry(seat.to_owned())
                .or_default()
                .push((ts, s("id").to_owned()));
        }
    }
    for v in m.values_mut() {
        v.sort();
    }
    m
}

/// The seat in a ledger's sender label: 「@7ebca(AIB2訊息線直接翻工程師)」, 「…@7ebca」.
pub fn label_seat(label: &str) -> Option<String> {
    let after = &label[label.rfind('@')? + 1..];
    let w: String = after
        .chars()
        .take_while(char::is_ascii_alphanumeric)
        .collect();
    im::seat_code(&w).map(str::to_owned)
}

/// The seat a letter's body opens with: 「7ebca:…」「@abb89 → …」「f1fae 轉 Andy」.
pub fn opening_seat(body: &str) -> Option<String> {
    let t = body.trim_start().trim_start_matches('@');
    let head: String = t.chars().take_while(char::is_ascii_alphanumeric).collect();
    let rest = t[head.len()..].trim_start();
    let opens = [":", ":", "→", "#", "轉"]
        .iter()
        .any(|p| rest.starts_with(p));
    im::seat_code(&head).filter(|_| opens).map(str::to_owned)
}

/// A letter's sender seat from its label, else its opening.
pub fn sender_of(label: &str, body: &str) -> Option<String> {
    label_seat(label).or_else(|| opening_seat(body))
}

/// A letter from the system itself (a reminder, a watcher, a notice), not from a seat or a
/// person: no sender seat and a label or opening that says so.
pub fn is_system(label: &str, body: &str) -> bool {
    const MARKS: [&str; 9] = [
        "auto-find-work",
        "提醒",
        "mergewatch",
        "[auto-",
        "【中斷通知",
        "[AIB 自動",
        "自動精煉",
        "開席選單",
        "系統",
    ];
    MARKS
        .iter()
        .any(|m| label.contains(m) || body.trim_start().starts_with(m))
}

/// The Taipei date of a unix time.
pub fn tpe_day(at: i64) -> String {
    crate::sessions::rfc3339_of_unix(at + aib_plan::mainline::TAIPEI)
        .chars()
        .take(10)
        .collect()
}

/// The mail ids (`q_` and 16 hex) a text names.
pub fn quoted_ids(text: &str) -> Vec<String> {
    text.split(|c: char| !(c.is_ascii_alphanumeric() || c == '_'))
        .filter(|w| {
            w.len() == 18 && w.starts_with("q_") && w[2..].bytes().all(|b| b.is_ascii_hexdigit())
        })
        .map(str::to_owned)
        .collect()
}

/// Put every record on its card, oldest first (rules `reply` and `context` follow earlier mail).
pub fn route(mut items: Vec<Item>, plan: &Plan) -> Vec<Routed> {
    items.sort_by(|a, b| (a.at, &a.key).cmp(&(b.at, &b.key)));
    let mut by_id: BTreeMap<String, String> = BTreeMap::new();
    let mut by_ledger: BTreeMap<String, String> = BTreeMap::new();
    // each pair's last anchor: (time, card, the pair's letters since it)
    let mut last: BTreeMap<String, (i64, String, usize)> = BTreeMap::new();
    // 519d0 07:29 (unassigned 40.8%, mostly seats' own letters with no card or PR): the card
    // each seat's last anchor (sent or got) was on, for its next letters that day, as near
    // as context (519d0 08:11): (time, card, the seat's letters since it)
    let mut seat_last: BTreeMap<String, (i64, String, usize)> = BTreeMap::new();
    let mut out = Vec::with_capacity(items.len());
    for mut it in items {
        let s = |k: &str| {
            it.source
                .get(k)
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_owned()
        };
        let (kind, id, ledger, replaces) =
            (s("kind"), s("id"), s("ledger_thread"), s("supersedes"));
        let mail = kind == "dm";
        if mail {
            let said = s("said_seat");
            let from = if said.is_empty() {
                it.from.trim_start_matches("seat:").to_owned()
            } else {
                said
            };
            for x in [from, s("to")] {
                if let Some((_, _, n)) = seat_last.get_mut(&x) {
                    *n += 1;
                }
            }
        }
        let own = it.card.clone().filter(|c| plan.ids.contains(c));
        let (card, by) = match own {
            Some(c) => (Some(c), By::Text),
            None if mail => {
                // the mail it answers: the ledger's thread, what it replaces, or a q_ id it names
                let named = quoted_ids(&it.body)
                    .into_iter()
                    .find_map(|q| by_id.get(&q).cloned());
                let answered = by_ledger
                    .get(&ledger)
                    .or_else(|| by_id.get(&replaces))
                    .cloned()
                    .or(named);
                let answered = answered.as_ref();
                let (c, b) =
                    card_of_mail(&it.body, plan, answered.map(String::as_str), None, it.at);
                // the import's context: the pair's anchor, near enough (CONTEXT_SECS / _MAX)
                // a letter naming its own work (a PR, head, branch) no card has is about
                // something else: it takes no context (519d0 08:32 B: #631 after #637)
                let elsewhere = names_other_work(&it.body);
                let same_day = last.get_mut(&it.thread).and_then(|(t, c, n)| {
                    *n += 1;
                    let near = tpe_day(*t) == tpe_day(it.at)
                        && it.at - *t <= CONTEXT_SECS
                        && CONTEXT_MAX >= *n
                        && !elsewhere;
                    near.then(|| c.clone())
                });
                match (c, same_day) {
                    (Some(c), _) => (Some(c), b),
                    (None, Some(c)) => (Some(c), By::Near),
                    (None, None) => {
                        let said = s("said_seat");
                        let from = if said.is_empty() {
                            it.from.trim_start_matches("seat:").to_owned()
                        } else {
                            said
                        };
                        // the sender's own day only: a letter to a busy seat (the hub gets
                        // every seat's letters) is not on what that seat last did (519d0
                        // 08:18: seat_day 9.4%, S15 to seats writing to f1fae)
                        let seats = [from];
                        let system = it.source.get("system") == Some(&json!(true));
                        let today = tpe_day(it.at);
                        let letters = || {
                            seats.iter().find_map(|x| {
                                seat_last
                                    .get(x)
                                    .filter(|(t, _, n)| {
                                        tpe_day(*t) == today
                                            && it.at <= *t + CONTEXT_SECS
                                            && *n <= CONTEXT_MAX
                                    })
                                    .map(|(_, c, _)| c.clone())
                            })
                        };
                        // a seat read off the letter's opening words only may be the one it
                        // is to: it takes no seat's day (519d0 08:27: offhub's 「f1fae:…」
                        // letters on f1fae's S15)
                        let guessed = s("said_from") == "opening";
                        let day = || seat_day_card(plan, &seats, it.at).or_else(letters);
                        // nor one about other work (B)
                        match (!guessed && !elsewhere).then(day).flatten() {
                            Some(c) if !system => (Some(c), By::SeatDay),
                            _ if system => (None, By::System),
                            _ => (None, By::None),
                        }
                    }
                }
            }
            // an idea is on the card it names (node) or in its seat's bucket (519d0 09:11)
            None if kind == "idea" => (None, By::None),
            None => card_of(&it.body, plan).map_or((None, By::None), |(c, b)| (Some(c), b)),
        };
        it.card = card;
        it.card_fallback = false;
        // Andy 11-19 a50-2: a letter still without a card goes in its sender's bucket, under
        // the sender's main item: its last status-line or plan mark at or before the letter,
        // else the card of its last anchored letter (status lines are kept since 11-12 only:
        // 519d0 08:49, f1fae's bucket was all 「-」). A seat read off the opening words has a
        // bucket too (it only takes no seat_day).
        if (mail || kind == "idea") && by == By::None {
            let said = it.source.get("said_seat").and_then(Value::as_str);
            let from = said
                .unwrap_or_else(|| it.from.trim_start_matches("seat:"))
                .to_owned();
            if im::seat_code(&from).is_some() {
                let main = plan
                    .seat_marks
                    .get(&from)
                    .and_then(|v| {
                        v.iter()
                            .rev()
                            .find(|(t, _)| *t <= it.at)
                            .map(|(_, c)| c.clone())
                    })
                    .or_else(|| seat_last.get(&from).map(|(_, c, _)| c.clone()));
                it.source["bucket_seat"] = json!(from);
                it.source["bucket_card"] = json!(main);
            }
        }
        // only an anchor is followed: a card the letter got from its pair or seat is not
        // passed on (519d0 08:11: context chains carried T13 across seats and days)
        let anchor = matches!(by, By::Text | By::Work | By::File | By::Reply);
        if let (true, true, Some(c)) = (mail, anchor, &it.card) {
            by_id.insert(id, c.clone());
            if !ledger.is_empty() {
                by_ledger.entry(ledger).or_insert_with(|| c.clone());
            }
            last.insert(it.thread.clone(), (it.at, c.clone(), 0));
            let said = it.source.get("said_seat").and_then(Value::as_str);
            let from = said.unwrap_or_else(|| it.from.trim_start_matches("seat:"));
            let to = it.source.get("to").and_then(Value::as_str).unwrap_or("");
            for x in [from, to] {
                if im::seat_code(x).is_some() {
                    seat_last.insert(x.to_owned(), (it.at, c.clone(), 0));
                }
            }
        }
        out.push(Routed { item: it, by });
    }
    // the same pair's next anchor that day, for the letters before it (519d0 07:29), as
    // near as context (08:11)
    let mut next: BTreeMap<String, (i64, String, usize)> = BTreeMap::new();
    for r in out.iter_mut().rev() {
        if r.item.source.get("kind").and_then(Value::as_str) != Some("dm") {
            continue;
        }
        let anchor = matches!(r.by, By::Text | By::Work | By::File | By::Reply);
        match (&r.item.card, anchor) {
            (Some(c), true) => {
                next.insert(r.item.thread.clone(), (r.item.at, c.clone(), 0));
            }
            (None, _) if r.by == By::None => {
                if let Some((t, c, n)) = next.get_mut(&r.item.thread) {
                    *n += 1;
                    if tpe_day(*t) == tpe_day(r.item.at)
                        && !names_other_work(&r.item.body)
                        && *t - r.item.at <= CONTEXT_SECS
                        && *n <= CONTEXT_MAX
                    {
                        r.item.card = Some(c.clone());
                        r.by = By::Near;
                    }
                }
            }
            _ => {
                if let Some((_, _, n)) = next.get_mut(&r.item.thread) {
                    *n += 1;
                }
            }
        }
    }
    out
}

/// A document's post: its title, its first lines (no front matter, no headings), the file.
pub fn short_doc(file: &str, text: &str) -> String {
    let title = crate::import::title_of(file, text);
    let mut lines = text.lines();
    let mut body: Vec<&str> = Vec::new();
    let mut front = text.starts_with("---");
    if front {
        lines.next();
    }
    for l in lines {
        if front {
            front = l.trim() != "---";
            continue;
        }
        let l = l.trim();
        if l.is_empty() || l.starts_with('#') {
            continue;
        }
        body.push(l);
        if body.len() == DOC_LINES {
            break;
        }
    }
    let mut out = format!("📄 {title}\n");
    for l in body {
        let l: String = l.chars().take(160).collect();
        out.push_str(&l);
        out.push('\n');
    }
    out.push_str(&format!("(全文:開檔 {file})"));
    out
}

/// The documents, one short post each.
/// The suggestion ledger (`<state>/ideas/ideas.jsonl`, aib-idea; 519d0 09:11, Andy 09:1x
/// 「好，朝這個方向做」): one post per `add`, on its `node` when the plan has it, else in the
/// seat's bucket; `review_batch` / `when` add nothing but the state the one post shows. A
/// line not read is skipped.
pub fn idea_items(r: &Roots, plan: &Plan, skipped: &mut usize) -> Vec<Item> {
    let Ok(text) = std::fs::read_to_string(r.state.join("ideas").join("ideas.jsonl")) else {
        return Vec::new();
    };
    let mut adds: Vec<Value> = Vec::new();
    let mut state: BTreeMap<String, String> = BTreeMap::new();
    // ideas someone already decided (519d0 09:17): no longer waiting on a person
    let mut decided: BTreeSet<String> = BTreeSet::new();
    let mut lines: Vec<Value> = Vec::new();
    for l in text.lines().filter(|l| !l.trim().is_empty()) {
        match serde_json::from_str::<Value>(l) {
            Ok(v) => lines.push(v),
            Err(_) => *skipped += 1,
        }
    }
    lines.sort_by_key(|v| v.get("at_ms").and_then(Value::as_i64).unwrap_or(0));
    let st = |v: &Value, k: &str| v.get(k).and_then(Value::as_str).unwrap_or("").to_owned();
    for v in lines {
        match v.get("ev").and_then(Value::as_str) {
            Some("add") => adds.push(v),
            Some("review_batch") => {
                let said = match st(&v, "verdict").as_str() {
                    "ok" => "Andy 看過、照建議",
                    "no" => "Andy 看過、不做",
                    _ => "Andy 看過",
                };
                let quote = st(&v, "quote");
                let line = if quote.is_empty() {
                    said.to_owned()
                } else {
                    format!("{said} {quote}")
                };
                for id in v.get("ids").and_then(Value::as_array).into_iter().flatten() {
                    if let Some(id) = id.as_str() {
                        state.insert(id.to_owned(), line.clone());
                        decided.insert(id.to_owned());
                    }
                }
            }
            Some("when") => {
                let line = format!("等:{}(最早 {})", st(&v, "blocked_by"), st(&v, "earliest"));
                state.insert(st(&v, "idea_id"), line);
            }
            _ => *skipped += 1,
        }
    }
    let mut out = Vec::new();
    for v in adds {
        let id = st(&v, "idea_id");
        if id.is_empty() {
            *skipped += 1;
            continue;
        }
        let at = v.get("at_ms").and_then(Value::as_i64).unwrap_or(0) / 1000;
        let human = st(&v, "classification") == "needs_human";
        let axis = match st(&v, "axis").as_str() {
            "stable" => "穩定".to_owned(),
            "fast" => "快速".to_owned(),
            "simple" => "簡化".to_owned(),
            "surprise" | "unexpected" => "沒想到".to_owned(),
            other => other.to_owned(),
        };
        let consent = if human {
            "需人同意"
        } else {
            "自動同意優化"
        };
        let now = state
            .get(&id)
            .cloned()
            .unwrap_or_else(|| "未處理".to_owned());
        let body = im::clean(&format!(
            "{} | {axis} | {consent} {}\n建議:{}\n狀態:{now}",
            st(&v, "grade"),
            st(&v, "title"),
            st(&v, "suggest")
        ));
        let node = st(&v, "node");
        let seat = st(&v, "recorded_by_seat");
        let mut source = json!({"kind": "idea", "id": id, "grade": st(&v, "grade"),
            "classification": st(&v, "classification"), "idea_state": now,
            "at": crate::sessions::rfc3339_of_unix(at)});
        if human && !decided.contains(&id) {
            // 7ebca's slot 1 reads it; a decided one is not put before Andy again
            source["needs_human"] = json!(true);
        }
        out.push(Item {
            thread: String::new(),
            thread_title: String::new(),
            category: None,
            key: format!("idea:{id}"),
            from: format!(
                "seat:{}",
                im::seat_code(&seat).map_or("import", |_| seat.as_str())
            ),
            at,
            card: plan.ids.contains(&node).then_some(node),
            source,
            body,
            doc: None,
            card_fallback: false,
        });
    }
    out
}

pub fn doc_items(r: &Roots, skipped: &mut usize) -> Vec<Item> {
    let mut out = Vec::new();
    for (prefix, kind) in DOC_KINDS {
        let mut files: Vec<std::path::PathBuf> = std::fs::read_dir(&r.pending)
            .into_iter()
            .flatten()
            .filter_map(|e| e.ok())
            .filter(|e| {
                e.file_name()
                    .to_str()
                    .is_some_and(|n| n.starts_with(prefix) && n.ends_with(".md"))
            })
            .map(|e| e.path())
            .collect();
        files.sort();
        for p in files {
            let file = p
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("")
                .to_owned();
            if crate::docs::secret_name(&file) || crate::docs::resolve(&r.pending, &file).is_err() {
                *skipped += 1;
                continue;
            }
            let Ok(text) = std::fs::read_to_string(&p) else {
                *skipped += 1;
                continue;
            };
            let at = std::fs::metadata(&p)
                .and_then(|m| m.modified())
                .ok()
                .and_then(|m| m.duration_since(std::time::UNIX_EPOCH).ok())
                .and_then(|d| i64::try_from(d.as_secs()).ok())
                .unwrap_or(0);
            let title = crate::import::title_of(&file, &text);
            out.push(Item {
                thread: String::new(),
                thread_title: String::new(),
                category: None,
                key: format!("{kind}:{file}"),
                from: format!(
                    "seat:{}",
                    crate::import::seat_of(&file).unwrap_or_else(|| "import".into())
                ),
                at,
                // the card is looked for in the whole text, the post keeps the start
                card: None,
                source: json!({"kind": kind, "id": file, "at": crate::sessions::rfc3339_of_unix(at),
                    "text_for_card": im::clean(&text).chars().take(20_000).collect::<String>()}),
                body: im::clean(&short_doc(&file, &text)),
                doc: Some(json!([{"root": "pending", "file": file, "title": title}])),
                card_fallback: false,
            });
        }
    }
    out
}

/// Every record this import reads: the mail and the 影子續跑 records whole, the documents short.
pub fn items_all(r: &Roots, plan: &Plan) -> (Vec<Item>, usize) {
    let mut skipped = 0;
    let none = BTreeSet::new();
    let mut v = im::dm_items(r, &none, &mut skipped);
    v.extend(im::shadow_items(r, &none, &mut skipped));
    v.extend(doc_items(r, &mut skipped));
    v.extend(correction_items(r, plan));
    v.extend(seat_day_items(r, plan));
    v.extend(idea_items(r, plan, &mut skipped));
    // a document's card is looked for in its whole text, not only the short post
    for it in v.iter_mut() {
        if let Some(t) = it.source.get("text_for_card").and_then(Value::as_str) {
            it.card = card_in_text(t, plan).or_else(|| plan.card_of_work(t));
        }
        if let Some(o) = it.source.as_object_mut() {
            o.remove("text_for_card");
        }
    }
    (v, skipped)
}

/// Each seat's status line history (`<state>/seat-now/<seat>.history.jsonl`), read as aib-plan
/// reads it ([`aib_plan::mainline::parse_log`]), by seat.
pub fn seat_logs(r: &Roots) -> Vec<(String, Vec<aib_plan::mainline::Entry>)> {
    let dir = r.state.join("seat-now");
    let mut v: Vec<(String, Vec<aib_plan::mainline::Entry>)> = std::fs::read_dir(&dir)
        .into_iter()
        .flatten()
        .filter_map(|e| e.ok())
        .filter_map(|e| {
            let name = e.file_name().to_str()?.to_owned();
            let seat = im::seat_code(name.strip_suffix(".history.jsonl")?)?.to_owned();
            let text = std::fs::read_to_string(e.path()).ok()?;
            Some((seat, aib_plan::mainline::parse_log(&text)))
        })
        .collect();
    v.sort_by(|a, b| a.0.cmp(&b.0));
    v
}

/// The seat's current 判斷 (`seat-now/<seat>.json` `review`): 符合度, why, idea.
fn current_review(r: &Roots, seat: &str) -> Option<(u8, String, String)> {
    let text =
        std::fs::read_to_string(r.state.join("seat-now").join(format!("{seat}.json"))).ok()?;
    let v: Value = serde_json::from_str(&text).ok()?;
    let rv = v.get("review")?;
    let s = |k: &str| rv.get(k).and_then(Value::as_str).unwrap_or("").to_owned();
    Some((
        aib_plan::mainline::track_of(rv.get("track")?)?,
        s("why"),
        s("idea"),
    ))
}

/// 判斷 and 校正 (NOTE r6 §3c, the old cockpit's 「判斷 72% 校正 1 · 總結 1 · 交接 1」 as
/// aib-plan counts it): a 判斷 is what a seat wrote when it handed back (符合度, why, idea); each
/// history line with a 符合度 is one `judgement` post on the card its line names (why / idea only
/// on the current one). A 校正 is a 判斷 back on the main line (≥ [`ON_TRACK_MIN`]) right after
/// one off it, the same Taipei day (aib-plan `corrections`): one `correction` post more, 「偏到
/// N% → 回到 M%」. Both are the seat's own word (5d33a CHECK-import-selfcheck M1): 「自評 ·
/// <seat>」, the card is 「狀態列自報」.
pub fn correction_items(r: &Roots, plan: &Plan) -> Vec<Item> {
    let mut out = Vec::new();
    for (seat, log) in seat_logs(r) {
        let review = current_review(r, &seat);
        let last = log.iter().rposition(|e| e.track.is_some());
        let mut before: Option<(u8, i64)> = None;
        for (i, e) in log.iter().enumerate() {
            let Some(pct) = e.track else { continue };
            let card = card_of(&e.item, plan).map(|(c, _)| c);
            let back = before
                .filter(|(p, at)| *p < ON_TRACK_MIN && tpe_day(*at) == tpe_day(e.seen))
                .filter(|_| pct >= ON_TRACK_MIN);
            before = Some((pct, e.seen));
            if let Some((off, _)) = back {
                out.push(Item {
                    thread: String::new(),
                    thread_title: String::new(),
                    category: None,
                    key: format!("correction:{seat}:{}:{i}", e.seen),
                    from: format!("seat:{seat}"),
                    at: e.seen,
                    body: format!(
                        "〔自評 · {seat}〕校正:偏到 {off}% → 回到 {pct}% · {}",
                        e.item
                    ),
                    source: json!({"kind": "correction", "id": format!("{seat}:{}:{i}", e.seen),
                        "at": crate::sessions::rfc3339_of_unix(e.seen), "seat": seat,
                        "from_track": off, "track": pct, "card_said": "status_line"}),
                    card: card.clone(),
                    card_fallback: false,
                    doc: None,
                });
            }
            let mut body = format!("〔自評 · {seat}〕判斷 {pct}% · {}", e.item);
            let mut src = json!({"kind": "judgement", "id": format!("{seat}:{}:{i}", e.seen),
                "at": crate::sessions::rfc3339_of_unix(e.seen), "seat": seat, "track": pct,
                "card_said": "status_line"});
            if let Some((_, why, idea)) = review.as_ref().filter(|v| Some(i) == last && v.0 == pct)
            {
                body.push_str(&format!("\n為什麼:{why}\n建議:{idea}"));
                src["why"] = json!(why);
                src["idea"] = json!(idea);
            }
            out.push(Item {
                thread: String::new(),
                thread_title: String::new(),
                category: None,
                key: format!("judgement:{seat}:{}:{i}", e.seen),
                from: format!("seat:{seat}"),
                at: e.seen,
                body,
                source: src,
                card,
                card_fallback: false,
                doc: None,
            });
        }
    }
    out
}

/// The summaries (`<pending>/to-maintainer-from-<seat>-*`, by change time) and the hand-overs
/// (`<state>/handoff/<seat>.receipts.jsonl`, complete ones, one per round) of each Taipei day —
/// what aib-plan's `day_counts` counts for today.
fn day_counts(r: &Roots, seat: &str) -> (BTreeMap<String, u32>, BTreeMap<String, u32>) {
    let mut sums: BTreeMap<String, u32> = BTreeMap::new();
    let prefix = format!("to-maintainer-from-{seat}-");
    for e in std::fs::read_dir(&r.pending)
        .into_iter()
        .flatten()
        .flatten()
    {
        if !e.file_name().to_string_lossy().starts_with(&prefix) {
            continue;
        }
        let at = e
            .metadata()
            .ok()
            .and_then(|m| m.modified().ok())
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map_or(0, |d| i64::try_from(d.as_secs()).unwrap_or(0));
        *sums.entry(tpe_day(at)).or_default() += 1;
    }
    let mut rounds: BTreeMap<String, BTreeSet<u64>> = BTreeMap::new();
    let text = std::fs::read_to_string(
        r.state
            .join("handoff")
            .join(format!("{seat}.receipts.jsonl")),
    )
    .unwrap_or_default();
    for v in text
        .lines()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
    {
        let done = v
            .get("incomplete")
            .is_none_or(|x| x.as_array().is_some_and(Vec::is_empty));
        let mismatch = v.get("seat_mismatch").and_then(Value::as_bool) == Some(true);
        let Some(ts) = v.get("ts").and_then(Value::as_f64) else {
            continue;
        };
        if !done || mismatch {
            continue;
        }
        let round = v.get("round_ts").and_then(Value::as_f64).unwrap_or(ts);
        #[allow(clippy::cast_possible_truncation)]
        let day = tpe_day(ts as i64);
        rounds.entry(day).or_default().insert(round.to_bits());
    }
    let hands = rounds
        .into_iter()
        .map(|(d, s)| (d, u32::try_from(s.len()).unwrap_or(u32::MAX)))
        .collect();
    (sums, hands)
}

/// The old cockpit's row, per seat and Taipei day with status lines: 判斷 (the day's last
/// 符合度) · 校正 (off the main line → back that day, aib-plan `corrections`) · 總結 · 交接,
/// and the day's first and current goal.
/// One post on the card of the current goal.
pub fn seat_day_items(r: &Roots, plan: &Plan) -> Vec<Item> {
    let mut out = Vec::new();
    for (seat, log) in seat_logs(r) {
        let (sums, hands) = day_counts(r, &seat);
        let mut days: BTreeMap<String, Vec<&aib_plan::mainline::Entry>> = BTreeMap::new();
        for e in &log {
            days.entry(tpe_day(e.seen)).or_default().push(e);
        }
        for (day, mut rows) in days {
            rows.sort_by_key(|e| e.seen);
            let (Some(first), Some(cur)) = (rows.first(), rows.last()) else {
                continue;
            };
            let judge = rows.iter().rev().find_map(|e| e.track);
            let tracks: Vec<u8> = rows.iter().filter_map(|e| e.track).collect();
            let corrections = tracks
                .windows(2)
                .filter(|w| w[0] < ON_TRACK_MIN && w[1] >= ON_TRACK_MIN)
                .count();
            let (n_sum, n_hand) = (
                sums.get(&day).copied().unwrap_or(0),
                hands.get(&day).copied().unwrap_or(0),
            );
            let hm = |at: i64| -> String {
                crate::sessions::rfc3339_of_unix(at + aib_plan::mainline::TAIPEI)
                    .chars()
                    .skip(11)
                    .take(5)
                    .collect()
            };
            let pct = judge.map_or("—".to_owned(), |p| format!("{p}%"));
            let body = format!(
                "〔自評 · {seat}〕{day} 判斷 {pct} · 校正 {corrections} · 總結 {n_sum} · 交接 {n_hand}\n最初目標 {} {}\n目前目標 {} {}",
                hm(first.seen),
                first.item,
                hm(cur.seen),
                cur.item
            );
            out.push(Item {
                thread: String::new(),
                thread_title: String::new(),
                category: None,
                key: format!("seat_day:{seat}:{day}"),
                from: format!("seat:{seat}"),
                at: cur.seen,
                body,
                source: json!({"kind": "seat_day", "id": format!("{seat}:{day}"),
                    "at": crate::sessions::rfc3339_of_unix(cur.seen), "seat": seat, "day": day,
                    "judge": judge, "corrections": corrections, "summaries": n_sum,
                    "handoffs": n_hand, "card_said": "status_line",
                    "first_goal": {"at": first.seen, "item": first.item},
                    "goal": {"at": cur.seen, "item": cur.item}}),
                card: card_of(&cur.item, plan).map(|(c, _)| c),
                card_fallback: false,
                doc: None,
            });
        }
    }
    out
}

/// The day a record without a card is put under (Taipei date of its time).
pub fn day_of(at: i64) -> String {
    tpe_day(at)
}

/// Where a routed record is shown, before any thread exists: `card:<id>` or `none:<day>`.
pub fn place_of(r: &Routed) -> String {
    if let Some(seat) = seat_day_of(&r.item) {
        return format!("seat_day:{seat}");
    }
    match &r.item.card {
        Some(c) => format!("card:{c}"),
        None if r.by == By::System => format!("system:{}", day_of(r.item.at)),
        None => match bucket_of(&r.item) {
            Some((seat, card)) => format!("bucket:{seat}:{}", card.unwrap_or("-")),
            None => format!("none:{}", day_of(r.item.at)),
        },
    }
}

/// The dry run's table.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Table {
    pub total: usize,
    pub by: BTreeMap<&'static str, usize>,
    /// how many of each kind (mail, 校正, 總結, 交接, …) — NOTE r3 §3c
    pub kinds: BTreeMap<String, usize>,
    /// the old ledger's state of each letter (queued, read, done, …) — NOTE r3 §3b
    pub states: BTreeMap<String, usize>,
    pub cards: usize,
    pub biggest: Vec<(String, usize)>,
    /// the 10 card threads with the most records (519d0 05:12)
    pub top_cards: Vec<(String, usize)>,
    /// the 5 senders with the most records without a card (519d0 05:12)
    pub none_by_from: Vec<(String, usize)>,
}

pub fn table_of(routed: &[Routed]) -> Table {
    let mut t = Table {
        total: routed.len(),
        ..Table::default()
    };
    let mut places: BTreeMap<String, usize> = BTreeMap::new();
    let mut none_from: BTreeMap<String, usize> = BTreeMap::new();
    for r in routed {
        if r.by == By::None {
            *none_from.entry(r.item.from.clone()).or_default() += 1;
        }
        *t.by.entry(r.by.word()).or_default() += 1;
        let s = |k: &str| r.item.source.get(k).and_then(Value::as_str).unwrap_or("");
        *t.kinds.entry(s("kind").to_owned()).or_default() += 1;
        if s("kind") == "dm" {
            *t.states.entry(s("status").to_owned()).or_default() += 1;
        }
        *places.entry(place_of(r)).or_default() += 1;
    }
    t.cards = places.keys().filter(|p| p.starts_with("card:")).count();
    let top = |m: BTreeMap<String, usize>, n: usize| {
        let mut v: Vec<(String, usize)> = m.into_iter().collect();
        v.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
        v.truncate(n);
        v
    };
    t.top_cards = top(
        places
            .iter()
            .filter(|(p, _)| p.starts_with("card:"))
            .map(|(p, n)| (p.clone(), *n))
            .collect(),
        10,
    );
    t.biggest = top(places, 10);
    t.none_by_from = top(none_from, 5);
    t
}

/// The table as lines a person reads.
pub fn table_text(t: &Table, skipped: usize) -> String {
    let pct = |n: usize| {
        if t.total == 0 {
            0.0
        } else {
            #[allow(clippy::cast_precision_loss)]
            let p = n as f64 * 100.0 / t.total as f64;
            p
        }
    };
    let mut s = format!(
        "records {} (skipped {skipped}) on {} cards\n",
        t.total, t.cards
    );
    for b in [
        By::Text,
        By::Work,
        By::File,
        By::Reply,
        By::Near,
        By::SeatDay,
        By::System,
        By::None,
    ] {
        let n = t.by.get(b.word()).copied().unwrap_or(0);
        s.push_str(&format!("  {:<10} {n:>6}  {:>5.1}%\n", b.word(), pct(n)));
    }
    s.push_str("kinds:\n");
    for (k, n) in &t.kinds {
        s.push_str(&format!("  {k:<10} {n:>6}\n"));
    }
    s.push_str("letters by ledger state:\n");
    for (k, n) in &t.states {
        s.push_str(&format!("  {k:<10} {n:>6}\n"));
    }
    s.push_str("top card threads:\n");
    for (p, n) in &t.top_cards {
        s.push_str(&format!("  {p:<24} {n:>6}\n"));
    }
    s.push_str("without a card, by sender:\n");
    for (p, n) in &t.none_by_from {
        s.push_str(&format!("  {p:<24} {n:>6}\n"));
    }
    s.push_str("biggest threads:\n");
    for (p, n) in &t.biggest {
        s.push_str(&format!("  {n:>6}  {p}\n"));
    }
    s
}

/// The N cards with the most records.
pub fn sample_cards(routed: &[Routed], n: usize) -> BTreeSet<String> {
    let mut count: BTreeMap<&str, usize> = BTreeMap::new();
    for r in routed {
        if let Some(c) = r.item.card.as_deref() {
            *count.entry(c).or_default() += 1;
        }
    }
    let mut v: Vec<(&str, usize)> = count.into_iter().collect();
    v.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(b.0)));
    v.into_iter().take(n).map(|(c, _)| c.to_owned()).collect()
}

fn now_pair() -> (String, i64) {
    let now = crate::sessions::now_rfc3339();
    let at = crate::revive::unix_of(&now).unwrap_or(0);
    (now, at)
}

/// The card's own thread (the page's for that item), opened when it has none.
pub fn card_thread(db: &MessageDb, card: &str, title: &str) -> Result<String, String> {
    let id = crate::sessions::new_id("t_").map_err(|e| e.to_string())?;
    let (now, at) = now_pair();
    let t = ThreadRow {
        thread_id: id.clone(),
        node: Some(card.to_owned()),
        title: title.to_owned(),
        opened_by: "seat:import".into(),
        created: now,
        created_unix: at,
        last_unix: at,
    };
    match db.thread_open(t, Vec::new()).map_err(|e| e.to_string())? {
        Opened::New => Ok(id),
        Opened::Existing(r) => Ok(r.thread_id),
        Opened::Taken => Err("thread id taken".into()),
    }
}

/// The thread of the records of `day` that found no card.
/// The seat whose day row this is (a `seat_day` record), if it is one.
pub fn seat_day_of(it: &Item) -> Option<&str> {
    (it.source.get("kind").and_then(Value::as_str) == Some("seat_day"))
        .then(|| it.source.get("seat").and_then(Value::as_str))
        .flatten()
}

/// The thread of one seat's day rows: 「<seat> · 每日(匯入)」, category `seat_day` (043dd 05:2x:
/// the seat window reads the newest post of it).
pub fn seat_day_thread(seat: &str) -> String {
    crate::import::thread_of(&format!("regroup-seat-day:{seat}"))
}

/// The thread of one day's system letters (reminders, watchers, notices; 519d0 06:00).
pub fn system_thread(day: &str) -> String {
    crate::import::thread_of(&format!("regroup-system:{day}"))
}

/// A letter's bucket (Andy 11-19 a50-2): its sender seat and that seat's main item then.
pub fn bucket_of(it: &Item) -> Option<(&str, Option<&str>)> {
    let seat = it.source.get("bucket_seat").and_then(Value::as_str)?;
    Some((seat, it.source.get("bucket_card").and_then(Value::as_str)))
}

/// One seat's bucket thread under one main item: 「未歸卡 · <seat>」.
pub fn bucket_thread(seat: &str, card: Option<&str>) -> String {
    crate::import::thread_of(&format!("regroup-bucket:{seat}:{}", card.unwrap_or("-")))
}

pub fn none_thread(day: &str) -> String {
    crate::import::thread_of(&format!("regroup-none:{day}"))
}

/// Write the routed records (only those of `only`'s cards when given).
pub fn write(
    routed: &[Routed],
    plan: &Plan,
    db: &MessageDb,
    only: Option<&BTreeSet<String>>,
) -> Result<Counts, String> {
    let mut c = Counts::default();
    let mut threads: BTreeMap<String, String> = BTreeMap::new();
    // 519d0 06:08: every thread is opened (and its kind set) before any post is written, so a
    // refused thread leaves no half import
    let mut rows = Vec::new();
    for r in routed {
        if let Some(set) = only {
            if !r.item.card.as_ref().is_some_and(|k| set.contains(k)) {
                continue;
            }
        }
        let place = place_of(r);
        let tid = match threads.get(&place) {
            Some(t) => t.clone(),
            None => {
                let t = match (&r.item.card, seat_day_of(&r.item)) {
                    (_, Some(seat)) => {
                        let id = seat_day_thread(seat);
                        let (now, at) = now_pair();
                        let row = ThreadRow {
                            thread_id: id.clone(),
                            node: None,
                            title: format!("{seat} · 每日(匯入)"),
                            opened_by: "seat:import".into(),
                            created: now,
                            created_unix: at,
                            last_unix: at,
                        };
                        db.thread_open(row, Vec::new()).map_err(|e| e.to_string())?;
                        db.thread_set_category(&id, "seat_day")
                            .map_err(|e| e.to_string())?;
                        id
                    }
                    (Some(card), None) => {
                        let title = plan
                            .titles
                            .get(card)
                            .cloned()
                            .unwrap_or_else(|| card.clone());
                        card_thread(db, card, &title)?
                    }
                    (None, None) if r.by == By::System => {
                        let day = day_of(r.item.at);
                        let id = system_thread(&day);
                        let (now, at) = now_pair();
                        let row = ThreadRow {
                            thread_id: id.clone(),
                            node: None,
                            title: format!("系統通知 · {day}(匯入)"),
                            opened_by: "seat:import".into(),
                            created: now,
                            created_unix: at,
                            last_unix: at,
                        };
                        db.thread_open(row, Vec::new()).map_err(|e| e.to_string())?;
                        db.thread_set_category(&id, "system")
                            .map_err(|e| e.to_string())?;
                        id
                    }
                    (None, None) if bucket_of(&r.item).is_some() => {
                        let (seat, card) = bucket_of(&r.item).unwrap_or(("", None));
                        let id = bucket_thread(seat, card);
                        let (now, at) = now_pair();
                        let title = match card {
                            Some(c) => format!(
                                "未歸卡 · {seat}(主項目 {})(匯入)",
                                plan.titles.get(c).map_or(c, String::as_str)
                            ),
                            None => format!("未歸卡 · {seat}(匯入)"),
                        };
                        let row = ThreadRow {
                            thread_id: id.clone(),
                            node: card.map(str::to_owned),
                            title,
                            opened_by: "seat:import".into(),
                            created: now,
                            created_unix: at,
                            last_unix: at,
                        };
                        db.thread_open(row, Vec::new()).map_err(|e| e.to_string())?;
                        id
                    }
                    (None, None) => {
                        let day = day_of(r.item.at);
                        let id = none_thread(&day);
                        let (now, at) = now_pair();
                        let row = ThreadRow {
                            thread_id: id.clone(),
                            node: None,
                            title: format!("未歸卡 · {day}(匯入)"),
                            opened_by: "seat:import".into(),
                            created: now,
                            created_unix: at,
                            last_unix: at,
                        };
                        db.thread_open(row, Vec::new()).map_err(|e| e.to_string())?;
                        id
                    }
                };
                threads.insert(place, t.clone());
                t
            }
        };
        let it = Item {
            thread: tid,
            key: format!("{KEY_PREFIX}{}", r.item.key),
            ..r.item.clone()
        };
        let mut row = im::row_of(&it)?;
        row.item.insert("card_rule".into(), json!(r.by.word()));
        for k in [
            "track",
            "from_track",
            "why",
            "idea",
            "card_said",
            "seat",
            "day",
            "judge",
            "corrections",
            "summaries",
            "handoffs",
            "first_goal",
            "goal",
            // ideas (519d0 09:11): 7ebca's slot 1 reads needs_human
            "needs_human",
            "grade",
            "classification",
            "idea_state",
        ] {
            if let Some(v) = r.item.source.get(k) {
                row.item.insert(k.into(), v.clone());
            }
        }
        rows.push(row);
    }
    for row in rows {
        match db.post_insert(row, Vec::new()) {
            Ok(Inserted::New) => c.new += 1,
            Ok(Inserted::Existing(_)) => c.already += 1,
            Err(e) => {
                return Err(format!(
                    "{e} ({} posts were written first; `aibd import regroup --undo` with the \
                     same cards takes them out)",
                    c.new
                ))
            }
        }
    }
    Ok(c)
}

/// `--undo`: this import's posts in every thread it writes to, and each thread left empty.
pub fn undo(routed: &[Routed], db: &MessageDb) -> Result<Done, String> {
    let mut threads: BTreeSet<String> = BTreeSet::new();
    for r in routed {
        if let Some(seat) = seat_day_of(&r.item) {
            threads.insert(seat_day_thread(seat));
            continue;
        }
        match &r.item.card {
            Some(card) => {
                let found = db
                    .threads_list(Some(card), None, 1)
                    .map_err(|e| e.to_string())?;
                threads.extend(found.into_iter().map(|t| t.thread_id));
            }
            None if r.by == By::System => {
                threads.insert(system_thread(&day_of(r.item.at)));
            }
            None => {
                threads.insert(match bucket_of(&r.item) {
                    Some((seat, card)) => bucket_thread(seat, card),
                    None => none_thread(&day_of(r.item.at)),
                });
            }
        }
    }
    let (mut posts, mut gone) = (0, true);
    for t in threads {
        let (n, empty) = db
            .undo_import(t, OWNER.into(), KEY_PREFIX.into())
            .map_err(|e| e.to_string())?;
        posts += n;
        gone &= empty;
    }
    Ok(Done::Undone {
        posts,
        thread: gone,
    })
}

/// Which cards `--cards` writes: the N with the most records, or these.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Sample {
    Top(usize),
    These(Vec<String>),
}

/// Keep the records of the last [`RECENT_DAYS`] before `now` (unix seconds) and the older ones
/// that found a card or are a seat's day row; returns how many were left out.
pub fn recent_only(routed: &mut Vec<Routed>, now: i64) -> usize {
    let from = now - RECENT_DAYS * 86_400;
    let before = routed.len();
    routed.retain(|r| {
        r.item.at >= from
            || r.item.card.is_some()
            || seat_day_of(&r.item).is_some()
            || r.item.source.get("kind").and_then(Value::as_str) == Some("idea")
    });
    before - routed.len()
}

/// Where `card`'s records came from (dry run with `--cards`): by rule, by kind, the five
/// biggest Taipei days, and the first ten not put by their own text (time, kind, rule, from,
/// the opening words), so a card one rule spreads too far is seen in one look.
pub fn trace_text(routed: &[Routed], card: &str) -> String {
    let on: Vec<&Routed> = routed
        .iter()
        .filter(|r| r.item.card.as_deref() == Some(card))
        .collect();
    let mut by: BTreeMap<&str, usize> = BTreeMap::new();
    let mut kinds: BTreeMap<String, usize> = BTreeMap::new();
    let mut days: BTreeMap<String, usize> = BTreeMap::new();
    for r in &on {
        *by.entry(r.by.word()).or_default() += 1;
        let k = r
            .item
            .source
            .get("kind")
            .and_then(Value::as_str)
            .unwrap_or("");
        *kinds.entry(k.to_owned()).or_default() += 1;
        *days.entry(tpe_day(r.item.at)).or_default() += 1;
    }
    let line = |m: Vec<(String, usize)>| {
        m.into_iter()
            .map(|(k, n)| format!("{k} {n}"))
            .collect::<Vec<_>>()
            .join(", ")
    };
    let mut big: Vec<(String, usize)> = days.into_iter().collect();
    big.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    big.truncate(5);
    let mut out = format!("card {card}: {}\n", on.len());
    out.push_str(&format!(
        "  by rule: {}\n",
        line(by.into_iter().map(|(k, n)| (k.to_owned(), n)).collect())
    ));
    out.push_str(&format!(
        "  by kind: {}\n",
        line(kinds.into_iter().collect())
    ));
    out.push_str(&format!("  biggest days: {}\n", line(big)));
    for r in on.iter().filter(|r| r.by != By::Text).take(10) {
        let k = r
            .item
            .source
            .get("kind")
            .and_then(Value::as_str)
            .unwrap_or("");
        let words: String = r.item.body.chars().take(60).collect();
        out.push_str(&format!(
            "  {} {k} {} {}: {}\n",
            crate::sessions::rfc3339_of_unix(r.item.at),
            r.by.word(),
            r.item.from,
            words.replace('\n', " ")
        ));
    }
    out
}

/// `aibd import regroup [--dry-run] [--cards N | --cards A,B,…] [--undo]`.
pub fn run(args: &[&str], data_dir: &Path) -> Result<Done, String> {
    let (mut dry, mut undo_it, mut cards) = (false, false, None);
    let mut a = args.iter();
    while let Some(x) = a.next() {
        match *x {
            "--dry-run" => dry = true,
            "--undo" => undo_it = true,
            "--cards" => {
                let v = a
                    .next()
                    .ok_or("--cards takes 1–200 or card ids with commas")?;
                cards = Some(match v.parse::<usize>() {
                    Ok(n) if (1..=200).contains(&n) => Sample::Top(n),
                    Ok(_) => return Err("--cards takes 1–200".into()),
                    Err(_) => Sample::These(
                        v.split(',')
                            .map(str::trim)
                            .filter(|c| !c.is_empty())
                            .map(str::to_owned)
                            .collect(),
                    ),
                });
            }
            other => return Err(format!("unknown argument {other:?}")),
        }
    }
    let r = Roots::from_env(data_dir).ok_or("no documents folder")?;
    let mut plan = Plan::of(crate::workspace::current_tree().as_ref());
    if plan.titles.is_empty() {
        return Err("the plan tree is empty: no card to put a record on".into());
    }
    let (items, skipped) = items_all(&r, &plan);
    plan.files = files_of(&items);
    let more = named_files(&items, &r.pending, &plan);
    plan.files.extend(more);
    let events = std::fs::read_to_string(
        crate::workspace::tree_source_in(data_dir).with_file_name("plan-events.jsonl"),
    )
    .unwrap_or_default();
    plan.seat_marks = seat_marks_of(&seat_logs(&r), &events, &plan);
    let mut routed = route(items, &plan);
    // Andy 11-19 a50-1: older than RECENT_DAYS only what found a card (a seat's day rows too)
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_secs()).unwrap_or(i64::MAX));
    let older = recent_only(&mut routed, now);
    let only = cards.map(|c| match c {
        Sample::Top(n) => sample_cards(&routed, n),
        // a card the plan does not have is named in the table, not written to
        Sample::These(v) => v.into_iter().collect(),
    });
    if dry {
        print!("{}", table_text(&table_of(&routed), skipped));
        println!("older than {RECENT_DAYS} days without a card, left out: {older}");
        if let Some(o) = &only {
            println!(
                "sample cards: {}",
                o.iter().cloned().collect::<Vec<_>>().join(" ")
            );
            // 519d0 08:06 (card:T13 2,945): where a named card's records came from
            for c in o {
                print!("{}", trace_text(&routed, c));
            }
        }
        return Ok(Done::Imported(Counts {
            skipped,
            ..Counts::default()
        }));
    }
    let db =
        MessageDb::open(&data_dir.join(aib_store::MESSAGES_FILE)).map_err(|e| e.to_string())?;
    if undo_it {
        return undo(&routed, &db);
    }
    let mut c = write(&routed, &plan, &db, only.as_ref())?;
    c.skipped = skipped;
    Ok(Done::Imported(c))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plan() -> Plan {
        Plan::of(Some(&json!({"nodes": [
            {"id": "V", "title": "總綱"},
            {"id": "N-pv-up", "title": "上預覽", "head": "3698e318875c", "evidence": "PR #860 收據綠"},
            {"id": "N-ws-thread-cat", "title": "匯入", "evidence": "分支 feat/import-four-a9136-1119;#860 也提到"},
            {"id": "S73", "title": "合併", "why": "見 #845"},
        ]})))
    }

    fn mail(id: &str, pair: &str, at: i64, body: &str, src: Value) -> Item {
        let mut source = json!({"kind": "dm", "id": id});
        if let (Some(o), Some(extra)) = (source.as_object_mut(), src.as_object()) {
            o.extend(extra.clone());
        }
        Item {
            thread: pair.into(),
            thread_title: String::new(),
            category: None,
            key: format!("dm:{id}"),
            from: "seat:a9136".into(),
            at,
            body: body.into(),
            source,
            card: None,
            card_fallback: false,
            doc: None,
        }
    }

    #[test]
    fn work_words_are_heads_pr_numbers_and_branches() {
        assert_eq!(
            work_words("頭 3698e318 與 #860,分支 feat/x-1。不是 #12、deadbeef、1234567"),
            vec!["3698e318", "#860", "feat/x-1"],
            "reason: work_words_wrong"
        );
    }

    #[test]
    fn a_word_two_cards_write_names_neither() {
        let p = plan();
        assert_eq!(p.card_of_work("#845 好了").as_deref(), Some("S73"));
        assert_eq!(p.card_of_work("#860 綠"), None, "reason: shared_word_named");
        assert_eq!(
            p.card_of_work("換 3698e318875cf175 了").as_deref(),
            Some("N-pv-up"),
            "reason: head_prefix_missed"
        );
        assert!(!p.ids.contains("V"), "reason: one_letter_id_in_text_rule");
    }

    /// 519d0 07:29: a seat's letter with no card takes the card of the seat's last carded
    /// letter that day (any pair), and a pair's letter the card of the pair's next one that day.
    #[test]
    fn a_seats_letters_that_day_and_the_pairs_next_letter_give_a_card() {
        let p = plan();
        let mut later = mail("q_4", "t_cd", 400, "#845 合了", json!({}));
        later.from = "seat:b1234".into();
        let mut first = mail("q_3", "t_cd", 300, "先看", json!({}));
        first.from = "seat:b1234".into();
        let items = vec![
            mail("q_1", "t_ab", 100, "N-pv-up 修好", json!({})),
            mail("q_2", "t_zz", 200, "好", json!({})),
            first,
            later,
            mail("q_5", "t_yy", 100 + 86_400, "早", json!({})),
        ];
        let r = route(items, &p);
        let got: Vec<(&str, Option<&str>, &str)> = r
            .iter()
            .map(|x| (x.item.key.as_str(), x.item.card.as_deref(), x.by.word()))
            .collect();
        assert_eq!(
            got,
            vec![
                ("dm:q_1", Some("N-pv-up"), "card_text"),
                ("dm:q_2", Some("N-pv-up"), "seat_day"),
                ("dm:q_3", Some("S73"), "context"),
                ("dm:q_4", Some("S73"), "pr"),
                ("dm:q_5", None, "unassigned"),
            ],
            "reason: seat_letters_or_next_wrong"
        );
    }

    /// 519d0 07:29: rule `file` was 0 — names glued to Chinese text, documents never imported.
    #[test]
    fn a_letter_naming_a_document_takes_the_documents_card() {
        assert_eq!(
            doc_names(
                "請讀 ~/x/pending/VERIFY-oneline2-0eed7-c31406c09.md(文件那條)、REVIEW-a.md。"
            ),
            vec!["VERIFY-oneline2-0eed7-c31406c09.md", "REVIEW-a.md"],
            "reason: names_not_found"
        );
        assert!(doc_names("RELAY-dm-x-1.txt 與 a-b.md 與 X-y.md").is_empty());
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("REVIEW-x-a9136.md"), "審 N-pv-up 的頭").unwrap();
        std::fs::write(d.path().join("CHECK-y.md"), "沒有卡").unwrap();
        // a card named only past the opening lines is not the document's
        let deep = format!("{}順帶 S73\n", "一行\n".repeat(DOC_HEAD_LINES));
        std::fs::write(d.path().join("PLAN-deep.md"), deep).unwrap();
        let mut p = plan();
        let items = vec![
            mail(
                "q_1",
                "t_ab",
                100,
                "請看 REVIEW-x-a9136.md(兩點)",
                json!({}),
            ),
            mail("q_2", "t_cd", 200, "CHECK-y.md 與 PLAN-gone.md", json!({})),
            mail("q_3", "t_ef", 300, "看 PLAN-deep.md", json!({})),
        ];
        let more = named_files(&items, d.path(), &p);
        assert_eq!(
            more.into_iter().collect::<Vec<_>>(),
            vec![("REVIEW-x-a9136.md".to_owned(), "N-pv-up".to_owned())],
            "reason: named_files_wrong"
        );
        p.files = named_files(&items, d.path(), &p);
        assert_eq!(
            card_of("請看 REVIEW-x-a9136.md(兩點)", &p),
            Some(("N-pv-up".to_owned(), By::File)),
            "reason: file_rule_not_used"
        );
    }

    #[test]
    fn a_traced_card_says_its_rules_kinds_days_and_first_records() {
        let items = vec![
            mail("q_1", "t_ab", 100, "N-pv-up 修好", json!({})),
            mail("q_2", "t_ab", 200, "收到", json!({})),
            // the next day: the same seat's same-day letters go on its card (seat_day)
            mail("q_3", "t_cd", 300 + 86_400, "別的", json!({})),
        ];
        let t = trace_text(&route(items, &plan()), "N-pv-up");
        assert!(
            t.starts_with("card N-pv-up: 2\n"),
            "reason: count_wrong {t}"
        );
        assert!(
            t.contains("by rule: card_text 1, context 1"),
            "reason: rules_wrong {t}"
        );
        assert!(t.contains("by kind: dm 2"), "reason: kinds_wrong {t}");
        assert!(
            t.contains("biggest days: 1970-01-01 2"),
            "reason: days_wrong {t}"
        );
        assert!(
            t.contains("context") && t.contains(": 收到"),
            "reason: sample_missing {t}"
        );
        assert!(
            !t.contains(": N-pv-up 修好"),
            "reason: own_text_sampled {t}"
        );
    }

    #[test]
    fn each_rule_puts_a_record_and_says_which() {
        let p = plan();
        let items = vec![
            mail(
                "q_1",
                "t_ab",
                100,
                "N-pv-up 修好",
                json!({"ledger_thread": "L1"}),
            ),
            mail("q_2", "t_ab", 200, "收到", json!({"ledger_thread": "L1"})),
            mail("q_3", "t_cd", 300, "#845 合了", json!({})),
            mail("q_4", "t_cd", 400, "好", json!({})),
            mail("q_5", "t_cd", 400 + 86_400, "晚安", json!({})),
            mail("q_6", "t_ef", 500, "改寫", json!({"supersedes": "q_3"})),
        ];
        let r = route(items, &p);
        let got: Vec<(&str, Option<&str>, &str)> = r
            .iter()
            .map(|x| (x.item.key.as_str(), x.item.card.as_deref(), x.by.word()))
            .collect();
        assert_eq!(
            got,
            vec![
                ("dm:q_1", Some("N-pv-up"), "card_text"),
                ("dm:q_2", Some("N-pv-up"), "reply"),
                ("dm:q_3", Some("S73"), "pr"),
                ("dm:q_4", Some("S73"), "context"),
                ("dm:q_6", Some("S73"), "reply"),
                ("dm:q_5", None, "unassigned"),
            ],
            "reason: rule_wrong"
        );
        // Andy 11-19 a50-2: a seat's letter without a card goes in its bucket, under the card
        // of its last anchored letter when it has no plan mark (519d0 08:49)
        assert_eq!(place_of(&r[5]), "bucket:a9136:S73", "reason: no_bucket");
        let mut import = mail("q_7", "t_gh", 600 + 86_400, "嗯", json!({}));
        import.from = "seat:import".into();
        let r = route(vec![import], &p);
        assert_eq!(place_of(&r[0]), format!("none:{}", day_of(600 + 86_400)));
    }

    /// Andy 11-19 a50-1 / a50-2: the last RECENT_DAYS whole, older only with a card; a letter
    /// without one goes under its sender's main item of then.
    #[test]
    fn old_cardless_letters_stay_out_and_new_ones_go_under_the_senders_main_item() {
        let mut p = plan();
        p.seat_marks
            .insert("a9136".into(), vec![(50, "S73".to_owned())]);
        let day = 86_400;
        let now = 100 * day;
        let items = vec![
            mail("q_old", "t_ab", 10 * day, "很早的 嗯", json!({})),
            mail("q_oldc", "t_cd", 10 * day + 5, "N-pv-up 很早", json!({})),
            mail("q_new", "t_ef", now - day, "最近 嗯", json!({})),
        ];
        let mut anchored = mail("q_b1", "t_gh", now - 3 * day, "S73 開工", json!({}));
        anchored.from = "seat:b0001".into();
        let mut later = mail("q_b2", "t_ij", now - day, "嗯", json!({}));
        later.from = "seat:b0001".into();
        let mut opened = mail(
            "q_g1",
            "t_kl",
            now - day,
            "c0009:看一下",
            json!({"said_seat": "c0009", "said_from": "opening"}),
        );
        opened.from = "seat:import".into();
        let items = [items, vec![anchored, later, opened]].concat();
        let mut r = route(items, &p);
        assert_eq!(recent_only(&mut r, now), 1, "reason: old_cardless_kept");
        let place = |k: &str| place_of(r.iter().find(|x| x.item.key == format!("dm:{k}")).unwrap());
        assert!(r.iter().all(|x| x.item.key != "dm:q_old"));
        assert_eq!(place("q_new"), "bucket:a9136:S73", "reason: main_item_lost");
        assert_eq!(
            place("q_b2"),
            "bucket:b0001:S73",
            "reason: last_anchor_not_main"
        );
        assert_eq!(
            place("q_g1"),
            "bucket:c0009:-",
            "reason: opened_seat_no_bucket"
        );
        assert_eq!(
            bucket_thread("a9136", Some("S73")),
            crate::import::thread_of("regroup-bucket:a9136:S73")
        );
    }

    #[test]
    fn a_mail_naming_a_documents_file_follows_that_documents_card() {
        let mut p = plan();
        p.files.insert("REVIEW-x-5d33a.md".into(), "S73".into());
        assert_eq!(
            card_of("請看 ~/.ez2-aib/pending/REVIEW-x-5d33a.md(r2)", &p),
            Some(("S73".to_owned(), By::File)),
            "reason: file_not_followed"
        );
        assert_eq!(card_of("請看 REVIEW-y.md", &p), None);
        let mut doc = mail("q_9", "t", 1, "", json!({"id": "D-1.md"}));
        doc.doc = Some(json!([]));
        doc.card = Some("N-pv-up".into());
        assert_eq!(
            files_of(&[doc]).get("D-1.md").map(String::as_str),
            Some("N-pv-up")
        );
    }

    fn roots_with_history(d: &Path) -> Roots {
        let now = d.join("state").join("seat-now");
        std::fs::create_dir_all(&now).unwrap();
        // a stage change without a 判斷, two 判斷, one bad line, the next day one more
        std::fs::write(
            now.join("a9136.history.jsonl"),
            "{\"item\":\"N-pv-up 開工\",\"started_at\":1,\"seen\":100}\n\
             {\"item\":\"N-pv-up 修 M1\",\"started_at\":1,\"seen\":200,\"track\":50}\n\
             {\"item\":\"N-pv-up 收據綠\",\"started_at\":1,\"seen\":300,\"track\":95}\n\
             not json\n\
             {\"item\":\"S73 審\",\"started_at\":2,\"seen\":86500,\"track\":60}\n\
             {\"item\":\"S73 再審\",\"started_at\":2,\"seen\":86550,\"track\":65}\n",
        )
        .unwrap();
        std::fs::write(
            now.join("a9136.json"),
            r#"{"review":{"track":65,"why":"為了 X","idea":"試 Y"}}"#,
        )
        .unwrap();
        let hand = d.join("state").join("handoff");
        std::fs::create_dir_all(&hand).unwrap();
        std::fs::write(
            hand.join("a9136.receipts.jsonl"),
            "{\"ts\":250,\"round_ts\":250}\n{\"ts\":260,\"round_ts\":250}\n\
             {\"ts\":270,\"incomplete\":[\"x\"]}\n",
        )
        .unwrap();
        std::fs::create_dir_all(d.join("pending")).unwrap();
        Roots {
            state: d.join("state"),
            pending: d.join("pending"),
            hub: None,
        }
    }

    #[test]
    fn each_judgement_is_one_self_marked_post_and_a_return_is_a_correction() {
        let d = tempfile::tempdir().unwrap();
        let r = roots_with_history(d.path());
        let v = correction_items(&r, &plan());
        let kinds: Vec<&str> = v
            .iter()
            .map(|i| i.source["kind"].as_str().unwrap_or(""))
            .collect();
        // 50 → 95 the same day is one 校正; 95 → 60 the next day and 60 → 65 are none
        assert_eq!(
            kinds,
            [
                "judgement",
                "correction",
                "judgement",
                "judgement",
                "judgement"
            ],
            "reason: kinds_wrong {v:?}"
        );
        assert_eq!(v[0].card.as_deref(), Some("N-pv-up"));
        assert!(
            v[1].body
                .starts_with("〔自評 · a9136〕校正:偏到 50% → 回到 95%"),
            "reason: correction_text {}",
            v[1].body
        );
        assert!(
            v[2].body.starts_with("〔自評 · a9136〕判斷 95% · "),
            "reason: not_marked_self_written {}",
            v[2].body
        );
        assert_eq!(v[2].source["card_said"], "status_line");
        // why / idea only on the current 判斷
        assert_eq!(v[4].source["why"], "為了 X", "reason: current_review_lost");
        assert!(v[3].source.get("why").is_none());
        assert!(v[0].source.get("why").is_none());
        let t = table_of(&route(v, &plan()));
        assert_eq!(
            t.kinds.get("correction"),
            Some(&1),
            "reason: kind_not_counted"
        );
    }

    #[test]
    fn a_seat_day_has_the_old_cockpit_row() {
        let d = tempfile::tempdir().unwrap();
        let r = roots_with_history(d.path());
        let v = seat_day_items(&r, &plan());
        assert_eq!(v.len(), 2, "reason: not_one_per_day {v:?}");
        let s = &v[0].source;
        assert_eq!(
            (
                s["judge"].as_u64(),
                s["corrections"].as_u64(),
                s["handoffs"].as_u64()
            ),
            (Some(95), Some(1), Some(1)),
            "reason: row_wrong {s}"
        );
        assert_eq!(s["first_goal"]["item"], "N-pv-up 開工");
        assert_eq!(s["goal"]["item"], "N-pv-up 收據綠");
        assert!(
            v[0].body.contains("判斷 95% · 校正 1 · 總結 0 · 交接 1"),
            "{}",
            v[0].body
        );
        assert_eq!(v[1].card.as_deref(), Some("S73"));
        let routed = route(v, &plan());
        assert_eq!(
            place_of(&routed[0]),
            "seat_day:a9136",
            "reason: not_in_seat_thread"
        );
    }

    #[test]
    fn review_grades_and_rounds_are_a_card_only_where_a_card_is_said() {
        let p = Plan::of(Some(&json!({"nodes": [
            {"id": "M1", "title": "舊卡"}, {"id": "R4", "title": "輪"}, {"id": "S79", "title": "自動推進"},
        ]})));
        assert!(looks_like_other("M1") && looks_like_other("R4") && looks_like_other("A1"));
        assert!(
            !looks_like_other("S79") && !looks_like_other("N-pv-up") && !looks_like_other("MX1")
        );
        assert_eq!(
            card_in_text("審查 0/1/2/0,M1 修好了", &p),
            None,
            "reason: grade_as_card"
        );
        assert_eq!(
            card_in_text("M1 修好、S79 下一步", &p).as_deref(),
            Some("S79")
        );
        assert_eq!(
            card_in_text("派工: M1 → a9136\n細節", &p),
            None,
            "reason: first_line_grade_as_card"
        );
        assert_eq!(
            card_in_text("派工: 修(卡 M1)→ a9136", &p).as_deref(),
            Some("M1")
        );
        assert_eq!(card_in_text("這張卡 M1 先放", &p).as_deref(), Some("M1"));
        assert_eq!(card_in_text("x\nnode: R4", &p).as_deref(), Some("R4"));
    }

    #[test]
    fn a_letter_naming_another_letters_id_follows_its_card() {
        assert_eq!(
            quoted_ids("回 q_0123456789abcdef 與 q_xyz、q_0123456789ABCDEF0"),
            vec!["q_0123456789abcdef"]
        );
        let r = route(
            vec![
                mail("q_0123456789abcdef", "t_ab", 100, "N-pv-up", json!({})),
                mail("q_2", "t_cd", 100_000, "回 q_0123456789abcdef", json!({})),
            ],
            &plan(),
        );
        assert_eq!(
            r[1].item.card.as_deref(),
            Some("N-pv-up"),
            "reason: quoted_id_not_followed"
        );
        assert_eq!(r[1].by, By::Reply);
    }

    /// 519d0 08:11: T13 2,930 / S15 1,435 by context chains across seats and days.
    #[test]
    fn context_follows_only_an_anchor_near_in_time_and_count() {
        let p = plan();
        // 8 h into a Taipei day (00:00 UTC is 08:00 there): the day lasts past +2 h
        let t0 = 86_400 * 3;
        let mut items = vec![mail("q_a", "t_ab", t0, "N-pv-up 修好", json!({}))];
        for i in 1..=CONTEXT_MAX + 1 {
            let n = i64::try_from(i).unwrap();
            items.push(mail(
                &format!("q_{i:02}"),
                "t_ab",
                t0 + n,
                "收到",
                json!({}),
            ));
        }
        // past CONTEXT_SECS, the same day, another seat's pair (its own count is low)
        let mut other = mail("q_b", "t_ef", t0, "N-pv-up 好", json!({}));
        other.from = "seat:c0001".into();
        items.push(other);
        let mut late = mail("q_late", "t_ef", t0 + CONTEXT_SECS + 60, "再說", json!({}));
        late.from = "seat:c0001".into();
        items.push(late);
        // a letter to a seat with an anchor, from one without: not on the receiver's card
        let mut c4 = mail("q_c4", "t_kl", t0, "N-pv-up 嗨", json!({}));
        c4.from = "seat:c0004".into();
        items.push(c4);
        let mut to4 = mail("q_to4", "t_mn", t0 + 60, "請看", json!({"to": "c0004"}));
        to4.from = "seat:c0005".into();
        items.push(to4);
        // naming its own work no card has, after and before an anchor: no context
        let mut w1 = mail("q_w1", "t_op", t0, "N-pv-up 好了", json!({}));
        w1.from = "seat:c0006".into();
        items.push(w1);
        let mut w2 = mail("q_w2", "t_op", t0 + 30, "#631 段 2 交了", json!({}));
        w2.from = "seat:c0006".into();
        items.push(w2);
        let mut w0 = mail("q_w0", "t_qr", t0 - 30, "feat/zz-c0007 推了", json!({}));
        w0.from = "seat:c0007".into();
        items.push(w0);
        // only an old head: still on the pair's card (519d0 08:39)
        let mut w4 = mail("q_w4", "t_op", t0 + 40, "頭 9abc1234def 收據綠", json!({}));
        w4.from = "seat:c0006".into();
        items.push(w4);
        let mut w3 = mail("q_w3", "t_qr", t0, "S73 合了", json!({}));
        w3.from = "seat:c0007".into();
        items.push(w3);
        // across Taipei midnight (t0 + 16 h), within CONTEXT_SECS
        let mid = t0 + 16 * 60 * 60;
        let mut eve = mail("q_eve", "t_gh", mid - 1800, "N-pv-up 晚", json!({}));
        eve.from = "seat:c0002".into();
        items.push(eve);
        let mut morn = mail("q_morn", "t_gh", mid + 1800, "早", json!({}));
        morn.from = "seat:c0002".into();
        items.push(morn);
        // the pair before the anchor, one before it by more than CONTEXT_SECS
        items.push(mail("q_pre", "t_cd", t0 + 100, "先看", json!({})));
        items.push(mail(
            "q_far",
            "t_cd",
            t0 + 50 - CONTEXT_SECS,
            "很早",
            json!({}),
        ));
        items.push(mail("q_s73", "t_cd", t0 + 200, "#845 合了", json!({})));
        let r = route(items, &p);
        let card = |k: &str| {
            let x = r.iter().find(|x| x.item.key == format!("dm:{k}")).unwrap();
            (x.item.card.clone(), x.by)
        };
        assert_eq!(card("q_01"), (Some("N-pv-up".into()), By::Near));
        assert_eq!(
            card(&format!("q_{CONTEXT_MAX:02}")),
            (Some("N-pv-up".into()), By::Near),
            "reason: limit_off_by_one"
        );
        assert_eq!(
            card(&format!("q_{:02}", CONTEXT_MAX + 1)).0,
            None,
            "reason: past_max_carded"
        );
        assert_eq!(card("q_late").0, None, "reason: past_secs_carded");
        assert_eq!(card("q_morn").0, None, "reason: next_day_carded");
        assert_eq!(card("q_to4").0, None, "reason: receivers_card_taken");
        assert_eq!(card("q_w2").0, None, "reason: own_work_took_context");
        assert_eq!(card("q_w0").0, None, "reason: own_work_took_look_ahead");
        assert_eq!(
            card("q_w4"),
            (Some("N-pv-up".into()), By::Near),
            "reason: old_head_counted_as_other_work"
        );
        assert_eq!(
            card("q_pre"),
            (Some("S73".into()), By::Near),
            "reason: ahead_lost"
        );
        assert_eq!(card("q_far").0, None, "reason: ahead_too_far_carded");
    }

    /// 519d0 09:11: one post per idea, on its node or in its seat's bucket; a review or a
    /// when changes only the state it shows; needs_human is flagged.
    #[test]
    fn each_idea_is_one_post_on_its_card_or_in_its_seats_bucket() {
        let d = tempfile::tempdir().unwrap();
        let r = roots_with_history(d.path());
        let ideas = r.state.join("ideas");
        std::fs::create_dir_all(&ideas).unwrap();
        let lines = [
            json!({"ev": "add", "idea_id": "i1", "at_ms": 1_000_000, "node": "S73",
                "grade": "B", "axis": "stable", "classification": "auto",
                "title": "收據印 sha", "suggest": "每支記一行", "recorded_by_seat": "043dd"}),
            json!({"ev": "add", "idea_id": "i2", "at_ms": 2_000_000, "node": null,
                "grade": "A", "axis": "simple", "classification": "needs_human",
                "title": "要花錢", "suggest": "買一台", "recorded_by_seat": "a9136"}),
            json!({"ev": "add", "idea_id": "i3", "at_ms": 2_500_000, "node": "S73",
                "grade": "C", "axis": "fast", "classification": "needs_human",
                "title": "519d0 已定", "suggest": "不用問", "recorded_by_seat": "519d0"}),
            json!({"ev": "review_batch", "ids": ["i1", "i3"], "at_ms": 3_000_000, "verdict": "ok",
                "quote": "「要」"}),
            json!({"ev": "when", "idea_id": "i2", "at_ms": 4_000_000, "blocked_by": "#860",
                "earliest": "11-20"}),
        ];
        let text: String = lines.iter().map(|l| format!("{l}\nnot json\n")).collect();
        std::fs::write(ideas.join("ideas.jsonl"), text).unwrap();
        let p = plan();
        let mut skipped = 0;
        let items = idea_items(&r, &p, &mut skipped);
        assert_eq!(skipped, 5, "reason: bad_lines_not_skipped");
        assert_eq!(items.len(), 3, "reason: review_or_when_posted");
        let routed = route(items, &p);
        let one = |id: &str| {
            routed
                .iter()
                .find(|x| x.item.key == format!("idea:{id}"))
                .unwrap()
        };
        assert_eq!(
            one("i1").item.card.as_deref(),
            Some("S73"),
            "reason: node_lost"
        );
        assert!(
            one("i1")
                .item
                .body
                .starts_with("B | 穩定 | 自動同意優化 收據印 sha"),
            "reason: shape {}",
            one("i1").item.body
        );
        assert!(one("i1")
            .item
            .body
            .contains("狀態:Andy 看過、照建議 「要」"));
        assert_eq!(one("i2").item.card, None);
        assert_eq!(
            place_of(one("i2")),
            "bucket:a9136:-",
            "reason: not_in_bucket"
        );
        assert!(one("i2").item.body.contains("狀態:等:#860(最早 11-20)"));
        assert_eq!(
            one("i2").item.source["needs_human"],
            json!(true),
            "reason: flag_lost"
        );
        assert!(one("i1").item.source.get("needs_human").is_none());
        assert!(
            one("i3").item.source.get("needs_human").is_none(),
            "reason: decided_still_asks_andy"
        );
    }

    #[test]
    fn a_letter_goes_on_the_card_its_seat_last_marked_that_day() {
        let mut p = plan();
        let d = tempfile::tempdir().unwrap();
        // a plan tree note of a9136 on S73 at 160, one of another seat, one on no card
        let events = "{\"by\":\"a9136\",\"id\":\"S73\",\"ts\":160,\"op\":\"note\"}\n\
                      {\"by\":\"7ebca\",\"id\":\"N-pv-up\",\"ts\":10,\"op\":\"note\"}\n\
                      {\"by\":\"a9136\",\"id\":\"ZZ9\",\"ts\":165}\nbad\n";
        p.seat_marks = seat_marks_of(&seat_logs(&roots_with_history(d.path())), events, &p);
        // a letter the ledger has no sender seat for goes by the seat it says it is from
        let mut said = mail("q_5", "t_ij", 170, "嗯", json!({"said_seat": "a9136"}));
        said.from = "seat:import".into();
        // one whose seat is only read off its words takes no seat's day
        let mut guess = mail(
            "q_6",
            "t_kl",
            86_700,
            "a9136:請看",
            json!({"said_seat": "a9136", "said_from": "opening"}),
        );
        guess.from = "seat:import".into();
        let r = route(
            vec![
                mail("q_1", "t_ab", 150, "好的", json!({})),
                mail("q_2", "t_cd", 170, "收到", json!({})),
                mail("q_3", "t_ef", 86_450, "早", json!({})),
                mail("q_4", "t_gh", 86_600, "嗯", json!({})),
                said,
                guess,
            ],
            &p,
        );
        // route keeps (time, key) order: q_5 (170) comes before q_3 (86_450)
        let got: Vec<(&str, Option<&str>, By)> = r
            .iter()
            .map(|x| (x.item.key.as_str(), x.item.card.as_deref(), x.by))
            .collect();
        assert_eq!(
            got,
            vec![
                ("dm:q_1", Some("N-pv-up"), By::SeatDay),
                ("dm:q_2", Some("S73"), By::SeatDay),
                ("dm:q_5", Some("S73"), By::SeatDay),
                ("dm:q_3", None, By::None),
                ("dm:q_4", Some("S73"), By::SeatDay),
                ("dm:q_6", None, By::None),
            ],
            "reason: seat_day_wrong"
        );
    }

    #[test]
    fn a_letter_without_a_sender_seat_finds_it_or_is_a_system_letter() {
        assert_eq!(
            sender_of("AIB2訊息線直接翻工程師@7ebca", "x").as_deref(),
            Some("7ebca")
        );
        assert_eq!(sender_of("", "7ebca:定版頭").as_deref(), Some("7ebca"));
        assert_eq!(
            sender_of("@7ebca(AIB2訊息線直接翻工程師)", "x").as_deref(),
            Some("7ebca"),
            "reason: real_label_shape"
        );
        assert_eq!(sender_of("", "f1fae 轉 Andy").as_deref(), Some("f1fae"));
        assert!(is_system("unattested:auto-find-work-dm", "提醒"));
        assert_eq!(
            sender_of("", "@abb89 → a9136 派工").as_deref(),
            Some("abb89")
        );
        assert_eq!(sender_of("", "f1fae → 519d0").as_deref(), Some("f1fae"));
        assert_eq!(sender_of("", "abcde 是個字"), None, "reason: word_as_seat");
        assert!(is_system("自動精煉提醒", "長訊"));
        assert!(is_system("", "提醒: 你有 3 封還沒收的信"));
        assert!(!is_system("", "N-pv-up 修好"));
        let mut sys = mail(
            "q_s",
            "t_ab",
            100,
            "提醒: 你有 3 封",
            json!({"system": true}),
        );
        sys.from = "seat:import".into();
        let mut named = mail(
            "q_n",
            "t_cd",
            100,
            "提醒: S73 #845 紅了",
            json!({"system": true}),
        );
        named.from = "seat:import".into();
        let r = route(vec![sys, named], &plan());
        // both at 100: route's (time, key) order puts q_n first, so each is found by key
        let by_key = |k: &str| r.iter().find(|x| x.item.key == k).unwrap();
        let s = by_key("dm:q_s");
        assert_eq!((s.item.card.as_deref(), s.by), (None, By::System));
        assert!(
            place_of(s).starts_with("system:"),
            "reason: system_in_none_bucket"
        );
        assert_eq!(
            by_key("dm:q_n").item.card.as_deref(),
            Some("S73"),
            "reason: system_naming_a_card_lost"
        );
    }

    /// 519d0 06:08: the dry run never set a thread's kind, so the first real write failed on
    /// `seat_day`; this one writes a seat's day row, a system letter and a carded letter.
    #[test]
    fn a_real_write_opens_each_kind_of_thread_and_posts_once() {
        let d = tempfile::tempdir().unwrap();
        let r = roots_with_history(d.path());
        let db = MessageDb::open(&d.path().join("m.sqlite")).unwrap();
        let mut items = seat_day_items(&r, &plan());
        let mut sys = mail(
            "q_s",
            "t_ab",
            100,
            "提醒: 你有 3 封",
            json!({"system": true}),
        );
        sys.from = "seat:import".into();
        items.push(sys);
        items.push(mail("q_c", "t_cd", 100, "N-pv-up 好了", json!({})));
        let n = items.len();
        assert!(n > 2, "reason: no_seat_day_items");
        let routed = route(items, &plan());
        let c = write(&routed, &plan(), &db, None).unwrap();
        assert_eq!((c.new, c.already), (n, 0), "reason: not_all_written {c:?}");
        let again = write(&routed, &plan(), &db, None).unwrap();
        assert_eq!((again.new, again.already), (0, n), "reason: written_twice");
        let k = db
            .categories_of(&[seat_day_thread("a9136"), system_thread(&day_of(100))])
            .unwrap();
        assert_eq!(
            k.get(&seat_day_thread("a9136")).map(String::as_str),
            Some("seat_day")
        );
        assert_eq!(
            k.get(&system_thread(&day_of(100))).map(String::as_str),
            Some("system")
        );
    }

    #[test]
    fn a_document_is_one_short_post_with_its_file() {
        let t =
            "---\nstatus: x\n---\n# 定稿 標題\n\n第一行\n## 小節\n第二行\n第三行\n第四行\n第五行\n";
        let s = short_doc("DECISION-council-x.md", t);
        assert!(s.starts_with("📄 定稿 標題\n第一行\n第二行"), "{s}");
        assert!(!s.contains("第五行"), "reason: whole_text_posted");
        assert!(!s.contains("status"), "reason: front_matter_posted");
        assert!(s.ends_with("(全文:開檔 DECISION-council-x.md)"));
    }

    #[test]
    fn the_table_counts_rules_cards_and_the_biggest_threads() {
        let p = plan();
        let r = route(
            vec![
                mail("q_1", "t_ab", 100, "N-pv-up", json!({})),
                mail("q_2", "t_ab", 150, "好", json!({})),
                mail("q_3", "t_xy", 100_000, "?", json!({})),
            ],
            &p,
        );
        let t = table_of(&r);
        assert_eq!((t.total, t.cards), (3, 1));
        assert_eq!(t.by.get("context"), Some(&1));
        assert_eq!(
            t.biggest[0],
            ("card:N-pv-up".to_owned(), 2),
            "reason: biggest_wrong"
        );
        assert_eq!(
            sample_cards(&r, 10).into_iter().collect::<Vec<_>>(),
            vec!["N-pv-up"]
        );
        assert!(
            table_text(&t, 0).contains("unassigned      1   33.3%"),
            "{}",
            table_text(&t, 0)
        );
    }

    #[test]
    fn records_go_into_their_cards_own_thread_and_undo_takes_them_back() {
        let d = tempfile::tempdir().unwrap();
        let db = MessageDb::open(&d.path().join("m.sqlite")).unwrap();
        let p = plan();
        // the page already opened the card's thread
        let page = card_thread(&db, "N-pv-up", "上預覽").unwrap();
        let r = route(
            vec![
                mail("q_1", "t_ab", 100, "N-pv-up 好", json!({})),
                mail("q_2", "t_zz", 900_000, "無卡", json!({})),
            ],
            &p,
        );
        let c = write(&r, &p, &db, None).unwrap();
        assert_eq!((c.new, c.already), (2, 0));
        let (posts, _) = db.posts_page(&page, None, 10).unwrap();
        assert_eq!(posts.len(), 1, "reason: not_in_cards_thread");
        assert_eq!(posts[0].item["card_rule"], json!("card_text"));
        assert_eq!(posts[0].idem_key.as_deref(), Some("rg:dm:q_1"));
        let again = write(&r, &p, &db, None).unwrap();
        assert_eq!((again.new, again.already), (0, 2), "reason: written_twice");
        // only the sample's cards
        let d2 = tempfile::tempdir().unwrap();
        let db2 = MessageDb::open(&d2.path().join("m.sqlite")).unwrap();
        let only: BTreeSet<String> = ["S73".to_owned()].into();
        assert_eq!(write(&r, &p, &db2, Some(&only)).unwrap().new, 0);
        match undo(&r, &db).unwrap() {
            Done::Undone { posts, .. } => assert_eq!(posts, 2),
            other => panic!("{other:?}"),
        }
        assert!(db.posts_page(&page, None, 10).unwrap().0.is_empty());
    }
}
