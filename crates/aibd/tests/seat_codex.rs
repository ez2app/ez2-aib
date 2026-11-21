#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the harness polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! S89-live-2 (b4297): a codex seat, end to end on the fake `codex app-server` (the test fake
//! acts like 0.167.0, measured r2/r3). aibd opens it safe (dgrs is refused before any seat is
//! written), writes the three opening lines first, writes a person's text as `turn/start` on
//! the thread codex named, ends the turn on `turn/completed`, and answers a request codex
//! makes of it with an error.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{log_with, seat_dir, Daemon};
use serde_json::Value;

fn json(line: &[u8]) -> Value {
    serde_json::from_slice(line).unwrap()
}

#[tokio::test(flavor = "multi_thread")]
async fn a_codex_seat_is_spoken_to_in_its_protocol() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cwd = seat_dir(dir.path(), "cx");
    let (seat, _) = d.open_as("codex", "safe", &cwd).await;

    // the opening: initialize, initialized, thread/start in the seat's folder
    let got = log_with(&cwd, 3);
    let methods: Vec<String> = got
        .iter()
        .map(|(_, l)| json(l)["method"].as_str().unwrap_or_default().to_owned())
        .collect();
    assert_eq!(
        methods,
        ["initialize", "initialized", "thread/start"],
        "reason: opening_wrong"
    );
    assert_eq!(json(&got[2].1)["params"]["cwd"], cwd.to_str().unwrap());
    // N-ws-ask-codex: the seat was started with aibd's talk server (ask_person among its tools)
    let args = std::fs::read_to_string(cwd.join("fake-provider-args.log")).unwrap_or_default();
    assert!(
        args.contains("mcp_servers.aibd.command=")
            && args.contains("ask_person")
            && !args.contains("--mcp-config"),
        "reason: codex_no_talk_tools {args}"
    );
    // N-codex-user-mcp: the user's own server codex listed as on is turned off, aibd's is not
    assert!(
        args.contains("mcp_servers.userx.enabled=false")
            && !args.contains("mcp_servers.aibd.enabled=false"),
        "reason: user_mcp_left_on {args}"
    );

    let cred = d.page(true).await;
    // the thread may still be starting for a moment: the person's text waits or goes
    let mut r = d.act(Some(&cred), &seat, "你好").await;
    for _ in 0..50 {
        if r.code == 200 {
            break;
        }
        assert_eq!(r.json["code"], "seat_starting", "{}", r.body);
        std::thread::sleep(std::time::Duration::from_millis(100));
        r = d.act(Some(&cred), &seat, "你好").await;
    }
    assert_eq!(r.code, 200, "{}", r.body);
    let got = log_with(&cwd, 4);
    let t = json(&got[3].1);
    assert_eq!(t["method"], "turn/start", "reason: not_turn_start {t}");
    assert!(
        t["params"]["threadId"]
            .as_str()
            .unwrap()
            .starts_with("th-fake-"),
        "{t}"
    );
    let texts: Vec<&str> = t["params"]["input"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|i| i["text"].as_str())
        .collect();
    assert_eq!(texts.last(), Some(&"你好"), "{t}");

    // codex asks aibd something: aibd says no, at once, on the same pipe
    let r = d.act(Some(&cred), &seat, "ask").await;
    assert_eq!(r.code, 200, "{}", r.body);
    let got = log_with(&cwd, 6);
    let answer = got
        .iter()
        .map(|(_, l)| json(l))
        .find(|v| v["id"] == 900)
        .expect("reason: request_not_answered");
    assert!(
        answer["error"]["code"].is_i64(),
        "reason: request_approved {answer}"
    );
    assert!(answer.get("result").is_none());
    d.shutdown().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_dgrs_codex_seat_is_refused_and_version_says_safe_only() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cwd = seat_dir(dir.path(), "cx");
    let body = serde_json::json!({"persona_uid": "un0eba", "provider": "codex", "mode": "dgrs",
        "cwd": cwd.to_str().unwrap()})
    .to_string();
    let r = d
        .call("POST", "/api/v1/seats/open", None, Some(&body))
        .await;
    assert_eq!(r.code, 400, "{}", r.body);
    assert_eq!(r.json["reason"], "provider_mode_unsupported", "{}", r.body);
    assert!(
        !cwd.join("fake-provider.log").exists(),
        "reason: dgrs_codex_started"
    );
    d.shutdown().await;
}

/// N-ws-codex-work (FEASIBILITY-codex-work-seat-7ebca-1120 C, COUNCIL-worktree-gitdir-7ebca-1120
/// Q3): a codex work seat opens in its own folder only, in the codex profile `seat` that writes
/// that folder and its `.git` and nothing else; given a folder it is refused before anything is
/// started. Off macOS (its sandbox not measured) codex has no work mode at all.
#[tokio::test(flavor = "multi_thread")]
async fn a_codex_work_seat_writes_its_own_folder_only() {
    // the page opens the author (a claude work seat) only with this machine's switch on
    // (dgrs_needs_switch otherwise; 7ebca all-45430b4fc)
    std::env::set_var("AIBD_SEAT_INPUT_DGRS", "1");
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let given = seat_dir(dir.path(), "cx");
    let body = |cwd: Option<&std::path::Path>| {
        let mut b =
            serde_json::json!({"persona_uid": "un0eba", "provider": "codex", "mode": "work"});
        if let Some(c) = cwd {
            b["cwd"] = serde_json::json!(c.to_str().unwrap());
        }
        b.to_string()
    };
    let r = d
        .call(
            "POST",
            "/api/v1/seats/open",
            None,
            Some(&body(Some(&given))),
        )
        .await;
    assert_eq!(r.code, 400, "{}", r.body);
    let why = if cfg!(target_os = "macos") {
        "codex_work_own_folder"
    } else {
        "provider_mode_unsupported"
    };
    assert_eq!(
        r.json["reason"], why,
        "reason: codex_work_in_given_folder {}",
        r.body
    );
    assert!(
        !given.join("fake-provider.log").exists(),
        "reason: given_folder_started"
    );
    if !cfg!(target_os = "macos") {
        d.shutdown().await;
        return;
    }
    // the page's open: with_seat + own_tree is refused before a tree is opened off the author's
    // folder (5d33a PREREAD-codexwork-1a40bef6 L1: the tree was left registered in its .git)
    let cred = d.page(true).await;
    let page = |b: serde_json::Value| {
        let (d, cred) = (&d, cred.clone());
        async move {
            d.call("POST", "/api/v1/act", Some(&cred), Some(&b.to_string()))
                .await
        }
    };
    let r = page(
        serde_json::json!({"type": "seat_open", "provider": "claude", "mode": "work",
        "persona_uid": "un0eba", "idem_key": "cw-author"}),
    )
    .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let author = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let r = page(
        serde_json::json!({"type": "seat_open", "provider": "codex", "mode": "work",
        "persona_uid": "un0eba", "idem_key": "cw-tree", "with_seat": author, "own_tree": true}),
    )
    .await;
    assert_eq!(r.code, 400, "{}", r.body);
    assert_eq!(r.json["reason"], "codex_work_own_folder", "{}", r.body);
    let work = aibd::folders::folder(&dir.path().join("data"), "seat-work");
    let trees: Vec<String> = std::fs::read_dir(&work)
        .map(|it| {
            it.filter_map(|e| e.ok()?.file_name().into_string().ok())
                .filter(|n| !n.starts_with("seat_"))
                .collect()
        })
        .unwrap_or_default();
    assert!(
        trees.is_empty(),
        "reason: tree_opened_before_refusal {trees:?}"
    );
    let wt = work.join(&author).join(".git").join("worktrees");
    assert!(!wt.exists(), "reason: tree_left_in_authors_git");
    let r = d
        .call("POST", "/api/v1/seats/open", None, Some(&body(None)))
        .await;
    assert_eq!(r.code, 201, "{}", r.body);
    let seat = r.json["seat"]["seat_id"].as_str().unwrap().to_string();
    let home = aibd::folders::folder(&dir.path().join("data"), "seat-work")
        .join(&seat)
        .canonicalize()
        .unwrap();
    let _ = log_with(&home, 3);
    let line = std::fs::read_to_string(home.join("fake-provider-args.log")).unwrap_or_default();
    let args: Vec<String> = line
        .lines()
        .last()
        .and_then(|l| l.split_once(' '))
        .and_then(|(_, j)| serde_json::from_str(j).ok())
        .unwrap_or_default();
    let set: Vec<&str> = args
        .windows(2)
        .filter(|w| w[0] == "-c")
        .map(|w| w[1].as_str())
        .collect();
    assert!(
        set.contains(&"default_permissions=\"seat\""),
        "reason: no_profile {args:?}"
    );
    let fs: Vec<&&str> = set
        .iter()
        .filter(|kv| kv.starts_with("permissions.seat.filesystem="))
        .collect();
    assert_eq!(fs.len(), 1, "reason: profile_folders_missing {args:?}");
    let h = home.to_str().unwrap();
    assert!(
        fs[0].contains(&format!("\"{h}\"=\"write\""))
            && fs[0].contains(&format!("\"{h}/.git\"=\"write\""))
            && fs[0].matches("=\"write\"").count() == 2
            && fs[0].contains("\"/private/tmp\"=\"none\""),
        "reason: writes_not_only_its_folder {}",
        fs[0]
    );
    assert!(
        !set.iter().any(|kv| kv.starts_with("sandbox_mode")),
        "reason: codex_own_sandbox {args:?}"
    );
    d.shutdown().await;
}

/// MEASURE-codex-folder-config r2 (7ebca 11-20): a folder with codex project settings starts
/// no codex session (the seat is written, its session refused) (a trusted project's `.codex/config.toml` is merged under aibd's own `-c`).
#[tokio::test(flavor = "multi_thread")]
async fn a_codex_seat_in_a_folder_with_codex_settings_is_not_started() {
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let cwd = seat_dir(dir.path(), "cs");
    std::fs::create_dir_all(cwd.join(".codex")).unwrap();
    std::fs::write(
        cwd.join(".codex").join("config.toml"),
        "[permissions.seat.filesystem]\n\"/\" = \"write\"\n",
    )
    .unwrap();
    let body = serde_json::json!({"persona_uid": "un0eba", "provider": "codex", "mode": "safe",
        "cwd": cwd.to_str().unwrap()})
    .to_string();
    let r = d
        .call("POST", "/api/v1/seats/open", None, Some(&body))
        .await;
    // a seat is a fact once written: the refusal is 201 {session: null, session_error}
    // (IFACE-b4-1b-2-session §5; a9136 st3 11-20 caught the old 4xx assert)
    let j: serde_json::Value = serde_json::from_str(&r.body).unwrap_or_default();
    assert!(
        r.code == 201 && j["session"].is_null() && r.body.contains("codex_folder_settings"),
        "reason: folder_settings_started {} {}",
        r.code,
        r.body
    );
    assert!(
        !cwd.join("fake-provider-args.log").exists(),
        "reason: codex_started"
    );
    d.shutdown().await;
}
