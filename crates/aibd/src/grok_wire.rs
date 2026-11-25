//! AP31 (`grok` 1.0.70): aibd speaking to `grok` turn-by-turn over `--output-format
//! streaming-json` (`-s <uuid>` on turn 1, `-r <uuid>` on later turns, persona prepended via
//! `--prompt-file` so nothing sensitive appears in `argv` / `ps`).
//!
//! Translates `grok` stdout `streaming-json` lines (`text`, `usage`, `end`, `error`) into
//! Claude-compatible `stream-json` lines (`assistant`, `result`) so `ctl`, `usage`,
//! `session_output`, and the web UI read them uniformly.

use crate::platform::session::{
    child_env, CandidateDiag, Exit, ExitWait, Input, Output, Running, SpawnFail, Spec, Stop,
    StopStatus,
};
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, Command, Stdio};
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

struct GrokState {
    exit: Mutex<Option<Exit>>,
    cv: Condvar,
    stopping: AtomicBool,
    active_child: Mutex<Option<Child>>,
}

struct GrokExitWait(Arc<GrokState>);

impl ExitWait for GrokExitWait {
    // ExitWait::wait is called off the async runtime by sessions.rs with the caller's timeout.
    #[allow(clippy::disallowed_methods)]
    fn wait(&self, timeout: Duration) -> Option<Exit> {
        let mut g = self.0.exit.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(e) = g.clone() {
            return Some(e);
        }
        let (g2, _) = self
            .0
            .cv
            .wait_timeout_while(g, timeout, |e| e.is_none())
            .unwrap_or_else(|e| e.into_inner());
        g = g2;
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

impl Running for GrokRunning {
    fn pid(&self) -> u32 {
        0
    }

    fn take_output(&mut self) -> Option<Output> {
        self.output.take()
    }

    fn take_input(&mut self) -> Option<Input> {
        self.input.take()
    }

    fn try_exit(&mut self) -> std::io::Result<Option<Exit>> {
        Ok(self
            .state
            .exit
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone())
    }

    fn exit_wait(&self) -> Arc<dyn ExitWait> {
        Arc::new(GrokExitWait(self.state.clone()))
    }

    fn stop(&mut self, _grace: Duration) -> Stop {
        self.state.stopping.store(true, Ordering::SeqCst);
        if let Some(mut child) = self
            .state
            .active_child
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .take()
        {
            let _ = child.kill();
            let _ = child.wait();
        }
        {
            let mut ex = self.state.exit.lock().unwrap_or_else(|e| e.into_inner());
            if ex.is_none() {
                *ex = Some(Exit::Code(0));
            }
        }
        self.state.cv.notify_all();
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
}

fn write_line(w: &mut std::io::PipeWriter, line: &str) {
    let _ = writeln!(w, "{line}");
    let _ = w.flush();
}

struct PromptGuard(std::path::PathBuf);

impl Drop for PromptGuard {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

fn write_prompt_file(path: &std::path::Path, content: &str) -> std::io::Result<()> {
    let mut opts = std::fs::OpenOptions::new();
    opts.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    let mut f = opts.open(path)?;
    f.write_all(content.as_bytes())?;
    f.flush()
}

/// Start a turn-by-turn `grok` session backed by pipes into `session_output`.
pub fn spawn(spec: &Spec, persona: Option<String>) -> Result<Box<dyn Running>, SpawnFail> {
    let pipe_err = |e: std::io::Error| SpawnFail::from_io(&e);
    let (out_r, mut out_w) = std::io::pipe().map_err(pipe_err)?;
    let (err_r, err_w) = std::io::pipe().map_err(pipe_err)?;
    let (tx, rx) = mpsc::channel::<Vec<u8>>();
    let state = Arc::new(GrokState {
        exit: Mutex::new(None),
        cv: Condvar::new(),
        stopping: AtomicBool::new(false),
        active_child: Mutex::new(None),
    });
    let st_bg = state.clone();
    let spec_bg = spec.clone();
    let env_bg = child_env(spec, std::env::vars_os());
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
                let pbuf = std::env::temp_dir()
                    .join(format!("aibd-grok-{}-{turn}.prompt", spec_bg.id));
                let _guard = PromptGuard(pbuf.clone());
                if write_prompt_file(&pbuf, &prompt).is_err() {
                    write_line(
                        &mut out_w,
                        &json!({"type":"result","subtype":"error","is_error":true,"result":"grok prompt write failed"}).to_string(),
                    );
                    continue;
                }
                let ppath = pbuf.to_string_lossy().into_owned();
                let args = turn_args(&spec_bg.argv, turn, &ppath);
                let child_res = Command::new(&spec_bg.argv[0])
                    .args(&args)
                    .current_dir(&spec_bg.cwd)
                    .env_clear()
                    .envs(env_bg.iter().map(|(k, v)| (k, v)))
                    .stdin(Stdio::null())
                    .stdout(Stdio::piped())
                    .stderr(Stdio::piped())
                    .spawn();
                let mut child = match child_res {
                    Ok(c) => c,
                    Err(e) => {
                        write_line(
                            &mut out_w,
                            &json!({"type":"result","subtype":"error","is_error":true,"result":format!("grok spawn failed: {e}")}).to_string(),
                        );
                        continue;
                    }
                };
                let c_out = child.stdout.take();
                let c_err = child.stderr.take();
                *st_bg.active_child.lock().unwrap_or_else(|e| e.into_inner()) = Some(child);
                let err_handle = c_err.map(|ce| {
                    let mut ew = err_w.try_clone().ok();
                    std::thread::spawn(move || {
                        let mut br = BufReader::new(ce);
                        let mut line = String::new();
                        while br.read_line(&mut line).unwrap_or(0) > 0 {
                            if let Some(w) = ew.as_mut() {
                                let _ = w.write_all(line.as_bytes());
                                let _ = w.flush();
                            }
                            line.clear();
                        }
                    })
                });
                let mut text_buf = String::new();
                let mut emitted_result = false;
                if let Some(co) = c_out {
                    let mut br = BufReader::new(co);
                    let mut line = String::new();
                    while br.read_line(&mut line).unwrap_or(0) > 0 {
                        if let Ok(v) = serde_json::from_str::<Value>(line.trim()) {
                            let kind = v.get("type").and_then(Value::as_str);
                            if matches!(kind, Some("end" | "error")) {
                                if let Some(aline) = assistant_line(&text_buf) {
                                    write_line(&mut out_w, &aline);
                                }
                            }
                        }
                        if let Some(out_line) = translate_line(&line, &mut text_buf) {
                            emitted_result = true;
                            write_line(&mut out_w, &out_line);
                        }
                        line.clear();
                    }
                }
                if let Some(h) = err_handle {
                    let _ = h.join();
                }
                let exit_ok = st_bg
                    .active_child
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .take()
                    .and_then(|mut c| c.wait().ok())
                    .is_some_and(|s| s.success());
                if !emitted_result {
                    if let Some(aline) = assistant_line(&text_buf) {
                        write_line(&mut out_w, &aline);
                    }
                    let msg = if exit_ok { "" } else { "grok exited with error" };
                    write_line(
                        &mut out_w,
                        &json!({"type":"result","subtype": if exit_ok { "success" } else { "error" },"is_error": !exit_ok,"result": msg}).to_string(),
                    );
                }
                turn += 1;
            }
            let mut ex = st_bg.exit.lock().unwrap_or_else(|e| e.into_inner());
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
}
