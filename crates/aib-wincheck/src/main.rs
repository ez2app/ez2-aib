//! `aib-wincheck [--pack <folder>] [--root <folder>] [--out <file>] [--no-ask]`: the Windows
//! hands-on check. See the crate's lib.rs. Double-clicked, it uses the folder it is in as the
//! pack, puts the tree back at `C:\aibw\ez2-aib` (where CI built it) and writes
//! `<home>\.ez2-aib\pending\WINCHECK-<machine>-<time>.txt`. `--root` is for trying it
//! elsewhere (a Mac): the test executables there must have been built at that root.

use aib_wincheck::{
    check_pack, failed_names, kept_env, parse_results, parse_sums, parse_tests, sha256_hex,
    step_args, suite_name, Counts, Report, TestExe, SCREEN_ASKS, STEPS, TREE, WINDOWS_ROOT,
};
use std::collections::BTreeMap;
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};
use std::time::Instant;

struct Args {
    pack: PathBuf,
    root: Option<PathBuf>,
    out: Option<PathBuf>,
    ask: bool,
}

fn args() -> Result<Args, String> {
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf))
        .ok_or("cannot tell where this program is")?;
    let mut a = Args {
        pack: exe_dir,
        root: cfg!(windows).then(|| PathBuf::from(WINDOWS_ROOT)),
        out: None,
        ask: true,
    };
    let mut it = std::env::args().skip(1);
    while let Some(w) = it.next() {
        match w.as_str() {
            "--pack" => a.pack = PathBuf::from(it.next().ok_or("--pack needs a folder")?),
            "--root" => a.root = Some(PathBuf::from(it.next().ok_or("--root needs a folder")?)),
            "--out" => a.out = Some(PathBuf::from(it.next().ok_or("--out needs a file")?)),
            "--no-ask" => a.ask = false,
            other => return Err(format!("unknown argument {other:?}")),
        }
    }
    Ok(a)
}

/// Every file under `dir` as `(name relative to dir with /, sha256)`, `SHA256SUMS` left out.
fn pack_files(dir: &Path) -> Result<Vec<(String, String)>, String> {
    fn walk(root: &Path, d: &Path, out: &mut Vec<(String, String)>) -> Result<(), String> {
        let mut entries: Vec<_> = std::fs::read_dir(d)
            .map_err(|e| format!("{}: {e}", d.display()))?
            .filter_map(Result::ok)
            .collect();
        entries.sort_by_key(std::fs::DirEntry::file_name);
        for e in entries {
            let p = e.path();
            let ft = e.file_type().map_err(|e| format!("{}: {e}", p.display()))?;
            if ft.is_dir() {
                walk(root, &p, out)?;
            } else if ft.is_file() {
                let rel = p
                    .strip_prefix(root)
                    .map_err(|e| e.to_string())?
                    .to_string_lossy()
                    .replace('\\', "/");
                if rel == "SHA256SUMS" {
                    continue;
                }
                let b = std::fs::read(&p).map_err(|e| format!("{}: {e}", p.display()))?;
                out.push((rel, sha256_hex(&b)));
            }
        }
        Ok(())
    }
    let mut v = Vec::new();
    walk(dir, dir, &mut v)?;
    Ok(v)
}

/// Run `exe` with `args`, a cleared environment with only the kept variables, and the
/// working folder `cwd`; stdout and stderr together, and the exit code.
fn run(exe: &Path, args: &[String], cwd: &Path) -> (Option<i32>, String, u128) {
    // cargo runs a test in its package's folder with CARGO_MANIFEST_DIR set to it; some
    // tests read it at run time
    let manifest = cwd.to_string_lossy().into_owned();
    let t = Instant::now();
    let o = Command::new(exe)
        .args(args)
        .current_dir(cwd)
        .env_clear()
        .envs(kept_env(std::env::vars()))
        .env("CARGO_MANIFEST_DIR", manifest)
        .stdin(Stdio::null())
        .output();
    let ms = t.elapsed().as_millis();
    match o {
        Ok(o) => {
            let mut s = String::from_utf8_lossy(&o.stdout).into_owned();
            s.push_str(&String::from_utf8_lossy(&o.stderr));
            (o.status.code(), s, ms)
        }
        Err(e) => (None, format!("cannot start: {e}"), ms),
    }
}

/// `rel` of the pack is still the file its sum names, read again right before it is run: the
/// folder may be one other accounts of the machine can write (c2f0b 16:19 L), so the check at
/// the start is not enough.
fn still_same(file: &Path, name: &str, sums: &BTreeMap<String, String>) -> bool {
    std::fs::read(file)
        .ok()
        .is_some_and(|b| sums.get(name).is_some_and(|w| *w == sha256_hex(&b)))
}

/// The marker that says a root folder is this program's own: a folder without it is never
/// emptied.
const MARK: &str = ".aib-wincheck";

/// Put the pack's tree at `root` (emptied first only when it is this program's own) and check
/// every file written against its sum. Returns the problems; empty = the tree is in place.
fn place_tree(pack: &Path, root: &Path, sums: &BTreeMap<String, String>) -> Vec<String> {
    if root.exists() {
        if !root.join(MARK).is_file() {
            return vec![format!(
                "ROOT-NOT-OURS {} is there and was not made by aib-wincheck; nothing is written",
                root.display()
            )];
        }
        if let Err(e) = std::fs::remove_dir_all(root) {
            return vec![format!("ROOT-CLEAR {}: {e}", root.display())];
        }
    }
    if let Err(e) = std::fs::create_dir_all(root).and_then(|()| std::fs::write(root.join(MARK), ""))
    {
        return vec![format!("ROOT-MAKE {}: {e}", root.display())];
    }
    let mut problems = Vec::new();
    for name in sums.keys() {
        let Some(rel) = name.strip_prefix(TREE) else {
            continue;
        };
        let to = root.join(rel);
        let copied = to
            .parent()
            .map_or(Ok(()), std::fs::create_dir_all)
            .and_then(|()| std::fs::copy(pack.join(name), &to));
        if let Err(e) = copied {
            problems.push(format!("ROOT-COPY {rel}: {e}"));
        } else if !still_same(&to, name, sums) {
            problems.push(format!("SUMS-CHANGED {rel} (differs once copied)"));
        }
    }
    // env!("CARGO_TARGET_TMPDIR") of integration tests
    if let Err(e) = std::fs::create_dir_all(root.join("target").join("tmp")) {
        problems.push(format!("ROOT-MAKE target/tmp: {e}"));
    }
    problems
}

/// y / n from the person; anything else asks again. `None` when there is no one to ask.
fn ask(q: &str) -> Option<bool> {
    let stdin = std::io::stdin();
    loop {
        print!("\n{q}\n  看到了按 y、沒看到按 n,再按 Enter:");
        let _ = std::io::stdout().flush();
        let mut l = String::new();
        if stdin.lock().read_line(&mut l).ok()? == 0 {
            return None;
        }
        match l.trim() {
            "y" | "Y" => return Some(true),
            "n" | "N" => return Some(false),
            _ => {}
        }
    }
}

fn home() -> PathBuf {
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

fn main() -> ExitCode {
    let a = match args() {
        Ok(a) => a,
        Err(e) => {
            eprintln!("aib-wincheck: {e}");
            return ExitCode::from(64);
        }
    };
    let mut r = Report::default();
    let machine = std::env::var("COMPUTERNAME")
        .or_else(|_| std::env::var("HOSTNAME"))
        .unwrap_or_else(|_| "unknown".into());
    let started = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    r.line(format!("WINCHECK-VERSION {}", env!("CARGO_PKG_VERSION")));
    r.line(format!(
        "HOST {}-{} {machine}",
        std::env::consts::OS,
        std::env::consts::ARCH
    ));
    r.line(format!("STARTED {started}"));
    r.line("EVIDENCE hands-on measurement, not signed evidence");
    match std::fs::read_to_string(a.pack.join("HEAD")) {
        Ok(h) => r.line(format!("HEAD {}", h.trim())),
        Err(e) => r.red(format!("HEAD-UNKNOWN {e}")),
    }

    // 1. the pack is what CI built, or nothing runs
    let sums = std::fs::read_to_string(a.pack.join("SHA256SUMS"))
        .map_err(|e| format!("SHA256SUMS: {e}"))
        .and_then(|t| parse_sums(&t));
    let files = pack_files(&a.pack);
    let trusted = match (&sums, &files) {
        (Ok(s), Ok(f)) => {
            let problems = check_pack(s, f);
            for p in &problems {
                r.red(p.clone());
            }
            if problems.is_empty() {
                r.line(format!("SUMS-OK {}", s.len()));
            }
            problems.is_empty()
        }
        (Err(e), _) | (_, Err(e)) => {
            r.red(format!("SUMS-BAD {e}"));
            false
        }
    };

    // the tree back where CI built it: a folder of this program's own, never a project
    // folder of the person's (c2f0b L1)
    let tests: Vec<TestExe> = match std::fs::read_to_string(a.pack.join(TREE).join("TESTS"))
        .map_err(|e| format!("TESTS: {e}"))
        .and_then(|t| parse_tests(&t))
    {
        Ok(t) => t,
        Err(e) => {
            r.red(format!("TESTS-BAD {e}"));
            Vec::new()
        }
    };
    let placed = match (&a.root, &sums, trusted) {
        (Some(root), Ok(s), true) => {
            let problems = place_tree(&a.pack, root, s);
            for p in &problems {
                r.red(p.clone());
            }
            if problems.is_empty() {
                r.line(format!("ROOT-OK {}", root.display()));
            }
            problems.is_empty()
        }
        (None, _, _) => {
            r.red("ROOT-NONE outside Windows give --root (where the tests were built)");
            false
        }
        _ => false,
    };

    if let (true, Some(root), Ok(sums)) = (placed, &a.root, &sums) {
        // 2. every test executable, as cargo test would run it
        let mut total = Counts::default();
        let mut suites = 0u32;
        let mut suite_red = false;
        for t in &tests {
            let file = t.exe.rsplit('/').next().unwrap_or(&t.exe);
            let suite = suite_name(file);
            print!("{suite} …");
            let _ = std::io::stdout().flush();
            let exe = root.join(&t.exe);
            if !still_same(&exe, &format!("{TREE}{}", t.exe), sums) {
                suite_red = true;
                r.red(format!(
                    "SUMS-CHANGED {} (changed after the check; not run)",
                    t.exe
                ));
                continue;
            }
            let (rc, out, ms) = run(&exe, &[], &root.join(&t.package));
            let c = parse_results(&out);
            println!(" {} 過 / {} 紅", c.passed, c.failed);
            suites += 1;
            total.add(c);
            r.line(format!(
                "SUITE {suite} rc={} passed={} failed={} ignored={} ms={ms}",
                rc.map_or("none".into(), |c| c.to_string()),
                c.passed,
                c.failed,
                c.ignored
            ));
            for n in failed_names(&out) {
                suite_red = true;
                r.red(format!("TEST-RED {suite} {n}"));
            }
            if rc != Some(0) && c.failed == 0 {
                suite_red = true;
                r.red(format!("SUITE-RED {suite} rc={rc:?} without a failed test"));
            }
        }
        r.line(format!(
            "TESTS-TOTAL suites={suites} passed={} failed={} ignored={}",
            total.passed, total.failed, total.ignored
        ));
        if tests.is_empty() {
            r.red("TESTS-NONE the pack has no test executables");
        } else if !suite_red {
            r.line("TEST-OK");
        }

        // 3. the named steps (c2f0b W-8), output kept whole
        for s in STEPS {
            let Some(t) = tests
                .iter()
                .find(|t| suite_name(t.exe.rsplit('/').next().unwrap_or(&t.exe)) == s.exe)
            else {
                r.red(format!("STEP-MISSING {} {}", s.name, s.exe));
                continue;
            };
            let exe = root.join(&t.exe);
            if !still_same(&exe, &format!("{TREE}{}", t.exe), sums) {
                r.red(format!(
                    "SUMS-CHANGED {} (changed after the check; step {} not run)",
                    t.exe, s.name
                ));
                continue;
            }
            r.line(format!("STEP-BEGIN {}", s.name));
            let (rc, out, ms) = run(&exe, &step_args(s), &root.join(&t.package));
            for l in out.lines() {
                r.line(format!("| {l}"));
            }
            let c = parse_results(&out);
            r.line(format!(
                "STEP-END {} rc={} passed={} failed={} ms={ms}",
                s.name,
                rc.map_or("none".into(), |c| c.to_string()),
                c.passed,
                c.failed
            ));
            if rc != Some(0) || c.passed == 0 {
                r.red(format!("STEP-RED {}", s.name));
            }
            if let Some(q) = s.ask {
                answer(&mut r, a.ask, s.name, q);
            }
        }
    }
    r.line("KNIVES-SKIPPED reason=no-toolchain (knives run in CI's rust-windows cell)");

    // 4. what only eyes can see
    for (i, q) in SCREEN_ASKS.iter().enumerate() {
        answer(&mut r, a.ask, &format!("SCREEN-{}", i + 1), q);
    }

    let text = r.finish();
    let out = a.out.unwrap_or_else(|| {
        home().join(".ez2-aib").join("pending").join(format!(
            "WINCHECK-{}-{started}.txt",
            machine.replace(|c: char| !c.is_ascii_alphanumeric() && c != '-', "_")
        ))
    });
    if let Some(d) = out.parent() {
        let _ = std::fs::create_dir_all(d);
    }
    match std::fs::write(&out, &text) {
        Ok(()) => println!("\n結果存好了:{}", out.display()),
        Err(e) => {
            eprintln!("aib-wincheck: {}: {e}", out.display());
            print!("{text}");
            return ExitCode::from(7);
        }
    }
    println!(
        "{}",
        if r.is_red() {
            "有紅的項目,結果已記下,不用重試。"
        } else {
            "全部通過。"
        }
    );
    if a.ask {
        println!("按 Enter 關閉視窗。");
        let mut l = String::new();
        let _ = std::io::stdin().lock().read_line(&mut l);
    }
    if r.is_red() {
        ExitCode::from(1)
    } else {
        ExitCode::SUCCESS
    }
}

/// Ask `q` and write `ASK <name> yes|no|unanswered`; no and unanswered are red.
fn answer(r: &mut Report, can_ask: bool, name: &str, q: &str) {
    match can_ask.then(|| ask(q)).flatten() {
        Some(true) => r.line(format!("ASK {name} yes")),
        Some(false) => r.red(format!("ASK {name} no")),
        None => r.red(format!("ASK {name} unanswered")),
    }
}
