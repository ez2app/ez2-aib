//! What a work seat does, journaled (W1 condition 6, 5d33a CHECK-preview-work-seat and
//! PREREAD-worktierpv2 M1; 519d0 QUEUE-after-demo #1): every file tool the seat uses gives
//! `seat.file_changed` (the path in its folder, sha256 first 12 hex before and after), every
//! command gives `seat.command_ran` (the command, whether it failed, seconds). Both name the
//! seat and, when its turn came from a conversation, that conversation.
//!
//! Read from the seat's own stream-json output (aibd does not run the tools): the "before"
//! hash is taken when the `tool_use` line is read, which can already be after a fast tool
//! ran; "failed" is claude's `is_error` (a command's exit code is not in the stream). So this
//! is a record for checking a demo against its folder, not a defense.

use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::Instant;

/// The tools that change a file, and the input key naming it.
const FILE_TOOLS: [(&str, &str); 4] = [
    ("Edit", "file_path"),
    ("Write", "file_path"),
    ("MultiEdit", "file_path"),
    ("NotebookEdit", "notebook_path"),
];

/// One tool call waiting for its result.
#[derive(Debug, Clone)]
struct Open {
    seat: String,
    tool: String,
    cwd: PathBuf,
    path: Option<PathBuf>,
    command: Option<String>,
    before: Option<String>,
    at: Instant,
}

/// The longest command kept in a record, in characters (0eed7 VERIFYPLAN E5: long ones cut).
pub const COMMAND_MAX: usize = 300;

/// A run of key-like characters this long or longer is masked (E5: no token / key shapes).
pub const SECRET_RUN: usize = 24;

/// What a record may say (0eed7 VERIFYPLAN E5): the home folder written as `~`, a long
/// key-like run (letters, digits, `_ - + =`) masked, at most [`COMMAND_MAX`] characters.
pub fn scrub(text: &str, home: Option<&Path>) -> String {
    let mut t = text.to_owned();
    if let Some(h) = home.and_then(Path::to_str).filter(|h| h.len() > 1) {
        t = t.replace(h, "~");
    }
    let mut out = String::with_capacity(t.len());
    let mut run = String::new();
    let flush = |run: &mut String, out: &mut String| {
        if run.chars().count() >= SECRET_RUN {
            out.push_str(&format!("<masked {}>", run.chars().count()));
        } else {
            out.push_str(run);
        }
        run.clear();
    };
    for c in t.chars() {
        if c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '+' | '=') {
            run.push(c);
        } else {
            flush(&mut run, &mut out);
            out.push(c);
        }
    }
    flush(&mut run, &mut out);
    if out.chars().count() > COMMAND_MAX {
        out = out.chars().take(COMMAND_MAX).collect::<String>() + "…";
    }
    out
}

/// A file's place as a record says it: relative to the seat's folder when inside it, else
/// scrubbed.
fn shown_path(p: &Path, cwd: &Path, home: Option<&Path>) -> String {
    match p.strip_prefix(cwd) {
        Ok(rel) => rel.to_string_lossy().replace('\\', "/"),
        Err(_) => scrub(&p.to_string_lossy(), home),
    }
}

fn seats() -> std::sync::MutexGuard<'static, HashMap<String, PathBuf>> {
    static S: OnceLock<Mutex<HashMap<String, PathBuf>>> = OnceLock::new();
    S.get_or_init(|| Mutex::new(HashMap::new()))
        .lock()
        .unwrap_or_else(|e| e.into_inner())
}

fn open_calls() -> std::sync::MutexGuard<'static, HashMap<String, Open>> {
    static O: OnceLock<Mutex<HashMap<String, Open>>> = OnceLock::new();
    O.get_or_init(|| Mutex::new(HashMap::new()))
        .lock()
        .unwrap_or_else(|e| e.into_inner())
}

fn channel() -> &'static OnceLock<tokio::sync::mpsc::UnboundedSender<(String, Value)>> {
    static TX: OnceLock<tokio::sync::mpsc::UnboundedSender<(String, Value)>> = OnceLock::new();
    &TX
}

/// A work seat's session starts in `cwd`: its output is watched from now on.
pub fn watch(seat: &str, cwd: &Path) {
    seats().insert(seat.to_owned(), cwd.to_path_buf());
}

/// The journal writer (once, at aibd start).
pub fn start(st: crate::api::AppState) {
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<(String, Value)>();
    if channel().set(tx).is_err() {
        return;
    }
    tokio::spawn(async move {
        while let Some((kind, payload)) = rx.recv().await {
            if !crate::seat_send::journal_event(&st, &kind, payload).await {
                tracing::warn!(event = %kind, "work seat record not written");
            }
        }
    });
}

/// What one output line says about tools: calls started, calls answered.
#[derive(Debug, Clone, PartialEq)]
pub enum Seen {
    Use {
        id: String,
        name: String,
        input: Value,
    },
    Done {
        id: String,
        failed: bool,
    },
}

/// The tool calls and results in one stream-json line (claude's `assistant` / `user`
/// messages). Lines without either are not parsed.
pub fn seen(line: &[u8]) -> Vec<Seen> {
    let mentions = |w: &[u8]| line.windows(w.len()).any(|x| x == w);
    if !mentions(b"tool_use") && !mentions(b"tool_result") {
        return Vec::new();
    }
    let Ok(v) = serde_json::from_slice::<Value>(line) else {
        return Vec::new();
    };
    let Some(blocks) = v["message"]["content"].as_array() else {
        return Vec::new();
    };
    blocks
        .iter()
        .filter_map(|b| match b["type"].as_str()? {
            "tool_use" => Some(Seen::Use {
                id: b["id"].as_str()?.to_owned(),
                name: b["name"].as_str()?.to_owned(),
                input: b["input"].clone(),
            }),
            "tool_result" => Some(Seen::Done {
                id: b["tool_use_id"].as_str()?.to_owned(),
                failed: b["is_error"].as_bool().unwrap_or(false),
            }),
            _ => None,
        })
        .collect()
}

/// sha256 of a file's bytes, first 12 hex; `None` when it is not there (made / removed).
pub fn sha12(p: &Path) -> Option<String> {
    use sha2::Digest;
    let bytes = std::fs::read(p).ok()?;
    let d = sha2::Sha256::digest(&bytes);
    Some(d.iter().take(6).map(|b| format!("{b:02x}")).collect())
}

/// The file a file tool names, resolved against the seat's folder.
fn file_of(name: &str, input: &Value, cwd: &Path) -> Option<PathBuf> {
    let key = FILE_TOOLS.iter().find(|(t, _)| *t == name)?.1;
    let p = PathBuf::from(input[key].as_str()?);
    Some(if p.is_absolute() { p } else { cwd.join(p) })
}

/// The record a finished call gives: `(event type, payload)`; tools that neither change a
/// file nor run a command give none.
fn record(
    o: &Open,
    failed: bool,
    thread: Option<String>,
    home: Option<&Path>,
) -> Option<(String, Value)> {
    let secs = (o.at.elapsed().as_secs_f64() * 1000.0).round() / 1000.0;
    if let Some(p) = &o.path {
        let after = sha12(p);
        return Some((
            "seat.file_changed".into(),
            json!({"seat_id": o.seat, "thread_id": thread, "tool": o.tool,
                   "path": shown_path(p, &o.cwd, home), "before": o.before, "after": after,
                   "failed": failed, "secs": secs}),
        ));
    }
    let command = o.command.as_ref()?;
    Some((
        "seat.command_ran".into(),
        json!({"seat_id": o.seat, "thread_id": thread, "tool": o.tool,
               "command": scrub(command, home), "failed": failed, "secs": secs}),
    ))
}

/// One output line of `seat` (cut lines are skipped: a cut line has no whole JSON).
pub fn heard(seat: &str, line: &[u8], cut: bool) {
    if cut {
        return;
    }
    let Some(cwd) = seats().get(seat).cloned() else {
        return;
    };
    for s in seen(line) {
        match s {
            Seen::Use { id, name, input } => {
                let path = file_of(&name, &input, &cwd);
                let before = path.as_deref().and_then(sha12);
                let command = (name == "Bash")
                    .then(|| input["command"].as_str().map(str::to_owned))
                    .flatten();
                open_calls().insert(
                    id,
                    Open {
                        seat: seat.to_owned(),
                        cwd: cwd.clone(),
                        tool: name,
                        path,
                        command,
                        before,
                        at: Instant::now(),
                    },
                );
            }
            Seen::Done { id, failed } => {
                let Some(o) = open_calls().remove(&id) else {
                    continue;
                };
                let thread = crate::reply_back::thread_of(&o.seat);
                let home = crate::seats::home_dir(|k| std::env::var(k).ok());
                let r = record(&o, failed, thread, home.as_deref());
                if let (Some(r), Some(tx)) = (r, channel().get()) {
                    let _ = tx.send(r);
                }
            }
        }
    }
}

/// W1 #6 (0eed7 VERIFY N2: every commit was "trial"): a work seat commits as itself, the
/// persona's name with the seat's short id, at an address that is never a real mailbox.
pub fn git_identity(seat_id: &str, name: &str) -> Vec<(String, String)> {
    let short = seat_id.strip_prefix("seat_").unwrap_or(seat_id);
    let short = &short[..short.len().min(5)];
    let who = format!("{name} ({short})");
    let mail = format!("{seat_id}@aibd.invalid");
    [
        ("GIT_AUTHOR_NAME", who.clone()),
        ("GIT_AUTHOR_EMAIL", mail.clone()),
        ("GIT_COMMITTER_NAME", who),
        ("GIT_COMMITTER_EMAIL", mail),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_owned(), v))
    .collect()
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    /// A file tool and a command, as claude's stream-json gives them, become one record each;
    /// a read tool gives none.
    #[test]
    fn a_work_seats_file_change_and_command_are_each_one_record() {
        let d = tempfile::tempdir().unwrap();
        let f = d.path().join("clamp.js");
        std::fs::write(&f, "old").unwrap();
        let use_line = json!({"type":"assistant","message":{"content":[
            {"type":"text","text":"x"},
            {"type":"tool_use","id":"t1","name":"Edit","input":{"file_path":"clamp.js"}},
            {"type":"tool_use","id":"t2","name":"Bash","input":{"command":"node --test"}},
            {"type":"tool_use","id":"t3","name":"Read","input":{"file_path":"clamp.js"}}]}})
        .to_string();
        let s = seen(use_line.as_bytes());
        assert_eq!(s.len(), 3, "reason: tool_use_missed {s:?}");
        let mut open = Vec::new();
        for x in &s {
            if let Seen::Use { id, name, input } = x {
                let path = file_of(name, input, d.path());
                open.push((
                    id.clone(),
                    Open {
                        seat: "seat_ab".into(),
                        cwd: d.path().to_path_buf(),
                        tool: name.clone(),
                        before: path.as_deref().and_then(sha12),
                        path,
                        command: (name == "Bash")
                            .then(|| input["command"].as_str().map(str::to_owned))
                            .flatten(),
                        at: Instant::now(),
                    },
                ));
            }
        }
        std::fs::write(&f, "new").unwrap();
        let done = json!({"type":"user","message":{"content":[
            {"type":"tool_result","tool_use_id":"t2","is_error":true,"content":"1 failed"}]}})
        .to_string();
        assert_eq!(
            seen(done.as_bytes()),
            vec![Seen::Done {
                id: "t2".into(),
                failed: true
            }]
        );
        let (k, p) = record(&open[0].1, false, Some("th_1".into()), None).unwrap();
        assert_eq!(k, "seat.file_changed");
        assert_eq!(p["before"], sha12_of(b"old"), "reason: before_hash");
        assert_eq!(p["after"], sha12_of(b"new"), "reason: after_hash");
        assert_eq!(p["thread_id"], "th_1");
        assert_eq!(p["path"], "clamp.js", "reason: path_not_relative");
        let (k, p) = record(&open[1].1, true, None, None).unwrap();
        assert_eq!(k, "seat.command_ran");
        assert_eq!(p["command"], "node --test");
        assert_eq!(p["failed"], true, "reason: failure_lost");
        assert!(
            record(&open[2].1, false, None, None).is_none(),
            "reason: read_recorded"
        );
        assert!(seen(br#"{"type":"result","result":"ok"}"#).is_empty());
    }

    fn sha12_of(b: &[u8]) -> String {
        use sha2::Digest;
        sha2::Sha256::digest(b)
            .iter()
            .take(6)
            .map(|x| format!("{x:02x}"))
            .collect()
    }

    /// 0eed7 VERIFYPLAN E5: no home folder, no key-like run, long commands cut.
    #[test]
    fn a_record_says_no_home_folder_and_no_key_shape() {
        let home = Path::new("/Users/someone");
        let s = scrub(
            "cat /Users/someone/.ssh/id && echo sk-ant-abcdefghijklmnopqrstuvwxyz0123",
            Some(home),
        );
        assert!(!s.contains("/Users/someone"), "reason: home_kept {s}");
        assert!(s.contains("~/.ssh/id"), "{s}");
        assert!(!s.contains("abcdefghijklmnop"), "reason: key_kept {s}");
        assert!(s.contains("<masked "), "{s}");
        assert_eq!(scrub("node --test", Some(home)), "node --test");
        let long = scrub(&"a ".repeat(400), None);
        assert_eq!(long.chars().count(), COMMAND_MAX + 1, "reason: not_cut");
    }

    /// 0eed7 VERIFY N2: two work seats commit under two names.
    #[test]
    fn each_work_seat_commits_as_itself() {
        let a = git_identity("seat_abcdef0123", "作者");
        let b = git_identity("seat_99999aaaa", "審查");
        let get = |v: &[(String, String)], k: &str| {
            v.iter()
                .find(|(x, _)| x == k)
                .map(|(_, y)| y.clone())
                .unwrap()
        };
        assert_eq!(get(&a, "GIT_AUTHOR_NAME"), "作者 (abcde)");
        assert_eq!(
            get(&a, "GIT_COMMITTER_EMAIL"),
            "seat_abcdef0123@aibd.invalid"
        );
        assert_ne!(
            get(&a, "GIT_AUTHOR_NAME"),
            get(&b, "GIT_AUTHOR_NAME"),
            "reason: same_identity"
        );
    }
}
