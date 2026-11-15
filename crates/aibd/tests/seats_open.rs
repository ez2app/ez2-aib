#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! `POST /api/v1/seats/open` and the seat rows on a real aibd (127.0.0.1:0, temp data dir,
//! a temp persona cards file): B4-1b-1 (IFACE-b4-1b-seat-open-a9136-1114.md r1–r3).
//! No processes are started, so this runs on every OS.
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
    let (stop_tx, stop_rx) = tokio::sync::oneshot::channel::<()>();
    let (st_tx, st_rx) = tokio::sync::oneshot::channel::<Started>();
    let c = Config {
        bind: "127.0.0.1:0".parse().unwrap(),
        data_dir: dir.to_path_buf(),
        allowed_origins: vec![],
        max_sessions: 2,
        // no session is started here: the default policy allows nothing
        session_policy: Default::default(),
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
    assert!(r.json["session"].is_null(), "B4-1b-1 starts nothing");
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
