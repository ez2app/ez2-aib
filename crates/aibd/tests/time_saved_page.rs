#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! The built-in page asks for the time-saved boxes (PLAN-skipstop-master F2): `time_saved` is
//! listed for the page, answers from the plugin area under the AIB home, and is not answered
//! without the page's credential. The plugin here is a script that prints a whole answer.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{Daemon, Reply};
use serde_json::{json, Value};
use std::os::unix::fs::PermissionsExt;

async fn act(d: &Daemon, cred: Option<&str>, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", cred, Some(&body.to_string()))
        .await
}

const SUM: &str = r#"{"confirmed_ms":7,"estimated_ms":3,"penalty_ms":1,"count":2}"#;

fn whole() -> String {
    let b = |clock: u64| {
        format!(
            r#"{{"total":{SUM},"skip":{SUM},"stop":{SUM},"clock_ms":{clock},"skip_clock_ms":{clock},"skip_clock_unknown_runs":0}}"#
        )
    };
    format!(
        r#"{{"v":1,"measure":"unit_time_sum","today":{},"week":{},"month":{},"year":{},"all":{},"since":null}}"#,
        b(1000),
        b(2000),
        b(3000),
        b(4000),
        b(5000)
    )
}

#[tokio::test(flavor = "multi_thread")]
async fn the_page_gets_the_plugin_s_boxes_and_nobody_else_does() {
    let dir = tempfile::tempdir().unwrap();
    let home = dir.path().join("aib-home");
    std::fs::create_dir_all(&home).unwrap();
    std::env::set_var("EZ2_AIB_HOME", &home);

    let d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;
    let ask = json!({"type": "time_saved", "utc_offset_min": 480});

    // the act is one a page may send
    let v = d.call("GET", "/api/v1/version", Some(&page), None).await;
    let acts = v.json["capabilities"]["acts"].as_array().unwrap();
    assert!(acts.iter().any(|a| a == "time_saved"), "{}", v.body);

    // nothing installed: said so, with no board (asked on another clock than below: an
    // answer is kept a few seconds for the same question)
    let r = act(&d, Some(&page), json!({"type": "time_saved"})).await;
    assert_eq!(
        (r.code, r.json.clone()),
        (200, json!({"state": "not_installed"})),
        "{}",
        r.body
    );

    // installed: the plugin's numbers, on the clock the page asked for
    let folder = home.join("plugins").join("aib-skipstop");
    std::fs::create_dir_all(folder.join("bin")).unwrap();
    let script = format!("#!/bin/sh\ncat <<'EOF_SAVED'\n{}\nEOF_SAVED\n", whole());
    let exe = folder.join("bin").join("aib-skipstop");
    std::fs::write(&exe, &script).unwrap();
    std::fs::set_permissions(&exe, std::fs::Permissions::from_mode(0o755)).unwrap();
    let file = json!({
        "schema": aib_plugin::SCHEMA, "name": "aib-skipstop", "version": "0.2.0",
        "summary": "s", "paid": false, "default": "on",
        "commands": [{"name": "aib-skipstop", "bin": "bin/aib-skipstop"}],
        "sha256": {"bin/aib-skipstop": aib_plugin::sha256_hex(script.as_bytes())},
    });
    std::fs::write(folder.join("aib-plugin.json"), file.to_string()).unwrap();
    let r = act(&d, Some(&page), ask.clone()).await;
    assert_eq!(
        (
            r.code,
            r.json["state"].as_str(),
            r.json["utc_offset_min"].as_i64()
        ),
        (200, Some("ok"), Some(480)),
        "{}",
        r.body
    );
    assert_eq!(
        r.json["saved"]["boxes"]["all"]["clock_ms"], 5000,
        "{}",
        r.body
    );
    assert_eq!(
        r.json["saved"]["boxes"]["today"]["clock_ms"], 1000,
        "{}",
        r.body
    );

    // asked again at once after the plugin is switched off: the kept answer, no new read
    aib_plugin::set_switch(&aib_plugin::switches_file(&home), "aib-skipstop", false).unwrap();
    let again = act(&d, Some(&page), ask.clone()).await;
    assert_eq!(again.json, r.json, "{}", again.body);
    // another question is read anew, and sees the switch
    let other = act(
        &d,
        Some(&page),
        json!({"type": "time_saved", "utc_offset_min": 60}),
    )
    .await;
    assert_eq!(other.json, json!({"state": "off"}), "{}", other.body);

    // the plugin area on the page: the list (the plugin is off since the lines above), and
    // the switch moved from the built-in page
    let l = act(&d, Some(&page), json!({"type": "plugin_list"})).await;
    assert_eq!(
        (l.code, l.json["state"].as_str()),
        (200, Some("ok")),
        "{}",
        l.body
    );
    let row = &l.json["plugins"][0];
    assert_eq!(
        (
            row["name"].as_str(),
            row["on"].as_bool(),
            row["runs"].as_str()
        ),
        (Some("aib-skipstop"), Some(false), Some("off")),
        "{}",
        l.body
    );
    let s = act(
        &d,
        Some(&page),
        json!({"type": "plugin_set", "name": "aib-skipstop", "on": true}),
    )
    .await;
    assert_eq!(
        (
            s.code,
            s.json["plugins"][0]["on"].as_bool(),
            s.json["plugins"][0]["runs"].as_str()
        ),
        (200, Some(true), Some("ok")),
        "{}",
        s.body
    );
    let s = act(
        &d,
        Some(&page),
        json!({"type": "plugin_set", "name": "nothing-here", "on": false}),
    )
    .await;
    assert_eq!(
        (s.code, s.json["code"].as_str()),
        (404, Some("no_such_plugin")),
        "{}",
        s.body
    );
    let s = act(
        &d,
        None,
        json!({"type": "plugin_set", "name": "aib-skipstop", "on": false}),
    )
    .await;
    assert!(s.code == 401 || s.code == 403, "{} {}", s.code, s.body);
    let l = act(&d, Some(&page), json!({"type": "plugin_list"})).await;
    assert_eq!(
        l.json["plugins"][0]["on"], true,
        "a call with no credential moved the switch: {}",
        l.body
    );

    // a user folder page (a page an AI may have written) may not move a switch, and a seat's
    // own token may not either: the switch stays where the built-in page put it
    let user = d.page_form(true, "&to=user").await;
    let off = json!({"type": "plugin_set", "name": "aib-skipstop", "on": false});
    let r = act(&d, Some(&user), off.clone()).await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (403, Some("page_only")),
        "{}",
        r.body
    );
    let (seat, _) = d.open("safe", &harness::seat_dir(dir.path(), "a")).await;
    let tok = aibd::seat_token::tokens().issue(&seat).unwrap();
    let r = act(&d, Some(&tok), off).await;
    assert!(
        r.code >= 400,
        "a seat's token switched a plugin: {} {}",
        r.code,
        r.body
    );
    let inst = json!({"type": "marketplace_install", "name": "aib-skipstop"});
    let r = act(&d, Some(&user), inst.clone()).await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (403, Some("page_only")),
        "{}",
        r.body
    );
    let r = act(&d, Some(&tok), inst).await;
    assert!(
        r.code >= 400,
        "a seat's token installed a plugin: {} {}",
        r.code,
        r.body
    );
    let l = act(&d, Some(&page), json!({"type": "plugin_list"})).await;
    assert_eq!(l.json["plugins"][0]["on"], true, "{}", l.body);

    // without the page's credential: not answered
    let r = act(&d, None, ask.clone()).await;
    assert!(r.code == 401 || r.code == 403, "{} {}", r.code, r.body);
    assert!(r.json.get("saved").is_none(), "{}", r.body);
}
