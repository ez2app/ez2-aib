#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! A1 (DESIGN-a1-edit-archive-08de5-1123 r0; f163c REVIEW-a1 r0): from the built-in page, a role
//! the person added is renamed (never onto a name another has) and a built-in one is locked;
//! an archived persona / role leaves the picker for the 已封存 list, opens no seat, and comes
//! back when restored.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;
use serde_json::{json, Value};

#[tokio::test(flavor = "multi_thread")]
async fn archived_ones_leave_the_picker_and_open_nothing_until_restored() {
    let dir = Box::leak(Box::new(tempfile::tempdir().unwrap()));
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let post = |b: Value| {
        let (d, cred) = (&d, cred.clone());
        async move {
            d.call("POST", "/api/v1/act", Some(&cred), Some(&b.to_string()))
                .await
        }
    };
    let get = |p: &'static str| {
        let d = &d;
        async move { d.call("GET", p, None, None).await.json }
    };
    let uids = |v: &Value, key: &str| -> Vec<String> {
        v[key]
            .as_array()
            .map(|a| {
                a.iter()
                    .filter_map(|r| r["uid"].as_str().map(str::to_owned))
                    .collect()
            })
            .unwrap_or_default()
    };

    // two roles the person added; renaming one onto the other's name is refused (L2)
    let a = post(json!({"type": "role_add", "name": "整理", "idem_key": "a1-r1"})).await;
    let a = a.json["uid"].as_str().unwrap().to_owned();
    let b = post(json!({"type": "role_add", "name": "翻譯", "idem_key": "a1-r2"})).await;
    let b = b.json["uid"].as_str().unwrap().to_owned();
    let r = post(json!({"type": "role_set", "uid": b, "name": "整理", "idem_key": "a1-r3"})).await;
    assert_eq!(r.code, 400, "reason: name_taken_let_in {}", r.body);
    assert_eq!(r.json["reason"], "name_taken");
    let r = post(
        json!({"type": "role_set", "uid": b, "name": "校對", "what": "看稿", "idem_key": "a1-r4"}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);

    // a built-in role is locked: not renamed, not archived
    let builtin = uids(&get("/api/v1/roles").await, "roles")[0].clone();
    for b in [
        json!({"type": "role_set", "uid": builtin, "name": "x", "idem_key": "a1-b1"}),
        json!({"type": "role_archive", "uid": builtin, "idem_key": "a1-b2"}),
    ] {
        let r = post(b).await;
        assert_eq!(r.code, 400, "{}", r.body);
        assert_eq!(
            r.json["reason"], "role_builtin",
            "reason: builtin_role_changed {}",
            r.body
        );
    }

    // archived: out of the picker, into 已封存; an open with it is refused; restored: back
    let r = post(json!({"type": "role_archive", "uid": a, "idem_key": "a1-a1"})).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let roles = get("/api/v1/roles").await;
    assert!(
        !uids(&roles, "roles").contains(&a),
        "reason: archived_in_picker"
    );
    assert!(uids(&roles, "archived").contains(&a));
    let r = post(json!({"type": "persona_archive", "uid": "un0eba", "idem_key": "a1-a2"})).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let ps = get("/api/v1/personas").await;
    assert!(
        uids(&ps, "personas").is_empty() && uids(&ps, "archived") == ["un0eba"],
        "{ps}"
    );
    let open = json!({"type": "seat_open", "provider": "claude", "mode": "safe",
                      "persona_uid": "un0eba", "idem_key": "a1-o1"});
    let r = post(open.clone()).await;
    assert_eq!(r.code, 400, "reason: archived_persona_opened {}", r.body);
    assert_eq!(r.json["reason"], "persona_archived");
    let r = post(json!({"type": "persona_restore", "uid": "un0eba", "idem_key": "a1-a3"})).await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(uids(&get("/api/v1/personas").await, "personas"), ["un0eba"]);
    let r = post(json!({"type": "persona_restore", "uid": "un0eba", "idem_key": "a1-a4"})).await;
    assert_eq!(r.code, 404, "reason: restored_twice {}", r.body);

    // a seat token cannot do any of this (built-in page only): no cred is refused
    let r = d
        .call(
            "POST",
            "/api/v1/act",
            None,
            Some(&json!({"type": "role_archive", "uid": b, "idem_key": "a1-x"}).to_string()),
        )
        .await;
    assert!(
        r.code == 401 || r.code == 403,
        "reason: unpaired_archived {}",
        r.body
    );
    d.shutdown().await;
}

/// The built-in persona (a machine with no cards file, 08de5 11-26) is listed as built-in and
/// is never changed: persona_set and persona_archive are refused before any owner window
/// (3e53b DECISION-builtin-persona-bounds 1-4).
#[tokio::test(flavor = "multi_thread")]
async fn the_built_in_persona_is_listed_and_never_changed() {
    let dir = Box::leak(Box::new(tempfile::tempdir().unwrap()));
    let mut d = Daemon::start(dir.path()).await;
    let cred = d.page(true).await;
    let listed = d.call("GET", "/api/v1/personas", None, None).await.json;
    assert_eq!(listed["personas"][0]["builtin"], false, "{listed}");
    std::fs::remove_file(dir.path().join("cards.json")).unwrap();
    let listed = d.call("GET", "/api/v1/personas", None, None).await.json;
    assert_eq!(
        (
            listed["personas"].as_array().map(Vec::len),
            listed["personas"][0]["uid"].as_str(),
            listed["personas"][0]["builtin"].as_bool(),
        ),
        (Some(1), Some("aib000"), Some(true)),
        "{listed}"
    );
    let sha = "f63539efbf94ab4e4f706a80c034bd4f4654b96301f5b4eb8601938da52e7cee";
    for (b, what) in [
        (
            json!({"type": "persona_set", "persona_uid": "aib000", "base_sha": sha,
                "duty": "不用問本人。", "idem_key": "bi-1"}),
            "set",
        ),
        (
            json!({"type": "persona_archive", "uid": "aib000", "idem_key": "bi-2"}),
            "archive",
        ),
    ] {
        let r = d
            .call("POST", "/api/v1/act", Some(&cred), Some(&b.to_string()))
            .await;
        assert_eq!(
            (r.code, r.json["reason"].as_str()),
            (400, Some("builtin_persona_read_only")),
            "reason: builtin_persona_changed {what} {}",
            r.body
        );
    }
    assert!(
        !dir.path().join("cards.json").exists(),
        "reason: builtin_made_cards"
    );
    d.shutdown().await;
}
