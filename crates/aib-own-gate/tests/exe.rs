//! S79-own o-3: the program as the hooks call it (hook JSON on standard input, `--root`).
use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};

const SESSION: &str = "5a920699-5f46-4725-b303-77177e8519d0";

fn run(root: &Path, cmd: &str, hook: &str) -> (i32, String) {
    let mut c = Command::new(env!("CARGO_BIN_EXE_aib-own-gate"))
        .args([cmd, "--root"])
        .arg(root)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn");
    c.stdin
        .take()
        .expect("stdin")
        .write_all(hook.as_bytes())
        .expect("write");
    let o = c.wait_with_output().expect("wait");
    (
        o.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&o.stderr).into_owned(),
    )
}

fn cabin(gated: &str) -> tempfile::TempDir {
    let d = tempfile::tempdir().expect("tempdir");
    let seats = d.path().join("instances/c1/seats");
    std::fs::create_dir_all(&seats).expect("seats");
    std::fs::write(
        seats.join("w1.json"),
        format!("{{\"session_uuid\":\"{SESSION}\"}}"),
    )
    .expect("seat file");
    std::fs::create_dir_all(d.path().join("drive")).expect("drive");
    std::fs::write(d.path().join("drive/own-gate-seats.txt"), gated).expect("gated");
    d
}

fn ledger(root: &Path) -> Vec<serde_json::Value> {
    std::fs::read_to_string(root.join("drive/own.jsonl"))
        .unwrap_or_default()
        .lines()
        .filter_map(|l| serde_json::from_str(l).ok())
        .collect()
}

/// OG1 + OG2: turn-start always exits 0 and writes the turn's start; a gated seat with no
/// progress is held once (exit 2, the commands on standard error), the second try ends.
#[test]
fn a_gated_seat_is_held_once_and_turn_start_never_holds() {
    let d = cabin("519d0\n");
    let hook = format!("{{\"session_id\":\"{SESSION}\",\"transcript_path\":\"/nonexistent\"}}");
    let (rc, _) = run(d.path(), "turn-start", &hook);
    assert_eq!(rc, 0);
    let l = ledger(d.path());
    assert_eq!(l.len(), 1);
    assert_eq!(l[0]["kind"], "turn_start");
    assert_eq!(l[0]["seat"], "519d0");
    let (rc, err) = run(d.path(), "stop", &hook);
    assert_eq!(rc, 2, "{err}");
    assert!(err.contains("aib-drive own --seat 519d0"), "{err}");
    // the commands are o-2's (56cda 11-16 09:4x): --seat before --id, evidence type:ref
    assert!(
        err.contains("aib-drive own done --seat 519d0 --id <id> --evidence <type>:<ref>"),
        "{err}"
    );
    assert!(
        err.contains("aib-drive own none --seat 519d0 --why"),
        "{err}"
    );
    let (rc, _) = run(d.path(), "stop", &hook);
    assert_eq!(rc, 0);
    let kinds: Vec<_> = ledger(d.path())
        .iter()
        .map(|v| v["kind"].as_str().unwrap_or("").to_owned())
        .collect();
    assert_eq!(kinds, ["turn_start", "blocked", "blocked"]);
}

/// A seat not on the list, or a session no seat file names, is never held and nothing is
/// written; bad input exits 0.
#[test]
fn an_ungated_or_unknown_seat_passes_untouched() {
    let d = cabin("a9136\n");
    let hook = format!("{{\"session_id\":\"{SESSION}\"}}");
    assert_eq!(run(d.path(), "turn-start", &hook).0, 0);
    assert_eq!(run(d.path(), "stop", &hook).0, 0);
    assert!(ledger(d.path()).is_empty());
    let d = cabin("519d0\n");
    let other = "{\"session_id\":\"00000000-0000-0000-0000-0000000519d0\"}";
    assert_eq!(run(d.path(), "stop", other).0, 0);
    assert_eq!(run(d.path(), "stop", "not json").0, 0);
    assert_eq!(run(d.path(), "bogus", "{}").0, 0);
    assert!(ledger(d.path()).is_empty());
}

/// OG5 (S79-own o5): a turn a timed prompt started, with its own line but no round line, is
/// held with the round command; once the round line is there it ends. A turn a person
/// typed is not asked for one. The turn start keeps the transcript path.
#[test]
fn a_turn_autopush_started_is_held_until_its_round_line() {
    let d = cabin("519d0\n");
    let tr = d.path().join("t.jsonl");
    let prompt = |origin: &str| {
        serde_json::json!({"type": "user", "promptSource": "system", "turnOrigin": origin,
                           "message": {"role": "user", "content": "x"}})
        .to_string()
    };
    std::fs::write(&tr, format!("{}\n", prompt("scheduled"))).expect("transcript");
    let hook = serde_json::json!({"session_id": SESSION, "transcript_path": tr}).to_string();
    assert_eq!(run(d.path(), "turn-start", &hook).0, 0);
    let l = ledger(d.path());
    assert_eq!(l[0]["transcript"], tr.to_str().unwrap_or(""));
    let turn = l[0]["turn_id"].as_str().unwrap_or("").to_owned();
    let at = l[0]["at_ms"].as_i64().unwrap_or(0);
    let done = serde_json::json!({"v": 1, "at_ms": at + 1, "seat": "519d0", "kind": "done", "turn_id": turn});
    let mut f = std::fs::OpenOptions::new()
        .append(true)
        .open(d.path().join("drive/own.jsonl"))
        .expect("own");
    writeln!(f, "{done}").expect("done line");
    let (rc, err) = run(d.path(), "stop", &hook);
    assert_eq!(rc, 2, "{err}");
    assert!(
        err.contains("aib-drive round done --seat 519d0 --recv"),
        "{err}"
    );
    assert!(
        !err.contains("這回合還沒有一筆可驗的推進"),
        "own is there: {err}"
    );
    let round = serde_json::json!({"v": 1, "kind": "round", "seat": "519d0", "at_ms": at + 2});
    std::fs::write(d.path().join("drive/rounds.jsonl"), format!("{round}\n")).expect("round");
    let (rc, err) = run(d.path(), "turn-start", &hook);
    assert_eq!(rc, 0, "{err}");
    // a new turn: its own line again, the old round line is before it
    let l = ledger(d.path());
    let last = l.last().cloned().unwrap_or_default();
    let (turn, at) = (
        last["turn_id"].as_str().unwrap_or("").to_owned(),
        last["at_ms"].as_i64().unwrap_or(0),
    );
    let mut f = std::fs::OpenOptions::new()
        .append(true)
        .open(d.path().join("drive/own.jsonl"))
        .expect("own");
    writeln!(f, "{}", serde_json::json!({"v": 1, "at_ms": at + 1, "seat": "519d0", "kind": "done", "turn_id": turn}))
        .expect("done line");
    let round = serde_json::json!({"v": 1, "kind": "round", "seat": "519d0", "at_ms": at + 2});
    let mut r = std::fs::OpenOptions::new()
        .append(true)
        .open(d.path().join("drive/rounds.jsonl"))
        .expect("rounds");
    writeln!(r, "{round}").expect("round line");
    assert_eq!(run(d.path(), "stop", &hook).0, 0);
    // a person typed the next one: no round line needed
    std::fs::write(
        &tr,
        format!("{}\n{}\n", prompt("scheduled"), prompt("human")),
    )
    .expect("transcript");
    assert_eq!(run(d.path(), "turn-start", &hook).0, 0);
    let l = ledger(d.path());
    let last = l.last().cloned().unwrap_or_default();
    let mut f = std::fs::OpenOptions::new()
        .append(true)
        .open(d.path().join("drive/own.jsonl"))
        .expect("own");
    writeln!(
        f,
        "{}",
        serde_json::json!({"v": 1, "at_ms": last["at_ms"].as_i64().unwrap_or(0) + 1,
        "seat": "519d0", "kind": "done", "turn_id": last["turn_id"]})
    )
    .expect("done line");
    assert_eq!(run(d.path(), "stop", &hook).0, 0);
}

/// CG1 (S107-loop): a turn that ran a receipt and wrote no card is held once with the aib-plan
/// commands; a turn that wrote one (plan-events by the seat after the turn start) ends.
#[test]
fn a_turn_that_used_a_test_machine_is_held_until_a_card_is_written() {
    let d = cabin("519d0\n");
    let tr = d.path().join("t.jsonl");
    let bash = serde_json::json!({"type": "assistant", "timestamp": "2099-01-01T00:00:00.000Z",
        "message": {"content": [{"type": "tool_use", "name": "Bash",
        "input": {"command": "OWNER=519d0 bash receipt-remote.sh auto r1-aaaaaaaaa ws b.bundle br t"}}]}});
    std::fs::write(&tr, format!("{bash}\n")).expect("transcript");
    let hook = serde_json::json!({"session_id": SESSION, "transcript_path": tr}).to_string();
    let own = |root: &Path| {
        let l = ledger(root);
        let last = l.last().cloned().unwrap_or_default();
        let mut f = std::fs::OpenOptions::new()
            .append(true)
            .open(root.join("drive/own.jsonl"))
            .expect("own");
        writeln!(
            f,
            "{}",
            serde_json::json!({"v": 1, "at_ms": last["at_ms"].as_i64().unwrap_or(0) + 1,
                "seat": "519d0", "kind": "done", "turn_id": last["turn_id"]})
        )
        .expect("done line");
        last["at_ms"].as_i64().unwrap_or(0)
    };
    assert_eq!(run(d.path(), "turn-start", &hook).0, 0);
    own(d.path());
    let (rc, err) = run(d.path(), "stop", &hook);
    assert_eq!(rc, 2, "{err}");
    assert!(
        err.contains("aib-own-gate(S107-loop):這回合用了收據"),
        "{err}"
    );
    assert!(err.contains("aib-plan note <卡號>"), "{err}");
    assert!(
        !err.contains("這回合還沒有一筆可驗的推進"),
        "own is there: {err}"
    );
    let blocked = ledger(d.path()).last().cloned().unwrap_or_default();
    assert_eq!(blocked["missing"], serde_json::json!(["card"]));
    // the next turn writes a card first: it ends on the first try
    assert_eq!(run(d.path(), "turn-start", &hook).0, 0);
    let at = own(d.path());
    let ev = d.path().join("pending/aib2-a63ab-1107/cabin-plan");
    std::fs::create_dir_all(&ev).expect("plan dir");
    let line = serde_json::json!({"ts": at / 1000 + 1, "by": "519d0", "op": "note", "id": "S1"});
    std::fs::write(ev.join("plan-events.jsonl"), format!("{line}\n")).expect("events");
    let (rc, err) = run(d.path(), "stop", &hook);
    assert_eq!(rc, 0, "{err}");
    assert_ne!(
        ledger(d.path()).last().cloned().unwrap_or_default()["kind"],
        "blocked"
    );
}
