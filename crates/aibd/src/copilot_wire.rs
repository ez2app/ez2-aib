//! AP30 (`copilot` 1.0.103): aibd speaking to `copilot` turn-by-turn over `--output-format json`
//! (`--resume=<sessionId>` on turn 2+ using the `sessionId` returned in turn 1's `result`).
//!
//! Translates `copilot` stdout JSON lines (`assistant.message`, `result`) into Claude-compatible
//! `stream-json` lines (`assistant`, `result`) so `ctl`, `usage`, `session_output`, and the web
//! UI read them uniformly. One program per turn through [`crate::turn_wire`]; the persona card
//! and the user's words go on the turn's stdin, never in argv (08de5 11-25, ebab8 q_cd554b24
//! condition 4: `-p <prompt>` put them where `ps` shows them). That copilot reads a piped
//! stdin as the prompt is from its own `--help` (`-p, or piped stdin`), not run here yet:
//! UNVERIFIED until the seat is measured signed in (condition 6).

use crate::turn_wire::{Brand, PromptBy};
use serde_json::{json, Value};
use std::path::Path;

/// Whether `prog` is the `copilot` binary (`copilot` or `/…/copilot` or `copilot.exe`).
pub fn is_copilot(prog: &str) -> bool {
    let base = prog.rsplit(['/', '\\']).next().unwrap_or(prog);
    let stem = base
        .strip_suffix(".exe")
        .or_else(|| base.strip_suffix(".EXE"))
        .unwrap_or(base);
    stem.eq_ignore_ascii_case("copilot")
}

/// The words that turn off every MCP server `copilot mcp list --json` lists (each
/// `--disable-mcp-server <name>`): the user's own (`~/.copilot/mcp-config.json`, a folder's
/// `.mcp.json`), a plugin's, and the built-in ones `--disable-builtin-mcps` does not reach
/// (1.0.115 lists `computer-use` from its built-in plugin beside `github-mcp-server`). A
/// listing that is not read, or a name that is not plain, starts nothing (`Err`).
pub fn mcp_off(listed: &str) -> Result<Vec<String>, String> {
    let v: Value = serde_json::from_str(listed.trim())
        .map_err(|_| "its MCP list could not be read".to_owned())?;
    let servers = v
        .get("mcpServers")
        .and_then(Value::as_object)
        .ok_or("its MCP list has no mcpServers")?;
    let mut out = Vec::new();
    for name in servers.keys() {
        let plain = !name.is_empty()
            && name.len() <= 64
            && name
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b));
        if !plain {
            return Err("an MCP server's name is not plain".into());
        }
        out.push("--disable-mcp-server".to_owned());
        out.push(name.clone());
    }
    Ok(out)
}

pub use crate::turn_wire::{turn_prompt, user_text};

/// Build `copilot` args for one turn: the seat's words, and `--resume=<sid>` once turn 1
/// gave its `sessionId`. No prompt: it goes on stdin.
pub fn turn_args(base_argv: &[String], session_id: Option<&str>) -> Vec<String> {
    let mut out: Vec<String> = base_argv.iter().skip(1).cloned().collect();
    if let Some(sid) = session_id {
        out.push(format!("--resume={sid}"));
    }
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

/// copilot as a [`Brand`] of [`crate::turn_wire`]: the prompt on stdin, `--resume` with
/// the session id its first `result` said.
#[derive(Default)]
struct Copilot {
    session_id: Option<String>,
}

impl Brand for Copilot {
    fn name(&self) -> &'static str {
        "copilot"
    }
    fn prompt_by(&self) -> PromptBy {
        PromptBy::Stdin // the prompt never in argv (condition 4)
    }
    fn turn_args(&mut self, base: &[String], _turn: usize, _file: Option<&Path>) -> Vec<String> {
        turn_args(base, self.session_id.as_deref())
    }
    fn lines(&mut self, raw: &str) -> Vec<String> {
        translate_line(raw, &mut self.session_id)
            .into_iter()
            .collect()
    }
    fn finish(&mut self) -> Vec<String> {
        Vec::new()
    }
}

/// copilot's words and output for [`crate::turn_wire::spawn`].
pub fn brand() -> Box<dyn Brand> {
    Box::new(Copilot::default())
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
        let t0 = turn_args(&base, None);
        assert_eq!(t0, ["--output-format", "json"]);
        let t1 = turn_args(&base, Some("f8781f79"));
        assert_eq!(t1, ["--output-format", "json", "--resume=f8781f79"]);
    }

    /// 1.0.115 `mcp list --json` here (trimmed): each server turned off by name; an unread
    /// list or a name that is not plain starts nothing.
    #[test]
    fn every_listed_mcp_server_is_turned_off() {
        let listed = r#"{"mcpServers":{"computer-use":{"source":"builtin"},"github-mcp-server":{"source":"builtin"}}}"#;
        assert_eq!(
            mcp_off(listed).unwrap(),
            [
                "--disable-mcp-server",
                "computer-use",
                "--disable-mcp-server",
                "github-mcp-server"
            ]
        );
        assert_eq!(
            mcp_off(r#"{"mcpServers":{}}"#).unwrap(),
            Vec::<String>::new()
        );
        for bad in [
            "",
            "nope",
            "{}",
            r#"{"mcpServers":{"a b":{}}}"#,
            r#"{"mcpServers":{"--allow-all":{}}}"#,
        ] {
            assert!(mcp_off(bad).is_err(), "reason: bad_list_started {bad}");
        }
    }

    /// Condition 4: copilot takes its prompt on stdin, and its words carry no prompt.
    #[test]
    fn the_copilot_prompt_goes_on_stdin_never_in_its_words() {
        let mut b = brand();
        assert_eq!(
            b.prompt_by(),
            PromptBy::Stdin,
            "reason: prompt_not_on_stdin"
        );
        let base = [
            "copilot".to_owned(),
            "--output-format".to_owned(),
            "json".to_owned(),
        ];
        for turn in [0, 1] {
            let a = b.turn_args(&base, turn, None);
            assert!(
                !a.iter().any(|w| w == "-p" || w == "--prompt" || w == "-i"),
                "reason: prompt_flag_in_words {a:?}"
            );
        }
    }
}
