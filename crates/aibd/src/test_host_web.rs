//! 「加一台測試機」 on the built-in page (DESIGN-add-test-host-b4297-1121 r1, 5d33a
//! PREREAD-addtesthost r1 passed): `POST /api/v1/act` from the built-in page in this pairing only.
//!
//! - `{"type":"test_host_list"}`: the machines of the owner's ssh config (read as data,
//!   [`crate::test_host_add::machines`]), each usable or with why not, and whether it is pinned.
//!   Nothing connects.
//! - `{"type":"test_host_add","name":…,"ticket"?}`: the fixed read through the owner's login
//!   (journal `receipt.host_probe {name, ok}`, never what it said), then the owner's fingerprint
//!   on the real address and host key; with the approved ticket the read is made again, the line
//!   is written by the fixed script, aibd's own key asks the gate `status`, and only a machine
//!   whose gate answered is pinned (5d33a L2).
//! - `{"type":"test_host_remove","name":…,"ticket"?}`: the same read, the fingerprint, the fixed
//!   removal of every marked line of aibd's key there, the machine taken off the list.
//!
//! The owner's login is used only for those three fixed commands (5d33a B1), never for a
//! receipt; ssh gets no config file and every value spelled out ([`crate::test_host_add::login_args`]).
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::api::AppState;
use crate::test_host_add::{self as th, Machine};
use crate::trusted_hosts::{self, TrustedHost};

pub const LIST: &str = "test_host_list";
pub const ADD: &str = "test_host_add";
pub const REMOVE: &str = "test_host_remove";

/// The run id the check asks the gate about: one no seat makes (`aibd0`), so the gate answers
/// with its own words about a run it does not have.
pub const CHECK_RUN: &str = "web-aibd0-000000000";
/// How long one ssh may take.
const SECS: u64 = 60;
/// How long ssh waits for the connection itself.
const CONNECT_SECS: u32 = 12;
/// What the page is told at most of the list.
const SHOWN_MAX: usize = 40;

/// Machines whose line was written but whose gate did not answer (not pinned): what the card
/// shows as ⚠️ until the machine is added again or taken away (1a93b 11-21 09:49). Kept only
/// while aibd runs; adding again is idempotent there.
static HALF: std::sync::Mutex<Vec<String>> = std::sync::Mutex::new(Vec::new());

fn half_set(name: &str, on: bool) {
    if let Ok(mut h) = HALF.lock() {
        h.retain(|n| n != name);
        if on {
            h.push(name.to_owned());
        }
    }
}

fn half_now() -> Vec<String> {
    HALF.lock().map(|h| h.clone()).unwrap_or_default()
}

/// One read-modify-write of the host list at a time.
static LIST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn programs() -> (PathBuf, PathBuf) {
    if cfg!(windows) {
        (
            "C:\\Windows\\System32\\OpenSSH\\ssh.exe".into(),
            "C:\\Windows\\System32\\OpenSSH\\ssh-keygen.exe".into(),
        )
    } else {
        ("/usr/bin/ssh".into(), "/usr/bin/ssh-keygen".into())
    }
}

/// The owner's home and login name, as aibd's own environment has them.
fn owner() -> Option<(String, String)> {
    let (home, user) = if cfg!(windows) {
        ("USERPROFILE", "USERNAME")
    } else {
        ("HOME", "USER")
    };
    let home = std::env::var(home).ok().filter(|h| !h.is_empty())?;
    let user = std::env::var(user).unwrap_or_default();
    Some((home, user))
}

fn hosts_file(data: &Path) -> PathBuf {
    data.join(trusted_hosts::DIR).join(trusted_hosts::FILE)
}

/// The pinned list now (no file: none).
fn pinned(data: &Path) -> Result<Vec<TrustedHost>, &'static str> {
    let p = hosts_file(data);
    if std::fs::symlink_metadata(&p).is_err() {
        return Ok(Vec::new());
    }
    trusted_hosts::load(&p)
        .map(|(l, _)| l)
        .map_err(|_| "host_list_unreadable")
}

/// What a child keeps of aibd's environment: nothing, except on Windows the few names its
/// system DLLs need to load at all (5d33a N2, same as `receipt_link`'s callers would need).
const KEPT_ENV_WINDOWS: [&str; 3] = ["SystemRoot", "SystemDrive", "WINDIR"];

fn clean_env(cmd: &mut Command) -> &mut Command {
    cmd.env_clear();
    if cfg!(windows) {
        for k in KEPT_ENV_WINDOWS {
            if let Some(v) = std::env::var_os(k) {
                cmd.env(k, v);
            }
        }
    }
    cmd
}

/// `text` into a new file at `p`: whatever was there (a file or a link someone put) is removed
/// first and the file is made new, so a link is never followed (5d33a N3).
fn write_new(p: &Path, text: &str) -> std::io::Result<()> {
    use std::io::Write;
    match std::fs::remove_file(p) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(e),
    }
    std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(p)?
        .write_all(text.as_bytes())
}

/// The list written whole beside itself and moved in place (an empty list: no file).
fn write_pinned(data: &Path, list: &[TrustedHost]) -> Result<(), &'static str> {
    let p = hosts_file(data);
    if list.is_empty() {
        return match std::fs::remove_file(&p) {
            Ok(()) => Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(_) => Err("host_list_unwritable"),
        };
    }
    let dir = data.join(trusted_hosts::DIR);
    std::fs::create_dir_all(&dir).map_err(|_| "host_list_unwritable")?;
    let tmp = dir.join(format!("{}.tmp", trusted_hosts::FILE));
    write_new(&tmp, &th::list_text(list)).map_err(|_| "host_list_unwritable")?;
    std::fs::rename(&tmp, &p).map_err(|_| "host_list_unwritable")
}

/// The machines of the owner's ssh config as this reads them now.
fn machines_now(data: &Path) -> Option<Vec<(String, Result<Machine, &'static str>)>> {
    let (home, user) = owner()?;
    let config =
        std::fs::read_to_string(Path::new(&home).join(".ssh").join("config")).unwrap_or_default();
    let own = data.join(trusted_hosts::DIR);
    Some(th::machines(
        &config,
        &user,
        &home,
        own.to_str().unwrap_or_default(),
    ))
}

/// One ssh, `input` on its standard input, stopped at [`SECS`]: its exit and what it printed
/// (`None`: it could not start or was stopped).
fn run(ssh: &Path, args: &[String], input: Option<String>) -> Option<(bool, String)> {
    let mut child = clean_env(&mut Command::new(ssh))
        .args(args)
        .stdin(if input.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    if let (Some(text), Some(mut w)) = (input, child.stdin.take()) {
        use std::io::Write;
        let _ = w.write_all(text.as_bytes());
    }
    let out = child.stdout.take()?;
    let reader = std::thread::Builder::new()
        .name("aibd-test-host".into())
        .spawn(move || {
            use std::io::Read;
            let mut s = String::new();
            let _ = out.take(64 * 1024).read_to_string(&mut s);
            s
        })
        .ok()?;
    let limit = std::time::Instant::now() + std::time::Duration::from_secs(SECS);
    let op = crate::timers::TEST_HOST_WAIT.begin(Some(limit));
    let status = loop {
        match child.try_wait() {
            Ok(Some(s)) => break Some(s),
            Err(_) => break None,
            Ok(None) => {}
        }
        if !op.tick() {
            let _ = child.kill();
            let _ = child.wait();
            break None;
        }
    };
    let text = reader.join().unwrap_or_default();
    status.map(|s| (s.success(), text))
}

/// The fixed read of `m` through the owner's login.
fn probe(ssh: &Path, m: &Machine) -> Result<th::Read, &'static str> {
    let (home, _) = owner().ok_or("no_home")?;
    let known = Path::new(&home).join(".ssh").join("known_hosts");
    let cmd = th::remote_command(th::READ).ok_or("read_command")?;
    let args = th::login_args(m, &known, &cmd)?;
    match run(ssh, &args, None) {
        Some((true, out)) => th::parse_read(&out),
        _ => Err("owner_login_failed"),
    }
}

/// aibd's own key asks the gate on `h` for [`CHECK_RUN`], trusting only `h`'s host key.
fn check(data: &Path, ssh: &Path, h: &TrustedHost) -> String {
    let dir = data.join(trusted_hosts::DIR);
    let known = dir.join("check_known_hosts");
    if write_new(
        &known,
        &trusted_hosts::known_hosts_text(std::slice::from_ref(h)),
    )
    .is_err()
    {
        return String::new();
    }
    let key = crate::receipt_link::key_path(data);
    let Ok(mut args) = trusted_hosts::ssh_args_file_key(h, &known, &key, CONNECT_SECS) else {
        return String::new();
    };
    args.push(format!("status {CHECK_RUN}"));
    let out = run(ssh, &args, None).map(|(_, o)| o).unwrap_or_default();
    let _ = std::fs::remove_file(&known);
    out
}

fn refuse(status: StatusCode, reason: &str) -> Response {
    let message = match reason {
        "page_only" => "test machines are added from the built-in page in this pairing only",
        "name" => "body must be {type, name, ticket?}; name is one of test_host_list's names",
        "no_such_machine" => "no machine of that name in the owner's ssh config now",
        "no_receipt_gate" => "the receipt gate is not installed on that machine yet",
        "not_a_mac" => "only a Mac can be a test machine for now",
        "owner_login_failed" => "the owner's own ssh login to that machine did not work",
        "write_failed" => "the line could not be written on that machine",
        "remove_failed" => "the line could not be taken away on that machine",
        _ => "the test machine could not be changed",
    };
    let code = match status {
        StatusCode::BAD_REQUEST => "invalid_request",
        StatusCode::NOT_FOUND => "not_found",
        StatusCode::CONFLICT => "conflict",
        StatusCode::BAD_GATEWAY => "upstream",
        StatusCode::INTERNAL_SERVER_ERROR => "internal",
        _ => "forbidden",
    };
    (
        status,
        Json(json!({"code": code, "reason": reason, "message": message,
            "request_id": crate::guard::request_id()})),
    )
        .into_response()
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Raw {
    #[serde(rename = "type")]
    _kind: String,
    name: String,
    #[serde(default)]
    ticket: Option<String>,
}

fn parse(body: Value) -> Option<Raw> {
    serde_json::from_value::<Raw>(body)
        .ok()
        .filter(|r| (1..=64).contains(&r.name.len()))
}

/// What the card shows (the layout is 1a93b's): each machine, usable or why not, pinned or not.
pub fn card(
    machines: &[(String, Result<Machine, &'static str>)],
    pinned: &[TrustedHost],
    half: &[String],
) -> Value {
    let rows: Vec<Value> = machines
        .iter()
        .take(SHOWN_MAX)
        .map(|(name, m)| {
            let on = pinned.iter().any(|p| &p.name == name);
            let h = !on && half.iter().any(|n| n == name);
            match m {
                Ok(m) => json!({"name": name, "usable": true, "pinned": on, "half": h,
                    "address": format!("{}@{}:{}", m.user, m.hostname, m.port)}),
                Err(why) => json!({"name": name, "usable": false, "pinned": on, "half": h,
                    "why": why}),
            }
        })
        .collect();
    json!({"machines": rows, "pinned": pinned.len(), "max": trusted_hosts::MAX_HOSTS})
}

/// `test_host_list`: nothing connects.
pub async fn list(st: AppState) -> Response {
    let data = crate::registry::data_of(&st);
    let got = tokio::task::spawn_blocking(move || {
        let ms = machines_now(&data)?;
        let p = pinned(&data).unwrap_or_default();
        Some(card(&ms, &p, &half_now()))
    })
    .await;
    match got {
        Ok(Some(v)) => Json(v).into_response(),
        _ => refuse(StatusCode::INTERNAL_SERVER_ERROR, "no_home"),
    }
}

/// Why a step failed, as the page is told.
fn status_of(why: &str) -> StatusCode {
    match why {
        "no_such_machine" => StatusCode::NOT_FOUND,
        "owner_login_failed" | "write_failed" | "remove_failed" => StatusCode::BAD_GATEWAY,
        "host_list_unwritable"
        | "host_list_unreadable"
        | "keygen_failed"
        | "key_unsafe"
        | "bad_public_key"
        | "no_home" => StatusCode::INTERNAL_SERVER_ERROR,
        _ => StatusCode::CONFLICT,
    }
}

/// The machine, what its read said, and aibd's public line, read now.
fn facts(data: &Path, name: &str) -> Result<(Machine, th::Read, String), &'static str> {
    let (ssh, keygen) = programs();
    let m = machines_now(data)
        .ok_or("no_home")?
        .into_iter()
        .find(|(n, _)| n == name)
        .ok_or("no_such_machine")?
        .1?;
    let public = crate::receipt_link::ensure_key(data, &keygen)?;
    let r = probe(&ssh, &m);
    Ok((m, r?, public))
}

/// `test_host_add`, for the built-in page `by`.
pub async fn add(st: AppState, body: Value, by: String) -> Response {
    let Some(req) = parse(body) else {
        return refuse(StatusCode::BAD_REQUEST, "name");
    };
    let data = crate::registry::data_of(&st);
    let (d, name) = (data.clone(), req.name.clone());
    let read = tokio::task::spawn_blocking(move || facts(&d, &name)).await;
    let read = read.unwrap_or(Err("no_home"));
    probe_event(&st, &req.name, read.is_ok()).await;
    let (m, r, public) = match read {
        Ok(f) => f,
        Err(why) => return refuse(status_of(why), why),
    };
    let Some(gate) = r.gate.as_deref() else {
        return refuse(StatusCode::CONFLICT, "no_receipt_gate");
    };
    let line = match crate::receipt_link::authorized_line(&public, gate, &r.from) {
        Ok(l) => l,
        Err(why) => return refuse(StatusCode::CONFLICT, why),
    };
    let ask = th::host_ask(th::OP_ADD, &m, &r.hostkey, &line, by.clone());
    let Some(ticket) = req.ticket else {
        return crate::repos::asked(&ask);
    };
    if !crate::owner_ok::approved_for(&ticket, &ask) {
        return crate::repos::not_approved();
    }
    let h = TrustedHost {
        name: m.name.clone(),
        hostname: m.hostname.clone(),
        port: m.port,
        user: m.user.clone(),
        hostkey: r.hostkey.clone(),
        os: "mac".into(),
    };
    let done = tokio::task::spawn_blocking(move || {
        let (ssh, _) = programs();
        let (home, _) = owner().ok_or("no_home")?;
        let known = Path::new(&home).join(".ssh").join("known_hosts");
        let cmd = th::remote_command(th::ADD).ok_or("write_failed")?;
        let args = th::login_args(&m, &known, &cmd)?;
        match run(&ssh, &args, Some(format!("{line}\n"))) {
            Some((true, out)) if out.trim() == "ADDED 1" => {}
            _ => return Err("write_failed"),
        }
        let said = check(&data, &ssh, &h);
        let _held = LIST_LOCK.lock().map_err(|_| "host_list_unwritable")?;
        let list = th::pinned_after_check(&pinned(&data)?, h, &said)?;
        write_pinned(&data, &list)
    })
    .await
    .unwrap_or(Err("host_list_unwritable"));
    let key_id = crate::receipt_link::key_id(&public);
    match done {
        Ok(()) => {
            half_set(&req.name, false);
            crate::seats::side_event(
                &st,
                "receipt",
                "receipt.host_added",
                json!({"name": req.name, "key_id": key_id, "by": by}),
            )
            .await;
            Json(json!({"done": true, "name": req.name, "pinned": true})).into_response()
        }
        // written there but the gate did not answer: not pinned, and the card offers the removal
        Err("gate_did_not_answer") => {
            half_set(&req.name, true);
            Json(json!({"done": true, "name": req.name, "pinned": false,
                "reason": "gate_did_not_answer"}))
            .into_response()
        }
        Err(why) => refuse(status_of(why), why),
    }
}

/// `test_host_remove`, for the built-in page `by`.
pub async fn remove(st: AppState, body: Value, by: String) -> Response {
    let Some(req) = parse(body) else {
        return refuse(StatusCode::BAD_REQUEST, "name");
    };
    let data = crate::registry::data_of(&st);
    let (d, name) = (data.clone(), req.name.clone());
    let read = tokio::task::spawn_blocking(move || facts(&d, &name)).await;
    let read = read.unwrap_or(Err("no_home"));
    probe_event(&st, &req.name, read.is_ok()).await;
    let (m, r, public) = match read {
        // a machine whose gate went away can still be taken away
        Ok(f) => f,
        Err(why) => return refuse(status_of(why), why),
    };
    let key: String = public.split(' ').take(2).collect::<Vec<_>>().join(" ");
    let ask = th::host_ask(th::OP_REMOVE, &m, &r.hostkey, &key, by.clone());
    let Some(ticket) = req.ticket else {
        return crate::repos::asked(&ask);
    };
    if !crate::owner_ok::approved_for(&ticket, &ask) {
        return crate::repos::not_approved();
    }
    let name = req.name.clone();
    let done = tokio::task::spawn_blocking(move || {
        let (ssh, _) = programs();
        let (home, _) = owner().ok_or("no_home")?;
        let known = Path::new(&home).join(".ssh").join("known_hosts");
        let cmd = th::remote_command(th::REMOVE).ok_or("remove_failed")?;
        let args = th::login_args(&m, &known, &cmd)?;
        match run(&ssh, &args, Some(format!("{key}\n"))) {
            Some((true, out)) if out.trim() == "REMOVED 0" => {}
            _ => return Err("remove_failed"),
        }
        let _held = LIST_LOCK.lock().map_err(|_| "host_list_unwritable")?;
        write_pinned(&data, &th::without_host(&pinned(&data)?, &name))
    })
    .await
    .unwrap_or(Err("host_list_unwritable"));
    match done {
        Ok(()) => {
            half_set(&req.name, false);
            crate::seats::side_event(
                &st,
                "receipt",
                "receipt.host_removed",
                json!({"name": req.name, "by": by}),
            )
            .await;
            Json(json!({"done": true, "name": req.name, "pinned": false})).into_response()
        }
        Err(why) => refuse(status_of(why), why),
    }
}

/// `receipt.host_probe {name, ok}`: never what the machine said (5d33a N3).
async fn probe_event(st: &AppState, name: &str, ok: bool) {
    crate::seats::side_event(
        st,
        "receipt",
        "receipt.host_probe",
        json!({"name": name, "ok": ok}),
    )
    .await;
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    #[test]
    fn the_check_asks_a_run_the_gate_accepts_and_no_seat_makes() {
        assert!(
            aib_receipt_gate::run_id_ok(CHECK_RUN),
            "reason: gate_refuses_the_check"
        );
        let seat = CHECK_RUN.split('-').nth(1).unwrap();
        assert!(seat.starts_with("aibd"), "reason: a_seat_code");
    }

    #[test]
    fn the_card_says_what_each_machine_is_and_never_its_key_file() {
        let m = Machine {
            name: "andymini".into(),
            hostname: "192.0.2.7".into(),
            port: 22,
            user: "owner".into(),
            identity_file: "/Users/me/.ssh/id_rsa".into(),
        };
        let pinned = vec![TrustedHost {
            name: "andymini".into(),
            hostname: "192.0.2.7".into(),
            port: 22,
            user: "owner".into(),
            hostkey: "ssh-ed25519 AAAA".into(),
            os: "mac".into(),
        }];
        let c = card(
            &[
                ("andymini".into(), Ok(m)),
                ("jump".into(), Err("reached_through_something_else")),
            ],
            &pinned,
            &["jump".to_owned(), "andymini".to_owned()],
        );
        assert_eq!(c["machines"][0]["address"], "owner@192.0.2.7:22");
        assert_eq!(c["machines"][0]["pinned"], true);
        assert_eq!(c["machines"][0]["half"], false, "reason: pinned_shown_half");
        assert_eq!(c["machines"][1]["half"], true, "reason: half_lost");
        assert_eq!(c["machines"][1]["usable"], false);
        assert_eq!(c["machines"][1]["why"], "reached_through_something_else");
        assert!(!c.to_string().contains("id_rsa"), "reason: key_file_shown");
    }

    #[test]
    fn the_list_is_written_beside_itself_and_none_left_is_no_file() {
        let d = tempfile::tempdir().unwrap();
        let h = TrustedHost {
            name: "andymini".into(),
            hostname: "192.0.2.7".into(),
            port: 22,
            user: "owner".into(),
            hostkey:
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
                    .into(),
            os: "mac".into(),
        };
        assert_eq!(pinned(d.path()).unwrap(), Vec::new());
        write_pinned(d.path(), std::slice::from_ref(&h)).unwrap();
        assert_eq!(pinned(d.path()).unwrap(), vec![h]);
        write_pinned(d.path(), &[]).unwrap();
        assert!(!hosts_file(d.path()).exists(), "reason: empty_list_left");
        assert_eq!(pinned(d.path()).unwrap(), Vec::new());
        // a link put where the temporary file goes is replaced, never written through
        #[cfg(unix)]
        {
            let outside = d.path().join("outside");
            std::fs::write(&outside, "keep").unwrap();
            let tmp = d
                .path()
                .join(trusted_hosts::DIR)
                .join(format!("{}.tmp", trusted_hosts::FILE));
            std::os::unix::fs::symlink(&outside, &tmp).unwrap();
            write_pinned(d.path(), std::slice::from_ref(&h)).unwrap();
            assert_eq!(
                std::fs::read_to_string(&outside).unwrap(),
                "keep",
                "reason: wrote_through_link"
            );
            assert_eq!(pinned(d.path()).unwrap(), vec![h.clone()]);
        }
    }

    #[test]
    fn a_child_keeps_no_environment_but_windows_system_names() {
        let mut c = Command::new("ssh");
        clean_env(&mut c);
        let kept: Vec<String> = c
            .get_envs()
            .filter(|(_, v)| v.is_some())
            .map(|(k, _)| k.to_string_lossy().into_owned())
            .collect();
        if cfg!(windows) {
            assert!(
                std::env::var_os("SystemRoot").is_none() || kept.iter().any(|k| k == "SystemRoot"),
                "reason: windows_without_systemroot {kept:?}"
            );
            assert!(kept.iter().all(|k| KEPT_ENV_WINDOWS.contains(&k.as_str())));
        } else {
            assert_eq!(kept, Vec::<String>::new(), "reason: env_kept_off_windows");
        }
    }

    #[test]
    fn a_body_is_a_name_and_maybe_a_ticket() {
        assert!(parse(json!({"type": ADD, "name": "andymini"})).is_some());
        assert!(parse(json!({"type": ADD, "name": "andymini", "ticket": "ot_1"})).is_some());
        assert!(parse(json!({"type": ADD, "name": ""})).is_none());
        assert!(
            parse(json!({"type": ADD, "name": "a", "hostname": "192.0.2.9"})).is_none(),
            "reason: page_gave_an_address"
        );
    }
}
