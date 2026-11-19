//! N-workspace usage (ORDER-workspace 第二波 7ebca; N-usage, the in-seat half, Claude first):
//! how full each 2.0 seat's context is, read from its own stream-json output.
//!
//! - The rule is 1.x `seat_fork.context_usage`: the last `assistant` message's `usage`,
//!   `input_tokens + cache_read_input_tokens + cache_creation_input_tokens` (a total of 0 is not
//!   a reading); the window is not told by the model name, so over [`SMALL_WINDOW`] it is
//!   [`LARGE_WINDOW`], otherwise [`SMALL_WINDOW`] (careful: a 1M seat reads full early).
//! - [`HANDOFF_PCT`] / [`RED_PCT`] are 1.x `aib/policy/ctx-thresholds.json` (85: hand over;
//!   95: stop new work). From 85 a seat is a slot-3 card ([`crate::slots`]).
//! - Kept in memory per session (gone on restart, like the seats' lines), at most
//!   [`MAX_SESSIONS`]: the numbers, the model name and when the last line came, never a word of
//!   the output. `last_output_at` is any line on stdout (a busy seat that writes nothing for long
//!   is a slot-4 card).
//! - `GET /api/v1/usage`: a page or the start-up token (a seat token cannot: guard).
//! - Codex (第三波 3; a codex seat speaks `codex app-server`, [`crate::codex_wire`]): the rule is
//!   1.x `statusline/codex/statusline-ez2-ctx.js` on app-server's `thread/tokenUsage/updated`:
//!   used = `tokenUsage.last.totalTokens`, window = `tokenUsage.modelContextWindow` (codex says
//!   it, nothing is guessed; none said = no reading), baseline [`CODEX_BASELINE`] (0 in 1.x);
//!   percent = 100 − round(remaining / window × 100), codex's own rounding; a window of 0 is
//!   100%. The model is `result.model` of the `thread/start` answer. Other brands open no seat in
//!   aibd yet (`aib_seatopen::PIPE_PROVIDERS`): nothing to read.
//! - `GET /api/v1/nodes/{id}/usage` (第四波, SPEC §15 「用了多久、花了多少」): one node's tokens per
//!   seat per hour, from the board's own count, not counted again here (§15 「跟流程表同一份資料，
//!   不另記」): `<data>/usage-cache.json` (1.x `board/stations-gen.js` `usageData`, `v: 2`): its
//!   `rows` `"<seat>|<unix hour>|<card>": [output, new input (with cache writes), re-read, replies]`
//!   and `cum` `{card: {seat: [output, new input, re-read, replies, active seconds, first, last]}}`.
//!   Numbers rounded as the board rounds them. No file: `source: "none"`, no seats.

use crate::api::AppState;
use axum::extract::{Path as UrlPath, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::sync::{LazyLock, Mutex, MutexGuard};

pub const SMALL_WINDOW: u64 = 200_000;
pub const LARGE_WINDOW: u64 = 1_000_000;
pub const HANDOFF_PCT: u8 = 85;
pub const RED_PCT: u8 = 95;
pub const MAX_SESSIONS: usize = 256;
/// 1.x `CODEX_CONTEXT_BASELINE_TOKENS`.
pub const CODEX_BASELINE: u64 = 0;

/// A codex `thread/tokenUsage/updated` line: `(used, window, pct)` (1.x codex rule).
pub fn codex_context_of(line: &Value) -> Option<(u64, u64, u8)> {
    if line.get("method").and_then(Value::as_str) != Some("thread/tokenUsage/updated") {
        return None;
    }
    let t = line.get("params")?.get("tokenUsage")?;
    let raw_window = t.get("modelContextWindow")?.as_u64()?;
    let raw_used = t.get("last")?.get("totalTokens")?.as_u64()?;
    // a window that cannot hold the baseline is full (1.x); a subtraction, not a comparison,
    // so a baseline of 0 is not an always-false test (clippy absurd_extreme_comparisons)
    let Some(window) = raw_window.checked_sub(CODEX_BASELINE).filter(|w| *w > 0) else {
        return Some((raw_window, raw_window, 100));
    };
    let used = raw_used.saturating_sub(CODEX_BASELINE);
    let remaining = window.saturating_sub(used);
    // round half up, as Math.round on a non-negative number
    let remaining_pct = (remaining.saturating_mul(200) / window)
        .div_ceil(2)
        .min(100);
    Some((
        used,
        window,
        u8::try_from(100 - remaining_pct).unwrap_or(100),
    ))
}

/// The context one assistant line reports (`None`: not an assistant line with a usage, or 0).
pub fn context_of(line: &Value) -> Option<u64> {
    if line.get("type").and_then(Value::as_str) != Some("assistant") {
        return None;
    }
    let u = line.get("message")?.get("usage")?.as_object()?;
    let total = [
        "input_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ]
    .iter()
    .map(|k| u.get(*k).and_then(Value::as_u64).unwrap_or(0))
    .fold(0u64, u64::saturating_add);
    (total > 0).then_some(total)
}

/// The model a line names (`system`/`init` or an assistant message).
pub fn model_of(line: &Value) -> Option<String> {
    let m = match line.get("type").and_then(Value::as_str) {
        Some("system") => line.get("model"),
        Some("assistant") => line.get("message").and_then(|m| m.get("model")),
        // codex: the answer to thread/start
        _ => line
            .get("result")
            .filter(|r| r.get("thread").is_some())
            .and_then(|r| r.get("model")),
    };
    m.and_then(Value::as_str)
        .filter(|s| !s.is_empty() && s.len() <= 120)
        .map(str::to_owned)
}

/// 1.x: over the small window it can only be the large one.
pub fn window_of(used: u64) -> u64 {
    if used > SMALL_WINDOW {
        LARGE_WINDOW
    } else {
        SMALL_WINDOW
    }
}

/// Whole percent used, at most 100.
pub fn pct(used: u64, window: u64) -> u8 {
    if window == 0 {
        return 100;
    }
    u8::try_from((used.saturating_mul(100) / window).min(100)).unwrap_or(100)
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Usage {
    pub session_id: String,
    pub seat_id: Option<String>,
    pub model: Option<String>,
    /// Tokens in the context at the last reading (`None`: no reading yet).
    pub used: Option<u64>,
    pub window: Option<u64>,
    pub pct: Option<u8>,
    /// When the last reading came.
    pub at: Option<String>,
    /// When the last stdout line came (any line).
    pub last_output_at: Option<String>,
    #[serde(skip)]
    order: u64,
}

/// One stdout line seen at `at`: the row after it.
pub fn apply(row: &mut Usage, raw: &[u8], at: &str) {
    row.last_output_at = Some(at.to_owned());
    // only a JSON object line can carry a reading; most lines are skipped without parsing
    if raw.first() != Some(&b'{')
        || !(has(raw, b"\"model\"") || has(raw, b"\"usage\"") || has(raw, b"tokenUsage"))
    {
        return;
    }
    let Ok(v) = serde_json::from_slice::<Value>(raw) else {
        return;
    };
    if let Some(m) = model_of(&v) {
        row.model = Some(m);
    }
    let reading = context_of(&v)
        .map(|used| {
            let w = window_of(used);
            (used, w, pct(used, w))
        })
        .or_else(|| codex_context_of(&v));
    if let Some((used, w, p)) = reading {
        row.used = Some(used);
        row.window = Some(w);
        row.pct = Some(p);
        row.at = Some(at.to_owned());
    }
}

fn has(hay: &[u8], needle: &[u8]) -> bool {
    hay.windows(needle.len()).any(|w| w == needle)
}

#[derive(Default)]
pub struct Board {
    inner: Mutex<(u64, BTreeMap<String, Usage>)>,
}

impl Board {
    fn lock(&self) -> MutexGuard<'_, (u64, BTreeMap<String, Usage>)> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn row<'a>(g: &'a mut (u64, BTreeMap<String, Usage>), session: &str) -> &'a mut Usage {
        if !g.1.contains_key(session) && g.1.len() >= MAX_SESSIONS {
            // the session seen longest ago leaves
            if let Some(old) =
                g.1.iter()
                    .min_by_key(|(_, u)| u.order)
                    .map(|(k, _)| k.clone())
            {
                g.1.remove(&old);
            }
        }
        g.0 += 1;
        let order = g.0;
        let r = g.1.entry(session.to_owned()).or_insert_with(|| Usage {
            session_id: session.to_owned(),
            seat_id: None,
            model: None,
            used: None,
            window: None,
            pct: None,
            at: None,
            last_output_at: None,
            order,
        });
        r.order = order;
        r
    }

    /// A session started for a seat (or for none).
    pub fn bind(&self, session: &str, seat: Option<&str>) {
        let mut g = self.lock();
        Self::row(&mut g, session).seat_id = seat.map(str::to_owned);
    }

    /// One stdout line of a session.
    pub fn observe(&self, session: &str, raw: &[u8], at: &str) {
        let mut g = self.lock();
        apply(Self::row(&mut g, session), raw, at);
    }

    pub fn all(&self) -> Vec<Usage> {
        let g = self.lock();
        g.1.values().cloned().collect()
    }
}

static BOARD: LazyLock<Board> = LazyLock::new(Board::default);

pub fn board() -> &'static Board {
    &BOARD
}

/// `GET /api/v1/usage`: `{handoff_pct, red_pct, usage: [row…]}`, newest session first.
pub async fn list(State(_st): State<AppState>) -> Response {
    let mut rows = board().all();
    rows.sort_by_key(|u| std::cmp::Reverse(u.order));
    Json(json!({ "handoff_pct": HANDOFF_PCT, "red_pct": RED_PCT, "usage": rows })).into_response()
}

/// The board's usage cache in the data folder.
pub const NODE_USAGE_FILE: &str = "usage-cache.json";
/// A cache larger than this is not read (today's is about 0.5 MB).
pub const NODE_USAGE_MAX_BYTES: u64 = 32 * 1024 * 1024;
/// Seconds in one point of a seat's line.
pub const HOUR_S: u64 = 3600;

fn round(v: &Value) -> u64 {
    v.as_f64()
        .filter(|f| f.is_finite() && *f > 0.0)
        .map_or(0, |f| f.round() as u64)
}

/// One node's answer from the board's cache (`None`: not the board's cache shape).
pub fn node_usage(cache: &Value, node: &str) -> Option<Value> {
    if cache.get("v").and_then(Value::as_u64) != Some(2) {
        return None;
    }
    let mut seats: BTreeMap<String, (Vec<[u64; 5]>, Option<Value>)> = BTreeMap::new();
    for (k, r) in cache.get("rows")?.as_object()? {
        let mut it = k.splitn(3, '|');
        let (Some(seat), Some(hb), Some(id)) = (it.next(), it.next(), it.next()) else {
            continue;
        };
        let (Ok(hb), Some(r)) = (hb.parse::<u64>(), r.as_array()) else {
            continue;
        };
        if id != node {
            continue;
        }
        let n = |i: usize| r.get(i).map_or(0, round);
        seats.entry(seat.to_owned()).or_default().0.push([
            hb.saturating_mul(HOUR_S),
            n(0),
            n(1),
            n(2),
            n(3),
        ]);
    }
    if let Some(c) = cache
        .get("cum")
        .and_then(|c| c.get(node))
        .and_then(Value::as_object)
    {
        for (seat, q) in c {
            let Some(q) = q.as_array() else { continue };
            let n = |i: usize| q.get(i).map_or(0, round);
            seats.entry(seat.clone()).or_default().1 = Some(json!({
                "output": n(0), "new_input": n(1), "reread": n(2), "replies": n(3),
                "active_s": n(4), "first": n(5), "last": n(6),
                // 043dd 11-18 21:18 (0eed7 L2): the same two times by the names the page reads
                "first_at": n(5), "last_at": n(6)
            }));
        }
    }
    let mut out: Vec<(u64, Value)> = seats
        .into_iter()
        .map(|(seat, (mut hours, total))| {
            hours.sort_unstable();
            let spent = total.as_ref().map_or_else(
                || hours.iter().map(|h| h[1] + h[2]).sum(),
                |t| t["output"].as_u64().unwrap_or(0) + t["new_input"].as_u64().unwrap_or(0),
            );
            (
                spent,
                json!({"seat_id": seat, "spent": spent, "hours": hours, "total": total}),
            )
        })
        .collect();
    // the seat that spent most first (the board's order); same spend by seat id
    out.sort_by_key(|(spent, _)| std::cmp::Reverse(*spent));
    Some(json!({
        "fields": ["hour_start", "output", "new_input", "reread", "replies"],
        "unit_s": HOUR_S,
        "seats": out.into_iter().map(|(_, v)| v).collect::<Vec<_>>(),
    }))
}

/// The ids under `node` in `tree` (`nodes[].parent`), children before grandchildren; a cycle or
/// a repeated id is walked once.
pub fn descendants(tree: &Value, node: &str) -> Vec<String> {
    let mut kids: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
    for n in tree
        .get("nodes")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let (Some(id), Some(p)) = (
            n.get("id").and_then(Value::as_str),
            n.get("parent").and_then(Value::as_str),
        ) else {
            continue;
        };
        if id != p {
            kids.entry(p).or_default().push(id);
        }
    }
    let mut seen: std::collections::BTreeSet<&str> = std::collections::BTreeSet::from([node]);
    let mut out: Vec<String> = Vec::new();
    let mut next: Vec<&str> = vec![node];
    while !next.is_empty() {
        let mut deeper = Vec::new();
        for p in next {
            for k in kids.get(p).into_iter().flatten() {
                if seen.insert(*k) {
                    out.push((*k).to_owned());
                    deeper.push(*k);
                }
            }
        }
        next = deeper;
    }
    out
}

/// 043dd 11-18 21:18 (0eed7 L1): `node` and everything under it summed from the cache's per
/// seat totals (the board's `costOf` `all`): `{spent, output, new_input, reread, replies,
/// active_s, nodes}`, `nodes` = how many of them (`node` included) have any use.
pub fn subtree_usage(cache: &Value, node: &str, under: &[String]) -> Value {
    let mut sum = [0u64; 5];
    let mut nodes = 0u64;
    let cum = cache.get("cum").and_then(Value::as_object);
    for id in std::iter::once(node).chain(under.iter().map(String::as_str)) {
        let Some(seats) = cum.and_then(|c| c.get(id)).and_then(Value::as_object) else {
            continue;
        };
        let mut any = false;
        for q in seats.values().filter_map(Value::as_array) {
            for (i, s) in sum.iter_mut().enumerate() {
                *s = s.saturating_add(q.get(i).map_or(0, round));
            }
            any = true;
        }
        nodes += u64::from(any);
    }
    json!({
        "spent": sum[0].saturating_add(sum[1]),
        "output": sum[0], "new_input": sum[1], "reread": sum[2], "replies": sum[3],
        "active_s": sum[4], "nodes": nodes
    })
}

/// `GET /api/v1/nodes/{id}/usage`: `{node_id, source, fields, unit_s, seats: [{seat_id, spent,
/// hours: [[hour_start, output, new_input, reread, replies]…], total}], subtree}`.
pub async fn of_node(UrlPath(id): UrlPath<String>) -> Response {
    if !crate::assign::node_id_ok(&id) {
        return (
            StatusCode::BAD_REQUEST,
            Json(
                json!({"code": "node_id", "message": "node_id has the wrong shape",
                "request_id": crate::guard::request_id()}),
            ),
        )
            .into_response();
    }
    let none = |id: &str| json!({"node_id": id, "source": "none", "fields": [], "unit_s": HOUR_S, "seats": []});
    let Some(path) = crate::workspace::data_file(NODE_USAGE_FILE) else {
        return Json(none(&id)).into_response();
    };
    let node = id.clone();
    let got = tokio::task::spawn_blocking(move || {
        let meta = std::fs::metadata(&path).ok()?;
        if meta.len() > NODE_USAGE_MAX_BYTES {
            return None;
        }
        let v: Value = serde_json::from_slice(&std::fs::read(&path).ok()?).ok()?;
        let under = crate::workspace::current_tree()
            .map(|t| descendants(&t, &node))
            .unwrap_or_default();
        let mut out = node_usage(&v, &node)?;
        out["subtree"] = subtree_usage(&v, &node, &under);
        Some(out)
    })
    .await
    .ok()
    .flatten();
    match got {
        Some(mut v) => {
            v["node_id"] = json!(id);
            v["source"] = json!("file");
            Json(v).into_response()
        }
        None => Json(none(&id)).into_response(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 043dd 0eed7 L1: the ids under a node, children then grandchildren, a cycle walked once.
    #[test]
    fn descendants_walk_the_whole_subtree_once() {
        let tree = json!({"nodes": [
            {"id": "A"}, {"id": "B", "parent": "A"}, {"id": "C", "parent": "B"},
            {"id": "D", "parent": "A"}, {"id": "E", "parent": "Z"},
            {"id": "X", "parent": "Y"}, {"id": "Y", "parent": "X"}, {"id": "S", "parent": "S"}
        ]});
        assert_eq!(
            descendants(&tree, "A"),
            ["B", "D", "C"],
            "reason: subtree_wrong"
        );
        assert_eq!(descendants(&tree, "C"), Vec::<String>::new());
        assert_eq!(descendants(&tree, "X"), ["Y"], "reason: cycle_not_once");
        assert_eq!(descendants(&tree, "S"), Vec::<String>::new());
    }

    /// 043dd 0eed7 L1: a node and everything under it summed (the board's costOf all).
    #[test]
    fn a_subtree_sums_every_seat_of_every_node_under_it() {
        let cache = json!({"v": 2, "rows": {}, "cum": {
            "A": {"s1": [10, 20, 30, 1, 5, 100, 200]},
            "B": {"s1": [1, 2, 3, 1, 1, 0, 0], "s2": [100, 200, 300, 2, 10, 0, 0]},
            "C": {"s3": [1000, 0, 0, 0, 0, 0, 0]},
            "Q": {"s9": [99999, 99999, 0, 0, 0, 0, 0]}
        }});
        let under = vec!["B".to_owned(), "C".to_owned(), "D".to_owned()];
        let t = subtree_usage(&cache, "A", &under);
        assert_eq!(t["output"], 1111, "reason: subtree_sum_wrong {t}");
        assert_eq!(t["new_input"], 222);
        assert_eq!(t["spent"], 1333);
        assert_eq!(t["reread"], 333);
        assert_eq!(t["replies"], 4);
        assert_eq!(t["active_s"], 16);
        assert_eq!(t["nodes"], 3, "reason: nodes_counted_wrong {t}");
        let own = subtree_usage(&cache, "A", &[]);
        assert_eq!(
            (own["spent"].as_u64(), own["nodes"].as_u64()),
            (Some(30), Some(1))
        );
    }

    const AT: &str = "2026-11-18T02:00:00.000Z";

    fn assistant(i: u64, r: u64, c: u64) -> String {
        json!({"type":"assistant","message":{"model":"claude-opus-5-5","content":[],
            "usage":{"input_tokens":i,"cache_read_input_tokens":r,"cache_creation_input_tokens":c,
            "output_tokens":999}}})
        .to_string()
    }

    #[test]
    fn the_context_is_the_three_input_counts_of_the_last_assistant_line() {
        let v: Value = serde_json::from_str(&assistant(10, 150_000, 20)).unwrap();
        assert_eq!(
            context_of(&v),
            Some(150_030),
            "reason: output_tokens_counted_or_one_missed"
        );
        let zero: Value = serde_json::from_str(&assistant(0, 0, 0)).unwrap();
        assert_eq!(context_of(&zero), None, "reason: zero_is_a_reading");
        let result = json!({"type":"result","usage":{"input_tokens":5}});
        assert_eq!(context_of(&result), None, "reason: result_line_read");
        // a user line can carry an assistant-shaped usage (a tool result echoing one): not a reading
        let user = json!({"type":"user","message":{"usage":{"input_tokens":5}}});
        assert_eq!(context_of(&user), None, "reason: non_assistant_line_read");
    }

    #[test]
    fn the_window_is_guessed_from_the_size_as_in_1x() {
        assert_eq!(window_of(200_000), SMALL_WINDOW);
        assert_eq!(window_of(200_001), LARGE_WINDOW);
        assert_eq!(pct(170_000, SMALL_WINDOW), 85);
        assert_eq!(pct(169_999, SMALL_WINDOW), 84, "reason: rounded_up");
        assert_eq!(pct(5_000_000, LARGE_WINDOW), 100);
        assert_eq!(pct(1, 0), 100);
    }

    #[test]
    fn a_row_keeps_the_last_reading_and_every_line_moves_last_output() {
        let mut r = Usage {
            session_id: "s".into(),
            seat_id: None,
            model: None,
            used: None,
            window: None,
            pct: None,
            at: None,
            last_output_at: None,
            order: 0,
        };
        apply(
            &mut r,
            br#"{"type":"system","subtype":"init","model":"claude-opus-5-5"}"#,
            "2026-11-18T01:00:00.000Z",
        );
        assert_eq!(r.model.as_deref(), Some("claude-opus-5-5"));
        assert_eq!(r.used, None);
        apply(&mut r, assistant(170_000, 0, 0).as_bytes(), AT);
        assert_eq!(
            (r.used, r.window, r.pct),
            (Some(170_000), Some(SMALL_WINDOW), Some(85))
        );
        assert_eq!(r.at.as_deref(), Some(AT));
        apply(&mut r, b"plain text, not json", "2026-11-18T03:00:00.000Z");
        assert_eq!(r.pct, Some(85), "reason: text_line_cleared_reading");
        assert_eq!(
            r.last_output_at.as_deref(),
            Some("2026-11-18T03:00:00.000Z")
        );
        apply(&mut r, br#"{"type":"assistant","message":{"usage":"#, AT);
        assert_eq!(r.pct, Some(85), "reason: broken_json_cleared_reading");
    }

    #[test]
    fn the_board_binds_a_seat_and_forgets_the_oldest_past_the_cap() {
        let b = Board::default();
        b.bind("s1", Some("seat_1"));
        b.observe("s1", assistant(300_000, 0, 0).as_bytes(), AT);
        let r = b.all().into_iter().find(|u| u.session_id == "s1").unwrap();
        assert_eq!(r.seat_id.as_deref(), Some("seat_1"));
        assert_eq!((r.window, r.pct), (Some(LARGE_WINDOW), Some(30)));
        for i in 0..MAX_SESSIONS + 5 {
            b.observe(&format!("x{i}"), b"x", AT);
        }
        let all = b.all();
        assert_eq!(all.len(), MAX_SESSIONS, "reason: board_grows_without_bound");
        assert!(
            all.iter().all(|u| u.session_id != "s1"),
            "reason: oldest_kept"
        );
    }

    fn codex(used: u64, window: Option<u64>) -> String {
        json!({"jsonrpc":"2.0","method":"thread/tokenUsage/updated","params":{"threadId":"t",
            "turnId":"u","tokenUsage":{"total":{"totalTokens":999_999,"inputTokens":1,
            "cachedInputTokens":0,"outputTokens":0,"reasoningOutputTokens":0},
            "last":{"totalTokens":used,"inputTokens":used,"cachedInputTokens":0,"outputTokens":0,
            "reasoningOutputTokens":0},"modelContextWindow":window}}})
        .to_string()
    }

    #[test]
    fn codex_reads_its_last_turn_against_the_window_it_names_rounded_as_codex() {
        let v = |s: String| serde_json::from_str::<Value>(&s).unwrap();
        assert_eq!(
            codex_context_of(&v(codex(217_600, Some(258_400)))),
            Some((217_600, 258_400, 84)),
            "reason: total_read_or_window_guessed"
        );
        // remaining 15.5% rounds up to 16: 84 used, not 85 (codex rounds the remaining first)
        assert_eq!(
            codex_context_of(&v(codex(845, Some(1_000)))).map(|r| r.2),
            Some(84)
        );
        assert_eq!(
            codex_context_of(&v(codex(850, Some(1_000)))).map(|r| r.2),
            Some(85)
        );
        assert_eq!(
            codex_context_of(&v(codex(5_000, Some(1_000)))).map(|r| r.2),
            Some(100)
        );
        assert_eq!(
            codex_context_of(&v(codex(0, Some(0)))).map(|r| r.2),
            Some(100)
        );
        assert_eq!(
            codex_context_of(&v(codex(10, None))),
            None,
            "reason: window_guessed"
        );
        let claude: Value = serde_json::from_str(&assistant(10, 0, 0)).unwrap();
        assert_eq!(codex_context_of(&claude), None);
    }

    #[test]
    fn a_codex_row_takes_the_model_from_thread_start_and_its_usage_lines() {
        let mut r = Usage {
            session_id: "c".into(),
            seat_id: None,
            model: None,
            used: None,
            window: None,
            pct: None,
            at: None,
            last_output_at: None,
            order: 0,
        };
        apply(
            &mut r,
            br#"{"jsonrpc":"2.0","id":2,"result":{"model":"gpt-5.5-codex","thread":{"id":"t1"},"cwd":"/x"}}"#,
            AT,
        );
        assert_eq!(
            r.model.as_deref(),
            Some("gpt-5.5-codex"),
            "reason: codex_model_missed"
        );
        apply(&mut r, codex(230_000, Some(258_400)).as_bytes(), AT);
        assert_eq!(
            (r.used, r.window, r.pct),
            (Some(230_000), Some(258_400), Some(89))
        );
        apply(
            &mut r,
            br#"{"jsonrpc":"2.0","method":"turn/completed","params":{}}"#,
            AT,
        );
        assert_eq!(r.pct, Some(89), "reason: other_codex_line_cleared_reading");
    }

    #[test]
    fn a_node_s_usage_is_the_board_s_rows_for_it_per_seat_per_hour() {
        // the shape of the board's usage-cache.json (11-18 12:29)
        let cache = json!({"v": 2, "files": {}, "rows": {
            "7b81e|498507|-": [27206, 446522, 9577454, 46],
            "7b81e|498507|S18": [10741.4, 19257.6, 5881800, 24],
            "7b81e|498508|S18": [32381, 313734, 11778122, 43],
            "ebb36|498508|S18": [2901, 3329, 824058, 6],
            "7b81e|498508|S180": [1, 1, 1, 1],
            "bad|x|S18": [1, 1, 1, 1]
        }, "cum": {"S18": {
            "7b81e": [238243, 1009505, 77975056, 380, 11542.2, 1794529487.093, 1794672535.275],
            "ebb36": [2901, 3329, 824058, 6, 72.03, 1794656532.475, 1794658641.055]
        }}});
        let v = node_usage(&cache, "S18").unwrap();
        let s = v["seats"].as_array().unwrap();
        assert_eq!(s.len(), 2, "reason: other_card_or_bad_row_read {v}");
        assert_eq!(s[0]["seat_id"], "7b81e", "reason: not_most_spent_first");
        assert_eq!(
            s[0]["hours"],
            json!([
                [498507 * 3600, 10741, 19258, 5881800, 24],
                [498508 * 3600, 32381, 313734, 11778122, 43]
            ]),
            "reason: hours_wrong"
        );
        assert_eq!(s[0]["spent"], 238243 + 1009505);
        assert_eq!(s[0]["total"]["active_s"], 11542);
        assert_eq!(s[1]["total"]["first"], 1794656532);
        let none = node_usage(&cache, "S19").unwrap();
        assert_eq!(none["seats"], json!([]));
        let mut v1 = cache.clone();
        v1["v"] = json!(1);
        assert!(node_usage(&v1, "S18").is_none(), "reason: other_shape_read");
    }
}
