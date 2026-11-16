//! crates/aib-knife/run-knives.sh run for real on a folder made here (S66-loop,
//! DESIGN-knives-loop-ebb36-1117 §刀): which scripts of a job run, which are skipped by OS,
//! that one red script does not stop the rest, that a bad header, an empty job and a script
//! over its limit are red, and that `env=` reaches only its own script. Unix only: the
//! watchdog kills a process group, which Git Bash on Windows does not have the same way.
#![cfg(unix)]

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU32, Ordering};

static NEXT: AtomicU32 = AtomicU32::new(0);

fn runner() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("crates/aib-knife/run-knives.sh")
}

/// A repo root with `crates/<c>/knives/<f>` for each `(path, text)`; one folder per call.
fn repo(files: &[(&str, &str)]) -> PathBuf {
    let n = NEXT.fetch_add(1, Ordering::Relaxed);
    let r = std::env::temp_dir().join(format!("run-knives-{}-{n}", std::process::id()));
    let _ = std::fs::remove_dir_all(&r);
    for (p, t) in files {
        let f = r.join(p);
        std::fs::create_dir_all(f.parent().unwrap()).unwrap();
        std::fs::write(f, t).unwrap();
    }
    r
}

fn run(root: &Path, job: &str) -> (i32, String) {
    let o = Command::new("bash")
        .arg(runner())
        .arg(job)
        .current_dir(root)
        .env("AIB_KNIVES_SECS_PER_MIN", "1")
        .env_remove("AIB_KNIVES_NO_SE")
        .output()
        .unwrap();
    let _ = std::fs::remove_dir_all(root);
    (
        o.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&o.stdout).into_owned(),
    )
}

fn here_os() -> &'static str {
    if cfg!(target_os = "macos") {
        "macos"
    } else {
        "linux"
    }
}

#[test]
fn the_scripts_of_the_job_run_and_the_other_os_is_skipped() {
    let other = if here_os() == "macos" {
        "windows"
    } else {
        "macos"
    };
    let r = repo(&[
        ("crates/a/knives/k1.sh", "#!/usr/bin/env bash\n# aib-knives: os=any\necho ran-k1\n"),
        (
            "crates/b/knives/k2.sh",
            "#!/usr/bin/env bash\n# aib-knives: job=cloud-knives\n# aib-knives: os=any\necho ran-k2\n",
        ),
        (
            "crates/c/knives/k3.sh",
            &format!("#!/usr/bin/env bash\n# aib-knives: os={other}\necho ran-k3\n"),
        ),
    ]);
    let (rc, out) = run(&r, "rust");
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("ran-k1"), "{out}");
    assert!(!out.contains("ran-k2"), "another job's script ran: {out}");
    assert!(!out.contains("ran-k3"), "another OS's script ran: {out}");
    assert!(
        out.contains(&format!(
            "KNIVES-SCRIPT crates/c/knives/k3.sh: SKIPPED why=not_this_os os={other}"
        )),
        "{out}"
    );
    assert!(out.contains("KNIVES-OK job=rust scripts=2"), "{out}");
}

#[test]
fn one_red_script_does_not_stop_the_others_and_the_run_is_red() {
    let r = repo(&[
        (
            "crates/a/knives/k1.sh",
            "#!/usr/bin/env bash\n# aib-knives: os=any\nexit 3\n",
        ),
        (
            "crates/b/knives/k2.sh",
            "#!/usr/bin/env bash\n# aib-knives: os=any\necho ran-k2\n",
        ),
    ]);
    let (rc, out) = run(&r, "rust");
    assert_eq!(rc, 1, "{out}");
    assert!(
        out.contains("KNIVES-SCRIPT crates/a/knives/k1.sh: rc=3"),
        "{out}"
    );
    assert!(out.contains("ran-k2"), "{out}");
    assert!(out.contains("KNIVES-RED 1: crates/a/knives/k1.sh"), "{out}");
}

#[test]
fn a_bad_header_and_a_job_with_no_script_are_red() {
    let r = repo(&[(
        "crates/a/knives/k1.sh",
        "#!/usr/bin/env bash\n# aib-knives: os=linux\necho ran-k1\n",
    )]);
    let (rc, out) = run(&r, "rust");
    assert_eq!(rc, 1, "{out}");
    assert!(
        out.contains("KNIVES-HEADER-BAD crates/a/knives/k1.sh: os=linux"),
        "{out}"
    );
    assert!(!out.contains("ran-k1"), "{out}");

    let r = repo(&[(
        "crates/a/knives/k1.sh",
        "#!/usr/bin/env bash\n# aib-knives: os=any\n",
    )]);
    let (rc, out) = run(&r, "nobody");
    assert_eq!(rc, 1, "{out}");
    assert!(out.contains("KNIVES-NONE job=nobody"), "{out}");
}

#[test]
fn a_script_over_its_limit_is_killed_with_its_children_and_red() {
    // timeout=1m is one second here (AIB_KNIVES_SECS_PER_MIN=1)
    let r = repo(&[(
        "crates/a/knives/k1.sh",
        "#!/usr/bin/env bash\n# aib-knives: os=any\n# aib-knives: timeout=1m\nsleep 30 &\necho $! > child.pid\nwait\n",
    )]);
    let pid_file = r.join("child.pid");
    let o = Command::new("bash")
        .arg(runner())
        .arg("rust")
        .current_dir(&r)
        .env("AIB_KNIVES_SECS_PER_MIN", "1")
        .output()
        .unwrap();
    let out = String::from_utf8_lossy(&o.stdout).into_owned();
    let child = std::fs::read_to_string(&pid_file).unwrap_or_default();
    let _ = std::fs::remove_dir_all(&r);
    assert_eq!(o.status.code(), Some(1), "{out}");
    assert!(
        out.contains("KNIVES-SCRIPT crates/a/knives/k1.sh: not_run why_not=timeout"),
        "{out}"
    );
    let child = child.trim();
    assert!(!child.is_empty(), "the script did not start: {out}");
    // the script's own child was in its process group, so it is gone too (given two
    // seconds: a killed orphan may still be a zombie for a moment, and `kill -0` sees one)
    let alive = Command::new("bash")
        .arg("-c")
        .arg(format!(
            "for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 {child} 2>/dev/null || exit 1; sleep 0.2; done"
        ))
        .status()
        .unwrap()
        .success();
    assert!(!alive, "child {child} still running after the timeout");
}

#[test]
fn an_env_header_reaches_only_its_own_script() {
    let r = repo(&[
        (
            "crates/a/knives/k1.sh",
            "#!/usr/bin/env bash\n# aib-knives: os=any\n# aib-knives: env=AIB_KNIVES_NO_SE=1\necho k1-se=${AIB_KNIVES_NO_SE:-unset}\n",
        ),
        (
            "crates/b/knives/k2.sh",
            "#!/usr/bin/env bash\n# aib-knives: os=any\necho k2-se=${AIB_KNIVES_NO_SE:-unset}\n",
        ),
    ]);
    let (rc, out) = run(&r, "rust");
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("k1-se=1"), "{out}");
    assert!(out.contains("k2-se=unset"), "{out}");

    let r = repo(&[(
        "crates/a/knives/k1.sh",
        "#!/usr/bin/env bash\n# aib-knives: os=any\n# aib-knives: env=A=$(id)\n",
    )]);
    let (rc, out) = run(&r, "rust");
    assert_eq!(rc, 1, "{out}");
    assert!(
        out.contains("KNIVES-HEADER-BAD crates/a/knives/k1.sh: env=A=$(id)"),
        "{out}"
    );
}
