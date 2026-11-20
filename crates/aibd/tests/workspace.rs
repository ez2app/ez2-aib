#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! N-workspace (ORDER-workspace-519d0-1118, 7b81e row): the three reads the workspace page
//! needs, through `GET /api/v1/…` as a page calls them. The tree file is the data folder's
//! `tree.json`; the person's words (`need`) go to the built-in page only.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;

const TREE: &str = r#"{"asof": "11-18 09:3x", "nodes": [
  {"id": "V", "parent": null, "kind": "vision", "title": "總綱"},
  {"id": "N-ws", "parent": "V", "kind": "item", "title": "工作台", "status": "開發中",
   "need": "無條件,先把這個畫面做出來", "owner": "519d0", "with": ["7b81e"]},
  {"id": "T1", "parent": "N-ws", "kind": "task", "title": "樹"}
]}"#;

#[tokio::test(flavor = "multi_thread")]
async fn the_workspace_reads_the_tree_the_host_and_what_aibd_can_do() {
    let dir = tempfile::tempdir().unwrap();
    // whether a brand opens reads its login under HOME (workspace::logged_in): a home of its
    // own with both logins, so the answer does not hang on the test machine's accounts
    // (a9136 wn1-d0d317739: Pro 13 has no codex login ⇒ codex not_logged_in)
    let home = dir.path().join("home");
    std::fs::create_dir_all(home.join(".claude")).unwrap();
    std::fs::create_dir_all(home.join(".codex")).unwrap();
    std::fs::write(home.join(".claude").join(".credentials.json"), "{}").unwrap();
    std::fs::write(home.join(".codex").join("auth.json"), "{}").unwrap();
    std::env::set_var("HOME", &home);
    std::env::remove_var("CODEX_HOME");
    let mut d = Daemon::start(dir.path()).await;

    // no file yet: an empty tree, not an error
    let r = d.call("GET", "/api/v1/tree", None, None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["source"], "none");
    assert_eq!(r.json["nodes"], serde_json::json!([]));

    std::fs::write(dir.path().join("data").join("tree.json"), TREE).unwrap();
    let r = d.call("GET", "/api/v1/tree", None, None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["source"], "file");
    assert_eq!(r.json["asof"], "11-18 09:3x");
    let ids: Vec<&str> = r.json["nodes"]
        .as_array()
        .unwrap()
        .iter()
        .map(|n| n["id"].as_str().unwrap())
        .collect();
    assert_eq!(ids, ["V", "N-ws", "T1"]);
    assert_eq!(r.json["nodes"][1]["station"], "開發中");
    assert_eq!(r.json["nodes"][1]["need"], "無條件,先把這個畫面做出來");

    let r = d
        .call("GET", "/api/v1/tree?root=N-ws&depth=0", None, None)
        .await;
    assert_eq!(r.json["nodes"].as_array().unwrap().len(), 1, "{}", r.body);
    let r = d.call("GET", "/api/v1/tree?root=nope", None, None).await;
    assert_eq!(r.code, 404, "{}", r.body);
    assert_eq!(r.json["code"], "node_not_found");

    // the built-in page reads the words; a user folder page their length and hash
    let built_in = d.page(true).await;
    let r = d
        .call(
            "GET",
            "/api/v1/tree?root=N-ws&depth=0",
            Some(&built_in),
            None,
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["nodes"][0]["need"], "無條件,先把這個畫面做出來");
    let user = d.page_form(true, "&to=user").await;
    let r = d
        .call("GET", "/api/v1/tree?root=N-ws&depth=0", Some(&user), None)
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["nodes"][0]["need"]["len"], 13);
    assert!(
        !r.body.contains("無條件"),
        "reason: words_to_user_page {}",
        r.body
    );
    assert_eq!(r.json["nodes"][0]["title"], "工作台");

    // a broken file is left as it is and said so
    std::fs::write(dir.path().join("data").join("tree.json"), "{").unwrap();
    let r = d.call("GET", "/api/v1/tree", Some(&user), None).await;
    assert_eq!(r.code, 503, "{}", r.body);
    assert_eq!(r.json["code"], "tree_unreadable");

    let r = d.call("GET", "/api/v1/host", Some(&user), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert!(
        r.json["memory"]["total_bytes"].as_u64().unwrap() > 0,
        "{}",
        r.body
    );
    assert!(
        r.json["disk"]["total_bytes"].as_u64().unwrap() > 0,
        "{}",
        r.body
    );
    assert_eq!(r.json["sessions"]["running"], 0);
    assert!(
        !r.body.contains(&dir.path().display().to_string()),
        "reason: host_names_a_path"
    );

    let r = d.call("GET", "/api/v1/version", Some(&user), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let ps = r.json["capabilities"]["providers"].as_array().unwrap();
    // AIB 2.0's brands only (§22), each saying whether it opens here and in which modes
    // (S89-live-2: codex safe only); the harness policy lists both, each a real file
    assert_eq!(
        ps,
        &vec![
            serde_json::json!({"id": "claude", "open": true, "modes": ["dgrs", "safe", "work"]}),
            serde_json::json!({"id": "codex", "open": true, "modes": ["safe"]}),
        ],
        "reason: brands_not_aib2_only {}",
        r.body
    );
    assert!(r.json["capabilities"]["routes"]
        .as_array()
        .unwrap()
        .contains(&serde_json::json!("/api/v1/tree")));

    // the persona cards a seat can be opened with: uid and name, no path, no hash (043dd)
    let r = d.call("GET", "/api/v1/personas", Some(&user), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let ps = r.json["personas"].as_array().unwrap();
    assert!(!ps.is_empty(), "{}", r.body);
    for p in ps {
        let keys: Vec<&str> = p.as_object().unwrap().keys().map(String::as_str).collect();
        assert_eq!(
            keys,
            ["display_name", "uid"],
            "reason: persona_says_more {p}"
        );
    }
    d.shutdown().await;
}
