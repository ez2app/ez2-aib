//! The 1.x letters still to be acted on as event cards (NOTE-import-regroup-519d0-1119 §7,
//! IFACE-ledger-cards-7ebca-1119): one card per letter, only to the cabin's live seats, from the
//! status the mirror keeps ([`crate::dm_import`] `dm_status`). A letter closed, archived or deleted
//! in the ledger leaves its card at the mirror's next pass.
//!
//! | ledger status | when | slot | card id |
//! |---|---|---|---|
//! | `hold_human` | | 1 決定 | `dmhold:<id>` |
//! | `queued` | over [`crate::slots::STUCK_MIN`] | 4 卡住 | `dmq:<id>` |
//! | `delivered` | over [`crate::slots::STUCK_MIN`] unread | 4 卡住 | `dmunread:<id>` |
//! | `read` + first line 「派工」 | over [`TASK_HOURS`] | 5 可推進 | `dmtask:<id>` |
//! | `read` + waiting for the person (`dm_wait`) | | 1 決定 | `dmwait:<id>` |
//!
//! Each card carries `open: {thread_id, message_id}` (where the page opens the letter) and the
//! plan card it is about: the letter's own `card` (the import's or the mirror's rule), else what
//! the shared rule ([`crate::import_regroup::card_of`]) finds in its first line, never a finding's
//! code ([`finding_code`]); else the seat's line decides later ([`crate::slots::attach_nodes`]).

use crate::import_regroup::{card_of, Plan};
use crate::slots::{actions, Card, NAMES, STUCK_MIN};
use aib_store::MessageRow;
use serde_json::{json, Value};

/// A dispatch read and not closed this long is a card (hours).
pub const TASK_HOURS: i64 = 2;

/// The ledger statuses a card can come from.
pub const STATUSES: [&str; 4] = ["hold_human", "queued", "delivered", "read"];

/// The longest first line on a card (characters).
pub const LINE_MAX: usize = 80;

fn s<'a>(v: &'a Value, k: &str) -> &'a str {
    v.get(k).and_then(Value::as_str).unwrap_or("")
}

/// The ledger letter id of an imported or mirrored row.
pub fn dm_id(r: &MessageRow) -> Option<String> {
    let own = r.item.get("dm_id").and_then(Value::as_str);
    let src = r
        .item
        .get("source")
        .and_then(|src| src.get("id"))
        .and_then(Value::as_str);
    [own, src]
        .into_iter()
        .flatten()
        .find(|x| !x.is_empty())
        .map(str::to_owned)
}

/// The plan the shared rule reads, without the nodes whose id is a finding's code.
pub fn plan_of(tree: Option<&serde_json::Value>) -> Plan {
    let mut p = Plan::of(tree);
    p.ids.retain(|id| !finding_code(id));
    p
}

/// A finding's code (`H1`, `M2`, `L1`, `N3`): reviews and checks write these in every line, so a
/// node with that id is never the card a letter is about (0eed7 VERIFY-dmcards L1).
/// Accepted cost (0eed7 11-19 05:1x): the plan's real `M1`–`M6` (the MVP route) are never a
/// letter's card here either; a letter about them names their title or a work word instead, or
/// the seat's line puts it there later.
pub fn finding_code(id: &str) -> bool {
    let mut c = id.chars();
    matches!(c.next(), Some('H' | 'M' | 'L' | 'N')) && id.len() > 1 && c.all(|d| d.is_ascii_digit())
}

/// What a seat waits for names the person (NOTE-import-regroup r3 §3b「等真人 ⇒ 決定」).
pub fn waits_for_person(wait: &str) -> bool {
    aib_store::PERSON_WORDS.iter().any(|w| wait.contains(w))
}

fn first_line(body: &str) -> String {
    body.lines()
        .find(|l| !l.trim().is_empty())
        .unwrap_or("")
        .trim()
        .chars()
        .take(LINE_MAX)
        .collect()
}

/// The cards of `rows` (imported letters, [`aib_store::MessageDb::imported_open`]) to seats in
/// `live`, at `now` (unix seconds); `ids` = the progress tree's node ids.
pub fn cards_of(rows: &[MessageRow], live: &[String], plan: &Plan, now: i64) -> Vec<Card> {
    let mut out = Vec::new();
    for r in rows {
        let it = Value::Object(r.item.clone());
        let to = it.get("source").map_or("", |src| s(src, "to"));
        if !live.iter().any(|l| l == to) {
            continue;
        }
        let Some(id) = dm_id(r) else { continue };
        let body = r.body.as_deref().unwrap_or("");
        let line = first_line(body);
        let age = now - r.created_unix;
        let (slot, kind, cid, state) = match s(&it, "dm_status") {
            "hold_human" => (1u8, "dm.hold", "dmhold", "在待審框等人看".to_owned()),
            // §3b: read and waiting for the person ⇒ 決定
            "read" if waits_for_person(s(&it, "dm_wait")) => (
                1,
                "dm.wait",
                "dmwait",
                format!(
                    "等真人:{}",
                    s(&it, "dm_wait").chars().take(LINE_MAX).collect::<String>()
                ),
            ),
            "queued" if age > STUCK_MIN * 60 => (
                4,
                "dm.queued",
                "dmq",
                format!("排隊超過 {STUCK_MIN} 分鐘沒送到"),
            ),
            "delivered" if age > STUCK_MIN * 60 => (
                4,
                "dm.unread",
                "dmunread",
                format!("送到超過 {STUCK_MIN} 分鐘還沒讀"),
            ),
            "read" if line.starts_with("派工") && age > TASK_HOURS * 3600 => (
                5,
                "dm.task",
                "dmtask",
                format!("派工讀了超過 {TASK_HOURS} 小時沒結"),
            ),
            _ => continue,
        };
        // the letter's own card (the import's or the mirror's rule), else the shared rule on the
        // first line only (0eed7 L1: a review's 「M1」 in the text took the tree's M1)
        // a card the import wrote counts only when the plan still names it: not a finding's code
        // it took for a node (0eed7 r2 L1, q_f84a7ea4 「M1」), not the root it fell back to
        // (`card_fallback`, 「V」 showed as the whole plan)
        let card = Some(s(&it, "card"))
            .filter(|c| plan.ids.contains(*c))
            .filter(|_| it.get("card_fallback") != Some(&json!(true)))
            .map(str::to_owned)
            .or_else(|| card_of(&line, plan).map(|(c, _)| c));
        let from = r.from_key.strip_prefix("seat:").unwrap_or(&r.from_key);
        out.push(Card {
            card_id: format!("{cid}:{id}"),
            slot,
            name: NAMES[usize::from(slot - 1)],
            kind: kind.into(),
            seat_id: Some(to.to_owned()),
            session_id: None,
            at: r.created.clone(),
            global_seq: None,
            item: Some(line),
            why: None,
            pct: None,
            card,
            rec: None,
            state: Some(state),
            src: Some(from.to_owned()),
            tag: None,
            open: Some(json!({"thread_id": r.thread_id, "message_id": r.message_id})),
            actions: actions(slot),
        });
    }
    out
}

/// The cabin's live 1.x seats and the cards of their open letters (none unless `legacy_cabin` is
/// set in `<data>/bridge.json`).
pub fn cards(db: &aib_store::MessageDb, now: i64) -> Vec<Card> {
    let cfg = crate::bridge_cfg::now();
    let Some(cabin) = cfg.legacy_cabin.as_deref() else {
        return Vec::new();
    };
    let Some(home) = crate::bridge_cfg::queue_root(&cfg, |k| std::env::var(k).ok()).and_then(|q| {
        q.parent()
            .and_then(std::path::Path::parent)
            .map(std::path::Path::to_path_buf)
    }) else {
        return Vec::new();
    };
    let live = crate::legacy_send::cabin_seats(&home.join("instances"), cabin);
    let (rows, all) = db
        .imported_open(
            crate::import::OWNER,
            &STATUSES,
            &live,
            aib_store::IMPORTED_MAX,
        )
        .unwrap_or_default();
    if all > rows.len() as u64 {
        // the oldest are kept; the newest wait for the next read
        tracing::warn!(
            shown = rows.len(),
            left_out = all - rows.len() as u64,
            "ledger cards: more open letters than one read takes"
        );
    }
    if rows.is_empty() {
        return Vec::new();
    }
    let plan = plan_of(crate::workspace::current_tree().as_ref());
    cards_of(&rows, &live, &plan, now)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(id: &str, status: &str, to: &str, body: &str, at: i64) -> MessageRow {
        let mut item = serde_json::Map::new();
        item.insert("dm_status".into(), json!(status));
        item.insert("source".into(), json!({"kind": "dm", "id": id, "to": to}));
        MessageRow {
            message_id: format!("m_{id}"),
            thread_id: "T1".into(),
            from_key: "seat:519d0".into(),
            to_key: "T1".into(),
            created: format!("t{at}"),
            created_unix: at,
            updated: String::new(),
            rev: 1,
            item,
            body: Some(body.into()),
            body_sha256: String::new(),
            len: 0,
            idem_owner: Some("import".into()),
            idem_key: Some(format!("dm:{id}")),
            journaled: false,
        }
    }

    #[test]
    fn a_letter_still_to_act_on_is_one_card_on_its_slot() {
        let now = 100_000;
        let old = now - 3 * 3600;
        let live = ["7ebca".to_owned()];
        let plan = Plan::of(Some(&json!({"nodes": [{"id": "N-web-dmbridge"}]})));
        let rows = [
            row("q_h", "hold_human", "7ebca", "請看", old),
            row("q_q", "queued", "7ebca", "排著", old),
            row("q_u", "delivered", "7ebca", "沒讀", old),
            row(
                "q_t",
                "read",
                "7ebca",
                "派工: N-web-dmbridge → 7ebca\n細節",
                old,
            ),
            row("q_r", "read", "7ebca", "一般回報", old),
            row("q_new", "delivered", "7ebca", "剛到", now - 60),
            row("q_tnew", "read", "7ebca", "派工: x", now - 600),
            row("q_gone", "delivered", "9f808", "退役席", old),
            row("q_done", "done", "7ebca", "做完", old),
            {
                let mut w = row("q_w", "read", "7ebca", "回報", old);
                w.item.insert("dm_wait".into(), json!("等 Andy 按指紋"));
                w
            },
        ];
        let c = cards_of(&rows, &live, &plan, now);
        let got: Vec<(&str, u8)> = c.iter().map(|c| (c.card_id.as_str(), c.slot)).collect();
        assert_eq!(
            got,
            [
                ("dmhold:q_h", 1),
                ("dmq:q_q", 4),
                ("dmunread:q_u", 4),
                ("dmtask:q_t", 5),
                ("dmwait:q_w", 1)
            ],
            "reason: wrong_cards"
        );
        let t = &c[3];
        assert_eq!(
            t.card.as_deref(),
            Some("N-web-dmbridge"),
            "reason: card_not_found"
        );
        assert_eq!(t.item.as_deref(), Some("派工: N-web-dmbridge → 7ebca"));
        assert_eq!(t.seat_id.as_deref(), Some("7ebca"));
        assert_eq!(t.src.as_deref(), Some("519d0"));
        assert_eq!(
            t.open,
            Some(json!({"thread_id": "T1", "message_id": "m_q_t"})),
            "reason: open_missing"
        );
        assert!(
            cards_of(&rows, &[], &plan, now).is_empty(),
            "reason: retired_seat_card"
        );
    }

    #[test]
    fn a_findings_code_is_not_a_card_and_the_text_is_not_searched() {
        let plan = plan_of(Some(
            &json!({"nodes": [{"id": "M1"}, {"id": "N-web-dmbridge"}]}),
        ));
        let live = ["7ebca".to_owned()];
        let r = row(
            "q_1",
            "hold_human",
            "7ebca",
            "REVIEW 不通過 M1\nN-web-dmbridge 那片",
            0,
        );
        let c = cards_of(&[r], &live, &plan, 10);
        assert_eq!(c[0].card.as_deref(), None, "reason: finding_code_as_card");
        let r = row("q_2", "hold_human", "7ebca", "M1 修好 N-web-dmbridge", 0);
        let c = cards_of(&[r], &live, &plan, 10);
        assert_eq!(
            c[0].card.as_deref(),
            Some("N-web-dmbridge"),
            "reason: finding_code_hid_the_card"
        );
        assert!(finding_code("M1") && finding_code("H12") && !finding_code("N-web-dmbridge"));
        assert!(!finding_code("M") && !finding_code("MX"));
    }

    #[test]
    fn the_import_rules_card_comes_first_and_the_line_is_cut() {
        let mut r = row("q_1", "hold_human", "7ebca", &"字".repeat(200), 0);
        r.item.insert("card".into(), json!("N-ws-thread-cat"));
        r.item.insert("dm_id".into(), json!("q_mirror"));
        let plan = plan_of(Some(&json!({"nodes": [
            {"id": "N-web-dmbridge"}, {"id": "N-ws-thread-cat"}, {"id": "M1"}, {"id": "V"}
        ]})));
        let live = ["7ebca".to_owned()];
        let c = cards_of(&[r.clone()], &live, &plan, 10);
        assert_eq!(
            c[0].card.as_deref(),
            Some("N-ws-thread-cat"),
            "reason: import_card_ignored"
        );
        // 0eed7 r2 L1: an import card that is a finding's code, or the root it fell back to,
        // is not the card; the first line decides
        for (card, fallback) in [("M1", false), ("V", true), ("N-ws-thread-cat", true)] {
            let mut b = row("q_2", "hold_human", "7ebca", "N-web-dmbridge 那片", 0);
            b.item.insert("card".into(), json!(card));
            b.item.insert("card_fallback".into(), json!(fallback));
            assert_eq!(
                cards_of(&[b], &live, &plan, 10)[0].card.as_deref(),
                Some("N-web-dmbridge"),
                "reason: import_card_kept {card}"
            );
        }
        assert_eq!(c[0].card_id, "dmhold:q_mirror", "reason: dm_id_not_first");
        assert_eq!(
            c[0].item.as_deref().map(|s| s.chars().count()),
            Some(LINE_MAX)
        );
    }
}
