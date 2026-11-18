//! Test-only stand-in for a seat's provider (S89-input; 0eed7 SPEC-seatinput-tests §1). It acts
//! like `claude -p --input-format stream-json` as measured on claude 2.1.326:
//! - reads stdin a line at a time; a line that is not JSON ends the process with rc 1 and
//!   `Error parsing streaming input line` on stderr (the real provider dies the same way);
//! - a user message prints an init, an assistant echo of its text and a success result;
//! - stdin at its end ends the process with rc 0.
//!
//! Every line read is also appended to `fake-provider.log` in the current folder as
//! `<pid> <hex of the raw line>`, so a test can count "one write, one line" and see that two
//! writes reached one process. The arguments (the pipe argv) are ignored.
//!
//! Never shipped: the release copies only `aibd` (aibd-release.yml).

use std::io::{BufRead, Write};

fn text_of(v: &serde_json::Value) -> Option<String> {
    let c = &v["message"]["content"];
    if let Some(s) = c.as_str() {
        return Some(s.to_owned());
    }
    let parts: Vec<&str> = c
        .as_array()?
        .iter()
        .filter_map(|p| p["text"].as_str())
        .collect();
    Some(parts.concat())
}

fn main() {
    let pid = std::process::id();
    let mut log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open("fake-provider.log")
        .ok();
    let stdin = std::io::stdin();
    let mut out = std::io::stdout();
    let mut line = Vec::new();
    let mut input = stdin.lock();
    loop {
        line.clear();
        match input.read_until(b'\n', &mut line) {
            Ok(0) | Err(_) => std::process::exit(0),
            Ok(_) => {}
        }
        if let Some(f) = log.as_mut() {
            let hex: String = line.iter().map(|b| format!("{b:02x}")).collect();
            let _ = writeln!(f, "{pid} {hex}");
        }
        let v: serde_json::Value = match serde_json::from_slice(&line) {
            Ok(v) => v,
            Err(e) => {
                eprintln!("Error parsing streaming input line: {e}");
                std::process::exit(1);
            }
        };
        let text = text_of(&v).unwrap_or_default();
        for msg in [
            serde_json::json!({"type": "system", "subtype": "init", "pid": pid}),
            serde_json::json!({"type": "assistant", "message": {"role": "assistant",
                "content": [{"type": "text", "text": text}]}, "len": text.len()}),
            serde_json::json!({"type": "result", "subtype": "success"}),
        ] {
            let _ = writeln!(out, "{msg}");
        }
        let _ = out.flush();
    }
}
