//! A seat proposes a new text for its own persona card; it never writes it (DESIGN-persona-
//! propose-osverify-c2f0b-1123 r2 §1; Andy 11-22「寫回人格:四種都只能提案 + 驗證」).
//!
//! This is S2-1, the proposal half: the seat's three acts, the built-in page's list and no.
//! Writing (the owner's fingerprint on `persona.write_back`, then `personas::write_card`) is
//! S2-2. The whole file is the small way in that §0 option A allows while owner-request's own
//! card is not in main: its fields, states and refusal words are owner-request r4's, and when
//! that card lands in main this file goes and `persona_propose` becomes a line of its table
//! (owner: c2f0b, the slice after owner-request's go / deny / owner_pending are merged).
//!
//! - The persona is the seat's own (`seat_meta.persona_uid`): a body naming one is refused
//!   (`request_param_not_taken`), so a seat cannot propose for another persona (D2).
//! - The text: UTF-8, at most 64 KiB and [`persona_diff::MAX_LINES`] lines, the same `uid` and
//!   `code` in its front matter, not the same as the card, and no added line with a character
//!   the diff would not show (refused, not cleaned: what is shown is what is written, D3).
//! - Frozen when asked: the whole text, the card's sha it was diffed against, the diff. There is
//!   no way to change a proposal; withdraw and propose again (that counts too).
//! - At most one proposal waiting per persona card (two from the same base: one is stale the
//!   moment the other is written), [`MAX_WAITING`] per seat, [`MAX_PER_HOUR`] asks an hour per
//!   seat counting withdrawn ones; the counts are memory only (aibd restarts: they start over).
//! - The page's list and no: the built-in page in this pairing only (not a user folder page,
//!   not a carried pairing), like every owner-request go / deny. A seat's token reads and
//!   withdraws its own proposals only; another seat's is `request_not_found`.
//! - The page draws every diff line and the reason as plain text (textContent), never as
//!   markdown or HTML (f163c PREREAD r0 M1); this module hands them over unchanged.
//!
//! Nothing here is a defence by itself: it decides what may be put in front of the owner. The
//! defence is S2-2's fingerprint window, whose words aibd makes from this frozen proposal.

use crate::api::AppState;
use crate::guard::request_id;
use crate::persona_diff::{self, Diff};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::sync::{LazyLock, Mutex};

/// The seat's acts.
pub const PROPOSE: &str = "persona_propose";
pub const STATUS: &str = "persona_propose_status";
pub const WITHDRAW: &str = "persona_propose_withdraw";
/// The built-in page's acts.
pub const LIST: &str = "persona_proposals";
pub const DENY: &str = "persona_propose_deny";

/// How long a proposal waits (owner-request r4 §3b: 30 minutes).
pub const TTL_SECS: u64 = 30 * 60;
pub const MAX_WAITING: usize = 3;
pub const MAX_PER_HOUR: usize = 10;
pub const WHY_MAX: usize = 200;

/// Where a proposal stands.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Stand {
    Waiting,
    Withdrawn,
    Denied,
    Expired,
}

impl Stand {
    pub fn word(self) -> &'static str {
        match self {
            Stand::Waiting => "waiting",
            Stand::Withdrawn => "withdrawn",
            Stand::Denied => "denied",
            Stand::Expired => "expired",
        }
    }
}

/// One frozen proposal.
#[derive(Debug, Clone)]
pub struct Proposal {
    pub id: String,
    pub seat_id: String,
    pub persona_uid: String,
    pub persona_name: String,
    pub base_sha: String,
    pub new_sha: String,
    pub new_text: String,
    pub diff: Diff,
    pub diff_sha: String,
    pub why: String,
    pub stand: Stand,
    pub expires_at: u64,
}

/// The card a proposal is made against, as aibd read it.
pub struct Card<'a> {
    pub uid: &'a str,
    pub name: &'a str,
    pub sha: &'a str,
    pub text: &'a str,
}

/// What a seat asked.
pub struct Ask<'a> {
    pub seat_id: &'a str,
    pub card_text: &'a str,
    pub why: &'a str,
}

/// The proposals of this aibd, in memory only.
#[derive(Default)]
pub struct Book {
    list: Mutex<Vec<Proposal>>,
    asks: Mutex<Vec<(String, u64)>>,
}

pub fn sha_hex(b: &[u8]) -> String {
    Sha256::digest(b)
        .iter()
        .map(|x| format!("{x:02x}"))
        .collect()
}

/// The value of `key:` in the text's front matter (between a first line `---` and the next).
pub fn front_value(text: &str, key: &str) -> Option<String> {
    let mut lines = text.lines();
    if lines.next()?.trim_end() != "---" {
        return None;
    }
    for l in lines {
        if l.trim_end() == "---" {
            return None;
        }
        if let Some((k, v)) = l.split_once(':') {
            if k.trim() == key {
                return Some(v.trim().trim_matches(|c| c == '"' || c == '\'').to_owned());
            }
        }
    }
    None
}

/// The seat's reason as the page card shows it (owner-request r4 §1a): no invisible
/// characters, nothing that reads as a clock or as the window's own 「請在」, at most
/// [`WHY_MAX`] characters. Only on the page card, marked unverified.
pub fn clean_why(s: &str) -> String {
    let mut kept: Vec<char> = s
        .chars()
        .filter(|c| !aib_approve::text::is_bad_char(*c))
        .take(WHY_MAX)
        .collect();
    for i in 1..kept.len().saturating_sub(1) {
        if matches!(kept[i], ':' | '：') && kept[i - 1].is_numeric() && kept[i + 1].is_numeric() {
            kept[i] = '·';
        }
    }
    kept.iter().collect::<String>().replace("請在", "請·在")
}

impl Book {
    fn list(&self) -> std::sync::MutexGuard<'_, Vec<Proposal>> {
        self.list.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Marks every waiting proposal past its end expired (r4 L4: before anything is counted).
    fn sweep(l: &mut [Proposal], now: u64) {
        for p in l.iter_mut() {
            if p.stand == Stand::Waiting && now >= p.expires_at {
                p.stand = Stand::Expired;
            }
        }
    }

    /// A seat proposes `ask.card_text` for `card`. Ok((proposal, dedup)) or the refusal word.
    pub fn propose(
        &self,
        card: &Card<'_>,
        ask: &Ask<'_>,
        id: impl FnOnce() -> Option<String>,
        now: u64,
    ) -> Result<(Proposal, bool), &'static str> {
        let new = ask.card_text;
        if new.len() as u64 > crate::personas::MAX_PERSONA_TEXT {
            return Err("persona_text_too_big");
        }
        if new == card.text {
            return Err("persona_no_change");
        }
        for key in ["uid", "code"] {
            if front_value(new, key) != front_value(card.text, key) {
                return Err("persona_identity_changed");
            }
        }
        let diff = persona_diff::diff(card.text, new).map_err(|e| e.reason())?;
        if persona_diff::hidden_char_line(&diff).is_some() {
            return Err("persona_bad_char");
        }
        let new_sha = sha_hex(new.as_bytes());
        let mut l = self.list();
        Self::sweep(&mut l, now);
        let waiting = |p: &&Proposal| p.stand == Stand::Waiting;
        if let Some(p) = l
            .iter()
            .filter(waiting)
            .find(|p| p.seat_id == ask.seat_id && p.new_sha == new_sha)
        {
            return Ok((p.clone(), true));
        }
        if l.iter().filter(waiting).any(|p| p.persona_uid == card.uid) {
            return Err("persona_proposal_open");
        }
        if l.iter()
            .filter(waiting)
            .filter(|p| p.seat_id == ask.seat_id)
            .count()
            >= MAX_WAITING
        {
            return Err("request_too_often");
        }
        {
            let mut a = self.asks.lock().unwrap_or_else(|e| e.into_inner());
            a.retain(|(_, at)| now < at + 3600);
            if a.iter().filter(|(s, _)| s == ask.seat_id).count() >= MAX_PER_HOUR {
                return Err("request_too_often");
            }
            a.push((ask.seat_id.to_owned(), now));
        }
        let p = Proposal {
            id: id().ok_or("internal")?,
            seat_id: ask.seat_id.to_owned(),
            persona_uid: card.uid.to_owned(),
            persona_name: card.name.to_owned(),
            base_sha: card.sha.to_owned(),
            diff_sha: persona_diff::diff_sha(card.uid, card.sha, &new_sha),
            new_sha,
            new_text: new.to_owned(),
            diff,
            why: clean_why(ask.why),
            stand: Stand::Waiting,
            expires_at: now + TTL_SECS,
        };
        l.push(p.clone());
        // the oldest ended ones leave first; the waiting ones stay
        while l.len() > 200 {
            match l.iter().position(|p| p.stand != Stand::Waiting) {
                Some(i) => {
                    l.remove(i);
                }
                None => break,
            }
        }
        Ok((p, false))
    }

    /// A proposal as `seat` may see it (`None`: the page, any). Another seat's is not found.
    pub fn get(&self, seat: Option<&str>, id: &str, now: u64) -> Option<Proposal> {
        let mut l = self.list();
        Self::sweep(&mut l, now);
        l.iter()
            .find(|p| p.id == id && seat.is_none_or(|s| s == p.seat_id))
            .cloned()
    }

    /// The seat withdraws its own waiting proposal (it still counts as an ask).
    pub fn withdraw(&self, seat: &str, id: &str, now: u64) -> Result<Proposal, &'static str> {
        self.end(Some(seat), id, Stand::Withdrawn, now)
    }

    /// The owner says no (the built-in page; no fingerprint: only stricter).
    pub fn deny(&self, id: &str, now: u64) -> Result<Proposal, &'static str> {
        self.end(None, id, Stand::Denied, now)
    }

    fn end(
        &self,
        seat: Option<&str>,
        id: &str,
        to: Stand,
        now: u64,
    ) -> Result<Proposal, &'static str> {
        let mut l = self.list();
        Self::sweep(&mut l, now);
        let p = l
            .iter_mut()
            .find(|p| p.id == id && seat.is_none_or(|own| own == p.seat_id))
            .ok_or("request_not_found")?;
        if p.stand != Stand::Waiting {
            return Err("request_not_waiting");
        }
        p.stand = to;
        Ok(p.clone())
    }

    /// Every waiting proposal, oldest first (the page's list).
    pub fn waiting(&self, now: u64) -> Vec<Proposal> {
        let mut l = self.list();
        Self::sweep(&mut l, now);
        l.iter()
            .filter(|p| p.stand == Stand::Waiting)
            .cloned()
            .collect()
    }
}

/// This aibd's proposals.
pub static BOOK: LazyLock<Book> = LazyLock::new(Book::default);

/// What the seat is told: no text (it has it), the counts and the hash it will see the owner
/// compare.
pub fn seat_view(p: &Proposal, dedup: bool) -> Value {
    json!({
        "request_id": p.id,
        "state": p.stand.word(),
        "expires_at": p.expires_at,
        "added": p.diff.added,
        "removed": p.diff.removed,
        "diff12": &p.diff_sha[..12],
        "dedup": dedup,
    })
}

/// What the built-in page draws (every `text` and `why` as plain text only).
pub fn page_view(p: &Proposal) -> Value {
    let lines: Vec<Value> = p
        .diff
        .lines
        .iter()
        .map(|l| json!({"op": l.op.word(), "old_no": l.old_no, "new_no": l.new_no, "text": l.text}))
        .collect();
    json!({
        "request_id": p.id,
        "seat_id": p.seat_id,
        "persona_uid": p.persona_uid,
        "persona_name": p.persona_name,
        "base12": &p.base_sha[..12.min(p.base_sha.len())],
        "new12": &p.new_sha[..12],
        "diff12": &p.diff_sha[..12],
        "added": p.diff.added,
        "removed": p.diff.removed,
        "why": p.why,
        "why_unverified": true,
        "expires_at": p.expires_at,
        "state": p.stand.word(),
        "diff": lines,
    })
}

fn refuse(code: &str) -> Response {
    let status = match code {
        "request_not_found" => StatusCode::NOT_FOUND,
        "request_too_often" => StatusCode::TOO_MANY_REQUESTS,
        "persona_proposal_open"
        | "request_not_waiting"
        | "persona_changed"
        | "persona_unreadable"
        | "persona_none" => StatusCode::CONFLICT,
        "persona_built_in_page_only" | "item_off" => StatusCode::FORBIDDEN,
        "internal" => StatusCode::INTERNAL_SERVER_ERROR,
        _ => StatusCode::BAD_REQUEST,
    };
    (
        status,
        Json(json!({ "code": code, "request_id": request_id() })),
    )
        .into_response()
}

/// A seat's act of this file (by `type`).
pub fn is_seat_act(body: &Value) -> bool {
    matches!(
        body.get("type").and_then(Value::as_str),
        Some(PROPOSE | STATUS | WITHDRAW)
    )
}

/// The keys each seat act takes; anything else is refused (`request_param_not_taken`).
fn keys_of(kind: &str) -> &'static [&'static str] {
    match kind {
        PROPOSE => &["type", "card_text", "why"],
        _ => &["type", "request_id"],
    }
}

/// The seat's own persona card and its text, read the way a seat open reads it.
fn own_card(
    st: &AppState,
    seat: &str,
) -> Result<(crate::personas::PersonaCard, String), &'static str> {
    let uid = st
        .store
        .seat_meta(seat)
        .ok()
        .flatten()
        .and_then(|m| m.persona_uid)
        .ok_or("persona_none")?;
    let card = st
        .personas
        .cards()
        .map_err(|_| "persona_unreadable")?
        .into_iter()
        .find(|c| c.uid == uid)
        .ok_or("persona_none")?;
    let path = card.text_path.clone().ok_or("persona_unreadable")?;
    let text = crate::personas::card_text(&path, &card.persona_sha).map_err(|e| e.reason())?;
    let text = String::from_utf8(text).map_err(|_| "persona_unreadable")?;
    Ok((card, text))
}

/// `persona_propose` / `_status` / `_withdraw` from a seat's own token.
pub async fn seat_act(st: AppState, seat: &str, body: Value) -> Response {
    let kind = body
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let Some(obj) = body.as_object() else {
        return refuse("invalid_request");
    };
    if obj.keys().any(|k| !keys_of(&kind).contains(&k.as_str())) {
        return refuse("request_param_not_taken");
    }
    let now = crate::owner_ok::unix_now();
    let seat = seat.to_owned();
    let done = tokio::task::spawn_blocking(move || match kind.as_str() {
        PROPOSE => {
            let text = body["card_text"].as_str().ok_or("card_text_missing")?;
            let why = body["why"].as_str().unwrap_or("");
            let (card, current) = own_card(&st, &seat)?;
            let (p, dedup) = BOOK.propose(
                &Card {
                    uid: &card.uid,
                    name: &card.name,
                    sha: &card.persona_sha,
                    text: &current,
                },
                &Ask {
                    seat_id: &seat,
                    card_text: text,
                    why,
                },
                || crate::sessions::new_id("pp_").ok(),
                now,
            )?;
            let d12 = p.diff_sha.get(..12).unwrap_or_default();
            tracing::info!(seat = %seat, persona = %p.persona_uid, diff12 = %d12, dedup, "persona proposal");
            Ok(seat_view(&p, dedup))
        }
        STATUS => {
            let id = body["request_id"].as_str().unwrap_or_default();
            BOOK.get(Some(&seat), id, now)
                .map(|p| seat_view(&p, false))
                .ok_or("request_not_found")
        }
        _ => {
            let id = body["request_id"].as_str().unwrap_or_default();
            BOOK.withdraw(&seat, id, now).map(|p| seat_view(&p, false))
        }
    })
    .await;
    match done {
        Ok(Ok(v)) => {
            let code = if v["state"] == "waiting" && v["dedup"] == false {
                StatusCode::ACCEPTED
            } else {
                StatusCode::OK
            };
            (code, Json(v)).into_response()
        }
        Ok(Err(code)) => refuse(code),
        Err(_) => refuse("internal"),
    }
}

/// `persona_proposals` / `persona_propose_deny {ids}` from a page: the built-in page in this
/// pairing only.
pub async fn page_act(page: &crate::pairing::PageAccess, body: Value) -> Response {
    if page.carried || crate::seat_input::held_as_user_page(page).await {
        return refuse("persona_built_in_page_only");
    }
    let now = crate::owner_ok::unix_now();
    match body.get("type").and_then(Value::as_str) {
        Some(LIST) => {
            let l: Vec<Value> = BOOK.waiting(now).iter().map(page_view).collect();
            Json(json!({ "proposals": l })).into_response()
        }
        Some(DENY) => {
            let Some(ids) = body["ids"]
                .as_array()
                .filter(|a| (1..=20).contains(&a.len()))
            else {
                return refuse("invalid_request");
            };
            let out: Vec<Value> = ids
                .iter()
                .map(|i| {
                    let id = i.as_str().unwrap_or_default();
                    match BOOK.deny(id, now) {
                        Ok(p) => json!({"request_id": id, "state": p.stand.word()}),
                        Err(code) => json!({"request_id": id, "code": code}),
                    }
                })
                .collect();
            Json(json!({ "results": out })).into_response()
        }
        _ => refuse("invalid_request"),
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    const OLD: &str = "---\nuid: lvkson\ncode: owner-sec\n---\n# 人格\n規則一\n規則二\n";

    fn card() -> Card<'static> {
        Card {
            uid: "lvkson",
            name: "AIB 本人核准安全工程師",
            sha: "4f97e4fa158f882f7f9806785ddc5f081b20bdc6f8f024a278324aa888643e6b",
            text: OLD,
        }
    }

    fn ask<'a>(seat: &'a str, text: &'a str) -> Ask<'a> {
        Ask {
            seat_id: seat,
            card_text: text,
            why: "學到 12:30 的事,請在今天按",
        }
    }

    fn ids() -> impl FnMut() -> Option<String> {
        let mut n = 0;
        move || {
            n += 1;
            Some(format!("pp_{n}"))
        }
    }

    fn new_text(extra: &str) -> String {
        format!("{OLD}{extra}\n")
    }

    #[test]
    fn a_proposal_is_frozen_with_its_diff_and_hash() {
        let b = Book::default();
        let mut id = ids();
        let t = new_text("規則三");
        let (p, dedup) = b
            .propose(&card(), &ask("seat_ab", &t), &mut id, 100)
            .unwrap();
        assert!(!dedup);
        assert_eq!((p.diff.added, p.diff.removed), (1, 0));
        assert_eq!(p.new_text, t);
        assert_eq!(p.base_sha, card().sha);
        assert_eq!(
            p.diff_sha,
            persona_diff::diff_sha("lvkson", card().sha, &sha_hex(t.as_bytes()))
        );
        assert_eq!(p.expires_at, 100 + TTL_SECS);
        // the reason: no clock, no 請在
        assert!(
            !p.why.contains("12:30") && !p.why.contains("請在"),
            "{}",
            p.why
        );
        // the same text again from the same seat is the same proposal
        let (q, dedup) = b
            .propose(&card(), &ask("seat_ab", &t), &mut id, 101)
            .unwrap();
        assert!(dedup);
        assert_eq!(q.id, p.id);
    }

    #[test]
    fn the_identity_and_hidden_characters_are_refused() {
        let b = Book::default();
        let mut id = ids();
        let renamed = OLD.replace("uid: lvkson", "uid: other1");
        assert_eq!(
            b.propose(&card(), &ask("seat_ab", &renamed), &mut id, 1)
                .err(),
            Some("persona_identity_changed")
        );
        let recoded = OLD.replace("code: owner-sec", "code: x");
        assert_eq!(
            b.propose(&card(), &ask("seat_ab", &recoded), &mut id, 1)
                .err(),
            Some("persona_identity_changed")
        );
        let hidden = new_text("看不見\u{202e}的字");
        assert_eq!(
            b.propose(&card(), &ask("seat_ab", &hidden), &mut id, 1)
                .err(),
            Some("persona_bad_char")
        );
        assert_eq!(
            b.propose(&card(), &ask("seat_ab", OLD), &mut id, 1).err(),
            Some("persona_no_change")
        );
    }

    /// PW11 (aibd half): the text is handed to the page unchanged; the page draws it as text.
    #[test]
    fn markup_is_kept_as_it_was_written() {
        let b = Book::default();
        let t = new_text("<!-- x -->\n<img src=x onerror=alert(1)>\n[a](http://x \"t\")");
        let (p, _) = b.propose(&card(), &ask("seat_ab", &t), ids(), 1).unwrap();
        let v = page_view(&p);
        let texts: Vec<&str> = v["diff"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|l| l["op"] == "add")
            .map(|l| l["text"].as_str().unwrap())
            .collect();
        assert_eq!(
            texts,
            [
                "<!-- x -->",
                "<img src=x onerror=alert(1)>",
                "[a](http://x \"t\")"
            ]
        );
        assert_eq!(v["why_unverified"], true);
    }

    #[test]
    fn one_waiting_per_card_and_three_per_seat() {
        let b = Book::default();
        let mut id = ids();
        b.propose(&card(), &ask("seat_ab", &new_text("一")), &mut id, 1)
            .unwrap();
        assert_eq!(
            b.propose(&card(), &ask("seat_cd", &new_text("二")), &mut id, 2)
                .err(),
            Some("persona_proposal_open")
        );
        let other = |uid: &'static str| Card {
            uid,
            name: "x",
            sha: "aa",
            text: OLD,
        };
        // other cards, same seat: two more fit, a fourth does not
        let t2 = new_text("二");
        let (u2, u3, u4) = ("bbbbbb", "cccccc", "dddddd");
        b.propose(&other(u2), &ask("seat_ab", &t2), &mut id, 3)
            .unwrap();
        b.propose(&other(u3), &ask("seat_ab", &t2), &mut id, 4)
            .unwrap();
        assert_eq!(
            b.propose(&other(u4), &ask("seat_ab", &t2), &mut id, 5)
                .err(),
            Some("request_too_often")
        );
    }

    #[test]
    fn withdrawn_ones_count_toward_the_hour() {
        let b = Book::default();
        let mut id = ids();
        for i in 0..MAX_PER_HOUR {
            let (p, _) = b
                .propose(
                    &card(),
                    &ask("seat_ab", &new_text(&i.to_string())),
                    &mut id,
                    10,
                )
                .unwrap();
            b.withdraw("seat_ab", &p.id, 10).unwrap();
        }
        assert_eq!(
            b.propose(&card(), &ask("seat_ab", &new_text("x")), &mut id, 11)
                .err(),
            Some("request_too_often")
        );
        // an hour later it fits again
        assert!(b
            .propose(&card(), &ask("seat_ab", &new_text("x")), &mut id, 10 + 3600)
            .is_ok());
    }

    #[test]
    fn expired_ones_are_swept_before_counting() {
        let b = Book::default();
        let mut id = ids();
        b.propose(&card(), &ask("seat_ab", &new_text("一")), &mut id, 1)
            .unwrap();
        // past its end the card is free again
        let (p, _) = b
            .propose(
                &card(),
                &ask("seat_cd", &new_text("二")),
                &mut id,
                1 + TTL_SECS,
            )
            .unwrap();
        assert_eq!(p.stand, Stand::Waiting);
        assert_eq!(b.waiting(1 + TTL_SECS).len(), 1);
    }

    #[test]
    fn a_seat_sees_and_withdraws_only_its_own() {
        let b = Book::default();
        let (p, _) = b
            .propose(&card(), &ask("seat_ab", &new_text("一")), ids(), 1)
            .unwrap();
        assert!(b.get(Some("seat_cd"), &p.id, 2).is_none());
        assert_eq!(
            b.withdraw("seat_cd", &p.id, 2).err(),
            Some("request_not_found")
        );
        assert!(b.get(Some("seat_ab"), &p.id, 2).is_some());
        assert!(b.get(None, &p.id, 2).is_some());
        assert_eq!(b.deny(&p.id, 2).unwrap().stand, Stand::Denied);
        assert_eq!(
            b.withdraw("seat_ab", &p.id, 3).err(),
            Some("request_not_waiting")
        );
        assert!(b.waiting(3).is_empty());
    }

    #[test]
    fn front_matter_values() {
        assert_eq!(front_value(OLD, "uid").as_deref(), Some("lvkson"));
        assert_eq!(
            front_value("---\nuid: \"q1\"\n---\n", "uid").as_deref(),
            Some("q1")
        );
        assert_eq!(front_value("no front\nuid: x\n", "uid"), None);
        assert_eq!(front_value("---\na: 1\n---\nuid: x\n", "uid"), None);
    }

    #[test]
    fn a_seat_act_takes_only_its_keys() {
        assert!(keys_of(PROPOSE).contains(&"card_text"));
        assert!(!keys_of(PROPOSE).contains(&"persona_uid"));
        assert!(!keys_of(STATUS).contains(&"seat_id"));
    }

    /// PW10: the list and the no are the built-in page's in this pairing only.
    #[tokio::test]
    async fn a_carried_pairing_or_a_user_page_neither_lists_nor_says_no() {
        for (carried, user_page, why) in [(true, false, "carried"), (false, true, "user_page")] {
            let page = crate::pairing::PageAccess {
                digest: [0; 32],
                confirmed: true,
                user_page,
                carried,
            };
            for kind in [LIST, DENY] {
                let r = page_act(&page, json!({"type": kind, "ids": ["pp_1"]})).await;
                assert_eq!(r.status(), StatusCode::FORBIDDEN, "reason: {why} {kind}");
            }
        }
    }
}
