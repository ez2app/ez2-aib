#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! 519d0 11-21 06:45 point 1: the built-in page sets a brand's default model; GET version shows
//! it; a seat opened with no model runs on it (`--model` in its argv, kept as the seat's model);
//! a seat that names one keeps its own; a user folder page may not set it.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};
use serde_json::{json, Value};

/// The fake provider's argument lists, once at least `n` whole lines are written (it writes
/// after the open answers: read at once, the log can still be empty; 5d33a st2 41fcd024b
/// default_not_used []). Gives what is there after 10 s.
fn args_of(cwd: &std::path::Path, n: usize) -> Vec<Vec<String>> {
    let mut got = Vec::new();
    for _ in 0..100 {
        let log = std::fs::read_to_string(cwd.join("fake-provider-args.log")).unwrap_or_default();
        got = log
            .split_inclusive('\n')
            .filter(|l| l.ends_with('\n'))
            .filter_map(|l| l.trim_end().split_once(' '))
            .filter_map(|(_, list)| serde_json::from_str::<Vec<String>>(list).ok())
            .collect();
        if got.len() >= n {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    got
}

fn model_in(args: &[String]) -> Option<&str> {
    let at = args.iter().position(|a| a == "--model")?;
    args.get(at + 1).map(String::as_str)
}

#[tokio::test(flavor = "multi_thread")]
async fn a_seat_opened_with_no_model_runs_on_the_brands_default() {
    let dir = tempfile::tempdir().unwrap();
    std::env::set_var("CLAUDE_CONFIG_DIR", dir.path().join("claude-config"));
    let mut d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;
    let set = |m: Value| {
        json!({"type": "default_model_set", "provider": "claude", "model": m}).to_string()
    };

    // refused shapes
    for (m, code) in [
        (
            json!("--dangerously-skip-permissions"),
            "default_model_shape",
        ),
        (json!("a b"), "default_model_shape"),
        (json!(7), "default_model_shape"),
    ] {
        let r = d
            .call("POST", "/api/v1/act", Some(&page), Some(&set(m)))
            .await;
        assert_eq!(
            (r.code, r.json["code"].as_str()),
            (400, Some(code)),
            "{}",
            r.body
        );
    }
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&page),
            Some(
                &json!({"type": "default_model_set", "provider": "nope", "model": "x"}).to_string(),
            ),
        )
        .await;
    assert_eq!(r.json["code"], "default_model_provider", "{}", r.body);

    // a user folder page may not
    let user = d.page_form(true, "&to=user").await;
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&user),
            Some(&set(json!("sonnet"))),
        )
        .await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (403, Some("default_model_built_in_page_only")),
        "reason: user_page_set_default {}",
        r.body
    );

    // set: the answer and GET version say it
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&page),
            Some(&set(json!("sonnet"))),
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["default_models"]["claude"], "sonnet", "{}", r.body);
    let v = d.call("GET", "/api/v1/version", Some(&page), None).await;
    assert_eq!(
        v.json["default_models"]["claude"], "sonnet",
        "reason: version_silent {}",
        v.body
    );
    assert!(
        v.json["capabilities"]["acts"]
            .as_array()
            .unwrap()
            .iter()
            .any(|a| a == "default_model_set"),
        "reason: act_not_listed"
    );

    // a seat with no model runs on it, and keeps it
    let wa = seat_dir(dir.path(), "a");
    let (a, _) = d.open("safe", &wa).await;
    let first = args_of(&wa, 1);
    assert_eq!(
        first.last().and_then(|x| model_in(x)),
        Some("sonnet"),
        "reason: default_not_used {first:?}"
    );
    let seats = d.call("GET", "/api/v1/seats", Some(&page), None).await;
    let row = seats.json["seats"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["seat_id"] == a.as_str())
        .cloned()
        .unwrap_or_default();
    assert_eq!(row["model"], "sonnet", "reason: model_not_kept {row}");

    // cleared: the next seat names no model again
    let r = d
        .call("POST", "/api/v1/act", Some(&page), Some(&set(Value::Null)))
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert!(
        r.json["default_models"].get("claude").is_none(),
        "reason: not_cleared {}",
        r.body
    );
    let wb = seat_dir(dir.path(), "b");
    let _ = d.open("safe", &wb).await;
    let second = args_of(&wb, 1);
    assert_eq!(
        second.last().and_then(|x| model_in(x)),
        None,
        "reason: cleared_default_used {second:?}"
    );

    // point 2: seat a moves to another model and goes on in its own conversation
    let conv = first
        .last()
        .and_then(|x| {
            x.iter()
                .position(|w| w == "--session-id")
                .and_then(|i| x.get(i + 1))
                .cloned()
        })
        .expect("reason: no_first_conversation");
    let mv = |m: &str, k: &str| {
        json!({"type": "seat_model_set", "seat_id": a, "model": m, "idem_key": k}).to_string()
    };
    let r = d
        .call("POST", "/api/v1/act", Some(&page), Some(&mv("--x", "m0")))
        .await;
    assert_eq!(r.json["code"], "seat_model_shape", "{}", r.body);
    let r = d
        .call("POST", "/api/v1/act", Some(&user), Some(&mv("opus", "m1")))
        .await;
    assert_eq!(
        r.json["code"], "seat_model_built_in_page_only",
        "reason: user_page_moved_model {}",
        r.body
    );
    let mut r = d
        .call("POST", "/api/v1/act", Some(&page), Some(&mv("opus", "m2")))
        .await;
    for _ in 0..50 {
        if r.json["code"] != "seat_busy" {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
        r = d
            .call("POST", "/api/v1/act", Some(&page), Some(&mv("opus", "m2")))
            .await;
    }
    assert_eq!(r.code, 200, "reason: model_not_moved {}", r.body);
    let again = args_of(&wa, first.len() + 1);
    let last = again.last().cloned().unwrap_or_default();
    assert_eq!(
        again.len(),
        first.len() + 1,
        "reason: not_started_once {again:?}"
    );
    assert_eq!(
        model_in(&last),
        Some("opus"),
        "reason: new_model_not_used {last:?}"
    );
    // the kept id: --resume when claude has it, the same id fresh when it never had a turn
    // (revive::conversation; the fake provider writes no transcript)
    let resumed = last
        .iter()
        .position(|w| w == "--resume" || w == "--session-id")
        .and_then(|i| last.get(i + 1));
    assert_eq!(
        resumed,
        Some(&conv),
        "reason: conversation_not_kept {last:?}"
    );

    let ev = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    assert!(
        ev.body.contains("aibd.default_model_set"),
        "reason: not_journaled"
    );
    d.shutdown().await;
}
