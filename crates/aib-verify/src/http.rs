//! A small blocking HTTP/1.1 client for aibd's loopback API. aib-verify does not link
//! aibd: it must be able to check any built aibd, old heads included, through the API only.

use serde_json::Value;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;

pub struct Client {
    pub addr: SocketAddr,
    pub token: String,
}

impl Client {
    /// One request on its own connection (`Connection: close`). Err = no HTTP answer.
    pub fn call(
        &self,
        method: &str,
        path: &str,
        body: Option<&Value>,
    ) -> Result<(u16, Value), String> {
        let body = body.map(Value::to_string).unwrap_or_default();
        let mut s = TcpStream::connect_timeout(&self.addr, Duration::from_secs(5))
            .map_err(|e| format!("connect {}: {e}", self.addr))?;
        s.set_read_timeout(Some(Duration::from_secs(60)))
            .map_err(|e| format!("read timeout: {e}"))?;
        write!(
            s,
            "{method} {path} HTTP/1.1\r\nHost: {}\r\nAuthorization: Bearer {}\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
            self.addr,
            self.token,
            body.len()
        )
        .map_err(|e| format!("write: {e}"))?;
        let mut raw = Vec::new();
        s.read_to_end(&mut raw).map_err(|e| format!("read: {e}"))?;
        parse(&raw)
    }
}

/// Status code and JSON body (Null when the body is empty). Handles a chunked body.
pub fn parse(raw: &[u8]) -> Result<(u16, Value), String> {
    let text = std::str::from_utf8(raw).map_err(|_| "response is not UTF-8".to_string())?;
    let (head, body) = text
        .split_once("\r\n\r\n")
        .ok_or_else(|| "no end of headers".to_string())?;
    let code: u16 = head
        .get(9..12)
        .and_then(|c| c.parse().ok())
        .ok_or_else(|| "no status code".to_string())?;
    let chunked = head.lines().skip(1).any(|l| {
        let l = l.to_ascii_lowercase();
        l.starts_with("transfer-encoding:") && l.contains("chunked")
    });
    let payload = if chunked {
        dechunk(body)?
    } else {
        body.to_string()
    };
    if payload.trim().is_empty() {
        return Ok((code, Value::Null));
    }
    let v = serde_json::from_str(&payload).map_err(|e| format!("body is not JSON: {e}"))?;
    Ok((code, v))
}

fn dechunk(mut s: &str) -> Result<String, String> {
    let mut out = String::new();
    loop {
        let (size, rest) = s
            .split_once("\r\n")
            .ok_or_else(|| "chunk size line missing".to_string())?;
        let size = size.split(';').next().unwrap_or_default().trim();
        let n = usize::from_str_radix(size, 16).map_err(|_| format!("bad chunk size {size:?}"))?;
        if n == 0 {
            return Ok(out);
        }
        let chunk = rest
            .get(..n)
            .ok_or_else(|| "chunk shorter than its size".to_string())?;
        out.push_str(chunk);
        s = rest
            .get(n..)
            .and_then(|r| r.strip_prefix("\r\n"))
            .ok_or_else(|| "chunk not followed by CRLF".to_string())?;
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)] // test code: a panic is the failure report
mod tests {
    use super::parse;

    #[test]
    fn content_length_body() {
        let (c, v) =
            parse(b"HTTP/1.1 201 Created\r\ncontent-length: 10\r\n\r\n{\"id\":\"s\"}").unwrap();
        assert_eq!(c, 201);
        assert_eq!(v["id"], "s");
    }

    #[test]
    fn chunked_body() {
        let (c, v) = parse(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4\r\n{\"a\"\r\n3\r\n:1}\r\n0\r\n\r\n",
        )
        .unwrap();
        assert_eq!(c, 200);
        assert_eq!(v["a"], 1);
    }

    #[test]
    fn empty_body_is_null_and_broken_input_is_an_error() {
        assert_eq!(
            parse(b"HTTP/1.1 204 No Content\r\n\r\n").unwrap().1,
            serde_json::Value::Null
        );
        assert!(parse(b"HTTP/1.1 200 OK\r\n").is_err());
        assert!(parse(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\n").is_err());
        assert!(parse(b"HTTP/1.1 200 OK\r\n\r\nnot json").is_err());
    }
}
