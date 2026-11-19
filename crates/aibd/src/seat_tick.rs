//! The web page's 自動推進 round (519d0 ORDER-webonly-today 第 7 項, Andy 11-20 05:5x「我要今天
//! 可以關閉 終端機 用網頁版 來接替所有開發」): aibd types one sentence to a chosen seat every so
//! many minutes, as the terminal's `/loop` did.
//!
//! - The list is `<data>/seat_ticks.json`, written only by the built-in page a person paired
//!   (`seat_tick_set` / `seat_tick_clear`; `seat_tick_list` reads it). One entry per seat.
//! - A sentence goes in between turns only (`write_input_between_turns`): a seat in a turn, busy,
//!   queued or starting gets it on a later pass; a seat that is not running, takes no input or
//!   is gone is skipped for that period (one `seat.tick_skipped`).
//! - It types like the page does: only to a seat whose mode takes input (safe, or dgrs with
//!   AIBD_SEAT_INPUT_DGRS=1), through the same line builder.
//! - 急停: `seat_tick_pause {on}` stops every sentence at once and keeps the list (the pass ends
//!   at its next wake); clearing an entry stops that seat's.
//! - It is not a person typing: it does not reset the cabin's [`crate::seat_send::ROUND_LIMIT`]
//!   count of seat-to-seat messages (only `type_to_seat` does), so a timed sentence cannot keep
//!   two seats talking past that gate.
//! - The first sentence comes one period after the entry is first seen (a restart sends nothing
//!   at once). The pass runs on [`crate::timers::SEAT_TICK`] only while the list is not empty,
//!   so an aibd nobody asked has no wakeups.
//!
//! Like every local file this list is no defence against an AI on the same account: it decides
//! what aibd types, it proves nothing (GUIDE-same-user-ai-threat-model).

use crate::api::AppState;
use crate::guard::request_id;
use crate::sessions::InputError;
use aib_store::NewEvent;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};

pub const FILE: &str = "seat_ticks.json";
pub const SET: &str = "seat_tick_set";
pub const CLEAR: &str = "seat_tick_clear";
pub const LIST: &str = "seat_tick_list";
/// The page's 急停 (5d33a / 519d0 06:17): `{on: true}` stops every timed sentence at once and
/// keeps the list; `{on: false}` lets them go again.
pub const PAUSE: &str = "seat_tick_pause";

/// The shortest and longest period, in minutes.
pub const MIN_EVERY: u32 = 5;
pub const MAX_EVERY: u32 = 24 * 60;
/// At most this many seats on the list, and this many characters in a sentence.
pub const MAX_TICKS: usize = 32;
pub const MAX_CHARS: usize = 2000;
/// The largest file read.
const MAX_FILE: u64 = 256 * 1024;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Tick {
    pub seat_id: String,
    pub every_min: u32,
    pub text: String,
    #[serde(default)]
    pub set_at: String,
}

#[derive(Debug, Default, Serialize, Deserialize)]
struct List {
    #[serde(default)]
    ticks: Vec<Tick>,
    #[serde(default)]
    paused: bool,
}

/// Whether the file says paused (a malformed file is not paused: it has no entries either).
pub fn parse_paused(bytes: &[u8]) -> bool {
    serde_json::from_slice::<List>(bytes).is_ok_and(|l| l.paused)
}

/// Why an entry is refused (the act's `code`).
pub fn check(t: &Tick) -> Result<(), &'static str> {
    if t.seat_id.is_empty() || t.seat_id.len() > 128 {
        return Err("seat_id_invalid");
    }
    if !(MIN_EVERY..=MAX_EVERY).contains(&t.every_min) {
        return Err("every_min_out_of_range");
    }
    let n = t.text.trim().chars().count();
    if n == 0 || n > MAX_CHARS {
        return Err("text_length");
    }
    Ok(())
}

/// The list in `bytes`; anything malformed is an empty list, an entry that fails [`check`] is
/// left out, a seat named twice keeps its first entry, at most [`MAX_TICKS`].
pub fn parse(bytes: &[u8]) -> Vec<Tick> {
    let list: List = serde_json::from_slice(bytes).unwrap_or_default();
    let mut out: Vec<Tick> = Vec::new();
    for t in list.ticks {
        if check(&t).is_ok() && !out.iter().any(|o| o.seat_id == t.seat_id) {
            out.push(t);
        }
        if out.len() >= MAX_TICKS {
            break;
        }
    }
    out
}

/// `list` with `t` in place of the seat's entry (or added).
pub fn with(mut list: Vec<Tick>, t: Tick) -> Result<Vec<Tick>, &'static str> {
    check(&t)?;
    if let Some(o) = list.iter_mut().find(|o| o.seat_id == t.seat_id) {
        *o = t;
        return Ok(list);
    }
    if list.len() >= MAX_TICKS {
        return Err("too_many_ticks");
    }
    list.push(t);
    Ok(list)
}

/// The seats whose sentence is due at `now` (unix seconds). A seat not in `last` yet is put
/// there at `now` and is not due (its first sentence comes one period later); seats no longer
/// on the list are dropped from `last`.
pub fn due(list: &[Tick], last: &mut HashMap<String, i64>, now: i64) -> Vec<Tick> {
    last.retain(|s, _| list.iter().any(|t| &t.seat_id == s));
    let mut out = Vec::new();
    for t in list {
        match last.get(&t.seat_id) {
            None => {
                last.insert(t.seat_id.clone(), now);
            }
            Some(&l) if now.saturating_sub(l) >= i64::from(t.every_min) * 60 => out.push(t.clone()),
            Some(_) => {}
        }
    }
    out
}

/// What one try did: sent, skipped for this period, or try again on the next pass.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    Sent,
    Skipped(&'static str),
    Later,
}

/// A write's error as an [`Outcome`]: a seat that will take it soon is tried again.
pub fn outcome_of(e: InputError) -> Outcome {
    match e {
        InputError::InTurn | InputError::Busy | InputError::Queued | InputError::Starting => {
            Outcome::Later
        }
        e @ (InputError::NoSession | InputError::NoInput | InputError::Closed) => {
            Outcome::Skipped(e.code())
        }
    }
}

fn path() -> Option<std::path::PathBuf> {
    crate::workspace::data_file(FILE)
}

/// The list on disk now and whether it is paused (no file, or none to read: empty, not paused).
pub fn load_all() -> (Vec<Tick>, bool) {
    let Some(p) = path() else {
        return (Vec::new(), false);
    };
    match std::fs::metadata(&p) {
        Ok(m) if m.is_file() && m.len() <= MAX_FILE => std::fs::read(&p)
            .map(|b| (parse(&b), parse_paused(&b)))
            .unwrap_or_default(),
        _ => (Vec::new(), false),
    }
}

/// The entries that may be sent now: none while paused.
pub fn load() -> Vec<Tick> {
    match load_all() {
        (_, true) => Vec::new(),
        (l, false) => l,
    }
}

fn save(list: &[Tick], paused: bool) -> Result<(), String> {
    let p = path().ok_or("no data folder")?;
    let tmp = p.with_extension("json.tmp");
    let body = serde_json::to_vec_pretty(&json!({ "ticks": list, "paused": paused }))
        .map_err(|e| e.to_string())?;
    std::fs::write(&tmp, body).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, &p).map_err(|e| e.to_string())
}

fn record(st: &AppState, kind: &str, payload: Value) {
    let Ok(id) = crate::sessions::new_id("ev_") else {
        return;
    };
    let event = NewEvent {
        event_id: id.clone(),
        aggregate_type: "seat_tick".into(),
        aggregate_id: id,
        sequence: 1,
        event_type: kind.into(),
        correlation_id: None,
        occurred_at: crate::sessions::now_rfc3339(),
        payload,
    };
    if let Err(e) = st.store.append(vec![event]) {
        tracing::warn!(kind, error = %e, "seat tick event not written");
    }
}

fn digest(text: &str) -> String {
    Sha256::digest(text.as_bytes())
        .iter()
        .take(8)
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// Type `t`'s sentence to its seat once (call it off the async runtime).
fn send(st: &AppState, t: &Tick) -> Outcome {
    let meta = match st.store.seat_meta(&t.seat_id) {
        Ok(Some(m)) => m,
        Ok(None) => return Outcome::Skipped("seat_not_found"),
        Err(_) => return Outcome::Later,
    };
    let user_page = crate::static_files::web_source() == "user";
    if let Err(code) = takes(
        meta.mode.as_deref(),
        user_page,
        crate::seat_input::dgrs_input_on(),
    ) {
        return Outcome::Skipped(code);
    }
    let Ok(line) = crate::seat_input::user_line(&t.text) else {
        return Outcome::Skipped("text_refused");
    };
    match st.sessions.write_input_between_turns(&t.seat_id, &line) {
        Ok(_) => Outcome::Sent,
        Err(e) => outcome_of(e),
    }
}

/// Whether a seat in `mode` takes a timed sentence now: while a user page serves the whole
/// machine only types to safe seats (守則〈補充 11-18〉5), judged at each send, not only when set
/// (5d33a PREREAD-seattick L1: a page that appears later stops the work / dgrs sentences).
fn takes(mode: Option<&str>, user_page: bool, dgrs_on: bool) -> Result<(), &'static str> {
    if user_page && mode != Some("safe") {
        return Err("user_page_serves_safe_only");
    }
    if !crate::seat_input::mode_takes_input(mode, dgrs_on) {
        return Err("seat_mode_takes_no_input");
    }
    Ok(())
}

static RUNNING: AtomicBool = AtomicBool::new(false);

/// Start the pass when the list is not empty and it is not running (at start-up and after
/// each `seat_tick_set`). It stops by itself once the list is empty.
pub fn ensure_started(st: AppState) {
    if load().is_empty() || RUNNING.swap(true, Ordering::SeqCst) {
        return;
    }
    let spawned = std::thread::Builder::new()
        .name("aibd-seat-tick".into())
        .spawn(move || {
            let mut last: HashMap<String, i64> = HashMap::new();
            loop {
                run(&st, &mut last);
                RUNNING.store(false, Ordering::SeqCst);
                // a set that came while the pass was ending found it running: look once more
                if load().is_empty() || RUNNING.swap(true, Ordering::SeqCst) {
                    break;
                }
            }
        });
    if spawned.is_err() {
        RUNNING.store(false, Ordering::SeqCst);
        tracing::error!("seat tick: could not start the thread");
    }
}

fn run(st: &AppState, last: &mut HashMap<String, i64>) {
    let op = crate::timers::SEAT_TICK.begin(None);
    let now = || crate::revive::unix_of(&crate::sessions::now_rfc3339()).unwrap_or(0);
    // the entries there now are seen at start: their first sentence is one period away
    let _ = due(&load(), last, now());
    while op.tick() {
        let list = load();
        if list.is_empty() {
            return;
        }
        let t_now = now();
        for t in due(&list, last, t_now) {
            match send(st, &t) {
                Outcome::Sent => {
                    last.insert(t.seat_id.clone(), t_now);
                    record(
                        st,
                        "seat.tick_sent",
                        json!({"seat_id": t.seat_id, "every_min": t.every_min,
                               "len": t.text.len(), "digest": digest(&t.text)}),
                    );
                }
                Outcome::Skipped(why) => {
                    last.insert(t.seat_id.clone(), t_now);
                    record(
                        st,
                        "seat.tick_skipped",
                        json!({"seat_id": t.seat_id, "reason": why}),
                    );
                }
                Outcome::Later => {}
            }
        }
    }
}

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({ "code": code, "message": message, "request_id": request_id() })),
    )
        .into_response()
}

/// `seat_tick_set {seat_id, every_min, text}`, `seat_tick_clear {seat_id}`, `seat_tick_list {}`:
/// the built-in page a person paired (not a user folder page, not a carried pairing: what it
/// sets is typed to a seat later with no one watching).
pub async fn act(st: AppState, page: crate::pairing::PageAccess, body: Value) -> Response {
    if page.carried || crate::seat_input::held_as_user_page(&page).await {
        return refuse(
            StatusCode::FORBIDDEN,
            "seat_tick_built_in_page_only",
            "a seat's timed sentence is set from the built-in page a person paired in this run",
        );
    }
    let kind = body.get("type").and_then(Value::as_str).unwrap_or_default();
    let seat_id = body
        .get("seat_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let (list, paused) = load_all();
    if kind == PAUSE {
        let on = body.get("on").and_then(Value::as_bool).unwrap_or(true);
        if let Err(e) = save(&list, on) {
            tracing::warn!(error = %e, "seat ticks not written");
            return refuse(
                StatusCode::INTERNAL_SERVER_ERROR,
                "internal",
                "list not written",
            );
        }
        record(
            &st,
            "seat.tick_paused",
            json!({ "paused": on, "ticks": list.len() }),
        );
        if !on {
            ensure_started(st);
        }
        return Json(json!({ "ticks": list, "paused": on })).into_response();
    }
    let next = match kind {
        LIST => return Json(json!({ "ticks": list, "paused": paused })).into_response(),
        CLEAR => {
            let n = list.len();
            let next: Vec<Tick> = list.into_iter().filter(|t| t.seat_id != seat_id).collect();
            if next.len() == n {
                return refuse(
                    StatusCode::NOT_FOUND,
                    "no_tick",
                    "no timed sentence for this seat",
                );
            }
            next
        }
        _ => {
            let t = Tick {
                seat_id: seat_id.clone(),
                every_min: body
                    .get("every_min")
                    .and_then(Value::as_u64)
                    .and_then(|n| u32::try_from(n).ok())
                    .unwrap_or(0),
                text: body
                    .get("text")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_owned(),
                set_at: crate::sessions::now_rfc3339(),
            };
            let store = st.store.clone();
            let sid = seat_id.clone();
            let known = tokio::task::spawn_blocking(move || store.seat_meta(&sid))
                .await
                .ok()
                .and_then(Result::ok)
                .flatten()
                .is_some();
            if !known {
                return refuse(StatusCode::NOT_FOUND, "seat_not_found", "no such seat");
            }
            match with(list, t) {
                Ok(l) => l,
                Err(code) => {
                    return refuse(StatusCode::BAD_REQUEST, code, "timed sentence not set")
                }
            }
        }
    };
    if let Err(e) = save(&next, paused) {
        tracing::warn!(error = %e, "seat ticks not written");
        return refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "list not written",
        );
    }
    let one = next.iter().find(|t| t.seat_id == seat_id);
    record(
        &st,
        if kind == CLEAR {
            "seat.tick_cleared"
        } else {
            "seat.tick_set"
        },
        json!({"seat_id": seat_id, "every_min": one.map(|t| t.every_min),
               "digest": one.map(|t| digest(&t.text))}),
    );
    ensure_started(st);
    Json(json!({ "ticks": next, "paused": paused })).into_response()
}

/// Whether `kind` is one of this module's acts.
pub fn is_act(kind: Option<&str>) -> bool {
    matches!(kind, Some(SET | CLEAR | LIST | PAUSE))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tick(seat: &str, every: u32, text: &str) -> Tick {
        Tick {
            seat_id: seat.into(),
            every_min: every,
            text: text.into(),
            set_at: String::new(),
        }
    }

    #[test]
    fn an_entry_needs_a_seat_a_period_in_range_and_a_sentence() {
        assert_eq!(check(&tick("s", 20, "繼續")), Ok(()));
        assert_eq!(check(&tick("", 20, "x")), Err("seat_id_invalid"));
        assert_eq!(
            check(&tick("s", MIN_EVERY - 1, "x")),
            Err("every_min_out_of_range")
        );
        assert_eq!(
            check(&tick("s", MAX_EVERY + 1, "x")),
            Err("every_min_out_of_range")
        );
        assert_eq!(check(&tick("s", 20, "  ")), Err("text_length"));
        let long = "字".repeat(MAX_CHARS + 1);
        assert_eq!(check(&tick("s", 20, &long)), Err("text_length"));
    }

    #[test]
    fn the_file_keeps_good_entries_once_per_seat_and_a_bad_file_is_empty() {
        let b = br#"{"ticks":[{"seat_id":"a","every_min":20,"text":"x"},
            {"seat_id":"a","every_min":30,"text":"y"},
            {"seat_id":"b","every_min":1,"text":"z"},
            {"seat_id":"c","every_min":60,"text":"w"}]}"#;
        let l = parse(b);
        assert_eq!(l.len(), 2, "reason: bad_or_twice_kept {l:?}");
        assert_eq!((l[0].seat_id.as_str(), l[0].every_min), ("a", 20));
        assert_eq!(l[1].seat_id, "c");
        assert!(parse(b"not json").is_empty());
        assert!(
            parse_paused(br#"{"ticks":[],"paused":true}"#),
            "reason: pause_not_read"
        );
        assert!(!parse_paused(b"not json") && !parse_paused(br#"{"ticks":[]}"#));
        let many: Vec<Value> = (0..MAX_TICKS + 3)
            .map(|i| json!({"seat_id": format!("s{i}"), "every_min": 20, "text": "x"}))
            .collect();
        let b = serde_json::to_vec(&json!({ "ticks": many })).unwrap();
        assert_eq!(parse(&b).len(), MAX_TICKS);
    }

    #[test]
    fn setting_replaces_the_seats_entry_and_the_list_has_a_limit() {
        let l = with(vec![tick("a", 20, "x")], tick("a", 30, "y")).unwrap();
        assert_eq!(l, vec![tick("a", 30, "y")], "reason: not_replaced");
        let full: Vec<Tick> = (0..MAX_TICKS)
            .map(|i| tick(&format!("s{i}"), 20, "x"))
            .collect();
        assert_eq!(
            with(full.clone(), tick("new", 20, "x")),
            Err("too_many_ticks")
        );
        assert!(
            with(full, tick("s0", 25, "x")).is_ok(),
            "reason: replace_refused_when_full"
        );
        assert_eq!(
            with(vec![], tick("a", 2, "x")),
            Err("every_min_out_of_range")
        );
    }

    #[test]
    fn a_sentence_is_due_one_period_after_it_was_first_seen_or_last_sent() {
        let list = vec![tick("a", 20, "x"), tick("b", 5, "y")];
        let mut last = HashMap::new();
        assert!(
            due(&list, &mut last, 1000).is_empty(),
            "reason: sent_at_once"
        );
        assert_eq!(last.get("a"), Some(&1000));
        assert!(due(&list, &mut last, 1000 + 5 * 60 - 1).is_empty());
        let d = due(&list, &mut last, 1000 + 5 * 60);
        assert_eq!(
            d.iter().map(|t| t.seat_id.as_str()).collect::<Vec<_>>(),
            ["b"]
        );
        let d = due(&list, &mut last, 1000 + 20 * 60);
        assert_eq!(d.len(), 2, "reason: a_not_due_at_its_period");
        // a seat taken off the list is forgotten; put back, it waits a whole period again
        let mut last = HashMap::from([("a".to_owned(), 0), ("gone".to_owned(), 0)]);
        let _ = due(&list[..1], &mut last, 10);
        assert!(!last.contains_key("gone"), "reason: removed_seat_kept");
        assert!(!last.contains_key("b"));
    }

    #[test]
    fn a_seat_that_will_take_it_soon_is_tried_again_and_the_rest_wait_a_period() {
        for e in [
            InputError::InTurn,
            InputError::Busy,
            InputError::Queued,
            InputError::Starting,
        ] {
            assert_eq!(outcome_of(e), Outcome::Later, "reason: retry_dropped {e:?}");
        }
        assert_eq!(
            outcome_of(InputError::NoSession),
            Outcome::Skipped("seat_not_running")
        );
        assert_eq!(
            outcome_of(InputError::Closed),
            Outcome::Skipped("seat_input_closed")
        );
        assert!(is_act(Some(SET)) && is_act(Some(CLEAR)) && is_act(Some(LIST)));
        assert!(is_act(Some(PAUSE)), "reason: pause_not_an_act");
        assert!(!is_act(Some("seat_input")) && !is_act(None));
    }

    #[test]
    fn a_user_page_that_appears_later_stops_all_but_safe_seats() {
        assert_eq!(takes(Some("work"), false, false), Ok(()));
        assert_eq!(takes(Some("safe"), true, false), Ok(()));
        assert_eq!(
            takes(Some("work"), true, false),
            Err("user_page_serves_safe_only"),
            "reason: user_page_work_typed"
        );
        assert_eq!(
            takes(Some("dgrs"), true, true),
            Err("user_page_serves_safe_only"),
            "reason: user_page_dgrs_typed"
        );
        assert_eq!(
            takes(Some("dgrs"), false, false),
            Err("seat_mode_takes_no_input")
        );
    }
}
