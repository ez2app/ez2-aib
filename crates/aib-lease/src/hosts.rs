// SPDX-License-Identifier: Apache-2.0
//! `aib-lease hosts`: the lease state of every test machine at once, read only, and which one
//! to use (A2, c477a 2026-11-12; Andy 04:2x "照 A1→A2→A3 做").
//!
//! Before this, each seat polled the machines by hand (or with its own script) and a machine
//! that became free went unused until someone looked again. This asks every machine in the
//! hosts file **in parallel** with one ssh each (`aib lease status --json`, the 1.x reader of
//! the same files) and prints one JSON object.
//!
//! - Hosts file: `~/.ez2-aib/hosts.json` (`AIB_HOSTS_FILE`), `{"hosts": [{"name", "ssh",
//!   "jump"?, "os"?, "order"?, "kinds"?}]}`. Order: `order`, then file order. No file ⇒ rc 2:
//!   there is nothing to guess. `os` defaults to `mac`; `kinds` lists what a machine takes
//!   (`short` / `medium` / `long`, or a tag such as `chrome` / `windows`), none ⇒ everything
//!   (Andy 04:2x「只跑哪類」, a63ab A2 M1).
//! - `--kind K` / `--os O` / `--owner ME` narrow `--pick` to the machines that take it. A queue
//!   whose head is `ME` is ours to take, so that machine counts as free (a63ab L1).
//! - Per machine: `free`, `queued` (free, but someone is waiting: acquire would put us behind
//!   them, so it is not picked), `busy`, `unreadable` (its lease files cannot be read: fail
//!   closed, never free) or `offline` (ssh failed or the answer was not the tool's).
//! - `--pick`: the JSON's `pick` is the first `free` machine in order. rc 0 one was picked,
//!   4 none free (some reachable), 3 none reachable.
//!   Without `--pick` rc 0 whenever the file could be read.
//! - Nothing here takes, queues or releases a lease: picking and acquiring are not one step,
//!   so a race is still settled by `acquire` (rc 4 ⇒ ask again).
//! - When none is free, `soonest` names the busy machine that should come free first: by the
//!   holder's own estimate (`acquire --expect S`) when it gave one, else by its lease expiry.
//! - `aib-lease run --any-host [--kind K]` (see [`resolve_any_host`]) runs on the first free
//!   machine that takes the run: never a `windows` one (the remote script is bash), and only
//!   one whose `kinds` take K — or, without `--kind`, the class `classify` gives these steps
//!   on that machine (unknown counts as long). None free ⇒ rc 75, nothing ran. Without a
//!   hosts file nothing changes for `--host`; only `--any-host` needs the file.
//! - Known, left to the slice that wires `aib test` (a63ab A2 M2): waiting in **one** queue.
//!   Nothing here queues; when none is free the report names `soonest` and gives
//!   `queue_hint`, the one `queue add` to copy for that machine only. A wait needs a holder
//!   that stays alive on that machine (or heartbeats), which `run` does not have yet.

use crate::run::DEFAULT_REMOTE_PATH;
use serde_json::{json, Value};
use std::path::PathBuf;
use std::process::{Command, Stdio};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HostCfg {
    pub name: String,
    pub ssh: String,
    pub jump: Option<String>,
    pub os: String,
    pub order: i64,
    /// What this machine takes; `None` ⇒ everything.
    pub kinds: Option<Vec<String>>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum State {
    Free,
    Queued,
    Busy,
    Unreadable,
    Offline,
}

impl State {
    pub fn name(self) -> &'static str {
        match self {
            State::Free => "free",
            State::Queued => "queued",
            State::Busy => "busy",
            State::Unreadable => "unreadable",
            State::Offline => "offline",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct HostView {
    pub name: String,
    pub state: State,
    pub owner: Option<String>,
    pub what: Option<String>,
    /// The lease's own expiry (the holder's ttl, not an estimate of when the run ends).
    pub expiry_epoch: Option<f64>,
    /// The holder's own estimate of its end (`acquire --expect`), when it gave one.
    pub expect_end_epoch: Option<f64>,
    pub queue_len: usize,
    pub queue_first: Option<String>,
    /// Why offline / unreadable, short.
    pub why: Option<String>,
}

pub const RC_PICKED: u8 = 0;
pub const RC_USAGE: u8 = 2;
pub const RC_NONE_REACHABLE: u8 = 3;
pub const RC_NONE_FREE: u8 = 4;

fn is_plain_host(s: &str) -> bool {
    !s.is_empty() && !s.starts_with('-') && !s.contains(char::is_whitespace)
}

/// The hosts file ⇒ machines in the order to try. Errors name the entry.
pub fn parse_config(text: &str) -> Result<Vec<HostCfg>, String> {
    let v: Value = serde_json::from_str(text).map_err(|e| format!("hosts file: {e}"))?;
    let list = v
        .get("hosts")
        .and_then(Value::as_array)
        .ok_or("hosts file: needs {\"hosts\": [...]}")?;
    let mut out = Vec::new();
    for (i, h) in list.iter().enumerate() {
        let get = |k: &str| h.get(k).and_then(Value::as_str).map(str::to_string);
        let name = get("name").ok_or(format!("hosts[{i}]: name is required"))?;
        let ssh = get("ssh").unwrap_or_else(|| name.clone());
        let jump = get("jump");
        for s in std::iter::once(&ssh).chain(jump.iter()) {
            if !is_plain_host(s) {
                return Err(format!("hosts[{i}] {name}: ssh / jump {s:?} must not be empty, start with '-' or hold spaces"));
            }
        }
        let kinds = match h.get("kinds") {
            None | Some(Value::Null) => None,
            Some(Value::Array(a)) => Some(
                a.iter()
                    .map(|k| k.as_str().filter(|k| !k.is_empty()).map(str::to_string))
                    .collect::<Option<Vec<_>>>()
                    .ok_or_else(|| {
                        format!("hosts[{i}] {name}: kinds holds only non-empty strings")
                    })?,
            ),
            Some(_) => return Err(format!("hosts[{i}] {name}: kinds is a list")),
        };
        if out.iter().any(|c: &HostCfg| c.name == name) {
            return Err(format!("hosts[{i}]: name {name:?} twice"));
        }
        out.push(HostCfg {
            name,
            ssh,
            jump,
            os: get("os").unwrap_or_else(|| "mac".into()),
            order: h.get("order").and_then(Value::as_i64).unwrap_or(i64::MAX),
            kinds,
        });
    }
    if out.is_empty() {
        return Err("hosts file: no hosts".into());
    }
    // stable: equal `order` keeps file order
    out.sort_by_key(|c| c.order);
    Ok(out)
}

pub fn config_path() -> PathBuf {
    if let Some(p) = std::env::var_os("AIB_HOSTS_FILE") {
        return PathBuf::from(p);
    }
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_default();
    home.join(".ez2-aib").join("hosts.json")
}

/// What one machine said ⇒ its view. `rc` is ssh's exit status (`None`: killed / not run).
/// Only the tool's own answer counts: rc 0 / 4 / 3 **and** a JSON object with a known `state`.
pub fn view(name: &str, rc: Option<i32>, stdout: &str) -> HostView {
    let mut v = HostView {
        name: name.to_string(),
        state: State::Offline,
        owner: None,
        what: None,
        expiry_epoch: None,
        expect_end_epoch: None,
        queue_len: 0,
        queue_first: None,
        why: None,
    };
    // the tool's JSON starts a line; a login banner may hold '{' inside a line (a63ab N1)
    let start = if stdout.starts_with('{') {
        Some(0)
    } else {
        stdout.find("\n{").map(|i| i + 1)
    };
    let doc: Option<Value> = start.and_then(|i| serde_json::from_str(&stdout[i..]).ok());
    let st = doc
        .as_ref()
        .and_then(|d| d.get("state"))
        .and_then(Value::as_str);
    let Some(d) = doc.as_ref().filter(|_| matches!(rc, Some(0 | 3 | 4))) else {
        v.why = Some(match rc {
            Some(255) => "ssh failed".into(),
            Some(c) => format!("rc {c}, no status answer"),
            None => "no exit status".into(),
        });
        return v;
    };
    let s = |x: &Value, k: &str| x.get(k).and_then(Value::as_str).map(str::to_string);
    let q = d.get("queue").and_then(Value::as_array);
    v.queue_len = q.map_or(0, Vec::len);
    v.queue_first = q.and_then(|q| q.first()).and_then(|e| s(e, "owner"));
    v.state = match (rc, st) {
        (Some(3), _) | (_, Some("unreadable")) => State::Unreadable,
        (Some(4), Some("held")) => State::Busy,
        (Some(0), Some("free" | "expired")) if v.queue_len > 0 => State::Queued,
        (Some(0), Some("free" | "expired")) => State::Free,
        _ => {
            // rc and state disagree: not an answer we can act on
            v.why = Some(format!("rc {rc:?} with state {st:?}"));
            State::Unreadable
        }
    };
    if v.state == State::Unreadable && v.why.is_none() {
        v.why = s(d, "why").or(Some("lease files unreadable".into()));
    }
    if let Some(l) = d.get("lease").filter(|_| v.state == State::Busy) {
        v.owner = s(l, "owner");
        v.what = s(l, "what");
        v.expiry_epoch = l.get("expiry_epoch").and_then(Value::as_f64);
        v.expect_end_epoch = l.get("expect_end_epoch").and_then(Value::as_f64);
    }
    v
}

/// `@c477a` and `c477a` are the same seat (a63ab N2).
pub fn same_owner(a: &str, b: &str) -> bool {
    let a = a.trim().trim_start_matches('@');
    !a.is_empty() && a == b.trim().trim_start_matches('@')
}

/// The view as `me` sees it: a free machine whose queue head is `me` is ours to take (L1).
pub fn as_seen_by(mut v: HostView, me: Option<&str>) -> HostView {
    if v.state == State::Queued
        && me.is_some_and(|m| v.queue_first.as_deref().is_some_and(|q| same_owner(q, m)))
    {
        v.state = State::Free;
    }
    v
}

/// Does this machine take runs of `kind`? No `kinds` ⇒ everything. A class nobody could
/// tell (`unknown`) is taken as `long`.
pub fn takes(h: &HostCfg, kind: &str) -> bool {
    let kind = if kind == "unknown" { "long" } else { kind };
    h.kinds
        .as_ref()
        .is_none_or(|ks| ks.iter().any(|k| k == kind))
}

/// The machines not `ok` taken out (views and cfg are in the same order).
pub fn eligible<'a>(
    views: &'a [HostView],
    cfg: &[HostCfg],
    ok: &dyn Fn(&HostCfg) -> bool,
) -> Vec<&'a HostView> {
    views
        .iter()
        .zip(cfg)
        .filter(|(_, h)| ok(h))
        .map(|(v, _)| v)
        .collect()
}

/// When none is free: the busy machine that should come free first, by the holder's estimate
/// when it gave one, else by its expiry. Only to tell a person where to wait; nothing queues.
pub fn soonest<'a>(views: &[&'a HostView]) -> Option<&'a HostView> {
    views
        .iter()
        .copied()
        .filter(|v| v.state == State::Busy)
        .filter_map(|v| v.expect_end_epoch.or(v.expiry_epoch).map(|t| (t, v)))
        .min_by(|a, b| a.0.total_cmp(&b.0))
        .map(|(_, v)| v)
}

/// The first free machine in order (views are already in config order), or why none.
pub fn pick<'a>(views: &[&'a HostView]) -> (Option<&'a str>, u8) {
    if let Some(v) = views.iter().find(|v| v.state == State::Free) {
        return (Some(v.name.as_str()), RC_PICKED);
    }
    let reachable = views
        .iter()
        .any(|v| matches!(v.state, State::Busy | State::Queued));
    (
        None,
        if reachable {
            RC_NONE_FREE
        } else {
            RC_NONE_REACHABLE
        },
    )
}

pub fn view_json(v: &HostView) -> Value {
    json!({
        "name": v.name, "state": v.state.name(), "owner": v.owner, "what": v.what,
        "expiry_epoch": v.expiry_epoch, "expect_end_epoch": v.expect_end_epoch,
        "queue_len": v.queue_len, "queue_first": v.queue_first,
        "why": v.why,
    })
}

/// The command each machine runs: the 1.x reader, with the team's PATH in front.
pub fn remote_status_command() -> String {
    format!("PATH=\"{DEFAULT_REMOTE_PATH}:$PATH\" aib lease status --json")
}

pub fn ssh_args(h: &HostCfg) -> Vec<String> {
    let mut a: Vec<String> = [
        "-o",
        "ForwardAgent=no",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=10",
        "-o",
        "ServerAliveCountMax=3",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    if let Some(j) = &h.jump {
        a.push("-J".into());
        a.push(j.clone());
    }
    a.push("--".into());
    a.push(h.ssh.clone());
    a.push(remote_status_command());
    a
}

fn ask(h: &HostCfg) -> HostView {
    let o = Command::new("ssh")
        .args(ssh_args(h))
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output();
    match o {
        Ok(o) => view(
            &h.name,
            o.status.code(),
            &String::from_utf8_lossy(&o.stdout),
        ),
        Err(e) => {
            let mut v = view(&h.name, None, "");
            v.why = Some(format!("ssh not started: {e}"));
            v
        }
    }
}

/// The hosts file, read and checked. `Err` names the file.
pub fn load_config() -> Result<(PathBuf, Vec<HostCfg>), String> {
    let path = config_path();
    let text = std::fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
    Ok((path, parse_config(&text)?))
}

/// Every machine asked at once; views in config order.
pub fn probe_all(cfg: &[HostCfg], me: Option<&str>) -> Vec<HostView> {
    std::thread::scope(|s| {
        let hs: Vec<_> = cfg
            .iter()
            .map(|h| s.spawn(move || as_seen_by(ask(h), me)))
            .collect();
        hs.into_iter()
            .zip(cfg)
            .map(|(j, h)| {
                j.join().unwrap_or_else(|_| {
                    let mut v = view(&h.name, None, "");
                    v.why = Some("probe panicked".into());
                    v
                })
            })
            .collect()
    })
}

/// The one `queue add` to copy when none is free: for the soonest machine only (M2).
pub fn queue_hint(h: &HostCfg, me: Option<&str>) -> String {
    let jump = h
        .jump
        .as_deref()
        .map(|j| format!("-J {j} "))
        .unwrap_or_default();
    format!(
        "ssh {jump}{} 'aib lease queue add --owner {} --what \"<what runs>\" --run-id <run id> --pid <pid of a waiting shell on that machine>' (one machine only; before moving: aib lease queue remove --run-id <run id>)",
        h.ssh,
        me.unwrap_or("<seat>")
    )
}

/// The one JSON object `hosts` prints (and `run --any-host` prints to stderr).
pub fn report(
    path: &std::path::Path,
    views: &[HostView],
    cfg: &[HostCfg],
    ok: &dyn Fn(&HostCfg) -> bool,
    me: Option<&str>,
) -> (Value, u8) {
    let el = eligible(views, cfg, ok);
    let (picked, rc) = pick(&el);
    let soon = picked.is_none().then(|| soonest(&el)).flatten();
    let hint = soon
        .and_then(|v| cfg.iter().find(|h| h.name == v.name))
        .map(|h| queue_hint(h, me));
    let hosts: Vec<Value> = views
        .iter()
        .zip(cfg)
        .map(|(v, h)| {
            let mut j = view_json(v);
            j["eligible"] = json!(ok(h));
            j
        })
        .collect();
    (
        json!({"result": "hosts", "file": path.display().to_string(), "hosts": hosts,
               "eligible": el.len(), "pick": picked, "soonest": soon.map(|v| v.name.clone()),
               "queue_hint": hint}),
        rc,
    )
}

pub fn cli(argv: &[String]) -> u8 {
    let usage = |e: String| {
        println!(
            "{}",
            json!({"result": "usage", "error": format!("{e}; aib-lease hosts [--pick] [--kind K] [--os O] [--owner ME]")})
        );
        RC_USAGE
    };
    let mut want_pick = false;
    let (mut kind, mut os, mut me) = (None, None, std::env::var("AIB_LEASE_OWNER").ok());
    let mut it = argv.iter();
    while let Some(a) = it.next() {
        let mut val = || it.next().cloned().filter(|v| !v.is_empty());
        match a.as_str() {
            "--pick" => want_pick = true,
            "--kind" => match val() {
                Some(v) => kind = Some(v),
                None => return usage("--kind needs a value".into()),
            },
            "--os" => match val() {
                Some(v) => os = Some(v),
                None => return usage("--os needs a value".into()),
            },
            "--owner" => match val() {
                Some(v) => me = Some(v),
                None => return usage("--owner needs a value".into()),
            },
            _ => return usage(format!("unknown argument {a}")),
        }
    }
    let (path, cfg) = match load_config() {
        Ok(c) => c,
        Err(e) => {
            println!("{}", json!({"result": "usage", "error": e}));
            return RC_USAGE;
        }
    };
    let me = me.filter(|m| !m.is_empty());
    let views = probe_all(&cfg, me.as_deref());
    let ok = |h: &HostCfg| {
        kind.as_deref().is_none_or(|k| takes(h, k)) && os.as_deref().is_none_or(|o| h.os == o)
    };
    let (doc, rc) = report(&path, &views, &cfg, &ok, me.as_deref());
    println!("{doc}");
    if want_pick {
        rc
    } else {
        RC_PICKED
    }
}

/// The arguments before `--` (after it they belong to the command being run).
fn own_args(argv: &[String]) -> &[String] {
    &argv[..argv.iter().position(|a| a == "--").unwrap_or(argv.len())]
}

/// `run --any-host` asked for? Not together with `--host` / `--jump`: the hosts file names both.
pub fn wants_any_host(argv: &[String]) -> Result<bool, String> {
    let own = own_args(argv);
    if !own.iter().any(|a| a == "--any-host") {
        return Ok(false);
    }
    if own.iter().any(|a| a == "--host" || a == "--jump") {
        return Err(
            "--any-host takes the host and its jump from the hosts file: no --host / --jump".into(),
        );
    }
    Ok(true)
}

/// The value after `flag` among our own arguments (before `--`).
pub fn own_value(argv: &[String], flag: &str) -> Option<String> {
    let own = own_args(argv);
    own.iter()
        .position(|a| a == flag)
        .and_then(|i| own.get(i + 1))
        .cloned()
}

/// The steps `run` will send, as `run` builds them: each `--step`, then `-- CMD ARGS` as one.
pub fn steps_of(argv: &[String]) -> Vec<String> {
    let own = own_args(argv);
    let mut steps: Vec<String> = own
        .windows(2)
        .filter(|w| w[0] == "--step")
        .map(|w| w[1].clone())
        .collect();
    if own.len() < argv.len() && argv.len() > own.len() + 1 {
        steps.push(crate::run::join_command(&argv[own.len() + 1..]));
    }
    steps
}

/// `argv` with `--any-host` (before `--`) replaced by the chosen machine's `--host` / `--jump`,
/// and `--kind K` (ours, not `run`'s) taken out.
pub fn with_host(argv: &[String], h: &HostCfg) -> Vec<String> {
    let cut = own_args(argv).len();
    let mut out = Vec::with_capacity(argv.len() + 3);
    let mut skip = false;
    for (i, a) in argv.iter().enumerate() {
        if skip {
            skip = false;
            continue;
        }
        if i < cut && a == "--kind" {
            skip = true;
            continue;
        }
        if i < cut && a == "--any-host" {
            out.push("--host".to_string());
            out.push(h.ssh.clone());
            if let Some(j) = &h.jump {
                out.push("--jump".to_string());
                out.push(j.clone());
            }
        } else {
            out.push(a.clone());
        }
    }
    out
}

/// For `aib-lease run`: `--any-host` ⇒ ask every machine, run on the first free one.
/// `Err(rc)`: 2 no hosts file / bad arguments; 75 none free (nothing ran — the same code as a
/// lease not granted, so a caller's retry loop needs nothing new). The machines' state goes
/// to stderr either way; nothing queues here.
pub fn resolve_any_host(argv: &[String]) -> Result<Vec<String>, u8> {
    match wants_any_host(argv) {
        Ok(false) => return Ok(argv.to_vec()),
        Ok(true) => {}
        Err(e) => {
            eprintln!("aib-lease run: {e}");
            return Err(RC_USAGE);
        }
    }
    if own_args(argv).iter().any(|a| a == "--kind")
        && own_value(argv, "--kind").is_none_or(|k| k.is_empty() || k.starts_with("--"))
    {
        eprintln!("aib-lease run: --kind needs a value");
        return Err(RC_USAGE);
    }
    let (path, cfg) = load_config().map_err(|e| {
        eprintln!("aib-lease run --any-host: {e}");
        RC_USAGE
    })?;
    let kind = own_value(argv, "--kind");
    let me = own_value(argv, "--owner")
        .or_else(|| std::env::var("AIB_LEASE_OWNER").ok())
        .filter(|m| !m.is_empty());
    let steps = steps_of(argv);
    let history = std::fs::read_to_string(crate::run::history_path()).unwrap_or_default();
    let long = crate::classify::read_long_config(
        &std::fs::read_to_string(crate::classify::long_config_path()).unwrap_or_default(),
    );
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    // the remote script is bash: never a windows machine; then what the machine takes
    let ok = |h: &HostCfg| {
        h.os != "windows"
            && match kind.as_deref() {
                Some(k) => takes(h, k),
                None => takes(
                    h,
                    crate::classify::classify(&h.ssh, &steps, &history, &long, now)
                        .class
                        .as_str(),
                ),
            }
    };
    let views = probe_all(&cfg, me.as_deref());
    let (doc, _) = report(&path, &views, &cfg, &ok, me.as_deref());
    eprintln!("aib-lease: {doc}");
    let picked = doc["pick"]
        .as_str()
        .and_then(|n| cfg.iter().find(|h| h.name == n));
    match picked {
        Some(h) => {
            eprintln!("aib-lease: --any-host: {} is free", h.name);
            Ok(with_host(argv, h))
        }
        None => {
            eprintln!("aib-lease: --any-host: no machine that takes this run is free, nothing ran");
            Err(75)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const FREE: &str = r#"{"state": "free", "lease": null, "why": null, "queue": []}"#;

    #[test]
    fn config_orders_by_order_then_file_and_defaults_ssh_to_name() {
        let c = parse_config(
            r#"{"hosts": [{"name": "c", "order": 3}, {"name": "a", "ssh": "u@a", "order": 1},
                          {"name": "w", "os": "windows"}, {"name": "b", "order": 1}]}"#,
        )
        .unwrap();
        let names: Vec<_> = c.iter().map(|h| h.name.as_str()).collect();
        assert_eq!(names, ["a", "b", "c", "w"]);
        assert_eq!(c[2].ssh, "c");
        assert_eq!(c[0].ssh, "u@a");
        assert_eq!(c[3].os, "windows");
    }

    #[test]
    fn config_refuses_option_like_hosts_twins_and_empty() {
        assert!(parse_config(r#"{"hosts": [{"name": "a", "ssh": "-oProxyCommand=x"}]}"#).is_err());
        assert!(parse_config(r#"{"hosts": [{"name": "a", "jump": "-J"}]}"#).is_err());
        assert!(parse_config(r#"{"hosts": [{"name": "a", "ssh": "a b"}]}"#).is_err());
        assert!(parse_config(r#"{"hosts": [{"name": "a"}, {"name": "a"}]}"#).is_err());
        assert!(parse_config(r#"{"hosts": []}"#).is_err());
        assert!(parse_config(r#"{"hosts": [{"ssh": "a"}]}"#).is_err());
        assert!(parse_config("not json").is_err());
    }

    #[test]
    fn free_is_free_only_with_rc_0_and_an_empty_queue() {
        assert_eq!(view("a", Some(0), FREE).state, State::Free);
        let expired = r#"{"state": "expired", "lease": {"owner": "x"}, "queue": []}"#;
        assert_eq!(view("a", Some(0), expired).state, State::Free);
        let waiting = r#"{"state": "free", "queue": [{"owner": "@4542e"}]}"#;
        let v = view("a", Some(0), waiting);
        assert_eq!(v.state, State::Queued);
        assert_eq!(v.queue_first.as_deref(), Some("@4542e"));
    }

    #[test]
    fn busy_carries_the_holder() {
        let held = r#"{"state": "held", "lease": {"owner": "@c477a", "what": "aibd",
                       "expiry_epoch": 1794430000.5}, "queue": [{"owner": "@f1fae"}]}"#;
        let v = view("a", Some(4), held);
        assert_eq!(v.state, State::Busy);
        assert_eq!(v.owner.as_deref(), Some("@c477a"));
        assert_eq!(v.expiry_epoch, Some(1794430000.5));
        assert_eq!(v.queue_len, 1);
    }

    #[test]
    fn unreadable_and_disagreeing_answers_are_never_free() {
        let un = r#"{"state": "unreadable", "why": "bad json", "queue": []}"#;
        assert_eq!(view("a", Some(3), un).state, State::Unreadable);
        // rc 0 but the tool says held, or rc 4 but free: do not act on it
        let held = r#"{"state": "held", "lease": {}, "queue": []}"#;
        assert_eq!(view("a", Some(0), held).state, State::Unreadable);
        assert_eq!(view("a", Some(4), FREE).state, State::Unreadable);
        assert_eq!(
            view("a", Some(0), r#"{"state": "maybe"}"#).state,
            State::Unreadable
        );
    }

    #[test]
    fn anything_but_the_tools_answer_is_offline() {
        assert_eq!(view("a", Some(255), "").state, State::Offline);
        assert_eq!(view("a", None, FREE).state, State::Offline);
        assert_eq!(
            view("a", Some(0), "zsh: command not found: aib").state,
            State::Offline
        );
        assert_eq!(view("a", Some(127), FREE).state, State::Offline);
        // a login banner before the JSON is fine
        assert_eq!(
            view("a", Some(0), &format!("Welcome\n{FREE}")).state,
            State::Free
        );
    }

    #[test]
    fn pick_takes_the_first_free_in_order_and_says_why_not() {
        let f = |n: &str, s| HostView {
            state: s,
            ..view(n, Some(0), FREE)
        };
        let vs = [
            f("a", State::Busy),
            f("b", State::Queued),
            f("c", State::Free),
            f("d", State::Free),
        ];
        let r: Vec<&HostView> = vs.iter().collect();
        assert_eq!(pick(&r), (Some("c"), RC_PICKED));
        assert_eq!(pick(&r[..2]), (None, RC_NONE_FREE));
        let dead = [f("a", State::Offline), f("b", State::Unreadable)];
        let r: Vec<&HostView> = dead.iter().collect();
        assert_eq!(pick(&r), (None, RC_NONE_REACHABLE));
    }

    #[test]
    fn soonest_prefers_the_holders_estimate_and_skips_what_is_not_busy() {
        let busy = |n: &str, exp: Option<f64>, end: Option<f64>| HostView {
            state: State::Busy,
            expiry_epoch: exp,
            expect_end_epoch: end,
            ..view(n, Some(0), FREE)
        };
        // b's lease runs longer, but its holder says it ends first
        let vs = [
            busy("a", Some(2000.0), None),
            busy("b", Some(9000.0), Some(1500.0)),
        ];
        let r: Vec<&HostView> = vs.iter().collect();
        assert_eq!(soonest(&r).map(|v| v.name.as_str()), Some("b"));
        let held = r#"{"state": "held", "lease": {"owner": "o", "expiry_epoch": 9000,
                       "expect_end_epoch": 1500}, "queue": []}"#;
        assert_eq!(view("x", Some(4), held).expect_end_epoch, Some(1500.0));
        let none = [HostView {
            state: State::Offline,
            ..busy("c", Some(1.0), None)
        }];
        assert!(soonest(&[&none[0]]).is_none());
    }

    fn host(name: &str, os: &str, kinds: Option<&[&str]>) -> HostCfg {
        HostCfg {
            name: name.into(),
            ssh: name.into(),
            jump: None,
            os: os.into(),
            order: 1,
            kinds: kinds.map(|k| k.iter().map(|s| s.to_string()).collect()),
        }
    }

    // a63ab M1: a machine takes only the kinds it lists; unknown counts as long
    #[test]
    fn a_machine_takes_only_its_kinds() {
        let all = host("a", "mac", None);
        let short = host("b", "mac", Some(&["short", "medium"]));
        assert!(takes(&all, "long") && takes(&all, "chrome"));
        assert!(takes(&short, "short") && !takes(&short, "long"));
        assert!(!takes(&short, "unknown"), "unknown is long");
        assert!(takes(&host("c", "mac", Some(&["long"])), "unknown"));
        let c =
            parse_config(r#"{"hosts": [{"name": "w", "os": "windows", "kinds": ["windows"]}]}"#)
                .unwrap();
        assert_eq!(c[0].kinds, Some(vec!["windows".to_string()]));
        assert!(parse_config(r#"{"hosts": [{"name": "w", "kinds": "short"}]}"#).is_err());
        assert!(parse_config(r#"{"hosts": [{"name": "w", "kinds": ["short", ""]}]}"#).is_err());
        assert!(parse_config(r#"{"hosts": [{"name": "w", "kinds": [1]}]}"#).is_err());
    }

    // a63ab M1 + M2: the free machine that does not take the run is passed over; none left
    // ⇒ the soonest one that does take it, with the one queue add to copy for it
    #[test]
    fn the_report_picks_among_the_machines_that_take_the_run_and_hints_one_queue() {
        let cfg = [
            host("win", "windows", None),
            host("short", "mac", Some(&["short"])),
            host("big", "mac", None),
        ];
        let free = view("x", Some(0), FREE);
        let busy = |end: f64| HostView {
            state: State::Busy,
            expiry_epoch: Some(end),
            ..free.clone()
        };
        let named = |n: &str, v: HostView| HostView {
            name: n.into(),
            ..v
        };
        let views = [
            named("win", free.clone()),
            named("short", free.clone()),
            named("big", busy(9000.0)),
        ];
        let ok = |h: &HostCfg| h.os != "windows" && takes(h, "long");
        let (doc, rc) = report(
            std::path::Path::new("/h.json"),
            &views,
            &cfg,
            &ok,
            Some("@c477a"),
        );
        assert_eq!(
            (doc["pick"].clone(), rc),
            (Value::Null, RC_NONE_FREE),
            "{doc}"
        );
        assert_eq!(doc["eligible"], 1);
        assert_eq!(doc["soonest"], "big");
        let hint = doc["queue_hint"].as_str().unwrap();
        assert!(
            hint.starts_with("ssh big 'aib lease queue add --owner @c477a "),
            "{hint}"
        );
        assert_eq!(doc["hosts"][0]["eligible"], false);
        // a short run may take the short machine
        let ok = |h: &HostCfg| h.os != "windows" && takes(h, "short");
        let (doc, rc) = report(std::path::Path::new("/h.json"), &views, &cfg, &ok, None);
        assert_eq!((doc["pick"].as_str(), rc), (Some("short"), RC_PICKED));
        assert_eq!(doc["queue_hint"], Value::Null);
    }

    // a63ab L1 / N2: a queue whose head is me is mine to take
    #[test]
    fn a_queue_headed_by_me_is_free_for_me_only() {
        let q = view(
            "a",
            Some(0),
            r#"{"state": "free", "queue": [{"owner": "@c477a"}]}"#,
        );
        assert_eq!(q.state, State::Queued);
        assert_eq!(as_seen_by(q.clone(), Some("c477a")).state, State::Free);
        assert_eq!(as_seen_by(q.clone(), Some("@c477a")).state, State::Free);
        assert_eq!(as_seen_by(q.clone(), Some("4542e")).state, State::Queued);
        assert_eq!(as_seen_by(q.clone(), None).state, State::Queued);
        assert_eq!(as_seen_by(q, Some("@")).state, State::Queued);
        let busy = view(
            "a",
            Some(4),
            r#"{"state": "held", "lease": {"owner": "@c477a"}, "queue": []}"#,
        );
        assert_eq!(as_seen_by(busy, Some("c477a")).state, State::Busy);
    }

    // a63ab N1: a banner with '{' inside a line does not hide the answer
    #[test]
    fn a_banner_with_a_brace_inside_a_line_is_skipped() {
        let out = format!("Last login {{tty}} from x\n{FREE}");
        assert_eq!(view("a", Some(0), &out).state, State::Free);
    }

    #[test]
    fn steps_are_read_as_run_builds_them_and_kind_is_taken_out() {
        let v = |x: &[&str]| x.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        let a = v(&[
            "--any-host",
            "--kind",
            "short",
            "--step",
            "cargo fmt",
            "--",
            "cargo",
            "test",
        ]);
        assert_eq!(steps_of(&a), v(&["cargo fmt", "'cargo' 'test'"]));
        assert_eq!(own_value(&a, "--kind").as_deref(), Some("short"));
        let h = host("m", "mac", None);
        assert_eq!(
            with_host(&a, &h),
            v(&["--host", "m", "--step", "cargo fmt", "--", "cargo", "test"])
        );
        // after `--` it is the command's
        let b = v(&["--any-host", "--", "x", "--kind", "y"]);
        assert_eq!(
            with_host(&b, &h),
            v(&["--host", "m", "--", "x", "--kind", "y"])
        );
        assert_eq!(own_value(&b, "--kind"), None);
    }

    #[test]
    fn any_host_is_replaced_before_the_double_dash_only() {
        let v = |x: &[&str]| x.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        let h = HostCfg {
            name: "mini".into(),
            ssh: "u@mini".into(),
            jump: Some("j".into()),
            os: "mac".into(),
            order: 1,
            kinds: None,
        };
        let a = v(&["--any-host", "--step", "x", "--", "echo", "--any-host"]);
        assert_eq!(wants_any_host(&a), Ok(true));
        assert_eq!(
            with_host(&a, &h),
            v(&[
                "--host",
                "u@mini",
                "--jump",
                "j",
                "--step",
                "x",
                "--",
                "echo",
                "--any-host"
            ])
        );
        // only the command says it: not ours
        assert_eq!(
            wants_any_host(&v(&["--host", "h", "--", "--any-host"])),
            Ok(false)
        );
        assert!(wants_any_host(&v(&["--any-host", "--host", "h"])).is_err());
        assert!(wants_any_host(&v(&["--jump", "j", "--any-host"])).is_err());
        let plain = v(&["--host", "h", "--step", "x"]);
        assert_eq!(resolve_any_host(&plain), Ok(plain.clone()));
    }

    #[test]
    fn ssh_is_hardened_and_the_host_comes_after_the_double_dash() {
        let h = HostCfg {
            name: "a".into(),
            ssh: "u@a".into(),
            jump: Some("j".into()),
            os: "mac".into(),
            order: 1,
            kinds: None,
        };
        let a = ssh_args(&h);
        let j = a.join(" ");
        for o in [
            "ForwardAgent=no",
            "ClearAllForwardings=yes",
            "BatchMode=yes",
            "ConnectTimeout=10",
        ] {
            assert!(j.contains(o), "{j}");
        }
        let dd = a.iter().position(|x| x == "--").unwrap();
        assert_eq!(a[dd + 1], "u@a");
        assert!(a[dd + 2].ends_with("aib lease status --json"));
        assert!(a.iter().position(|x| x == "-J").unwrap() < dd);
    }
}
