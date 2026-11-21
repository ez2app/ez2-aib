#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! 1a93b DESIGN-seat-clear r1 (519d0 11-20 23:38): the page's 清空 (act seat_clear) stops a seat
//! that said its handoff is written and opens it on a new conversation whose first line is
//! aibd's: read the handoff. No handoff, a handoff outside the seat's own places, or one the
//! page did not see: nothing is stopped. The same key again answers the first answer.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{log_with, read_log, seat_dir, Daemon};
use serde_json::json;
use std::path::{Path, PathBuf};

fn args_log(cwd: &Path) -> Vec<String> {
    std::fs::read_to_string(cwd.join("fake-provider-args.log"))
        .unwrap_or_default()
        .split_inclusive('\n')
        .filter(|l| l.ends_with('\n'))
        .map(|l| l.trim_end().to_owned())
        .collect()
}

fn find_db(dir: &Path) -> PathBuf {
    let mut todo = vec![dir.to_path_buf()];
    while let Some(d) = todo.pop() {
        for e in std::fs::read_dir(&d).unwrap().flatten() {
            let p = e.path();
            if p.is_dir() {
                todo.push(p);
            } else if p.file_name().is_some_and(|n| n == "aibd.sqlite") {
                return p;
            }
        }
    }
    panic!("reason: no_store");
}

/// The fake provider logs `<pid> <JSON list of its arguments>` (aibd-fake-provider.rs): the
/// argument after `flag` in that list.
fn the_id_after(line: &str, flag: &str) -> Option<String> {
    let (_, list) = line.split_once(' ')?;
    let args: Vec<String> = serde_json::from_str(list).ok()?;
    let at = args.iter().position(|a| a == flag)?;
    args.get(at + 1).cloned()
}

#[tokio::test(flavor = "multi_thread")]
async fn the_page_clears_a_seat_onto_a_new_conversation_that_reads_its_handoff() {
    let dir = tempfile::tempdir().unwrap();
    std::env::set_var("CLAUDE_CONFIG_DIR", dir.path().join("claude-config"));
    let wb = seat_dir(dir.path(), "b");
    let mut d = Daemon::start(dir.path()).await;
    let (b, sb) = d.open("safe", &wb).await;
    let store = aib_store::Store::open(&find_db(dir.path())).unwrap();
    let meta = store.seat_meta(&b).unwrap().unwrap();
    let old_id = meta.resume_id.clone();
    let cred = d.page(true).await;
    let tok = aibd::seat_token::tokens().issue(&b).unwrap();
    let clear = |at: &str, key: &str| {
        json!({"type": "seat_clear", "seat_id": b, "handoff_at": at, "idem_key": key}).to_string()
    };

    // no handoff: refused, nothing stopped
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&clear("x", "c0")))
        .await;
    assert_eq!(r.code, 409, "reason: cleared_without_handoff {}", r.body);
    assert_eq!(r.json["code"], "no_handoff");

    // the seat works a turn, then says where its note is
    let r = d.act(Some(&cred), &b, "hello").await;
    assert!(r.code < 300, "{} {}", r.code, r.body);
    log_with(&wb, 1);
    let handoff = |path: String| {
        let tok = tok.clone();
        let d = &d;
        async move {
            let ask = json!({"type": "seat_handoff", "path": path}).to_string();
            let r = d.call("POST", "/api/v1/act", Some(&tok), Some(&ask)).await;
            assert_eq!(r.code, 200, "{}", r.body);
            r.json["handoff"]["at"].as_str().unwrap().to_owned()
        }
    };
    // a note outside the seat's places: refused
    let at = handoff("/tmp/HANDOFF-其他席-1.md".into()).await;
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&clear(&at, "c1")))
        .await;
    assert_eq!(r.code, 409, "reason: path_outside_cleared {}", r.body);
    assert_eq!(r.json["code"], "handoff_path_outside");

    let note = format!("{}/秘密交接-1.md", meta.cwd.trim_end_matches('/'));
    let at = handoff(note.clone()).await;
    // the page saw another one: refused
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&cred),
            Some(&clear("2026-01-01T00:00:00.000Z", "c2")),
        )
        .await;
    assert_eq!(r.code, 409, "reason: unseen_handoff_used {}", r.body);
    assert_eq!(r.json["code"], "handoff_changed");
    let starts = args_log(&wb).len();

    // cleared: a new conversation, the kept id swapped, the first line read
    let mut r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&clear(&at, "c3")))
        .await;
    for _ in 0..50 {
        if r.json["code"] != "seat_busy" {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
        r = d
            .call("POST", "/api/v1/act", Some(&cred), Some(&clear(&at, "c3")))
            .await;
    }
    assert_eq!(r.code, 200, "reason: not_cleared {}", r.body);
    let sid = r.json["session_id"].as_str().unwrap().to_owned();
    assert_ne!(sid, sb, "reason: same_session");
    assert_eq!(r.json["kept"], true, "{}", r.body);
    assert_eq!(r.json["first_line"], true, "{}", r.body);
    let args = args_log(&wb);
    assert_eq!(args.len(), starts + 1, "reason: not_started_once {args:?}");
    let again = args.last().unwrap();
    assert!(!again.contains("--resume"), "reason: resumed_old {again}");
    let new_id = the_id_after(again, "--session-id").expect("reason: no_new_id");
    assert_ne!(Some(new_id.clone()), old_id, "reason: same_conversation");
    let kept = store.seat_meta(&b).unwrap().unwrap().resume_id;
    assert_eq!(
        kept.as_deref(),
        Some(new_id.as_str()),
        "reason: resume_id_not_swapped"
    );
    let lines = log_with(&wb, 2);
    let first = String::from_utf8_lossy(&lines.last().unwrap().1).into_owned();
    assert!(
        first.contains("先讀交接單") && first.contains("秘密交接-1.md"),
        "reason: first_line_not_the_handoff {first}"
    );

    // the same key again: the first answer, nothing started
    let n = read_log(&wb).len();
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&clear(&at, "c3")))
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["replayed"], true, "reason: cleared_twice {}", r.body);
    assert_eq!(r.json["session_id"], sid.as_str());
    assert_eq!(args_log(&wb).len(), starts + 1, "reason: started_twice");
    assert_eq!(read_log(&wb).len(), n, "reason: typed_twice");

    // the handoff is used: another key finds none
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&clear(&at, "c4")))
        .await;
    assert_eq!(r.code, 409, "reason: handoff_used_twice {}", r.body);
    assert_eq!(r.json["code"], "no_handoff");

    let r = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    assert!(r.body.contains("seat.cleared"), "reason: not_journaled");
    assert!(
        r.body.contains("seat.resume_id_swapped"),
        "reason: swap_not_journaled"
    );
    // the seat.cleared events carry the path as length + hash only (the fake provider echoes the
    // typed first line back as session output, so the whole journal does name it)
    let cleared: Vec<&serde_json::Value> = r.json["events"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| e["type"] == "seat.cleared")
        .collect();
    assert!(!cleared.is_empty(), "reason: not_journaled");
    for e in &cleared {
        assert!(
            !e.to_string().contains("秘密交接"),
            "reason: path_in_journal {e}"
        );
        assert!(
            e["payload"]["handoff"]["sha256"].is_string(),
            "reason: no_hash {e}"
        );
    }
    d.shutdown().await;
}
