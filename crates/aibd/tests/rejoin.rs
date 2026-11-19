#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! N-revive (ORDER-workspace-519d0-1118 第三波 1): aibd stops on purpose (the version swap) and
//! starts again on the same data. The seat that was running is started again with its own
//! conversation picked up (`--resume <the id it was opened with>`) and takes text; the seat the
//! person had stopped stays stopped.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{log_with, seat_dir, text_of, Daemon};
use std::time::{Duration, Instant};

fn args_of(cwd: &std::path::Path) -> Vec<(String, Vec<String>)> {
    std::fs::read_to_string(cwd.join("fake-provider-args.log"))
        .unwrap_or_default()
        .lines()
        .map(|l| {
            let (pid, j) = l.split_once(' ').unwrap();
            (pid.to_string(), serde_json::from_str(j).unwrap())
        })
        .collect()
}

fn after(a: &[String], flag: &str) -> Option<String> {
    a.iter().position(|w| w == flag).map(|i| a[i + 1].clone())
}

#[tokio::test(flavor = "multi_thread")]
async fn a_running_seat_comes_back_with_its_conversation_after_aibd_restarts() {
    let dir = tempfile::tempdir().unwrap();
    // where claude would keep the conversations (this test binary is its own process)
    let claude = dir.path().join("claude-config");
    std::env::set_var("CLAUDE_CONFIG_DIR", &claude);
    let (wa, wb) = (seat_dir(dir.path(), "a"), seat_dir(dir.path(), "b"));
    let mut d = Daemon::start(dir.path()).await;
    let (a, _) = d.open("safe", &wa).await;
    let (b, sb) = d.open("safe", &wb).await;
    let r = d
        .call("DELETE", &format!("/api/v1/sessions/{sb}"), None, None)
        .await;
    assert!(r.code < 300, "{} {}", r.code, r.body);
    d.shutdown().await;
    let first = args_of(&wa);
    assert_eq!(first.len(), 1, "{first:?}");
    let conv = after(&first[0].1, "--session-id").unwrap();
    // the seat had a turn: claude kept its conversation (the fake provider keeps none)
    let kept = aibd::revive::claude_dir_for(&claude, wa.to_str().unwrap());
    std::fs::create_dir_all(&kept).unwrap();
    std::fs::write(kept.join(format!("{conv}.jsonl")), "{}\n").unwrap();

    let mut d = Daemon::start(dir.path()).await;
    let end = Instant::now() + Duration::from_secs(15);
    let running = loop {
        let r = d
            .call("GET", &format!("/api/v1/seats/{a}/sessions"), None, None)
            .await;
        let list = r.json["sessions"].as_array().cloned().unwrap_or_default();
        if let Some(s) = list.iter().find(|s| s["state"] == "running") {
            break s.clone();
        }
        assert!(
            Instant::now() < end,
            "reason: seat_not_picked_up {}",
            r.body
        );
        tokio::time::sleep(Duration::from_millis(100)).await;
    };
    let again = args_of(&wa);
    assert_eq!(again.len(), 2, "{again:?}");
    assert_eq!(
        after(&again[1].1, "--resume").as_deref(),
        Some(conv.as_str()),
        "reason: not_the_same_conversation {again:?}"
    );
    assert!(after(&again[1].1, "--session-id").is_none(), "{again:?}");

    // the seat picked up again takes text in its new process
    // typing takes a confirmed paired page on the chain (S89-input); this run's page
    let cred = d.page(true).await;
    let r = d.act(Some(&cred), &a, "接回來了嗎").await;
    assert!(r.code < 300, "{} {}", r.code, r.body);
    let log = log_with(&wa, 1);
    assert_eq!(text_of(&log.last().unwrap().1), "接回來了嗎");
    assert_eq!(log.last().unwrap().0, again[1].0, "{running}");

    // the seat the person stopped stays stopped
    let r = d
        .call("GET", &format!("/api/v1/seats/{b}/sessions"), None, None)
        .await;
    let list = r.json["sessions"].as_array().cloned().unwrap_or_default();
    assert!(
        list.iter().all(|s| s["state"] != "running"),
        "reason: stopped_seat_restarted {}",
        r.body
    );
    // started once at most: the stop can land before the fake provider wrote its argv (0 on
    // lin 5c0eb46a); a pick-up would make it 2
    assert!(args_of(&wb).len() <= 1, "reason: stopped_seat_restarted");
    d.shutdown().await;
}
