#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // polls the fake provider's log on a plain sleep
#![cfg(unix)]

//! S107-userhtml (519d0 11-18 03:5x → corrected to option 2 + 3, 5d33a): the pairing exchange page
//! sends the credential to the built-in page under /__builtin/; a user folder page pairs for itself
//! (to=user) and that credential types only to safe seats, also after the page is deleted (marked
//! when it was redeemed). While a user page serves `/`, every credential is held to safe seats
//! (one paired on the built-in page could be read if that tab went to `/`), and one redeemed then
//! is marked too (5d33a PREREAD-oneweb M1: whoever serves `/` at redeem counts, not the form). Its own process: the
//! user folder is set once per process and the dgrs switch is read at start.

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
    // the user page pairs for itself
    let user_cred = d.page_form(true, "&to=user").await;
    assert_eq!(d.act(Some(&user_cred), &safe, "a").await.code, 200);
    let r = d.act(Some(&user_cred), &dgrs, "b").await;
    assert_eq!(r.code, 403, "{}", r.body);
    assert_eq!(r.json["code"], "user_page_safe_seats_only");
    // a pairing redeemed while the user page serves `/`, even one that leaves out to=user:
    // aibd cannot tell who sent the form, so it is a user page's (5d33a PREREAD-oneweb M1)
    let while_user = d.page(true).await;
    let r = d.act(Some(&while_user), &dgrs, "c").await;
    assert_eq!(r.json["code"], "user_page_safe_seats_only", "{}", r.body);
    // the page deleted: both stay marked (marked when redeemed) ...
    std::fs::remove_file(&page).unwrap();
    for (cred, text) in [(&while_user, "d"), (&user_cred, "e")] {
        let r = d.act(Some(cred), &dgrs, text).await;
        assert_eq!(r.json["code"], "user_page_safe_seats_only", "{}", r.body);
    }
    // ... and a pairing made after it, on the built-in page, reaches dgrs (the switch is on)
    let built_in = d.page(true).await;
    assert_eq!(d.act(Some(&built_in), &dgrs, "f").await.code, 200);
    d.shutdown().await;
}
