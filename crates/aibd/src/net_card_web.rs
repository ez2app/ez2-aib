//! The network exception card on the built-in page (IFACE-netcard-3e53b-1123 r1; f163c pre-read
//! of fc7f9 70cb243b5b L1; ebab8 11-23 08:58): `POST /api/v1/act` from the built-in page in this
//! pairing only.
//!
//! - `{"type":"net_card_list"}` → `{items:[…]}`: the cards waiting for the owner
//!   ([`crate::net_card::Card::shown`]).
//! - `{"type":"net_card_answer","request_id":…,"choice":…,"ticket"?}`: 拒絕 and (on an ordinary
//!   card) 只這一次 finish at once; any other yes raises the owner's window (202 with a ticket)
//!   and finishes only when the page sends the approved ticket back (choice (a) of r1).
//! - `{"type":"net_allow_list","seat_id"?}` / `{"type":"net_allow_revoke","seat_id","ip","port"}`:
//!   a seat's 「一直允許」 rows; taking one back needs no fingerprint (it only makes things
//!   stricter).
//! - `GET /api/v1/seats` carries `net_cards`, how many cards wait ([`waiting_now`]).
//!
//! What is not here yet: the proxy that holds a connection and makes a card, and so what a yes
//! does to a live connection (the answer is recorded and a 「一直允許」 row is written; nothing
//! is let through yet). Until then no card exists and `net_cards` is 0.
//! - The 「一直允許」 rows are kept in aibd's process only for now: they last until aibd restarts
//!   (f163c N2). Where they are stored for good (aib_store, bound to the seat) is decided with
//!   the proxy slice, and the window's words with it.

use std::collections::BTreeMap;
use std::net::{IpAddr, SocketAddr};
use std::sync::Mutex;

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::api::AppState;
use crate::net_card::{AllowList, Card, Cards, Lasts, Step};

pub const LIST: &str = "net_card_list";
pub const ANSWER: &str = "net_card_answer";
pub const ALLOW_LIST: &str = "net_allow_list";
pub const ALLOW_REVOKE: &str = "net_allow_revoke";

static CARDS: Mutex<Option<Cards>> = Mutex::new(None);
static ALLOW: Mutex<BTreeMap<String, AllowList>> = Mutex::new(BTreeMap::new());
static INVALID_ROW_LOG: Mutex<BTreeMap<(String, &'static str), u64>> = Mutex::new(BTreeMap::new());

fn should_log_invalid(ticket: &str, state: &'static str, now_unix: u64) -> bool {
    let Ok(mut g) = INVALID_ROW_LOG.lock() else {
        return false;
    };
    let key = (ticket.to_owned(), state);
    if g.get(&key).is_some_and(|&prev| now_unix < prev + 60) {
        return false;
    }
    if g.len() > 256 {
        g.clear();
    }
    g.insert(key, now_unix);
    true
}

fn now() -> u64 {
    crate::pair_carry::now_unix()
}

fn with_cards<T>(f: impl FnOnce(&mut Cards) -> T) -> Option<T> {
    let mut g = CARDS.lock().ok()?;
    Some(f(g.get_or_insert_with(Cards::default)))
}

/// A card made by aibd's proxy (or `net_ask`).
pub fn raise(card: Card) {
    with_cards(|c| c.add(card));
}

/// `net_cards` on `GET /api/v1/seats`: how many cards wait for the owner now.
pub fn waiting_now() -> usize {
    with_cards(|c| c.waiting(now())).unwrap_or(0)
}

/// Whether a seat's list has this address and port (the proxy asks before raising a card).
pub fn allowed(seat_id: &str, ip: IpAddr, port: u16) -> bool {
    ALLOW
        .lock()
        .ok()
        .and_then(|a| a.get(seat_id).map(|l| l.has(ip, port)))
        .unwrap_or(false)
}

fn refuse(status: StatusCode, reason: &str) -> Response {
    let message = match reason {
        "net_card_unknown" => "no card with that request_id",
        "net_card_used" => "that card was answered already",
        "net_card_expired" => "that card waited too long and was refused",
        "not_a_choice" => "that choice is not on this card",
        "not_on_list" => "that address and port is not on the seat's list",
        "body" => "the body does not have this act's fields",
        _ => "the network card could not be answered",
    };
    let code = match status {
        StatusCode::BAD_REQUEST => "invalid_request",
        StatusCode::NOT_FOUND => "not_found",
        StatusCode::CONFLICT => "conflict",
        StatusCode::INTERNAL_SERVER_ERROR => "internal",
        _ => "forbidden",
    };
    (
        status,
        Json(json!({"code": code, "reason": reason, "message": message,
            "request_id": crate::guard::request_id()})),
    )
        .into_response()
}

fn status_of(reason: &str) -> StatusCode {
    match reason {
        "net_card_unknown" | "not_on_list" => StatusCode::NOT_FOUND,
        "net_card_used" | "net_card_expired" => StatusCode::CONFLICT,
        _ => StatusCode::BAD_REQUEST,
    }
}

/// `net_card_list`.
pub async fn list() -> Response {
    match with_cards(|c| c.list(now())) {
        Some(items) => Json(json!({ "items": items })).into_response(),
        None => refuse(StatusCode::INTERNAL_SERVER_ERROR, "cards"),
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Answer {
    #[serde(rename = "type")]
    _kind: String,
    request_id: String,
    choice: String,
    #[serde(default)]
    ticket: Option<String>,
}

type SignedAlways = (String, String, Vec<u8>, Vec<u8>);

/// The card's answer through aibd's own owner check: `OP_NET_ALLOW_ALWAYS` requires a
/// hardware-signed ticket (`owner_ok::approved_signed_for`); others use `approved_for`.
fn step(req: &Answer, by: &str) -> Option<(Option<Card>, Step, Option<SignedAlways>)> {
    with_cards(|c| {
        let held = c.find(&req.request_id);
        let mut signed: Option<SignedAlways> = None;
        let step = c.answer(
            &req.request_id,
            &req.choice,
            req.ticket.as_deref(),
            now(),
            by,
            |t, ask| {
                if ask.operation == crate::net_card::OP_NET_ALLOW_ALWAYS {
                    let Some(sig) = crate::owner_ok::approved_signed_for(t, ask) else {
                        return false;
                    };
                    signed = Some((t.to_owned(), ask.resource.clone(), ask.params.clone(), sig));
                    true
                } else {
                    crate::owner_ok::approved_for(t, ask)
                }
            },
        );
        (held, step, signed)
    })
}

/// `net_card_answer`, for the built-in page `by`.
pub async fn answer(st: AppState, body: Value, by: String) -> Response {
    let Ok(req) = serde_json::from_value::<Answer>(body) else {
        return refuse(StatusCode::BAD_REQUEST, "body");
    };
    let Some((held, step, signed)) = step(&req, &by) else {
        return refuse(StatusCode::INTERNAL_SERVER_ERROR, "cards");
    };
    match step {
        Step::Ask(ask) => {
            let r = crate::repos::asked(&ask);
            // the card waits for the ticket only once the window is up (f163c L1): busy,
            // cooling, limited or no verification here leaves it to be answered again
            let window_up = r.status() == StatusCode::ACCEPTED;
            if window_up {
                if let Some(c) = crate::net_card::Choice::parse(&req.choice) {
                    with_cards(|cards| cards.opened(&req.request_id, c));
                }
            }
            r
        }
        Step::NotApproved => crate::repos::not_approved(),
        Step::Refused(why) => refuse(status_of(why), why),
        Step::Done(choice, yes) => {
            let mut listed = false;
            if let (Some(card), Some(yes)) = (&held, &yes) {
                if yes.lasts() == Lasts::Always {
                    if let Some((ticket, resource, params, sig)) = signed {
                        let row = aib_store::net_allow::NetAllowRow {
                            ticket,
                            by_key: by.clone(),
                            resource,
                            params,
                            sig,
                            approved_at: now() as i64,
                        };
                        match st.messages.net_allow_add(row) {
                            Ok(added) => listed = added,
                            Err(_) => return refuse(StatusCode::INTERNAL_SERVER_ERROR, "allow"),
                        }
                    }
                    if let Ok(mut a) = ALLOW.lock() {
                        let _ = a
                            .entry(card.seat_id.clone())
                            .or_insert_with(|| AllowList::new(&card.seat_id))
                            .add(card.dest, yes);
                    }
                }
            }
            crate::seats::side_event(
                &st,
                "net",
                "net.card_answered",
                json!({"request_id": req.request_id, "choice": choice.id(),
                    "let_through": yes.is_some(), "listed": listed, "by": by}),
            )
            .await;
            Json(json!({"done": true})).into_response()
        }
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AllowQuery {
    #[serde(rename = "type")]
    _kind: String,
    #[serde(default)]
    seat_id: Option<String>,
}

/// `net_allow_list`.
pub async fn allow_list(st: AppState, body: Value) -> Response {
    let Ok(q) = serde_json::from_value::<AllowQuery>(body) else {
        return refuse(StatusCode::BAD_REQUEST, "body");
    };
    let mut items = match ALLOW.lock() {
        Ok(a) => rows(&a, q.seat_id.as_deref()),
        Err(_) => return refuse(StatusCode::INTERNAL_SERVER_ERROR, "allow"),
    };
    let want_uid = match q.seat_id.as_deref() {
        Some(sid) => st
            .store
            .seat_meta(sid)
            .ok()
            .flatten()
            .and_then(|m| m.persona_uid),
        None => None,
    };
    let persona_active = match want_uid.as_deref() {
        Some(uid) => !crate::persona_edit::archived_uids(&st.messages, "persona")
            .unwrap_or_default()
            .contains(uid),
        None => true,
    };
    let own_ips = crate::host_addr::own_addresses().unwrap_or_default();
    let now_unix = now();
    let mut bad_events = Vec::new();
    if let Ok(db_rows) = st.messages.net_allow_list() {
        let is_revoked = |t: &str| st.messages.net_allow_is_revoked(t).unwrap_or(true);
        append_db_allow_rows(
            &mut items,
            &db_rows,
            q.seat_id.as_deref(),
            want_uid.as_deref(),
            persona_active,
            &own_ips,
            now_unix,
            &is_revoked,
            &crate::owner_ok::verify,
            &mut bad_events,
        );
    }
    for (ticket, state) in bad_events {
        if should_log_invalid(&ticket, state, now_unix) {
            crate::seats::side_event(
                &st,
                "net",
                "net.allow_row_invalid",
                json!({"ticket": ticket, "state": state}),
            )
            .await;
        }
    }
    Json(json!({ "items": items })).into_response()
}

#[allow(clippy::too_many_arguments)]
fn append_db_allow_rows(
    items: &mut Vec<Value>,
    db_rows: &[aib_store::net_allow::NetAllowRow],
    seat_id: Option<&str>,
    want_uid: Option<&str>,
    persona_active: bool,
    own_ips: &[IpAddr],
    now_unix: u64,
    is_revoked: &dyn Fn(&str) -> bool,
    verify_fn: &dyn Fn(&[u8], &[u8]) -> bool,
    bad_events: &mut Vec<(String, &'static str)>,
) {
    for r in db_rows {
        let Some((uid, canon, issued_at, until)) = crate::net_allow::parse_always_params(&r.params)
        else {
            continue;
        };
        if seat_id.is_some() && want_uid != Some(uid.as_str()) {
            continue;
        }
        let parsed_addr = canon.parse::<SocketAddr>().ok();
        let state = if is_revoked(&r.ticket) {
            "revoked_reappeared"
        } else if !crate::net_allow::row_sig_ok(r, verify_fn) {
            "bad_sig"
        } else if now_unix < issued_at
            || now_unix >= until
            || until <= issued_at
            || until - issued_at > crate::net_allow::MAX_DAYS_SECS
        {
            "expired"
        } else if parsed_addr.is_none_or(|sa| {
            crate::egress::verdict(&[sa.ip()], sa.port(), own_ips, &|_, _| true)
                != crate::egress::Verdict::Pass(sa.ip())
        }) {
            "class_changed"
        } else if !persona_active {
            "persona_inactive"
        } else {
            "ok"
        };
        if state != "ok" {
            bad_events.push((r.ticket.clone(), state));
        }
        let dup = parsed_addr.and_then(|sa| {
            let ip_str = sa.ip().to_string();
            let port_u64 = u64::from(sa.port());
            items.iter_mut().find(|existing| {
                existing.get("ip").and_then(Value::as_str) == Some(ip_str.as_str())
                    && existing.get("port").and_then(Value::as_u64) == Some(port_u64)
                    && (seat_id.is_none()
                        || existing.get("seat_id").and_then(Value::as_str) == seat_id)
            })
        });
        if let Some(existing) = dup {
            existing["ticket"] = json!(r.ticket);
            existing["persona_uid"] = json!(uid);
            existing["addr"] = json!(canon);
            existing["until"] = json!(until);
            existing["state"] = json!(state);
            continue;
        }
        let mut obj = json!({
            "ticket": r.ticket,
            "persona_uid": uid,
            "addr": canon,
            "until": until,
            "state": state,
        });
        if let Some(sa) = parsed_addr {
            obj["ip"] = json!(sa.ip().to_string());
            obj["port"] = json!(sa.port());
        }
        if let Some(sid) = seat_id {
            obj["seat_id"] = json!(sid);
        }
        items.push(obj);
    }
}

/// The list's rows for the page, of one seat or of all.
pub fn rows(a: &BTreeMap<String, AllowList>, seat: Option<&str>) -> Vec<Value> {
    a.values()
        .filter(|l| seat.is_none_or(|s| l.seat() == s))
        .flat_map(|l| {
            l.rows().map(move |at| {
                json!({"seat_id": l.seat(), "ip": at.ip().to_string(), "port": at.port()})
            })
        })
        .collect()
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Revoke {
    #[serde(rename = "type")]
    _kind: String,
    seat_id: String,
    ip: IpAddr,
    port: u16,
}

/// `net_allow_revoke`, for the built-in page `by`.
pub async fn allow_revoke(st: AppState, body: Value, by: String) -> Response {
    let Ok(r) = serde_json::from_value::<Revoke>(body) else {
        return refuse(StatusCode::BAD_REQUEST, "body");
    };
    let at = SocketAddr::new(r.ip, r.port);
    let want_canon = crate::grants::canon(at);
    let ts = now() as i64;
    let want_uid = st
        .store
        .seat_meta(&r.seat_id)
        .ok()
        .flatten()
        .and_then(|m| m.persona_uid);
    let mut db_revoked = false;
    if let Ok(rows) = st.messages.net_allow_list() {
        for row in rows {
            if let Some((uid, canon, _, _)) = crate::net_allow::parse_always_params(&row.params) {
                if want_uid.as_deref() == Some(uid.as_str()) && canon == want_canon {
                    match st.messages.net_allow_revoke(&row.ticket, ts) {
                        Ok(true) => db_revoked = true,
                        Ok(false) => {}
                        Err(_) => return refuse(StatusCode::INTERNAL_SERVER_ERROR, "allow"),
                    }
                }
            }
        }
    }
    let closed = ALLOW
        .lock()
        .ok()
        .and_then(|mut a| a.get_mut(&r.seat_id).and_then(|l| l.revoke(at, &[])));
    if closed.is_none() && !db_revoked {
        return refuse(StatusCode::NOT_FOUND, "not_on_list");
    }
    let closed_count = closed.map_or(0, |v| v.len());
    crate::seats::side_event(
        &st,
        "net",
        "net.allow_revoked",
        json!({"seat_id": r.seat_id, "ip": r.ip.to_string(), "port": r.port, "by": by}),
    )
    .await;
    Json(json!({"done": true, "closed": closed_count})).into_response()
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    fn held(id: &str) -> Card {
        Card {
            request_id: id.into(),
            seat_id: "a1b2c".into(),
            persona_uid: "p_1".into(),
            persona_name: "x".into(),
            dest_name: "db.lan".into(),
            dest: "10.0.0.5:5432".parse().unwrap(),
            source: "proxy",
            why: None,
            expires_at: now() + 100,
            high: false,
            listener: None,
        }
    }

    /// ebab8 11-23 09:11 standing rule: the answer goes through aibd's own owner check. In this
    /// test binary no owner is wired, so no ticket is ever approved: a ticket for a window that
    /// is up still does not finish the card.
    #[test]
    fn the_answer_finishes_only_through_aibds_own_owner_check() {
        raise(held("rq_u_owner"));
        with_cards(|c| c.opened("rq_u_owner", crate::net_card::Choice::Min15));
        let req = Answer {
            _kind: ANSWER.into(),
            request_id: "rq_u_owner".into(),
            choice: "min15".into(),
            ticket: Some("ot_forged".into()),
        };
        let (_, s, _) = step(&req, "page:1").unwrap();
        assert_eq!(
            s,
            Step::NotApproved,
            "reason: ticket_not_checked_by_owner_ok"
        );
    }

    #[test]
    fn the_page_sees_each_always_row_as_ip_and_a_numeric_port() {
        let yes = crate::net_card::decide(Some("always"), crate::net_card::Owner::Yes).unwrap();
        let mut a = BTreeMap::new();
        let mut l = AllowList::new("a1b2c");
        assert!(l.add("10.0.0.5:5432".parse().unwrap(), &yes));
        assert!(l.add("[fd00::7]:443".parse().unwrap(), &yes));
        a.insert("a1b2c".to_owned(), l);
        a.insert("d3e4f".to_owned(), AllowList::new("d3e4f"));
        let all = rows(&a, None);
        assert_eq!(all.len(), 2);
        assert_eq!(all[0]["port"], 5432, "reason: port_not_a_number");
        // IPv6 without brackets (IFACE r1)
        assert_eq!(all[1]["ip"], "fd00::7", "reason: ipv6_bracketed");
        assert!(rows(&a, Some("d3e4f")).is_empty());
    }

    #[test]
    fn db_allow_rows_emit_all_states_and_never_hide_invalid_rows() {
        let mk = |ticket: &str, uid: &str, canon: &str, until: u64, sig: Vec<u8>| {
            aib_store::net_allow::NetAllowRow {
                ticket: ticket.into(),
                by_key: "owner".into(),
                resource: canon.into(),
                params: format!("aibd/net-allow/v1\n{uid}\n{canon}\n1000\n{until}\nrq_1\n")
                    .into_bytes(),
                sig,
                approved_at: 1000,
            }
        };
        let rows = vec![
            mk("tk_ok", "p_1", "8.8.8.8:443", 5000, vec![1]),
            mk("tk_exp", "p_1", "10.0.0.6:5432", 1500, vec![1]),
            mk("tk_bad", "p_1", "10.0.0.7:5432", 5000, vec![9]),
            mk("tk_pub", "p_1", "10.0.0.5:5432", 5000, vec![1]),
            mk("tk_rev", "p_1", "10.0.0.8:5432", 5000, vec![1]),
            mk("tk_other", "p_2", "10.0.0.9:5432", 5000, vec![1]),
        ];
        let mut out = Vec::new();
        let mut bad_events = Vec::new();
        append_db_allow_rows(
            &mut out,
            &rows,
            Some("a1b2c"),
            Some("p_1"),
            true,
            &[],
            2000,
            &|t| t == "tk_rev",
            &|_, sig| sig == [1],
            &mut bad_events,
        );
        assert_eq!(out.len(), 5);
        assert_eq!(out[0]["state"], "ok");
        assert_eq!(out[0]["ip"], "8.8.8.8");
        assert_eq!(out[0]["port"], 443);
        assert_eq!(out[1]["state"], "expired");
        assert_eq!(out[2]["state"], "bad_sig");
        assert_eq!(out[3]["state"], "class_changed");
        assert_eq!(out[4]["state"], "revoked_reappeared");
        assert_eq!(bad_events.len(), 4);

        let mut inactive = Vec::new();
        let mut ev2 = Vec::new();
        append_db_allow_rows(
            &mut inactive,
            &rows[..1],
            Some("a1b2c"),
            Some("p_1"),
            false,
            &[],
            2000,
            &|_| false,
            &|_, sig| sig == [1],
            &mut ev2,
        );
        assert_eq!(inactive[0]["state"], "persona_inactive");

        let mut none = Vec::new();
        let mut ev3 = Vec::new();
        append_db_allow_rows(
            &mut none,
            &rows,
            Some("a1b2c"),
            None,
            true,
            &[],
            2000,
            &|_| false,
            &|_, _| true,
            &mut ev3,
        );
        assert!(none.is_empty());
    }
}
