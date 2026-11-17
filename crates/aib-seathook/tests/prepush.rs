// SPDX-License-Identifier: Apache-2.0
//! The built binary in a throwaway repository: block mode stops on PUSH-FREEZE, warn mode never
//! stops, a check that cannot run (no leak program, no origin/main) is `error` and never stops,
//! and install-pre-push leaves a hook that is not ours alone. Same on macOS and Windows.

use std::path::Path;
use std::process::{Command, Stdio};

fn git(dir: &Path, args: &[&str]) {
    let ok = Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(args)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .expect("git")
        .success();
    assert!(ok, "git {args:?}");
}

fn repo() -> tempfile::TempDir {
    let d = tempfile::tempdir().expect("tempdir");
    git(d.path(), &["init", "-q"]);
    git(
        d.path(),
        &[
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        ],
    );
    d
}

fn run(repo: &Path, home: &Path, stdin: &str) -> (i32, String) {
    use std::io::Write;
    let mut c = Command::new(env!("CARGO_BIN_EXE_aib-seathook"))
        .arg("pre-push")
        .current_dir(repo)
        .env("AIB_SEATHOOK_HOME", home)
        .env("AIB_LEAK_BIN", home.join("no-such-leak-program"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn");
    c.stdin
        .take()
        .expect("stdin")
        .write_all(stdin.as_bytes())
        .expect("write");
    let o = c.wait_with_output().expect("wait");
    (
        o.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&o.stderr).into_owned(),
    )
}

#[test]
fn block_stops_on_freeze_warn_never_stops_and_errors_never_stop() {
    let r = repo();
    let home = tempfile::tempdir().expect("home");
    let pending = home.path().join("pending");
    std::fs::create_dir_all(&pending).expect("mkdir");

    // nothing frozen, block mode: the leak program is missing and there is no origin/main ⇒ errors only ⇒ 0
    std::fs::write(home.path().join("seathook.toml"), "pre_push = \"block\"\n").expect("cfg");
    let (rc, err) = run(r.path(), home.path(), "");
    assert_eq!(rc, 0, "{err}");
    assert!(err.contains("seathook pre-push block freeze pass"), "{err}");
    assert!(err.contains("seathook pre-push block leak error"), "{err}");

    // frozen, block mode ⇒ 1, the reason printed
    std::fs::write(pending.join("PUSH-FREEZE"), "merge window\n").expect("freeze");
    let (rc, err) = run(r.path(), home.path(), "");
    assert_eq!(rc, 1, "{err}");
    assert!(
        err.contains("freeze fail: PUSH-FREEZE: merge window"),
        "{err}"
    );

    // frozen, warn mode (default: no config) ⇒ 0, still printed
    std::fs::remove_file(home.path().join("seathook.toml")).expect("rm cfg");
    let (rc, err) = run(r.path(), home.path(), "");
    assert_eq!(rc, 0, "{err}");
    assert!(err.contains("seathook pre-push warn freeze fail"), "{err}");

    // every check is in the ledger
    let ledger =
        std::fs::read_to_string(home.path().join("seathook").join("events.jsonl")).expect("ledger");
    assert!(ledger.lines().count() >= 9, "{ledger}");
    assert!(ledger.contains("\"check\":\"freeze\""), "{ledger}");
}

#[test]
fn install_leaves_a_foreign_hook_alone_and_rewrites_its_own() {
    let r = repo();
    let hooks = r.path().join(".git").join("hooks");
    std::fs::create_dir_all(&hooks).expect("hooks");
    let exe = env!("CARGO_BIN_EXE_aib-seathook");
    let install = || {
        Command::new(exe)
            .args(["install-pre-push", "--repo"])
            .arg(r.path())
            .output()
            .expect("install")
            .status
            .code()
    };
    std::fs::write(hooks.join("pre-push"), "#!/bin/sh\nexit 0\n").expect("foreign");
    assert_eq!(install(), Some(2));
    assert_eq!(
        std::fs::read_to_string(hooks.join("pre-push")).expect("read"),
        "#!/bin/sh\nexit 0\n"
    );
    std::fs::remove_file(hooks.join("pre-push")).expect("rm");
    assert_eq!(install(), Some(0));
    assert_eq!(install(), Some(0), "our own shim is rewritten");
    let shim = std::fs::read_to_string(hooks.join("pre-push")).expect("read");
    assert!(
        shim.contains("aib-seathook pre-push shim") && shim.contains("pre-push \"$@\""),
        "{shim}"
    );
}
