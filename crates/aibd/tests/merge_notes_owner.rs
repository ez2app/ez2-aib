#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls on a plain sleep
#![cfg(unix)]

//! aibd §8 8c (DESIGN-merge-6770f-1123 r1 §4b-2, §5) through real requests: a brain merge with
//! `notes: copy` copies the merged brain's notes into the kept brain's `merged-from-<key>/`,
//! `merge_undo` takes back what is unchanged, and a copy that cannot be whole (a cloud
//! placeholder) refuses the run with nothing copied and nothing archived. Then persona merges
//! for f163c REVIEW-macts: the owner window's words (M1), a plan gone stale (L1), a run that
//! stops midway put back (L2), the switch a seat had before kept by undo (L3), a plan run
//! once (N1).
//! One test in this binary: it sets AIBD_BRAIN_ROOT for the daemon in this process.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{Daemon, Reply};
use serde_json::{json, Value};
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

struct Yes(Arc<AtomicUsize>, Arc<Mutex<Vec<String>>>);

impl aibd::touch::Presence for Yes {
    fn ask(&self, reason: &str, _msg: &[u8]) -> Result<(), &'static str> {
        self.1.lock().unwrap().push(reason.to_owned());
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
    let said = Arc::new(Mutex::new(Vec::new()));
    aibd::owner_ok::set_presence(Arc::new(Yes(n.clone(), said.clone())), Arc::new(Gate));

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
    // M1 (f163c REVIEW-macts): the owner window says what is merged, not a hash
    let last = said.lock().unwrap().last().cloned().unwrap();
    assert!(
        last.contains("第二大腦合併 1 組") && last.contains("封存 30 天"),
        "reason: window_without_words {last}"
    );
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
    // M3: the brain batch's journal had its steps and they are all put back
    let r2 = act(
        &d,
        &page,
        json!({"type": "merge_log", "batch_id": batch_id}),
    )
    .await;
    let steps = r2.json["batches"][0]["steps"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    assert!(
        steps.iter().any(|s| s["op"] == "notes_copy")
            && steps.iter().any(|s| s["op"] == "archive")
            && steps.iter().all(|s| s["state"] == "undone"),
        "reason: journal_empty {}",
        r2.body
    );
    let last = said.lock().unwrap().last().cloned().unwrap();
    assert!(
        last.contains("還原第二大腦合併 1 組"),
        "reason: undo_window_without_words {last}"
    );
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

    // persona merges: L1 a plan gone stale, L3 the switch a seat had before, N1 run once
    let sha64 = "0".repeat(63) + "1";
    let card =
        |uid: &str, name: &str| json!({"uid": uid, "display_name": name, "persona_sha": sha64});
    std::fs::write(
        dir.path().join("cards.json"),
        json!({"schema_version": "aib-cards/v2", "cards": {
            "aaaaa1": card("aaaaa1", "研究員"),
            "aaaaa2": card("aaaaa2", "研究員-2"),
            "bbbbb1": card("bbbbb1", "審稿員"),
            "bbbbb2": card("bbbbb2", "審稿員-2"),
            "ccccc1": card("ccccc1", "記錄員"),
            "ccccc2": card("ccccc2", "記錄員-2"),
        }})
        .to_string(),
    )
    .unwrap();
    let persona = |keep: &str, merge: &str, to: &str| {
        json!({"type": "merge_preview", "kind": "persona",
               "groups": [{"keep": keep, "merge": [merge], "seat_to": {"seat_x": to}}]})
    };
    let r = act(&d, &page, persona("aaaaa1", "aaaaa2", "aaaaa1")).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let run_a =
        json!({"type": "merge_run", "plan_sha": r.json["plan_sha"], "plan": r.json["plan"]});
    let r = act(&d, &page, persona("aaaaa2", "aaaaa1", "aaaaa2")).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let run_c =
        json!({"type": "merge_run", "plan_sha": r.json["plan_sha"], "plan": r.json["plan"]});
    let r = with_ticket(&d, &page, &n, run_a.clone()).await;
    assert_eq!(r.code, 200, "{}", r.body);
    // N1: the same plan is not taken a second time
    let r = act(&d, &page, run_a).await;
    assert_eq!(r.code, 409, "reason: plan_ran_twice {}", r.body);
    // L1: plan C keeps aaaaa2, which plan A archived after C's preview
    let r = with_ticket(&d, &page, &n, run_c).await;
    assert_eq!(r.code, 409, "reason: stale_plan_ran {}", r.body);
    assert!(r.body.contains("plan_stale"), "{}", r.body);

    // L3: seat_x waits for aaaaa1 (plan A); plan B moves it to bbbbb1; undoing B puts A's back
    let r = act(&d, &page, persona("bbbbb1", "bbbbb2", "bbbbb1")).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let run_b =
        json!({"type": "merge_run", "plan_sha": r.json["plan_sha"], "plan": r.json["plan"]});
    let r = with_ticket(&d, &page, &n, run_b).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let batch_b = r.json["batch_id"].as_str().unwrap().to_owned();
    let msgs = aib_store::MessageDb::open(&data.join(aib_store::MESSAGES_FILE)).unwrap();
    let next =
        |db: &aib_store::MessageDb| db.seat_next("seat_x").unwrap().and_then(|r| r.persona_next);
    assert_eq!(next(&msgs).as_deref(), Some("bbbbb1"));
    assert!(msgs.archived("persona", "bbbbb2").unwrap().is_some());
    assert_eq!(msgs.alias_resolve("bbbbb2").unwrap(), "bbbbb1");
    let r = with_ticket(
        &d,
        &page,
        &n,
        json!({"type": "merge_undo", "batch_id": batch_b}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        next(&msgs).as_deref(),
        Some("aaaaa1"),
        "reason: earlier_switch_lost"
    );
    assert!(
        msgs.archived("persona", "bbbbb2").unwrap().is_none(),
        "reason: archive_not_undone"
    );
    assert_eq!(
        msgs.alias_resolve("bbbbb2").unwrap(),
        "bbbbb2",
        "reason: alias_not_undone"
    );

    // L2: a run that stops midway (a seat id the journal refuses, after the archive and alias
    // steps) puts back what it did: ccccc2 is neither archived nor an alias afterwards
    let r = act(
        &d,
        &page,
        json!({"type": "merge_preview", "kind": "persona",
               "groups": [{"keep": "ccccc1", "merge": ["ccccc2"],
                           "seat_to": {"bad seat": "ccccc1"}}]}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    let run_l2 =
        json!({"type": "merge_run", "plan_sha": r.json["plan_sha"], "plan": r.json["plan"]});
    let r = with_ticket(&d, &page, &n, run_l2).await;
    assert_eq!(r.code, 500, "reason: half_run_passed {}", r.body);
    assert!(r.body.contains("seat_next_failed"), "{}", r.body);
    assert!(
        r.body.contains("已做的步驟已倒回"),
        "reason: rollback_words_wrong {}",
        r.body
    );
    assert!(
        msgs.archived("persona", "ccccc2").unwrap().is_none(),
        "reason: half_run_left_archived"
    );
    assert_eq!(msgs.alias_resolve("ccccc2").unwrap(), "ccccc2");

    d.shutdown().await;
}
