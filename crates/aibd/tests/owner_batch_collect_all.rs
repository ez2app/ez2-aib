#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls on a plain sleep
#![cfg(unix)]

//! 指紋批次簽 B1 (LIST-ownerbatch-collect-sideeffects-08de5-1123 r1):
//! collecting all 7 batchable acts (`repo_register`, `seat_open` with repo, `seat_trust`,
//! `seat_move`, `seat_send_back`, `repo_push`, `pr_open`) writes nothing under aibd's folders,
//! appends no DB/journal events, and raises no window. What collecting may leave behind is named
//! one by one in [`collect_may_write`] (08de5 JUDGE-ownerbatch-3pts-08de5-1125 2): `seat_send_back`'s
//! plan, which runs before the owner is asked on main too, makes its scratch folder and fetches the
//! seat's commits into the registered repo as objects only. No ref, no file of the work tree.
//! (The `409 batch_item_done_without_window` path is a guard, not a way a listed act goes today:
//! it is held by `owner_batch::tests::an_item_done_without_a_window_is_told_as_done` and knife
//! obw-done, and by `owner_batch_signs`; JUDGE 3.)

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon, Reply};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Arc;

struct Yes(Arc<AtomicUsize>);

impl aibd::touch::Presence for Yes {
    fn ask(&self, _reason: &str, _msg: &[u8]) -> Result<(), &'static str> {
        self.0.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}

static OPEN: AtomicBool = AtomicBool::new(false);

struct Gate;

impl aibd::owner_ok::WindowGate for Gate {
    fn page_now(&self, digest: [u8; 32]) -> Option<aibd::pairing::PageAccess> {
        Some(aibd::pairing::PageAccess {
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

struct FakePr;

impl aibd::pr_open::PrApi for FakePr {
    fn ready(&self, _slug: &str) -> bool {
        true
    }
    fn open(
        &self,
        _slug: &str,
        _head: &str,
        _base: &str,
        _title: &str,
        _body: &str,
        _draft: bool,
    ) -> Result<aibd::pr_open::Opened, aibd::pr_open::PrError> {
        Err(aibd::pr_open::PrError::Failed("must_not_run".into()))
    }
}

fn g(dir: &Path, args: &[&str]) {
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
    assert!(
        o.status.success(),
        "precondition: git {args:?}: {}",
        String::from_utf8_lossy(&o.stderr)
    );
}

fn init_repo(root: &Path, name: &str, with_web_branch: bool) -> PathBuf {
    let repo = root.join(name);
    std::fs::create_dir_all(&repo).unwrap();
    g(&repo, &["init", "-q"]);
    std::fs::write(repo.join("a.txt"), name).unwrap();
    g(&repo, &["add", "."]);
    g(&repo, &["commit", "-q", "-m", "one"]);
    if with_web_branch {
        g(&repo, &["branch", "web/ab12-1120-1"]);
    }
    repo
}

/// aibd's two databases (what they gain is counted as events below), with their -wal / -shm.
const DBS: [&str; 2] = ["data/aibd.sqlite", "data/messages.sqlite"];

/// Every path under `root` with what it holds: a folder, a link, or a file's SHA-256. Left out
/// are [`DBS`], and the test's own stand-in for the AI program writing its two logs in the
/// folders of the seats opened before the count (`stand_ins`): it writes them when it starts,
/// which may be after the first look. Its log in any other folder is a seat that collecting
/// started, and is counted.
fn tree(root: &Path, stand_ins: &[PathBuf]) -> BTreeMap<String, String> {
    let mut out = BTreeMap::new();
    let mut dirs = vec![root.to_path_buf()];
    while let Some(d) = dirs.pop() {
        for e in std::fs::read_dir(&d).unwrap().flatten() {
            let p = e.path();
            let rel = p.strip_prefix(root).unwrap().to_string_lossy().into_owned();
            if DBS.iter().any(|d| {
                rel == *d
                    || rel
                        .strip_prefix(d)
                        .is_some_and(|x| x == "-wal" || x == "-shm")
            }) {
                continue;
            }
            let own_log = matches!(
                p.file_name().and_then(|n| n.to_str()),
                Some("fake-provider.log" | "fake-provider-args.log")
            );
            if own_log
                && p.parent()
                    .and_then(|d| d.canonicalize().ok())
                    .is_some_and(|d| stand_ins.contains(&d))
            {
                continue;
            }
            let meta = std::fs::symlink_metadata(&p).unwrap();
            let what = if meta.file_type().is_symlink() {
                format!("link {}", std::fs::read_link(&p).unwrap().display())
            } else if meta.is_dir() {
                dirs.push(p);
                "dir".to_owned()
            } else {
                let d = Sha256::digest(std::fs::read(&p).unwrap_or_default());
                d.iter().map(|b| format!("{b:02x}")).collect()
            };
            out.insert(rel, what);
        }
    }
    out
}

/// The only paths collecting may add (JUDGE 2): `seat_send_back`'s scratch folder, and the pack
/// its plan fetched into the registered repo (objects only). Anything else new is a failure.
fn collect_may_write(rel: &str) -> bool {
    if rel == "data/send-back" || rel.starts_with("data/send-back/") {
        return true;
    }
    rel.strip_prefix("demo/.git/objects/pack/pack-")
        .is_some_and(|f| {
            !f.contains('/') && [".pack", ".idx", ".rev"].iter().any(|e| f.ends_with(e))
        })
}

/// What git says of a repo: every ref with what it names and its own settings, and the work
/// tree's state (asked without the index refresh, which would write).
fn repo_state(repo: &Path) -> (String, String) {
    let out = |args: &[&str]| {
        let o = Command::new("git")
            .arg("-C")
            .arg(repo)
            .args(args)
            .output()
            .unwrap();
        assert!(o.status.success(), "precondition: git {args:?}");
        String::from_utf8_lossy(&o.stdout).into_owned()
    };
    (
        out(&["for-each-ref", "--format=%(refname) %(objectname)"])
            + &out(&["rev-parse", "HEAD"])
            + &out(&["config", "--list", "--local"]),
        out(&[
            "--no-optional-locks",
            "status",
            "--porcelain",
            "--untracked-files=all",
        ]),
    )
}

async fn act(d: &Daemon, cred: &str, body: Value) -> Reply {
    d.call("POST", "/api/v1/act", Some(cred), Some(&body.to_string()))
        .await
}

#[tokio::test(flavor = "multi_thread")]
async fn collecting_all_seven_acts_asks_each_and_writes_only_what_is_named() {
    let windows = Arc::new(AtomicUsize::new(0));
    aibd::owner_ok::set_presence(Arc::new(Yes(windows.clone())), Arc::new(Gate));
    aibd::pr_open::set_api(Arc::new(FakePr));

    let dir = Box::leak(Box::new(tempfile::tempdir().unwrap()));
    let root = std::fs::canonicalize(dir.path()).unwrap();
    let state = root.join("import-state");
    std::fs::create_dir_all(state.join("dm-queue").join("7ebca")).unwrap();
    std::env::set_var("AIBD_IMPORT_STATE", &state);
    std::env::set_var("AIBD_SEAT_INPUT_DGRS", "1");

    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let data = dir.path().join("data");

    let demo = init_repo(&root, "demo", true);
    let fresh = init_repo(&root, "repo-new", false);
    let hooks = aibd::seats::seat_git_hooks(&data.join("private"));
    std::fs::create_dir_all(&hooks).unwrap();
    let checked = aibd::repos::check("demo", demo.to_str().unwrap(), &data, &hooks).unwrap();
    let all = aibd::repos::Repos {
        repos: vec![aibd::repos::Repo {
            name: checked.name,
            path: checked.top.to_string_lossy().into_owned(),
            remote: "git@github.com:ez2app-test-7b81e/demo.git".into(),
            manifest: checked.manifest,
            added_at: 1,
        }],
    };
    std::fs::write(data.join("repos.json"), serde_json::to_vec(&all).unwrap()).unwrap();
    std::fs::write(
        data.join("bridge.json"),
        r#"{"trusted_seats": true, "repo_push": true, "pr_open": true}"#,
    )
    .unwrap();

    // Plain safe seat for seat_trust / seat_move
    let (plain_seat, _) = d.open("safe", &seat_dir(dir.path(), "plain")).await;

    // Open a work seat on "demo" and add a commit so seat_send_back reaches owner_ok::ask
    OPEN.store(true, Ordering::SeqCst);
    let r = act(
        &d,
        &cred,
        json!({
            "type": "seat_open",
            "provider": "claude",
            "mode": "work",
            "repo": "demo",
            "persona_uid": "un0eba",
            "idem_key": "setup-repo-seat"
        }),
    )
    .await;
    let r = if r.code == 202 {
        let ticket = r.json["ticket"].as_str().unwrap().to_owned();
        let r2 = act(
            &d,
            &cred,
            json!({
                "type": "seat_open",
                "provider": "claude",
                "mode": "work",
                "repo": "demo",
                "persona_uid": "un0eba",
                "idem_key": "setup-repo-seat",
                "ticket": ticket
            }),
        )
        .await;
        assert_eq!(r2.code, 200, "{}", r2.body);
        r2
    } else {
        assert_eq!(r.code, 201, "{}", r.body);
        r
    };
    let db = aib_store::Store::open(&data.join("aibd.sqlite")).unwrap();
    let seat_obj = r.json.get("seat").unwrap_or(&r.json);
    let repo_seat = seat_obj["seat_id"].as_str().unwrap().to_owned();
    let repo_tree = PathBuf::from(db.seat_meta(&repo_seat).unwrap().unwrap().cwd);
    std::fs::write(repo_tree.join("change.txt"), "new work\n").unwrap();
    g(&repo_tree, &["add", "change.txt"]);
    g(&repo_tree, &["commit", "-q", "-m", "seat work"]);

    // Freeze window gate and reset counter before collecting
    OPEN.store(false, Ordering::SeqCst);
    windows.store(0, Ordering::SeqCst);

    let before_repo = (repo_state(&demo), repo_state(&fresh));
    let stand_ins = [
        seat_dir(dir.path(), "plain"),
        repo_tree.canonicalize().unwrap(),
    ];
    let before_tree = tree(dir.path(), &stand_ins);
    let before_db_events = db.events_after(0, 10_000).unwrap().len();
    let before_api_events = d
        .call("GET", "/api/v1/events?limit=500", None, None)
        .await
        .json["events"]
        .as_array()
        .unwrap()
        .len();

    let acts = [
        (
            "repo_register",
            json!({"type": "repo_register", "body": {"name": "repo-new", "path": fresh.to_string_lossy()}}),
        ),
        (
            "seat_open",
            json!({"type": "seat_open", "body": {"provider": "claude", "mode": "dgrs", "repo": "demo", "persona_uid": "un0eba", "idem_key": "ob-open"}}),
        ),
        (
            "seat_trust",
            json!({"type": "seat_trust", "body": {"seat_id": plain_seat}}),
        ),
        (
            "seat_move",
            json!({"type": "seat_move", "body": {"old_code": "7ebca", "seat_id": plain_seat}}),
        ),
        (
            "seat_send_back",
            json!({"type": "seat_send_back", "body": {"seat_id": repo_seat}}),
        ),
        (
            "repo_push",
            json!({"type": "repo_push", "body": {"repo": "demo", "branch": "web/ab12-1120-1"}}),
        ),
        (
            "pr_open",
            json!({"type": "pr_open", "body": {"repo": "demo", "branch": "web/ab12-1120-1", "title": "a title", "body": "a body"}}),
        ),
    ];

    for (idx, (label, item)) in acts.into_iter().enumerate() {
        let r = act(
            &d,
            &cred,
            json!({"type": "owner_batch", "items": [item], "idem_key": format!("ob-collect-{idx}")}),
        )
        .await;
        assert_eq!(
            (r.code, r.json["reason"].as_str()),
            (403, Some("pair_touch_limited")),
            "act {label} did not reach collect cleanly: {}",
            r.body
        );
    }

    assert_eq!(
        windows.load(Ordering::SeqCst),
        0,
        "reason: collecting_raised_a_window"
    );
    // git first (a ref is told as a ref), then every path
    let after_repo = (repo_state(&demo), repo_state(&fresh));
    assert_eq!(
        (&after_repo.0 .0, &after_repo.1 .0),
        (&before_repo.0 .0, &before_repo.1 .0),
        "reason: collecting_wrote_a_ref"
    );
    assert_eq!(
        (&after_repo.0 .1, &after_repo.1 .1),
        (&before_repo.0 .1, &before_repo.1 .1),
        "reason: collecting_touched_the_work_tree"
    );
    let after_tree = tree(dir.path(), &stand_ins);
    let gone: Vec<&String> = before_tree
        .keys()
        .filter(|p| !after_tree.contains_key(*p))
        .collect();
    assert!(gone.is_empty(), "reason: collecting_removed_files {gone:?}");
    let changed: Vec<&String> = before_tree
        .iter()
        .filter(|(p, what)| after_tree.get(*p).is_some_and(|w| w != *what))
        .map(|(p, _)| p)
        .collect();
    assert!(
        changed.is_empty(),
        "reason: collecting_changed_files {changed:?}"
    );
    let wrote: Vec<&String> = after_tree
        .keys()
        .filter(|p| !before_tree.contains_key(*p) && !collect_may_write(p))
        .collect();
    assert!(wrote.is_empty(), "reason: collecting_wrote_files {wrote:?}");
    assert_eq!(
        db.events_after(0, 10_000).unwrap().len(),
        before_db_events,
        "reason: collecting_wrote_db_events"
    );
    let after_api_events = d
        .call("GET", "/api/v1/events?limit=500", None, None)
        .await
        .json["events"]
        .as_array()
        .unwrap()
        .len();
    assert_eq!(
        after_api_events, before_api_events,
        "reason: collecting_wrote_journal"
    );

    d.shutdown().await;
}
