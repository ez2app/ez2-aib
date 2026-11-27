//! The new-user guide (6770f IFACE-guide-6770f-1126 r3–r4b; ebab8 SPEC-onboarding-projects r4;
//! 3e53b DECISION-onboarding-bounds r3b; f163c PREREAD 1–13). `GET /api/v1/guide` tells the
//! page, for its two lists of steps, whether each step is done: a boolean (or `null`, "can't
//! tell") and a fixed short word, read from what aibd already has. The act `guide_done` writes
//! the one record that the person went through the first list.
//!
//! Nothing decides anything from this module or its record (knife a): no gate, no act and no
//! seat open reads them. The GET writes nothing and starts nothing (knives b, n, p); before the
//! page is paired it says only that aibd is up and pairing is next (knives c, f); every `why`
//! is a word of this file, never a path, an account, a machine or a program's output (knives e,
//! q).

use crate::api::AppState;
use crate::guard::Caller;
use crate::session_policy_setup::LookKind;
use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::{Extension, Json};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

/// The act that writes the record (3e53b's name).
pub const DONE_ACT: &str = "guide_done";
/// `<data>/guide/done`: aibd's own folder, never the user's.
pub const DIR: &str = "guide";
pub const FILE: &str = "done";
/// The journal event of the one write.
pub const EVENT: &str = "guide.done";
/// The record is `{at, source}`; anything longer is not one.
const MAX_RECORD: u64 = 512;

/// The first list, in order (r3: Windows Hello is part of pairing, no step of its own).
pub const START: [&str; 6] = ["health", "pair", "claude", "allow", "first", "tour"];
/// The second list (r4 / r4b: only `host_add` is read in this version).
pub const HOSTS: [&str; 4] = ["host_add", "host_reach", "host_handoff", "site_first"];

/// What the GET reads, gathered once, off the runtime.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Facts {
    /// ok / not_found / npm_only / not_logged_in
    pub claude: &'static str,
    /// ok / no_policy / no_claude
    pub allow: &'static str,
    /// A seat's session has written a line; `None`: the store could not be read.
    pub first: Option<bool>,
    /// A seat's session runs now (r7, fc7f9 B1); a store that cannot say ⇒ false.
    pub open: bool,
    pub record: Record,
    /// aibd's pinned host list names a machine; `None`: the list could not be read.
    pub hosts: Option<bool>,
}

/// The record `guide_done` (or the start-up check) wrote.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Record {
    None,
    Done {
        at: String,
        source: String,
    },
    /// There, but not a plain file of the record's shape (r3a: the panel stays lit by `first`).
    Unreadable,
}

fn step(id: &str, done: Option<bool>, why: &str, next: Option<&str>) -> Value {
    let mut v = json!({"id": id, "done": done, "why": why});
    if let (Some(n), false) = (next, done == Some(true)) {
        v["next_act"] = json!(n);
    }
    v
}

fn locked(id: &str) -> Value {
    json!({"id": id, "why": "locked"})
}

/// Before the page is paired: aibd is up, pairing is next, every other step is locked and says
/// nothing of its state; nothing was read to make this.
pub fn unpaired_body() -> Value {
    let mut start = vec![
        step("health", Some(true), "ok", None),
        step("pair", Some(false), "not_paired", Some("pair")),
    ];
    start.extend(START[2..].iter().map(|id| locked(id)));
    json!({
        "projects": [
            {"id": "start", "lit": true, "steps": start},
            {"id": "hosts", "lit": false, "steps": HOSTS.iter().map(|id| locked(id)).collect::<Vec<_>>()},
        ],
        "panel_lit": false,
        "done_at": null,
    })
}

/// A paired page's answer, from `f` only.
pub fn body(f: &Facts) -> Value {
    let claude = step(
        "claude",
        Some(f.claude == "ok"),
        f.claude,
        match f.claude {
            "not_logged_in" => Some("provider_relogin"),
            _ => Some("provider_install"),
        },
    );
    let allow = step(
        "allow",
        Some(f.allow == "ok"),
        f.allow,
        Some("session_policy_init"),
    );
    let first = match f.first {
        Some(true) => step("first", Some(true), "ok", None),
        // r7 (fc7f9 B1): a seat is there and has not spoken ⇒ the chat, not another seat
        Some(false) if f.open => step("first", Some(false), "seat_open", Some("focus_chat")),
        Some(false) => step("first", Some(false), "none_yet", Some("start_claude")),
        None => step("first", Some(false), "source_error", Some("start_claude")),
    };
    let first_done = f.first == Some(true);
    let (tour, panel_lit, done_at) = match &f.record {
        Record::Done { at, .. } => (step("tour", Some(true), "ok", None), true, json!(at)),
        // r3a: a record that cannot be read does not turn an old user's panel dark
        Record::Unreadable => (
            step("tour", None, "source_error", None),
            first_done,
            Value::Null,
        ),
        Record::None if first_done => (
            step("tour", Some(false), "not_yet", Some("open_partner_panel")),
            false,
            Value::Null,
        ),
        Record::None => (
            step("tour", Some(false), "after_first", None),
            false,
            Value::Null,
        ),
    };
    let start_done = f.claude == "ok" && f.allow == "ok" && first_done && panel_lit;
    let host_add = match f.hosts {
        Some(true) => step("host_add", Some(true), "ok", None),
        Some(false) => step("host_add", Some(false), "none", Some("add_host")),
        None => step("host_add", Some(false), "source_error", Some("add_host")),
    };
    let mut hosts = vec![host_add];
    // r4b: nothing reads these yet; they are never done in this version
    hosts.extend(
        HOSTS[1..]
            .iter()
            .map(|id| step(id, Some(false), "next_version", None)),
    );
    json!({
        "projects": [
            {"id": "start", "lit": true, "steps": [
                step("health", Some(true), "ok", None),
                step("pair", Some(true), "paired", None),
                claude, allow, first, tour,
            ]},
            {"id": "hosts", "lit": start_done, "steps": hosts},
        ],
        "panel_lit": panel_lit,
        "done_at": done_at,
    })
}

/// The `claude` step's word: the session policy's look found a native program, and the login
/// file the seats read is there.
pub fn claude_word(kind: LookKind, logged_in: impl FnOnce() -> bool) -> &'static str {
    match kind {
        LookKind::Native if logged_in() => "ok",
        LookKind::Native => "not_logged_in",
        LookKind::Npm => "npm_only",
        _ => "not_found",
    }
}

/// The `allow` step's word, from the programs of the session policy in force.
pub fn allow_word(programs: &[String]) -> &'static str {
    if programs.is_empty() {
        "no_policy"
    } else if programs.iter().any(|p| p == "claude") {
        "ok"
    } else {
        "no_claude"
    }
}

/// Has a seat's session written a line? `None`: the store could not say.
pub fn spoken(store: &aib_store::Store) -> Option<bool> {
    store
        .a_seat_has_spoken(
            &crate::session_output::aggregate_id(""),
            crate::session_output::LINE,
        )
        .ok()
}

/// Does aibd's pinned host list (`<data>/trusted/hosts.json`, the one `test_host_add` writes;
/// never the user's own `~/.ez2-aib/hosts.json`) name a machine? Only that: no name, no
/// address. `None`: it is there but cannot be read.
pub fn pinned(data: &Path) -> Option<bool> {
    let p = data
        .join(crate::trusted_hosts::DIR)
        .join(crate::trusted_hosts::FILE);
    match std::fs::symlink_metadata(&p) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Some(false),
        Err(_) => return None,
        Ok(_) => {}
    }
    crate::trusted_hosts::load(&p)
        .ok()
        .map(|(hosts, _)| !hosts.is_empty())
}

/// Everything the GET answers from; reads only.
pub fn gather(data: &Path, store: &aib_store::Store, programs: &[String]) -> Facts {
    use crate::session_policy_setup as sp;
    // the session policy's own look (with L2 + C, placed copy checked at its measured size)
    let dirs = sp::search_dirs(data);
    let kind = sp::placed_verified(
        vec![sp::look("claude", &dirs)],
        data,
        aib_seatopen::measured::this_platform(),
        false,
    )[0]
    .kind;
    Facts {
        claude: claude_word(kind, || crate::workspace::login_now("claude")),
        allow: allow_word(programs),
        first: spoken(store),
        open: store.a_seat_is_open().unwrap_or(false),
        record: read_record(data),
        hosts: pinned(data),
    }
}

pub fn record_path(data: &Path) -> PathBuf {
    data.join(DIR).join(FILE)
}

/// The record as it is now. The folder and the file must be what they look like: a link at
/// either is not a record.
pub fn read_record(data: &Path) -> Record {
    let not_there = |e: &std::io::Error| e.kind() == std::io::ErrorKind::NotFound;
    match std::fs::symlink_metadata(data.join(DIR)) {
        Err(e) if not_there(&e) => return Record::None,
        Ok(m) if m.file_type().is_dir() => {}
        _ => return Record::Unreadable,
    }
    let p = record_path(data);
    let m = match std::fs::symlink_metadata(&p) {
        Err(e) if not_there(&e) => return Record::None,
        Err(_) => return Record::Unreadable,
        Ok(m) => m,
    };
    if !m.file_type().is_file() || m.len() > MAX_RECORD {
        return Record::Unreadable;
    }
    std::fs::read(&p)
        .ok()
        .and_then(|b| parse_record(&b))
        .unwrap_or(Record::Unreadable)
}

/// `{at, source}`: an RFC 3339 time aibd took, and `opened` (the page's act) or `existing`
/// (the start-up check).
pub fn parse_record(b: &[u8]) -> Option<Record> {
    let v: Value = serde_json::from_slice(b).ok()?;
    let at = v.get("at")?.as_str()?;
    let source = v.get("source")?.as_str()?;
    (is_time(at) && matches!(source, "opened" | "existing")).then(|| Record::Done {
        at: at.to_owned(),
        source: source.to_owned(),
    })
}

fn is_time(s: &str) -> bool {
    (20..=35).contains(&s.len())
        && s.as_bytes()[0].is_ascii_digit()
        && s.bytes()
            .all(|b| b.is_ascii_digit() || b"-:.TZ+".contains(&b))
}

/// Write the record once. A record already there is never replaced (knife i): it is
/// returned as it is, with `false`. A folder that is not a plain folder is not written into.
pub fn write_once(data: &Path, at: &str, source: &str) -> Result<(Record, bool), &'static str> {
    if let Ok(m) = std::fs::symlink_metadata(data.join(DIR)) {
        if !m.file_type().is_dir() {
            return Err("guide_source_error");
        }
    }
    let text = json!({"at": at, "source": source}).to_string();
    match crate::session_policy_setup::write_new(&record_path(data), text.as_bytes()) {
        Ok(()) => Ok((
            Record::Done {
                at: at.to_owned(),
                source: source.to_owned(),
            },
            true,
        )),
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => match read_record(data) {
            r @ Record::Done { .. } => Ok((r, false)),
            _ => Err("guide_source_error"),
        },
        Err(_) => Err("guide_write_failed"),
    }
}

/// At aibd's start, once: someone who used AIB before this guide existed is not walked
/// through it again. No record, and a seat has spoken ⇒ the record, as `existing` (knife k: not
/// when no seat has). A new user who finishes the first five steps while aibd runs is not
/// caught by this; they press the last step.
pub fn start_check(data: &Path, spoken: Option<bool>, now: &str) -> Option<Record> {
    if read_record(data) != Record::None || spoken != Some(true) {
        return None;
    }
    match write_once(data, now, "existing") {
        Ok((r, true)) => Some(r),
        _ => None,
    }
}

/// Run [`start_check`] off the runtime and journal a write.
pub fn at_start(st: AppState) {
    tokio::spawn(async move {
        let data = crate::registry::data_of(&st);
        let store = st.store.clone();
        let wrote = tokio::task::spawn_blocking(move || {
            start_check(&data, spoken(&store), &crate::sessions::now_rfc3339())
        })
        .await;
        if let Ok(Some(Record::Done { source, .. })) = wrote {
            journal(&st, &source).await;
        }
    });
}

async fn journal(st: &AppState, source: &str) {
    let Ok(id) = crate::sessions::new_id("ev_") else {
        return;
    };
    let ev = aib_store::NewEvent {
        event_id: id.clone(),
        aggregate_type: "guide".into(),
        aggregate_id: id,
        sequence: 1,
        event_type: EVENT.into(),
        correlation_id: None,
        occurred_at: crate::sessions::now_rfc3339(),
        payload: json!({"source": source}),
    };
    let store = st.store.clone();
    if !matches!(
        tokio::task::spawn_blocking(move || store.append(vec![ev])).await,
        Ok(Ok(_))
    ) {
        tracing::warn!("guide: the record is written, its journal event is not");
    }
}

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({"code": code, "reason": code, "message": message,
                    "request_id": crate::guard::request_id()})),
    )
        .into_response()
}

/// `GET /api/v1/guide`, behind the token as every route but the public ones: no token is 401
/// (the page then shows the first two steps itself, as [`unpaired_body`] has them). A page
/// credential not confirmed yet gets [`unpaired_body`]; a confirmed page (or the start-up
/// token) the facts.
pub async fn get(State(st): State<AppState>, Extension(c): Extension<Caller>) -> Response {
    let paired = match &c {
        Caller::StartupToken => true,
        Caller::Page(p) => p.confirmed,
        Caller::Anonymous | Caller::Seat(_) => false,
    };
    if !paired {
        return Json(unpaired_body()).into_response();
    }
    let data = crate::registry::data_of(&st);
    let store = st.store.clone();
    let programs: Vec<String> = st
        .policy()
        .programs
        .iter()
        .map(|p| p.name.clone())
        .collect();
    match tokio::task::spawn_blocking(move || gather(&data, &store, &programs)).await {
        Ok(f) => Json(body(&f)).into_response(),
        Err(_) => refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "guide read failed",
        ),
    }
}

/// act `guide_done` (r3 / r3b), a confirmed page: no value is taken from the page, the time
/// is aibd's (knife h). Before a seat has spoken ⇒ 409 `guide_not_ready`, nothing written.
/// Once written, every later press answers the same record.
pub async fn done_act(st: &AppState, body: Value) -> Response {
    match body.as_object() {
        Some(o) if o.keys().all(|k| k == "type") => {}
        _ => {
            return refuse(
                StatusCode::BAD_REQUEST,
                "guide_bad_request",
                "only type: the time is aibd's own",
            )
        }
    }
    let data = crate::registry::data_of(st);
    let store = st.store.clone();
    let done = tokio::task::spawn_blocking(move || match read_record(&data) {
        Record::Done { at, source } => Ok((at, source, false)),
        Record::Unreadable => Err("guide_source_error"),
        Record::None => {
            if spoken(&store) != Some(true) {
                return Err("guide_not_ready");
            }
            match write_once(&data, &crate::sessions::now_rfc3339(), "opened")? {
                (Record::Done { at, source }, fresh) => Ok((at, source, fresh)),
                _ => Err("guide_source_error"),
            }
        }
    })
    .await;
    match done {
        Ok(Ok((at, source, fresh))) => {
            if fresh {
                journal(st, &source).await;
            }
            (
                StatusCode::OK,
                Json(json!({"ok": true, "at": at, "source": source})),
            )
                .into_response()
        }
        Ok(Err("guide_not_ready")) => refuse(
            StatusCode::CONFLICT,
            "guide_not_ready",
            "a seat has not answered yet",
        ),
        Ok(Err(code)) => refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            code,
            "the guide record cannot be read or written",
        ),
        Err(_) => refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "guide write failed",
        ),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    fn facts() -> Facts {
        Facts {
            claude: "ok",
            allow: "ok",
            first: Some(true),
            open: false,
            record: Record::Done {
                at: "2026-11-26T05:00:00.000Z".into(),
                source: "opened".into(),
            },
            hosts: Some(false),
        }
    }

    fn steps(v: &Value, project: usize) -> Vec<Value> {
        v["projects"][project]["steps"].as_array().unwrap().clone()
    }

    fn of<'a>(steps: &'a [Value], id: &str) -> &'a Value {
        steps.iter().find(|s| s["id"] == id).unwrap()
    }

    /// knives c / f: before pairing only health and pair carry a state; every other step of
    /// both lists is `locked`, with no `done` and no `next_act`
    #[test]
    fn before_pairing_only_health_and_pair_say_anything() {
        let v = unpaired_body();
        let s = steps(&v, 0);
        let ids: Vec<&str> = s.iter().map(|x| x["id"].as_str().unwrap()).collect();
        assert_eq!(ids, START, "reason: start_order");
        assert_eq!(of(&s, "health")["done"], true);
        assert_eq!(of(&s, "pair")["done"], false);
        assert_eq!(of(&s, "pair")["why"], "not_paired");
        assert_eq!(of(&s, "pair")["next_act"], "pair");
        for x in s.iter().skip(2).chain(steps(&v, 1).iter()) {
            assert_eq!(x["why"], "locked", "reason: unpaired_not_locked {x}");
            assert!(x.get("done").is_none(), "reason: unpaired_done_leaked {x}");
            assert!(
                x.get("next_act").is_none(),
                "reason: unpaired_next_leaked {x}"
            );
        }
        assert_eq!(v["panel_lit"], false);
        assert_eq!(v["done_at"], Value::Null);
        assert_eq!(v["projects"][1]["lit"], false);
    }

    /// knife d: every step's done follows its fact, nothing is fixed
    #[test]
    fn each_step_follows_what_was_read() {
        let v = body(&facts());
        let s = steps(&v, 0);
        for id in START {
            assert_eq!(of(&s, id)["done"], true, "reason: all_done {id}");
            assert!(of(&s, id).get("next_act").is_none(), "{id}");
        }
        assert_eq!(v["panel_lit"], true);
        assert_eq!(v["done_at"], "2026-11-26T05:00:00.000Z");
        assert_eq!(v["projects"][1]["lit"], true, "reason: hosts_lit");

        let mut f = facts();
        f.claude = "not_logged_in";
        let s = steps(&body(&f), 0);
        assert_eq!(of(&s, "claude")["done"], false, "reason: claude_fixed");
        assert_eq!(of(&s, "claude")["next_act"], "provider_relogin");
        f.claude = "not_found";
        assert_eq!(
            of(&steps(&body(&f), 0), "claude")["next_act"],
            "provider_install"
        );

        let mut f = facts();
        f.allow = "no_claude";
        let v = body(&f);
        let s = steps(&v, 0);
        assert_eq!(of(&s, "allow")["done"], false, "reason: allow_fixed");
        assert_eq!(of(&s, "allow")["next_act"], "session_policy_init");
        // f163c 12: a step that stops holding later does not darken the panel
        assert_eq!(v["panel_lit"], true, "reason: panel_follows_steps");
        assert_eq!(v["projects"][1]["lit"], false);

        let mut f = facts();
        f.first = None;
        let s = steps(&body(&f), 0);
        assert_eq!(of(&s, "first")["done"], false);
        assert_eq!(of(&s, "first")["why"], "source_error");
    }

    /// r2 / r3: the last step waits for the first answer, then for the press
    #[test]
    fn the_tour_step_waits_for_a_seat_then_for_the_press() {
        let mut f = facts();
        f.record = Record::None;
        f.first = Some(false);
        let s = steps(&body(&f), 0);
        assert_eq!(of(&s, "tour")["why"], "after_first");
        assert!(of(&s, "tour").get("next_act").is_none());
        f.first = Some(true);
        let v = body(&f);
        let s = steps(&v, 0);
        assert_eq!(of(&s, "tour")["why"], "not_yet");
        assert_eq!(of(&s, "tour")["next_act"], "open_partner_panel");
        assert_eq!(v["panel_lit"], false);
        assert_eq!(v["done_at"], Value::Null);
    }

    /// r7 (fc7f9 B1): a seat open and silent ⇒ focus_chat; spoken or failed ⇒ as before
    #[test]
    fn an_open_seat_that_has_not_spoken_points_at_the_chat() {
        let mut f = facts();
        f.record = Record::None;
        f.first = Some(false);
        let s = steps(&body(&f), 0);
        assert_eq!(of(&s, "first")["why"], "none_yet");
        assert_eq!(of(&s, "first")["next_act"], "start_claude");
        f.open = true;
        let s = steps(&body(&f), 0);
        assert_eq!(of(&s, "first")["why"], "seat_open");
        assert_eq!(of(&s, "first")["next_act"], "focus_chat");
        assert_eq!(of(&s, "first")["done"], false);
        f.first = None;
        let s = steps(&body(&f), 0);
        assert_eq!(of(&s, "first")["why"], "source_error");
        f.first = Some(true);
        let s = steps(&body(&f), 0);
        assert_eq!(of(&s, "first")["why"], "ok");
        assert!(of(&s, "first").get("next_act").is_none());
    }

    /// knife l (r3a): a record that cannot be read leaves the panel lit when a seat has spoken
    #[test]
    fn an_unreadable_record_keeps_an_old_users_panel_lit() {
        let mut f = facts();
        f.record = Record::Unreadable;
        let v = body(&f);
        assert_eq!(v["panel_lit"], true, "reason: broken_record_dark");
        assert_eq!(v["done_at"], Value::Null);
        let s = steps(&v, 0);
        assert_eq!(of(&s, "tour")["done"], Value::Null);
        assert_eq!(of(&s, "tour")["why"], "source_error");
        f.first = Some(false);
        assert_eq!(body(&f)["panel_lit"], false);
    }

    /// knife o (r4b): only host_add is read; the other three are never done in this version
    #[test]
    fn only_host_add_is_read_in_the_second_list() {
        let mut f = facts();
        let s = steps(&body(&f), 1);
        let ids: Vec<&str> = s.iter().map(|x| x["id"].as_str().unwrap()).collect();
        assert_eq!(ids, HOSTS);
        assert_eq!(of(&s, "host_add")["done"], false);
        assert_eq!(of(&s, "host_add")["why"], "none");
        assert_eq!(of(&s, "host_add")["next_act"], "add_host");
        f.hosts = Some(true);
        let s = steps(&body(&f), 1);
        assert_eq!(of(&s, "host_add")["done"], true, "reason: host_add_fixed");
        for id in &HOSTS[1..] {
            assert_eq!(of(&s, id)["done"], false, "reason: next_version_done {id}");
            assert_eq!(of(&s, id)["why"], "next_version");
        }
        f.hosts = None;
        assert_eq!(of(&steps(&body(&f), 1), "host_add")["why"], "source_error");
    }

    /// knife e / q: every word the answer carries is one of this file's
    #[test]
    fn every_why_is_a_fixed_word() {
        const WORDS: &[&str] = &[
            "ok",
            "paired",
            "not_paired",
            "locked",
            "not_found",
            "npm_only",
            "not_logged_in",
            "no_policy",
            "no_claude",
            "none_yet",
            "seat_open",
            "source_error",
            "not_yet",
            "after_first",
            "none",
            "next_version",
        ];
        let mut all = vec![unpaired_body()];
        for claude in ["ok", "not_found", "npm_only", "not_logged_in"] {
            for record in [Record::None, Record::Unreadable, facts().record] {
                for hosts in [Some(true), Some(false), None] {
                    all.push(body(&Facts {
                        claude,
                        allow: "no_policy",
                        first: None,
                        open: false,
                        record: record.clone(),
                        hosts,
                    }));
                }
            }
        }
        for v in &all {
            for p in 0..2 {
                for s in steps(v, p) {
                    let w = s["why"].as_str().unwrap();
                    assert!(WORDS.contains(&w), "reason: why_not_fixed {w}");
                    assert_eq!(
                        s.as_object().unwrap().len(),
                        s.as_object()
                            .unwrap()
                            .keys()
                            .filter(|k| ["id", "done", "why", "next_act"].contains(&k.as_str()))
                            .count(),
                        "reason: extra_field {s}"
                    );
                }
            }
        }
    }

    #[test]
    fn the_words_of_claude_and_allow() {
        assert_eq!(claude_word(LookKind::Native, || true), "ok");
        assert_eq!(claude_word(LookKind::Native, || false), "not_logged_in");
        assert_eq!(claude_word(LookKind::Npm, || true), "npm_only");
        assert_eq!(claude_word(LookKind::Script, || true), "not_found");
        assert_eq!(claude_word(LookKind::NotFound, || true), "not_found");
        assert_eq!(allow_word(&[]), "no_policy");
        assert_eq!(allow_word(&["codex".into()]), "no_claude");
        assert_eq!(allow_word(&["codex".into(), "claude".into()]), "ok");
    }

    /// knife i: the record is written once; a second write answers the first
    #[test]
    fn the_record_is_written_once_and_read_back() {
        let d = tempfile::tempdir().unwrap();
        assert_eq!(read_record(d.path()), Record::None);
        let (r, fresh) = write_once(d.path(), "2026-11-26T05:00:00.000Z", "opened").unwrap();
        assert!(fresh);
        assert_eq!(read_record(d.path()), r);
        let (again, fresh) = write_once(d.path(), "2026-11-26T06:00:00.000Z", "existing").unwrap();
        assert!(!fresh, "reason: second_write_fresh");
        assert_eq!(again, r, "reason: second_write_replaced");
        assert_eq!(read_record(d.path()), r, "reason: record_overwritten");
    }

    #[test]
    fn a_record_of_another_shape_is_unreadable() {
        let d = tempfile::tempdir().unwrap();
        std::fs::create_dir(d.path().join(DIR)).unwrap();
        for bad in [
            &b"not json"[..],
            br#"{"at":"2026-11-26T05:00:00Z","source":"page"}"#,
            br#"{"at":"/Users/x","source":"opened"}"#,
            br#"{"source":"opened"}"#,
        ] {
            std::fs::write(record_path(d.path()), bad).unwrap();
            assert_eq!(read_record(d.path()), Record::Unreadable, "{bad:?}");
        }
        std::fs::write(
            record_path(d.path()),
            br#"{"at":"2026-11-26T05:00:00Z","source":"existing"}"#,
        )
        .unwrap();
        assert!(matches!(read_record(d.path()), Record::Done { .. }));
        // a record that cannot be read is never replaced by a write
        std::fs::write(record_path(d.path()), b"broken").unwrap();
        assert_eq!(
            write_once(d.path(), "2026-11-26T05:00:00.000Z", "opened"),
            Err("guide_source_error")
        );
        assert_eq!(std::fs::read(record_path(d.path())).unwrap(), b"broken");
    }

    #[cfg(unix)]
    #[test]
    fn a_linked_folder_or_record_is_not_a_record_and_not_written_through() {
        let d = tempfile::tempdir().unwrap();
        let elsewhere = d.path().join("elsewhere");
        std::fs::create_dir(&elsewhere).unwrap();
        let data = d.path().join("data");
        std::fs::create_dir(&data).unwrap();
        std::os::unix::fs::symlink(&elsewhere, data.join(DIR)).unwrap();
        assert_eq!(read_record(&data), Record::Unreadable);
        assert_eq!(
            write_once(&data, "2026-11-26T05:00:00.000Z", "opened"),
            Err("guide_source_error"),
            "reason: wrote_through_link"
        );
        assert!(!elsewhere.join(FILE).exists());
        std::fs::remove_file(data.join(DIR)).unwrap();
        std::fs::create_dir(data.join(DIR)).unwrap();
        let target = elsewhere.join("rec");
        std::fs::write(
            &target,
            br#"{"at":"2026-11-26T05:00:00Z","source":"opened"}"#,
        )
        .unwrap();
        std::os::unix::fs::symlink(&target, record_path(&data)).unwrap();
        assert_eq!(read_record(&data), Record::Unreadable, "reason: link_read");
    }

    /// knife k: the start-up check writes only when a seat has spoken and nothing is there
    #[test]
    fn the_start_check_writes_only_for_someone_who_used_aib_before() {
        let d = tempfile::tempdir().unwrap();
        let now = "2026-11-26T05:00:00.000Z";
        assert_eq!(start_check(d.path(), Some(false), now), None);
        assert_eq!(start_check(d.path(), None, now), None);
        assert_eq!(
            read_record(d.path()),
            Record::None,
            "reason: start_wrote_new_user"
        );
        let r = start_check(d.path(), Some(true), now).unwrap();
        assert_eq!(
            r,
            Record::Done {
                at: now.into(),
                source: "existing".into()
            }
        );
        assert_eq!(
            start_check(d.path(), Some(true), "2026-11-27T05:00:00.000Z"),
            None
        );
        assert_eq!(read_record(d.path()), r);
    }

    /// knife m: the host step reads aibd's pinned list, never the user's own file
    #[test]
    fn host_add_reads_the_pinned_list_only() {
        let d = tempfile::tempdir().unwrap();
        assert_eq!(pinned(d.path()), Some(false));
        let t = d.path().join(crate::trusted_hosts::DIR);
        std::fs::create_dir(&t).unwrap();
        std::fs::write(t.join(crate::trusted_hosts::FILE), b"{").unwrap();
        assert_eq!(pinned(d.path()), None, "reason: broken_list_read");
    }

    /// knives n / p: the code behind the GET runs nothing and connects nowhere
    #[test]
    fn the_guide_starts_nothing_and_connects_nowhere() {
        let src = include_str!("guide.rs");
        let code = src.split("#[cfg(test)]").next().unwrap();
        for bad in [
            "Command::new",
            "host_ssh",
            "TcpStream",
            "crate::hosts::",
            "probe(",
        ] {
            assert!(!code.contains(bad), "reason: guide_runs {bad}");
        }
    }

    /// knife a: nothing outside this module, its route and its act reads it
    #[test]
    fn no_gate_reads_the_guide() {
        let src = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
        let mut hits = Vec::new();
        let mut stack = vec![src];
        while let Some(dir) = stack.pop() {
            for e in std::fs::read_dir(&dir).unwrap() {
                let p = e.unwrap().path();
                if p.is_dir() {
                    stack.push(p);
                    continue;
                }
                if p.extension().is_none_or(|x| x != "rs") || p.ends_with("guide.rs") {
                    continue;
                }
                let t = std::fs::read_to_string(&p).unwrap();
                for (n, line) in t.lines().enumerate() {
                    if line.contains("guide::") || line.contains("guide/done") {
                        hits.push(format!(
                            "{}:{}: {}",
                            p.file_name().unwrap().to_string_lossy(),
                            n + 1,
                            line.trim()
                        ));
                    }
                }
            }
        }
        // the route, the act's arm and its list entry, and the start-up call only
        let allowed = ["api.rs", "seat_input.rs", "server.rs"];
        let bad: Vec<&String> = hits
            .iter()
            .filter(|h| !allowed.iter().any(|a| h.starts_with(&format!("{a}:"))))
            .collect();
        assert!(bad.is_empty(), "reason: guide_read_elsewhere {bad:?}");
        // api.rs 1, seat_input.rs 3 (list entry, arm, call), server.rs 1
        assert!(hits.len() <= 5, "reason: guide_used_more {hits:?}");
    }
}
