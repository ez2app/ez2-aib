//! `aib-drive observe` end to end (1ced7 PR-C L1): the real program with
//! fake `gh` / `aib` on PATH. A test binary of its own: spawning a child
//! while another test in the same process holds the observe lock lets the
//! child inherit that lock until it execs, and the lock tests would see a
//! second observe that is not there (Mac B receipt 2e85de19).
#![cfg(unix)] // the fake `gh` / `aib` are sh scripts; on Windows the helpers below would be unused (-D warnings)
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

/// V8 (1ced7 PR-C acceptance) with real data: #661 / #667 / #678 as GitHub
/// had them on 11-11 (pull, commits, files, timeline, check runs at the
/// reviewed heads; rules on main read 11-11 evening), and their review first
/// lines. Every PR is read through (commits / files / timeline / check runs:
/// 1ced7 r3 N1), and v0 proposes no merge: nobody's identity is verified yet
/// and the freeze and merge slot are not read.
#[cfg(unix)]
#[test]
fn real_prs_661_667_678_are_read_through_and_get_no_merge() {
    use aib_drive::decide::Intent;
    use std::os::unix::fs::PermissionsExt;
    let fix = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/v8");
    let d = root("v8");
    let bin = d.join("bin");
    let pending = d.join("pending");
    std::fs::create_dir_all(&bin).unwrap();
    std::fs::create_dir_all(&pending).unwrap();
    for (name, line) in [
        ("REVIEW-661-consent-run-2240e-0c327b35.md", "結論: 不通過 頭: 0c327b358846842e54e843380f57a0617bbe0ae6 H/M/L/N: 0/2/3/4 範圍: 增量 35c384b90ec8043de2f3cf6a1521ec2d842ab912..0c327b358846842e54e843380f57a0617bbe0ae6"),
        ("REVIEW-661-r2-2240e-d4bafde2.md", "結論: 通過 頭: d4bafde28b279252c5b5916da30f63395aaa4143 H/M/L/N: 0/0/1/0 範圍: 增量 0c327b358846842e54e843380f57a0617bbe0ae6..d4bafde28b279252c5b5916da30f63395aaa4143"),
        ("REVIEW-667-unixonly-2240e-cca3249e.md", "結論: 通過 頭: cca3249e502694a89becbd48f7db31b072b9098f H/M/L/N: 0/0/0/0 範圍: 增量 347e16c045eed58b07b87884817a7a045fe66992..cca3249e502694a89becbd48f7db31b072b9098f(讀碼，待 CI)"),
        ("REVIEW-678-release-tempdir-2240e-7bcb6ee0.md", "結論: 通過 頭: 7bcb6ee014c446d41455fa32664e646860421fe4 H/M/L/N: 0/0/0/1 範圍: 增量 cdddab4398e8effcbb67bd01d5303f1ab00000f6..7bcb6ee014c446d41455fa32664e646860421fe4"),
    ] {
        std::fs::write(pending.join(name), format!("{line}\n\nbody\n")).unwrap();
    }
    let log = d.join("argv.log");
    // a path is served from fixtures/v8/<path with / ? & = as _>.json
    let gh = format!(
        r#"#!/bin/sh
printf 'gh' >> '{log}'; for a in "$@"; do printf ' %s' "$a" >> '{log}'; done; echo >> '{log}'
if [ "$2" = rate_limit ]; then echo '{{"resources":{{"core":{{"remaining":5000}}}}}}'; exit 0; fi
f='{fix}'/$(printf '%s' "$2" | tr '/?&=' '____').json
[ -f "$f" ] || {{ echo "404 $2" >&2; exit 1; }}
cat "$f"
"#,
        log = log.display(),
        fix = fix.display()
    );
    let aib = format!(
        "#!/bin/sh\nprintf 'aib' >> '{log}'; for a in \"$@\"; do printf ' %s' \"$a\" >> '{log}'; done; echo >> '{log}'\necho '[]'\n",
        log = log.display()
    );
    for (name, body) in [("gh", gh), ("aib", aib)] {
        let p = bin.join(name);
        std::fs::write(&p, body).unwrap();
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    let path = format!(
        "{}:{}",
        bin.display(),
        std::env::var("PATH").unwrap_or_default()
    );
    let out = std::process::Command::new(env!("CARGO_BIN_EXE_aib-drive"))
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
        .env("EZ2_AIB_STATE_ROOT", d.join("state"))
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(0), "{out:?}");
    let text = String::from_utf8(out.stdout).unwrap();
    let r: Record = serde_json::from_str(text.lines().nth(1).unwrap()).unwrap();
    assert!(r.unknown.is_none(), "{r:?}");
    let heads: Vec<u64> = r.heads.keys().copied().collect();
    assert_eq!(heads, vec![661, 667, 678]);
    assert!(
        !r.would.iter().any(|i| matches!(i, Intent::Merge { .. })),
        "{:?}",
        r.would
    );
    assert!(
        !r.would.iter().any(Intent::is_shared_write),
        "{:?}",
        r.would
    );
    let argv = std::fs::read_to_string(&log).unwrap();
    for (n, head) in [
        (661, "d4bafde28b279252c5b5916da30f63395aaa4143"),
        (667, "cca3249e502694a89becbd48f7db31b072b9098f"),
        (678, "7bcb6ee014c446d41455fa32664e646860421fe4"),
    ] {
        for want in [
            format!("gh api repos/{REPO}/pulls/{n}/commits?per_page=100"),
            format!("gh api repos/{REPO}/pulls/{n}/files?per_page=100"),
            format!("gh api repos/{REPO}/issues/{n}/timeline?per_page=100"),
            format!("gh api repos/{REPO}/commits/{head}/check-runs?filter=latest&per_page=100"),
        ] {
            assert!(argv.lines().any(|l| l == want), "{want} not read:\n{argv}");
        }
    }
}
