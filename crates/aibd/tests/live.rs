#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

//! Real socket tests: start aibd on 127.0.0.1:0 with a temp data dir, talk raw HTTP.

use aibd::config::Config;
use aibd::server::{self, Started};
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::Path;
use std::time::Duration;

fn http(addr: std::net::SocketAddr, raw: &str) -> String {
    let mut s = TcpStream::connect(addr).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
    s.write_all(raw.as_bytes()).unwrap();
    let mut out = String::new();
    let _ = s.read_to_string(&mut out);
    out
}

fn cfg(dir: &Path) -> Config {
    Config {
        bind: "127.0.0.1:0".parse().unwrap(),
        data_dir: dir.to_path_buf(),
        allowed_origins: vec![],
    }
}

async fn start(
    dir: &Path,
) -> (
    Started,
    tokio::sync::oneshot::Sender<()>,
    tokio::task::JoinHandle<Result<(), server::StartError>>,
) {
    let (stop_tx, stop_rx) = tokio::sync::oneshot::channel::<()>();
    let (st_tx, st_rx) = tokio::sync::oneshot::channel::<Started>();
    let c = cfg(dir);
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
    #[allow(clippy::disallowed_methods)] // test deadline only, not a daemon timer
    let started = tokio::time::timeout(Duration::from_secs(10), st_rx)
        .await
        .unwrap()
        .unwrap();
    (started, stop_tx, h)
}

#[tokio::test(flavor = "multi_thread")]
async fn serves_on_loopback_with_token_file_and_cleans_up() {
    let d = tempfile::tempdir().unwrap();
    let (st, stop, h) = start(d.path()).await;
    assert!(st.addr.ip().is_loopback());

    let token = std::fs::read_to_string(&st.token_path).unwrap();
    assert_eq!(token.len(), 43);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let m = std::fs::metadata(&st.token_path)
            .unwrap()
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(m, 0o600);
    }
    let info = std::fs::read_to_string(&st.run_info_path).unwrap();
    assert!(
        !info.contains(&token),
        "run info must not contain the token"
    );

    let addr = st.addr;
    let port = addr.port();
    let ok = tokio::task::spawn_blocking(move || {
        http(addr, &format!("GET /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"))
    })
    .await
    .unwrap();
    assert!(ok.starts_with("HTTP/1.1 200"), "{ok}");

    let t2 = token.clone();
    let authed = tokio::task::spawn_blocking(move || {
        http(addr, &format!("GET /api/v1/events HTTP/1.1\r\nHost: localhost:{port}\r\nAuthorization: Bearer {t2}\r\nConnection: close\r\n\r\n"))
    })
    .await
    .unwrap();
    assert!(authed.starts_with("HTTP/1.1 200"), "{authed}");

    let rebind = tokio::task::spawn_blocking(move || {
        http(addr, &format!("GET /api/v1/health HTTP/1.1\r\nHost: attacker.example:{port}\r\nConnection: close\r\n\r\n"))
    })
    .await
    .unwrap();
    assert!(rebind.starts_with("HTTP/1.1 403"), "{rebind}");

    stop.send(()).unwrap();
    h.await.unwrap().unwrap();
    assert!(
        !st.token_path.exists(),
        "token file must be removed on shutdown"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn second_instance_on_same_data_dir_is_refused() {
    let d = tempfile::tempdir().unwrap();
    let (_st, stop, h) = start(d.path()).await;
    let second = server::run(cfg(d.path()), async {}, |_| {}).await;
    assert!(matches!(second, Err(server::StartError::AlreadyRunning)));
    stop.send(()).unwrap();
    h.await.unwrap().unwrap();
}

#[tokio::test(flavor = "multi_thread")]
async fn events_written_before_restart_are_read_back() {
    let d = tempfile::tempdir().unwrap();
    {
        let s = aib_store::Store::open(&d.path().join("aibd.sqlite")).unwrap();
        s.append(vec![aib_store::NewEvent {
            event_id: "e1".into(),
            aggregate_type: "seat".into(),
            aggregate_id: "seat-1".into(),
            sequence: 1,
            event_type: "seat.created".into(),
            correlation_id: None,
            occurred_at: "2026-11-07T00:00:00Z".into(),
            payload: serde_json::json!({}),
        }])
        .unwrap();
    }
    let (st, stop, h) = start(d.path()).await;
    let token = std::fs::read_to_string(&st.token_path).unwrap();
    let addr = st.addr;
    let port = addr.port();
    let body = tokio::task::spawn_blocking(move || {
        http(addr, &format!("GET /api/v1/events?cursor=0 HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {token}\r\nConnection: close\r\n\r\n"))
    })
    .await
    .unwrap();
    assert!(body.contains("\"event_id\":\"e1\""), "{body}");
    assert!(body.contains("\"next_cursor\":1"), "{body}");
    stop.send(()).unwrap();
    h.await.unwrap().unwrap();
}

#[test]
fn wildcard_bind_is_refused_by_config() {
    let get = |k: &str| (k == "AIBD_BIND").then(|| "0.0.0.0:47770".to_string());
    assert!(Config::from_lookup(get, Some("/tmp/x".into())).is_err());
}
