//! The receiving side (B1 S3 part 1): `take`, `close`, `wait` (dm_take.py), on the store.
//!
//! Same answers as the frozen dm_take.py, one difference on purpose: 1.x reads the item,
//! moves it, then writes the receiver's note (`taken_rev`, `touched_at`, `wait`) from the
//! copy it read before, outside any lock. A `close` landing in between is written over
//! and the dm goes back to `read` (the S0 evidence). Here `take` and `wait` hold the
//! seat's transition lock from the first read to the last write; the moves re-enter it and
//! the note is written from a fresh read inside it. `close` writes no note: it reads and
//! checks under the lock, then lets go before the done move, whose evidence check may run
//! git (kept outside the lock as in 1.x); that move re-checks rev and status in its lock.
//!
//! Every function returns the JSON object dm_take.py returns (the CLI prints it), with
//! the full text under `text`.

use std::cell::RefCell;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::time::Duration;

use aib_dmq::actor::{parse_actor, ActorClass};
use aib_dmq::lock::{Level, TRANSITION_LOCK_TIMEOUT_MS};
use aib_dmq::norm::{is_dispatch, kind};
use aib_dmq::rev::{item_rev, IfRev};
use aib_dmq::spec::{Reason, Status};
use aib_dmq::transition::Outcome;
use aib_dmq::util::{py_isspace, py_strip, take_chars, truthy};
use serde_json::{json, Map, Value};

use crate::locks::{lock, LockError};
use crate::store::{Caller, ItemRead, Move, Store, StoreError};

/// dm_queue.WAIT_MAX_CHARS.
pub const WAIT_MAX_CHARS: usize = 300;
/// dm_queue.TIDY_RELAY_MAX_BYTES: a long-message file bigger than this is not read.
pub const RELAY_MAX_BYTES: u64 = 200_000;
/// dm_queue.TZ_NAME is Asia/Taipei: UTC+8, no daylight time since 1979.
const TAIPEI_OFFSET_S: i64 = 8 * 3600;
/// O_NONBLOCK without a libc dependency (the value differs by kernel).
#[cfg(any(
    target_os = "macos",
    target_os = "ios",
    target_os = "freebsd",
    target_os = "openbsd",
    target_os = "netbsd",
    target_os = "dragonfly"
))]
const O_NONBLOCK: i32 = 0x0004;
#[cfg(all(
    unix,
    not(any(
        target_os = "macos",
        target_os = "ios",
        target_os = "freebsd",
        target_os = "openbsd",
        target_os = "netbsd",
        target_os = "dragonfly"
    ))
))]
const O_NONBLOCK: i32 = 0o4000;

const UNREAD: [&str; 3] = ["queued", "delivered", "hold_human"];
const CLOSED: [&str; 3] = ["done", "archived", "deleted"];

/// What a call needs from the caller's world: who is calling, where long messages may
/// live, and the time. The CLI builds it from the environment; nothing here reads it.
pub struct Ctx<'a> {
    pub caller: &'a Caller,
    /// The human-actor gate (`_human_from_seat`): the caller runs inside a seat.
    pub caller_is_seat: bool,
    /// dm_relay.relay_dirs, already resolved (AIB_DM_RELAY_DIR, then <home>/pending).
    pub relay_dirs: &'a [PathBuf],
    pub now: i64,
}

/// dm_relay.relay_dirs from an environment lookup (AIB_DM_RELAY_DIR, then
/// `<EZ2_AIB_HOME or ~/.ez2-aib>/pending`, `~` being HOME, else USERPROFILE as Python's
/// expanduser does on Windows), each resolved; a folder that does not exist is left out
/// (no long-message file can be in it).
pub fn relay_dirs(env: impl Fn(&str) -> Option<String>) -> Vec<PathBuf> {
    let nonempty = |k: &str| env(k).filter(|v| !v.is_empty());
    let home = nonempty("EZ2_AIB_HOME").map(PathBuf::from).or_else(|| {
        nonempty("HOME")
            .or_else(|| nonempty("USERPROFILE"))
            .map(|h| Path::new(&h).join(".ez2-aib"))
    });
    [
        nonempty("AIB_DM_RELAY_DIR").map(PathBuf::from),
        home.map(|h| h.join("pending")),
    ]
    .into_iter()
    .flatten()
    .filter_map(|d| std::fs::canonicalize(d).ok())
    .collect()
}

/// dm_relay.relay_path_of: the path in a 「長訊已落檔(N 位元組[,來自 @x[ 的背景分身]]),請讀:<path>」
/// pointer, absolute paths only. The byte count is ASCII digits here (Python's `\d` also
/// takes other scripts' digits; dm-ctl only writes ASCII).
///
/// Known gap: only a `/`-rooted path counts, as in 1.x, so long messages are Unix only.
/// dm-ctl writes relay files on the Macs; a Windows seat sees the pointer line itself
/// until a Windows sender exists (drive paths get added here then, with a test).
pub fn relay_path_of(msg: &str) -> Option<&str> {
    let s = py_strip(msg);
    let rest = s.strip_prefix("長訊已落檔(")?;
    let digits = rest.bytes().take_while(u8::is_ascii_digit).count();
    if digits == 0 {
        return None;
    }
    let mut rest = rest[digits..].strip_prefix(" 位元組")?;
    if let Some(r) = rest.strip_prefix(",來自 @") {
        let n = r
            .bytes()
            .take_while(|b| b.is_ascii_digit() || b.is_ascii_lowercase())
            .count();
        if n == 0 {
            return None;
        }
        rest = &r[n..];
        rest = rest.strip_prefix(" 的背景分身").unwrap_or(rest);
    }
    let tail = rest.strip_prefix("),請讀:")?;
    // `(.+)\s*$`: at least one char, and `.` does not cross a newline
    if tail.is_empty() || tail.contains('\n') {
        return None;
    }
    let path = py_strip(tail);
    path.starts_with('/').then_some(path)
}

/// dm_relay.relay_file_ok: resolved, named RELAY-dm-<[A-Za-z0-9_-]+>.txt, directly in one of
/// the relay folders. Anything else is not read (a short dm must not pull a local file in).
pub fn relay_file_ok(path: &Path, dirs: &[PathBuf]) -> bool {
    let Ok(rp) = std::fs::canonicalize(path) else {
        return false;
    };
    let name_ok = rp
        .file_name()
        .and_then(|n| n.to_str())
        .and_then(|n| n.strip_prefix("RELAY-dm-"))
        .and_then(|n| n.strip_suffix(".txt"))
        .is_some_and(|core| {
            !core.is_empty()
                && core
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        });
    name_ok && rp.parent().is_some_and(|p| dirs.iter().any(|d| d == p))
}

/// dm_queue._onboard_relay_text: a regular file only, at most RELAY_MAX_BYTES, UTF-8 with
/// replacement. Opened non-blocking on Unix like 1.x (a FIFO swapped in cannot hang the
/// caller, who holds the seat's transition lock), then checked on the open file.
fn relay_text(path: &Path) -> Option<String> {
    let mut o = std::fs::OpenOptions::new();
    o.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.custom_flags(O_NONBLOCK);
    }
    let f = o.open(path).ok()?;
    if !f.metadata().ok()?.is_file() {
        return None;
    }
    let mut raw = Vec::new();
    f.take(RELAY_MAX_BYTES + 1).read_to_end(&mut raw).ok()?;
    if raw.len() as u64 > RELAY_MAX_BYTES {
        return None;
    }
    Some(String::from_utf8_lossy(&raw).into_owned())
}

/// dm_take._text_of: (full text, relay path); the text is None when the pointer's file
/// cannot be read.
fn text_of(item: &Map<String, Value>, dirs: &[PathBuf]) -> (Option<String>, Option<String>) {
    let msg = item.get("msg").and_then(Value::as_str).unwrap_or("");
    let Some(path) = relay_path_of(msg) else {
        return (Some(msg.to_string()), None);
    };
    let p = Path::new(path);
    let text = if relay_file_ok(p, dirs) {
        relay_text(p)
    } else {
        None
    };
    (text, Some(path.to_string()))
}

/// Unicode general category Cf (format characters, Unicode 16): dm_take._clean_wait
/// drops them.
fn is_format(c: char) -> bool {
    matches!(c as u32,
        0x00AD | 0x0600..=0x0605 | 0x061C | 0x06DD | 0x070F | 0x0890..=0x0891 | 0x08E2
        | 0x180E | 0x200B..=0x200F | 0x202A..=0x202E | 0x2060..=0x2064 | 0x2066..=0x206F
        | 0xFEFF | 0xFFF9..=0xFFFB | 0x110BD | 0x110CD | 0x13430..=0x1343F
        | 0x1BCA0..=0x1BCA3 | 0x1D173..=0x1D17A | 0xE0001 | 0xE0020..=0xE007F)
}

/// dm_take._clean_wait: format and invisible characters dropped, control characters and
/// line / paragraph separators (Cc, Zl, Zp) turned into a space, runs of space made one,
/// at most WAIT_MAX_CHARS characters.
pub fn clean_wait(text: Option<&str>) -> String {
    let s: String = text
        .unwrap_or("")
        .chars()
        .filter(|c| !is_format(*c))
        .map(|c| {
            if c.is_control() || c == '\u{2028}' || c == '\u{2029}' {
                ' '
            } else {
                c
            }
        })
        .collect();
    let joined = s
        .split(py_isspace)
        .filter(|w| !w.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    take_chars(&joined, WAIT_MAX_CHARS)
}

/// dm_queue.taipei_stamp: "MM-DD HH:MM:SS" of that moment in Taipei.
pub fn taipei_stamp(epoch: i64) -> String {
    let t = epoch + TAIPEI_OFFSET_S;
    let (days, secs) = (t.div_euclid(86_400), t.rem_euclid(86_400));
    // civil_from_days (Howard Hinnant), proleptic Gregorian
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    format!(
        "{m:02}-{d:02} {:02}:{:02}:{:02}",
        secs / 3600,
        secs % 3600 / 60,
        secs % 60
    )
}

fn epoch_of(v: Option<&Value>) -> Option<i64> {
    match v? {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f.trunc() as i64)),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

/// dm_take._summary.
fn summary(
    item: &Map<String, Value>,
    text: Option<&str>,
    relay: Option<&str>,
    now: i64,
) -> Map<String, Value> {
    let v = Value::Object(item.clone());
    let pick = |k: &str| item.get(k).filter(|x| truthy(Some(x))).cloned();
    let wait = match item.get("wait") {
        Some(Value::Object(w)) => w.get("text").cloned().unwrap_or(Value::Null),
        _ => Value::Null,
    };
    let mut m = Map::new();
    m.insert("id".into(), item.get("id").cloned().unwrap_or(Value::Null));
    m.insert("rev".into(), json!(item_rev(&v)));
    m.insert(
        "status".into(),
        item.get("status").cloned().unwrap_or(Value::Null),
    );
    m.insert(
        "from".into(),
        pick("sender_label").unwrap_or_else(|| json!("未知")),
    );
    m.insert("kind".into(), json!(kind(item.get("kind"))));
    m.insert(
        "when".into(),
        pick("when")
            .unwrap_or_else(|| json!(taipei_stamp(epoch_of(item.get("ts")).unwrap_or(now)))),
    );
    m.insert("relay_path".into(), json!(relay));
    m.insert("wait".into(), wait);
    m.insert("chars".into(), json!(text.map(|t| t.chars().count())));
    m
}

fn refusal(reason: &str, message: &str) -> Value {
    json!({"ok": false, "reason": reason, "message": message})
}

/// The answer a move gives the CLI (the transition result without the item).
fn moved_json(m: &crate::store::Moved) -> Value {
    json!({"ok": m.reason == Reason::Ok, "reason": m.reason.as_str(), "rev": m.rev, "message": m.message})
}

fn live_item(
    store: &Store,
    seat: &str,
    id: &str,
) -> Result<Option<Map<String, Value>>, StoreError> {
    Ok(match store.read_item(seat, id)? {
        ItemRead::Item { item, .. }
            if item.get("status").and_then(Value::as_str) != Some("deleted") =>
        {
            Some(item)
        }
        _ => None,
    })
}

fn status_of(item: &Map<String, Value>) -> &str {
    item.get("status").and_then(Value::as_str).unwrap_or("")
}

enum Hold {
    Got(crate::locks::SeatLock),
    Timeout,
    /// No queue folder for this seat: nothing to take (and no folder is made for it).
    NoSeat,
}

/// Hold the seat's transition lock for a whole call (the moves inside re-enter it).
fn hold(store: &Store, seat: &str) -> Result<Hold, StoreError> {
    let dir = store.seat_dir(seat)?;
    if !dir.is_dir() {
        return Ok(Hold::NoSeat);
    }
    match lock(
        &dir,
        seat,
        Level::Transition,
        Duration::from_millis(TRANSITION_LOCK_TIMEOUT_MS),
    ) {
        Ok(g) => Ok(Hold::Got(g)),
        Err(LockError::Timeout { .. }) => Ok(Hold::Timeout),
        Err(e) => Err(e.into()),
    }
}

/// The lock wait ran out: audited like _transition's timeout (to = the move asked for).
fn lock_timeout(
    store: &Store,
    ctx: &Ctx<'_>,
    seat: &str,
    id: &str,
    to: &str,
    actor: &str,
) -> Value {
    let mut a = Map::new();
    a.insert("ts".into(), json!(ctx.now));
    a.insert("id".into(), json!(id));
    a.insert("to".into(), json!(to));
    a.insert("actor".into(), json!(actor));
    a.insert("ok".into(), json!(false));
    a.insert("reason".into(), json!("lock_timeout"));
    store.audit(seat, ctx.caller, a);
    json!({"ok": false, "reason": "lock_timeout", "rev": null, "message": Reason::LockTimeout.message()})
}

/// What a call answers: the JSON the CLI prints, and the receipts its moves made for the
/// outside sender of a global dm. The lock is already let go when this comes back; the
/// caller sends them (_global_delivery_for_transition), as 1.x does after its lock.
pub struct Answer {
    pub out: Value,
    pub receipts: Vec<Outcome>,
}

type Sink = RefCell<Vec<Outcome>>;

fn answer(sink: Sink, out: Value) -> Answer {
    Answer {
        out,
        receipts: sink.into_inner(),
    }
}

/// One move with an explicit rev (or none, for the system's own follow-ups).
#[allow(clippy::too_many_arguments)]
fn step(
    store: &Store,
    ctx: &Ctx<'_>,
    sink: &Sink,
    seat: &str,
    id: &str,
    to: Status,
    actor: &str,
    if_rev: Option<i64>,
    evidence: Option<&str>,
    why: Option<&str>,
) -> Result<crate::store::Moved, StoreError> {
    store.transition(
        &Move {
            seat,
            id,
            to,
            actor: Some(actor),
            why,
            evidence,
            if_rev: if_rev.map_or(IfRev::Absent, IfRev::Value),
            no_rev_check: if_rev.is_none(),
            expect_status: None,
            caller_is_seat: ctx.caller_is_seat,
            now: ctx.now,
        },
        ctx.caller,
    )?;
    if let Some(o) = m.outcome.take_if(|o| o.receipt.is_some()) {
        sink.borrow_mut().push(o);
    }
    Ok(m)
}

/// dm_queue.mark_read: read, then a notice (kind=notice) is archived by the system.
fn mark_read(
    store: &Store,
    ctx: &Ctx<'_>,
    sink: &Sink,
    seat: &str,
    id: &str,
    actor: &str,
    if_rev: i64,
) -> Result<crate::store::Moved, StoreError> {
    let m = step(
        store,
        ctx,
        sink,
        seat,
        id,
        Status::Read,
        actor,
        Some(if_rev),
        None,
        None,
    )?;
    if m.reason == Reason::Ok {
        if let Some(it) = live_item(store, seat, id)? {
            if it.get("kind").and_then(Value::as_str) == Some("notice") && status_of(&it) == "read"
            {
                step(
                    store,
                    ctx,
                    sink,
                    seat,
                    id,
                    Status::Archived,
                    "system",
                    None,
                    None,
                    Some("notice_read"),
                )?;
            }
        }
    }
    Ok(m)
}

/// dm_take._note under the held lock: a fresh read, the receiver's fields set, written
/// back (no rev bump: rev is the sender's content revision).
fn note(
    store: &Store,
    seat: &str,
    id: &str,
    fields: Map<String, Value>,
) -> Result<Option<Map<String, Value>>, StoreError> {
    let Some(mut it) = live_item(store, seat, id)? else {
        return Ok(None);
    };
    it.extend(fields);
    store.write_item(seat, &it)?;
    Ok(Some(it))
}

/// dm_take.take: the full text, marked read, the rev seen kept as `taken_rev`.
pub fn take(
    store: &Store,
    ctx: &Ctx<'_>,
    seat: &str,
    id: &str,
    actor: &str,
    wait: Option<&str>,
) -> Result<Answer, StoreError> {
    let sink = Sink::default();
    let out = take_in(store, ctx, &sink, seat, id, actor, wait)?;
    Ok(answer(sink, out))
}

fn take_in(
    store: &Store,
    ctx: &Ctx<'_>,
    sink: &Sink,
    seat: &str,
    id: &str,
    actor: &str,
    wait: Option<&str>,
) -> Result<Value, StoreError> {
    let me = parse_actor(Some(actor));
    if !me
        .as_ref()
        .is_some_and(|a| a.class == ActorClass::Seat && a.ident.as_deref() == Some(seat))
    {
        return Ok(refusal("actor", Reason::Actor.message()));
    }
    let _held = match hold(store, seat)? {
        Hold::Got(g) => g,
        Hold::Timeout => return Ok(lock_timeout(store, ctx, seat, id, "read", actor)),
        Hold::NoSeat => return Ok(refusal("not_found", Reason::NotFound.message())),
    };
    let Some(item) = live_item(store, seat, id)? else {
        return Ok(refusal("not_found", Reason::NotFound.message()));
    };
    let (text, relay) = text_of(&item, ctx.relay_dirs);
    let Some(text) = text else {
        return Ok(
            json!({"ok": false, "reason": "relay_unreadable", "relay_path": relay,
                         "message": "長訊落檔讀不到(不存在 / 不是一般檔 / 太大),沒有標已讀"}),
        );
    };
    let seen = item_rev(&Value::Object(item.clone()));
    let status = status_of(&item).to_string();
    if UNREAD.contains(&status.as_str()) {
        let r = mark_read(store, ctx, sink, seat, id, actor, seen)?;
        if r.reason != Reason::Ok {
            return Ok(moved_json(&r));
        }
    } else if CLOSED.contains(&status.as_str()) {
        let mut out = summary(&item, Some(&text), relay.as_deref(), ctx.now);
        out.insert("ok".into(), json!(true));
        out.insert("reason".into(), json!("already_closed"));
        out.insert("text".into(), json!(text));
        return Ok(Value::Object(out));
    } else if status != "read" {
        return Ok(
            json!({"ok": false, "reason": "transition", "status": status,
                         "message": Reason::Transition.message()}),
        );
    }
    let mut fields = Map::new();
    fields.insert("taken_rev".into(), json!(seen));
    fields.insert("touched_at".into(), json!(ctx.now));
    let w = clean_wait(wait);
    if wait.is_some() && !w.is_empty() {
        fields.insert("wait".into(), json!({"text": w, "at": ctx.now}));
    }
    let mut cur = live_item(store, seat, id)?.unwrap_or(item);
    if status_of(&cur) == "read" {
        // a notice archived right after its read gets no note
        if let Some(n) = note(store, seat, id, fields)? {
            cur = n;
        }
    }
    let mut out = summary(&cur, Some(&text), relay.as_deref(), ctx.now);
    out.insert("ok".into(), json!(true));
    out.insert("reason".into(), json!("ok"));
    out.insert("text".into(), json!(text));
    if status_of(&cur) == "read" {
        let mut next = Map::new();
        next.insert(
            "close".into(),
            json!(format!(
                "close --id {id} --evidence <PR 網址 / commit / 檔 / 回信 q_ id> 或 --why <理由>"
            )),
        );
        if !is_dispatch(&Value::Object(cur.clone())) {
            next.insert(
                "reply_and_close".into(),
                json!(format!(
                    "aib dm <位址> --close {id} \"…\"(回信送達才結這封)"
                )),
            );
        }
        if !truthy(out.get("wait")) {
            next.insert(
                "wait".into(),
                json!(format!(
                    "還沒做完 ⇒ wait --id {id} \"等誰 / 等什麼\"(超過 4 小時沒寫會被提醒)"
                )),
            );
        }
        out.insert("next".into(), Value::Object(next));
    }
    Ok(Value::Object(out))
}

/// `int(taken) != cur` for a stored taken_rev; a value Python's int() would choke on
/// counts as changed (refuse and ask for a new take, rather than crash or pass).
fn taken_differs(taken: &Value, cur: i64) -> bool {
    let n = match taken {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f.trunc() as i64)),
        Value::String(s) => py_strip(s).parse::<i64>().ok(),
        Value::Bool(b) => Some(i64::from(*b)),
        _ => None,
    };
    n != Some(cur)
}

/// dm_take.close: read (if still unread) and done in one call, evidence or a reason
/// required; refused when the dm changed after the receiver's take.
pub fn close(
    store: &Store,
    ctx: &Ctx<'_>,
    seat: &str,
    id: &str,
    actor: &str,
    evidence: Option<&str>,
    why: Option<&str>,
) -> Result<Answer, StoreError> {
    let sink = Sink::default();
    let out = close_in(store, ctx, &sink, seat, id, actor, evidence, why)?;
    Ok(answer(sink, out))
}

#[allow(clippy::too_many_arguments)]
fn close_in(
    store: &Store,
    ctx: &Ctx<'_>,
    sink: &Sink,
    seat: &str,
    id: &str,
    actor: &str,
    evidence: Option<&str>,
    why: Option<&str>,
) -> Result<Value, StoreError> {
    let has_why = why.is_some_and(|w| !py_strip(w).is_empty());
    if !(evidence.is_some_and(|e| !e.is_empty()) || has_why) {
        return Ok(refusal("evidence", Reason::Evidence.message()));
    }
    let held = match hold(store, seat)? {
        Hold::Got(g) => g,
        Hold::Timeout => return Ok(lock_timeout(store, ctx, seat, id, "done", actor)),
        Hold::NoSeat => return Ok(refusal("not_found", Reason::NotFound.message())),
    };
    let Some(item) = live_item(store, seat, id)? else {
        return Ok(refusal("not_found", Reason::NotFound.message()));
    };
    let cur = item_rev(&Value::Object(item.clone()));
    let status = status_of(&item).to_string();
    let (text, relay) = text_of(&item, ctx.relay_dirs);
    let unread = UNREAD.contains(&status.as_str());
    if unread {
        if text.is_none() {
            return Ok(
                json!({"ok": false, "reason": "relay_unreadable", "relay_path": relay,
                             "message": "長訊落檔讀不到,沒有標已讀也沒有結"}),
            );
        }
        let r = mark_read(store, ctx, sink, seat, id, actor, cur)?;
        if r.reason != Reason::Ok {
            return Ok(moved_json(&r));
        }
    } else if status == "read" {
        if let Some(taken) = item.get("taken_rev").filter(|t| !t.is_null()) {
            if taken_differs(taken, cur) {
                return Ok(
                    json!({"ok": false, "reason": "rev_mismatch", "rev": cur, "taken_rev": taken,
                    "message": format!("這封在你 take 之後被改過(rev {} → {cur}),請先重新 take 看新內容", py_repr(taken))}),
                );
            }
        }
    } else {
        return Ok(
            json!({"ok": false, "reason": "transition", "status": status,
                         "message": Reason::Transition.message()}),
        );
    }
    if live_item(store, seat, id)?.as_ref().map(status_of) != Some("read") {
        return Ok(json!({"ok": true, "reason": "already_closed", "id": id,
                         "message": "讀過就被系統備存了(知會信),不用再結"}));
    }
    // the evidence check may run git (up to 10 s a candidate): not under the lock, as 1.x.
    // The done move re-checks the rev and the status inside its own lock.
    drop(held);
    let r = step(
        store,
        ctx,
        sink,
        seat,
        id,
        Status::Done,
        actor,
        Some(cur),
        evidence,
        why,
    )?;
    let mut out = moved_json(&r);
    out["id"] = json!(id);
    if unread && r.reason == Reason::Ok {
        out["text"] = json!(text);
        out["relay_path"] = json!(relay);
    }
    Ok(out)
}

/// `%s` of a taken_rev in the refusal text (a string prints bare, like Python).
fn py_repr(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        other => other.to_string(),
    }
}

/// dm_take.set_wait: what an in-progress dm waits for (empty clears it). The receiver or
/// its tidy-up agent (`agent:<seat>/<id>`) only; a `read` dm only.
pub fn set_wait(
    store: &Store,
    ctx: &Ctx<'_>,
    seat: &str,
    id: &str,
    actor: &str,
    text: Option<&str>,
) -> Result<Answer, StoreError> {
    let out = set_wait_in(store, ctx, seat, id, actor, text)?;
    Ok(answer(Sink::default(), out))
}

fn set_wait_in(
    store: &Store,
    ctx: &Ctx<'_>,
    seat: &str,
    id: &str,
    actor: &str,
    text: Option<&str>,
) -> Result<Value, StoreError> {
    let ok_actor = parse_actor(Some(actor)).is_some_and(|a| match (a.class, a.ident.as_deref()) {
        (ActorClass::Seat, Some(i)) => i == seat,
        (ActorClass::Agent, Some(i)) => i.split('/').next() == Some(seat),
        _ => false,
    });
    if !ok_actor {
        return Ok(refusal("actor", Reason::Actor.message()));
    }
    let _held = match hold(store, seat)? {
        Hold::Got(g) => g,
        Hold::Timeout => return Ok(lock_timeout(store, ctx, seat, id, "wait", actor)),
        Hold::NoSeat => return Ok(refusal("not_found", Reason::NotFound.message())),
    };
    let Some(item) = live_item(store, seat, id)? else {
        return Ok(refusal("not_found", Reason::NotFound.message()));
    };
    if status_of(&item) != "read" {
        return Ok(
            json!({"ok": false, "reason": "transition", "status": item.get("status"),
                         "message": "只有進行中(已讀未結)的信能寫等什麼"}),
        );
    }
    let t = clean_wait(text);
    let wait_v = if t.is_empty() {
        Value::Null
    } else {
        json!({"text": t, "at": ctx.now})
    };
    let mut fields = Map::new();
    fields.insert("wait".into(), wait_v);
    fields.insert("touched_at".into(), json!(ctx.now));
    note(store, seat, id, fields)?;
    let t_or_none = if t.is_empty() { Value::Null } else { json!(t) };
    let mut a = Map::new();
    a.insert("ts".into(), json!(ctx.now));
    a.insert("id".into(), json!(id));
    a.insert("to".into(), json!("wait"));
    a.insert("actor".into(), json!(actor));
    a.insert("ok".into(), json!(true));
    a.insert("reason".into(), json!("ok"));
    a.insert("wait".into(), t_or_none.clone());
    store.audit(seat, ctx.caller, a);
    Ok(json!({"ok": true, "reason": "ok", "id": id, "wait": t_or_none}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn relay_pointer_forms() {
        assert_eq!(
            relay_path_of("長訊已落檔(936 位元組,來自 @043dd),請讀:/a/RELAY-dm-x.txt"),
            Some("/a/RELAY-dm-x.txt")
        );
        assert_eq!(
            relay_path_of("  長訊已落檔(1 位元組,來自 @ab12 的背景分身),請讀:/p  \n"),
            Some("/p")
        );
        assert_eq!(relay_path_of("長訊已落檔(9 位元組),請讀:/p"), Some("/p"));
        assert_eq!(relay_path_of("長訊已落檔(9 位元組),請讀:rel/p"), None);
        assert_eq!(relay_path_of("長訊已落檔( 位元組),請讀:/p"), None);
        assert_eq!(relay_path_of("長訊已落檔(9 位元組,來自 @AB),請讀:/p"), None);
        assert_eq!(relay_path_of("長訊已落檔(9 位元組),請讀:/p\nmore"), None);
        assert_eq!(relay_path_of("hi"), None);
    }

    #[test]
    fn clean_wait_like_python() {
        assert_eq!(
            clean_wait(Some("  等\u{200b} 043dd\n\t回\u{2028}信 ")),
            "等 043dd 回 信"
        );
        assert_eq!(clean_wait(Some("a\u{202e}b\u{feff}c")), "abc");
        assert_eq!(clean_wait(None), "");
        assert_eq!(
            clean_wait(Some(&"字".repeat(400))).chars().count(),
            WAIT_MAX_CHARS
        );
    }

    #[test]
    fn taipei_stamp_is_that_moment() {
        // 2026-11-13 22:08:10 +08:00
        assert_eq!(taipei_stamp(1_794_578_890), "11-13 22:08:10");
        assert_eq!(taipei_stamp(0), "01-01 08:00:00");
        assert_eq!(taipei_stamp(951_782_400 - 8 * 3600), "02-29 00:00:00");
    }
}
