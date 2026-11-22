//! Time-saved events (aib_ledger::timesave): what a saving must say, and how the page's five
//! boxes are added up: confirmed and estimated apart, observe mode in no total, a wrong block
//! taken out with its penalty beside, and whatever cannot be read counted instead of guessed.

use aib_ledger::envelope::parse_rfc3339_ms;
use aib_ledger::events::{self, Ctx};
use aib_ledger::timesave::{self, Period, Saved, Sum, System, Unit};
use serde_json::{json, Value};

const TAIPEI: i32 = 8 * 60;

fn ms(s: &str) -> i64 {
    parse_rfc3339_ms(s).expect("rfc 3339")
}

fn ctx() -> Ctx {
    Ctx {
        host: Some("andymini".into()),
        seat: Some("c5d88".into()),
        cards: vec![],
        status_item: None,
    }
}

fn gate(saved_ms: u64, confirmed: bool, real: bool) -> Saved<'static> {
    Saved {
        system: System::Precheck,
        rule: "knife-precheck",
        verdict: "reject",
        avoided: "receipt run",
        saved_ms,
        confirmed,
        basis: "median of this machine's last 7 days of the same receipt",
        real,
        head: Some("351c5694c7"),
    }
}

fn reuse(saved_ms: u64) -> Saved<'static> {
    Saved {
        system: System::Shadow,
        rule: "reuse",
        verdict: "reuse",
        avoided: "unit crates/aib-ledger tests",
        saved_ms,
        confirmed: true,
        basis: "the source run's own time",
        real: true,
        head: None,
    }
}

fn id(v: &Value) -> String {
    v["id"].as_str().expect("id").to_string()
}

// Monday 2026-11-23 10:00 in Taipei
const NOW: &str = "2026-11-23T02:00:00.000Z";

#[test]
fn a_saving_says_which_system_what_it_avoided_and_where_its_number_comes_from() {
    let at = ms(NOW);
    let v = timesave::saved(&ctx(), &gate(52 * 60_000, false, true), at).expect("event");
    assert_eq!(v["kind"], "time_saved");
    assert_eq!(v["system"], "precheck");
    assert_eq!(v["rule"], "knife-precheck");
    assert_eq!(v["verdict"], "reject");
    assert_eq!(v["avoided"], "receipt run");
    assert_eq!(v["saved_ms"], 3_120_000);
    assert_eq!(v["confirmed"], false);
    assert_eq!(v["mode"], "real");
    assert_eq!(v["head"], "351c5694c7");
    assert_eq!(v["seat"], "c5d88");
    assert_eq!(v["at"], NOW);
    let v = timesave::saved(&ctx(), &gate(1, true, false), at).expect("event");
    assert_eq!(v["mode"], "observe");

    // a gate does not "reuse", shadow rerun does not "reject"
    let mut s = gate(1, true, true);
    s.verdict = "reuse";
    assert!(timesave::saved(&ctx(), &s, at).is_err());
    let mut s = reuse(1);
    s.verdict = "reject";
    assert!(timesave::saved(&ctx(), &s, at).is_err());
    // no number without where it comes from; no saving of nothing; a rule is one word
    let mut s = gate(1, false, true);
    s.basis = "  ";
    assert!(timesave::saved(&ctx(), &s, at)
        .unwrap_err()
        .contains("basis"));
    let mut s = gate(1, true, true);
    s.avoided = "";
    assert!(timesave::saved(&ctx(), &s, at)
        .unwrap_err()
        .contains("avoided"));
    for rule in ["", "Knife Precheck", "a b", "刀"] {
        let mut s = gate(1, true, true);
        s.rule = rule;
        assert!(timesave::saved(&ctx(), &s, at).is_err(), "{rule:?}");
    }
    assert!(timesave::confirm(&ctx(), " ", at).is_err());
    assert!(timesave::wrong(&ctx(), "x", 1, "", at).is_err());
    assert!(timesave::wrong(&ctx(), "", 1, "why", at).is_err());
}

#[test]
fn confirmed_and_estimated_are_apart_and_observe_mode_is_in_no_total() {
    let at = ms(NOW);
    let evs = vec![
        timesave::saved(&ctx(), &gate(600_000, true, true), at).expect("a"),
        timesave::saved(&ctx(), &gate(300_000, false, true), at).expect("b"),
        timesave::saved(&ctx(), &gate(9_000_000, true, false), at).expect("observe"),
        timesave::saved(&ctx(), &reuse(40_000), at).expect("c"),
    ];
    let s = timesave::summarize(&evs, at, TAIPEI);
    let want = Period {
        precheck: Sum {
            confirmed_ms: 600_000,
            estimated_ms: 300_000,
            penalty_ms: 0,
            count: 2,
        },
        shadow: Sum {
            confirmed_ms: 40_000,
            estimated_ms: 0,
            penalty_ms: 0,
            count: 1,
        },
    };
    assert_eq!(s.today, want);
    assert_eq!(s.all, want);
    assert_eq!(s.observed, 1);
    assert_eq!(s.bad, 0);
    assert_eq!(s.since_ms, Some(at));
    assert_eq!(s.all.total().confirmed_ms, 640_000);
    assert_eq!(s.all.total().estimated_ms, 300_000);
}

#[test]
fn a_checked_estimate_becomes_confirmed_and_a_wrong_block_is_taken_out_with_its_penalty() {
    let at = ms(NOW);
    let est = timesave::saved(&ctx(), &gate(300_000, false, true), at).expect("est");
    let blocked = timesave::saved(&ctx(), &gate(500_000, true, true), at).expect("blocked");
    let kept = timesave::saved(&ctx(), &gate(7_000, true, true), at).expect("kept");
    let evs = vec![
        est.clone(),
        blocked.clone(),
        kept,
        timesave::confirm(&ctx(), &id(&est), at + 1).expect("confirm"),
        timesave::wrong(
            &ctx(),
            &id(&blocked),
            120_000,
            "it was a good receipt",
            at + 2,
        )
        .expect("wrong"),
    ];
    let s = timesave::summarize(&evs, at + 10, TAIPEI);
    assert_eq!(
        s.all.precheck,
        Sum {
            confirmed_ms: 307_000,
            estimated_ms: 0,
            penalty_ms: 120_000,
            count: 2
        }
    );
    assert_eq!(s.bad, 0);
}

#[test]
fn what_cannot_be_read_is_counted_and_never_added() {
    let at = ms(NOW);
    let good = timesave::saved(&ctx(), &gate(1_000, true, true), at).expect("good");
    let est = timesave::saved(&ctx(), &gate(2_000, false, true), at).expect("est");
    let seen = timesave::saved(&ctx(), &gate(4_000, true, false), at).expect("observe");
    let mut no_basis = timesave::saved(&ctx(), &gate(8_000, true, true), at).expect("x");
    no_basis["basis"] = json!("");
    let mut odd_mode = timesave::saved(&ctx(), &gate(16_000, true, true), at).expect("x");
    odd_mode["mode"] = json!("maybe");
    let mut text_ms = timesave::saved(&ctx(), &gate(1, true, true), at).expect("x");
    text_ms["saved_ms"] = json!("32000");
    let mut misfit = timesave::saved(&ctx(), &gate(64_000, true, true), at).expect("x");
    misfit["verdict"] = json!("reuse");
    let evs = vec![
        good.clone(),
        est.clone(),
        seen.clone(),
        good.clone(), // the same id again: not a second saving
        no_basis,
        odd_mode,
        text_ms,
        misfit,
        json!("a string"),
        json!({"at": NOW}), // no kind
        timesave::confirm(&ctx(), "no-such-id", at).expect("c"),
        timesave::confirm(&ctx(), &id(&good), at).expect("c"), // already confirmed
        timesave::confirm(&ctx(), &id(&seen), at).expect("c"), // observe mode
        timesave::wrong(&ctx(), "no-such-id", 5, "why", at).expect("w"),
        timesave::wrong(&ctx(), &id(&seen), 5, "why", at).expect("w"), // observe mode
        timesave::wrong(&ctx(), &id(&est), 5, "why", at).expect("w"),
        timesave::wrong(&ctx(), &id(&est), 5, "why", at).expect("w"), // a second one
        timesave::confirm(&ctx(), &id(&est), at).expect("c"), // checked, though wrong: no error
        // another kind in the same files is not this sum's and not bad
        timesave::unit_time(
            &ctx(),
            &Unit {
                unit: "crates/a/knives/k.sh",
                ms: 9,
                runner: "macos-latest",
                size: None,
                load1: None,
                outcome: "pass",
            },
            at,
        )
        .expect("u"),
    ];
    let s = timesave::summarize(&evs, at, TAIPEI);
    assert_eq!(
        s.all.precheck,
        Sum {
            confirmed_ms: 1_000,
            estimated_ms: 0,
            penalty_ms: 5,
            count: 1
        }
    );
    assert_eq!(s.observed, 1);
    assert_eq!(s.bad, 13, "{s:?}");
}

#[test]
fn a_box_holds_what_happened_in_it_and_the_week_starts_on_monday_on_the_callers_clock() {
    // Monday 2026-11-23 00:00 in Taipei is Sunday 16:00 UTC
    let monday = ms("2026-11-22T16:00:00.000Z");
    let now = ms(NOW);
    assert_eq!(
        timesave::starts(now, TAIPEI),
        [
            monday,
            monday,
            ms("2026-10-31T16:00:00.000Z"),
            ms("2025-12-31T16:00:00.000Z"),
        ]
    );
    // on a UTC clock the same moment is Monday 02:00: the day and the week start at 00:00 UTC
    assert_eq!(
        timesave::starts(now, 0)[..2],
        [
            ms("2026-11-23T00:00:00.000Z"),
            ms("2026-11-23T00:00:00.000Z")
        ]
    );
    // 20:00 UTC on Sunday is already Monday 04:00 in Taipei: the day is the caller's, not UTC's
    assert_eq!(
        timesave::starts(ms("2026-11-22T20:00:00.000Z"), TAIPEI)[0],
        monday
    );
    // a Sunday: its week began six days before
    assert_eq!(
        timesave::starts(ms("2026-11-22T05:00:00.000Z"), 0)[1],
        ms("2026-11-16T00:00:00.000Z")
    );
    let evs = vec![
        timesave::saved(&ctx(), &gate(1, true, true), now).expect("today"),
        timesave::saved(&ctx(), &gate(10, true, true), monday - 1).expect("last week"),
        timesave::saved(
            &ctx(),
            &gate(100, true, true),
            ms("2026-10-31T15:59:59.999Z"),
        )
        .expect("last month"),
        timesave::saved(
            &ctx(),
            &gate(1_000, true, true),
            ms("2025-12-31T15:59:59.999Z"),
        )
        .expect("last year"),
    ];
    let s = timesave::summarize(&evs, now, TAIPEI);
    let c = |p: &Period| p.precheck.confirmed_ms;
    assert_eq!(
        [c(&s.today), c(&s.week), c(&s.month), c(&s.year), c(&s.all)],
        [1, 1, 11, 111, 1_111]
    );
    assert_eq!(s.since_ms, Some(ms("2025-12-31T15:59:59.999Z")));
    // a penalty is in the box it was found out in, not the box of the saving it takes out
    let old = timesave::saved(&ctx(), &gate(50, true, true), monday - 5).expect("old");
    let evs = vec![
        old.clone(),
        timesave::wrong(&ctx(), &id(&old), 7, "why", now).expect("w"),
    ];
    let s = timesave::summarize(&evs, now, TAIPEI);
    assert_eq!((s.today.precheck.penalty_ms, c(&s.month)), (7, 0));
}

#[test]
fn the_summary_is_read_from_every_programs_month_files_and_a_broken_line_is_counted() {
    let d = tempfile::tempdir().expect("tmp");
    let at = ms(NOW);
    let est = timesave::saved(&ctx(), &gate(300_000, false, true), at).expect("est");
    events::append(d.path(), "aib-precheck", &est, at).expect("append");
    let old = ms("2026-10-05T00:00:00.000Z");
    let r = timesave::saved(&ctx(), &reuse(40_000), old).expect("reuse");
    events::append(d.path(), "aib-test-rs", &r, old).expect("append");
    // the confirm is another program's line: a ref is looked up over all the files
    let c = timesave::confirm(&ctx(), &id(&est), at + 1).expect("confirm");
    events::append(d.path(), "aib-test-rs", &c, at + 1).expect("append");
    let f = events::dir(d.path(), "aib-test-rs").join(events::month_file(at));
    let mut text = std::fs::read_to_string(&f).expect("read");
    text.push_str("{\"kind\":\"time_saved\", cut short\n");
    std::fs::write(&f, text).expect("write");

    let s = timesave::summary_of(
        d.path(),
        &["aib-precheck", "aib-test-rs", "no-such-prog"],
        at + 5,
        TAIPEI,
    );
    assert_eq!(s.all.precheck.confirmed_ms, 300_000);
    assert_eq!(s.all.shadow.confirmed_ms, 40_000);
    assert_eq!(s.month.shadow.confirmed_ms, 0, "October is not this month");
    assert_eq!(s.bad, 1);
    assert_eq!(s.since_ms, Some(old));
    // nothing written yet: every box empty and no start
    let empty = tempfile::tempdir().expect("tmp");
    let s = timesave::summary_of(empty.path(), &["aib-precheck"], at, TAIPEI);
    assert_eq!((s.all, s.since_ms, s.bad), (Period::default(), None, 0));
}

#[test]
fn a_units_time_says_which_machine_and_how_big() {
    let at = ms(NOW);
    let u = Unit {
        unit: "crates/aibd/knives/knives-09-seat-talk-mcp.sh",
        ms: 1_260_000,
        runner: "macos-latest",
        size: Some(31),
        load1: Some(4.5),
        outcome: "pass",
    };
    let v = timesave::unit_time(&ctx(), &u, at).expect("event");
    assert_eq!(v["kind"], "unit_time");
    assert_eq!(v["outcome"], "pass");
    assert_eq!(v["unit"], u.unit);
    assert_eq!(v["ms"], 1_260_000);
    assert_eq!(v["runner"], "macos-latest");
    assert_eq!(v["size"], 31);
    assert_eq!(v["load1"], 4.5);
    // what the runner does not know is absent, not 0
    let v = timesave::unit_time(
        &ctx(),
        &Unit {
            size: None,
            load1: Some(f64::NAN),
            ..u.clone()
        },
        at,
    )
    .expect("event");
    assert!(v.get("size").is_none() && v.get("load1").is_none(), "{v}");
    for bad in [
        Unit {
            unit: " ",
            ..u.clone()
        },
        Unit {
            runner: "",
            ..u.clone()
        },
        Unit {
            outcome: "Pass!",
            ..u.clone()
        },
    ] {
        assert!(timesave::unit_time(&ctx(), &bad, at).is_err(), "{bad:?}");
    }
}

#[test]
fn a_confirm_or_a_wrong_that_is_not_a_whole_event_changes_nothing() {
    let at = ms(NOW);
    let est = timesave::saved(&ctx(), &gate(300_000, false, true), at).expect("est");
    let kept = timesave::saved(&ctx(), &gate(7_000, true, true), at).expect("kept");
    let whole_c = timesave::confirm(&ctx(), &id(&est), at).expect("c");
    let whole_w = timesave::wrong(&ctx(), &id(&kept), 9, "why", at).expect("w");
    let cut = |v: &Value, field: &str| {
        let mut v = v.clone();
        v.as_object_mut().expect("object").remove(field);
        v
    };
    let mut evs = vec![est.clone(), kept.clone()];
    // its own field, and each of the common ones every event carries
    let fields = [
        "ref",
        "v",
        "id",
        "at",
        "seat",
        "cards",
        "status_item",
        "host",
        "outcome",
    ];
    for field in fields {
        evs.push(cut(&whole_c, field));
        evs.push(cut(&whole_w, field));
    }
    // a saving that lost a common field is no saving, whatever its own fields say
    for field in &fields[1..] {
        evs.push(cut(
            &timesave::saved(&ctx(), &gate(4_000_000, true, true), at).expect("x"),
            field,
        ));
    }
    let mut odd_v = timesave::saved(&ctx(), &gate(8_000_000, true, true), at).expect("x");
    odd_v["v"] = json!(2);
    evs.push(odd_v);
    evs.push(cut(&whole_w, "penalty_ms"));
    evs.push(cut(&whole_w, "basis"));
    let mut bad_at = whole_c.clone();
    bad_at["at"] = json!("yesterday");
    evs.push(bad_at);
    // a saving without its rule or what it avoided is not a saving either
    evs.push(cut(
        &timesave::saved(&ctx(), &gate(1, true, true), at).expect("x"),
        "rule",
    ));
    evs.push(cut(
        &timesave::saved(&ctx(), &gate(2, true, true), at).expect("x"),
        "avoided",
    ));
    let s = timesave::summarize(&evs, at, TAIPEI);
    assert_eq!(
        s.all.precheck,
        Sum {
            confirmed_ms: 7_000,
            estimated_ms: 300_000,
            penalty_ms: 0,
            count: 2
        }
    );
    assert_eq!(s.bad, 32, "{s:?}");
}

#[test]
fn a_saving_that_was_checked_can_still_be_found_wrong() {
    let at = ms(NOW);
    let est = timesave::saved(&ctx(), &gate(300_000, false, true), at).expect("est");
    // every order of one confirm and one wrong ends the same way; a second of either is bad
    let c = || timesave::confirm(&ctx(), &id(&est), at + 1).expect("c");
    let w = || timesave::wrong(&ctx(), &id(&est), 40, "why", at + 2).expect("w");
    for (marks, bad) in [
        (vec![c(), w()], 0),
        (vec![w(), c()], 0),
        (vec![c(), c(), w()], 1),
        (vec![c(), w(), w()], 1),
    ] {
        let mut evs = vec![est.clone()];
        evs.extend(marks);
        let s = timesave::summarize(&evs, at + 10, TAIPEI);
        assert_eq!(
            s.all.precheck,
            Sum {
                confirmed_ms: 0,
                estimated_ms: 0,
                penalty_ms: 40,
                count: 0
            },
            "{s:?}"
        );
        assert_eq!(s.bad, bad, "{s:?}");
    }
}

#[test]
fn an_event_dated_after_the_readers_clock_is_in_no_box() {
    let now = ms(NOW);
    let slack = timesave::FUTURE_SLACK_MS;
    let old = timesave::saved(&ctx(), &gate(50, true, true), now - 1_000).expect("old");
    let est = timesave::saved(&ctx(), &gate(300, false, true), now - 1_000).expect("est");
    let far = ms("2027-11-23T02:00:00.000Z");
    let evs = vec![
        old.clone(),
        est.clone(),
        // another machine's clock a little ahead, up to the slack: read
        timesave::saved(&ctx(), &gate(1, true, true), now + slack).expect("at the slack"),
        // one millisecond past it, and next year: not read
        timesave::saved(&ctx(), &gate(1_000, true, true), now + slack + 1).expect("past"),
        timesave::saved(&ctx(), &gate(10_000, true, true), far).expect("far"),
        // a wrong from next year does not take `old` out, a confirm from next year checks nothing
        timesave::wrong(&ctx(), &id(&old), 7, "why", far).expect("w"),
        timesave::confirm(&ctx(), &id(&est), far).expect("c"),
    ];
    let s = timesave::summarize(&evs, now, TAIPEI);
    assert_eq!(
        s.today.precheck,
        Sum {
            confirmed_ms: 51,
            estimated_ms: 300,
            penalty_ms: 0,
            count: 3
        }
    );
    assert_eq!(s.all, s.today);
    assert_eq!(s.bad, 4);
    // the same confirm, dated now, does check the estimate
    let evs = vec![
        est.clone(),
        timesave::confirm(&ctx(), &id(&est), now).expect("c"),
    ];
    let s = timesave::summarize(&evs, now, TAIPEI);
    assert_eq!(
        (s.all.precheck.confirmed_ms, s.all.precheck.estimated_ms),
        (300, 0)
    );
}

#[test]
fn the_boxes_start_right_west_of_utc_before_1970_and_at_the_ends_of_the_range() {
    // New York (UTC-5), 2026-11-22 23:30 local is 11-23 04:30 UTC: still Sunday there
    let s = timesave::starts(ms("2026-11-23T04:30:00.000Z"), -5 * 60);
    assert_eq!(
        s,
        [
            ms("2026-11-22T05:00:00.000Z"),
            ms("2026-11-16T05:00:00.000Z"),
            ms("2026-11-01T05:00:00.000Z"),
            ms("2026-01-01T05:00:00.000Z"),
        ]
    );
    // 1969-12-31 (a Wednesday) 12:00 UTC: the week began Monday 12-29
    let s = timesave::starts(-43_200_000, 0);
    assert_eq!(
        s,
        [
            -86_400_000,
            -3 * 86_400_000,
            -31 * 86_400_000,
            -365 * 86_400_000
        ]
    );
    // no panic at the ends
    let _ = timesave::starts(i64::MAX, 14 * 60);
    let _ = timesave::starts(i64::MIN, -12 * 60);
}

#[cfg(unix)]
#[test]
fn a_folder_that_is_there_and_cannot_be_read_is_counted() {
    use std::os::unix::fs::PermissionsExt;
    let d = tempfile::tempdir().expect("tmp");
    let at = ms(NOW);
    let v = timesave::saved(&ctx(), &gate(5, true, true), at).expect("v");
    events::append(d.path(), "aib-precheck", &v, at).expect("append");
    let dir = events::dir(d.path(), "aib-precheck");
    std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o000)).expect("chmod");
    let unreadable = std::fs::read_dir(&dir).is_err();
    let s = timesave::summary_of(d.path(), &["aib-precheck"], at, TAIPEI);
    std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700)).expect("chmod back");
    // root reads any folder: there the folder was read, and the saving is counted
    if unreadable {
        assert_eq!((s.all.precheck.confirmed_ms, s.bad), (0, 1), "{s:?}");
    } else {
        assert_eq!((s.all.precheck.confirmed_ms, s.bad), (5, 0), "{s:?}");
    }
}
