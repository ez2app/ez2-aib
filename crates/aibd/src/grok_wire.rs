//! AP31 (`grok` 1.0.70): aibd speaking to `grok` turn-by-turn over `--output-format
//! streaming-json` (`-s <uuid>` on turn 1, `-r <uuid>` on later turns, persona prepended via
//! `--prompt-file` so nothing sensitive appears in `argv` / `ps`).
//!
//! Translates `grok` stdout `streaming-json` lines (`text`, `usage`, `end`, `error`) into
//! Claude-compatible `stream-json` lines (`assistant`, `result`) so `ctl`, `usage`,
//! `session_output`, and the web UI read them uniformly.

use crate::turn_wire::{Brand, PromptBy};
use serde_json::{json, Value};
use std::path::Path;

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

pub use crate::turn_wire::{turn_prompt, user_text};

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

/// grok as a [`Brand`] of [`crate::turn_wire`]: the prompt in a private file
/// (`--prompt-file`), `-s` on turn 1 and `-r` after; its `text` pieces gathered into one
/// `assistant` line said before the `result`.
#[derive(Default)]
struct Grok {
    text: String,
}

impl Brand for Grok {
    fn name(&self) -> &'static str {
        "grok"
    }
    fn prompt_by(&self) -> PromptBy {
        PromptBy::File
    }
    fn turn_args(&mut self, base: &[String], turn: usize, file: Option<&Path>) -> Vec<String> {
        let file = file
            .map(|f| f.to_string_lossy().into_owned())
            .unwrap_or_default();
        turn_args(base, turn, &file)
    }
    fn lines(&mut self, raw: &str) -> Vec<String> {
        let mut out = Vec::new();
        if let Ok(v) = serde_json::from_str::<Value>(raw.trim()) {
            if matches!(v.get("type").and_then(Value::as_str), Some("end" | "error")) {
                out.extend(assistant_line(&self.text));
            }
        }
        out.extend(translate_line(raw, &mut self.text));
        out
    }
    fn finish(&mut self) -> Vec<String> {
        assistant_line(&std::mem::take(&mut self.text))
            .into_iter()
            .collect()
    }
}

/// grok's words and output for [`crate::turn_wire::spawn`].
pub fn brand() -> Box<dyn Brand> {
    Box::new(Grok::default())
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
}
