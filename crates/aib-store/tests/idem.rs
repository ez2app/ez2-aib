#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! Idempotency keys claimed before the work and finished after it (T81): sessions start
//! and stop processes, so the key cannot be written with the result in one transaction.

use aib_store::{IdemClaim, IdemKey, Store};
use serde_json::json;

const NOW: &str = "2026-11-13T00:00:00.000Z";

fn key(k: &str, request: &str) -> IdemKey {
    IdemKey {
        scope: "POST /api/v1/sessions".into(),
        key: k.into(),
        request: request.into(),
    }
}

#[test]
fn a_claimed_key_is_in_progress_until_it_is_finished_then_replays() {
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    let k = key("k1", "sha256:a");
    assert_eq!(s.idem_claim(&k, NOW).unwrap(), IdemClaim::Fresh);
    // the same key again while the first one still works: never a second start
    assert_eq!(s.idem_claim(&k, NOW).unwrap(), IdemClaim::InProgress);
    assert_eq!(
        s.idem_claim(&key("k1", "sha256:b"), NOW).unwrap(),
        IdemClaim::Conflict
    );
    assert!(s.idem_finish(&k, 201, &json!({"id": "s_1"})).unwrap());
    assert_eq!(
        s.idem_claim(&k, NOW).unwrap(),
        IdemClaim::Replay {
            status: 201,
            response: json!({"id": "s_1"})
        }
    );
    // a finished key is not finished or released again
    assert!(!s.idem_finish(&k, 202, &json!({"id": "s_2"})).unwrap());
    assert!(!s.idem_release(&k).unwrap());
    assert_eq!(
        s.idem_claim(&key("k1", "sha256:b"), NOW).unwrap(),
        IdemClaim::Conflict
    );
}

#[test]
fn a_released_key_can_be_claimed_again() {
    let dir = tempfile::tempdir().unwrap();
    let s = Store::open(&dir.path().join("aibd.sqlite")).unwrap();
    let k = key("k2", "sha256:a");
    assert_eq!(s.idem_claim(&k, NOW).unwrap(), IdemClaim::Fresh);
    // another request cannot release or finish someone else's claim
    assert!(!s.idem_release(&key("k2", "sha256:b")).unwrap());
    assert!(!s
        .idem_finish(&key("k2", "sha256:b"), 201, &json!({}))
        .unwrap());
    assert!(s.idem_release(&k).unwrap());
    assert_eq!(s.idem_claim(&k, NOW).unwrap(), IdemClaim::Fresh);
}

#[test]
fn scopes_are_separate_and_a_claim_left_by_a_crash_stays_in_progress() {
    let dir = tempfile::tempdir().unwrap();
    let db = dir.path().join("aibd.sqlite");
    let k = key("k3", "sha256:a");
    let other = IdemKey {
        scope: "DELETE /api/v1/sessions/s_1".into(),
        ..k.clone()
    };
    {
        let s = Store::open(&db).unwrap();
        assert_eq!(s.idem_claim(&k, NOW).unwrap(), IdemClaim::Fresh);
        assert_eq!(s.idem_claim(&other, NOW).unwrap(), IdemClaim::Fresh);
        assert!(s.idem_finish(&other, 200, &json!({"id": "s_1"})).unwrap());
    }
    // reopened as after a crash: the unfinished claim never lets the work run twice
    let s = Store::open(&db).unwrap();
    assert_eq!(s.idem_claim(&k, NOW).unwrap(), IdemClaim::InProgress);
    assert!(matches!(
        s.idem_claim(&other, NOW).unwrap(),
        IdemClaim::Replay { status: 200, .. }
    ));
}
