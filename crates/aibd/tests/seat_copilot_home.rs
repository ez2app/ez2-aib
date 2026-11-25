#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls the fake provider's log on a plain sleep
#![cfg(target_os = "macos")]

//! 3e53b REVIEW-copilot e3d07aff L1 (08de5 11-25): a copilot safe seat, opened end to end on the
//! fake `copilot`, runs every probe and every turn with the home aibd made for it
//! (`<data>/copilot-home/c_…`, holding the signed-in user's `config.json` alone), never the
//! user's own `~/.copilot`. Its own process: it sets HOME and takes copilot's names out of the
//! environment. macOS only: a copilot seat is refused elsewhere (copilot_home_unverified_on_windows).

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

/// The lines the fake `copilot` wrote in `cwd`, once there are at least `n` (at most 20 s).
fn env_lines(cwd: &Path, n: usize) -> Vec<Value> {
    let until = Instant::now() + Duration::from_secs(20);
    loop {
        let text = std::fs::read_to_string(cwd.join("fake-copilot-env.log")).unwrap_or_default();
        let lines: Vec<Value> = text
            .lines()
            .filter_map(|l| serde_json::from_str(l).ok())
            .collect();
        if lines.len() >= n || Instant::now() > until {
            return lines;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn canon(p: &str) -> PathBuf {
    std::fs::canonicalize(p).unwrap_or_else(|_| PathBuf::from(p))
}

#[tokio::test(flavor = "multi_thread")]
async fn a_copilot_seat_runs_in_the_home_aibd_made_for_it() {
    let dir = tempfile::tempdir().unwrap();
    // the user's own copilot: signed in, and with what must never reach a seat (a hook, a
    // plugin on, every folder trusted): run with it, the seat's turn would be refused
    let user = dir.path().join("user");
    std::fs::create_dir_all(user.join(".copilot").join("hooks")).unwrap();
    std::fs::write(user.join(".copilot").join("hooks").join("h.json"), "{}").unwrap();
    std::fs::write(
        user.join(".copilot").join("config.json"),
        r#"// managed automatically
{"loggedInUsers":[{"host":"https://github.com","login":"octo-cat"}],"trustedFolders":["/"],"hooks":{"sessionStart":[]},"enabledPlugins":{"a@b":true}}"#,
    )
    .unwrap();
    std::env::set_var("HOME", &user);
    let names: Vec<String> = std::env::vars()
        .map(|(k, _)| k)
        .filter(|k| {
            k.starts_with("COPILOT_")
                || k.starts_with("XDG_")
                || k.starts_with("GH_")
                || k.starts_with("GITHUB_")
        })
        .collect();
    for k in names {
        std::env::remove_var(k);
    }

    // N3: a home an earlier aibd left (it died before its seat's turn thread ended), with the
    // conversation copilot wrote there, is gone once aibd has started
    let left = dir
        .path()
        .join("data")
        .join("copilot-home")
        .join("c_dead00");
    {
        use std::os::unix::fs::DirBuilderExt;
        std::fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(left.join("session-state"))
            .unwrap();
    }
    std::fs::write(left.join("session-state").join("s.jsonl"), "talk").unwrap();

    let mut d = Daemon::start_copilot(dir.path()).await;
    assert!(!left.exists(), "reason: left_home_kept_at_start");
    let cwd = seat_dir(dir.path(), "cp");
    let (seat, _) = d.open_as("copilot", "safe", &cwd).await;
    let cred = d.page(true).await;
    let mut r = d.act(Some(&cred), &seat, "你好").await;
    for _ in 0..50 {
        if r.code == 200 {
            break;
        }
        assert_eq!(r.json["code"], "seat_starting", "{}", r.body);
        std::thread::sleep(Duration::from_millis(100));
        r = d.act(Some(&cred), &seat, "你好").await;
    }
    assert_eq!(r.code, 200, "{}", r.body);

    // the open's two probes (--version, mcp list), then the turn's own check and the turn
    let got = env_lines(&cwd, 4);
    assert!(got.len() >= 4, "reason: copilot_turn_not_run {got:?}");
    let homes = canon(
        dir.path()
            .join("data")
            .join("copilot-home")
            .to_str()
            .unwrap(),
    );
    let first = got[0]["COPILOT_HOME"]
        .as_str()
        .unwrap_or_default()
        .to_owned();
    for line in &got {
        let home = line["COPILOT_HOME"].as_str().unwrap_or_default();
        assert!(
            !home.is_empty(),
            "reason: copilot_ran_with_the_users_home {line}"
        );
        assert_eq!(home, first, "reason: copilot_home_changed {line}");
        let real = canon(home);
        assert_eq!(
            real.parent(),
            Some(homes.as_path()),
            "reason: copilot_home_not_aibds {line}"
        );
        assert!(
            real.file_name()
                .is_some_and(|n| n.to_string_lossy().starts_with("c_")),
            "reason: copilot_home_not_aibds {line}"
        );
        assert_eq!(
            line["files"],
            serde_json::json!(["config.json"]),
            "reason: copilot_home_holds_more {line}"
        );
    }
    // what the home's config holds: the signed-in user, nothing that runs or trusts
    let cfg: Value =
        serde_json::from_str(&std::fs::read_to_string(canon(&first).join("config.json")).unwrap())
            .unwrap();
    let keys: Vec<&str> = cfg
        .as_object()
        .unwrap()
        .keys()
        .map(String::as_str)
        .collect();
    assert_eq!(keys, ["loggedInUsers"], "reason: copilot_home_config {cfg}");

    // the home goes with the seat
    d.shutdown().await;
    let until = Instant::now() + Duration::from_secs(10);
    while canon(&first).exists() && Instant::now() < until {
        std::thread::sleep(Duration::from_millis(100));
    }
    assert!(!canon(&first).exists(), "reason: copilot_home_left");
}
