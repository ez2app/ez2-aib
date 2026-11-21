//! The seat folder held open (5d33a PREREAD-g5-bridge L1 / L2): a seat folder that is a link
//! takes no write, a folder changed into a link after it was opened keeps the write where it
//! was opened, a named pipe under a ledger name does not hang a read, and the frozen Python
//! writer and this one exclude each other on the same locks (20 letters each at once).
#![cfg(unix)]

use std::os::unix::fs::symlink;
use std::path::Path;
use std::process::Command;

use aib_dmq::spec::ITEM_SCHEMA;
use aib_dmq_store::{ItemRead, Store};
use serde_json::{json, Map, Value};

const SEAT: &str = "aaaaa";

fn item(id: &str) -> Map<String, Value> {
    json!({"schema": ITEM_SCHEMA, "id": id, "seat": SEAT, "msg": "x", "status": "queued",
           "rev": 1, "sender_label": "@bbbbb"})
    .as_object()
    .cloned()
    .unwrap_or_default()
}

fn queue(root: &Path) -> std::path::PathBuf {
    root.join("state").join("dm-queue")
}

fn python(script: &Path, root: &Path, msg: &str) -> std::process::Child {
    Command::new("python3")
        .arg(script)
        .args(["enqueue", "--seat", SEAT, "--sender-label", "@bbbbb"])
        .args(["--sender-kind", "seat", "--msg", msg])
        .env_clear()
        .env("PATH", "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin")
        .env("HOME", root)
        .env("EZ2_AIB_STATE_ROOT", root)
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .stdout(std::process::Stdio::null())
        .spawn()
        .expect("python3")
}

fn script() -> std::path::PathBuf {
    let p = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../aib/src/cockpit/dm_queue.py");
    assert!(p.is_file(), "{}", p.display());
    p
}

fn items(dir: &Path) -> usize {
    names(dir)
        .iter()
        .filter(|n| n.starts_with("q_") && n.ends_with(".json"))
        .count()
}

fn names(p: &Path) -> Vec<String> {
    let mut v: Vec<String> = std::fs::read_dir(p)
        .map(|rd| {
            rd.flatten()
                .map(|e| e.file_name().to_string_lossy().into_owned())
                .collect()
        })
        .unwrap_or_default();
    v.sort();
    v
}

#[test]
fn a_seat_folder_that_is_a_link_takes_no_write() {
    let d = tempfile::tempdir().expect("tempdir");
    let s = Store::new(d.path(), d.path());
    let other = d.path().join("elsewhere");
    std::fs::create_dir_all(&other).expect("other");
    std::fs::create_dir_all(queue(d.path())).expect("queue");
    symlink(&other, queue(d.path()).join(SEAT)).expect("link");

    assert!(s.write_item(SEAT, &item("q_00000000000000a1")).is_err());
    assert!(s.order_append(SEAT, "q_00000000000000a1").is_err());
    s.audit(SEAT, &Default::default(), Map::new());
    assert_eq!(s.bump_source_rev(SEAT), None);
    assert!(matches!(
        s.read_item(SEAT, "q_00000000000000a1"),
        Ok(ItemRead::Missing)
    ));
    assert!(names(&other).is_empty(), "{:?}", names(&other));
}

#[test]
fn a_queue_folder_that_is_a_link_takes_no_write() {
    let d = tempfile::tempdir().expect("tempdir");
    let s = Store::new(d.path(), d.path());
    let other = d.path().join("elsewhere");
    std::fs::create_dir_all(other.join(SEAT)).expect("other");
    std::fs::create_dir_all(d.path().join("state")).expect("state");
    symlink(&other, queue(d.path())).expect("link");

    assert!(s.write_item(SEAT, &item("q_00000000000000a1")).is_err());
    assert!(s.order_append(SEAT, "q_00000000000000a1").is_err());
    assert!(names(&other.join(SEAT)).is_empty(), "{:?}", names(&other));
}

#[test]
fn a_folder_changed_into_a_link_after_opening_keeps_the_write_where_it_was_opened() {
    let d = tempfile::tempdir().expect("tempdir");
    let s = Store::new(d.path(), d.path());
    let dir = s.ensure_dir(SEAT).expect("open");
    let q = queue(d.path());
    let moved = q.join("moved");
    let other = d.path().join("elsewhere");
    std::fs::create_dir_all(&other).expect("other");
    std::fs::rename(q.join(SEAT), &moved).expect("move away");
    symlink(&other, q.join(SEAT)).expect("link in its place");

    dir.replace("order.json", b"{}\n").expect("write");
    let mut f = dir.append("audit.jsonl").expect("audit");
    std::io::Write::write_all(&mut f, b"{}\n").expect("line");
    assert!(names(&other).is_empty(), "{:?}", names(&other));
    assert_eq!(names(&moved), vec!["audit.jsonl", "order.json"]);
}

#[test]
fn a_named_pipe_under_an_item_name_does_not_hang_a_read() {
    let d = tempfile::tempdir().expect("tempdir");
    let s = Store::new(d.path(), d.path());
    s.ensure_dir(SEAT).expect("open");
    let fifo = queue(d.path()).join(SEAT).join("q_00000000000000b2.json");
    let ok = Command::new("mkfifo").arg(&fifo).status().expect("mkfifo");
    assert!(ok.success());
    assert!(matches!(
        s.read_item(SEAT, "q_00000000000000b2"),
        Ok(ItemRead::Missing)
    ));
}

/// 20 letters from the frozen Python `dm_queue.py enqueue` and 20 from this writer at the
/// same time: none lost from order.json, every item whole, the source rev +1 per item write
/// and per order write of this writer (Python's order append does not move it).
#[test]
fn the_python_writer_and_this_one_share_the_locks() {
    let script = script();
    let d = tempfile::tempdir().expect("tempdir");
    let s = Store::new(d.path(), d.path());
    s.ensure_dir(SEAT).expect("open");
    let kids: Vec<_> = (0..20)
        .map(|i| python(&script, d.path(), &format!("py {i}")))
        .collect();
    let mut ours = Vec::new();
    for i in 0..20u32 {
        let id = format!("q_{:016x}", 0xa000_0000_u64 + u64::from(i));
        s.write_item(SEAT, &item(&id)).expect("write");
        s.order_append(SEAT, &id).expect("order");
        ours.push(id);
        // spread over the Python writers' run (fsx::pause: the crate's ADR 0005 short wait)
        aib_dmq_store::fsx::pause(std::time::Duration::from_millis(15));
    }
    for mut k in kids {
        assert!(k.wait().expect("wait").success());
    }
    let ids = s.order_ids(SEAT).expect("ids");
    assert_eq!(ids.len(), 40, "{ids:?}");
    let mut uniq = ids.clone();
    uniq.sort();
    uniq.dedup();
    assert_eq!(uniq.len(), 40);
    for id in &ours {
        assert!(ids.contains(id), "{id} lost");
    }
    for id in &ids {
        assert!(
            matches!(s.read_item(SEAT, id), Ok(ItemRead::Item { .. })),
            "{id} not whole"
        );
    }
    assert_eq!(s.source_rev(SEAT), 20 + 40);
}

/// The folder lock this writer takes is the one Python's atomic_json waits on: held here, a
/// Python enqueue writes nothing for 2 s (its own wait is 10 s); let go, it finishes.
#[test]
fn a_held_folder_lock_holds_the_python_writer() {
    let script = script();
    let d = tempfile::tempdir().expect("tempdir");
    let s = Store::new(d.path(), d.path());
    let dir = queue(d.path()).join(SEAT);
    s.ensure_dir(SEAT).expect("open");
    let held = aib_dmq_store::locks::lock(
        &dir,
        SEAT,
        aib_dmq::lock::Level::Directory,
        std::time::Duration::from_secs(1),
    )
    .expect("lock");
    let mut kid = python(&script, d.path(), "held");
    let until = std::time::Instant::now() + std::time::Duration::from_secs(2);
    while std::time::Instant::now() < until {
        assert_eq!(items(&dir), 0, "Python wrote under the held lock");
        assert!(
            kid.try_wait().expect("try_wait").is_none(),
            "Python did not wait"
        );
        aib_dmq_store::fsx::pause(std::time::Duration::from_millis(50));
    }
    drop(held);
    assert!(kid.wait().expect("wait").success());
    assert_eq!(items(&dir), 1);
    assert_eq!(s.order_ids(SEAT).expect("ids").len(), 1);
}
