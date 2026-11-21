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

/// A test's folder, kept until the test binary ends. The daemons of this binary run in one
/// process and share one assignments file: the first daemon's (`assign::set_db` is set once).
/// Reads open that file by path each time, so a test whose folder went away with its
/// `TempDir` took the file from every test still running: their `with_seat` joins found no
/// rows and `GET /assignments` answered nothing (#869 rust macos-latest, job 136152503938:
/// `not_on_the_owners_item []`). Nothing here removes a folder another test may be reading.
fn kept_dir() -> &'static tempfile::TempDir {
    Box::leak(Box::new(tempfile::tempdir().unwrap()))
}

#[tokio::test(flavor = "multi_thread")]
async fn with_the_switch_on_the_page_opens_a_dgrs_seat() {
    std::env::set_var("AIBD_SEAT_INPUT_DGRS", "1");
    let dir = kept_dir();
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
    let dir = kept_dir();
    // room for all six seats to run: past four the later ones only queue, and a seat that is
    // not running is refused before its folder is looked at (#867 SW1 did not bite)
    let mut d = Daemon::start_max(dir.path(), 8).await;
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
    // a safe seat naming a running work seat's folder is refused (k867d SW3: in
    // seats_page_open the seat it named was a safe one, refused by the later mode check too)
    let r = post(open("safe-with", "safe", Some(&author))).await;
    assert_eq!(
        r.code, 400,
        "reason: safe_seat_shared_a_work_folder {}",
        r.body
    );
    assert_eq!(r.json["reason"], "with_seat", "{}", r.body);
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
    // N-webonly-c4 (0eed7 VERIFY-mom-preview-entry r1): the page's open made the folder a git
    // repository with one first commit, once for both seats, before either started
    assert!(
        shared.join(".git").is_dir(),
        "reason: work_folder_not_a_repository"
    );
    let log = std::process::Command::new("git")
        .args(["log", "--format=%H"])
        .current_dir(&shared)
        .output()
        .unwrap();
    assert_eq!(
        String::from_utf8_lossy(&log.stdout).lines().count(),
        1,
        "reason: not_one_first_commit"
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
    // DECISION-workseat-3plus-a9136-1120 3: a third work seat in that folder would write the
    // same tree, index and HEAD: refused, and nothing is opened. By the owner's name it is the
    // cap (409); by the reviewer's (a seat sharing, not owning, the folder) it is refused too
    // (this batch: with_seat names the folder's owner only; the preview resolves it to the
    // owner and gives the same 409)
    let had = d.call("GET", "/api/v1/seats", None, None).await.json["seats"]
        .as_array()
        .map_or(0, Vec::len);
    let r = post(open("third", "work", Some(&author))).await;
    assert_eq!(r.code, 409, "reason: third_writer_let_in {}", r.body);
    assert_eq!(r.json["code"], "shared_folder_writers_full", "{}", r.body);
    let r = post(open("third-b", "work", Some(&reviewer))).await;
    assert!(
        r.code == 409 || r.code == 400,
        "reason: third_writer_let_in {}",
        r.body
    );
    let now = d.call("GET", "/api/v1/seats", None, None).await.json["seats"]
        .as_array()
        .map_or(0, Vec::len);
    assert_eq!(now, had, "reason: refused_seat_opened");

    // (opened while a session slot is free: the start-up token waits for running)
    // a work seat the start-up token opened in a folder it chose (a work seat's folder is
    // under <data>/seat-work, 5d33a M1 on ecf54f91d; still not that seat's own folder)
    let seat_work = aibd::folders::folder(&dir.path().join("data"), "seat-work");
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

/// FINDING-andy-realrun-519d0-1120 1 (Andy 11-20 00:06): the reviewer the page opens in the
/// author's folder (`with_seat`) is put on the author's items as observer, so it is in their
/// threads and posts its review there (an ask_person needs the review in the thread); a seat
/// opened without `with_seat` is not.
#[tokio::test(flavor = "multi_thread")]
async fn a_seat_opened_in_another_seats_folder_is_on_that_seats_items() {
    std::env::set_var("AIBD_SEAT_INPUT_DGRS", "1");
    let dir = kept_dir();
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let post = |b: serde_json::Value| {
        let (d, cred) = (&d, cred.clone());
        async move {
            d.call("POST", "/api/v1/act", Some(&cred), Some(&b.to_string()))
                .await
        }
    };
    let open = |key: &str, with: Option<&str>| {
        let mut b = json!({"type": "seat_open", "provider": "claude", "mode": "work",
                           "persona_uid": "un0eba", "idem_key": key});
        if let Some(w) = with {
            b["with_seat"] = json!(w);
        }
        b
    };
    let r = post(open("author", None)).await;
    assert_eq!(r.code, 201, "{}", r.body);
    let author = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let r = post(
        json!({"type": "assign_add", "node_id": "G3", "seat_id": author,
                        "role": "author"}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    // an item the author only watches is not passed on (5d33a N2: no chain of with_seat opens)
    let r = post(
        json!({"type": "assign_add", "node_id": "G4", "seat_id": author,
                        "role": "observer"}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    let r = post(open("reviewer", Some(&author))).await;
    assert_eq!(r.code, 201, "{}", r.body);
    let reviewer = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let reviewer_session = r.json["session"]["id"].as_str().unwrap().to_string();
    let r = post(open("apart", None)).await;
    assert_eq!(r.code, 201, "{}", r.body);
    let apart = r.json["seat"]["seat_id"].as_str().unwrap().to_string();

    let rows = |seat: String| {
        let d = &d;
        async move {
            d.call(
                "GET",
                &format!("/api/v1/assignments?seat={seat}"),
                None,
                None,
            )
            .await
            .json["assignments"]
                .as_array()
                .cloned()
                .unwrap_or_default()
        }
    };
    let got = rows(reviewer.clone()).await;
    assert_eq!(got.len(), 1, "reason: reviewer_not_on_the_item {got:?}");
    assert_eq!(got[0]["node_id"], "G3");
    assert_eq!(got[0]["role"], "observer", "reason: role_not_observer");
    assert!(
        got[0]["by"]
            .as_str()
            .unwrap_or("")
            .starts_with("with_seat:"),
        "reason: by_not_said {got:?}"
    );
    assert!(
        rows(apart).await.is_empty(),
        "reason: unrelated_seat_put_on"
    );
    // the item's thread: the reviewer posts its review there with its own token
    let r = post(json!({"type": "thread_open", "node": "G3", "title": "G3"})).await;
    assert!(r.code == 200 || r.code == 201, "{}", r.body);
    let t = r.json["thread"]["thread_id"]
        .as_str()
        .or(r.json["thread_id"].as_str())
        .unwrap()
        .to_string();
    let tok = aibd::seat_token::tokens().issue(&reviewer).unwrap();
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&tok),
            Some(
                &json!({"type": "thread_send", "thread_id": t, "body": "審查:要改 1 件"})
                    .to_string(),
            ),
        )
        .await;
    assert_eq!(r.code, 201, "reason: reviewer_not_in_the_thread {}", r.body);
    // the folder holds two writers (MAX_WORK_SEATS_PER_FOLDER; 5d33a all-5858545ef: a third is
    // 409): the reviewer stops before the next one opens in it
    let r = d
        .call(
            "DELETE",
            &format!("/api/v1/sessions/{reviewer_session}"),
            None,
            None,
        )
        .await;
    assert!(r.code < 300, "{}", r.body);
    // 5d33a PREREAD-withseat-nochain N1: a seat opened with_seat a with_seat reviewer is not put
    // on the items the reviewer came to by with_seat (no chain)
    let mut b = open("rev-b", Some(&author));
    b["role"] = json!("reviewer");
    let r = post(b).await;
    assert_eq!(r.code, 201, "{}", r.body);
    let rev_b = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let rev_b_session = r.json["session"]["id"].as_str().unwrap().to_string();
    let got = rows(rev_b.clone()).await;
    assert!(
        got.iter()
            .any(|a| a["node_id"] == "G3" && a["role"] == "reviewer"),
        "reason: reviewer_role_not_joined {got:?}"
    );
    // author and rev_b fill the folder: the chained seat takes a tree of its own off it
    let mut b = open("chained", Some(&rev_b));
    b["own_tree"] = json!(true);
    let r = post(b).await;
    assert_eq!(r.code, 201, "{}", r.body);
    let chained = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let chained_session = r.json["session"]["id"].as_str().unwrap().to_string();
    // naming the reviewer shares the folder's owner (519d0 VERIFY-selftest H1): the new seat is
    // on the author's own items as observer, by the author, never on what rev_b came to by
    // with_seat (no chain: rev_b's reviewer rows are not passed on)
    let got = rows(chained).await;
    assert!(
        !got.is_empty()
            && got.iter().all(|a| a["role"] == "observer"
                && a["by"].as_str().unwrap_or("").ends_with(&author[5..10])),
        "reason: with_seat_chain_spread {got:?}"
    );
    // DECISION-workseat-3plus-a9136-1120 4: opened as the reviewer, it is on the items as
    // reviewer (it reads the author's tree); another role asked this way is still observer
    // the harness runs four sessions at most (7ebca all-45430b4fc: author2 waited for a slot,
    // so rev2's with_seat named a seat not running): rev_b and chained stop first
    for s in [&rev_b_session, &chained_session] {
        let r = d
            .call("DELETE", &format!("/api/v1/sessions/{s}"), None, None)
            .await;
        assert!(r.code < 300, "{}", r.body);
    }
    let r = post(open("author2", None)).await;
    let author2 = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let r = post(
        json!({"type": "assign_add", "node_id": "G4", "seat_id": author2,
                        "role": "author"}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    let mut b = open("rev2", Some(&author2));
    b["role"] = json!("reviewer");
    let r = post(b).await;
    assert_eq!(r.code, 201, "{}", r.body);
    let rev2 = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let got = rows(rev2).await;
    assert_eq!(got.len(), 1, "{got:?}");
    assert_eq!(
        got[0]["role"], "reviewer",
        "reason: review_seat_not_reviewer {got:?}"
    );
    let mut b = open("dep2", Some(&author2));
    b["role"] = json!("deployer");
    b["own_tree"] = json!(true);
    let r = post(b).await;
    assert_eq!(r.code, 201, "{}", r.body);
    let dep2 = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let got = rows(dep2).await;
    assert_eq!(
        got[0]["role"], "observer",
        "reason: other_role_came_by_with_seat {got:?}"
    );

    d.shutdown().await;
}

/// DECISION-workseat-3plus-a9136-1120 1: with `own_tree` a new work seat writes in a git tree of
/// its own off another work seat's folder (its own branch, the folder's HEAD), so the shared
/// folder's two-writer cap does not hold it back; the open records `repo` and `base_head`
/// (the tree read finds the tree by them). `own_tree` without `with_seat` is refused.
#[tokio::test(flavor = "multi_thread")]
async fn a_work_seat_with_its_own_tree_writes_beside_the_folder_not_in_it() {
    std::env::set_var("AIBD_SEAT_INPUT_DGRS", "1");
    let dir = kept_dir();
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let open = |key: &str, with: Option<&str>, own: bool| {
        let mut b = json!({"type": "seat_open", "provider": "claude", "mode": "work",
                           "persona_uid": "un0eba", "idem_key": key});
        if let Some(w) = with {
            b["with_seat"] = json!(w);
        }
        if own {
            b["own_tree"] = json!(true);
        }
        b.to_string()
    };
    let post = |b: String| {
        let (d, cred) = (&d, cred.clone());
        async move { d.call("POST", "/api/v1/act", Some(&cred), Some(&b)).await }
    };
    let r = post(open("a", None, false)).await;
    assert_eq!(r.code, 201, "{}", r.body);
    let author = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let r = post(open("b", Some(&author), false)).await;
    assert_eq!(r.code, 201, "{}", r.body);
    // the folder has its two writers; a third with its own tree still opens
    let r = post(open("c", Some(&author), true)).await;
    assert_eq!(r.code, 201, "reason: own_tree_refused {}", r.body);
    let third = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let third_session = r.json["session"]["id"].as_str().unwrap_or("").to_string();
    let seat_work = aibd::folders::folder(&dir.path().join("data"), "seat-work");
    let tree = std::fs::read_dir(&seat_work)
        .unwrap()
        .flatten()
        .map(|e| e.path().join("repo"))
        .find(|p| p.join(".git").is_dir())
        .unwrap_or_else(|| panic!("reason: no_own_tree under {}", seat_work.display()));
    assert_eq!(
        started_in(&tree, 1).len(),
        1,
        "reason: third_not_in_its_tree"
    );
    let shared = find_dir(dir.path(), &author).unwrap();
    assert_eq!(
        started_in(&shared, 2).len(),
        2,
        "reason: third_wrote_in_the_folder"
    );
    let branch = std::process::Command::new("git")
        .args(["rev-parse", "--abbrev-ref", "HEAD"])
        .current_dir(&tree)
        .output()
        .unwrap();
    assert!(
        String::from_utf8_lossy(&branch.stdout).starts_with("seat/"),
        "reason: not_its_own_branch"
    );
    let ev = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    let opened = ev.json["events"]
        .as_array()
        .unwrap()
        .iter()
        .find(|e| e["type"] == "seat.opened" && e["payload"]["seat_id"] == third.as_str())
        .cloned()
        .unwrap_or_else(|| panic!("no seat.opened {}", ev.body));
    let by = &opened["payload"]["opened_by"];
    assert_eq!(
        by["base_head"].as_str().map(str::len),
        Some(40),
        "reason: no_base_head {opened}"
    );
    assert!(by["repo"].is_string(), "reason: no_repo {opened}");
    // 043dd round 2: the seat list says whose tree it is, its branch and what is not merged
    let seats = d.call("GET", "/api/v1/seats", None, None).await;
    let row = seats.json["seats"]
        .as_array()
        .and_then(|a| a.iter().find(|s| s["seat_id"] == third.as_str()).cloned())
        .unwrap_or_default();
    assert_eq!(
        row["folder_owner"]["how"], "tree",
        "reason: no_folder_owner {row}"
    );
    assert!(
        row["tree"]["branch"]
            .as_str()
            .is_some_and(|b| b.starts_with("seat/")),
        "reason: no_tree_branch {row}"
    );
    assert_eq!(row["tree"]["unmerged"], 0, "{row}");
    // 043dd round 2 合回; 519d0 11-21: open and idle is not busy (nothing committed yet, so
    // nothing is merged); stopped, its commit goes into the author's folder
    let mb = json!({"type": "seat_merge_back", "seat_id": third, "idem_key": "mb-1"});
    let r = post(mb.to_string()).await;
    assert_eq!(r.code, 409, "{}", r.body);
    assert_ne!(
        r.json["code"], "seat_busy",
        "reason: idle_seat_refused {}",
        r.body
    );
    let r = d
        .call(
            "DELETE",
            &format!("/api/v1/sessions/{third_session}"),
            None,
            None,
        )
        .await;
    assert!(r.code < 300, "{} {}", r.code, r.body);
    std::fs::write(tree.join("third.txt"), "3").unwrap();
    let git = |args: &[&str]| {
        let ok = std::process::Command::new("git")
            .args(["-c", "user.name=t", "-c", "user.email=t@example.invalid"])
            .args(["-c", "commit.gpgsign=false"])
            .args(args)
            .current_dir(&tree)
            .status()
            .unwrap()
            .success();
        assert!(ok, "git {args:?}");
    };
    git(&["add", "third.txt"]);
    git(&["commit", "-q", "-m", "third"]);
    let r = post(mb.to_string()).await;
    assert_eq!(r.code, 200, "reason: not_merged_back {}", r.body);
    assert_eq!(r.json["merged"], 1, "{}", r.body);
    assert!(
        shared.join("third.txt").is_file(),
        "reason: not_in_the_authors_folder"
    );
    let r = post(mb.to_string()).await;
    assert_eq!(r.json["code"], "nothing_to_merge", "{}", r.body);
    // 519d0 11-20: the author in a turn (it reads and writes its folder as it goes) is not
    // merged into
    std::fs::write(tree.join("fourth.txt"), "4").unwrap();
    git(&["add", "fourth.txt"]);
    git(&["commit", "-q", "-m", "fourth"]);
    let r = d.act(Some(&cred), &author, "hold").await;
    assert_eq!(r.code, 200, "{}", r.body);
    let r = post(mb.to_string()).await;
    assert_eq!(r.code, 409, "reason: merged_into_a_busy_author {}", r.body);
    assert_eq!(r.json["code"], "author_busy", "{}", r.body);
    assert!(
        !shared.join("fourth.txt").exists(),
        "reason: merged_into_a_busy_author"
    );
    // own_tree names whose folder through with_seat
    let r = post(open("d", None, true)).await;
    assert_eq!(r.code, 400, "reason: own_tree_without_with_seat {}", r.body);
    d.shutdown().await;
}

/// 519d0 VERIFY-selftest 2a3e3cbc H1: naming a seat that itself shares a folder (a reviewer
/// opened with `with_seat`) opens the new seat in that folder's owner's folder, on the owner's
/// items (not the reviewer's), also with `own_tree`; once the owner has stopped, it is refused.
#[tokio::test(flavor = "multi_thread")]
async fn naming_a_seat_that_shares_a_folder_opens_in_its_owners_folder() {
    std::env::set_var("AIBD_SEAT_INPUT_DGRS", "1");
    let dir = kept_dir();
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let post = |b: serde_json::Value| {
        let (d, cred) = (&d, cred.clone());
        async move {
            d.call("POST", "/api/v1/act", Some(&cred), Some(&b.to_string()))
                .await
        }
    };
    let open = |key: &str, with: Option<&str>, own: bool| {
        let mut b = json!({"type": "seat_open", "provider": "claude", "mode": "work",
                           "persona_uid": "un0eba", "idem_key": key});
        if let Some(w) = with {
            b["with_seat"] = json!(w);
        }
        if own {
            b["own_tree"] = json!(true);
        }
        b
    };
    let r = post(open("author", None, false)).await;
    assert_eq!(r.code, 201, "{}", r.body);
    let author = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let author_session = r.json["session"]["id"].as_str().unwrap_or("").to_string();
    let r =
        post(json!({"type": "assign_add", "node_id": "G5", "seat_id": author, "role": "author"}))
            .await;
    assert_eq!(r.code, 200, "{}", r.body);
    let r = post(open("reviewer", Some(&author), false)).await;
    assert_eq!(r.code, 201, "{}", r.body);
    let reviewer = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    // the third names the reviewer (the page's default: the newest seat), with its own tree
    let r = post(open("third", Some(&reviewer), true)).await;
    assert_eq!(r.code, 201, "reason: named_sharer_refused {}", r.body);
    let third = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let rows = d
        .call(
            "GET",
            &format!("/api/v1/assignments?seat={third}"),
            None,
            None,
        )
        .await
        .json["assignments"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    assert_eq!(rows.len(), 1, "reason: not_on_the_owners_item {rows:?}");
    assert_eq!(rows[0]["node_id"], "G5");
    assert!(
        rows[0]["by"]
            .as_str()
            .unwrap_or("")
            .ends_with(&author[5..10]),
        "reason: joined_the_sharer_not_the_owner {rows:?}"
    );
    // the author stops: its folder has no running owner, so naming the reviewer is refused
    assert!(
        !author_session.is_empty(),
        "no session in the open's answer"
    );
    let r = d
        .call(
            "DELETE",
            &format!("/api/v1/sessions/{author_session}"),
            None,
            None,
        )
        .await;
    assert!(r.code < 300, "{} {}", r.code, r.body);
    let r = post(open("fourth", Some(&reviewer), true)).await;
    assert_eq!(
        r.code, 400,
        "reason: stopped_owners_folder_shared {}",
        r.body
    );
    d.shutdown().await;
}

/// 5d33a PREREAD-nx d523a5af L1: restarting a stopped work seat counts its folder's writers as
/// an open does: A and B share, B stops, C opens with A, restarting B is 409
/// shared_folder_writers_full (it would be the third writer of one tree).
#[tokio::test(flavor = "multi_thread")]
async fn restarting_a_seat_into_a_full_folder_is_refused() {
    std::env::set_var("AIBD_SEAT_INPUT_DGRS", "1");
    let dir = kept_dir();
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let post = |b: serde_json::Value| {
        let (d, cred) = (&d, cred.clone());
        async move {
            d.call("POST", "/api/v1/act", Some(&cred), Some(&b.to_string()))
                .await
        }
    };
    let open = |key: &str, with: Option<&str>| {
        let mut b = json!({"type": "seat_open", "provider": "claude", "mode": "work",
                           "persona_uid": "un0eba", "idem_key": key});
        if let Some(w) = with {
            b["with_seat"] = json!(w);
        }
        b
    };
    let r = post(open("a", None)).await;
    assert_eq!(r.code, 201, "{}", r.body);
    let a = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let r = post(open("b", Some(&a))).await;
    assert_eq!(r.code, 201, "{}", r.body);
    let b = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let sb = r.json["session"]["id"].as_str().unwrap().to_string();
    let r = d
        .call("DELETE", &format!("/api/v1/sessions/{sb}"), None, None)
        .await;
    assert!(r.code < 300, "{} {}", r.code, r.body);
    let r = post(open("c", Some(&a))).await;
    assert_eq!(r.code, 201, "{}", r.body);
    let r = post(json!({"type": "seat_restart", "seat_id": b, "idem_key": "re-b"})).await;
    assert_eq!(r.code, 409, "reason: third_writer_restarted {}", r.body);
    assert_eq!(r.json["code"], "shared_folder_writers_full", "{}", r.body);
    d.shutdown().await;
}
