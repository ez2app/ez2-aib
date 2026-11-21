#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! N-web-worktree (b4297; DESIGN-web-worktree r3 §1.1 §1.2) over HTTP: the built-in page
//! registers a repo and opens a dgrs seat with one only through the owner's fingerprint. Until
//! the fingerprint queue is wired (owner_ok stub) both answer `owner_check_unavailable` and
//! write nothing; a repo with a work seat, with another seat's folder, or a name the registry
//! does not have is refused before any ask. Its own binary: the dgrs switch is read once when
//! aibd starts.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;
use serde_json::json;

fn seats(v: &serde_json::Value) -> usize {
    v["seats"].as_array().map_or(0, Vec::len)
}

#[tokio::test(flavor = "multi_thread")]
async fn a_repo_reaches_a_seat_only_through_the_owners_fingerprint() {
    std::env::set_var("AIBD_SEAT_INPUT_DGRS", "1");
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let post = |b: serde_json::Value| {
        let (d, cred) = (&d, cred.clone());
        async move {
            d.call("POST", "/api/v1/act", Some(&cred), Some(&b.to_string()))
                .await
        }
    };

    // registering: aibd reads the repo, then asks; with no queue it writes nothing
    let repo = dir.path().join("my-repo");
    std::fs::create_dir_all(&repo).unwrap();
    let git = std::process::Command::new("git")
        .args(["init", "-q"])
        .current_dir(&repo)
        .status()
        .unwrap();
    assert!(git.success());
    // one commit, so a copy of it has a head (DESIGN-work-repo-7b81e-1120)
    std::fs::write(repo.join("a.txt"), "a").unwrap();
    for args in [
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
    let r = post(json!({"type": "repo_register", "name": "my-repo",
                        "path": repo.to_string_lossy()}))
    .await;
    assert_eq!(r.code, 403, "reason: register_not_asked {}", r.body);
    assert_eq!(r.json["reason"], "owner_check_unavailable", "{}", r.body);
    let data = dir.path().join("data");
    assert!(
        !data.join("repos.json").exists(),
        "reason: registered_unasked"
    );
    // a ticket the stub never issued approves nothing
    let r = post(json!({"type": "repo_register", "name": "my-repo",
                        "path": repo.to_string_lossy(), "ticket": "t-made-up"}))
    .await;
    assert_eq!(r.code, 403, "{}", r.body);
    assert_eq!(r.json["reason"], "owner_check_not_approved", "{}", r.body);
    assert!(
        !data.join("repos.json").exists(),
        "reason: made_up_ticket_wrote"
    );

    // a registry with that repo (as a fingerprint would have left it): version lists the name
    std::fs::create_dir_all(&data).unwrap();
    // the manifest as registering it reads it (so a work seat's open finds it unchanged)
    let hooks = dir.path().join("fixture-hooks");
    std::fs::create_dir_all(&hooks).unwrap();
    let c = aibd::repos::check("my-repo", repo.to_str().unwrap(), &data, &hooks).unwrap();
    let reg = aibd::repos::Repo {
        name: c.name,
        path: c.top.to_string_lossy().into_owned(),
        remote: c.remote,
        manifest: c.manifest,
        added_at: 1,
    };
    std::fs::write(
        data.join("repos.json"),
        json!({"repos": [serde_json::to_value(&reg).unwrap()]}).to_string(),
    )
    .unwrap();
    let v = d.call("GET", "/api/v1/version", None, None).await;
    assert_eq!(
        v.json["repos"],
        json!([{"name": "my-repo"}]),
        "reason: not_listed {}",
        v.body
    );

    let before = seats(&d.call("GET", "/api/v1/seats", None, None).await.json);
    let open = |key: &str, mode: &str, repo: &str| {
        json!({"type": "seat_open", "provider": "claude", "mode": mode,
               "persona_uid": "un0eba", "idem_key": key, "repo": repo})
    };
    assert_eq!(
        v.json["repo_modes"],
        json!(["dgrs", "work"]),
        "reason: repo_modes {}",
        v.body
    );
    // refused before any ask: a safe seat, an unknown name
    for (key, mode, name) in [("w0", "safe", "my-repo"), ("u1", "dgrs", "not-there")] {
        let r = post(open(key, mode, name)).await;
        assert_eq!(r.code, 400, "reason: repo_not_refused {key} {}", r.body);
        assert_eq!(r.json["reason"], "repo", "{}", r.body);
    }
    // nor in another seat's folder
    let mut with = open("s1", "dgrs", "my-repo");
    with["with_seat"] = json!("seat_0000000000000000");
    let r = post(with).await;
    assert_eq!(r.code, 400, "{}", r.body);
    assert_eq!(
        r.json["reason"], "repo",
        "reason: with_seat_and_repo {}",
        r.body
    );
    // a registered name: the open asks the fingerprint; with no queue, no seat and no tree
    let r = post(open("d1", "dgrs", "my-repo")).await;
    assert_eq!(r.code, 403, "reason: open_not_asked {}", r.body);
    assert_eq!(r.json["reason"], "owner_check_unavailable", "{}", r.body);
    let mut t = open("d2", "dgrs", "my-repo");
    t["ticket"] = json!("t-made-up");
    let r = post(t).await;
    assert_eq!(r.code, 403, "{}", r.body);
    assert_eq!(r.json["reason"], "owner_check_not_approved", "{}", r.body);
    let after = seats(&d.call("GET", "/api/v1/seats", None, None).await.json);
    assert_eq!(after, before, "reason: refused_open_wrote");
    let work = data.join("seat-work");
    let trees = std::fs::read_dir(&work)
        .map(|r| {
            r.flatten()
                .filter(|e| e.file_name().to_string_lossy().starts_with("wt"))
                .count()
        })
        .unwrap_or(0);
    assert_eq!(trees, 0, "reason: tree_opened_unasked");
    // G2 (043dd): the page reads a ticket's state; nothing is wired, so "unavailable"; only
    // the page asks; a word that is not a ticket is refused
    let t = d
        .call("GET", "/api/v1/owner/tickets/t-1", Some(&cred), None)
        .await;
    assert_eq!(t.code, 200, "reason: ticket_route {}", t.body);
    assert_eq!(t.json["state"], "unavailable", "{}", t.body);
    let t = d.call("GET", "/api/v1/owner/tickets/t-1", None, None).await;
    assert_eq!(t.code, 403, "reason: ticket_not_page_only {}", t.body);
    assert_eq!(t.json["code"], "page_only", "{}", t.body);
    let t = d
        .call("GET", "/api/v1/owner/tickets/a.b", Some(&cred), None)
        .await;
    assert_eq!(t.code, 400, "reason: ticket_shape {}", t.body);
    // DESIGN-work-repo-7b81e-1120 (519d0 06:35): a work seat with a registered repo opens
    // without a new ask, in its own copy (a clone under seat-work), and the registered repo's
    // .git gets no worktree
    let r = post(open("w1", "work", "my-repo")).await;
    assert_eq!(r.code, 201, "reason: work_repo_refused {}", r.body);
    let copies: Vec<std::path::PathBuf> = std::fs::read_dir(&work)
        .unwrap()
        .flatten()
        .filter(|e| e.file_name().to_string_lossy().starts_with("wt"))
        .map(|e| e.path())
        .collect();
    assert_eq!(copies.len(), 1, "reason: one_copy {copies:?}");
    let git_dirs = std::fs::read_dir(&copies[0])
        .unwrap()
        .flatten()
        .filter(|e| e.path().join(".git").is_dir())
        .count();
    assert_eq!(git_dirs, 1, "reason: not_its_own_copy");
    assert!(
        !repo.join(".git").join("worktrees").exists(),
        "reason: registered_repo_git_written"
    );
    // N-webonly-c6-merge (IFACE-web-merge r1): the list names the copy's repo; 送回倉 is the
    // built-in page's, refused while the seat runs, and a ticket comes with its plan's head
    let w1 = r.json["seat"]["seat_id"].as_str().unwrap().to_owned();
    let list = d.call("GET", "/api/v1/seats", None, None).await.json;
    let row = list["seats"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["seat_id"] == w1.as_str())
        .unwrap()
        .clone();
    assert_eq!(
        (&row["repo_copy"]["repo"], &row["repo_copy"]["sendable"]),
        (&json!("my-repo"), &json!(true)),
        "reason: repo_copy_not_listed {row}"
    );
    assert_eq!(
        row["repo_copy"]["head"].as_str().map(str::len),
        Some(40),
        "reason: repo_copy_without_head {row}"
    );
    // second slice (sent_trail): nothing sent yet is an empty list; what the seat sent is
    // listed with the step it stands at, and a failed step is the one offered again
    assert_eq!(
        row["repo_copy"]["sent"],
        json!([]),
        "reason: sent_not_listed {row}"
    );
    assert_eq!(row["repo_copy"]["sent_more"], json!(0), "{row}");
    let head = row["repo_copy"]["head"].as_str().unwrap().to_owned();
    let sent = "web/w1-1120-1";
    assert!(aibd::sent_trail::sent(
        &data, &w1, "my-repo", sent, &head, "t0"
    ));
    let step = aibd::sent_trail::STEP_PUSH;
    aibd::sent_trail::failed(&data, "my-repo", sent, step, "push_failed", "t1").unwrap();
    let list = d.call("GET", "/api/v1/seats", None, None).await.json;
    let mine: Vec<serde_json::Value> = list["seats"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s["repo_copy"]["sent"].clone())
        .filter(|v| v.as_array().is_some_and(|a| !a.is_empty()))
        .collect();
    assert_eq!(
        mine,
        [
            json!([{"repo": "my-repo", "branch": sent, "head": head, "state": "sent",
                 "pushed": null, "number": null, "next": "push",
                 "failed": {"step": "push", "why": "push_failed", "at": "t1"}}])
        ],
        "reason: the_trail_is_not_on_its_seat_only"
    );
    let send = json!({"type": "seat_send_back", "seat_id": w1});
    // 519d0 11-21: open and idle (not in a turn) is not busy: the send goes on to read the
    // copy (nothing committed here, so nothing goes and no branch is written, below)
    let r = post(send.clone()).await;
    assert_ne!(
        r.json["code"], "seat_busy",
        "reason: idle_seat_refused {}",
        r.body
    );
    assert_ne!(r.code, 200, "reason: sent_without_a_fingerprint {}", r.body);
    let mut half = send.clone();
    half["ticket"] = json!("t-made-up");
    let r = post(half).await;
    assert_eq!(r.code, 400, "reason: ticket_without_head {}", r.body);
    assert_eq!(r.json["reason"], "head", "{}", r.body);
    let r = post(json!({"type": "seat_send_back", "seat_id": "seat_nobody"})).await;
    assert_eq!(r.code, 404, "reason: unknown_seat {}", r.body);
    // not the page: a call without the page's credential never reaches the act
    let r = d
        .call("POST", "/api/v1/act", None, Some(&send.to_string()))
        .await;
    assert!(
        r.code == 401 || r.code == 403,
        "reason: send_back_without_page {} {}",
        r.code,
        r.body
    );
    assert_eq!(
        std::process::Command::new("git")
            .args(["for-each-ref", "refs/heads/web"])
            .current_dir(&repo)
            .output()
            .unwrap()
            .stdout,
        b"",
        "reason: refused_send_wrote_a_branch"
    );
    // ORDER-webonly-today 12: a named base must be a full commit, and goes with a repo
    let mut b = open("w2", "work", "my-repo");
    b["base"] = json!("abc1234");
    let r = post(b).await;
    assert_eq!(r.code, 400, "{}", r.body);
    assert_eq!(r.json["reason"], "base", "reason: short_base {}", r.body);
    let mut b = json!({"type": "seat_open", "provider": "claude", "mode": "work",
                       "persona_uid": "un0eba", "idem_key": "w3"});
    b["base"] = json!("0".repeat(40));
    let r = post(b).await;
    assert_eq!(r.code, 400, "{}", r.body);
    assert_eq!(
        r.json["reason"], "base",
        "reason: base_without_repo {}",
        r.body
    );
    d.shutdown().await;
}

/// N-web-worktree r3: the tree reads answer only for a seat aibd opened a tree for; a seat in
/// its own plain folder, or one that is not there, has none (404 `no_tree`), so a reader never
/// reaches a folder aibd did not make from a registered repo.
#[tokio::test(flavor = "multi_thread")]
async fn a_seat_without_a_repo_tree_has_nothing_to_read() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let open = json!({"type": "seat_open", "provider": "claude", "mode": "safe",
                      "persona_uid": "un0eba", "idem_key": "plain"});
    let r = d
        .call("POST", "/api/v1/act", Some(&cred), Some(&open.to_string()))
        .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let seat = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    for who in [Some(cred.as_str()), None] {
        for tail in ["tree?path=", "tree/log", "tree/diff"] {
            for s in [seat.as_str(), "seat_0000000000000000", "not-a-seat"] {
                let r = d
                    .call("GET", &format!("/api/v1/seats/{s}/{tail}"), who, None)
                    .await;
                assert_eq!(
                    r.code, 404,
                    "reason: read_without_tree {s} {tail} {}",
                    r.body
                );
                assert_eq!(r.json["reason"], "no_tree", "{}", r.body);
            }
        }
    }
    let ev = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    assert!(
        !ev.body.contains("worktree.read"),
        "reason: refused_read_journaled"
    );
    d.shutdown().await;
}
