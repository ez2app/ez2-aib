//! D7a (DECISION-council-driveweb-519d0-1121 r1 §2; 5d33a PREREAD-d7d5 0/0/2/1): the
//! automatic-dispatch switch lives in aibd, and only the built-in page in this pairing moves it.
//!
//! `POST /api/v1/act`:
//! - `{"type":"drive_toggle","to":"on"|"off"|"paused","why":"…","ticket"?}`:
//!   - `paused` = everything stops (reminders too); any paired built-in page, no fingerprint
//!     (stopping may never wait).
//!   - `off` = dispatching stops, reminders go on; no fingerprint (only stricter).
//!   - `on` = dispatching starts: the owner's fingerprint (owner_ok, the ask shape of every
//!     owner act: no ticket ⇒ `202 {ticket}`; with it ⇒ written only when it approves this very
//!     request). The key's signature of the approval is kept with the switch.
//! - `{"type":"drive_status"}`: the switch, who moved it, why, when, whether it carries a
//!   signature this aibd's key checks, and what the page may press (`can_on` / `can_off` /
//!   `can_pause`).
//!
//! The switch is `<data>/drive-switch.json` (`aib-drive-switch/v1`), each move a line of
//! `<data>/drive-switch.jsonl`. Honest limit (5d33a Q1): a local file an AI on this account can
//! write is no defence by itself; what this adds is that turning on goes through the page and
//! the fingerprint, and an `on` without a signature this aibd checks shows as unsigned (and
//! aib-drive refuses to dispatch on it). That stops a slip or an old command, not a forgery.
//! A missing or unreadable switch is `off`.

use std::path::Path;

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};

use crate::api::AppState;

pub const TOGGLE: &str = "drive_toggle";
pub const STATUS: &str = "drive_status";
/// The operation the owner approves.
pub const OP: &str = "drive.dispatch_on";
pub const FILE: &str = "drive-switch.json";
pub const JOURNAL: &str = "drive-switch.jsonl";
pub const SCHEMA: &str = "aib-drive-switch/v1";
/// The longest reason, in characters.
pub const WHY_MAX: usize = 200;
/// The three states, in the order the page shows them.
pub const STATES: [&str; 3] = ["on", "off", "paused"];

/// The switch as written.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Switch {
    pub to: String,
    pub by: String,
    pub why: String,
    pub at: u64,
    /// For `on`: the approval's ticket and the key's signature of its binding (hex).
    pub ticket: String,
    pub sig: String,
}

impl Switch {
    fn off() -> Switch {
        Switch {
            to: "off".into(),
            by: String::new(),
            why: String::new(),
            at: 0,
            ticket: String::new(),
            sig: String::new(),
        }
    }

    pub fn to_value(&self) -> Value {
        json!({"schema": SCHEMA, "to": self.to, "by": self.by, "why": self.why, "at": self.at,
            "ticket": self.ticket, "sig": self.sig})
    }
}

/// The switch in `data`; `off` when missing, a link, too large or not this shape.
pub fn read(data: &Path) -> Switch {
    let file = data.join(FILE);
    let ok = std::fs::symlink_metadata(&file).is_ok_and(|m| m.is_file() && m.len() <= 4096);
    let v: Option<Value> = ok
        .then(|| std::fs::read(&file).ok())
        .flatten()
        .and_then(|b| serde_json::from_slice(&b).ok());
    let Some(v) = v.filter(|v| v["schema"] == SCHEMA) else {
        return Switch::off();
    };
    let s = |k: &str| v[k].as_str().unwrap_or("").to_owned();
    let to = s("to");
    if !STATES.contains(&to.as_str()) {
        return Switch::off();
    }
    Switch {
        to,
        by: s("by"),
        why: s("why"),
        at: v["at"].as_u64().unwrap_or(0),
        ticket: s("ticket"),
        sig: s("sig"),
    }
}

/// A reason a person typed: 1 to [`WHY_MAX`] characters, no control characters.
pub fn why_ok(why: &str) -> bool {
    let n = why.trim().chars().count();
    (1..=WHY_MAX).contains(&n) && !why.chars().any(char::is_control)
}

/// What the owner is asked for `on`: aibd's words only, the reason cut to 60 characters.
pub fn ask_of(why: &str, by: String) -> crate::owner_ok::Ask {
    let shown: String = why.chars().take(60).collect();
    crate::owner_ok::Ask {
        category: aib_approve::category::Category::Command,
        operation: OP,
        resource: format!("打開自動推進派工 · 理由:{shown}"),
        params: serde_json::to_vec(&json!({"to": "on", "why": why})).unwrap_or_default(),
        by,
    }
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn unhex(s: &str) -> Option<Vec<u8>> {
    if s.len() % 2 != 0 {
        return None;
    }
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(s.get(i..i + 2)?, 16).ok())
        .collect()
}

/// Whether an `on` carries the key's signature of the approval it names (the binding is
/// rebuilt from the switch: operation, words, parameters, who).
pub fn signed(s: &Switch) -> bool {
    if s.to != "on" || s.ticket.is_empty() {
        return false;
    }
    let Some(sig) = unhex(&s.sig) else {
        return false;
    };
    let ask = ask_of(&s.why, s.by.clone());
    crate::owner_ok::verify(&crate::owner_ok::binding(&s.ticket, &ask), &sig)
}

/// Write the switch and its journal line.
pub fn write(data: &Path, s: &Switch) -> Result<(), &'static str> {
    let file = data.join(FILE);
    if std::fs::symlink_metadata(&file).is_ok_and(|m| m.file_type().is_symlink()) {
        return Err("drive_switch_store");
    }
    let body = serde_json::to_vec(&s.to_value()).map_err(|_| "drive_switch_store")?;
    crate::platform::write_private_file(&file, &body).map_err(|_| "drive_switch_store")?;
    let mut line = serde_json::to_vec(&s.to_value()).map_err(|_| "drive_switch_store")?;
    line.push(b'\n');
    let mut j = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(data.join(JOURNAL))
        .map_err(|_| "drive_switch_store")?;
    std::io::Write::write_all(&mut j, &line).map_err(|_| "drive_switch_store")
}

fn refuse(status: StatusCode, reason: &str) -> Response {
    let message = match reason {
        "drive_toggle_body" => "body must be {type, to: on|off|paused, why, ticket?}",
        "drive_switch_off" => "aibd has no data folder for the switch here",
        _ => "the switch could not be written",
    };
    let code = match status {
        StatusCode::BAD_REQUEST => "invalid_request",
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

/// `{to, why, ticket?}` of a toggle body (nothing else taken).
pub fn parse(body: &Value) -> Option<(String, String, Option<String>)> {
    let o = body.as_object()?;
    if o.keys()
        .any(|k| !matches!(k.as_str(), "type" | "to" | "why" | "ticket"))
    {
        return None;
    }
    let to = o.get("to")?.as_str()?;
    let why = o.get("why")?.as_str()?;
    if !STATES.contains(&to) || !why_ok(why) {
        return None;
    }
    let ticket = match o.get("ticket") {
        None => None,
        Some(t) => Some(t.as_str()?.to_owned()),
    };
    if ticket.is_some() && to != "on" {
        return None;
    }
    Some((to.to_owned(), why.trim().to_owned(), ticket))
}

/// `drive_toggle`, for the built-in page `by` (`page:<pair>`).
pub async fn toggle(st: AppState, body: Value, by: String) -> Response {
    let Some((to, why, ticket)) = parse(&body) else {
        return refuse(StatusCode::BAD_REQUEST, "drive_toggle_body");
    };
    let Some(data) = crate::bridge_cfg::dir() else {
        return refuse(StatusCode::FORBIDDEN, "drive_switch_off");
    };
    let (mut t, mut sig) = (String::new(), String::new());
    if to == "on" {
        let ask = ask_of(&why, by.clone());
        let Some(tk) = ticket else {
            return crate::repos::asked(&ask);
        };
        let Some(s) = crate::owner_ok::approved_signed_for(&tk, &ask) else {
            return crate::repos::not_approved();
        };
        t = tk;
        sig = hex(&s);
    }
    let s = Switch {
        to: to.clone(),
        by: by.clone(),
        why: why.clone(),
        at: crate::pair_carry::now_unix(),
        ticket: t,
        sig,
    };
    let wrote = {
        let s = s.clone();
        tokio::task::spawn_blocking(move || write(&data, &s)).await
    };
    match wrote {
        Ok(Ok(())) => {}
        Ok(Err(why)) => return refuse(StatusCode::INTERNAL_SERVER_ERROR, why),
        Err(_) => return refuse(StatusCode::INTERNAL_SERVER_ERROR, "drive_switch_store"),
    }
    crate::seats::side_event(
        &st,
        TOGGLE,
        "drive.switched",
        json!({"to": to, "by": by, "why": why}),
    )
    .await;
    Json(json!({"done": true, "to": s.to, "at": s.at})).into_response()
}

/// `drive_status`, for the built-in page. The page may always pause and turn off; turning on
/// needs the owner check to be available here.
pub fn status_of(s: &Switch, owner_check: bool) -> Value {
    json!({
        "on": s.to == "on",
        "state": s.to,
        "by": s.by,
        "why": s.why,
        "at": s.at,
        "signed": signed(s),
        "can_on": owner_check && s.to != "on",
        "can_off": s.to != "off",
        "can_pause": s.to != "paused",
    })
}

pub async fn status(_st: AppState) -> Response {
    let Some(data) = crate::bridge_cfg::dir() else {
        return refuse(StatusCode::FORBIDDEN, "drive_switch_off");
    };
    let s = tokio::task::spawn_blocking(move || read(&data))
        .await
        .unwrap_or_else(|_| Switch::off());
    Json(status_of(&s, crate::owner_ok::available())).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// DS1: only on / off / paused and a typed reason are taken; a ticket only with `on`.
    #[test]
    fn a_toggle_takes_three_states_and_a_reason() {
        let ok = |v: Value| parse(&v).is_some();
        assert!(ok(json!({"type": TOGGLE, "to": "paused", "why": "先停"})));
        assert!(ok(
            json!({"type": TOGGLE, "to": "on", "why": "開", "ticket": "ot_1"})
        ));
        assert!(
            !ok(json!({"type": TOGGLE, "to": "maybe", "why": "x"})),
            "reason: fourth_state"
        );
        assert!(
            !ok(json!({"type": TOGGLE, "to": "off", "why": " "})),
            "reason: empty_why"
        );
        assert!(
            !ok(json!({"type": TOGGLE, "to": "off", "why": "a\nb"})),
            "reason: control_why"
        );
        assert!(
            !ok(json!({"type": TOGGLE, "to": "off", "why": "x", "ticket": "ot_1"})),
            "reason: ticket_without_on"
        );
        assert!(
            !ok(json!({"type": TOGGLE, "to": "off", "why": "x", "by": "andy"})),
            "reason: page_names_who"
        );
        assert!(!ok(
            json!({"type": TOGGLE, "to": "off", "why": "長".repeat(WHY_MAX + 1)})
        ));
    }

    /// DS2: a missing, bad or unknown switch is off; a written one reads back.
    #[test]
    fn a_missing_or_bad_switch_is_off() {
        let d = tempfile::tempdir().unwrap();
        assert_eq!(read(d.path()).to, "off");
        std::fs::write(d.path().join(FILE), br#"{"schema":"x","to":"on"}"#).unwrap();
        assert_eq!(read(d.path()).to, "off", "reason: other_schema_read");
        std::fs::write(
            d.path().join(FILE),
            format!(r#"{{"schema":"{SCHEMA}","to":"go"}}"#),
        )
        .unwrap();
        assert_eq!(read(d.path()).to, "off", "reason: unknown_state_read");
        let s = Switch {
            to: "paused".into(),
            by: "page:0011aabb".into(),
            why: "先停".into(),
            at: 7,
            ticket: String::new(),
            sig: String::new(),
        };
        write(d.path(), &s).unwrap();
        write(d.path(), &s).unwrap();
        assert_eq!(read(d.path()), s);
        let j = std::fs::read_to_string(d.path().join(JOURNAL)).unwrap();
        assert_eq!(j.lines().count(), 2, "reason: move_not_journaled");
    }

    /// DS3 (5d33a Q1): an `on` written without a signature this aibd checks is not signed, and
    /// the page may not turn on without the owner check.
    #[test]
    fn an_on_without_a_checked_signature_is_unsigned() {
        let mut s = Switch::off();
        s.to = "on".into();
        s.ticket = "ot_1".into();
        s.sig = "00ff".into();
        assert!(!signed(&s), "reason: forged_on_signed");
        let v = status_of(&s, false);
        assert_eq!(v["on"], true);
        assert_eq!(v["signed"], false);
        assert_eq!(
            (v["can_on"].as_bool(), v["can_pause"].as_bool()),
            (Some(false), Some(true))
        );
        assert_eq!(
            status_of(&Switch::off(), false)["can_on"],
            false,
            "reason: on_without_owner_check"
        );
        assert_eq!(status_of(&Switch::off(), true)["can_on"], true);
        assert_eq!(status_of(&Switch::off(), true)["can_off"], false);
    }

    #[test]
    fn hex_reads_back() {
        assert_eq!(
            unhex(&hex(&[0, 1, 0xab, 0xff])),
            Some(vec![0, 1, 0xab, 0xff])
        );
        assert_eq!(unhex("abc"), None);
        assert_eq!(unhex("zz"), None);
    }
}
