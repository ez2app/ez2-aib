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
//!   "jump"?, "os"?, "order"?}]}`. Order: `order`, then file order. No file ⇒ rc 2: there is
//!   nothing to guess.
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
//! - `aib-lease run --any-host` (see [`resolve_any_host`]) runs on the first free machine;
//!   none free ⇒ rc 75, nothing ran. Without a hosts file nothing changes for `--host`; only
//!   `--any-host` needs the file.

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
        if out.iter().any(|c: &HostCfg| c.name == name) {
            return Err(format!("hosts[{i}]: name {name:?} twice"));
        }
        out.push(HostCfg {
            name,
            ssh,
            jump,
            os: get("os").unwrap_or_else(|| "mac".into()),
            order: h.get("order").and_then(Value::as_i64).unwrap_or(i64::MAX),
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
    let doc: Option<Value> = stdout
        .find('{')
        .and_then(|i| serde_json::from_str(&stdout[i..]).ok());
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

/// When none is free: the busy machine that should come free first, by the holder's estimate
/// when it gave one, else by its expiry. Only to tell a person where to wait; nothing queues.
pub fn soonest(views: &[HostView]) -> Option<&HostView> {
    views
        .iter()
        .filter(|v| v.state == State::Busy)
        .filter_map(|v| v.expect_end_epoch.or(v.expiry_epoch).map(|t| (t, v)))
        .min_by(|a, b| a.0.total_cmp(&b.0))
        .map(|(_, v)| v)
}

/// The first free machine in order (views are already in config order), or why none.
pub fn pick(views: &[HostView]) -> (Option<&str>, u8) {
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
pub fn probe_all(cfg: &[HostCfg]) -> Vec<HostView> {
    std::thread::scope(|s| {
        let hs: Vec<_> = cfg.iter().map(|h| s.spawn(move || ask(h))).collect();
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

/// The one JSON object `hosts` prints (and `run --any-host` prints when it picks nothing).
pub fn report(path: &std::path::Path, views: &[HostView]) -> Value {
    let (picked, _) = pick(views);
    json!({"result": "hosts", "file": path.display().to_string(),
           "hosts": views.iter().map(view_json).collect::<Vec<_>>(),
           "pick": picked, "soonest": soonest(views).map(|v| v.name.clone())})
}

pub fn cli(argv: &[String]) -> u8 {
    let mut want_pick = false;
    for a in argv {
        match a.as_str() {
            "--pick" => want_pick = true,
            _ => {
                println!(
                    "{}",
                    json!({"result": "usage", "error": format!("unknown argument {a}; aib-lease hosts [--pick]")})
                );
                return RC_USAGE;
            }
        }
    }
    let (path, cfg) = match load_config() {
        Ok(c) => c,
        Err(e) => {
            println!("{}", json!({"result": "usage", "error": e}));
            return RC_USAGE;
        }
    };
    let views = probe_all(&cfg);
    println!("{}", report(&path, &views));
    if want_pick {
        pick(&views).1
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

/// `argv` with `--any-host` (before `--`) replaced by the chosen machine's `--host` / `--jump`.
pub fn with_host(argv: &[String], h: &HostCfg) -> Vec<String> {
    let cut = own_args(argv).len();
    let mut out = Vec::with_capacity(argv.len() + 3);
    for (i, a) in argv.iter().enumerate() {
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
    let (path, cfg) = load_config().map_err(|e| {
        eprintln!("aib-lease run --any-host: {e}");
        RC_USAGE
    })?;
    let views = probe_all(&cfg);
    eprintln!("aib-lease: {}", report(&path, &views));
    match pick(&views)
        .0
        .and_then(|n| cfg.iter().find(|h| h.name == n))
    {
        Some(h) => {
            eprintln!("aib-lease: --any-host: {} is free", h.name);
            Ok(with_host(argv, h))
        }
        None => {
            eprintln!("aib-lease: --any-host: no machine is free, nothing ran");
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
        assert_eq!(pick(&vs), (Some("c"), RC_PICKED));
        assert_eq!(pick(&vs[..2]), (None, RC_NONE_FREE));
        let dead = [f("a", State::Offline), f("b", State::Unreadable)];
        assert_eq!(pick(&dead), (None, RC_NONE_REACHABLE));
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
        assert_eq!(soonest(&vs).map(|v| v.name.as_str()), Some("b"));
        let held = r#"{"state": "held", "lease": {"owner": "o", "expiry_epoch": 9000,
                       "expect_end_epoch": 1500}, "queue": []}"#;
        assert_eq!(view("x", Some(4), held).expect_end_epoch, Some(1500.0));
        let none = [HostView {
            state: State::Offline,
            ..busy("c", Some(1.0), None)
        }];
        assert!(soonest(&none).is_none());
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
