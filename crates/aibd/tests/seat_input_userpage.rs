#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! S107-userhtml (519d0 11-18 03:5x §3, 5d33a): a credential handed to a user folder page types
//! only to safe seats, and stays so after the page is deleted (marked when it was redeemed); while
//! a user page serves `/`, a credential paired on the built-in page is held to safe seats too. Its
//! own process: the user folder is set once per process and the dgrs switch is read at start.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::{seat_dir, Daemon};

#[tokio::test(flavor = "multi_thread")]
async fn a_user_page_credential_types_only_to_safe_seats_even_after_the_page_is_gone() {
    std::env::set_var("AIBD_SEAT_INPUT_DGRS", "1");
    let dir = tempfile::tempdir().unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let web = dir.path().join("data").join("web");
    std::fs::create_dir_all(&web).unwrap();
    let page = web.join("index.html");
    std::fs::write(&page, "<p>mine</p>").unwrap();
    let (safe, _) = d.open("safe", &seat_dir(dir.path(), "safe")).await;
    let (dgrs, _) = d.open("dgrs", &seat_dir(dir.path(), "dgrs")).await;
    // paired while the user page serves `/`
    let user_cred = d.page(true).await;
    assert_eq!(d.act(Some(&user_cred), &safe, "a").await.code, 200);
    let r = d.act(Some(&user_cred), &dgrs, "b").await;
    assert_eq!(r.code, 403, "{}", r.body);
    assert_eq!(r.json["code"], "user_page_safe_seats_only");
    // the page deleted: the credential is still marked
    std::fs::remove_file(&page).unwrap();
    let r = d.act(Some(&user_cred), &dgrs, "c").await;
    assert_eq!(r.json["code"], "user_page_safe_seats_only", "{}", r.body);
    // paired on the built-in page: reaches dgrs (the switch is on) ...
    let built_in = d.page(true).await;
    assert_eq!(d.act(Some(&built_in), &dgrs, "d").await.code, 200);
    // ... until a user page serves `/` again (a reload in that tab would hand it over)
    std::fs::write(&page, "<p>again</p>").unwrap();
    let r = d.act(Some(&built_in), &dgrs, "e").await;
    assert_eq!(r.json["code"], "user_page_safe_seats_only", "{}", r.body);
    assert_eq!(d.act(Some(&built_in), &safe, "f").await.code, 200);
    d.shutdown().await;
}
