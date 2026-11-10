//! `aib-seatops dm close` against a queue laid out the way the Python queue lays it out, and
//! against the Python queue itself (two writers on the same dms, and old / new on one fixture).
#![cfg(unix)]

use aib_seatops::close::{close, close_with, Closed, Refused, Request, Roots};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

const SEAT: &str = "1ced7";

struct Q {
    _t: tempfile::TempDir,
    roots: Roots,
}

fn q() -> Q {
    let t = tempfile::tempdir().expect("tempdir");
    let base = std::fs::canonicalize(t.path()).expect("canon");
    let state = base.join("state");
    let pending = state.join("pending");
    std::fs::create_dir_all(state.join("state").join("dm-queue").join(SEAT)).expect("mkdir");
    std::fs::create_dir_all(&pending).expect("mkdir");
    Q {
        _t: t,
        roots: Roots {
            state,
            relay_dirs: vec![pending],
            repo: base.join("no-repo"),
        },
    }
}

fn id(n: u32) -> String {
    format!("q_{n:016x}")
}

impl Q {
    fn dir(&self) -> PathBuf {
        self.roots.seat_dir(SEAT)
    }
    fn put(&self, id: &str, status: &str, msg: &str) {
        let v = json!({
            "schema": "aib-dm-queue-item/v2", "id": id, "rev": 1, "seat": SEAT, "msg": msg,
            "status": status, "kind": "other", "sender_label": "@a63ab", "sender_kind": "seat",
            "sender_seat": "a63ab", "sender_source": "pane_pid_chain", "ts": 1794310000,
            "sent_at": 1794310000, "delivered_at": 1794310001, "read_at": null, "done_at": null,
            "done_evidence": null, "priority": "normal", "origin": "live", "attempts": 0,
            "thread": null, "supersedes": null, "global": null, "im": null,
        });
        std::fs::write(self.dir().join(format!("{id}.json")), format!("{v}\n")).expect("w");
    }
    fn item(&self, id: &str) -> Value {
        serde_json::from_str(
            &std::fs::read_to_string(self.dir().join(format!("{id}.json"))).expect("r"),
        )
        .expect("json")
    }
    fn audit(&self) -> Vec<Value> {
        std::fs::read_to_string(self.dir().join("audit.jsonl"))
            .unwrap_or_default()
            .lines()
            .filter_map(|l| serde_json::from_str(l).ok())
            .collect()
    }
    fn req(&self, id: &str, expect: &str, ev: Option<&str>, why: Option<&str>) -> Request {
        Request {
            seat: SEAT.into(),
            id: id.into(),
            rev: 1,
            expect: expect.into(),
            evidence: ev.map(str::to_owned),
            why: why.map(str::to_owned),
        }
    }
    fn close(
        &self,
        id: &str,
        expect: &str,
        ev: Option<&str>,
        why: Option<&str>,
    ) -> Result<Closed, Refused> {
        close(&self.roots, &self.req(id, expect, ev, why))
    }
}

#[test]
fn a_delivered_dm_is_closed_with_a_reason_and_leaves_an_audit_line() {
    let q = q();
    q.put(&id(1), "delivered", "please review #628 now");
    let c = q
        .close(&id(1), "review #628", None, Some("reviewed"))
        .expect("close");
    assert_eq!(
        (c.done_kind.as_str(), c.from.as_str()),
        ("reason", "delivered")
    );
    let it = q.item(&id(1));
    assert_eq!(it["status"], "done");
    assert_eq!(it["done_kind"], "reason");
    assert_eq!(it["done_why"], "reviewed");
    assert_eq!(it["done_evidence"], Value::Null);
    assert!(
        it["done_at"].as_u64().is_some() && it["read_at"].as_u64().is_some(),
        "{it}"
    );
    let a = q.audit();
    assert_eq!(a.len(), 1);
    assert_eq!(
        (&a[0]["ok"], &a[0]["to"], &a[0]["from"]),
        (&json!(true), &json!("done"), &json!("delivered"))
    );
    assert_eq!(
        std::fs::read_to_string(q.dir().join("rev"))
            .expect("rev")
            .trim(),
        "1"
    );
    // closing it again is refused; nothing is written
    let r = q
        .close(&id(1), "review #628", None, Some("again"))
        .expect_err("again");
    assert_eq!(r.reason, "transition");
    assert_eq!(q.item(&id(1))["done_why"], "reviewed");
}

#[test]
fn evidence_is_one_whole_token() {
    let q = q();
    let f = q.roots.state.join("pending").join("REVIEW-x.md");
    std::fs::write(&f, "ok\n").expect("w");
    for n in 1..=4 {
        q.put(&id(n), "read", "please review the thing");
    }
    let c = q
        .close(&id(1), "review the", Some(&f.to_string_lossy()), None)
        .expect("file");
    assert_eq!(c.done_kind, "file");
    assert_eq!(q.item(&id(1))["done_evidence"], json!(f.to_string_lossy()));
    let c = q
        .close(
            &id(2),
            "review the",
            Some("https://github.com/ez2app/ez2-aib/pull/628"),
            None,
        )
        .expect("pr");
    assert_eq!(c.done_kind, "pr");
    // a sentence is not read for evidence: refused without --why, a reason with it
    let sentence = format!("see {}", f.display());
    let r = q
        .close(&id(3), "review the", Some(&sentence), None)
        .expect_err("sentence");
    assert_eq!(r.reason, "evidence");
    assert_eq!(q.item(&id(3))["status"], "read");
    let c = q
        .close(&id(4), "review the", Some(&sentence), Some("done"))
        .expect("why");
    assert_eq!(c.done_kind, "reason");
}

#[test]
fn expect_and_rev_are_checked_before_anything_is_written() {
    let q = q();
    q.put(&id(1), "delivered", "please review #628 now");
    let r = q
        .close(&id(1), "review #627", None, Some("x"))
        .expect_err("expect");
    assert_eq!(r.reason, "snapshot");
    let mut req = q.req(&id(1), "review #628", None, Some("x"));
    req.rev = 2;
    assert_eq!(
        close(&q.roots, &req).expect_err("rev").reason,
        "rev_mismatch"
    );
    assert_eq!(q.item(&id(1))["status"], "delivered");
    assert!(q.audit().is_empty());
}

/// S1: the content is compared again inside the lock.
#[test]
fn content_changed_after_the_snapshot_is_not_closed() {
    let q = q();
    q.put(&id(1), "delivered", "please review #628 now");
    let req = q.req(&id(1), "review #628", None, Some("x"));
    let r = close_with(&q.roots, &req, Duration::ZERO, &|| {
        q.put(
            &id(1),
            "delivered",
            "please review #628 now, and also delete branch X",
        );
    })
    .expect_err("changed");
    assert_eq!(r.reason, "changed", "{r:?}");
    assert_eq!(q.item(&id(1))["status"], "delivered");
}

#[test]
fn a_long_dm_is_closed_on_its_relay_content() {
    let q = q();
    let relay = q
        .roots
        .state
        .join("pending")
        .join("RELAY-dm-9320-1110-000000-1.txt");
    std::fs::write(&relay, "B9 plan: start now").expect("w");
    let msg = format!("長訊已落檔(20 位元組,來自 @a63ab),請讀:{}", relay.display());
    q.put(&id(1), "delivered", &msg);
    q.put(&id(2), "delivered", &msg);
    // the needle must be in the relay, not only in the pointer
    assert_eq!(
        q.close(&id(1), "RELAY-dm-9320", None, Some("x"))
            .expect_err("ptr")
            .reason,
        "snapshot"
    );
    // the relay changes between the snapshot and the lock
    let req = q.req(&id(1), "B9 plan", None, Some("x"));
    let r = close_with(&q.roots, &req, Duration::ZERO, &|| {
        std::fs::write(&relay, "B9 plan: stop, do not start").expect("w");
    })
    .expect_err("relay changed");
    assert_eq!(r.reason, "changed");
    assert!(q.close(&id(2), "do not start", None, Some("read")).is_ok());
}

#[test]
fn queued_global_and_old_dms_go_to_the_python_path() {
    let q = q();
    q.put(&id(1), "queued", "please review #628 now");
    assert_eq!(
        q.close(&id(1), "review #628", None, Some("x"))
            .expect_err("queued")
            .reason,
        "use_python"
    );
    q.put(&id(2), "delivered", "please review #628 now");
    let mut it = q.item(&id(2));
    it["global"] = json!({"msgid": "m1"});
    std::fs::write(q.dir().join(format!("{}.json", id(2))), it.to_string()).expect("w");
    assert_eq!(
        q.close(&id(2), "review #628", None, Some("x"))
            .expect_err("global")
            .reason,
        "use_python"
    );
    q.put(&id(3), "delivered", "please review #628 now");
    let mut it = q.item(&id(3));
    it["schema"] = json!("aib-dm-queue-item/v1");
    std::fs::write(q.dir().join(format!("{}.json", id(3))), it.to_string()).expect("w");
    assert_eq!(
        q.close(&id(3), "review #628", None, Some("x"))
            .expect_err("v1")
            .reason,
        "use_python"
    );
}

#[test]
fn the_order_and_the_selection_follow_python() {
    let q = q();
    let order = |ids: &[String]| {
        std::fs::write(
            q.dir().join("order.json"),
            json!({"schema": "aib-dm-queue-order/v1", "seat": SEAT, "ids": ids}).to_string(),
        )
        .expect("w")
    };
    std::fs::write(q.dir().join("view.json"), "{}").expect("w");
    q.put(&id(1), "read", "please review #628 now");
    q.put(&id(2), "delivered", "something else waits");
    order(&[id(1)]);
    q.close(&id(1), "review #628", None, Some("x"))
        .expect("close");
    let o: Value =
        serde_json::from_str(&std::fs::read_to_string(q.dir().join("order.json")).expect("r"))
            .expect("j");
    assert_eq!(o["ids"], json!([]));
    // another dm still waits: the selection stays
    assert!(q.dir().join("view.json").exists());
    q.close(&id(2), "something else", None, Some("x"))
        .expect("close");
    assert!(!q.dir().join("view.json").exists());
}

// ---- against the Python queue ----------------------------------------------------------

fn cockpit() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../aib/src/cockpit")
}

/// A Python worker: imports dm_queue, prints `ready`, then runs `read_done(..., why="py")` on
/// each id it reads from stdin and prints one JSON result per line.
struct Py(
    std::process::Child,
    std::io::BufReader<std::process::ChildStdout>,
);

impl Py {
    fn start(roots: &Roots) -> Py {
        let code = r#"
import json, os, sys
sys.path.insert(0, sys.argv[1])
import dm_queue as Q
env = dict(os.environ)
print("ready", flush=True)
for line in sys.stdin:
    i = line.strip()
    r = Q.read_done(sys.argv[2], i, "seat:" + sys.argv[2], None, env=env, if_rev=1, why="py")
    print(json.dumps({"id": i, "ok": bool(r.get("ok")), "reason": r.get("reason")}), flush=True)
"#;
        let mut c = Command::new("python3")
            .arg("-B")
            .arg("-c")
            .arg(code)
            .arg(cockpit())
            .arg(SEAT)
            .env("EZ2_AIB_STATE_ROOT", &roots.state)
            .env_remove("EZ2_AIB_HOME")
            .env("HOME", roots.state.parent().expect("base"))
            .env("EZ2_AIB_LOCALE", "en")
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::inherit())
            .spawn()
            .expect("python3 must be on PATH for this test");
        let mut out = std::io::BufReader::new(c.stdout.take().expect("stdout"));
        let mut line = String::new();
        std::io::BufRead::read_line(&mut out, &mut line).expect("ready");
        assert_eq!(line.trim(), "ready", "the Python worker did not start");
        Py(c, out)
    }
    fn send(&mut self, ids: &[String]) {
        let w = self.0.stdin.as_mut().expect("stdin");
        for i in ids {
            std::io::Write::write_all(w, format!("{i}\n").as_bytes()).expect("send");
        }
        std::io::Write::flush(w).expect("flush");
    }
    /// Close stdin, collect every result line, check the worker exited 0.
    fn finish(mut self) -> Vec<Value> {
        drop(self.0.stdin.take());
        let rows = std::io::BufRead::lines(&mut self.1)
            .map_while(Result::ok)
            .filter_map(|l| serde_json::from_str(&l).ok())
            .collect();
        assert!(
            self.0.wait().expect("wait").success(),
            "Python worker failed"
        );
        rows
    }
}

fn closes_of(q: &Q, i: &str) -> Vec<Value> {
    q.audit()
        .into_iter()
        .filter(|r| r["id"] == json!(i) && r["to"] == "done" && r["ok"] == json!(true))
        .collect()
}

/// S2: Rust holds the seat's transition lock while Python tries to close the same dm. Python
/// must wait and then find it closed; without the lock both would write `done`.
#[test]
fn python_waits_for_a_rust_close_of_the_same_dm() {
    let q = q();
    q.put(&id(1), "delivered", "please review #628 now");
    let mut py = Py::start(&q.roots);
    std::thread::scope(|s| {
        let rust = s.spawn(|| {
            close_with(
                &q.roots,
                &q.req(&id(1), "review #628", None, Some("rust")),
                Duration::from_millis(1500),
                &|| {},
            )
        });
        std::thread::sleep(Duration::from_millis(300)); // Rust is inside the lock now
        py.send(&[id(1)]);
        assert!(rust.join().expect("join").is_ok());
    });
    let rows = py.finish();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0]["ok"], json!(false), "{rows:?}");
    let oks = closes_of(&q, &id(1));
    assert_eq!(oks.len(), 1, "closed {} times: {oks:?}", oks.len());
    assert_eq!(q.item(&id(1))["done_why"], "rust");
}

/// Rust and Python close the same 60 dms at the same time (from opposite ends). Each dm is
/// closed exactly once, and the item names the writer the audit names.
#[test]
fn rust_and_python_closing_the_same_dms_close_each_once() {
    let q = q();
    let ids: Vec<String> = (1..=60).map(id).collect();
    for i in &ids {
        q.put(i, "delivered", "please review #628 now");
    }
    let mut py = Py::start(&q.roots);
    py.send(&ids);
    for i in ids.iter().rev() {
        let _ = close_with(
            &q.roots,
            &q.req(i, "review #628", None, Some("rust")),
            Duration::from_millis(2),
            &|| {},
        );
    }
    let rows = py.finish();
    assert_eq!(rows.len(), ids.len());
    let mut rust = 0;
    for i in &ids {
        let oks = closes_of(&q, i);
        assert_eq!(oks.len(), 1, "{i} closed {} times: {oks:?}", oks.len());
        let by_rust = oks[0]["via"] == "aib-seatops";
        assert_eq!(
            q.item(i)["done_why"],
            if by_rust { "rust" } else { "py" },
            "{i}"
        );
        rust += usize::from(by_rust);
    }
    assert!(rust > 0, "Rust closed nothing");
}

/// Old and new on one fixture: Python `read_done` and `aib-seatops dm close` write the same
/// item, apart from the times.
#[test]
fn python_and_rust_write_the_same_closed_item() {
    let (a, b) = (q(), q());
    for q in [&a, &b] {
        q.put(&id(1), "delivered", "please review #628 now");
    }
    let mut py = Py::start(&a.roots);
    py.send(&[id(1)]);
    let rows = py.finish();
    assert_eq!(rows[0]["ok"], json!(true), "{rows:?}");
    b.close(&id(1), "review #628", None, Some("py"))
        .expect("rust");
    let strip = |mut v: Value| {
        for k in ["done_at", "read_at"] {
            assert!(v[k].as_u64().is_some(), "{k}: {v}");
            v[k] = Value::Null;
        }
        v
    };
    assert_eq!(strip(a.item(&id(1))), strip(b.item(&id(1))));
}

/// The command: closes, refreshes the `aib now` time, and still exits 0 with one line when
/// there is no `aib now` record.
#[test]
fn the_command_closes_and_refreshes_the_status_line() {
    let q = q();
    q.put(&id(1), "delivered", "please review #628 now");
    q.put(&id(2), "delivered", "please review #629 now");
    let run = |i: &str, expect: &str| {
        Command::new(env!("CARGO_BIN_EXE_aib-seatops"))
            .args([
                "dm", "close", "--seat", SEAT, "--id", i, "--rev", "1", "--expect", expect,
                "--why", "done",
            ])
            .env("EZ2_AIB_STATE_ROOT", &q.roots.state)
            .env_remove("EZ2_AIB_HOME")
            .output()
            .expect("run")
    };
    // no aib now record yet
    let out = run(&id(1), "review #628");
    assert_eq!(out.status.code(), Some(0));
    assert!(String::from_utf8_lossy(&out.stdout).starts_with(&format!("CLOSED {}", id(1))));
    assert!(String::from_utf8_lossy(&out.stderr).contains("status line not refreshed"));
    // with a record: only its time moves
    let now_dir = q.roots.state.join("state").join("seat-now");
    std::fs::create_dir_all(&now_dir).expect("mkdir");
    let rec =
        json!({"at": 1_000, "item": "B9", "state": "reply", "steps_done": 1, "steps_total": 5});
    std::fs::write(now_dir.join(format!("{SEAT}.json")), rec.to_string()).expect("w");
    std::fs::write(
        now_dir.join(format!("{SEAT}.line")),
        format!(
            "x | {}\n",
            aib_seatops::nowline::stamp(1_000).expect("stamp")
        ),
    )
    .expect("w");
    let out = run(&id(2), "review #629");
    assert_eq!(
        out.status.code(),
        Some(0),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let got: Value = serde_json::from_str(
        &std::fs::read_to_string(now_dir.join(format!("{SEAT}.json"))).expect("r"),
    )
    .expect("j");
    assert!(got["at"].as_u64().is_some_and(|a| a > 1_000), "{got}");
    assert_eq!(
        (&got["state"], &got["item"]),
        (&json!("reply"), &json!("B9"))
    );
    // refused: rc 2, nothing refreshed
    let out = run(&id(2), "review #629");
    assert_eq!(out.status.code(), Some(2));
}
