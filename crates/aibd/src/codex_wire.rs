//! S89-live-2 (b4297, DESIGN-s89live2-codex-pipe-b4297-1118 r2/r3): aibd speaking
//! `codex app-server` (stdio JSON-RPC, one line a message) for a codex seat.
//!
//! Everything above the pipe stays as it is for claude: seat_input, seat_send, catch-up and
//! redeliver build one stream-json user line; this module turns that line into codex's
//! `turn/start` at the moment it is written. At start aibd writes the three opening lines
//! (`initialize`, `initialized`, `thread/start` with the persona card as
//! `developerInstructions` and the seat's folder as `cwd`); codex answers `thread/start` with
//! the thread's id, and a text is written only once that id is known. A turn ends on
//! `turn/completed`. Codex asking aibd anything (an approval, a question: a JSON-RPC request
//! from the server) is answered with an error: nobody is there to answer, and a seat with
//! `approval_policy="never"` should never ask (r3).
//!
//! The thread id lives in memory with the session; a new session starts a new thread.

use serde_json::{json, Value};
use std::sync::OnceLock;

/// The id of aibd's `initialize` request.
const INIT_ID: u64 = 1;
/// The id of aibd's `thread/start` request; its answer carries the thread id.
const THREAD_ID: u64 = 2;
/// A15 (DESIGN-a15-quota-08de5-1123 r1): the id of aibd's `account/rateLimits/read`, sent
/// last in the opening; its answer is read by `crate::usage` (whitelisted fields only).
const QUOTA_ID: u64 = 3;
/// `turn/start` ids count up from here (only told apart in logs).
const FIRST_TURN_ID: u64 = 100;

/// One codex session's state on aibd's side.
#[derive(Default)]
pub struct Codex {
    thread: OnceLock<String>,
    turns: std::sync::atomic::AtomicU64,
}

/// What one stdout line of codex means to aibd.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Heard {
    /// The thread was started now: texts can be written.
    pub thread_started: bool,
    /// The turn under way ended.
    pub turn_ended: bool,
    /// A line to write back at once (the refusal of a request codex made).
    pub reply: Option<Vec<u8>>,
}

fn line(v: &Value) -> Vec<u8> {
    let mut b = v.to_string().into_bytes();
    b.push(b'\n');
    b
}

/// The opening lines (A15: four; the last asks the account's limits). `persona` is the card's text (none: no developer instructions).
pub fn opening(cwd: &str, persona: Option<&str>) -> Vec<u8> {
    let mut params = json!({"cwd": cwd});
    if let Some(p) = persona.filter(|p| !p.is_empty()) {
        params["developerInstructions"] = json!(p);
    }
    let mut out = line(
        &json!({"jsonrpc": "2.0", "id": INIT_ID, "method": "initialize",
        "params": {"clientInfo": {"name": "aibd", "version": env!("CARGO_PKG_VERSION")}}}),
    );
    out.extend(line(&json!({"jsonrpc": "2.0", "method": "initialized"})));
    out.extend(line(
        &json!({"jsonrpc": "2.0", "id": THREAD_ID, "method": "thread/start",
        "params": params}),
    ));
    out.extend(line(
        &json!({"jsonrpc": "2.0", "id": QUOTA_ID, "method": "account/rateLimits/read"}),
    ));
    out
}

/// The text blocks of one stream-json user line, in order.
fn texts(user_line: &[u8]) -> Option<Vec<String>> {
    let v: Value = serde_json::from_slice(user_line).ok()?;
    match v.get("message")?.get("content")? {
        Value::String(s) => Some(vec![s.clone()]),
        Value::Array(a) => Some(
            a.iter()
                .filter(|b| b.get("type").and_then(Value::as_str) == Some("text"))
                .filter_map(|b| b.get("text").and_then(Value::as_str).map(str::to_owned))
                .collect(),
        ),
        _ => None,
    }
}

impl Codex {
    pub fn thread(&self) -> Option<&str> {
        self.thread.get().map(String::as_str)
    }

    /// `user_line` (one stream-json user message) as codex's `turn/start` on this thread;
    /// `None` while the thread has not started or when the line is not a user message.
    pub fn turn_line(&self, user_line: &[u8]) -> Option<Vec<u8>> {
        let thread = self.thread.get()?;
        let input: Vec<Value> = texts(user_line)?
            .into_iter()
            .map(|t| json!({"type": "text", "text": t}))
            .collect();
        if input.is_empty() {
            return None;
        }
        let id = FIRST_TURN_ID
            + self
                .turns
                .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        Some(line(
            &json!({"jsonrpc": "2.0", "id": id, "method": "turn/start",
            "params": {"threadId": thread, "input": input}}),
        ))
    }

    /// What a stdout line says. A line cut at the reader's cap is not JSON: it says nothing
    /// (codex's long lines are deltas and items, never the end of a turn).
    pub fn heard(&self, l: &[u8], cut: bool) -> Heard {
        let mut h = Heard::default();
        if cut {
            return h;
        }
        let Ok(v) = serde_json::from_slice::<Value>(l) else {
            return h;
        };
        let method = v.get("method").and_then(Value::as_str);
        match (v.get("id"), method) {
            // a request of the server's: nobody answers it but aibd, and aibd says no
            (Some(id), Some(_)) => {
                h.reply = Some(line(&json!({"jsonrpc": "2.0", "id": id,
                    "error": {"code": -32601, "message": "refused by aibd: a seat is not asked"}})));
            }
            (Some(id), None) if id.as_u64() == Some(THREAD_ID) => {
                if let Some(t) = v.pointer("/result/thread/id").and_then(Value::as_str) {
                    h.thread_started = self.thread.set(t.to_owned()).is_ok();
                }
            }
            (None, Some("turn/completed")) => h.turn_ended = true,
            _ => {}
        }
        h
    }
}

/// Whether `argv0` (the resolved program) is codex.
pub fn is_codex(argv0: &str) -> bool {
    // both separators: a Windows path read on any machine (memory: rust-path-both-separators)
    let name = argv0.rsplit(['/', '\\']).next().unwrap_or(argv0);
    let stem = name
        .strip_suffix(".exe")
        .or_else(|| name.strip_suffix(".EXE"))
        .unwrap_or(name);
    stem.eq_ignore_ascii_case("codex")
}

/// N-codex-user-mcp (7ebca 11-20): the `-c` flags that turn off every MCP server a codex seat
/// would load but aibd's own. `listed` is what `codex <the seat's flags> mcp list --json`
/// printed: codex's own reading of the user's settings, plugins and the seat's flags (a `-c`
/// merges into the user's `mcp_servers`, it cannot replace the table, so each one is named).
/// A list that is not codex's array, or a server whose name a `-c` key cannot spell (codex
/// splits the key on every `.`, quotes or not), is an error: the seat does not start.
/// `listed` is read without aibd's own `-c` ([`without_ours`]): a server already named `aibd`
/// there is the user's or the folder's, and ours would only be merged into it (its `env`,
/// `cwd`, `url` kept: a program run in aibd's MCP process outside the sandbox, 5d33a
/// PREREAD-codexmcp M1), so the seat does not start either.
pub fn mcp_off(listed: &str) -> Result<Vec<String>, &'static str> {
    let v: Value = serde_json::from_str(listed).map_err(|_| "codex_mcp_unlisted")?;
    let rows = v.as_array().ok_or("codex_mcp_unlisted")?;
    let mut off = Vec::new();
    for r in rows {
        let name = r["name"].as_str().ok_or("codex_mcp_unlisted")?;
        if name == "aibd" {
            return Err("codex_mcp_aibd_taken");
        }
        // only an explicit false is off: a row without the field is treated as on
        if r["enabled"] == Value::Bool(false) {
            continue;
        }
        let spelt = !name.is_empty()
            && name
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_');
        if !spelt {
            return Err("codex_mcp_unnamed");
        }
        off.push("-c".to_owned());
        off.push(format!("mcp_servers.{name}.enabled=false"));
    }
    Ok(off)
}

/// A `.codex` folder codex would load as project settings for a seat in `cwd`: in `cwd` or
/// any folder above it up to the git root (the folder holding `.git`; `cwd` alone when there is
/// none), any kind (a file or link is refused too), leaving out `codex_home` (the user's own
/// settings, not a project's). 7ebca MEASURE-codex-folder-config r2: a codex work seat's
/// folder is always trusted (codex writes it so itself), and a trusted project's
/// `.codex/config.toml` is merged under aibd's `-c` (`permissions.seat.filesystem`,
/// `shell_environment_policy.set`, `features` are tables: a project adds a writable root or
/// turns a tool on), so the seat does not start, as a claude seat with a `.claude` does not.
pub fn folder_settings(
    cwd: &std::path::Path,
    codex_home: Option<&std::path::Path>,
) -> Option<std::path::PathBuf> {
    let home = codex_home.and_then(|h| std::fs::canonicalize(h).ok());
    let has = |d: &std::path::Path, n: &str| std::fs::symlink_metadata(d.join(n)).is_ok();
    let top = cwd.ancestors().find(|d| has(d, ".git")).unwrap_or(cwd);
    for d in cwd.ancestors() {
        let c = d.join(".codex");
        let theirs = home.is_some() && std::fs::canonicalize(&c).ok() == home;
        if has(d, ".codex") && !theirs {
            return Some(c);
        }
        if d == top {
            break;
        }
    }
    None
}

/// The seat's flags without aibd's own server (`-c mcp_servers.aibd.<key>=…` pairs), so a list
/// read with them shows only what the user's settings and the seat's folder name.
pub fn without_ours(flags: &[String]) -> Vec<String> {
    let mut out = Vec::new();
    let mut i = 0;
    while i < flags.len() {
        if flags[i] == "-c"
            && flags
                .get(i + 1)
                .is_some_and(|v| v.starts_with("mcp_servers.aibd."))
        {
            i += 2;
            continue;
        }
        out.push(flags[i].clone());
        i += 1;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// MEASURE-codex-folder-config r2: a `.codex` in the seat's folder or above it inside the
    /// repo is found (any kind); the user's own settings folder and folders outside the repo
    /// are not.
    #[test]
    fn a_codex_folder_in_the_seats_repo_is_found() {
        let d = tempfile::tempdir().unwrap();
        let repo = d.path().join("repo");
        let sub = repo.join("a").join("b");
        std::fs::create_dir_all(repo.join(".git")).unwrap();
        std::fs::create_dir_all(&sub).unwrap();
        assert_eq!(folder_settings(&sub, None), None);
        assert_eq!(folder_settings(&repo, None), None);
        std::fs::create_dir_all(repo.join("a").join(".codex")).unwrap();
        assert!(
            folder_settings(&sub, None).is_some(),
            "reason: parent_codex_missed"
        );
        std::fs::remove_dir_all(repo.join("a").join(".codex")).unwrap();
        std::fs::write(repo.join(".codex"), "x").unwrap();
        assert!(
            folder_settings(&sub, None).is_some(),
            "reason: file_codex_missed"
        );
        assert!(
            folder_settings(&repo, None).is_some(),
            "reason: top_codex_missed"
        );
        // above the repo is not the project's; the user's own settings folder is theirs
        std::fs::remove_file(repo.join(".codex")).unwrap();
        std::fs::create_dir_all(d.path().join(".codex")).unwrap();
        assert_eq!(
            folder_settings(&sub, None),
            None,
            "reason: outside_repo_taken"
        );
        let plain = d.path().join("plain");
        std::fs::create_dir_all(plain.join(".codex")).unwrap();
        assert!(
            folder_settings(&plain, None).is_some(),
            "reason: no_repo_cwd_missed"
        );
        assert_eq!(
            folder_settings(&plain, Some(&plain.join(".codex"))),
            None,
            "reason: codex_home_refused"
        );
    }

    /// The list is read without aibd's own `-c` pairs, every other flag kept in order.
    #[test]
    fn the_list_is_read_without_aibds_own_server() {
        let f: Vec<String> = [
            "app-server",
            "-c",
            "mcp_servers.aibd.command=\"x\"",
            "-c",
            "sandbox_mode=\"read-only\"",
            "-c",
            "mcp_servers.aibd.args=[]",
            "--x",
        ]
        .iter()
        .map(|s| (*s).to_owned())
        .collect();
        assert_eq!(
            without_ours(&f),
            ["app-server", "-c", "sandbox_mode=\"read-only\"", "--x"],
            "reason: ours_listed"
        );
    }

    /// N-codex-user-mcp: a server of the user's own settings that is on is turned off by
    /// name; aibd's own and the ones already off are left; a list codex did not print, or a
    /// name `-c` cannot spell, starts nothing.
    #[test]
    fn a_codex_seat_turns_off_the_users_own_mcp_servers() {
        let listed = r#"[{"name":"user-x_1","enabled":true},
            {"name":"node_repl","enabled":false},{"name":"no_field"}]"#;
        assert_eq!(
            mcp_off(listed).unwrap(),
            [
                "-c",
                "mcp_servers.user-x_1.enabled=false",
                "-c",
                "mcp_servers.no_field.enabled=false"
            ],
            "reason: user_mcp_left_on"
        );
        // 5d33a M1: a server of the user's (or the folder's) named aibd is not ours
        for row in [
            r#"{"name":"aibd","enabled":true}"#,
            r#"{"name":"aibd","enabled":false}"#,
        ] {
            assert_eq!(
                mcp_off(&format!("[{row}]")).unwrap_err(),
                "codex_mcp_aibd_taken",
                "reason: users_aibd_merged {row}"
            );
        }
        assert_eq!(mcp_off("[]").unwrap(), Vec::<String>::new());
        assert_eq!(mcp_off("").unwrap_err(), "codex_mcp_unlisted");
        assert_eq!(
            mcp_off(r#"{"name":"x"}"#).unwrap_err(),
            "codex_mcp_unlisted"
        );
        assert_eq!(
            mcp_off(r#"[{"enabled":true}]"#).unwrap_err(),
            "codex_mcp_unlisted"
        );
        for bad in ["we.ird", "a b", "x=y", "", "名"] {
            let l = serde_json::json!([{"name": bad, "enabled": true}]).to_string();
            assert_eq!(
                mcp_off(&l).unwrap_err(),
                "codex_mcp_unnamed",
                "reason: unspelt {bad}"
            );
        }
    }

    fn parse(b: &[u8]) -> Vec<Value> {
        b.split(|c| *c == b'\n')
            .filter(|l| !l.is_empty())
            .map(|l| serde_json::from_slice(l).unwrap())
            .collect()
    }

    #[test]
    fn the_opening_carries_the_persona_in_the_protocol() {
        let o = parse(&opening("/w/seat", Some("你是審查席")));
        assert_eq!(o.len(), 4);
        // A15: the account's limits are asked last, once the thread is on its way
        assert_eq!(o[3]["method"], "account/rateLimits/read");
        assert_eq!(o[0]["method"], "initialize");
        assert_eq!(o[1]["method"], "initialized");
        assert!(o[1].get("id").is_none());
        assert_eq!(o[2]["method"], "thread/start");
        assert_eq!(o[2]["id"], THREAD_ID);
        assert_eq!(o[2]["params"]["cwd"], "/w/seat");
        assert_eq!(o[2]["params"]["developerInstructions"], "你是審查席");
        let bare = parse(&opening("/w", None));
        assert!(bare[2]["params"].get("developerInstructions").is_none());
    }

    #[test]
    fn a_text_waits_for_the_thread_then_goes_as_turn_start() {
        let c = Codex::default();
        let u = r#"{"type":"user","message":{"role":"user","content":[{"type":"text","text":"補看"},{"type":"text","text":"你好"}]}}"#.as_bytes();
        assert!(c.turn_line(u).is_none(), "reason: written_before_thread");
        let h = c.heard(br#"{"id":2,"result":{"thread":{"id":"th-1"}}}"#, false);
        assert!(h.thread_started);
        assert_eq!(c.thread(), Some("th-1"));
        // a second answer does not move it
        assert!(
            !c.heard(br#"{"id":2,"result":{"thread":{"id":"th-2"}}}"#, false)
                .thread_started
        );
        let t = parse(&c.turn_line(u).unwrap());
        assert_eq!(t[0]["method"], "turn/start");
        assert_eq!(t[0]["params"]["threadId"], "th-1");
        assert_eq!(t[0]["params"]["input"][0]["text"], "補看");
        assert_eq!(t[0]["params"]["input"][1]["text"], "你好");
        let s = br#"{"type":"user","message":{"role":"user","content":"x"}}"#;
        assert_eq!(
            parse(&c.turn_line(s).unwrap())[0]["params"]["input"][0]["text"],
            "x"
        );
        assert!(c.turn_line(b"not json").is_none());
    }

    #[test]
    fn turn_completed_ends_a_turn_and_requests_are_refused() {
        let c = Codex::default();
        assert!(
            c.heard(br#"{"method":"turn/completed","params":{}}"#, false)
                .turn_ended
        );
        assert!(
            !c.heard(br#"{"method":"item/completed","params":{}}"#, false)
                .turn_ended
        );
        assert!(!c.heard(br#"{"method":"turn/completed""#, true).turn_ended);
        let h = c.heard(
            br#"{"id":7,"method":"item/commandExecution/requestApproval","params":{}}"#,
            false,
        );
        let r = parse(&h.reply.expect("reason: request_not_refused"));
        assert_eq!(r[0]["id"], 7);
        assert!(r[0]["error"]["code"].is_i64());
        assert!(r[0].get("result").is_none());
        assert!(c.heard(br#"{"id":100,"result":{}}"#, false).reply.is_none());
    }

    #[test]
    fn codex_is_told_by_its_program_name() {
        assert!(is_codex("/Users/x/.local/bin/codex"));
        assert!(is_codex(r"C:\Users\x\bin\codex.exe"));
        assert!(!is_codex("/usr/bin/claude"));
        assert!(!is_codex("/opt/codex-tools/run"));
    }
}
