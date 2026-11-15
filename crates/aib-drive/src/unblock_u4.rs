//! u-4 of `aib-drive unblock` (DECISION-council-unblock-519d0-1115 r1 §2 / §5, 56cda):
//! U6 one test red on several PRs, U11 a review nobody took up, U8 seats act found gone.
//!
//! Read-only (§1 / §6): files only — receipts and reviews in `pending/`, the dm queue's
//! item files, act's journal and aib-slice's cache. No gh, no `drive/lock`, nothing
//! written except this slice's own `drive/unblock-src/u-4.json` with `--write`.
//! The rules are pure functions over parsed facts, so each has its own test and knife;
//! [`run`] only gathers the facts.
//!
//! Until u-1 (the framework and `unblock.json`) lands this is `aib-drive unblock-u4`;
//! v-2 merges its file into `unblock.json` (rows in IFACE-unblock-json-v2 a9136 11-15).

use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

/// U6: a test is a blocker when it is red on at least this many branches / PRs.
pub const U6_MIN_SUBJECTS: usize = 2;
/// U11: a review dispatch read this long ago (seconds) with no review of its head.
pub const U11_AFTER_SECS: u64 = 30 * 60;
/// U11 / U8 / receipts: only what happened in the last day is read.
pub const DAY_SECS: u64 = 24 * 60 * 60;
/// The first line of a review dispatch (派工 dm, RULES: 「派工: X → 席」).
pub const REVIEW_ASK: &str = "派工: 審";
/// The file this slice writes with `--write`, under `<root>/drive/` (IFACE-unblock-json-v2
/// a9136 11-15: each source writes `unblock-src/<source>.json`, v-2 merges them).
pub const OUT_FILE: &str = "unblock-src/u-4.json";
/// The row and file schema (IFACE-unblock-json-v2 r0).
pub const SCHEMA: &str = "aib-drive.unblock.v2";
/// The `source` of every row this slice writes.
pub const SOURCE: &str = "u-4";

/// One blocker row in unblock.json v2's shape (IFACE-unblock-json-v2 r0). `blocks` is
/// v-2's to count, so a source never writes it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Blocker {
    pub code: &'static str,
    /// `<code>:<subject>`: the same blocker keeps it when its evidence changes.
    pub id: String,
    /// Lowercase ASCII letters, digits and `-` only ([`slug`]).
    pub subject: String,
    /// One line for the board and the dm.
    pub what: String,
    /// Changes only when the evidence does (u-5 sends again on a change); no colon.
    pub fingerprint: String,
    pub evidence: Vec<String>,
    /// The seat that is to clear it now; `""` when not known.
    pub owner: String,
    /// The full head, for a blocker about one head.
    pub head: Option<String>,
    /// When the blocker was first seen (ms); 0 = not known here, [`run`] fills it.
    pub since_ms: u64,
}

impl Blocker {
    fn new(code: &'static str, subject: &str, what: String, evidence: Vec<String>) -> Self {
        let subject = slug(subject);
        Blocker {
            code,
            id: format!("{code}:{subject}"),
            subject,
            what,
            fingerprint: String::new(),
            evidence,
            owner: String::new(),
            head: None,
            since_ms: 0,
        }
    }
    /// The row as v-2 reads it; `as_of_ms` is when the files were read.
    pub fn to_json(&self, as_of_ms: u64) -> Value {
        let mut j = json!({
            "id": self.id, "code": self.code, "subject": self.subject, "what": self.what,
            "fingerprint": self.fingerprint, "evidence": self.evidence.join("; "),
            "owner": self.owner, "since_ms": self.since_ms, "as_of_ms": as_of_ms,
            "source": SOURCE,
        });
        if let Some(h) = &self.head {
            j["head"] = json!(h);
        }
        j
    }
}

/// `s` as a v2 subject / fingerprint part: lowercase ASCII letters and digits, every other
/// run of characters one `-`, none at either end (no colon, so `id` splits once).
pub fn slug(s: &str) -> String {
    let mut out = String::new();
    for c in s.chars() {
        if c.is_ascii_alphanumeric() {
            out.push(c.to_ascii_lowercase());
        } else if !out.is_empty() && !out.ends_with('-') {
            out.push('-');
        }
    }
    out.trim_end_matches('-').to_owned()
}

/// A short digest of `parts` for a fingerprint: FNV-1a 64, 8 hex (stable across builds,
/// unlike `DefaultHasher`).
pub fn digest(parts: &[String]) -> String {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for p in parts {
        for b in p.bytes().chain([0]) {
            h ^= u64::from(b);
            h = h.wrapping_mul(0x0100_0000_01b3);
        }
    }
    format!("{:08x}", h >> 32)
}

// ---------------------------------------------------------------- U6

/// One test that failed in a receipt's test phase: its name and where it panicked
/// (`file:line`, empty when no panic line was printed).
pub type Fail = (String, String);

/// A receipt's head and the tests that failed in its **test phase** — between `RC test=`
/// and the next `RC ` line. Knife runs come after that and fail on purpose, so they are
/// never read (DECISION §2: a fake knife bite is U20, not U6).
pub fn receipt_fails(text: &str) -> (Option<String>, Vec<Fail>) {
    let mut head = None;
    let mut names: Vec<String> = Vec::new();
    let mut locs: BTreeMap<String, String> = BTreeMap::new();
    let mut in_test = false;
    for line in text.lines() {
        if head.is_none() {
            if let Some(h) = line
                .strip_prefix("== HEAD=")
                .or_else(|| line.strip_prefix("HEAD-END "))
            {
                head = Some(h.trim().to_owned());
            }
        }
        if line.starts_with("RC test=") {
            in_test = true;
            continue;
        }
        if line.starts_with("RC ") {
            in_test = false;
            continue;
        }
        if !in_test {
            continue;
        }
        if let Some(n) = line
            .strip_prefix("test ")
            .and_then(|r| r.strip_suffix(" ... FAILED"))
        {
            if !names.iter().any(|x| x == n) {
                names.push(n.to_owned());
            }
        } else if let Some((n, loc)) = panic_line(line) {
            locs.entry(n).or_insert(loc);
        }
    }
    let fails = names
        .into_iter()
        .map(|n| {
            let loc = locs.get(&n).cloned().unwrap_or_default();
            (n, loc)
        })
        .collect();
    (head, fails)
}

/// `thread 'NAME' (id) panicked at FILE:LINE:COL:` → (NAME, `FILE:LINE`).
fn panic_line(line: &str) -> Option<(String, String)> {
    let rest = line.strip_prefix("thread '")?;
    let (name, rest) = rest.split_once('\'')?;
    let at = rest.split_once(" panicked at ")?.1;
    let mut parts = at.split(':');
    let file = parts.next()?;
    let ln = parts
        .next()
        .filter(|l| !l.is_empty() && l.bytes().all(|b| b.is_ascii_digit()))?;
    Some((name.to_owned(), format!("{file}:{ln}")))
}

/// One receipt as U6 reads it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Receipt {
    /// The file name without `RECEIPT-` / `.out`.
    pub rid: String,
    pub head: String,
    pub fails: Vec<Fail>,
}

/// Head → the branch / PR it is the head of now (aib-slice's cache). A receipt of a head
/// that is no longer any branch's head is an old run: the red may be fixed since.
pub fn current_heads(sweep: &Value) -> BTreeMap<String, String> {
    let mut m = BTreeMap::new();
    for b in sweep["branches"].as_array().into_iter().flatten() {
        let (Some(h), Some(name)) = (b["head"].as_str(), b["branch"].as_str()) else {
            continue;
        };
        let subject = match b["pr"].as_u64() {
            Some(n) => format!("#{n}"),
            None => name.to_owned(),
        };
        m.insert(h.to_owned(), subject);
    }
    for p in sweep["open_prs"].as_array().into_iter().flatten() {
        if let (Some(h), Some(n)) = (p["head"].as_str(), p["number"].as_u64()) {
            m.insert(h.to_owned(), format!("#{n}"));
        }
    }
    m
}

/// U6: the same test, failing at the same place, red on [`U6_MIN_SUBJECTS`] or more
/// branches / PRs at their current heads ⇒ one fix slice, the others wait and rerun
/// (DECISION §2: the key is the test name and the panic location, not the name alone).
pub fn u6(receipts: &[Receipt], current: &BTreeMap<String, String>) -> Vec<Blocker> {
    let mut by: BTreeMap<(String, String), BTreeMap<String, String>> = BTreeMap::new();
    for r in receipts {
        let Some(subject) = current.get(&r.head) else {
            continue;
        };
        for (name, loc) in &r.fails {
            by.entry((name.clone(), loc.clone()))
                .or_default()
                .entry(subject.clone())
                .or_insert_with(|| r.rid.clone());
        }
    }
    let mut out = Vec::new();
    for ((name, loc), subjects) in by {
        if subjects.len() < U6_MIN_SUBJECTS {
            continue;
        }
        let at = if loc.is_empty() {
            "?".to_owned()
        } else {
            loc.clone()
        };
        let mut evidence: Vec<String> = subjects
            .iter()
            .map(|(s, rid)| format!("{s} 紅(RECEIPT-{rid}.out)"))
            .collect();
        evidence.push(format!(
            "修一片給 {} 的作者,其他等修片後重跑",
            crate_of(&loc)
        ));
        let mut b = Blocker::new(
            "U6",
            &format!("test-{name}-{at}"),
            format!("{name} @ {at} 在 {} 張紅", subjects.len()),
            evidence,
        );
        // the PRs it is red on, not the receipts: a rerun that stays red is not news
        b.fingerprint = format!(
            "red-{}-{}",
            subjects.len(),
            digest(&subjects.keys().cloned().collect::<Vec<_>>())
        );
        out.push(b);
    }
    out
}

/// `crates/<name>/…` → `<name>`; anything else → the path itself (or `?`).
fn crate_of(loc: &str) -> String {
    let file = loc.split(':').next().unwrap_or_default();
    match file
        .strip_prefix("crates/")
        .and_then(|r| r.split('/').next())
    {
        Some(c) if !c.is_empty() => c.to_owned(),
        _ if file.is_empty() => "?".to_owned(),
        _ => file.to_owned(),
    }
}

// ---------------------------------------------------------------- U11

/// One review dispatch dm, as U11 reads it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReviewAsk {
    pub id: String,
    /// The reviewer seat it went to.
    pub to: String,
    /// The dispatch's first line.
    pub line: String,
    /// The head to review: the longest hex run of 8+ in the dm (a full head wins).
    pub head: Option<String>,
    pub sent_at: u64,
    pub read_at: Option<u64>,
}

/// A dm queue item that is a review dispatch still in force; anything else ⇒ `None`
/// (superseded / deleted / expired items were taken back).
pub fn review_ask(item: &Value) -> Option<ReviewAsk> {
    let msg = item["msg"].as_str()?;
    let line = msg.lines().next()?.trim();
    if !line.starts_with(REVIEW_ASK) {
        return None;
    }
    let status = item["status"].as_str().unwrap_or_default();
    if matches!(status, "superseded" | "deleted" | "expired") {
        return None;
    }
    Some(ReviewAsk {
        id: item["id"].as_str()?.to_owned(),
        to: item["seat"].as_str()?.to_owned(),
        line: line.to_owned(),
        head: longest_hex(msg),
        sent_at: item["sent_at"].as_u64()?,
        read_at: item["read_at"].as_u64(),
    })
}

/// The longest run of 8–40 lowercase hex characters in `s` (a head or its short form).
pub fn longest_hex(s: &str) -> Option<String> {
    s.split(|c: char| !(c.is_ascii_digit() || ('a'..='f').contains(&c)))
        .filter(|t| (8..=40).contains(&t.len()) && t.bytes().any(|b| b.is_ascii_digit()))
        .max_by_key(|t| t.len())
        .map(str::to_owned)
}

/// The head named on a review's first line (`頭: <sha>`), or `None`.
pub fn review_head(first_line: &str) -> Option<String> {
    let rest = first_line.split_once("頭:")?.1.trim_start();
    longest_hex(rest.split_whitespace().next()?)
}

/// U11: a review dispatch read [`U11_AFTER_SECS`] or more ago whose head has no review
/// yet (no `REVIEW-*.md` whose first line names that head) ⇒ hand it to a free reviewer.
/// An unread dispatch is act's nudge, not U11 (56cda opinion, DECISION §2); one older than
/// a day is left to the people.
pub fn u11(asks: &[ReviewAsk], reviewed: &BTreeSet<String>, now: u64) -> Vec<Blocker> {
    let mut latest: BTreeMap<(String, String), &ReviewAsk> = BTreeMap::new();
    for a in asks {
        let Some(h) = &a.head else { continue };
        if now.saturating_sub(a.sent_at) > DAY_SECS {
            continue;
        }
        let k = (h.clone(), a.to.clone());
        if latest.get(&k).is_none_or(|b| b.sent_at < a.sent_at) {
            latest.insert(k, a);
        }
    }
    let mut out = Vec::new();
    for ((head, to), a) in latest {
        let Some(read) = a.read_at else { continue };
        let waited = now.saturating_sub(read);
        if waited < U11_AFTER_SECS {
            continue;
        }
        if reviewed
            .iter()
            .any(|r| r.starts_with(head.as_str()) || head.starts_with(r.as_str()))
        {
            continue;
        }
        let short = head.get(..8).unwrap_or(&head);
        let mut b = Blocker::new(
            "U11",
            &format!("review-{short}-{to}"),
            format!("{} 已讀 {} 分沒有審查稿", a.line, waited / 60),
            vec![
                format!("{} → @{to}", a.id),
                format!("沒有第一行頭碼是 {short} 的 REVIEW-*.md"),
                "改派有空的審查席".to_owned(),
            ],
        );
        b.fingerprint = format!("ask-{}", slug(&a.id));
        b.owner = to.clone();
        b.head = (head.len() == 40).then(|| head.clone());
        b.since_ms = (read + U11_AFTER_SECS) * 1000;
        out.push(b);
    }
    out
}

// ---------------------------------------------------------------- U8

/// U8: act's `gone` lines (a nudge whose seat is not there; final) of the last day, one
/// row per seat (DECISION §2: read act's ledger, do not detect it a second time).
pub fn u8(journal: &str, now: u64) -> Vec<Blocker> {
    let mut by: BTreeMap<String, (Vec<String>, String, u64)> = BTreeMap::new();
    for line in journal.lines() {
        let Ok(v) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if v["kind"].as_str() != Some("gone") {
            continue;
        }
        let at = v["at_ms"].as_u64().map_or(0, |ms| ms / 1000);
        if now.saturating_sub(at) > DAY_SECS {
            continue;
        }
        let Some(key) = v["key"].as_str() else {
            continue;
        };
        let parts: Vec<&str> = key.split(':').collect();
        let (Some(dm), Some(seat)) = (parts.get(1), parts.last()) else {
            continue;
        };
        let e = by.entry((*seat).to_owned()).or_default();
        if !e.0.iter().any(|d| d == dm) {
            e.0.push((*dm).to_owned());
        }
        e.1 = v["why"].as_str().unwrap_or_default().to_owned();
        let ms = at * 1000;
        if e.2 == 0 || ms < e.2 {
            e.2 = ms;
        }
    }
    by.into_iter()
        .map(|(seat, (dms, why, first_ms))| {
            let shown: Vec<&str> = dms.iter().take(5).map(String::as_str).collect();
            let fingerprint = format!("gone-{}-{}", dms.len(), digest(&dms));
            let mut b = Blocker::new(
                "U8",
                &format!("seat-{seat}"),
                format!("@{seat} 不在,{} 封信沒人收", dms.len()),
                vec![
                    format!(
                        "信 {}{}",
                        shown.join("、"),
                        if dms.len() > 5 { " …" } else { "" }
                    ),
                    format!("act: {why}"),
                    "信裡的事改派(U13),信交 S82-g".to_owned(),
                ],
            );
            b.fingerprint = fingerprint;
            b.since_ms = first_ms;
            b
        })
        .collect()
}

/// Every u-4 row.
pub fn blockers(
    receipts: &[Receipt],
    current: &BTreeMap<String, String>,
    asks: &[ReviewAsk],
    reviewed: &BTreeSet<String>,
    journal: &str,
    now: u64,
) -> Vec<Blocker> {
    let mut v = u6(receipts, current);
    v.extend(u11(asks, reviewed, now));
    v.extend(u8(journal, now));
    v
}

// ---------------------------------------------------------------- reading the files

fn age_secs(e: &std::fs::DirEntry, now: u64) -> u64 {
    e.metadata()
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map_or(u64::MAX, |d| now.saturating_sub(d.as_secs()))
}

/// `pending/<prefix>*<suffix>` changed in the last `max_age` seconds, as (name, path).
fn recent(
    dir: &Path,
    prefix: &str,
    suffix: &str,
    now: u64,
    max_age: u64,
) -> Option<Vec<(String, std::path::PathBuf)>> {
    let rd = std::fs::read_dir(dir).ok()?;
    let mut v = Vec::new();
    for e in rd.flatten() {
        let name = e.file_name().to_string_lossy().into_owned();
        if name.starts_with(prefix) && name.ends_with(suffix) && age_secs(&e, now) <= max_age {
            v.push((name, e.path()));
        }
    }
    v.sort();
    Some(v)
}

fn receipts(root: &Path, now: u64) -> Option<Vec<Receipt>> {
    let files = recent(&root.join("pending"), "RECEIPT-", ".out", now, DAY_SECS)?;
    let mut v = Vec::new();
    for (name, path) in files {
        let Ok(text) = std::fs::read_to_string(&path) else {
            continue;
        };
        let (Some(head), fails) = receipt_fails(&text) else {
            continue;
        };
        let rid = name
            .trim_start_matches("RECEIPT-")
            .trim_end_matches(".out")
            .to_owned();
        v.push(Receipt { rid, head, fails });
    }
    Some(v)
}

/// Heads named on the first line of the reviews written in the last two days.
fn reviewed(root: &Path, now: u64) -> Option<BTreeSet<String>> {
    use std::io::BufRead;
    let files = recent(&root.join("pending"), "REVIEW-", ".md", now, 2 * DAY_SECS)?;
    let mut s = BTreeSet::new();
    for (_, path) in files {
        let Ok(f) = std::fs::File::open(&path) else {
            continue;
        };
        let mut first = String::new();
        if std::io::BufReader::new(f).read_line(&mut first).is_ok() {
            if let Some(h) = review_head(&first) {
                s.insert(h);
            }
        }
    }
    Some(s)
}

/// Review dispatches among the dm queue items changed in the last day
/// (`state/dm-queue/<seat>/<id>.json`).
fn asks(root: &Path, now: u64) -> Option<Vec<ReviewAsk>> {
    let rd = std::fs::read_dir(root.join("state/dm-queue")).ok()?;
    let mut v = Vec::new();
    for seat in rd.flatten() {
        if !seat.path().is_dir() {
            continue;
        }
        let Ok(items) = std::fs::read_dir(seat.path()) else {
            continue;
        };
        for e in items.flatten() {
            let name = e.file_name();
            let name = name.to_string_lossy();
            if !(name.starts_with("q_") && name.ends_with(".json")) || age_secs(&e, now) > DAY_SECS
            {
                continue;
            }
            let Some(item) = std::fs::read(e.path())
                .ok()
                .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
            else {
                continue;
            };
            if let Some(a) = review_ask(&item) {
                v.push(a);
            }
        }
    }
    Some(v)
}

/// id → `since_ms` of the rows the last `--write` kept (none when it cannot be read).
fn first_seen(path: &Path) -> BTreeMap<String, u64> {
    let Some(v) = std::fs::read(path)
        .ok()
        .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
    else {
        return BTreeMap::new();
    };
    v["rows"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|r| Some((r["id"].as_str()?.to_owned(), r["since_ms"].as_u64()?)))
        .filter(|(_, ms)| *ms > 0)
        .collect()
}

/// IFACE v2 `since_ms`: kept from the last write when the id was there, else what the
/// rule knows, else this round.
pub fn carry_since(seen: &BTreeMap<String, u64>, b: &Blocker, now_ms: u64) -> u64 {
    match seen.get(&b.id) {
        Some(&ms) => ms,
        None if b.since_ms > 0 => b.since_ms,
        None => now_ms,
    }
}

pub const USAGE: &str =
    "usage: aib-drive unblock-u4 [--json] [--write]   (read-only; --write keeps the rows in drive/unblock-src/u-4.json)\n";

/// `aib-drive unblock-u4`: rc 0 (rows or none), 2 usage / no state root. A source that
/// cannot be read is listed in `unreadable`, never taken for "no blocker" (U21).
pub fn run(args: &[String], root: Option<&Path>, now: u64) -> (i32, String) {
    let (mut as_json, mut write) = (false, false);
    for a in args {
        match a.as_str() {
            "--json" => as_json = true,
            "--write" => write = true,
            _ => return (2, USAGE.to_owned()),
        }
    }
    let Some(root) = root else {
        return (2, "aib-drive unblock-u4: no state root\n".to_owned());
    };
    // (name, why, the one code it covers): IFACE v2 r3 `unreadable` [{name, why, covers}]; v-2 takes a row of a
    // covered code that went away for unknown, not cleared
    let mut unreadable: Vec<(&str, &str, &str)> = Vec::new();
    let current = match std::fs::read(root.join("drive/slice-sweep.json"))
        .ok()
        .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
    {
        Some(s) => current_heads(&s),
        None => {
            unreadable.push(("drive/slice-sweep.json", "讀不到,U6 不判", "U6"));
            BTreeMap::new()
        }
    };
    let rec = receipts(root, now).unwrap_or_else(|| {
        unreadable.push(("pending/RECEIPT-*.out", "讀不到,U6 不判", "U6"));
        Vec::new()
    });
    let mut ask = asks(root, now).unwrap_or_else(|| {
        unreadable.push(("state/dm-queue", "讀不到,U11 不判", "U11"));
        Vec::new()
    });
    // with no review list every dispatch would look untaken: U11 is not judged
    let rev = reviewed(root, now).unwrap_or_else(|| {
        unreadable.push(("pending/REVIEW-*.md", "讀不到,U11 不判", "U11"));
        ask.clear();
        BTreeSet::new()
    });
    let journal = match std::fs::read_to_string(root.join("drive/act-dryrun.jsonl")) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(_) => {
            unreadable.push(("drive/act-dryrun.jsonl", "讀不到,U8 不判", "U8"));
            String::new()
        }
    };
    let mut rows = blockers(&rec, &current, &ask, &rev, &journal, now);
    let out_path = root.join("drive").join(OUT_FILE);
    let seen = first_seen(&out_path);
    let now_ms = now * 1000;
    for b in &mut rows {
        b.since_ms = carry_since(&seen, b, now_ms);
    }
    let j = json!({
        "schema": SCHEMA,
        "as_of_ms": now_ms,
        "rows": rows.iter().map(|b| b.to_json(now_ms)).collect::<Vec<_>>(),
        // U21: what could not be read is said; v-2 takes it for sources[].why
        "unreadable": unreadable.iter().map(|(name, why, covers)| json!({"name": name, "why": why, "covers": [covers]})).collect::<Vec<_>>(),
    });
    if write {
        let Some(dir) = out_path.parent() else {
            return (1, format!("aib-drive unblock-u4: {OUT_FILE}: no folder\n"));
        };
        let tmp = dir.join(".u-4.json.tmp");
        if let Err(e) = std::fs::create_dir_all(dir)
            .and_then(|()| std::fs::write(&tmp, format!("{j}\n")))
            .and_then(|()| std::fs::rename(&tmp, &out_path))
        {
            return (1, format!("aib-drive unblock-u4: {OUT_FILE}: {e}\n"));
        }
    }
    if as_json {
        return (0, format!("{j}\n"));
    }
    let mut out = String::new();
    for b in &rows {
        out.push_str(&format!(
            "{} {} → {}:{}\n",
            b.id,
            b.what,
            if b.owner.is_empty() { "?" } else { &b.owner },
            b.evidence.join(";")
        ));
    }
    if rows.is_empty() {
        out.push_str("u-4:沒有卡點\n");
    }
    if !unreadable.is_empty() {
        out.push_str(&format!(
            "讀不到(不算沒卡點):{}\n",
            unreadable
                .iter()
                .map(|(n, w, _)| format!("{n}({w})"))
                .collect::<Vec<_>>()
                .join("、")
        ));
    }
    (0, out)
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW: u64 = 1_794_746_000;
    const HA: &str = "aaaaaaaa1111111111111111111111111111aaaa";
    const HB: &str = "bbbbbbbb2222222222222222222222222222bbbb";
    const HC: &str = "cccccccc3333333333333333333333333333cccc";

    /// A receipt whose test phase fails `tests` (name, loc) and whose knife phase fails a
    /// knife's own test, as receipt-run prints them.
    fn receipt_text(head: &str, tests: &[(&str, &str)]) -> String {
        let mut t = format!("START x\n== HEAD={head}\nRC fmt=0\nRC clippy=0\n");
        t.push_str(if tests.is_empty() {
            "RC test=0\n"
        } else {
            "RC test=101\n"
        });
        for (n, loc) in tests {
            t.push_str(&format!("test {n} ... FAILED\n"));
            t.push_str(&format!("thread '{n}' (123) panicked at {loc}:5:\n"));
        }
        t.push_str("TESTS-TOTAL suites=1 passed=1 failed=1\n");
        t.push_str("RC knife_K1=101 (want non-0)\n");
        t.push_str("test knife_only_test ... FAILED\n");
        t.push_str("thread 'knife_only_test' (9) panicked at crates/x/src/k.rs:1:1:\n");
        t.push_str(&format!("HEAD-END {head}\n"));
        t
    }

    fn rec(rid: &str, head: &str, tests: &[(&str, &str)]) -> Receipt {
        let (h, fails) = receipt_fails(&receipt_text(head, tests));
        Receipt {
            rid: rid.into(),
            head: h.expect("head"),
            fails,
        }
    }

    fn current() -> BTreeMap<String, String> {
        current_heads(&json!({
            "branches": [
                {"branch": "feat/a-1ced7-1115", "head": HA, "pr": 801},
                {"branch": "feat/b-a9136-1115", "head": HB, "pr": null},
            ],
            "open_prs": [{"number": 802, "head": HC, "head_ref": "feat/c-ebb36-1115"}],
        }))
    }

    /// The test phase only: a knife's red is not a test red, and the panic place is kept.
    #[test]
    fn a_receipt_gives_its_head_and_only_the_test_phase_reds() {
        let (h, f) = receipt_fails(&receipt_text(
            HA,
            &[("t_one", "crates/aibd/tests/s.rs:529")],
        ));
        assert_eq!(h.as_deref(), Some(HA));
        assert_eq!(
            f,
            vec![("t_one".to_owned(), "crates/aibd/tests/s.rs:529".to_owned())]
        );
        assert!(receipt_fails(&receipt_text(HA, &[])).1.is_empty());
    }

    /// DECISION §2: the same test at the same place red on two PRs is one U6 row; on one PR,
    /// at two places, or on a head that moved on, it is not.
    #[test]
    fn one_test_red_at_one_place_on_two_prs_is_one_row() {
        let loc = "crates/aibd/tests/s.rs:529";
        let rs = vec![
            rec(
                "r1-1ced7-aaaaaaaa",
                HA,
                &[("t_one", loc), ("t_solo", "crates/aib/src/a.rs:3")],
            ),
            rec("r2-a9136-bbbbbbbb", HB, &[("t_one", loc)]),
            rec(
                "r3-ebb36-cccccccc",
                HC,
                &[("t_one", "crates/aibd/tests/s.rs:600")],
            ),
            // an old head of 801: not counted again, the red may be fixed
            rec(
                "r0-1ced7-dddddddd",
                "dddddddd4444444444444444444444444444dddd",
                &[("t_one", loc)],
            ),
        ];
        let rows = u6(&rs, &current());
        assert_eq!(rows.len(), 1, "{rows:?}");
        assert_eq!(rows[0].id, "U6:test-t-one-crates-aibd-tests-s-rs-529");
        assert!(rows[0].fingerprint.starts_with("red-2-"), "{rows:?}");
        assert!(rows[0].what.contains("2 張"), "{rows:?}");
        assert!(
            rows[0].evidence.iter().any(|e| e.starts_with("#801 紅")),
            "{rows:?}"
        );
        assert!(rows[0]
            .evidence
            .iter()
            .any(|e| e.starts_with("feat/b-a9136-1115 紅")));
        assert!(rows[0].evidence.iter().any(|e| e.contains("aibd 的作者")));
        // two receipts of one PR are one PR
        let same = vec![
            rec("r1", HA, &[("t_one", loc)]),
            rec("r1b", HA, &[("t_one", loc)]),
        ];
        assert!(u6(&same, &current()).is_empty());
    }

    fn ask(id: &str, to: &str, head: &str, sent: u64, read: Option<u64>) -> ReviewAsk {
        review_ask(&json!({
            "id": id, "seat": to, "status": "done", "sent_at": sent, "read_at": read,
            "msg": format!("派工: 審 S79 u4 {} → {to}\n頭 {head}(未推)", &head[..8]),
        }))
        .expect("a review dispatch")
    }

    /// 56cda opinion / DECISION §2: read 30 minutes ago with no review of its head ⇒ U11;
    /// unread (act nudges it), read lately, or reviewed ⇒ not.
    #[test]
    fn a_review_read_thirty_minutes_ago_with_no_review_of_its_head_is_a_row() {
        let reviewed: BTreeSet<String> =
            [review_head(&format!("結論: 通過 頭: {HC}")).expect("head")]
                .into_iter()
                .collect();
        let asks = vec![
            ask("q_1", "5d33a", HA, NOW - 3600, Some(NOW - U11_AFTER_SECS)),
            ask("q_2", "5d33a", HB, NOW - 3600, None),
            ask(
                "q_3",
                "1ced7",
                HB,
                NOW - 3600,
                Some(NOW - U11_AFTER_SECS + 60),
            ),
            ask("q_4", "1ced7", HC, NOW - 3600, Some(NOW - 3000)),
            // a day old: left to the people
            ask("q_5", "1ced7", HA, NOW - DAY_SECS - 1, Some(NOW - DAY_SECS)),
        ];
        let rows = u11(&asks, &reviewed, NOW);
        assert_eq!(rows.len(), 1, "{rows:?}");
        assert_eq!(rows[0].id, "U11:review-aaaaaaaa-5d33a");
        assert_eq!(rows[0].owner, "5d33a");
        assert_eq!(rows[0].head.as_deref(), Some(HA));
        assert_eq!(rows[0].since_ms, NOW * 1000, "untaken since read + 30 min");
        assert!(rows[0].evidence[0].starts_with("q_1 → @5d33a"), "{rows:?}");
        // a review naming the short head counts too
        let short: BTreeSet<String> = ["aaaaaaaa1111".to_owned()].into_iter().collect();
        assert!(u11(&asks[..1], &short, NOW).is_empty());
    }

    /// Only a 派工: 審 dm still in force is a review dispatch.
    #[test]
    fn only_a_review_dispatch_in_force_is_read() {
        let item = |msg: &str, status: &str| json!({"id": "q_9", "seat": "5d33a", "status": status, "sent_at": NOW, "msg": msg});
        assert!(review_ask(&item(&format!("派工: 審 x {HA} → 5d33a"), "queued")).is_some());
        assert!(review_ask(&item(&format!("派工: 審 x {HA} → 5d33a"), "superseded")).is_none());
        assert!(review_ask(&item(&format!("派工: 修 x {HA} → 5d33a"), "queued")).is_none());
        assert_eq!(
            longest_hex(&format!("頭 {} 與 {HA}", &HB[..8])).as_deref(),
            Some(HA)
        );
        assert_eq!(longest_hex("沒有頭碼 abcdef"), None);
    }

    /// DECISION §2: U8 reads act's gone lines (one row per seat, the last day); retry lines
    /// and old gone lines are not U8.
    #[test]
    fn act_gone_lines_of_the_last_day_are_one_row_per_seat() {
        let ms = |s: u64| s * 1000;
        let journal = [
            json!({"kind": "gone", "at_ms": ms(NOW - 60), "key": "nudge:q_a:-:c477a", "why": "aib dm stage resolve, exit Some(2)"}),
            json!({"kind": "gone", "at_ms": ms(NOW - 50), "key": "nudge:q_b:-:c477a", "why": "aib dm stage resolve, exit Some(2)"}),
            json!({"kind": "gone", "at_ms": ms(NOW - 40), "key": "nudge:q_b:-:c477a", "why": "again"}),
            json!({"kind": "retry", "at_ms": ms(NOW - 30), "key": "nudge:q_c:-:f88fa", "why": "busy"}),
            json!({"kind": "gone", "at_ms": ms(NOW - DAY_SECS - 1), "key": "nudge:q_d:-:0c105", "why": "old"}),
        ]
        .iter()
        .map(Value::to_string)
        .collect::<Vec<_>>()
        .join("\n");
        let rows = u8(&journal, NOW);
        assert_eq!(rows.len(), 1, "{rows:?}");
        assert_eq!(rows[0].id, "U8:seat-c477a");
        assert_eq!(rows[0].since_ms, ms(NOW - 60), "the first gone line");
        assert!(rows[0].fingerprint.starts_with("gone-2-"), "{rows:?}");
        assert!(rows[0].what.contains("2 封"), "{rows:?}");
        assert!(rows[0].evidence[0].contains("q_a、q_b"), "{rows:?}");
    }

    /// IFACE-unblock-json-v2 r0: id = code:subject with no colon in the subject or the
    /// fingerprint, no `blocks` from a source, and `since_ms` kept from the last write.
    #[test]
    fn a_row_has_the_v2_shape_and_keeps_when_it_was_first_seen() {
        assert_eq!(slug("review-AAAA:5d33a / 審"), "review-aaaa-5d33a");
        assert_eq!(digest(&["a".into(), "b".into()]).len(), 8);
        assert_ne!(
            digest(&["ab".into()]),
            digest(&["a".into(), "b".into()]),
            "parts are kept apart"
        );
        let mut b = Blocker::new(
            "U8",
            "seat-C477A",
            "x".into(),
            vec!["e1".into(), "e2".into()],
        );
        b.fingerprint = "gone-1-0".into();
        let j = b.to_json(7);
        assert_eq!(j["id"], "U8:seat-c477a");
        assert_eq!(j["evidence"], "e1; e2");
        assert_eq!(
            (j["as_of_ms"].as_u64(), j["source"].as_str()),
            (Some(7), Some(SOURCE))
        );
        assert!(j.get("blocks").is_none() && j.get("head").is_none(), "{j}");
        let seen: BTreeMap<String, u64> = [("U8:seat-c477a".to_owned(), 5)].into_iter().collect();
        assert_eq!(carry_since(&seen, &b, 9), 5, "kept from the last write");
        b.since_ms = 6;
        assert_eq!(
            carry_since(&BTreeMap::new(), &b, 9),
            6,
            "what the rule knows"
        );
        b.since_ms = 0;
        assert_eq!(carry_since(&BTreeMap::new(), &b, 9), 9, "else this round");
    }
}
