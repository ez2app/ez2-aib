//! `<data>/run/aibd.json` → the loopback address aibd listens on (T97-2m, IFACE-T97-2m r2 §3).
//! Moved out of `aibd ctl` so `aibd install` / `start` can find health with the same rules;
//! the token stays with ctl (health is public).

use serde_json::Value;
use std::net::{IpAddr, SocketAddr};
use std::path::Path;

/// Read `dir/aibd.json`; only a loopback ip and a nonzero port are accepted.
pub fn addr(dir: &Path) -> Result<SocketAddr, String> {
    let info: Value = serde_json::from_slice(
        &std::fs::read(dir.join("aibd.json")).map_err(|e| format!("read run info: {e}"))?,
    )
    .map_err(|e| format!("invalid run info: {e}"))?;
    let ip: IpAddr = info
        .get("ip")
        .and_then(Value::as_str)
        .ok_or("run info missing ip")?
        .parse()
        .map_err(|_| "invalid run info ip")?;
    if ip != IpAddr::V4(std::net::Ipv4Addr::LOCALHOST)
        && ip != IpAddr::V6(std::net::Ipv6Addr::LOCALHOST)
    {
        return Err("run info is not loopback".into());
    }
    let port = info
        .get("port")
        .and_then(Value::as_u64)
        .and_then(|p| u16::try_from(p).ok())
        .filter(|p| *p != 0)
        .ok_or("run info invalid port")?;
    Ok(SocketAddr::new(ip, port))
}

#[cfg(test)]
mod tests {
    use super::addr;

    fn with(json: &str) -> Result<std::net::SocketAddr, String> {
        let d = tempfile::tempdir().expect("tmp");
        std::fs::write(d.path().join("aibd.json"), json).expect("write");
        addr(d.path())
    }

    #[test]
    fn a_loopback_address_with_a_port_is_read() {
        assert_eq!(
            with(r#"{"ip":"127.0.0.1","port":4100}"#)
                .expect("ok")
                .to_string(),
            "127.0.0.1:4100"
        );
    }

    #[test]
    fn an_address_that_is_not_loopback_is_refused() {
        assert_eq!(
            with(r#"{"ip":"192.0.2.7","port":4100}"#).unwrap_err(),
            "run info is not loopback"
        );
    }

    #[test]
    fn port_zero_is_refused() {
        assert_eq!(
            with(r#"{"ip":"127.0.0.1","port":0}"#).unwrap_err(),
            "run info invalid port"
        );
    }
}
