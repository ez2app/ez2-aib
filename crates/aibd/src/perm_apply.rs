//! aibd §7 7b (DESIGN-brainnote-08de5-1123 §2d / §7; SPEC-ai-partner-open-ebab8-1123-r2 §3.7 /
//! §6; ebab8 01:47 / 02:52 / 02:58; f163c REVIEW-permtpl r1 / r2): a permission template opens a
//! seat, and is applied to an open seat.
//!
//! - aibd expands the template itself from its table (the page's names or items are never
//!   trusted): its base becomes the seat's mode and its items what the seat holds.
//! - Only less, never more (ebab8 01:47): an item on that the base does not give is not given
//!   and named in `not_enforceable` (「這一項這一版還沒有,這一位實際沒拿到」); an item off that
//!   the base gives and aibd cannot take away would leave the seat holding more than the
//!   template, so that template is refused (`template_more_than_given`), never opened.
//! - Fingerprint at open (SPEC §6.5, §3.1): unless the base is safe or work and the items are
//!   exactly that mode's own; a dgrs base always asks. The window lists the expanded items;
//!   the ticket is bound to (tpl_uid, rev, items).
//! - Applied to an open seat (SPEC §3.7, §6.6): the same base only (another base is another
//!   seat); a lower takes effect when aibd restarts the seat, which it does now; a raise asks
//!   the fingerprint and waits for the seat's next restart. Until a restart the seat runs with
//!   what it had: the page shows 「重開後生效」, never 「已調低」.
//! - Every change is logged (aib_store::seat_perm); a log row can be taken back (收回), which
//!   applies the row's `from` state by the same rules.

use crate::api::AppState;
use crate::perm_tpl::{
    gives_now, needs_owner_change, not_enforceable, turned_on, Items, DROPPABLE_NOW, ITEMS,
};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};

pub const APPLY: &str = "perm_template_apply";
pub const SEAT_LOG: &str = "seat_perm_log";
pub const REVOKE: &str = "seat_perm_revoke";

/// A seat opened from a template whose items are more than its mode's own.
pub const OP_OPEN: &str = "seat.open_template";
/// A template applied to an open seat, or a change taken back, that turns an item on.
pub const OP_APPLY: &str = "seat.apply_template";

/// The words shown beside each item a seat does not really get (ebab8 01:47).
pub const NOT_GIVEN_WORDS: &str = "這一項這一版還沒有,這一位實際沒拿到";

/// A template as aibd expanded it for one seat.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Expanded {
    pub tpl_uid: String,
    pub rev: i64,
    pub name: String,
    pub base: String,
    pub items: Items,
}

impl Expanded {
    /// From a kept template (`None`: its items are not a full set).
    pub fn of(t: &aib_store::PermTemplate) -> Option<Expanded> {
        let items = Items::parse(&serde_json::from_str::<Value>(&t.items_json).ok()?)?;
        Some(Expanded {
            tpl_uid: t.tpl_uid.clone(),
            rev: t.rev,
            name: t.name.clone(),
            base: t.base.clone(),
            items,
        })
    }

    pub fn state(&self) -> aib_store::PermState {
        aib_store::PermState {
            tpl_uid: self.tpl_uid.clone(),
            tpl_rev: self.rev,
            base: self.base.clone(),
            items_json: self.items.json(),
        }
    }

    /// The SHA-256 of the normal form the fingerprint is bound to.
    pub fn items_sha(&self) -> String {
        crate::preview::sha256_hex(
            format!(
                "{}\n{}\n{}\n{}",
                self.tpl_uid,
                self.rev,
                self.base,
                self.items.json()
            )
            .as_bytes(),
        )
    }
}

/// The items `base` gives that this template turns off and aibd cannot take away: a seat would
/// hold more than the template says, so the template is refused (only less, never more).
pub fn more_than_given(base: &str, items: &Items) -> Vec<&'static str> {
    gives_now(base)
        .iter()
        .copied()
        .filter(|k| !items.on(k) && !DROPPABLE_NOW.contains(k))
        .collect()
}

/// The items on that `base` does not give today (shown, never given).
pub fn not_given(base: &str, items: &Items) -> Vec<String> {
    let more = more_than_given(base, items);
    not_enforceable(base, items)
        .into_iter()
        .filter(|k| !more.contains(&k.as_str()))
        .collect()
}

/// `not_enforceable` as the page reads it: each item, the platform, the words.
pub fn not_given_json(base: &str, items: &Items) -> Value {
    Value::Array(
        not_given(base, items)
            .into_iter()
            .map(|item| json!({"item": item, "platform": std::env::consts::OS, "words": NOT_GIVEN_WORDS}))
            .collect(),
    )
}

/// The page's tools a seat with these items does not get (the knife: an item not held is not
/// in the seat's tool list).
pub fn tools_dropped(items: &Items) -> Vec<&'static str> {
    if items.on("brain") {
        Vec::new()
    } else {
        vec![crate::seats::BRAIN_TOOL]
    }
}

/// Take `drop` out of `--allowedTools` in `argv` (nothing else changes).
pub fn drop_tools(argv: &mut [String], drop: &[&str]) {
    if drop.is_empty() {
        return;
    }
    let Some(at) = argv.iter().position(|a| a == "--allowedTools") else {
        return;
    };
    if let Some(list) = argv.get_mut(at + 1) {
        let kept: Vec<&str> = list.split(',').filter(|t| !drop.contains(t)).collect();
        *list = kept.join(",");
    }
}

/// Whether opening a seat from this template asks the owner's fingerprint (SPEC §6.5, §3.1):
/// every time, unless the base is safe or work and the items are exactly that mode's own.
pub fn open_needs_owner(base: &str, items: &Items) -> bool {
    !(matches!(base, "safe" | "work") && *items == Items::from_keys(gives_now(base)))
}

/// The items a mode gives a seat opened without a template (f163c REVIEW-permtpl r2 N1).
pub fn mode_items(mode: &str) -> Items {
    Items::from_keys(gives_now(mode))
}

/// The window's line for an open or an apply: the template, its base, every item it holds,
/// what this turns on, and what is not given (owner_ok keeps the end of a long line, so what
/// this turns on is last).
pub fn window_line(e: &Expanded, raised: &[&str]) -> String {
    let word = |k: &str| ITEMS.iter().find(|(x, _)| *x == k).map_or("?", |(_, w)| *w);
    let held: Vec<&str> = e.items.keys_on().into_iter().map(word).collect();
    let missing: Vec<&str> = not_given(&e.base, &e.items)
        .iter()
        .map(|k| word(k))
        .collect();
    let mut s = format!("用權限模板「{}」底 {}:{}", e.name, e.base, held.join("、"));
    if !missing.is_empty() {
        s.push_str(&format!(";這一版還沒有 {}", missing.join("、")));
    }
    if !raised.is_empty() {
        let r: Vec<&str> = raised.iter().copied().map(word).collect();
        s.push_str(&format!(";這次多開 {}", r.join("、")));
    }
    s
}

/// What applying `new` to a seat holding `old` is.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Change {
    Same,
    /// Takes effect when aibd restarts the seat (it does so now).
    Lower,
    /// Asks the fingerprint, then takes effect at the seat's next restart.
    Raise,
}

pub fn change_of(old_base: &str, old: &Items, new_base: &str, new: &Items) -> Change {
    if old_base == new_base && old == new {
        Change::Same
    } else if needs_owner_change(old_base, old, new_base, new) {
        Change::Raise
    } else {
        Change::Lower
    }
}

pub(crate) fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({"error": {"code": code, "message": message}})),
    )
        .into_response()
}

/// The owner's ask for opening or applying `e` (`seat`: the seat applied to, `None` at open).
pub fn ask(e: &Expanded, seat: Option<&str>, extra: Value, by: String) -> crate::owner_ok::Ask {
    let raised: Vec<&str> = match seat {
        None => e
            .items
            .keys_on()
            .into_iter()
            .filter(|k| !gives_now(&e.base).contains(k))
            .collect(),
        Some(_) => extra
            .get("raised")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .filter_map(|s| ITEMS.iter().find(|(k, _)| *k == s).map(|(k, _)| *k))
                    .collect()
            })
            .unwrap_or_default(),
    };
    let resource = window_line(e, &raised);
    let params = serde_json::to_vec(&json!({
        "tpl_uid": e.tpl_uid, "rev": e.rev, "items_sha": e.items_sha(), "seat": seat, "extra": extra,
    }))
    .unwrap_or_default();
    // each operation written out, so owner_ok's scan of `operation:` lines sees both
    match seat {
        None => crate::owner_ok::Ask {
            category: aib_approve::category::Category::Command,
            operation: OP_OPEN,
            resource,
            params,
            by,
        },
        Some(_) => crate::owner_ok::Ask {
            category: aib_approve::category::Category::Command,
            operation: OP_APPLY,
            resource,
            params,
            by,
        },
    }
}

/// The template `tpl_uid` at revision `rev`, expanded; `Err` is the response.
pub async fn template_at(
    st: &AppState,
    tpl_uid: &str,
    rev: i64,
) -> Result<Expanded, Box<Response>> {
    if !aib_store::ranks::key_ok(tpl_uid) {
        return Err(Box::new(refuse(
            StatusCode::BAD_REQUEST,
            "template",
            "template must be a tpl_uid perm_template_list lists",
        )));
    }
    let (db, uid) = (st.messages.clone(), tpl_uid.to_owned());
    let t = match tokio::task::spawn_blocking(move || db.perm_template(&uid)).await {
        Ok(Ok(Some(t))) => t,
        Ok(Ok(None)) => {
            return Err(Box::new(refuse(
                StatusCode::NOT_FOUND,
                "template",
                "no such template (removed?)",
            )))
        }
        _ => {
            return Err(Box::new(refuse(
                StatusCode::INTERNAL_SERVER_ERROR,
                "internal",
                "templates unreadable",
            )))
        }
    };
    if t.rev != rev {
        return Err(Box::new(
            (
                StatusCode::CONFLICT,
                Json(json!({"error": {"code": "template_changed",
                    "message": "這個模板剛被改過,請重新整理再選一次", "rev": t.rev}})),
            )
                .into_response(),
        ));
    }
    let Some(e) = Expanded::of(&t) else {
        return Err(Box::new(refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "template items unreadable",
        )));
    };
    let more = more_than_given(&e.base, &e.items);
    if !more.is_empty() {
        return Err(Box::new(
            (
                StatusCode::CONFLICT,
                Json(json!({"error": {"code": "template_more_than_given",
                    "message": "這個模板關掉的項目,這一版關不掉;用它開會比模板寫的多,所以不開",
                    "items": more}})),
            )
                .into_response(),
        ));
    }
    Ok(e)
}

/// At a seat's restart ([`crate::revive`]): a change waiting for it becomes what it runs with;
/// then the tools its items do not hold. A seat with no row keeps its mode's own.
pub async fn at_restart(st: &AppState, seat_id: &str) -> Vec<&'static str> {
    let (db, id) = (st.messages.clone(), seat_id.to_owned());
    let read = tokio::task::spawn_blocking(move || {
        let took = db
            .seat_perm_take_pending(&id, "aibd:restart", &crate::sessions::now_rfc3339())
            .is_ok();
        (took, db.seat_perm(&id).map(|r| r.map(|p| p.now.items_json)))
    })
    .await;
    let (took, row) = match read {
        Ok((took, Ok(row))) => (took, Ok(row)),
        Ok((took, Err(_))) => (took, Err(())),
        Err(_) => (false, Err(())),
    };
    if !took {
        // f163c REVIEW-papply M1: the change waiting for this restart was not written; say so
        tracing::warn!(
            seat_id,
            "seat_perm pending not applied at restart: brain held back"
        );
        let ev = crate::sessions::new_id("ev_")
            .ok()
            .map(|id| aib_store::NewEvent {
                event_id: id.clone(),
                aggregate_type: "seat_perm".into(),
                aggregate_id: id,
                sequence: 1,
                event_type: "seat.perm.restart_unapplied".into(),
                correlation_id: None,
                occurred_at: crate::sessions::now_rfc3339(),
                payload: json!({"seat_id": seat_id, "held_back": [crate::seats::BRAIN_TOOL]}),
            });
        let store = st.store.clone();
        let _ = tokio::task::spawn_blocking(move || ev.map(|ev| store.append(vec![ev]))).await;
    }
    restart_drop(took, row)
}

/// [`at_restart`] on what was read: `written` = the waiting change was written (or there was none).
/// Not written, or the row unread: hold less, never more (f163c REVIEW-papply M1: a lowering
/// waiting for this restart must not be skipped by running on the old items).
pub fn restart_drop(written: bool, row: Result<Option<String>, ()>) -> Vec<&'static str> {
    if !written {
        return vec![crate::seats::BRAIN_TOOL];
    }
    match row {
        Ok(Some(items_json)) => serde_json::from_str::<Value>(&items_json)
            .ok()
            .and_then(|v| Items::parse(&v))
            .map_or_else(|| vec![crate::seats::BRAIN_TOOL], |i| tools_dropped(&i)),
        Ok(None) => Vec::new(),
        Err(()) => vec![crate::seats::BRAIN_TOOL],
    }
}

/// Whether a seat holds `item` now (a seat with no row: its mode's own; unread: no).
pub async fn holds(st: &AppState, seat_id: &str, mode: &str, item: &str) -> bool {
    let (db, id) = (st.messages.clone(), seat_id.to_owned());
    let row = match tokio::task::spawn_blocking(move || db.seat_perm(&id)).await {
        Ok(Ok(p)) => Ok(p.map(|p| (p.now.items_json, p.pending.map(|w| w.items_json)))),
        _ => Err(()),
    };
    held_in(row, mode, item)
}

/// [`holds`] on what was read: the seat's row's items now and the change waiting for its
/// restart (none: its mode's own; unread: no). Held only when both hold it: a lowering waiting
/// takes the item now, a raise waiting gives nothing before the restart (f163c REVIEW-papply
/// L6; and a restart that could not write the change keeps the lowering in force).
/// f163c bnote L1 (bound to 7b): a template with 第二大腦 off keeps brain_note from writing.
pub fn held_in(row: Result<Option<(String, Option<String>)>, ()>, mode: &str, item: &str) -> bool {
    let on = |items_json: &str| {
        serde_json::from_str::<Value>(items_json)
            .ok()
            .and_then(|v| Items::parse(&v))
            .is_some_and(|i| i.on(item))
    };
    match row {
        Ok(Some((now, waiting))) => {
            let waiting_holds = match waiting {
                None => true,
                Some(w) => on(&w),
            };
            on(&now) && waiting_holds
        }
        Ok(None) => gives_now(mode).contains(&item),
        Err(()) => false,
    }
}

fn fields(body: &Value) -> Option<(String, String, i64, Option<String>)> {
    let o = body.as_object()?;
    if !o.keys().all(|k| {
        matches!(
            k.as_str(),
            "type" | "seat_id" | "tpl_uid" | "rev" | "ticket"
        )
    }) {
        return None;
    }
    let seat = o
        .get("seat_id")?
        .as_str()
        .filter(|s| aib_store::ranks::key_ok(s))?
        .to_owned();
    let tpl = o.get("tpl_uid")?.as_str()?.to_owned();
    let rev = o.get("rev")?.as_i64()?;
    let ticket = o.get("ticket").and_then(Value::as_str).map(str::to_owned);
    Some((seat, tpl, rev, ticket))
}

/// The seat's mode and what it holds now (a seat with no row gets one: its mode's own, so a
/// change has a `from`).
/// The seat's mode, the items it runs with now, and the change waiting for its restart.
async fn now_of(
    st: &AppState,
    seat_id: &str,
) -> Result<(String, Items, Option<Items>), Box<Response>> {
    let (store, id) = (st.store.clone(), seat_id.to_owned());
    let mode = match tokio::task::spawn_blocking(move || store.seat_metas()).await {
        Ok(Ok(m)) => m.into_iter().find(|x| x.seat_id == id).and_then(|x| x.mode),
        _ => {
            return Err(Box::new(refuse(
                StatusCode::INTERNAL_SERVER_ERROR,
                "internal",
                "seat unreadable",
            )))
        }
    };
    let Some(mode) = mode else {
        return Err(Box::new(refuse(
            StatusCode::NOT_FOUND,
            "seat_not_found",
            "no such seat",
        )));
    };
    let (db, id, m) = (st.messages.clone(), seat_id.to_owned(), mode.clone());
    let row = tokio::task::spawn_blocking(move || {
        if db.seat_perm(&id)?.is_none() {
            let s = aib_store::PermState {
                tpl_uid: format!("mode.{m}"),
                tpl_rev: 1,
                base: m.clone(),
                items_json: mode_items(&m).json(),
            };
            db.seat_perm_open(&id, &s, "aibd:mode", &crate::sessions::now_rfc3339())?;
        }
        db.seat_perm(&id)
    })
    .await;
    let Ok(Ok(Some(p))) = row else {
        return Err(Box::new(refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "seat permissions unreadable",
        )));
    };
    let parse = |j: &str| {
        serde_json::from_str::<Value>(j)
            .ok()
            .and_then(|v| Items::parse(&v))
    };
    let unreadable = || {
        Box::new(refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "seat permissions unreadable",
        ))
    };
    let now = parse(&p.now.items_json).ok_or_else(unreadable)?;
    let pending = match p.pending {
        None => None,
        Some(w) => Some(parse(&w.items_json).ok_or_else(unreadable)?),
    };
    Ok((mode, now, pending))
}

/// An apply of `new` to a seat of `mode` running `now`, with `pending` waiting for its restart.
/// Measured against what it would start with next (`pending`, or `now`); and a raise over
/// what it runs `now` is a raise even under a waiting raise it stays within: the owner
/// approved that template, not this one (f163c REVIEW-papply L5).
pub fn change_with_pending(
    mode: &str,
    now: &Items,
    pending: Option<&Items>,
    new: &Items,
) -> Change {
    let next = change_of(mode, pending.unwrap_or(now), mode, new);
    if next != Change::Same && change_of(mode, now, mode, new) == Change::Raise {
        Change::Raise
    } else {
        next
    }
}

/// Apply `e` to `seat_id` (an apply, or a take-back: `action` `revoke`).
async fn apply_to(
    st: &AppState,
    seat_id: &str,
    e: &Expanded,
    ticket: Option<String>,
    by: &str,
    revoking: Option<i64>,
) -> Response {
    let (mode, now, pending) = match now_of(st, seat_id).await {
        Ok(x) => x,
        Err(r) => return *r,
    };
    if e.base != mode {
        return refuse(
            StatusCode::CONFLICT,
            "template_base_differs",
            "這個模板的底模式跟這一位不同;換底模式請開一位新的",
        );
    }
    let change = change_with_pending(&mode, &now, pending.as_ref(), &e.items);
    if change == Change::Same {
        return Json(json!({"seat_id": seat_id, "unchanged": true,
            "not_enforceable": not_given_json(&e.base, &e.items)}))
        .into_response();
    }
    if change == Change::Raise {
        let raised = turned_on(&now, &e.items);
        let a = ask(
            e,
            Some(seat_id),
            json!({"raised": raised, "revoking": revoking}),
            by.to_owned(),
        );
        let Some(t) = ticket else {
            return crate::repos::asked(&a);
        };
        if !crate::owner_ok::approved_for(&t, &a) {
            return crate::repos::not_approved();
        }
    }
    let action = match (revoking, change) {
        (Some(_), _) => "revoke",
        (None, Change::Lower) => "lower",
        _ => "raise_pending",
    };
    let (db, id, s, act, who) = (
        st.messages.clone(),
        seat_id.to_owned(),
        e.state(),
        action.to_owned(),
        by.to_owned(),
    );
    let w = tokio::task::spawn_blocking(move || {
        let at = crate::sessions::now_rfc3339();
        let w = db.seat_perm_pend(&id, &s, &act, &who, &at)?;
        if let Some(log_id) = revoking {
            db.seat_perm_log_revoked(&id, log_id, &who, &at)?;
        }
        Ok::<_, aib_store::StoreError>(w)
    })
    .await;
    if !matches!(w, Ok(Ok(aib_store::PermWrite::Done(_)))) {
        return refuse(StatusCode::INTERNAL_SERVER_ERROR, "internal", "not written");
    }
    crate::seats::side_event(
        st,
        APPLY,
        "seat.perm.change",
        json!({"seat_id": seat_id, "tpl_uid": e.tpl_uid, "rev": e.rev, "action": action,
               "items": e.items.keys_on(), "by": by}),
    )
    .await;
    let ne = not_given_json(&e.base, &e.items);
    if change == Change::Raise {
        // a raise waits for the seat's next restart (the person restarts it when it suits)
        return Json(json!({"seat_id": seat_id, "pending_restart": true, "not_enforceable": ne}))
            .into_response();
    }
    // a lower: aibd restarts the seat now; until that is done the page says 重開後生效
    if st.sessions.in_turn(seat_id) {
        return Json(
            json!({"seat_id": seat_id, "pending_restart": true, "restart_after_turn": true,
            "not_enforceable": ne}),
        )
        .into_response();
    }
    let running: Vec<String> = st
        .sessions
        .list()
        .into_iter()
        .filter(|x| x.seat_id.as_deref() == Some(seat_id))
        .filter(|x| matches!(x.state, "running" | "pending"))
        .map(|x| x.id)
        .collect();
    for id in running {
        let s = st.sessions.clone();
        let stopped =
            tokio::task::spawn_blocking(move || s.stop(&id, crate::sessions::DEFAULT_GRACE)).await;
        if !matches!(stopped, Ok(Ok(ref rec)) if !rec.holds_slot) {
            return Json(
                json!({"seat_id": seat_id, "pending_restart": true, "stop_unconfirmed": true,
                "not_enforceable": ne}),
            )
            .into_response();
        }
    }
    match crate::revive::restart(st, seat_id, json!({"page": "built_in", "perm": action})).await {
        Ok(session_id) => Json(
            json!({"seat_id": seat_id, "restarting": true, "session_id": session_id,
            "not_enforceable": ne}),
        )
        .into_response(),
        Err(_) => Json(
            json!({"seat_id": seat_id, "pending_restart": true, "restart_failed": true,
            "not_enforceable": ne}),
        )
        .into_response(),
    }
}

/// `perm_template_apply {seat_id, tpl_uid, rev, ticket?}` from the built-in page.
pub async fn apply(st: AppState, body: Value, by: String) -> Response {
    let Some((seat, tpl, rev, ticket)) = fields(&body) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "body must be {type, seat_id, tpl_uid, rev, ticket?}",
        );
    };
    let e = match template_at(&st, &tpl, rev).await {
        Ok(e) => e,
        Err(r) => return *r,
    };
    apply_to(&st, &seat, &e, ticket, &by, None).await
}

/// `seat_perm_log {seat_id}`: what the seat was given and every change, newest first.
pub async fn seat_log(st: AppState, body: Value) -> Response {
    let Some(seat) = body
        .get("seat_id")
        .and_then(Value::as_str)
        .filter(|s| aib_store::ranks::key_ok(s))
        .map(str::to_owned)
    else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "seat_id is needed",
        );
    };
    let db = st.messages.clone();
    let got = tokio::task::spawn_blocking(move || {
        Ok::<_, aib_store::StoreError>((db.seat_perm(&seat)?, db.seat_perm_log(&seat)?))
    })
    .await;
    let Ok(Ok((row, log))) = got else {
        return refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "seat permissions unreadable",
        );
    };
    let js = |s: &Option<String>| {
        s.as_deref()
            .and_then(|x| serde_json::from_str::<Value>(x).ok())
    };
    Json(json!({
        "now": row.as_ref().map(|p| json!({"tpl_uid": p.now.tpl_uid, "rev": p.now.tpl_rev, "base": p.now.base,
            "items": serde_json::from_str::<Value>(&p.now.items_json).ok()})),
        "pending": row.as_ref().and_then(|p| p.pending.as_ref()).map(|s| json!({"tpl_uid": s.tpl_uid, "rev": s.tpl_rev,
            "base": s.base, "items": serde_json::from_str::<Value>(&s.items_json).ok(), "words": "重開後生效"})),
        "log": log.iter().map(|l| json!({"log_id": l.log_id, "action": l.action, "at": l.at, "by": l.by,
            "from": js(&l.from_json), "to": js(&l.to_json), "revoked_at": l.revoked_at})).collect::<Vec<_>>(),
    }))
    .into_response()
}

/// `seat_perm_revoke {seat_id, log_id, ticket?}` (收回): the row's `from` state applied by the
/// same rules (taking back a lower turns items on: fingerprint).
pub async fn revoke(st: AppState, body: Value, by: String) -> Response {
    let o = body.as_object();
    let ok = o.is_some_and(|o| {
        o.keys()
            .all(|k| matches!(k.as_str(), "type" | "seat_id" | "log_id" | "ticket"))
    });
    let seat = body
        .get("seat_id")
        .and_then(Value::as_str)
        .filter(|s| aib_store::ranks::key_ok(s))
        .map(str::to_owned);
    let log_id = body.get("log_id").and_then(Value::as_i64);
    let (true, Some(seat), Some(log_id)) = (ok, seat, log_id) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "body must be {type, seat_id, log_id, ticket?}",
        );
    };
    let ticket = body
        .get("ticket")
        .and_then(Value::as_str)
        .map(str::to_owned);
    let (db, s2) = (st.messages.clone(), seat.clone());
    let log = match tokio::task::spawn_blocking(move || db.seat_perm_log(&s2)).await {
        Ok(Ok(l)) => l,
        _ => {
            return refuse(
                StatusCode::INTERNAL_SERVER_ERROR,
                "internal",
                "seat permissions unreadable",
            )
        }
    };
    let Some(row) = log.into_iter().find(|l| l.log_id == log_id) else {
        return refuse(
            StatusCode::NOT_FOUND,
            "log_not_found",
            "no such change of this seat",
        );
    };
    if row.revoked_at.is_some() {
        return refuse(StatusCode::CONFLICT, "revoked_already", "這一筆已經收回了");
    }
    let from = row
        .from_json
        .as_deref()
        .and_then(|s| serde_json::from_str::<Value>(s).ok());
    let parsed = from.as_ref().and_then(|f| {
        Some(Expanded {
            tpl_uid: f.get("tpl_uid")?.as_str()?.to_owned(),
            rev: f.get("rev")?.as_i64()?,
            name: f.get("tpl_uid")?.as_str()?.to_owned(),
            base: f.get("base")?.as_str()?.to_owned(),
            items: Items::parse(f.get("items")?)?,
        })
    });
    let Some(e) = parsed else {
        return refuse(
            StatusCode::CONFLICT,
            "nothing_to_revoke",
            "這一筆是開的時候給的,沒有可以收回的上一步",
        );
    };
    if !more_than_given(&e.base, &e.items).is_empty() {
        return refuse(
            StatusCode::CONFLICT,
            "template_more_than_given",
            "收回後會比當時寫的多,所以不收回",
        );
    }
    apply_to(&st, &seat, &e, ticket, &by, Some(log_id)).await
}

#[cfg(test)]
mod tests {
    use super::*;

    fn items(keys: &[&str]) -> Items {
        Items::from_keys(keys)
    }

    /// 7B-1 (SPEC §6.5, §3.1; ebab8 02:52 / 02:58): an open is free only on a safe / work base
    /// with exactly that mode's items; more, fewer or a dgrs base asks.
    #[test]
    fn an_open_asks_unless_it_is_exactly_the_modes_own() {
        assert!(!open_needs_owner("work", &mode_items("work")));
        assert!(!open_needs_owner("safe", &mode_items("safe")));
        assert!(
            open_needs_owner("dgrs", &mode_items("dgrs")),
            "reason: dgrs_opened_free"
        );
        let mut w = mode_items("work").keys_on();
        w.push("push");
        assert!(
            open_needs_owner("work", &items(&w)),
            "reason: work_plus_push_free"
        );
        assert!(open_needs_owner(
            "work",
            &items(&["chat", "code", "own", "cmd", "brain", "any"])
        ));
        assert!(open_needs_owner("host", &items(&["chat"])));
    }

    /// 7B-2 (ebab8 01:47): only less, never more. An item off that the base gives and aibd
    /// cannot take away refuses the template; an item on that the base does not give is named,
    /// not given.
    #[test]
    fn a_seat_never_holds_more_than_its_template() {
        let no_code = items(&["chat", "own", "cmd", "brain"]);
        assert_eq!(more_than_given("work", &no_code), ["code"]);
        let no_brain = items(&["chat", "code", "own", "cmd"]);
        assert!(
            more_than_given("work", &no_brain).is_empty(),
            "reason: brain_not_droppable"
        );
        let net = items(&["chat", "code", "own", "cmd", "brain", "net"]);
        assert_eq!(not_given("work", &net), ["net"]);
        let ne = not_given_json("work", &net);
        assert_eq!(ne[0]["item"], "net");
        assert_eq!(ne[0]["words"], NOT_GIVEN_WORDS);
    }

    /// 7B-3 (ebab8 01:47 刀): an item not held is not in the seat's tool list.
    #[test]
    fn an_item_not_held_is_not_in_the_tool_list() {
        let mut a: Vec<String> = vec![
            "--mcp-config".into(),
            "c.json".into(),
            "--allowedTools".into(),
            format!(
                "mcp__aibd__talk,{},Bash(git add *)",
                crate::seats::BRAIN_TOOL
            ),
        ];
        drop_tools(
            &mut a,
            &tools_dropped(&items(&["chat", "code", "own", "cmd"])),
        );
        assert!(
            !a[3].contains("brain_note"),
            "reason: dropped_tool_listed {}",
            a[3]
        );
        assert!(a[3].contains("mcp__aibd__talk") && a[3].contains("Bash(git add *)"));
        let mut b = a.clone();
        b[3] = format!("x,{}", crate::seats::BRAIN_TOOL);
        drop_tools(&mut b, &tools_dropped(&mode_items("work")));
        assert!(b[3].contains("brain_note"), "reason: held_tool_dropped");
    }

    /// 7B-4 (SPEC §3.7): the same items is no change; turning an item on or the base up is a
    /// raise; anything else a lower.
    #[test]
    fn a_change_is_a_raise_or_a_lower() {
        let w = mode_items("work");
        assert_eq!(change_of("work", &w, "work", &w), Change::Same);
        let no_brain = items(&["chat", "code", "own", "cmd"]);
        assert_eq!(change_of("work", &w, "work", &no_brain), Change::Lower);
        assert_eq!(change_of("work", &no_brain, "work", &w), Change::Raise);
        assert_eq!(
            change_of("safe", &mode_items("safe"), "work", &w),
            Change::Raise
        );
    }

    /// 7B-6 (ebab8 01:47 刀; f163c r1 M1): the open asks the owner before anything is opened,
    /// both starts (open and restart) leave out what the seat does not hold, and the brain
    /// act checks the item (a brand whose tools are not listed).
    #[test]
    fn every_start_and_the_brain_act_read_the_template() {
        let seats = include_str!("seats.rs").replace("\r\n", "\n");
        let at = seats
            .find("            if crate::perm_apply::open_needs_owner(&e.base, &e.items) {")
            .expect("reason: open_not_asked");
        let arm = &seats[at..at
            + seats[at..]
                .find("            Some(e)\n")
                .expect("reason: arm_moved")];
        assert!(
            arm.contains("return crate::repos::asked(&a);")
                && arm.contains("if !crate::owner_ok::approved_for(t, &a) {"),
            "reason: open_ask_not_answered"
        );
        assert!(
            seats.contains(
                "crate::perm_apply::drop_tools(a, &crate::perm_apply::tools_dropped(&p.items));"
            ),
            "reason: open_keeps_tools"
        );
        let revive = include_str!("revive.rs");
        assert!(
            revive.contains("let drop = crate::perm_apply::at_restart(st, &seat.seat_id).await;")
                && revive.contains("crate::perm_apply::drop_tools(&mut argv, &drop);"),
            "reason: restart_keeps_tools"
        );
        assert!(
            include_str!("brain_note.rs")
                .contains("if !crate::perm_apply::holds(&st, seat_id, &mode, \"brain\").await {"),
            "reason: brain_act_ignores_template"
        );
    }

    /// f163c REVIEW-papply L5: under a waiting raise H, a smaller template M (within H, above
    /// now) is still a raise: asked, and it waits for a restart instead of restarting now.
    #[test]
    fn a_smaller_template_under_a_waiting_raise_is_still_asked() {
        let it = |on: &[&str]| Items::from_keys(on);
        let now = it(&["chat"]);
        let high = it(&["chat", "code", "cmd"]);
        let mid = it(&["chat", "code"]);
        assert_eq!(
            change_with_pending("safe", &now, Some(&high), &mid),
            Change::Raise,
            "reason: smaller_under_pending_raise_not_asked"
        );
        assert_eq!(
            change_with_pending("safe", &now, Some(&high), &high),
            Change::Same
        );
        assert_eq!(
            change_with_pending("safe", &now, Some(&high), &now),
            Change::Lower
        );
        assert_eq!(change_with_pending("safe", &now, None, &mid), Change::Raise);
        assert_eq!(
            change_with_pending("safe", &high, None, &mid),
            Change::Lower
        );
    }

    /// f163c REVIEW-papply M1: a restart whose waiting change was not written holds the brain
    /// back, whatever the old items say; an unread row too; a seat with no row keeps its mode's.
    #[test]
    fn a_restart_that_could_not_take_its_change_holds_less() {
        let on: Value = {
            let mut m = serde_json::Map::new();
            for (k, _) in ITEMS {
                m.insert(k.to_owned(), Value::Bool(gives_now("work").contains(&k)));
            }
            Value::Object(m)
        };
        let on = on.to_string();
        assert!(restart_drop(true, Ok(Some(on.clone()))).is_empty());
        assert_eq!(
            restart_drop(false, Ok(Some(on))),
            vec![crate::seats::BRAIN_TOOL],
            "reason: unapplied_lowering_ran_on_old_items"
        );
        assert_eq!(restart_drop(true, Err(())), vec![crate::seats::BRAIN_TOOL]);
        assert!(restart_drop(true, Ok(None)).is_empty());
    }

    /// f163c bnote L1 (bound to 7b): a seat whose template has 第二大腦 off does not hold it,
    /// whatever its mode gives; with no template its mode decides; unread holds nothing.
    #[test]
    fn a_template_with_the_brain_off_keeps_the_seat_from_its_brain() {
        let items = |on: &[&str]| {
            let mut m = serde_json::Map::new();
            for (k, _) in ITEMS {
                m.insert(k.to_owned(), Value::Bool(on.contains(&k)));
            }
            Value::Object(m).to_string()
        };
        let work_own: Vec<&str> = gives_now("work").to_vec();
        assert!(
            work_own.contains(&"brain"),
            "precondition: work gives brain"
        );
        let off: Vec<&str> = work_own.iter().copied().filter(|k| *k != "brain").collect();
        assert!(
            !held_in(Ok(Some((items(&off), None))), "work", "brain"),
            "reason: brain_off_template_held"
        );
        assert!(held_in(Ok(Some((items(&work_own), None))), "work", "brain"));
        // L6: a lowering waiting for the restart takes it now; a raise waiting gives nothing yet
        assert!(
            !held_in(
                Ok(Some((items(&work_own), Some(items(&off))))),
                "work",
                "brain"
            ),
            "reason: waiting_lowering_still_held"
        );
        assert!(!held_in(
            Ok(Some((items(&off), Some(items(&work_own))))),
            "work",
            "brain"
        ));
        assert!(held_in(Ok(None), "work", "brain"), "reason: mode_own_lost");
        assert!(!held_in(Err(()), "work", "brain"), "reason: unread_held");
    }

    /// 7B-5: the window names every item held, what is not given, and what this turns on last.
    #[test]
    fn the_window_lists_the_expanded_items() {
        let e = Expanded {
            tpl_uid: "tpl_a".into(),
            rev: 3,
            name: "寫稿".into(),
            base: "work".into(),
            items: items(&["chat", "code", "own", "cmd", "brain", "net", "push"]),
        };
        let l = window_line(&e, &["push"]);
        assert!(
            l.contains("對話") && l.contains("第二大腦"),
            "reason: items_not_listed {l}"
        );
        assert!(
            l.contains("這一版還沒有 外網"),
            "reason: not_given_unlisted {l}"
        );
        assert!(l.ends_with("這次多開 推送"), "reason: raise_not_last {l}");
        let e2 = Expanded {
            rev: 4,
            ..e.clone()
        };
        assert_ne!(
            e.items_sha(),
            e2.items_sha(),
            "reason: sha_not_bound_to_rev"
        );
    }
}
