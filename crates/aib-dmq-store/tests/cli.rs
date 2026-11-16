//! The `aib-dmq` binary (B1 S3b) on a temp state root: what `aib dm-queue take / close /
//! wait / reopen` print and exit with, and when it hands the call back to Python (exit 75,
//! nothing touched).

use std::path::Path;
use std::process::{Command, Output};

use aib_dmq::spec::ITEM_SCHEMA;
use aib_dmq_store::{ItemRead, Store};
use serde_json::{json, Map, Value};

const SEAT: &str = "aaaaa";
const ID: &str = "q_00000000000000c3";

struct T {
    d: tempfile::TempDir,
    s: Store,
}

impl T {
    fn new() -> T {
        let d = tempfile::tempdir().expect("tempdir");
        let s = Store::new(d.path(), d.path());
        T { d, s }
    }
    fn put(&self, status: &str, extra: Value) {
        let mut m: Map<String, Value> = json!({"schema": ITEM_SCHEMA, "id": ID, "seat": SEAT,
            "msg": "請審 S3b\n", "status": status, "rev": 3, "kind": "report",
            "sender": "agent:bbbbb", "sender_label": "@bbbbb", "ts": 1_794_578_000})
        .as_object()
        .expect("object")
        .clone();
        if let Value::Object(e) = extra {
            m.extend(e);
        }
        self.s.write_item(SEAT, &m).expect("write");
    }
    fn item(&self) -> Map<String, Value> {
        match self.s.read_item(SEAT, ID).expect("read") {
            ItemRead::Item { item, .. } => item,
            other => panic!("{other:?}"),
        }
    }
    fn run(&self, args: &[&str]) -> Output {
        Command::new(env!("CARGO_BIN_EXE_aib-dmq"))
            .args(args)
            .env_clear()
            .env("EZ2_AIB_STATE_ROOT", self.d.path())
            .env("HOME", self.d.path())
            .env("AIB_DMQ_REPO", self.d.path())
            .env("PATH", std::env::var_os("PATH").unwrap_or_default())
            .envs(std::env::var_os("SYSTEMROOT").map(|v| ("SYSTEMROOT", v)))
            .output()
            .expect("run aib-dmq")
    }
    fn evidence(&self) -> String {
        let p = self.d.path().join("REVIEW.md");
        std::fs::write(&p, "ok\n").expect("evidence");
        p.to_string_lossy().into_owned()
    }
}

fn rc(o: &Output) -> i32 {
    o.status.code().unwrap_or(-1)
}

fn stdout(o: &Output) -> String {
    String::from_utf8_lossy(&o.stdout).into_owned()
}

fn head(o: &Output) -> Value {
    let s = stdout(o);
    serde_json::from_str(s.lines().next().unwrap_or("")).unwrap_or(Value::Null)
}

#[test]
fn take_prints_one_json_line_then_the_text() {
    let t = T::new();
    t.put("delivered", json!({}));
    let o = t.run(&["take", "--seat", SEAT, "--id", ID]);
    assert_eq!(rc(&o), 0, "{}", stdout(&o));
    let h = head(&o);
    assert_eq!(
        (h["ok"].clone(), h["reason"].clone()),
        (json!(true), json!("ok"))
    );
    assert!(
        h.get("text").is_none(),
        "the text is not on the first line: {h}"
    );
    let s = stdout(&o);
    assert!(
        s.ends_with("\n----- 全文 -----\n請審 S3b\n----- 完 -----\n"),
        "{s}"
    );
    assert!(
        s.lines().next().is_some_and(|l| l.contains("\"ok\": true")),
        "Python separators: {s}"
    );
    assert_eq!(t.item()["status"], json!("read"));
    assert_eq!(t.item()["taken_rev"], json!(3));
}

#[test]
fn close_needs_evidence_then_closes_with_it() {
    let t = T::new();
    t.put("read", json!({"taken_rev": 3}));
    let o = t.run(&["close", "--seat", SEAT, "--id", ID]);
    assert_eq!(rc(&o), 9, "{}", stdout(&o));
    assert_eq!(head(&o)["reason"], json!("evidence"));
    assert_eq!(t.item()["status"], json!("read"), "nothing moved");
    let ev = t.evidence();
    let o = t.run(&["close", "--seat", SEAT, "--id", ID, "--evidence", &ev]);
    assert_eq!(rc(&o), 0, "{}", stdout(&o));
    assert_eq!(t.item()["status"], json!("done"));
}

#[test]
fn another_seat_is_refused_with_7() {
    let t = T::new();
    t.put("delivered", json!({}));
    let o = t.run(&["take", "--seat", SEAT, "--id", ID, "--by", "seat:bbbbb"]);
    assert_eq!(rc(&o), 7, "{}", stdout(&o));
    assert_eq!(t.item()["status"], json!("delivered"));
}

#[test]
fn wait_takes_its_text_as_the_positional_argument() {
    let t = T::new();
    t.put("read", json!({}));
    let o = t.run(&["wait", "--seat", SEAT, "--id", ID, "等 519d0 回"]);
    assert_eq!(rc(&o), 0, "{}", stdout(&o));
    assert_eq!(t.item()["wait"]["text"], json!("等 519d0 回"));
    let o = t.run(&["wait", "--seat", SEAT, "--id", ID]);
    assert_eq!(rc(&o), 0, "{}", stdout(&o));
    assert_eq!(t.item()["wait"], Value::Null, "no text clears it");
}

#[test]
fn reopen_puts_a_done_dm_back_to_read_and_prints_python_s_bytes() {
    let t = T::new();
    t.put("done", json!({"done_why": "結錯"}));
    let o = t.run(&["reopen", "--seat", SEAT, "--id", ID, "--why", " 還沒做完 "]);
    assert_eq!(rc(&o), 0, "{}", stdout(&o));
    assert_eq!(
        stdout(&o),
        "{\"message\": \"\", \"ok\": true, \"reason\": \"ok\", \"rev\": 3}\n"
    );
    let it = t.item();
    assert_eq!(it["status"], json!("read"));
    assert_eq!(it["reopen_why"], json!("reopen:還沒做完"));
}

#[test]
fn reopen_refusals_name_the_move_like_python() {
    let t = T::new();
    t.put("read", json!({}));
    let o = t.run(&["reopen", "--seat", SEAT, "--id", ID, "--why", "x"]);
    assert_eq!(rc(&o), 8, "{}", stdout(&o));
    assert_eq!(
        stdout(&o),
        "{\"actor\": \"seat:aaaaa\", \"from\": \"read\", \"message\": \"這個狀態不能這樣改\", \
         \"ok\": false, \"reason\": \"transition\", \"rev\": 3, \"to\": \"read\"}\n"
    );
    let o = t.run(&["reopen", "--seat", SEAT, "--id", ID, "--why", "  "]);
    assert_eq!(rc(&o), 8);
    assert_eq!(
        stdout(&o),
        "{\"message\": \"回復要寫理由\", \"ok\": false, \"reason\": \"transition\"}\n"
    );
    let o = t.run(&[
        "reopen",
        "--seat",
        SEAT,
        "--id",
        "q_00000000000000ff",
        "--why",
        "x",
    ]);
    assert_eq!(rc(&o), 4, "{}", stdout(&o));
    assert_eq!(head(&o)["reason"], json!("not_found"));
    assert_eq!(t.item()["status"], json!("read"));
}

fn file_bytes(dir: &Path) -> Vec<(String, Vec<u8>)> {
    let mut out = Vec::new();
    for e in walk(dir) {
        out.push((
            e.to_string_lossy().into_owned(),
            std::fs::read(&e).unwrap_or_default(),
        ));
    }
    out.sort();
    out
}

fn walk(dir: &Path) -> Vec<std::path::PathBuf> {
    let mut out = Vec::new();
    for e in std::fs::read_dir(dir).into_iter().flatten().flatten() {
        let p = e.path();
        if p.is_dir() {
            out.extend(walk(&p));
        } else {
            out.push(p);
        }
    }
    out
}

#[test]
fn a_global_dm_goes_back_to_python_untouched() {
    let t = T::new();
    t.put("delivered", json!({"global": {"msgid": "m1"}}));
    let ev = t.evidence();
    let before = file_bytes(t.d.path());
    for args in [
        &["take", "--seat", SEAT, "--id", ID][..],
        &["close", "--seat", SEAT, "--id", ID, "--evidence", &ev],
        &["reopen", "--seat", SEAT, "--id", ID, "--why", "x"],
    ] {
        let o = t.run(args);
        assert_eq!(rc(&o), 75, "{args:?}: {}", stdout(&o));
        assert!(o.stdout.is_empty(), "{args:?}");
    }
    assert_eq!(
        file_bytes(t.d.path()),
        before,
        "no file written, no audit line"
    );
}

#[test]
fn what_argparse_or_seat_who_must_answer_goes_back_to_python() {
    let t = T::new();
    t.put("delivered", json!({}));
    let before = file_bytes(t.d.path());
    for args in [
        &["take", "--id", ID][..],
        &["take", "--seat", "", "--id", ID],
        // wait has no global-dm read first: only the empty-seat check sends it back
        &["wait", "--seat", "", "--id", ID],
        &["take", "--seat", SEAT, "--id", ID, "--ev", "x"],
        &["take", "--seat", SEAT, "-h"],
        &["reopen", "--seat", SEAT, "--id", ID],
        &["read", "--seat", SEAT, "--id", ID],
    ] {
        let o = t.run(args);
        assert_eq!(rc(&o), 75, "{args:?}: {}", stdout(&o));
        assert!(o.stdout.is_empty(), "{args:?}");
    }
    assert_eq!(file_bytes(t.d.path()), before);
}

/// aib/src/cockpit/dmq-route.sh with this binary: off by default, Rust when listed, Python
/// again on 75 (a global dm), and Rust's exit code otherwise.
#[cfg(unix)]
#[test]
fn the_bash_dispatcher_runs_rust_when_listed_and_python_on_75() {
    let route = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../aib/src/cockpit/dmq-route.sh");
    let t = T::new();
    let run = |list: &str, args: &[&str]| -> Output {
        Command::new("bash")
            .arg("-c")
            .arg("set -euo pipefail; . \"$0\"; aib_dmq_route /nowhere/src/cockpit \"$@\"; echo PYTHON \"$@\"")
            .arg(&route)
            .args(args)
            .env_clear()
            .env("EZ2_AIB_STATE_ROOT", t.d.path())
            .env("HOME", t.d.path())
            .env("AIB_DMQ_RUST", list)
            .env("AIB_DMQ_BIN", env!("CARGO_BIN_EXE_aib-dmq"))
            .env("PATH", std::env::var_os("PATH").unwrap_or_default())
            .output()
            .expect("bash")
    };
    t.put("delivered", json!({}));
    let o = run("", &["take", "--seat", SEAT, "--id", ID]);
    assert_eq!(
        (rc(&o), stdout(&o)),
        (0, format!("PYTHON take --seat {SEAT} --id {ID}\n"))
    );
    assert_eq!(
        t.item()["status"],
        json!("delivered"),
        "off: Rust did not run"
    );
    let o = run(
        "close,take",
        &["take", "--seat", SEAT, "--id", ID, "--by", "seat:bbbbb"],
    );
    assert_eq!(rc(&o), 7, "Rust's refusal is the exit code: {}", stdout(&o));
    assert!(
        !stdout(&o).contains("PYTHON"),
        "and Python does not run after it"
    );
    // a bare name that is an executable file in the current folder: `[ -f ]` would pass
    // there while running it looks the name up in PATH
    let cwd = tempfile::tempdir().expect("cwd");
    let fake = cwd.path().join("aib-dmq");
    std::fs::write(&fake, "#!/bin/sh\necho RUST\n").expect("fake");
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).expect("chmod");
    }
    let o = Command::new("bash")
        .arg("-c")
        .arg(". \"$0\"; aib_dmq_route /nowhere/src/cockpit \"$@\"; echo PYTHON")
        .arg(&route)
        .args(["take", "--seat", SEAT, "--id", ID])
        .current_dir(cwd.path())
        .env_clear()
        .env("AIB_DMQ_RUST", "take")
        .env("AIB_DMQ_BIN", "aib-dmq")
        .env("PATH", std::env::var_os("PATH").unwrap_or_default())
        .output()
        .expect("bash");
    assert_eq!(
        (rc(&o), stdout(&o)),
        (0, "PYTHON\n".to_owned()),
        "a relative AIB_DMQ_BIN is not run"
    );
    let o = run("take", &["take", "--seat", SEAT, "--id", ID]);
    assert_eq!(rc(&o), 0);
    assert!(stdout(&o).contains("----- 全文 -----") && !stdout(&o).contains("PYTHON"));
    assert_eq!(t.item()["status"], json!("read"));
    t.put("done", json!({"global": {"msgid": "m1"}}));
    let o = run(
        "reopen",
        &["reopen", "--seat", SEAT, "--id", ID, "--why", "x"],
    );
    assert_eq!(
        (rc(&o), stdout(&o)),
        (
            0,
            format!("PYTHON reopen --seat {SEAT} --id {ID} --why x\n")
        ),
        "a global dm: 75, then the Python line"
    );
    assert_eq!(t.item()["status"], json!("done"));
}

#[test]
fn reopen_of_a_tombstone_is_not_found_before_any_move() {
    let t = T::new();
    t.put("deleted", json!({}));
    let o = t.run(&["reopen", "--seat", SEAT, "--id", ID, "--why", "x"]);
    assert_eq!(rc(&o), 4, "{}", stdout(&o));
    assert_eq!(
        stdout(&o),
        "{\"message\": \"這封信已經不在了\", \"ok\": false, \"reason\": \"not_found\"}\n",
        "no rev, as dm_triage_agent.reopen returns early"
    );
    let audit =
        t.d.path()
            .join("state/dm-queue")
            .join(SEAT)
            .join("audit.jsonl");
    assert!(!audit.exists(), "no audit line");
}

#[cfg(unix)]
#[test]
fn an_argument_that_is_not_utf8_goes_back_to_python() {
    use std::os::unix::ffi::OsStrExt;
    let t = T::new();
    t.put("read", json!({}));
    let o = Command::new(env!("CARGO_BIN_EXE_aib-dmq"))
        .args(["wait", "--seat", SEAT, "--id", ID])
        .arg(std::ffi::OsStr::from_bytes(b"\xff\xfe"))
        .env_clear()
        .env("EZ2_AIB_STATE_ROOT", t.d.path())
        .output()
        .expect("run");
    assert_eq!(rc(&o), 75, "{}", String::from_utf8_lossy(&o.stderr));
    assert!(
        t.item().get("wait").is_none_or(Value::is_null),
        "nothing written"
    );
}
