//! B12 `aib-seatops rules expiry` as a command: exit codes, JSON, and who gets told.

use std::path::{Path, PathBuf};
use std::process::Command;

const BIN: &str = env!("CARGO_BIN_EXE_aib-seatops");
const R158: &str = "### 【暫時】§15.8 CI 先行【到期:2026-11-11 03:00 台北 / 解除條件:043dd「§15.8 解除」信 / 負責席:043dd】\n";
// 2026-11-11 03:00 Taipei
const DUE: i64 = 1_794_337_200;

fn rules(dir: &Path, text: &str) -> PathBuf {
    let p = dir.join("RULES.md");
    std::fs::write(&p, format!("# RULES\n\n## §1 一般\n{text}")).expect("write");
    p
}

fn run(args: &[&str], path: Option<&str>) -> (i32, String, String) {
    let mut c = Command::new(BIN);
    c.args(args);
    if let Some(p) = path {
        c.env("PATH", p);
    }
    let o = c.output().expect("run");
    (
        o.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&o.stdout).into_owned(),
        String::from_utf8_lossy(&o.stderr).into_owned(),
    )
}

#[test]
fn exit_codes_follow_the_worst_entry() {
    let t = tempfile::tempdir().expect("tmp");
    let f = rules(t.path(), R158);
    let f = f.to_str().expect("utf8");
    let before = (DUE - 60).to_string();
    let after = (DUE + 60).to_string();
    let (rc, out, _) = run(&["rules", "expiry", "--file", f, "--now", &before], None);
    assert_eq!(
        (rc, out.starts_with("OPEN    L4 到期 2026-11-11 03:00")),
        (0, true),
        "{out}"
    );
    let (rc, out, _) = run(&["rules", "expiry", "--file", f, "--now", &after], None);
    assert_eq!((rc, out.starts_with("EXPIRED L4")), (1, true), "{out}");
    let f2 = rules(t.path(), &format!("{R158}## 【暫時】§9 沒寫到期\n"));
    let (rc, out, _) = run(
        &[
            "rules",
            "expiry",
            "--file",
            f2.to_str().expect("utf8"),
            "--now",
            &after,
        ],
        None,
    );
    assert_eq!(rc, 2, "{out}");
    assert!(out.contains("MISSING L5 no_due"), "{out}");
}

#[test]
fn json_names_state_owner_and_due() {
    let t = tempfile::tempdir().expect("tmp");
    let f = rules(t.path(), R158);
    let after = (DUE + 1).to_string();
    let (rc, out, _) = run(
        &[
            "rules",
            "expiry",
            "--file",
            f.to_str().expect("utf8"),
            "--now",
            &after,
            "--json",
        ],
        None,
    );
    assert_eq!(rc, 1);
    let v: serde_json::Value = serde_json::from_str(out.trim()).expect("json");
    assert_eq!(v[0]["state"], "expired");
    assert_eq!(v[0]["owner"], "043dd");
    assert_eq!(v[0]["due"], "2026-11-11 03:00");
    assert_eq!(v[0]["line"], 4);
}

#[test]
fn usage_and_unreadable_files() {
    assert_eq!(run(&["rules", "expiry"], None).0, 2);
    assert_eq!(
        run(&["rules", "expiry", "--file", "x", "--now", "soon"], None).0,
        2
    );
    assert_eq!(
        run(&["rules", "expiry", "--file", "x", "--notify"], None).0,
        2
    );
    assert_eq!(run(&["rules", "list"], None).0, 2);
    let t = tempfile::tempdir().expect("tmp");
    let missing = t.path().join("nope.md");
    assert_eq!(
        run(
            &["rules", "expiry", "--file", missing.to_str().expect("utf8")],
            None
        )
        .0,
        3
    );
}

/// --notify: the owner and the captain each get one dm through `aib dm <cabin>/<seat>`; a dm
/// that does not come back delivered is exit 3.
#[cfg(unix)]
#[test]
fn notify_tells_the_owner_and_the_captain() {
    use std::os::unix::fs::PermissionsExt;
    let t = tempfile::tempdir().expect("tmp");
    let f = rules(t.path(), R158);
    let bin = t.path().join("bin");
    std::fs::create_dir(&bin).expect("mkdir");
    let log = t.path().join("dm.log");
    let fake = bin.join("aib");
    std::fs::write(
        &fake,
        format!("#!/bin/sh\nprintf '%s|%s|%s\\n' \"$1\" \"$2\" \"$3\" >> '{}'\necho \"RESULT stage=${{FAKE_STAGE:-delivered}} id=q_x\"\n", log.display()),
    )
    .expect("fake");
    std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).expect("chmod");
    let path = format!("{}:/usr/bin:/bin", bin.display());
    let after = (DUE + 60).to_string();
    let args = [
        "rules",
        "expiry",
        "--file",
        f.to_str().expect("utf8"),
        "--now",
        &after,
        "--notify",
        "--cabin",
        "9320",
    ];
    let (rc, out, err) = run(&args, Some(&path));
    assert_eq!(rc, 1, "{out}{err}");
    let sent = std::fs::read_to_string(&log).expect("log");
    let to: Vec<&str> = sent
        .lines()
        .map(|l| l.split('|').nth(1).unwrap_or(""))
        .collect();
    assert_eq!(to, ["9320/043dd", "9320/a63ab"], "{sent}");
    assert!(
        sent.lines()
            .all(|l| l.starts_with("dm|") && l.contains("EXPIRED L4")),
        "{sent}"
    );
    // not delivered ⇒ 3
    let mut c = Command::new(BIN);
    c.args(args).env("PATH", &path).env("FAKE_STAGE", "resolve");
    assert_eq!(c.output().expect("run").status.code(), Some(3));
    // nothing due ⇒ nobody is told
    std::fs::remove_file(&log).expect("rm");
    let before = (DUE - 60).to_string();
    let quiet = [
        "rules",
        "expiry",
        "--file",
        f.to_str().expect("utf8"),
        "--now",
        &before,
        "--notify",
        "--cabin",
        "9320",
    ];
    assert_eq!(run(&quiet, Some(&path)).0, 0);
    assert!(!log.exists());
}
