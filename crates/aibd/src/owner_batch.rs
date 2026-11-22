//! 指紋批次簽, the pure part (DESIGN-ownerbatch-impl-08de5-1123 r0, f163c REVIEW r0 0/0/3/1;
//! what to do: DESIGN-owner-request-2d297-1122 r4 §2b–§2d). Nothing here is wired yet: which
//! operations may share one window, the list's hash, the window's numbered lines, how many fit,
//! and the duplicates a page sent twice. The collector, the window and the replay come next.
//!
//! - One window, one group (r4 §2c): 搬家 (register a repo, open a seat with it, trust, move
//!   messages), 送回倉, 推分支 / 開 PR. Anything else is not batched in this version.
//! - seat_tick / drive_switch keep the key's signature of their own ticket, which a batch's
//!   child ticket has not: refused `signed_alone` until B4 (f163c r0: only stricter).
//! - The hash, the window's numbers and the page's list are in one order (f163c N1).

use crate::api::AppState;
use crate::owner_ok::{self, Ask};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::Value;
use sha2::{Digest, Sha256};

/// The page act that opens a batch.
pub const KIND: &str = "owner_batch";
/// At most this many in one window.
pub const MAX_ITEMS: usize = 8;
/// The characters the system window shows of the whole list.
pub const WINDOW_CHARS: usize = 900;
/// One item's line longer than this is never batched (r4 §2c: it goes alone).
pub const LINE_CHARS: usize = 160;

/// Which operations share a window (r4 §2c's first groups).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Group {
    /// 搬家: a repo registered, a seat opened with it, trusted, its messages moved.
    Move,
    /// 送回倉 (several seats).
    SendBack,
    /// 推分支 / 開 PR (several seats).
    Push,
}

impl Group {
    pub fn code(self) -> &'static str {
        match self {
            Self::Move => "move",
            Self::SendBack => "send_back",
            Self::Push => "push",
        }
    }
}

/// The operations whose approval is kept as the key's signature of their own ticket.
pub const SIGNED: [&str; 3] = [
    crate::seat_tick::OP_SET,
    crate::seat_tick::OP_RESUME,
    crate::drive_switch::OP,
];

/// The group of an operation, or why it is not batched (`signed_alone` / `not_batchable`).
pub fn group_of(op: &str) -> Result<Group, &'static str> {
    if SIGNED.contains(&op) {
        return Err("signed_alone");
    }
    match op {
        crate::repos::OP_REGISTER
        | crate::repos::OP_OPEN
        | crate::seat_trust::OP
        | crate::seat_move::OP => Ok(Group::Move),
        crate::send_back::OP_SEND_BACK => Ok(Group::SendBack),
        crate::repo_push::OP | crate::pr_open::OP => Ok(Group::Push),
        _ => Err("not_batchable"),
    }
}

/// Why a list is not one batch.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BatchError {
    Empty,
    /// More than fit in one window: at most `max`.
    TooBig {
        max: usize,
    },
    /// Item `i` cannot share this window: `reason`.
    Mixed {
        i: usize,
        reason: &'static str,
    },
}

impl BatchError {
    pub fn code(&self) -> &'static str {
        match self {
            Self::Empty => "batch_empty",
            Self::TooBig { .. } => "batch_too_big",
            Self::Mixed { .. } => "batch_mixed",
        }
    }
}

/// The window's lines, numbered from 1 in the list's order: the plain words and the object.
pub fn window_lines(asks: &[Ask]) -> Vec<String> {
    asks.iter()
        .enumerate()
        .map(|(i, a)| {
            format!(
                "{}. {}({})",
                i + 1,
                owner_ok::top_words(a),
                owner_ok::object_shown(&a.resource)
            )
        })
        .collect()
}

/// How many of `lines`, from the first, fit one window.
pub fn fitting(lines: &[String]) -> usize {
    let mut used = 0;
    let mut n = 0;
    for l in lines.iter().take(MAX_ITEMS) {
        used += l.chars().count() + 1;
        if used > WINDOW_CHARS {
            break;
        }
        n += 1;
    }
    n
}

/// The list as one batch: its group, or the first reason it is not.
pub fn check(asks: &[Ask]) -> Result<Group, BatchError> {
    let first = asks.first().ok_or(BatchError::Empty)?;
    let group = group_of(first.operation).map_err(|reason| BatchError::Mixed { i: 0, reason })?;
    for (i, a) in asks.iter().enumerate().skip(1) {
        match group_of(a.operation) {
            Err(reason) => return Err(BatchError::Mixed { i, reason }),
            Ok(g) if g != group => return Err(BatchError::Mixed { i, reason: "group" }),
            Ok(_) => {}
        }
    }
    let lines = window_lines(asks);
    if let Some(i) = lines.iter().position(|l| l.chars().count() > LINE_CHARS) {
        return Err(BatchError::Mixed {
            i,
            reason: "too_long",
        });
    }
    let max = fitting(&lines);
    if asks.len() > max {
        return Err(BatchError::TooBig { max });
    }
    Ok(group)
}

/// The list's hash (r4 §2c): every item's operation, resource and params hash in order, and
/// how many there are. The order is in it: the same items in another order are another list.
pub fn batch_sha(asks: &[Ask]) -> String {
    let mut h = Sha256::new();
    h.update(format!("aibd/owner-batch/v1\n{}\n", asks.len()));
    for (i, a) in asks.iter().enumerate() {
        // each field with its length (f163c pre N1): a resource holding a line end cannot
        // pass for the next field
        h.update(format!(
            "{i}\n{}:{}\n{}:{}\n{}\n",
            a.operation.len(),
            a.operation,
            a.resource.len(),
            a.resource,
            owner_ok::params_sha256(&a.params)
        ));
    }
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

/// The bytes the key signs for one batch ticket.
pub fn binding(ticket: &str, batch_sha: &str, by: &str) -> Vec<u8> {
    format!("aibd/owner-batch/v1\n{ticket}\n{batch_sha}\n{by}\n").into_bytes()
}

/// An item's identity for de-duplication: its act type and body without `idem_key` /
/// `ticket` (two sends of one act are one item).
pub fn item_sha(kind: &str, body: &Value) -> String {
    let mut b = body.clone();
    if let Some(o) = b.as_object_mut() {
        o.remove("idem_key");
        o.remove("ticket");
    }
    owner_ok::params_sha256(format!("{kind}\n{b}").as_bytes())
}

/// The page's items with the later copies of one item dropped (r4 N2): the indexes kept, in
/// order, and those merged into an earlier one.
pub fn dedup(items: &[(String, Value)]) -> (Vec<usize>, Vec<usize>) {
    let mut seen = std::collections::BTreeSet::new();
    let (mut kept, mut merged) = (Vec::new(), Vec::new());
    for (i, (kind, body)) in items.iter().enumerate() {
        if seen.insert(item_sha(kind, body)) {
            kept.push(i);
        } else {
            merged.push(i);
        }
    }
    (kept, merged)
}

/// What one item's collection gave: exactly one fingerprint ask, or why it cannot be batched
/// (none = it needs no fingerprint; more than one = not one approval).
pub fn one(asks: Vec<Ask>) -> Result<Ask, &'static str> {
    let mut it = asks.into_iter();
    match (it.next(), it.next()) {
        (Some(a), None) => Ok(a),
        (None, _) => Err("no_touch"),
        (Some(_), Some(_)) => Err("many_asks"),
    }
}

// ---------------------------------------------------------------- wiring (B1 接線)

/// The page acts a batch may hold (f163c list r0: collecting runs the act up to its ask, so
/// only acts that always ask before they write are let in). A page seat open asks only with
/// a repo and a mode other than work: anything else would open a seat while being collected.
pub fn collectable(kind: &str, body: &Value) -> bool {
    match kind {
        crate::repos::REGISTER
        | crate::seat_trust::TRUST
        | crate::seat_move::MOVE
        | crate::send_back::SEND_BACK
        | crate::repo_push::PUSH
        | crate::pr_open::OPEN => true,
        crate::seats::PAGE_OPEN => {
            body["repo"].is_string() && body["mode"].as_str().is_some_and(|m| m != "work")
        }
        _ => false,
    }
}

/// One item's end, as the ticket's `items[i]` gives it.
fn ended(state: &str, reason: Option<&str>, result: Option<Value>) -> Value {
    let mut v = serde_json::json!({"state": state});
    if let Some(r) = reason {
        v["reason"] = serde_json::json!(r);
    }
    if let Some(r) = result {
        v["result"] = r;
    }
    v
}

struct Kept {
    /// `page:<8 bytes hex>`: whose batch (the ticket's GET reads with it).
    by: String,
    digest: [u8; 32],
    group: Group,
    /// What aibd sends again, in order (its own copy: the page sends nothing after opening).
    items: Vec<(String, Value)>,
    asks: Vec<Ask>,
    /// The page's index of each kept item, and of each merged one with the kept one it is.
    kept_at: Vec<usize>,
    merged: Vec<(usize, usize)>,
    states: Vec<Value>,
    until: u64,
}

type Table = std::collections::HashMap<String, Kept>;

static BATCHES: std::sync::LazyLock<std::sync::Mutex<Table>> =
    std::sync::LazyLock::new(Default::default);

fn batches() -> std::sync::MutexGuard<'static, Table> {
    BATCHES.lock().unwrap_or_else(|e| e.into_inner())
}

/// The ticket's `items` for the page that opened it, in the page's order; a merged item gives
/// the result of the one it is (f163c B1 pre N2).
pub fn items_of(ticket: &str, by: &str) -> Option<Value> {
    let g = batches();
    let b = g.get(ticket).filter(|b| b.by == by)?;
    let n = b.kept_at.len() + b.merged.len();
    let mut out = vec![Value::Null; n];
    for (k, i) in b.kept_at.iter().enumerate() {
        let mut v = b.states[k].clone();
        v["i"] = serde_json::json!(i);
        out[*i] = v;
    }
    for (i, k) in &b.merged {
        let mut v = b.states[*k].clone();
        v["i"] = serde_json::json!(i);
        v["merged_into"] = serde_json::json!(b.kept_at[*k]);
        out[*i] = v;
    }
    Some(Value::Array(out))
}

fn set_state(ticket: &str, k: usize, v: Value) {
    if let Some(s) = batches().get_mut(ticket).and_then(|b| b.states.get_mut(k)) {
        *s = v;
    }
}

fn refuse(code: StatusCode, reason: &str, more: Value) -> Response {
    let mut v = serde_json::json!({"code": if code == StatusCode::FORBIDDEN { "forbidden" } else { "bad_request" },
        "reason": reason, "request_id": crate::guard::request_id()});
    if let (Some(o), Some(m)) = (v.as_object_mut(), more.as_object()) {
        o.extend(m.clone());
    }
    (code, Json(v)).into_response()
}

fn hex8(d: &[u8; 32]) -> String {
    d[..8].iter().map(|b| format!("{b:02x}")).collect()
}

/// A page act on its way, boxed.
type Sent = std::pin::Pin<Box<dyn std::future::Future<Output = Response> + Send>>;

/// The page act through the one front door, boxed (the act may be this very batch's caller).
fn send(st: &AppState, page: crate::pairing::PageAccess, body: Value) -> Sent {
    Box::pin(crate::seat_input::act(
        axum::extract::State(st.clone()),
        axum::Extension(crate::guard::Caller::Page(page)),
        Some(Json(body)),
    ))
}

async fn body_of(r: Response) -> Value {
    let b = axum::body::to_bytes(r.into_body(), 256 * 1024)
        .await
        .unwrap_or_default();
    serde_json::from_slice(&b).unwrap_or(Value::Null)
}

/// `{"type":"owner_batch","items":[{"type":…,"body":{…}},…],"idem_key"}` from the built-in page
/// (the caller checked that): each item collected, one window for the list, then aibd sends
/// each one again itself once the owner said yes. `202 {ticket, code, until, batch_sha, group,
/// items:[{i, words}], merged}`.
pub async fn open(st: AppState, page: crate::pairing::PageAccess, body: Value) -> Response {
    sweep(crate::owner_ok::unix_now());
    let keys_ok = body.as_object().is_some_and(|o| {
        o.keys()
            .all(|k| matches!(k.as_str(), "type" | "items" | "idem_key"))
    });
    let Some(raw) = body["items"]
        .as_array()
        .filter(|a| keys_ok && !a.is_empty())
    else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "body",
            serde_json::json!({
            "message": "body must be {type, items:[{type, body}], idem_key}"}),
        );
    };
    if raw.len() > MAX_ITEMS * 2 {
        return refuse(
            StatusCode::BAD_REQUEST,
            "batch_too_big",
            serde_json::json!({"max": MAX_ITEMS}),
        );
    }
    let mut list = Vec::new();
    for (i, it) in raw.iter().enumerate() {
        let kind = it["type"].as_str().unwrap_or_default();
        let Some(b) = it["body"].as_object() else {
            return refuse(StatusCode::BAD_REQUEST, "item", serde_json::json!({"i": i}));
        };
        let mut b = Value::Object(b.clone());
        if let Some(o) = b.as_object_mut() {
            o.remove("ticket");
            o.insert("type".into(), serde_json::json!(kind));
        }
        if !collectable(kind, &b) {
            return refuse(
                StatusCode::BAD_REQUEST,
                "batch_mixed",
                serde_json::json!({"i": i, "why": "not_batchable"}),
            );
        }
        list.push((kind.to_owned(), b));
    }
    let (kept_at, merged_at) = dedup(&list);
    let shas: Vec<String> = list.iter().map(|(k, b)| item_sha(k, b)).collect();
    let merged: Vec<(usize, usize)> = merged_at
        .iter()
        .filter_map(|i| Some((*i, kept_at.iter().position(|k| shas[*k] == shas[*i])?)))
        .collect();
    let mut asks = Vec::new();
    for i in &kept_at {
        let (r, got) = crate::owner_ok::collecting(send(&st, page, list[*i].1.clone())).await;
        if r.status() != StatusCode::ACCEPTED {
            let status = r.status();
            let b = body_of(r).await;
            // f163c wire pre L2: an act that asked nothing has already been done; the page is
            // told so as it is, never as refused
            let (code, reason) = not_asked(status);
            return refuse(
                code,
                reason,
                serde_json::json!({
                "i": i, "status": status.as_u16(), "result": b,
                "why": b["reason"].as_str().or(b["code"].as_str())}),
            );
        }
        match one(got) {
            Ok(a) => asks.push(a),
            Err(why) => {
                return refuse(
                    StatusCode::BAD_REQUEST,
                    "batch_mixed",
                    serde_json::json!({"i": i, "why": why}),
                )
            }
        }
    }
    let group = match check(&asks) {
        Ok(g) => g,
        Err(e) => {
            let mut more = serde_json::json!({});
            match e {
                BatchError::TooBig { max } => more["max"] = serde_json::json!(max),
                BatchError::Mixed { i, reason } => {
                    more["i"] = serde_json::json!(kept_at[i]);
                    more["why"] = serde_json::json!(reason);
                }
                BatchError::Empty => {}
            }
            return refuse(StatusCode::BAD_REQUEST, e.code(), more);
        }
    };
    let Ok(ticket) = crate::sessions::new_id("ot_") else {
        return refuse(
            StatusCode::FORBIDDEN,
            crate::owner_ok::UNAVAILABLE,
            serde_json::json!({}),
        );
    };
    let (sha, lines) = (batch_sha(&asks), window_lines(&asks));
    let by = format!("page:{}", hex8(&page.digest));
    let n = asks.len();
    batches().insert(
        ticket.clone(),
        Kept {
            by: by.clone(),
            digest: page.digest,
            group,
            items: kept_at.iter().map(|i| list[*i].clone()).collect(),
            asks,
            kept_at: kept_at.clone(),
            merged: merged.clone(),
            states: vec![ended("waiting", None, None); n],
            until: crate::owner_ok::unix_now() + crate::owner_ok::TICKET_SECS + 600,
        },
    );
    let rt = tokio::runtime::Handle::current();
    let st2 = st.clone();
    let asked = crate::owner_ok::ask_batch(crate::owner_ok::BatchAsk {
        ticket: ticket.clone(),
        batch_sha: sha.clone(),
        lines: lines.clone(),
        by,
        answered: Box::new(move |t, yes| {
            if yes {
                let _detached = rt.spawn(run(st2, t));
            } else {
                let n = batches().get(&t).map_or(0, |b| b.states.len());
                for k in 0..n {
                    set_state(&t, k, ended("failed", Some("owner_said_no"), None));
                }
            }
        }),
    });
    match asked {
        crate::owner_ok::Asked::Ticket(t) => {
            tracing::info!(target: "owner_batch", ticket12 = &t[..t.len().min(12)], n, group = group.code(),
                batch_sha12 = &sha[..12], "owner_batch.opened");
            (
                StatusCode::ACCEPTED,
                Json(serde_json::json!({"done": false, "ticket": t,
                    "code": crate::owner_ok::pair_code(&t), "batch_sha": sha, "group": group.code(),
                    "max": MAX_ITEMS, "merged": merged_at,
                    "items": kept_at.iter().zip(lines).map(|(i, w)| serde_json::json!({"i": i, "words": w})).collect::<Vec<_>>()})),
            )
                .into_response()
        }
        crate::owner_ok::Asked::Unavailable(why) => {
            batches().remove(&ticket);
            let mut v = crate::owner_ok::refusal(why);
            v["request_id"] = serde_json::json!(crate::guard::request_id());
            (StatusCode::FORBIDDEN, Json(v)).into_response()
        }
    }
}

/// After the yes: the list used once, then each item sent again in order with its own child
/// ticket and aibd's copy of its body, the pairing read as it is now (f163c L3). In the 搬家
/// group an item after a failed one is skipped; the other groups go on (f163c list L4).
async fn run(st: AppState, ticket: String) {
    let Some((by, digest, group, items, asks)) = batches().get(&ticket).map(|b| {
        (
            b.by.clone(),
            b.digest,
            b.group,
            b.items.clone(),
            b.asks.clone(),
        )
    }) else {
        return;
    };
    if let Err(why) = crate::owner_ok::take_batch(&ticket, &by) {
        for k in 0..items.len() {
            set_state(&ticket, k, ended("failed", Some(why), None));
        }
        return;
    }
    let mut broke = false;
    for (k, ((kind, body), ask)) in items.into_iter().zip(asks).enumerate() {
        let end = if skips(group, broke) {
            ended("skipped", Some("an_earlier_move_failed"), None)
        } else {
            one_item(&st, &ticket, digest, body, &ask).await
        };
        broke |= end["state"] != "done";
        tracing::info!(target: "owner_batch", ticket12 = &ticket[..ticket.len().min(12)], k,
            act = kind.as_str(), operation = ask.operation, state = end["state"].as_str().unwrap_or(""),
            reason = end["reason"].as_str().unwrap_or(""), "owner_batch.item");
        set_state(&ticket, k, end);
    }
}

/// f163c list L4: in the 搬家 group an item after a failed one is not done (each step needs
/// the one before); a send back or a push stands alone.
/// An item that did not ask while being collected: done already (2xx) or refused; the batch
/// stops either way (f163c wire pre L2).
pub fn not_asked(status: StatusCode) -> (StatusCode, &'static str) {
    if status.is_success() {
        (StatusCode::CONFLICT, "batch_item_done_without_window")
    } else {
        (StatusCode::BAD_REQUEST, "batch_item_refused")
    }
}

pub fn skips(group: Group, broke: bool) -> bool {
    broke && group == Group::Move
}

async fn one_item(st: &AppState, batch: &str, digest: [u8; 32], body: Value, ask: &Ask) -> Value {
    let Some(page) = crate::owner_ok::page_now(digest).filter(|p| p.confirmed) else {
        return ended("failed", Some("pairing_gone"), None);
    };
    let Some(child) = crate::owner_ok::child(batch, ask) else {
        return ended("failed", Some("batch_not_approved"), None);
    };
    let mut b = body;
    b["ticket"] = serde_json::json!(child);
    let r = send(st, page, b).await;
    let status = r.status();
    let v = body_of(r).await;
    if status.is_success() && status != StatusCode::ACCEPTED {
        return ended("done", None, Some(v));
    }
    // the child ticket is bound to the ask this item gave when collected: an act that now asks
    // for anything else is not approved by it (f163c list L3)
    if v["reason"] == "owner_check_not_approved" {
        return ended("failed", Some("params_changed"), None);
    }
    let why = v["reason"]
        .as_str()
        .or(v["code"].as_str())
        .unwrap_or("refused")
        .to_owned();
    let mut e = ended("failed", None, None);
    e["reason"] = serde_json::json!(why);
    e
}

/// Batches past their time are dropped (their tickets are gone from owner_ok too).
pub fn sweep(now: u64) {
    batches().retain(|_, b| now < b.until);
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;
    use aib_approve::category::Category;
    use serde_json::json;

    fn ask(op: &'static str, resource: &str, params: Value) -> Ask {
        Ask {
            category: Category::Command,
            operation: op,
            resource: resource.to_owned(),
            params: serde_json::to_vec(&params).unwrap(),
            by: "page:p1".into(),
        }
    }

    fn push(n: usize) -> Vec<Ask> {
        (0..n)
            .map(|i| {
                ask(
                    crate::repo_push::OP,
                    &format!("ez2-aib · web/s{i}"),
                    json!({"repo": "ez2-aib", "branch": format!("web/s{i}")}),
                )
            })
            .collect()
    }

    /// B1-1: one group per window; a signed one and an unlisted one are refused by index.
    #[test]
    fn one_window_holds_one_group_and_no_signed_act() {
        assert_eq!(check(&push(2)), Ok(Group::Push));
        let mut mixed = push(2);
        mixed.push(ask(crate::send_back::OP_SEND_BACK, "s", json!({})));
        assert_eq!(
            check(&mixed),
            Err(BatchError::Mixed {
                i: 2,
                reason: "group"
            })
        );
        let mut signed = push(1);
        signed.push(ask(crate::seat_tick::OP_SET, "s", json!({})));
        assert_eq!(
            check(&signed),
            Err(BatchError::Mixed {
                i: 1,
                reason: "signed_alone"
            }),
            "reason: signed_batched"
        );
        assert_eq!(group_of(crate::drive_switch::OP), Err("signed_alone"));
        assert_eq!(group_of(crate::gh_token::OP), Err("not_batchable"));
        assert_eq!(group_of(crate::repos::OP_REGISTER), Ok(Group::Move));
        assert_eq!(check(&[]), Err(BatchError::Empty));
    }

    /// B1-2: the list's hash holds the order and the count; the window numbers in that order.
    #[test]
    fn the_hash_and_the_window_keep_the_order() {
        let a = push(3);
        let mut b = a.clone();
        b.swap(0, 2);
        assert_ne!(batch_sha(&a), batch_sha(&b), "reason: order_not_in_hash");
        assert_ne!(
            batch_sha(&a[..2]),
            batch_sha(&a),
            "reason: count_not_in_hash"
        );
        let mut c = a.clone();
        c[1].params = b"{}".to_vec();
        assert_ne!(batch_sha(&a), batch_sha(&c), "reason: params_not_in_hash");
        // pre N1: a resource holding line ends cannot pass for the fields after it. With the
        // fields only split by line ends, these two lists would be the same bytes.
        let op = crate::repo_push::OP;
        let (p0, p1, p2) = (json!({"n": 0}), json!({"n": 1}), json!({"n": 2}));
        let h = |v: &Value| owner_ok::params_sha256(&serde_json::to_vec(v).unwrap());
        let d = vec![
            ask(op, "x", p0.clone()),
            ask(op, &format!("Y\n{}\n1\n{op}\nZ", h(&p1)), p2.clone()),
        ];
        let e = vec![
            ask(op, &format!("x\n{}\n1\n{op}\nY", h(&p0)), p1),
            ask(op, "Z", p2),
        ];
        assert_ne!(batch_sha(&d), batch_sha(&e), "reason: fields_not_delimited");
        let lines = window_lines(&b);
        assert!(
            lines[0].starts_with("1. ") && lines[0].contains("web/s2"),
            "{lines:?}"
        );
        assert!(
            lines[2].starts_with("3. ") && lines[2].contains("web/s0"),
            "{lines:?}"
        );
        assert_eq!(lines.len(), 3, "reason: an_item_not_in_the_window");
    }

    /// B1-3: more than a window holds is refused with how many fit; a line too long goes alone.
    #[test]
    fn what_does_not_fit_one_window_is_refused() {
        assert_eq!(
            check(&push(MAX_ITEMS + 1)),
            Err(BatchError::TooBig { max: MAX_ITEMS })
        );
        let long = ask(
            crate::repos::OP_REGISTER,
            &"r".repeat(80),
            json!({"name": "n".repeat(100)}),
        );
        let mut v = vec![ask(crate::repos::OP_REGISTER, "x", json!({"name": "x"}))];
        v.push(long);
        assert_eq!(
            check(&v),
            Err(BatchError::Mixed {
                i: 1,
                reason: "too_long"
            })
        );
        let wide: Vec<String> = (0..6).map(|_| "字".repeat(200)).collect();
        assert_eq!(fitting(&wide), 4, "reason: window_overfilled");
    }

    /// B1-4: the same act sent twice (another idem_key) is one item; the first one stays.
    #[test]
    fn the_same_act_twice_is_one_item() {
        let items = vec![
            (
                "repo_push".to_owned(),
                json!({"repo": "r", "branch": "b", "idem_key": "1"}),
            ),
            (
                "repo_push".to_owned(),
                json!({"repo": "r", "branch": "c", "idem_key": "2"}),
            ),
            (
                "repo_push".to_owned(),
                json!({"repo": "r", "branch": "b", "idem_key": "3"}),
            ),
            (
                "pr_open".to_owned(),
                json!({"repo": "r", "branch": "b", "idem_key": "4"}),
            ),
        ];
        assert_eq!(
            dedup(&items),
            (vec![0, 1, 3], vec![2]),
            "reason: duplicate_kept"
        );
    }

    /// B1-5: an item joins a batch only when collecting it asked exactly one fingerprint.
    #[test]
    fn an_item_is_batched_only_with_one_ask() {
        assert_eq!(one(vec![]).map(|_| ()), Err("no_touch"));
        assert_eq!(one(push(2)).map(|_| ()), Err("many_asks"));
        assert_eq!(one(push(1)).unwrap().resource, "ez2-aib · web/s0");
        let b = binding("ot_1", &batch_sha(&push(1)), "page:p1");
        assert!(String::from_utf8(b)
            .unwrap()
            .starts_with("aibd/owner-batch/v1\not_1\n"));
    }

    /// f163c list r0: only acts that always ask before writing are collected; a page seat open
    /// only with a repo and not in work mode (a work open asks nothing and would open).
    #[test]
    fn only_acts_that_ask_first_are_collected() {
        let open = |b: Value| collectable(crate::seats::PAGE_OPEN, &b);
        assert!(open(json!({"repo": "ez2-aib", "mode": "dgrs"})));
        assert!(open(json!({"repo": "ez2-aib", "mode": "safe"})));
        assert!(
            !open(json!({"repo": "ez2-aib", "mode": "work"})),
            "reason: work_open_collected"
        );
        assert!(
            !open(json!({"mode": "dgrs"})),
            "reason: open_without_repo_collected"
        );
        assert!(collectable(crate::repo_push::PUSH, &json!({})));
        assert!(!collectable(KIND, &json!({})), "reason: batch_in_batch");
        assert!(!collectable("seat_input", &json!({})));
    }

    /// f163c list L4: after a failed 搬家 step the rest is skipped; other groups go on.
    #[test]
    fn a_failed_move_step_stops_the_rest_only_in_its_group() {
        assert!(skips(Group::Move, true));
        assert!(!skips(Group::Move, false));
        assert!(!skips(Group::SendBack, true) && !skips(Group::Push, true));
    }

    /// B1 pre N2: a merged item reads as the one it is, with where it was merged.
    #[test]
    fn a_merged_item_reads_as_the_kept_one() {
        batches().insert(
            "ot_t1".into(),
            Kept {
                by: "page:p".into(),
                digest: [0; 32],
                group: Group::Push,
                items: vec![],
                asks: vec![],
                kept_at: vec![0, 2],
                merged: vec![(1, 0)],
                states: vec![ended("done", None, None), ended("failed", Some("x"), None)],
                until: u64::MAX,
            },
        );
        let v = items_of("ot_t1", "page:p").unwrap();
        assert_eq!(v[1]["state"], "done", "reason: merged_not_as_kept {v}");
        assert_eq!(v[1]["merged_into"], 0);
        assert_eq!(v[2]["state"], "failed");
        assert!(
            items_of("ot_t1", "page:q").is_none(),
            "reason: other_page_reads"
        );
        batches().remove("ot_t1");
    }

    /// f163c wire pre L2: an item done with no window is told as done, not refused.
    #[test]
    fn an_item_done_without_a_window_is_told_as_done() {
        assert_eq!(
            not_asked(StatusCode::OK).1,
            "batch_item_done_without_window",
            "reason: done_told_as_refused"
        );
        assert_eq!(not_asked(StatusCode::CREATED).0, StatusCode::CONFLICT);
        assert_eq!(not_asked(StatusCode::BAD_REQUEST).1, "batch_item_refused");
        assert_eq!(not_asked(StatusCode::FORBIDDEN).0, StatusCode::BAD_REQUEST);
    }
}
