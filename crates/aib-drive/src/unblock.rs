//! v-2: merge every source's `drive/unblock-src/<source>.json` into `drive/unblock.json`
//! and write `drive/blocking/<seat>.txt` (IFACE-unblock-json-v2-a9136-1115 r3).
//!
//! Each source (u-2 f1fae, u-3 ebb36, u-4 56cda, plan / pr / lease a9136) writes only its own
//! file; this module never reads GitHub, hosts or seats itself. What it adds:
//! - a row check (bad rows are refused and named in `sources[].why`);
//! - same id from two sources: the newer `as_of_ms` wins;
//! - `blocks`: how many other rows wait on this one, directly or not (deduplicated, a loop
//!   counts once);
//! - stale sources (`as_of_ms` older than [`STALE_MS`]): their rows stay, marked `stale`;
//! - rows gone since the last round: `cleared`, or `unknown` when what would have shown them
//!   could not be read (source missing / stale / an `unreadable` part that `covers` the code).

use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;

pub const SCHEMA: &str = "aib-drive.unblock.v2";
pub const FILE: &str = "unblock.json";
pub const SRC_DIR: &str = "unblock-src";
pub const BLOCKING_DIR: &str = "blocking";
/// A source file older than this is stale (IFACE r2: 20 minutes, two of next's rounds).
pub const STALE_MS: i64 = 20 * 60 * 1000;
/// The highest blocker code known (DECISION 解法表: U1 … U22).
pub const MAX_CODE: u32 = 22;
/// Seats that take no work (519d0 11-16: the system seat 63695), one code per line, `#`
/// starts a comment; they are marked role `system` in the seat facts U13 reads.
pub const NOT_WORKERS: &str = "u13-exclude.txt";
/// Review seats, same format: they take only 驗收 nodes (a9136 S79 設計審 M1: not written in
/// the code; no file = no review seat).
pub const REVIEWERS: &str = "u13-reviewers.txt";
/// Every `cleared` gone row, one JSON line each with the round's time (519d0 11-16: a
/// cleared row shows in unblock.json for one round only, so which round cleared it was lost).
pub const GONE_LOG: &str = "unblock-gone.jsonl";

/// One source file as read.
#[derive(Debug, Clone)]
pub struct Src {
    /// The file's stem: `u-2`, `u-3`, `lease-lin` …
    pub name: String,
    /// `Err` = the file could not be read or parsed (one line why).
    pub doc: Result<Value, String>,
}

/// What a source could see this round.
#[derive(Debug, Clone, Default)]
struct Seen {
    /// Read and fresh.
    ok: bool,
    /// Codes it could not see (an `unreadable` part's `covers`).
    hidden: BTreeSet<String>,
    /// An `unreadable` part without `covers`: everything it has is hidden.
    hidden_all: bool,
}

impl Seen {
    fn hides(&self, code: &str) -> bool {
        !self.ok || self.hidden_all || self.hidden.contains(code)
    }
}

fn code_ok(c: &str) -> bool {
    c.strip_prefix('U')
        .filter(|n| !n.is_empty() && n.len() <= 2 && n.bytes().all(|b| b.is_ascii_digit()))
        .and_then(|n| n.parse::<u32>().ok())
        .is_some_and(|n| (1..=MAX_CODE).contains(&n))
}

fn subject_ok(s: &str) -> bool {
    !s.is_empty()
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

fn seat_ok(s: &str) -> bool {
    s.len() == 5 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

fn text<'a>(o: &'a Map<String, Value>, k: &str) -> &'a str {
    o.get(k).and_then(Value::as_str).unwrap_or("")
}

/// Why this row is refused, `None` = it is fine.
fn refuse(o: &Map<String, Value>) -> Option<String> {
    let (id, code, subject) = (text(o, "id"), text(o, "code"), text(o, "subject"));
    if !code_ok(code) {
        return Some(format!("未知碼 {code:?}"));
    }
    if !subject_ok(subject) {
        return Some(format!("subject {subject:?} 只能小寫英數與 -"));
    }
    if id != format!("{code}:{subject}") {
        return Some(format!("id {id:?} 不是 code:subject"));
    }
    let fp = text(o, "fingerprint");
    if fp.is_empty() || fp.contains(':') {
        return Some(format!("{id} fingerprint 空或含冒號"));
    }
    for k in ["what", "evidence"] {
        if text(o, k).trim().is_empty() {
            return Some(format!("{id} 缺 {k}"));
        }
    }
    let owner = o.get("owner").and_then(Value::as_str);
    if !owner.is_some_and(|s| s.is_empty() || seat_ok(s)) {
        return Some(format!("{id} owner 要是席碼或空字串"));
    }
    for k in ["since_ms", "as_of_ms"] {
        if o.get(k).and_then(Value::as_i64).is_none() {
            return Some(format!("{id} 缺 {k}"));
        }
    }
    None
}

/// The merged document and the `blocking/<seat>.txt` texts. `old` is the last unblock.json
/// (for gone rows); `known_seats` are seats that already have a blocking file (set back to 0
/// when they own nothing now).
pub fn merge(
    srcs: &[Src],
    old: Option<&Value>,
    known_seats: &BTreeSet<String>,
    now_ms: i64,
) -> (Value, BTreeMap<String, String>) {
    let mut sources = Vec::new();
    let mut seen: BTreeMap<String, Seen> = BTreeMap::new();
    let mut rows: BTreeMap<String, Map<String, Value>> = BTreeMap::new();
    for s in srcs {
        let doc = match &s.doc {
            Ok(d) => d,
            Err(why) => {
                sources.push(json!({"name": s.name, "ok": false, "why": why}));
                seen.insert(s.name.clone(), Seen::default());
                continue;
            }
        };
        let as_of = doc.get("as_of_ms").and_then(Value::as_i64);
        let fresh = as_of.is_some_and(|t| now_ms - t <= STALE_MS);
        let schema_ok = doc.get("schema").and_then(Value::as_str) == Some(SCHEMA);
        // 5d33a v-2 L1: a file of another schema is not read whole either
        let ok = fresh && schema_ok;
        let mut why: Vec<String> = Vec::new();
        if !schema_ok {
            why.push(format!("schema 不是 {SCHEMA}"));
        }
        if !fresh {
            why.push("資料過期".into());
        }
        let mut sn = Seen {
            ok,
            ..Seen::default()
        };
        let unreadable = doc
            .get("unreadable")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        for u in &unreadable {
            let part = u.get("name").and_then(Value::as_str).unwrap_or("?");
            let mut uw = u
                .get("why")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_owned();
            match u.get("covers").and_then(Value::as_array) {
                Some(c) => sn
                    .hidden
                    .extend(c.iter().filter_map(Value::as_str).map(str::to_owned)),
                None => {
                    sn.hidden_all = true;
                    uw = format!("{uw}(缺 covers,當作全部碼都看不到)");
                }
            }
            sources.push(json!({"name": format!("{}/{part}", s.name), "ok": false,
                "why": uw, "partial_of": s.name}));
        }
        let mut refused = 0usize;
        let mut first_refusal = String::new();
        for r in doc
            .get("rows")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            let Some(o) = r.as_object() else {
                // a row that is not even an object: which code it hides is not known
                sn.hidden_all = !r.is_object();
                refused += 1;
                continue;
            };
            if let Some(w) = refuse(o) {
                // 5d33a v-2 L1: a refused row's code is not seen this round; a code that is
                // itself wrong (02a6eefd N1) could have been any code
                sn.hidden.insert(text(o, "code").to_owned());
                sn.hidden_all |= !code_ok(text(o, "code"));
                if refused == 0 {
                    first_refusal = w;
                }
                refused += 1;
                continue;
            }
            let mut o = o.clone();
            o.insert("source".into(), json!(s.name));
            if !ok {
                o.insert("stale".into(), json!(true));
            }
            let id = text(&o, "id").to_owned();
            let newer = rows.get(&id).is_none_or(|have| {
                o.get("as_of_ms").and_then(Value::as_i64)
                    > have.get("as_of_ms").and_then(Value::as_i64)
            });
            if newer {
                rows.insert(id, o);
            }
        }
        if refused > 0 {
            why.push(format!("拒收 {refused} 列:{first_refusal}"));
        }
        let mut e = json!({"name": s.name, "as_of_ms": as_of, "ok": ok});
        if !unreadable.is_empty() {
            e["partial"] = json!(true);
        }
        if !why.is_empty() {
            e["why"] = json!(why.join(";"));
        }
        sources.push(e);
        seen.insert(s.name.clone(), sn);
    }

    // IFACE r1: a blocker keeps the first time it was seen, whatever its source remembers.
    for r in old
        .and_then(|o| o.get("rows"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let first = r.get("since_ms").and_then(Value::as_i64);
        if let (Some(o), Some(first)) = (rows.get_mut(text_v(r, "id")), first) {
            if o.get("since_ms").and_then(Value::as_i64) > Some(first) {
                o.insert("since_ms".into(), json!(first));
            }
        }
    }

    let down = downstream(&rows);
    for (id, o) in &mut rows {
        o.insert(
            "blocks".into(),
            json!(down.get(id).map_or(0, BTreeSet::len)),
        );
    }

    let gone = gone_rows(old, &rows, &seen, now_ms);
    let blocking = blocking_files(&rows, &down, known_seats, now_ms);

    let mut list: Vec<Value> = rows.into_values().map(Value::Object).collect();
    list.sort_by(|a, b| {
        let n = |v: &Value, k: &str| v.get(k).and_then(Value::as_i64).unwrap_or(0);
        n(b, "blocks")
            .cmp(&n(a, "blocks"))
            .then(n(a, "since_ms").cmp(&n(b, "since_ms")))
            .then(text_v(a, "id").cmp(text_v(b, "id")))
    });
    let doc = json!({
        "schema": SCHEMA,
        "as_of_ms": now_ms,
        "as_of": crate::act::rfc3339_ms(now_ms),
        "sources": sources,
        "rows": list,
        "gone": gone,
    });
    (doc, blocking)
}

fn text_v<'a>(v: &'a Value, k: &str) -> &'a str {
    v.get(k).and_then(Value::as_str).unwrap_or("")
}

/// For each row id, every other row that waits on it, directly or through others.
fn downstream(rows: &BTreeMap<String, Map<String, Value>>) -> BTreeMap<String, BTreeSet<String>> {
    let mut direct: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
    for (id, o) in rows {
        for w in o
            .get("waits_on")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
        {
            if w != id && rows.contains_key(w) {
                direct.entry(w).or_default().push(id);
            }
        }
    }
    let mut out = BTreeMap::new();
    for id in rows.keys() {
        let mut seen: BTreeSet<String> = BTreeSet::new();
        let mut todo: Vec<&str> = direct.get(id.as_str()).cloned().unwrap_or_default();
        while let Some(x) = todo.pop() {
            if x == id || !seen.insert(x.to_owned()) {
                continue;
            }
            todo.extend(direct.get(x).into_iter().flatten());
        }
        out.insert(id.clone(), seen);
    }
    out
}

/// Rows in `old` (and `unknown` gone rows carried from it) that are not here now.
fn gone_rows(
    old: Option<&Value>,
    rows: &BTreeMap<String, Map<String, Value>>,
    seen: &BTreeMap<String, Seen>,
    now_ms: i64,
) -> Vec<Value> {
    let Some(old) = old else { return Vec::new() };
    let mut was: BTreeMap<String, (Value, Option<i64>)> = BTreeMap::new();
    for g in old
        .get("gone")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|g| text_v(g, "state") == "unknown")
    {
        let since = g.get("gone_since_ms").and_then(Value::as_i64);
        was.insert(text_v(g, "id").to_owned(), (g.clone(), since));
    }
    for r in old
        .get("rows")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        was.insert(text_v(r, "id").to_owned(), (r.clone(), None));
    }
    let mut out = Vec::new();
    for (id, (r, since)) in was {
        if id.is_empty() || rows.contains_key(&id) {
            continue;
        }
        let (code, source) = (text_v(&r, "code"), text_v(&r, "source"));
        let sn = seen.get(source);
        let (state, why) = match sn {
            None => ("unknown", format!("來源 {source} 這輪沒有檔案")),
            Some(s) if !s.ok => ("unknown", format!("來源 {source} 讀不到或過期")),
            Some(s) if s.hides(code) => ("unknown", format!("{source} 有讀不到的部分蓋住 {code}")),
            Some(_) => ("cleared", format!("{source} 完整讀到、這列不在了")),
        };
        out.push(json!({"id": id, "code": code, "source": source,
            "owner": text_v(&r, "owner"), "what": text_v(&r, "what"),
            "state": state, "why": why, "gone_since_ms": since.unwrap_or(now_ms)}));
    }
    out
}

/// `blocking/<seat>.txt` for each owner (and each seat that had a file before).
fn blocking_files(
    rows: &BTreeMap<String, Map<String, Value>>,
    down: &BTreeMap<String, BTreeSet<String>>,
    known: &BTreeSet<String>,
    now_ms: i64,
) -> BTreeMap<String, String> {
    let mut by: BTreeMap<String, Vec<&Map<String, Value>>> = BTreeMap::new();
    for o in rows.values() {
        let s = text(o, "owner");
        if seat_ok(s) {
            by.entry(s.to_owned()).or_default().push(o);
        }
    }
    for k in known {
        by.entry(k.clone()).or_default();
    }
    let as_of = crate::act::rfc3339_ms(now_ms);
    by.into_iter()
        .map(|(seat, mine)| {
            let mut t = format!("as_of {as_of}\n");
            if !mine.is_empty() && mine.iter().all(|o| o.contains_key("stale")) {
                t.push_str("blocks ?\n");
            } else {
                let n: BTreeSet<&String> = mine
                    .iter()
                    .filter_map(|o| down.get(text(o, "id")))
                    .flatten()
                    .collect();
                t.push_str(&format!("blocks {}\n", n.len()));
            }
            if let Some(o) = mine
                .iter()
                .min_by_key(|o| o.get("since_ms").and_then(Value::as_i64).unwrap_or(now_ms))
            {
                let since = o.get("since_ms").and_then(Value::as_i64).unwrap_or(now_ms);
                let mins = (now_ms - since).max(0) / 60_000;
                let what = text(o, "what").replace(['\n', '\r'], " ");
                t.push_str(&format!("oldest {} {mins}m {what}\n", text(o, "id")));
            }
            (seat, t)
        })
        .collect()
}

fn read_json(p: &Path) -> Result<Value, String> {
    let s = fs::read_to_string(p).map_err(|e| format!("讀不到:{e}"))?;
    serde_json::from_str(&s).map_err(|e| format!("不是 JSON:{e}"))
}

fn write_atomic(p: &Path, body: &str) -> Result<(), String> {
    let tmp = p.with_extension(format!("tmp.{}", std::process::id()));
    fs::write(&tmp, body).map_err(|e| format!("{}: {e}", tmp.display()))?;
    fs::rename(&tmp, p).map_err(|e| format!("{}: {e}", p.display()))
}

/// A JSON file that must have been written in the last [`STALE_MS`] (5d33a v-2 N3: a hand-run
/// `unblock` would otherwise report from an act-next.json hours old).
fn read_fresh(p: &Path, now_ms: i64) -> Result<Value, String> {
    let modified = fs::metadata(p)
        .and_then(|m| m.modified())
        .map_err(|e| format!("讀不到:{e}"))?;
    let at = modified
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_millis()).unwrap_or(i64::MAX));
    if now_ms - at > STALE_MS {
        return Err(format!(
            "{} 已 {} 分沒更新",
            p.display(),
            (now_ms - at) / 60_000
        ));
    }
    read_json(p)
}

/// The `drive` source's files (act-next.json, refresh.log, refresh.lock, act.json,
/// act-dryrun.jsonl).
fn drive_inputs(drive: &Path, now_ms: i64) -> crate::unblock_drive::Inputs {
    let lines = |f: &str| -> Result<Vec<Value>, String> {
        match fs::read_to_string(drive.join(f)) {
            Ok(t) => Ok(t
                .lines()
                .filter_map(|l| serde_json::from_str(l).ok())
                .collect()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Vec::new()),
            Err(e) => Err(format!("{f}: {e}")),
        }
    };
    let lock = fs::read_to_string(drive.join("refresh.lock"))
        .ok()
        .and_then(|t| {
            let mut w = t.split_whitespace();
            Some((w.next()?.parse().ok()?, w.next()?.parse().ok()?))
        });
    let woulds = lines("act-dryrun.jsonl").map(|v| {
        v.iter()
            .filter(|l| text_v(l, "kind") == "would")
            .filter_map(|l| Some((text_v(l, "action").to_owned(), l.get("at_ms")?.as_i64()?)))
            .collect()
    });
    crate::unblock_drive::Inputs {
        next: read_fresh(&drive.join("act-next.json"), now_ms),
        refresh_last: lines("refresh.log").map(|v| v.last().cloned()),
        lock,
        switches: crate::act::read_switches(drive),
        woulds,
        plan: drive
            .parent()
            .map(|r| r.join("pending/aib2-a63ab-1107/cabin-plan/data.json"))
            .ok_or_else(|| "no state root".to_owned())
            .and_then(|p| read_json(&p)),
        seats: read_json(&drive.join("act-seats.json"))
            .and_then(|s| mark_not_workers(s, &drive.join(NOT_WORKERS)))
            .and_then(|s| mark_reviewers(s, &drive.join(REVIEWERS))),
    }
}

/// The seat codes in a [`NOT_WORKERS`] file: one per line, `#` starts a comment.
pub fn not_worker_codes(text: &str) -> BTreeSet<String> {
    text.lines()
        .map(|l| l.split('#').next().unwrap_or("").trim())
        .filter(|c| !c.is_empty())
        .map(str::to_owned)
        .collect()
}

/// Mark every seat listed in `list` as role `system`; no file = nobody listed.
fn mark_not_workers(seats: Value, list: &Path) -> Result<Value, String> {
    mark_role(seats, list, "system")
}

/// Seats listed in [`REVIEWERS`] get role `reviewer` (a missing file marks none).
fn mark_reviewers(seats: Value, list: &Path) -> Result<Value, String> {
    mark_role(seats, list, "reviewer")
}

fn mark_role(mut seats: Value, list: &Path, role: &str) -> Result<Value, String> {
    let text = match fs::read_to_string(list) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(seats),
        Err(e) => return Err(format!("{}: {e}", list.display())),
    };
    let codes = not_worker_codes(&text);
    for x in seats
        .get_mut("seats")
        .and_then(Value::as_array_mut)
        .into_iter()
        .flatten()
    {
        // a seat in both files stays system: it takes no work at all (a9136 S79 r2 N1)
        if codes.contains(text_v(x, "seat")) && text_v(x, "role") != "system" {
            x["role"] = json!(role);
        }
    }
    Ok(seats)
}

/// One round against `drive`: read sources and the old file, merge, and with `write` save
/// unblock.json and the blocking files. Returns the merged document.
pub fn run(drive: &Path, now_ms: i64, write: bool) -> Result<Value, String> {
    let mut srcs = Vec::new();
    if let Ok(rd) = fs::read_dir(drive.join(SRC_DIR)) {
        let mut paths: Vec<_> = rd
            .filter_map(Result::ok)
            .map(|e| e.path())
            .filter(|p| p.extension().is_some_and(|x| x == "json"))
            .collect();
        paths.sort();
        for p in paths {
            let name = p
                .file_stem()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default();
            srcs.push(Src {
                name,
                doc: read_json(&p),
            });
        }
    }
    if !srcs.iter().any(|s| s.name == crate::unblock_drive::NAME) {
        srcs.push(Src {
            name: crate::unblock_drive::NAME.into(),
            doc: Ok(crate::unblock_drive::doc(
                &drive_inputs(drive, now_ms),
                now_ms,
            )),
        });
    }
    let old = read_json(&drive.join(FILE)).ok();
    let bdir = drive.join(BLOCKING_DIR);
    let known: BTreeSet<String> = fs::read_dir(&bdir)
        .into_iter()
        .flatten()
        .filter_map(Result::ok)
        .filter_map(|e| {
            let n = e.file_name().to_string_lossy().into_owned();
            n.strip_suffix(".txt")
                .filter(|s| seat_ok(s))
                .map(str::to_owned)
        })
        .collect();
    let (doc, blocking) = merge(&srcs, old.as_ref(), &known, now_ms);
    if write {
        fs::create_dir_all(&bdir).map_err(|e| format!("{}: {e}", bdir.display()))?;
        let body = serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())? + "\n";
        write_atomic(&drive.join(FILE), &body)?;
        for (seat, t) in &blocking {
            write_atomic(&bdir.join(format!("{seat}.txt")), t)?;
        }
        let log = read_lines(&drive.join(crate::unblock_u13::LOG))?;
        append_lines(
            drive,
            crate::unblock_u13::LOG,
            crate::unblock_u13::log_lines(&doc, &log, now_ms),
        )?;
        append_lines(drive, GONE_LOG, cleared_lines(&doc, now_ms))?;
    }
    Ok(doc)
}

/// The `cleared` gone rows of `doc`, each with `at_ms` (the round) added.
pub fn cleared_lines(doc: &Value, now_ms: i64) -> Vec<Value> {
    doc.get("gone")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|g| text_v(g, "state") == "cleared")
        .map(|g| {
            let mut l = g.clone();
            l["at_ms"] = json!(now_ms);
            l
        })
        .collect()
}

fn append_lines(drive: &Path, file: &str, lines: Vec<Value>) -> Result<(), String> {
    if lines.is_empty() {
        return Ok(());
    }
    use std::io::Write;
    let mut f = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(drive.join(file))
        .map_err(|e| format!("{file}: {e}"))?;
    for l in lines {
        writeln!(f, "{l}").map_err(|e| format!("{file}: {e}"))?;
    }
    Ok(())
}

/// A JSON-lines file, unparsable lines skipped; missing = empty.
fn read_lines(p: &Path) -> Result<Vec<Value>, String> {
    match fs::read_to_string(p) {
        Ok(t) => Ok(t
            .lines()
            .filter_map(|l| serde_json::from_str(l).ok())
            .collect()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Vec::new()),
        Err(e) => Err(format!("{}: {e}", p.display())),
    }
}

/// `--match`: how the U13 nominations so far compare with the plan tree's owners now.
pub fn match_report(drive: &Path, now_ms: i64) -> Result<Value, String> {
    let log = read_lines(&drive.join(crate::unblock_u13::LOG))?;
    let plan = drive
        .parent()
        .map(|r| r.join("pending/aib2-a63ab-1107/cabin-plan/data.json"))
        .ok_or_else(|| "no state root".to_owned())
        .and_then(|p| read_json(&p))?;
    let seats = read_json(&drive.join("act-seats.json"))
        .and_then(|s| mark_not_workers(s, &drive.join(NOT_WORKERS)))
        .and_then(|s| mark_reviewers(s, &drive.join(REVIEWERS)))?;
    Ok(crate::unblock_u13::report(&log, &plan, &seats, now_ms))
}

/// `aib-drive unblock [--write | --match]`: print the merged document; `--write` also saves
/// it (and logs U13 nominations); `--match` prints the nominations-vs-owners report instead.
pub fn cli(args: &[String]) -> u8 {
    let mut write = false;
    let mut matching = false;
    for a in args {
        match a.as_str() {
            "--write" => write = true,
            "--match" => matching = true,
            _ => {
                eprintln!("usage: aib-drive unblock [--write | --match]");
                return 2;
            }
        }
    }
    let Some(drive) = crate::act::drive_dir() else {
        eprintln!("aib-drive unblock: no EZ2_AIB_HOME and no home folder");
        return 2;
    };
    if matching {
        return match match_report(&drive, crate::act::now_ms()) {
            Ok(r) => {
                println!("{}", serde_json::to_string_pretty(&r).unwrap_or_default());
                0
            }
            Err(e) => {
                eprintln!("aib-drive unblock --match: {e}");
                1
            }
        };
    }
    match run(&drive, crate::act::now_ms(), write) {
        Ok(doc) => {
            println!("{}", serde_json::to_string_pretty(&doc).unwrap_or_default());
            0
        }
        Err(e) => {
            eprintln!("aib-drive unblock: {e}");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW: i64 = 1_794_753_000_000;

    fn row(code: &str, subject: &str, owner: &str, waits: &[&str]) -> Value {
        json!({"id": format!("{code}:{subject}"), "code": code, "subject": subject,
            "what": format!("{code} {subject}"), "fingerprint": "fp1", "evidence": "e",
            "owner": owner, "waits_on": waits, "since_ms": NOW - 30 * 60_000,
            "as_of_ms": NOW - 60_000})
    }

    fn src(name: &str, as_of: i64, rows: Vec<Value>) -> Src {
        Src {
            name: name.into(),
            doc: Ok(json!({"schema": SCHEMA, "as_of_ms": as_of, "rows": rows})),
        }
    }

    fn ids(doc: &Value) -> Vec<(String, i64)> {
        doc["rows"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| (text_v(r, "id").to_owned(), r["blocks"].as_i64().unwrap()))
            .collect()
    }

    fn gone(doc: &Value, id: &str) -> String {
        doc["gone"]
            .as_array()
            .unwrap()
            .iter()
            .find(|g| text_v(g, "id") == id)
            .map(|g| text_v(g, "state").to_owned())
            .unwrap_or_default()
    }

    #[test]
    fn blocks_counts_everyone_downstream_once_even_in_a_loop() {
        let s = src(
            "u-2",
            NOW,
            vec![
                row("U14", "pr-784", "56cda", &[]),
                row("U14", "pr-785", "56cda", &["U14:pr-784"]),
                row("U7", "pr-786", "043dd", &["U14:pr-785"]),
                row("U7", "pr-787", "043dd", &["U7:pr-788"]),
                row("U7", "pr-788", "043dd", &["U7:pr-787"]),
            ],
        );
        let (doc, _) = merge(&[s], None, &BTreeSet::new(), NOW);
        let got: BTreeMap<String, i64> = ids(&doc).into_iter().collect();
        assert_eq!(got["U14:pr-784"], 2);
        assert_eq!(got["U14:pr-785"], 1);
        assert_eq!(got["U7:pr-786"], 0);
        assert_eq!(got["U7:pr-787"], 1);
        assert_eq!(got["U7:pr-788"], 1);
        assert_eq!(ids(&doc)[0].0, "U14:pr-784");
    }

    #[test]
    fn a_source_cannot_set_blocks_or_its_source_name() {
        let mut r = row("U3", "host-lin", "", &[]);
        r["blocks"] = json!(99);
        r["source"] = json!("u-2");
        let (doc, _) = merge(&[src("u-3", NOW, vec![r])], None, &BTreeSet::new(), NOW);
        assert_eq!(doc["rows"][0]["blocks"], 0);
        assert_eq!(doc["rows"][0]["source"], "u-3");
    }

    #[test]
    fn bad_rows_are_refused_and_named() {
        let mut colon = row("U14", "pr-1", "", &[]);
        colon["fingerprint"] = json!("a:b");
        let mut wrong_id = row("U14", "pr-2", "", &[]);
        wrong_id["id"] = json!("U14:pr-9");
        let rows = vec![
            row("U99", "pr-3", "", &[]),
            row("U14", "PR-4", "", &[]),
            colon,
            wrong_id,
            row("U14", "pr-5", "ABCDE", &[]),
            row("U14", "pr-6", "56cda", &[]),
        ];
        let (doc, _) = merge(&[src("u-2", NOW, rows)], None, &BTreeSet::new(), NOW);
        assert_eq!(ids(&doc), vec![("U14:pr-6".to_owned(), 0)]);
        let why = text_v(&doc["sources"][0], "why");
        assert!(why.starts_with("拒收 5 列:未知碼"), "{why}");
    }

    #[test]
    fn the_newer_row_wins_when_two_sources_say_the_same_id() {
        let mut a = row("U11", "pr-9", "1ced7", &[]);
        a["as_of_ms"] = json!(NOW - 5_000);
        let mut b = row("U11", "pr-9", "5d33a", &[]);
        b["as_of_ms"] = json!(NOW - 1_000);
        let (doc, _) = merge(
            &[src("u-2", NOW, vec![b]), src("u-4", NOW, vec![a])],
            None,
            &BTreeSet::new(),
            NOW,
        );
        assert_eq!(doc["rows"].as_array().unwrap().len(), 1);
        assert_eq!(doc["rows"][0]["owner"], "5d33a");
    }

    #[test]
    fn a_stale_source_keeps_its_rows_marked_and_hides_what_went() {
        let old = json!({"rows": [
            with_source(row("U3", "host-lin", "", &[]), "u-3"),
            with_source(row("U9", "seat-x1", "", &[]), "u-3"),
        ]});
        let s = src(
            "u-3",
            NOW - STALE_MS - 1,
            vec![row("U3", "host-lin", "", &[])],
        );
        let (doc, _) = merge(&[s], Some(&old), &BTreeSet::new(), NOW);
        assert_eq!(doc["sources"][0]["ok"], false);
        assert_eq!(doc["rows"][0]["stale"], true);
        assert_eq!(gone(&doc, "U9:seat-x1"), "unknown");
    }

    fn with_source(r: Value, s: &str) -> Value {
        let mut r = r;
        r["source"] = json!(s);
        r
    }

    #[test]
    fn a_gone_row_outside_covers_is_cleared_and_inside_is_unknown() {
        let old = json!({"rows": [
            with_source(row("U3", "host-lin", "", &[]), "u-3"),
            with_source(row("U19", "rc-1", "", &[]), "u-3"),
        ]});
        let s = Src {
            name: "u-3".into(),
            doc: Ok(json!({"schema": SCHEMA, "as_of_ms": NOW, "rows": [],
                "unreadable": [{"name": "remote", "why": "remote unfinished receipts not read",
                    "covers": ["U19", "U20"]}]})),
        };
        let (doc, _) = merge(&[s], Some(&old), &BTreeSet::new(), NOW);
        assert_eq!(gone(&doc, "U3:host-lin"), "cleared");
        assert_eq!(gone(&doc, "U19:rc-1"), "unknown");
        let src0 = doc["sources"]
            .as_array()
            .unwrap()
            .iter()
            .find(|s| s["name"] == "u-3")
            .unwrap()
            .clone();
        assert_eq!(src0["ok"], true);
        assert_eq!(src0["partial"], true);
        assert!(doc["sources"]
            .as_array()
            .unwrap()
            .iter()
            .any(|s| s["name"] == "u-3/remote" && s["partial_of"] == "u-3"));
    }

    #[test]
    fn an_unreadable_part_without_covers_hides_every_code() {
        let old = json!({"rows": [with_source(row("U3", "host-lin", "", &[]), "u-3")]});
        let s = Src {
            name: "u-3".into(),
            doc: Ok(json!({"schema": SCHEMA, "as_of_ms": NOW, "rows": [],
                "unreadable": [{"name": "ssh", "why": "ssh timeout"}]})),
        };
        let (doc, _) = merge(&[s], Some(&old), &BTreeSet::new(), NOW);
        assert_eq!(gone(&doc, "U3:host-lin"), "unknown");
        assert!(doc["sources"]
            .as_array()
            .unwrap()
            .iter()
            .any(|s| text_v(s, "why").contains("缺 covers")));
    }

    #[test]
    fn an_unknown_gone_row_is_cleared_once_its_source_reads_whole_again() {
        let old = json!({"rows": [], "gone": [{"id": "U19:rc-1", "code": "U19",
            "source": "u-3", "state": "unknown", "gone_since_ms": NOW - 600_000}]});
        let (doc, _) = merge(
            &[src("u-3", NOW, vec![])],
            Some(&old),
            &BTreeSet::new(),
            NOW,
        );
        assert_eq!(gone(&doc, "U19:rc-1"), "cleared");
        assert_eq!(doc["gone"][0]["gone_since_ms"], NOW - 600_000);
        let (again, _) = merge(
            &[src("u-3", NOW, vec![])],
            Some(&doc),
            &BTreeSet::new(),
            NOW,
        );
        assert!(again["gone"].as_array().unwrap().is_empty());
    }

    #[test]
    fn a_missing_source_file_makes_its_gone_rows_unknown() {
        let old = json!({"rows": [with_source(row("U6", "test-x", "56cda", &[]), "u-4")]});
        let (doc, _) = merge(&[], Some(&old), &BTreeSet::new(), NOW);
        assert_eq!(gone(&doc, "U6:test-x"), "unknown");
    }

    #[test]
    fn blocking_counts_a_seats_downstream_once_and_names_its_oldest() {
        let mut older = row("U14", "pr-784", "56cda", &[]);
        older["since_ms"] = json!(NOW - 52 * 60_000);
        let s = src(
            "u-2",
            NOW,
            vec![
                older,
                row("U14", "pr-785", "56cda", &["U14:pr-784"]),
                row("U7", "pr-786", "043dd", &["U14:pr-785", "U14:pr-784"]),
            ],
        );
        let known: BTreeSet<String> = ["7b81e".to_owned()].into();
        let (_, b) = merge(&[s], None, &known, NOW);
        let t = &b["56cda"];
        assert!(t.contains("\nblocks 2\n"), "{t}");
        assert!(t.contains("\noldest U14:pr-784 52m U14 pr-784\n"), "{t}");
        assert!(b["043dd"].contains("\nblocks 0\n"));
        assert_eq!(b["7b81e"].lines().nth(1), Some("blocks 0"));
        assert_eq!(b["7b81e"].lines().count(), 2);
    }

    #[test]
    fn blocking_is_unknown_when_every_row_of_the_seat_is_stale() {
        let s = src(
            "u-3",
            NOW - STALE_MS - 1,
            vec![row("U3", "host-lin", "ebb36", &[])],
        );
        let (_, b) = merge(&[s], None, &BTreeSet::new(), NOW);
        assert_eq!(b["ebb36"].lines().nth(1), Some("blocks ?"));
    }

    #[test]
    fn an_unparsable_source_is_named_not_fatal() {
        let bad = Src {
            name: "u-4".into(),
            doc: Err("不是 JSON:x".into()),
        };
        let (doc, _) = merge(
            &[bad, src("u-2", NOW, vec![row("U14", "pr-1", "", &[])])],
            None,
            &BTreeSet::new(),
            NOW,
        );
        assert_eq!(doc["sources"][0]["ok"], false);
        assert_eq!(doc["rows"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn run_writes_both_files_atomically_and_reads_them_back() {
        let d = std::env::temp_dir().join(format!("aib-unblock-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(d.join(SRC_DIR)).unwrap();
        fs::write(
            d.join(SRC_DIR).join("u-2.json"),
            json!({"schema": SCHEMA, "as_of_ms": NOW, "rows": [row("U14", "pr-1", "56cda", &[])]})
                .to_string(),
        )
        .unwrap();
        fs::write(d.join(SRC_DIR).join("notes.txt"), "x").unwrap();
        let doc = run(&d, NOW, true).unwrap();
        let names: Vec<&str> = doc["sources"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|s| s["name"].as_str())
            .collect();
        assert_eq!(
            names,
            vec!["u-2", "drive/next", "drive/plan", "drive"],
            "notes.txt is not a source"
        );
        let back = read_json(&d.join(FILE)).unwrap();
        assert_eq!(back["rows"][0]["id"], "U14:pr-1");
        assert!(fs::read_to_string(d.join(BLOCKING_DIR).join("56cda.txt"))
            .unwrap()
            .contains("blocks 0"));
        fs::write(
            d.join(SRC_DIR).join("u-2.json"),
            json!({"schema": SCHEMA,
            "as_of_ms": NOW, "rows": []})
            .to_string(),
        )
        .unwrap();
        let doc2 = run(&d, NOW + 1, false).unwrap();
        assert_eq!(gone(&doc2, "U14:pr-1"), "cleared");
        let leftovers: Vec<_> = fs::read_dir(&d)
            .unwrap()
            .filter_map(Result::ok)
            .filter(|e| e.file_name().to_string_lossy().contains(".tmp."))
            .collect();
        assert!(leftovers.is_empty());
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn a_blocker_keeps_the_first_time_it_was_seen() {
        let old = json!({"rows": [with_source(row("U14", "pr-1", "", &[]), "u-2")]});
        let mut now_row = row("U14", "pr-1", "", &[]);
        now_row["since_ms"] = json!(NOW);
        let (doc, _) = merge(
            &[src("u-2", NOW, vec![now_row])],
            Some(&old),
            &BTreeSet::new(),
            NOW,
        );
        assert_eq!(doc["rows"][0]["since_ms"], NOW - 30 * 60_000);
    }

    fn with_code(code: &str, subject: &str, src_name: &str) -> Value {
        with_source(row(code, subject, "", &[]), src_name)
    }

    #[test]
    fn a_source_of_another_schema_cannot_clear_its_old_rows() {
        let old = json!({"rows": [with_code("U14", "pr-1", "u-2")]});
        let s = Src {
            name: "u-2".into(),
            doc: Ok(json!({"schema": "aib-drive.unblock.v1", "as_of_ms": NOW, "rows": []})),
        };
        let (doc, _) = merge(&[s], Some(&old), &BTreeSet::new(), NOW);
        assert_eq!(doc["sources"][0]["ok"], false);
        assert_eq!(gone(&doc, "U14:pr-1"), "unknown");
    }

    #[test]
    fn a_refused_row_hides_its_code_but_not_the_others() {
        let old = json!({"rows": [with_code("U3", "host-lin", "u-3"), with_code("U9", "seat-x1", "u-3")]});
        let mut bad = row("U3", "host-lin", "", &[]);
        bad["fingerprint"] = json!("a:b");
        let (doc, _) = merge(
            &[src("u-3", NOW, vec![bad])],
            Some(&old),
            &BTreeSet::new(),
            NOW,
        );
        assert_eq!(gone(&doc, "U3:host-lin"), "unknown");
        assert_eq!(gone(&doc, "U9:seat-x1"), "cleared");
        let (doc2, _) = merge(
            &[src("u-3", NOW, vec![json!("not a row")])],
            Some(&old),
            &BTreeSet::new(),
            NOW,
        );
        assert_eq!(gone(&doc2, "U9:seat-x1"), "unknown");
    }

    #[test]
    fn an_old_act_next_json_is_unreadable_for_the_drive_source() {
        let d = std::env::temp_dir().join(format!("aib-unblock-next-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        let p = d.join("act-next.json");
        fs::write(&p, json!({"sources": [], "for_andy": []}).to_string()).unwrap();
        let old =
            std::time::UNIX_EPOCH + std::time::Duration::from_millis((NOW - 30 * 60_000) as u64);
        fs::File::options()
            .write(true)
            .open(&p)
            .unwrap()
            .set_modified(old)
            .unwrap();
        let why = read_fresh(&p, NOW).unwrap_err();
        assert!(why.contains("30 分沒更新"), "{why}");
        assert!(read_fresh(&p, NOW - 25 * 60_000).is_ok());
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn a_seat_in_the_exclude_file_is_marked_system_and_comments_are_ignored() {
        let d = std::env::temp_dir().join(format!("aib-unblock-excl-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        let p = d.join(NOT_WORKERS);
        let seats = json!({"seats": [{"seat": "63695", "role": "dev"},
            {"seat": "a9136", "role": "dev"}, {"seat": "1ced7", "role": "dev"}]});
        let same = mark_not_workers(seats.clone(), &p).unwrap();
        assert_eq!(same, seats);
        fs::write(&p, "# system seats\n63695  # restart notices\n\n1ced7\n").unwrap();
        let m = mark_not_workers(seats, &p).unwrap();
        assert_eq!(m["seats"][0]["role"], "system");
        assert_eq!(m["seats"][1]["role"], "dev");
        assert_eq!(m["seats"][2]["role"], "system");
        let _ = fs::remove_dir_all(&d);
    }

    /// a9136 S79 設計審 M1: review seats come from u13-reviewers.txt; no file marks none.
    #[test]
    fn a_seat_in_the_reviewers_file_is_marked_reviewer_and_none_without_it() {
        let d = std::env::temp_dir().join(format!("aib-unblock-rev-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        let p = d.join(REVIEWERS);
        let seats = json!({"seats": [{"seat": "5d33a", "role": "dev"},
            {"seat": "a9136", "role": "dev"}]});
        assert_eq!(mark_reviewers(seats.clone(), &p).unwrap(), seats);
        fs::write(&p, "# review seats\n5d33a  # 驗收審查員\n").unwrap();
        let m = mark_reviewers(seats, &p).unwrap();
        assert_eq!(m["seats"][0]["role"], "reviewer");
        assert_eq!(m["seats"][1]["role"], "dev");
        let sys = json!({"seats": [{"seat": "5d33a", "role": "system"}]});
        assert_eq!(
            mark_reviewers(sys, &p).unwrap()["seats"][0]["role"],
            "system"
        );
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn only_cleared_gone_rows_are_logged_with_their_round() {
        let doc = json!({"gone": [
            {"id": "U13:node-s71", "state": "cleared"},
            {"id": "U19:rc-1", "state": "unknown"}]});
        let l = cleared_lines(&doc, NOW);
        assert_eq!(l.len(), 1);
        assert_eq!(l[0]["id"], "U13:node-s71");
        assert_eq!(l[0]["at_ms"], NOW);
        assert!(cleared_lines(&json!({}), NOW).is_empty());
    }

    #[test]
    fn a_row_with_a_wrong_code_hides_every_code_of_its_source() {
        let old = json!({"rows": [with_code("U5", "node-t1", "u-2")]});
        let bad = row("U55", "node-t1", "", &[]);
        let (doc, _) = merge(
            &[src("u-2", NOW, vec![bad])],
            Some(&old),
            &BTreeSet::new(),
            NOW,
        );
        assert_eq!(gone(&doc, "U5:node-t1"), "unknown");
    }
}
