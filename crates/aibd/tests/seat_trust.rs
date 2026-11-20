#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! T1 end to end without a fingerprint reader (DESIGN-trust-t1): the built-in page asks, and
//! with no fingerprint to ask nothing is written; a seat's own token can neither give nor take
//! a trust; a seat trusted now is not asked again and its end is not moved; the page takes a
//! trust back without a fingerprint and the seat's next gh_view is refused.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon, Reply};
use serde_json::{json, Value};

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

fn now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs()
}

#[tokio::test(flavor = "multi_thread")]
async fn a_trust_is_given_by_the_owners_finger_only_and_taken_back_by_the_page() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let data = dir.path().join("data");
    let file = data.join("trusted").join("seats.json");
    let page = d.page(true).await;
    let (seat, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let tok = aibd::seat_token::tokens().issue(&seat).unwrap();
    let trust = json!({"type": "seat_trust", "seat_id": seat});
    let why = |r: &Reply| r.json["reason"].as_str().map(str::to_owned);

    // the switch is off: no window is asked for
    let r = act(&d, &page, trust.clone()).await;
    assert_eq!(
        (r.code, why(&r).as_deref()),
        (403, Some("trusted_seats_off")),
        "{}",
        r.body
    );
    std::fs::write(data.join("bridge.json"), r#"{"trusted_seats": true}"#).unwrap();

    // what the page may not say
    for (body, code) in [
        (
            json!({"type": "seat_trust", "seat_id": seat, "until": now() + 999_999}),
            400,
        ),
        (
            json!({"type": "seat_trust", "seat_id": seat, "hours": 99}),
            400,
        ),
        (json!({"type": "seat_trust", "seat_id": "../x"}), 400),
        (
            json!({"type": "seat_trust", "seat_id": "seat_nosuchseat00"}),
            404,
        ),
    ] {
        let r = act(&d, &page, body.clone()).await;
        assert_eq!(r.code, code, "reason: took_{body} {}", r.body);
    }

    // no fingerprint can be asked here: refused, and nothing is written
    let r = act(&d, &page, trust.clone()).await;
    assert_eq!(
        (r.code, why(&r).as_deref()),
        (403, Some("owner_check_unavailable")),
        "{}",
        r.body
    );
    let r = act(
        &d,
        &page,
        json!({"type": "seat_trust", "seat_id": seat, "ticket": "ot_made_up"}),
    )
    .await;
    assert_eq!(r.code, 403, "reason: a_made_up_ticket_passed {}", r.body);
    assert!(
        !file.exists(),
        "reason: trust_written_without_a_fingerprint"
    );

    // a seat cannot give itself a trust
    let r = act(&d, &tok, trust.clone()).await;
    assert!(r.code >= 400, "reason: seat_trusted_itself {}", r.body);
    assert!(!file.exists(), "reason: trust_written_by_a_seat");

    // trusted now (as the fingerprint would have written it): not asked again, the end not moved
    let until = now() + 600;
    std::fs::create_dir_all(file.parent().unwrap()).unwrap();
    let written = json!({"seats": {seat.clone(): until}}).to_string();
    std::fs::write(&file, &written).unwrap();
    let r = act(&d, &page, trust.clone()).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        (
            r.json["already"].as_bool(),
            r.json["trusted_until"].as_u64()
        ),
        (Some(true), Some(until)),
        "reason: asked_again_or_end_moved {}",
        r.body
    );
    assert_eq!(
        std::fs::read_to_string(&file).unwrap(),
        written,
        "reason: file_rewritten_for_already"
    );

    // a seat cannot take a trust back either (its own or another's): only the page
    let r = act(&d, &tok, json!({"type": "seat_untrust", "seat_id": seat})).await;
    assert!(r.code >= 400, "reason: seat_untrusted {}", r.body);
    assert_eq!(
        std::fs::read_to_string(&file).unwrap(),
        written,
        "reason: seat_changed_the_file"
    );

    // the page takes it back, no fingerprint; the seat's next ask is refused
    let r = act(&d, &page, json!({"type": "seat_untrust", "seat_id": seat})).await;
    assert_eq!(
        (r.code, r.json["was_trusted"].as_bool()),
        (200, Some(true)),
        "{}",
        r.body
    );
    let left: Value = serde_json::from_str(&std::fs::read_to_string(&file).unwrap()).unwrap();
    assert_eq!(left, json!({"seats": {}}), "reason: trust_left_in_the_file");
    let r = act(
        &d,
        &tok,
        json!({"type": "gh_view", "what": "pr", "number": "1"}),
    )
    .await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (403, Some("seat_not_trusted")),
        "reason: asked_after_untrust {}",
        r.body
    );
    let r = act(&d, &page, json!({"type": "seat_untrust", "seat_id": seat})).await;
    assert_eq!(
        (r.code, r.json["was_trusted"].as_bool()),
        (200, Some(false)),
        "{}",
        r.body
    );

    // journaled once: who was untrusted, by which page; never a path or a remote
    let ev = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    let events = ev.json["events"].as_array().unwrap();
    let un: Vec<&Value> = events
        .iter()
        .filter(|e| e["type"] == "seat.untrusted")
        .collect();
    assert_eq!(un.len(), 1, "reason: untrust_journaled_{}", un.len());
    assert_eq!(un[0]["payload"]["seat"], seat.as_str());
    assert!(
        un[0]["payload"]["by"]
            .as_str()
            .is_some_and(|b| b.starts_with("page:")),
        "reason: no_by {}",
        un[0]
    );
    assert!(
        !events.iter().any(|e| e["type"] == "seat.trusted"),
        "reason: trusted_journaled_without_a_fingerprint"
    );
    d.shutdown().await;
}
