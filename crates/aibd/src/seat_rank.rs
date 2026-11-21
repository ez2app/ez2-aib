//! Who may mark a letter urgent, set by the owner (DECISION-council-webinbox-519d0-1121 r2
//! 「誰能標急」; OPINION-role-rank-a9136-1121 r2; 5d33a PREREAD-inboxaibd F r2 0/0/0/2).
//!
//! `POST /api/v1/act {"type":"rank_set","what":…,"ticket"?}` from the built-in page in this
//! pairing only (the caller refuses a user folder page, a carried pairing, a seat's token),
//! every change with the owner's fingerprint, like [`crate::seat_move`]: without a ticket aibd
//! checks the request and asks (`202 {ticket}`); with it aibd checks again and writes only
//! what the ticket approved. `what`:
//! - `role` `{role_uid, rank}`: a role's rank (`plain` / `lead`; a role is never the owner);
//! - `grant` / `revoke` `{seat_id, role_uid}`: give a seat a role's rank, or take it back;
//! - `perm` `{act, rank}`: the least rank an act needs (only `urgent` now).
//!
//! The ranks live in the message library ([`aib_store::ranks`]), never in a file an AI can
//! edit (M1); a seat's node assignments give it nothing (M2). Journals `rank.changed
//! {what, …, by}`. `{"type":"rank_show","seat_id"}` answers a seat's grants, its rank and the
//! urgent act's least rank, for the page (no fingerprint; a role no longer listed is marked,
//! N4).

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use aib_store::ranks::{self, Rank};

use crate::api::AppState;
use crate::guard::request_id;

pub const SET: &str = "rank_set";
pub const SHOW: &str = "rank_show";
/// The operation the owner approves.
pub const OP: &str = "rank.set";
/// The acts a least rank may be set for.
pub const ACTS: [&str; 1] = [ranks::URGENT];

/// One change, checked.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Change {
    Role { role_uid: String, rank: Rank },
    Grant { seat_id: String, role_uid: String },
    Revoke { seat_id: String, role_uid: String },
    Perm { act: String, rank: Rank },
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Raw {
    #[serde(rename = "type")]
    _kind: String,
    what: String,
    #[serde(default)]
    role_uid: Option<String>,
    #[serde(default)]
    seat_id: Option<String>,
    #[serde(default)]
    act: Option<String>,
    #[serde(default)]
    rank: Option<String>,
    #[serde(default)]
    ticket: Option<String>,
}

/// The change a body asks, and its ticket; `None` when it is not one of the four shapes.
pub fn parse(body: Value) -> Option<(Change, Option<String>)> {
    let r: Raw = serde_json::from_value(body).ok()?;
    let role = || r.role_uid.clone().filter(|u| ranks::key_ok(u));
    let seat = || {
        r.seat_id
            .clone()
            .filter(|s| crate::seat_trust::seat_id_ok(s))
    };
    let rank = || r.rank.as_deref().and_then(Rank::from_word);
    let change = match r.what.as_str() {
        "role" if r.seat_id.is_none() && r.act.is_none() => Change::Role {
            role_uid: role()?,
            rank: rank().filter(|k| *k <= Rank::Lead)?,
        },
        "grant" if r.rank.is_none() && r.act.is_none() => Change::Grant {
            seat_id: seat()?,
            role_uid: role()?,
        },
        "revoke" if r.rank.is_none() && r.act.is_none() => Change::Revoke {
            seat_id: seat()?,
            role_uid: role()?,
        },
        "perm" if r.role_uid.is_none() && r.seat_id.is_none() => Change::Perm {
            act: r.act.clone().filter(|a| ACTS.contains(&a.as_str()))?,
            rank: rank()?,
        },
        _ => return None,
    };
    Some((change, r.ticket))
}

/// The parameters the owner approves (the window's sentence is read from them).
pub fn params_of(c: &Change) -> Value {
    match c {
        Change::Role { role_uid, rank } => {
            json!({"what": "role", "role_uid": role_uid, "rank": rank.word()})
        }
        Change::Grant { seat_id, role_uid } => {
            json!({"what": "grant", "seat_id": seat_id, "role_uid": role_uid})
        }
        Change::Revoke { seat_id, role_uid } => {
            json!({"what": "revoke", "seat_id": seat_id, "role_uid": role_uid})
        }
        Change::Perm { act, rank } => json!({"what": "perm", "act": act, "rank": rank.word()}),
    }
}

/// A rank in the owner's words.
pub fn rank_words(w: &str) -> Option<&'static str> {
    match Rank::from_word(w)? {
        Rank::Plain => Some("一般"),
        Rank::Lead => Some("主管"),
        Rank::Owner => Some("擁有者"),
    }
}

fn seat_short(seat_id: &str) -> String {
    seat_id
        .strip_prefix("seat_")
        .unwrap_or(seat_id)
        .chars()
        .take(5)
        .collect()
}

/// What the owner is asked.
pub fn ask_of(c: &Change, by: String) -> crate::owner_ok::Ask {
    let resource = match c {
        Change::Role { role_uid, rank } => format!(
            "角色 {role_uid} 的權級設為 {}",
            rank_words(rank.word()).unwrap_or("")
        ),
        Change::Grant { seat_id, role_uid } => {
            format!("給席 {} 角色 {role_uid} 的權級", seat_short(seat_id))
        }
        Change::Revoke { seat_id, role_uid } => {
            format!("收回席 {} 角色 {role_uid} 的權級", seat_short(seat_id))
        }
        Change::Perm { act, rank } => {
            format!("{act} 最低要 {}", rank_words(rank.word()).unwrap_or(""))
        }
    };
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP,
        resource,
        params: serde_json::to_vec(&params_of(c)).unwrap_or_default(),
        by,
    }
}

/// What aibd reads before it asks and again before it writes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Facts {
    pub user_page: bool,
    /// The seat's row state (`None`: no such seat; not read for a role or an act).
    pub seat_state: Option<String>,
    /// The role is a built-in one or in the registry now.
    pub role_listed: bool,
}

/// The role uids the page can pick now: the six built-in ones and the registry's.
pub fn listed_roles(data: &std::path::Path) -> Vec<String> {
    let mut all: Vec<String> = crate::registry::BUILTIN_ROLES
        .iter()
        .map(|(u, _, _)| (*u).to_owned())
        .collect();
    if let Ok(r) = crate::registry::load(data) {
        all.extend(r.roles.into_iter().map(|e| e.uid));
    }
    all
}

/// May the change be asked / written? Taking a grant back needs no listed role (N4: a role
/// removed from the list is still taken back).
pub fn gate(c: &Change, f: &Facts) -> Result<(), (StatusCode, &'static str)> {
    if f.user_page {
        return Err((StatusCode::FORBIDDEN, "page_only"));
    }
    let seat_ok = || match f.seat_state.as_deref() {
        None => Err((StatusCode::NOT_FOUND, "seat_not_found")),
        Some("ARCHIVED") => Err((StatusCode::NOT_FOUND, "seat_archived")),
        Some(_) => Ok(()),
    };
    match c {
        Change::Role { .. } | Change::Grant { .. } if !f.role_listed => {
            Err((StatusCode::NOT_FOUND, "role_not_found"))
        }
        Change::Grant { .. } => seat_ok(),
        _ => Ok(()),
    }
}

fn refuse(status: StatusCode, reason: &str) -> Response {
    let message = match reason {
        "page_only" => "ranks are set from the built-in page in this pairing only",
        "seat_not_found" | "seat_archived" => "no such seat now",
        "role_not_found" => "no such role in the list now",
        "rank_changed" => "the change is not what the fingerprint approved: ask again",
        "body" => {
            "body must be {type, what: role|grant|revoke|perm, role_uid?, seat_id?, act?, rank?, ticket?}"
        }
        _ => "the rank could not be written",
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
            "request_id": request_id()})),
    )
        .into_response()
}

fn read_facts(st: &AppState, c: &Change) -> Facts {
    let role = match c {
        Change::Role { role_uid, .. }
        | Change::Grant { role_uid, .. }
        | Change::Revoke { role_uid, .. } => Some(role_uid.as_str()),
        Change::Perm { .. } => None,
    };
    let seat = match c {
        Change::Grant { seat_id, .. } | Change::Revoke { seat_id, .. } => Some(seat_id.as_str()),
        _ => None,
    };
    Facts {
        user_page: crate::static_files::web_source() == "user",
        seat_state: seat.and_then(|s| st.store.seat(s).ok().flatten().map(|r| r.state)),
        role_listed: role.is_some_and(|r| {
            listed_roles(&crate::registry::data_of(st))
                .iter()
                .any(|u| u == r)
        }),
    }
}

fn write(m: &aib_store::MessageDb, c: &Change, by: &str, at: &str) -> aib_store::Result<bool> {
    match c {
        Change::Role { role_uid, rank } => m.role_rank_set(role_uid, *rank, by, at).map(|()| true),
        Change::Grant { seat_id, role_uid } => m.rank_grant(seat_id, role_uid, by, at),
        Change::Revoke { seat_id, role_uid } => m.rank_revoke(seat_id, role_uid, at),
        Change::Perm { act, rank } => m.perm_set(act, *rank, by, at).map(|()| true),
    }
}

/// `rank_set`, for the built-in page `by` (`page:<pair>`).
pub async fn set(st: AppState, body: Value, by: String) -> Response {
    let Some((change, ticket)) = parse(body) else {
        return refuse(StatusCode::BAD_REQUEST, "body");
    };
    let facts = {
        let (st2, c) = (st.clone(), change.clone());
        tokio::task::spawn_blocking(move || read_facts(&st2, &c)).await
    };
    let Ok(f) = facts else {
        return refuse(StatusCode::INTERNAL_SERVER_ERROR, "store");
    };
    if let Err((status, why)) = gate(&change, &f) {
        return refuse(status, why);
    }
    let ask = ask_of(&change, by.clone());
    let Some(ticket) = ticket else {
        return crate::repos::asked(&ask);
    };
    if !crate::owner_ok::approved_for(&ticket, &ask) {
        return crate::repos::not_approved();
    }
    let facts = {
        let (st2, c) = (st.clone(), change.clone());
        tokio::task::spawn_blocking(move || read_facts(&st2, &c)).await
    };
    let Ok(f) = facts else {
        return refuse(StatusCode::INTERNAL_SERVER_ERROR, "store");
    };
    if let Err((status, why)) = gate(&change, &f) {
        return refuse(status, why);
    }
    let (m, c, b) = (st.messages.clone(), change.clone(), by.clone());
    let wrote =
        tokio::task::spawn_blocking(move || write(&m, &c, &b, &crate::sessions::now_rfc3339()))
            .await;
    let Ok(Ok(changed)) = wrote else {
        return refuse(StatusCode::INTERNAL_SERVER_ERROR, "store");
    };
    let mut ev = params_of(&change);
    ev["by"] = json!(by);
    ev["changed"] = json!(changed);
    crate::seats::side_event(&st, SET, "rank.changed", ev).await;
    let mut out = params_of(&change);
    out["done"] = json!(true);
    out["changed"] = json!(changed);
    Json(out).into_response()
}

/// `rank_show {seat_id}`, for the built-in page: the seat's grants (a role no longer listed is
/// `listed: false`), its rank, and the urgent act's least rank.
pub async fn show(st: AppState, body: Value) -> Response {
    let Some(sid) = body
        .as_object()
        .filter(|o| o.len() == 2 && o.contains_key("type"))
        .and_then(|o| o.get("seat_id"))
        .and_then(Value::as_str)
        .filter(|s| crate::seat_trust::seat_id_ok(s))
        .map(str::to_owned)
    else {
        return refuse(StatusCode::BAD_REQUEST, "body");
    };
    let st2 = st.clone();
    let got = tokio::task::spawn_blocking(move || {
        let listed = listed_roles(&crate::registry::data_of(&st2));
        let grants = st2.messages.rank_grants(&sid).ok()?;
        Some(json!({
            "seat_id": sid,
            "rank": ranks::seat_rank(&st2.messages, &sid).word(),
            "urgent_min": ranks::perm_min(&st2.messages, ranks::URGENT).word(),
            "grants": grants.iter().map(|g| json!({
                "role_uid": g.role_uid,
                "role_rank": g.role_rank.map(Rank::word),
                "listed": listed.iter().any(|u| *u == g.role_uid),
                "granted_at": g.granted_at,
            })).collect::<Vec<_>>(),
        }))
    })
    .await;
    match got {
        Ok(Some(v)) => Json(v).into_response(),
        _ => refuse(StatusCode::INTERNAL_SERVER_ERROR, "store"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn p(v: Value) -> Option<Change> {
        parse(v).map(|(c, _)| c)
    }

    /// RS1: only the four shapes are taken; a role is never set to the owner; only listed acts.
    #[test]
    fn only_the_four_shapes_are_taken() {
        let seat = "seat_33ff9a0b1c2d3e4f";
        assert_eq!(
            p(json!({"type": SET, "what": "role", "role_uid": "reviewer", "rank": "lead"})),
            Some(Change::Role {
                role_uid: "reviewer".into(),
                rank: Rank::Lead
            })
        );
        assert_eq!(
            p(json!({"type": SET, "what": "role", "role_uid": "reviewer", "rank": "owner"})),
            None,
            "reason: role_set_to_owner"
        );
        assert_eq!(
            p(json!({"type": SET, "what": "grant", "seat_id": seat, "role_uid": "reviewer"})),
            Some(Change::Grant {
                seat_id: seat.into(),
                role_uid: "reviewer".into()
            })
        );
        assert_eq!(
            p(json!({"type": SET, "what": "perm", "act": "urgent", "rank": "plain"})),
            Some(Change::Perm {
                act: "urgent".into(),
                rank: Rank::Plain
            })
        );
        for bad in [
            json!({"type": SET, "what": "perm", "act": "seat_move", "rank": "plain"}),
            json!({"type": SET, "what": "grant", "seat_id": "x", "role_uid": "reviewer"}),
            json!({"type": SET, "what": "grant", "seat_id": seat, "role_uid": "a b"}),
            json!({"type": SET, "what": "grant", "seat_id": seat, "role_uid": "r", "rank": "lead"}),
            json!({"type": SET, "what": "role", "role_uid": "r", "rank": "LEAD"}),
            json!({"type": SET, "what": "all", "role_uid": "r"}),
            json!({"type": SET, "what": "role", "role_uid": "r", "rank": "lead", "extra": 1}),
        ] {
            assert_eq!(p(bad.clone()), None, "reason: shape_taken {bad}");
        }
    }

    /// RS2: a grant needs a listed role and a live seat; taking back needs neither (N4); the
    /// user folder page never sets a rank.
    #[test]
    fn a_grant_needs_a_listed_role_and_a_live_seat() {
        let seat = "seat_33ff9a0b1c2d3e4f".to_owned();
        let g = Change::Grant {
            seat_id: seat.clone(),
            role_uid: "r1".into(),
        };
        let r = Change::Revoke {
            seat_id: seat,
            role_uid: "r1".into(),
        };
        let live = Facts {
            user_page: false,
            seat_state: Some("READY".into()),
            role_listed: true,
        };
        assert_eq!(gate(&g, &live), Ok(()));
        let unlisted = Facts {
            role_listed: false,
            ..live.clone()
        };
        assert_eq!(
            gate(&g, &unlisted),
            Err((StatusCode::NOT_FOUND, "role_not_found"))
        );
        assert_eq!(
            gate(&r, &unlisted),
            Ok(()),
            "reason: revoke_needs_listed_role"
        );
        let gone = Facts {
            seat_state: None,
            ..live.clone()
        };
        assert_eq!(
            gate(&g, &gone),
            Err((StatusCode::NOT_FOUND, "seat_not_found"))
        );
        let user = Facts {
            user_page: true,
            ..live
        };
        assert_eq!(gate(&r, &user), Err((StatusCode::FORBIDDEN, "page_only")));
    }

    /// RS3: what the owner approves is exactly the change: another change is another ask.
    #[test]
    fn the_ticket_binds_the_exact_change() {
        let a = ask_of(
            &Change::Perm {
                act: "urgent".into(),
                rank: Rank::Lead,
            },
            String::new(),
        );
        let b = ask_of(
            &Change::Perm {
                act: "urgent".into(),
                rank: Rank::Plain,
            },
            String::new(),
        );
        assert_ne!(a.params, b.params, "reason: rank_not_bound");
        assert_eq!(a.operation, OP);
    }
}
