#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls on a plain sleep
#![cfg(unix)]

//! aibd §8 8c (DESIGN-merge-6770f-1123 r1 §4b-2, §5) through real requests: a brain merge with
//! `notes: copy` copies the merged brain's notes into the kept brain's `merged-from-<key>/`,
//! `merge_undo` takes back what is unchanged, and a copy that cannot be whole (a cloud
//! placeholder) refuses the run with nothing copied and nothing archived.
//! One test in this binary: it sets AIBD_BRAIN_ROOT for the daemon in this process.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{Daemon, Reply};
use serde_json::{json, Value};
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

struct Yes(Arc<AtomicUsize>);

impl aibd::touch::Presence for Yes {
    fn ask(&self, _reason: &str, _msg: &[u8]) -> Result<(), &'static str> {
        self.0.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}

struct Gate;

impl aibd::owner_ok::WindowGate for Gate {
    fn open(&self) -> Result<(), &'static str> {
        Ok(())
    }
    fn closed(&self, _yes: bool) {}
}

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

async fn answered(n: &AtomicUsize, want: usize) {
    for _ in 0..100 {
        if n.load(Ordering::SeqCst) >= want {
            // the answer is written just after the presence returns
            tokio::time::sleep(std::time::Duration::from_millis(100)).await;
            return;
        }
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }
    panic!("reason: window_never_asked");
}

/// Ask without a ticket (202), let the window answer, send again with the ticket.
async fn with_ticket(d: &Daemon, cred: &str, n: &AtomicUsize, body: Value) -> Reply {
    let before = n.load(Ordering::SeqCst);
    let r = act(d, cred, body.clone()).await;
    assert_eq!(r.code, 202, "reason: not_asked {}", r.body);
    let t = r.json["ticket"].as_str().unwrap().to_owned();
    answered(n, before + 1).await;
    let mut b = body;
    b["ticket"] = json!(t);
    act(d, cred, b).await
}

fn names(d: &Path) -> Vec<String> {
    let mut v: Vec<String> = std::fs::read_dir(d)
        .map(|r| {
            r.flatten()
                .map(|e| e.file_name().to_string_lossy().into_owned())
                .collect()
        })
        .unwrap_or_default();
    v.sort();
    v
}

async fn preview(d: &Daemon, page: &str) -> (String, Value, Value) {
    let r = act(
        d,
        page,
        json!({
            "type": "merge_preview",
            "kind": "brain",
            "groups": [{"keep": "b_one", "merge": ["b_two"], "notes": "copy"}]
        }),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    (
        r.json["plan_sha"].as_str().unwrap().to_owned(),
        r.json["plan"].clone(),
        r.json["notes_check"].clone(),
    )
}

#[tokio::test(flavor = "multi_thread")]
async fn mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run() {
    let n = Arc::new(AtomicUsize::new(0));
    aibd::owner_ok::set_presence(Arc::new(Yes(n.clone())), Arc::new(Gate));

    let dir = Box::leak(Box::new(tempfile::tempdir().unwrap()));
    let lib = dir.path().join("library");
    let one = lib.join("Brains").join("one");
    let two = lib.join("Brains").join("two");
    std::fs::create_dir_all(&one).unwrap();
    std::fs::create_dir_all(two.join("sub")).unwrap();
    std::fs::write(two.join("a.md"), "alpha").unwrap();
    std::fs::write(two.join("sub").join("b.md"), "beta").unwrap();
    std::fs::create_dir_all(lib.join(".ez2").join("index")).unwrap();
    std::fs::write(
        lib.join(".ez2").join("index").join("folder-id-index.json"),
        json!({"items": [
            {"status": "active", "routeKey": "rk-one", "folderPath": "Brains/one"},
            {"status": "active", "routeKey": "rk-two", "folderPath": "Brains/two"}
        ]})
        .to_string(),
    )
    .unwrap();
    std::env::set_var("AIBD_BRAIN_ROOT", &lib);

    let mut d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;
    let data = dir.path().join("data");
    std::fs::write(
        data.join("registry.json"),
        json!({
            "roles": [],
            "brains": [
                {"uid": "b_one", "name": "研究筆記", "route_key": "rk-one"},
                {"uid": "b_two", "name": "研究筆記-2", "route_key": "rk-two"}
            ]
        })
        .to_string(),
    )
    .unwrap();

    // preview counts the notes
    let (sha, plan, check) = preview(&d, &page).await;
    assert_eq!(check["files"], 2, "{check}");
    assert_eq!(check["cloud_placeholders"], 0, "{check}");

    // run (owner window): copied whole into merged-from-rk-two, the merged brain only read
    let r = with_ticket(
        &d,
        &page,
        &n,
        json!({"type": "merge_run", "plan_sha": sha, "plan": plan}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    let batch_id = r.json["batch_id"].as_str().unwrap().to_owned();
    assert_eq!(
        names(&one),
        vec!["merged-from-rk-two"],
        "reason: not_copied"
    );
    assert_eq!(
        std::fs::read_to_string(one.join("merged-from-rk-two").join("sub").join("b.md")).unwrap(),
        "beta"
    );
    assert_eq!(names(&two), vec!["a.md", "sub"], "reason: source_changed");

    // undo: unchanged copies go to aibd's archive, the copy folder is gone
    let r = with_ticket(
        &d,
        &page,
        &n,
        json!({"type": "merge_undo", "batch_id": batch_id}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["state"], "undone", "{}", r.body);
    assert!(
        names(&one).is_empty(),
        "reason: not_taken_back {:?}",
        names(&one)
    );
    assert_eq!(
        std::fs::read_to_string(
            data.join("archive")
                .join("merge-undo")
                .join(&batch_id)
                .join("merged-from-rk-two")
                .join("a.md")
        )
        .unwrap(),
        "alpha"
    );

    // a cloud placeholder: the run is refused, nothing copied, nothing archived
    std::fs::write(two.join(".c.md.icloud"), "x").unwrap();
    let (sha, plan, check) = preview(&d, &page).await;
    assert_eq!(check["cloud_placeholders"], 1, "{check}");
    let r = with_ticket(
        &d,
        &page,
        &n,
        json!({"type": "merge_run", "plan_sha": sha, "plan": plan}),
    )
    .await;
    assert_eq!(r.code, 409, "reason: placeholder_copied {}", r.body);
    assert!(r.body.contains("notes_cloud_placeholder"), "{}", r.body);
    assert!(
        names(&one).is_empty(),
        "reason: half_copy {:?}",
        names(&one)
    );
    let r = act(&d, &page, json!({"type": "merge_find", "kind": "brain"})).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert!(
        r.body.contains("b_two"),
        "reason: archived_anyway {}",
        r.body
    );

    d.shutdown().await;
}
