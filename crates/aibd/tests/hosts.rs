#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! GET /api/v1/hosts (N-workspace wave 3, 7b81e 2; a9136 11-18 12:03): the hosts file
//! `aib-lease hosts` reads (`AIB_HOSTS_FILE`, its own process), each machine by nickname only;
//! one answer kept a minute; the built-in page only.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};

#[tokio::test(flavor = "multi_thread")]
async fn the_page_sees_each_machine_by_nickname_and_one_answer_a_minute() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("hosts.json");
    // .invalid never resolves (RFC 6761): ssh fails at once, the machine is offline
    std::fs::write(
        &file,
        r#"{"hosts": [{"name": "nick", "ssh": "probe-user@nohost.invalid"}]}"#,
    )
    .unwrap();
    std::env::set_var("AIB_HOSTS_FILE", &file);
    let mut d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;

    let r = d.call("GET", "/api/v1/hosts", Some(&page), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["source"], "aib-lease");
    assert_eq!(r.json["hosts"][0]["name"], "nick");
    assert_eq!(r.json["hosts"][0]["state"], "offline", "{}", r.body);
    assert!(
        !r.body.contains("nohost")
            && !r.body.contains("probe-user")
            && !r.body.contains("hosts.json"),
        "reason: where_shown {}",
        r.body
    );
    let asof = r.json["asof"].clone();

    // the next request within the minute gets the same answer, not a new round
    std::fs::write(&file, r#"{"hosts": []}"#).unwrap();
    let r = d.call("GET", "/api/v1/hosts", Some(&page), None).await;
    assert_eq!(r.json["asof"], asof, "reason: asked_again {}", r.body);
    assert_eq!(r.json["hosts"].as_array().unwrap().len(), 1);

    // a user folder page and a seat token do not see the machines
    let user = d.page_form(true, "&to=user").await;
    let r = d.call("GET", "/api/v1/hosts", Some(&user), None).await;
    assert_eq!(r.code, 403, "reason: user_page_sees {}", r.body);
    assert_eq!(r.json["code"], "hosts_built_in_page_only");
    let (seat, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let tok = aibd::seat_token::tokens().issue(&seat).unwrap();
    let r = d.call("GET", "/api/v1/hosts", Some(&tok), None).await;
    assert_ne!(r.code, 200, "reason: seat_token_sees {}", r.body);
    d.shutdown().await;
}
