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

fn args_of(cwd: &std::path::Path) -> Vec<Vec<String>> {
    std::fs::read_to_string(cwd.join("fake-provider-args.log"))
        .unwrap_or_default()
        .lines()
        .filter_map(|l| l.split_once(' '))
        .filter_map(|(_, list)| serde_json::from_str::<Vec<String>>(list).ok())
        .collect()
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
    let first = args_of(&wa);
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
    let second = args_of(&wb);
    assert_eq!(
        second.last().and_then(|x| model_in(x)),
        None,
        "reason: cleared_default_used {second:?}"
    );

    let ev = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    assert!(
        ev.body.contains("aibd.default_model_set"),
        "reason: not_journaled"
    );
    d.shutdown().await;
}
