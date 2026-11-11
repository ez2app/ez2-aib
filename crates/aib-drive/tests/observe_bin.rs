//! `aib-drive observe` end to end (1ced7 PR-C L1): the real program with
//! fake `gh` / `aib` on PATH. A test binary of its own: spawning a child
//! while another test in the same process holds the observe lock lets the
//! child inherit that lock until it execs, and the lock tests would see a
//! second observe that is not there (Mac B receipt 2e85de19).
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_drive::observe::Record;

const REPO: &str = "ez2app/ez2-aib";

fn root(tag: &str) -> std::path::PathBuf {
    let d = std::env::temp_dir().join(format!("aib-drive-obs-bin-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// V1 / V2 end to end (1ced7 PR-C L1): the real program with fake `gh` and
/// `aib` first on PATH, each logging its argv. Only reads are run: `gh api
/// rate_limit`, `gh api repos/<repo>/…` and `aib dm-queue list`. Also
/// covers K1 / K4: there is no other program and no write verb.
#[cfg(unix)]
#[test]
fn the_program_runs_only_reads_and_prints_one_line_and_json() {
    use std::os::unix::fs::PermissionsExt;
    let d = root("bin");
    let bin = d.join("bin");
    let pending = d.join("pending");
    std::fs::create_dir_all(&bin).unwrap();
    std::fs::create_dir_all(&pending).unwrap();
    let log = d.join("argv.log");
    let fake = |name: &str, body: &str| {
        let p = bin.join(name);
        let script = format!(
            "#!/bin/sh\nprintf '%s' '{name}' >> '{log}'\nfor a in \"$@\"; do printf ' %s' \"$a\" >> '{log}'; done\necho >> '{log}'\n{body}\n",
            log = log.display()
        );
        std::fs::write(&p, script).unwrap();
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
    };
    fake(
        "gh",
        r#"if [ "$2" = rate_limit ]; then echo "{\"resources\":{\"core\":{\"remaining\":$QUOTA}}}"; else echo '[]'; fi"#,
    );
    fake("aib", "echo '[]'");
    let path = format!(
        "{}:{}",
        bin.display(),
        std::env::var("PATH").unwrap_or_default()
    );
    let run = |quota: &str| {
        std::process::Command::new(env!("CARGO_BIN_EXE_aib-drive"))
            .args(["observe", "--repo", REPO, "--pending"])
            .arg(&pending)
            .args([
                "--captain",
                "f1fae",
                "--collector",
                "f1fae",
                "--seats",
                "2240e",
                "--once",
            ])
            .env("PATH", &path)
            .env("QUOTA", quota)
            .env("EZ2_AIB_STATE_ROOT", d.join("state"))
            .output()
            .unwrap()
    };
    let out = run("5000");
    assert_eq!(out.status.code(), Some(0), "{out:?}");
    let text = String::from_utf8(out.stdout).unwrap();
    let mut it = text.lines();
    assert!(it.next().unwrap().contains("dry-run"), "{text}");
    let r: Record = serde_json::from_str(it.next().unwrap()).unwrap();
    assert!(r.dry_run && r.unknown.is_none());
    let argv = std::fs::read_to_string(&log).unwrap();
    let calls: Vec<&str> = argv.lines().collect();
    assert_eq!(calls.first(), Some(&"gh api rate_limit"), "{argv}");
    for c in &calls {
        let read = *c == "gh api rate_limit"
            || c.starts_with(&format!("gh api repos/{REPO}/"))
            || c.starts_with("aib dm-queue list ");
        assert!(read && !c.contains(" -X") && !c.contains(" -f"), "{c}");
    }
    // below the floor: no round of GitHub reads, rc 3 with the reason
    std::fs::remove_file(&log).unwrap();
    let out = run("10");
    assert_eq!(out.status.code(), Some(3), "{out:?}");
    assert!(String::from_utf8_lossy(&out.stdout).contains("配額保留"));
    assert_eq!(
        std::fs::read_to_string(&log).unwrap(),
        "gh api rate_limit\n"
    );
}
