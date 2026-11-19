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
    std::fs::write(
        data.join("repos.json"),
        json!({"repos": [{"name": "my-repo", "path": repo.to_string_lossy(), "remote": "",
                          "manifest": {"files": [], "keys": [], "git_config": ""},
                          "added_at": 1}]})
        .to_string(),
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
    // refused before any ask: a work seat, an unknown name
    for (key, mode, name) in [("w1", "work", "my-repo"), ("u1", "dgrs", "not-there")] {
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
