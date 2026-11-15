//! S79 A3 dm_done: a close appended to `<root>/state/dm-queue/<seat>/audit.jsonl` is found by
//! [`aib_drive::dmdone::Watch`] and written as one dm_done event that observe answers.
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_drive::dmdone::{self, Closed, Watch};
use aib_drive::observe::{self, Clock, RealClock, Wake};
use aib_drive::wake;

use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::Duration;

const ID1: &str = "q_0123456789abcdef";
const ID2: &str = "q_fedcba9876543210";

fn root(tag: &str, with_drive: bool) -> PathBuf {
    let d = std::env::temp_dir().join(format!("aib-drive-dmdone-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(d.join("state").join("dm-queue")).unwrap();
    if with_drive {
        std::fs::create_dir_all(d.join("drive")).unwrap();
    }
    d
}

fn audit(root: &Path, seat: &str) -> PathBuf {
    let dir = root.join("state").join("dm-queue").join(seat);
    std::fs::create_dir_all(&dir).unwrap();
    dir.join("audit.jsonl")
}

fn append(path: &Path, text: &str) {
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .unwrap();
    f.write_all(text.as_bytes()).unwrap();
}

/// One audit line as 1.x dm_queue.py writes it.
fn line(id: &str, from: &str, to: &str, ok: bool) -> String {
    format!(
        "{{\"actor\": \"seat:b433b\", \"from\": \"{from}\", \"id\": \"{id}\", \"ok\": {ok}, \"reason\": \"ok\", \"rev\": 1, \"to\": \"{to}\", \"ts\": 1794700186}}\n"
    )
}

fn closed(seat: &str, id: &str) -> Closed {
    Closed {
        seat: seat.to_owned(),
        id: id.to_owned(),
    }
}

#[test]
fn only_a_successful_transition_to_done_with_a_dm_id_is_a_close() {
    assert_eq!(
        dmdone::closed_in("b433b", &line(ID1, "read", "done", true)),
        Some(closed("b433b", ID1))
    );
    // the refused second close (state already done) is not a close
    assert_eq!(
        dmdone::closed_in("b433b", &line(ID1, "done", "done", false)),
        None
    );
    assert_eq!(
        dmdone::closed_in("b433b", &line(ID1, "delivered", "read", true)),
        None
    );
    assert_eq!(
        dmdone::closed_in("b433b", &line("q_short", "read", "done", true)),
        None
    );
    assert_eq!(
        dmdone::closed_in("b433b", &line("../x/q_01234567", "read", "done", true)),
        None
    );
    assert_eq!(dmdone::closed_in("b433b", "not json"), None);
    assert_eq!(closed("b433b", ID1).reference(), format!("dm/b433b/{ID1}"));
}

#[test]
fn a_cut_last_line_is_read_again_next_time_and_whole_lines_move_the_offset() {
    let whole = line(ID1, "read", "done", true);
    let cut = &line(ID2, "read", "done", true)[..20];
    let bytes = format!("{whole}{cut}");
    let (next, got) = dmdone::closes_in("b433b", 100, bytes.as_bytes());
    assert_eq!(got, vec![closed("b433b", ID1)]);
    assert_eq!(next, 100 + whole.len() as u64);
    assert_eq!(dmdone::closes_in("b433b", 7, cut.as_bytes()), (7, vec![]));
}

#[test]
fn closes_from_before_the_first_look_are_not_events_later_ones_are() {
    let r = root("first", true);
    let a = audit(&r, "b433b");
    append(&a, &line(ID1, "read", "done", true));
    let mut w = Watch::new(&r);
    assert_eq!(w.scan(), vec![]);
    let end = std::fs::metadata(&a).unwrap().len();
    assert_eq!(w.offset("b433b"), Some(end));
    append(&a, &line(ID2, "delivered", "read", true));
    append(&a, &line(ID2, "read", "done", true));
    assert_eq!(w.scan(), vec![closed("b433b", ID2)]);
    assert_eq!(w.scan(), vec![], "a close is found once");
    // the offsets survive a new watch (observe restarted)
    let offsets = std::fs::read_to_string(r.join("drive").join(dmdone::OFFSETS_FILE)).unwrap();
    assert!(offsets.contains("b433b"), "{offsets}");
    let mut again = Watch::new(&r);
    assert_eq!(again.scan(), vec![]);
    append(&a, &line(ID1, "read", "done", true));
    assert_eq!(again.scan(), vec![closed("b433b", ID1)]);
    let _ = std::fs::remove_dir_all(&r);
}

#[test]
fn a_shorter_file_starts_again_at_its_end_without_events_and_other_names_are_skipped() {
    let r = root("shrink", true);
    let a = audit(&r, "c477a");
    append(&a, &line(ID1, "read", "done", true));
    append(&a, &line(ID2, "read", "done", true));
    let mut w = Watch::new(&r);
    let _ = w.scan();
    std::fs::write(&a, line(ID1, "read", "done", true)).unwrap();
    assert_eq!(w.scan(), vec![]);
    assert_eq!(
        w.offset("c477a"),
        Some(std::fs::metadata(&a).unwrap().len())
    );
    let other = r.join("state").join("dm-queue").join("not-a-seat");
    std::fs::create_dir_all(&other).unwrap();
    append(&other.join("audit.jsonl"), &line(ID1, "read", "done", true));
    let _ = w.scan();
    append(&other.join("audit.jsonl"), &line(ID2, "read", "done", true));
    assert_eq!(w.scan(), vec![]);
    assert_eq!(w.offset("not-a-seat"), None);
    let _ = std::fs::remove_dir_all(&r);
}

#[test]
fn wake_writes_the_newest_close_as_a_dm_done_event_that_observe_answers() {
    let r = root("wake", true);
    let a = audit(&r, "b433b");
    append(&a, "");
    let mut w = Watch::new(&r);
    assert_eq!(w.wake(1_794_700_000), None, "nothing closed");
    append(&a, &line(ID1, "read", "done", true));
    append(&a, &line(ID2, "read", "done", true));
    let ev = w.wake(1_794_700_001).unwrap();
    assert_eq!(ev.kind, "dm_done");
    assert_eq!(ev.by, dmdone::BY);
    assert!(
        !wake::is_own(&ev, "aib-drive"),
        "act must not skip it as its own"
    );
    let file = observe::Paths::under(&r).event;
    assert_eq!(observe::read_event(&file), Some(format!("dm/b433b/{ID2}")));
    let _ = std::fs::remove_dir_all(&r);
}

#[test]
fn without_a_drive_folder_nothing_is_written_and_nothing_is_made() {
    let r = root("nodrive", false);
    let a = audit(&r, "b433b");
    let mut w = Watch::new(&r);
    append(&a, &line(ID1, "read", "done", true));
    assert_eq!(w.wake(1_794_700_000), None);
    assert!(!r.join("drive").exists());
    let _ = std::fs::remove_dir_all(&r);
}

#[test]
fn the_real_clock_wakes_on_a_dm_close_within_its_wait() {
    let r = root("clock", true);
    let a = audit(&r, "b433b");
    append(&a, &line(ID1, "read", "done", true));
    let mut clock = RealClock::new().with_dm_done(&r);
    // closed after the clock started looking: a close, not history
    append(&a, &line(ID2, "read", "done", true));
    let file = observe::Paths::under(&r).event;
    assert_eq!(clock.wait(Duration::from_secs(3), &file), Wake::Event);
    assert_eq!(observe::read_event(&file), Some(format!("dm/b433b/{ID2}")));
    let _ = std::fs::remove_dir_all(&r);
}
