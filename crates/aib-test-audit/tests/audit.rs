// SPDX-License-Identifier: Apache-2.0
//! SPEC-shadow-u4-1ced7-1116 r1 §7: the nine acceptance cases, each as pure data.

use aib_test_audit::*;
use std::collections::BTreeMap;

const NOW: i64 = 1_794_800_000;

fn pass(n: u32) -> Verdict {
    Verdict {
        outcome: Outcome::Pass,
        tests_ran: Some(n),
        failed: vec![],
        output_digest: "d1".into(),
        censored: false,
    }
}

fn fresh(kind: &str) -> History {
    History {
        kind: kind.into(),
        reuse_streak: 1,
        last_real_run_at: Some(NOW - DAY),
        ever_audited: true,
        last_audit_incomparable: false,
    }
}

fn meta(e: &str, inv: &str, rule: &str) -> SourceMeta {
    SourceMeta {
        event_id: e.into(),
        invocation_id: inv.into(),
        rule_version: rule.into(),
    }
}

/// §7 1: a source whose result is the opposite of the real run is a mismatch; the unit is
/// revoked and its next reuse refused.
#[test]
fn a_forged_source_is_a_mismatch_and_the_unit_is_revoked() {
    let mut forged = pass(3);
    forged.outcome = Outcome::Fail;
    forged.failed = vec!["t::a".into()];
    assert_eq!(compare(&pass(3), &forged), Compare::Mismatch);
    let bad = meta("ev-src", "inv-1", "r1");
    let lines = on_mismatch("u-a", &bad, std::slice::from_ref(&bad), "ev-audit", NOW);
    let table: String = lines.iter().map(|e| line(e) + "\n").collect();
    let parsed = parse(&table);
    let ask = Ask {
        unit: "u-a",
        key: "k-new",
        source: "ev-other",
        rule_version: "r2",
    };
    assert_eq!(
        refused(&parsed, &ask).as_deref(),
        Some("run_source_revoked:unit:audit_mismatch")
    );
}

/// §7 1 (parts): a different count or different failed names is a mismatch too; only the
/// normalised output differing is "behavior changed"; an unfinished audit compares to nothing.
#[test]
fn compare_names_what_differs() {
    assert_eq!(compare(&pass(3), &pass(3)), Compare::Same);
    assert_eq!(compare(&pass(3), &pass(4)), Compare::Mismatch);
    // the outcome alone differing (same count, no failed names) is a mismatch
    let mut invalid = pass(3);
    invalid.outcome = Outcome::Invalid;
    assert_eq!(compare(&invalid, &pass(3)), Compare::Mismatch);
    let mut other_names = pass(3);
    other_names.outcome = Outcome::Fail;
    other_names.failed = vec!["t::b".into()];
    let mut names = other_names.clone();
    names.failed = vec!["t::a".into()];
    assert_eq!(compare(&names, &other_names), Compare::Mismatch);
    let mut out = pass(3);
    out.output_digest = "d2".into();
    assert_eq!(compare(&out, &pass(3)), Compare::BehaviorChanged);
    let mut cut = pass(3);
    cut.outcome = Outcome::Timeout;
    assert_eq!(compare(&cut, &pass(3)), Compare::Incomparable);
    let mut censored = pass(3);
    censored.censored = true;
    assert_eq!(compare(&censored, &pass(3)), Compare::Incomparable);
}

/// §7 2: the other sources written in the same invocation under the same rule version are
/// suspected and refused until lifted; others are not touched.
#[test]
fn sources_written_beside_a_bad_one_are_suspect_until_lifted() {
    let bad = meta("ev-1", "inv-1", "r1");
    let all = vec![
        bad.clone(),
        meta("ev-2", "inv-1", "r1"),
        meta("ev-3", "inv-1", "r0"),
        meta("ev-4", "inv-2", "r1"),
    ];
    assert_eq!(suspects(&bad, &all), ["ev-2"]);
    let mut entries = on_mismatch("u-a", &bad, &all, "ev-audit", NOW);
    let ask = |s| Ask {
        unit: "u-b",
        key: "k",
        source: s,
        rule_version: "r1",
    };
    let t = Ok(entries.clone());
    assert_eq!(
        refused(&t, &ask("ev-2")).as_deref(),
        Some("run_source_revoked:source:suspect")
    );
    assert_eq!(refused(&t, &ask("ev-4")), None);
    entries.push(Entry::Lift {
        scope: Scope::Source("ev-2".into()),
        at: NOW + 60,
        by: "engine".into(),
        reason: "reconfirmed by ev-9".into(),
    });
    assert_eq!(refused(&Ok(entries), &ask("ev-2")), None);
}

/// §7 3: flaky needs a pass and a fail on the same key and environment; a retry after a red is
/// not evidence, and different environments do not count together.
#[test]
fn flaky_needs_both_results_on_the_same_key_and_environment() {
    let run = |key: &str, env: &str, outcome, retry| RealRun {
        key: key.into(),
        env_id: env.into(),
        outcome,
        retry_after_red: retry,
    };
    assert!(flaky(&[
        run("k", "e", Outcome::Pass, false),
        run("k", "e", Outcome::Fail, false),
    ]));
    assert!(!flaky(&[
        run("k", "e", Outcome::Fail, false),
        run("k", "e", Outcome::Pass, true),
    ]));
    assert!(!flaky(&[
        run("k", "e1", Outcome::Pass, false),
        run("k", "e2", Outcome::Fail, false),
    ]));
    assert!(!flaky(&[
        run("k1", "e", Outcome::Pass, false),
        run("k2", "e", Outcome::Fail, false),
    ]));
}

/// §7 4: each declared class is never reused; the words round-trip through the table.
#[test]
fn each_never_reuse_class_is_named_and_reads_back() {
    let d = |f: fn(&mut Declared)| {
        let mut x = Declared::default();
        f(&mut x);
        never_reuse(x)
    };
    assert_eq!(
        d(|x| x.writes_worktree = true),
        Some(NeverReuse::WritesWorktree)
    );
    assert_eq!(
        d(|x| x.reads_outside = true),
        Some(NeverReuse::ReadsOutside)
    );
    assert_eq!(d(|x| x.side_effect = true), Some(NeverReuse::SideEffect));
    assert_eq!(
        d(|x| x.time_random_home = true),
        Some(NeverReuse::TimeRandomHome)
    );
    assert_eq!(d(|_| {}), None);
    // a side effect wins over the others
    assert_eq!(
        d(|x| {
            x.writes_worktree = true;
            x.side_effect = true;
        }),
        Some(NeverReuse::SideEffect)
    );
    for n in NeverReuse::ALL {
        assert_eq!(NeverReuse::from_word(n.word()), Some(n));
        let why = Why::NeverReuse(n);
        assert_eq!(Why::from_word(&why.word()), Some(why));
    }
}

/// §7 5: no round yet, a changed environment, or past the period ⇒ this run is the round.
#[test]
fn the_defense_round_is_due_when_overdue_first_or_after_a_change() {
    assert_eq!(
        defense_due(None, 7, false, NOW),
        Defense::Due {
            overdue_days: None,
            why: "first"
        }
    );
    assert_eq!(
        defense_due(Some(NOW - 8 * DAY), 7, false, NOW),
        Defense::Due {
            overdue_days: Some(1),
            why: "overdue"
        }
    );
    assert_eq!(
        defense_due(Some(NOW - 6 * DAY), 7, false, NOW),
        Defense::NotDue
    );
    assert_eq!(
        defense_due(Some(NOW - DAY / 2), 1, true, NOW),
        Defense::Due {
            overdue_days: None,
            why: "env_changed"
        }
    );
    // internal trial: daily
    assert!(matches!(
        defense_due(Some(NOW - DAY), 1, false, NOW),
        Defense::Due { why: "overdue", .. }
    ));
}

/// §7 6: a unit whose real input was never listed reuses a stale result; the round finds it
/// against the snapshot, and a defense round always runs.
#[test]
fn the_defense_round_finds_a_unit_that_would_have_reused_a_stale_result() {
    let h = fresh("test");
    let c = choose(&h, &BTreeMap::new(), true, 1, "u", Some(NOW - DAY), NOW);
    assert_eq!(c.audit_kind, AuditKind::Defense);
    assert_eq!(c.decision_code, "run_defense_round");
    let mut snapshot = BTreeMap::new();
    snapshot.insert("u-ok".to_string(), pass(2));
    snapshot.insert("u-missed".to_string(), pass(2));
    let mut actual = snapshot.clone();
    let mut red = pass(2);
    red.outcome = Outcome::Fail;
    red.failed = vec!["t::reads_unlisted".into()];
    actual.insert("u-missed".to_string(), red);
    assert_eq!(
        defense_findings(&snapshot, &actual),
        [("u-missed".to_string(), Compare::Mismatch)]
    );
}

/// §7 7: the seed alone redraws the same random audits; the rate is 1/5 early and 1/10 later.
#[test]
fn the_same_seed_draws_the_same_audits() {
    let units: Vec<String> = (0..200).map(|i| format!("u{i}")).collect();
    let picked = |seed: u64| -> Vec<String> {
        units
            .iter()
            .filter(|u| {
                choose(
                    &fresh("test"),
                    &BTreeMap::new(),
                    false,
                    seed,
                    u,
                    Some(NOW - DAY),
                    NOW,
                )
                .audit_kind
                    == AuditKind::Random
            })
            .cloned()
            .collect()
    };
    assert_eq!(picked(42), picked(42));
    assert_ne!(picked(42), picked(43));
    // early rate ~1/5 of 200
    let n = picked(42).len();
    assert!((20..=60).contains(&n), "{n}");
    assert_eq!(rate(Some(NOW - 13 * DAY), NOW), EARLY_RATE);
    assert_eq!(rate(Some(NOW - 14 * DAY), NOW), LATER_RATE);
    assert_eq!(rate(None, NOW), EARLY_RATE);
    let c = choose(
        &fresh("test"),
        &BTreeMap::new(),
        false,
        42,
        &picked(42)[0],
        None,
        NOW,
    );
    assert_eq!(c.inclusion_probability, Some(EARLY_RATE));
    // the defense round's order is fixed by the seed too
    assert_eq!(shuffled(&units, 7), shuffled(&units, 7));
    let mut back = shuffled(&units, 7);
    back.sort();
    let mut sorted = units.clone();
    sorted.sort();
    assert_eq!(back, sorted);
}

/// §7 8: one bad line makes the table unreadable, and an unreadable table refuses every reuse.
#[test]
fn a_broken_table_refuses_every_reuse() {
    let good = line(&Entry::Revoke {
        scope: Scope::Unit("u-x".into()),
        why: Why::Manual,
        at: NOW,
        by: "user".into(),
        evidence: vec![],
    });
    let broken = format!("{good}\n{{\"op\": \"revoke\"\n");
    let t = parse(&broken);
    assert!(t.is_err());
    let ask = Ask {
        unit: "u-anything",
        key: "k",
        source: "s",
        rule_version: "r",
    };
    assert!(refused(&t, &ask)
        .unwrap()
        .starts_with("run_source_revoked:table_unreadable:"));
    // a lift without a reason is a bad line too
    let no_reason = "{\"op\":\"lift\",\"scope\":\"unit\",\"id\":\"u-x\",\"at\":1,\"by\":\"user\",\"reason\":\" \"}";
    assert!(parse(no_reason).is_err());
    // an empty table allows
    assert_eq!(refused(&parse(""), &ask), None);
}

/// §7 9: a kind audited at under half the overall share gets a forced audit for a unit never
/// audited; the other forced reasons each force one.
#[test]
fn forced_audits_cover_streaks_age_rare_kinds_and_unfinished_audits() {
    let mut kinds = KindCounts::new();
    kinds.insert("test".into(), (40, 200));
    kinds.insert("knife".into(), (1, 100));
    assert!(undersampled(&kinds, "knife"));
    assert!(!undersampled(&kinds, "test"));
    assert!(undersampled(&kinds, "lint"), "a kind not seen yet");
    assert!(!undersampled(&KindCounts::new(), "knife"));
    // a seed that draws no random audit for "u", so the forced reasons decide
    let quiet = (0..u64::MAX)
        .find(|s| draw(*s, "u") >= EARLY_RATE)
        .expect("some seed draws no audit");
    let forced = |h: &History| choose(h, &kinds, false, quiet, "u", Some(NOW - DAY), NOW);
    let mut knife = fresh("knife");
    knife.ever_audited = false;
    let c = forced(&knife);
    assert_eq!(c.audit_kind, AuditKind::Forced);
    assert_eq!(
        c.reason_codes,
        ["run_audit_forced", "forced:kind_undersampled"]
    );
    let mut streak = fresh("test");
    streak.reuse_streak = STREAK_LIMIT;
    assert_eq!(forced(&streak).reason_codes[1], "forced:streak10");
    let mut old = fresh("test");
    old.last_real_run_at = Some(NOW - AGE_LIMIT_DAYS * DAY);
    assert_eq!(forced(&old).reason_codes[1], "forced:age7d");
    let mut cut = fresh("test");
    cut.last_audit_incomparable = true;
    assert_eq!(forced(&cut).reason_codes[1], "forced:incomparable");
    // nothing forcing and not drawn ⇒ reuse, no probability on the row
    let mut drawn_out = None;
    for seed in 0..1000u64 {
        let c = choose(
            &fresh("test"),
            &kinds,
            false,
            seed,
            "u",
            Some(NOW - DAY),
            NOW,
        );
        if c.audit_kind == AuditKind::None {
            drawn_out = Some(c);
            break;
        }
    }
    let c = drawn_out.expect("some seed draws no audit");
    assert_eq!(c.decision_code, "reuse_same_key");
    assert_eq!(c.inclusion_probability, None);
}

/// spec §1 order (5d33a f6c37623 M1): the random draw comes before the forced reasons, so a unit
/// that is also forced can land in the random sample (with its probability, still naming why it
/// was forced); otherwise the units reused longest never reach the sample.
#[test]
fn a_forced_unit_can_still_be_drawn_into_the_random_sample() {
    let mut streak = fresh("test");
    streak.reuse_streak = STREAK_LIMIT;
    let drawn = (0..u64::MAX)
        .find(|s| draw(*s, "u") < EARLY_RATE)
        .expect("some seed draws an audit");
    let c = choose(&streak, &KindCounts::new(), false, drawn, "u", None, NOW);
    assert_eq!(c.audit_kind, AuditKind::Random);
    assert_eq!(c.decision_code, "run_audit_random");
    assert_eq!(c.inclusion_probability, Some(EARLY_RATE));
    assert_eq!(c.reason_codes, ["run_audit_random", "forced:streak10"]);
}
