#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! §20 試用預覽版 end to end (DESIGN-preview-a9136-1118 rev 2, 5d33a CHECK-preview r1): the
//! built-in page turns the preview on for the exact file it showed; it runs with its own HOME,
//! data folder and port and nothing else of this aibd's; every start is on the books; any page
//! stops it.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;
use serde_json::{json, Value};

const FAKE: &str = env!("CARGO_BIN_EXE_aibd-fake-provider");

/// The row of preview `id` in `GET /api/v1/previews`.
fn row(v: &Value, id: &str) -> Value {
    v["previews"]
        .as_array()
        .unwrap()
        .iter()
        .find(|p| p["id"] == id)
        .cloned()
        .unwrap_or(Value::Null)
}

fn events_of(v: &Value, kind: &str) -> Vec<Value> {
    v["events"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| e["type"] == kind)
        .cloned()
        .collect()
}

#[tokio::test(flavor = "multi_thread")]
async fn the_built_in_page_turns_on_the_preview_it_agreed_to_in_its_own_home_and_data() {
    let dir = tempfile::tempdir().unwrap();
    std::env::set_var("SSH_AUTH_SOCK", "/tmp/agent-not-for-the-preview");
    let mut d = Daemon::start(dir.path()).await;
    let page = d.page(true).await;
    let user = d.page_form(true, "&to=user").await;
    // §20b: an empty list shows sample rows, which cannot start
    let r = d.call("GET", "/api/v1/previews", Some(&page), None).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let rows = r.json["previews"].as_array().unwrap();
    assert!(
        rows.len() >= 2
            && rows
                .iter()
                .all(|p| p["sample"] == true && p["available"] == false)
    );
    let add =
        json!({"type": "preview_add", "name": "清單式預覽", "card": "N-ws-preview"}).to_string();
    let r = d.call("POST", "/api/v1/act", Some(&user), Some(&add)).await;
    assert_eq!(r.code, 403, "reason: user_page_lists {}", r.body);
    let r = d.call("POST", "/api/v1/act", Some(&page), Some(&add)).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let id = r.json["id"].as_str().unwrap().to_string();
    let pv = dir.path().join("data").join("previews").join(&id);
    let r = d.call("GET", "/api/v1/previews", Some(&page), None).await;
    assert_eq!(row(&r.json, &id)["available"], false, "{}", r.body);
    std::fs::create_dir_all(&pv).unwrap();
    std::fs::copy(FAKE, pv.join("aibd")).unwrap();
    std::fs::write(pv.join("VERSION"), "0.2.0-preview.8 3f2a91c0\n").unwrap();
    let r = d.call("GET", "/api/v1/previews", Some(&page), None).await;
    let me = row(&r.json, &id);
    assert_eq!(me["available"], true, "{}", r.body);
    assert_eq!(me["running"], false);
    let sha = me["sha256"].as_str().unwrap().to_string();
    assert_eq!(sha.len(), 64);

    let start = |sha: &str, consent: bool| {
        json!({"type": "preview_start", "id": id, "version": "0.2.0-preview.8 3f2a91c0",
            "sha256": sha, "consent": consent})
        .to_string()
    };
    let act = |cred: String, body: String| {
        let d = &d;
        async move {
            d.call("POST", "/api/v1/act", Some(&cred), Some(&body))
                .await
        }
    };
    // a user folder page cannot agree for the person
    let r = act(user.clone(), start(&sha, true)).await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (403, Some("preview_needs_built_in_page")),
        "{}",
        r.body
    );
    let r = act(page.clone(), start(&sha, false)).await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (400, Some("consent_required")),
        "{}",
        r.body
    );
    // the file the page showed, not another
    let r = act(page.clone(), start(&"0".repeat(64), true)).await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (409, Some("preview_changed")),
        "{}",
        r.body
    );

    let r = act(page.clone(), start(&sha, true)).await;
    assert_eq!(r.code, 200, "{}", r.body);
    // 519d0 11-19 21:26: the preview runs under the policy this aibd runs under (given to it in
    // memory here, from AIBD_SESSION_POLICY for the parent page), so its brands are set up
    let handed = std::fs::read_to_string(pv.join("data").join("session-policy.json"))
        .unwrap_or_else(|e| panic!("reason: preview_without_policy {e}"));
    let names: Vec<String> = aibd::allowlist::parse(&handed)
        .unwrap()
        .programs
        .into_iter()
        .map(|p| p.name)
        .collect();
    assert_eq!(
        names,
        ["claude", "codex"],
        "reason: preview_policy_differs {handed}"
    );
    // Andy 11-19 08:5x: it opens on the built-in page already paired, by a one-time pass in
    // the fragment; down the preview's stdin goes only the pass's digest
    let url = r.json["url"].as_str().unwrap().to_string();
    let pass = url
        .strip_prefix("http://127.0.0.1:45678/__builtin/#pv=")
        .unwrap_or_else(|| panic!("reason: no_pass {url}"))
        .to_string();
    assert_eq!(pass.len(), 43, "{url}");
    let piped = |n: usize| {
        let f = pv.join("data").join("passes.txt");
        for _ in 0..100 {
            let t = std::fs::read_to_string(&f).unwrap_or_default();
            if t.lines().count() >= n {
                return t;
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
        panic!("reason: pass_not_piped (wanted {n} lines)");
    };
    let t = piped(1);
    assert_eq!(t, aibd::preview::pass_line(&pass), "reason: pipe_line");
    assert!(!t.contains(&pass), "reason: pass_in_pipe");
    // 打開 hands a new pass, from the paired built-in page only
    let open = json!({"type": "preview_open", "id": id}).to_string();
    let r = act(user.clone(), open.clone()).await;
    assert_eq!(r.code, 403, "reason: user_page_opens_paired {}", r.body);
    let r = act(page.clone(), open.clone()).await;
    assert_eq!(r.code, 200, "{}", r.body);
    let again = r.json["url"]
        .as_str()
        .unwrap()
        .rsplit("#pv=")
        .next()
        .unwrap()
        .to_string();
    assert_ne!(again, pass, "reason: pass_handed_twice");
    assert_eq!(
        piped(2).lines().nth(1).unwrap(),
        aibd::preview::pass_line(&again).trim_end()
    );
    // this aibd is no preview: it takes no pass
    let r = d
        .form("/api/v1/pair/preview", &format!("pass={again}"), None)
        .await;
    assert_eq!(r.code, 404, "reason: parent_takes_pass {}", r.body);
    let r = d
        .form(
            "/api/v1/pair/preview",
            &format!("pass={again}"),
            Some("http://198.51.100.7"),
        )
        .await;
    assert_eq!(r.code, 403, "reason: pass_from_other_origin {}", r.body);
    let r = d
        .form("/api/v1/pair/preview", &format!("pass={again}"), Some(""))
        .await;
    assert_eq!(r.code, 403, "reason: pass_without_origin {}", r.body);
    let r = d.call("GET", "/api/v1/previews", Some(&page), None).await;
    assert_eq!(row(&r.json, &id)["running"], true, "{}", r.body);

    // its own HOME and data, nothing else of this aibd's (5d33a M1 / L2)
    let seen: Value =
        serde_json::from_str(&std::fs::read_to_string(pv.join("data").join("seen.json")).unwrap())
            .unwrap();
    let env = &seen["env"];
    assert_eq!(
        env["HOME"],
        pv.join("home").to_string_lossy().as_ref(),
        "reason: home_shared {seen}"
    );
    assert_eq!(
        env["AIBD_DATA_DIR"],
        pv.join("data").to_string_lossy().as_ref(),
        "reason: data_shared"
    );
    assert_eq!(env["AIBD_PREVIEW"], "1");
    assert_eq!(env["AIBD_BIND"], "127.0.0.1:0");
    // 519d0 11-19 21:32: it reads the persona cards its parent reads, not its own HOME's
    assert_eq!(
        env["AIBD_PERSONA_CARDS"],
        dir.path().join("cards.json").to_string_lossy().as_ref(),
        "reason: preview_cards_not_parents {seen}"
    );
    assert!(
        env.get("SSH_AUTH_SOCK").is_none(),
        "reason: env_carried {seen}"
    );
    let extra = seen["fds"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|f| !matches!(f.as_str(), Some("0" | "1" | "2")))
        .count();
    assert!(
        extra <= 1,
        "reason: fd_inherited (one is the listing's own) {seen}"
    );

    // any page stops it; the books say who agreed to which file, and who stopped it
    let r = act(
        user.clone(),
        json!({"type": "preview_stop", "id": id}).to_string(),
    )
    .await;
    assert_eq!(
        (r.code, &r.json["was_running"]),
        (200, &json!(true)),
        "{}",
        r.body
    );
    let r = d.call("GET", "/api/v1/previews", Some(&page), None).await;
    assert_eq!(row(&r.json, &id)["running"], false, "{}", r.body);
    let r = act(page.clone(), open).await;
    assert_eq!(
        (r.code, r.json["code"].as_str()),
        (409, Some("preview_not_running")),
        "{}",
        r.body
    );
    let r = d.call("GET", "/api/v1/events?cursor=0", None, None).await;
    let agreed = events_of(&r.json, "preview.consented");
    assert_eq!(agreed.len(), 1, "reason: consent_not_recorded {}", r.body);
    assert_eq!(agreed[0]["payload"]["sha256"], sha.as_str());
    assert_eq!(agreed[0]["payload"]["page"], "built_in");
    assert_eq!(events_of(&r.json, "preview.started").len(), 1);
    assert_eq!(
        events_of(&r.json, "preview.stopped")[0]["payload"]["by"],
        "user_page"
    );
    d.shutdown().await;
}
