//! AP31 (`grok` 1.0.70): aibd speaking to `grok` turn-by-turn over `--output-format
//! streaming-json` (`-s <uuid>` on turn 1, `-r <uuid>` on later turns, persona prepended via
//! `--prompt-file` so nothing sensitive appears in `argv` / `ps`).
//!
//! Translates `grok` stdout `streaming-json` lines (`text`, `usage`, `end`, `error`) into
//! Claude-compatible `stream-json` lines (`assistant`, `result`) so `ctl`, `usage`,
//! `session_output`, and the web UI read them uniformly.

use serde_json::{json, Value};

/// Whether `prog` is the `grok` binary (`grok` or `/…/grok` or `grok.exe`).
pub fn is_grok(prog: &str) -> bool {
    let name = std::path::Path::new(prog)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or(prog);
    name.eq_ignore_ascii_case("grok")
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
        assert_eq!(translate_line(r#"{"type":"text","data":"HE"}"#, &mut buf), None);
        assert_eq!(translate_line(r#"{"type":"text","data":"LLO"}"#, &mut buf), None);
        assert_eq!(
            assistant_line(&buf).as_deref(),
            Some(r#"{"message":{"content":[{"text":"HELLO","type":"text"}],"role":"assistant"},"type":"assistant"}"#)
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
}
