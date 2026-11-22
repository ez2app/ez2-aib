#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls on a plain sleep
#![cfg(unix)]

//! 指紋批次簽 B1 (DESIGN-ownerbatch-impl-08de5-1123 r0; f163c wire pre 7c7822a6 L1, list r0
//! L3): collecting a list writes nothing and opens no window; one yes does every item, each sent
//! again by aibd itself; with the pairing gone by then, every item fails and nothing is done.
//! Its own binary: the owner (a fake presence that says yes and counts), its gate and the
//! pairing it reads are set once, before aibd starts.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Arc;

/// Says yes to every window, and counts them.
struct Yes(Arc<AtomicUsize>);

impl aibd::touch::Presence for Yes {
    fn ask(&self, _reason: &str, _msg: &[u8]) -> Result<(), &'static str> {
        self.0.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}

/// Windows open only while `OPEN`; the page is still paired only while `PAIRED`.
static OPEN: AtomicBool = AtomicBool::new(false);
static PAIRED: AtomicBool = AtomicBool::new(true);

struct Gate;

impl aibd::owner_ok::WindowGate for Gate {
    fn page_now(&self, digest: [u8; 32]) -> Option<aibd::pairing::PageAccess> {
        PAIRED
            .load(Ordering::SeqCst)
            .then(|| aibd::pairing::PageAccess {
                digest,
                confirmed: true,
                user_page: false,
                carried: false,
            })
    }
    fn open(&self) -> Result<(), &'static str> {
        if OPEN.load(Ordering::SeqCst) {
            Ok(())
        } else {
            Err("pair_touch_limited")
        }
    }
    fn closed(&self, _yes: bool) {}
}

fn git_repo(root: &Path, name: &str) -> PathBuf {
    let repo = root.join(name);
    std::fs::create_dir_all(&repo).unwrap();
    std::fs::write(repo.join("a.txt"), name).unwrap();
    for args in [
        &["init", "-q"][..],
        &["add", "a.txt"][..],
        &[
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t.invalid",
            "commit",
            "-q",
            "-m",
            "a",
        ][..],
    ] {
        let ok = std::process::Command::new("git")
            .args(args)
            .current_dir(&repo)
            .status()
            .unwrap();
        assert!(ok.success());
    }
    repo
}

/// Every path under aibd's folders (names only: a database's size moves on its own).
fn tree(root: &Path) -> Vec<String> {
    let mut out = Vec::new();
    let mut dirs = vec![root.to_path_buf()];
    while let Some(d) = dirs.pop() {
        for e in std::fs::read_dir(&d).unwrap().flatten() {
            let p = e.path();
            let rel = p.strip_prefix(root).unwrap().to_string_lossy().into_owned();
            if rel.starts_with("repo-") || rel.ends_with("-wal") || rel.ends_with("-shm") {
                continue;
            }
            if p.is_dir() {
                dirs.push(p);
            }
            out.push(rel);
        }
    }
    out.sort();
    out
}

fn registered(data: &Path) -> Vec<String> {
    let v: Value = std::fs::read(data.join("repos.json"))
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or(Value::Null);
    let list = v["repos"].as_array().cloned().unwrap_or_default();
    let mut names: Vec<String> = list
        .iter()
        .filter_map(|r| r["name"].as_str().map(str::to_owned))
        .collect();
    names.sort();
    names
}

#[tokio::test(flavor = "multi_thread")]
async fn one_yes_does_the_list_and_collecting_it_writes_nothing() {
    let n = Arc::new(AtomicUsize::new(0));
    aibd::owner_ok::set_presence(Arc::new(Yes(n.clone())), Arc::new(Gate));
    let dir = Box::leak(Box::new(tempfile::tempdir().unwrap()));
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let post = |b: Value| {
        let (d, cred) = (&d, cred.clone());
        async move {
            d.call("POST", "/api/v1/act", Some(&cred), Some(&b.to_string()))
                .await
        }
    };
    let reg = |name: &str| {
        let path = git_repo(dir.path(), name);
        json!({"type": "repo_register", "body": {"name": name, "path": path.to_string_lossy()}})
    };
    let batch = |items: Vec<Value>, key: &str| json!({"type": "owner_batch", "items": items, "idem_key": key});
    let data = dir.path().join("data");
    let (a, b, c) = (reg("repo-a"), reg("repo-b"), reg("repo-c"));

    // L1: aibd's own hooks folder is made before any ask (as a single act makes it); then
    // collecting the list with no window to open writes nothing and asks nothing
    let r = post(json!({"type": "repo_register", "name": "repo-a",
                        "path": a["body"]["path"]}))
    .await;
    assert_eq!(r.code, 403, "{}", r.body);
    let before = tree(dir.path());
    let r = post(batch(vec![a.clone(), b.clone()], "ob-1")).await;
    assert_eq!(r.code, 403, "reason: no_window_but_not_refused {}", r.body);
    assert_eq!(r.json["reason"], "pair_touch_limited", "{}", r.body);
    assert_eq!(
        n.load(Ordering::SeqCst),
        0,
        "reason: collecting_raised_a_window"
    );
    assert_eq!(tree(dir.path()), before, "reason: collecting_wrote");
    assert!(
        registered(&data).is_empty(),
        "reason: collecting_registered"
    );

    // one window for the list; once it is answered, every item is done by aibd
    OPEN.store(true, Ordering::SeqCst);
    let r = post(batch(vec![a.clone(), b.clone()], "ob-2")).await;
    assert_eq!(r.code, 202, "{}", r.body);
    let t = r.json["ticket"].as_str().unwrap().to_owned();
    let items = ended(&d, &cred, &t, 2).await;
    assert_eq!(n.load(Ordering::SeqCst), 1, "reason: not_one_window");
    assert!(
        items.iter().all(|i| i["state"] == "done"),
        "reason: items_not_done {items:?}"
    );
    assert_eq!(
        registered(&data),
        ["repo-a", "repo-b"],
        "reason: resend_not_done"
    );

    // list L3: the pairing gone by the time aibd sends again ⇒ the item fails, nothing done
    PAIRED.store(false, Ordering::SeqCst);
    let r = post(batch(vec![c], "ob-3")).await;
    assert_eq!(r.code, 202, "{}", r.body);
    let t = r.json["ticket"].as_str().unwrap().to_owned();
    let items = ended(&d, &cred, &t, 1).await;
    assert_eq!(
        items[0]["state"], "failed",
        "reason: gone_pairing_did_it {items:?}"
    );
    assert_eq!(items[0]["reason"], "pairing_gone");
    assert_eq!(
        registered(&data),
        ["repo-a", "repo-b"],
        "reason: gone_pairing_registered"
    );

    // wire pre L2: an item that asks nothing (a seat trusted now) was done while collected; the
    // list stops and says so, never as refused
    PAIRED.store(true, Ordering::SeqCst);
    let (seat, _) = d.open("safe", &harness::seat_dir(dir.path(), "t")).await;
    std::fs::write(data.join("bridge.json"), r#"{"trusted_seats": true}"#).unwrap();
    let file = data.join("trusted").join("seats.json");
    std::fs::create_dir_all(file.parent().unwrap()).unwrap();
    let until = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs()
        + 600;
    std::fs::write(&file, json!({"seats": {seat.clone(): until}}).to_string()).unwrap();
    let trust = json!({"type": "seat_trust", "body": {"seat_id": seat}});
    let windows = n.load(Ordering::SeqCst);
    let r = post(batch(vec![trust], "ob-4")).await;
    assert_eq!(r.code, 409, "reason: done_told_as_refused {}", r.body);
    assert_eq!(
        r.json["reason"], "batch_item_done_without_window",
        "{}",
        r.body
    );
    assert_eq!(r.json["result"]["already"], true, "{}", r.body);
    assert_eq!(
        n.load(Ordering::SeqCst),
        windows,
        "reason: window_for_a_done_item"
    );
    d.shutdown().await;
}

/// The list's items once none is waiting any more.
async fn ended(d: &Daemon, cred: &str, t: &str, want: usize) -> Vec<Value> {
    let path = format!("/api/v1/owner/tickets/{t}");
    for _ in 0..100 {
        let r = d.call("GET", &path, Some(cred), None).await;
        let items = r.json["items"].as_array().cloned().unwrap_or_default();
        if items.len() == want && items.iter().all(|i| i["state"] != "waiting") {
            return items;
        }
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    }
    panic!("reason: list_never_ended");
}
