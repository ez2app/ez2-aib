// SPDX-License-Identifier: Apache-2.0
//! aib-persona — persona evidence ledger and the tools-root pin.
//!
//!   aib-persona evidence add  --persona-code C --lane L --day YYYY-MM-DD --pointer P --by seat:X [--session S] [--note N]
//!   aib-persona evidence list --persona-code C [--all] [--recheck]
//!   aib-persona evidence void --persona-code C --id e<line>-<hex16> --by seat:X --why TEXT
//!   aib-persona groups --persona-code C
//!   aib-persona doctor [--show]
//!   aib-persona doctor --pin --repo DIR --ref REF
//!
//! Exit: 0 done; 2 usage or refused (bad pointer, void not allowed, nothing pinned); 3 the
//! ledger or the pinned tree is unreadable or broken.
//!
//! Environment: EZ2_PERSONA_CANDIDATE_ROOT (default ~/.ez2/persona-candidates);
//! EZ2_AIB_STATE_ROOT / EZ2_AIB_HOME (default ~/.ez2-aib) for the dm queue and the pin;
//! EZ2_PERSONA_RECEIPT_ROOTS (':'-separated) replaces the allowed receipt roots
//! (default ~/.ez2-aib, ~/ez2-aib, ~/ez2-aib-wt, ~/wt.noindex); AIB_PERSONA_GH names the gh
//! program. None of these is a defence: the ledger is not a security boundary (see lib.rs).

use aib_persona::pointer::{self, World};
use aib_persona::{
    by_ok, day_ok, groups, lane_ok, list_row, parse, persona_code_ok, pin, plan_void, Kind, Ledger,
    VoidPlan, EVIDENCE_V2, MAX_LEDGER_BYTES,
};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};
use std::time::{SystemTime, UNIX_EPOCH};

enum Fail {
    Usage(String),
    Broken(String),
}

type R<T> = Result<T, Fail>;

fn usage<T>(m: impl Into<String>) -> R<T> {
    Err(Fail::Usage(m.into()))
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn home() -> PathBuf {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

fn env_path(keys: &[&str]) -> Option<PathBuf> {
    keys.iter()
        .filter_map(std::env::var_os)
        .find(|v| !v.is_empty())
        .map(PathBuf::from)
}

fn aib_home() -> PathBuf {
    env_path(&["EZ2_AIB_STATE_ROOT", "EZ2_AIB_HOME"]).unwrap_or_else(|| home().join(".ez2-aib"))
}

fn candidate_root() -> PathBuf {
    env_path(&["EZ2_PERSONA_CANDIDATE_ROOT"])
        .unwrap_or_else(|| home().join(".ez2").join("persona-candidates"))
}

fn receipt_roots() -> Vec<PathBuf> {
    match std::env::var("EZ2_PERSONA_RECEIPT_ROOTS") {
        Ok(v) if !v.is_empty() => v
            .split(':')
            .filter(|s| !s.is_empty())
            .map(PathBuf::from)
            .collect(),
        _ => {
            let h = home();
            vec![
                aib_home(),
                h.join("ez2-aib"),
                h.join("ez2-aib-wt"),
                h.join("wt.noindex"),
            ]
        }
    }
}

/// A dm id is in `<aib home>/state/dm-queue/<seat>/<id>.json` or one level deeper (retired/…).
fn dm_exists(id: &str) -> bool {
    if !pointer::is_dm_id(id) {
        return false;
    }
    let q = aib_home().join("state").join("dm-queue");
    let name = format!("{id}.json");
    let Ok(rd) = std::fs::read_dir(&q) else {
        return false;
    };
    for d in rd.flatten() {
        let p = d.path();
        if p.join(&name).is_file() {
            return true;
        }
        if let Ok(sub) = std::fs::read_dir(&p) {
            if sub.flatten().any(|s| s.path().join(&name).is_file()) {
                return true;
            }
        }
    }
    false
}

fn pr_exists(repo: &str, n: u64) -> Result<bool, String> {
    let gh = std::env::var("AIB_PERSONA_GH").unwrap_or_else(|_| "gh".into());
    let out = Command::new(&gh)
        .args(["api", &format!("repos/{repo}/pulls/{n}"), "--jq", ".number"])
        .output()
        .map_err(|e| format!("{gh}: {e}"))?;
    if out.status.success() {
        return Ok(String::from_utf8_lossy(&out.stdout).trim() == n.to_string());
    }
    let err = String::from_utf8_lossy(&out.stderr);
    if err.contains("Not Found") || err.contains("HTTP 404") {
        Ok(false)
    } else {
        Err(err.trim().chars().take(200).collect())
    }
}

#[derive(Default)]
struct Args {
    words: Vec<String>,
    flags: BTreeMap<String, String>,
    bools: Vec<String>,
}

const BOOL_FLAGS: [&str; 4] = ["--all", "--recheck", "--pin", "--show"];

fn parse_args(argv: &[String]) -> R<Args> {
    let mut a = Args::default();
    let mut it = argv.iter();
    while let Some(k) = it.next() {
        if BOOL_FLAGS.contains(&k.as_str()) {
            a.bools.push(k.clone());
        } else if k.starts_with("--") {
            let Some(v) = it.next() else {
                return usage(format!("{k} needs a value"));
            };
            if a.flags.insert(k.clone(), v.clone()).is_some() {
                return usage(format!("{k} given twice"));
            }
        } else {
            a.words.push(k.clone());
        }
    }
    Ok(a)
}

impl Args {
    fn get(&self, k: &str) -> R<&str> {
        match self.flags.get(k) {
            Some(v) => Ok(v),
            None => usage(format!("{k} is required")),
        }
    }
    fn opt(&self, k: &str) -> Option<&str> {
        self.flags.get(k).map(String::as_str)
    }
    fn has(&self, k: &str) -> bool {
        self.bools.iter().any(|b| b == k)
    }
    /// Refuse flags this command does not take.
    fn only(&self, flags: &[&str], bools: &[&str]) -> R<()> {
        if let Some(k) = self.flags.keys().find(|k| !flags.contains(&k.as_str())) {
            return usage(format!("unknown argument {k}"));
        }
        if let Some(k) = self.bools.iter().find(|k| !bools.contains(&k.as_str())) {
            return usage(format!("unknown argument {k}"));
        }
        Ok(())
    }
}

fn ledger_path(code: &str) -> R<PathBuf> {
    if !persona_code_ok(code) {
        return usage(format!("bad --persona-code {code:?}"));
    }
    Ok(candidate_root().join(code).join("evidence.jsonl"))
}

/// The ledger is never written through a link (043dd #627 M3; the Python writer's
/// writeguard rule, captain's review L5-01): the file itself must not be a symlink or
/// anything but a regular file, and its folder, once resolved, must still be under the
/// candidate root. Not a defence against this account (same-user threat model): it keeps a
/// stray link from sending evidence lines into some other file.
fn refuse_links(path: &Path) -> R<()> {
    let broken = |m: String| Err(Fail::Broken(m));
    if let Ok(md) = std::fs::symlink_metadata(path) {
        if md.file_type().is_symlink() {
            return broken(format!("{}: the ledger is a link; refused", path.display()));
        }
        if !md.is_file() {
            return broken(format!("{}: not a regular file; refused", path.display()));
        }
    }
    let real = |p: &Path| {
        std::fs::canonicalize(p).map_err(|e| Fail::Broken(format!("{}: {e}", p.display())))
    };
    let dir = path
        .parent()
        .ok_or_else(|| Fail::Broken(format!("{}: no folder", path.display())))?;
    let (real_root, real_dir) = (real(&candidate_root())?, real(dir)?);
    if !real_dir.starts_with(&real_root) {
        return broken(format!(
            "{}: the folder resolves to {}, outside the candidate root {}; refused",
            path.display(),
            real_dir.display(),
            real_root.display()
        ));
    }
    Ok(())
}

/// Open the ledger for writing, take the exclusive lock, and parse what is there.
fn open_locked(path: &Path) -> R<(File, Ledger)> {
    if let Some(dir) = path.parent() {
        let mut b = std::fs::DirBuilder::new();
        b.recursive(true);
        #[cfg(unix)]
        std::os::unix::fs::DirBuilderExt::mode(&mut b, 0o700);
        b.create(dir)
            .map_err(|e| Fail::Broken(format!("{}: {e}", dir.display())))?;
    }
    refuse_links(path)?;
    let mut o = OpenOptions::new();
    o.read(true).append(true).create(true);
    #[cfg(unix)]
    std::os::unix::fs::OpenOptionsExt::mode(&mut o, 0o600);
    let mut f = o
        .open(path)
        .map_err(|e| Fail::Broken(format!("{}: {e}", path.display())))?;
    f.lock()
        .map_err(|e| Fail::Broken(format!("lock {}: {e}", path.display())))?;
    let mut bytes = vec![];
    f.seek(SeekFrom::Start(0))
        .and_then(|_| f.read_to_end(&mut bytes))
        .map_err(|e| Fail::Broken(format!("{}: {e}", path.display())))?;
    let led = parse(&bytes).map_err(|e| Fail::Broken(format!("{}: {e}", path.display())))?;
    if led.torn_tail {
        return Err(Fail::Broken(format!(
            "{}: last line incomplete; refuse to append",
            path.display()
        )));
    }
    Ok((f, led))
}

fn append_line(f: &mut File, path: &Path, v: &Value) -> R<String> {
    let line = serde_json::to_string(v).map_err(|e| Fail::Broken(e.to_string()))?;
    let size = f
        .metadata()
        .map_err(|e| Fail::Broken(format!("{}: {e}", path.display())))?
        .len() as usize;
    if size + line.len() + 1 > MAX_LEDGER_BYTES {
        return Err(Fail::Broken(format!(
            "{} would pass {MAX_LEDGER_BYTES} bytes",
            path.display()
        )));
    }
    f.write_all(format!("{line}\n").as_bytes())
        .and_then(|_| f.sync_all())
        .map_err(|e| Fail::Broken(format!("{}: {e}", path.display())))?;
    Ok(line)
}

fn read_shared(path: &Path) -> R<Ledger> {
    let mut f = match File::open(path) {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return Ok(Ledger {
                next_no: 1,
                ..Ledger::default()
            })
        }
        Err(e) => return Err(Fail::Broken(format!("{}: {e}", path.display()))),
    };
    f.lock_shared()
        .map_err(|e| Fail::Broken(format!("lock {}: {e}", path.display())))?;
    let mut bytes = vec![];
    f.read_to_end(&mut bytes)
        .map_err(|e| Fail::Broken(format!("{}: {e}", path.display())))?;
    parse(&bytes).map_err(|e| Fail::Broken(format!("{}: {e}", path.display())))
}

fn evidence_add(a: &Args) -> R<Value> {
    a.only(
        &[
            "--persona-code",
            "--lane",
            "--day",
            "--pointer",
            "--by",
            "--session",
            "--note",
        ],
        &[],
    )?;
    let code = a.get("--persona-code")?;
    let (lane, day, by) = (a.get("--lane")?, a.get("--day")?, a.get("--by")?);
    if !lane_ok(lane) {
        return usage(format!("bad --lane {lane:?}"));
    }
    if !day_ok(day) {
        return usage(format!("bad --day {day:?} (a real YYYY-MM-DD)"));
    }
    if !by_ok(by) {
        return usage(format!("bad --by {by:?} (seat:<code> or human:<name>)"));
    }
    let path = ledger_path(code)?;
    let (cwd, h, roots) = (
        std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")),
        home(),
        receipt_roots(),
    );
    let w = World {
        cwd: &cwd,
        home: &h,
        roots: &roots,
        dm_exists: &dm_exists,
        pr_exists: &pr_exists,
    };
    let ptr = a.get("--pointer")?.trim().to_string();
    let c = pointer::check(&ptr, &w).map_err(Fail::Usage)?;
    let at = now_ms();
    let mut v = json!({
        "schema": EVIDENCE_V2, "persona_code": code, "lane": lane, "day": day,
        "pointer": ptr, "pointer_kind": c.kind.name(), "by": by,
        "session": a.opt("--session"),
        "note": a.opt("--note").unwrap_or("").chars().take(200).collect::<String>(),
        "at_ms": at,
    });
    if let Some(r) = &c.resolved {
        v["resolved"] = json!(r);
        v["sha256"] = json!(c.sha256);
        v["checked_at_ms"] = json!(at);
    }
    if let Some(n) = c.line {
        v["line_no"] = json!(n);
    }
    let (mut f, led) = open_locked(&path)?;
    let line = append_line(&mut f, &path, &v)?;
    v["id"] = json!(aib_persona::line_id(led.next_no, line.as_bytes()));
    Ok(v)
}

fn evidence_void(a: &Args) -> R<Value> {
    a.only(&["--persona-code", "--id", "--by", "--why"], &[])?;
    let code = a.get("--persona-code")?;
    let path = ledger_path(code)?;
    let (id, by, why) = (a.get("--id")?, a.get("--by")?, a.get("--why")?);
    let (mut f, led) = open_locked(&path)?;
    match plan_void(&led, code, id, by, why, now_ms(), &dm_exists).map_err(Fail::Usage)? {
        VoidPlan::Already(vid) => Ok(json!({"void_of": id, "already": true, "void_id": vid})),
        VoidPlan::Write(mut v) => {
            let line = append_line(&mut f, &path, &v)?;
            v["id"] = json!(aib_persona::line_id(led.next_no, line.as_bytes()));
            v["already"] = json!(false);
            Ok(v)
        }
    }
}

fn recheck(row: &mut Value) {
    let (Some(p), Some(want)) = (
        row.get("resolved")
            .and_then(Value::as_str)
            .map(PathBuf::from),
        row.get("sha256")
            .and_then(Value::as_str)
            .map(str::to_string),
    ) else {
        return;
    };
    // a FIFO put where the receipt was would block the open (043dd #627 L1)
    let state = match std::fs::metadata(&p).and_then(|md| {
        if md.is_file() {
            File::open(&p)
        } else {
            Err(std::io::Error::other("not a regular file"))
        }
    }) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => "missing",
        Err(_) => "unreadable",
        Ok(f) => {
            let mut b = vec![];
            match f.take(pointer::MAX_RECEIPT_BYTES + 1).read_to_end(&mut b) {
                Ok(_) if aib_persona::sha256::hex(&b) == want => "same",
                Ok(_) => "changed",
                Err(_) => "unreadable",
            }
        }
    };
    row["recheck"] = json!(state);
}

fn evidence_list(a: &Args) -> R<Value> {
    a.only(&["--persona-code"], &["--all", "--recheck"])?;
    let code = a.get("--persona-code")?;
    let led = read_shared(&ledger_path(code)?)?;
    let voided = led.voided();
    let all = a.has("--all");
    let mut rows = vec![];
    for l in &led.lines {
        let hidden = l.kind == Kind::Void || voided.contains_key(&l.id);
        if hidden && !all {
            continue;
        }
        let mut row = list_row(l, &voided);
        if a.has("--recheck") {
            recheck(&mut row);
        }
        rows.push(row);
    }
    Ok(json!({"persona_code": code, "rows": rows, "torn_tail": led.torn_tail}))
}

fn groups_cmd(a: &Args) -> R<Value> {
    a.only(&["--persona-code"], &[])?;
    let code = a.get("--persona-code")?;
    let led = read_shared(&ledger_path(code)?)?;
    let mut g = groups(&led);
    g["persona_code"] = json!(code);
    Ok(g)
}

fn doctor(a: &Args) -> R<Value> {
    a.only(&["--repo", "--ref"], &["--pin", "--show"])?;
    let dir = aib_home().join("persona-tools");
    if a.has("--pin") {
        let (repo, r) = (a.get("--repo")?, a.get("--ref")?);
        let doc = pin::pin(Path::new(repo), r, &dir, now_ms()).map_err(Fail::Broken)?;
        return Ok(json!({"ok": true, "pinned": doc}));
    }
    match pin::show(&dir) {
        Ok((doc, root)) => Ok(json!({"ok": true, "root": root.to_string_lossy(), "pinned": doc})),
        Err(e) if !dir.join(pin::PINNED).exists() => usage(e),
        Err(e) => Err(Fail::Broken(e)),
    }
}

fn run(argv: &[String]) -> R<Value> {
    let a = parse_args(argv)?;
    match a
        .words
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>()
        .as_slice()
    {
        ["evidence", "add"] => evidence_add(&a),
        ["evidence", "list"] => evidence_list(&a),
        ["evidence", "void"] => evidence_void(&a),
        ["groups"] => groups_cmd(&a),
        ["doctor"] => doctor(&a),
        w => usage(format!(
            "unknown command {w:?} (evidence add|list|void, groups, doctor)"
        )),
    }
}

/// Printed for -h / --help (043dd #627 L3: `aib persona evidence --help` reached the Rust
/// program and was read as a flag that needs a value).
const HELP: &str = "\
aib-persona evidence add  --persona-code C --lane L --day YYYY-MM-DD --pointer P --by seat:X [--session S] [--note N]
aib-persona evidence list --persona-code C [--all] [--recheck]
aib-persona evidence void --persona-code C --id e<line>-<hex16> --by seat:X --why TEXT
aib-persona groups --persona-code C
aib-persona doctor [--show]
aib-persona doctor --pin --repo DIR --ref REF

pointers: a commit (7-40 hex), ev_..., q_<16 hex> (in the dm queue), pr:<owner>/<repo>#<n>,
a receipt (.json/.jsonl/.md/.txt) or path:line under an allowed root; a file in this folder
is name.md or name.ext:N.
exit: 0 done; 2 usage or refused; 3 the ledger or the pinned tree is unreadable or broken.
";

fn main() -> ExitCode {
    // an argument that is not UTF-8 is a usage error (rc 2), not a panic (043dd #627 L3)
    let argv: Vec<String> = match std::env::args_os()
        .skip(1)
        .map(|a| a.into_string())
        .collect::<Result<_, _>>()
    {
        Ok(v) => v,
        Err(a) => {
            eprintln!("aib-persona: argument {a:?} is not UTF-8");
            return ExitCode::from(2);
        }
    };
    if argv.iter().any(|a| a == "-h" || a == "--help") {
        print!("{HELP}");
        return ExitCode::SUCCESS;
    }
    match run(&argv) {
        Ok(v) => {
            let s = serde_json::to_string_pretty(&v).unwrap_or_else(|_| v.to_string());
            println!("{s}");
            ExitCode::SUCCESS
        }
        Err(Fail::Usage(m)) => {
            eprintln!("aib-persona: {m}");
            ExitCode::from(2)
        }
        Err(Fail::Broken(m)) => {
            eprintln!("aib-persona: {m}");
            ExitCode::from(3)
        }
    }
}
