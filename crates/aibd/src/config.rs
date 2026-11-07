use std::net::{IpAddr, SocketAddr};
use std::path::PathBuf;

#[derive(Debug, Clone)]
pub struct Config {
    pub bind: SocketAddr,
    pub data_dir: PathBuf,
    /// Exact `Origin` values allowed (scheme://host:port). Empty = none.
    pub allowed_origins: Vec<String>,
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum ConfigError {
    #[error("AIBD_BIND must be a loopback address (127.0.0.1 or ::1), got {0}")]
    NotLoopback(String),
    #[error("AIBD_BIND is not ip:port: {0}")]
    BadBind(String),
    #[error("no data directory: set AIBD_DATA_DIR")]
    NoDataDir,
    #[error("AIBD_ALLOWED_ORIGINS entry is not an origin (scheme://host[:port]): {0}")]
    BadOrigin(String),
}

pub const DEFAULT_BIND: &str = "127.0.0.1:47770";

pub fn parse_bind(s: &str) -> Result<SocketAddr, ConfigError> {
    let a: SocketAddr = s.parse().map_err(|_| ConfigError::BadBind(s.to_string()))?;
    let ok = match a.ip() {
        IpAddr::V4(v4) => v4 == std::net::Ipv4Addr::LOCALHOST,
        IpAddr::V6(v6) => v6 == std::net::Ipv6Addr::LOCALHOST,
    };
    if ok {
        Ok(a)
    } else {
        Err(ConfigError::NotLoopback(s.to_string()))
    }
}

fn parse_origin(s: &str) -> Result<String, ConfigError> {
    let t = s.trim();
    let rest = t
        .strip_prefix("http://")
        .or_else(|| t.strip_prefix("https://"))
        .or_else(|| t.strip_prefix("tauri://"));
    match rest {
        Some(r) if !r.is_empty() && !r.contains('/') && !r.contains('*') => Ok(t.to_string()),
        _ => Err(ConfigError::BadOrigin(s.to_string())),
    }
}

impl Config {
    /// Read `AIBD_BIND`, `AIBD_DATA_DIR`, `AIBD_ALLOWED_ORIGINS` through `get`
    /// (injected so tests never touch the real environment).
    pub fn from_lookup(
        get: impl Fn(&str) -> Option<String>,
        default_data_dir: Option<PathBuf>,
    ) -> Result<Config, ConfigError> {
        let bind = parse_bind(&get("AIBD_BIND").unwrap_or_else(|| DEFAULT_BIND.to_string()))?;
        let data_dir = get("AIBD_DATA_DIR")
            .map(PathBuf::from)
            .or(default_data_dir)
            .ok_or(ConfigError::NoDataDir)?;
        let allowed_origins = get("AIBD_ALLOWED_ORIGINS")
            .map(|v| {
                v.split(',')
                    .filter(|s| !s.trim().is_empty())
                    .map(parse_origin)
                    .collect::<Result<Vec<_>, _>>()
            })
            .transpose()?
            .unwrap_or_default();
        Ok(Config {
            bind,
            data_dir,
            allowed_origins,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_loopback_binds_are_accepted() {
        assert!(parse_bind("127.0.0.1:0").is_ok());
        assert!(parse_bind("[::1]:47770").is_ok());
        for bad in [
            "0.0.0.0:47770",
            "[::]:47770",
            "192.168.1.5:47770",
            "127.0.0.2:1",
        ] {
            assert!(
                matches!(parse_bind(bad), Err(ConfigError::NotLoopback(_))),
                "{bad} must be refused"
            );
        }
        assert!(matches!(
            parse_bind("localhost:1"),
            Err(ConfigError::BadBind(_))
        ));
    }

    #[test]
    fn origins_must_be_exact() {
        let get = |k: &str| {
            (k == "AIBD_ALLOWED_ORIGINS").then(|| "tauri://localhost, http://127.0.0.1:5173".into())
        };
        let c = Config::from_lookup(get, Some("/x".into())).unwrap();
        assert_eq!(
            c.allowed_origins,
            ["tauri://localhost", "http://127.0.0.1:5173"]
        );
        for bad in ["*", "http://*.example.com", "127.0.0.1:5173", "http://a/b"] {
            let get = move |k: &str| (k == "AIBD_ALLOWED_ORIGINS").then(|| bad.to_string());
            assert!(
                Config::from_lookup(get, Some("/x".into())).is_err(),
                "{bad}"
            );
        }
    }

    #[test]
    fn data_dir_required() {
        assert_eq!(
            Config::from_lookup(|_| None, None).unwrap_err(),
            ConfigError::NoDataDir
        );
    }
}
