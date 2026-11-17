//! S107-product slice 1 acceptance (IFACE-s107-product-a9136-1117 r1 §8): events land in the
//! month file whole, two writers never interleave, a failed write leaves the caller alone, a
//! seat that is not named is null with no cards, and old months go whole.

use aib_ledger::events::{self, Ctx};
use serde_json::{json, Map, Value};
use std::path::Path;
use std::process::Command;

const AT: i64 = 1_794_870_000_000; // 2026-11 (UTC)

fn lines(p: &Path) -> Vec<Value> {
    std::fs::read_to_string(p)
        .expect("read")
        .lines()
        .map(|l| serde_json::from_str(l).expect("whole json line"))
        .collect()
}

fn ev(ctx: &Ctx, n: u64) -> Value {
    let mut m = Map::new();
    m.insert("n".into(), json!(n));
    events::build(ctx, "clean", "deleted", AT, m)
}

/// E1: one event ⇒ one line in `<root>/state/<prog>/events/2026-11.jsonl` with every common field.
#[test]
fn an_event_is_one_whole_line_in_its_month_file() {
    let d = tempfile::tempdir().expect("tmp");
    let ctx = Ctx {
        host: Some("andymini".into()),
        seat: Some("a9136".into()),
        cards: vec!["S107-product".into()],
        status_item: Some("S107-product 片 1".into()),
    };
    let f = events::append(d.path(), "aib-slice", &ev(&ctx, 1), AT).expect("append");
    assert_eq!(
        f,
        d.path()
            .join("state")
            .join("aib-slice")
            .join("events")
            .join("2026-11.jsonl")
    );
    let l = lines(&f);
    assert_eq!(l.len(), 1);
    for k in events::COMMON {
        assert!(l[0].get(k).is_some(), "{k}");
    }
    assert_eq!(l[0]["host"], "andymini");
    assert!(l[0]["id"]
        .as_str()
        .is_some_and(|i| i.starts_with("andymini:")));
}

/// E4: no seat named ⇒ seat null, cards empty, nothing guessed from other seats' files.
#[test]
fn a_seat_that_is_not_named_is_null_with_no_cards() {
    let d = tempfile::tempdir().expect("tmp");
    let now = d.path().join("state").join("seat-now");
    std::fs::create_dir_all(&now).expect("mkdir");
    std::fs::write(now.join("a9136.json"), r#"{"item": "S79-u5 HOST7 A13 改"}"#).expect("w");
    let is = |w: &str| w == "S79-u5";
    let none = Ctx::load(d.path(), None, None, is);
    assert_eq!(
        (none.seat, none.cards.len(), none.status_item),
        (None, 0, None)
    );
    let named = Ctx::load(d.path(), Some("a9136"), Some(r#"{"self": "pro13"}"#), is);
    assert_eq!(named.seat.as_deref(), Some("a9136"));
    assert_eq!(named.cards, ["S79-u5"]);
    assert_eq!(named.host.as_deref(), Some("pro13"));
    // a seat code that would walk out of seat-now is never read
    std::fs::write(
        d.path().join("state").join("a9136.json"),
        r#"{"item": "S79-u5"}"#,
    )
    .expect("w");
    assert_eq!(events::seat_item(d.path(), "../a9136"), None);
}

/// E6: the events folder cannot be made ⇒ append says why, append_or_warn returns quietly.
#[test]
fn a_write_that_fails_leaves_the_caller_alone() {
    let d = tempfile::tempdir().expect("tmp");
    // `state` is a file, so `state/<prog>/events` cannot be made (the same on Unix and Windows)
    std::fs::write(d.path().join("state"), "x").expect("w");
    let e = events::append(d.path(), "aib-drive", &ev(&Ctx::default(), 1), AT);
    assert!(e.as_ref().is_err_and(|e| e.starts_with("mkdir:")), "{e:?}");
    events::append_or_warn(d.path(), "aib-drive", &ev(&Ctx::default(), 1), AT);
}

/// E7 helper: with EVENTS_CHILD_ROOT set, write 100 events there and stop; else nothing.
#[test]
fn writer_child() {
    let Some(root) = std::env::var_os("EVENTS_CHILD_ROOT") else {
        return;
    };
    let ctx = Ctx::default();
    for n in 0..100 {
        events::append(Path::new(&root), "aib-drive", &ev(&ctx, n), AT).expect("append");
    }
}

/// E7: two processes writing 100 events each ⇒ 200 lines, every one whole JSON.
#[test]
fn two_processes_writing_at_once_never_interleave() {
    let d = tempfile::tempdir().expect("tmp");
    let exe = std::env::current_exe().expect("exe");
    let spawn = || {
        Command::new(&exe)
            .args(["--exact", "writer_child", "--test-threads", "1"])
            .env("EVENTS_CHILD_ROOT", d.path())
            .spawn()
            .expect("spawn")
    };
    let (mut a, mut b) = (spawn(), spawn());
    assert!(a.wait().expect("wait").success());
    assert!(b.wait().expect("wait").success());
    let f = events::dir(d.path(), "aib-drive").join("2026-11.jsonl");
    let l = lines(&f);
    assert_eq!(l.len(), 200);
    let mut ids: Vec<&str> = l.iter().filter_map(|v| v["id"].as_str()).collect();
    ids.sort_unstable();
    ids.dedup();
    assert_eq!(ids.len(), 200, "ids repeat");
}

/// E8: a month file goes only once its whole month ended more than keep_days ago.
#[test]
fn old_months_go_whole_and_other_files_stay() {
    let d = tempfile::tempdir().expect("tmp");
    let dir = events::dir(d.path(), "aib-slice");
    std::fs::create_dir_all(&dir).expect("mkdir");
    for f in [
        "2026-07.jsonl",
        "2026-08.jsonl",
        "2026-09.jsonl",
        "events.lock",
        "notes.txt",
    ] {
        std::fs::write(dir.join(f), "{}\n").expect("w");
    }
    // 2026-11-29: 90 days back is 2026-08-31 ⇒ July has ended long enough, August has not
    let now = aib_ledger::envelope::parse_rfc3339_ms("2026-11-29T00:00:00.000Z").expect("t");
    let gone = events::expire(d.path(), "aib-slice", 90, now);
    assert_eq!(gone, vec![(dir.join("2026-07.jsonl"), 3)]);
    for f in ["2026-08.jsonl", "2026-09.jsonl", "events.lock", "notes.txt"] {
        assert!(dir.join(f).exists(), "{f}");
    }
    assert!(events::expire(d.path(), "no-such-prog", 90, now).is_empty());
}
