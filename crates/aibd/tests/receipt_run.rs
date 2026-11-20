#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![cfg(unix)]

//! N-webonly 第 4 件, the aibd ↔ page half end to end: a paired page asks for a receipt of a
//! head, reads where it stands and withdraws it. aibd as shipped runs nothing (`no_host`); with
//! a runner behind the trait a run goes running → done only when aibd itself reads the `.out`
//! green. One test: the runner is one per process.

#[path = "support/seat_input_harness.rs"]
mod harness;
use aibd::receipt_run::{Ask, ReceiptRunner, Start};
use harness::{seat_dir, Daemon};
use serde_json::json;
use std::sync::{Arc, Mutex};

const H: &str = "6f547e599826ea87ecaa770771fefcd188c59cd4";

#[derive(Default)]
struct Fake {
    out: Mutex<Option<Vec<u8>>>,
    asked: Mutex<Vec<Ask>>,
    cancelled: Mutex<Vec<String>>,
    /// Whether aibd held its list while it called (5d33a L2: never).
    held: Mutex<Vec<bool>>,
}

impl ReceiptRunner for Fake {
    fn start(&self, ask: &Ask) -> Start {
        self.held
            .lock()
            .unwrap()
            .push(aibd::receipt_run::file_held());
        self.asked.lock().unwrap().push(ask.clone());
        Start::Started {
            host: "t1".into(),
            knives_sent: 0,
        }
    }
    fn read(&self, _run_id: &str) -> Option<Vec<u8>> {
        self.held
            .lock()
            .unwrap()
            .push(aibd::receipt_run::file_held());
        self.out.lock().unwrap().clone()
    }
    fn cancel(&self, run_id: &str) {
        self.held
            .lock()
            .unwrap()
            .push(aibd::receipt_run::file_held());
        self.cancelled.lock().unwrap().push(run_id.to_owned());
    }
}

fn green() -> Vec<u8> {
    let body = format!(
        "== HEAD={H}\nRC fmt=0\nRC anchors=0\nRC clippy=0\nRC winclippy=0\nRC test=0\n\
         TESTS-TOTAL suites=3 passed=40 failed=0 ignored=0\nHEAD-END {H}\nHEAD {H}\n\
         HEAD-END {H}\nEND x\n"
    );
    aibd::receipt_check::seal(body.as_bytes())
}

#[tokio::test(flavor = "multi_thread")]
async fn a_page_asks_for_a_receipt_reads_it_and_withdraws_it() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let (seat, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let cred = d.page(true).await;
    let code = &seat[seat.len() - 5..];
    let act = |cred: String, body: serde_json::Value| {
        let d = &d;
        async move {
            d.call("POST", "/api/v1/act", Some(&cred), Some(&body.to_string()))
                .await
        }
    };
    let ask = json!({"type": "receipt_request", "seat_id": seat, "head": H, "scope": "full"});

    // as shipped: nothing is run anywhere
    let r = act(cred.clone(), ask.clone()).await;
    assert_eq!(r.code, 503, "reason: ran_with_no_runner {}", r.body);
    assert_eq!(r.json["code"], "no_host", "{}", r.body);
    // a head is 40 hex; a run id has its shape; an unknown run is not there
    let mut bad = ask.clone();
    bad["head"] = json!("main");
    let r = act(cred.clone(), bad).await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (400, Some("bad_head")),
        "{}",
        r.body
    );
    let first = format!("web-{code}-{}", &H[..9]);
    let r = act(
        cred.clone(),
        json!({"type": "receipt_read", "run_id": first}),
    )
    .await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (404, Some("no_such_run")),
        "{}",
        r.body
    );
    let r = act(
        cred.clone(),
        json!({"type": "receipt_read", "run_id": "x; id"}),
    )
    .await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (400, Some("bad_run_id")),
        "{}",
        r.body
    );
    // a seat's token and an unconfirmed page ask for nothing
    let tok = aibd::seat_token::tokens().issue(&seat).unwrap();
    let r = act(tok, ask.clone()).await;
    assert!(r.code >= 400, "reason: seat_token_asked {}", r.body);
    let unconfirmed = d.page(false).await;
    let r = act(unconfirmed, ask.clone()).await;
    assert_eq!(r.code, 403, "reason: unconfirmed_page_asked {}", r.body);

    // with a runner behind the trait
    let fake = Arc::new(Fake::default());
    aibd::receipt_run::set_runner(fake.clone());
    let r = act(cred.clone(), ask.clone()).await;
    assert_eq!(r.code, 201, "reason: not_started {}", r.body);
    assert_eq!(r.json["run_id"], first.as_str(), "{}", r.body);
    assert_eq!(r.json["state"], "running", "{}", r.body);
    assert_eq!(r.json["host"], "t1");
    assert_eq!(fake.asked.lock().unwrap()[0].head, H);
    // no .out yet: still running, not green
    let r = act(
        cred.clone(),
        json!({"type": "receipt_read", "run_id": first}),
    )
    .await;
    assert_eq!(
        (r.code, r.json["state"].as_str()),
        (200, Some("running")),
        "{}",
        r.body
    );
    assert_eq!(r.json["green"], false);
    // a .out that does not read green ends the run red, with why
    *fake.out.lock().unwrap() = Some(b"KNIVES-OK\n".to_vec());
    let r = act(
        cred.clone(),
        json!({"type": "receipt_read", "run_id": first}),
    )
    .await;
    assert_eq!(
        r.json["state"], "red",
        "reason: unread_receipt_done {}",
        r.body
    );
    assert_eq!(r.json["reasons"][0], "seal_missing", "{}", r.body);
    assert!(
        !r.body.contains("KNIVES-OK"),
        "reason: receipt_line_shown {}",
        r.body
    );
    let r = act(
        cred.clone(),
        json!({"type": "receipt_withdraw", "run_id": first}),
    )
    .await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (409, Some("already_done")),
        "{}",
        r.body
    );

    // the same head again: a new run id; a green .out ends it done
    *fake.out.lock().unwrap() = None;
    let r = act(cred.clone(), ask.clone()).await;
    let second = format!("{first}-2");
    assert_eq!(
        r.json["run_id"],
        second.as_str(),
        "reason: run_id_reused {}",
        r.body
    );
    *fake.out.lock().unwrap() = Some(green());
    let r = act(
        cred.clone(),
        json!({"type": "receipt_read", "run_id": second}),
    )
    .await;
    assert_eq!(r.json["state"], "done", "reason: green_not_done {}", r.body);
    assert_eq!(r.json["green"], true, "{}", r.body);
    assert_eq!(r.json["tests_total"]["passed"], 40, "{}", r.body);

    // a run under way is withdrawn and the runner is told
    *fake.out.lock().unwrap() = None;
    let r = act(cred.clone(), ask.clone()).await;
    let third = format!("{first}-3");
    assert_eq!(r.json["run_id"], third.as_str(), "{}", r.body);
    let r = act(
        cred.clone(),
        json!({"type": "receipt_withdraw", "run_id": third}),
    )
    .await;
    assert_eq!(
        (r.code, r.json["state"].as_str()),
        (200, Some("withdrawn")),
        "{}",
        r.body
    );
    assert_eq!(
        *fake.cancelled.lock().unwrap(),
        [third.clone()],
        "reason: runner_not_told"
    );
    // a withdrawn run is not read from the runner again
    *fake.out.lock().unwrap() = Some(green());
    let r = act(
        cred.clone(),
        json!({"type": "receipt_read", "run_id": third}),
    )
    .await;
    assert_eq!(
        r.json["state"], "withdrawn",
        "reason: withdrawn_run_read {}",
        r.body
    );
    // the runner was never called with aibd's list held (a machine that hangs holds up nothing)
    let held = fake.held.lock().unwrap().clone();
    assert!(held.len() >= 7, "reason: runner_not_called {held:?}");
    assert!(
        !held.contains(&true),
        "reason: runner_called_in_lock {held:?}"
    );
    d.shutdown().await;
}
