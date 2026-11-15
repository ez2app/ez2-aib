//! S79 n-4a wake: `aib-drive event <kind> [--ref X]` writes one JSON line into
//! `<root>/drive/event` by temp file + rename; observe reads the report id back.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_drive::wake;

use std::path::PathBuf;
use std::process::Command;

const SHA: &str = "0123456789abcdef0123456789abcdef01234567";

fn root(tag: &str, with_drive: bool) -> PathBuf {
    let d = std::env::temp_dir().join(format!("aib-drive-wake-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    if with_drive {
        std::fs::create_dir_all(d.join("drive")).unwrap();
    }
    d
}

fn v(s: &[&str]) -> Vec<String> {
    s.iter().map(|x| (*x).to_owned()).collect()
}

fn run(args: &[&str], root: &std::path::Path, by_env: Option<&str>) -> wake::Out {
    wake::cli_event(
        &v(args),
        Ok(root.to_path_buf()),
        by_env.map(str::to_owned),
        (1_794_700_000, 7),
        4242,
    )
}

fn listing(dir: &std::path::Path) -> Vec<String> {
    let mut n: Vec<String> = std::fs::read_dir(dir)
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
        .collect();
    n.sort();
    n
}

#[test]
fn an_event_is_one_json_line_put_in_place_by_rename() {
    let d = root("line", true);
    let o = run(&["seat_free", "--ref", "7b81e"], &d, None);
    assert_eq!(o.code, 0, "{o:?}");
    assert_eq!(listing(&d.join("drive")), ["event"], "no temp file left");
    let text = std::fs::read_to_string(d.join("drive/event")).unwrap();
    assert!(
        text.ends_with('\n') && text.lines().count() == 1,
        "{text:?}"
    );
    let ev = wake::parse(&text).unwrap();
    assert_eq!(
        ev,
        wake::Event {
            id: "ev-1794700000-4242-000000007".into(),
            kind: "seat_free".into(),
            reference: Some("7b81e".into()),
            at: 1_794_700_000,
            by: "cli".into(),
        }
    );
    assert!(
        text.contains("\"ref\":\"7b81e\""),
        "the field is named ref: {text}"
    );
}

#[test]
fn a_later_event_replaces_the_line() {
    let d = root("latest", true);
    assert_eq!(run(&["tick"], &d, None).code, 0);
    assert_eq!(
        run(&["merge", "--ref", &format!("pr/762@{SHA}")], &d, None).code,
        0
    );
    let ev = wake::parse(&std::fs::read_to_string(d.join("drive/event")).unwrap()).unwrap();
    assert_eq!(ev.kind, "merge", "the latest wins (one round either way)");
    assert_eq!(listing(&d.join("drive")), ["event"]);
}

#[test]
fn no_drive_folder_means_nothing_written_and_nothing_made() {
    let d = root("nodrive", false);
    let o = run(&["tick"], &d, None);
    assert_eq!(o.code, 0, "{o:?}");
    assert!(o.stdout.contains("not written"), "{o:?}");
    assert!(!d.join("drive").exists(), "the folder is not created");
}

#[test]
fn a_write_that_fails_is_exit_5_and_leaves_no_temp_file() {
    let d = root("fail", true);
    // `event` is a non-empty folder: the rename cannot replace it
    std::fs::create_dir_all(d.join("drive/event/x")).unwrap();
    let o = run(&["tick"], &d, None);
    assert_eq!(o.code, 5, "{o:?}");
    assert_eq!(listing(&d.join("drive")), ["event"], "temp file removed");
    let o = wake::cli_event(&v(&["tick"]), Err("no root".into()), None, (1, 0), 1);
    assert_eq!(o.code, 5, "{o:?}");
}

#[test]
fn each_kind_takes_only_its_own_ref() {
    let d = root("refs", true);
    let ok = |a: &[&str]| run(a, &d, None).code;
    assert_eq!(ok(&["merge", "--ref", &format!("pr/762@{SHA}")]), 0);
    assert_eq!(ok(&["merge"]), 0, "a ref is optional");
    assert_eq!(ok(&["merge", "--ref", "pr/762"]), 2, "no head");
    assert_eq!(
        ok(&["merge", "--ref", "dm/7b81e/q_1"]),
        2,
        "another kind's ref"
    );
    assert_eq!(ok(&["dm_done", "--ref", "dm/7b81e/q_00000000000000c3"]), 0);
    assert_eq!(ok(&["dm_done", "--ref", "dm//q_1"]), 2);
    assert_eq!(ok(&["seat_free", "--ref", "7b81e"]), 0);
    assert_eq!(ok(&["seat_free", "--ref", "7B81E"]), 2);
    assert_eq!(ok(&["seat_free", "--ref", "7b81"]), 2);
    assert_eq!(ok(&["tick", "--ref", "x"]), 2, "a tick carries nothing");
    assert_eq!(ok(&["receipt", "--ref", "n12-1-verify-c239fb579.log"]), 0);
    assert_eq!(ok(&["review", "--ref", "a\nb"]), 2, "one line");
    assert_eq!(ok(&["review", "--ref", &"x".repeat(121)]), 2, "at most 120");
}

#[test]
fn usage_errors_are_exit_2_and_write_nothing() {
    let d = root("usage", true);
    for bad in [
        &[][..],
        &["--ref", "x"],
        &["nap"],
        &["tick", "--oops"],
        &["tick", "--by"],
        &["tick", "--by", "a", "--by", "b"],
        &["tick", "--by", "a\tb"],
    ] {
        let o = run(bad, &d, None);
        assert_eq!(o.code, 2, "{bad:?} {o:?}");
        assert!(o.stderr.contains("usage:"), "{bad:?}");
    }
    assert!(listing(&d.join("drive")).is_empty(), "nothing written");
}

#[test]
fn by_names_the_writer_and_a_consumer_skips_its_own() {
    let d = root("by", true);
    let read = || wake::parse(&std::fs::read_to_string(d.join("drive/event")).unwrap()).unwrap();
    run(&["tick"], &d, Some("aib-drive"));
    let ev = read();
    assert_eq!(ev.by, "aib-drive", "AIB_DRIVE_BY");
    assert!(wake::is_own(&ev, "aib-drive"));
    assert!(!wake::is_own(&ev, "f1fae"));
    run(&["tick", "--by", "mergewatch"], &d, Some("aib-drive"));
    assert_eq!(read().by, "mergewatch", "--by wins over the environment");
    run(&["tick"], &d, Some(""));
    assert_eq!(read().by, "cli", "empty env = default");
}

#[test]
fn observe_reads_the_report_id_of_new_and_old_files() {
    let pr = format!("pr/762@{SHA}");
    let line = |kind: &str, r: Option<&str>| {
        serde_json::to_string(&wake::make(kind, r, "cli", 1, 1, 1)).unwrap()
    };
    assert_eq!(
        wake::report_event_of(&line("merge", Some(&pr))),
        Some(pr.clone())
    );
    assert_eq!(
        wake::report_event_of(&line("dm_done", Some("dm/7b81e/q_1"))),
        Some("dm/7b81e/q_1".to_owned())
    );
    assert_eq!(
        wake::report_event_of(&line("seat_free", Some("7b81e"))),
        None,
        "wake only"
    );
    assert_eq!(wake::report_event_of(&line("tick", None)), None);
    // a bare id written before this slice still counts; anything else is a wake-up
    assert_eq!(wake::report_event_of(&format!("{pr}\n")), Some(pr));
    assert_eq!(wake::report_event_of(""), None);
    assert_eq!(wake::report_event_of("{not json"), None);
}

#[test]
fn the_program_writes_the_event_under_the_state_root() {
    let d = root("bin", true);
    let o = Command::new(env!("CARGO_BIN_EXE_aib-drive"))
        .args(["event", "seat_free", "--ref", "7b81e"])
        .env_clear()
        .env("EZ2_AIB_STATE_ROOT", &d)
        .env("AIB_DRIVE_BY", "aib-drive")
        .envs(std::env::var_os("SYSTEMROOT").map(|v| ("SYSTEMROOT", v)))
        .output()
        .unwrap();
    assert_eq!(
        o.status.code(),
        Some(0),
        "{}",
        String::from_utf8_lossy(&o.stderr)
    );
    let ev = wake::parse(&std::fs::read_to_string(d.join("drive/event")).unwrap()).unwrap();
    assert_eq!(
        (ev.kind.as_str(), ev.by.as_str()),
        ("seat_free", "aib-drive")
    );
    let o = Command::new(env!("CARGO_BIN_EXE_aib-drive"))
        .args(["nap"])
        .env_clear()
        .env("EZ2_AIB_STATE_ROOT", &d)
        .envs(std::env::var_os("SYSTEMROOT").map(|v| ("SYSTEMROOT", v)))
        .output()
        .unwrap();
    assert_eq!(o.status.code(), Some(2));
}
