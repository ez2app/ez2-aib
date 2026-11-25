#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! `POST /api/v1/seats/open` and the seat rows on a real aibd (127.0.0.1:0, temp data dir,
//! a temp persona cards file): B4-1b-1 (IFACE-b4-1b-seat-open-a9136-1114.md r1–r3).
//! B4-1b-2b starts the seat's provider: the tests that start a process stand `/bin/sleep`
//! in for `claude` (unix); the rest start nothing and run on every OS.
// test deadlines only, not daemon timers (ADR 0005 named exception)
#![allow(clippy::disallowed_methods)]

use aibd::config::Config;
use aibd::server::{self, Started};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::Path;
use std::time::Duration;

struct Daemon {
    addr: SocketAddr,
    token: String,
    stop: Option<tokio::sync::oneshot::Sender<()>>,
    h: Option<tokio::task::JoinHandle<Result<(), server::StartError>>>,
}

async fn start(dir: &Path, cards: Option<std::path::PathBuf>) -> Daemon {
    // the default policy allows nothing: an open answers session_error (B4-1b-2b)
    start_with(dir, cards, Default::default(), 2).await
}

async fn start_with(
    dir: &Path,
    cards: Option<std::path::PathBuf>,
    session_policy: aibd::allowlist::SessionPolicy,
    max_sessions: usize,
) -> Daemon {
    let (stop_tx, stop_rx) = tokio::sync::oneshot::channel::<()>();
    let (st_tx, st_rx) = tokio::sync::oneshot::channel::<Started>();
    let c = Config {
        bind: "127.0.0.1:0".parse().unwrap(),
        data_dir: dir.to_path_buf(),
        allowed_origins: vec![],
        max_sessions,
        session_policy,
        persona_cards: cards,
    };
    let h = tokio::spawn(async move {
        server::run(
            c,
            async {
                let _ = stop_rx.await;
            },
            move |s| {
                let _ = st_tx.send(s);
            },
        )
        .await
    });
    let st = match tokio::time::timeout(Duration::from_secs(20), st_rx)
        .await
        .unwrap()
    {
        Ok(st) => st,
        // the server ended before it said it started: show why instead of RecvError
        // bounded: a server that dropped the sender but still runs must not hang the test (ebb36 #683 N1)
        Err(_) => panic!(
            "aibd did not start: {:?}",
            tokio::time::timeout(Duration::from_secs(5), h).await
        ),
    };
    Daemon {
        addr: st.addr,
        token: std::fs::read_to_string(&st.token_path).unwrap(),
        stop: Some(stop_tx),
        h: Some(h),
    }
}

struct Reply {
    code: u16,
    head: String,
    json: serde_json::Value,
}

impl Daemon {
    /// One raw HTTP/1.1 request (blocking; run it on a blocking thread).
    fn raw(&self, method: &str, path: &str, body: Option<&str>, extra: &str) -> Reply {
        let port = self.addr.port();
        let mut req = format!(
            "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\
             Authorization: Bearer {}\r\n{extra}",
            self.token
        );
        match body {
            Some(b) => {
                req += &format!(
                    "Content-Type: application/json\r\nContent-Length: {}\r\n\r\n{b}",
                    b.len()
                )
            }
            None => req += "Content-Length: 0\r\n\r\n",
        }
        let mut s = TcpStream::connect(self.addr).unwrap();
        s.set_read_timeout(Some(Duration::from_secs(30))).unwrap();
        s.write_all(req.as_bytes()).unwrap();
        let mut out = String::new();
        let _ = s.read_to_string(&mut out);
        let code: u16 = out.get(9..12).and_then(|c| c.parse().ok()).unwrap_or(0);
        let (head, body) = out.split_once("\r\n\r\n").unwrap_or((&out, ""));
        Reply {
            code,
            head: head.to_ascii_lowercase(),
            json: serde_json::from_str(body).unwrap_or(serde_json::Value::Null),
        }
    }

    async fn call(&self, method: &str, path: &str, body: Option<&str>, extra: &str) -> Reply {
        let (addr, token) = (self.addr, self.token.clone());
        let (m, p, b, e) = (
            method.to_string(),
            path.to_string(),
            body.map(str::to_string),
            extra.to_string(),
        );
        tokio::task::spawn_blocking(move || {
            let d = Daemon {
                addr,
                token,
                stop: None,
                h: None,
            };
            d.raw(&m, &p, b.as_deref(), &e)
        })
        .await
        .unwrap()
    }

    async fn shutdown(&mut self) {
        self.stop.take().unwrap().send(()).unwrap();
        self.h.take().unwrap().await.unwrap().unwrap();
    }
}

const SHA_A: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const SHA_B: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

/// An `aib-cards/v2` file with the main-line card and one more.
fn cards_file(dir: &Path, main_name: &str, main_sha: &str) -> std::path::PathBuf {
    let p = dir.join("cards.json");
    let doc = serde_json::json!({
        "schema_version": "aib-cards/v2",
        "cards": {
            "mnnftg": {"uid": "mnnftg", "display_name": main_name, "persona_sha": main_sha},
            "un0eba": {"uid": "un0eba", "display_name": "AIB2主線核心架構師", "persona_sha": SHA_B},
        }
    });
    std::fs::write(&p, doc.to_string()).unwrap();
    p
}

fn cwd() -> String {
    std::env::temp_dir().to_str().unwrap().to_string()
}

fn open_body(uid: &str, extra: &str) -> String {
    let cwd = serde_json::Value::String(cwd()).to_string();
    format!(r#"{{"persona_uid":"{uid}","provider":"claude","mode":"dgrs","cwd":{cwd}{extra}}}"#)
}

fn key(k: &str) -> String {
    format!("Idempotency-Key: {k}\r\n")
}

async fn events_of(d: &Daemon, id: &str) -> Vec<serde_json::Value> {
    let ev = d
        .call(
            "GET",
            &format!("/api/v1/events?cursor=0&aggregate_id={id}"),
            None,
            "",
        )
        .await;
    ev.json["events"].as_array().cloned().unwrap_or_default()
}

/// A seat opened by persona: 201 with the seat (persona, provider, mode, display) and
/// no session; one `seat.opened` event that names the card's sha and never the cwd;
/// the seat is listed and read back after a restart; its name follows a rename.
#[tokio::test(flavor = "multi_thread")]
async fn a_seat_opens_by_persona_and_shows_persona_provider_and_mode() {
    let dir = tempfile::tempdir().unwrap();
    let cards = cards_file(dir.path(), "AIB 2.0 主軸統籌", SHA_A);
    let mut d = start(dir.path(), Some(cards.clone())).await;
    let r = d
        .call(
            "POST",
            "/api/v1/seats/open",
            Some(&open_body(
                "mnnftg",
                r#","model":"opus","cabin":"dab9","axis_id":"aib2""#,
            )),
            &key("open-1"),
        )
        .await;
    assert_eq!(r.code, 201, "{}", r.json);
    assert!(r.json["session"].is_null(), "nothing is listed to start");
    assert_eq!(r.json["session_error"]["code"], "program_not_allowed");
    let s = &r.json["seat"];
    let id = s["seat_id"].as_str().unwrap().to_string();
    assert_eq!(s["name"], "AIB 2.0 主軸統籌");
    assert_eq!(
        s["persona"],
        serde_json::json!({"uid": "mnnftg", "name": "AIB 2.0 主軸統籌", "persona_sha": SHA_A})
    );
    assert_eq!(
        (&s["provider"], &s["mode"], &s["model"], &s["model_pinned"]),
        (
            &serde_json::json!("claude"),
            &serde_json::json!("dgrs"),
            &serde_json::json!("opus"),
            &serde_json::json!(true)
        )
    );
    assert_eq!(
        (&s["cabin"], &s["axis_id"]),
        (&serde_json::json!("dab9"), &serde_json::json!("aib2"))
    );
    assert_eq!(s["display"], "AIB 2.0 主軸統籌 · claude · dgrs");
    let text = s.to_string();
    assert!(
        !text.contains("cwd") && !text.contains(&cwd()),
        "no cwd in a seat: {text}"
    );

    let evs = events_of(&d, &id).await;
    assert_eq!(evs.len(), 1, "{evs:?}");
    assert_eq!(evs[0]["type"], "seat.opened");
    let p = &evs[0]["payload"];
    // IFACE §2: the persona by uid and card version, never its name (read when shown)
    assert_eq!(
        (&p["persona_uid"], &p["persona_sha"]),
        (&serde_json::json!("mnnftg"), &serde_json::json!(SHA_A))
    );
    assert!(
        p.get("persona").is_none() && !p.to_string().contains("主軸統籌"),
        "{p}"
    );
    assert_eq!(p["seat_id"], id.as_str());
    assert!(
        !p.to_string().contains(&cwd()),
        "no cwd in the journal: {p}"
    );

    // listed with the same fields
    let all = d.call("GET", "/api/v1/seats", None, "").await;
    assert_eq!(
        all.json["seats"][0]["display"],
        "AIB 2.0 主軸統籌 · claude · dgrs"
    );
    d.shutdown().await;

    // read back after a restart, under the card's new name (read when shown, 7b81e §2-1)
    cards_file(dir.path(), "主軸統籌", SHA_B);
    let mut d = start(dir.path(), Some(cards)).await;
    let one = d
        .call("GET", &format!("/api/v1/seats/{id}"), None, "")
        .await;
    assert_eq!(one.code, 200, "{}", one.json);
    assert_eq!(one.json["persona"]["name"], "主軸統籌");
    assert_eq!(one.json["persona"]["persona_sha"], SHA_B);
    assert_eq!(
        one.json["name"], "AIB 2.0 主軸統籌",
        "the seat's own name is kept"
    );
    assert_eq!(one.json["display"], "主軸統籌 · claude · dgrs");
    d.shutdown().await;
}

/// Andy 11-14 10:44: the second seat of a persona shows its seat code; the first does
/// not; another persona's seat does not; a plain seat shows its name.
#[tokio::test(flavor = "multi_thread")]
async fn the_second_seat_of_a_persona_shows_its_seat_code() {
    let dir = tempfile::tempdir().unwrap();
    let cards = cards_file(dir.path(), "AIB 2.0 主軸統籌", SHA_A);
    let mut d = start(dir.path(), Some(cards)).await;
    let mut opened = Vec::new();
    for (k, uid) in [("k1", "mnnftg"), ("k2", "un0eba"), ("k3", "mnnftg")] {
        let r = d
            .call(
                "POST",
                "/api/v1/seats/open",
                Some(&open_body(uid, "")),
                &key(k),
            )
            .await;
        assert_eq!(r.code, 201, "{}", r.json);
        opened.push(r.json["seat"]["display"].as_str().unwrap().to_string());
        tokio::time::sleep(Duration::from_millis(5)).await; // distinct created_at
    }
    let plain = d
        .call("POST", "/api/v1/seats", Some(r#"{"name":"手開的席"}"#), "")
        .await;
    assert_eq!(plain.code, 201);
    let all = d.call("GET", "/api/v1/seats", None, "").await;
    let rows: Vec<&str> = all.json["seats"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s["display"].as_str().unwrap())
        .collect();
    let third = all.json["seats"][2]["seat_id"].as_str().unwrap();
    let code = &third["seat_".len().."seat_".len() + 5];
    assert_eq!(
        rows,
        [
            "AIB 2.0 主軸統籌 · claude · dgrs".to_string(),
            "AIB2主線核心架構師 · claude · dgrs".to_string(),
            format!("AIB 2.0 主軸統籌 · claude · dgrs · {code}"),
            "手開的席".to_string(),
        ]
    );
    // the answer to each open already shows what the list shows (043dd M3)
    assert_eq!(opened, rows[..3]);
    // a single seat read shows the same suffix as the list
    let one = d
        .call("GET", &format!("/api/v1/seats/{third}"), None, "")
        .await;
    assert_eq!(one.json["display"], rows[2]);
    d.shutdown().await;
}

/// dry_run answers the plan and writes nothing: no seat, no event (519d0 r2 (b)).
#[tokio::test(flavor = "multi_thread")]
async fn a_dry_run_answers_the_plan_and_writes_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let cards = cards_file(dir.path(), "AIB 2.0 主軸統籌", SHA_A);
    let mut d = start(dir.path(), Some(cards)).await;
    let r = d
        .call(
            "POST",
            "/api/v1/seats/open",
            Some(&open_body("mnnftg", r#","new":false,"dry_run":true"#)),
            "",
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.json);
    assert_eq!(r.json["plan"]["persona"]["persona_sha"], SHA_A);
    assert_eq!(r.json["plan"]["provider"], "claude");
    let all = d.call("GET", "/api/v1/seats", None, "").await;
    assert_eq!(all.json["seats"], serde_json::json!([]));
    let ev = d.call("GET", "/api/v1/events?cursor=0", None, "").await;
    assert_eq!(
        ev.json["events"],
        serde_json::json!([]),
        "a dry run sends no event"
    );
    d.shutdown().await;
}

/// The refusals: each writes nothing.
#[tokio::test(flavor = "multi_thread")]
async fn bad_opens_are_refused_and_write_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let cards = cards_file(dir.path(), "AIB 2.0 主軸統籌", SHA_A);
    let mut d = start(dir.path(), Some(cards)).await;
    // 519d0 IFACE r5: cwd must be an existing folder
    let a_file = dir.path().join("a-file");
    std::fs::write(&a_file, "x").unwrap();
    let with_cwd = |p: &std::path::Path| {
        serde_json::json!({"persona_uid": "mnnftg", "provider": "claude", "mode": "dgrs", "cwd": p.to_str().unwrap()})
            .to_string()
    };
    let rows: Vec<(String, &str, u16, &str)> = vec![
        (with_cwd(&dir.path().join("missing")), "k", 400, "cwd"),
        (with_cwd(&a_file), "l", 400, "cwd"),
        // no key: a retry could open a second seat
        (open_body("mnnftg", ""), "", 400, "key_missing"),
        (open_body("nosuch", ""), "a", 400, "persona_missing"),
        (open_body("AIB 2.0 主軸統籌", ""), "b", 400, "persona_shape"),
        (open_body("mnnftg", r#","model":"!""#), "c", 400, "model"),
        (open_body("mnnftg", r#","cabin":"Dab9""#), "d", 400, "cabin"),
        (open_body("mnnftg", r#","role":"dev""#), "e", 400, "body"),
        (
            open_body("mnnftg", "").replace("\"dgrs\"", "\"root\""),
            "f",
            400,
            "mode",
        ),
        (
            open_body("mnnftg", "").replace("\"claude\"", "\"nope\""),
            "g",
            400,
            "provider",
        ),
        (
            r#"{"persona_uid":"mnnftg","provider":"claude","mode":"dgrs","cwd":"rel/dir"}"#.into(),
            "h",
            400,
            "cwd",
        ),
        (open_body("mnnftg", r#","new":true"#), "i", 400, "new"),
        (
            open_body("mnnftg", r#","axis_id":"-x""#),
            "j",
            400,
            "axis_id",
        ),
    ];
    // every refusal is invalid_request with a stable reason (043dd M1; IFACE r5)
    for (body, k, code, reason) in &rows {
        let extra = if k.is_empty() { String::new() } else { key(k) };
        let r = d
            .call("POST", "/api/v1/seats/open", Some(body), &extra)
            .await;
        assert_eq!(
            (r.code, r.json["code"].as_str(), r.json["reason"].as_str()),
            (*code, Some("invalid_request"), Some(*reason)),
            "{body} {}",
            r.json
        );
    }
    let all = d.call("GET", "/api/v1/seats", None, "").await;
    assert_eq!(all.json["seats"], serde_json::json!([]));
    let ev = d.call("GET", "/api/v1/events?cursor=0", None, "").await;
    assert_eq!(ev.json["events"], serde_json::json!([]));
    d.shutdown().await;
}

/// Unreadable cards are 503 persona_source_unavailable, never "no such persona"; the
/// seat list still answers (names null).
#[tokio::test(flavor = "multi_thread")]
async fn cards_that_cannot_be_read_are_503_and_the_list_still_answers() {
    let dir = tempfile::tempdir().unwrap();
    let cards = cards_file(dir.path(), "AIB 2.0 主軸統籌", SHA_A);
    let mut d = start(dir.path(), Some(cards.clone())).await;
    let r = d
        .call(
            "POST",
            "/api/v1/seats/open",
            Some(&open_body("mnnftg", "")),
            &key("ok"),
        )
        .await;
    assert_eq!(r.code, 201, "{}", r.json);
    for broken in ["{", r#"{"schema_version":"aib-cards/v1","cards":{}}"#] {
        std::fs::write(&cards, broken).unwrap();
        let r = d
            .call(
                "POST",
                "/api/v1/seats/open",
                Some(&open_body("mnnftg", "")),
                &key("x"),
            )
            .await;
        assert_eq!(r.code, 503, "{broken}: {}", r.json);
        assert_eq!(r.json["code"], "persona_source_unavailable");
        let all = d.call("GET", "/api/v1/seats", None, "").await;
        assert_eq!(all.code, 200);
        assert!(
            all.json["seats"][0]["persona"]["name"].is_null(),
            "{}",
            all.json
        );
        assert_eq!(all.json["seats"][0]["persona"]["uid"], "mnnftg");
        assert_eq!(
            all.json["seats"][0]["display"], "AIB 2.0 主軸統籌 · claude · dgrs",
            "the seat's own name stands in"
        );
    }
    std::fs::remove_file(&cards).unwrap();
    let r = d
        .call(
            "POST",
            "/api/v1/seats/open",
            Some(&open_body("mnnftg", "")),
            &key("y"),
        )
        .await;
    assert_eq!(r.code, 503, "{}", r.json);
    d.shutdown().await;
    // no cards file configured at all
    let mut d = start(dir.path(), None).await;
    let r = d
        .call(
            "POST",
            "/api/v1/seats/open",
            Some(&open_body("mnnftg", "")),
            &key("z"),
        )
        .await;
    assert_eq!(r.code, 503, "{}", r.json);
    d.shutdown().await;
}

/// The same key and body replay the first answer; the same key with another body is
/// 409; either way one seat. A retry is answered before the cards are read: cards
/// broken since the first answer still replay it (043dd L1).
#[tokio::test(flavor = "multi_thread")]
async fn a_retried_open_is_one_seat() {
    let dir = tempfile::tempdir().unwrap();
    let cards = cards_file(dir.path(), "AIB 2.0 主軸統籌", SHA_A);
    let mut d = start(dir.path(), Some(cards.clone())).await;
    let a = d
        .call(
            "POST",
            "/api/v1/seats/open",
            Some(&open_body("mnnftg", "")),
            &key("same"),
        )
        .await;
    assert_eq!(a.code, 201, "{}", a.json);
    let b = d
        .call(
            "POST",
            "/api/v1/seats/open",
            Some(&open_body("mnnftg", "")),
            &key("same"),
        )
        .await;
    assert_eq!(b.code, 201);
    assert_eq!(b.json, a.json);
    assert!(b.head.contains("idempotent-replayed: true"), "{}", b.head);
    std::fs::write(&cards, "{").unwrap();
    let b2 = d
        .call(
            "POST",
            "/api/v1/seats/open",
            Some(&open_body("mnnftg", "")),
            &key("same"),
        )
        .await;
    assert_eq!((b2.code, &b2.json), (201, &a.json), "a replay, not a 503");
    assert!(b2.head.contains("idempotent-replayed: true"), "{}", b2.head);
    let c = d
        .call(
            "POST",
            "/api/v1/seats/open",
            Some(&open_body("un0eba", "")),
            &key("same"),
        )
        .await;
    assert_eq!(c.code, 409, "{}", c.json);
    let all = d.call("GET", "/api/v1/seats", None, "").await;
    assert_eq!(
        all.json["seats"].as_array().unwrap().len(),
        1,
        "{}",
        all.json
    );
    d.shutdown().await;
}

/// 043dd #759 L1: a seat opened, the answer lost, the folder renamed or removed since:
/// the retry with the same key is the first answer again, not a 400. A new key is refused.
#[tokio::test(flavor = "multi_thread")]
async fn a_retry_after_the_folder_is_gone_is_still_a_replay() {
    let dir = tempfile::tempdir().unwrap();
    let cards = cards_file(dir.path(), "AIB 2.0 主軸統籌", SHA_A);
    let mut d = start(dir.path(), Some(cards)).await;
    let work = dir.path().join("work");
    std::fs::create_dir(&work).unwrap();
    let body = serde_json::json!({"persona_uid": "mnnftg", "provider": "claude", "mode": "dgrs", "cwd": work.to_str().unwrap()})
        .to_string();
    let a = d
        .call("POST", "/api/v1/seats/open", Some(&body), &key("w1"))
        .await;
    assert_eq!(a.code, 201, "{}", a.json);
    std::fs::remove_dir(&work).unwrap();
    let b = d
        .call("POST", "/api/v1/seats/open", Some(&body), &key("w1"))
        .await;
    assert_eq!((b.code, &b.json), (201, &a.json), "a replay, not a 400");
    assert!(b.head.contains("idempotent-replayed: true"), "{}", b.head);
    let c = d
        .call("POST", "/api/v1/seats/open", Some(&body), &key("w2"))
        .await;
    assert_eq!(
        (c.code, c.json["reason"].as_str()),
        (400, Some("cwd")),
        "{}",
        c.json
    );
    let all = d.call("GET", "/api/v1/seats", None, "").await;
    assert_eq!(
        all.json["seats"].as_array().unwrap().len(),
        1,
        "{}",
        all.json
    );
    d.shutdown().await;
}

/// 043dd #759 N1: a dry run is never a retry. With a key an open already used it still
/// answers the plan, not that open's 201.
#[tokio::test(flavor = "multi_thread")]
async fn a_dry_run_with_a_used_key_answers_the_plan() {
    let dir = tempfile::tempdir().unwrap();
    let cards = cards_file(dir.path(), "AIB 2.0 主軸統籌", SHA_A);
    let mut d = start(dir.path(), Some(cards)).await;
    let a = d
        .call(
            "POST",
            "/api/v1/seats/open",
            Some(&open_body("mnnftg", "")),
            &key("used"),
        )
        .await;
    assert_eq!(a.code, 201, "{}", a.json);
    let p = d
        .call(
            "POST",
            "/api/v1/seats/open",
            Some(&open_body("mnnftg", r#","dry_run":true"#)),
            &key("used"),
        )
        .await;
    assert_eq!(p.code, 200, "{}", p.json);
    assert_eq!(p.json["plan"]["persona"]["persona_sha"], SHA_A);
    assert!(!p.head.contains("idempotent-replayed"), "{}", p.head);
    let all = d.call("GET", "/api/v1/seats", None, "").await;
    assert_eq!(
        all.json["seats"].as_array().unwrap().len(),
        1,
        "{}",
        all.json
    );
    d.shutdown().await;
}

/// `claude` as the session policy lists it, standing in with `/bin/sleep` (native, no
/// interpreter): what it is given does not matter, only that it starts.
#[cfg(unix)]
fn claude_policy() -> aibd::allowlist::SessionPolicy {
    aibd::allowlist::SessionPolicy {
        programs: vec![
            aibd::allowlist::Program {
                name: "claude".into(),
                path: "/bin/sleep".into(),
                signer: None,
            },
            aibd::allowlist::Program {
                name: "sleep".into(),
                path: "/bin/sleep".into(),
                signer: None,
            },
        ],
        env: vec![],
        interpreters_ok: false,
    }
}

/// B4-1b-2b K2 / K4: the open starts the seat's provider over pipes in the same request
/// (the pipe argv, the program the policy lists), answers the running session, and a
/// retry with the same key answers that one session again and starts no second one.
#[cfg(unix)]
#[tokio::test(flavor = "multi_thread")]
async fn an_open_starts_the_provider_and_a_retry_starts_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let cards = cards_file(dir.path(), "AIB 2.0 主軸統籌", SHA_A);
    let mut d = start_with(dir.path(), Some(cards), claude_policy(), 2).await;
    let body = open_body("mnnftg", r#","model":"opus""#);
    let r = d
        .call("POST", "/api/v1/seats/open", Some(&body), &key("run-1"))
        .await;
    assert_eq!(r.code, 201, "{}", r.json);
    assert!(r.json["session_error"].is_null(), "{}", r.json);
    let seat = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let s = &r.json["session"];
    assert_eq!(s["state"], "running", "{s}");
    assert_eq!(s["seat_id"], seat.as_str());
    let argv: Vec<&str> = s["argv"]
        .as_array()
        .unwrap()
        .iter()
        .map(|a| a.as_str().unwrap())
        .collect();
    assert!(
        argv[0].ends_with("sleep"),
        "the listed path, not the name: {argv:?}"
    );
    for w in [
        "-p",
        "stream-json",
        "--session-id",
        "--dangerously-skip-permissions",
    ] {
        assert!(argv.contains(&w), "{w} in {argv:?}");
    }
    let sid = s["id"].as_str().unwrap().to_string();
    let again = d
        .call("POST", "/api/v1/seats/open", Some(&body), &key("run-1"))
        .await;
    assert_eq!(again.code, 201, "{}", again.json);
    assert!(again.head.contains("idempotent-replayed: true"));
    assert_eq!(
        again.json["session"]["id"],
        sid.as_str(),
        "the same session"
    );
    let listed = d
        .call("GET", &format!("/api/v1/seats/{seat}/sessions"), None, "")
        .await;
    let n = listed.json["sessions"]
        .as_array()
        .map(Vec::len)
        .unwrap_or_else(|| listed.json.as_array().map(Vec::len).unwrap_or(0));
    assert_eq!(n, 1, "one process for one key: {}", listed.json);
    d.shutdown().await;
}

/// S89-live-workdir (0eed7 autotest 0c976d68): opened without a folder, a seat works in its own
/// empty folder in aibd's data directory, never wherever the caller was; a given folder with
/// project settings a provider would load is refused and opens nothing.
#[cfg(unix)]
#[tokio::test(flavor = "multi_thread")]
async fn a_seat_without_a_folder_gets_its_own_and_a_project_folder_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let cards = cards_file(dir.path(), "AIB 2.0 主軸統籌", SHA_A);
    let mut d = start_with(dir.path(), Some(cards), claude_policy(), 2).await;
    let bare = r#"{"persona_uid":"mnnftg","provider":"claude","mode":"safe"}"#;
    let plan = d
        .call(
            "POST",
            "/api/v1/seats/open",
            Some(r#"{"persona_uid":"mnnftg","provider":"claude","mode":"safe","dry_run":true}"#),
            "",
        )
        .await;
    assert_eq!(plan.code, 200, "{}", plan.json);
    let want = aibd::folders::folder(dir.path(), "seat-work");
    assert!(
        plan.json["plan"]["cwd"]
            .as_str()
            .unwrap()
            .starts_with(want.to_str().unwrap()),
        "{}",
        plan.json
    );
    let r = d
        .call("POST", "/api/v1/seats/open", Some(bare), &key("own-1"))
        .await;
    assert_eq!(r.code, 201, "{}", r.json);
    let seat = r.json["seat"]["seat_id"].as_str().unwrap();
    let own = want.join(seat);
    assert!(own.is_dir(), "{own:?}");
    assert_eq!(std::fs::read_dir(&own).unwrap().count(), 0, "empty");
    // a folder a provider would load project settings from
    let project = dir.path().join("repo");
    std::fs::create_dir_all(project.join("sub")).unwrap();
    std::fs::write(project.join("CLAUDE.md"), "x").unwrap();
    let body = serde_json::json!({"persona_uid": "mnnftg", "provider": "claude", "mode": "safe",
                                  "cwd": project.join("sub").to_str().unwrap()})
    .to_string();
    let r = d
        .call("POST", "/api/v1/seats/open", Some(&body), &key("proj-1"))
        .await;
    assert_eq!(r.code, 400, "{}", r.json);
    assert_eq!(r.json["reason"], "cwd_has_project_settings");
    let seats = d.call("GET", "/api/v1/seats", None, "").await;
    assert_eq!(seats.json["seats"].as_array().map(Vec::len), Some(1));
    d.shutdown().await;
}

/// B4-1b-2b: a session that has to wait for a slot is answered `pending` (201: the seat
/// was made), and when it leaves the queue the gate runs again without refusing the env
/// aibd itself set (AIB_SEAT_ID / AIB_PERSONA_*).
#[cfg(unix)]
#[tokio::test(flavor = "multi_thread")]
async fn a_queued_seat_session_starts_when_a_slot_frees() {
    let dir = tempfile::tempdir().unwrap();
    let cards = cards_file(dir.path(), "AIB 2.0 主軸統籌", SHA_A);
    let mut d = start_with(dir.path(), Some(cards), claude_policy(), 1).await;
    let hold = d
        .call(
            "POST",
            "/api/v1/sessions",
            Some(r#"{"argv":["/bin/sleep","30"]}"#),
            "",
        )
        .await;
    assert_eq!(hold.code, 201, "{}", hold.json);
    let r = d
        .call(
            "POST",
            "/api/v1/seats/open",
            Some(&open_body("mnnftg", "")),
            &key("queued-1"),
        )
        .await;
    assert_eq!(r.code, 201, "the seat was made: {}", r.json);
    assert_eq!(r.json["session"]["state"], "pending", "{}", r.json);
    // S89-live-4: the answer says its place and the slots
    assert_eq!(r.json["session"]["queue"]["position"], 1, "{}", r.json);
    assert_eq!(r.json["session"]["queue"]["slots"], 1, "{}", r.json);
    let sid = r.json["session"]["id"].as_str().unwrap().to_string();
    let held = hold.json["id"].as_str().unwrap();
    let stop = d
        .call("DELETE", &format!("/api/v1/sessions/{held}"), None, "")
        .await;
    assert!(stop.code < 300, "{}", stop.json);
    let deadline = std::time::Instant::now() + Duration::from_secs(20);
    let s = loop {
        let r = d
            .call("GET", &format!("/api/v1/sessions/{sid}"), None, "")
            .await;
        // 5d33a REVIEW-integ5 SO26: a session that is gone (404, no state) is no start
        assert_eq!(r.code, 200, "reason: session_gone {}", r.json);
        let s = r.json;
        if s["state"] != "pending" || std::time::Instant::now() > deadline {
            break s;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    };
    assert!(
        s["state"].is_string() && s["state"] != "pending",
        "it left the queue: {s}"
    );
    assert_ne!(
        s["state"], "failed",
        "the gate did not refuse aibd's own env: {s}"
    );
    assert_ne!(
        s["ended_by"], "policy_refused",
        "the gate did not refuse aibd's own env: {s}"
    );
    d.shutdown().await;
}

/// B4-1b-2b K3, changed by S89-live-5 (0eed7 J2, 519d0 04:02): a provider aib knows but cannot
/// yet start over pipes (agy; grok opens a safe seat since 08de5 11-25) is refused before anything is written — no empty seat
/// left behind — and a dry run says the same instead of printing a plan.
#[tokio::test(flavor = "multi_thread")]
async fn a_provider_that_cannot_start_is_refused_and_leaves_no_seat() {
    let dir = tempfile::tempdir().unwrap();
    let cards = cards_file(dir.path(), "AIB 2.0 主軸統籌", SHA_A);
    let mut d = start(dir.path(), Some(cards)).await;
    let cwd = serde_json::Value::String(cwd()).to_string();
    // S89-live-2: codex opens safe only; its dgrs is refused the same way, with its own code
    for (provider, mode, extra, why) in [
        ("agy", "safe", "", "provider_unsupported"),
        ("agy", "safe", r#","dry_run":true"#, "provider_unsupported"),
        ("codex", "dgrs", "", "provider_mode_unsupported"),
        (
            "codex",
            "dgrs",
            r#","dry_run":true"#,
            "provider_mode_unsupported",
        ),
    ] {
        let body = format!(
            r#"{{"persona_uid":"mnnftg","provider":"{provider}","mode":"{mode}","cwd":{cwd}{extra}}}"#
        );
        let r = d
            .call(
                "POST",
                "/api/v1/seats/open",
                Some(&body),
                &key(&format!("{provider}-{mode}-{}", extra.len())),
            )
            .await;
        assert_eq!(r.code, 400, "{provider}{extra}: {}", r.json);
        assert_eq!(r.json["reason"], why, "{}", r.json);
    }
    let seats = d.call("GET", "/api/v1/seats", None, "").await;
    assert_eq!(seats.code, 200, "{}", seats.json);
    let n = seats.json["seats"]
        .as_array()
        .map(Vec::len)
        .unwrap_or_else(|| seats.json.as_array().map(Vec::len).unwrap_or(0));
    assert_eq!(n, 0, "no seat was written: {}", seats.json);
    d.shutdown().await;
}

/// B4-1b-2b K3 (knife SO20; a9136 11-18 rewrite after 57bd0261): a provider that can start
/// but whose session is refused (the default policy allows no program) still opens the seat:
/// 201, no session, `session_error` says why, and the seat is there.
#[tokio::test(flavor = "multi_thread")]
async fn a_session_that_cannot_start_leaves_the_seat_open() {
    let dir = tempfile::tempdir().unwrap();
    let cards = cards_file(dir.path(), "AIB 2.0 主軸統籌", SHA_A);
    let mut d = start(dir.path(), Some(cards)).await;
    let cwd = serde_json::Value::String(cwd()).to_string();
    let body =
        format!(r#"{{"persona_uid":"mnnftg","provider":"claude","mode":"safe","cwd":{cwd}}}"#);
    let r = d
        .call("POST", "/api/v1/seats/open", Some(&body), &key("so20-1"))
        .await;
    assert_eq!(r.code, 201, "{}", r.json);
    assert!(r.json["session"].is_null(), "{}", r.json);
    assert_eq!(
        r.json["session_error"]["code"], "program_not_allowed",
        "{}",
        r.json
    );
    let seat = r.json["seat"]["seat_id"].as_str().unwrap();
    let got = d
        .call("GET", &format!("/api/v1/seats/{seat}"), None, "")
        .await;
    assert_eq!(got.code, 200, "the seat stays: {}", got.json);
    d.shutdown().await;
}

/// B4-1b-2b: the dry-run plan shows the argv in full and the env names aibd will set
/// (never their values), and writes nothing.
#[tokio::test(flavor = "multi_thread")]
async fn a_dry_run_plan_shows_the_argv_and_the_env_names() {
    let dir = tempfile::tempdir().unwrap();
    let cards = cards_file(dir.path(), "AIB 2.0 主軸統籌", SHA_A);
    let mut d = start(dir.path(), Some(cards)).await;
    let r = d
        .call(
            "POST",
            "/api/v1/seats/open",
            Some(&open_body("mnnftg", r#","dry_run":true"#)),
            "",
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.json);
    let argv = r.json["plan"]["argv"].as_array().unwrap();
    assert_eq!(argv[0], "claude");
    assert!(argv.iter().any(|a| a == "--session-id"));
    // S89-live-mcp: no account-level connectors
    assert!(argv.iter().any(|a| a == "--strict-mcp-config"), "{argv:?}");
    assert_eq!(
        r.json["plan"]["env"],
        serde_json::json!(["AIB_SEAT_ID", "AIB_PERSONA_UID", "AIB_PERSONA_SHA"])
    );
    let seats = d.call("GET", "/api/v1/seats", None, "").await;
    assert_eq!(
        seats.json["seats"].as_array().map(Vec::len),
        Some(0),
        "{}",
        seats.json
    );
    d.shutdown().await;
}

/// S89-live-persona (Andy 11-18: the seat answered as plain Claude): the card text from the
/// registry's vault, checked against its hash, is kept in aibd's folder and handed to the
/// provider as a file; a card whose text changed since the registry read it refuses the open.
#[tokio::test(flavor = "multi_thread")]
async fn a_seat_gets_its_persona_text_and_a_changed_card_is_refused() {
    use sha2::Digest;
    let dir = tempfile::tempdir().unwrap();
    let vault = dir.path().join("vault");
    let card_dir = vault.join("lib").join("mnnftg");
    std::fs::create_dir_all(&card_dir).unwrap();
    let text = "你是 AndyAI 分身。";
    std::fs::write(card_dir.join("角色.md"), text).unwrap();
    let sha: String = sha2::Sha256::digest(text.as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    let cards = dir.path().join("cards.json");
    let doc = serde_json::json!({
        "schema_version": "aib-cards/v2",
        "source": {"vault_root": vault.to_str().unwrap()},
        "cards": {"mnnftg": {"uid": "mnnftg", "display_name": "AndyAI分身", "persona_sha": sha,
                              "card_path_rel": "lib/mnnftg/角色.md"}},
    });
    std::fs::write(&cards, doc.to_string()).unwrap();
    let mut d = start(dir.path(), Some(cards)).await;
    let kept = dir
        .path()
        .join("sessions")
        .join(format!("persona-{sha}.md"));
    // dry run: the plan names the file, nothing is written
    let r = d
        .call(
            "POST",
            "/api/v1/seats/open",
            Some(&open_body("mnnftg", r#","dry_run":true"#)),
            "",
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.json);
    let argv: Vec<&str> = r.json["plan"]["argv"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|a| a.as_str())
        .collect();
    assert_eq!(
        argv[argv.len() - 2..],
        ["--append-system-prompt-file", kept.to_str().unwrap()]
    );
    assert!(!argv.iter().any(|a| a.contains("AndyAI")), "never the text");
    assert_eq!(r.json["plan"]["persona"]["applied"], true);
    assert!(!kept.exists());
    // a real open keeps the checked text and says so in its event
    let r = d
        .call(
            "POST",
            "/api/v1/seats/open",
            Some(&open_body("mnnftg", "")),
            &key("persona-1"),
        )
        .await;
    assert_eq!(r.code, 201, "{}", r.json);
    assert_eq!(std::fs::read_to_string(&kept).unwrap(), text);
    let id = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let ev = events_of(&d, &id).await;
    assert_eq!(ev[0]["payload"]["persona_applied"], true, "{ev:?}");
    // the card edited after the registry read it: refused, no second seat
    std::fs::write(card_dir.join("角色.md"), "改過了").unwrap();
    let r = d
        .call(
            "POST",
            "/api/v1/seats/open",
            Some(&open_body("mnnftg", "")),
            &key("persona-2"),
        )
        .await;
    assert_eq!(r.code, 400, "{}", r.json);
    assert_eq!(r.json["reason"], "persona_changed");
    let seats = d.call("GET", "/api/v1/seats", None, "").await;
    assert_eq!(seats.json["seats"].as_array().map(Vec::len), Some(1));
    d.shutdown().await;
}

/// S89-input §3: the start-up token cannot type to a seat; only a confirmed paired page may.
#[cfg(unix)]
#[tokio::test(flavor = "multi_thread")]
async fn the_start_up_token_cannot_type_to_a_seat() {
    let dir = tempfile::tempdir().unwrap();
    let cards = cards_file(dir.path(), "AIB 2.0 主軸統籌", SHA_A);
    let d = start_with(dir.path(), Some(cards), claude_policy(), 2).await;
    let body = r#"{"type":"seat_input","seat_id":"seat_x","text":"hi"}"#;
    let r = d.call("POST", "/api/v1/act", Some(body), "").await;
    assert_eq!(r.code, 403, "{}", r.json);
    assert_eq!(r.json["code"], "act_needs_a_paired_page", "{}", r.json);
}

/// §8 8b-2 (6770f): an old persona uid merged into another opens as that one; an archived
/// persona does not open (400 persona_archived) and nothing is written.
#[tokio::test(flavor = "multi_thread")]
async fn mg20_an_old_uid_opens_as_the_kept_persona_and_an_archived_one_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let cards = cards_file(dir.path(), "AIB 2.0 主軸統籌", SHA_A);
    let mut d = start(dir.path(), Some(cards)).await;
    let db = aib_store::MessageDb::open(&dir.path().join(aib_store::MESSAGES_FILE)).unwrap();
    db.alias_set("zzzzz1", "un0eba", "mb_1", 1).unwrap();
    let r = d
        .call(
            "POST",
            "/api/v1/seats/open",
            Some(&open_body("zzzzz1", r#","new":false,"dry_run":true"#)),
            "",
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.json);
    assert_eq!(
        r.json["plan"]["persona"]["persona_sha"], SHA_B,
        "the old uid opens as the kept one"
    );
    db.archive_add(aib_store::ArchivedItem {
        kind: "persona".into(),
        uid: "mnnftg".into(),
        archived_at: 1,
        keep_until: None,
        reason: "merge".into(),
        batch_id: Some("mb_1".into()),
        copy_dir: None,
        sha256: None,
        restored_at: None,
    })
    .unwrap();
    let r = d
        .call(
            "POST",
            "/api/v1/seats/open",
            Some(&open_body("mnnftg", "")),
            &key("arch"),
        )
        .await;
    assert_eq!(
        (r.code, r.json["code"].as_str(), r.json["reason"].as_str()),
        (400, Some("invalid_request"), Some("persona_archived")),
        "{}",
        r.json
    );
    let all = d.call("GET", "/api/v1/seats", None, "").await;
    assert_eq!(all.json["seats"], serde_json::json!([]));
    d.shutdown().await;
}
