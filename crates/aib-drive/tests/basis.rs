//! a9136 11-17 (METHOD-drive-hitrate-5d33a r1 columns 2–8): act writes one basis line per
//! planned dm, with the rows it carries, how old their facts were and the recipient's facts.

use aib_drive::act::{basis_line, rfc3339_ms, rfc3339_to_ms, History, NextOut, SeatsOut, Would};
use serde_json::{json, Value};

const NOW: i64 = 1_794_850_000_000;
const HEAD: &str = "8bcef26f063e93052aa7fe0d89300719a1aa5a27";

#[test]
fn rfc3339_times_act_writes_read_back_to_the_same_ms_and_others_are_refused() {
    for ms in [
        0,
        1_000,
        NOW,
        NOW + 123,
        951_782_400_000, /* 2000-02-29 */
    ] {
        assert_eq!(
            rfc3339_to_ms(&rfc3339_ms(ms)),
            Some(ms),
            "{}",
            rfc3339_ms(ms)
        );
    }
    assert_eq!(
        rfc3339_to_ms("2026-11-17T01:02:03Z"),
        Some(1_794_877_323_000)
    );
    assert_eq!(
        rfc3339_to_ms("2026-11-17T01:02:03.5Z"),
        Some(1_794_877_323_500)
    );
    assert_eq!(rfc3339_to_ms("2026-11-17T01:02:03+08:00"), None);
    assert_eq!(rfc3339_to_ms("2026-13-17T01:02:03Z"), None);
    assert_eq!(rfc3339_to_ms(""), None);
}

fn would(ranks: Vec<u64>) -> Would {
    Would {
        action: "notify_fix".into(),
        to: "1ced7".into(),
        first_line: "派工: 修 #812 → 1ced7".into(),
        body: Vec::new(),
        keys: vec!["notify_fix:812:k:1ced7".into()],
        throttled: true,
        ranks,
        send_key: "notify_fix:812:k:1ced7".into(),
        dm: None,
        pr_checks: None,
    }
}

#[test]
fn a_basis_line_carries_its_rows_their_age_and_the_recipients_facts() {
    let next: NextOut = serde_json::from_value(json!({
        "complete": true,
        "rows": [
            {"rank": 1, "layer": 2, "kind": "fix", "what": "CI red", "pr": 812, "head": HEAD,
             "branch": "fix/aibd-j0-forget-a9136-1116", "to": "1ced7",
             "why_him": "author", "why_now": "smoke red", "needs": "none", "size": "slice",
             "data_at": rfc3339_ms(NOW - 5 * 60_000)},
            {"rank": 2, "layer": 3, "kind": "review", "what": "other", "pr": 1, "to": "x",
             "needs": "none", "data_at": rfc3339_ms(NOW)}
        ]
    }))
    .expect("next");
    let seats: SeatsOut = serde_json::from_value(json!({
        "complete": true,
        "seats": [{"seat": "1ced7", "cabin": "dab9", "role": "dev", "alive": {"v": "yes"},
                   "capacity": {"unread": 2}, "dispatchable": "yes", "size": "slice"}]
    }))
    .expect("seats");
    let mut hist = History::default();
    hist.last_sent_ms.insert("1ced7".into(), NOW - 40 * 60_000);
    hist.went_to.insert(
        "notify_fix:812:k".into(),
        ("1ced7".into(), NOW - 90 * 60_000),
    );
    let v: Value = serde_json::from_str(&basis_line(
        &would(vec![1]),
        &next,
        &seats,
        &hist,
        NOW,
        true,
    ))
    .expect("json");
    assert_eq!(v["kind"], "basis");
    assert_eq!(v["key"], "notify_fix:812:k:1ced7");
    let rows = v["rows"].as_array().expect("rows");
    assert_eq!(rows.len(), 1, "only the rows this dm carries");
    assert_eq!(rows[0]["head"], HEAD);
    assert_eq!(rows[0]["pr"], 812);
    assert_eq!(rows[0]["facts_age_min"], 5);
    assert_eq!(v["seats"][0]["unread"], 2);
    assert_eq!(v["seats"][0]["dispatchable"], "yes");
    assert_eq!(
        v["last_sent_to_seat"][0]["last_sent"],
        rfc3339_ms(NOW - 40 * 60_000)
    );
    assert_eq!(v["same_work_before"][0]["went_to"], "1ced7");
    assert_eq!(
        v["same_work_before"][0]["at"],
        rfc3339_ms(NOW - 90 * 60_000)
    );
}
