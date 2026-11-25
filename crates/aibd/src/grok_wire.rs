//! AP31 (`grok` 1.0.70): aibd speaking to `grok` turn-by-turn over `--output-format
//! streaming-json` (`-s <uuid>` on turn 1, `-r <uuid>` on later turns, persona prepended via
//! `--prompt-file` so nothing sensitive appears in `argv` / `ps`).
//!
//! Translates `grok` stdout `streaming-json` lines (`text`, `usage`, `end`, `error`) into
//! Claude-compatible `stream-json` lines (`assistant`, `result`) so `ctl`, `usage`,
//! `session_output`, and the web UI read them uniformly.

use crate::lineage::Lineage;
use crate::platform::session::{
    CandidateDiag, Exit, ExitWait, Input, Output, Running, SpawnFail, SpawnFn, Spec, Stop,
    StopStatus,
};
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Condvar, Mutex};
use std::time::Duration;

/// Whether `prog` is the `grok` binary (`grok` or `/…/grok` or `grok.exe`).
pub fn is_grok(prog: &str) -> bool {
    let base = prog.rsplit(['/', '\\']).next().unwrap_or(prog);
    let stem = base
        .strip_suffix(".exe")
        .or_else(|| base.strip_suffix(".EXE"))
        .unwrap_or(base);
    stem.eq_ignore_ascii_case("grok")
}

/// What `grok inspect --json` (1.0.70) lists that would run in a safe seat: none may be there.
/// MCP servers and LSP servers are programs grok starts outside the seat's sandbox; hooks run
/// commands; an enabled plugin brings any of them. `Err` says which, with how many; a listing
/// that is not a JSON object, or lacks one of these keys, is refused too (08de5 11-25: grok
/// on this Mac lists 2 MCP servers from ~/.cursor/mcp.json, 2 hooks and 4 plugins from
/// ~/.claude/plugins, 2 LSP servers, with no flag to turn them off).
pub fn nothing_loaded(listed: &str) -> Result<(), String> {
    let v: Value = serde_json::from_str(listed.trim())
        .map_err(|_| "its set-up listing could not be read".to_owned())?;
    let mut found = Vec::new();
    // a key this listing did not have when it was read (1.0.70, 08de5 11-25) that is not
    // empty: something grok loads now that nobody looked at (3e53b REVIEW-grok L2)
    let top = v.as_object().ok_or("its set-up listing is not an object")?;
    for (key, val) in top {
        let empty = match val {
            Value::Null | Value::Bool(false) => true,
            Value::Array(a) => a.is_empty(),
            Value::Object(o) => o.is_empty(),
            Value::String(s) => s.is_empty(),
            _ => false,
        };
        if !INSPECT_KEYS.contains(&key.as_str()) && !empty {
            found.push(format!(
                "{} (not known to aibd)",
                key.chars().take(40).collect::<String>()
            ));
        }
    }
    for key in ["mcpServers", "hooks", "lspServers"] {
        let n = v
            .get(key)
            .and_then(Value::as_array)
            .ok_or_else(|| format!("its set-up listing has no {key}"))?
            .len();
        if n > 0 {
            found.push(format!("{key} {n}"));
        }
    }
    let on = v
        .get("plugins")
        .and_then(Value::as_array)
        .ok_or("its set-up listing has no plugins")?
        .iter()
        .filter(|p| p.get("enabled").and_then(Value::as_bool) != Some(false))
        .count();
    if on > 0 {
        found.push(format!("plugins {on}"));
    }
    if found.is_empty() {
        Ok(())
    } else {
        Err(format!(
            "grok would load the user's own {}; turn them off for grok first",
            found.join(", ")
        ))
    }
}

/// The top-level keys `grok inspect --json` printed on 1.0.70 (08de5 11-25, this Mac signed in).
pub const INSPECT_KEYS: &[&str] = &[
    "grokVersion",
    "channel",
    "updateChannel",
    "cwd",
    "projectRoot",
    "projectTrusted",
    "projectInstructions",
    "permissions",
    "loginPolicy",
    "hooks",
    "skills",
    "agents",
    "plugins",
    "marketplaces",
    "mcpServers",
    "lspServers",
    "configSources",
    "experiments",
    "externalCompat",
    "configWarnings",
];

/// The first line of every turn's prompt file. grok 1.0.70 runs a prompt file whose first
/// line is a slash command as that command, on this machine, without the model (`/hooks-list`
/// listed the user's 22 hooks; measured signed in, EVIDENCE-brand-grok-08de5-1125 §M2), and
/// its commands include `hooks-add` and `always-approve`; on a later line it is only text.
/// The user's words, the persona card or a handoff note an AI wrote never come first.
pub const PROMPT_OPENER: &str = "Message from the aibd seat:";

/// What a turn's prompt file holds: [`PROMPT_OPENER`], then the prompt.
pub fn prompt_file_text(prompt: &str) -> String {
    format!("{PROMPT_OPENER}\n{prompt}")
}

/// Extract the plain user text from one Claude-style `stream-json` user line.
pub fn user_text(line: &[u8]) -> Option<String> {
    let v: Value = serde_json::from_slice(line).ok()?;
    let msg = v.get("message")?;
    let content = msg.get("content")?;
    if let Some(s) = content.as_str() {
        return Some(s.to_owned());
    }
    let arr = content.as_array()?;
    let mut out = String::new();
    for b in arr {
        if b.get("type").and_then(Value::as_str) == Some("text") {
            if let Some(t) = b.get("text").and_then(Value::as_str) {
                if !out.is_empty() {
                    out.push('\n');
                }
                out.push_str(t);
            }
        }
    }
    (!out.is_empty()).then_some(out)
}

/// Build the turn-1 prompt combining `persona` (if any) and `prompt`.
pub fn turn_prompt(persona: Option<&str>, prompt: &str, is_first: bool) -> String {
    match persona.filter(|p| is_first && !p.is_empty()) {
        Some(p) => format!("{p}\n\n{prompt}"),
        None => prompt.to_owned(),
    }
}

/// Translate one `grok --output-format streaming-json` line into zero or one
/// Claude-compatible `stream-json` JSON line (without trailing newline).
pub fn translate_line(raw: &str, buf: &mut String) -> Option<String> {
    let v: Value = serde_json::from_str(raw.trim()).ok()?;
    match v.get("type").and_then(Value::as_str)? {
        "text" => {
            if let Some(d) = v.get("data").and_then(Value::as_str) {
                buf.push_str(d);
            }
            None
        }
        "end" => {
            let text = std::mem::take(buf);
            let usage = v.get("usage").cloned().unwrap_or_else(|| json!({}));
            let is_err = v
                .get("stopReason")
                .and_then(Value::as_str)
                .is_some_and(|r| r == "error");
            let mut out = json!({
                "type": "result",
                "subtype": if is_err { "error" } else { "success" },
                "is_error": is_err,
                "result": text,
                "usage": usage,
            });
            if let Some(sid) = v.get("sessionId").and_then(Value::as_str) {
                out["session_id"] = json!(sid);
            }
            Some(out.to_string())
        }
        "error" => {
            let msg = v
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or("grok error");
            Some(
                json!({
                    "type": "result",
                    "subtype": "error",
                    "is_error": true,
                    "result": msg,
                })
                .to_string(),
            )
        }
        _ => None,
    }
}

/// Wrap accumulated assistant text into one Claude-style `assistant` event line if non-empty.
pub fn assistant_line(text: &str) -> Option<String> {
    if text.is_empty() {
        return None;
    }
    Some(
        json!({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}]
            }
        })
        .to_string(),
    )
}

/// Build `grok` args for turn `turn_idx` (`0` keeps `-s`, later turns switch to `-r`) and
/// append `--prompt-file <path>`.
pub fn turn_args(base_argv: &[String], turn_idx: usize, prompt_file: &str) -> Vec<String> {
    let mut out = Vec::with_capacity(base_argv.len() + 2);
    for arg in base_argv.iter().skip(1) {
        if turn_idx > 0 && arg == "-s" {
            out.push("-r".to_owned());
        } else {
            out.push(arg.clone());
        }
    }
    out.push("--prompt-file".to_owned());
    out.push(prompt_file.to_owned());
    out
}

/// How a turn's `grok` is started: through aibd's own spawn (the platform's tree, and M1
/// lineage on macOS), never a bare `Command` — a turn's program is a session tree like any
/// other, so a stop finds what it started (08de5 11-25, ebab8 q_cd554b24 condition 2).
#[derive(Clone)]
pub struct Turns {
    pub spawn: SpawnFn,
    pub lineage: Option<Arc<Lineage>>,
    /// Where a turn's prompt file is written: aibd's own folder, owner only
    /// ([`crate::platform::create_private_dir`]), never the shared temp folder (condition 3).
    pub prompt_dir: PathBuf,
    /// The seat's gate run again on each turn's words before it starts: grok starts anew and
    /// reads its set-up again every turn, so what was added after the seat opened (an MCP
    /// server, a hook, a plugin, another version) is found before that turn (3e53b M1).
    pub check: fn(&Spec) -> Result<(), String>,
}

/// How long a turn's stop may wait for the tree after its output closed.
const TURN_GRACE: Duration = Duration::from_secs(2);
/// How long the end of a turn waits for its root's exit after its output closed.
const TURN_EXIT_WAIT: Duration = Duration::from_secs(5);

struct GrokState {
    exit: Mutex<Option<Exit>>,
    cv: Condvar,
    stopping: AtomicBool,
    /// The turn running now, as aibd's spawn returned it.
    active: Mutex<Option<Box<dyn Running>>>,
    /// A finished turn's stop that was not clean: the session's stop reports it.
    left: Mutex<Option<Stop>>,
}

fn locked<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

struct GrokExitWait(Arc<GrokState>);

impl ExitWait for GrokExitWait {
    // ExitWait::wait is called off the async runtime by sessions.rs with the caller's timeout.
    #[allow(clippy::disallowed_methods)]
    fn wait(&self, timeout: Duration) -> Option<Exit> {
        let g = locked(&self.0.exit);
        if let Some(e) = g.clone() {
            return Some(e);
        }
        let (g, _) = self
            .0
            .cv
            .wait_timeout_while(g, timeout, |e| e.is_none())
            .unwrap_or_else(|e| e.into_inner());
        g.clone()
    }
}

struct GrokInput {
    tx: mpsc::Sender<Vec<u8>>,
    buf: Vec<u8>,
}

impl Write for GrokInput {
    fn write(&mut self, data: &[u8]) -> std::io::Result<usize> {
        self.buf.extend_from_slice(data);
        while let Some(pos) = self.buf.iter().position(|&b| b == b'\n') {
            let line: Vec<u8> = self.buf.drain(..=pos).collect();
            self.tx
                .send(line)
                .map_err(|_| std::io::Error::new(std::io::ErrorKind::BrokenPipe, "grok closed"))?;
        }
        Ok(data.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

struct GrokRunning {
    state: Arc<GrokState>,
    output: Option<Output>,
    input: Option<Input>,
}

/// The stop of a session with no turn running and none left unclean: nothing of it is alive.
fn idle_stop() -> Stop {
    Stop {
        status: StopStatus::Clean,
        survivors: Vec::new(),
        tree_size: 0,
        signals: Vec::new(),
        blind_spots: Vec::new(),
        candidates: CandidateDiag {
            count: 0,
            orphan_extra: 0,
            unreadable: Vec::new(),
            unreadable_more: 0,
            other_coalition: Vec::new(),
            other_coalition_more: 0,
        },
    }
}

impl Running for GrokRunning {
    fn pid(&self) -> u32 {
        locked(&self.state.active).as_ref().map_or(0, |r| r.pid())
    }

    fn take_output(&mut self) -> Option<Output> {
        self.output.take()
    }

    fn take_input(&mut self) -> Option<Input> {
        self.input.take()
    }

    fn try_exit(&mut self) -> std::io::Result<Option<Exit>> {
        Ok(locked(&self.state.exit).clone())
    }

    fn exit_wait(&self) -> Arc<dyn ExitWait> {
        Arc::new(GrokExitWait(self.state.clone()))
    }

    /// The turn running now is stopped as the platform stops a tree, and its own report is
    /// the session's (a tree not clean stays not clean); with none running, a finished turn
    /// whose stop was not clean is reported; else nothing is alive.
    fn stop(&mut self, grace: Duration) -> Stop {
        self.state.stopping.store(true, Ordering::SeqCst);
        let turn = locked(&self.state.active).take().map(|mut r| r.stop(grace));
        let left = locked(&self.state.left).take();
        {
            let mut ex = locked(&self.state.exit);
            if ex.is_none() {
                *ex = Some(Exit::Code(0));
            }
        }
        self.state.cv.notify_all();
        turn.or(left).unwrap_or_else(idle_stop)
    }
}

fn write_line(w: &mut std::io::PipeWriter, line: &str) {
    let _ = writeln!(w, "{line}");
    let _ = w.flush();
}

/// A turn's prompt file, removed when the turn is over.
struct PromptGuard(PathBuf);

impl Drop for PromptGuard {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

/// Write `content` to a new file of a random name in `dir` (aibd's own folder, owner only):
/// created only if no file is there (O_EXCL), never through a link (O_NOFOLLOW on Unix; on
/// Windows create_new refuses an existing name, links included — UNVERIFIED on a machine),
/// owner only (0600; on Windows the folder's owner-only DACL). An AI of the same account that
/// guessed the name cannot make aibd write through it (08de5 11-25 TAKEOVER-brands §1.3).
fn write_prompt_file(dir: &std::path::Path, content: &str) -> std::io::Result<PathBuf> {
    crate::platform::create_private_dir(dir)?;
    let name = crate::sessions::new_id("p_").map_err(std::io::Error::other)?;
    let path = dir.join(format!("{name}.prompt"));
    let mut f = open_new_private(&path)?;
    f.write_all(content.as_bytes())?;
    f.flush()?;
    Ok(path)
}

/// A new file at `path`, owner only: refused when anything is at that name, a link included.
fn open_new_private(path: &std::path::Path) -> std::io::Result<std::fs::File> {
    let mut opts = std::fs::OpenOptions::new();
    opts.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600).custom_flags(nix::libc::O_NOFOLLOW);
    }
    opts.open(path)
}

/// One turn: started through `turns.spawn`, its stdout translated onto `out_w`, its stderr
/// copied onto `err_w`; the turn's tree stopped (and reaped) once its output closed. Returns
/// whether the root ended with code 0.
fn run_turn(
    st: &GrokState,
    turns: &Turns,
    spec: &Spec,
    args: Vec<String>,
    out_w: &mut std::io::PipeWriter,
    err_w: &std::io::PipeWriter,
) -> Result<bool, String> {
    let turn_spec = Spec {
        argv: std::iter::once(spec.argv[0].clone()).chain(args).collect(),
        input: false,
        ..spec.clone()
    };
    if let Err(why) = (turns.check)(&turn_spec) {
        return Err(format!("grok turn not started: {why}"));
    }
    let mut run = (turns.spawn)(&turn_spec, turns.lineage.clone())
        .map_err(|e| format!("grok spawn failed: {}", e.tag))?;
    let output = run.take_output();
    *locked(&st.active) = Some(run);
    // a stop that came before the turn was kept finds it here (it set `stopping` first)
    if st.stopping.load(Ordering::SeqCst) {
        if let Some(mut r) = locked(&st.active).take() {
            let s = r.stop(TURN_GRACE);
            *locked(&st.left) = Some(s);
        }
        return Ok(false);
    }
    let Some(Output { stdout, stderr }) = output else {
        return Err("grok output not captured".into());
    };
    let mut ew = err_w.try_clone().ok();
    let err_copy = std::thread::spawn(move || {
        let mut br = BufReader::new(stderr);
        let mut line = String::new();
        while br.read_line(&mut line).unwrap_or(0) > 0 {
            if let Some(w) = ew.as_mut() {
                let _ = w.write_all(line.as_bytes());
                let _ = w.flush();
            }
            line.clear();
        }
    });
    let mut text_buf = String::new();
    let mut emitted_result = false;
    let mut br = BufReader::new(stdout);
    let mut line = String::new();
    while br.read_line(&mut line).unwrap_or(0) > 0 {
        if let Ok(v) = serde_json::from_str::<Value>(line.trim()) {
            if matches!(v.get("type").and_then(Value::as_str), Some("end" | "error")) {
                if let Some(aline) = assistant_line(&text_buf) {
                    write_line(out_w, &aline);
                }
            }
        }
        if let Some(out_line) = translate_line(&line, &mut text_buf) {
            emitted_result = true;
            write_line(out_w, &out_line);
        }
        line.clear();
    }
    let _ = err_copy.join();
    // the output closed: the root's exit, then the turn's tree stopped and reaped; a stop
    // that took the turn first has its report already
    let exit_ok = match locked(&st.active).take() {
        Some(mut r) => {
            let ex = r.exit_wait().wait(TURN_EXIT_WAIT);
            let s = r.stop(TURN_GRACE);
            if !matches!(s.status, StopStatus::Clean) {
                *locked(&st.left) = Some(s);
            }
            ex == Some(Exit::Code(0))
        }
        None => false,
    };
    if !emitted_result {
        if let Some(aline) = assistant_line(&text_buf) {
            write_line(out_w, &aline);
        }
        let msg = if exit_ok {
            ""
        } else {
            "grok exited with error"
        };
        write_line(
            out_w,
            &json!({"type":"result","subtype": if exit_ok { "success" } else { "error" },"is_error": !exit_ok,"result": msg}).to_string(),
        );
    }
    Ok(exit_ok)
}

/// Start a turn-by-turn `grok` session backed by pipes into `session_output`. Each turn's
/// program is started through `turns` (aibd's own spawn).
pub fn spawn(
    spec: &Spec,
    persona: Option<String>,
    turns: Turns,
) -> Result<Box<dyn Running>, SpawnFail> {
    let pipe_err = |e: std::io::Error| SpawnFail::from_io(&e);
    let (out_r, mut out_w) = std::io::pipe().map_err(pipe_err)?;
    let (err_r, err_w) = std::io::pipe().map_err(pipe_err)?;
    let (tx, rx) = mpsc::channel::<Vec<u8>>();
    let state = Arc::new(GrokState {
        exit: Mutex::new(None),
        cv: Condvar::new(),
        stopping: AtomicBool::new(false),
        active: Mutex::new(None),
        left: Mutex::new(None),
    });
    let st_bg = state.clone();
    let spec_bg = spec.clone();
    let spawned = std::thread::Builder::new()
        .name("aibd-grok-turns".into())
        .spawn(move || {
            let mut turn = 0usize;
            while let Ok(raw_in) = rx.recv() {
                if st_bg.stopping.load(Ordering::SeqCst) {
                    break;
                }
                let Some(text) = user_text(&raw_in) else {
                    continue;
                };
                let prompt = turn_prompt(persona.as_deref(), &text, turn == 0);
                let fail = |out_w: &mut std::io::PipeWriter, why: &str| {
                    write_line(
                        out_w,
                        &json!({"type":"result","subtype":"error","is_error":true,"result":why})
                            .to_string(),
                    );
                };
                let path = match write_prompt_file(&turns.prompt_dir, &prompt_file_text(&prompt)) {
                    Ok(p) => p,
                    Err(_) => {
                        fail(&mut out_w, "grok prompt write failed");
                        continue;
                    }
                };
                let _guard = PromptGuard(path.clone());
                let args = turn_args(&spec_bg.argv, turn, &path.to_string_lossy());
                if let Err(why) = run_turn(&st_bg, &turns, &spec_bg, args, &mut out_w, &err_w) {
                    fail(&mut out_w, &why);
                    continue;
                }
                turn += 1;
            }
            let mut ex = locked(&st_bg.exit);
            if ex.is_none() {
                *ex = Some(Exit::Code(0));
            }
            st_bg.cv.notify_all();
        });
    if let Err(e) = spawned {
        return Err(SpawnFail::from_io(&e));
    }
    let input: Option<Input> = spec.input.then(|| {
        Box::new(GrokInput {
            tx,
            buf: Vec::new(),
        }) as Input
    });
    Ok(Box::new(GrokRunning {
        state,
        output: Some(Output {
            stdout: Box::new(out_r),
            stderr: Box::new(err_r),
        }),
        input,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn grok_binary_name_matches_plain_and_path() {
        assert!(is_grok("grok"));
        assert!(is_grok("/Users/u/.local/bin/grok"));
        assert!(is_grok(r"C:\bin\grok.exe"));
        assert!(!is_grok("claude"));
    }

    #[test]
    fn extracts_user_text_and_prepends_persona_only_on_first_turn() {
        let line = br#"{"type":"user","message":{"role":"user","content":[{"type":"text","text":"hello"}]}}"#;
        let t = user_text(line).unwrap();
        assert_eq!(t, "hello");
        assert_eq!(
            turn_prompt(Some("You are A."), &t, true),
            "You are A.\n\nhello"
        );
        assert_eq!(turn_prompt(Some("You are A."), &t, false), "hello");
    }

    #[test]
    fn translates_streaming_json_text_and_end_to_result() {
        let mut buf = String::new();
        assert_eq!(
            translate_line(r#"{"type":"text","data":"HE"}"#, &mut buf),
            None
        );
        assert_eq!(
            translate_line(r#"{"type":"text","data":"LLO"}"#, &mut buf),
            None
        );
        assert_eq!(
            assistant_line(&buf).as_deref(),
            Some(
                r#"{"message":{"content":[{"text":"HELLO","type":"text"}],"role":"assistant"},"type":"assistant"}"#
            )
        );
        let res = translate_line(
            r#"{"type":"end","stopReason":"end_turn","sessionId":"abc","usage":{"input_tokens":10}}"#,
            &mut buf,
        )
        .unwrap();
        let v: Value = serde_json::from_str(&res).unwrap();
        assert_eq!(v["type"], "result");
        assert_eq!(v["is_error"], false);
        assert_eq!(v["result"], "HELLO");
        assert_eq!(v["session_id"], "abc");
        assert_eq!(v["usage"]["input_tokens"], 10);
        assert!(buf.is_empty());
    }

    #[test]
    fn turn_args_switches_s_to_r_after_first_turn() {
        let base = vec![
            "grok".to_owned(),
            "--output-format".to_owned(),
            "streaming-json".to_owned(),
            "-s".to_owned(),
            "0f1e2d3c-4b5a-4968-8776-655443322110".to_owned(),
        ];
        let t0 = turn_args(&base, 0, "/tmp/p0.txt");
        assert!(t0
            .windows(2)
            .any(|w| w == ["-s", "0f1e2d3c-4b5a-4968-8776-655443322110"]));
        assert_eq!(&t0[t0.len() - 2..], ["--prompt-file", "/tmp/p0.txt"]);
        let t1 = turn_args(&base, 1, "/tmp/p1.txt");
        assert!(t1
            .windows(2)
            .any(|w| w == ["-r", "0f1e2d3c-4b5a-4968-8776-655443322110"]));
        assert!(!t1.iter().any(|w| w == "-s"));
    }

    /// grok 1.0.70 `inspect --json` on andymini-class Mac (08de5 11-25, trimmed to the keys read).
    const INSPECT_LOADED: &str = r#"{"grokVersion":"1.0.70","mcpServers":[{"name":"notebooks","transport":"stdio","target":"node","source":{"type":"mcpJson","path":"/Users/u/.cursor/mcp.json"},"compatibilityStatus":"enabled","vendor":"cursor"}],"hooks":[{"event":"(plugin)","hookType":"file","target":"/Users/u/.claude/plugins/cache/x/hooks/hooks.json"}],"plugins":[{"name":"rust-analyzer-lsp","scope":"user","enabled":true},{"name":"off","enabled":false}],"lspServers":[{"name":"rust-analyzer","command":"rust-analyzer","args":[]}],"skills":[{"name":"a"}]}"#;

    #[test]
    fn a_grok_that_would_load_the_users_own_set_up_is_refused() {
        let e = nothing_loaded(INSPECT_LOADED).unwrap_err();
        for part in ["mcpServers 1", "hooks 1", "lspServers 1", "plugins 1"] {
            assert!(e.contains(part), "reason: {part}_not_said {e}");
        }
        let empty = r#"{"mcpServers":[],"hooks":[],"lspServers":[],"plugins":[{"name":"off","enabled":false}],"skills":[{"name":"a"}]}"#;
        assert_eq!(nothing_loaded(empty), Ok(()));
        for key in ["mcpServers", "hooks", "lspServers"] {
            let mut v: Value = serde_json::from_str(empty).unwrap();
            v[key] = json!([{"name": "x"}]);
            assert!(
                nothing_loaded(&v.to_string()).is_err(),
                "reason: {key}_opened"
            );
            v.as_object_mut().unwrap().remove(key);
            assert!(
                nothing_loaded(&v.to_string()).is_err(),
                "reason: no_{key}_opened"
            );
        }
        let mut v: Value = serde_json::from_str(empty).unwrap();
        v["plugins"] = json!([{"name": "p"}]);
        assert!(
            nothing_loaded(&v.to_string()).is_err(),
            "reason: plugin_without_enabled_opened"
        );
        // 3e53b L2: a key the measured listing did not have, not empty: refused; empty: not
        let mut v: Value = serde_json::from_str(empty).unwrap();
        v["commands"] = json!([{"name": "run", "shell": "x"}]);
        let e = nothing_loaded(&v.to_string()).unwrap_err();
        assert!(
            e.contains("commands (not known to aibd)"),
            "reason: unknown_key_opened {e}"
        );
        v["commands"] = json!([]);
        v["newFlag"] = Value::Null;
        assert_eq!(
            nothing_loaded(&v.to_string()),
            Ok(()),
            "reason: empty_unknown_refused"
        );
        for bad in ["", "not json", "[]"] {
            assert!(
                nothing_loaded(bad).is_err(),
                "reason: unread_listing_opened {bad}"
            );
        }
    }

    /// The specs every turn was started with, through the fake spawn below.
    static STARTED: std::sync::Mutex<Vec<(Spec, String)>> = std::sync::Mutex::new(Vec::new());

    /// The seat's gate as a test has it: refused once a file `refuse` is in the seat's folder.
    fn folder_check(spec: &Spec) -> Result<(), String> {
        if spec.cwd.join("refuse").exists() {
            Err("grok would load the user's own hooks 1 (grok_user_setup)".into())
        } else {
            Ok(())
        }
    }

    struct DoneWait;
    impl ExitWait for DoneWait {
        fn wait(&self, _timeout: Duration) -> Option<Exit> {
            Some(Exit::Code(0))
        }
    }

    /// A turn that printed one answer and left a process behind.
    struct FakeTurn(Option<Output>);
    impl Running for FakeTurn {
        fn pid(&self) -> u32 {
            4242
        }
        fn take_output(&mut self) -> Option<Output> {
            self.0.take()
        }
        fn try_exit(&mut self) -> std::io::Result<Option<Exit>> {
            Ok(Some(Exit::Code(0)))
        }
        fn exit_wait(&self) -> Arc<dyn ExitWait> {
            Arc::new(DoneWait)
        }
        fn stop(&mut self, _grace: Duration) -> Stop {
            Stop {
                status: StopStatus::TreeNotClean,
                survivors: vec![4243],
                ..idle_stop()
            }
        }
    }

    fn fake_spawn(spec: &Spec, _l: Option<Arc<Lineage>>) -> Result<Box<dyn Running>, SpawnFail> {
        let file = spec
            .argv
            .iter()
            .position(|w| w == "--prompt-file")
            .and_then(|i| spec.argv.get(i + 1))
            .and_then(|p| std::fs::read_to_string(p).ok())
            .unwrap_or_default();
        STARTED.lock().unwrap().push((spec.clone(), file));
        let out = "{\"type\":\"text\",\"data\":\"OK\"}\n{\"type\":\"end\",\"stopReason\":\"end_turn\",\"sessionId\":\"s\"}\n";
        Ok(Box::new(FakeTurn(Some(Output {
            stdout: Box::new(std::io::Cursor::new(out.as_bytes().to_vec())),
            stderr: Box::new(std::io::Cursor::new(Vec::new())),
        }))))
    }

    /// The turns started for the seat in `cwd` (tests run side by side).
    fn mine(cwd: &std::path::Path) -> Vec<(Spec, String)> {
        STARTED
            .lock()
            .unwrap()
            .iter()
            .filter(|(s, _)| s.cwd == cwd)
            .cloned()
            .collect()
    }

    /// 3e53b M1: the gate is run again before every turn; a turn after something was added
    /// to the user's set-up is refused with the reason and never started.
    #[test]
    fn a_turn_after_the_set_up_changed_is_refused_and_not_started() {
        let d = tempfile::tempdir().unwrap();
        let spec = Spec {
            id: "s_0123456789abcdee".into(),
            argv: vec!["/bin/grok".into(), "-s".into(), "u".into()],
            cwd: d.path().into(),
            env: Vec::new(),
            pass: Vec::new(),
            input: true,
        };
        let turns = Turns {
            spawn: fake_spawn,
            lineage: None,
            prompt_dir: d.path().join("turn-prompts"),
            check: folder_check,
        };
        let mut s = spawn(&spec, None, turns).unwrap();
        let mut input = s.take_input().unwrap();
        let mut out = std::io::BufReader::new(s.take_output().unwrap().stdout);
        let mut turn = |text: &str| {
            writeln!(
                input,
                "{}",
                json!({"type":"user","message":{"role":"user","content":text}})
            )
            .unwrap();
            loop {
                let mut l = String::new();
                assert!(out.read_line(&mut l).unwrap() > 0, "reason: no_result");
                if l.contains("\"result\"") {
                    return l;
                }
            }
        };
        assert!(
            !turn("one").contains("is_error\":true"),
            "reason: first_turn_refused"
        );
        std::fs::write(d.path().join("refuse"), "").unwrap();
        let r = turn("two");
        assert!(
            r.contains("grok turn not started") && r.contains("grok_user_setup"),
            "reason: changed_set_up_started {r}"
        );
        assert_eq!(mine(d.path()).len(), 1, "reason: refused_turn_started");
    }

    /// 08de5 11-25 conditions 2 and 3: a turn is started through the spawn aibd hands over
    /// (its argv the seat's own words plus the turn's prompt file, no stdin), the prompt file
    /// sits in the private folder and is gone after the turn, and a turn whose tree did not
    /// stop clean is what the session's stop reports.
    #[test]
    fn every_turn_goes_through_aibds_spawn_and_its_stop_is_reported() {
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().join("turn-prompts");
        let spec = Spec {
            id: "s_0123456789abcdef".into(),
            argv: vec![
                "/bin/grok".into(),
                "--sandbox".into(),
                "readonly".into(),
                "-s".into(),
                "u".into(),
            ],
            cwd: d.path().into(),
            env: Vec::new(),
            pass: Vec::new(),
            input: true,
        };
        let turns = Turns {
            spawn: fake_spawn,
            lineage: None,
            prompt_dir: dir.clone(),
            check: folder_check,
        };
        let mut s = spawn(&spec, Some("CARD".into()), turns).unwrap();
        let mut input = s.take_input().unwrap();
        let mut out = std::io::BufReader::new(s.take_output().unwrap().stdout);
        writeln!(
            input,
            "{}",
            json!({"type":"user","message":{"role":"user","content":"/hooks-add rm"}})
        )
        .unwrap();
        let mut seen = String::new();
        while !seen.contains("\"result\"") {
            let mut l = String::new();
            assert!(
                out.read_line(&mut l).unwrap() > 0,
                "reason: no_result {seen}"
            );
            seen.push_str(&l);
        }
        assert!(seen.contains("OK"), "{seen}");
        let started: Vec<Spec> = mine(d.path()).into_iter().map(|(s, _)| s).collect();
        assert_eq!(started.len(), 1, "reason: turn_not_through_spawn");
        let file = &mine(d.path())[0].1;
        assert_eq!(
            file.lines().next(),
            Some(PROMPT_OPENER),
            "reason: prompt_file_starts_with_user_words {file:?}"
        );
        assert!(file.ends_with("CARD\n\n/hooks-add rm"), "{file:?}");
        let a = &started[0].argv;
        assert_eq!(&a[..5], &spec.argv[..], "reason: seat_words_changed {a:?}");
        assert_eq!(a[5], "--prompt-file");
        assert!(
            a[6].starts_with(&dir.to_string_lossy().into_owned()),
            "reason: prompt_not_private {a:?}"
        );
        assert!(!started[0].input, "reason: turn_given_stdin");
        // the file goes when the turn is over, just after its result line was written
        let gone = (0..100).any(|_| {
            let g = !std::path::Path::new(&a[6]).exists();
            if !g {
                #[allow(clippy::disallowed_methods)] // a test's short poll, no timer to register
                std::thread::sleep(Duration::from_millis(20));
            }
            g
        });
        assert!(gone, "reason: prompt_left");
        let stop = s.stop(Duration::from_millis(10));
        assert_eq!(
            stop.status,
            StopStatus::TreeNotClean,
            "reason: unclean_turn_said_clean"
        );
        assert_eq!(stop.survivors, vec![4243]);
    }

    /// Condition 3: the prompt file gets a new random name each time, owner only, and a name
    /// already there (a link planted by an AI of the same account) is never written through.
    #[cfg(unix)]
    #[test]
    fn a_prompt_file_is_new_private_and_never_written_through_a_link() {
        use std::os::unix::fs::PermissionsExt;
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().join("p");
        let a = write_prompt_file(&dir, "one").unwrap();
        let b = write_prompt_file(&dir, "two").unwrap();
        assert_ne!(a, b, "reason: guessable_name");
        assert_eq!(
            std::fs::metadata(&a).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert_eq!(
            std::fs::metadata(&dir).unwrap().permissions().mode() & 0o777,
            0o700
        );
        // the open itself: a name that is there, a link, is refused and its target untouched
        let target = d.path().join("victim");
        std::fs::write(&target, "keep").unwrap();
        let planted = dir.join("planted.prompt");
        std::os::unix::fs::symlink(&target, &planted).unwrap();
        assert!(
            open_new_private(&planted).is_err(),
            "reason: written_through_link"
        );
        assert_eq!(std::fs::read_to_string(&target).unwrap(), "keep");
        // and a plain file already there is not written over either
        let there = dir.join("there.prompt");
        std::fs::write(&there, "old").unwrap();
        assert!(
            open_new_private(&there).is_err(),
            "reason: existing_file_reused"
        );
        assert_eq!(std::fs::read_to_string(&there).unwrap(), "old");
    }
}
