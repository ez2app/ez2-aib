#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! §18 search end to end (Andy 11-18 「要有一個 搜尋框 搜尋出來 要分類」): grouped by kind with
//! counts, threads by category; a post's text only to the built-in page a person paired.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;

#[tokio::test(flavor = "multi_thread")]
async fn search_groups_by_kind_and_keeps_post_text_to_the_built_in_page() {
    let dir = tempfile::tempdir().unwrap();
    let pending = dir.path().join("pending");
    std::fs::create_dir(&pending).unwrap();
    std::env::set_var("AIBD_PLAN_PENDING", &pending);
    std::fs::write(
        pending.join("SUMMARY-1d-043dd-1113.md"),
        "# 一天總結\n今天 pinecone 做完三件事",
    )
    .unwrap();
    let mut d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;
    let user = d.page_form(true, "&to=user").await;
    let db = aib_store::MessageDb::open(&dir.path().join("data").join(aib_store::MESSAGES_FILE))
        .unwrap();
    aibd::import::import_from(&pending, "SUMMARY-", "summary", "總結(匯入)", Some(&db)).unwrap();
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            Some(&page),
            Some(r#"{"type":"thread_open","title":"Pinecone 計畫"}"#),
        )
        .await;
    assert_eq!(r.code, 201, "{}", r.body);

    // the built-in page: title and post text, by kind, the text around the match
    let r = d
        .call("GET", "/api/v1/search?q=pinecone", Some(&page), None)
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["full"], true);
    let g = &r.json["groups"];
    let t = g
        .as_array()
        .unwrap()
        .iter()
        .find(|x| x["kind"] == "thread")
        .expect("reason: no_thread_group");
    assert_eq!(t["count"], 2, "{}", r.body);
    assert_eq!(
        t["by_category"]["summary"], 1,
        "reason: kind_not_counted {}",
        r.body
    );
    assert_eq!(t["by_category"]["general"], 1, "{}", r.body);
    let body_hit = t["items"]
        .as_array()
        .unwrap()
        .iter()
        .find(|x| x["hit"] == "body")
        .expect("reason: body_not_searched");
    assert!(
        body_hit["snippet"]
            .as_str()
            .unwrap()
            .contains("pinecone 做完"),
        "{}",
        r.body
    );
    assert!(body_hit["message_id"].as_str().is_some());

    // a user page: the title only, no post text, no snippet
    let r = d
        .call("GET", "/api/v1/search?q=pinecone", Some(&user), None)
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["full"], false);
    assert!(
        !r.body.contains("做完"),
        "reason: text_to_user_page {}",
        r.body
    );
    let t = &r.json["groups"][0];
    assert_eq!(
        (t["kind"].as_str(), t["count"].as_u64()),
        (Some("thread"), Some(1)),
        "{}",
        r.body
    );
    assert_eq!(t["items"][0]["hit"], "title");
    assert!(
        t["items"][0]["last_at"].as_str().is_some(),
        "reason: no_last_at"
    );
    // 5d33a M3: a user page cannot open a document, so gets no documents group
    let r = d
        .call(
            "GET",
            "/api/v1/search?q=%E4%B8%80%E5%A4%A9%E7%B8%BD%E7%B5%90",
            Some(&user),
            None,
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert!(
        !r.body.contains("SUMMARY-1d"),
        "reason: doc_names_to_user_page {}",
        r.body
    );

    // documents a post links, by title; a group with nothing found is left out
    let r = d
        .call(
            "GET",
            "/api/v1/search?q=%E4%B8%80%E5%A4%A9%E7%B8%BD%E7%B5%90",
            Some(&page),
            None,
        )
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    let kinds: Vec<&str> = r.json["groups"]
        .as_array()
        .unwrap()
        .iter()
        .map(|x| x["kind"].as_str().unwrap())
        .collect();
    assert!(kinds.contains(&"doc"), "reason: doc_not_found {}", r.body);
    assert!(
        !kinds.contains(&"seat"),
        "reason: empty_group_shown {}",
        r.body
    );

    for (q, code) in [
        ("", "q_bad"),
        ("a&limit=0", "limit_bad"),
        ("a&path=x", "unknown_field"),
    ] {
        let r = d
            .call("GET", &format!("/api/v1/search?q={q}"), Some(&page), None)
            .await;
        assert_eq!(
            (r.code, r.json["code"].as_str()),
            (400, Some(code)),
            "{q}: {}",
            r.body
        );
    }
    // 5d33a M1: while a user folder page serves `/`, the built-in page's credential is held
    // the same way (gives_words): no post text
    let web = dir.path().join("data").join("web");
    std::fs::create_dir_all(&web).unwrap();
    std::fs::write(web.join("index.html"), "<!doctype html><title>u</title>").unwrap();
    let r = d
        .call("GET", "/api/v1/search?q=pinecone", Some(&page), None)
        .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(
        r.json["full"], false,
        "reason: full_while_user_page_serves {}",
        r.body
    );
    assert!(!r.body.contains("做完"), "{}", r.body);
    d.shutdown().await;
}
