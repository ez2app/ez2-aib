//! Threads of many (N-workspace; SPEC-workspace-multiseat-519d0-1118 §1.3, §7, §8): one
//! conversation per item of the project tree, where every member reads the same timeline and
//! `@` calls a seat in. No private messages between seats.
//!
//! - `thread_open {node?, title, members: [{seat, role}]}`: a page or a seat opens a thread.
//!   A tree item has one thread (§1.3, second wave): opening an item that has one answers 200
//!   with it (`existing: true`) and adds the members it did not have; the page does this when
//!   an item of the tree is clicked. Without `node` it is a free thread, always new (201);
//!   a page sending `reuse: "title"` gets the earliest free thread of that title instead
//!   (200 `existing: true`, the members it lacked added), new only when there is none.
//!   A seat may only put seats of its own cabin in, and is a member itself (`author` unless
//!   it lists itself). A person is never a member row: every paired page is the person.
//! - `thread_send {thread_id, body, to?: [seat …] | "all", idem_key?}`: a post. The sender is
//!   the credential (a page is `human:<16 hex>`, a seat `seat:<id>`), never a field. `to` are
//!   the seats called (`@`); the other members are `cc` and are only told (b4297: at their
//!   next turn). A seat posts only in threads it is a member of.
//! - Each called seat gets a delivery row of its own, queued with the text (`post_id` in its
//!   item); delivering it is the seat side's (b4297 @ 點名送達). A safe sender (a safe seat,
//!   or a user folder page) never calls a dgrs seat: that row is written `failed`
//!   (`safe_cannot_send_to_dgrs`) so the page shows who could not be reached.
//! - Limits are the thread's (§1.3): one text at most [`BODY_MAX`] bytes; at most
//!   [`PER_MINUTE`] posts a minute; after [`QUIET_LIMIT`] posts in a row with no person, seats
//!   stop (`thread_needs_a_person`) until a person posts.
//! - Reads: `GET /api/v1/threads?node=` (a page) and `GET /api/v1/threads/t_…` (a page;
//!   texts on the built-in page only), `thread_view {thread_id, cursor?}` (a member seat, with
//!   texts: they are its thread's). Events (`thread.opened`, `thread.posted`) carry lengths and
//!   SHA-256, never the text.

use crate::api::AppState;
use crate::guard::request_id;
use aib_store::{Inserted, Member, MessageRow, ThreadRow};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;

pub const OPEN: &str = "thread_open";
pub const SEND: &str = "thread_send";
pub const VIEW: &str = "thread_view";
pub const READ: &str = "thread_read";
/// A seat's own list of the threads it is a member of (`thread_list {node?, limit?}`), so its
/// `aibd mcp` can show it where it may post (third wave 3).
pub const LIST: &str = "thread_list";

/// Longest text of one post (§1.3: 32 KiB).
pub const BODY_MAX: usize = 32 * 1024;
/// Most posts one thread takes in a minute.
pub const PER_MINUTE: usize = 20;
/// Posts in a row with no person after which seats stop (§1.3: 20 rounds).
pub const QUIET_LIMIT: usize = 20;
/// Longest title.
pub const TITLE_MAX: usize = 200;
/// Most members a thread opens with.
pub const MEMBERS_MAX: usize = 32;
/// Roles of a seat on an item (§1.2: 作者 / 審查 / 驗收 / 換上 / 相依 / 旁聽).
pub const ROLES: [&str; 6] = [
    "author", "reviewer", "verifier", "deployer", "depends", "observer",
];

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({ "code": code, "message": message, "request_id": request_id() })),
    )
        .into_response()
}

fn internal(what: &str) -> Response {
    refuse(StatusCode::INTERNAL_SERVER_ERROR, "internal", what)
}

fn bad(message: &str) -> Response {
    refuse(StatusCode::BAD_REQUEST, "invalid_request", message)
}

fn now_unix() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| i64::try_from(d.as_secs()).unwrap_or(i64::MAX))
        .unwrap_or(0)
}

/// Who is acting, as the credential says.
#[derive(Debug, Clone)]
pub struct Sender {
    /// `human:<16 hex>` or `seat:<id>`.
    pub key: String,
    /// A seat's mode is not dgrs, or the page is a user folder page.
    pub safe: bool,
    /// The seat's cabin (a page has none).
    pub cabin: Option<String>,
    /// Texts are given back (a seat, or the built-in page).
    pub with_body: bool,
    /// A user folder page (its words are not the person's: shown as 用戶頁, 5d33a M1).
    pub user_page: bool,
    /// G13: aibd brings this seat's turn answer back; the post that called it.
    pub auto_reply_to: Option<String>,
    /// G13: the turn was not done, and why (the post is 「沒做成」).
    pub auto_failed: Option<String>,
}

impl Sender {
    pub(crate) fn is_person(&self) -> bool {
        self.key.starts_with("human:")
    }
}

/// Whether `body` is one of the thread acts.
pub fn is_thread_act(body: &Value) -> bool {
    matches!(
        body.get("type").and_then(Value::as_str),
        Some(OPEN | SEND | VIEW | READ | LIST)
    )
}

/// `t_` and 16 lowercase hex.
pub fn thread_id_ok(t: &str) -> bool {
    t.strip_prefix("t_").is_some_and(|h| {
        h.len() == 16
            && h.bytes()
                .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
    })
}

/// A member given as `seat:<id>` or a bare seat id ⇒ `seat:<id>`.
fn seat_key(s: &str) -> Option<String> {
    let k = if s.starts_with("seat:") {
        s.to_owned()
    } else {
        format!("seat:{s}")
    };
    crate::messages::key_ok(&k).then_some(k)
}

/// Who a post calls and who it only tells. `to` is `None` (nobody called), `"all"`, or a list
/// of member seats; the sender is never in either list. `Err` is the refusal code.
/// `@all` narrowed to the seats running now: the called ones that are not move to `cc`.
pub fn all_present(
    to: Vec<String>,
    mut cc: Vec<String>,
    live: &[String],
) -> (Vec<String>, Vec<String>) {
    let (run, idle): (Vec<String>, Vec<String>) = to.into_iter().partition(|k| {
        live.iter()
            .any(|l| k.strip_prefix("seat:") == Some(l.as_str()))
    });
    cc.extend(idle);
    cc.sort();
    (run, cc)
}

/// Whether a dgrs seat may read this post's text: only when this machine's dgrs switch is on
/// and its sender was not safe (5d33a PREREAD 2feffc7b M1; the same rule as the catch-up,
/// f0074760 / a85fa439: a post that does not say counts as safe).
pub fn dgrs_reads(p: &MessageRow, switch_on: bool) -> bool {
    switch_on && p.item.get("sender_safe").and_then(Value::as_bool) == Some(false)
}

pub fn split_to(
    members: &[String],
    sender: &str,
    to: Option<&Value>,
) -> Result<(Vec<String>, Vec<String>), &'static str> {
    let others: Vec<String> = members.iter().filter(|m| *m != sender).cloned().collect();
    let called: Vec<String> = match to {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::String(s)) if s == "all" => others.clone(),
        Some(Value::Array(a)) => {
            let mut v = BTreeSet::new();
            for x in a {
                let k = x.as_str().and_then(seat_key).ok_or("to_not_a_seat")?;
                if k == sender {
                    continue;
                }
                if !others.contains(&k) {
                    return Err("to_not_a_member");
                }
                v.insert(k);
            }
            v.into_iter().collect()
        }
        Some(_) => return Err("to_not_a_seat"),
    };
    let cc = others.into_iter().filter(|m| !called.contains(m)).collect();
    Ok((called, cc))
}

/// Whether a thread takes one more post now: `recent` is `(created_unix, sender_kind)` of
/// the last posts, newest first. `Err` is the refusal code.
pub fn pace(recent: &[(i64, String)], now: i64, person: bool) -> Result<(), &'static str> {
    if recent.iter().filter(|(t, _)| now - *t < 60).count() >= PER_MINUTE {
        return Err("thread_rate_limited");
    }
    if !person
        && recent.len() >= QUIET_LIMIT
        && recent
            .iter()
            .take(QUIET_LIMIT)
            .all(|(_, k)| k.as_str() != "human")
    {
        return Err("thread_needs_a_person");
    }
    Ok(())
}

/// Why a called seat cannot get a post from this sender, if it cannot.
pub fn unreachable(sender_safe: bool, to_mode: Option<&str>) -> Option<&'static str> {
    (sender_safe && to_mode == Some("dgrs")).then_some("safe_cannot_send_to_dgrs")
}

fn digest(t: &str) -> String {
    use sha2::Digest;
    sha2::Sha256::digest(t.as_bytes())
        .iter()
        .map(|x| format!("{x:02x}"))
        .collect()
}

fn thread_object(t: &ThreadRow, members: &[Member]) -> Value {
    json!({
        "thread_id": t.thread_id,
        "node": t.node,
        "title": t.title,
        "opened_by": t.opened_by,
        "created": t.created,
        "members": members.iter().map(|m| json!({"seat": m.member, "role": m.role})).collect::<Vec<_>>(),
    })
}

/// A post as the page reads it: the message object plus `to`, `cc` and each `@`'s own state.
/// Who has read up to (or past) this post: the readers whose mark sits at or after it
/// (`pos` = the post's place in the order written).
fn readers_of(pos: Option<i64>, marks: &[(String, String, i64)]) -> Vec<String> {
    marks
        .iter()
        .filter(|(_, _, at)| pos.is_some_and(|p| *at >= p))
        .map(|(r, _, _)| r.clone())
        .collect()
}

fn post_object(
    p: &MessageRow,
    pos: Option<i64>,
    deliveries: &[MessageRow],
    marks: &[(String, String, i64)],
    me: &str,
    with_body: bool,
) -> Value {
    let mut o = crate::messages::object(p, me, with_body);
    let list = |k: &str| p.item.get(k).cloned().unwrap_or_else(|| json!([]));
    let calls: Vec<Value> = deliveries
        .iter()
        .filter(|d| d.item.get("post_id").and_then(Value::as_str) == Some(&p.message_id))
        .map(|d| {
            let mut c = json!({
                "seat": d.to_key,
                "message_id": d.message_id,
                "state": crate::messages::state_of(&d.item),
                "status": d.item.get("status"),
                "revision": d.rev,
            });
            if let (Some(code), Some(m)) = (d.item.get("failed_code"), c.as_object_mut()) {
                m.insert("failed_code".into(), code.clone());
            }
            c
        })
        .collect();
    if let Some(m) = o.as_object_mut() {
        m.insert("to".into(), list("to"));
        m.insert("cc".into(), list("cc"));
        m.insert("calls".into(), json!(calls));
        m.insert("read_by".into(), json!(readers_of(pos, marks)));
        m.insert("sender_kind".into(), json!(p.item.get("sender_kind")));
        // G13 (0eed7 VERIFY-g13 L1): a turn's answer brought back shows as one
        for k in ["auto", "reply_to", "failed", "not_running"] {
            if let Some(v) = p.item.get(k) {
                m.insert(k.into(), v.clone());
            }
        }
        // 0eed7 VERIFY-oneline2 M1: an imported summary / handoff / document names its file,
        // so the page can open it (GET /api/v1/doc); the title follows the body rule
        if let Some(d) = doc_of(p, with_body) {
            m.insert("doc".into(), d);
        }
    }
    o
}

/// A post's documents: `n` (its place, what GET /api/v1/doc takes), `kind` (the doc wall's
/// name prefix), `root`, and with the words (not on the user page, 5d33a
/// CHECK-userpage-docname: the name can say customer / members) `file`; the title follows the
/// body rule. Only names the doc wall opens (0eed7 r2 N2). `None` when it names none.
fn doc_of(p: &MessageRow, words: bool) -> Option<Value> {
    // one `{…}` or a list, as docs::doc_of reads it
    let docs: Vec<&Value> = match p.item.get("doc")? {
        Value::Array(a) => a.iter().collect(),
        d @ Value::Object(_) => vec![d],
        _ => return None,
    };
    let out: Vec<Value> = docs
        .into_iter()
        .enumerate()
        .filter_map(|(n, d)| {
            // 0eed7 r2 N2: only a name the doc wall opens (no link that answers 403)
            let file = d.get("file")?.as_str().filter(|f| crate::docs::listed(f))?;
            let kind = crate::docs::DOC_KINDS
                .iter()
                .find(|k| file.starts_with(*k))?
                .trim_end_matches('-');
            let title = d.get("title").and_then(Value::as_str).unwrap_or("");
            let mut o = json!({
                "n": n,
                "kind": kind,
                "root": d.get("root").and_then(Value::as_str).unwrap_or("pending"),
                "title": crate::workspace::words_or_hash(title, words),
            });
            if words {
                o["file"] = json!(file);
            }
            Some(o)
        })
        .collect();
    (!out.is_empty()).then(|| json!(out))
}

/// Who is in a thread now. A tree item's thread is that item's own assignments, read at each
/// use (5d33a wave-2 N1: removing an assignment takes the thread away the next time; N2 / C3:
/// a seat on an item above gets nothing); the members written at open are not used for it.
/// A free thread (no node) is the members written when it was opened. When the assignments
/// cannot be read a tree item's thread has no seat (closed rather than open). Blocking.
pub fn members_now(t: &ThreadRow, stored: Vec<Member>) -> Vec<Member> {
    let Some(node) = t.node.as_deref() else {
        return stored;
    };
    let mut out: Vec<Member> = Vec::new();
    for (seat, role) in crate::assign::members_of(node).unwrap_or_default() {
        let Some(member) = seat_key(&seat) else {
            continue;
        };
        if !out.iter().any(|m| m.member == member) {
            out.push(Member { member, role });
        }
    }
    out
}

/// The threads `member` is in now, newest first, at most `lim` (all with `None`, the person):
/// its free threads by the written members, and the threads of the items it is assigned on
/// now (not the members written at open). `node` keeps one item. What a seat may catch up on
/// is this list too (5d33a 7bfb32ab N1). Blocking.
pub fn threads_of(
    db: &aib_store::MessageDb,
    member: Option<&str>,
    node: Option<&str>,
    lim: u32,
) -> aib_store::Result<Vec<ThreadRow>> {
    let Some(me) = member else {
        return db.threads_list(node, None, lim);
    };
    let mut ts: Vec<ThreadRow> = db
        .threads_list(node, Some(me), lim)?
        .into_iter()
        .filter(|t| t.node.is_none())
        .collect();
    let mine = crate::assign::nodes_of(me.trim_start_matches("seat:")).unwrap_or_default();
    for n in mine.iter().filter(|n| node.is_none_or(|x| x == n.as_str())) {
        ts.extend(db.threads_list(Some(n), None, lim)?);
    }
    ts.sort_by(|a, b| (b.last_unix, &b.thread_id).cmp(&(a.last_unix, &a.thread_id)));
    ts.truncate(usize::try_from(lim.clamp(1, aib_store::PAGE_MAX)).unwrap_or(1));
    Ok(ts)
}

/// Whether `member` is in thread `thread_id` now: a call queued before an assignment was
/// removed is not delivered after it (5d33a 7bfb32ab N2; for the catch-up delivery). Blocking.
pub fn is_member_now(
    db: &aib_store::MessageDb,
    thread_id: &str,
    member: &str,
) -> aib_store::Result<bool> {
    let Some(t) = db.thread_get(thread_id)? else {
        return Ok(false);
    };
    let m = members_now(&t, db.thread_members(thread_id)?);
    Ok(m.iter().any(|x| x.member == member))
}

pub(crate) async fn blocking<T: Send + 'static>(
    f: impl FnOnce() -> aib_store::Result<T> + Send + 'static,
) -> Option<T> {
    tokio::task::spawn_blocking(f).await.ok()?.ok()
}

async fn seat_metas(st: &AppState) -> Option<Vec<aib_store::SeatMeta>> {
    let store = st.store.clone();
    blocking(move || store.seat_metas()).await
}

/// `thread_open {node?, title, members: [{seat, role}]}`.
pub async fn open_as(st: AppState, s: Sender, body: Value) -> Response {
    let title = body.get("title").and_then(Value::as_str).map(str::trim);
    let Some(title) = title.filter(|t| !t.is_empty() && t.len() <= TITLE_MAX) else {
        return bad("title is 1–200 bytes");
    };
    let node = match body.get("node") {
        None | Some(Value::Null) => None,
        Some(Value::String(n)) if !n.is_empty() && n.len() <= 128 => Some(n.clone()),
        Some(_) => return bad("node is the id of a project tree item"),
    };
    // §17-1: the writer gives the kind when it opens the thread
    let category = match body.get("category") {
        None | Some(Value::Null) => None,
        Some(Value::String(k)) if aib_store::CATEGORIES.contains(&k.as_str()) => Some(k.clone()),
        Some(_) => {
            return refuse(
                StatusCode::BAD_REQUEST,
                "category_unknown",
                "category is council, summary, handoff, drive or shadow",
            )
        }
    };
    let list = match body.get("members") {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::Array(a)) if a.len() <= MEMBERS_MAX => a.clone(),
        Some(_) => return bad("members is a list of at most 32 {seat, role}"),
    };
    let mut members: Vec<Member> = Vec::new();
    for m in &list {
        let (Some(k), Some(role)) = (
            m.get("seat").and_then(Value::as_str).and_then(seat_key),
            m.get("role").and_then(Value::as_str),
        ) else {
            return bad("a member is {seat, role}");
        };
        if !ROLES.contains(&role) {
            return bad("role is author, reviewer, verifier, deployer, depends or observer");
        }
        if members.iter().any(|x| x.member == k) {
            return bad("a seat is a member once");
        }
        members.push(Member {
            member: k,
            role: role.to_owned(),
        });
    }
    if !s.is_person() && !members.iter().any(|m| m.member == s.key) {
        members.push(Member {
            member: s.key.clone(),
            role: "author".into(),
        });
    }
    let Some(metas) = seat_metas(&st).await else {
        return internal("seats not read");
    };
    for m in &members {
        let id = m.member.trim_start_matches("seat:");
        let Some(meta) = metas.iter().find(|x| x.seat_id == id) else {
            return refuse(
                StatusCode::NOT_FOUND,
                "seat_not_found",
                "a member is not a seat",
            );
        };
        // a seat opens threads with the seats of its cabin only
        if !s.is_person() && meta.cabin != s.cabin {
            return refuse(
                StatusCode::FORBIDDEN,
                "seat_not_in_cabin",
                "a seat opens a thread with the seats of its own cabin",
            );
        }
    }
    // a seat opens a tree item's thread only when it is assigned on that item itself (C3)
    if let (false, Some(n)) = (s.is_person(), node.clone()) {
        let me = s.key.trim_start_matches("seat:").to_owned();
        let on = blocking(move || {
            Ok(crate::assign::members_of(&n)
                .unwrap_or_default()
                .iter()
                .any(|(seat, _)| *seat == me))
        })
        .await;
        if on != Some(true) {
            return refuse(
                StatusCode::FORBIDDEN,
                "not_assigned_here",
                "a seat opens the thread of a tree item it is assigned on",
            );
        }
    }
    let Ok(thread_id) = crate::sessions::new_id("t_") else {
        return internal("no thread id");
    };
    let (now, at) = (crate::sessions::now_rfc3339(), now_unix());
    let t = ThreadRow {
        thread_id,
        node,
        title: title.to_owned(),
        opened_by: s.key.clone(),
        created: now,
        created_unix: at,
        last_unix: at,
    };
    // a tree item's members are its assignments: the list sent is not written (5d33a N3)
    let written = if t.node.is_some() {
        Vec::new()
    } else {
        members.clone()
    };
    // 043dd 02:42: a page reopening its free thread by title adds the seats it lacks
    let by_title = s.is_person() && t.node.is_none() && body.get("reuse") == Some(&json!("title"));
    let (db, t2, m2) = (st.messages.clone(), t.clone(), written);
    let opened = blocking(move || {
        if by_title {
            db.thread_open_by_title(t2, m2)
        } else {
            db.thread_open(t2, m2)
        }
    });
    match opened.await {
        Some(aib_store::Opened::New) => {}
        // the tree item's one thread (§1.3): the page clicked an item that has one
        Some(aib_store::Opened::Existing(found)) => {
            let (db, id) = (st.messages.clone(), found.thread_id.clone());
            let f2 = (*found).clone();
            let Some((all, kind)) = blocking(move || {
                let kind = db.categories_of(std::slice::from_ref(&id))?.remove(&id);
                Ok((members_now(&f2, db.thread_members(&id)?), kind))
            })
            .await
            else {
                return internal("thread not read");
            };
            // the item's thread keeps its own kind
            let mut o = thread_object(&found, &all);
            with_kind(&mut o, kind.as_ref());
            return Json(json!({ "thread": o, "existing": true })).into_response();
        }
        Some(aib_store::Opened::Taken) => {
            return refuse(StatusCode::CONFLICT, "thread_exists", "try again")
        }
        None => return internal("thread not written"),
    }
    if let Some(k) = category.clone() {
        let (db, id) = (st.messages.clone(), t.thread_id.clone());
        if blocking(move || db.thread_set_category(&id, &k))
            .await
            .is_none()
        {
            return internal("thread kind not written");
        }
    }
    // a tree item's thread shows who is in it now (its assignments), not the list sent
    let shown = match t.node {
        None => members.clone(),
        Some(_) => {
            let (t2, m2) = (t.clone(), members.clone());
            blocking(move || Ok(members_now(&t2, m2)))
                .await
                .unwrap_or_default()
        }
    };
    let _ = crate::messages::journal(
        st.store.clone(),
        st.messages.clone(),
        "thread.opened",
        json!({
            "thread_id": t.thread_id, "node": t.node, "opened_by": t.opened_by,
            "category": category,
            "members": shown.iter().map(|m| json!({"seat": m.member, "role": m.role})).collect::<Vec<_>>(),
        }),
        Vec::new(),
    )
    .await;
    let mut o = thread_object(&t, &shown);
    with_kind(&mut o, category.as_ref());
    // 043dd 02:48: `existing` is said both ways, so a page tells this aibd from one without
    // `reuse` (which leaves the key out)
    (
        StatusCode::CREATED,
        Json(json!({ "thread": o, "existing": false })),
    )
        .into_response()
}

/// The thread, its members' keys, and whether `s` may read and post in it.
pub(crate) async fn load(
    st: &AppState,
    s: &Sender,
    thread_id: &str,
) -> Result<(ThreadRow, Vec<Member>), Box<Response>> {
    let gone = || refuse(StatusCode::NOT_FOUND, "thread_not_found", "no such thread");
    if !thread_id_ok(thread_id) {
        return Err(Box::new(gone()));
    }
    let (db, id) = (st.messages.clone(), thread_id.to_owned());
    let got = blocking(move || {
        let Some(t) = db.thread_get(&id)? else {
            return Ok(None);
        };
        let m = members_now(&t, db.thread_members(&id)?);
        Ok(Some((t, m)))
    })
    .await;
    let Some(Some((t, members))) = got else {
        return Err(Box::new(gone()));
    };
    if !s.is_person() && !members.iter().any(|m| m.member == s.key) {
        return Err(Box::new(gone()));
    }
    Ok((t, members))
}

/// `thread_send {thread_id, body, to?, idem_key?}`.
pub async fn send_as(st: AppState, s: Sender, body: Value) -> Response {
    // N-ws-doc-link (5d33a CHECKLIST-doc-link D2): a post's document is registered by aibd or
    // the import, never by the sender, or a seat could hand the person's page any file
    if body.get("doc").is_some() {
        return refuse(
            StatusCode::BAD_REQUEST,
            "unknown_field",
            "doc is not set by a post; aibd registers it",
        );
    }
    let (Some(thread_id), Some(text)) = (
        body.get("thread_id").and_then(Value::as_str),
        body.get("body").and_then(Value::as_str),
    ) else {
        return bad("body must be {type, thread_id, body}");
    };
    // §17-4 / 5d33a CHECKLIST-doc-link D2: a post's doc and source are written by aibd and
    // its import only; a page or a seat cannot name a file for the person's page to read
    if ["doc", "source", "auto", "reply_to"]
        .iter()
        .any(|k| body.get(*k).is_some())
    {
        return refuse(
            StatusCode::BAD_REQUEST,
            "field_not_allowed",
            "doc and source are written by aibd, not sent",
        );
    }
    if text.trim().is_empty() || text.len() > BODY_MAX {
        return refuse(
            StatusCode::PAYLOAD_TOO_LARGE,
            "text_too_long",
            "a post is 1 byte to 32 KiB",
        );
    }
    let idem = match body.get("idem_key") {
        None | Some(Value::Null) => None,
        Some(Value::String(k))
            if (1..=crate::messages::IDEM_MAX).contains(&k.len())
                && k.bytes().all(|b| b.is_ascii_graphic()) =>
        {
            Some(k.clone())
        }
        Some(_) => return bad("idem_key is 1–64 printable characters"),
    };
    let (t, members) = match load(&st, &s, thread_id).await {
        Ok(x) => x,
        Err(r) => return *r,
    };
    let keys: Vec<String> = members.iter().map(|m| m.member.clone()).collect();
    let (to, cc) = match split_to(&keys, &s.key, body.get("to")) {
        Ok(x) => x,
        Err(code) => {
            return refuse(
                StatusCode::BAD_REQUEST,
                code,
                "to is \"all\" or a list of the thread's member seats; a seat that is not a member cannot be called in by a seat: ask the person in this thread to @ it in (the page puts it on the item)",
            )
        }
    };
    // §5: @all is every seat present (running); the idle members are only told (5d33a
    // PREREAD 2feffc7b N1: calling them would wake them)
    let (to, cc) = if body.get("to").and_then(Value::as_str) == Some("all") {
        all_present(to, cc, &st.sessions.live_seats())
    } else {
        (to, cc)
    };
    let sha = digest(text);
    // the same key from the same sender: the first post, nothing written again
    if let Some(k) = &idem {
        let (db, o, k2) = (st.messages.clone(), s.key.clone(), k.clone());
        match blocking(move || db.by_idem(&o, &k2)).await {
            Some(Some(r)) if r.thread_id == t.thread_id && r.body_sha256 == sha => {
                return answer_post(&st, &s, r, StatusCode::OK).await
            }
            Some(Some(_)) => {
                return refuse(
                    StatusCode::CONFLICT,
                    "idem_conflict",
                    "this idem_key was used for another text",
                )
            }
            Some(None) => {}
            None => return internal("messages not read"),
        }
    }
    let (db, tid) = (st.messages.clone(), t.thread_id.clone());
    let at = now_unix();
    let Some(recent) = blocking(move || db.recent_posts(&tid, QUIET_LIMIT as u32)).await else {
        return internal("thread not read");
    };
    if let Err(code) = pace(&recent, at, s.is_person()) {
        return refuse(
            StatusCode::TOO_MANY_REQUESTS,
            code,
            if code == "thread_needs_a_person" {
                "seats wait for a person in this thread"
            } else {
                "this thread takes 20 posts a minute"
            },
        );
    }
    let Some(metas) = seat_metas(&st).await else {
        return internal("seats not read");
    };
    let Ok(post_id) = crate::sessions::new_id("q_") else {
        return internal("no message id");
    };
    let now = crate::sessions::now_rfc3339();
    let kind = if s.is_person() { "human" } else { "seat" };
    let row =
        |id: String, to_key: String, item: Map<String, Value>, idem: Option<String>| MessageRow {
            message_id: id,
            thread_id: t.thread_id.clone(),
            from_key: s.key.clone(),
            to_key,
            created: now.clone(),
            created_unix: at,
            updated: now.clone(),
            rev: 1,
            item,
            body: Some(text.to_owned()),
            body_sha256: sha.clone(),
            len: i64::try_from(text.len()).unwrap_or(i64::MAX),
            idem_owner: idem.as_ref().map(|_| s.key.clone()),
            idem_key: idem,
            journaled: false,
        };
    let base = |id: &str, status: &str| {
        let mut item = aib_dmq::norm::v2_defaults();
        for (k, v) in [
            ("id", json!(id)),
            ("status", json!(status)),
            ("talk_kind", json!("say")),
            ("sender_seat", json!(s.key)),
            ("sender_kind", json!(kind)),
            ("thread", json!(t.thread_id)),
            ("sent_at", json!(at)),
        ] {
            item.insert(k.into(), v);
        }
        item
    };
    let mut post_item = base(&post_id, "delivered");
    post_item.insert("to".into(), json!(to));
    post_item.insert("cc".into(), json!(cc));
    post_item.insert("node".into(), json!(t.node));
    // 5d33a PREREAD a71bbbdb M1: who may reach a dgrs seat is judged again where a post is
    // read later (catch-up): whether its sender was safe, and a user folder page's words
    // are not the person's
    post_item.insert("sender_safe".into(), json!(s.safe));
    post_item.insert("user_page".into(), json!(s.user_page));
    if let Some(origin) = &s.auto_reply_to {
        let auto = if s.auto_failed.is_some() {
            "turn_failed"
        } else {
            "turn_end"
        };
        post_item.insert("auto".into(), json!(auto));
        post_item.insert("reply_to".into(), json!(origin));
        if let Some(why) = &s.auto_failed {
            post_item.insert("failed".into(), json!(why));
        }
    }
    let post = row(post_id.clone(), t.thread_id.clone(), post_item, idem);
    let mut deliveries = Vec::new();
    for seat in &to {
        let Ok(id) = crate::sessions::new_id("q_") else {
            return internal("no message id");
        };
        let mode = metas
            .iter()
            .find(|m| format!("seat:{}", m.seat_id) == *seat)
            .and_then(|m| m.mode.clone());
        let mut item = base(&id, "queued");
        item.insert("post_id".into(), json!(post_id));
        item.insert("mode".into(), json!(mode));
        item.insert("delivery".into(), json!("pending"));
        // the called seat's header names a user folder page as 用戶頁 (5d33a 761b8e22 L1)
        item.insert("user_page".into(), json!(s.user_page));
        if let Some(code) = unreachable(s.safe, mode.as_deref()) {
            item.insert("failed".into(), json!(true));
            item.insert("delivery".into(), json!("refused"));
            item.insert("failed_code".into(), json!(code));
        }
        deliveries.push(row(id, seat.clone(), item, None));
    }
    let (db, p2, d2) = (st.messages.clone(), post.clone(), deliveries.clone());
    let (post, fresh) = match blocking(move || db.post_insert(p2, d2)).await {
        Some(Inserted::New) => (post, true),
        Some(Inserted::Existing(r)) => (*r, false),
        None => return internal("post not written"),
    };
    if fresh {
        let mut rows = vec![post.clone()];
        rows.extend(deliveries.iter().cloned());
        let _ = crate::messages::journal(
            st.store.clone(),
            st.messages.clone(),
            "thread.posted",
            json!({ "thread_id": t.thread_id, "node": t.node, "post_id": post.message_id,
                    "to": to, "cc": cc }),
            rows,
        )
        .await;
        // the called seats that run get it now; the rest at their start (seat side)
        for seat in &to {
            let id = seat.trim_start_matches("seat:").to_owned();
            tokio::spawn(crate::seat_send::redeliver(st.clone(), id));
        }
        // 0eed7 VERIFY-turnfail L1: a called seat that is not running says so in the thread;
        // only for a new post (a resend of the same idem_key is not fresh: no second note)
        let live = st.sessions.seats_up();
        let asleep: Vec<String> = deliveries
            .iter()
            .filter(|d| d.item.get("delivery") == Some(&json!("pending")))
            .map(|d| d.to_key.trim_start_matches("seat:").to_owned())
            .filter(|id| !live.contains(id))
            .collect();
        if !asleep.is_empty() {
            not_running(&st, &t.thread_id, &post.message_id, &asleep).await;
        }
    }
    answer_post(&st, &s, post, StatusCode::CREATED).await
}

/// The words of the notice that called seats are not running.
pub fn not_running_text(seats: &[String]) -> String {
    let codes: Vec<String> = seats.iter().map(|s| crate::seats::seat_code(s)).collect();
    format!("{} 沒在跑,開了才會收到", codes.join("、"))
}

/// A post from aibd itself (`system:aibd`, `auto: "not_running"`, calls nobody) under the post
/// `reply_to`: the seats it called are not running, they get it when they start.
async fn not_running(st: &AppState, thread_id: &str, reply_to: &str, seats: &[String]) {
    let text = not_running_text(seats);
    let _ = system_post(
        st,
        thread_id,
        reply_to,
        &text,
        "not_running",
        &[("not_running", json!(seats))],
    )
    .await;
}

/// A post from aibd itself (`system:aibd`, calls nobody) in `thread_id` under `reply_to`,
/// marked `auto` (`not_running`; `decided`: a person answered a seat's ask_person card,
/// [`crate::asks`]). `false` = not written.
pub(crate) async fn system_post(
    st: &AppState,
    thread_id: &str,
    reply_to: &str,
    text: &str,
    auto: &str,
    extra: &[(&str, Value)],
) -> bool {
    let Ok(id) = crate::sessions::new_id("q_") else {
        return false;
    };
    let (now, at) = (crate::sessions::now_rfc3339(), now_unix());
    let mut item = aib_dmq::norm::v2_defaults();
    for (k, v) in [
        ("id", json!(id)),
        ("status", json!("delivered")),
        ("talk_kind", json!("say")),
        ("sender_seat", json!(SYSTEM_KEY)),
        ("sender_kind", json!("system")),
        ("thread", json!(thread_id)),
        ("sent_at", json!(at)),
        ("to", json!([])),
        ("auto", json!(auto)),
        ("reply_to", json!(reply_to)),
    ] {
        item.insert(k.into(), v);
    }
    for (k, v) in extra {
        item.insert((*k).into(), v.clone());
    }
    let sha = digest(text);
    let row = MessageRow {
        message_id: id,
        thread_id: thread_id.to_owned(),
        from_key: SYSTEM_KEY.into(),
        to_key: thread_id.to_owned(),
        created: now.clone(),
        created_unix: at,
        updated: now,
        rev: 1,
        item,
        body: Some(text.to_owned()),
        body_sha256: sha,
        len: i64::try_from(text.len()).unwrap_or(i64::MAX),
        idem_owner: None,
        idem_key: None,
        journaled: false,
    };
    let db = st.messages.clone();
    blocking(move || db.post_insert(row, Vec::new()))
        .await
        .is_some()
}

/// The sender of aibd's own notices in a thread.
pub const SYSTEM_KEY: &str = "system:aibd";

async fn answer_post(st: &AppState, s: &Sender, post: MessageRow, status: StatusCode) -> Response {
    let (db, tid, pid) = (
        st.messages.clone(),
        post.thread_id.clone(),
        vec![post.message_id.clone()],
    );
    let deliveries = blocking(move || db.deliveries_of(&tid, &pid))
        .await
        .unwrap_or_default();
    (
        status,
        Json(json!({ "post": post_object(&post, None, &deliveries, &[], &s.key, s.with_body) })),
    )
        .into_response()
}

/// `thread_read {thread_id, message_id}`: the reader's mark in the thread moves to that post
/// (never back); the page shows each post's readers from these marks. A seat's own delivery
/// rows are moved by the seat side (message_read); this is the timeline's mark only.
pub async fn read_as(st: AppState, s: Sender, body: Value) -> Response {
    let (Some(thread_id), Some(id)) = (
        body.get("thread_id").and_then(Value::as_str),
        body.get("message_id").and_then(Value::as_str),
    ) else {
        return bad("body must be {type, thread_id, message_id}");
    };
    // 5d33a 2a L1: a user folder page reads counts and state; it does not mark the person read
    if s.is_person() && s.safe {
        return refuse(
            StatusCode::FORBIDDEN,
            "read_built_in_page_only",
            "the person marks a thread read on the built-in page",
        );
    }
    let (t, _) = match load(&st, &s, thread_id).await {
        Ok(x) => x,
        Err(r) => return *r,
    };
    let (db, tid, me, id) = (
        st.messages.clone(),
        t.thread_id.clone(),
        s.key.clone(),
        id.to_owned(),
    );
    let got = blocking(move || {
        let Some(p) = db
            .get(&id)?
            .filter(|p| p.thread_id == tid && p.to_key == tid)
        else {
            return Ok(None);
        };
        let at = db.position(&p.message_id)?;
        let now = db.read_mark(&tid, &me)?;
        let now_at = match &now {
            Some(m) => db.position(m)?,
            None => None,
        };
        let ahead = matches!((now_at, at), (Some(n), Some(a)) if n >= a);
        if !ahead {
            db.set_read_mark(&tid, &me, &p.message_id)?;
        }
        Ok(Some(if ahead { now } else { Some(p.message_id) }))
    })
    .await;
    match got {
        Some(Some(upto)) => {
            Json(json!({ "thread_id": t.thread_id, "read_upto": upto })).into_response()
        }
        Some(None) => refuse(
            StatusCode::NOT_FOUND,
            "message_not_found",
            "no such post in this thread",
        ),
        None => internal("read mark not written"),
    }
}

/// One thread with its posts, oldest first, at most [`aib_store::PAGE_MAX`] before `cursor`.
pub async fn view_as(st: AppState, s: Sender, thread_id: &str, cursor: Option<String>) -> Response {
    let (t, members) = match load(&st, &s, thread_id).await {
        Ok(x) => x,
        Err(r) => return *r,
    };
    let dgrs_reader = !s.is_person() && !s.safe;
    let switch_on = crate::seat_input::dgrs_input_on();
    let (db, tid, me) = (st.messages.clone(), t.thread_id.clone(), s.key.clone());
    let got = blocking(move || {
        let (posts, more) = db.posts_page(&tid, cursor.as_deref(), aib_store::PAGE_MAX)?;
        let ids: Vec<String> = posts.iter().map(|p| p.message_id.clone()).collect();
        let deliveries = db.deliveries_of(&tid, &ids)?;
        let mark = db.read_mark(&tid, &me)?;
        let marks = db.read_marks_of(&tid)?;
        let pos = posts
            .iter()
            .map(|p| db.position(&p.message_id))
            .collect::<aib_store::Result<Vec<_>>>()?;
        Ok((posts, more, deliveries, mark, marks, pos))
    })
    .await;
    let Some((posts, more, deliveries, mark, marks, pos)) = got else {
        return internal("thread not read");
    };
    let mut out = thread_object(&t, &members);
    let (db, id) = (st.messages.clone(), t.thread_id.clone());
    let kind = blocking(move || Ok(db.categories_of(std::slice::from_ref(&id))?.remove(&id)))
        .await
        .flatten();
    with_kind(&mut out, kind.as_ref());
    if let Some(o) = out.as_object_mut() {
        o.insert(
            "posts".into(),
            json!(posts
                .iter()
                .zip(&pos)
                .map(|(p, at)| {
                    // a dgrs seat reading: a safe sender's text is {len, sha256} only
                    let body = s.with_body && (!dgrs_reader || dgrs_reads(p, switch_on));
                    post_object(p, *at, &deliveries, &marks, &s.key, body)
                })
                .collect::<Vec<_>>()),
        );
        if let Some(m) = mark {
            o.insert("read_upto".into(), json!(m));
        }
        if more {
            if let Some(first) = posts.first() {
                o.insert("next".into(), json!(first.message_id));
            }
        }
    }
    Json(out).into_response()
}

/// Which kind a list asks for: one of [`aib_store::CATEGORIES`], `none` (threads with no
/// kind), or every thread (left out). Anything else is refused.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Kind {
    Every,
    None,
    Of(String),
}

pub fn kind_asked(q: Option<&str>) -> Option<Kind> {
    match q {
        None | Some("") => Some(Kind::Every),
        Some("none") => Some(Kind::None),
        Some(k) if aib_store::CATEGORIES.contains(&k) => Some(Kind::Of(k.to_owned())),
        Some(_) => None,
    }
}

/// `counts` of a list (§17-2: the page shows them on its tabs, it does not count): `all`, each
/// kind, and `none`; every kind is there, 0 when none.
pub fn counts_object(kinds: &[(String, u64)]) -> Value {
    let mut o = Map::new();
    let all: u64 = kinds.iter().map(|(_, n)| n).sum();
    o.insert("all".into(), json!(all));
    for k in aib_store::CATEGORIES {
        let n = kinds.iter().find(|(x, _)| x == k).map_or(0, |(_, n)| *n);
        o.insert(k.into(), json!(n));
    }
    let none = kinds
        .iter()
        .find(|(x, _)| x.is_empty())
        .map_or(0, |(_, n)| *n);
    o.insert("none".into(), json!(none));
    Value::Object(o)
}

/// Threads newest first: of one item with `node`; a seat sees only those it is a member of.
/// `category` keeps one kind (`none` = no kind); `counts` are of what this reader may read.
pub async fn list_as(
    st: AppState,
    s: Sender,
    node: Option<String>,
    limit: Option<u32>,
    category: Option<String>,
    seat: Option<String>,
) -> Response {
    let Some(kind) = kind_asked(category.as_deref()) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "category_unknown",
            "category is council, summary, handoff, drive, shadow or none",
        );
    };
    if let Some(code) = &seat {
        if !seat_code_ok(code) {
            return refuse(
                StatusCode::BAD_REQUEST,
                "seat_unknown",
                "seat is a seat code: five lower hex digits",
            );
        }
    }
    // a seat token reads only its own threads: no seat= there
    let seat = seat.filter(|_| s.is_person());
    let member = (!s.is_person()).then(|| s.key.clone());
    let db = st.messages.clone();
    let lim = limit.unwrap_or(aib_store::PAGE_MAX);
    let got = blocking(move || {
        let (ts, counts) = match (&member, &seat) {
            // a page asking for one seat (N-web-dmbridge, 043dd 01:39): the threads it is in and
            // its mail-import threads, kept and counted here
            (None, Some(code)) => {
                let all = db.threads_of_seat(code, aib_store::PAGE_MAX)?;
                let all: Vec<ThreadRow> = all
                    .into_iter()
                    .filter(|t| node.is_none() || t.node == node)
                    .collect();
                let ids: Vec<String> = all.iter().map(|t| t.thread_id.clone()).collect();
                let kinds = db.categories_of(&ids)?;
                let mut counts: Vec<(String, u64)> = Vec::new();
                for t in &all {
                    let k = kinds.get(&t.thread_id).cloned().unwrap_or_default();
                    match counts.iter_mut().find(|(x, _)| *x == k) {
                        Some((_, n)) => *n += 1,
                        None => counts.push((k, 1)),
                    }
                }
                let kept = all
                    .into_iter()
                    .filter(|t| match &kind {
                        Kind::Every => true,
                        Kind::None => !kinds.contains_key(&t.thread_id),
                        Kind::Of(k) => kinds.get(&t.thread_id) == Some(k),
                    })
                    .take(usize::try_from(lim.clamp(1, aib_store::PAGE_MAX)).unwrap_or(1))
                    .collect();
                (kept, counts)
            }
            // a page reads every thread: the store picks and counts
            (None, None) => {
                let ts = match &kind {
                    Kind::Every => db.threads_list(node.as_deref(), None, lim)?,
                    Kind::None => db.threads_of_kind(node.as_deref(), None, lim)?,
                    Kind::Of(k) => db.threads_of_kind(node.as_deref(), Some(k), lim)?,
                };
                (ts, db.kind_counts(node.as_deref())?)
            }
            // a seat: only its own threads, then kept and counted here
            (Some(me), _) => {
                let mine = threads_of(&db, Some(me), node.as_deref(), aib_store::PAGE_MAX)?;
                let ids: Vec<String> = mine.iter().map(|t| t.thread_id.clone()).collect();
                let kinds = db.categories_of(&ids)?;
                let mut counts: Vec<(String, u64)> = Vec::new();
                for t in &mine {
                    let k = kinds.get(&t.thread_id).cloned().unwrap_or_default();
                    match counts.iter_mut().find(|(x, _)| *x == k) {
                        Some((_, n)) => *n += 1,
                        None => counts.push((k, 1)),
                    }
                }
                let kept = mine
                    .into_iter()
                    .filter(|t| match &kind {
                        Kind::Every => true,
                        Kind::None => !kinds.contains_key(&t.thread_id),
                        Kind::Of(k) => kinds.get(&t.thread_id) == Some(k),
                    })
                    .take(usize::try_from(lim.clamp(1, aib_store::PAGE_MAX)).unwrap_or(1))
                    .collect();
                (kept, counts)
            }
        };
        let ids: Vec<String> = ts.iter().map(|t: &ThreadRow| t.thread_id.clone()).collect();
        let kinds = db.categories_of(&ids)?;
        let mut out = Vec::new();
        for t in ts {
            let m = members_now(&t, db.thread_members(&t.thread_id)?);
            let mut o = thread_object(&t, &m);
            with_kind(&mut o, kinds.get(&t.thread_id));
            out.push(o);
        }
        Ok((out, counts))
    })
    .await;
    match got {
        Some((v, counts)) => {
            Json(json!({ "threads": v, "counts": counts_object(&counts) })).into_response()
        }
        None => internal("threads not read"),
    }
}

/// A thread object's `category` (`null` = an ordinary thread).
fn with_kind(o: &mut Value, kind: Option<&String>) {
    if let Some(m) = o.as_object_mut() {
        m.insert("category".into(), json!(kind));
    }
}

/// A seat token's thread act (`thread_open`, `thread_send`, `thread_view`, `thread_read`,
/// `thread_list`: only the threads it is a member of, [`list_as`]).
pub async fn seat_act(st: AppState, seat_id: &str, body: Value) -> Response {
    let store = st.store.clone();
    let id = seat_id.to_owned();
    let meta = blocking(move || store.seat_meta(&id)).await.flatten();
    let s = Sender {
        key: format!("seat:{seat_id}"),
        safe: meta.as_ref().and_then(|m| m.mode.as_deref()) != Some("dgrs"),
        cabin: meta.and_then(|m| m.cabin),
        with_body: true,
        user_page: false,
        auto_reply_to: None,
        auto_failed: None,
    };
    match body.get("type").and_then(Value::as_str) {
        Some(OPEN) => open_as(st, s, body).await,
        Some(SEND) => send_as(st, s, body).await,
        Some(READ) => read_as(st, s, body).await,
        Some(LIST) => {
            let node = body.get("node").and_then(Value::as_str).map(str::to_owned);
            let limit = body
                .get("limit")
                .and_then(Value::as_u64)
                .map(|n| n.min(u64::from(aib_store::PAGE_MAX)) as u32);
            let category = body
                .get("category")
                .and_then(Value::as_str)
                .map(str::to_owned);
            list_as(st, s, node, limit, category, None).await
        }
        _ => {
            let Some(t) = body
                .get("thread_id")
                .and_then(Value::as_str)
                .map(str::to_owned)
            else {
                return bad("body must be {type, thread_id}");
            };
            let cursor = body
                .get("cursor")
                .and_then(Value::as_str)
                .map(str::to_owned);
            view_as(st, s, &t, cursor).await
        }
    }
}

/// G13: post `body` (a thread_send) as `seat_id`, marked as its turn answer brought back for
/// `reply_to` (aibd only; a seat or page cannot set it).
pub async fn seat_act_auto(
    st: AppState,
    seat_id: &str,
    body: Value,
    reply_to: &str,
    failed: Option<String>,
) -> Response {
    let store = st.store.clone();
    let id = seat_id.to_owned();
    let meta = blocking(move || store.seat_meta(&id)).await.flatten();
    let s = Sender {
        key: format!("seat:{seat_id}"),
        safe: meta.as_ref().and_then(|m| m.mode.as_deref()) != Some("dgrs"),
        cabin: meta.and_then(|m| m.cabin),
        with_body: true,
        user_page: false,
        auto_reply_to: Some(reply_to.to_owned()),
        auto_failed: failed,
    };
    send_as(st, s, body).await
}

/// Whether this page reads the two-seat thread `thread_id` (`seat:<a>~seat:<b>`): a
/// built-in page (never a user folder page, nor any page while one serves `/`), and both
/// seats are of one cabin (519d0 11-18 裁定 2).
pub async fn page_reads_seat_pair(
    st: &AppState,
    page: &crate::pairing::PageAccess,
    thread_id: &str,
) -> bool {
    let Some((a, b)) = crate::messages::ends(thread_id) else {
        return false;
    };
    let (Some(a), Some(b)) = (a.strip_prefix("seat:"), b.strip_prefix("seat:")) else {
        return false;
    };
    if crate::seat_input::held_as_user_page(page).await {
        return false;
    }
    let Some(metas) = seat_metas(st).await else {
        return false;
    };
    let cabin = |id: &str| {
        metas
            .iter()
            .find(|m| m.seat_id == id)
            .map(|m| m.cabin.clone())
    };
    matches!((cabin(a), cabin(b)), (Some(x), Some(y)) if x == y)
}

/// The sender a page is: the person; safe when it is (or `/` is served by) a user page, or
/// when its pairing was carried over an update (S89-pair-carry DECISION §2: it reaches safe
/// seats only, in a conversation too; 5d33a REVIEW-pair-carry-6f21b9a1 N1).
pub async fn page_sender(page: &crate::pairing::PageAccess) -> Sender {
    let user = crate::seat_input::held_as_user_page(page).await;
    Sender {
        key: crate::contacts_api::page_key(&crate::guard::Caller::Page(*page)).unwrap_or_default(),
        safe: user || page.carried,
        cabin: None,
        with_body: !user,
        user_page: user,
        auto_reply_to: None,
        auto_failed: None,
    }
}

#[derive(Debug, Default, serde::Deserialize)]
pub struct ListQuery {
    node: Option<String>,
    limit: Option<u32>,
    category: Option<String>,
    /// N-web-dmbridge: one seat's threads (a seat code)
    seat: Option<String>,
}

/// A seat code as the ledgers write it: five lower hex digits ([`crate::seats::is_code`]).
fn seat_code_ok(s: &str) -> bool {
    crate::seats::is_code(s)
}

/// `GET /api/v1/threads?node=&limit=&category=&seat=` (a paired page).
pub async fn list(
    axum::extract::State(st): axum::extract::State<AppState>,
    axum::Extension(c): axum::Extension<crate::guard::Caller>,
    axum::extract::Query(q): axum::extract::Query<ListQuery>,
) -> Response {
    let crate::guard::Caller::Page(page) = c else {
        return refuse(
            StatusCode::FORBIDDEN,
            "threads_need_a_page",
            "threads are read by a paired page",
        );
    };
    list_as(
        st,
        page_sender(&page).await,
        q.node,
        q.limit,
        q.category,
        q.seat,
    )
    .await
}

#[cfg(test)]
mod tests {
    use super::*;

    fn keys(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| (*s).to_owned()).collect()
    }

    #[test]
    fn a_post_calls_only_members_and_tells_the_rest() {
        let m = keys(&["seat:a", "seat:b", "seat:c"]);
        assert_eq!(
            split_to(&m, "human:0123456789abcdef", None),
            Ok((vec![], keys(&["seat:a", "seat:b", "seat:c"])))
        );
        assert_eq!(
            split_to(&m, "seat:a", Some(&json!("all"))),
            Ok((keys(&["seat:b", "seat:c"]), vec![]))
        );
        assert_eq!(
            split_to(&m, "seat:a", Some(&json!(["c", "seat:a"]))),
            Ok((keys(&["seat:c"]), keys(&["seat:b"])))
        );
        assert_eq!(
            split_to(&m, "seat:a", Some(&json!(["seat:z"]))),
            Err("to_not_a_member")
        );
        assert_eq!(
            split_to(&m, "seat:a", Some(&json!(["a/b"]))),
            Err("to_not_a_seat")
        );
        assert_eq!(
            split_to(&m, "seat:a", Some(&json!(3))),
            Err("to_not_a_seat")
        );
    }

    #[test]
    fn seats_stop_after_twenty_posts_without_a_person_and_a_minute_takes_twenty() {
        let seats: Vec<(i64, String)> = (0..20).map(|i| (1000 - i * 100, "seat".into())).collect();
        assert_eq!(pace(&seats, 5000, false), Err("thread_needs_a_person"));
        assert_eq!(pace(&seats, 5000, true), Ok(()));
        let mut one_person = seats.clone();
        one_person[19].1 = "human".into();
        assert_eq!(pace(&one_person, 5000, false), Ok(()));
        let burst: Vec<(i64, String)> = (0..20).map(|_| (5000, "human".into())).collect();
        assert_eq!(pace(&burst, 5030, true), Err("thread_rate_limited"));
        assert_eq!(pace(&burst, 5060, true), Ok(()));
    }

    #[test]
    fn a_safe_sender_never_reaches_a_dgrs_seat() {
        assert_eq!(
            unreachable(true, Some("dgrs")),
            Some("safe_cannot_send_to_dgrs")
        );
        assert_eq!(unreachable(false, Some("dgrs")), None);
        assert_eq!(unreachable(true, Some("safe")), None);
        assert_eq!(unreachable(true, None), None);
    }

    #[test]
    fn a_thread_id_is_t_and_sixteen_lowercase_hex() {
        assert!(thread_id_ok("t_0123456789abcdef"));
        for bad in [
            "t_0123456789ABCDEF",
            "t_0123",
            "seat:a~seat:b",
            "x_0123456789abcdef",
        ] {
            assert!(!thread_id_ok(bad), "{bad}");
        }
    }

    /// G13 (0eed7 VERIFY-g13 L1): a post brought back at a turn's end says so on the page.
    #[test]
    fn the_not_running_note_names_seats_by_their_code() {
        assert_eq!(
            not_running_text(&["seat_1bc99b1f0a".into(), "7ebca".into()]),
            "1bc99、7ebca 沒在跑,開了才會收到"
        );
    }

    #[test]
    fn a_brought_back_answer_shows_auto_and_reply_to() {
        let mut item = serde_json::Map::new();
        item.insert("auto".into(), json!("turn_end"));
        item.insert("reply_to".into(), json!("msg_0"));
        item.insert("failed".into(), json!("Not logged in"));
        let p = MessageRow {
            message_id: "msg_1".into(),
            thread_id: "t_1".into(),
            from_key: "seat:a".into(),
            to_key: "t_1".into(),
            created: String::new(),
            created_unix: 0,
            updated: String::new(),
            rev: 1,
            item,
            body: Some("x".into()),
            body_sha256: String::new(),
            len: 1,
            idem_owner: None,
            idem_key: None,
            journaled: false,
        };
        let o = post_object(&p, None, &[], &[], "human:0123456789abcdef", true);
        assert_eq!(o["auto"], json!("turn_end"), "reason: auto_not_shown");
        assert_eq!(o["reply_to"], json!("msg_0"));
        assert_eq!(
            o["failed"],
            json!("Not logged in"),
            "reason: failed_not_shown"
        );
    }

    /// 0eed7 VERIFY-oneline2 M1: an imported summary's post names its file for the page.
    #[test]
    fn an_imported_documents_post_names_its_file() {
        let mut item = serde_json::Map::new();
        item.insert(
            "doc".into(),
            // a name with a path in it is not one the doc wall opens (5d33a cc1: RG23)
            json!([{"root": "pending", "file": "SUMMARY-1d-043dd-1113.md", "title": "一天總結"},
                   {"root": "pending", "file": "SUMMARY-x/../../secret.md", "title": "x"}]),
        );
        let p = MessageRow {
            message_id: "msg_1".into(),
            thread_id: "t_1".into(),
            from_key: "seat:a".into(),
            to_key: "t_1".into(),
            created: String::new(),
            created_unix: 0,
            updated: String::new(),
            rev: 1,
            item,
            body: Some("x".into()),
            body_sha256: String::new(),
            len: 1,
            idem_owner: None,
            idem_key: None,
            journaled: false,
        };
        let o = post_object(&p, None, &[], &[], "human:0123456789abcdef", true);
        assert_eq!(
            o["doc"].as_array().map(Vec::len),
            Some(1),
            "reason: path_name_listed {o}"
        );
        assert_eq!(
            o["doc"][0]["file"],
            json!("SUMMARY-1d-043dd-1113.md"),
            "reason: doc_not_shown"
        );
        assert_eq!(o["doc"][0]["title"], json!("一天總結"));
        let o = post_object(&p, None, &[], &[], "human:0123456789abcdef", false);
        assert_eq!(
            o["doc"][0]["title"]["len"],
            json!(4),
            "reason: title_words_shown"
        );
        assert_eq!(
            (
                o["doc"][0].get("file"),
                &o["doc"][0]["n"],
                &o["doc"][0]["kind"]
            ),
            (None, &json!(0), &json!("SUMMARY")),
            "reason: file_name_on_user_page"
        );
        let mut other = p.clone();
        other.item.insert(
            "doc".into(),
            json!([{"file": "notes-a9136.md", "title": "x"}, {"file": "SUMMARY-x.md"}]),
        );
        let o = post_object(&other, None, &[], &[], "x", true);
        assert_eq!(
            o["doc"].as_array().map(Vec::len),
            Some(1),
            "reason: unlisted_doc_shown"
        );
        let mut one = p.clone();
        one.item.insert(
            "doc".into(),
            json!({"file": "HANDOFF-a9136-1117.md", "title": "交接"}),
        );
        assert_eq!(
            post_object(&one, None, &[], &[], "x", true)["doc"][0]["kind"],
            json!("HANDOFF"),
            "reason: one_doc_object_dropped"
        );
        let mut bare = p.clone();
        bare.item.clear();
        assert!(post_object(&bare, None, &[], &[], "x", true)
            .get("doc")
            .is_none());
    }

    /// 5d33a 2feffc7b M1: a dgrs seat reads a post's text only from a sender that was not
    /// safe, and only with the switch on; a post that does not say counts as safe.
    #[test]
    fn a_dgrs_seat_reads_no_safe_senders_text() {
        let post = |safe: Option<bool>| {
            let mut item = serde_json::Map::new();
            if let Some(b) = safe {
                item.insert("sender_safe".into(), json!(b));
            }
            MessageRow {
                message_id: "msg_1".into(),
                thread_id: "t_1".into(),
                from_key: "seat:a".into(),
                to_key: String::new(),
                created: String::new(),
                created_unix: 0,
                updated: String::new(),
                rev: 1,
                item,
                body: Some("x".into()),
                body_sha256: String::new(),
                len: 1,
                idem_owner: None,
                idem_key: None,
                journaled: false,
            }
        };
        assert!(dgrs_reads(&post(Some(false)), true));
        assert!(
            !dgrs_reads(&post(Some(true)), true),
            "reason: safe_text_to_dgrs"
        );
        assert!(
            !dgrs_reads(&post(None), true),
            "reason: unsaid_counts_unsafe"
        );
        assert!(
            !dgrs_reads(&post(Some(false)), false),
            "reason: switch_off_reads"
        );
    }

    /// 5d33a 2feffc7b N1 / SPEC §5: @all calls the seats present; the idle ones are told.
    #[test]
    fn at_all_calls_only_the_seats_running() {
        let (to, cc) = all_present(
            vec!["seat:a".into(), "seat:b".into()],
            vec!["seat:c".into()],
            &["a".into()],
        );
        assert_eq!(to, vec!["seat:a".to_owned()], "reason: idle_seat_called");
        assert_eq!(cc, vec!["seat:b".to_owned(), "seat:c".to_owned()]);
    }
}
