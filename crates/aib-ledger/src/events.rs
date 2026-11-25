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

/// The longest status line kept on an event, in characters (the rest becomes `…`).
pub const ITEM_MAX: usize = 200;

/// A seat code: 5 lowercase hex (`a9136`). Anything else is not a seat (5d33a slice 1 L1): it
/// is never used as a file name and an event says null.
pub fn seat_code(s: &str) -> bool {
    s.len() == 5 && s.bytes().all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f'))
}

/// Text from a file somebody else writes, safe to show: control characters and the invisible
/// format / direction marks (U+200B–U+200F, U+202A–U+202E, U+2066–U+2069, U+FEFF …) dropped,
/// at most [`ITEM_MAX`] characters (5d33a slice 1 N1: a status line that reverses the board).
pub fn clean_text(s: &str) -> String {
    let hidden = |c: char| {
        c.is_control()
            || matches!(c as u32,
                0xAD | 0x61C | 0x180E | 0x200B..=0x200F | 0x202A..=0x202E
                | 0x2060..=0x2064 | 0x2066..=0x206F | 0xFEFF | 0xFFF9..=0xFFFB)
    };
    let kept: Vec<char> = s.chars().filter(|c| !hidden(*c)).collect();
    if kept.len() <= ITEM_MAX {
        return kept.into_iter().collect();
    }
    let mut out: String = kept[..ITEM_MAX - 1].iter().collect();
    out.push('…');
    out
}

/// The aib state root, in `aib_init.py`'s order: `EZ2_AIB_STATE_ROOT`, then `EZ2_AIB_HOME`,
/// then `<home>/.ez2-aib` (an empty value counts as unset); no home ⇒ None. One answer for
/// every program that writes events (IFACE §7 item 3).
pub fn state_root(
    env_root: Option<&std::ffi::OsStr>,
    aib_home: Option<&std::ffi::OsStr>,
    home: Option<&Path>,
) -> Option<PathBuf> {
    if let Some(r) = env_root.filter(|r| !r.is_empty()) {
        return Some(PathBuf::from(r));
    }
    if let Some(h) = aib_home.filter(|h| !h.is_empty()) {
        return Some(PathBuf::from(h));
    }
    home.map(|h| h.join(".ez2-aib"))
}

/// `AIB_PLAN_DATA` when set and not empty, else the cabin plan under `<root>/pending` (the
/// same file `aib-plan` reads by default; a setting for it is IFACE §5, slice 6).
pub fn plan_file(root: &Path, env: Option<&std::ffi::OsStr>) -> PathBuf {
    match env.filter(|e| !e.is_empty()) {
        Some(e) => PathBuf::from(e),
        None => root
            .join("pending")
            .join("aib2-a63ab-1107")
            .join("cabin-plan")
            .join("data.json"),
    }
}

/// Every node id of a plan tree: each string `"id"` anywhere in the JSON. Not JSON ⇒ empty
/// (no card is then taken from a status line).
pub fn plan_ids(plan_json: &str) -> std::collections::BTreeSet<String> {
    fn walk(v: &Value, out: &mut std::collections::BTreeSet<String>) {
        match v {
            Value::Object(o) => {
                if let Some(Value::String(id)) = o.get("id") {
                    out.insert(id.clone());
                }
                o.values().for_each(|c| walk(c, out));
            }
            Value::Array(a) => a.iter().for_each(|c| walk(c, out)),
            _ => {}
        }
    }
    let mut out = std::collections::BTreeSet::new();
    if let Ok(v) = serde_json::from_str::<Value>(plan_json) {
        walk(&v, &mut out);
    }
    out
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
    if !seat_code(seat) {
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
        let seat = seat.map(str::trim).filter(|s| seat_code(s));
        let status_item = seat
            .and_then(|s| seat_item(root, s))
            .map(|i| clean_text(&i));
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

    /// [`Ctx::load`] from this machine's files: the hosts file (`AIB_HOSTS_FILE`, else
    /// `<root>/hosts.json`) and the plan (`AIB_PLAN_DATA`, else [`plan_file`]). A file that
    /// cannot be read leaves its part null / empty.
    pub fn for_run(root: &Path, seat: Option<&str>) -> Ctx {
        let read = |p: PathBuf| std::fs::read_to_string(p).ok();
        let hosts = read(hosts_file(
            root,
            std::env::var_os("AIB_HOSTS_FILE").as_deref(),
        ));
        let ids = read(plan_file(
            root,
            std::env::var_os("AIB_PLAN_DATA").as_deref(),
        ))
        .map(|t| plan_ids(&t))
        .unwrap_or_default();
        Ctx::load(root, seat, hosts.as_deref(), |w| ids.contains(w))
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
        // a `:` in the name would make the id four fields no more (5d33a slice 1 N2)
        Some(h) => format!("{}:{ms}:{pid}:{seq}", h.replace(':', "_")),
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
    crate::ensure_dir(&d)?;
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
    fn a_host_with_a_colon_still_makes_a_four_field_id() {
        let id = event_id(Some("[2001:db8::1]"), 7);
        let f: Vec<&str> = id.split(':').collect();
        assert_eq!(f.len(), 4, "{id}");
        assert_eq!((f[0], f[1]), ("[2001_db8__1]", "7"));
    }

    #[test]
    fn only_five_hex_is_a_seat_unix_or_windows_shaped() {
        assert!(seat_code("a9136") && seat_code("519d0"));
        for bad in [
            "", "a913", "a91366", "A9136", "a913g", "../a9", "..\\a9", "a\\913", "c:\\x", "a9.36",
            " a913",
        ] {
            assert!(!seat_code(bad), "{bad:?}");
        }
        let d = tempfile::tempdir().expect("tmp");
        let ctx = Ctx::load(d.path(), Some("a9\\36"), None, |_| true);
        assert_eq!((ctx.seat, ctx.status_item), (None, None));
    }

    #[test]
    fn a_loaded_status_line_is_cleaned_and_its_cards_still_found() {
        let d = tempfile::tempdir().expect("tmp");
        let now = d.path().join("state").join("seat-now");
        std::fs::create_dir_all(&now).expect("mkdir");
        std::fs::write(now.join("a9136.json"), r#"{"item": "S1 \u202e做\u200b完"}"#).expect("w");
        let ctx = Ctx::load(d.path(), Some("a9136"), None, |w| w == "S1");
        assert_eq!(ctx.status_item.as_deref(), Some("S1 做完"));
        assert_eq!(ctx.cards, ["S1"]);
    }

    #[test]
    fn a_run_reads_the_hosts_and_the_plan_under_the_root() {
        if std::env::var_os("AIB_PLAN_DATA").is_some()
            || std::env::var_os("AIB_HOSTS_FILE").is_some()
        {
            return; // this machine points elsewhere: the files under the root are not read
        }
        let d = tempfile::tempdir().expect("tmp");
        let r = d.path();
        let now = r.join("state").join("seat-now");
        std::fs::create_dir_all(&now).expect("mkdir");
        std::fs::write(now.join("a9136.json"), r#"{"item": "S1 HOST7 T06-c"}"#).expect("w");
        std::fs::write(r.join("hosts.json"), r#"{"self": "HOST7"}"#).expect("w");
        let plan = plan_file(r, None);
        std::fs::create_dir_all(plan.parent().expect("dir")).expect("mkdir");
        std::fs::write(&plan, r#"{"id":"root","children":[{"id":"T06-c"}]}"#).expect("w");
        let ctx = Ctx::for_run(r, Some("a9136"));
        assert_eq!(
            (ctx.host.as_deref(), ctx.cards),
            (Some("HOST7"), vec!["T06-c".to_owned()])
        );
        let bare = Ctx::for_run(&r.join("nothing"), Some("a9136"));
        assert_eq!((bare.host, bare.cards.len()), (None, 0));
    }

    #[test]
    fn a_status_line_loses_hidden_marks_and_is_cut() {
        assert_eq!(
            clean_text("S1\u{202E}gnihtemos\u{200B} 做\u{7}"),
            "S1gnihtemos 做"
        );
        assert_eq!(clean_text("\u{FEFF}a\u{2066}b\u{2069}\r\nc"), "abc");
        let long = "字".repeat(ITEM_MAX + 5);
        let cut = clean_text(&long);
        assert_eq!(cut.chars().count(), ITEM_MAX);
        assert!(cut.ends_with('…'));
        assert_eq!(clean_text(&"字".repeat(ITEM_MAX)), "字".repeat(ITEM_MAX));
    }

    #[test]
    fn state_root_and_plan_ids() {
        let os = |s: &'static str| Some(std::ffi::OsStr::new(s));
        let h = Path::new("h");
        assert_eq!(
            state_root(os("r"), os("x"), Some(h)),
            Some(PathBuf::from("r"))
        );
        assert_eq!(
            state_root(os(""), os("x"), Some(h)),
            Some(PathBuf::from("x"))
        );
        assert_eq!(state_root(None, os(""), Some(h)), Some(h.join(".ez2-aib")));
        assert_eq!(state_root(None, None, None), None);
        assert_eq!(plan_file(h, os("p.json")), PathBuf::from("p.json"));
        assert!(plan_file(h, os("")).ends_with("cabin-plan/data.json"));
        let ids = plan_ids(r#"{"id":"root","children":[{"id":"S1","n":{"id":"T06-c"}},{"id":3}]}"#);
        assert_eq!(ids.into_iter().collect::<Vec<_>>(), ["S1", "T06-c", "root"]);
        assert!(plan_ids("not json").is_empty());
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
