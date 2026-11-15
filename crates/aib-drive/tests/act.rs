//! S79 n-3 act: the dry run end to end through `aib-drive act` (its unit tests live in
//! act.rs, in the library since the integration), under a root folder whose
//! name has a space and Chinese in it.

use serde_json::{json, Value};
use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output};

const H: &str = "1b0d079b58bf33909f6b8302e82b543d40043581";
const NOW: i64 = 1_794_700_800_000; // 2026-11-15T00:00:00.000Z

struct World {
    base: PathBuf,
    root: PathBuf,
}

impl World {
    fn new(tag: &str) -> World {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.as_nanos());
        let base = std::env::temp_dir().join(format!(
            "aib-drive-act-cli-{tag}-{}-{nanos}",
            std::process::id()
        ));
        let root = base.join("home 有 空白").join(".ez2-aib");
        fs::create_dir_all(&root).expect("mkdir");
        World { base, root }
    }
    fn dir(&self) -> PathBuf {
        self.root.join("drive")
    }
    fn file(&self, name: &str, body: &str) -> String {
        let p = self.base.join(name);
        fs::write(&p, body).expect("write");
        p.to_string_lossy().into_owned()
    }
    fn run(&self, args: &[&str], now: i64) -> Output {
        Command::new(env!("CARGO_BIN_EXE_aib-drive"))
            .args(args)
            .env("EZ2_AIB_HOME", &self.root)
            .env_remove("EZ2_AIB_STATE_ROOT")
            .env_remove("AIB_DRIVE_BY")
            .env("AIB_DRIVE_NOW_MS", now.to_string())
            .output()
            .expect("run aib-drive-act")
    }
    fn journal(&self) -> Vec<Value> {
        fs::read_to_string(self.dir().join("act-dryrun.jsonl"))
            .unwrap_or_default()
            .lines()
            .map(|l| serde_json::from_str(l).expect("journal json"))
            .collect()
    }
}

impl Drop for World {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.base);
    }
}

fn out(o: &Output) -> String {
    String::from_utf8_lossy(&o.stdout).into_owned()
}

fn next_json() -> String {
    format!(
        r##"{{"schema":"aib-drive.next.v1","as_of":"x","complete":true,"sources":[],
  "rows":[{{"rank":1,"layer":3,"kind":"review","what":"#757 R4","pr":757,"head":"{H}","node":"S59","to":"1ced7",
            "why_him":"交換審查","why_now":"收據綠","unblocks":2,"waited_min":30,"needs":"none","size":"review","data_at":"t"}},
          {{"rank":2,"layer":1,"kind":"merge","what":"#747 可合","pr":747,"to":"f1fae","needs":"none","size":"review"}}],
  "unknown":[],"lost":[]}}"##
    )
}

const SEATS: &str = r#"{"schema":"aib-drive.seats.v1","seats":[
  {"seat":"1ced7","cabin":"dab9","role":"dev","alive":{"v":"yes"},"hands":{"v":"free","free_since_min":12},
   "capacity":{"usage_pct":null,"unread":3,"wip_unpushed":1,"open_prs":1,"on_deck":false},
   "waiting_on":{"v":"receipt","eta_min":20},"dispatchable":"yes","size":"review","first":[],"reasons":[]}]}"#;

fn args<'a>(next: &'a str, seats: &'a str) -> Vec<&'a str> {
    vec![
        "act",
        "--once",
        "--dry-run",
        "--next",
        next,
        "--seats",
        seats,
    ]
}

#[test]
fn the_dry_run_writes_would_lines_and_sends_nothing() {
    let w = World::new("dry");
    let (n, s) = (
        w.file("next.json", &next_json()),
        w.file("seats.json", SEATS),
    );
    let o = w.run(&args(&n, &s), NOW);
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    assert!(
        out(&o).contains("would open_review [關] 派工: 審 #757 R4 → 1ced7"),
        "{}",
        out(&o)
    );
    let j = w.journal();
    assert_eq!(j.len(), 2, "{j:?}");
    assert_eq!(j[0]["kind"], "would");
    assert_eq!(j[0]["first_line"], "派工: 審 #757 R4 → 1ced7");
    assert_eq!(j[0]["keys"][0], format!("open_review:#757:{H}:1ced7"));
    assert_eq!(j[0]["switch_on"], false, "switches default off");
    assert_eq!(j[0]["at"], "2026-11-15T00:00:00.000Z");
    assert_eq!(j[1]["kind"], "round");
    assert_eq!(j[1]["skipped"][0]["why"], "layer 1 is not an act action");
    // the same round again: a dispatch already planned is not planned again (its key is in
    // the journal, the same as with --send); nudges judge by the ledger (see the queue test)
    let o = w.run(&args(&n, &s), NOW + 60_000);
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    assert!(out(&o).starts_with("act dry-run: 0 would"), "{}", out(&o));
    // the lock is released after each run
    assert!(!w.dir().join("act.lock").exists());
}

#[test]
fn only_the_dry_run_exists_and_a_missing_source_is_rc_3() {
    let w = World::new("rc");
    let (n, s) = (
        w.file("next.json", &next_json()),
        w.file("seats.json", SEATS),
    );
    assert_eq!(
        w.run(&["act", "--once", "--next", &n, "--seats", &s], NOW)
            .status
            .code(),
        Some(2)
    );
    assert_eq!(
        w.run(&["act", "--dry-run", "--once"], NOW).status.code(),
        Some(2)
    );
    // --dry-run and --send together: exactly one of them
    assert_eq!(
        w.run(
            &[
                "act",
                "--once",
                "--dry-run",
                "--send",
                "--next",
                &n,
                "--seats",
                &s
            ],
            NOW
        )
        .status
        .code(),
        Some(2)
    );
    let missing = w.base.join("no-such.json").to_string_lossy().into_owned();
    assert_eq!(w.run(&args(&missing, &s), NOW).status.code(), Some(3));
    let bad = w.file("bad.json", "{not json");
    assert_eq!(w.run(&args(&n, &bad), NOW).status.code(), Some(3));
    assert!(w.journal().is_empty(), "nothing written");
}

#[test]
fn a_held_lock_or_a_broken_switch_file_is_rc_5() {
    let w = World::new("lock");
    let (n, s) = (
        w.file("next.json", &next_json()),
        w.file("seats.json", SEATS),
    );
    fs::create_dir_all(w.dir()).expect("mkdir");
    fs::write(w.dir().join("act.lock"), format!("1 {}", NOW + 60_000)).expect("lock");
    let o = w.run(&args(&n, &s), NOW);
    assert_eq!(o.status.code(), Some(5), "{o:?}");
    assert!(w.journal().is_empty());
    fs::remove_file(w.dir().join("act.lock")).expect("rm");
    fs::write(w.dir().join("act.json"), r#"{"dispatch":"yes"}"#).expect("sw");
    assert_eq!(w.run(&args(&n, &s), NOW).status.code(), Some(5));
}

#[test]
fn a_switch_flip_is_recorded_with_who_and_why() {
    let w = World::new("switch");
    let o = w.run(
        &[
            "switch",
            "open_review",
            "on",
            "--by",
            "519d0",
            "--why",
            "對照期達標",
        ],
        NOW,
    );
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    assert_eq!(out(&o), "open_review: off → on\n");
    let sw: Value =
        serde_json::from_str(&fs::read_to_string(w.dir().join("act.json")).expect("act.json"))
            .expect("json");
    assert_eq!(sw["open_review"], true);
    assert_eq!(sw["dispatch"], false);
    let rec = fs::read_to_string(w.dir().join("switches.jsonl")).expect("rec");
    assert!(
        rec.contains("\"by\":\"519d0\"") && rec.contains("對照期達標"),
        "{rec}"
    );
    // the dry run now marks that action as on
    let (n, s) = (
        w.file("next.json", &next_json()),
        w.file("seats.json", SEATS),
    );
    let o = w.run(&args(&n, &s), NOW);
    assert!(out(&o).contains("would open_review [開]"), "{}", out(&o));
    // usage
    assert_eq!(
        w.run(
            &["switch", "merge", "on", "--by", "519d0", "--why", "x"],
            NOW
        )
        .status
        .code(),
        Some(2)
    );
    assert_eq!(
        w.run(&["switch", "nudge", "on", "--by", "519d0"], NOW)
            .status
            .code(),
        Some(2)
    );
    assert_eq!(
        w.run(
            &["switch", "nudge", "on", "--by", "Andy", "--why", "x"],
            NOW
        )
        .status
        .code(),
        Some(2)
    );
}

#[test]
fn the_state_root_comes_first_and_sig_nulls_still_make_a_round() {
    let w = World::new("root");
    let state = w.base.join("另一個 state 根");
    // sig 99cd24c9 writes on_deck / usage_pct / wip_unpushed / open_prs as null today
    let seats = SEATS.replace(
        r#""usage_pct":null,"unread":3,"wip_unpushed":1,"open_prs":1,"on_deck":false"#,
        r#""usage_pct":null,"unread":3,"wip_unpushed":null,"open_prs":null,"on_deck":null"#,
    );
    assert_ne!(seats, SEATS);
    let (n, s) = (
        w.file("next.json", &next_json()),
        w.file("seats.json", &seats),
    );
    let o = Command::new(env!("CARGO_BIN_EXE_aib-drive"))
        .args(args(&n, &s))
        .env("EZ2_AIB_HOME", &w.root)
        .env("EZ2_AIB_STATE_ROOT", &state)
        .env("AIB_DRIVE_NOW_MS", NOW.to_string())
        .output()
        .expect("run aib-drive-act");
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    let text = fs::read_to_string(state.join("drive").join("act-dryrun.jsonl")).expect("journal");
    let j: Vec<Value> = text
        .lines()
        .map(|l| serde_json::from_str(l).expect("json"))
        .collect();
    assert_eq!(j.len(), 2, "{j:?}");
    assert!(j[0]["body"].to_string().contains("on_deck null"), "{j:?}");
    assert_eq!(j[1]["kind"], "round");
    assert!(
        !w.dir().exists(),
        "nothing under EZ2_AIB_HOME when the state root is set"
    );
}

/// A5 (IFACE §5): the event that woke act was written by act itself ⇒ no round, nothing
/// journaled; the same event from another writer makes the round as usual.
#[test]
fn an_event_act_wrote_itself_does_not_start_a_round() {
    let w = World::new("own");
    let (n, s) = (
        w.file("next.json", &next_json()),
        w.file("seats.json", SEATS),
    );
    let ev = |by: &str| {
        format!(r#"{{"id":"e1","kind":"dm_done","ref":"dm/q_1","at":1794700800,"by":"{by}"}}"#)
    };
    let mine = w.file("event-mine", &ev("act"));
    let mut a = args(&n, &s);
    a.extend(["--event", mine.as_str()]);
    let o = w.run(&a, NOW);
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    assert!(
        out(&o).contains("event e1 was written by act itself, no round"),
        "{}",
        out(&o)
    );
    assert!(w.journal().is_empty(), "{:?}", w.journal());

    let theirs = w.file("event-theirs", &ev("1ced7"));
    let mut a = args(&n, &s);
    a.extend(["--event", theirs.as_str()]);
    let o = w.run(&a, NOW);
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    assert_eq!(w.journal().len(), 2, "{:?}", w.journal());

    // --by names act: an event by that name is its own
    let named = w.file("event-named", &ev("drive-a"));
    let mut a = args(&n, &s);
    a.extend(["--event", named.as_str(), "--by", "drive-a"]);
    let o = w.run(&a, NOW + 60_000);
    assert!(out(&o).contains("no round"), "{}", out(&o));
    assert_eq!(w.journal().len(), 2, "{:?}", w.journal());
}

/// 7b81e A5 N2: --by alone (no --event) does nothing, so it is a usage error, not silence.
#[test]
fn by_without_an_event_is_a_usage_error() {
    let w = World::new("byalone");
    let (n, s) = (
        w.file("next.json", &next_json()),
        w.file("seats.json", SEATS),
    );
    let mut a = args(&n, &s);
    a.extend(["--by", "drive-a"]);
    let o = w.run(&a, NOW);
    assert_eq!(o.status.code(), Some(2), "{o:?}");
    assert!(
        String::from_utf8_lossy(&o.stderr).contains("--by goes with --event"),
        "{o:?}"
    );
    assert!(w.journal().is_empty());
}

/// IFACE §1: a next list that is not complete (rc 3) does not drive work — `--send` journals
/// the round and sends none of it (ebb36 D1+C5 asked whether complete = false stops act);
/// only nudges and tellings go out (519d0 11-15). Every switch is off here, so no outside
/// program runs even if the rule were gone.
#[test]
fn send_with_an_incomplete_next_sends_nothing() {
    let w = World::new("incomplete");
    let (n, s) = (
        w.file(
            "next.json",
            &next_json().replace(r#""complete":true"#, r#""complete":false"#),
        ),
        w.file("seats.json", SEATS),
    );
    let o = w.run(
        &["act", "--once", "--send", "--next", &n, "--seats", &s],
        NOW,
    );
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    assert!(
        out(&o).contains("act send: next is not complete, only nudges and tellings go out"),
        "{}",
        out(&o)
    );
    let j = w.journal();
    assert_eq!(
        j.last().map(|v| v["next_complete"].clone()),
        Some(json!(false))
    );
    assert!(!w.dir().join("intents.jsonl").exists());
}

/// 7b81e d3ad28dc M2 + 519d0 11-15: an incomplete next holds back the review dispatch and
/// gives its key back (planned again at once), while the nudge reads only the dm queue and
/// goes out anyway. No `aib` on PATH: the nudge's recheck cannot read the queue, so it is
/// dropped and backed off, and nothing real is sent from a test.
#[test]
fn an_incomplete_next_does_not_eat_the_nudge() {
    let w = World::new("incomplete-nudge");
    for sw in ["nudge", "open_review"] {
        let o = w.run(&["switch", sw, "on", "--by", "043dd", "--why", "test"], NOW);
        assert_eq!(o.status.code(), Some(0), "{o:?}");
    }
    let (n, s, d) = (
        w.file(
            "next.json",
            &next_json().replace(r#""complete":true"#, r#""complete":false"#),
        ),
        w.file("seats.json", SEATS),
        w.file(
            "dm.json",
            r#"[{"id":"q_aaaa0001","to":"1ced7","from":"a9136","state":"queued","queued_min":9}]"#,
        ),
    );
    let empty = w.base.join("no-aib");
    fs::create_dir_all(&empty).expect("empty PATH dir");
    let o = Command::new(env!("CARGO_BIN_EXE_aib-drive"))
        .args([
            "act", "--once", "--send", "--next", &n, "--seats", &s, "--dm", &d,
        ])
        .env("EZ2_AIB_HOME", &w.root)
        .env("PATH", &empty)
        .env_remove("EZ2_AIB_STATE_ROOT")
        .env_remove("AIB_DRIVE_BY")
        .env("AIB_DRIVE_NOW_MS", NOW.to_string())
        .output()
        .expect("run");
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    let text = out(&o);
    assert!(text.contains("would nudge [開] 提醒: "), "{text}");
    assert!(text.contains("would open_review [開]"), "{text}");
    // the nudge went to the ledger (its recheck said no: no queue to read)
    assert!(
        text.contains("send nudge:q_aaaa0001:-:1ced7 → 1ced7"),
        "{text}"
    );
    assert!(!text.contains("send open_review:"), "{text}");
    let retry: Vec<Value> = w
        .journal()
        .into_iter()
        .filter(|v| v["kind"] == "retry")
        .collect();
    let back: Vec<&Value> = retry
        .iter()
        .filter(|v| v["backoff"] == json!(false))
        .collect();
    assert_eq!(back.len(), 1, "{retry:?}");
    assert_eq!(back[0]["key"], format!("open_review:#757:{H}:1ced7"));
    assert!(
        retry
            .iter()
            .any(|v| v["key"] == "nudge:q_aaaa0001:-:1ced7" && v["backoff"] == json!(true)),
        "{retry:?}"
    );
}

/// E10 end to end: work sent before is followed each round, its steps printed one by one;
/// a step that cannot be read (here the queue: no `aib` on PATH) is `?`, never `否`, so
/// nothing is marked. The seat's `aib now` item started after the send ⇒ 開工 是.
#[test]
fn work_sent_before_is_followed_step_by_step() {
    let w = World::new("follow");
    let sent = NOW - 3 * 3_600_000;
    let key = format!("dispatch:S79-x:{H}:1ced7");
    fs::create_dir_all(w.dir()).expect("drive");
    fs::write(
        w.dir().join("intents.jsonl"),
        format!(
            "{}\n{}\n{}\n",
            json!({"v":1,"kind":"intent","key":key,"at_ms":sent,"action":"dispatch","to":"1ced7"}),
            json!({"v":1,"kind":"sending","key":key,"at_ms":sent}),
            json!({"v":1,"kind":"done","key":key,"at_ms":sent,"dm_id":"q_00000001","stage":"delivered"})
        ),
    )
    .expect("ledger");
    let sn = w.root.join("state/seat-now");
    fs::create_dir_all(&sn).expect("seat-now");
    fs::write(
        sn.join("1ced7.json"),
        json!({"item": "接了", "started_at": (sent + 60_000) / 1000}).to_string(),
    )
    .expect("seat-now file");
    let empty = w.base.join("no-aib");
    fs::create_dir_all(&empty).expect("empty PATH dir");
    let (n, s) = (
        w.file("next.json", &next_json()),
        w.file("seats.json", SEATS),
    );
    let o = Command::new(env!("CARGO_BIN_EXE_aib-drive"))
        .args(["act", "--once", "--dry-run", "--next", &n, "--seats", &s])
        .env("EZ2_AIB_HOME", &w.root)
        .env("PATH", &empty)
        .env_remove("EZ2_AIB_STATE_ROOT")
        .env_remove("AIB_DRIVE_BY")
        .env("AIB_DRIVE_NOW_MS", NOW.to_string())
        .output()
        .expect("run");
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    assert!(
        out(&o).contains(&format!(
            "follow {key} → 1ced7: 送達 是 已讀 ? 接單 ? 開工 是 (q_00000001)"
        )),
        "{}",
        out(&o)
    );
    assert!(!w.journal().iter().any(|v| v["kind"] == "mark"));
}

/// 519d0 11-15: with no `--dm`, act reads the live queue `<root>/state/dm-queue` itself,
/// read only. The item is an aib-dm-queue-item/v2 file as dm_queue.py writes it.
#[test]
fn without_dm_act_reads_the_queued_items_of_the_live_queue() {
    let w = World::new("queue");
    let q = w.root.join("state").join("dm-queue");
    let item: Value = serde_json::from_str(include_str!("fixtures/dm-queue-item-v2-queued.json"))
        .expect("fixture");
    // queued 10 minutes at NOW (sent_at in seconds)
    let mut queued = item.clone();
    queued["sent_at"] = json!(NOW / 1000 - 600);
    let mut read = item.clone();
    read["id"] = json!("q_00000000000000aa");
    read["status"] = json!("read");
    read["sent_at"] = json!(NOW / 1000 - 3600);
    let seat = q.join("56cda");
    fs::create_dir_all(&seat).expect("mkdir");
    fs::create_dir_all(q.join("logs")).expect("mkdir");
    fs::write(seat.join("q_0627a36d2f012a16.json"), queued.to_string()).expect("write");
    fs::write(seat.join("q_00000000000000aa.json"), read.to_string()).expect("write");
    // 5d33a 624d4c96 L1: live items with sender_seat null (18 of 28 on this Mac)
    let mut nobody = queued.clone();
    nobody["id"] = json!("q_00000000000000bb");
    nobody["sender_seat"] = Value::Null;
    // an onboarding notice: no seat anywhere (87167 18:0x, f88fa q_b83f22a6)
    nobody["sender_kind"] = json!("unknown");
    nobody["sender_label"] = json!("開席通知");
    // to another seat: two nudges to one seat in one round are one dm (E8)
    nobody["seat"] = json!("a9136");
    let other = q.join("a9136");
    fs::create_dir_all(&other).expect("mkdir");
    fs::write(other.join("q_00000000000000bb.json"), nobody.to_string()).expect("write");
    // 87167 18:0x: 17 provider-launch items carry the seat only in the label
    let mut labelled = queued.clone();
    labelled["id"] = json!("q_00000000000000dd");
    labelled["sender_seat"] = Value::Null;
    labelled["sender_label"] = json!("@043dd(AIB2省熱重構工程師)");
    labelled["seat"] = json!("f88fa");
    let third = q.join("f88fa");
    fs::create_dir_all(&third).expect("mkdir");
    fs::write(third.join("q_00000000000000dd.json"), labelled.to_string()).expect("write");
    // 519d0 11-15 on 5d33a N2: an item a test sent is not nudged
    let mut test_sent = queued.clone();
    test_sent["id"] = json!("q_00000000000000cc");
    test_sent["origin"] = json!("test");
    fs::write(seat.join("q_00000000000000cc.json"), test_sent.to_string()).expect("write");
    fs::write(seat.join("q_broken.json"), "{not json").expect("write");
    fs::write(seat.join("order.json"), r#"{"ids": []}"#).expect("write");
    fs::write(q.join("logs").join("q_x.json"), queued.to_string()).expect("write");
    let (n, s) = (
        w.file("next.json", &next_json()),
        w.file("seats.json", SEATS),
    );
    let o = w.run(&args(&n, &s), NOW);
    assert_eq!(o.status.code(), Some(0), "{o:?}");
    let text = out(&o);
    assert!(
        text.contains("would nudge [關] 提醒: 你有一封還沒收的信 q_0627a36d2f012a16 → 56cda"),
        "{text}"
    );
    assert_eq!(text.matches("would nudge").count(), 3, "{text}");
    let bodies: Vec<String> = w
        .journal()
        .into_iter()
        .filter(|v| v["kind"] == "would" && v["action"] == "nudge")
        .map(|v| v["body"][0].as_str().unwrap_or_default().to_owned())
        .collect();
    assert_eq!(
        bodies,
        [
            "寄件人 519d0,已排隊 10 分",
            "寄件人 不明,已排隊 10 分",
            "寄件人 043dd,已排隊 10 分"
        ],
        "{bodies:?}"
    );
    let err = String::from_utf8_lossy(&o.stderr);
    assert!(
        err.contains("q_broken.json") && err.contains("(skipped)"),
        "{err}"
    );
    // 87167 18:0x: a dry run reads the ledger like a send, so a dry run after a dry run
    // still shows the nudges a real round would send (it once thought them done)
    let again = w.run(&args(&n, &s), NOW + 60_000);
    assert_eq!(again.status.code(), Some(0), "{again:?}");
    assert_eq!(
        out(&again).matches("would nudge").count(),
        3,
        "{}",
        out(&again)
    );
    // the queue is only read
    assert!(seat.join("q_0627a36d2f012a16.json").is_file());
}

/// 87167 18:0x (f88fa q_b83f22a6 backed off for ever, nobody told): a nudge to a seat with
/// no cabin in a seat roster read whole is gone, and the next round tells the sender once;
/// the same with a roster not read whole is only backed off (5d33a 0a39f00a L1). No `aib`
/// on PATH: no cabin fails before any queue read, and nothing real is sent from a test.
#[test]
fn a_nudge_to_a_seat_missing_from_a_whole_roster_is_gone_and_told_once() {
    for (complete, kind) in [(true, "gone"), (false, "retry")] {
        let w = World::new(&format!("no-cabin-{complete}"));
        let o = w.run(
            &["switch", "nudge", "on", "--by", "043dd", "--why", "test"],
            NOW,
        );
        assert_eq!(o.status.code(), Some(0), "{o:?}");
        let roster = SEATS.replacen(
            r#""seats":["#,
            &format!(r#""complete":{complete},"seats":["#),
            1,
        );
        let (n, s, d) = (
            w.file("next.json", &next_json()),
            w.file("seats.json", &roster),
            w.file(
                "dm.json",
                r#"[{"id":"q_b83f22a6","to":"f88fa","from":"","state":"queued","queued_min":9}]"#,
            ),
        );
        let empty = w.base.join("no-aib");
        fs::create_dir_all(&empty).expect("empty PATH dir");
        let send = |now: i64| {
            Command::new(env!("CARGO_BIN_EXE_aib-drive"))
                .args([
                    "act", "--once", "--send", "--next", &n, "--seats", &s, "--dm", &d,
                ])
                .env("EZ2_AIB_HOME", &w.root)
                .env("PATH", &empty)
                .env_remove("EZ2_AIB_STATE_ROOT")
                .env_remove("AIB_DRIVE_BY")
                .env("AIB_DRIVE_NOW_MS", now.to_string())
                .output()
                .expect("run")
        };
        let o = send(NOW);
        assert_eq!(o.status.code(), Some(0), "{o:?}");
        let lines: Vec<Value> = w
            .journal()
            .into_iter()
            .filter(|v| v["key"] == "nudge:q_b83f22a6:-:f88fa" && v["kind"] != "would")
            .collect();
        assert_eq!(lines.len(), 1, "{lines:?}");
        assert_eq!(lines[0]["kind"], kind, "{lines:?}");
        if complete {
            // the round after: the sender is 不明, so only the coordinator is told
            let o = send(NOW + 60_000);
            assert_eq!(o.status.code(), Some(0), "{o:?}");
            let told: Vec<Value> = w
                .journal()
                .into_iter()
                .filter(|v| v["kind"] == "would" && v["action"] == "tell_sender")
                .collect();
            assert_eq!(told.len(), 1, "{told:?}");
            assert_eq!(told[0]["to"], "519d0", "{told:?}");
            let first = told[0]["first_line"].as_str().unwrap_or_default();
            assert!(first.contains("收件席不在"), "{first}");
        }
    }
}
