//! aib-goal2-fixture — a throw-away legacy-AIB cabin with idle seats, for the
//! goal-2 A/A and ABBA measurements (PLAN-goal2-fixture-043dd-1110 §2).
//!
//!   aib-goal2-fixture prepare --dir D
//!   aib-goal2-fixture up      --dir D --aib PATH --role ROLE [--seats 6]
//!   aib-goal2-fixture attach  --dir D --view cockpit|war-room|neutral   (macOS `script`)
//!   aib-goal2-fixture detach  --dir D
//!   aib-goal2-fixture status  --dir D
//!   aib-goal2-fixture down    --dir D
//!   aib-goal2-fixture shim    --dir D -- <tmux args>      (what D/bin/tmux runs)
//!
//! Legacy aib talks to two tmux servers: the cockpit on the default socket
//! (bare `tmux`) and the cabin hub (`-L aihub-<cabin>`). Many of its helpers
//! call tmux by absolute path or build `-S $TMUX_TMPDIR/tmux-<uid>/<name>`
//! themselves, so a PATH shim alone cannot contain them. Two layers:
//!
//! 1. every aib call runs with `TMUX_TMPDIR=D/t`, `HOME=D/home` and the state
//!    root under it, so tmux's own socket directory, `~/.ez2-aib` and the
//!    cabin list are the sandbox's whichever way tmux is reached;
//! 2. `D/bin/tmux` (first in PATH) routes explicitly to
//!    `-S D/t/tmux-<uid>/<name>` and only after checking that the socket
//!    directory exists and its real path is inside D (if `TMUX_TMPDIR` were
//!    missing, tmux would fall back to the real `/tmp` servers). Names: the
//!    outer `default`, and the cabin hub: the first `aihub-<x>` seen is bound
//!    (`D/hub-alias`, created exclusively); any other `-L` / `-S` among tmux's
//!    global options is refused and nothing is sent. `$TMUX` is kept only
//!    when it points into the sandbox.
//!
//! `down` sends `kill-server` to the sandbox sockets through the same check
//! and confirms the recorded server pids are gone. It never kills by name.
//! The idle stand-in for the AI CLI (`D/bin/claude`) prints one line and execs
//! a long sleep: no fork, no network.
//!
//! `attach` puts one client on the sandbox outer session under a pseudo
//! terminal (`script -q /dev/null`), never the caller's terminal, looking at
//! the cockpit, the war-room, or a fixture-only `fx-neutral` window that runs
//! a sleep. `detach` / `down` signal only the process group they recorded,
//! after checking its start time.
#![allow(clippy::disallowed_methods)] // measurement tool: plain sleeps are fine

use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};

/// Why a call was refused. Nothing was sent to tmux.
#[derive(Debug, PartialEq, Eq)]
pub struct Refused(pub String);

fn refused<T>(m: impl Into<String>) -> Result<T, Refused> {
    Err(Refused(m.into()))
}

/// tmux global options that take a value (`-c` command, `-f` file, `-T`
/// features) and the ones that take none. Anything else is refused.
const GLOBAL_WITH_VALUE: &str = "cfT";
const GLOBAL_FLAGS: &str = "2CDlNuvV";

/// The outer (cockpit) server's socket name.
pub const OUTER: &str = "default";
/// The only other names the shim binds: legacy aib's cabin hubs.
const HUB_PREFIX: &str = "aihub-";

/// Refuse a socket selector (`-L`, `-S`) among tmux's global options, i.e.
/// before the command name. Options after the command are the command's own
/// (`-S` of `capture-pane` is a start line, not a socket).
pub fn reject_global_selector(args: &[String]) -> Result<(), Refused> {
    let mut i = 0;
    while i < args.len() {
        let a = args[i].as_str();
        if a == "--" || !a.starts_with('-') || a == "-" {
            return Ok(());
        }
        let mut opts = a[1..].chars();
        while let Some(f) = opts.next() {
            if f == 'L' || f == 'S' {
                return refused("tmux socket selector is not allowed here");
            }
            if GLOBAL_WITH_VALUE.contains(f) {
                if opts.as_str().is_empty() {
                    i += 1;
                    if i >= args.len() {
                        return refused(format!("tmux -{f} without its value"));
                    }
                }
                break;
            }
            if !GLOBAL_FLAGS.contains(f) {
                return refused(format!("unknown tmux global option -{f}"));
            }
        }
        i += 1;
    }
    Ok(())
}

/// Where one shim call goes.
#[derive(Debug, PartialEq, Eq)]
pub struct Route {
    /// socket name inside the sandbox socket directory
    pub name: String,
    /// the tmux arguments after the selector
    pub rest: Vec<String>,
    /// a hub name to bind now (none was bound yet)
    pub bind: Option<String>,
}

/// May `name` be used, given the bound hub? Returns the name to bind, if any.
fn admit(name: &str, bound: Option<&str>) -> Result<Option<String>, Refused> {
    if name == OUTER {
        return Ok(None);
    }
    match bound {
        Some(b) if b == name => Ok(None),
        Some(b) => refused(format!("socket {name} refused: this sandbox's hub is {b}")),
        None if name.starts_with(HUB_PREFIX) && name.len() > HUB_PREFIX.len() => {
            Ok(Some(name.to_string()))
        }
        None => refused(format!("socket {name} is not a cabin hub")),
    }
}

/// The shim's routing (pure). `bound`: the hub name already bound;
/// `tmux_env`: the sandbox socket name `$TMUX` points at, if any; `which`:
/// the sandbox socket name a `-S` path is, if it is in the sandbox at all.
pub fn route(
    args: &[String],
    bound: Option<&str>,
    tmux_env: Option<&str>,
    which: &dyn Fn(&str) -> Option<String>,
) -> Result<Route, Refused> {
    // Global options come before the command and may sit on either side of
    // the selector (legacy aib's hub viewer runs `tmux -u -L aihub-x attach`).
    // Keep them, take out the one selector, refuse a second one.
    let mut kept: Vec<String> = Vec::new();
    let mut picked: Option<String> = None;
    let mut i = 0;
    while i < args.len() {
        let a = args[i].as_str();
        if a == "--" || !a.starts_with('-') || a == "-" {
            break;
        }
        let body = &a[1..];
        let mut flags = String::new();
        let mut consumed = false;
        for (at, f) in body.char_indices() {
            if f == 'L' || f == 'S' {
                if picked.is_some() {
                    return refused("more than one tmux socket selector");
                }
                let inline = &body[at + 1..];
                let value = if inline.is_empty() {
                    i += 1;
                    match args.get(i) {
                        Some(v) => v.clone(),
                        None => return refused(format!("-{f} without its value")),
                    }
                } else {
                    inline.to_string()
                };
                let name = if f == 'L' {
                    if value.is_empty() || value.contains('/') {
                        return refused("-L without a plain name");
                    }
                    value
                } else {
                    match which(&value) {
                        Some(n) => n,
                        None => return refused("-S is not a socket of this sandbox"),
                    }
                };
                picked = Some(name);
                if !flags.is_empty() {
                    // `-uL name`: keep the plain flags before the selector
                    kept.push(format!("-{flags}"));
                }
                consumed = true;
                break;
            }
            if GLOBAL_WITH_VALUE.contains(f) {
                // the rest of this word, or the next word, is the value
                kept.push(a.to_string());
                if body[at + f.len_utf8()..].is_empty() {
                    i += 1;
                    match args.get(i) {
                        Some(v) => kept.push(v.clone()),
                        None => return refused(format!("tmux -{f} without its value")),
                    }
                }
                consumed = true;
                break;
            }
            if !GLOBAL_FLAGS.contains(f) {
                return refused(format!("unknown tmux global option -{f}"));
            }
            flags.push(f);
        }
        if !consumed {
            kept.push(a.to_string());
        }
        i += 1;
    }
    kept.extend(args[i..].iter().cloned());
    let name = match picked {
        Some(n) => n,
        None => tmux_env.unwrap_or(OUTER).to_string(),
    };
    let bind = admit(&name, bound)?;
    reject_global_selector(&kept)?;
    Ok(Route {
        name,
        rest: kept,
        bind,
    })
}

/// `$TMUX` is `<socket>,<pid>,<session>`: its sandbox socket name, if any.
pub fn tmux_env_name(value: &str, which: &dyn Fn(&str) -> Option<String>) -> Option<String> {
    value.split(',').next().and_then(which)
}

/// The sandbox directory D.
pub struct Sandbox {
    pub dir: PathBuf,
}

impl Sandbox {
    pub fn tmpdir(&self) -> PathBuf {
        self.dir.join("t")
    }

    fn sockdir_rel(&self, uid: &str) -> PathBuf {
        self.tmpdir().join(format!("tmux-{uid}"))
    }

    /// The socket directory exists and its real path is
    /// `<real D>/t/tmux-<uid>`. Returns that real path.
    pub fn sockdir(&self, uid: &str) -> Result<PathBuf, Refused> {
        if !self.dir.is_dir() {
            return refused(format!("sandbox dir is gone: {}", self.dir.display()));
        }
        let Ok(root) = std::fs::canonicalize(&self.dir) else {
            return refused("sandbox dir has no real path");
        };
        let want = root.join("t").join(format!("tmux-{uid}"));
        match std::fs::canonicalize(self.sockdir_rel(uid)) {
            Ok(real) if real == want => Ok(want),
            Ok(real) => refused(format!(
                "socket dir resolves outside the sandbox: {}",
                real.display()
            )),
            Err(_) => refused("socket dir is gone (tmux would fall back to /tmp)"),
        }
    }

    /// The real path of socket `name`, after the directory check.
    pub fn socket(&self, name: &str, uid: &str) -> Result<PathBuf, Refused> {
        if name.is_empty() || name.contains('/') || name == "." || name == ".." {
            return refused(format!("bad socket name {name:?}"));
        }
        Ok(self.sockdir(uid)?.join(name))
    }

    /// The sandbox socket name a path is: its parent's real path is the
    /// sandbox socket directory (the socket itself may not exist yet).
    pub fn which(&self, p: &str, uid: &str) -> Option<String> {
        let dir = self.sockdir(uid).ok()?;
        let pp = Path::new(p);
        let parent = std::fs::canonicalize(pp.parent()?).ok()?;
        let name = pp.file_name()?.to_str()?.to_string();
        (parent == dir).then_some(name)
    }
}

// ---- process side ----------------------------------------------------------

/// The real uid, as tmux uses for `tmux-<uid>`. A syscall, not `id -u`: the
/// shim runs on every tmux call of the measured tree and must not fork.
#[cfg(unix)]
fn uid() -> String {
    nix::unistd::getuid().to_string()
}

#[cfg(not(unix))]
fn uid() -> String {
    String::new()
}

fn arg(args: &[String], flag: &str) -> Option<String> {
    args.windows(2).find(|w| w[0] == flag).map(|w| w[1].clone())
}

fn real_tmux() -> Option<PathBuf> {
    [
        "/opt/homebrew/bin/tmux",
        "/usr/local/bin/tmux",
        "/usr/bin/tmux",
    ]
    .iter()
    .map(PathBuf::from)
    .find(|p| p.is_file())
}

fn bound_hub(dir: &Path) -> Option<String> {
    std::fs::read_to_string(dir.join("hub-alias"))
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// Bind the hub name once; a concurrent first call that lost re-reads.
fn bind_hub(dir: &Path, name: &str) -> Result<(), Refused> {
    use std::io::Write;
    match std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(dir.join("hub-alias"))
    {
        Ok(mut f) => f
            .write_all(name.as_bytes())
            .map_err(|e| Refused(format!("hub-alias: {e}"))),
        Err(_) => match bound_hub(dir) {
            Some(b) if b == name => Ok(()),
            Some(b) => refused(format!("hub {name} refused: this sandbox's hub is {b}")),
            None => refused("hub-alias exists but is empty"),
        },
    }
}

/// One guarded tmux call from the fixture itself (never through the shim).
fn tm(sb: &Sandbox, name: &str, args: &[&str]) -> Result<std::process::Output, String> {
    let sock = sb.socket(name, &uid()).map_err(|r| r.0)?;
    let a: Vec<String> = args.iter().map(|s| s.to_string()).collect();
    reject_global_selector(&a).map_err(|r| r.0)?;
    let tmux = real_tmux().ok_or("no tmux")?;
    Command::new(tmux)
        .arg("-S")
        .arg(&sock)
        .args(args)
        .env("TMUX_TMPDIR", sb.tmpdir())
        .env_remove("TMUX")
        .env_remove("TMUX_PANE")
        .stdin(Stdio::null())
        .output()
        .map_err(|e| e.to_string())
}

fn lines(o: &std::process::Output) -> Vec<String> {
    if !o.status.success() {
        return vec![];
    }
    String::from_utf8_lossy(&o.stdout)
        .lines()
        .map(str::to_string)
        .collect()
}

const STUB: &str = "#!/bin/sh\n\
# aib-goal2-fixture idle stand-in for the AI CLI: no fork, no network\n\
printf '%s\\n' 'fixture idle seat (no AI CLI)'\n\
exec /bin/sleep 2147483647\n";

fn mkdir_700(p: &Path) -> Result<(), String> {
    std::fs::create_dir_all(p).map_err(|e| format!("{}: {e}", p.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(p, std::fs::Permissions::from_mode(0o700))
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

fn cmd_prepare(args: &[String]) -> Result<Value, String> {
    let dir = PathBuf::from(arg(args, "--dir").ok_or("--dir")?);
    if dir.exists() {
        return Err(format!("{} already exists (down it first)", dir.display()));
    }
    let u = uid();
    let sb = Sandbox { dir: dir.clone() };
    mkdir_700(&dir)?;
    mkdir_700(&dir.join("bin"))?;
    mkdir_700(&dir.join("home/.ez2-aib"))?;
    mkdir_700(&sb.sockdir_rel(&u))?;
    let sockdir = sb.sockdir(&u).map_err(|r| r.0)?;
    let me = std::env::current_exe().map_err(|e| e.to_string())?;
    let real_dir = std::fs::canonicalize(&dir).map_err(|e| e.to_string())?;
    let shim = format!(
        "#!/bin/sh\nexec '{}' shim --dir '{}' -- \"$@\"\n",
        me.display(),
        real_dir.display()
    );
    write_exec(&dir.join("bin/tmux"), &shim)?;
    write_exec(&dir.join("bin/claude"), STUB)?;
    let cfg = json!({
        "socket_dir": sockdir,
        "tmux_tmpdir": real_dir.join("t"),
        "home": real_dir.join("home"),
        "state_root": real_dir.join("home/.ez2-aib"),
        "bin": real_dir.join("bin"),
        "real_tmux": real_tmux(),
    });
    std::fs::write(dir.join("fixture.json"), cfg.to_string()).map_err(|e| e.to_string())?;
    Ok(cfg)
}

fn write_exec(p: &Path, body: &str) -> Result<(), String> {
    std::fs::write(p, body).map_err(|e| format!("{}: {e}", p.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(p, std::fs::Permissions::from_mode(0o700))
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

fn read_cfg(dir: &Path) -> Result<Value, String> {
    let t = std::fs::read_to_string(dir.join("fixture.json"))
        .map_err(|e| format!("fixture.json: {e}"))?;
    serde_json::from_str(&t).map_err(|e| format!("fixture.json: {e}"))
}

/// The environment every aib call of the fixture runs with.
fn sandbox_env(c: &mut Command, cfg: &Value) {
    let s = |k: &str| cfg[k].as_str().unwrap_or_default().to_string();
    let path = format!("{}:{}", s("bin"), std::env::var("PATH").unwrap_or_default());
    c.env("PATH", path)
        .env("HOME", s("home"))
        .env("TMUX_TMPDIR", s("tmux_tmpdir"))
        .env("EZ2_AIB_STATE_ROOT", s("state_root"))
        .env("EZ2_AIB_HOME", s("state_root"))
        .env("AIB_NO_ATTACH", "1")
        .env("AIB_NO_CLIENT_SWITCH", "1")
        .env("AIB_AUTO_UPDATE", "0")
        .env_remove("EZ2_AIHUB_SOCKET")
        .env_remove("EZ2_AIB_INSTANCE")
        .env_remove("EZ2_AIB_INSTANCE_HOME")
        .env_remove("EZ2_TMUX_SESSION")
        .env_remove("EZ2_VAULT_DIR")
        .env_remove("AIB_TMUX_BIN")
        .env_remove("EZ2_AIB_TMUX_BIN")
        .env_remove("TMUX")
        .env_remove("TMUX_PANE");
}

fn cmd_up(args: &[String]) -> Result<Value, String> {
    let dir = PathBuf::from(arg(args, "--dir").ok_or("--dir")?);
    let aib = arg(args, "--aib").ok_or("--aib PATH")?;
    let role = arg(args, "--role").ok_or("--role ROLE")?;
    let seats: u32 = arg(args, "--seats")
        .unwrap_or_else(|| "6".into())
        .parse()
        .map_err(|_| "--seats N")?;
    let cfg = read_cfg(&dir)?;
    let sb = Sandbox { dir: dir.clone() };
    let mut steps = vec![];
    let mut first = Command::new(&aib);
    first.args(["claude", "dgrs", &role, "new"]);
    sandbox_env(&mut first, &cfg);
    steps.push(run_step(&sb, first)?);
    let hub = bound_hub(&dir).ok_or("aib never selected a cabin hub (-L aihub-…)")?;
    let cabin = hub.trim_start_matches(HUB_PREFIX).to_string();
    for _ in 1..seats {
        let mut c = Command::new(&aib);
        c.args(["claude", "dgrs", &role, "--again"])
            .arg(format!("--instance={cabin}"));
        sandbox_env(&mut c, &cfg);
        steps.push(run_step(&sb, c)?);
    }
    Ok(json!({"hub": hub, "cabin": cabin, "steps": steps, "status": status(&sb)?}))
}

fn run_step(sb: &Sandbox, mut c: Command) -> Result<Value, String> {
    sb.sockdir(&uid()).map_err(|r| r.0)?;
    let o = c.stdin(Stdio::null()).output().map_err(|e| e.to_string())?;
    let v = json!({
        "argv": format!("{c:?}"),
        "rc": o.status.code(),
        "stdout_tail": String::from_utf8_lossy(&o.stdout).lines().rev().take(8).collect::<Vec<_>>(),
        "stderr_tail": String::from_utf8_lossy(&o.stderr).lines().rev().take(12).collect::<Vec<_>>(),
    });
    if !o.status.success() {
        return Err(format!("step failed: {v}"));
    }
    Ok(v)
}

/// Every socket in the sandbox socket directory.
fn socket_names(sb: &Sandbox) -> Result<Vec<String>, String> {
    let d = sb.sockdir(&uid()).map_err(|r| r.0)?;
    let mut v: Vec<String> = std::fs::read_dir(&d)
        .map_err(|e| e.to_string())?
        .flatten()
        .filter_map(|e| e.file_name().to_str().map(str::to_string))
        .collect();
    v.sort();
    Ok(v)
}

fn server_status(sb: &Sandbox, name: &str) -> Result<Value, String> {
    let sock = sb.socket(name, &uid()).map_err(|r| r.0)?;
    let pid: Option<u32> = lines(&tm(sb, name, &["display-message", "-p", "#{pid}"])?)
        .first()
        .and_then(|l| l.trim().parse().ok());
    let panes: Vec<Value> = lines(&tm(
        sb,
        name,
        &[
            "list-panes",
            "-a",
            "-F",
            "#{pane_pid}\t#{session_name}:#{window_index}.#{pane_index}\t#{window_name}\t#{pane_current_command}",
        ],
    )?)
    .iter()
    .filter_map(|l| {
        let mut it = l.split('\t');
        let pid: u32 = it.next()?.parse().ok()?;
        Some(json!({
            "pid": pid,
            "start": aib_procwatch::start_time(pid),
            "target": it.next()?,
            "window": it.next().unwrap_or(""),
            "command": it.next().unwrap_or(""),
        }))
    })
    .collect();
    let clients = lines(&tm(
        sb,
        name,
        &[
            "list-clients",
            "-F",
            "#{client_pid}\t#{session_name}:#{window_index}\t#{window_name}",
        ],
    )?);
    Ok(json!({
        "socket": sock,
        "server_pid": pid,
        "server_start": pid.and_then(aib_procwatch::start_time),
        "panes": panes,
        "clients": clients,
    }))
}

fn status(sb: &Sandbox) -> Result<Value, String> {
    let mut servers = serde_json::Map::new();
    for n in socket_names(sb)? {
        servers.insert(n.clone(), server_status(sb, &n)?);
    }
    let attach = std::fs::read_to_string(sb.dir.join(ATTACH_FILE))
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok());
    Ok(json!({"hub_alias": bound_hub(&sb.dir), "servers": servers, "attach": attach}))
}

/// The outer window a fixture client looks at.
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum View {
    /// the cockpit window (hub viewer + right column)
    Cockpit,
    /// legacy aib's war-room window
    WarRoom,
    /// a window the fixture adds, running only a sleep
    Neutral,
}

impl View {
    pub fn parse(s: &str) -> Option<View> {
        match s {
            "cockpit" => Some(View::Cockpit),
            "war-room" => Some(View::WarRoom),
            "neutral" => Some(View::Neutral),
            _ => None,
        }
    }

    pub fn window(self) -> &'static str {
        match self {
            View::Cockpit => "cockpit",
            View::WarRoom => "war-room",
            View::Neutral => NEUTRAL_WINDOW,
        }
    }
}

const NEUTRAL_WINDOW: &str = "fx-neutral";
const ATTACH_FILE: &str = "attach.json";

/// The legacy aib outer session (`aib-<cabin>`) on the sandbox default server.
fn outer_session(sb: &Sandbox) -> Result<String, String> {
    let v: Vec<String> = lines(&tm(sb, OUTER, &["list-sessions", "-F", "#{session_name}"])?)
        .into_iter()
        .filter(|s| s.starts_with("aib-"))
        .collect();
    match v.as_slice() {
        [one] => Ok(one.clone()),
        _ => Err(format!(
            "expected one aib-<cabin> outer session, found {v:?}"
        )),
    }
}

/// Clients of the sandbox default server: (pid, window name).
fn outer_clients(sb: &Sandbox) -> Result<Vec<(u32, String)>, String> {
    Ok(lines(&tm(
        sb,
        OUTER,
        &["list-clients", "-F", "#{client_pid}\t#{window_name}"],
    )?)
    .iter()
    .filter_map(|l| {
        let (p, w) = l.split_once('\t')?;
        Some((p.parse().ok()?, w.to_string()))
    })
    .collect())
}

/// Attach one client to the sandbox outer session under a pseudo terminal
/// (`script`), never the caller's terminal. Its stdin is a pipe from a
/// sleep that never writes, so the client stays until `detach` / `down`
/// signals the process group recorded in D/attach.json.
fn cmd_attach(args: &[String]) -> Result<Value, String> {
    let dir = PathBuf::from(arg(args, "--dir").ok_or("--dir")?);
    let view = arg(args, "--view")
        .as_deref()
        .and_then(View::parse)
        .ok_or("--view cockpit|war-room|neutral")?;
    let cfg = read_cfg(&dir)?;
    let sb = Sandbox { dir: dir.clone() };
    if dir.join(ATTACH_FILE).exists() {
        return Err("a fixture client is already attached (detach it first)".into());
    }
    if !outer_clients(&sb)?.is_empty() {
        return Err("the sandbox outer server already has a client".into());
    }
    let session = outer_session(&sb)?;
    if view == View::Neutral {
        let have = lines(&tm(
            &sb,
            OUTER,
            &["list-windows", "-t", &session, "-F", "#{window_name}"],
        )?);
        if !have.iter().any(|w| w == NEUTRAL_WINDOW) {
            let o = tm(
                &sb,
                OUTER,
                &[
                    "new-window",
                    "-d",
                    "-t",
                    &format!("{session}:"),
                    "-n",
                    NEUTRAL_WINDOW,
                    "exec /bin/sleep 2147483647",
                ],
            )?;
            if !o.status.success() {
                return Err(format!(
                    "new-window {NEUTRAL_WINDOW}: {}",
                    String::from_utf8_lossy(&o.stderr)
                ));
            }
        }
    }
    let target = format!("{session}:{}", view.window());
    let o = tm(&sb, OUTER, &["select-window", "-t", &target])?;
    if !o.status.success() {
        return Err(format!(
            "select-window {target}: {}",
            String::from_utf8_lossy(&o.stderr)
        ));
    }
    let shim = dir.join("bin/tmux");
    let mut c = Command::new("/bin/sh");
    c.arg("-c")
        .arg("/bin/sleep 2147483647 | /usr/bin/script -q /dev/null \"$0\" attach -t \"$1\"")
        .arg(&shim)
        .arg(&target)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    sandbox_env(&mut c, &cfg);
    c.env("TERM", "xterm-256color");
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        c.process_group(0);
    }
    let child = c.spawn().map_err(|e| format!("spawn attach: {e}"))?;
    let pgid = child.id();
    let rec = json!({"pgid": pgid, "start": aib_procwatch::start_time(pgid), "view": view.window(), "target": target});
    std::fs::write(dir.join(ATTACH_FILE), rec.to_string()).map_err(|e| e.to_string())?;
    let mut seen = vec![];
    for _ in 0..100 {
        seen = outer_clients(&sb)?;
        if !seen.is_empty() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    match seen.as_slice() {
        [(_, w)] if w == view.window() => Ok(
            json!({"attach": rec, "clients": seen.iter().map(|(p, w)| json!({"pid": p, "window": w})).collect::<Vec<_>>()}),
        ),
        _ => {
            let _ = detach(&sb);
            Err(format!(
                "attach did not settle on {}: clients {seen:?}",
                view.window()
            ))
        }
    }
}

/// Signal the recorded attach process group, if it is still the one we
/// started, and wait for the outer server to have no client.
fn detach(sb: &Sandbox) -> Result<Value, String> {
    let p = sb.dir.join(ATTACH_FILE);
    let Ok(t) = std::fs::read_to_string(&p) else {
        return Ok(json!({"attached": false}));
    };
    let rec: Value = serde_json::from_str(&t).map_err(|e| format!("{ATTACH_FILE}: {e}"))?;
    let pgid = rec["pgid"]
        .as_u64()
        .and_then(|v| u32::try_from(v).ok())
        .ok_or("attach pgid")?;
    let same =
        rec["start"].as_u64().is_some() && aib_procwatch::start_time(pgid) == rec["start"].as_u64();
    let mut kill_rc = None;
    if same {
        kill_rc = Command::new("/bin/kill")
            .args(["-TERM", "--", &format!("-{pgid}")])
            .status()
            .ok()
            .and_then(|s| s.code());
    }
    let mut left = vec![];
    for _ in 0..50 {
        left = outer_clients(sb).unwrap_or_default();
        if left.is_empty() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    if !left.is_empty() {
        return Err(format!("clients still attached after detach: {left:?}"));
    }
    std::fs::remove_file(&p).map_err(|e| e.to_string())?;
    Ok(json!({"attached": true, "pgid": pgid, "same_identity": same, "kill_rc": kill_rc}))
}

fn cmd_detach(args: &[String]) -> Result<Value, String> {
    let dir = PathBuf::from(arg(args, "--dir").ok_or("--dir")?);
    detach(&Sandbox { dir })
}

fn cmd_down(args: &[String]) -> Result<Value, String> {
    let dir = PathBuf::from(arg(args, "--dir").ok_or("--dir")?);
    let sb = Sandbox { dir: dir.clone() };
    let mut out = serde_json::Map::new();
    if sb.dir.join(ATTACH_FILE).exists() && socket_names(&sb)?.iter().any(|n| n == OUTER) {
        out.insert("detach".into(), detach(&sb)?);
    }
    for name in socket_names(&sb)? {
        let pid = server_status(&sb, &name)?["server_pid"].as_u64();
        let kill = tm(&sb, &name, &["kill-server"])?;
        let mut gone = pid.is_none();
        if let Some(p) = pid.and_then(|p| u32::try_from(p).ok()) {
            for _ in 0..50 {
                if aib_procwatch::start_time(p).is_none() {
                    gone = true;
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
        }
        if !gone {
            return Err(format!(
                "{name} server {pid:?} still running after kill-server"
            ));
        }
        out.insert(
            name,
            json!({"server_pid": pid, "kill_server_rc": kill.status.code()}),
        );
    }
    std::fs::remove_dir_all(&dir).map_err(|e| e.to_string())?;
    Ok(json!({"servers": out, "removed": dir}))
}

fn shim_main(args: &[String]) -> ExitCode {
    let fail = |m: String| {
        eprintln!("aib-goal2-fixture shim refused: {m}");
        ExitCode::from(97)
    };
    let Some(dir) = arg(args, "--dir") else {
        return fail("--dir".into());
    };
    let rest: Vec<String> = args
        .iter()
        .skip_while(|a| *a != "--")
        .skip(1)
        .cloned()
        .collect();
    let sb = Sandbox {
        dir: PathBuf::from(&dir),
    };
    let u = uid();
    let which = |p: &str| sb.which(p, &u);
    let env_name = std::env::var("TMUX")
        .ok()
        .and_then(|v| tmux_env_name(&v, &which));
    let bound = bound_hub(&sb.dir);
    let r = match route(&rest, bound.as_deref(), env_name.as_deref(), &which) {
        Ok(r) => r,
        Err(e) => return fail(e.0),
    };
    if let Some(name) = &r.bind {
        if let Err(e) = bind_hub(&sb.dir, name) {
            return fail(e.0);
        }
    }
    let sock = match sb.socket(&r.name, &u) {
        Ok(s) => s,
        Err(e) => return fail(e.0),
    };
    let Some(tmux) = real_tmux() else {
        return fail("no tmux".into());
    };
    let mut c = Command::new(tmux);
    c.arg("-S")
        .arg(&sock)
        .args(&r.rest)
        .env("TMUX_TMPDIR", sb.tmpdir());
    if env_name.is_none() {
        c.env_remove("TMUX").env_remove("TMUX_PANE");
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        let e = c.exec();
        fail(format!("exec tmux: {e}"))
    }
    #[cfg(not(unix))]
    {
        fail("unix only".into())
    }
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let rest = args.get(1..).unwrap_or(&[]);
    let r = match args.first().map(String::as_str) {
        Some("shim") => return shim_main(rest),
        Some("prepare") => cmd_prepare(rest),
        Some("up") => cmd_up(rest),
        Some("status") => match arg(rest, "--dir") {
            Some(d) => status(&Sandbox {
                dir: PathBuf::from(d),
            }),
            None => Err("--dir".into()),
        },
        Some("attach") => cmd_attach(rest),
        Some("detach") => cmd_detach(rest),
        Some("down") => cmd_down(rest),
        _ => {
            eprintln!(
                "usage: aib-goal2-fixture prepare|up|attach|detach|status|down|shim --dir D …"
            );
            return ExitCode::from(2);
        }
    };
    match r {
        Ok(v) => {
            println!("{v}");
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("aib-goal2-fixture: {e}");
            ExitCode::from(1)
        }
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)] // test code: a panic is the failure report
mod tests {
    use super::*;

    fn s(v: &[&str]) -> Vec<String> {
        v.iter().map(|x| x.to_string()).collect()
    }

    #[test]
    fn global_selectors_are_refused_command_options_are_not() {
        assert!(reject_global_selector(&s(&["-L", "x", "ls"])).is_err());
        assert!(reject_global_selector(&s(&["-S", "/tmp/x", "ls"])).is_err());
        assert!(reject_global_selector(&s(&["-uL", "x"])).is_err());
        assert!(reject_global_selector(&s(&["-f", "/dev/null", "-L", "x"])).is_err());
        assert!(reject_global_selector(&s(&["-Z"])).is_err());
        assert!(reject_global_selector(&s(&["-f"])).is_err());
        // capture-pane -S is a start line
        assert!(reject_global_selector(&s(&["capture-pane", "-S", "-5", "-p"])).is_ok());
        assert!(reject_global_selector(&s(&["-u", "-f", "/dev/null", "ls"])).is_ok());
        assert!(reject_global_selector(&s(&["-f/dev/null", "ls"])).is_ok());
    }

    fn which(p: &str) -> Option<String> {
        p.strip_prefix("/sb/t/tmux-501/").map(str::to_string)
    }

    fn r(args: &[&str], bound: Option<&str>, env: Option<&str>) -> Result<Route, Refused> {
        route(&s(args), bound, env, &which)
    }

    #[test]
    fn the_first_cabin_hub_is_bound_and_no_other() {
        let x = r(&["-L", "aihub-ab12", "ls"], None, None).unwrap();
        assert_eq!(x.name, "aihub-ab12");
        assert_eq!(x.bind.as_deref(), Some("aihub-ab12"));
        assert_eq!(x.rest, s(&["ls"]));
        let x = r(&["-L", "aihub-ab12", "ls"], Some("aihub-ab12"), None).unwrap();
        assert_eq!(x.bind, None);
        // another cabin, a non-hub name, an empty suffix, a path: refused
        assert!(r(&["-L", "aihub-zz99", "ls"], Some("aihub-ab12"), None).is_err());
        assert!(r(&["-L", "aihub", "ls"], None, None).is_err());
        assert!(r(&["-L", "aihub-", "ls"], None, None).is_err());
        assert!(r(&["-L", "x/../aihub-1", "ls"], None, None).is_err());
        assert!(r(&["-L"], None, None).is_err());
        // the outer server keeps its own name
        assert_eq!(
            r(&["-L", "default", "ls"], None, None).unwrap().name,
            "default"
        );
    }

    #[test]
    fn bare_calls_follow_a_sandbox_tmux_env_else_the_outer_server() {
        assert_eq!(
            r(&["display-message", "-p", "x"], None, None).unwrap().name,
            "default"
        );
        let x = r(&["display-message"], Some("aihub-ab12"), Some("aihub-ab12")).unwrap();
        assert_eq!(x.name, "aihub-ab12");
        assert_eq!(
            tmux_env_name("/sb/t/tmux-501/aihub-ab12,123,0", &which).as_deref(),
            Some("aihub-ab12")
        );
        assert_eq!(
            tmux_env_name("/private/tmp/tmux-501/default,1,0", &which),
            None
        );
        assert_eq!(tmux_env_name("", &which), None);
    }

    #[test]
    fn only_sandbox_sockets_pass_s_and_no_second_selector() {
        assert_eq!(
            r(&["-S", "/sb/t/tmux-501/default", "ls"], None, None)
                .unwrap()
                .name,
            "default"
        );
        assert!(r(
            &["-S", "/private/tmp/tmux-501/default", "kill-server"],
            None,
            None
        )
        .is_err());
        assert!(r(
            &["-S", "/private/tmp/tmux-501/aihub-ab12", "ls"],
            Some("aihub-ab12"),
            None
        )
        .is_err());
        assert!(r(
            &["-S", "/sb/t/tmux-501/aihub-zz", "ls"],
            Some("aihub-ab12"),
            None
        )
        .is_err());
        assert!(r(
            &["-S", "/sb/t/tmux-501/default", "-L", "x", "ls"],
            None,
            None
        )
        .is_err());
        assert!(r(&["-L", "aihub-ab12", "-L", "aihub-ab12", "ls"], None, None).is_err());
        assert!(r(
            &[
                "-u",
                "-L",
                "aihub-ab12",
                "-S",
                "/sb/t/tmux-501/default",
                "ls"
            ],
            None,
            None
        )
        .is_err());
        assert!(r(&["-u", "-x", "-L", "aihub-ab12", "ls"], None, None).is_err());
    }

    #[test]
    fn views_name_their_outer_window() {
        assert_eq!(View::parse("cockpit").map(View::window), Some("cockpit"));
        assert_eq!(View::parse("war-room").map(View::window), Some("war-room"));
        assert_eq!(
            View::parse("neutral").map(View::window),
            Some(NEUTRAL_WINDOW)
        );
        assert_eq!(View::parse("other"), None);
    }

    #[test]
    fn global_flags_around_the_selector_are_kept() {
        // legacy aib's hub viewer: `tmux -u -L aihub-x attach -t aihub`
        let x = r(
            &["-u", "-L", "aihub-ab12", "attach", "-t", "aihub"],
            None,
            None,
        )
        .unwrap();
        assert_eq!(x.name, "aihub-ab12");
        assert_eq!(x.rest, s(&["-u", "attach", "-t", "aihub"]));
        assert_eq!(x.bind.as_deref(), Some("aihub-ab12"));
        let y = r(
            &["-uLaihub-ab12", "-f", "/dev/null", "ls"],
            Some("aihub-ab12"),
            None,
        )
        .unwrap();
        assert_eq!(y.name, "aihub-ab12");
        assert_eq!(y.rest, s(&["-u", "-f", "/dev/null", "ls"]));
        let z = r(&["-u", "-S", "/sb/t/tmux-501/default", "ls"], None, None).unwrap();
        assert_eq!(z.name, "default");
        assert_eq!(z.rest, s(&["-u", "ls"]));
        let bare = r(&["-u", "ls"], None, None).unwrap();
        assert_eq!(bare.name, "default");
        assert_eq!(bare.rest, s(&["-u", "ls"]));
    }

    #[test]
    fn a_missing_socket_dir_is_refused_not_left_to_tmux() {
        let d = std::env::temp_dir().join(format!("aibg2fx-dir-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        let sb = Sandbox { dir: d.clone() };
        // D exists but D/t/tmux-<uid> does not: tmux would use /tmp
        assert!(sb.sockdir("501").is_err());
        assert!(sb.socket("default", "501").is_err());
        std::fs::create_dir_all(d.join("t/tmux-501")).unwrap();
        let ok = sb.socket("default", "501").unwrap();
        assert!(ok.ends_with("t/tmux-501/default"), "{ok:?}");
        assert!(sb.socket("../x", "501").is_err());
        let p = d.join("t/tmux-501/aihub-ab12");
        assert_eq!(
            sb.which(p.to_str().unwrap(), "501").as_deref(),
            Some("aihub-ab12")
        );
        assert_eq!(sb.which("/private/tmp/tmux-501/default", "501"), None);
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn the_hub_name_binds_once() {
        let d = std::env::temp_dir().join(format!("aibg2fx-bind-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        assert!(bind_hub(&d, "aihub-ab12").is_ok());
        assert!(bind_hub(&d, "aihub-ab12").is_ok());
        assert!(bind_hub(&d, "aihub-zz99").is_err());
        assert_eq!(bound_hub(&d).as_deref(), Some("aihub-ab12"));
        let _ = std::fs::remove_dir_all(&d);
    }
}
