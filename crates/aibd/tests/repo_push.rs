#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! G2 at the door (DESIGN-repo-push-g2): only the built-in page asks for a push, only with the
//! switch on, only for a web/ branch of a registered repo; with no fingerprint to ask, and with
//! a ticket nobody approved, nothing reaches the remote; a seat's token cannot ask at all.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon, Reply};
use serde_json::{json, Value};
use std::path::Path;
use std::process::Command;

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

fn g(dir: &Path, args: &[&str]) -> String {
    let o = Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(["-c", "user.name=t", "-c", "user.email=t@example.com"])
        .args([
            "-c",
            "init.defaultBranch=main",
            "-c",
            "commit.gpgsign=false",
        ])
        .args(args)
        .output()
        .unwrap();
    assert!(o.status.success(), "precondition: git {args:?}");
    String::from_utf8_lossy(&o.stdout).trim().to_owned()
}

#[tokio::test(flavor = "multi_thread")]
async fn a_push_is_asked_by_the_built_in_page_only_and_never_runs_without_the_owner() {
    let dir = tempfile::tempdir().unwrap();
    let root = std::fs::canonicalize(dir.path()).unwrap();
    let (repo, remote) = (root.join("repo"), root.join("remote.git"));
    std::fs::create_dir_all(&repo).unwrap();
    std::fs::create_dir_all(&remote).unwrap();
    g(&remote, &["init", "-q", "--bare", "."]);
    g(&repo, &["init", "-q"]);
    std::fs::write(repo.join("a.txt"), "a").unwrap();
    g(&repo, &["add", "."]);
    g(&repo, &["commit", "-q", "-m", "one"]);
    g(&repo, &["branch", "web/ab12-1120-1"]);

    let mut d = Daemon::start(dir.path()).await;
    let data = dir.path().join("data");
    let all = aibd::repos::Repos {
        repos: vec![aibd::repos::Repo {
            name: "demo".into(),
            path: repo.to_string_lossy().into_owned(),
            // a remote as a real one is written; nothing here ever connects to it
            remote: "git@github.example:o/r.git".into(),
            manifest: aibd::seat_worktree::Manifest::default(),
            added_at: 0,
        }],
    };
    std::fs::write(data.join("repos.json"), serde_json::to_vec(&all).unwrap()).unwrap();
    let page = d.page(true).await;
    let (seat, _) = d.open("safe", &seat_dir(dir.path(), "a")).await;
    let tok = aibd::seat_token::tokens().issue(&seat).unwrap();
    let ask = json!({"type": "repo_push", "repo": "demo", "branch": "web/ab12-1120-1"});
    let why = |r: &Reply| r.json["reason"].as_str().map(str::to_owned);

    // off
    let r = act(&d, &page, ask.clone()).await;
    assert_eq!(
        (r.code, why(&r).as_deref()),
        (403, Some("repo_push_off")),
        "{}",
        r.body
    );
    std::fs::write(data.join("bridge.json"), r#"{"repo_push": true}"#).unwrap();

    // what the page may not name
    for (body, code, reason) in [
        (
            json!({"type": "repo_push", "repo": "demo", "branch": "main"}),
            400,
            "push_branch",
        ),
        (
            json!({"type": "repo_push", "repo": "demo", "branch": "web/ab12-1120-2"}),
            404,
            "push_branch_unknown",
        ),
        (
            json!({"type": "repo_push", "repo": "nope", "branch": "web/ab12-1120-1"}),
            404,
            "push_repo_unknown",
        ),
        (
            json!({"type": "repo_push", "repo": "demo", "branch": "web/ab12-1120-1", "sha": "1111111111111111111111111111111111111111"}),
            400,
            "push_fields",
        ),
        (
            json!({"type": "repo_push", "repo": "demo", "branch": "web/ab12-1120-1", "remote": "git@evil.example:o/r.git"}),
            400,
            "push_fields",
        ),
        (
            json!({"type": "repo_push", "repo": "demo", "branch": "web/ab12-1120-1", "force": true}),
            400,
            "push_fields",
        ),
    ] {
        let r = act(&d, &page, body.clone()).await;
        assert_eq!(
            (r.code, why(&r).as_deref()),
            (code, Some(reason)),
            "reason: took_{body} {}",
            r.body
        );
    }

    // no fingerprint can be asked here, and a made-up ticket approves nothing
    let r = act(&d, &page, ask.clone()).await;
    assert_eq!(
        (r.code, why(&r).as_deref()),
        (403, Some("owner_check_unavailable")),
        "{}",
        r.body
    );
    let mut with = ask.clone();
    with["ticket"] = json!("ot_made_up");
    let r = act(&d, &page, with).await;
    assert_eq!(r.code, 403, "reason: a_made_up_ticket_pushed {}", r.body);

    // a seat's token cannot ask
    let r = act(&d, &tok, ask.clone()).await;
    assert!(r.code >= 400, "reason: a_seat_pushed {}", r.body);

    // nothing left the machine, nothing was left behind, the repo was not written
    assert_eq!(
        g(&remote, &["for-each-ref"]),
        "",
        "reason: something_reached_the_remote"
    );
    assert!(
        !data.join("push-scratch").exists()
            || std::fs::read_dir(data.join("push-scratch"))
                .unwrap()
                .count()
                == 0,
        "reason: scratch_left"
    );
    assert_eq!(
        g(&repo, &["remote"]),
        "",
        "reason: remote_added_to_the_repo"
    );
    let ev = d.call("GET", "/api/v1/events?limit=500", None, None).await;
    assert!(
        !ev.json["events"]
            .as_array()
            .unwrap()
            .iter()
            .any(|e| e["type"] == "repo.pushed"),
        "reason: pushed_journaled"
    );
    d.shutdown().await;
}
