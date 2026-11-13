//! `aib-dmq take | close | wait | reopen`: the receiving end of the dm queue in Rust
//! (B1 S3b). The bash dispatcher (`aib dm-queue …`, aib/src/cockpit/aib) runs this first
//! when the subcommand is listed in `AIB_DMQ_RUST`, and the frozen Python otherwise.
//!
//! Same flags as dm_queue.py's parsers, same output, same exit codes:
//!
//! * take / close / wait print dm_take.emit: one JSON line without the text, then the
//!   text between 「----- 全文 -----」 and 「----- 完 -----」 when there is one. The line's
//!   separators are Python's; its keys come out sorted (Python keeps insertion order: a
//!   known difference, the values are the same).
//! * reopen prints `_print_json` (sort_keys): byte for byte.
//! * exit 0 when ok, else _TRANSITION_RC by reason, 4 for a reason not in it.
//!
//! Exit 75 means "not handled here, nothing was touched": the dispatcher then runs the
//! Python command with the same arguments. That is the answer for:
//!
//! * anything this parser does not take exactly (an unknown or abbreviated flag, `-h`, a
//!   missing `--id`, a stray argument): argparse has the real message;
//! * no `--seat`: Python finds the caller's seat (seat_who); this does not guess;
//! * take / close / reopen of a global dm: its sender gets a receipt
//!   (`_global_delivery_for_transition`), which is not written in Rust yet (S5).
//!
//! Environment: the state root is `EZ2_AIB_STATE_ROOT`, else `EZ2_AIB_HOME`, else
//! `~/.ez2-aib` (seats_dir_lib.state_root). A commit given as evidence is looked up in
//! `AIB_DMQ_REPO` (the dispatcher passes the folder dm_queue.py uses, `_HERE.parents[1]`),
//! else the current folder. Any other failure (I/O, a broken folder) is exit 1 with one
//! line on stderr, as a Python traceback would be; nothing is retried in Python then,
//! since a move may already be written.

use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::{SystemTime, UNIX_EPOCH};

use aib_dmq::util::truthy;
use aib_dmq_store::pyjson;
use aib_dmq_store::recv::{self, relay_dirs, Answer, Ctx};
use aib_dmq_store::store::{Caller, ItemRead, Store};
use serde_json::Value;

/// "Not handled here, nothing touched": the dispatcher runs Python instead.
const FALLBACK: u8 = 75;

/// seat_act.SESSION_ENVS: a CLI conversation id in the shell means a seat.
const SESSION_ENVS: [&str; 3] = [
    "AIB_RESUME_SESSION_UUID",
    "CLAUDE_CODE_SESSION_ID",
    "CODEX_THREAD_ID",
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Cmd {
    Take,
    Close,
    Wait,
    Reopen,
}

#[derive(Debug, Default)]
struct Args {
    seat: Option<String>,
    id: Option<String>,
    by: Option<String>,
    wait: Option<String>,
    evidence: Option<String>,
    why: Option<String>,
    text: Option<String>,
}

/// The flags each parser has (dm_queue.py build_parser), exactly; None = not ours.
fn parse(argv: &[String]) -> Option<(Cmd, Args)> {
    let (cmd, rest) = argv.split_first()?;
    let cmd = match cmd.as_str() {
        "take" => Cmd::Take,
        "close" => Cmd::Close,
        "wait" => Cmd::Wait,
        "reopen" => Cmd::Reopen,
        _ => return None,
    };
    let mut a = Args::default();
    let mut it = rest.iter();
    while let Some(arg) = it.next() {
        if !arg.starts_with('-') || arg == "-" {
            if cmd != Cmd::Wait || a.text.is_some() {
                return None;
            }
            a.text = Some(arg.clone());
            continue;
        }
        let (name, inline) = match arg.split_once('=') {
            Some((n, v)) => (n, Some(v.to_owned())),
            None => (arg.as_str(), None),
        };
        let slot = match (name, cmd) {
            ("--seat", _) => &mut a.seat,
            ("--id", _) => &mut a.id,
            ("--by", _) => &mut a.by,
            ("--wait", Cmd::Take) => &mut a.wait,
            ("--evidence", Cmd::Close) => &mut a.evidence,
            ("--why", Cmd::Close | Cmd::Reopen) => &mut a.why,
            // `--`, `-h`, an abbreviation, a flag of another parser: argparse decides
            _ => return None,
        };
        let value = match inline {
            Some(v) => v,
            // argparse takes a value that looks like a flag only with `=`
            None => match it.next() {
                Some(v) if !v.starts_with('-') || v == "-" => v.clone(),
                _ => return None,
            },
        };
        *slot = Some(value);
    }
    if a.id.is_none() || (cmd == Cmd::Reopen && a.why.is_none()) {
        return None;
    }
    Some((cmd, a))
}

fn env(k: &str) -> Option<String> {
    std::env::var(k).ok().filter(|v| !v.is_empty())
}

fn state_root() -> Option<PathBuf> {
    env("EZ2_AIB_STATE_ROOT")
        .or_else(|| env("EZ2_AIB_HOME"))
        .map(PathBuf::from)
        .or_else(|| {
            env("HOME")
                .or_else(|| env("USERPROFILE"))
                .map(|h| Path::new(&h).join(".ez2-aib"))
        })
}

/// seat_act.caller_is_seat.
fn caller_is_seat() -> bool {
    SESSION_ENVS.iter().any(|k| env(k).is_some())
        || env("TMUX_PANE").is_some()
        || env("TMUX").is_some()
}

/// _TRANSITION_RC, 4 for a reason not in it.
fn exit_code(out: &Value) -> u8 {
    if out.get("ok") == Some(&Value::Bool(true)) {
        return 0;
    }
    match out.get("reason").and_then(Value::as_str).unwrap_or("") {
        "rev_mismatch" => 5,
        "no_rev" => 6,
        "actor" | "actor_missing" => 7,
        "lock_timeout" => 10,
        "transition" | "im_room_only" => 8,
        "evidence" => 9,
        _ => 4,
    }
}

/// dm_take.emit.
fn emit(out: &Value) -> Result<String, serde_json::Error> {
    let mut head = out.clone();
    let text = head
        .as_object_mut()
        .and_then(|m| m.remove("text"))
        .and_then(|t| t.as_str().map(str::to_owned));
    let mut s = pyjson::dumps(&head)?;
    s.push('\n');
    if let Some(t) = text {
        s.push_str("----- 全文 -----\n");
        s.push_str(t.trim_end_matches('\n'));
        s.push_str("\n----- 完 -----\n");
    }
    Ok(s)
}

fn fail(what: &str, e: impl std::fmt::Display) -> ExitCode {
    eprintln!("aib-dmq: {what}: {e}");
    ExitCode::from(1)
}

fn main() -> ExitCode {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let Some((cmd, a)) = parse(&argv) else {
        return ExitCode::from(FALLBACK);
    };
    // `--seat ""` is no seat to argparse's caller too (`if not args.seat`)
    let (Some(seat), Some(id)) = (a.seat.as_deref().filter(|s| !s.is_empty()), a.id.as_deref())
    else {
        return ExitCode::from(FALLBACK);
    };
    let Some(root) = state_root() else {
        return ExitCode::from(FALLBACK);
    };
    let repo = env("AIB_DMQ_REPO").map_or_else(|| PathBuf::from("."), PathBuf::from);
    let store = Store::new(&root, &repo);
    // args.by = args.by or "seat:" + args.seat
    let by =
        a.by.clone()
            .filter(|b| !b.is_empty())
            .unwrap_or_else(|| format!("seat:{seat}"));

    if cmd != Cmd::Wait {
        match store.read_item(seat, id) {
            Ok(ItemRead::Item { item, .. }) if truthy(item.get("global")) => {
                eprintln!("aib-dmq: {id} is a global dm (its receipt is sent by the Python path)");
                return ExitCode::from(FALLBACK);
            }
            Ok(_) => {}
            // a seat code that is not a seat code, an unreadable folder: Python's answer
            Err(_) => return ExitCode::from(FALLBACK),
        }
    }

    let caller = Caller::from_env();
    let dirs = relay_dirs(|k| std::env::var(k).ok());
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_secs()).unwrap_or(i64::MAX));
    let ctx = Ctx {
        caller: &caller,
        caller_is_seat: caller_is_seat(),
        relay_dirs: &dirs,
        now,
    };
    let answer = match cmd {
        Cmd::Take => recv::take(&store, &ctx, seat, id, &by, a.wait.as_deref()),
        Cmd::Close => recv::close(
            &store,
            &ctx,
            seat,
            id,
            &by,
            a.evidence.as_deref(),
            a.why.as_deref(),
        ),
        Cmd::Wait => recv::set_wait(&store, &ctx, seat, id, &by, a.text.as_deref().or(Some(""))),
        Cmd::Reopen => recv::reopen(&store, &ctx, seat, id, &by, a.why.as_deref().unwrap_or("")),
    };
    let Answer { out, receipts } = match answer {
        Ok(a) => a,
        Err(e) => return fail(cmd_name(cmd), e),
    };
    if !receipts.is_empty() {
        // only a global dm has one, and those were handed to Python above
        eprintln!(
            "aib-dmq: {} receipt(s) for {id} not sent (global dm reached the Rust path)",
            receipts.len()
        );
    }
    let text = match cmd {
        Cmd::Reopen => pyjson::dumps(&out).map(|mut s| {
            s.push('\n');
            s
        }),
        _ => emit(&out),
    };
    match text {
        Ok(s) => print!("{s}"),
        Err(e) => return fail("output", e),
    }
    ExitCode::from(exit_code(&out))
}

fn cmd_name(cmd: Cmd) -> &'static str {
    match cmd {
        Cmd::Take => "take",
        Cmd::Close => "close",
        Cmd::Wait => "wait",
        Cmd::Reopen => "reopen",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(s: &[&str]) -> Vec<String> {
        s.iter().map(|x| (*x).to_owned()).collect()
    }

    #[test]
    fn the_flags_are_argparse_s_and_nothing_else() {
        let (c, a) =
            parse(&v(&["take", "--seat", "aaaaa", "--id=q_1", "--wait", "x"])).expect("take");
        assert_eq!(c, Cmd::Take);
        assert_eq!(
            (a.seat.as_deref(), a.id.as_deref(), a.wait.as_deref()),
            (Some("aaaaa"), Some("q_1"), Some("x"))
        );
        let (_, a) = parse(&v(&["wait", "--id", "q_1", "等 519d0"])).expect("wait");
        assert_eq!(a.text.as_deref(), Some("等 519d0"));
        let (_, a) = parse(&v(&["reopen", "--id", "q_1", "--why", "結錯"])).expect("reopen");
        assert_eq!(a.why.as_deref(), Some("結錯"));
        // argparse's own: abbreviations, help, `--`, another parser's flag, a missing value
        for bad in [
            &["take", "--id", "q_1", "--ev", "x"][..],
            &["take", "--id", "q_1", "--evidence", "x"],
            &["close", "--id", "q_1", "--wait", "x"],
            &["take", "--id", "q_1", "-h"],
            &["take", "--id", "q_1", "--"],
            &["take", "--id"],
            &["take", "--id", "--seat"],
            &["take", "--seat", "aaaaa"],
            &["take", "--id", "q_1", "extra"],
            &["wait", "--id", "q_1", "a", "b"],
            &["reopen", "--id", "q_1"],
            &["read", "--id", "q_1"],
            &[],
        ] {
            assert!(parse(&v(bad)).is_none(), "{bad:?}");
        }
    }

    #[test]
    fn emit_is_dm_take_emit() {
        let out = serde_json::json!({"ok": true, "text": "全文\n\n", "id": "q_1"});
        assert_eq!(
            emit(&out).expect("emit"),
            "{\"id\": \"q_1\", \"ok\": true}\n----- 全文 -----\n全文\n----- 完 -----\n"
        );
        let out = serde_json::json!({"ok": false, "reason": "evidence", "text": null});
        assert_eq!(
            emit(&out).expect("emit"),
            "{\"ok\": false, \"reason\": \"evidence\"}\n"
        );
    }

    #[test]
    fn exit_codes_are_transition_rc() {
        let rc = |r: &str| exit_code(&serde_json::json!({"ok": false, "reason": r}));
        assert_eq!(
            exit_code(&serde_json::json!({"ok": true, "reason": "already_closed"})),
            0
        );
        assert_eq!(
            [
                rc("rev_mismatch"),
                rc("no_rev"),
                rc("actor"),
                rc("actor_missing"),
                rc("lock_timeout")
            ],
            [5, 6, 7, 7, 10]
        );
        assert_eq!(
            [rc("transition"), rc("im_room_only"), rc("evidence")],
            [8, 8, 9]
        );
        assert_eq!([rc("not_found"), rc("relay_unreadable"), rc("")], [4, 4, 4]);
    }
}
