//! S79 n-3 act: the dry run end to end through `aib-drive act` (its unit tests live in
//! act.rs, in the library since the integration), under a root folder whose
//! name has a space and Chinese in it.

use serde_json::Value;
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
    // the same round again: already done, nothing new
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
