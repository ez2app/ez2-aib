//! 要你決定的卡 + 真人未審清單卡 (519d0 11-19 09:11 dispatch; Andy 09:1x 「好,朝這個方向做」).
//!
//! - An idea that needs a person (aib-idea `classification: needs_human`, still pending) is a
//!   slot-1 card `idea:<id>` with 同意 / 不同意 (`actions: ["agree", "disagree"]`), act
//!   `idea_answer {card_id, answer}`.
//! - The decisions 519d0 made for Andy to look at later (`<root>/state/drive/andy-<seat>.txt`
//!   lines with 「真人未審」 or a dated 「<seat> 裁 / 改裁」) are one slot-1 card `review:list`,
//!   `open.list = [{id, text, ref, line}]` (id = SHA-256 of the normalized line, 12 hex); act
//!   `review_seen {line_ids}` for one or several. A user folder page sees only the count.
//! - Every press asks the owner's fingerprint (DECISION-council-ideas-2-519d0-1119 §2: 同意 /
//!   不同意 each time, 看過 once for a batch). Until the aib-consent queue is wired none can be
//!   asked: the press is `idea.pressed` / `review.pressed` with `verified: false`
//!   (「頁面按過、未證實」) and nothing is answered or seen. The events `idea.answered` /
//!   `review.seen` are what a proved press will write; they are replayed at start already.
//! - Ideas that went through on their own are records on their node's card window
//!   ([`node_ideas`], 「已自動同意」).
//!
//! Only a confirmed built-in page answers either (the slots' gate in seat_input.rs).

use crate::api::AppState;
use crate::pairing::PageAccess;
use crate::slots::{Card, NAMES};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::path::Path;
use std::sync::{LazyLock, Mutex};

/// The buttons of an idea card.
pub const IDEA_ACTIONS: &[&str] = &["agree", "disagree"];
/// The button of each line of the review list.
pub const SEEN_ACTIONS: &[&str] = &["seen"];
/// The card id of the review list.
pub const LIST_ID: &str = "review:list";
/// The longest line of the list kept (characters).
pub const LINE_MAX: usize = 300;
/// At most this many lines in the list (the oldest first).
pub const LIST_MAX: usize = 200;

/// A line as its id sees it (DECISION-council-ideas-2 §3): full-width ASCII and the
/// ideographic space made half-width, runs of white space one space, the ends trimmed.
pub fn normalize(text: &str) -> String {
    let half: String = text
        .chars()
        .map(|c| match c {
            '\u{3000}' => ' ',
            '\u{FF01}'..='\u{FF5E}' => char::from_u32(c as u32 - 0xFEE0).unwrap_or(c),
            c => c,
        })
        .collect();
    half.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// A line's id: the first 12 hex of the SHA-256 of its [`normalize`]d words before `|` (§3,
/// CONTRACT-ideas-consent-7ebca-a9136-1119 §1).
pub fn line_id(text: &str) -> String {
    Sha256::digest(normalize(text).as_bytes())
        .iter()
        .take(6)
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// Whether a line of `andy-<seat>.txt` is a decision waiting for Andy's look.
pub fn after_review(line: &str) -> bool {
    if line.contains("真人未審") {
        return true;
    }
    // 「11-19 08:25 519d0 裁:…」 / 「… 519d0 改裁:…」
    let mut w = line.split_whitespace();
    let date = w.next().unwrap_or("");
    let time = w.next().unwrap_or("");
    let seat = w.next().unwrap_or("");
    let rest = w.next().unwrap_or("");
    let digits = |s: &str, n: usize| s.len() == n && s.bytes().all(|b| b.is_ascii_digit());
    date.len() == 5
        && digits(&date[..2], 2)
        && date.as_bytes()[2] == b'-'
        && digits(&date[3..], 2)
        && time.len() == 5
        && digits(&time[..2], 2)
        && time.as_bytes()[2] == b':'
        && seat.len() == 5
        && seat
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        && (rest.starts_with("裁") || rest.starts_with("改裁"))
}

static SEEN: LazyLock<Mutex<BTreeSet<String>>> = LazyLock::new(|| Mutex::new(BTreeSet::new()));

fn seen_lock() -> std::sync::MutexGuard<'static, BTreeSet<String>> {
    SEEN.lock().unwrap_or_else(|e| e.into_inner())
}

/// Mark a line looked at; `false` if it was already.
pub fn see(line: &str) -> bool {
    seen_lock().insert(line.to_owned())
}

pub fn seen() -> BTreeSet<String> {
    seen_lock().clone()
}

pub(crate) fn clear_seen() {
    seen_lock().clear();
}

/// The waiting lines of `text` (an `andy-<seat>.txt`): (id, text, ref), oldest first, the seen
/// ones and repeats left out.
pub fn list_rows(text: &str, seen: &BTreeSet<String>) -> Vec<Row> {
    let mut ids = BTreeSet::new();
    text.lines()
        .enumerate()
        .map(|(n, l)| (n + 1, l.trim()))
        .filter(|(_, l)| !l.is_empty() && after_review(l))
        .filter_map(|(line, l)| {
            let (words, refs) = match l.rsplit_once('|') {
                Some((w, r)) => (
                    w.trim(),
                    Some(r.trim().to_owned()).filter(|r| !r.is_empty()),
                ),
                None => (l, None),
            };
            // only the words before `|`: a ref changed is not the line changed (CONTRACT §1)
            let id = line_id(words);
            if seen.contains(&id) || !ids.insert(id.clone()) {
                return None;
            }
            Some(Row {
                id,
                text: words.chars().take(LINE_MAX).collect(),
                refs,
                line,
                replaces: Vec::new(),
            })
        })
        .take(LIST_MAX)
        .collect()
}

/// One waiting line of the list: its id, its words (the `|ref` cut off), the ref, and its
/// line number in the file (1-based, §3 「記來源行號」).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Row {
    pub id: String,
    pub text: String,
    pub refs: Option<String>,
    pub line: usize,
    /// The ids this line was rewritten from (same source line, other words; §3 「已改寫 → 新
    /// id」): their 看過 stays on them.
    pub replaces: Vec<String>,
}

/// The waiting rows of a structured after-review ledger (`andy-review-<seat>.jsonl`, 519d0
/// 11-19 09:35: `{id, line, at, kind: decision | pending_review | note, by, text, src}`): the
/// decision and pending_review lines, the ledger's own id (12 hex) kept as it is. A later line
/// for the same `src` + `line` with another id is that line rewritten: the newest one stands and
/// names the older ids in `replaces`. A line that does not read is left out and counted.
pub fn ledger_rows(text: &str, seen: &BTreeSet<String>) -> (Vec<Row>, usize) {
    let mut bad = 0;
    let mut all: Vec<(String, Row)> = Vec::new();
    for l in text.lines().filter(|l| !l.trim().is_empty()) {
        let Ok(v) = serde_json::from_str::<Value>(l) else {
            bad += 1;
            continue;
        };
        let st = |k: &str| v.get(k).and_then(Value::as_str);
        let id = st("id")
            .filter(|i| i.len() == 12 && i.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')));
        let line = match v.get("line") {
            Some(Value::String(n)) => n.parse::<usize>().ok(),
            Some(Value::Number(n)) => n.as_u64().and_then(|n| usize::try_from(n).ok()),
            _ => None,
        };
        let (Some(id), Some(kind), Some(words), Some(line)) = (id, st("kind"), st("text"), line)
        else {
            bad += 1;
            continue;
        };
        if !matches!(kind, "decision" | "pending_review") {
            continue;
        }
        let (words, refs) = match words.rsplit_once('|') {
            Some((w, r)) => (
                w.trim(),
                Some(r.trim().to_owned()).filter(|r| !r.is_empty()),
            ),
            None => (words.trim(), None),
        };
        let key = format!("{}#{line}", st("src").unwrap_or(""));
        all.push((
            key,
            Row {
                id: id.to_owned(),
                text: words.chars().take(LINE_MAX).collect(),
                refs,
                line,
                replaces: Vec::new(),
            },
        ));
    }
    let mut out: Vec<Row> = Vec::new();
    let mut at: std::collections::BTreeMap<String, usize> = std::collections::BTreeMap::new();
    for (key, row) in all {
        match at.get(&key) {
            Some(&i) if out[i].id == row.id => {}
            Some(&i) => {
                let old = std::mem::replace(&mut out[i], row);
                out[i].replaces = old.replaces;
                out[i].replaces.push(old.id);
            }
            None => {
                at.insert(key, out.len());
                out.push(row);
            }
        }
    }
    let mut ids = BTreeSet::new();
    out.retain(|r| !seen.contains(&r.id) && ids.insert(r.id.clone()));
    out.truncate(LIST_MAX);
    (out, bad)
}

/// The one review-list card, or none when no line waits.
pub fn list_card(rows: &[Row], at: &str) -> Option<Card> {
    if rows.is_empty() {
        return None;
    }
    let list: Vec<Value> = rows
        .iter()
        .map(|r| {
            json!({"id": r.id, "text": r.text, "ref": r.refs, "line": r.line,
                   "replaces": r.replaces})
        })
        .collect();
    Some(Card {
        card_id: LIST_ID.into(),
        slot: 1,
        name: NAMES[0],
        kind: "andy.review_list".into(),
        seat_id: None,
        session_id: None,
        at: at.to_owned(),
        global_seq: None,
        item: Some(format!("真人未審 {} 條", rows.len())),
        why: None,
        pct: None,
        card: None,
        rec: None,
        state: None,
        src: None,
        tag: None,
        open: Some(json!({ "list": list })),
        actions: SEEN_ACTIONS,
    })
}

/// The ideas still waiting for a person, as slot-1 cards (a ledger that does not read whole
/// gives none: a line it could not read may be the answer).
pub fn idea_cards(ideas: &str, tree: &BTreeSet<String>) -> Vec<Card> {
    let l = aib_idea::replay(ideas);
    if !l.complete() {
        return Vec::new();
    }
    l.ideas
        .iter()
        .filter(|i| i.status() == "needs_human")
        .map(|i| Card {
            card_id: format!("idea:{}", i.id),
            slot: 1,
            name: NAMES[0],
            kind: "idea.consent".into(),
            seat_id: Some(i.seat.clone()),
            session_id: None,
            at: crate::sessions::rfc3339_of_unix(i.at_ms / 1000),
            global_seq: None,
            item: Some(i.title.clone()),
            why: Some(i.benefit.clone()),
            pct: None,
            card: i.node.clone().filter(|n| tree.contains(n)),
            rec: Some(i.suggest.clone()),
            state: Some(i.cost.clone()),
            src: Some(i.seat.clone()),
            tag: None,
            open: None,
            actions: IDEA_ACTIONS,
        })
        .collect()
}

/// Both kinds from a state root (`<root>/state/ideas/ideas.jsonl`,
/// `<root>/state/drive/andy-<seat>.txt`).
pub fn cards_in(root: &Path, tree: &BTreeSet<String>) -> Vec<Card> {
    let state = root.join("state");
    let ideas =
        std::fs::read_to_string(state.join("ideas").join("ideas.jsonl")).unwrap_or_default();
    let mut out = idea_cards(&ideas, tree);
    let seen = seen();
    let mut rows = Vec::new();
    let mut bad = 0usize;
    let mut at = String::new();
    if let Ok(rd) = std::fs::read_dir(state.join("drive")) {
        let mut files: Vec<_> = rd
            .flatten()
            .filter_map(|e| e.file_name().into_string().ok())
            .filter(|n| {
                n.strip_prefix("andy-")
                    .and_then(|r| r.strip_suffix(".txt"))
                    .is_some_and(|s| s.len() == 5 && s.bytes().all(|b| b.is_ascii_hexdigit()))
            })
            .collect();
        files.sort();
        for f in files {
            let mut p = state.join("drive").join(&f);
            // the structured ledger beside it is the list when there is one (519d0 11-19 09:35;
            // the .txt is only its output then)
            let ledger = state.join("drive").join(
                f.replacen("andy-", "andy-review-", 1)
                    .replace(".txt", ".jsonl"),
            );
            if ledger.is_file() {
                if let Ok(t) = std::fs::read_to_string(&ledger) {
                    let (r, b) = ledger_rows(&t, &seen);
                    rows.extend(r);
                    bad += b;
                }
                p = ledger;
            } else if let Ok(t) = std::fs::read_to_string(&p) {
                rows.extend(list_rows(&t, &seen));
            }
            if let Some(m) = std::fs::metadata(&p)
                .ok()
                .and_then(|m| m.modified().ok())
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            {
                let s = crate::sessions::rfc3339_of_unix(i64::try_from(m.as_secs()).unwrap_or(0));
                at = at.max(s);
            }
        }
    }
    rows.truncate(LIST_MAX);
    let mut list = list_card(&rows, &at);
    if let (Some(c), true) = (list.as_mut(), bad > 0) {
        if let Some(Value::Object(o)) = c.open.as_mut() {
            o.insert("unread".into(), json!(bad));
        }
    }
    out.extend(list);
    out
}

/// The cards now (none when the state root is not known).
pub fn cards() -> Vec<Card> {
    let Some(root) = crate::slots::state_root() else {
        return Vec::new();
    };
    let tree: BTreeSet<String> = crate::slots::tree_ids().into_iter().collect();
    cards_in(&root, &tree)
}

/// Whether `line` is one of the waiting lines now.
pub fn waiting_line(line: &str) -> bool {
    cards().iter().any(|c| {
        c.card_id == LIST_ID
            && c.open
                .as_ref()
                .and_then(|o| o.get("list"))
                .and_then(Value::as_array)
                .is_some_and(|l| {
                    l.iter()
                        .any(|r| r.get("id").and_then(Value::as_str) == Some(line))
                })
    })
}

/// Whether `card` is an idea card waiting now.
pub fn waiting_idea(card: &str) -> bool {
    cards()
        .iter()
        .any(|c| c.card_id == card && c.kind == "idea.consent")
}

/// What a user folder page sees of a review list: how many lines, no words.
pub fn hide_list(v: &mut Value) {
    if let Some(o) = v.as_object_mut() {
        let n = o
            .get("open")
            .and_then(|o| o.get("list"))
            .and_then(Value::as_array)
            .map_or(0, Vec::len);
        o.insert("open".into(), json!({ "count": n }));
    }
}

/// The ideas hung on `node` that went through on their own (auto, pending or ok): one record
/// each for the card window, newest first: `{at: "MM-DD HH:MM" (Taipei), by: <seat>,
/// kind: "idea", kinds: ["idea"], text, mark: "已自動同意", evidence: <source>}`. The title is a
/// seat's words: hashed for a page without words.
pub fn node_ideas(ideas: &str, node: &str, words: bool) -> Vec<Value> {
    let l = aib_idea::replay(ideas);
    if !l.complete() {
        return Vec::new();
    }
    let mut out: Vec<(i64, Value)> = l
        .ideas
        .iter()
        .filter(|i| i.node.as_deref() == Some(node))
        .filter(|i| matches!(i.status(), "auto" | "ok"))
        .map(|i| {
            let t = crate::sessions::rfc3339_of_unix(i.at_ms / 1000 + 8 * 3600);
            let at = t.get(5..16).unwrap_or("").replace('T', " ");
            (
                i.at_ms,
                json!({"at": at, "by": i.seat, "kind": "idea", "kinds": ["idea"],
                       "text": crate::workspace::words_or_hash(&i.title, words),
                       "mark": "已自動同意", "evidence": i.source}),
            )
        })
        .collect();
    out.sort_by_key(|x| std::cmp::Reverse(x.0));
    out.into_iter().map(|(_, v)| v).collect()
}

/// [`node_ideas`] from the state root's ledger (none when it is not known or unread).
pub fn node_ideas_now(node: &str, words: bool) -> Vec<Value> {
    let Some(root) = crate::slots::state_root() else {
        return Vec::new();
    };
    let text = std::fs::read_to_string(root.join("state").join("ideas").join("ideas.jsonl"))
        .unwrap_or_default();
    node_ideas(&text, node, words)
}

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({ "code": code, "message": message, "request_id": crate::guard::request_id() })),
    )
        .into_response()
}

fn only(body: &Value, keys: &[&str]) -> bool {
    body.as_object()
        .is_some_and(|o| o.keys().all(|k| keys.contains(&k.as_str())))
}

/// The paired page that pressed (its key digest, 8 bytes in hex): who, never what it holds.
fn by(page: &PageAccess) -> String {
    page.digest[..8]
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// The owner's fingerprint for a page's press (DECISION-council-ideas-2 §2): every 同意 /
/// 不同意, and one for a batch of 看過. Until the aib-consent queue is wired no ticket can be
/// asked: the press is only recorded as 「頁面按過、未證實」 and nothing is answered.
fn fingerprint(
    operation: &'static str,
    resource: String,
    params: Vec<u8>,
    page: &PageAccess,
) -> Value {
    let ask = crate::owner_ok::Ask {
        category: aib_approve::category::Category::ProjectRuling,
        operation,
        resource,
        params,
        by: format!("page:{}", by(page)),
    };
    match crate::owner_ok::ask(&ask) {
        crate::owner_ok::Asked::Unavailable(c) => json!(c),
        // a ticket is not acted on at this stage either (the check before acting is not wired)
        crate::owner_ok::Asked::Ticket(_) => json!(crate::owner_ok::UNAVAILABLE),
    }
}

/// act `idea_answer {card_id, answer: "agree" | "disagree"}` (a confirmed built-in page, the
/// slots' gate): 404 `idea_not_waiting` when that idea card is not open now. Each press asks
/// the fingerprint; unproved it writes `idea.pressed {verified: false}` and answers
/// `{card_id, answer, verified: false, code, journaled}` with the card still waiting.
pub async fn answer(st: AppState, page: PageAccess, body: Value) -> Response {
    let card = body
        .get("card_id")
        .and_then(Value::as_str)
        .map(str::to_owned);
    let ans = body
        .get("answer")
        .and_then(Value::as_str)
        .map(str::to_owned);
    let (Some(card), Some(ans)) = (
        card.filter(|c| only(&body, &["type", "card_id", "answer"]) && c.starts_with("idea:")),
        ans.filter(|a| a == "agree" || a == "disagree"),
    ) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "body must be {type, card_id: \"idea:<id>\", answer: \"agree\" | \"disagree\"}",
        );
    };
    let c2 = card.clone();
    let open = tokio::task::spawn_blocking(move || waiting_idea(&c2))
        .await
        .unwrap_or(false);
    if !open {
        return refuse(
            StatusCode::NOT_FOUND,
            "idea_not_waiting",
            "that idea is not waiting for an answer",
        );
    }
    let code = fingerprint(
        if ans == "agree" {
            "idea.agree"
        } else {
            "idea.disagree"
        },
        card.clone(),
        format!("{card}\n{ans}").into_bytes(),
        &page,
    );
    let journaled = crate::slots::journal(
        &st,
        "idea.pressed",
        json!({"card_id": card, "answer": ans, "by": by(&page), "verified": false, "code": code}),
    )
    .await;
    Json(
        json!({"card_id": card, "answer": ans, "verified": false, "code": code,
                "journaled": journaled}),
    )
    .into_response()
}

/// The hash of a batch of lines for the fingerprint window (§2: 件數 + 清單雜湊): the first
/// 12 hex of the SHA-256 of the ids joined by newlines, in the order given.
pub fn batch_hash(ids: &[String]) -> String {
    Sha256::digest(ids.join("\n").as_bytes())
        .iter()
        .take(6)
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// act `review_seen {line_ids: [<12 hex>, …]}` (a confirmed built-in page; one press for
/// several lines, at most [`LIST_MAX`], no repeats): 404 `line_not_waiting` when one is not on
/// the list now. One fingerprint for the batch; unproved it writes `review.pressed
/// {verified: false}` and answers `{line_ids, count, hash, verified: false, code, journaled}`
/// with every line still waiting.
pub async fn seen_act(st: AppState, page: PageAccess, body: Value) -> Response {
    let ids: Option<Vec<String>> = body.get("line_ids").and_then(Value::as_array).map(|a| {
        a.iter()
            .filter_map(Value::as_str)
            .filter(|l| l.len() == 12 && l.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')))
            .map(str::to_owned)
            .collect()
    });
    let given = body
        .get("line_ids")
        .and_then(Value::as_array)
        .map_or(0, Vec::len);
    let Some(ids) = ids.filter(|v| {
        only(&body, &["type", "line_ids"])
            && !v.is_empty()
            && v.len() == given
            && v.len() <= LIST_MAX
            && v.iter().collect::<BTreeSet<_>>().len() == v.len()
    }) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "body must be {type, line_ids: [12 hex, …]}, no repeats",
        );
    };
    let i2 = ids.clone();
    let open = tokio::task::spawn_blocking(move || i2.iter().all(|l| waiting_line(l)))
        .await
        .unwrap_or(false);
    if !open {
        return refuse(
            StatusCode::NOT_FOUND,
            "line_not_waiting",
            "a line is not on the list",
        );
    }
    let hash = batch_hash(&ids);
    let code = fingerprint(
        "review.seen",
        format!("{} 條 {hash}", ids.len()),
        ids.join("\n").into_bytes(),
        &page,
    );
    let journaled = crate::slots::journal(
        &st,
        "review.pressed",
        json!({"line_ids": ids, "hash": hash, "by": by(&page), "verified": false, "code": code}),
    )
    .await;
    Json(
        json!({"line_ids": ids, "count": ids.len(), "hash": hash, "verified": false,
                "code": code, "journaled": journaled}),
    )
    .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    const ADD: &str = r#"{"at_ms":1794645000000,"axis":"stable","benefit":"省時間","classification":"needs_human","cost":"回復:revert","ev":"add","event_id":"0d1e2f30-4152-4637-8899-aabbccddeeff","grade":"A","idea_id":"11111111-2222-4333-8444-555555555555","node":"S76","now":"現況","policy_version":1,"recorded_by_seat":"043dd","source":null,"suggest":"做法","title":"要人同意的一條","touches":["prod","data"],"v":1}"#;
    const AUTO: &str = r#"{"at_ms":1794645000001,"axis":"simple","benefit":"b","classification":"auto","cost":"c","ev":"add","event_id":"0d1e2f30-4152-4637-8899-aabbccddeef0","grade":"C","idea_id":"11111111-2222-4333-8444-555555555556","node":null,"now":"n","policy_version":1,"recorded_by_seat":"519d0","source":null,"suggest":"s","title":"自動的一條","touches":["none"],"v":1}"#;

    #[test]
    fn a_pending_needs_human_idea_is_a_decide_card_with_agree_and_disagree() {
        let text = format!("{ADD}\n{AUTO}\n");
        let tree: BTreeSet<String> = ["S76".to_owned()].into();
        let c = idea_cards(&text, &tree);
        assert_eq!(c.len(), 1, "reason: auto_or_missing {c:?}");
        assert_eq!(c[0].card_id, "idea:11111111-2222-4333-8444-555555555555");
        assert_eq!((c[0].slot, c[0].card.as_deref()), (1, Some("S76")));
        assert_eq!(c[0].actions, IDEA_ACTIONS);
        assert_eq!(c[0].item.as_deref(), Some("要人同意的一條"));
        // a ledger that does not read whole gives no card
        assert!(
            idea_cards(&format!("{ADD}\nnot json\n"), &tree).is_empty(),
            "reason: partial_ledger_read"
        );
    }

    #[test]
    fn an_auto_idea_on_a_node_is_a_record_marked_auto_approved() {
        let text = format!(
            "{ADD}\n{}\n",
            AUTO.replace(r#""node":null"#, r#""node":"S76""#)
        );
        let r = node_ideas(&text, "S76", true);
        assert_eq!(r.len(), 1, "reason: needs_human_or_missing {r:?}");
        assert_eq!(r[0]["kind"], "idea");
        assert_eq!(r[0]["mark"], "已自動同意");
        assert_eq!(r[0]["text"], "自動的一條");
        assert!(
            node_ideas(&text, "S77", true).is_empty(),
            "reason: other_node"
        );
    }

    #[test]
    fn the_structured_ledger_gives_its_own_ids_and_a_rewritten_line_names_the_old_one() {
        let text = concat!(
            r#"{"id":"ac305269bbd8","line":"1","at":null,"kind":"note","by":"seat:519d0","text":"n","src":"andy-519d0.txt"}"#,
            "\n",
            r#"{"id":"111111111111","line":"2","at":"11-19 08:25","kind":"decision","by":"seat:519d0","text":"11-19 08:25 519d0 裁:x|S1","src":"andy-519d0.txt"}"#,
            "\n",
            r#"{"id":"222222222222","line":"3","at":null,"kind":"pending_review","by":"seat:519d0","text":"真人未審(後審):y","src":"andy-519d0.txt"}"#,
            "\n",
            "not json\n",
            r#"{"id":"333333333333","line":"2","at":"11-19 08:30","kind":"decision","by":"seat:519d0","text":"11-19 08:25 519d0 裁:x 改字","src":"andy-519d0.txt"}"#,
            "\n",
        );
        let (rows, bad) = ledger_rows(text, &BTreeSet::new());
        assert_eq!(bad, 1, "reason: bad_line_not_counted");
        let ids: Vec<&str> = rows.iter().map(|r| r.id.as_str()).collect();
        assert_eq!(
            ids,
            ["333333333333", "222222222222"],
            "reason: note_or_old_kept {rows:?}"
        );
        assert_eq!(rows[0].replaces, ["111111111111"], "reason: rewrite_lost");
        assert_eq!(rows[0].line, 2);
        let seen: BTreeSet<String> = ["222222222222".to_owned()].into();
        assert_eq!(ledger_rows(text, &seen).0.len(), 1, "reason: seen_kept");
    }

    #[test]
    fn the_after_review_lines_are_one_list_and_a_seen_line_leaves_it() {
        let text = "preview.7 已公開|x\n真人未審(後審):我親手合了 #818|S79-release\n11-19 08:25 519d0 裁:預覽不退\n11-19 08:33 519d0 改裁:撤回 08:32\n需你決定:刪分支|y\n11-19 09:00 519d0 說:不是裁\n";
        let rows = list_rows(text, &BTreeSet::new());
        let texts: Vec<&str> = rows.iter().map(|r| r.text.as_str()).collect();
        assert_eq!(
            texts,
            [
                "真人未審(後審):我親手合了 #818",
                "11-19 08:25 519d0 裁:預覽不退",
                "11-19 08:33 519d0 改裁:撤回 08:32"
            ],
            "reason: wrong_lines"
        );
        assert_eq!(rows[0].refs.as_deref(), Some("S79-release"));
        assert_eq!(
            rows.iter().map(|r| r.line).collect::<Vec<_>>(),
            [2, 3, 4],
            "reason: line_numbers"
        );
        assert_eq!(rows[0].id.len(), 12);
        // the same words with full-width letters and other spacing are the same line
        assert_eq!(
            line_id("11-19 08:25 519d0 裁:預覽不退"),
            line_id(" 11-19\u{3000}08:25  ５１９ｄ０ 裁:預覽不退 ")
        );
        // the ref after `|` is not part of the id
        let moved = list_rows(&text.replace("|S79-release", "| S80"), &BTreeSet::new());
        assert_eq!(moved[0].id, rows[0].id, "reason: ref_in_id");
        assert_eq!(rows[0].id, line_id("真人未審(後審):我親手合了 #818"));
        let seen: BTreeSet<String> = [rows[1].id.clone()].into();
        assert_eq!(list_rows(text, &seen).len(), 2, "reason: seen_kept");
        let c = list_card(&rows, "2026-11-19T00:00:00Z").unwrap();
        assert_eq!((c.card_id.as_str(), c.slot), (LIST_ID, 1));
        assert_eq!(c.item.as_deref(), Some("真人未審 3 條"));
        let mut v = c.view();
        hide_list(&mut v);
        assert_eq!(
            v["open"],
            json!({"count": 3}),
            "reason: list_words_to_user_page"
        );
        assert!(list_card(&[], "x").is_none());
    }
}
