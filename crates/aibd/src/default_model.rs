//! 519d0 11-21 06:45 point 1 (0eed7 VERIFY-pv6, 6f1cb EVID-uiclick): a seat opened with no model
//! got whatever the provider's CLI picks itself (for claude that was a model whose quota was
//! spent until 11-27, so every claude seat in the preview failed). aibd keeps one default model
//! per brand in `<data>/default-models.json`; an open that names no model takes it. The built-in
//! page reads them in `GET /api/v1/version` (`default_models`) and changes one with
//! `act default_model_set {provider, model}` (`model: null` clears it).
//!
//! A model name is a plain word ([`aib_seatopen::model_shape`], at most [`MAX_LEN`]); a brand is
//! one of [`aib_seatopen::AIB2_BRANDS`]. Anything else in the file is left out, never used.

use crate::api::AppState;
use crate::guard::request_id;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Map, Value};
use std::collections::BTreeMap;
use std::path::Path;

/// The file, in aibd's data folder.
pub const FILE: &str = "default-models.json";
/// The act kind.
pub const KIND: &str = "default_model_set";
/// The event.
pub const EVENT: &str = "aibd.default_model_set";
/// Longest model name taken.
pub const MAX_LEN: usize = 64;

/// A brand and a model name aibd will use.
pub fn usable(provider: &str, model: &str) -> bool {
    aib_seatopen::AIB2_BRANDS.contains(&provider)
        && model.len() <= MAX_LEN
        && aib_seatopen::model_shape(model)
}

/// The usable pairs of a file's object.
pub fn parse(v: &Value) -> BTreeMap<String, String> {
    v.as_object()
        .into_iter()
        .flatten()
        .filter_map(|(p, m)| {
            let m = m.as_str()?;
            usable(p, m).then(|| (p.clone(), m.to_owned()))
        })
        .collect()
}

/// The defaults in `data_dir` (none when the file is missing or unreadable).
pub fn load(data_dir: &Path) -> BTreeMap<String, String> {
    std::fs::read(data_dir.join(FILE))
        .ok()
        .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
        .map(|v| parse(&v))
        .unwrap_or_default()
}

/// The defaults now (none before aibd knows its data folder).
pub fn now() -> BTreeMap<String, String> {
    crate::bridge_cfg::dir()
        .map(|d| load(&d))
        .unwrap_or_default()
}

/// The model an open uses: the one it named, else the brand's default.
pub fn pick(
    named: Option<String>,
    provider: &str,
    defaults: &BTreeMap<String, String>,
) -> Option<String> {
    named.or_else(|| defaults.get(provider).cloned())
}

/// `defaults` with `provider` set to `model` (`None` removes it).
pub fn with(
    mut defaults: BTreeMap<String, String>,
    provider: &str,
    model: Option<&str>,
) -> BTreeMap<String, String> {
    match model {
        Some(m) => {
            defaults.insert(provider.to_owned(), m.to_owned());
        }
        None => {
            defaults.remove(provider);
        }
    }
    defaults
}

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({ "code": code, "message": message, "request_id": request_id() })),
    )
        .into_response()
}

/// `{type, provider, model}` from the built-in page (dispatched from `seat_input::act`): a user
/// folder page may not, nor a pairing carried over an update (the default decides what every
/// new seat runs on).
pub async fn act(st: AppState, page: crate::pairing::PageAccess, body: Value) -> Response {
    if page.carried || crate::seat_input::held_as_user_page(&page).await {
        return refuse(
            StatusCode::FORBIDDEN,
            "default_model_built_in_page_only",
            "the default model is set from the built-in page of this pairing only",
        );
    }
    let Some(o) = body.as_object() else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "default_model_fields",
            "body must be an object",
        );
    };
    if o.keys()
        .any(|k| !matches!(k.as_str(), "type" | "provider" | "model"))
    {
        return refuse(
            StatusCode::BAD_REQUEST,
            "default_model_fields",
            "default_model_set takes provider and model only",
        );
    }
    let provider = o
        .get("provider")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let model = match o.get("model") {
        Some(Value::Null) | None => None,
        Some(Value::String(m)) => Some(m.trim()),
        Some(_) => {
            return refuse(
                StatusCode::BAD_REQUEST,
                "default_model_shape",
                "model is a name or null",
            )
        }
    };
    if !aib_seatopen::AIB2_BRANDS.contains(&provider) {
        return refuse(
            StatusCode::BAD_REQUEST,
            "default_model_provider",
            "no such brand",
        );
    }
    if model.is_some_and(|m| !usable(provider, m)) {
        return refuse(
            StatusCode::BAD_REQUEST,
            "default_model_shape",
            "a model name is letters, digits and . _ : / - (at most 64)",
        );
    }
    let Some(dir) = crate::bridge_cfg::dir() else {
        return refuse(
            StatusCode::SERVICE_UNAVAILABLE,
            "internal",
            "no data folder",
        );
    };
    let (p, m) = (provider.to_owned(), model.map(str::to_owned));
    let written = tokio::task::spawn_blocking(move || {
        let next = with(load(&dir), &p, m.as_deref());
        let text = serde_json::to_vec_pretty(&next).map_err(|e| e.to_string())?;
        crate::platform::write_private_file(&dir.join(FILE), &text).map_err(|e| e.to_string())?;
        Ok::<_, String>(next)
    })
    .await;
    let Ok(Ok(next)) = written else {
        return refuse(StatusCode::INTERNAL_SERVER_ERROR, "internal", "not written");
    };
    crate::seat_send::journal_event(&st, EVENT, json!({ "provider": provider, "model": model }))
        .await;
    let map: Map<String, Value> = next
        .into_iter()
        .map(|(k, v)| (k, Value::String(v)))
        .collect();
    Json(json!({ "default_models": map })).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_a_brand_and_a_plain_model_name_are_kept() {
        let v = json!({
            "claude": "sonnet",
            "codex": "gpt-5.5",
            "fable": "x",
            "claude2": "sonnet",
        });
        let m = parse(&v);
        assert_eq!(m.len(), 2, "reason: unknown_brand_kept {m:?}");
        assert!(
            parse(&json!({"claude": "--dangerously"})).is_empty(),
            "reason: flag_kept"
        );
        assert!(parse(&json!({"claude": "a b"})).is_empty());
        assert!(
            parse(&json!({"claude": "a".repeat(65)})).is_empty(),
            "reason: long_kept"
        );
        assert!(parse(&json!({"claude": 3})).is_empty());
        assert!(parse(&json!(["claude"])).is_empty());
    }

    #[test]
    fn an_open_with_no_model_takes_the_brands_default_and_a_named_one_wins() {
        let d = parse(&json!({"claude": "sonnet"}));
        assert_eq!(
            pick(None, "claude", &d).as_deref(),
            Some("sonnet"),
            "reason: default_not_used"
        );
        assert_eq!(
            pick(Some("opus".into()), "claude", &d).as_deref(),
            Some("opus"),
            "reason: named_overridden"
        );
        assert_eq!(pick(None, "codex", &d), None);
    }

    #[test]
    fn a_default_is_set_and_cleared_through_the_file() {
        let dir = tempfile::tempdir().unwrap();
        assert!(load(dir.path()).is_empty());
        let next = with(load(dir.path()), "claude", Some("sonnet"));
        std::fs::write(dir.path().join(FILE), serde_json::to_vec(&next).unwrap()).unwrap();
        assert_eq!(
            load(dir.path()).get("claude").map(String::as_str),
            Some("sonnet")
        );
        let next = with(load(dir.path()), "claude", None);
        assert!(next.is_empty(), "reason: not_cleared");
    }
}
