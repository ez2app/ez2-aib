#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness waits on a plain sleep and a timeout
#![cfg(unix)]

//! L1c (IFACE-provider-lifecycle-c2f0b-1126 §3.8, r1-5 N2): what a download cut off by a crash
//! left under `<data>/run/dl` is gone once aibd has started again; a link there goes as the
//! link, what it points at stays.

#[path = "support/seat_input_harness.rs"]
mod harness;

use harness::Daemon;

#[tokio::test(flavor = "multi_thread")]
async fn a_start_removes_downloads_a_crash_left_and_never_follows_a_link() {
    let dir = tempfile::tempdir().unwrap();
    let dl = dir.path().join("data/run/dl");
    std::fs::create_dir_all(dl.join("3f2a/sub")).unwrap();
    std::fs::write(dl.join("3f2a/sub/claude"), b"half a download").unwrap();
    let outside = tempfile::tempdir().unwrap();
    std::fs::write(outside.path().join("keep"), b"k").unwrap();
    std::os::unix::fs::symlink(outside.path(), dl.join("link")).unwrap();

    let mut d = Daemon::start(dir.path()).await;
    let left: Vec<_> = std::fs::read_dir(&dl)
        .map(|r| r.flatten().map(|e| e.file_name()).collect())
        .unwrap_or_default();
    assert!(
        left.is_empty(),
        "reason: download_left_after_start {left:?}"
    );
    assert!(
        outside.path().join("keep").exists(),
        "reason: followed_link_at_start"
    );
    d.shutdown().await;
}
