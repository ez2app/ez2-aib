//! `aib-release binsync`: put a locally used Rust tool where the cockpit runs it, only when
//! its build says it came clean from main, and keep a ledger and one previous copy
//! (goal2 title-beat step 2, PLAN-aibtitle-install-043dd-1113 r2 package 3).
//!
//! Until now every tool in `~/.ez2-aib/bin` was copied there by hand: nothing recorded which
//! commit a file was built from, a PR head could go on (the EU02 #638 lesson, 11-11), and a
//! `cp` over a file that a pane execs every frame can be read half written or keep a stale
//! code signature on macOS. Here:
//!
//! * **only tools on [`ALLOWED`]** (today `aib-title`), and only a regular file at the
//!   destination (a symlink there is refused);
//! * the file is read **once**; its hash, its `aib-build-info` and its `aib-tool-info` come
//!   from those bytes, and those bytes are what gets written (Codex H on r1: check A, install
//!   B);
//! * the tool info names this tool and a target this machine runs (`aib-tool-info`, written by
//!   the tool's build.rs: the build-info mark alone is the same in every tool);
//! * the git check is [`host_build::check`] as is: built clean, an ancestor of
//!   `origin/main` as this clone last fetched it, a descendant of what is there now unless
//!   `--allow-downgrade <why>`. Missing objects are "could not answer" (rc 3), never "not on
//!   main". The answer names the main commit it was judged against and when origin/main last
//!   moved in this clone: it says "matches the local snapshot", not "is on main now". Nothing
//!   is fetched. The same file again is `same` (no swap: `.prev` stays the real previous one);
//! * one writer at a time: an exclusive lock on `<dest>/.aib-binsync.lock` (held for the
//!   whole install / rollback; a second one gets rc 3 `busy`);
//! * the swap: write a new file with a name of its own (`create_new`), make it executable,
//!   sync, read it back and compare the hash, then `rename` it over the tool. The tool path
//!   is there the whole time: a reader gets the old file or the new one. The old file is
//!   first copied to `<name>.prev` the same way. A process already running the old file
//!   keeps it (rename never edits a file in place, which is what upsets macOS code
//!   signing). Windows cannot rename over a running .exe: not handled here (PLAN 2d);
//! * the ledger `<dest>/BINS.json` is written last, also new file + rename. It is a record,
//!   not the authority on what runs: whether a binary and the repository agree is the
//!   handshake's job (aib-title SAMPLER_PROTO).
//!
//! Stopped half way (killed, power): the next install or rollback removes an install's
//! leftover temporary files (`.<name>.tmp-new-*` / `-prev-*`, copies of files still in
//! place) under the lock; a rollback's (`-back-*` / `-fwd-*`) may be the only copy of a
//! version, so they are kept as `<name>.rescued-*` and `status` lists them. Every rename is
//! followed by a directory sync. An install stopped after `.prev` was replaced but before the
//! tool was: the tool is the old one and `.prev` is a copy of it (the `.prev` before is gone,
//! as it would be after a finished install). A tool renamed but a ledger not yet written:
//! rc 0 `ledger_stale`, `status` says so, installing the same file again writes the ledger
//! (`same`). An unreadable ledger is never written over (`ledger_unreadable`, rc 3).
//!
//! Exit: 0 done / status read, 2 refused, 3 could not answer (git, I/O, busy), 64 usage.

use crate::host_build::{self, info_in_binary, BuildInfo, Current, Git, Verdict};
use serde_json::{json, Map, Value};
use std::fs::{self, File, OpenOptions};
use std::io::{ErrorKind, Read, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

/// The tools this command installs. A name not here is refused: the other tools in
/// `~/.ez2-aib/bin` belong to the seats that wrote them until they ask to be added.
pub const ALLOWED: &[&str] = &["aib-title"];
pub const LEDGER: &str = "BINS.json";
pub const LOCK: &str = ".aib-binsync.lock";
pub const TOOL_MARK: &str = "aib-tool-info ";
/// History lines kept in the ledger per tool.
pub const HISTORY_MAX: usize = 20;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolInfo {
    pub name: String,
    pub target: String,
}

fn triple_char(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_' || b == b'-' || b == b'.'
}

/// `aib-tool-info name=N target=T;` (the mark and the `;` may be left out): both keys once,
/// in that order, nothing else.
pub fn parse_tool_info(line: &str) -> Option<ToolInfo> {
    let s = line.trim();
    let s = s.strip_prefix(TOOL_MARK).unwrap_or(s);
    let s = s.strip_suffix(';').unwrap_or(s);
    let mut parts = s.split(' ');
    let name = parts.next()?.strip_prefix("name=")?;
    let target = parts.next()?.strip_prefix("target=")?;
    if parts.next().is_some()
        || name.is_empty()
        || target.is_empty()
        || !name.bytes().all(triple_char)
        || !target.bytes().all(triple_char)
    {
        return None;
    }
    Some(ToolInfo {
        name: name.to_owned(),
        target: target.to_owned(),
    })
}

/// The tool info written into a built file; every copy must say the same thing.
pub fn tool_info_in_binary(bytes: &[u8]) -> Result<ToolInfo, String> {
    let mark = TOOL_MARK.as_bytes();
    let mut found: Option<ToolInfo> = None;
    let mut i = 0;
    while let Some(at) = bytes[i..].windows(mark.len()).position(|w| w == mark) {
        let start = i + at;
        let rest = &bytes[start..];
        let end = rest
            .iter()
            .take(200)
            .position(|b| *b == b';')
            .ok_or("tool info without its end")?;
        let line = std::str::from_utf8(&rest[..=end]).map_err(|_| "tool info is not text")?;
        let info =
            parse_tool_info(line).ok_or_else(|| format!("tool info not understood: {line:?}"))?;
        match &found {
            Some(f) if *f != info => return Err("the file carries two different tool infos".into()),
            _ => found = Some(info),
        }
        i = start + mark.len();
    }
    found.ok_or_else(|| "no tool info in the file (built before tool info existed?)".into())
}

/// Whether a target triple is one this machine runs: same architecture, same OS family.
pub fn target_fits(target: &str, os: &str, arch: &str) -> bool {
    let arch_ok = target.split('-').next() == Some(arch);
    let os_ok = match os {
        "macos" => target.contains("-apple-darwin"),
        "linux" => target.contains("-linux-"),
        "windows" => target.contains("-windows-"),
        _ => false,
    };
    arch_ok && os_ok
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    host_build::sha256_hex(bytes)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Outcome {
    pub verdict: Verdict,
    pub code: &'static str,
    pub detail: String,
    /// Everything else the answer carries (hashes, main, fetch time, allowed flags).
    pub facts: Map<String, Value>,
}

impl Outcome {
    fn new(verdict: Verdict, code: &'static str, detail: impl Into<String>) -> Self {
        Self {
            verdict,
            code,
            detail: detail.into(),
            facts: Map::new(),
        }
    }
    fn with(mut self, k: &str, v: Value) -> Self {
        self.facts.insert(k.to_owned(), v);
        self
    }
    pub fn to_json(&self) -> Value {
        let mut m = self.facts.clone();
        m.insert(
            "verdict".into(),
            json!(match self.verdict {
                Verdict::Ok => "ok",
                Verdict::Refused => "refused",
                Verdict::Error => "error",
            }),
        );
        m.insert("code".into(), json!(self.code));
        m.insert("detail".into(), json!(self.detail));
        Value::Object(m)
    }
    pub fn exit_code(&self) -> i32 {
        match self.verdict {
            Verdict::Ok => 0,
            Verdict::Refused => 2,
            Verdict::Error => 3,
        }
    }
}

fn err(code: &'static str, detail: impl Into<String>) -> Outcome {
    Outcome::new(Verdict::Error, code, detail)
}
fn refuse(code: &'static str, detail: impl Into<String>) -> Outcome {
    Outcome::new(Verdict::Refused, code, detail)
}

/// What one install asks for. `bytes` is the candidate file, read once by the caller.
#[derive(Debug, Clone)]
pub struct Install {
    pub dest: PathBuf,
    pub name: String,
    pub bytes: Vec<u8>,
    pub by: String,
    pub allow_downgrade: Option<String>,
    pub allow_unknown_current: Option<String>,
    /// When origin/main last moved in this clone (its reflog), printed with the answer: a
    /// main judged against that is old shows as old (it can only look older than it is).
    pub main_moved_at: Option<u64>,
    /// The OS / architecture this runs on (`std::env::consts` in the command; fixed in tests).
    pub os: String,
    pub arch: String,
    pub now: u64,
}

fn tool_path(dest: &Path, name: &str) -> PathBuf {
    dest.join(name)
}
fn prev_path(dest: &Path, name: &str) -> PathBuf {
    dest.join(format!("{name}.prev"))
}
fn tmp_prefix(name: &str) -> String {
    format!(".{name}.tmp-")
}

/// A regular file, a missing path, or something this refuses to replace.
enum Slot {
    Missing,
    File(Vec<u8>),
}

fn read_slot(p: &Path) -> Result<Slot, Outcome> {
    match fs::symlink_metadata(p) {
        Err(e) if e.kind() == ErrorKind::NotFound => Ok(Slot::Missing),
        Err(e) => Err(err("io", format!("{}: {e}", p.display()))),
        Ok(m) if !m.file_type().is_file() => Err(refuse(
            "not_regular",
            format!(
                "{} is not a regular file (a symlink or a directory is never replaced)",
                p.display()
            ),
        )),
        Ok(_) => fs::read(p)
            .map(Slot::File)
            .map_err(|e| err("io", format!("{}: {e}", p.display()))),
    }
}

/// The exclusive lock for one install / rollback; released when dropped.
pub struct Lock(#[allow(dead_code)] File);

pub fn lock(dest: &Path) -> Result<Lock, Outcome> {
    let p = dest.join(LOCK);
    let f = OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(&p)
        .map_err(|e| err("io", format!("{}: {e}", p.display())))?;
    match f.try_lock() {
        Ok(()) => Ok(Lock(f)),
        Err(fs::TryLockError::WouldBlock) => Err(err(
            "busy",
            format!("another install or rollback holds {}", p.display()),
        )),
        Err(fs::TryLockError::Error(e)) => Err(err("io", format!("{}: {e}", p.display()))),
    }
}

/// Leftover temporary files of this tool (a run stopped half way), under the lock only.
/// An install's (`new`, `prev`) and the ledger's are copies of something still in place and
/// are removed. A rollback's (`back`, `fwd`) may be the only copy of a version (stopped
/// between its two renames, §18 review M1), so they are never removed: each is renamed to
/// `<name>.rescued-<what>-<now>-<n>` and counted; `status` lists them. Returns (removed,
/// rescued).
fn sweep(dest: &Path, name: &str, now: u64) -> Result<(usize, usize), Outcome> {
    let pre = tmp_prefix(name);
    let (mut removed, mut rescued) = (0, 0);
    let rd = fs::read_dir(dest).map_err(|e| err("io", format!("{}: {e}", dest.display())))?;
    for e in rd.flatten() {
        let fname = e.file_name().to_string_lossy().into_owned();
        let Some(rest) = fname.strip_prefix(&pre) else {
            continue;
        };
        let what = rest.split('-').next().unwrap_or("");
        if matches!(what, "back" | "fwd") {
            // never over an earlier rescue (two runs in the same second, 33e28 N1)
            let mut k = 0;
            let to = loop {
                let t = dest.join(format!("{name}.rescued-{what}-{now}-{rescued}-{k}"));
                if fs::symlink_metadata(&t).is_err() {
                    break t;
                }
                k += 1;
            };
            put(&e.path(), &to)?;
            rescued += 1;
        } else {
            fs::remove_file(e.path())
                .map_err(|x| err("io", format!("{}: {x}", e.path().display())))?;
            removed += 1;
        }
    }
    sync_dir(dest);
    Ok((removed, rescued))
}

/// Both sweeps an install / rollback makes: this tool's temps and the ledger's.
fn sweep_all(dest: &Path, name: &str, now: u64) -> Result<(usize, usize), Outcome> {
    let (a, b) = sweep(dest, name, now)?;
    let (c, d) = sweep(dest, "BINS", now)?;
    Ok((a + c, b + d))
}

fn sync_dir(dest: &Path) {
    // directory entries reach the disk (unix); Windows cannot open a directory this way and
    // its rename is already journalled by NTFS
    #[cfg(unix)]
    if let Ok(d) = File::open(dest) {
        let _ = d.sync_all();
    }
    #[cfg(not(unix))]
    let _ = dest;
}

/// Write `bytes` to a new temporary file of its own in `dest` (0755 for a tool, 0644 for the
/// ledger), synced and read back; return its path. The caller renames it into place.
fn stage(
    dest: &Path,
    name: &str,
    what: &str,
    bytes: &[u8],
    now: u64,
    exec: bool,
) -> Result<PathBuf, Outcome> {
    let p = dest.join(format!(
        "{}{what}-{}-{now}-{}",
        tmp_prefix(name),
        std::process::id(),
        sha256_hex(bytes).get(..12).unwrap_or("")
    ));
    let io = |e: std::io::Error| err("io", format!("{}: {e}", p.display()));
    let mut f = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&p)
        .map_err(io)?;
    f.write_all(bytes).map_err(io)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = if exec { 0o755 } else { 0o644 };
        f.set_permissions(fs::Permissions::from_mode(mode))
            .map_err(io)?;
    }
    f.sync_all().map_err(io)?;
    drop(f);
    let mut back = Vec::new();
    File::open(&p)
        .and_then(|mut r| r.read_to_end(&mut back))
        .map_err(io)?;
    if back != bytes {
        let _ = fs::remove_file(&p);
        return Err(err(
            "io",
            format!("{} did not read back what was written", p.display()),
        ));
    }
    Ok(p)
}

/// Rename, then sync the directory so this rename reaches the disk before the next one
/// (§18 review L1: two renames in a row may otherwise land out of order on power loss).
fn put(from: &Path, to: &Path) -> Result<(), Outcome> {
    fs::rename(from, to).map_err(|e| {
        err(
            "io",
            format!("rename {} -> {}: {e}", from.display(), to.display()),
        )
    })?;
    if let Some(d) = to.parent() {
        sync_dir(d);
    }
    Ok(())
}

/// The ledger; a missing one is empty. Anything else that cannot be read (bad JSON, not an
/// object, a permission error) is an error, never "empty": writing over it would drop every
/// tool's record and history (§18 review L3).
fn read_ledger(dest: &Path) -> Result<Map<String, Value>, Outcome> {
    let p = dest.join(LEDGER);
    let text = match fs::read_to_string(&p) {
        Ok(t) => t,
        Err(e) if e.kind() == ErrorKind::NotFound => return Ok(Map::new()),
        Err(e) => return Err(err("ledger_unreadable", format!("{}: {e}", p.display()))),
    };
    match serde_json::from_str::<Value>(&text) {
        Ok(Value::Object(m)) => Ok(m),
        _ => Err(err(
            "ledger_unreadable",
            format!(
                "{} is not a JSON object: fix or move it away first",
                p.display()
            ),
        )),
    }
}

fn write_ledger(dest: &Path, name: &str, entry: Value, now: u64) -> Result<(), Outcome> {
    let mut all = read_ledger(dest)?;
    let mut history: Vec<Value> = all
        .get(name)
        .and_then(|v| v.get("history"))
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let mut entry = entry;
    if let Some(o) = entry.as_object_mut() {
        let mut line = o.clone();
        line.remove("history");
        history.push(Value::Object(line));
        let skip = history.len().saturating_sub(HISTORY_MAX);
        o.insert("history".into(), Value::Array(history.split_off(skip)));
    }
    all.insert(name.to_owned(), entry);
    let text = serde_json::to_string_pretty(&Value::Object(all))
        .map_err(|e| err("io", format!("ledger: {e}")))?;
    let tmp = stage(dest, "BINS", "ledger", text.as_bytes(), now, false)?;
    put(&tmp, &dest.join(LEDGER))
}

fn info_json(i: &BuildInfo) -> Value {
    json!({"version": i.version, "commit": i.commit, "dirty": i.dirty})
}

fn ledger_entry(
    req: &Install,
    candidate: &BuildInfo,
    tool: &ToolInfo,
    main: &Option<String>,
    action: &str,
    prev_sha: Value,
    base: &Outcome,
) -> Value {
    json!({
        "sha256": sha256_hex(&req.bytes),
        "version": candidate.version,
        "commit": candidate.commit,
        "target": tool.target,
        "main": main,
        "main_moved_at": req.main_moved_at,
        "installed_at": req.now,
        "by": req.by,
        "action": action,
        "prev_sha256": prev_sha,
        "allowed": base.facts.get("allowed").cloned().unwrap_or(Value::Null),
    })
}

/// Install `req.bytes` as `req.name` under `req.dest`. Nothing is written unless every check
/// passes.
pub fn install(git: &dyn Git, req: &Install) -> Outcome {
    if !ALLOWED.contains(&req.name.as_str()) {
        return refuse(
            "name_not_allowed",
            format!("{} is not one of {ALLOWED:?}", req.name),
        );
    }
    let sha = sha256_hex(&req.bytes);
    let tool = match tool_info_in_binary(&req.bytes) {
        Ok(t) => t,
        Err(e) => return refuse("tool_info", e).with("sha256", json!(sha)),
    };
    if tool.name != req.name {
        return refuse(
            "other_tool",
            format!("the file says it is {}, not {}", tool.name, req.name),
        )
        .with("sha256", json!(sha));
    }
    if !target_fits(&tool.target, &req.os, &req.arch) {
        return refuse(
            "other_target",
            format!("built for {}, this is {}-{}", tool.target, req.os, req.arch),
        )
        .with("sha256", json!(sha));
    }
    let candidate = match info_in_binary(&req.bytes) {
        Ok(i) => i,
        Err(e) => return refuse("candidate_unknown", e).with("sha256", json!(sha)),
    };
    if let Err(e) = fs::create_dir_all(&req.dest) {
        return err("io", format!("{}: {e}", req.dest.display()));
    }
    let _lock = match lock(&req.dest) {
        Ok(l) => l,
        Err(o) => return o,
    };
    let (swept, rescued) = match sweep_all(&req.dest, &req.name, req.now) {
        Ok(n) => n,
        Err(o) => return o,
    };
    if let Err(o) = read_ledger(&req.dest) {
        return o;
    }
    let target_path = tool_path(&req.dest, &req.name);
    let current = match read_slot(&target_path) {
        Ok(s) => s,
        Err(o) => return o,
    };
    if let Err(o) = read_slot(&prev_path(&req.dest, &req.name)) {
        return o;
    }
    let (cur_info, first) = match &current {
        Slot::Missing => (Current::Unknown, true),
        Slot::File(b) => {
            if let Ok(t) = tool_info_in_binary(b) {
                if t.name != req.name {
                    return refuse(
                        "current_other_tool",
                        format!("{} says it is {}", target_path.display(), t.name),
                    );
                }
            }
            (
                info_in_binary(b)
                    .map(Current::Known)
                    .unwrap_or(Current::Unknown),
                false,
            )
        }
    };
    let hreq = host_build::Request {
        candidate_sha256: Some(sha.clone()),
        allow_downgrade: req.allow_downgrade.clone(),
        // first install: there is nothing to take away
        allow_unknown_current: if first {
            Some(format!(
                "first install: nothing at {}",
                target_path.display()
            ))
        } else {
            req.allow_unknown_current.clone()
        },
        ..host_build::Request::for_host(candidate.clone(), cur_info.clone())
    };
    let rep = host_build::check(git, &hreq);
    // a reason given that the check did not need (on a first install the user's
    // --allow-unknown-current is replaced by "first install"): shown, as host-build does
    let unused: Vec<Value> = [
        ("allow_downgrade", &req.allow_downgrade),
        ("allow_unknown_current", &req.allow_unknown_current),
    ]
    .into_iter()
    .filter_map(|(flag, why)| {
        let why = why.as_ref()?;
        let used = rep.allowed.iter().any(|(f, w)| *f == flag && w == why);
        (!used).then(|| json!({"flag": flag, "why": why}))
    })
    .collect();
    let mut base = Outcome::new(rep.verdict, rep.code, rep.detail.clone())
        .with("name", json!(req.name))
        .with("path", json!(target_path.display().to_string()))
        .with("sha256", json!(sha))
        .with("candidate", info_json(&candidate))
        .with("target", json!(tool.target))
        .with("main_ref", json!(host_build::MAIN_REF))
        .with("main", json!(rep.main))
        .with("main_moved_at", json!(req.main_moved_at))
        .with(
            "judged",
            json!("against this clone's origin/main as last fetched, not the remote now"),
        )
        .with(
            "allowed",
            json!(rep
                .allowed
                .iter()
                .map(|(f, w)| json!({"flag": f, "why": w}))
                .collect::<Vec<_>>()),
        )
        .with("swept_tmp", json!(swept))
        .with("rescued_tmp", json!(rescued))
        .with("unused_allow", json!(unused));
    if let Current::Known(i) = &cur_info {
        base = base.with("current", info_json(i));
    }
    if rep.verdict != Verdict::Ok {
        return base;
    }
    // the same file again (a retry after ledger_stale, or a rebuild with equal bytes): no swap,
    // or .prev would become a copy of the tool and the real previous version would be lost
    // (§18 review M2); only the ledger is written
    if matches!(&current, Slot::File(b) if sha256_hex(b) == sha) {
        // the bytes are right; the mode may not be (33e28 N2)
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if let Err(e) = fs::set_permissions(&target_path, fs::Permissions::from_mode(0o755)) {
                return err("io", format!("{}: {e}", target_path.display()));
            }
        }
        let entry = ledger_entry(
            req,
            &candidate,
            &tool,
            &rep.main,
            "same",
            // .prev did not change: keep naming what binsync moved there, or a rollback to an
            // unmarked hand copy is refused after a re-run (33e28 incr L1)
            read_ledger(&req.dest)
                .ok()
                .and_then(|l| l.get(&req.name)?.get("prev_sha256").cloned())
                .unwrap_or(Value::Null),
            &base,
        );
        return match write_ledger(&req.dest, &req.name, entry, req.now) {
            Ok(()) => Outcome {
                code: "same",
                detail: format!("{} already is this file", target_path.display()),
                ..base
            },
            Err(o) => o,
        };
    }
    // publish: new file staged first, the old one copied to .prev, then the swap
    let staged = match stage(&req.dest, &req.name, "new", &req.bytes, req.now, true) {
        Ok(p) => p,
        Err(o) => return o,
    };
    let mut prev_sha = Value::Null;
    if let Slot::File(old) = &current {
        let p = match stage(&req.dest, &req.name, "prev", old, req.now, true) {
            Ok(p) => p,
            Err(o) => return o,
        };
        if let Err(o) = put(&p, &prev_path(&req.dest, &req.name)) {
            return o;
        }
        prev_sha = json!(sha256_hex(old));
    }
    if let Err(o) = put(&staged, &target_path) {
        return o;
    }
    let entry = ledger_entry(
        req,
        &candidate,
        &tool,
        &rep.main,
        "install",
        prev_sha.clone(),
        &base,
    );
    let base = base
        .with("prev_sha256", prev_sha)
        .with("installed", json!(true));
    match write_ledger(&req.dest, &req.name, entry, req.now) {
        Ok(()) => base,
        // the tool IS in place: rc 0 with its own code, never rc 3 "nothing done", or a
        // retry would install the same file again (§18 review L2)
        Err(o) => Outcome {
            code: "ledger_stale",
            detail: format!("installed; the ledger was not written: {}", o.detail),
            ..base
        },
    }
}

/// Swap the tool and its `.prev` (so a second rollback goes forward again).
pub fn rollback(
    dest: &Path,
    name: &str,
    by: &str,
    why: &str,
    os: &str,
    arch: &str,
    now: u64,
) -> Outcome {
    if !ALLOWED.contains(&name) {
        return refuse(
            "name_not_allowed",
            format!("{name} is not one of {ALLOWED:?}"),
        );
    }
    let _lock = match lock(dest) {
        Ok(l) => l,
        Err(o) => return o,
    };
    let rescued = match sweep_all(dest, name, now) {
        Ok((_, r)) => r,
        Err(o) => return o,
    };
    if let Err(o) = read_ledger(dest) {
        return o;
    }
    let (tp, pp) = (tool_path(dest, name), prev_path(dest, name));
    let prev = match read_slot(&pp) {
        Ok(Slot::File(b)) => b,
        Ok(Slot::Missing) => return refuse("no_prev", format!("{} is not there", pp.display())),
        Err(o) => return o,
    };
    let cur = match read_slot(&tp) {
        Ok(s) => s,
        Err(o) => return o,
    };
    match tool_info_in_binary(&prev) {
        Ok(t) if t.name == name && target_fits(&t.target, os, arch) => {}
        Ok(t) => {
            return refuse(
                "prev_not_this_tool",
                format!("{} is {} for {}", pp.display(), t.name, t.target),
            )
        }
        // an unmarked .prev (a hand copy from before build info) is taken back only when the
        // ledger says this very file is the one binsync moved there (33e28 L1: otherwise the
        // first rollback after the first install is always refused)
        Err(e) => {
            let moved = read_ledger(dest)
                .ok()
                .and_then(|l| l.get(name)?.get("prev_sha256")?.as_str().map(str::to_owned));
            if moved.as_deref() != Some(sha256_hex(&prev).as_str()) {
                return refuse(
                    "prev_not_this_tool",
                    format!(
                        "{e}; and the ledger does not name {} as the file it moved there",
                        pp.display()
                    ),
                );
            }
        }
    }
    let staged_prev = match stage(dest, name, "back", &prev, now, true) {
        Ok(p) => p,
        Err(o) => return o,
    };
    let mut cur_sha = Value::Null;
    if let Slot::File(c) = &cur {
        let p = match stage(dest, name, "fwd", c, now, true) {
            Ok(p) => p,
            Err(o) => return o,
        };
        if let Err(o) = put(&p, &pp) {
            return o;
        }
        cur_sha = json!(sha256_hex(c));
    }
    if let Err(o) = put(&staged_prev, &tp) {
        return o;
    }
    sync_dir(dest);
    let info = info_in_binary(&prev).ok();
    let sha = sha256_hex(&prev);
    let entry = json!({
        "sha256": sha,
        "version": info.as_ref().map(|i| i.version.clone()),
        "commit": info.as_ref().and_then(|i| i.commit.clone()),
        "installed_at": now,
        "by": by,
        "action": "rollback",
        "why": why,
        "prev_sha256": cur_sha,
    });
    let out = Outcome::new(
        Verdict::Ok,
        "ok",
        format!("{} is back to {sha}", tp.display()),
    )
    .with("name", json!(name))
    .with("sha256", json!(sha))
    .with("prev_sha256", cur_sha)
    .with("rescued_tmp", json!(rescued));
    match write_ledger(dest, name, entry, now) {
        Ok(()) => out,
        Err(o) => Outcome {
            code: "ledger_stale",
            detail: format!("rolled back; the ledger was not written: {}", o.detail),
            ..out
        },
    }
}

/// What is installed: per allowed tool, the file's hash and build, whether the ledger agrees,
/// and (when git can say) whether its commit is on the local origin/main. Reads only.
pub fn status(git: Option<&dyn Git>, dest: &Path) -> Outcome {
    let (ledger, ledger_state) = match read_ledger(dest) {
        Ok(m) => (m, "ok"),
        Err(_) => (Map::new(), "ledger_unreadable"),
    };
    let main = git.map(|g| g.resolve(host_build::MAIN_REF));
    let mut tools = Vec::new();
    for name in ALLOWED {
        let p = tool_path(dest, name);
        let mut t = Map::new();
        t.insert("name".into(), json!(name));
        t.insert("path".into(), json!(p.display().to_string()));
        let bytes = match read_slot(&p) {
            Ok(Slot::Missing) => {
                t.insert("state".into(), json!("missing"));
                tools.push(Value::Object(t));
                continue;
            }
            Ok(Slot::File(b)) => b,
            Err(o) => {
                t.insert("state".into(), json!(o.code));
                tools.push(Value::Object(t));
                continue;
            }
        };
        let sha = sha256_hex(&bytes);
        let led = ledger
            .get(*name)
            .and_then(|v| v.get("sha256"))
            .and_then(Value::as_str);
        t.insert(
            "state".into(),
            json!(match led {
                None => "no_ledger",
                Some(s) if s == sha => "ok",
                Some(_) => "ledger_stale",
            }),
        );
        t.insert("sha256".into(), json!(sha));
        let info = info_in_binary(&bytes).ok();
        t.insert(
            "build".into(),
            info.as_ref().map(info_json).unwrap_or(Value::Null),
        );
        let on_main = match (&main, info.as_ref().and_then(|i| i.commit.as_deref())) {
            (Some(Ok(m)), Some(c)) => match git.map(|g| g.is_ancestor(c, m)) {
                Some(Ok(true)) => json!(true),
                Some(Ok(false)) => json!(false),
                _ => json!("unknown"),
            },
            _ => json!("unknown"),
        };
        t.insert("on_main".into(), on_main);
        t.insert(
            "prev".into(),
            json!(matches!(
                read_slot(&prev_path(dest, name)),
                Ok(Slot::File(_))
            )),
        );
        let rescued_pre = format!("{name}.rescued-");
        let rescued: Vec<String> = fs::read_dir(dest)
            .map(|rd| {
                rd.flatten()
                    .map(|e| e.file_name().to_string_lossy().into_owned())
                    .filter(|n| n.starts_with(&rescued_pre))
                    .collect()
            })
            .unwrap_or_default();
        t.insert("rescued".into(), json!(rescued));
        tools.push(Value::Object(t));
    }
    Outcome::new(Verdict::Ok, "ok", "read only")
        .with("dest", json!(dest.display().to_string()))
        .with("ledger", json!(ledger_state))
        .with(
            "main",
            match &main {
                Some(Ok(m)) => json!(m),
                _ => Value::Null,
            },
        )
        .with("tools", Value::Array(tools))
}

/// Seconds since the epoch, for the ledger.
pub fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}
