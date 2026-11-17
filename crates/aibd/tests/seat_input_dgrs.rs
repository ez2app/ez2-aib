#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! S89-dgrs (Andy 11-18 02:5x 「全開」, QUEUE-focus-519d0-1118 r4 §1): with this machine's switch
//! `AIBD_SEAT_INPUT_DGRS=1` a dgrs seat takes a confirmed page's text too, journaled with its mode
//! and verified_person false. Its own process: the switch is read from the environment at start
//! (tests/seat_input.rs keeps it off and checks dgrs is refused).

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{log_with, seat_dir, text_of, Daemon};

#[tokio::test(flavor = "multi_thread")]
async fn with_the_switch_on_a_dgrs_seat_takes_input_and_says_so() {
    // every test in this file sets the same value
    std::env::set_var("AIBD_SEAT_INPUT_DGRS", "1");
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cwd = seat_dir(dir.path(), "seat");
    let (seat, _) = d.open("dgrs", &cwd).await;
    let cred = d.page(true).await;
    let r = d.act(Some(&cred), &seat, "dgrs hi").await;
    assert_eq!(r.code, 200, "{}", r.body);
    let log = log_with(&cwd, 1);
    assert_eq!(log.len(), 1);
    assert_eq!(text_of(&log[0].1), "dgrs hi");
    // the start-up token is still refused
    assert_eq!(d.act(None, &seat, "token").await.code, 403);
    let r = d.call("GET", "/api/v1/events?cursor=0", None, None).await;
    let ev = r.json["events"]
        .as_array()
        .unwrap()
        .iter()
        .find(|e| e["type"] == "seat.input")
        .cloned()
        .unwrap();
    assert_eq!(ev["payload"]["mode"], "dgrs");
    assert_eq!(ev["payload"]["verified_person"], false);
    d.shutdown().await;
}
