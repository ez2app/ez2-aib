#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! N-pv-env-rest (7b81e 11-20): the board's usage file is read where `AIBD_USAGE_SOURCE` names it,
//! not only from `<data>/usage-cache.json` (the preview had none; nobody copies it by hand). Its
//! own binary: the variable is set once for this process.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;

const CACHE: &str = r#"{"v": 2, "files": {}, "rows": {
  "7b81e|498507|N-ws": [10741, 19257, 5881800, 24]
}, "cum": {"N-ws": {
  "7b81e": [10741, 19257, 5881800, 24, 600, 1794625200, 1794628000]
}}}"#;

#[tokio::test(flavor = "multi_thread")]
async fn the_usage_file_is_read_where_aibd_usage_source_names_it() {
    let dir = tempfile::tempdir().unwrap();
    let board = tempfile::tempdir().unwrap();
    let file = board.path().join("usage-cache.json");
    std::fs::write(&file, CACHE).unwrap();
    std::env::set_var("AIBD_USAGE_SOURCE", &file);
    let mut d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;
    let r = d
        .call("GET", "/api/v1/nodes/N-ws/usage", Some(&page), None)
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        r.json["source"], "file",
        "reason: usage_source_not_read {}",
        r.body
    );
    assert_eq!(r.json["seats"][0]["seat_id"], "7b81e");
    assert!(
        !dir.path().join("data").join("usage-cache.json").exists(),
        "reason: usage_copied"
    );
    d.shutdown().await;
}
