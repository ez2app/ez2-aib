//! `aib-wincheck [--pack <folder>] [--out <file>] [--no-ask]`: the Windows hands-on check.
//! See the crate's lib.rs. Double-clicked, it uses the folder it is in as the pack and writes
//! `<home>\.ez2-aib\pending\WINCHECK-<machine>-<time>.txt`.

use aib_wincheck::{
    check_pack, failed_names, kept_env, parse_results, parse_sums, sha256_hex, step_args,
    suite_name, Counts, Report, SCREEN_ASKS, STEPS,
};
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};
use std::time::Instant;

struct Args {
    pack: PathBuf,
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
        out: None,
        ask: true,
    };
    let mut it = std::env::args().skip(1);
    while let Some(w) = it.next() {
        match w.as_str() {
            "--pack" => a.pack = PathBuf::from(it.next().ok_or("--pack needs a folder")?),
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
    let t = Instant::now();
    let o = Command::new(exe)
        .args(args)
        .current_dir(cwd)
        .env_clear()
        .envs(kept_env(std::env::vars()))
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

    // a folder of its own to run in, never a project folder of the person's (c2f0b L1)
    let work = home().join(".ez2-aib").join("wincheck").join("work");
    if let Err(e) = std::fs::create_dir_all(&work) {
        r.red(format!("WORK-DIR {}: {e}", work.display()));
    }

    if trusted {
        // 2. every test executable, as cargo test would run it
        let tests: Vec<String> = files
            .as_ref()
            .map(|f| {
                f.iter()
                    .map(|(n, _)| n.clone())
                    .filter(|n| n.starts_with("tests/") && n.ends_with(".exe"))
                    .collect()
            })
            .unwrap_or_default();
        let mut total = Counts::default();
        let mut suites = 0u32;
        let mut suite_red = false;
        for t in &tests {
            let file = t.trim_start_matches("tests/");
            print!("{} …", suite_name(file));
            let _ = std::io::stdout().flush();
            let (rc, out, ms) = run(&a.pack.join(t), &[], &work);
            let c = parse_results(&out);
            println!(" {} 過 / {} 紅", c.passed, c.failed);
            suites += 1;
            total.add(c);
            r.line(format!(
                "SUITE {} rc={} passed={} failed={} ignored={} ms={ms}",
                suite_name(file),
                rc.map_or("none".into(), |c| c.to_string()),
                c.passed,
                c.failed,
                c.ignored
            ));
            for n in failed_names(&out) {
                suite_red = true;
                r.red(format!("TEST-RED {} {n}", suite_name(file)));
            }
            if rc != Some(0) && c.failed == 0 {
                suite_red = true;
                r.red(format!(
                    "SUITE-RED {} rc={rc:?} without a failed test",
                    suite_name(file)
                ));
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
            let exe = files.as_ref().ok().and_then(|f| {
                f.iter()
                    .map(|(n, _)| n.as_str())
                    .find(|n| {
                        n.strip_prefix("tests/")
                            .is_some_and(|x| suite_name(x) == s.exe)
                    })
                    .map(str::to_owned)
            });
            let Some(exe) = exe else {
                r.red(format!("STEP-MISSING {} {}", s.name, s.exe));
                continue;
            };
            r.line(format!("STEP-BEGIN {}", s.name));
            let (rc, out, ms) = run(&a.pack.join(&exe), &step_args(s), &work);
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
