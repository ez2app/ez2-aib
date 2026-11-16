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
