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
//! - The text: UTF-8, at most 64 KiB and [`persona_diff::MAX_LINES`] lines, the front matter
//!   byte for byte the card's (r4 §9), not the same as the card, and no added line with a character
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
/// The built-in page approves one proposal: the owner's fingerprint (or login password) for
/// [`OP_WRITE_BACK`], then [`crate::persona_write::write`] (S2-2 part 2; waits for A12).
pub const APPROVE: &str = "persona_propose_approve";
/// The owner_ok operation of writing a proposal back to its card.
pub const OP_WRITE_BACK: &str = "persona.write_back";

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
    /// Approved and being written (not swept, not withdrawn, not denied).
    Writing,
    Written,
    /// Approved but the write was refused; the code is in the journal and the answer.
    Failed,
}

impl Stand {
    pub fn word(self) -> &'static str {
        match self {
            Stand::Waiting => "waiting",
            Stand::Withdrawn => "withdrawn",
            Stand::Denied => "denied",
            Stand::Expired => "expired",
            Stand::Writing => "writing",
            Stand::Written => "written",
            Stand::Failed => "failed",
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

/// The front matter as written: from a first line `---` through the next `---` line, ends of
/// line included; `None` without one (or never closed).
pub fn front_block(text: &str) -> Option<&str> {
    let first = text.split_inclusive('\n').next()?;
    if first.trim_end() != "---" {
        return None;
    }
    let mut end = first.len();
    for l in text[first.len()..].split_inclusive('\n') {
        end += l.len();
        if l.trim_end() == "---" {
            return Some(&text[..end]);
        }
    }
    None
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
        // the whole front matter, not only uid / code: the 1.x card list takes its other
        // fields (category, name, …) from it, and a changed one leaves that list stale
        // (DESIGN-persona-propose-osverify-c2f0b-1123 r4 §9, S5)
        if front_block(new) != front_block(card.text) {
            return Err("persona_identity_changed");
        }
        let diff = persona_diff::diff(card.text, new).map_err(|e| e.reason())?;
        if persona_diff::hidden_char_line(&diff).is_some() {
            return Err("persona_bad_char");
        }
        let new_sha = sha_hex(new.as_bytes());
        // the same card, the same base and the same text: the same proposal (90856 5b44ae992
        // receipt: by seat and text only, a seat's same text for another card came back as
        // the first card's proposal)
        let diff_sha = persona_diff::diff_sha(card.uid, card.sha, &new_sha);
        let mut l = self.list();
        Self::sweep(&mut l, now);
        let waiting = |p: &&Proposal| p.stand == Stand::Waiting;
        if let Some(p) = l
            .iter()
            .filter(waiting)
            .find(|p| p.seat_id == ask.seat_id && p.diff_sha == diff_sha)
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
            diff_sha,
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

    /// The owner said yes: a waiting proposal whose diff is still `diff_sha` goes to
    /// [`Stand::Writing`] (one write at a time per proposal; a seat can no longer withdraw it).
    pub fn begin_write(
        &self,
        id: &str,
        diff_sha: &str,
        now: u64,
    ) -> Result<Proposal, &'static str> {
        let mut l = self.list();
        Self::sweep(&mut l, now);
        let p = l
            .iter_mut()
            .find(|p| p.id == id)
            .ok_or("request_not_found")?;
        if p.stand != Stand::Waiting {
            return Err("request_not_waiting");
        }
        if p.diff_sha != diff_sha {
            return Err("request_changed");
        }
        p.stand = Stand::Writing;
        Ok(p.clone())
    }

    /// The write came back: [`Stand::Written`] or [`Stand::Failed`]; only from Writing.
    pub fn end_write(&self, id: &str, written: bool) -> Result<Proposal, &'static str> {
        let mut l = self.list();
        let p = l
            .iter_mut()
            .find(|p| p.id == id)
            .ok_or("request_not_found")?;
        if p.stand != Stand::Writing {
            return Err("request_not_writing");
        }
        p.stand = if written {
            Stand::Written
        } else {
            Stand::Failed
        };
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
        // the page sends it back with APPROVE: the yes is for the text the owner saw
        "diff_sha": p.diff_sha,
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
        | "request_not_writing"
        | "request_changed"
        | "persona_changed"
        | "persona_unreadable"
        | "persona_none"
        | "persona_archived"
        | "persona_aliased"
        | "persona_registry_edited"
        | "persona_registry_changed"
        | "persona_write_mismatch" => StatusCode::CONFLICT,
        "persona_path_outside" | "persona_path_symlink" => StatusCode::CONFLICT,
        "persona_built_in_page_only" | "item_off" => StatusCode::FORBIDDEN,
        // the card could not be written on this machine: not the page's request (f163c N1)
        "internal" | "persona_write_io" => StatusCode::INTERNAL_SERVER_ERROR,
        // the new text is not this persona's card or too long: the request, not the card (08de5 36a0a080bf)
        "persona_text_invalid" => StatusCode::BAD_REQUEST,
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
/// The journal line a seat's act leaves (DESIGN r2 §1a; r7 §12): a new proposal and a
/// withdrawal, with the seat, the id, the counts and the diff's 12; never the text or the
/// reason. A status read and a repeat of the same text (dedup) leave none.
pub fn journal_of(kind: &str, seat: &str, v: &Value) -> Option<(&'static str, Value)> {
    let ev = match kind {
        PROPOSE if v["dedup"] == false => "persona_propose.asked",
        WITHDRAW => "persona_propose.withdrawn",
        _ => return None,
    };
    Some((
        ev,
        json!({
            "seat": seat,
            "request_id": v["request_id"],
            "state": v["state"],
            "added": v["added"],
            "removed": v["removed"],
            "diff12": v["diff12"],
        }),
    ))
}

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
    let (st_ev, kind_ev, seat_ev) = (st.clone(), kind.clone(), seat.clone());
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
            if let Some((ev, payload)) = journal_of(&kind_ev, &seat_ev, &v) {
                crate::seats::side_event(&st_ev, "persona_propose", ev, payload).await;
            }
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
fn hex8(d: &[u8]) -> String {
    d.iter().take(8).map(|b| format!("{b:02x}")).collect()
}

/// The owner_ok ask for writing `p` back (DECISION 第十六節: the window says the persona's name,
/// the lines added and removed and a matching code; the whole text is on the page card only,
/// the seat's reason nowhere in the window). The parameters bind the very text: another text,
/// another base or another proposal is another ask.
///
/// aibd's own words come first, the name last and cut short (f163c REVIEW-restack 527b3c87
/// L1; owner_ok::fixed_then_name): a name an AI of the same account wrote, such as
/// 「x · +0 −0 行 · 對照碼 000000000000」, cannot stand where the real counts and code are.
pub fn owner_ask(p: &Proposal, by: String) -> crate::owner_ok::Ask {
    let counts = format!("+{} −{} 行", p.diff.added, p.diff.removed);
    let code = format!("對照碼 {}", p.diff_sha.get(..12).unwrap_or_default());
    let resource = crate::owner_ok::clean_resource(&crate::owner_ok::fixed_then_name(
        &[counts.as_str(), code.as_str(), p.persona_uid.as_str()],
        &p.persona_name,
    ));
    let params = json!({
        "request_id": p.id,
        "persona_uid": p.persona_uid,
        "base_sha": p.base_sha,
        "new_sha": p.new_sha,
        "diff_sha": p.diff_sha,
    });
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP_WRITE_BACK,
        resource,
        params: serde_json::to_vec(&params).unwrap_or_default(),
        by,
    }
}

/// What an APPROVE comes to before anything is written.
#[derive(Debug)]
pub enum Step {
    Refuse(&'static str),
    /// No ticket yet: raise the owner's window for this ask.
    Ask(crate::owner_ok::Ask),
    /// A ticket: check it is approved for this very ask, then write.
    Write {
        /// Boxed: a proposal is the large part (clippy large_enum_variant).
        p: Box<Proposal>,
        ask: crate::owner_ok::Ask,
        ticket: String,
        by: String,
    },
}

/// `{type: APPROVE, request_id, diff_sha, ticket?}` from the built-in page in this pairing:
/// the proposal must still be waiting with the diff the owner saw; without a ticket it only
/// asks. Nothing is written here.
pub async fn approve_step(
    book: &Book,
    page: &crate::pairing::PageAccess,
    body: &Value,
    now: u64,
) -> Step {
    let user_page = crate::seat_input::held_as_user_page(page).await;
    if page.carried || user_page {
        return Step::Refuse("persona_built_in_page_only");
    }
    let (Some(id), Some(diff_sha)) = (
        body.get("request_id").and_then(Value::as_str),
        body.get("diff_sha").and_then(Value::as_str),
    ) else {
        return Step::Refuse("invalid_request");
    };
    let Some(p) = book.get(None, id, now) else {
        return Step::Refuse("request_not_found");
    };
    if !matches!(p.stand, Stand::Waiting) {
        return Step::Refuse("request_not_waiting");
    }
    if diff_sha != p.diff_sha {
        return Step::Refuse("request_changed");
    }
    let by = format!("page:{}", hex8(&page.digest));
    let ask = owner_ask(&p, by.clone());
    match body.get("ticket").and_then(Value::as_str) {
        None => Step::Ask(ask),
        Some(t) => Step::Write {
            p: Box::new(p),
            ask,
            ticket: t.to_owned(),
            by,
        },
    }
}

/// [`approve_step`], then: the ticket approved for this very ask (used up), the proposal moved
/// to Writing, [`crate::persona_write::write`] against the base the proposal was made on (a
/// card changed since is `persona_changed` there), Written or Failed, one journal line.
pub async fn approve(st: &AppState, page: &crate::pairing::PageAccess, body: &Value) -> Response {
    let (p, ask, ticket, by) =
        match approve_step(&BOOK, page, body, crate::owner_ok::unix_now()).await {
            Step::Refuse(code) => return refuse(code),
            Step::Ask(ask) => return crate::repos::asked(&ask),
            Step::Write { p, ask, ticket, by } => (p, ask, ticket, by),
        };
    if !crate::owner_ok::approved_for(&ticket, &ask) {
        return crate::repos::not_approved();
    }
    let p = match BOOK.begin_write(&p.id, &p.diff_sha, crate::owner_ok::unix_now()) {
        Ok(p) => p,
        Err(code) => return refuse(code),
    };
    let r = crate::persona_write::write(
        st,
        &p.persona_uid,
        &p.base_sha,
        p.new_text.clone().into_bytes(),
        &by,
    )
    .await;
    let _ = BOOK.end_write(&p.id, r.is_ok());
    let mut line = json!({"request_id": p.id, "seat": p.seat_id, "persona_uid": p.persona_uid,
        "base12": p.base_sha.get(..12).unwrap_or_default(),
        "diff12": p.diff_sha.get(..12).unwrap_or_default(), "by": by});
    match r {
        Ok(w) => {
            line["new12"] = json!(w.new_sha.get(..12).unwrap_or_default());
            // the card is written; the 1.x card list could not follow and is tried again (08de5)
            line["list_behind"] = json!(w.list_behind);
            crate::seats::side_event(st, "persona_propose", "persona_propose.written", line).await;
            Json(
                json!({"request_id": p.id, "state": Stand::Written.word(), "new_sha": w.new_sha,
                "list_behind": w.list_behind}),
            )
            .into_response()
        }
        Err(e) => {
            line["code"] = json!(e.code());
            crate::seats::side_event(st, "persona_propose", "persona_propose.write_failed", line)
                .await;
            refuse(e.code())
        }
    }
}

/// The built-in page's list, no and approve; a no leaves `persona_propose.denied` in the journal.
pub async fn page_act(st: &AppState, page: &crate::pairing::PageAccess, body: Value) -> Response {
    if body.get("type").and_then(Value::as_str) == Some(APPROVE) {
        return approve(st, page, &body).await;
    }
    let (r, denied) = page_answer(page, body).await;
    for d in denied {
        crate::seats::side_event(st, "persona_propose", "persona_propose.denied", d).await;
    }
    r
}

/// [`page_act`] without the journal: the answer, and one line for each proposal said no to.
pub async fn page_answer(page: &crate::pairing::PageAccess, body: Value) -> (Response, Vec<Value>) {
    if page.carried || crate::seat_input::held_as_user_page(page).await {
        return (refuse("persona_built_in_page_only"), Vec::new());
    }
    let now = crate::owner_ok::unix_now();
    match body.get("type").and_then(Value::as_str) {
        Some(LIST) => {
            let l: Vec<Value> = BOOK.waiting(now).iter().map(page_view).collect();
            (Json(json!({ "proposals": l })).into_response(), Vec::new())
        }
        Some(DENY) => {
            let Some(ids) = body["ids"]
                .as_array()
                .filter(|a| (1..=20).contains(&a.len()))
            else {
                return (refuse("invalid_request"), Vec::new());
            };
            let mut denied = Vec::new();
            let out: Vec<Value> = ids
                .iter()
                .map(|i| {
                    let id = i.as_str().unwrap_or_default();
                    match BOOK.deny(id, now) {
                        Ok(p) => {
                            denied.push(json!({"request_id": id, "seat": p.seat_id,
                                "diff12": p.diff_sha.get(..12).unwrap_or_default()}));
                            json!({"request_id": id, "state": p.stand.word()})
                        }
                        Err(code) => json!({"request_id": id, "code": code}),
                    }
                })
                .collect();
            let by = format!("page:{}", hex8(&page.digest));
            for d in &mut denied {
                d["by"] = json!(by);
            }
            (Json(json!({ "results": out })).into_response(), denied)
        }
        _ => (refuse("invalid_request"), Vec::new()),
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

    /// r7 §12: a new proposal and a withdrawal are journaled with counts and the diff's 12,
    /// never the text or the reason; a status read and a repeat leave nothing.
    #[test]
    fn asking_and_withdrawing_are_journaled_without_the_text() {
        let v = json!({"request_id": "pp_1", "state": "waiting", "added": 2, "removed": 1,
            "diff12": "a1b2c3d4e5f6", "dedup": false, "card_text": "x", "why": "y"});
        let (ev, p) = journal_of(PROPOSE, "seat_ab", &v).unwrap();
        assert_eq!(ev, "persona_propose.asked");
        assert_eq!(p["seat"], "seat_ab");
        assert_eq!(p["diff12"], "a1b2c3d4e5f6");
        assert!(p.get("card_text").is_none() && p.get("why").is_none());
        let mut again = v.clone();
        again["dedup"] = json!(true);
        assert!(journal_of(PROPOSE, "seat_ab", &again).is_none());
        assert!(journal_of(STATUS, "seat_ab", &v).is_none());
        let (ev, _) = journal_of(WITHDRAW, "seat_ab", &v).unwrap();
        assert_eq!(ev, "persona_propose.withdrawn");
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
        // r4 §9 S5: any other front matter field too
        let moved = OLD.replace("code: owner-sec\n", "code: owner-sec\ncategory: 研究\n");
        assert_eq!(
            b.propose(&card(), &ask("seat_ab", &moved), &mut id, 1)
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
        assert_eq!(
            front_block(OLD),
            Some("---\nuid: lvkson\ncode: owner-sec\n---\n")
        );
        assert_eq!(front_block("no front\n---\n"), None);
        assert_eq!(front_block("---\nuid: x\n"), None);
    }

    #[test]
    fn a_seat_act_takes_only_its_keys() {
        assert!(keys_of(PROPOSE).contains(&"card_text"));
        assert!(!keys_of(PROPOSE).contains(&"persona_uid"));
        assert!(!keys_of(STATUS).contains(&"seat_id"));
    }

    fn waiting_one(b: &Book) -> Proposal {
        let t = new_text("規則三");
        b.propose(&card(), &ask("seat_ab", &t), ids(), 100)
            .unwrap()
            .0
    }

    fn built_in() -> crate::pairing::PageAccess {
        crate::pairing::PageAccess {
            digest: [7; 32],
            confirmed: true,
            user_page: false,
            carried: false,
        }
    }

    /// PA1: without a ticket an approve only asks; with one it goes on to the check and write.
    #[tokio::test]
    async fn approve_without_a_ticket_writes_nothing() {
        let b = Book::default();
        let p = waiting_one(&b);
        let body = json!({"type": APPROVE, "request_id": p.id, "diff_sha": p.diff_sha});
        assert!(matches!(
            approve_step(&b, &built_in(), &body, 101).await,
            Step::Ask(_)
        ));
        let mut body = body;
        body["ticket"] = json!("ot_1");
        assert!(matches!(
            approve_step(&b, &built_in(), &body, 101).await,
            Step::Write { ref ticket, .. } if ticket == "ot_1"
        ));
        assert_eq!(b.get(None, &p.id, 101).unwrap().stand, Stand::Waiting);
    }

    /// PA2: the ask binds the very text — another diff, base or new text is another ask.
    #[test]
    fn a_ticket_for_another_text_writes_nothing() {
        let b = Book::default();
        let p = waiting_one(&b);
        let a0 = owner_ask(&p, "page:1".into());
        let changes: [fn(&mut Proposal); 4] = [
            |q: &mut Proposal| q.diff_sha = "0".repeat(64),
            |q: &mut Proposal| q.base_sha = "1".repeat(64),
            |q: &mut Proposal| q.new_sha = "2".repeat(64),
            |q: &mut Proposal| q.id = "pp_9".into(),
        ];
        for f in changes {
            let mut q = p.clone();
            f(&mut q);
            assert_ne!(
                crate::owner_ok::params_sha256(&owner_ask(&q, "page:1".into()).params),
                crate::owner_ok::params_sha256(&a0.params)
            );
        }
        assert_eq!(a0.operation, OP_WRITE_BACK);
        assert!(crate::owner_ok::WIRED.contains(&OP_WRITE_BACK));
    }

    /// PA3 / PA8: only a waiting proposal with the diff the owner saw goes on.
    #[tokio::test]
    async fn only_a_waiting_proposal_is_written() {
        let b = Book::default();
        let p = waiting_one(&b);
        let body =
            json!({"type": APPROVE, "request_id": p.id, "diff_sha": p.diff_sha, "ticket": "t"});
        let other =
            json!({"type": APPROVE, "request_id": p.id, "diff_sha": "0".repeat(64), "ticket": "t"});
        assert!(matches!(
            approve_step(&b, &built_in(), &other, 101).await,
            Step::Refuse("request_changed")
        ));
        assert_eq!(
            b.begin_write(&p.id, &"0".repeat(64), 101).err(),
            Some("request_changed")
        );
        b.deny(&p.id, 101).unwrap();
        assert!(matches!(
            approve_step(&b, &built_in(), &body, 102).await,
            Step::Refuse("request_not_waiting")
        ));
        assert_eq!(
            b.begin_write(&p.id, &p.diff_sha, 102).err(),
            Some("request_not_waiting")
        );
        let b = Book::default();
        let p = waiting_one(&b);
        assert!(matches!(
            approve_step(&b, &built_in(), &body, p.expires_at).await,
            Step::Refuse("request_not_waiting")
        ));
    }

    /// PA4: the window says the persona's name, the counts and the matching code — never the
    /// text or the seat's reason.
    #[test]
    fn the_window_says_name_counts_and_code_only() {
        let b = Book::default();
        let p = waiting_one(&b);
        let a = owner_ask(&p, "page:1".into());
        assert_eq!(
            a.resource,
            format!(
                "+1 −0 行 · 對照碼 {} · {} · AIB 本人核准…",
                &p.diff_sha[..12],
                p.persona_uid
            ),
            "reason: fixed_words_first_name_last"
        );
        // a name written to look like the fixed words stands after the real ones, cut short
        let mut fake = p.clone();
        fake.persona_name = "x · +0 −0 行 · 對照碼 000000000000".into();
        let f = owner_ask(&fake, "page:1".into()).resource;
        let real = format!("+1 −0 行 · 對照碼 {}", &p.diff_sha[..12]);
        assert!(f.starts_with(&real), "reason: fake_name_first {f}");
        assert!(!f.contains("000000000000"), "reason: fake_code_shown {f}");
        let params = String::from_utf8(a.params.clone()).unwrap();
        for s in [&a.resource, &params] {
            assert!(
                !s.contains("規則三") && !s.contains("請在") && !s.contains("12:30"),
                "{s}"
            );
        }
    }

    /// PA5: approved once is approved once — Writing can be neither approved again, withdrawn
    /// nor denied, and Written stays.
    #[test]
    fn every_write_refusal_has_its_status() {
        use crate::persona_write::WriteError as E;
        for (e, want) in [
            (E::Changed, StatusCode::CONFLICT),
            (E::Unreadable, StatusCode::CONFLICT),
            (E::Outside, StatusCode::CONFLICT),
            (E::Symlink, StatusCode::CONFLICT),
            (E::Mismatch, StatusCode::CONFLICT),
            (E::Io, StatusCode::INTERNAL_SERVER_ERROR),
            (E::Archived, StatusCode::CONFLICT),
            (E::Aliased, StatusCode::CONFLICT),
            (E::RegistryEdited, StatusCode::CONFLICT),
            (E::RegistryChanged, StatusCode::CONFLICT),
            (E::TextInvalid, StatusCode::BAD_REQUEST),
        ] {
            assert_eq!(refuse(e.code()).status(), want, "{}", e.code());
        }
    }

    #[test]
    fn a_written_proposal_cannot_be_written_again() {
        let b = Book::default();
        let p = waiting_one(&b);
        assert_eq!(
            b.begin_write(&p.id, &p.diff_sha, 101).unwrap().stand,
            Stand::Writing
        );
        assert_eq!(
            b.begin_write(&p.id, &p.diff_sha, 101).err(),
            Some("request_not_waiting")
        );
        assert!(b.withdraw("seat_ab", &p.id, 101).is_err());
        assert!(b.deny(&p.id, 101).is_err());
        // not swept while writing
        assert_eq!(
            b.get(None, &p.id, p.expires_at + 1).unwrap().stand,
            Stand::Writing
        );
        assert_eq!(b.end_write(&p.id, true).unwrap().stand, Stand::Written);
        assert_eq!(b.end_write(&p.id, true).err(), Some("request_not_writing"));
        assert_eq!(
            b.begin_write(&p.id, &p.diff_sha, 102).err(),
            Some("request_not_waiting")
        );
        assert!(b.waiting(102).is_empty());
    }

    /// PA6: approve is the built-in page's in this pairing only.
    #[tokio::test]
    async fn approve_is_the_built_in_pages_only() {
        let b = Book::default();
        let p = waiting_one(&b);
        let body = json!({"type": APPROVE, "request_id": p.id, "diff_sha": p.diff_sha});
        for (carried, user_page) in [(true, false), (false, true)] {
            let page = crate::pairing::PageAccess {
                carried,
                user_page,
                ..built_in()
            };
            assert!(matches!(
                approve_step(&b, &page, &body, 101).await,
                Step::Refuse("persona_built_in_page_only")
            ));
        }
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
                let (r, denied) = page_answer(&page, json!({"type": kind, "ids": ["pp_1"]})).await;
                assert!(denied.is_empty());
                assert_eq!(r.status(), StatusCode::FORBIDDEN, "reason: {why} {kind}");
            }
        }
    }
}
