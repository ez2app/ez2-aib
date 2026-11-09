use std::net::{IpAddr, SocketAddr};
use std::path::PathBuf;

pub use crate::allowlist::SessionPolicy;

#[derive(Debug, Clone)]
pub struct Config {
    pub bind: SocketAddr,
    pub data_dir: PathBuf,
    /// Exact `Origin` values allowed (scheme://host:port). Empty = none.
    pub allowed_origins: Vec<String>,
    /// Running sessions allowed at once (`AIBD_MAX_SESSIONS`, 1–64, default 8).
    pub max_sessions: usize,
    /// Programs and env names a session may use (M1 gate, `AIBD_SESSION_POLICY`).
    /// Default: nothing may be started.
    pub session_policy: SessionPolicy,
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
    #[error("AIBD_MAX_SESSIONS must be 1-64, got {0}")]
    BadMaxSessions(String),
    #[error(transparent)]
    SessionPolicy(#[from] crate::allowlist::PolicyError),
}

pub const DEFAULT_BIND: &str = "127.0.0.1:47770";
/// The most the default running limit can be (ADR 0005 §5: at most 4 for now; 8 is the
/// stress-test target, set with `AIBD_MAX_SESSIONS`, not a default).
pub const DEFAULT_MAX_SESSIONS: usize = 4;
/// The least the default running limit can be, also used when the memory size cannot be
/// read.
pub const MIN_DEFAULT_SESSIONS: usize = 2;
/// Memory one live CLI is budgeted (ADR 0005 §5: 500 MB, taken as MiB).
pub const SESSION_MEMORY_BUDGET: u64 = 500 * 1024 * 1024;
pub const MAX_SESSIONS_LIMIT: usize = 64;

/// ADR 0005 §5: `clamp(floor(physical memory × 0.5 ÷ 500 MB), 2, 4)`; unknown memory ⇒ 2.
pub fn default_max_sessions(physical_memory: Option<u64>) -> usize {
    match physical_memory {
        Some(m) => usize::try_from(m / 2 / SESSION_MEMORY_BUDGET)
            .unwrap_or(usize::MAX)
            .clamp(MIN_DEFAULT_SESSIONS, DEFAULT_MAX_SESSIONS),
        None => MIN_DEFAULT_SESSIONS,
    }
}

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
    /// Read `AIBD_BIND`, `AIBD_DATA_DIR`, `AIBD_ALLOWED_ORIGINS`, `AIBD_MAX_SESSIONS` and
    /// `AIBD_SESSION_POLICY` (a file, read now) through `get`
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
        let max_sessions = match get("AIBD_MAX_SESSIONS") {
            None => default_max_sessions(crate::platform::physical_memory()),
            Some(v) => match v.trim().parse::<usize>() {
                Ok(n) if (1..=MAX_SESSIONS_LIMIT).contains(&n) => n,
                _ => return Err(ConfigError::BadMaxSessions(v)),
            },
        };
        let session_policy = match get("AIBD_SESSION_POLICY") {
            None => SessionPolicy::default(),
            Some(v) => crate::allowlist::from_path(&v)?,
        };
        Ok(Config {
            bind,
            data_dir,
            allowed_origins,
            max_sessions,
            session_policy,
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
    fn session_policy_unset_allows_nothing_and_a_bad_file_stops_start() {
        let c = Config::from_lookup(|_| None, Some("/x".into())).unwrap();
        assert_eq!(c.session_policy, SessionPolicy::default());
        let get = |k: &str| (k == "AIBD_SESSION_POLICY").then(|| "relative.json".to_string());
        assert_eq!(
            Config::from_lookup(get, Some("/x".into())).unwrap_err(),
            ConfigError::SessionPolicy(crate::allowlist::PolicyError::NotAbsolute)
        );
    }

    #[test]
    fn the_default_limit_follows_memory_between_2_and_4() {
        const GIB: u64 = 1024 * 1024 * 1024;
        assert_eq!(default_max_sessions(None), 2);
        assert_eq!(default_max_sessions(Some(GIB)), 2); // 1 session's worth, raised to 2
        assert_eq!(default_max_sessions(Some(3 * GIB)), 3);
        assert_eq!(default_max_sessions(Some(8 * GIB)), 4); // 8, capped at 4
        assert_eq!(default_max_sessions(Some(u64::MAX)), 4);
    }

    #[test]
    fn data_dir_required() {
        assert_eq!(
            Config::from_lookup(|_| None, None).unwrap_err(),
            ConfigError::NoDataDir
        );
    }
}
