// SPDX-License-Identifier: Apache-2.0
//! aib-title — `title-beat.sh --once` in one process (goal2 title-beat,
//! PLAN-goal2-titlebeat-abba-043dd-1112 §7, step 1).
//!
//! Measured on main 04098b18 (andymini, RESULT-goal2-titlebeat-pre-043dd-1112): one
//! `title-beat.sh --once` costs 33 forks, and the right pane runs it every frame — 27% of
//! all forks of an idle cockpit. About 14 of them are sourcing cockpit-env.sh, 8 the beat
//! file, 5 tmux, 3 python. This binary does the same steps with no shell:
//!
//! * the beat file under the same `<file>.lock` (flock on unix, LockFileEx on Windows),
//!   temp + rename, then read back — the protocol of `aib_state_transition`;
//! * the hub's active window: one `tmux -L <hub> display-message`;
//! * the title string: `$EZ2_PY3 $EZ2_COCKPIT_DIR/title/title_sample.py`, the very file the
//!   bash path runs, with the same `EZ2_TB_*` variables — so the string cannot drift;
//! * ver / state read from `title-version.json` here instead of two `python3 -c`;
//! * the three options in one tmux call (`set-option … ; set-option …`).
//!
//! What it does not redo: cockpit-env.sh. The caller (pane-right-tabs.sh) has sourced it and
//! exported the cabin variables, which are fixed for the life of a pane; the python reads
//! only those (EZ2_AIB_INSTANCE / _HOME / _ROOT, EZ2_COCKPIT_STATE_DIR, EZ2_SEATS_DIR …).
//!
//! Exit codes follow the bash path: 0 (a title that cannot be written never blocks the
//! caller), 2 usage, the sampler's code when the python fails (bash: `set -e` on
//! `TITLE="$(sample 0)"`), 1 when the sampler cannot start.

use serde_json::Value;
use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

pub const BEAT_FILE: &str = "title-beat-seq";
pub const VERSION_FILE: &str = "title-version.json";
pub const SAMPLER: &str = "title/title_sample.py";

/// The interface between this binary and title_sample.py (PLAN-aibtitle-install r2 package 2):
/// the variables it is given and what it prints. Bump it here AND as `TB_PROTO = N` in
/// title_sample.py whenever either changes; tests/title.rs pins the two to each other.
/// A binary left installed while the repository moves on then stops with rc 126 instead of
/// printing a wrong title, and the pane's existing "3 frames of 126" rule falls back to
/// title-beat.sh (pane-right-tabs.sh title_beat_launch).
pub const SAMPLER_PROTO: u32 = 1;

/// title_sample.py exits with this when `AIB_TITLE_PROTO` is set and is not its `TB_PROTO`
/// (an older binary than the sampler). Mapped to 126 like a sampler without our number.
pub const PROTO_MISMATCH_RC: i32 = 75;

/// Exit code for "this binary and the sampler do not agree": the pane counts it as broken.
pub const PROTO_FALLBACK_RC: i32 = 126;

/// The `TB_PROTO = N` line of title_sample.py: exactly one, digits only, else None
/// (a sampler from before the handshake has none).
pub fn sampler_proto(text: &str) -> Option<u32> {
    let mut found = None;
    for line in text.lines() {
        if let Some(rest) = line.strip_prefix("TB_PROTO = ") {
            let rest = rest.trim_end();
            if found.is_some() || rest.is_empty() || !rest.bytes().all(|b| b.is_ascii_digit()) {
                return None;
            }
            found = rest.parse().ok();
        }
    }
    found
}

/// Read the sampler under `cockpit` and say whether it speaks SAMPLER_PROTO. One file read,
/// no process: runs every frame before anything is written.
pub fn proto_check(cockpit: &Path) -> Result<(), String> {
    let p = cockpit.join(SAMPLER);
    let text = fs::read_to_string(&p).map_err(|e| format!("{}: {e}", p.display()))?;
    match sampler_proto(&text) {
        Some(n) if n == SAMPLER_PROTO => Ok(()),
        Some(n) => Err(format!(
            "{} 是介面 {n},這支 aib-title 是 {SAMPLER_PROTO}",
            p.display()
        )),
        None => Err(format!(
            "{} 沒有 TB_PROTO(比這支 aib-title 舊)",
            p.display()
        )),
    }
}

/// The environment this run reads, as a map so tests can drive it.
#[derive(Clone, Debug, Default)]
pub struct Env(pub BTreeMap<String, String>);

impl Env {
    pub fn from_process() -> Env {
        Env(std::env::vars_os()
            .filter_map(|(k, v)| Some((k.into_string().ok()?, v.into_string().ok()?)))
            .collect())
    }
    /// `${NAME:-}`: unset and empty are the same.
    pub fn get(&self, k: &str) -> &str {
        self.0.get(k).map(String::as_str).unwrap_or("")
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Opts {
    pub print_only: bool,
    pub dry: bool,
}

/// The commit this binary was built from (40 hex, or `unknown`) and whether the sources had
/// uncommitted changes (`build.rs`), in the one shape `aib-release host-build` already reads
/// (`aib_release::host_build::parse_info`). One literal, so the bytes sit together in the file.
pub const BUILD_INFO: &str = concat!(
    "aib-build-info version=",
    env!("CARGO_PKG_VERSION"),
    " commit=",
    env!("AIB_TITLE_COMMIT"),
    " dirty=",
    env!("AIB_TITLE_DIRTY"),
    ";"
);

/// Which tool and which target the file is (PLAN-aibtitle-install r2, Codex H): the build-info
/// mark alone is the same in every tool, so another tool's binary would pass a check that only
/// reads BUILD_INFO. A separate line keeps BUILD_INFO in the shape host-build reads.
pub const TOOL_INFO: &str = concat!(
    "aib-tool-info name=aib-title target=",
    env!("AIB_TITLE_TARGET"),
    ";"
);

/// `aib-title --version`: `aib-title <version> <os>-<arch>`.
pub fn version_line() -> String {
    format!(
        "aib-title {} {}-{}",
        env!("CARGO_PKG_VERSION"),
        std::env::consts::OS,
        std::env::consts::ARCH
    )
}

/// The two read-only commands, each only as the sole argument: `--version` (one line) and
/// `build-info` (BUILD_INFO, then TOOL_INFO). Anything else is the heartbeat (`parse_args`).
pub fn info_command(args: &[String]) -> Option<String> {
    match args {
        [a] if a == "--version" => Some(version_line()),
        [a] if a == "build-info" => Some(format!("{BUILD_INFO}\n{TOOL_INFO}")),
        _ => None,
    }
}

/// Parse the bash flags (`--once`, `--print`, `--dry-run`). `--loop` / `--install` stay in
/// title-beat.sh. Err = the usage line, exit 2.
pub fn parse_args(args: &[String], env: &Env) -> Result<Opts, String> {
    let mut o = Opts {
        print_only: false,
        dry: env.get("AIB_DRY_RUN") == "1",
    };
    for a in args {
        match a.as_str() {
            "--once" | "once" => {}
            "--print" => o.print_only = true,
            "--dry-run" => o.dry = true,
            "--loop" | "--install" => {
                return Err(format!(
                    "aib-title: {a} 仍走 title-beat.sh(這支只做 --once)"
                ))
            }
            _ => return Err(format!("unknown option: {a}")),
        }
    }
    Ok(o)
}

/// `$(cat f)` then `case "$n" in ''|*[!0-9]*) n=0`: trailing newlines dropped, anything
/// that is not all digits is "0". The digits are kept as text (bash prints them as is).
pub fn beat_text(raw: &str) -> String {
    let t = raw.trim_end_matches('\n');
    if t.is_empty() || !t.bytes().all(|b| b.is_ascii_digit()) {
        "0".to_string()
    } else {
        t.to_string()
    }
}

/// `_advance_beat`: (n + 1) % 4, decimal, no overflow on long digit strings.
/// Known difference: bash reads a leading 0 as octal ("010" = 8); the file only ever
/// holds 0–3, which both read the same.
pub fn advance(raw: &str) -> u32 {
    let n = beat_text(raw)
        .bytes()
        .fold(0u32, |r, b| (r * 10 + u32::from(b - b'0')) % 4);
    (n + 1) % 4
}

/// `aib_state_transition "$BEAT_FILE" _advance_beat`: mkdir -p, exclusive lock on
/// `<file>.lock`, read, compute, `<file>.tmp.<pid>`, rename. The lock is released on drop.
pub fn transition(file: &Path) -> Result<(), String> {
    let dir = file.parent().ok_or("no parent")?;
    fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let mut lock = file.as_os_str().to_owned();
    lock.push(".lock");
    // read + write, not append: Windows LockFileEx needs GENERIC_READ or GENERIC_WRITE
    let fh = OpenOptions::new()
        .create(true)
        .read(true)
        .write(true)
        .truncate(false)
        .open(PathBuf::from(lock))
        .map_err(|e| e.to_string())?;
    fh.lock().map_err(|e| e.to_string())?;
    let cur = fs::read(file)
        .map(|b| String::from_utf8_lossy(&b).into_owned())
        .unwrap_or_default();
    let next = advance(&cur);
    let mut tmp = file.as_os_str().to_owned();
    tmp.push(format!(".tmp.{}", std::process::id()));
    let tmp = PathBuf::from(tmp);
    let write = fs::File::create(&tmp).and_then(|mut f| writeln!(f, "{next}"));
    if let Err(e) = write {
        return Err(e.to_string());
    }
    if let Err(e) = fs::rename(&tmp, file) {
        let _ = fs::remove_file(&tmp);
        return Err(e.to_string());
    }
    drop(fh);
    Ok(())
}

/// `next_beat`: advance under the lock, then read the file back (a write that failed
/// leaves the beat frozen at the file's value, as in bash).
pub fn next_beat(state_dir: &Path) -> String {
    let file = state_dir.join(BEAT_FILE);
    if transition(&file).is_err() {
        eprintln!("title-beat: beat 狀態寫入失敗({}),節拍凍結", file.display());
    }
    match fs::read(&file) {
        Ok(b) => beat_text(&String::from_utf8_lossy(&b)),
        Err(_) => "0".to_string(),
    }
}

/// `$(cmd)`: stdout with every trailing newline removed.
pub fn strip_nl(b: &[u8]) -> String {
    String::from_utf8_lossy(b)
        .trim_end_matches('\n')
        .to_string()
}

fn tmux() -> Command {
    let mut c = Command::new("tmux");
    c.env_remove("TMUX");
    c
}

/// `hub_active_window`: the window the hub shows now; "" when there is no hub socket or
/// tmux fails (never a made-up window).
pub fn hub_window(env: &Env) -> String {
    let sock = env.get("EZ2_AIHUB_SOCKET");
    if sock.is_empty() {
        return String::new();
    }
    match tmux()
        .args(["-L", sock, "display-message", "-p", "#{window_id}"])
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
    {
        Ok(o) => strip_nl(&o.stdout),
        Err(_) => String::new(),
    }
}

/// Python's `str(x)` for what `print(json.load(f).get(k) or default)` prints.
/// Known difference: floats print in serde_json's form, lists / dicts as JSON; the file is
/// written by our own tool with string fields.
pub fn py_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Null => "None".into(),
        other => other.to_string(),
    }
}

fn py_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// ver / state as the two `python3 -c` print them: `None` when the file is not there
/// (`[ -f "$vf" ]` false); ("", "") when it cannot be read or parsed, or is not an object
/// (both pythons fail, `|| true`, nothing printed).
pub fn version_fields(state_dir: &Path) -> Option<(String, String)> {
    let vf = state_dir.join(VERSION_FILE);
    if !vf.is_file() {
        return None;
    }
    let parsed: Option<serde_json::Map<String, Value>> = fs::read(&vf)
        .ok()
        .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
        .and_then(|v| match v {
            Value::Object(m) => Some(m),
            _ => None,
        });
    let Some(m) = parsed else {
        return Some((String::new(), String::new()));
    };
    let field = |k: &str, dflt: &str| match m.get(k) {
        Some(v) if py_truthy(v) => py_str(v).trim_end_matches('\n').to_string(),
        _ => dflt.to_string(),
    };
    Some((field("ver", ""), field("state", "unknown")))
}

/// The tmux commands `write_title` runs, in order (each is the argv after `tmux`).
pub fn title_commands(
    session: &str,
    title: &str,
    ver: Option<(String, String)>,
) -> Vec<Vec<String>> {
    let set = |k: &str, v: &str| -> Vec<String> {
        ["set-option", "-t", session, k, v]
            .iter()
            .map(|s| s.to_string())
            .collect()
    };
    let mut cmds = vec![set("@aib_title", title)];
    if let Some((ver, state)) = ver {
        if !ver.is_empty() {
            cmds.push(set("@aib_ver", &ver));
        }
        if !state.is_empty() {
            cmds.push(set("@aib_ver_state", &state));
        }
    }
    cmds
}

/// One tmux argv for all commands, `;` between them. tmux treats an argument that ends in
/// `;` as a separator, so when any value ends in `;` this returns None and the caller runs
/// the commands one by one (what bash always did).
pub fn chained(cmds: &[Vec<String>]) -> Option<Vec<String>> {
    if cmds.iter().flatten().any(|a| a.ends_with(';')) {
        return None;
    }
    let mut out = Vec::new();
    for (i, c) in cmds.iter().enumerate() {
        if i > 0 {
            out.push(";".to_string());
        }
        out.extend(c.iter().cloned());
    }
    Some(out)
}

/// `write_title`. Dry mode prints the commands (`+ env -u TMUX tmux …`) instead of running them.
pub fn write_title(env: &Env, title: &str, dry: bool, out: &mut dyn Write) {
    let session = env.get("EZ2_TMUX_SESSION");
    if session.is_empty() {
        eprintln!("title-beat: EZ2_TMUX_SESSION 未設,不知道要寫到哪個 session");
        return;
    }
    let cmds = title_commands(
        session,
        title,
        version_fields(Path::new(env.get("EZ2_COCKPIT_STATE_DIR"))),
    );
    if dry {
        for c in &cmds {
            let _ = writeln!(out, "+ env -u TMUX tmux {}", c.join(" "));
        }
        return;
    }
    let runs: Vec<Vec<String>> = match chained(&cmds) {
        Some(one) => vec![one],
        None => cmds,
    };
    for argv in runs {
        let _ = tmux()
            .args(&argv)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
}

/// Run the sampler. Ok(title) or Err(exit code).
pub fn sample(env: &Env, beat: &str, window: &str, print_only: bool) -> Result<String, i32> {
    let py = match env.get("EZ2_PY3") {
        "" => "python3",
        p => p,
    };
    let sampler = Path::new(env.get("EZ2_COCKPIT_DIR")).join(SAMPLER);
    let uuid = match env.get("EZ2_SESSION_UUID") {
        "" => env.get("CLAUDE_CODE_SESSION_ID"),
        u => u,
    };
    let mut c = Command::new(py);
    // the process environment as is (the pane's, cockpit-env.sh already sourced), minus TMUX
    c.arg(&sampler).env_remove("TMUX");
    c.env("EZ2_TB_UUID", uuid)
        .env("EZ2_TB_BEAT", beat)
        .env("EZ2_TB_COLOR", "0")
        .env("EZ2_TB_PRINT_ONLY", if print_only { "1" } else { "0" })
        .env("EZ2_TB_WINDOW", window)
        // the handshake: not an EZ2_TB_* input (the title does not depend on it), so the bash
        // path and this one still hand the sampler the same EZ2_TB_* set
        .env("AIB_TITLE_PROTO", SAMPLER_PROTO.to_string())
        .stdin(Stdio::null())
        .stderr(Stdio::inherit());
    match c.output() {
        Ok(o) if o.status.success() => Ok(strip_nl(&o.stdout)),
        Ok(o) => Err(o.status.code().unwrap_or(1)),
        Err(e) => {
            eprintln!("aib-title: {py} {}: {e}", sampler.display());
            Err(1)
        }
    }
}

/// `title-beat.sh --once [--print]`: returns the exit code.
pub fn once(env: &Env, o: Opts, out: &mut dyn Write) -> i32 {
    for k in ["EZ2_COCKPIT_DIR", "EZ2_COCKPIT_STATE_DIR"] {
        if env.get(k).is_empty() {
            // bash would have sourced cockpit-env.sh; without it there is nothing to run
            eprintln!("aib-title: {k} 未設(要由已載入 cockpit-env.sh 的右窗格呼叫)");
            return 1;
        }
    }
    // before the beat file moves: a binary that does not match the sampler changes nothing
    if let Err(m) = proto_check(Path::new(env.get("EZ2_COCKPIT_DIR"))) {
        eprintln!("aib-title: {m};改走 title-beat.sh");
        return PROTO_FALLBACK_RC;
    }
    let beat = next_beat(Path::new(env.get("EZ2_COCKPIT_STATE_DIR")));
    let mut window = env.get("EZ2_TB_WINDOW").to_string();
    if window.is_empty() && !o.print_only {
        window = hub_window(env);
    }
    let title = match sample(env, &beat, &window, o.print_only) {
        Ok(t) => t,
        Err(PROTO_MISMATCH_RC) => {
            eprintln!(
                "aib-title: title_sample.py 說介面不合(rc {PROTO_MISMATCH_RC});改走 title-beat.sh"
            );
            return PROTO_FALLBACK_RC;
        }
        Err(rc) => return rc,
    };
    if !o.print_only {
        write_title(env, &title, o.dry, out);
    }
    let _ = writeln!(out, "{title}");
    0
}
