//! Test-only stand-in for a seat's provider (S89-input; 0eed7 SPEC-seatinput-tests §1). It acts
//! like `claude -p --input-format stream-json` as measured on claude 2.1.326:
//! - reads stdin a line at a time; a line that is not JSON ends the process with rc 1 and
//!   `Error parsing streaming input line` on stderr (the real provider dies the same way);
//! - a user message prints an init, an assistant echo of its text and a success result;
//! - stdin at its end ends the process with rc 0, unless it was started with `--hold-open`:
//!   then it stays up (at most [`STALL_FOR`]) until it is stopped. A session aibd starts for a
//!   terminal has no stdin (null), so without it the fake is gone in milliseconds and a test that
//!   wants it `running` races its exit (f1fae NOTE-seat-input-runner-image 11-19: the newer
//!   macOS runner image loses that race every time);
//! - the text `stall` makes it stop reading for good (a provider that hangs), so a test can fill
//!   the pipe;
//! - the text `mute` prints the init and nothing else (c44: the model never answers); its turn
//!   stays open like `hold`'s.
//! - the text `hold` prints the init and the echo but no result: its turn stays open until the
//!   next line, whose result ends it (N-workspace: a test can keep a seat in a turn).
//!
//! Every line read is also appended to `fake-provider.log` in the current folder as
//! `<pid> <hex of the raw line>`, so a test can count "one write, one line" and see that two
//! writes reached one process. The arguments (the pipe argv) are otherwise ignored; they are
//! appended to `fake-provider-args.log` as `<pid> <JSON list>` (N-revive: a test sees
//! `--resume <id>` after aibd restarted).
//!
//! S89-live-2: started with `--version` it prints `codex-cli 0.167.0`; started with `app-server`
//! as its first argument it acts like `codex app-server` as measured on 0.167.0 (r2): answers
//! `initialize`, answers `thread/start` with a thread id (`th-fake-<pid>`, and records the
//! developerInstructions it got as `persona` on that line of the log), and for each
//! `turn/start` prints an agentMessage item with the turn's text and `turn/completed` (the
//! text `hold`: no turn/completed). The text `ask` makes it send aibd a request first (an
//! approval) and log aibd's answer. A turn/start before the thread exists ends it with rc 3.
//!
//! 08de5 11-25 (3e53b REVIEW-copilot e3d07aff L1): copied under the file name `copilot` it acts
//! like copilot 1.0.115 for aibd's checks only: `--version` prints `GitHub Copilot CLI 1.0.115`,
//! `mcp list --json` an empty list, and every start appends to `fake-copilot-env.log` in the
//! current folder one JSON line: its args, the `COPILOT_HOME` it was given and the names in that
//! folder (a test sees which home each probe and turn ran with). A turn reads stdin and ends.
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

fn codex(pid: u32, log: &mut Option<std::fs::File>) -> ! {
    let mut out = std::io::stdout();
    let mut input = std::io::stdin().lock();
    let mut line = Vec::new();
    let mut thread: Option<String> = None;
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
        let Ok(v) = serde_json::from_slice::<serde_json::Value>(&line) else {
            std::process::exit(1);
        };
        let id = v["id"].clone();
        let say = |out: &mut std::io::Stdout, m: serde_json::Value| {
            let _ = writeln!(out, "{m}");
            let _ = out.flush();
        };
        match v["method"].as_str() {
            Some("initialize") => say(
                &mut out,
                serde_json::json!({"id": id, "result": {"userAgent": "fake"}}),
            ),
            Some("thread/start") => {
                let t = format!("th-fake-{pid}");
                thread = Some(t.clone());
                say(
                    &mut out,
                    serde_json::json!({"id": id, "result": {"thread": {"id": t}}}),
                );
            }
            Some("turn/start") => {
                if thread.as_deref() != v["params"]["threadId"].as_str() {
                    std::process::exit(3);
                }
                let text: String = v["params"]["input"]
                    .as_array()
                    .map(|a| {
                        a.iter()
                            .filter_map(|i| i["text"].as_str())
                            .collect::<Vec<_>>()
                            .join("\n")
                    })
                    .unwrap_or_default();
                say(
                    &mut out,
                    serde_json::json!({"id": id, "result": {"turn": {"id": "tu"}}}),
                );
                if text == "mute" {
                    // c44: codex took the text and its model says nothing (0eed7 at 47788:
                    // turn_aborted after 3 minutes, no token): only codex's own lines
                    say(
                        &mut out,
                        serde_json::json!({"method": "turn/started", "params": {"turn": {"id": "tu"}}}),
                    );
                    say(
                        &mut out,
                        serde_json::json!({"method": "item/started",
                        "params": {"item": {"type": "userMessage", "id": "it"}}}),
                    );
                    continue;
                }
                if text == "ask" {
                    say(
                        &mut out,
                        serde_json::json!({"id": 900, "method": "item/commandExecution/requestApproval", "params": {}}),
                    );
                }
                say(
                    &mut out,
                    serde_json::json!({"method": "item/completed",
                    "params": {"item": {"type": "agentMessage", "text": text}}}),
                );
                if text != "hold" {
                    say(
                        &mut out,
                        serde_json::json!({"method": "turn/completed", "params": {}}),
                    );
                }
            }
            _ => {}
        }
    }
}

/// How long a "stall" text keeps the fake from reading before it exits.
const STALL_FOR: std::time::Duration = std::time::Duration::from_secs(90);
/// §20 試用預覽版 tests: run as the preview build (AIBD_PREVIEW=1) it writes what it was given
/// (its environment and open descriptors, 5d33a M1 / L2) to `<AIBD_DATA_DIR>/seen.json`, then its
/// port to `run/aibd.json` as aibd does, and waits to be stopped (at most 120 s).
fn preview_fake() {
    let Some(data) = std::env::var_os("AIBD_DATA_DIR") else {
        std::process::exit(3);
    };
    let data = std::path::PathBuf::from(data);
    let env: std::collections::BTreeMap<String, String> = std::env::vars().collect();
    let fds: Vec<String> = std::fs::read_dir("/dev/fd")
        .map(|d| {
            d.filter_map(|e| e.ok())
                .map(|e| e.file_name().to_string_lossy().into_owned())
                .collect()
        })
        .unwrap_or_default();
    let seen = serde_json::json!({"env": env, "fds": fds});
    let _ = std::fs::write(data.join("seen.json"), seen.to_string());
    let _ = std::fs::create_dir_all(data.join("run"));
    let _ = std::fs::write(
        data.join("run").join("aibd.json"),
        serde_json::json!({"port": 45678, "pid": std::process::id()}).to_string(),
    );
    // what the parent hands down stdin (preview passes), one line each, to passes.txt
    let passes = data.join("passes.txt");
    std::thread::spawn(move || {
        use std::io::{BufRead, Write};
        for line in std::io::stdin().lock().lines().map_while(Result::ok) {
            if let Ok(mut f) = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&passes)
            {
                let _ = writeln!(f, "{line}");
            }
        }
    });
    let until = std::time::Instant::now() + std::time::Duration::from_secs(120);
    while let Some(left) = until.checked_duration_since(std::time::Instant::now()) {
        // ADR 0005 named exception: a test fake, not aibd; nothing else runs here
        #[allow(clippy::disallowed_methods)]
        std::thread::park_timeout(left);
    }
}

/// Stay up until stopped (at most [`STALL_FOR`]), then end with rc 0.
fn hold_then_exit() -> ! {
    let until = std::time::Instant::now() + STALL_FOR;
    while let Some(left) = until.checked_duration_since(std::time::Instant::now()) {
        // ADR 0005 named exception: a test fake, not aibd; nothing else runs here
        #[allow(clippy::disallowed_methods)]
        std::thread::park_timeout(left);
    }
    std::process::exit(0);
}

/// The `copilot` stand-in (see the file's head).
fn copilot(args: &[String]) {
    let home = std::env::var("COPILOT_HOME").ok();
    let names: Vec<String> = home
        .as_deref()
        .and_then(|h| std::fs::read_dir(h).ok())
        .map(|d| {
            let mut v: Vec<String> = d
                .filter_map(|e| e.ok())
                .map(|e| e.file_name().to_string_lossy().into_owned())
                .collect();
            v.sort();
            v
        })
        .unwrap_or_default();
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open("fake-copilot-env.log")
    {
        let line = serde_json::json!({"args": args, "COPILOT_HOME": home, "files": names});
        let _ = writeln!(f, "{line}");
    }
    if args.first().map(String::as_str) == Some("--version") {
        println!("GitHub Copilot CLI 1.0.115");
        return;
    }
    if args.ends_with(&["mcp".to_owned(), "list".to_owned(), "--json".to_owned()]) {
        println!(r#"{{"mcpServers":{{}}}}"#);
        return;
    }
    let mut sink = Vec::new();
    let _ = std::io::Read::read_to_end(&mut std::io::stdin(), &mut sink);
}

/// Called as `grok` (08de5 11-25, 3e53b REVIEW-grok 4733597f N1): a grok of a version aibd did
/// not measure (1.0.71). It writes each call's args to `fake-grok-calls.log` in its folder and
/// does nothing else, so a seat started on it shows as a line other than `--version`.
fn grok(args: &[String]) {
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open("fake-grok-calls.log")
    {
        let _ = writeln!(f, "{}", serde_json::json!(args));
    }
    if args.first().map(String::as_str) == Some("--version") {
        println!("grok 1.0.71 (test)");
    }
}

fn main() {
    if std::env::var("AIBD_PREVIEW").as_deref() == Ok("1") {
        preview_fake();
        return;
    }
    let pid = std::process::id();
    let args: Vec<String> = std::env::args().skip(1).collect();
    let me = std::env::args().next().unwrap_or_default();
    if std::path::Path::new(&me)
        .file_stem()
        .is_some_and(|n| n == "copilot")
    {
        copilot(&args);
        return;
    }
    if std::path::Path::new(&me)
        .file_stem()
        .is_some_and(|n| n == "grok")
    {
        grok(&args);
        return;
    }
    if args.first().map(String::as_str) == Some("--version") {
        println!("codex-cli 0.167.0");
        return;
    }
    // N-codex-user-mcp: `codex <flags> mcp list --json` before a codex seat starts (not in the
    // args log, which holds the seat's start): one of the user's that is on, one off, and aibd
    // when its -c is among the flags (codex merges it in, as the real one does)
    if args.ends_with(&["mcp".to_owned(), "list".to_owned(), "--json".to_owned()]) {
        let ours = if args.iter().any(|a| a.starts_with("mcp_servers.aibd.")) {
            r#"{"name":"aibd","enabled":true},"#
        } else {
            ""
        };
        println!(
            r#"[{ours}{{"name":"userx","enabled":true}},{{"name":"node_repl","enabled":false}}]"#
        );
        return;
    }
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open("fake-provider-args.log")
    {
        let _ = writeln!(f, "{pid} {}", serde_json::json!(args));
    }
    let mut log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open("fake-provider.log")
        .ok();
    if args.first().map(String::as_str) == Some("app-server") {
        codex(pid, &mut log);
    }
    let hold_open = args.iter().any(|a| a == "--hold-open");
    let stdin = std::io::stdin();
    let mut out = std::io::stdout();
    let mut line = Vec::new();
    let mut input = stdin.lock();
    loop {
        line.clear();
        match input.read_until(b'\n', &mut line) {
            Ok(0) | Err(_) if hold_open => hold_then_exit(),
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
        if text == "stall" {
            // stops reading, then goes after STALL_FOR: a write aibd never bounds (knife SI12)
            // would otherwise hold the test process past its failure for good (#860 macOS:
            // knives-04 stuck 2 h). A passing test stops the seat long before.
            hold_then_exit();
        }
        let ends = text != "hold";
        // N-workspace usage: `usage <n>` makes the assistant report n input tokens (the context
        // a real provider would report); any other text reports 1000
        let tokens: u64 = text
            .strip_prefix("usage ")
            .and_then(|n| n.trim().parse().ok())
            .unwrap_or(1000);
        for msg in [
            serde_json::json!({"type": "system", "subtype": "init", "pid": pid,
                "model": "claude-fake"}),
            serde_json::json!({"type": "assistant", "message": {"role": "assistant",
                "model": "claude-fake", "content": [{"type": "text", "text": text}],
                "usage": {"input_tokens": tokens, "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0, "output_tokens": 5}},
                "len": text.len()}),
            serde_json::json!({"type": "result", "subtype": "success"}),
        ]
        .iter()
        // c44: `mute` prints the init only (a provider that took the text, no word from the model)
        .take(match text.as_str() {
            "mute" => 1,
            _ if ends => 3,
            _ => 2,
        }) {
            let _ = writeln!(out, "{msg}");
        }
        let _ = out.flush();
    }
}
