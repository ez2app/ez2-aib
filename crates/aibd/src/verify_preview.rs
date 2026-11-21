//! G6 V4 (DESIGN-g6-verify-runner-7b81e-1121 §2; 5d33a PREREAD-g6 M2: GET only until a test
//! shows a preview's aibd cannot reach a real seat): a review seat reads one fixed route of a
//! running preview's aibd, and is told its shape, never its content.
//!
//! - [`PREVIEW_GET`] `{preview_id, route}`: `route` is one of [`ROUTES`] (no path parameters,
//!   no query). The address is the port this aibd started the preview on
//!   ([`crate::preview::reap`]), never one the asker names; the key is the preview's own
//!   `run/aibd.token` under `<data>/previews/<id>/data`. Only `GET` is ever sent.
//! - The answer ([`shape`]): the HTTP status, the body's length, the JSON object's top-level
//!   keys, the length of each top-level array, and `ok` / `reason` / `code` when they are plain
//!   words. Nothing else of the body.
use crate::api::AppState;
use axum::{http::StatusCode, response::IntoResponse, response::Response, Json};
use serde::Deserialize;
use serde_json::{json, Map, Value};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::Path;
use std::time::{Duration, Instant};

pub const PREVIEW_GET: &str = "verify_preview_get";
/// The routes a check may read: what a reviewer looked at with curl, all without parameters.
pub const ROUTES: [&str; 10] = [
    crate::guard::HEALTH,
    crate::guard::VERSION,
    crate::guard::SEATS,
    crate::guard::FLOWS,
    crate::guard::CONTACTS,
    crate::guard::NOW,
    crate::guard::SLOTS,
    crate::guard::ROLES,
    crate::guard::TREE,
    crate::guard::ASSIGNMENTS,
];
/// The keys of an answer, and no other.
pub const SHAPE_KEYS: [&str; 7] = ["status", "bytes", "keys", "counts", "ok", "reason", "code"];
pub const MAX_ANSWER: usize = 2 * 1024 * 1024;
pub const MAX_KEYS: usize = 50;
pub const ANSWER_SECS: u64 = 10;

/// A key or reason word as it may be told: `[a-z0-9_]`, 1 to 64.
fn word(s: &str) -> bool {
    (1..=64).contains(&s.len())
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
}

/// What a page is told of one answer (see the module doc). `body` that is not JSON is told by
/// its length only.
pub fn shape(status: u16, body: &[u8]) -> Value {
    let v: Option<Value> = serde_json::from_slice(body).ok();
    let mut keys: Vec<String> = Vec::new();
    let mut counts = Map::new();
    let (mut ok, mut reason, mut code) = (Value::Null, Value::Null, Value::Null);
    match &v {
        Some(Value::Object(o)) => {
            for (k, val) in o {
                if !word(k) {
                    continue;
                }
                if keys.len() < MAX_KEYS {
                    keys.push(k.clone());
                }
                if let Value::Array(a) = val {
                    counts.insert(k.clone(), json!(a.len()));
                }
            }
            if let Some(Value::Bool(b)) = o.get("ok") {
                ok = json!(b);
            }
            for (name, slot) in [("reason", &mut reason), ("code", &mut code)] {
                if let Some(Value::String(s)) = o.get(name) {
                    if word(s) {
                        *slot = json!(s);
                    }
                }
            }
        }
        Some(Value::Array(a)) => {
            counts.insert(String::new(), json!(a.len()));
        }
        _ => {}
    }
    keys.sort();
    json!({
        "status": status,
        "bytes": body.len(),
        "keys": keys,
        "counts": counts,
        "ok": ok,
        "reason": reason,
        "code": code,
    })
}

fn decode_chunks(mut data: &[u8]) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    loop {
        let end = data.windows(2).position(|x| x == b"\r\n")?;
        let head = std::str::from_utf8(data.get(..end)?).ok()?;
        let size = usize::from_str_radix(head.split(';').next().unwrap_or(""), 16).ok()?;
        data = data.get(end + 2..)?;
        if size == 0 {
            return Some(out);
        }
        out.extend_from_slice(data.get(..size)?);
        if data.get(size..size + 2)? != b"\r\n" {
            return None;
        }
        data = data.get(size + 2..)?;
    }
}

/// The status and body of a raw HTTP/1.1 response (content-length or chunked).
pub fn parse(raw: &[u8]) -> Option<(u16, Vec<u8>)> {
    let split = raw.windows(4).position(|x| x == b"\r\n\r\n")?;
    let head = std::str::from_utf8(raw.get(..split)?).ok()?;
    let mut lines = head.split("\r\n");
    let status = lines
        .next()?
        .split_whitespace()
        .nth(1)?
        .parse::<u16>()
        .ok()?;
    let mut chunked = false;
    for l in lines {
        if let Some((k, v)) = l.split_once(':') {
            if k.eq_ignore_ascii_case("transfer-encoding") {
                chunked = v.trim().eq_ignore_ascii_case("chunked");
            }
        }
    }
    let body = raw.get(split + 4..)?;
    let body = if chunked {
        decode_chunks(body)?
    } else {
        body.to_vec()
    };
    Some((status, body))
}

/// One `GET path` to `addr` with `token`, at most [`MAX_ANSWER`] bytes in [`ANSWER_SECS`].
/// Blocking.
pub fn get(addr: SocketAddr, token: &str, path: &str) -> Result<Vec<u8>, &'static str> {
    let mut s = TcpStream::connect_timeout(&addr, Duration::from_secs(3))
        .map_err(|_| "preview_unreachable")?;
    s.set_write_timeout(Some(Duration::from_secs(3)))
        .map_err(|_| "preview_unreachable")?;
    write!(
        s,
        "GET {path} HTTP/1.1\r\nHost: {addr}\r\nConnection: close\r\nAuthorization: Bearer {token}\r\n\r\n"
    )
    .map_err(|_| "preview_unreachable")?;
    let deadline = Instant::now() + Duration::from_secs(ANSWER_SECS);
    let mut raw = Vec::new();
    let mut buf = [0_u8; 8192];
    loop {
        let left = deadline
            .checked_duration_since(Instant::now())
            .filter(|d| !d.is_zero())
            .ok_or("verify_timeout")?;
        s.set_read_timeout(Some(left))
            .map_err(|_| "verify_timeout")?;
        let n = s.read(&mut buf).map_err(|_| "verify_timeout")?;
        if n == 0 {
            return Ok(raw);
        }
        raw.extend_from_slice(buf.get(..n).unwrap_or_default());
        if raw.len() > MAX_ANSWER {
            return Err("answer_too_large");
        }
    }
}

/// The preview's own key: `<data>/previews/<id>/data/run/aibd.token`.
pub fn token_of(data: &Path, id: &str) -> Option<String> {
    let p = data
        .join(crate::preview::DIR)
        .join(id)
        .join("data")
        .join("run")
        .join("aibd.token");
    let t = std::fs::read_to_string(p).ok()?;
    let t = t.trim();
    (!t.is_empty() && t.bytes().all(|b| b.is_ascii_graphic())).then(|| t.to_owned())
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Ask {
    #[serde(rename = "type")]
    _type: String,
    preview_id: String,
    route: String,
}

fn refuse(status: StatusCode, reason: &str, message: &str) -> Response {
    (
        status,
        Json(
            json!({"code": "refused", "reason": reason, "message": message,
                    "request_id": crate::guard::request_id()}),
        ),
    )
        .into_response()
}

/// The asked route, if it is one of [`ROUTES`].
pub fn route_of(r: &str) -> Option<&'static str> {
    ROUTES.iter().copied().find(|x| *x == r)
}

/// [`PREVIEW_GET`]; the caller decided who may ask ([`crate::verify_run`]).
pub async fn act(st: AppState, body: Value) -> Response {
    let Ok(a) = serde_json::from_value::<Ask>(body) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "verify_fields",
            "body must be {type, preview_id, route}",
        );
    };
    if !crate::preview::valid_id(&a.preview_id) {
        return refuse(
            StatusCode::BAD_REQUEST,
            "preview_id_shape",
            "not a preview id",
        );
    }
    let Some(route) = route_of(&a.route) else {
        return refuse(
            StatusCode::BAD_REQUEST,
            "verify_route",
            "not one of the routes a check reads",
        );
    };
    let Some((port, ..)) = crate::preview::reap(&st.store, &a.preview_id) else {
        return refuse(
            StatusCode::NOT_FOUND,
            "preview_not_running",
            "that preview is not running",
        );
    };
    let private = st.sessions.private_dir().to_path_buf();
    let data = private.parent().unwrap_or(&private).to_path_buf();
    let Some(token) = token_of(&data, &a.preview_id) else {
        return refuse(
            StatusCode::SERVICE_UNAVAILABLE,
            "preview_key_missing",
            "the preview has not written its key",
        );
    };
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let got = tokio::task::spawn_blocking(move || get(addr, &token, route)).await;
    match got {
        Ok(Ok(raw)) => match parse(&raw) {
            Some((status, body)) => Json(shape(status, &body)).into_response(),
            None => refuse(
                StatusCode::BAD_GATEWAY,
                "preview_answer_bad",
                "not an HTTP answer",
            ),
        },
        Ok(Err(why)) => refuse(StatusCode::BAD_GATEWAY, why, "the preview did not answer"),
        Err(_) => refuse(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "the check did not end",
        ),
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;
    use std::net::TcpListener;

    fn keys(v: &Value) -> Vec<String> {
        let mut k: Vec<String> = v.as_object().unwrap().keys().cloned().collect();
        k.sort();
        k
    }

    #[test]
    fn an_answer_is_told_by_its_shape_not_its_content() {
        let body = json!({
            "seats": [{"name": "審查", "cwd": "/Users/someone/secret"}, {"name": "b"}],
            "ok": true,
            "reason": "not_built",
            "code": "Has Spaces / and words",
            "Weird-Key": 1,
            "note": "a letter's words",
        })
        .to_string();
        let v = shape(200, body.as_bytes());
        let mut want: Vec<String> = SHAPE_KEYS.iter().map(|s| s.to_string()).collect();
        want.sort();
        assert_eq!(keys(&v), want, "reason: shape_keys");
        let t = v.to_string();
        for secret in ["審查", "secret", "letter", "Spaces", "Weird"] {
            assert!(!t.contains(secret), "reason: told {secret} {t}");
        }
        assert_eq!(v["counts"]["seats"], 2);
        assert_eq!(v["keys"], json!(["code", "note", "ok", "reason", "seats"]));
        assert_eq!(v["ok"], true);
        assert_eq!(v["reason"], "not_built");
        assert_eq!(v["code"], Value::Null, "reason: a sentence told as a code");
        assert_eq!(v["bytes"], body.len());
        // not JSON: its length only
        let raw = shape(502, b"<html>the preview's own words</html>");
        assert!(!raw.to_string().contains("words"), "reason: html_told");
        assert_eq!(raw["keys"], json!([]));
    }

    #[test]
    fn only_the_fixed_routes_are_read() {
        for r in ROUTES {
            assert_eq!(route_of(r), Some(r));
            assert!(
                !r.contains('?') && !r.contains('{'),
                "reason: parameter in {r}"
            );
        }
        for bad in [
            "/api/v1/act",
            "/api/v1/seats/seat_ab12",
            "/api/v1/health?x=1",
            "/api/v1/sessions",
            "/api/v1/doc",
            "http://example.com/",
            "",
        ] {
            assert_eq!(route_of(bad), None, "reason: let_in {bad}");
        }
    }

    #[test]
    fn a_chunked_or_sized_answer_is_read_and_a_broken_one_is_not() {
        let sized = b"HTTP/1.1 200 OK\r\ncontent-length: 7\r\n\r\n{\"a\":1}";
        assert_eq!(parse(sized), Some((200, b"{\"a\":1}".to_vec())));
        let chunked =
            b"HTTP/1.1 404 Not Found\r\nTransfer-Encoding: chunked\r\n\r\n3\r\n{\"b\r\n4\r\n\":2}\r\n0\r\n\r\n";
        assert_eq!(parse(chunked), Some((404, b"{\"b\":2}".to_vec())));
        assert_eq!(
            parse(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nz\r\n"),
            None
        );
        assert_eq!(parse(b"no head at all"), None);
    }

    #[test]
    fn the_preview_is_asked_with_get_and_its_own_key_only() {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = l.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut c, _) = l.accept().unwrap();
            let mut req = Vec::new();
            let mut b = [0_u8; 1024];
            while !req.windows(4).any(|w| w == b"\r\n\r\n") {
                let n = c.read(&mut b).unwrap();
                if n == 0 {
                    break;
                }
                req.extend_from_slice(&b[..n]);
            }
            c.write_all(b"HTTP/1.1 200 OK\r\ncontent-length: 11\r\n\r\n{\"ok\":true}")
                .unwrap();
            String::from_utf8(req).unwrap()
        });
        let raw = get(addr, "k-preview-1", crate::guard::SEATS).unwrap();
        let req = server.join().unwrap();
        assert!(
            req.starts_with("GET /api/v1/seats HTTP/1.1\r\n"),
            "reason: not_a_get {req}"
        );
        assert!(
            req.contains("Authorization: Bearer k-preview-1\r\n"),
            "reason: key {req}"
        );
        assert_eq!(parse(&raw), Some((200, b"{\"ok\":true}".to_vec())));
    }

    #[test]
    fn the_previews_key_is_read_from_its_own_data_folder() {
        let d = tempfile::tempdir().unwrap();
        let run = d
            .path()
            .join("previews")
            .join("pv_one")
            .join("data")
            .join("run");
        std::fs::create_dir_all(&run).unwrap();
        std::fs::write(run.join("aibd.token"), "tok-1\n").unwrap();
        assert_eq!(token_of(d.path(), "pv_one").as_deref(), Some("tok-1"));
        assert_eq!(token_of(d.path(), "pv_two"), None);
        std::fs::write(run.join("aibd.token"), "two words\n").unwrap();
        assert_eq!(token_of(d.path(), "pv_one"), None, "reason: bad_key_used");
    }
}
