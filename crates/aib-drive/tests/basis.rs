//! a9136 11-17 (METHOD-drive-hitrate-5d33a r1 columns 2–8): act writes one basis line per
//! planned dm, with the rows it carries, how old their facts were and the recipient's facts.

use aib_drive::act::{
    basis_line, basis_send_line, branch_head_in, branch_seat, review_seat, reviews_at_head,
    rfc3339_ms, rfc3339_to_ms, row_author, screen_rows, screen_rows_at, History, NextOut, Row,
    SeatsOut, Would,
};
use aib_drive::source::DmLookup;
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
    let pending = std::env::temp_dir().join(format!("aib-drive-basis-{}", std::process::id()));
    std::fs::create_dir_all(&pending).expect("dir");
    std::fs::write(
        pending.join("REVIEW-812-1ced7-8bcef26f.md"),
        format!("結論: 通過 頭: {HEAD} H/M/L/N: 0/0/0/0 範圍: 全文\n"),
    )
    .expect("write");
    std::fs::write(pending.join("REVIEW-other.md"), "結論: 通過 頭: 0000\n").expect("write");
    std::fs::write(pending.join("NOTE-x.md"), format!("頭: {HEAD}\n")).expect("write");
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
        Some(&pending),
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
    // X5: the review already bound to this head (only REVIEW-*.md, only the first line)
    assert_eq!(
        rows[0]["review_at_head"],
        json!(["REVIEW-812-1ced7-8bcef26f.md"])
    );
    assert_eq!(reviews_at_head(&pending, "ffff"), Vec::<String>::new());
    let _ = std::fs::remove_dir_all(&pending);

    // X4: at send time the PR is read live beside the planned head
    struct Gh(&'static str);
    impl DmLookup for Gh {
        fn all(&self, _seat: &str) -> Result<Value, String> {
            Ok(json!([]))
        }
        fn pr(&self, _n: u64) -> Result<Value, String> {
            Ok(json!({"state": "open", "head": {"sha": self.0}}))
        }
    }
    let same: Value =
        serde_json::from_str(&basis_send_line(&would(vec![1]), &next, &Gh(HEAD), NOW))
            .expect("json");
    assert_eq!(same["kind"], "basis_send");
    assert_eq!(same["prs"][0]["pr_head_at_send"], HEAD);
    assert_eq!(same["prs"][0]["same"], true);
    let moved: Value =
        serde_json::from_str(&basis_send_line(&would(vec![1]), &next, &Gh("abc"), NOW))
            .expect("json");
    assert_eq!(moved["prs"][0]["pr_head_at_send"], "abc");
    assert_eq!(moved["prs"][0]["same"], false);
    assert_eq!(moved["prs"].as_array().map(Vec::len), Some(1));
}

#[test]
fn a_review_row_whose_head_has_a_review_and_a_pr_row_with_no_head_are_not_planned() {
    let pending = std::env::temp_dir().join(format!("aib-drive-screen-{}", std::process::id()));
    std::fs::create_dir_all(&pending).expect("dir");
    let other = "a6c647e48276e9ad04709f991f7a045465876e6d";
    let own = "0982729f439b6be23f57b50166bc649d8a2d4969";
    // a review by another seat binds HEAD; the author's own review binds `own` (5d33a r1 M1)
    for (f, h) in [
        ("REVIEW-812-1ced7-8bcef26f.md", HEAD),
        ("REVIEW-explain-a9136-0982729f.md", own),
    ] {
        std::fs::write(
            pending.join(f),
            format!("結論: 通過 頭: {h} H/M/L/N: 0/0/0/0 範圍: 全文\n"),
        )
        .expect("write");
    }
    let next: NextOut = serde_json::from_value(json!({
        "complete": true,
        "rows": [
            {"rank": 1, "layer": 3, "pr": 812, "head": HEAD, "branch": "fix/aibd-j0-forget-a9136-1116", "to": "1ced7"},
            {"rank": 2, "layer": 2, "kind": "fix", "pr": 825, "to": "56cda"},
            {"rank": 3, "layer": 3, "pr": 827, "head": other, "to": "5d33a"},
            {"rank": 4, "layer": 5, "node": "S79-explain", "to": "a9136"},
            {"rank": 5, "layer": 2, "kind": "fix", "pr": 826, "head": "3db09796", "to": "043dd"},
            {"rank": 6, "layer": 2, "kind": "fix", "what": "修 aibd 送信實送測試", "to": "56cda"},
            {"rank": 7, "layer": 3, "pr": 840, "head": own, "branch": "feat/drive-explain-a9136-1117", "to": "5d33a"}
        ]
    }))
    .expect("next");
    let (kept, skips) = screen_rows(&next, Some(&pending));
    let ranks: Vec<u64> = kept.rows.iter().map(|r| r.rank).collect();
    // rank 7 stays: the only review at its head is its author's own
    assert_eq!(ranks, [3, 4, 7]);
    assert!(kept.complete);
    let why: Vec<(u64, &str)> = skips.iter().map(|s| (s.rank, s.why.as_str())).collect();
    assert_eq!(why.len(), 4, "{why:?}");
    assert!(
        why[0].1.starts_with("review already at 8bcef26f"),
        "{why:?}"
    );
    assert!(why[1].1.starts_with("#825: no 40-hex head"), "{why:?}");
    assert!(why[2].1.starts_with("#826: no 40-hex head"), "{why:?}");
    // a fix row with neither PR nor head (5d33a r1 M2: trial row 6)
    assert!(why[3].1.starts_with("rank 6: no 40-hex head"), "{why:?}");
    // with no pending folder only the head rule holds
    let (kept, _) = screen_rows(&next, None);
    assert_eq!(
        kept.rows.iter().map(|r| r.rank).collect::<Vec<_>>(),
        [1, 3, 4, 7]
    );
    let _ = std::fs::remove_dir_all(&pending);
}

#[test]
fn a_branch_row_whose_branch_moved_or_is_gone_is_not_planned_and_a_check_file_is_a_review() {
    let pending = std::env::temp_dir().join(format!("aib-drive-screen-at-{}", std::process::id()));
    std::fs::create_dir_all(&pending).expect("dir");
    let stack = "6cdd8267daf4f7358d351ce0b4dd4c93643169fc";
    let old = "8a314005f455f4c723cd8fe1fba31fbf6b1be96a";
    let now = "c9a025719cbf83ef509c83ce235bd0066d5cbf6b";
    // an integration stack checked by 5d33a and rechecked by a9136 (5d33a M3)
    for f in [
        "CHECK-integ1-5d33a-6cdd8267.md",
        "RECHECK-integ1-827-a9136-6cdd8267.md",
    ] {
        std::fs::write(
            pending.join(f),
            format!("結論: 通過 頭: {stack} H/M/L/N: 0/0/0/1 範圍: 增量\n"),
        )
        .expect("write");
    }
    assert_eq!(
        reviews_at_head(&pending, stack),
        [
            "CHECK-integ1-5d33a-6cdd8267.md",
            "RECHECK-integ1-827-a9136-6cdd8267.md"
        ]
    );
    let next: NextOut = serde_json::from_value(json!({
        "complete": true,
        "rows": [
            {"rank": 1, "layer": 2, "kind": "fix", "head": old, "branch": "feat/drive-explain-a9136-1117", "to": "a9136"},
            {"rank": 2, "layer": 2, "kind": "fix", "head": old, "branch": "feat/gone-a9136-1117", "to": "a9136"},
            {"rank": 3, "layer": 2, "kind": "fix", "head": now, "branch": "feat/same-a9136-1117", "to": "a9136"},
            {"rank": 4, "layer": 3, "pr": 827, "head": stack, "branch": "integ/batch1-drive-f1fae-1117", "to": "1ced7"},
            {"rank": 5, "layer": 2, "kind": "fix", "pr": 840, "head": old, "branch": "feat/drive-explain-a9136-1117", "to": "a9136"},
            {"rank": 6, "layer": 2, "kind": "fix", "head": old, "branch": "feat/unknown-a9136-1117", "to": "a9136"}
        ]
    }))
    .expect("next");
    let lookup = |b: &str| match b {
        "feat/drive-explain-a9136-1117" | "feat/same-a9136-1117" => Some(Some(now.to_owned())),
        "feat/gone-a9136-1117" => Some(None),
        _ => None,
    };
    let (kept, skips) = screen_rows_at(&next, Some(&pending), &lookup);
    // 3 is at its branch head, 5 has a PR (the PR's head is the fact), 6 cannot be looked up
    assert_eq!(
        kept.rows.iter().map(|r| r.rank).collect::<Vec<_>>(),
        [3, 5, 6]
    );
    let why: Vec<(u64, &str)> = skips.iter().map(|s| (s.rank, s.why.as_str())).collect();
    assert_eq!(why.len(), 3, "{why:?}");
    assert!(
        why[0].1.starts_with(
            "branch feat/drive-explain-a9136-1117 moved off the row's head 8a314005 to c9a02571"
        ),
        "{why:?}"
    );
    assert_eq!(why[1].1, "branch feat/gone-a9136-1117 is gone");
    assert!(
        why[2]
            .1
            .starts_with("review already at 6cdd8267: CHECK-integ1-5d33a"),
        "{why:?}"
    );
    // with no lookup the old rules only: 1, 2 and 6 stay
    let (kept, _) = screen_rows(&next, Some(&pending));
    assert_eq!(
        kept.rows.iter().map(|r| r.rank).collect::<Vec<_>>(),
        [1, 2, 3, 5, 6]
    );
    let _ = std::fs::remove_dir_all(&pending);
}

#[test]
fn a_branch_is_looked_up_in_a_real_clone_and_odd_names_are_not() {
    let repo = std::env::temp_dir().join(format!("aib-drive-bh-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&repo);
    std::fs::create_dir_all(&repo).expect("dir");
    let git = |args: &[&str]| {
        let ok = std::process::Command::new("git")
            .arg("-C")
            .arg(&repo)
            .args(["-c", "user.name=t", "-c", "user.email=t@example.invalid"])
            .args(args)
            .output()
            .is_ok_and(|o| o.status.success());
        assert!(ok, "git {args:?}");
    };
    git(&["init", "-q"]);
    git(&["commit", "-q", "--allow-empty", "-m", "one"]);
    git(&["branch", "feat/x-a9136-1117"]);
    let h = branch_head_in(&repo, "feat/x-a9136-1117").expect("looked up");
    assert!(h.as_deref().is_some_and(|h| h.len() == 40), "{h:?}");
    assert_eq!(branch_head_in(&repo, "feat/none-a9136-1117"), Some(None));
    // only on origin (the scan reads refs/remotes/origin too; 5d33a cf7e9e3e L1)
    let head = h.clone().expect("head");
    git(&[
        "update-ref",
        "refs/remotes/origin/feat/remote-a9136-1117",
        &head,
    ]);
    assert_eq!(
        branch_head_in(&repo, "feat/remote-a9136-1117"),
        Some(Some(head))
    );
    // an option-like or spaced name is never handed to git
    assert_eq!(branch_head_in(&repo, "--all"), None);
    assert_eq!(branch_head_in(&repo, "a b"), None);
    // not a repo: unknown, not gone
    let outside = std::env::temp_dir().join(format!("aib-drive-bh-none-{}", std::process::id()));
    std::fs::create_dir_all(&outside).expect("dir");
    assert_eq!(branch_head_in(&outside, "feat/x-a9136-1117"), None);
    let _ = std::fs::remove_dir_all(&repo);
    let _ = std::fs::remove_dir_all(&outside);
}

#[test]
fn the_author_is_the_seat_in_the_branch_name_and_a_review_names_its_reviewer() {
    assert_eq!(branch_seat("feat/drive-explain-a9136-1117"), Some("a9136"));
    assert_eq!(branch_seat("integ-src/j0-forget-a9136"), Some("a9136"));
    assert_eq!(branch_seat("feat/release-p7-56cda-1116"), Some("56cda"));
    // digits only (a date) are not a seat
    assert_eq!(branch_seat("docs/notes-11171"), None);
    assert_eq!(
        review_seat("REVIEW-s79-inst13-5d33a-a6c647e4.md"),
        Some("5d33a")
    );
    assert_eq!(review_seat("REVIEW-x.md"), None);
    let open: Row = serde_json::from_value(json!({"rank": 1, "layer": 3, "to": "5d33a",
        "branch": "feat/drive-explain-a9136-1117"}))
    .expect("row");
    assert_eq!(row_author(&open).as_deref(), Some("a9136"));
    let fix: Row =
        serde_json::from_value(json!({"rank": 2, "layer": 2, "kind": "fix", "to": "56cda"}))
            .expect("row");
    assert_eq!(row_author(&fix).as_deref(), Some("56cda"));
    // a review row with no seat in its branch has no known author (never its reviewer)
    let anon: Row =
        serde_json::from_value(json!({"rank": 3, "layer": 3, "to": "5d33a"})).expect("row");
    assert_eq!(row_author(&anon), None);
}
