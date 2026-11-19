#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! N-workspace wave 2 (b4297; 5d33a CHECKLIST B1 / B3): with this machine's switch on, the
//! built-in page opens a dgrs seat, with the argv aibd builds for it (no more, no less); a
//! brand that has no dgrs mode (codex, safe only) is still refused. Its own binary: the
//! switch is read once when aibd starts.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;
use serde_json::json;

#[tokio::test(flavor = "multi_thread")]
async fn with_the_switch_on_the_page_opens_a_dgrs_seat() {
    std::env::set_var("AIBD_SEAT_INPUT_DGRS", "1");
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&cred),
            Some(
                &json!({"type": "seat_open", "provider": "claude", "mode": "dgrs",
                        "persona_uid": "un0eba", "idem_key": "d-1"})
                .to_string(),
            ),
        )
        .await;
    assert_eq!(r.code, 201, "{}", r.body);
    assert_eq!(r.json["seat"]["mode"], "dgrs", "{}", r.body);
    let seat = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    // E4 (5d33a CHECKLIST-wave3): its own event, with the page that opened it
    let ev = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    let dg: Vec<&serde_json::Value> = ev.json["events"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| e["type"] == "seat.dgrs_opened")
        .collect();
    assert_eq!(dg.len(), 1, "reason: dgrs_open_not_journaled {}", ev.body);
    assert_eq!(dg[0]["payload"]["seat_id"], seat.as_str());
    assert_eq!(dg[0]["payload"]["opened_by"]["page"], "built_in");
    // B3: the page's open goes through the same argv builder as the start-up token's (one
    // code path, seats::open_checked); what the page may send has no field that reaches it
    // (seats_page_open: cwd / cabin / argv refused)
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&cred),
            Some(
                &json!({"type": "seat_open", "provider": "codex", "mode": "dgrs",
                        "persona_uid": "un0eba", "idem_key": "d-2"})
                .to_string(),
            ),
        )
        .await;
    assert_eq!(r.code, 400, "{}", r.body);
    assert_eq!(r.json["reason"], "provider_mode_unsupported", "{}", r.body);
    d.shutdown().await;
}

fn find_dir(root: &std::path::Path, name: &str) -> Option<std::path::PathBuf> {
    for e in std::fs::read_dir(root).ok()?.flatten() {
        let p = e.path();
        if p.is_dir() && !p.is_symlink() {
            if p.file_name().is_some_and(|n| n == name) {
                return Some(p);
            }
            if let Some(f) = find_dir(&p, name) {
                return Some(f);
            }
        }
    }
    None
}

/// The pids whose provider started in `dir` (each writes its argv there at start).
fn started_in(dir: &std::path::Path, want: usize) -> std::collections::BTreeSet<String> {
    let end = std::time::Instant::now() + std::time::Duration::from_secs(10);
    loop {
        let text = std::fs::read_to_string(dir.join("fake-provider-args.log")).unwrap_or_default();
        let pids: std::collections::BTreeSet<String> = text
            .lines()
            .filter_map(|l| l.split_once(' ').map(|(p, _)| p.to_owned()))
            .collect();
        if pids.len() >= want || std::time::Instant::now() > end {
            return pids;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
}

/// 0eed7 STUCK-live-in 15:23 / 5d33a CHECK-preview-work-seat r2: the page opens the reviewer
/// work seat in the author work seat's own folder with `with_seat`, and the event says so; a
/// dgrs seat's folder, a folder turned into a link, and a seat that is not there are refused.
#[tokio::test(flavor = "multi_thread")]
async fn a_work_seat_opens_in_another_work_seats_own_folder_and_no_other() {
    std::env::set_var("AIBD_SEAT_INPUT_DGRS", "1");
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let open = |key: &str, mode: &str, with: Option<&str>| {
        let mut b = json!({"type": "seat_open", "provider": "claude", "mode": mode,
                           "persona_uid": "un0eba", "idem_key": key});
        if let Some(w) = with {
            b["with_seat"] = json!(w);
        }
        b.to_string()
    };
    let post = |b: String| {
        let (d, cred) = (&d, cred.clone());
        async move { d.call("POST", "/api/v1/act", Some(&cred), Some(&b)).await }
    };
    let r = post(open("author", "work", None)).await;
    assert_eq!(r.code, 201, "{}", r.body);
    let author = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let r = post(open("reviewer", "work", Some(&author))).await;
    assert_eq!(r.code, 201, "reason: with_seat_refused {}", r.body);
    let reviewer = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let shared = find_dir(dir.path(), &author).unwrap_or_else(|| panic!("no folder {author}"));
    assert!(
        find_dir(dir.path(), &reviewer).is_none(),
        "reason: reviewer_got_its_own_folder"
    );
    assert_eq!(
        started_in(&shared, 2).len(),
        2,
        "reason: not_both_in_the_folder"
    );
    // the open is journaled with whose folder it shares
    let ev = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    let opened = ev.json["events"]
        .as_array()
        .unwrap()
        .iter()
        .find(|e| e["type"] == "seat.opened" && e["payload"]["seat_id"] == reviewer.as_str())
        .cloned()
        .unwrap_or_else(|| panic!("no seat.opened {}", ev.body));
    assert_eq!(
        opened["payload"]["opened_by"]["with_seat"],
        author.strip_prefix("seat_").unwrap()[..5],
        "reason: with_seat_not_journaled {opened}"
    );
    // 5d33a CHECK-preview-work-seat r2 b: each page work open has its own seat.work_opened
    let work_opened: Vec<String> = ev.json["events"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| e["type"] == "seat.work_opened")
        .filter_map(|e| e["payload"]["seat_id"].as_str().map(str::to_owned))
        .collect();
    assert!(
        work_opened.contains(&author) && work_opened.contains(&reviewer),
        "reason: no_work_opened_event {work_opened:?}"
    );

    // (opened while a session slot is free: the start-up token waits for running)
    // a work seat the start-up token opened in a folder it chose (a work seat's folder is
    // under <data>/seat-work, 5d33a M1 on ecf54f91d; still not that seat's own folder)
    let seat_work = dir.path().join("data").join("seat-work");
    let (chosen, _) = d
        .open_as("claude", "work", &harness::seat_dir(&seat_work, "chosen"))
        .await;
    // a dgrs seat's folder is not shared; nor a safe seat's (only work with work)
    let r = post(open("wild", "dgrs", None)).await;
    assert_eq!(r.code, 201, "{}", r.body);
    let wild = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    // a work seat whose folder is then swapped for a link
    let r = post(open("linked", "work", None)).await;
    assert_eq!(r.code, 201, "{}", r.body);
    let linked = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let lf = find_dir(dir.path(), &linked).unwrap();
    let elsewhere = dir.path().join("elsewhere");
    std::fs::create_dir_all(&elsewhere).unwrap();
    std::fs::rename(&lf, dir.path().join("moved")).unwrap();
    std::os::unix::fs::symlink(&elsewhere, &lf).unwrap();
    let before = d.call("GET", "/api/v1/seats", None, None).await.json["seats"]
        .as_array()
        .map_or(0, Vec::len);
    for (key, with) in [
        ("x4", chosen.as_str()),
        ("x1", wild.as_str()),
        ("x2", linked.as_str()),
        ("x3", "seat_0000000000000000"),
    ] {
        let r = post(open(key, "work", Some(with))).await;
        assert_eq!(r.code, 400, "reason: with_seat_taken {with} {}", r.body);
        assert_eq!(r.json["reason"], "with_seat", "{}", r.body);
    }
    let after = d.call("GET", "/api/v1/seats", None, None).await.json["seats"]
        .as_array()
        .map_or(0, Vec::len);
    assert_eq!(after, before, "reason: refused_open_wrote");
    d.shutdown().await;
}
