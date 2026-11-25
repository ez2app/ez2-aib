//! 519d0 11-21 06:45 point 2 (0eed7 VERIFY-pv6 M4): a seat whose usage ran out is moved to
//! another model from the built-in page and goes on in the same conversation. `act
//! seat_model_set {seat_id, model, idem_key}`: the seat's model is written (pinned, event
//! `seat.model_set`), its running session stopped and the seat started again the way 重開 does
//! (its kept conversation, `--resume`), now with `--model <model>`.
//!
//! Refused: a user folder page or a carried pairing (403), a model that is not a plain name
//! (400), a seat in a turn (409 `seat_busy`, nothing changed), a stop aibd could not confirm
//! (409 `stop_unconfirmed`: the model is written, the seat is not started twice).

use crate::api::AppState;
use crate::guard::request_id;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};

/// The act kind.
pub const KIND: &str = "seat_model_set";

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({ "code": code, "message": message, "request_id": request_id() })),
    )
        .into_response()
}

/// The fields, or the refusal code.
pub fn fields(body: &Value) -> Result<(String, String), &'static str> {
    let o = body.as_object().ok_or("seat_model_fields")?;
    if o.keys().any(|k| {
        !matches!(
            k.as_str(),
            "type" | "seat_id" | "model" | "idem_key" | "ticket"
        )
    }) {
        return Err("seat_model_fields");
    }
    let key_ok = o
        .get("idem_key")
        .and_then(Value::as_str)
        .is_some_and(|k| (1..=128).contains(&k.len()));
    let seat = o.get("seat_id").and_then(Value::as_str).filter(|_| key_ok);
    let Some(seat) = seat else {
        return Err("seat_model_fields");
    };
    let model = o.get("model").and_then(Value::as_str).unwrap_or("").trim();
    if model.is_empty()
        || model.len() > crate::default_model::MAX_LEN
        || !aib_seatopen::model_shape(model)
    {
        return Err("seat_model_shape");
    }
    Ok((seat.to_owned(), model.to_owned()))
}

pub async fn act(st: AppState, page: crate::pairing::PageAccess, body: Value) -> Response {
    if page.carried || crate::seat_input::held_as_user_page(&page).await {
        return refuse(
            StatusCode::FORBIDDEN,
            "seat_model_built_in_page_only",
            "a seat's model is changed from the built-in page of this pairing only",
        );
    }
    let (seat_id, model) = match fields(&body) {
        Ok(f) => f,
        Err(code) => {
            return refuse(
                StatusCode::BAD_REQUEST,
                code,
                "body must be {type, seat_id, model (a plain name, at most 64), idem_key}",
            )
        }
    };
    if st.sessions.in_turn(&seat_id) {
        return refuse(
            StatusCode::CONFLICT,
            "seat_busy",
            "the seat is in a turn now; change its model when the turn is over",
        );
    }
    // A11 r1 L2: a new model starts a dgrs seat again with no sandbox: the owner first,
    // before the model is written or the seat stopped
    let ticket = body.get("ticket").and_then(Value::as_str);
    let asker: String = page.digest[..8]
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    let asker = format!("page:{asker}");
    if let Some(r) =
        crate::repos::dgrs_restart_gate(&st, &seat_id, ticket, asker, crate::repos::GateAct::Model)
            .await
    {
        return r;
    }
    let (store, id, m) = (st.store.clone(), seat_id.clone(), model.clone());
    let written = tokio::task::spawn_blocking(move || {
        let ev = crate::sessions::new_id("ev_").map_err(|_| ())?;
        store
            .set_model(&id, &m, &ev, &crate::sessions::now_rfc3339())
            .map_err(|_| ())
    })
    .await;
    match written {
        Ok(Ok(true)) => {}
        Ok(Ok(false)) => return refuse(StatusCode::NOT_FOUND, "seat_not_found", "no such seat"),
        _ => return refuse(StatusCode::INTERNAL_SERVER_ERROR, "internal", "not written"),
    }
    let running: Vec<String> = st
        .sessions
        .list()
        .into_iter()
        .filter(|x| x.seat_id.as_deref() == Some(seat_id.as_str()))
        .filter(|x| matches!(x.state, "running" | "pending"))
        .map(|x| x.id)
        .collect();
    for id in running {
        let s = st.sessions.clone();
        let stopped =
            tokio::task::spawn_blocking(move || s.stop(&id, crate::sessions::DEFAULT_GRACE)).await;
        // macOS never claims a clean stop: the slot decides (as ctl, seat_clear)
        if !matches!(stopped, Ok(Ok(ref rec)) if !rec.holds_slot) {
            return refuse(
                StatusCode::CONFLICT,
                "stop_unconfirmed",
                "the model is set, but the seat's session did not stop for sure; press 重開 when it has",
            );
        }
    }
    let by = json!({"page": "built_in", "model_set": model});
    use crate::revive::NotRestarted as N;
    match crate::revive::restart(&st, &seat_id, by).await {
        Ok(session_id) => Json(json!({
            "seat_id": seat_id,
            "model": model,
            "session_id": session_id,
        }))
        .into_response(),
        Err(N::Unknown) => refuse(StatusCode::NOT_FOUND, "seat_not_found", "no such seat"),
        Err(N::Archived) => refuse(
            StatusCode::CONFLICT,
            "seat_archived",
            "the seat is archived",
        ),
        Err(N::Running) => refuse(StatusCode::CONFLICT, "seat_running", "the seat runs now"),
        Err(N::Skip(crate::revive::Skip::DgrsOff)) => refuse(
            StatusCode::FORBIDDEN,
            "dgrs_needs_switch",
            "this machine has not turned dgrs seats on for the page (AIBD_SEAT_INPUT_DGRS=1)",
        ),
        Err(N::Skip(why)) => refuse(StatusCode::CONFLICT, "restart_refused", why.as_str()),
        Err(N::Unread) => refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "seat not read",
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_a_seat_a_plain_model_and_a_key_are_taken() {
        let ok = json!({"type": KIND, "seat_id": "seat_a", "model": "sonnet", "idem_key": "k"});
        assert_eq!(fields(&ok), Ok(("seat_a".into(), "sonnet".into())));
        let with = |k: &str, v: Value| {
            let mut b = ok.clone();
            b[k] = v;
            fields(&b)
        };
        assert_eq!(
            with("model", json!("--dangerously-skip-permissions")),
            Err("seat_model_shape"),
            "reason: flag_taken"
        );
        assert_eq!(
            with("model", json!("a".repeat(65))),
            Err("seat_model_shape"),
            "reason: long_taken"
        );
        assert_eq!(with("model", json!("")), Err("seat_model_shape"));
        assert_eq!(
            with("idem_key", json!("")),
            Err("seat_model_fields"),
            "reason: no_key_taken"
        );
        assert_eq!(
            with("cwd", json!("/x")),
            Err("seat_model_fields"),
            "reason: extra_field_taken"
        );
    }
}
