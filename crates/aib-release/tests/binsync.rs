// SPDX-License-Identifier: Apache-2.0
//! aib-release binsync (PLAN-aibtitle-install-043dd-1113 r2 package 3): the checks refuse
//! before anything is written, the swap keeps the tool path present, `.prev` and the ledger
//! follow, a second writer is turned away, leftovers are swept, rollback swaps back.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_release::binsync::{
    install, lock, parse_tool_info, rollback, status, target_fits, tool_info_in_binary, Install,
    LEDGER, LOCK,
};
use aib_release::host_build::{Git, Verdict, MAIN_REF};
use serde_json::Value;
use std::collections::BTreeSet;
use std::fs;
use std::path::Path;

// a line of commits: A < B < C (C = origin/main); D is on a branch off A, not on main
const A: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const B: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const C: &str = "cccccccccccccccccccccccccccccccccccccccc";
const D: &str = "dddddddddddddddddddddddddddddddddddddddd";
const MISSING: &str = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee";

struct FakeGit {
    main: Option<&'static str>,
}

impl Git for FakeGit {
    fn resolve(&self, r: &str) -> Result<String, String> {
        assert_eq!(r, MAIN_REF, "only the fixed main ref is asked");
        self.main
            .map(str::to_owned)
            .ok_or_else(|| "no origin/main in this clone".to_owned())
    }
    fn has_commit(&self, sha: &str) -> Result<bool, String> {
        Ok([A, B, C, D].contains(&sha))
    }
    fn is_ancestor(&self, a: &str, b: &str) -> Result<bool, String> {
        let order = |x: &str| [A, B, C].iter().position(|y| *y == x);
        Ok(a == b
            || (a == A && b == D)
            || matches!((order(a), order(b)), (Some(i), Some(j)) if i <= j))
    }
}

const MAIN: FakeGit = FakeGit { main: Some(C) };

/// A fake built file: some bytes, the build info, the tool info.
fn bin(commit: &str, dirty: u8, name: &str, target: &str) -> Vec<u8> {
    format!(
        "\x7fELF-ish header ... aib-build-info version=0.2.0 commit={commit} dirty={dirty}; ... \
         aib-tool-info name={name} target={target}; ... tail {commit}{name}"
    )
    .into_bytes()
}
fn good(commit: &str) -> Vec<u8> {
    bin(commit, 0, "aib-title", "aarch64-apple-darwin")
}

fn req(dest: &Path, bytes: Vec<u8>) -> Install {
    Install {
        dest: dest.to_owned(),
        name: "aib-title".into(),
        bytes,
        by: "@043dd".into(),
        allow_downgrade: None,
        allow_unknown_current: None,
        main_moved_at: Some(1_794_500_000),
        os: "macos".into(),
        arch: "aarch64".into(),
        now: 1_794_515_000,
    }
}

fn ledger(dest: &Path) -> Value {
    serde_json::from_str(&fs::read_to_string(dest.join(LEDGER)).unwrap()).unwrap()
}

/// The names in `dest`, minus the lock file.
fn names(dest: &Path) -> BTreeSet<String> {
    fs::read_dir(dest)
        .map(|rd| {
            rd.flatten()
                .map(|e| e.file_name().to_string_lossy().into_owned())
                .filter(|n| n != LOCK)
                .collect()
        })
        .unwrap_or_default()
}

#[test]
fn tool_info_parses_one_exact_shape() {
    let t = parse_tool_info("aib-tool-info name=aib-title target=aarch64-apple-darwin;").unwrap();
    assert_eq!(
        (t.name.as_str(), t.target.as_str()),
        ("aib-title", "aarch64-apple-darwin")
    );
    for bad in [
        "aib-tool-info target=x name=aib-title;",
        "aib-tool-info name=aib-title;",
        "aib-tool-info name=aib-title target=x extra=1;",
        "aib-tool-info name= target=x;",
        "aib-tool-info name=a\nb target=x;",
    ] {
        assert!(parse_tool_info(bad).is_none(), "{bad:?}");
    }
    let two = [
        bin(A, 0, "aib-title", "aarch64-apple-darwin"),
        bin(A, 0, "aib-other", "aarch64-apple-darwin"),
    ]
    .concat();
    assert!(
        tool_info_in_binary(&two).is_err(),
        "two_different_tool_infos"
    );
    assert!(tool_info_in_binary(b"no marks").is_err(), "none");
}

#[test]
fn target_must_be_this_machine() {
    assert!(target_fits("aarch64-apple-darwin", "macos", "aarch64"));
    assert!(target_fits("x86_64-unknown-linux-gnu", "linux", "x86_64"));
    assert!(target_fits("x86_64-pc-windows-msvc", "windows", "x86_64"));
    assert!(
        !target_fits("x86_64-apple-darwin", "macos", "aarch64"),
        "arch"
    );
    assert!(
        !target_fits("aarch64-unknown-linux-gnu", "macos", "aarch64"),
        "os"
    );
    assert!(
        !target_fits("aarch64-apple-darwinx", "linux", "aarch64"),
        "os2"
    );
}

#[test]
fn first_install_puts_the_file_and_the_ledger() {
    let td = tempfile::tempdir().unwrap();
    let dest = td.path().join("bin");
    let out = install(&MAIN, &req(&dest, good(B)));
    assert_eq!(out.verdict, Verdict::Ok, "{:?}", out.to_json());
    assert_eq!(fs::read(dest.join("aib-title")).unwrap(), good(B), "bytes");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = fs::metadata(dest.join("aib-title"))
            .unwrap()
            .permissions()
            .mode();
        assert_eq!(mode & 0o777, 0o755, "executable");
    }
    assert!(!dest.join("aib-title.prev").exists(), "no_prev_on_first");
    let j = out.to_json();
    assert_eq!(j["main"], C, "judged_against_main");
    assert_eq!(j["main_moved_at"], 1_794_500_000u64, "main_time_shown");
    assert!(
        j["allowed"][0]["why"]
            .as_str()
            .unwrap()
            .starts_with("first install"),
        "first_install_named: {j}"
    );
    let l = ledger(&dest);
    assert_eq!(l["aib-title"]["commit"], B, "ledger_commit");
    assert_eq!(l["aib-title"]["sha256"], j["sha256"], "ledger_sha");
    assert_eq!(l["aib-title"]["by"], "@043dd", "ledger_by");
    assert_eq!(
        l["aib-title"]["history"].as_array().unwrap().len(),
        1,
        "history"
    );
    assert_eq!(
        names(&dest),
        ["BINS.json", "aib-title"].map(String::from).into(),
        "no_leftovers"
    );
}

#[test]
fn refusals_write_nothing() {
    let cases: Vec<(&str, Vec<u8>, FakeGit, Verdict, &str)> = vec![
        ("dirty", bin(B, 1, "aib-title", "aarch64-apple-darwin"), MAIN, Verdict::Refused, "candidate_dirty"),
        ("unknown", bin("unknown", 0, "aib-title", "aarch64-apple-darwin"), MAIN, Verdict::Refused, "candidate_unknown"),
        ("pr_head", good(D), MAIN, Verdict::Refused, "not_on_main"),
        ("not_fetched", good(MISSING), MAIN, Verdict::Error, "git"),
        ("offline_no_main", good(B), FakeGit { main: None }, Verdict::Error, "git"),
        ("other_tool", bin(B, 0, "aib-plan", "aarch64-apple-darwin"), MAIN, Verdict::Refused, "other_tool"),
        ("other_target", bin(B, 0, "aib-title", "x86_64-apple-darwin"), MAIN, Verdict::Refused, "other_target"),
        ("no_tool_info", b"aib-build-info version=0.2.0 commit=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb dirty=0;".to_vec(), MAIN, Verdict::Refused, "tool_info"),
    ];
    for (what, bytes, git, verdict, code) in cases {
        let td = tempfile::tempdir().unwrap();
        let dest = td.path().join("bin");
        let out = install(&git, &req(&dest, bytes));
        assert_eq!(
            (out.verdict, out.code),
            (verdict, code),
            "{what}: {:?}",
            out.to_json()
        );
        assert!(
            !dest.join("aib-title").exists(),
            "{what}: nothing_installed"
        );
        assert!(!dest.join(LEDGER).exists(), "{what}: no_ledger");
    }
    let td = tempfile::tempdir().unwrap();
    let mut r = req(td.path(), good(B));
    r.name = "aib-plan".into();
    assert_eq!(install(&MAIN, &r).code, "name_not_allowed", "allowlist");
}

#[test]
fn upgrade_keeps_prev_and_downgrade_needs_a_reason() {
    let td = tempfile::tempdir().unwrap();
    let dest = td.path();
    assert_eq!(install(&MAIN, &req(dest, good(B))).verdict, Verdict::Ok);
    // a descendant: allowed, prev = the earlier file
    let out = install(&MAIN, &req(dest, good(C)));
    assert_eq!(out.verdict, Verdict::Ok, "upgrade");
    assert_eq!(fs::read(dest.join("aib-title")).unwrap(), good(C), "new");
    assert_eq!(
        fs::read(dest.join("aib-title.prev")).unwrap(),
        good(B),
        "prev"
    );
    assert_eq!(
        ledger(dest)["aib-title"]["prev_sha256"],
        aib_release::binsync::sha256_hex(&good(B)),
        "ledger_prev"
    );
    let out = install(&MAIN, &req(dest, good(A)));
    assert_eq!(
        (out.verdict, out.code),
        (Verdict::Refused, "not_a_descendant"),
        "downgrade_refused"
    );
    assert_eq!(
        fs::read(dest.join("aib-title")).unwrap(),
        good(C),
        "untouched"
    );
    let mut r = req(dest, good(A));
    r.allow_downgrade = Some("A is the last good one".into());
    let out = install(&MAIN, &r);
    assert_eq!(out.verdict, Verdict::Ok, "downgrade_with_reason");
    assert_eq!(out.to_json()["allowed"][0]["flag"], "allow_downgrade");
}

#[test]
fn current_without_build_info_needs_a_reason() {
    let td = tempfile::tempdir().unwrap();
    let dest = td.path();
    fs::write(dest.join("aib-title"), b"an old hand-copied build").unwrap();
    let out = install(&MAIN, &req(dest, good(B)));
    assert_eq!(out.code, "current_unknown", "refused");
    let mut r = req(dest, good(B));
    r.allow_unknown_current = Some("hand copy from 11-12".into());
    assert_eq!(install(&MAIN, &r).verdict, Verdict::Ok, "with_reason");
    assert_eq!(
        fs::read(dest.join("aib-title.prev")).unwrap(),
        b"an old hand-copied build",
        "old_kept"
    );
}

#[cfg(unix)]
#[test]
fn symlink_at_the_tool_or_prev_is_refused() {
    let td = tempfile::tempdir().unwrap();
    let dest = td.path();
    let elsewhere = dest.join("elsewhere");
    fs::write(&elsewhere, b"x").unwrap();
    for link in ["aib-title", "aib-title.prev"] {
        std::os::unix::fs::symlink(&elsewhere, dest.join(link)).unwrap();
        let out = install(&MAIN, &req(dest, good(B)));
        assert_eq!(out.code, "not_regular", "{link}");
        assert_eq!(
            fs::read(&elsewhere).unwrap(),
            b"x",
            "{link}: target untouched"
        );
        fs::remove_file(dest.join(link)).unwrap();
    }
}

#[test]
fn a_second_writer_is_turned_away() {
    let td = tempfile::tempdir().unwrap();
    let dest = td.path();
    let held = lock(dest).ok().unwrap();
    let out = install(&MAIN, &req(dest, good(B)));
    assert_eq!((out.verdict, out.code), (Verdict::Error, "busy"), "busy");
    assert!(!dest.join("aib-title").exists(), "nothing_written");
    drop(held);
    assert_eq!(
        install(&MAIN, &req(dest, good(B))).verdict,
        Verdict::Ok,
        "after_release"
    );
}

#[test]
fn leftovers_from_a_stopped_run_are_swept() {
    let td = tempfile::tempdir().unwrap();
    let dest = td.path();
    for n in [".aib-title.tmp-new-1-2-x", ".BINS.tmp-ledger-1-2-x"] {
        fs::write(dest.join(n), b"half").unwrap();
    }
    fs::write(dest.join(".aib-plan.tmp-new-1-2-x"), b"not ours").unwrap();
    let out = install(&MAIN, &req(dest, good(B)));
    assert_eq!(out.to_json()["swept_tmp"], 2, "swept_two");
    assert_eq!(
        names(dest),
        [".aib-plan.tmp-new-1-2-x", "BINS.json", "aib-title"]
            .map(String::from)
            .into(),
        "only_ours_swept"
    );
}

#[test]
fn rollback_swaps_and_a_second_one_goes_forward() {
    let td = tempfile::tempdir().unwrap();
    let dest = td.path();
    install(&MAIN, &req(dest, good(B)));
    install(&MAIN, &req(dest, good(C)));
    let out = rollback(
        dest,
        "aib-title",
        "@043dd",
        "C draws the title wrong",
        "macos",
        "aarch64",
        9,
    );
    assert_eq!(out.verdict, Verdict::Ok, "{:?}", out.to_json());
    assert_eq!(fs::read(dest.join("aib-title")).unwrap(), good(B), "back");
    assert_eq!(
        fs::read(dest.join("aib-title.prev")).unwrap(),
        good(C),
        "forward_kept"
    );
    let l = ledger(dest);
    assert_eq!(l["aib-title"]["action"], "rollback");
    assert_eq!(l["aib-title"]["why"], "C draws the title wrong");
    rollback(
        dest,
        "aib-title",
        "@043dd",
        "fixed after all",
        "macos",
        "aarch64",
        10,
    );
    assert_eq!(
        fs::read(dest.join("aib-title")).unwrap(),
        good(C),
        "forward"
    );
    // .prev of another tool or target is refused
    fs::write(
        dest.join("aib-title.prev"),
        bin(B, 0, "aib-plan", "aarch64-apple-darwin"),
    )
    .unwrap();
    let out = rollback(
        dest,
        "aib-title",
        "@043dd",
        "try it",
        "macos",
        "aarch64",
        11,
    );
    assert_eq!(out.code, "prev_not_this_tool");
    assert_eq!(
        fs::read(dest.join("aib-title")).unwrap(),
        good(C),
        "untouched"
    );
    fs::remove_file(dest.join("aib-title.prev")).unwrap();
    assert_eq!(
        rollback(
            dest,
            "aib-title",
            "@043dd",
            "try it",
            "macos",
            "aarch64",
            12
        )
        .code,
        "no_prev"
    );
}

#[test]
fn status_says_when_the_ledger_is_behind() {
    let td = tempfile::tempdir().unwrap();
    let dest = td.path();
    let s = status(Some(&MAIN), dest).to_json();
    assert_eq!(s["tools"][0]["state"], "missing", "missing");
    install(&MAIN, &req(dest, good(B)));
    let s = status(Some(&MAIN), dest).to_json();
    assert_eq!(s["tools"][0]["state"], "ok", "ok");
    assert_eq!(s["tools"][0]["on_main"], true, "on_main");
    // replaced behind its back (a hand copy, or a run stopped before the ledger)
    fs::write(dest.join("aib-title"), good(D)).unwrap();
    let s = status(Some(&MAIN), dest).to_json();
    assert_eq!(s["tools"][0]["state"], "ledger_stale", "stale");
    assert_eq!(s["tools"][0]["on_main"], false, "pr_head_shown");
    // offline: hashes still read, main unknown
    let s = status(Some(&FakeGit { main: None }), dest).to_json();
    assert_eq!(s["tools"][0]["on_main"], "unknown", "offline");
    assert_eq!(s["main"], Value::Null);
}

/// Readers that run the tool every frame never see it missing or half written while
/// installs swap it (the rename property; macOS exec itself is checked on the real cabin).
#[test]
fn a_reader_sees_the_old_or_the_new_file_never_a_gap() {
    if cfg!(windows) {
        return; // renaming over a file another handle reads is not the Windows story (PLAN 2d)
    }
    let td = tempfile::tempdir().unwrap();
    let dest = td.path().to_owned();
    // big files, so a half-written one would be likely to show
    let pad = |c: &str| [good(c), vec![b'x'; 256 * 1024]].concat();
    let (b, c) = (pad(B), pad(C));
    let mut r = req(&dest, b.clone());
    assert_eq!(install(&MAIN, &r).verdict, Verdict::Ok);
    let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let reader = {
        let (stop, p, b, c) = (stop.clone(), dest.join("aib-title"), b.clone(), c.clone());
        std::thread::spawn(move || {
            let mut reads = 0u32;
            while !stop.load(std::sync::atomic::Ordering::Relaxed) {
                let got = fs::read(&p).expect("the tool path is never missing");
                assert!(
                    got == b || got == c,
                    "half-written read ({} bytes)",
                    got.len()
                );
                reads += 1;
            }
            reads
        })
    };
    r.allow_downgrade = Some("swap back and forth for the test".into());
    for i in 0..40u64 {
        r.bytes = if i % 2 == 0 { c.clone() } else { b.clone() };
        r.now = i;
        assert_eq!(install(&MAIN, &r).verdict, Verdict::Ok, "install {i}");
    }
    stop.store(true, std::sync::atomic::Ordering::Relaxed);
    let reads = reader.join().expect("reader saw a gap or a half file");
    assert!(reads > 0, "the reader ran");
}

/// §18 review M2: the same file again swaps nothing, so `.prev` stays the real previous one.
#[test]
fn the_same_file_again_keeps_prev() {
    let td = tempfile::tempdir().unwrap();
    let dest = td.path();
    install(&MAIN, &req(dest, good(B)));
    install(&MAIN, &req(dest, good(C)));
    let out = install(&MAIN, &req(dest, good(C)));
    assert_eq!((out.verdict, out.code), (Verdict::Ok, "same"), "same");
    assert_eq!(
        fs::read(dest.join("aib-title.prev")).unwrap(),
        good(B),
        "prev_kept"
    );
    assert_eq!(
        ledger(dest)["aib-title"]["action"],
        "same",
        "ledger_says_same"
    );
}

/// §18 review M1: a rollback stopped between its renames leaves the only copy of a version
/// in a temp file; the next run keeps it as `.rescued-*`, and status lists it.
#[test]
fn a_stopped_rollback_leaves_nothing_to_lose() {
    let td = tempfile::tempdir().unwrap();
    let dest = td.path();
    install(&MAIN, &req(dest, good(B)));
    fs::write(dest.join(".aib-title.tmp-back-1-2-x"), good(A)).unwrap();
    fs::write(dest.join(".aib-title.tmp-fwd-1-2-x"), good(B)).unwrap();
    fs::write(dest.join(".aib-title.tmp-new-1-2-x"), b"half").unwrap();
    let out = install(&MAIN, &req(dest, good(C)));
    let j = out.to_json();
    assert_eq!(
        (j["swept_tmp"].clone(), j["rescued_tmp"].clone()),
        (1.into(), 2.into()),
        "{j}"
    );
    let rescued: Vec<String> = names(dest)
        .into_iter()
        .filter(|n| n.starts_with("aib-title.rescued-"))
        .collect();
    assert_eq!(rescued.len(), 2, "kept: {rescued:?}");
    let kept: BTreeSet<Vec<u8>> = rescued
        .iter()
        .map(|n| fs::read(dest.join(n)).unwrap())
        .collect();
    assert!(kept.contains(&good(A)), "the_only_copy_of_A_kept");
    let s = status(Some(&MAIN), dest).to_json();
    assert_eq!(
        s["tools"][0]["rescued"].as_array().unwrap().len(),
        2,
        "status_lists"
    );
}

/// §18 review L3: an unreadable ledger is never written over.
#[test]
fn an_unreadable_ledger_stops_before_anything() {
    let td = tempfile::tempdir().unwrap();
    let dest = td.path();
    fs::write(dest.join(LEDGER), b"{ half written").unwrap();
    let out = install(&MAIN, &req(dest, good(B)));
    assert_eq!(
        (out.verdict, out.code),
        (Verdict::Error, "ledger_unreadable"),
        "install"
    );
    assert!(!dest.join("aib-title").exists(), "nothing_installed");
    assert_eq!(
        fs::read(dest.join(LEDGER)).unwrap(),
        b"{ half written",
        "ledger_untouched"
    );
    assert_eq!(
        rollback(dest, "aib-title", "x", "because", "macos", "aarch64", 1).code,
        "ledger_unreadable",
        "rollback"
    );
    assert_eq!(
        status(Some(&MAIN), dest).to_json()["ledger"],
        "ledger_unreadable",
        "status"
    );
}

/// §18 review L5: a reason the check did not use is shown; N1: the ledger is not executable.
#[test]
fn unused_reasons_are_shown_and_the_ledger_is_not_executable() {
    let td = tempfile::tempdir().unwrap();
    let dest = td.path();
    let mut r = req(dest, good(B));
    r.allow_unknown_current = Some("there is an old hand copy".into());
    r.allow_downgrade = Some("just in case".into());
    let j = install(&MAIN, &r).to_json();
    let flags: BTreeSet<String> = j["unused_allow"]
        .as_array()
        .unwrap()
        .iter()
        .map(|u| u["flag"].as_str().unwrap().to_owned())
        .collect();
    assert_eq!(
        flags,
        ["allow_downgrade", "allow_unknown_current"]
            .map(String::from)
            .into(),
        "{j}"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = fs::metadata(dest.join(LEDGER))
            .unwrap()
            .permissions()
            .mode();
        assert_eq!(mode & 0o777, 0o644, "ledger_0644");
    }
}

/// 33e28 L1: the first install moves an unmarked hand copy to .prev; rolling back to it works
/// because the ledger names that file, and an unmarked .prev the ledger does not name is refused.
#[test]
fn rollback_to_the_hand_copy_the_first_install_moved() {
    let td = tempfile::tempdir().unwrap();
    let dest = td.path();
    fs::write(dest.join("aib-title"), b"an old hand-copied build").unwrap();
    let mut r = req(dest, good(B));
    r.allow_unknown_current = Some("hand copy from 11-12".into());
    assert_eq!(install(&MAIN, &r).verdict, Verdict::Ok);
    // the same file again (a re-run) must not forget which file .prev is (33e28 incr L1)
    let mut again = req(dest, good(B));
    again.now = 4;
    assert_eq!(install(&MAIN, &again).code, "same", "rerun_same");
    let out = rollback(
        dest,
        "aib-title",
        "x",
        "B draws wrong",
        "macos",
        "aarch64",
        5,
    );
    assert_eq!(out.verdict, Verdict::Ok, "{:?}", out.to_json());
    assert_eq!(
        fs::read(dest.join("aib-title")).unwrap(),
        b"an old hand-copied build",
        "back_to_hand_copy"
    );
    // forward again, then replace .prev with an unmarked file nobody recorded
    rollback(
        dest,
        "aib-title",
        "x",
        "forward again",
        "macos",
        "aarch64",
        6,
    );
    fs::write(dest.join("aib-title.prev"), b"something else").unwrap();
    assert_eq!(
        rollback(dest, "aib-title", "x", "try it", "macos", "aarch64", 7).code,
        "prev_not_this_tool",
        "unrecorded_unmarked_refused"
    );
    assert_eq!(
        fs::read(dest.join("aib-title")).unwrap(),
        good(B),
        "untouched"
    );
}

/// 33e28 N1: a rescue never overwrites an earlier one from the same second.
#[test]
fn rescues_in_the_same_second_do_not_collide() {
    let td = tempfile::tempdir().unwrap();
    let dest = td.path();
    for bytes in [good(A), good(C)] {
        fs::write(dest.join(".aib-title.tmp-back-1-2-x"), &bytes).unwrap();
        let mut r = req(dest, good(B));
        r.now = 42;
        install(&MAIN, &r);
    }
    let kept: BTreeSet<Vec<u8>> = names(dest)
        .into_iter()
        .filter(|n| n.starts_with("aib-title.rescued-"))
        .map(|n| fs::read(dest.join(n)).unwrap())
        .collect();
    assert!(
        kept.contains(&good(A)) && kept.contains(&good(C)),
        "both_kept"
    );
}
