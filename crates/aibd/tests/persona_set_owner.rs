#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
#![allow(clippy::disallowed_methods)] // the shared harness polls on a plain sleep / timeout
#![cfg(unix)]

//! A1 (ebab8 11-23 09:11 standing rule: an owner gate and a built-in-page-only gate have an
//! integration test and knives). persona_set: a change with no ticket is asked and writes
//! nothing; a ticket the owner gave for other words is not approved and writes nothing; its
//! own ticket gets past the gate. A user folder page reads and changes no persona, brain or
//! role, and neither lists nor takes back a seat's notes. Its own binary: the owner (a fake
//! presence that says yes and counts) is set once, before aibd starts.

#[path = "support/seat_input_harness.rs"]
mod harness;
use harness::Daemon;
use serde_json::{json, Value};
use sha2::Digest;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

/// Says yes to every window, and counts them.
struct Yes(Arc<AtomicUsize>);

impl aibd::touch::Presence for Yes {
    fn ask(&self, _reason: &str, _msg: &[u8]) -> Result<(), &'static str> {
        self.0.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}

/// Lets every window open.
struct Open;

impl aibd::owner_ok::WindowGate for Open {
    fn open(&self) -> Result<(), &'static str> {
        Ok(())
    }
    fn closed(&self, _yes: bool) {}
}

/// The window answers on its own thread: wait until `n` windows were answered.
async fn answered(n: &AtomicUsize, want: usize) {
    for _ in 0..100 {
        if n.load(Ordering::SeqCst) >= want {
            // the answer is written just after the presence returns
            tokio::time::sleep(std::time::Duration::from_millis(100)).await;
            return;
        }
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }
    panic!("reason: window_never_asked");
}

#[tokio::test(flavor = "multi_thread")]
async fn a_persona_changes_only_with_its_own_ticket_and_only_from_the_built_in_page() {
    let n = Arc::new(AtomicUsize::new(0));
    aibd::owner_ok::set_presence(Arc::new(Yes(n.clone())), Arc::new(Open));
    let dir = Box::leak(Box::new(tempfile::tempdir().unwrap()));
    let root = std::fs::canonicalize(dir.path()).unwrap();
    let mut d = Daemon::start(&root).await;
    // a card aibd can read (cards.json is read on every ask): the registry's shape
    let vault = root.join("vault");
    let card_dir = vault.join("lib").join("pw0set");
    std::fs::create_dir_all(&card_dir).unwrap();
    let text = "---\nuid: pw0set\nname: \"寫卡測試\"\n---\n\n## 職責\n\n舊的職責\n";
    let card = card_dir.join("角色.md");
    std::fs::write(&card, text).unwrap();
    let sha: String = sha2::Sha256::digest(text.as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    let doc = json!({
        "schema_version": "aib-cards/v2",
        "source": {"vault_root": vault.to_str().unwrap()},
        "cards": {
            "un0eba": {"uid": "un0eba", "display_name": "送字測試", "persona_sha": "a".repeat(64)},
            "pw0set": {"uid": "pw0set", "display_name": "寫卡測試", "persona_sha": sha,
                       "card_path_rel": "lib/pw0set/角色.md"}
        }
    });
    std::fs::write(root.join("cards.json"), doc.to_string()).unwrap();

    let cred = d.page(true).await;
    let post = |c: &str, b: Value| {
        let (d, c) = (&d, c.to_owned());
        async move {
            d.call("POST", "/api/v1/act", Some(&c), Some(&b.to_string()))
                .await
        }
    };
    let r = post(
        &cred,
        json!({"type": "persona_read", "persona_uid": "pw0set"}),
    )
    .await;
    assert_eq!(r.code, 200, "{}", r.body);
    assert_eq!(r.json["base_sha"], sha.as_str(), "{}", r.body);
    let set = |duty: &str, t: Option<&str>| {
        let mut b = json!({"type": "persona_set", "persona_uid": "pw0set", "base_sha": sha,
                           "duty": duty});
        if let Some(t) = t {
            b["ticket"] = json!(t);
        }
        b
    };

    // (a) no ticket: asked, nothing written
    let r = post(&cred, set("新的職責", None)).await;
    assert_eq!(r.code, 202, "reason: persona_set_not_asked {}", r.body);
    let t = r.json["ticket"].as_str().unwrap().to_owned();
    answered(&n, 1).await;
    assert_eq!(
        std::fs::read_to_string(&card).unwrap(),
        text,
        "reason: written_unasked"
    );

    // (b) that ticket for other words: not approved, nothing written
    let r = post(&cred, set("別的職責", Some(&t))).await;
    assert_eq!(r.code, 403, "reason: other_words_written {}", r.body);
    assert!(r.body.contains("owner_check_not_approved"), "{}", r.body);
    assert_eq!(std::fs::read_to_string(&card).unwrap(), text);

    // (c) a user folder page: no persona read or changed, no notes listed or taken back
    let user = d.page_form(true, "&to=user").await;
    for b in [
        json!({"type": "persona_read", "persona_uid": "pw0set"}),
        set("新的職責", Some(&t)),
        json!({"type": "role_archive", "uid": "rol_abc123", "idem_key": "u-1"}),
    ] {
        let r = post(&user, b).await;
        assert_eq!(r.code, 403, "reason: user_page_registry {}", r.body);
        assert!(r.body.contains("registry_built_in_page_only"), "{}", r.body);
    }
    for b in [
        json!({"type": "brain_note_list", "seat": "seat_x"}),
        json!({"type": "brain_note_delete", "seat": "seat_x"}),
    ] {
        let r = post(&user, b).await;
        assert_eq!(r.code, 403, "reason: user_page_notes {}", r.body);
        assert!(r.body.contains("page_only"), "{}", r.body);
    }
    assert_eq!(std::fs::read_to_string(&card).unwrap(), text);

    // (d) its own ticket gets past the gate and updates the card (N12)
    let asked = n.load(Ordering::SeqCst);
    let r = post(&cred, set("新的職責", Some(&t))).await;
    assert_eq!(
        r.code, 200,
        "reason: approved_persona_set_refused {} {}",
        r.code, r.body
    );
    assert!(
        std::fs::read_to_string(&card).unwrap().contains("新的職責"),
        "reason: card_not_updated"
    );
    assert_eq!(n.load(Ordering::SeqCst), asked);
    d.shutdown().await;
}
