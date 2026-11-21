//! `aib-drive act --auto` end to end (5d33a 624d4c96 M1: act --auto wrote next's text for
//! people into act-next.json, act could not read it and every watch round's act stopped
//! there; no test ran act --auto). The real program, fake `gh` / `aib` / `aib-idea`, a live
//! dm-queue item, every switch off.
#![cfg(unix)] // the fakes are sh scripts; on Windows the helpers below would be unused (-D warnings)
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use serde_json::{json, Value};
use std::os::unix::fs::PermissionsExt;
use std::path::Path;

fn fake(dir: &Path, name: &str, log: &Path, body: &str) {
    let p = dir.join(name);
    let script = format!(
        "#!/bin/sh\nprintf '%s' '{name}' >> '{log}'\nfor a in \"$@\"; do printf ' %s' \"$a\" >> '{log}'; done\necho >> '{log}'\n{body}\n",
        log = log.display()
    );
    std::fs::write(&p, script).unwrap();
    std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
}

#[test]
fn act_auto_reads_next_as_json_and_nudges_a_live_queued_dm_as_a_dry_run() {
    let d = std::env::temp_dir().join(format!("aib-drive-act-auto-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    let (path_bin, own_bin, drive) = (d.join("path"), d.join("bin"), d.join("drive"));
    for p in [&path_bin, &own_bin, &drive] {
        std::fs::create_dir_all(p).unwrap();
    }
    let log = d.join("argv.log");
    fake(&path_bin, "gh", &log, "echo '[]'");
    fake(&path_bin, "aib", &log, "echo '[]'");
    // next's --ideas default runs <root>/bin/aib-idea when it is there
    fake(&own_bin, "aib-idea", &log, "echo '[]'");
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let mut item: Value =
        serde_json::from_str(include_str!("fixtures/dm-queue-item-v2-queued.json")).unwrap();
    item["sent_at"] = json!(now - 600);
    let seat = d.join("state").join("dm-queue").join("56cda");
    std::fs::create_dir_all(&seat).unwrap();
    std::fs::write(seat.join("q_0627a36d2f012a16.json"), item.to_string()).unwrap();
    let path = format!(
        "{}:{}",
        path_bin.display(),
        std::env::var("PATH").unwrap_or_default()
    );
    let o = std::process::Command::new(env!("CARGO_BIN_EXE_aib-drive"))
        .args(["act", "--auto"])
        .env("PATH", &path)
        .env("EZ2_AIB_STATE_ROOT", &d)
        .env("AIB_DRIVE_AIBD_SWITCH", "on")
        .env_remove("EZ2_AIB_HOME")
        .env("AIB_DRIVE_REPO", "ez2app/ez2-aib")
        .output()
        .unwrap();
    let text = String::from_utf8_lossy(&o.stdout);
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    // what next wrote for act is JSON act reads
    let next: Value =
        serde_json::from_str(&std::fs::read_to_string(drive.join("act-next.json")).unwrap())
            .unwrap();
    assert!(next["rows"].is_array(), "{next}");
    let seats: Value =
        serde_json::from_str(&std::fs::read_to_string(drive.join("act-seats.json")).unwrap())
            .unwrap();
    assert!(seats.is_object(), "{seats}");
    assert!(
        text.contains("would nudge [關] 提醒: 你有一封還沒收的信 q_0627a36d2f012a16 → 56cda"),
        "{text}"
    );
    assert!(drive.join("act-dryrun.jsonl").is_file());
    // v-2: each act --auto round also writes unblock.json, with aib-drive's own source
    let ub: Value =
        serde_json::from_str(&std::fs::read_to_string(drive.join("unblock.json")).unwrap())
            .unwrap();
    assert_eq!(ub["schema"], "aib-drive.unblock.v2", "{ub}");
    assert!(
        ub["sources"]
            .as_array()
            .unwrap()
            .iter()
            .any(|s| s["name"] == "drive"),
        "{ub}"
    );
    // every switch off: nothing is sent, the queue item is untouched
    let calls = std::fs::read_to_string(&log).unwrap_or_default();
    assert!(!calls.lines().any(|l| l.starts_with("aib dm")), "{calls}");
    assert_eq!(
        std::fs::read_to_string(seat.join("q_0627a36d2f012a16.json")).unwrap(),
        item.to_string()
    );
    let _ = std::fs::remove_dir_all(&d);
}
