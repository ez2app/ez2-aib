//! Action events (S107-product slice 1, IFACE-s107-product-a9136-1117 r1 §1; SPEC-s107-product
//! §2 / §10): the program that does something writes one line the moment it does it, carrying
//! the card it was for, so the board reads events instead of guessing from record text.
//!
//! - File: `<root>/state/<prog>/events/<YYYY-MM>.jsonl` (UTC month), one whole line per event,
//!   appended under `events.lock` in the same folder (the crate's lock: flock on Unix, LockFileEx
//!   on Windows).
//! - A failed write never changes what the calling program does or its exit code:
//!   [`append_or_warn`] says one stderr line and returns.
//! - Common fields on every line: `v`, `id`, `kind`, `at`, `seat`, `cards`, `status_item`,
//!   `host`, `outcome`; the kind's own fields beside them (they cannot overwrite a common one).
//! - Old month files go whole once their month ended more than `keep_days` ago ([`expire`]);
//!   the caller records each removal as its own `clean` event.

use serde_json::{json, Map, Value};
use std::collections::hash_map::RandomState;
use std::hash::{BuildHasher, Hasher};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::OnceLock;
use std::time::Duration;

/// The line format version.
pub const V: u64 = 1;
/// Retention when the configuration says nothing (SPEC-s107-product §10: `events_keep_days`).
pub const KEEP_DAYS_DEFAULT: u32 = 90;
/// One event line, newline included, is at most this many bytes.
pub const MAX_LINE: usize = 64 * 1024;
/// How long a writer waits for another writer's lock.
pub const LOCK_WAIT: Duration = Duration::from_secs(2);
/// The fields every event carries; a kind's own fields never replace them.
pub const COMMON: [&str; 9] = [
    "v",
    "id",
    "kind",
    "at",
    "seat",
    "cards",
    "status_item",
    "host",
    "outcome",
];

/// `<root>/state/<prog>/events`
pub fn dir(root: &Path, prog: &str) -> PathBuf {
    root.join("state").join(prog).join("events")
}

/// `2026-11.jsonl` for the UTC month of `ms`.
pub fn month_file(ms: i64) -> String {
    let (y, m, _) = crate::envelope::civil_from_days(ms.div_euclid(86_400_000));
    format!("{:04}-{:02}.jsonl", y.clamp(0, 9999), m)
}

/// The machine's own name: the top-level `"self"` of the hosts file (IFACE §5). Not the
/// system host name: no name configured ⇒ None, written as null.
pub fn self_host(hosts_json: &str) -> Option<String> {
    let v: Value = serde_json::from_str(hosts_json).ok()?;
    let s = v.get("self")?.as_str()?.trim();
    (!s.is_empty()).then(|| s.to_owned())
}

/// `AIB_HOSTS_FILE` when set and not empty, else `<root>/hosts.json`.
pub fn hosts_file(root: &Path, env: Option<&std::ffi::OsStr>) -> PathBuf {
    match env.filter(|e| !e.is_empty()) {
        Some(e) => PathBuf::from(e),
        None => root.join("hosts.json"),
    }
}

/// Card-like words of a status line, in order, each once: `[A-Z][A-Za-z0-9-]*` with a digit
/// in it, kept only when `is_node` knows it (host and knife names are not cards).
pub fn cards_in(item: &str, is_node: impl Fn(&str) -> bool) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let word = |c: char| c.is_ascii_alphanumeric() || c == '-';
    for w in item.split(|c: char| !word(c)) {
        let w = w.trim_end_matches('-');
        let starts = w.chars().next().is_some_and(|c| c.is_ascii_uppercase());
        if starts
            && w.chars().any(|c| c.is_ascii_digit())
            && is_node(w)
            && !out.iter().any(|o| o == w)
        {
            out.push(w.to_owned());
        }
    }
    out
}

/// The `item` of `<root>/state/seat-now/<seat>.json`; unreadable ⇒ None.
pub fn seat_item(root: &Path, seat: &str) -> Option<String> {
    if seat.is_empty() || seat.contains(['/', '\\', '.']) {
        return None;
    }
    let p = root
        .join("state")
        .join("seat-now")
        .join(format!("{seat}.json"));
    let v: Value = serde_json::from_str(&std::fs::read_to_string(p).ok()?).ok()?;
    v.get("item")?.as_str().map(str::to_owned)
}

/// Who and what an event is about. Built once per run, reused for each line.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Ctx {
    pub host: Option<String>,
    pub seat: Option<String>,
    pub cards: Vec<String>,
    pub status_item: Option<String>,
}

impl Ctx {
    /// The seat is `seat` when given, else `AIB_SEAT_CODE` (the caller passes its value); no
    /// seat ⇒ null and no cards, never guessed. `is_node` answers whether a word is a plan
    /// card id (unreadable plan ⇒ the caller passes `|_| false`, and cards stay empty).
    pub fn load(
        root: &Path,
        seat: Option<&str>,
        hosts_json: Option<&str>,
        is_node: impl Fn(&str) -> bool,
    ) -> Ctx {
        let seat = seat.map(str::trim).filter(|s| !s.is_empty());
        let status_item = seat.and_then(|s| seat_item(root, s));
        let cards = status_item
            .as_deref()
            .map(|i| cards_in(i, &is_node))
            .unwrap_or_default();
        Ctx {
            host: hosts_json.and_then(self_host),
            seat: seat.map(str::to_owned),
            cards,
            status_item,
        }
    }
}

/// This process's random tag, taken once (a machine with no name still never collides with
/// another machine with no name: 5d33a r0 L3).
fn process_tag() -> u64 {
    static TAG: OnceLock<u64> = OnceLock::new();
    *TAG.get_or_init(|| {
        let mut h = RandomState::new().build_hasher();
        h.write_u32(std::process::id());
        h.finish()
    })
}

static SEQ: AtomicU64 = AtomicU64::new(0);

/// `<host>:<ms>:<pid>:<seq>`, or `null-<8 hex>:<ms>:<pid>:<seq>` with no host: the dedupe key
/// when events from several machines are put together.
pub fn event_id(host: Option<&str>, ms: i64) -> String {
    let seq = SEQ.fetch_add(1, Ordering::Relaxed);
    let pid = std::process::id();
    match host {
        Some(h) => format!("{h}:{ms}:{pid}:{seq}"),
        None => format!("null-{:08x}:{ms}:{pid}:{seq}", process_tag() & 0xffff_ffff),
    }
}

/// One event object: the common fields, then the kind's own fields (a common name among them
/// is dropped, so a kind can never rewrite who / when / which card).
pub fn build(ctx: &Ctx, kind: &str, outcome: &str, at_ms: i64, extra: Map<String, Value>) -> Value {
    let mut o = Map::new();
    o.insert("v".into(), json!(V));
    o.insert("id".into(), json!(event_id(ctx.host.as_deref(), at_ms)));
    o.insert("kind".into(), json!(kind));
    o.insert("at".into(), json!(crate::envelope::rfc3339_ms(at_ms)));
    o.insert("seat".into(), json!(ctx.seat));
    o.insert("cards".into(), json!(ctx.cards));
    o.insert("status_item".into(), json!(ctx.status_item));
    o.insert("host".into(), json!(ctx.host));
    o.insert("outcome".into(), json!(outcome));
    for (k, v) in extra {
        if !COMMON.contains(&k.as_str()) {
            o.insert(k, v);
        }
    }
    Value::Object(o)
}

/// Append one event to its month file under the folder's lock; the folder is made when
/// missing. Returns the file written. Errors read `mkdir: …`, `lock …`, `write: …`.
pub fn append(root: &Path, prog: &str, ev: &Value, at_ms: i64) -> Result<PathBuf, String> {
    let d = dir(root, prog);
    std::fs::create_dir_all(&d).map_err(|e| format!("mkdir: {e}"))?;
    let _lock = crate::take_lock(&d.join("events.lock"), LOCK_WAIT)?;
    let f = d.join(month_file(at_ms));
    let line = serde_json::to_string(ev).map_err(|e| format!("json: {e}"))?;
    match crate::append(&f, &line, MAX_LINE) {
        Ok(()) => Ok(f),
        Err(crate::Append::TooLong(n)) => Err(format!("write: line over {n} bytes")),
        Err(crate::Append::Io(e)) => Err(format!("write: {e}")),
    }
}

/// [`append`], and on failure one stderr line `<prog>: event not written: …`. Never fails the
/// caller (events are for people to read, not a gate).
pub fn append_or_warn(root: &Path, prog: &str, ev: &Value, at_ms: i64) {
    if let Err(e) = append(root, prog, ev, at_ms) {
        eprintln!("{prog}: event not written: {e}");
    }
}

/// The first millisecond after the UTC month named by `YYYY-MM.jsonl`; None for any other name.
fn month_end_ms(name: &str) -> Option<i64> {
    let stem = name.strip_suffix(".jsonl")?;
    let (y, m) = stem.split_once('-')?;
    if y.len() != 4 || m.len() != 2 {
        return None;
    }
    let y: i64 = y.parse().ok()?;
    let m: i64 = m.parse().ok()?;
    if !(1..=12).contains(&m) {
        return None;
    }
    let (ny, nm) = if m == 12 { (y + 1, 1) } else { (y, m + 1) };
    Some(crate::envelope::days_from_civil(ny, nm, 1) * 86_400_000)
}

/// Remove month files whose whole month ended more than `keep_days` before `now_ms`; other
/// files are left alone. Returns each removed file with its size, for the caller's `clean`
/// events.
pub fn expire(root: &Path, prog: &str, keep_days: u32, now_ms: i64) -> Vec<(PathBuf, u64)> {
    let cutoff = now_ms - i64::from(keep_days) * 86_400_000;
    let Ok(rd) = std::fs::read_dir(dir(root, prog)) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for e in rd.flatten() {
        let name = e.file_name().to_string_lossy().into_owned();
        if !month_end_ms(&name).is_some_and(|end| end <= cutoff) {
            continue;
        }
        let bytes = e.metadata().map(|m| m.len()).unwrap_or(0);
        if std::fs::remove_file(e.path()).is_ok() {
            out.push((e.path(), bytes));
        }
    }
    out.sort();
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn month_files_are_named_by_utc_month() {
        // 2026-11-30T23:59:59.999Z and one ms later
        let last = crate::envelope::parse_rfc3339_ms("2026-11-30T23:59:59.999Z").expect("t");
        assert_eq!(month_file(last), "2026-11.jsonl");
        assert_eq!(month_file(last + 1), "2026-12.jsonl");
        assert_eq!(month_end_ms("2026-11.jsonl"), Some(last + 1));
        assert_eq!(
            month_end_ms("2026-12.jsonl").map(month_file),
            Some("2027-01.jsonl".into())
        );
        for bad in [
            "2026-13.jsonl",
            "26-11.jsonl",
            "2026-11.json",
            "events.lock",
        ] {
            assert_eq!(month_end_ms(bad), None, "{bad}");
        }
    }

    #[test]
    fn only_card_ids_in_the_tree_are_taken_from_a_status_line() {
        let tree = ["S79-u5", "S107-loop", "T06-c"];
        let is = |w: &str| tree.contains(&w);
        assert_eq!(cards_in("S79-u5 HOST7 A13 改", is), ["S79-u5"]);
        assert_eq!(
            cards_in("S107-loop d7c80e12 送審;T06-c、S107-loop 再一次", is),
            ["S107-loop", "T06-c"]
        );
        assert!(cards_in("沒有卡號", is).is_empty());
    }

    #[test]
    fn the_host_name_comes_only_from_the_hosts_file() {
        assert_eq!(
            self_host(r#"{"self": "lin-macmini", "hosts": []}"#).as_deref(),
            Some("lin-macmini")
        );
        assert_eq!(self_host(r#"{"hosts": []}"#), None);
        assert_eq!(self_host(r#"{"self": "  "}"#), None);
        assert_eq!(self_host("not json"), None);
        let r = Path::new("root");
        assert_eq!(hosts_file(r, None), r.join("hosts.json"));
        assert_eq!(hosts_file(r, Some("".as_ref())), r.join("hosts.json"));
        assert_eq!(
            hosts_file(r, Some("x/h.json".as_ref())),
            PathBuf::from("x/h.json")
        );
    }

    #[test]
    fn ids_never_repeat_and_a_nameless_machine_gets_a_random_tag() {
        let a = event_id(Some("andymini"), 5);
        let b = event_id(Some("andymini"), 5);
        assert_ne!(a, b);
        assert!(a.starts_with("andymini:5:"), "{a}");
        let n = event_id(None, 5);
        let tag = n.split(':').next().expect("tag");
        assert!(tag.starts_with("null-") && tag.len() == 13, "{n}");
        assert_eq!(tag, event_id(None, 6).split(':').next().expect("tag"));
    }

    #[test]
    fn a_kind_cannot_rewrite_the_common_fields() {
        let ctx = Ctx {
            host: None,
            seat: Some("a9136".into()),
            cards: vec!["S1".into()],
            status_item: Some("S1 做".into()),
        };
        let extra = json!({"path": "wt:x/target", "seat": "043dd", "cards": [], "bytes": 3})
            .as_object()
            .cloned()
            .expect("object");
        let e = build(&ctx, "clean", "deleted", 0, extra);
        assert_eq!(e["seat"], "a9136");
        assert_eq!(e["cards"], json!(["S1"]));
        assert_eq!(e["host"], Value::Null);
        assert_eq!(e["at"], "1970-01-01T00:00:00.000Z");
        assert_eq!(
            (e["path"].as_str(), e["bytes"].as_u64()),
            (Some("wt:x/target"), Some(3))
        );
        for k in COMMON {
            assert!(e.get(k).is_some(), "{k}");
        }
    }
}
