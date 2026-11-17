//! S79-own o5 (DECISION-andy-519d0-1116-0530.md §19): `aib-drive round done` writes one line
//! a round, `aib-drive round log` lists them.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
use aib_drive::rounds::*;
use serde_json::{json, Value};
use std::path::Path;
use std::process::Command;

const SEAT: &str = "519d0";
/// 2026-11-16T03:00:00Z (11:00 Taipei).
const T0: i64 = 1_794_798_000_000;
/// A uuid v4 for a round line written by a test.
const EV_ID: &str = "0f1e2d3c-4b5a-4968-8776-655443322110";

fn req<'a>(own: &'a str, why: Option<&'a str>) -> DoneReq<'a> {
    DoneReq {
        seat: SEAT,
        recv: Some("1"),
        sent: Some("2"),
        own: Some(own),
        why,
        dm_in: Some("q_090523d520cc14bc"),
        dm_out: Some("q_ee985af473319b0a,q_20ebe7fefc193bcf"),
        trigger: None,
        event_id: EV_ID,
        at_ms: T0,
    }
}

fn turn(trigger: Option<&'static str>) -> Turn {
    Turn {
        id: format!("{SEAT}-{}", T0 - 60_000),
        start_ms: T0 - 60_000,
        trigger,
    }
}

/// RN1: a round line carries the counts, the own item, the dm ids and who started the
/// turn as the transcript says.
#[test]
fn a_round_line_carries_counts_own_dms_and_the_transcripts_trigger() {
    let v = build_line(
        &req("node:S79-own", None),
        Some(&turn(Some("自動推進"))),
        "",
    )
    .unwrap();
    // aib-ledger envelope v 2: one clock (occurred_at, no at_ms), one stream a turn, sequence 1
    assert_eq!(
        (v["v"].as_i64(), v["ev"].as_str()),
        (Some(2), Some("round"))
    );
    assert_eq!(v["event_id"], EV_ID);
    assert_eq!(v["occurred_at"], "2026-11-16T03:00:00.000Z");
    assert!(v.get("at_ms").is_none() && v.get("kind").is_none(), "{v}");
    assert_eq!(v["aggregate_type"], "drive.rounds");
    assert_eq!(v["aggregate_id"], format!("{SEAT}/{SEAT}-{}", T0 - 60_000));
    assert_eq!(v["sequence"], 1);
    assert_eq!(v["invocation_id"], v["turn_id"]);
    assert_eq!(v["turn_id"], format!("{SEAT}-{}", T0 - 60_000));
    assert_eq!(v["trigger"], "自動推進");
    assert_eq!(v["trigger_src"], "transcript");
    assert_eq!((v["recv"].as_u64(), v["sent"].as_u64()), (Some(1), Some(2)));
    assert_eq!(v["own"], "node:S79-own");
    assert_eq!(
        v["dm_out"],
        json!(["q_ee985af473319b0a", "q_20ebe7fefc193bcf"])
    );
    assert!(v.get("why").is_none());
}

/// RN2: a round with nothing done is written with its why; none without a why is refused.
#[test]
fn a_round_with_nothing_done_needs_a_why_and_is_written() {
    let mut r = req("none", Some("清單空:等 Andy"));
    r.recv = Some("0");
    r.sent = Some("0");
    r.dm_in = None;
    r.dm_out = None;
    let v = build_line(&r, Some(&turn(Some("自動推進"))), "").unwrap();
    assert_eq!(v["own"], "none");
    assert_eq!(v["why"], "清單空:等 Andy");
    assert_eq!(v["dm_in"], json!([]));
    let e = build_line(&req("none", None), None, "").unwrap_err();
    assert!(e.contains("--why"), "{e}");
}

/// RN3: --trigger wins over the transcript; no transcript is unknown; a bad trigger, bad
/// counts and bad dm ids are refused.
#[test]
fn the_trigger_flag_wins_and_shapes_are_checked() {
    let mut r = req("node:X", None);
    r.trigger = Some("真人");
    let v = build_line(&r, Some(&turn(Some("自動推進"))), "").unwrap();
    assert_eq!(
        (v["trigger"].as_str(), v["trigger_src"].as_str()),
        (Some("真人"), Some("flag"))
    );
    let v = build_line(&req("node:X", None), Some(&turn(None)), "").unwrap();
    assert_eq!(
        (v["trigger"].as_str(), v["trigger_src"].as_str()),
        (Some("unknown"), Some("none"))
    );
    let v = build_line(&req("node:X", None), None, "").unwrap();
    assert_eq!(v["turn_id"], "");
    r.trigger = Some("自動找事");
    assert!(build_line(&r, None, "").is_err());
    let mut r = req("node:X", None);
    r.recv = Some("一");
    assert!(build_line(&r, None, "").unwrap_err().contains("--recv"));
    let mut r = req("node:X", None);
    r.sent = None;
    assert!(build_line(&r, None, "").unwrap_err().contains("--sent"));
    let mut r = req("node:X", None);
    r.dm_in = Some("q_090523d520cc14bc,q_ABC");
    assert!(build_line(&r, None, "").unwrap_err().contains("--dm-in"));
    let mut r = req("", None);
    r.own = None;
    assert!(build_line(&r, None, "").unwrap_err().contains("--own"));
}

/// RN4: one line a turn: a second round done in the same turn is refused; another turn
/// or another seat is not.
#[test]
fn a_turn_is_written_once() {
    let first = build_line(&req("node:X", None), Some(&turn(Some("自動推進"))), "").unwrap();
    let ledger = format!("{first}\n");
    let e = build_line(&req("node:Y", None), Some(&turn(Some("自動推進"))), &ledger).unwrap_err();
    assert!(e.contains("已經記過"), "{e}");
    let mut later = turn(Some("自動推進"));
    later.id = format!("{SEAT}-{T0}");
    assert!(build_line(&req("node:Y", None), Some(&later), &ledger).is_ok());
}

/// RN5: the log is one line a round in Taipei time; unknown is listed as 分不出來 and
/// counted; --since cuts; the hand-written ledger (at with +08:00, no at_ms) is read.
#[test]
fn the_log_lists_every_round_and_counts_the_ones_it_cannot_tell() {
    let a = build_line(
        &req("node:S79-own", None),
        Some(&turn(Some("自動推進"))),
        "",
    )
    .unwrap();
    let mut r = req("none", Some("清單空"));
    r.at_ms = T0 + 600_000;
    let b = build_line(&r, Some(&turn(None)), "").unwrap();
    let hand = json!({"at": "2026-11-16T10:46+08:00", "seat": SEAT, "trigger": "自動推進",
                      "recv": 1, "sent": 2, "own": "實查 aibd", "dm_in": [], "dm_out": []});
    let other = json!({"kind": "round", "seat": "a9136", "at_ms": T0, "trigger": "真人"});
    let text = [
        hand.to_string(),
        a.to_string(),
        b.to_string(),
        other.to_string(),
    ]
    .join("\n");
    let rows = rows(&text, SEAT, None);
    assert_eq!(rows.len(), 3);
    let t = render(SEAT, &rows);
    assert!(
        t.contains("11-16 10:46 自動推進 收 1 發 2 · 做:實查 aibd\n"),
        "{t}"
    );
    assert!(
        t.contains("11-16 11:00 自動推進 收 1 發 2 · 做:node:S79-own · dm 入 1 出 2\n"),
        "{t}"
    );
    assert!(
        t.contains("11-16 11:10 分不出來 收 1 發 2 · 做:沒做(清單空)"),
        "{t}"
    );
    assert!(
        t.contains("合計:自動推進 2、真人 0(輸入框來的，含席位來信)、分不出來 1;沒做事的 1 輪"),
        "{t}"
    );
    let since = parse_since("5m", T0 + 600_000).unwrap();
    assert_eq!(rows_len(&text, since), 1);
    assert_eq!(
        rows_len(&text, parse_since("2026-11-16T11:00+08:00", 0).unwrap()),
        2
    );
    assert!(parse_since("yesterday", T0).is_none());
}

fn rows_len(text: &str, since: i64) -> usize {
    rows(text, SEAT, Some(since)).len()
}

/// Times: UTC text, Taipei display, RFC 3339 with an offset and without seconds.
#[test]
fn times_are_read_and_shown() {
    assert_eq!(utc_text(T0), "2026-11-16T03:00:00Z");
    assert_eq!(show_time(T0), "11-16 11:00");
    assert_eq!(parse_time("2026-11-16T11:00+08:00"), Some(T0));
    assert_eq!(parse_time("2026-11-16T11:00:00+08:00"), Some(T0));
    assert_eq!(parse_time("2026-11-16T03:00:00Z"), Some(T0));
    assert_eq!(parse_time("2026-11-15T22:00-05:00"), Some(T0));
    assert_eq!(parse_time("11:00"), None);
}

/// A scratch state root removed when the test ends.
struct Root(std::path::PathBuf);
impl Root {
    fn path(&self) -> &Path {
        &self.0
    }
}
impl Drop for Root {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn run(root: &Path, args: &[&str], now: i64) -> (i32, String, String) {
    let o = Command::new(env!("CARGO_BIN_EXE_aib-drive"))
        .args(args)
        .env("EZ2_AIB_STATE_ROOT", root)
        .env("AIB_DRIVE_NOW_MS", now.to_string())
        .output()
        .unwrap();
    (
        o.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&o.stdout).into_owned(),
        String::from_utf8_lossy(&o.stderr).into_owned(),
    )
}

/// RN6: the program reads the turn start's transcript, appends one line, refuses the
/// second in the same turn, and the log shows it.
#[test]
fn round_done_reads_the_turn_start_transcript_and_round_log_shows_it() {
    let dir = std::env::temp_dir().join(format!("aib-drive-rounds-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    let root = Root(dir);
    let drive = root.path().join("drive");
    std::fs::create_dir_all(&drive).unwrap();
    let tr = root.path().join("t.jsonl");
    std::fs::write(
        &tr,
        format!(
            "{}\n{}\n",
            json!({"type": "user", "isMeta": true, "promptSource": "system", "turnOrigin": "scheduled",
                   "message": {"role": "user", "content": "定時"}}),
            json!({"type": "user", "toolUseResult": {}, "message": {"role": "user",
                   "content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}})
        ),
    )
    .unwrap();
    let start = aib_own_gate::turn_start_line(SEAT, T0 - 60_000, tr.to_str());
    std::fs::write(drive.join("own.jsonl"), format!("{start}\n")).unwrap();
    let args = [
        "round",
        "done",
        "--seat",
        SEAT,
        "--recv",
        "0",
        "--sent",
        "0",
        "--own",
        "none",
        "--why",
        "清單空",
    ];
    let (rc, out, err) = run(root.path(), &args, T0);
    assert_eq!(rc, 0, "{err}");
    let v: Value = serde_json::from_str(out.trim()).unwrap();
    assert_eq!(v["trigger"], "自動推進");
    assert_eq!(v["occurred_at"], "2026-11-16T03:00:00.000Z");
    let (rc, _, err) = run(root.path(), &args, T0 + 1000);
    assert_eq!(rc, 1, "{err}");
    let ledger = std::fs::read_to_string(drive.join("rounds.jsonl")).unwrap();
    assert_eq!(ledger.lines().count(), 1);
    assert!(aib_own_gate::round_logged(
        &aib_own_gate::parse_ledger(&ledger),
        SEAT,
        T0 - 60_000
    ));
    let (rc, out, _) = run(root.path(), &["round", "log", "--seat", SEAT], T0);
    assert_eq!(rc, 0);
    assert!(
        out.contains("11-16 11:00 自動推進 收 0 發 0 · 做:沒做(清單空)"),
        "{out}"
    );
    let (rc, _, _) = run(
        root.path(),
        &["round", "log", "--seat", SEAT, "--since", "soon"],
        T0,
    );
    assert_eq!(rc, 2);
}

/// RN6 (IFACE-ledger-envelope-b433b-1117 r3 §7): a round without a turn start gets its own
/// stream; an event_id that is not uuid-shaped is refused; new ids are v4 and differ; the log
/// and the gate read the v 2 line and the v 1 lines before it alike.
#[test]
fn a_round_line_is_an_envelope_v2_line_in_a_stream_of_its_own() {
    let v = build_line(&req("node:X", None), None, "").unwrap();
    assert_eq!(v["aggregate_id"], format!("{SEAT}/ev:{EV_ID}"));
    assert_eq!(v["sequence"], 1);
    let mut r = req("node:X", None);
    r.event_id = "e1";
    assert!(build_line(&r, None, "").unwrap_err().contains("event_id"));
    let (a, b) = (new_event_id(), new_event_id());
    assert_ne!(a, b);
    assert_eq!((a.len(), &a[14..15], &a[8..9]), (36, "4", "-"), "{a}");
    assert!(
        matches!(&a[19..20], "8" | "9" | "a" | "b"),
        "variant 10: {a}"
    );
    let v1 = json!({"v": 1, "kind": "round", "seat": SEAT, "at_ms": T0 - 3_600_000,
                    "trigger": "真人", "recv": 0, "sent": 0, "own": "x"});
    let v2 = build_line(
        &req("node:S79-own", None),
        Some(&turn(Some("自動推進"))),
        "",
    )
    .unwrap();
    let text = format!("{v1}\n{v2}");
    let got = rows(&text, SEAT, None);
    assert_eq!(got.len(), 2);
    assert_eq!(at_ms(&got[1]), Some(T0));
    assert!(rows(&text, SEAT, Some(T0)).len() == 1);
    assert!(aib_own_gate::round_logged(
        &aib_own_gate::parse_ledger(&text),
        SEAT,
        T0
    ));
    let dup = build_line(&req("node:Y", None), Some(&turn(None)), &text).unwrap_err();
    assert!(dup.contains("已經記過了"), "{dup}");
}
