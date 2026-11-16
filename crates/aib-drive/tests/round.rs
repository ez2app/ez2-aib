//! S79-own o-3 (IFACE-s79-own-gate-a9136-1116 r1 §3): `aib-drive round` prints a seat's
//! steps; read only.
use aib_drive::own::{Item, OwnOut, Skip, SCHEMA};
use aib_drive::round::steps;
use std::collections::BTreeMap;

fn out(items: Vec<Item>, plan: &str, skipped: Vec<Skip>) -> OwnOut {
    let mut sources = BTreeMap::new();
    sources.insert("plan".to_owned(), plan.to_owned());
    OwnOut {
        schema: SCHEMA,
        seat: "b433b".into(),
        role: "dev".into(),
        as_of: "2026-11-16T02:00:00Z".into(),
        total: items.len(),
        all: items.clone(),
        items,
        skipped,
        sources,
    }
}

fn item(id: &str) -> Item {
    Item {
        id: id.into(),
        what: "做 o-3".into(),
        why_you: "owner".into(),
        why_now: "前置好".into(),
        evidence: "commit:<40 hex>".into(),
        unblocks: 2,
        since: String::new(),
    }
}

/// RD1: step 0 is the first item with its evidence; step 2 names its id in done / progress.
#[test]
fn step_zero_is_the_first_item_and_the_ledger_line_names_it() {
    let t = steps(
        &out(
            vec![item("node:S79-own"), item("plan:abcd1234")],
            "ok",
            vec![],
        ),
        false,
    );
    assert!(
        t.contains("0. 能自己做的第 1 件:node:S79-own — 做 o-3"),
        "{t}"
    );
    assert!(t.contains("證據樣式:commit:<40 hex>"), "{t}");
    assert!(
        t.contains("aib-drive own done --seat b433b --id node:S79-own --evidence <type>:<ref>"),
        "{t}"
    );
    assert!(
        t.contains("aib-drive own progress --seat b433b --id node:S79-own"),
        "{t}"
    );
    assert!(!t.contains("plan:abcd1234"), "only the first item: {t}");
}

/// RD2: an empty list says why and gives the none line; an unreadable plan says so and
/// asks for no none.
#[test]
fn an_empty_list_gives_none_and_an_unreadable_plan_is_not_idleness() {
    let skip = vec![Skip {
        id: "node:X".into(),
        reason: "等 Andy".into(),
    }];
    let t = steps(&out(vec![], "ok", skip), false);
    assert!(t.contains("沒有(清單空:node:X 等 Andy)"), "{t}");
    assert!(t.contains("aib-drive own none --seat b433b --why"), "{t}");
    let t = steps(&out(vec![], "failed", vec![]), false);
    assert!(t.contains("進度樹讀不到"), "{t}");
    assert!(!t.contains("own none"), "{t}");
}

/// RD3: the throttle step only for a seat that sends, with act's own numbers.
#[test]
fn the_throttle_is_printed_only_for_a_sender_with_acts_numbers() {
    let o = out(vec![item("dm:q_1")], "ok", vec![]);
    let quiet = steps(&o, false);
    assert!(!quiet.contains("派工節流"), "{quiet}");
    assert!(quiet.contains("3. 交回前:aib now"), "{quiet}");
    let send = steps(&o, true);
    assert!(
        send.contains(&format!(
            "同一席 {} 分鐘最多一封;未讀 ≥ {} 或失聯不發",
            aib_drive::act::SEAT_GAP_MIN,
            aib_drive::act::UNREAD_MAX
        )),
        "{send}"
    );
    assert!(send.contains("4. 交回前:aib now"), "{send}");
}
