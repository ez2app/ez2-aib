//! 1a93b DESIGN-seat-clear-1a93b-1120 r1 (519d0 11-20 23:31; 5d33a PREREAD-seatclear 0/0/1/3): the
//! person presses 清空 on the built-in page once the seat said its handoff note is written
//! ([`crate::seat_now::HANDOFF`]); aibd stops the seat and opens it again on a **new**
//! conversation (same seat, persona, mode, folder, model and tools), whose first line aibd writes
//! itself: read the handoff note and go on from it. The old conversation stays on disk.
//!
//! `POST /api/v1/act {"type":"seat_clear","seat_id":…,"handoff_at":<the at the page saw>,"idem_key":…}`
//!
//! - **Who**: a confirmed pairing on the built-in page only; not a user folder page, not a
//!   pairing carried over an update; a dgrs seat only with this machine's dgrs input switch.
//!   No fingerprint (5d33a agreed: stop, restart and typing a line need none, and a clear can
//!   be undone: the old conversation is kept).
//! - **Before anything stops** ([`check`]): the seat has a handoff, the one the page saw; its path
//!   is the seat's own ([`path_ours`]: `<pending>/HANDOFF-<code>-*.md` or under the seat's
//!   folder, 5d33a L1); it was written after the last text the seat got and at most
//!   [`FRESH_S`] ago; the seat is not in a turn; no session of it is stop-unconfirmed.
//! - **Steps**: stop → start on a new id ([`crate::revive::start_fresh`]) → swap the kept resume
//!   id from the old one (only then) → type the first line ([`first_line`]). A start that fails
//!   leaves the resume id as it was, so 重開 goes back to the old conversation; a first line that
//!   does not go in puts the handoff back on the board (`handoff_used: false`).
//! - `idem_key` is the store's idempotency key (as node_*): the same key and body answer the first
//!   answer again; a refused clear gives the key back.
//! - Journals `seat.cleared {seat_id, old_session_id, session_id, kept, first_line,
//!   handoff:{len, sha256}, by}`: the path as length + hash only.

use crate::api::AppState;
use crate::guard::request_id;
use crate::revive::NotRestarted;
use crate::seat_now::Handoff;
use aib_store::{IdemClaim, IdemKey, NewEvent};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::path::Path;

pub const KIND: &str = "seat_clear";
/// A handoff older than this (seconds) is written again before a clear.
pub const FRESH_S: i64 = 30 * 60;

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({ "code": code, "message": message, "request_id": request_id() })),
    )
        .into_response()
}

/// A refusal before or during the clear: status, code, words.
type Why = (StatusCode, &'static str, String);

fn why(s: StatusCode, c: &'static str, m: &str) -> Why {
    (s, c, m.to_owned())
}

/// `/` for `\`, and a drive letter folded to lower case, so the two forms of one path compare
/// equal (Windows: `C:\x` and `c:/x`).
fn fold(p: &str) -> String {
    let mut s = p.replace('\\', "/");
    let b = s.as_bytes();
    if b.len() >= 2 && b[0].is_ascii_alphabetic() && b[1] == b':' {
        let d = s[..1].to_ascii_lowercase();
        s.replace_range(..1, &d);
    }
    s
}

/// Whether the handoff note's path is the seat's own (5d33a PREREAD-seatclear L1): no `.` or
/// `..` part, and either `<pending>/HANDOFF-<code>-<anything>.md` directly in the pending folder
/// or anywhere under the seat's folder `cwd`. Both separators are read; only the text is
/// compared (no link followed, no file opened: the new conversation opens it).
pub fn path_ours(path: &str, code: &str, pending: Option<&Path>, cwd: &str) -> bool {
    let p = fold(path);
    if p.split('/').any(|seg| seg == "." || seg == "..") {
        return false;
    }
    let under = |root: &str| {
        let r = fold(root);
        let r = r.trim_end_matches('/');
        !r.is_empty() && p.len() > r.len() + 1 && p.starts_with(r) && p.as_bytes()[r.len()] == b'/'
    };
    if !cwd.is_empty() && under(cwd) {
        return true;
    }
    let Some(dir) = pending.and_then(|d| d.to_str()) else {
        return false;
    };
    let r = fold(dir);
    let r = r.trim_end_matches('/');
    let Some(name) = p
        .strip_prefix(r)
        .and_then(|rest| rest.strip_prefix('/'))
        .filter(|n| !n.contains('/'))
    else {
        return false;
    };
    let lower = name.to_ascii_lowercase();
    !code.is_empty()
        && name.starts_with(&format!("HANDOFF-{code}-"))
        && lower.ends_with(".md")
        && name.len() > format!("HANDOFF-{code}-.md").len()
}

/// The checks before anything is stopped, in the order they are told.
pub fn check(
    h: Option<&Handoff>,
    asked_at: &str,
    path_ok: bool,
    last_input: Option<&str>,
    in_turn: bool,
    now_unix: i64,
) -> Result<(), Why> {
    let Some(h) = h else {
        return Err(why(
            StatusCode::CONFLICT,
            "no_handoff",
            "the seat has not said its handoff note is written (seat_handoff)",
        ));
    };
    if h.at != asked_at {
        return Err(why(
            StatusCode::CONFLICT,
            "handoff_changed",
            "the seat wrote another handoff since the page read it; read again",
        ));
    }
    if !path_ok {
        return Err(why(
            StatusCode::CONFLICT,
            "handoff_path_outside",
            "the handoff note is not the seat's own: pending/HANDOFF-<seat code>-*.md or under its folder",
        ));
    }
    if in_turn {
        return Err(why(
            StatusCode::CONFLICT,
            "seat_busy",
            "the seat is in a turn now; clear it when the turn is over",
        ));
    }
    let after_input = last_input.is_none_or(|t| h.at.as_str() > t);
    let fresh = crate::revive::unix_of(&h.at).is_some_and(|w| now_unix - w <= FRESH_S);
    if !after_input || !fresh {
        return Err(why(
            StatusCode::CONFLICT,
            "handoff_stale",
            "the handoff is older than the seat's last text, or over 30 minutes old; ask it to write it again",
        ));
    }
    Ok(())
}

/// Of a seat's open rows (`open_to`): how many still wait to be written to it, and how many
/// another seat wrote to it that it has not closed (delivered / read, not a shared
/// conversation's post; the count mail_nudge reminds of).
pub fn mail_counts(rows: &[aib_store::MessageRow]) -> (usize, usize) {
    let queued = rows
        .iter()
        .filter(|r| crate::seat_send::waits_to_be_written(r))
        .count();
    let unclosed = rows
        .iter()
        .filter(|r| {
            let t = |k: &str| r.item.get(k).and_then(Value::as_str);
            matches!(t("status"), Some("delivered" | "read"))
                && t("sender_kind") == Some("seat")
                && r.item.get("post_id").is_none_or(Value::is_null)
        })
        .count();
    (queued, unclosed)
}

/// The new conversation's first line: aibd's words, the path, the hash when the seat gave
/// one, and how many messages it has not closed. Nothing from the page.
pub fn first_line(path: &str, sha256: Option<&str>, unclosed: usize) -> String {
    let sha = sha256.map_or(String::new(), |h| format!("(sha256 {h})"));
    let check = if sha256.is_some() {
        "核對雜湊後"
    } else {
        ""
    };
    format!(
        "這是清空後的新一段。先讀交接單 {path}{sha},{check}照它接著做;讀不到或核不對就停下來回報,不要自己猜。{}",
        if unclosed > 0 {
            format!("另有 {unclosed} 封別席寄來、收了沒結的信,讀完要結案。")
        } else {
            "也看一下待審框有沒有收了沒結的信。".to_owned()
        }
    )
}

fn sha_of(s: &str) -> String {
    Sha256::digest(s.as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn hex8(b: &[u8]) -> String {
    b.iter().take(8).map(|x| format!("{x:02x}")).collect()
}

/// The act (dispatched from `seat_input::act` for a confirmed page).
pub async fn act(st: AppState, page: crate::pairing::PageAccess, body: Value) -> Response {
    if crate::seat_input::held_as_user_page(&page).await {
        return refuse(
            StatusCode::FORBIDDEN,
            "seat_clear_built_in_page_only",
            "a seat is cleared from the built-in page only",
        );
    }
    if page.carried {
        return refuse(
            StatusCode::FORBIDDEN,
            "carried_pair_read_and_safe_only",
            "a pairing carried over an update clears no seat; pair again",
        );
    }
    let fields_ok = body.as_object().is_some_and(|o| {
        o.keys()
            .all(|k| matches!(k.as_str(), "type" | "seat_id" | "handoff_at" | "idem_key"))
    });
    let s = |k: &str| body.get(k).and_then(Value::as_str);
    let (Some(seat_id), Some(asked_at), Some(key)) = (s("seat_id"), s("handoff_at"), s("idem_key"))
    else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "body must be {type, seat_id, handoff_at, idem_key}",
        );
    };
    if !fields_ok
        || !crate::assign::seat_id_ok(seat_id)
        || !(1..=128).contains(&key.len())
        || key.chars().any(char::is_control)
    {
        return refuse(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "body must be {type, seat_id, handoff_at, idem_key (1-128)}",
        );
    }
    let mut rest = body.clone();
    if let Some(o) = rest.as_object_mut() {
        o.remove("idem_key");
    }
    let by = hex8(&page.digest);
    let idem = IdemKey {
        scope: format!("act {KIND} {by}"),
        key: key.to_owned(),
        request: crate::idem::request_digest(&rest),
    };
    let (store, k) = (st.store.clone(), idem.clone());
    let claim =
        tokio::task::spawn_blocking(move || store.idem_claim(&k, &crate::sessions::now_rfc3339()))
            .await;
    match claim {
        Ok(Ok(IdemClaim::Fresh)) => {}
        Ok(Ok(IdemClaim::Replay { response, .. })) => {
            let mut again = response;
            again["replayed"] = json!(true);
            return Json(again).into_response();
        }
        Ok(Ok(IdemClaim::Conflict | IdemClaim::InProgress)) => {
            return refuse(
                StatusCode::CONFLICT,
                "idempotency_conflict",
                "this idem_key was used for another clear, or that clear is still running",
            )
        }
        _ => return refuse(StatusCode::INTERNAL_SERVER_ERROR, "internal", "not cleared"),
    }
    let (seat, asked) = (seat_id.to_owned(), asked_at.to_owned());
    let done = clear(&st, &page, &seat, &asked, &by).await;
    let (store, k) = (st.store.clone(), idem);
    let kept_key = done.clone();
    let _ = tokio::task::spawn_blocking(move || match &kept_key {
        Ok(v) => store.idem_finish(&k, 200, v).map(|_| ()),
        Err(_) => store.idem_release(&k).map(|_| ()),
    })
    .await;
    match done {
        Ok(v) => Json(v).into_response(),
        Err((s, c, m)) => refuse(s, c, &m),
    }
}

async fn clear(
    st: &AppState,
    page: &crate::pairing::PageAccess,
    seat_id: &str,
    asked_at: &str,
    by: &str,
) -> Result<Value, Why> {
    let Some(_held) = crate::revive::hold(seat_id) else {
        return Err(why(
            StatusCode::CONFLICT,
            "seat_running",
            "the seat is being restarted or cleared now",
        ));
    };
    let (store, id) = (st.store.clone(), seat_id.to_owned());
    let read = tokio::task::spawn_blocking(move || -> aib_store::Result<_> {
        Ok((store.seat(&id)?, store.seat_meta(&id)?))
    })
    .await;
    let Ok(Ok((seat, meta))) = read else {
        return Err(why(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "seat not read",
        ));
    };
    let (Some(seat), Some(meta)) = (seat, meta) else {
        return Err(why(StatusCode::NOT_FOUND, "seat_not_found", "no such seat"));
    };
    if aib_store::ARCHIVED == seat.state {
        return Err(why(
            StatusCode::CONFLICT,
            "seat_archived",
            "the seat is archived",
        ));
    }
    if meta.mode.as_deref() == Some("dgrs") && !crate::seat_input::dgrs_input_on() {
        return Err(why(
            StatusCode::FORBIDDEN,
            "dgrs_needs_switch",
            "this machine has not turned dgrs seats on for the page (AIBD_SEAT_INPUT_DGRS=1)",
        ));
    }
    let handoff = crate::seat_now::board().handoffs().get(seat_id).cloned();
    let code = seat_id.strip_prefix("seat_").unwrap_or(seat_id);
    let pending = crate::docs::doc_root("pending");
    let path_ok = handoff
        .as_ref()
        .is_some_and(|h| path_ours(&h.path, code, pending.as_deref(), &meta.cwd));
    check(
        handoff.as_ref(),
        asked_at,
        path_ok,
        st.sessions.last_input_at(seat_id).as_deref(),
        st.sessions.in_turn(seat_id),
        crate::pair_carry::now_unix() as i64,
    )?;
    let Some(h) = handoff else {
        return Err(why(StatusCode::CONFLICT, "no_handoff", "no handoff"));
    };
    // a message waiting to be written is the seat's work too: the new conversation's start
    // would write it before (or over) the handoff line (1a93b REVIEW-day N2)
    let (db, key) = (st.messages.clone(), format!("seat:{seat_id}"));
    let rows = tokio::task::spawn_blocking(move || db.open_to(&key, aib_store::PAGE_MAX))
        .await
        .ok()
        .and_then(Result::ok)
        .unwrap_or_default();
    let (queued, unclosed) = mail_counts(&rows);
    if queued > 0 {
        return Err(why(
            StatusCode::CONFLICT,
            "seat_busy",
            "messages still wait to be written to the seat; clear it when they are",
        ));
    }
    let mine: Vec<_> = st
        .sessions
        .list()
        .into_iter()
        .filter(|x| x.seat_id.as_deref() == Some(seat_id))
        .collect();
    // a stop aibd could not look at keeps its slot (holds_slot): a process may still run. macOS
    // never claims a clean stop, so every stop there reads stop_unconfirmed; the slot is the
    // judge, as for ctl (clr-full-1de5ab892 test: 409 stop_unconfirmed on a clean stop)
    if mine
        .iter()
        .any(|x| x.holds_slot && x.state == "stop_unconfirmed")
    {
        return Err(why(
            StatusCode::CONFLICT,
            "stop_unconfirmed",
            "a session of this seat may still run; see its stop card first",
        ));
    }
    let running: Vec<String> = mine
        .iter()
        .filter(|x| matches!(x.state, "running" | "pending"))
        .map(|x| x.id.clone())
        .collect();
    for id in &running {
        let s = st.sessions.clone();
        let id = id.clone();
        let stopped =
            tokio::task::spawn_blocking(move || s.stop(&id, crate::sessions::DEFAULT_GRACE)).await;
        match stopped {
            Ok(Ok(rec)) if !rec.holds_slot => {}
            Ok(Ok(_)) => {
                return Err(why(
                    StatusCode::CONFLICT,
                    "stop_unconfirmed",
                    "the seat's session did not stop for sure; nothing else was done",
                ))
            }
            _ => {
                return Err(why(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "internal",
                    "not stopped",
                ))
            }
        }
    }
    let conv = crate::seats::new_uuid()
        .map_err(|_| why(StatusCode::INTERNAL_SERVER_ERROR, "internal", "no new id"))?;
    let started = crate::revive::start_fresh(st, seat_id, conv.clone()).await;
    let session_id = match started {
        Ok(s) => s,
        Err(e) => {
            // stopped, not started: the kept resume id is the old one, so 重開 goes back to it;
            // the handoff goes back on the board for the next try
            crate::seat_now::board().set_handoff(seat_id, h.clone());
            return Err(match e {
                NotRestarted::Skip(crate::revive::Skip::DgrsOff) => why(
                    StatusCode::FORBIDDEN,
                    "dgrs_needs_switch",
                    "this machine has not turned dgrs seats on for the page",
                ),
                NotRestarted::Skip(w) => (
                    StatusCode::CONFLICT,
                    "restart_refused",
                    format!(
                        "stopped, not started again: {}; 重開 picks the old conversation up",
                        w.as_str()
                    ),
                ),
                NotRestarted::Running => {
                    why(StatusCode::CONFLICT, "seat_running", "the seat runs now")
                }
                NotRestarted::Archived => why(
                    StatusCode::CONFLICT,
                    "seat_archived",
                    "the seat is archived",
                ),
                NotRestarted::Unknown => {
                    why(StatusCode::NOT_FOUND, "seat_not_found", "no such seat")
                }
                NotRestarted::Unread => why(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "internal",
                    "seat not read",
                ),
            });
        }
    };
    let (store, sid, old, new) = (
        st.store.clone(),
        seat_id.to_owned(),
        meta.resume_id.clone(),
        conv.clone(),
    );
    let kept = tokio::task::spawn_blocking(move || {
        let ev = crate::sessions::new_id("ev_").ok()?;
        store
            .swap_resume_id(
                &sid,
                old.as_deref(),
                &new,
                &ev,
                &crate::sessions::now_rfc3339(),
            )
            .ok()
    })
    .await
    .ok()
    .flatten()
    .unwrap_or(false);
    let line = first_line(&h.path, h.sha256.as_deref(), unclosed);
    let typed = crate::seat_input::type_to_seat(st, page, seat_id, &line)
        .await
        .is_ok();
    if !typed {
        // not used up: the page's 請它讀交接單 may send it again (5d33a N3)
        crate::seat_now::board().set_handoff(seat_id, h.clone());
    }
    let out = json!({
        "seat_id": seat_id,
        "session_id": session_id,
        "old_session_id": running.first(),
        "kept": kept,
        "first_line": typed,
        "handoff_used": typed,
        "handoff": {"path": h.path, "at": h.at},
    });
    let payload = json!({
        "seat_id": seat_id,
        "session_id": session_id,
        "old_session_id": running.first(),
        "kept": kept,
        "first_line": typed,
        "handoff": {"len": h.path.chars().count(), "sha256": sha_of(&h.path)},
        "by": {"pairing": by, "page": "built_in"},
    });
    let store = st.store.clone();
    let _ = tokio::task::spawn_blocking(move || {
        let id = crate::sessions::new_id("ev_").ok()?;
        store
            .append(vec![NewEvent {
                event_id: id.clone(),
                aggregate_type: "seat_clear".into(),
                aggregate_id: id,
                sequence: 1,
                event_type: "seat.cleared".into(),
                correlation_id: None,
                occurred_at: crate::sessions::now_rfc3339(),
                payload,
            }])
            .ok()
    })
    .await;
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn h(path: &str, at: &str) -> Handoff {
        Handoff {
            path: path.into(),
            sha256: None,
            at: at.into(),
        }
    }

    #[test]
    fn a_handoff_path_is_the_seats_own_or_refused() {
        let pend = Path::new("/Users/x/.ez2-aib/pending");
        let cwd = "/Users/x/seat-work/seat_1a93b";
        let ok = |p: &str| path_ours(p, "1a93b", Some(pend), cwd);
        assert!(ok("/Users/x/.ez2-aib/pending/HANDOFF-1a93b-1120.md"));
        assert!(ok("/Users/x/.ez2-aib/pending/HANDOFF-1a93b-a.MD"));
        assert!(ok("/Users/x/seat-work/seat_1a93b/notes/h.md"));
        for bad in [
            "/Users/x/.ez2-aib/pending/HANDOFF-0eed7-1120.md",
            "/Users/x/.ez2-aib/pending/HANDOFF-1a93b-.md",
            "/Users/x/.ez2-aib/pending/notes/HANDOFF-1a93b-1.md",
            "/Users/x/.ez2-aib/pending/../pending/HANDOFF-1a93b-1.md",
            "/Users/x/seat-work/seat_1a93b/../seat_0eed7/h.md",
            "/Users/x/seat-work/seat_1a93b2/h.md",
            "/Users/x/seat-work/seat_1a93b",
            "/tmp/HANDOFF-1a93b-1.md",
            "/Users/x/.ez2-aib/pending/./HANDOFF-1a93b-1.md",
        ] {
            assert!(!ok(bad), "reason: path_outside_taken {bad}");
        }
        // both separators, a drive letter in either case
        let wp = Path::new("C:\\Users\\x\\.ez2-aib\\pending");
        let w = |p: &str| path_ours(p, "1a93b", Some(wp), "c:/Users/x/seat-work/seat_1a93b");
        assert!(w("c:/Users/x/.ez2-aib/pending/HANDOFF-1a93b-1.md"));
        assert!(w("C:\\Users\\x\\seat-work\\seat_1a93b\\h.md"));
        assert!(
            !w("C:\\Users\\x\\seat-work\\seat_1a93b\\..\\seat_0eed7\\h.md"),
            "reason: path_outside_taken"
        );
        assert!(
            !path_ours(
                "/Users/x/.ez2-aib/pending/HANDOFF-1a93b-1.md",
                "1a93b",
                None,
                ""
            ),
            "reason: no_pending_took_it"
        );
    }

    #[test]
    fn a_clear_needs_the_handoff_the_page_saw_written_after_the_last_text_and_fresh() {
        let at = "2026-11-20T15:30:00.000Z";
        let now = crate::revive::unix_of(at).unwrap() + 60;
        let hh = h("/p/HANDOFF-1a93b-1.md", at);
        let code = |r: Result<(), Why>| r.err().map(|e| e.1);
        assert_eq!(
            code(check(
                Some(&hh),
                at,
                true,
                Some("2026-11-20T15:29:00.000Z"),
                false,
                now
            )),
            None
        );
        assert_eq!(code(check(Some(&hh), at, true, None, false, now)), None);
        assert_eq!(
            code(check(None, at, true, None, false, now)),
            Some("no_handoff")
        );
        assert_eq!(
            code(check(
                Some(&hh),
                "2026-11-20T15:00:00.000Z",
                true,
                None,
                false,
                now
            )),
            Some("handoff_changed")
        );
        assert_eq!(
            code(check(Some(&hh), at, false, None, false, now)),
            Some("handoff_path_outside")
        );
        assert_eq!(
            code(check(Some(&hh), at, true, None, true, now)),
            Some("seat_busy")
        );
        assert_eq!(
            code(check(
                Some(&hh),
                at,
                true,
                Some("2026-11-20T15:31:00.000Z"),
                false,
                now
            )),
            Some("handoff_stale"),
            "reason: worked_after_the_handoff"
        );
        assert_eq!(
            code(check(Some(&hh), at, true, None, false, now + FRESH_S)),
            Some("handoff_stale"),
            "reason: old_handoff_taken"
        );
    }

    #[test]
    fn queued_messages_and_unclosed_ones_are_counted_apart() {
        let row = |pairs: &[(&str, Value)]| {
            let mut r =
                crate::seat_send::queued_row("seat_a", "seat_b", Some("safe"), "x", "00").unwrap();
            for (k, v) in pairs {
                r.item.insert((*k).to_owned(), v.clone());
            }
            r
        };
        let rows = [
            row(&[]),
            row(&[("status", json!("delivered")), ("session_id", json!("s1"))]),
            row(&[("status", json!("read")), ("session_id", json!("s1"))]),
            row(&[("status", json!("read")), ("post_id", json!("p1"))]),
            row(&[("status", json!("done"))]),
        ];
        assert_eq!(mail_counts(&rows), (1, 2), "reason: mail_miscounted");
        assert_eq!(mail_counts(&[]), (0, 0));
    }

    #[test]
    fn the_first_line_is_aibds_words_with_the_path_and_the_hash() {
        let l = first_line("/p/HANDOFF-1a93b-1.md", None, 0);
        assert!(
            l.contains("/p/HANDOFF-1a93b-1.md")
                && l.contains("先讀交接單")
                && !l.contains("sha256")
        );
        let s = "ab".repeat(32);
        let l = first_line("/p/h.md", Some(&s), 3);
        assert!(l.contains("另有 3 封"), "reason: unclosed_not_said {l}");
        assert!(
            l.contains(&s) && l.contains("核對雜湊"),
            "reason: hash_not_said"
        );
        assert!(!l.contains('\n'), "reason: two_lines");
    }
}
