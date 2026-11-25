#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! 08de5 11-26 (Andy 02:0x「用那台既有的登入,過期或沒有一樣彈出綁定視窗」; c2f0b W-10 e; fc7f9
//! f40246bf6b reads `code`): a page that opens a seat of a brand with no login where the seats
//! read it gets 409 `not_logged_in` with the brand, before any fingerprint window and with no
//! seat written; once the login is there the same open goes through, with no restart. Its own
//! binary: it keeps a HOME with no login (the harness gives the others one with both).

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;
use serde_json::json;

#[tokio::test(flavor = "multi_thread")]
async fn a_brand_with_no_login_is_refused_before_any_window() {
    harness::keep_home();
    // the dgrs switch on, so a dgrs open would ask the owner (202) if it got that far
    std::env::set_var("AIBD_SEAT_INPUT_DGRS", "1");
    let dir = Box::leak(Box::new(tempfile::tempdir().unwrap()));
    let home = dir.path().join("home");
    std::fs::create_dir_all(&home).unwrap();
    std::env::set_var("HOME", &home);
    std::env::remove_var("CODEX_HOME");
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let post = |b: serde_json::Value| {
        let (d, cred) = (&d, cred.clone());
        async move {
            d.call("POST", "/api/v1/act", Some(&cred), Some(&b.to_string()))
                .await
        }
    };
    let count = || async {
        d.call("GET", "/api/v1/seats", None, None).await.json["seats"]
            .as_array()
            .map_or(0, Vec::len)
    };
    for (provider, mode, key) in [
        ("claude", "dgrs", "nl-1"),
        ("claude", "safe", "nl-2"),
        ("codex", "safe", "nl-3"),
    ] {
        let r = post(
            json!({"type": "seat_open", "provider": provider, "mode": mode,
                            "persona_uid": "un0eba", "idem_key": key}),
        )
        .await;
        assert_eq!(
            r.code, 409,
            "reason: {provider}_{mode}_not_refused {}",
            r.body
        );
        assert_eq!(r.json["code"], "not_logged_in", "{}", r.body);
        assert_eq!(r.json["provider"], provider, "{}", r.body);
        assert!(
            r.json.get("ticket").is_none(),
            "reason: window_asked {}",
            r.body
        );
    }
    assert_eq!(count().await, 0, "reason: seat_written");

    // the login lands (as the relogin card leaves it): the same open goes through, no restart
    std::fs::create_dir_all(home.join(".claude")).unwrap();
    std::fs::write(home.join(".claude").join(".credentials.json"), "{}").unwrap();
    let r = post(
        json!({"type": "seat_open", "provider": "claude", "mode": "safe",
                        "persona_uid": "un0eba", "idem_key": "nl-4"}),
    )
    .await;
    assert_eq!(r.code, 201, "reason: logged_in_still_refused {}", r.body);
    // codex still has none
    let r = post(
        json!({"type": "seat_open", "provider": "codex", "mode": "safe",
                        "persona_uid": "un0eba", "idem_key": "nl-5"}),
    )
    .await;
    assert_eq!(r.code, 409, "reason: codex_let_through {}", r.body);
    d.shutdown().await;
}
