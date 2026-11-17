// SPDX-License-Identifier: Apache-2.0
//! aib-seathook — seat-side checks (see lib.rs).
//!
//!   aib-seathook pre-push [REMOTE [URL]]      git's pre-push hook; refs on standard input
//!   aib-seathook install-pre-push [--repo DIR] writes the clone's pre-push hook (a two-line shim
//!                                              that runs this binary by its absolute path);
//!                                              a pre-push hook that is not ours is left alone (exit 2)
//!
//! Config: `~/.ez2-aib/seathook.toml`, key `pre_push = "warn" | "block"` (default warn).
//! Every run appends one line per check to `~/.ez2-aib/seathook/events.jsonl`.
//! Env: AIB_SEATHOOK_HOME (instead of ~/.ez2-aib, for tests), AIB_LEAK_BIN (the leak program).
//! Exit: 0 pass or warn mode; 1 block mode and a check failed; 2 usage / install refused.

use aib_seathook::{
    deleted_lines, exit_code, freeze_verdict, mode_from_config, parse_pushed, report_line,
    workflow_verdict, Mode, Verdict,
};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

const SHIM_MARK: &str = "# aib-seathook pre-push shim";

fn state_home() -> PathBuf {
    if let Some(h) = std::env::var_os("AIB_SEATHOOK_HOME") {
        return PathBuf::from(h);
    }
    let home = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_default();
    home.join(".ez2-aib")
}

fn git(dir: &Path, args: &[&str]) -> Result<String, String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(args)
        .stdin(Stdio::null())
        .output()
        .map_err(|e| format!("git not run: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "git {} failed: {}",
            args.join(" "),
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

fn leak_program() -> PathBuf {
    if let Some(p) = std::env::var_os("AIB_LEAK_BIN") {
        return PathBuf::from(p);
    }
    let name = format!("aib-public-leak{}", std::env::consts::EXE_SUFFIX);
    if let Some(dir) = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf))
    {
        let sib = dir.join(&name);
        if sib.is_file() {
            return sib;
        }
    }
    PathBuf::from(name)
}

fn leak_check(top: &Path, stdin: &str, remote: Option<&str>) -> Verdict {
    let prog = leak_program();
    let mut cmd = Command::new(&prog);
    cmd.args(["--local-sources", "--pre-push", "--repo"])
        .arg(top);
    if let Some(r) = remote {
        cmd.args(["--remote", r]);
    }
    let child = cmd
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn();
    let mut child = match child {
        Ok(c) => c,
        Err(e) => return Verdict::Error(format!("{} not run: {e}", prog.display())),
    };
    if let Some(mut w) = child.stdin.take() {
        let _ = w.write_all(stdin.as_bytes());
    }
    match child.wait_with_output() {
        Err(e) => Verdict::Error(format!("{} did not finish: {e}", prog.display())),
        Ok(o) => match o.status.code() {
            Some(0) => Verdict::Pass,
            Some(1) => Verdict::Fail(format!(
                "leak scan found something: {}",
                last_line(&o.stdout, &o.stderr)
            )),
            c => Verdict::Error(format!(
                "leak scan exit {c:?}: {}",
                last_line(&o.stdout, &o.stderr)
            )),
        },
    }
}

fn last_line(out: &[u8], err: &[u8]) -> String {
    let all = format!(
        "{}\n{}",
        String::from_utf8_lossy(out),
        String::from_utf8_lossy(err)
    );
    all.lines()
        .map(str::trim)
        .rfind(|l| !l.is_empty())
        .unwrap_or("-")
        .to_owned()
}

fn fmt_check(top: &Path) -> Verdict {
    if !top.join("Cargo.toml").is_file() {
        return Verdict::Pass;
    }
    match Command::new("cargo")
        .args(["fmt", "--all", "--", "--check"])
        .current_dir(top)
        .stdin(Stdio::null())
        .output()
    {
        Err(e) => Verdict::Error(format!("cargo not run: {e}")),
        Ok(o) if o.status.success() => Verdict::Pass,
        Ok(o) if o.status.code() == Some(1) => {
            let files: Vec<String> = String::from_utf8_lossy(&o.stdout)
                .lines()
                .filter_map(|l| l.strip_prefix("Diff in "))
                .map(|l| l.split(':').next().unwrap_or(l).trim().to_owned())
                .collect();
            Verdict::Fail(format!(
                "cargo fmt --check: {} place(s) not formatted",
                files.len().max(1)
            ))
        }
        Ok(o) => Verdict::Error(format!("cargo fmt exit {:?}", o.status.code())),
    }
}

fn workflow_checks(top: &Path, stdin: &str) -> Vec<Verdict> {
    let pushed = parse_pushed(stdin);
    let base = match git(top, &["rev-parse", "--verify", "--quiet", "origin/main"]) {
        Ok(b) => b.trim().to_owned(),
        Err(e) => return vec![Verdict::Error(format!("no origin/main: {e}"))],
    };
    pushed
        .iter()
        .map(|p| {
            if p.remote_ref == "refs/heads/main" || p.local_ref.starts_with("refs/tags/") {
                return Verdict::Pass;
            }
            let range = format!("{base}...{}", p.local_sha);
            match git(top, &["diff", "--numstat", &range, "--", ".github/"]) {
                Ok(ns) => workflow_verdict(&p.remote_ref, deleted_lines(&ns)),
                Err(e) => Verdict::Error(e),
            }
        })
        .collect()
}

fn append_ledger(home: &Path, mode: Mode, verdicts: &[(&str, Verdict)]) {
    let dir = home.join("seathook");
    if std::fs::create_dir_all(&dir).is_err() {
        return;
    }
    let at_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_millis() as u64);
    let seat = std::env::var("AIB_SEAT_CODE").unwrap_or_default();
    let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join("events.jsonl"))
    else {
        return;
    };
    for (check, v) in verdicts {
        let line = serde_json::json!({
            "fmt": "aib-ev/1", "at_ms": at_ms, "seat": seat, "kind": "pre-push",
            "mode": mode.to_string(), "check": check, "verdict": v.word(), "why": v.why(),
        });
        let _ = writeln!(f, "{line}");
    }
}

fn pre_push(args: &[String]) -> i32 {
    let mut stdin = String::new();
    let _ = std::io::stdin().read_to_string(&mut stdin);
    let home = state_home();
    let mode =
        mode_from_config(&std::fs::read_to_string(home.join("seathook.toml")).unwrap_or_default());
    let top = match git(Path::new("."), &["rev-parse", "--show-toplevel"]) {
        Ok(t) => PathBuf::from(t.trim()),
        Err(e) => {
            eprintln!("{}", report_line(mode, "repo", &Verdict::Error(e)));
            return 0;
        }
    };
    let freeze = std::fs::read_to_string(home.join("pending").join("PUSH-FREEZE")).ok();
    let mut verdicts: Vec<(&str, Verdict)> = vec![
        ("freeze", freeze_verdict(freeze.as_deref())),
        (
            "leak",
            leak_check(&top, &stdin, args.first().map(String::as_str)),
        ),
        ("fmt", fmt_check(&top)),
    ];
    for v in workflow_checks(&top, &stdin) {
        verdicts.push(("workflow", v));
    }
    for (check, v) in &verdicts {
        eprintln!("{}", report_line(mode, check, v));
    }
    append_ledger(&home, mode, &verdicts);
    let rc = exit_code(mode, &verdicts);
    if rc != 0 {
        eprintln!("seathook pre-push: push stopped (block mode); fix the lines above or ask the coordinator");
    }
    rc
}

fn install(args: &[String]) -> i32 {
    let repo = match args {
        [flag, dir] if flag == "--repo" => PathBuf::from(dir),
        [] => PathBuf::from("."),
        _ => {
            eprintln!("usage: aib-seathook install-pre-push [--repo DIR]");
            return 2;
        }
    };
    let hooks = match git(&repo, &["rev-parse", "--git-path", "hooks"]) {
        Ok(h) => {
            let h = PathBuf::from(h.trim());
            if h.is_absolute() {
                h
            } else {
                repo.join(h)
            }
        }
        Err(e) => {
            eprintln!("{e}");
            return 2;
        }
    };
    let target = hooks.join("pre-push");
    if let Ok(old) = std::fs::read_to_string(&target) {
        if !old.contains(SHIM_MARK) {
            eprintln!("{} is not ours; left alone", target.display());
            return 2;
        }
    }
    let exe = match std::env::current_exe() {
        Ok(p) => p,
        Err(e) => {
            eprintln!("cannot find this program: {e}");
            return 2;
        }
    };
    // git for Windows runs hooks with its own sh too; forward slashes work there
    let exe = exe.to_string_lossy().replace('\\', "/");
    let shim = format!(
        "#!/bin/sh\n{SHIM_MARK} (warn / block is in ~/.ez2-aib/seathook.toml)\n[ -x '{exe}' ] || {{ echo \"aib-seathook missing: {exe}\" >&2; exit 0; }}\nexec '{exe}' pre-push \"$@\"\n"
    );
    if let Err(e) = std::fs::create_dir_all(&hooks).and_then(|()| std::fs::write(&target, shim)) {
        eprintln!("write {}: {e}", target.display());
        return 2;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&target, std::fs::Permissions::from_mode(0o755));
    }
    println!("installed {}", target.display());
    0
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let rc = match args.split_first() {
        Some((cmd, rest)) if cmd == "pre-push" => pre_push(rest),
        Some((cmd, rest)) if cmd == "install-pre-push" => install(rest),
        _ => {
            eprintln!(
                "usage: aib-seathook pre-push [REMOTE [URL]] | install-pre-push [--repo DIR]"
            );
            2
        }
    };
    std::process::exit(rc);
}
