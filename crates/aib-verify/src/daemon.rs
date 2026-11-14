//! One aibd started for one group of runs: its own data folder, loopback port 0, a
//! policy written for it, a slot limit. Only this process is ever signalled; dropping
//! the handle stops it (TERM, then KILL after 15 s).

use crate::http::Client;
use serde_json::{json, Value};
use std::fs::File;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

pub struct Daemon {
    child: Child,
    pub client: Client,
    pub log: PathBuf,
}

impl Daemon {
    /// `dir` is emptied of an old `data/` first; the log is `dir/aibd.log`.
    pub fn start(
        bin: &Path,
        dir: &Path,
        policy: &Value,
        max_sessions: u32,
    ) -> Result<Daemon, String> {
        let _ = std::fs::remove_dir_all(dir.join("data"));
        Daemon::start_on(bin, dir, policy, max_sessions, "aibd.log")
    }

    /// The same `dir/data` as a stopped one (a restart); the log is `dir/<log>`.
    pub fn restart(
        bin: &Path,
        dir: &Path,
        policy: &Value,
        max_sessions: u32,
        log: &str,
    ) -> Result<Daemon, String> {
        Daemon::start_on(bin, dir, policy, max_sessions, log)
    }

    fn start_on(
        bin: &Path,
        dir: &Path,
        policy: &Value,
        max_sessions: u32,
        log_name: &str,
    ) -> Result<Daemon, String> {
        std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
        let data = dir.join("data");
        let pol = dir.join("policy.json");
        std::fs::write(&pol, policy.to_string()).map_err(|e| format!("{}: {e}", pol.display()))?;
        let log = dir.join(log_name);
        let out = File::create(&log).map_err(|e| format!("{}: {e}", log.display()))?;
        let err = out.try_clone().map_err(|e| format!("log: {e}"))?;
        let mut child = Command::new(bin)
            .env("AIBD_DATA_DIR", &data)
            .env("AIBD_SESSION_POLICY", &pol)
            .env("AIBD_BIND", "127.0.0.1:0")
            .env("AIBD_MAX_SESSIONS", max_sessions.to_string())
            .env("RUST_LOG", "info")
            .stdin(Stdio::null())
            .stdout(out)
            .stderr(err)
            .spawn()
            .map_err(|e| format!("start {}: {e}", bin.display()))?;
        let info = data.join("run").join("aibd.json");
        let deadline = Instant::now() + Duration::from_secs(20);
        let port = loop {
            if let Some(port) = std::fs::read_to_string(&info)
                .ok()
                .and_then(|s| serde_json::from_str::<Value>(&s).ok())
                .filter(|v| v["pid"].as_u64() == Some(u64::from(child.id())))
                .and_then(|v| v["port"].as_u64())
                .and_then(|p| u16::try_from(p).ok())
            {
                break port;
            }
            if let Ok(Some(st)) = child.try_wait() {
                return Err(format!("aibd ended at start ({st}); see {}", log.display()));
            }
            if Instant::now() > deadline {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!(
                    "aibd wrote no {} in 20 s; see {}",
                    info.display(),
                    log.display()
                ));
            }
            std::thread::sleep(Duration::from_millis(200));
        };
        let token = std::fs::read_to_string(data.join("run").join("aibd.token"))
            .map_err(|e| format!("token: {e}"))?
            .trim()
            .to_string();
        Ok(Daemon {
            child,
            client: Client {
                addr: SocketAddr::from(([127, 0, 0, 1], port)),
                token,
            },
            log,
        })
    }

    /// A new seat; its id.
    pub fn seat(&self, name: &str) -> Result<String, String> {
        let (c, v) = self
            .client
            .call("POST", "/api/v1/seats", Some(&json!({ "name": name })))?;
        v["seat_id"]
            .as_str()
            .filter(|_| c / 100 == 2)
            .map(str::to_string)
            .ok_or_else(|| format!("seat create: {c} {v}"))
    }

    pub fn start_session(&self, seat: &str, argv: &[String]) -> Result<(u16, Value), String> {
        self.client.call(
            "POST",
            &format!("/api/v1/seats/{seat}/sessions"),
            Some(&json!({ "argv": argv })),
        )
    }

    pub fn session(&self, id: &str) -> Result<Value, String> {
        let (c, v) = self
            .client
            .call("GET", &format!("/api/v1/sessions/{id}"), None)?;
        if c == 200 {
            Ok(v)
        } else {
            Err(format!("GET session {id}: {c} {v}"))
        }
    }

    /// GET a session: the status code and body (a 404 is an answer, not an error).
    pub fn session_raw(&self, id: &str) -> Result<(u16, Value), String> {
        self.client
            .call("GET", &format!("/api/v1/sessions/{id}"), None)
    }

    /// The events of one aggregate (oldest first, at most 500).
    pub fn events_for(&self, id: &str) -> Result<Vec<Value>, String> {
        let (c, v) = self.client.call(
            "GET",
            &format!("/api/v1/events?cursor=0&limit=500&aggregate_id={id}"),
            None,
        )?;
        match v["events"].as_array() {
            Some(a) if c == 200 => Ok(a.clone()),
            _ => Err(format!("GET events {id}: {c} {v}")),
        }
    }

    pub fn stop_session(&self, id: &str) -> Result<(u16, Value), String> {
        self.client
            .call("DELETE", &format!("/api/v1/sessions/{id}"), None)
    }
}

impl Drop for Daemon {
    fn drop(&mut self) {
        if !matches!(self.child.try_wait(), Ok(None)) {
            return;
        }
        let _ = signal(self.child.id(), "TERM");
        let deadline = Instant::now() + Duration::from_secs(15);
        while Instant::now() < deadline {
            if !matches!(self.child.try_wait(), Ok(None)) {
                return;
            }
            std::thread::sleep(Duration::from_millis(200));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

/// `kill -<sig> <pid>` through /bin/kill (the workspace forbids unsafe, so no libc).
pub fn signal(pid: u32, sig: &str) -> bool {
    Command::new("/bin/kill")
        .args([format!("-{sig}"), pid.to_string()])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|s| s.success())
}

/// Alive = `ps` shows the pid and it is not a zombie.
pub fn alive(pid: u32) -> bool {
    Command::new("/bin/ps")
        .args(["-o", "stat=", "-p", &pid.to_string()])
        .stderr(Stdio::null())
        .output()
        .map(|o| {
            let st = String::from_utf8_lossy(&o.stdout).trim().to_string();
            !st.is_empty() && !st.starts_with('Z')
        })
        .unwrap_or(false)
}
