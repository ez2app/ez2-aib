//! Reading a seat's mail (B1dm-S4): which tab an item is on, the waiting-tab order, the
//! badges, the unread / stale counts per seat, the wait reminders, expiry and the age line
//! put in front of a delivered message. Translated from the frozen 1.x
//! `aib/src/cockpit/dm_queue.py` (`TAB_OF_STATUS`, `_waiting_key`, `tabs_payload`,
//! `brief_payload`, `stale_payload`, `wait_overdue`, `wait_stale`, `_expired`,
//! `list_items`, `list_for_box`, `delivery_text`); the contract is
//! `crates/aib-dmq/tests/fixtures/view-v1.json`, made from that Python.
//!
//! Pure: items are v2 item objects (`serde_json::Value`) the caller loaded (from the 1.x
//! files or the 2.0 message table); `order_ids` is the delivery order (1.x `order.json`),
//! `waiting_order` the person's manual order of delivered mail (1.x `waiting-order.json`);
//! `now` is epoch seconds. Not here: the long-message preview (reads the relay file), the
//! global / IM row fields, the triage tags (another file) and the screen focus state.

use std::collections::{BTreeMap, HashMap};

use serde_json::Value;

use crate::norm;
use crate::util::{is_none, py_int, py_strip, truthy};

/// The five tabs, in screen order.
pub const TABS: [&str; 5] = ["waiting", "read", "done", "archived", "deleted"];
/// Statuses shown on the waiting tab.
pub const WAITING_STATUSES: [&str; 4] = ["queued", "claimed", "hold_human", "delivered"];
/// Statuses counted as unread.
pub const UNREAD_STATUSES: [&str; 3] = ["queued", "delivered", "claimed"];
/// In progress (read) and untouched this long ⇒ a reminder.
pub const STALE_WAIT_S: i64 = 4 * 3600;
/// Unread longer than this many minutes counts as stuck.
pub const STALE_MINUTES_DEFAULT: i64 = 30;
/// Delivered this many minutes after sending ⇒ the age line says it may be out of date.
pub const MAYBE_STALE_MINUTES: i64 = 30;
const TAIPEI_OFFSET_S: i64 = 8 * 3600;

/// The tab of a status; anything unknown (or missing) is on the waiting tab.
pub fn tab_of_status(status: Option<&str>) -> &'static str {
    match status {
        Some("read") => "read",
        Some("done") => "done",
        Some("archived") => "archived",
        Some("deleted") => "deleted",
        _ => "waiting",
    }
}

/// Python `int(v)` on a JSON value; `None` where Python would raise.
pub fn int_of(v: Option<&Value>) -> Option<i64> {
    match v? {
        Value::Bool(b) => Some(i64::from(*b)),
        Value::Number(n) => n.as_i64().or_else(|| {
            n.as_f64()
                .filter(|f| f.is_finite())
                .map(|f| f.trunc() as i64)
        }),
        Value::String(s) => py_int(s),
        _ => None,
    }
}

/// Python `int(item.get(k) or 0)`.
fn int_or_zero(item: &Value, k: &str) -> i64 {
    let v = item.get(k);
    if truthy(v) {
        int_of(v).unwrap_or(0)
    } else {
        0
    }
}

fn status(item: &Value) -> Option<&str> {
    item.get("status").and_then(Value::as_str)
}

fn id(item: &Value) -> &str {
    item.get("id").and_then(Value::as_str).unwrap_or("")
}

/// The last time anyone touched it: read, wrote what it waits for, or took it.
pub fn touched_at(item: &Value) -> Option<i64> {
    let mut vals = vec![item.get("read_at"), item.get("touched_at")];
    if let Some(w) = item.get("wait").filter(|w| w.is_object()) {
        vals.push(w.get("at"));
    }
    vals.into_iter().filter_map(int_of).max()
}

/// `str(w.get("text") or "").strip()` is not empty.
fn has_wait_text(item: &Value) -> bool {
    let Some(w) = item.get("wait").filter(|w| w.is_object()) else {
        return false;
    };
    let text = w.get("text");
    if !truthy(text) {
        return false;
    }
    match text {
        Some(Value::String(s)) => !py_strip(s).is_empty(),
        _ => true,
    }
}

fn touched_over(item: &Value, now: i64) -> bool {
    touched_at(item).is_some_and(|t| now.saturating_sub(t) > STALE_WAIT_S)
}

/// In progress, says what it waits for, untouched for more than four hours.
pub fn wait_overdue(item: &Value, now: i64) -> bool {
    status(item) == Some("read") && has_wait_text(item) && touched_over(item, now)
}

/// In progress, says nothing about what it waits for, untouched for more than four hours.
pub fn wait_stale(item: &Value, now: i64) -> bool {
    status(item) == Some("read") && !has_wait_text(item) && touched_over(item, now)
}

/// Its `expires` time has come (a bad or missing `expires` never expires).
pub fn expired(item: &Value, now: i64) -> bool {
    let exp = item.get("expires");
    if is_none(exp) {
        return false;
    }
    int_of(exp).is_some_and(|e| e <= now)
}

/// Marked expired, or past `expires`, while still on the waiting tab.
pub fn expired_now(item: &Value, now: i64) -> bool {
    (truthy(item.get("expired")) || expired(item, now))
        && status(item).is_some_and(|s| WAITING_STATUSES.contains(&s))
}

/// A newer version of an earlier message, not yet read.
pub fn is_update(item: &Value) -> bool {
    truthy(item.get("supersedes")) && matches!(status(item), Some("queued" | "delivered"))
}

/// Whole minutes since it was sent (never negative); `None` when unknown.
pub fn age_min(item: &Value, now: i64) -> Option<i64> {
    let sent = item.get("sent_at");
    if is_none(sent) {
        return None;
    }
    int_of(sent).map(|s| now.saturating_sub(s).div_euclid(60).max(0))
}

/// The waiting-tab sort key: not yet delivered first, then from a person, then asking for a
/// decision, then updates, then priority, then delivery order (mail not in `order_ids`
/// after it, in the person's manual order, then the rest), then time, then id.
pub type WaitingKey = (u8, u8, u8, u8, usize, u64, i64, String);

/// [`WaitingKey`] of one item.
pub fn waiting_key(
    item: &Value,
    order_idx: &HashMap<String, usize>,
    waiting_idx: &HashMap<String, usize>,
) -> WaitingKey {
    let rid = id(item);
    let rank = match order_idx.get(rid) {
        Some(n) => *n as u64,
        None => 1_000_000_000 + waiting_idx.get(rid).map_or(1_000_000, |n| *n as u64),
    };
    (
        u8::from(row_status(item) == "delivered"),
        u8::from(norm::sender_kind(item.get("sender_kind")) != "human"),
        u8::from(norm::kind(item.get("kind")) != "decide"),
        u8::from(!is_update(item)),
        norm::priority_rank(norm::priority(item.get("priority"), "normal")),
        rank,
        int_or_zero(item, "ts"),
        rid.to_string(),
    )
}

fn index_of(ids: &[String]) -> HashMap<String, usize> {
    ids.iter()
        .enumerate()
        .map(|(n, i)| (i.clone(), n))
        .collect()
}

/// One tab: its rows (sorted) and the oldest row's time.
#[derive(Debug, Clone, PartialEq)]
pub struct Tab<'a> {
    pub rows: Vec<&'a Value>,
    pub oldest_ts: Option<i64>,
    /// `HH:MM` of the oldest row.
    pub oldest_when: Option<String>,
}

/// The `when` a row shows: the item's own, else its `ts` (or `now`) in Taipei.
pub fn row_when(item: &Value, now: i64) -> String {
    match item.get("when") {
        Some(v) if truthy(Some(v)) => v.as_str().map_or_else(|| v.to_string(), str::to_string),
        _ => {
            let ts = item.get("ts");
            taipei_stamp(if is_none(ts) {
                now
            } else {
                int_of(ts).unwrap_or(now)
            })
        }
    }
}

/// Python `s[6:11]` on a `MM-DD HH:MM:SS` stamp; `None` when empty.
fn hh_mm(when: &str) -> Option<String> {
    let s: String = when.chars().skip(6).take(5).collect();
    (!s.is_empty()).then_some(s)
}

fn oldest<'a>(rows: &[&'a Value]) -> Option<&'a Value> {
    // Python min(): the first of equal keys.
    rows.iter()
        .copied()
        .fold(None, |best: Option<&Value>, r| match best {
            Some(b) if int_or_zero(b, "ts") <= int_or_zero(r, "ts") => Some(b),
            _ => Some(r),
        })
}

/// All five tabs of a seat, sorted as the screen shows them (the screen must not sort
/// again). The waiting tab by [`waiting_key`]; the others newest first, then id.
pub fn tabs<'a>(
    items: &'a [Value],
    order_ids: &[String],
    waiting_order: &[String],
    now: i64,
) -> Vec<(&'static str, Tab<'a>)> {
    let mut by_tab: Vec<(&'static str, Vec<&'a Value>)> =
        TABS.iter().map(|t| (*t, Vec::new())).collect();
    let mut sorted: Vec<&Value> = items.iter().collect();
    sorted.sort_by(|a, b| id(a).cmp(id(b)));
    for item in sorted {
        let tab = tab_of_status(status(item));
        if let Some((_, rows)) = by_tab.iter_mut().find(|(t, _)| *t == tab) {
            rows.push(item);
        }
    }
    let (oidx, widx) = (index_of(order_ids), index_of(waiting_order));
    by_tab
        .into_iter()
        .map(|(t, mut rows)| {
            if t == "waiting" {
                rows.sort_by_cached_key(|r| waiting_key(r, &oidx, &widx));
            } else {
                rows.sort_by(|a, b| {
                    int_or_zero(b, "ts")
                        .cmp(&int_or_zero(a, "ts"))
                        .then_with(|| id(a).cmp(id(b)))
                });
            }
            let first = oldest(&rows);
            let tab = Tab {
                oldest_ts: first.map(|r| int_or_zero(r, "ts")),
                oldest_when: first.and_then(|r| hh_mm(&row_when(r, now))),
                rows,
            };
            (t, tab)
        })
        .collect()
}

/// The badge counts over the waiting and read tabs.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Badges {
    pub unread: usize,
    pub expired: usize,
    pub update: usize,
    pub hold: usize,
    pub im_hold: usize,
    pub global_hold: usize,
    pub decide: usize,
    pub human: usize,
    pub stale_wait: usize,
    pub wait_overdue: usize,
}

/// The status a row shows: a missing or empty one reads as queued.
pub fn row_status(item: &Value) -> &str {
    status(item).filter(|s| !s.is_empty()).unwrap_or("queued")
}

fn is_unread(item: &Value, now: i64) -> bool {
    UNREAD_STATUSES.contains(&row_status(item)) && !expired_now(item, now)
}

fn rows_of<'a, 'b>(tabs: &'b [(&str, Tab<'a>)], name: &str) -> &'b [&'a Value] {
    tabs.iter()
        .find(|(t, _)| *t == name)
        .map_or(&[], |(_, tab)| tab.rows.as_slice())
}

/// The badges of [`tabs`] output.
pub fn badges(tabs: &[(&str, Tab<'_>)], now: i64) -> Badges {
    let waiting = rows_of(tabs, "waiting");
    let read = rows_of(tabs, "read");
    let count = |rows: &[&Value], f: &dyn Fn(&Value) -> bool| rows.iter().filter(|r| f(r)).count();
    let held = |r: &Value| row_status(r) == "hold_human";
    let has = |r: &Value, k: &str| r.get(k).is_some_and(Value::is_object);
    Badges {
        unread: count(waiting, &|r| is_unread(r, now)),
        expired: count(waiting, &|r| expired_now(r, now)),
        update: count(waiting, &is_update),
        hold: count(waiting, &|r| held(r) && !has(r, "global") && !has(r, "im")),
        im_hold: count(waiting, &|r| held(r) && has(r, "im")),
        global_hold: count(waiting, &|r| held(r) && has(r, "global")),
        decide: count(waiting, &|r| norm::kind(r.get("kind")) == "decide"),
        human: count(waiting, &|r| {
            norm::sender_kind(r.get("sender_kind")) == "human"
        }),
        stale_wait: count(read, &|r| wait_stale(r, now)),
        wait_overdue: count(read, &|r| !wait_stale(r, now) && wait_overdue(r, now)),
    }
}

/// The oldest unread row of the waiting tab: (ts, `HH:MM`), for "N unread (oldest HH:MM)".
pub fn oldest_unread(tabs: &[(&str, Tab<'_>)], now: i64) -> Option<(i64, Option<String>)> {
    let waiting = tabs.iter().find(|(t, _)| *t == "waiting")?;
    let unread: Vec<&Value> = waiting
        .1
        .rows
        .iter()
        .copied()
        .filter(|r| is_unread(r, now))
        .collect();
    oldest(&unread).map(|r| (int_or_zero(r, "ts"), hh_mm(&row_when(r, now))))
}

/// `tabs --counts`: rows per tab, rows per status, and read rows that do not say what they
/// wait for.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Counts {
    pub tabs: Vec<(&'static str, usize)>,
    pub by_status: BTreeMap<String, usize>,
    pub read_without_wait: usize,
}

/// [`Counts`] of [`tabs`] output (the same wait rule as the screen's waiting tail).
pub fn counts(tabs: &[(&'static str, Tab<'_>)]) -> Counts {
    let mut by_status = BTreeMap::new();
    for (_, tab) in tabs {
        for r in &tab.rows {
            *by_status.entry(row_status(r).to_string()).or_insert(0) += 1;
        }
    }
    Counts {
        tabs: tabs.iter().map(|(t, tab)| (*t, tab.rows.len())).collect(),
        by_status,
        read_without_wait: rows_of(tabs, "read")
            .iter()
            .filter(|r| !has_wait_text(r))
            .count(),
    }
}

/// One seat's unread / stuck counts.
#[derive(Debug, Clone, PartialEq)]
pub struct Stale {
    pub unread: usize,
    pub stale: usize,
    pub expired: usize,
    pub minutes: i64,
    pub oldest_ts: Option<i64>,
    /// The oldest unread item's own `when`, as stored.
    pub oldest_when: Option<Value>,
    /// The stuck ones, in id order.
    pub ids: Vec<String>,
}

fn sent_or_ts(item: &Value) -> i64 {
    for k in ["sent_at", "ts"] {
        let v = item.get(k);
        if truthy(v) {
            return int_of(v).unwrap_or(0);
        }
    }
    0
}

/// Unread (queued / delivered / claimed and never read), of which sent more than `minutes`
/// ago; expired ones are counted apart and are not unread.
pub fn stale(items: &[Value], now: i64, minutes: i64) -> Stale {
    let cutoff = now.saturating_sub(minutes.saturating_mul(60));
    let mut sorted: Vec<&Value> = items.iter().collect();
    sorted.sort_by(|a, b| id(a).cmp(id(b)));
    let cand: Vec<&Value> = sorted
        .into_iter()
        .filter(|i| {
            status(i).is_some_and(|s| UNREAD_STATUSES.contains(&s)) && is_none(i.get("read_at"))
        })
        .collect();
    let gone = |i: &Value| truthy(i.get("expired")) || expired(i, now);
    let unread: Vec<&Value> = cand.iter().copied().filter(|i| !gone(i)).collect();
    let stuck: Vec<&Value> = unread
        .iter()
        .copied()
        .filter(|i| {
            !is_none(i.get("sent_at")) && int_of(i.get("sent_at")).is_some_and(|s| s < cutoff)
        })
        .collect();
    let first = unread
        .iter()
        .copied()
        .fold(None, |best: Option<&Value>, r| match best {
            Some(b) if sent_or_ts(b) <= sent_or_ts(r) => Some(b),
            _ => Some(r),
        });
    Stale {
        unread: unread.len(),
        stale: stuck.len(),
        expired: cand.iter().filter(|i| gone(i)).count(),
        minutes,
        oldest_ts: first.map(sent_or_ts),
        oldest_when: first.map(|i| i.get("when").cloned().unwrap_or(Value::Null)),
        ids: stuck.iter().map(|i| id(i).to_string()).collect(),
    }
}

/// The delivery-order list (1.x `list`): the items named in `order_ids`, in that order,
/// without the one the drainer holds (claimed); newest first when asked.
pub fn list<'a>(items: &'a [Value], order_ids: &[String], newest_first: bool) -> Vec<&'a Value> {
    let by_id: HashMap<&str, &Value> = items.iter().map(|i| (id(i), i)).collect();
    let mut out: Vec<&Value> = order_ids
        .iter()
        .filter_map(|i| by_id.get(i.as_str()).copied())
        .filter(|i| !matches!(status(i), Some("claimed" | "deleted")))
        .collect();
    if newest_first {
        out.reverse();
    }
    out
}

/// [`list`] in box order (1.x `list --box`): urgent → important → normal → reminder; within
/// one, first come first sent.
pub fn list_for_box<'a>(items: &'a [Value], order_ids: &[String]) -> Vec<&'a Value> {
    let idx = index_of(order_ids);
    let mut out = list(items, order_ids, false);
    out.sort_by_cached_key(|i| {
        (
            norm::priority_rank(norm::priority(i.get("priority"), "normal")),
            idx.get(id(i)).copied().unwrap_or(1_000_000_000),
            id(i).to_string(),
        )
    });
    out
}

/// (year, month, day) of a day count since 1970-01-01.
fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    (yoe + era * 400 + i64::from(month <= 2), month, day)
}

/// `MM-DD HH:MM:SS` in Taipei (UTC+8, no daylight saving) of epoch seconds.
pub fn taipei_stamp(epoch: i64) -> String {
    let local = epoch.saturating_add(TAIPEI_OFFSET_S);
    let (_, month, day) = civil_from_days(local.div_euclid(86_400));
    let s = local.rem_euclid(86_400);
    format!(
        "{month:02}-{day:02} {:02}:{:02}:{:02}",
        s / 3600,
        s % 3600 / 60,
        s % 60
    )
}

fn hms(epoch: i64) -> String {
    taipei_stamp(epoch).chars().skip(6).collect()
}

/// The text put in front of a delivered (or shown-with-age) message: when it was sent, now,
/// how long ago; "may be out of date" from 30 minutes; which one it replaces. A message
/// with no send time says so instead of guessing.
pub fn delivery_text(item: &Value, now: i64) -> String {
    let sent = {
        let s = item.get("sent_at");
        if truthy(s) {
            s
        } else {
            item.get("ts")
        }
    };
    let body = item.get("msg").and_then(Value::as_str).unwrap_or("");
    let mut head = match int_of(sent).filter(|s| *s > 0) {
        None => format!("本則發出時刻量不到(舊版佇列),現在 {}", hms(now)),
        Some(sent) => {
            let secs = now.saturating_sub(sent).max(0);
            let age = secs / 60;
            let elapsed = if age > 0 {
                format!("{age} 分 {} 秒", secs % 60)
            } else {
                format!("{secs} 秒")
            };
            let mut h = format!("本則發出於 {},現在 {},已過 {elapsed}", hms(sent), hms(now));
            if age >= MAYBE_STALE_MINUTES {
                h.push_str(";可能過時,動手前先確認還算數");
            }
            h
        }
    };
    let sup = item.get("supersedes");
    if truthy(sup) {
        let old_when = item
            .get("_supersedes_when")
            .and_then(Value::as_str)
            .map(|w| w.chars().skip(6).take(5).collect::<String>())
            .unwrap_or_default();
        let shown = if old_when.is_empty() {
            sup.and_then(Value::as_str).map_or_else(
                || sup.map(Value::to_string).unwrap_or_default(),
                str::to_string,
            )
        } else {
            old_when
        };
        head.push_str(&format!("(取代 {shown} 那則)"));
    }
    format!("{head}\n{body}")
}

/// What `show` prints: the message, or with the age line in front; a trailing newline is
/// added when the text has none (nothing for an empty message).
pub fn show_text(item: &Value, now: i64, with_age: bool) -> String {
    let msg = item.get("msg").and_then(Value::as_str).unwrap_or("");
    if msg.is_empty() {
        return String::new();
    }
    let text = if with_age {
        delivery_text(item, now)
    } else {
        msg.to_string()
    };
    if text.ends_with('\n') {
        text
    } else {
        format!("{text}\n")
    }
}
