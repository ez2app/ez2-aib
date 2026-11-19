//! `GET /api/v1/hosts` (N-workspace third wave, 7b81e item 2; DESIGN-workspace-hosts-7b81e-1118,
//! a9136 11-18 12:03): how busy every test machine is, read only, from the same hosts file and
//! the same per-machine answer `aib-lease hosts` reads (`aib_lease::hosts`: config, ssh
//! arguments, answer parsing); nothing here takes, queues or releases a lease.
//!
//! - ssh: BatchMode, no agent or forwarding, connect timeout 5 s (`ssh_args_timeout`); each
//!   child is killed at [`ROUND_LIMIT`] by the registered timer `hosts.probe_wait`, so a hung
//!   machine never holds aibd. On Windows the system `ssh.exe` runs directly.
//! - One round at a time; a request that comes while one runs waits for it. The answer, a
//!   failed one too, is kept [`CACHE`] (a page polling every 2 s asks at most once a minute).
//! - The answer names machines by their hosts-file nickname only: no ssh target, user, jump or
//!   path (public repo rule §16), and only seat / run / queue length of who holds a machine.
//! - Built-in page only (a user folder page: 403, in the guard of this handler).

use crate::guard::{request_id, Caller};
use aib_lease::hosts::{self, HostCfg, HostView};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::{Extension, Json};
use serde_json::{json, Value};
use std::io::Read;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

/// How long an answer is kept.
pub const CACHE: Duration = Duration::from_secs(60);
/// The longest one machine's probe may take before its ssh is killed.
pub const ROUND_LIMIT: Duration = Duration::from_secs(15);
/// ssh connect timeout, seconds (a9136: ≤ 5).
pub const CONNECT_S: u32 = 5;
/// The most characters of a holder's `what` shown.
pub const WHAT_MAX: usize = 120;

static LAST: tokio::sync::Mutex<Option<(Instant, Value)>> = tokio::sync::Mutex::const_new(None);

fn refuse(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({ "code": code, "message": message, "request_id": request_id() })),
    )
        .into_response()
}

/// One machine as the page sees it: nickname, state, who holds it (seat, run), queue length,
/// load and disk flags; never where it is.
pub fn host_json(v: &HostView) -> Value {
    let what = v
        .what
        .as_deref()
        .map(|w| w.chars().take(WHAT_MAX).collect::<String>());
    json!({
        "name": v.name,
        "state": v.state.name(),
        "seat": v.owner,
        "what": what,
        "queue_len": v.queue_len,
        "queue_first": v.queue_first,
        "expect_end_epoch": v.expect_end_epoch,
        // a9136 hosts core 3: run id and start time (N-workspace-c2); the run id is cut like what
        "start_epoch": v.start_epoch,
        "run_id": v.run_id.as_deref().map(|r| r.chars().take(WHAT_MAX).collect::<String>()),
        "hot": hosts::is_hot(v),
        "unleased_busy": hosts::unleased_busy(v),
        "low_disk": hosts::low_disk(v),
        "disk_free_gb": v.disk_free_kb.map(|kb| kb / (1024 * 1024)),
        "load_per_core": hosts::load_ratio(v),
        // offline / unreadable: only the parser's own short words, never ssh's message
        "why": v.why.as_deref().filter(|_| v.state.name() == "offline" || v.state.name() == "unreadable"),
    })
}

/// The answer for a set of views, with when it was asked.
pub fn body(views: &[HostView], asof: &str) -> Value {
    let free = views.iter().filter(|v| v.state.name() == "free").count();
    json!({
        "source": "aib-lease",
        "asof": asof,
        "free": free,
        "hosts": views.iter().map(host_json).collect::<Vec<_>>(),
    })
}

/// Run one ssh (or the local probe), at most [`ROUND_LIMIT`]; (exit code, stdout).
fn run(mut cmd: Command) -> (Option<i32>, String) {
    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let Ok(mut child) = cmd.spawn() else {
        return (None, String::new());
    };
    // read while it runs, so a long answer cannot fill the pipe and hold the child
    let reader = child.stdout.take().map(|mut out| {
        std::thread::spawn(move || {
            let mut s = String::new();
            let _ = out.by_ref().take(1 << 20).read_to_string(&mut s);
            s
        })
    });
    let op = crate::timers::HOSTS_PROBE_WAIT.begin(Some(Instant::now() + ROUND_LIMIT));
    let code = loop {
        match child.try_wait() {
            Ok(Some(s)) => break s.code(),
            Ok(None) => {}
            Err(_) => break None,
        }
        if !op.tick() {
            if let Ok(Some(s)) = child.try_wait() {
                break s.code();
            }
            let _ = child.kill();
            let _ = child.wait();
            break None;
        }
    };
    let out = reader.and_then(|r| r.join().ok()).unwrap_or_default();
    (code, out)
}

/// One machine, the way `aib-lease hosts` asks it (its fallback jump only after a network
/// failure, rc 255), with aibd's own timeouts.
fn ask(h: &HostCfg) -> HostView {
    if h.ssh == "local" {
        let mut c = Command::new("sh");
        c.args(["-c", &hosts::remote_status_command()]);
        let (rc, out) = run(c);
        return hosts::view(&h.name, rc, &out);
    }
    let go = |jump: Option<&str>| {
        let mut c = Command::new("ssh");
        c.args(hosts::ssh_args_timeout(h, jump, CONNECT_S));
        run(c)
    };
    let mut got = go(h.jump.as_deref());
    if got.0 == Some(255) {
        if let Some(fb) = h.jump_fallback.as_deref() {
            got = go(Some(fb));
        }
    }
    let mut v = if h.os == "windows" {
        hosts::windows_view(&h.name, got.0, &got.1)
    } else {
        hosts::view(&h.name, got.0, &got.1)
    };
    if got.0.is_none() && v.why.is_none() {
        v.why = Some("no answer in 15 s".into());
    }
    v
}

/// Every machine in the hosts file at once; `None` = no hosts file (nothing to show).
pub fn probe() -> Option<Vec<HostView>> {
    let (_, cfg) = hosts::load_config().ok()?;
    Some(std::thread::scope(|s| {
        let hs: Vec<_> = cfg.iter().map(|h| s.spawn(move || ask(h))).collect();
        hs.into_iter()
            .zip(&cfg)
            .map(|(j, h)| {
                j.join().unwrap_or_else(|_| {
                    let mut v = hosts::view(&h.name, None, "");
                    v.why = Some("probe failed".into());
                    v
                })
            })
            .collect()
    }))
}

/// `GET /api/v1/hosts`.
pub async fn list(Extension(c): Extension<Caller>) -> Response {
    let built_in = match &c {
        Caller::StartupToken => true,
        Caller::Page(p) => !crate::seat_input::held_as_user_page(p).await,
        Caller::Anonymous | Caller::Seat(_) => false,
    };
    if !built_in {
        return refuse(
            StatusCode::FORBIDDEN,
            "hosts_built_in_page_only",
            "machine status is shown on the built-in page only",
        );
    }
    // one round at a time: a request that comes during a round waits here for its answer
    let mut last = LAST.lock().await;
    if let Some((at, v)) = last.as_ref() {
        if at.elapsed() < CACHE {
            let mut v = v.clone();
            v["age_s"] = json!(at.elapsed().as_secs());
            return Json(v).into_response();
        }
    }
    let now = crate::sessions::now_rfc3339();
    let v = match tokio::task::spawn_blocking(probe).await {
        Ok(Some(views)) => body(&views, &now),
        Ok(None) => json!({"source": "none", "asof": now, "free": 0, "hosts": []}),
        Err(_) => json!({"source": "aib-lease", "asof": now, "free": 0, "hosts": [],
                         "why": "probe failed"}),
    };
    *last = Some((Instant::now(), v.clone()));
    let mut v = v;
    v["age_s"] = json!(0);
    Json(v).into_response()
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    #[test]
    fn a_machine_is_shown_by_its_nickname_with_who_holds_it_and_never_where_it_is() {
        let held = r#"{"state": "held", "lease": {"owner": "7b81e", "what": "7b81e-wsn2 全工作區整套",
            "expiry_epoch": 1.0, "acquired_epoch": 0.5, "run_id": "7b81e-wsn2-1",
            "host": "user@198.51.100.7", "cwd": "/Users/x/wt"}, "queue": [{"owner": "043dd"}]}"#;
        let v = hosts::view("andymini", Some(4), held);
        let j = host_json(&v);
        assert_eq!(j["name"], "andymini");
        assert_eq!(j["state"], "busy");
        assert_eq!(j["seat"], "7b81e");
        assert_eq!(j["queue_len"], 1);
        assert_eq!(j["queue_first"], "043dd");
        assert_eq!(j["start_epoch"], 0.5, "reason: start_missing");
        assert_eq!(j["run_id"], "7b81e-wsn2-1");
        let text = j.to_string();
        assert!(
            !text.contains("198.51.100.7") && !text.contains('@') && !text.contains("/Users"),
            "reason: where_shown {text}"
        );
        let off = hosts::view("pro13", Some(255), "");
        let j = host_json(&off);
        assert_eq!(j["state"], "offline");
        assert_eq!(j["why"], "ssh failed");
        let b = body(&[hosts::view("andymini", Some(4), held), off], "t");
        assert_eq!(
            (b["free"].as_u64(), b["hosts"].as_array().unwrap().len()),
            (Some(0), 2)
        );
        assert!(b.get("file").is_none(), "reason: path_shown");
    }

    #[test]
    fn every_ssh_is_batch_with_no_forwarding_and_a_five_second_connect() {
        let cfg =
            hosts::parse_config(r#"{"hosts": [{"name": "m", "ssh": "u@m.invalid"}]}"#).unwrap();
        let a = hosts::ssh_args_timeout(&cfg[0], None, CONNECT_S).join(" ");
        for want in [
            "BatchMode=yes",
            "ForwardAgent=no",
            "ClearAllForwardings=yes",
            "ConnectTimeout=5",
        ] {
            assert!(a.contains(want), "reason: ssh_{want} {a}");
        }
    }

    #[test]
    fn a_long_holder_label_is_cut() {
        let long = format!(
            r#"{{"state": "held", "lease": {{"owner": "a", "what": "{}"}}, "queue": []}}"#,
            "字".repeat(500)
        );
        let j = host_json(&hosts::view("m", Some(4), &long));
        assert_eq!(j["what"].as_str().unwrap().chars().count(), WHAT_MAX);
    }
}
