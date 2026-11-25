//! AP30 (`copilot` 1.0.103): aibd speaking to `copilot` turn-by-turn over `--output-format json`
//! (`--resume=<sessionId>` on turn 2+ using the `sessionId` returned in turn 1's `result`).
//!
//! Translates `copilot` stdout JSON lines (`assistant.message`, `result`) into Claude-compatible
//! `stream-json` lines (`assistant`, `result`) so `ctl`, `usage`, `session_output`, and the web
//! UI read them uniformly.

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

/// Whether `prog` is the `copilot` binary (`copilot` or `/…/copilot` or `copilot.exe`).
pub fn is_copilot(prog: &str) -> bool {
    let base = prog.rsplit(['/', '\\']).next().unwrap_or(prog);
    let stem = base
        .strip_suffix(".exe")
        .or_else(|| base.strip_suffix(".EXE"))
        .unwrap_or(base);
    stem.eq_ignore_ascii_case("copilot")
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

/// Build `copilot` args for one turn (`--resume=<sid>` when `session_id` is `Some`, plus `-p <prompt>`).
pub fn turn_args(base_argv: &[String], session_id: Option<&str>, prompt: &str) -> Vec<String> {
    let mut out: Vec<String> = base_argv.iter().skip(1).cloned().collect();
    if let Some(sid) = session_id {
        out.push(format!("--resume={sid}"));
    }
    out.push("-p".to_owned());
    out.push(prompt.to_owned());
    out
}

/// Translate one `copilot --output-format json` line into zero or one Claude-compatible
/// `stream-json` JSON line (without trailing newline). Captures `sessionId` from `result`.
pub fn translate_line(raw: &str, session_id_out: &mut Option<String>) -> Option<String> {
    let v: Value = serde_json::from_str(raw.trim()).ok()?;
    match v.get("type").and_then(Value::as_str)? {
        "assistant.message" => {
            let content = v
                .get("data")
                .and_then(|d| d.get("content"))
                .and_then(Value::as_str)
                .unwrap_or("");
            if content.is_empty() {
                return None;
            }
            Some(
                json!({
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": content}]
                    }
                })
                .to_string(),
            )
        }
        "result" => {
            if let Some(sid) = v.get("sessionId").and_then(Value::as_str) {
                *session_id_out = Some(sid.to_owned());
            }
            let exit_code = v.get("exitCode").and_then(Value::as_i64).unwrap_or(1);
            let is_err = exit_code != 0;
            let usage = v.get("usage").cloned().unwrap_or_else(|| json!({}));
            let mut out = json!({
                "type": "result",
                "subtype": if is_err { "error" } else { "success" },
                "is_error": is_err,
                "result": "",
                "usage": usage,
            });
            if let Some(sid) = session_id_out.as_deref() {
                out["session_id"] = json!(sid);
            }
            Some(out.to_string())
        }
        _ => None,
    }
}

struct CopilotState {
    exit: Mutex<Option<Exit>>,
    cv: Condvar,
    stopping: AtomicBool,
    active_child: Mutex<Option<Child>>,
}

struct CopilotExitWait(Arc<CopilotState>);

impl ExitWait for CopilotExitWait {
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

struct CopilotInput {
    tx: mpsc::Sender<Vec<u8>>,
    buf: Vec<u8>,
}

impl Write for CopilotInput {
    fn write(&mut self, data: &[u8]) -> std::io::Result<usize> {
        self.buf.extend_from_slice(data);
        while let Some(pos) = self.buf.iter().position(|&b| b == b'\n') {
            let line: Vec<u8> = self.buf.drain(..=pos).collect();
            self.tx.send(line).map_err(|_| {
                std::io::Error::new(std::io::ErrorKind::BrokenPipe, "copilot closed")
            })?;
        }
        Ok(data.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

struct CopilotRunning {
    state: Arc<CopilotState>,
    output: Option<Output>,
    input: Option<Input>,
}

impl Running for CopilotRunning {
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
        Arc::new(CopilotExitWait(self.state.clone()))
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

/// Start a turn-by-turn `copilot` session backed by pipes into `session_output`.
pub fn spawn(spec: &Spec, persona: Option<String>) -> Result<Box<dyn Running>, SpawnFail> {
    let pipe_err = |e: std::io::Error| SpawnFail::from_io(&e);
    let (out_r, mut out_w) = std::io::pipe().map_err(pipe_err)?;
    let (err_r, err_w) = std::io::pipe().map_err(pipe_err)?;
    let (tx, rx) = mpsc::channel::<Vec<u8>>();
    let state = Arc::new(CopilotState {
        exit: Mutex::new(None),
        cv: Condvar::new(),
        stopping: AtomicBool::new(false),
        active_child: Mutex::new(None),
    });
    let st_bg = state.clone();
    let spec_bg = spec.clone();
    let env_bg = child_env(spec, std::env::vars_os());
    let spawned = std::thread::Builder::new()
        .name("aibd-copilot-turns".into())
        .spawn(move || {
            let mut turn = 0usize;
            let mut session_id: Option<String> = None;
            while let Ok(raw_in) = rx.recv() {
                if st_bg.stopping.load(Ordering::SeqCst) {
                    break;
                }
                let Some(text) = user_text(&raw_in) else {
                    continue;
                };
                let prompt = turn_prompt(persona.as_deref(), &text, turn == 0);
                let args = turn_args(&spec_bg.argv, session_id.as_deref(), &prompt);
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
                            &json!({"type":"result","subtype":"error","is_error":true,"result":format!("copilot spawn failed: {e}")}).to_string(),
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
                let mut emitted_result = false;
                if let Some(co) = c_out {
                    let mut br = BufReader::new(co);
                    let mut line = String::new();
                    while br.read_line(&mut line).unwrap_or(0) > 0 {
                        if let Some(out_line) = translate_line(&line, &mut session_id) {
                            if out_line.contains(r#""type":"result""#) {
                                emitted_result = true;
                            }
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
                    let msg = if exit_ok { "" } else { "copilot exited with error" };
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
        Box::new(CopilotInput {
            tx,
            buf: Vec::new(),
        }) as Input
    });
    Ok(Box::new(CopilotRunning {
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
    fn copilot_binary_name_matches_plain_and_path() {
        assert!(is_copilot("copilot"));
        assert!(is_copilot("/opt/homebrew/bin/copilot"));
        assert!(is_copilot(r"C:\bin\copilot.exe"));
        assert!(!is_copilot("claude"));
    }

    #[test]
    fn translates_assistant_message_and_result_with_session_id() {
        let mut sid = None;
        let a = translate_line(
            r#"{"type":"assistant.message","data":{"content":"PONG"}}"#,
            &mut sid,
        )
        .unwrap();
        let va: Value = serde_json::from_str(&a).unwrap();
        assert_eq!(va["type"], "assistant");
        assert_eq!(va["message"]["content"][0]["text"], "PONG");
        let r = translate_line(
            r#"{"type":"result","sessionId":"f8781f79","exitCode":0,"usage":{"premiumRequests":1}}"#,
            &mut sid,
        )
        .unwrap();
        let vr: Value = serde_json::from_str(&r).unwrap();
        assert_eq!(vr["type"], "result");
        assert_eq!(vr["is_error"], false);
        assert_eq!(vr["session_id"], "f8781f79");
        assert_eq!(sid.as_deref(), Some("f8781f79"));
    }

    #[test]
    fn turn_args_adds_resume_only_when_session_id_is_present() {
        let base = vec![
            "copilot".to_owned(),
            "--output-format".to_owned(),
            "json".to_owned(),
        ];
        let t0 = turn_args(&base, None, "hi");
        assert_eq!(t0, ["--output-format", "json", "-p", "hi"]);
        let t1 = turn_args(&base, Some("f8781f79"), "next");
        assert_eq!(
            t1,
            ["--output-format", "json", "--resume=f8781f79", "-p", "next"]
        );
    }
}
