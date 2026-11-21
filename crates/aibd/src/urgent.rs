//! 待審 1.5 期:急件一級(DESIGN-web-inbox-aibd-7ebca-1121 r3 §F;DECISION-council-webinbox-519d0
//! r2「誰能標急」;5d33a PREREAD r3 0/0/0/2).
//!
//! - A seat asks `seat_send {…, "level": "urgent"}`. aibd decides, the letter does not: the
//!   sender seat's rank (`aib_store::ranks::seat_rank`, only grants the owner's fingerprint
//!   wrote) must reach the urgent act's least rank (`perm_min`, Lead when unset or unreadable).
//! - Not enough rank, or more than [`HOUR_CAP`] urgent letters from the seat in the last hour ⇒
//!   the letter goes as a plain one (Q2: downgraded, not refused) and the answer says why.
//! - `level_by` keeps the role uid that gave the rank (the page shows its name, escaped).
//! - Letters bridged in from a 1.x ledger never pass here (dm_import writes no level): they are
//!   never urgent.

use aib_store::ranks::{self, Rank};
use aib_store::MessageDb;
use serde_json::Value;

/// The one level above plain.
pub const URGENT: &str = "urgent";
/// Urgent letters one seat may send in an hour; more go as plain ones.
pub const HOUR_CAP: u64 = 3;
/// The seat's rank is below the urgent act's least rank.
pub const RANK_TOO_LOW: &str = "rank_too_low";
/// The seat sent [`HOUR_CAP`] urgent letters in the last hour.
pub const HOUR_CAP_REACHED: &str = "urgent_hour_cap";

/// Whether a stored letter is urgent: seat_send marked it and it did not come in from a 1.x
/// ledger (`imported`; such a letter's fields are the ledger file's, which an AI can write).
pub fn is_urgent(row: &aib_store::MessageRow) -> bool {
    row.item.get("level").and_then(Value::as_str) == Some(URGENT)
        && row.item.get("imported").and_then(Value::as_bool) != Some(true)
}

/// `level` as asked: `None` / `"normal"` ⇒ plain, `"urgent"` ⇒ urgent, anything else refused.
pub fn asked(v: Option<&Value>) -> Result<bool, ()> {
    match v {
        None | Some(Value::Null) => Ok(false),
        Some(Value::String(s)) if s == "normal" => Ok(false),
        Some(Value::String(s)) if s == URGENT => Ok(true),
        _ => Err(()),
    }
}

/// Whether `seat_id` may send this letter urgent now: `Ok(role uid)` (the `level_by`) or
/// `Err(reason)` and the letter goes plain.
pub fn judge(db: &MessageDb, seat_id: &str, now_unix: i64) -> Result<String, &'static str> {
    let rank = ranks::seat_rank(db, seat_id);
    if rank < ranks::perm_min(db, ranks::URGENT) {
        return Err(RANK_TOO_LOW);
    }
    // a count in SQL, not a page of the sent box (5d33a REVIEW-urgent N1); a failed count is
    // taken as the cap reached (fails closed)
    let in_hour = db
        .sent_at_level_since(&format!("seat:{seat_id}"), URGENT, now_unix - 3600)
        .unwrap_or(u64::MAX);
    if in_hour >= HOUR_CAP {
        return Err(HOUR_CAP_REACHED);
    }
    Ok(role_of(db, seat_id, rank))
}

/// The role uid of the seat's highest live grant (the rank word when the least rank is so low
/// that a seat with no grant passes).
fn role_of(db: &MessageDb, seat_id: &str, rank: Rank) -> String {
    db.rank_grants(seat_id)
        .unwrap_or_default()
        .into_iter()
        .filter(|g| g.role_rank.unwrap_or(Rank::Plain).min(Rank::Lead) == rank)
        .map(|g| g.role_uid)
        .next()
        .unwrap_or_else(|| rank.word().to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn db() -> (tempfile::TempDir, MessageDb) {
        let d = tempfile::tempdir().unwrap();
        let db = MessageDb::open(&d.path().join(aib_store::MESSAGES_FILE)).unwrap();
        (d, db)
    }

    const AT: &str = "2026-11-21T13:00:00+08:00";

    fn sent(db: &MessageDb, seat: &str, level: Option<&str>, at: i64) {
        let mut row =
            crate::seat_send::queued_row(seat, "s_ffffffffffffffff", Some("work"), "hi", "00")
                .unwrap();
        row.item.insert("sent_at".into(), json!(at));
        row.created_unix = at;
        if let Some(l) = level {
            row.item.insert("level".into(), json!(l));
        }
        db.insert(row).unwrap();
    }

    #[test]
    fn a_bridged_letter_is_never_urgent() {
        let mut row = crate::seat_send::queued_row(
            "s_0123456789abcdef",
            "s_ffffffffffffffff",
            None,
            "x",
            "0",
        )
        .unwrap();
        row.item.insert("level".into(), json!(URGENT));
        row.item.insert("level_by".into(), json!("role_lead"));
        let o = crate::messages::object(&row, "seat:s_ffffffffffffffff", false);
        assert_eq!(o["level"], URGENT, "reason: urgent_not_shown");
        assert_eq!(o["level_by"], "role_lead");
        row.item.insert("imported".into(), json!(true));
        let o = crate::messages::object(&row, "seat:s_ffffffffffffffff", false);
        assert!(o.get("level").is_none(), "reason: bridged_urgent {o}");
    }

    #[test]
    fn only_urgent_and_normal_are_levels() {
        assert_eq!(asked(None), Ok(false));
        assert_eq!(asked(Some(&json!("normal"))), Ok(false));
        assert_eq!(asked(Some(&json!("urgent"))), Ok(true));
        for bad in [json!("URGENT"), json!(1), json!(true), json!("high")] {
            assert_eq!(asked(Some(&bad)), Err(()), "reason: level_taken {bad}");
        }
    }

    #[test]
    fn a_plain_role_cannot_mark_urgent() {
        let (_d, db) = db();
        let seat = "s_0123456789abcdef";
        assert_eq!(
            judge(&db, seat, 0),
            Err(RANK_TOO_LOW),
            "reason: no_grant_urgent"
        );
        db.role_rank_set("role_writer", Rank::Plain, "act:role_rank_set", AT)
            .unwrap();
        db.rank_grant(seat, "role_writer", "act:rank_grant", AT)
            .unwrap();
        assert_eq!(
            judge(&db, seat, 0),
            Err(RANK_TOO_LOW),
            "reason: plain_urgent"
        );
        db.role_rank_set("role_lead", Rank::Lead, "act:role_rank_set", AT)
            .unwrap();
        db.rank_grant(seat, "role_lead", "act:rank_grant", AT)
            .unwrap();
        assert_eq!(
            judge(&db, seat, 0),
            Ok("role_lead".into()),
            "reason: lead_refused"
        );
    }

    #[test]
    fn a_missing_table_falls_back_to_supervisor() {
        // knife UR3: the mutant lets a seat pass when the least rank cannot be read
        let (_d, db) = db();
        assert_eq!(ranks::perm_min(&db, ranks::URGENT), Rank::Lead);
        assert_eq!(judge(&db, "s_0123456789abcdef", 0), Err(RANK_TOO_LOW));
        db.perm_set(ranks::URGENT, Rank::Plain, "act:perm_set", AT)
            .unwrap();
        assert_eq!(
            judge(&db, "s_0123456789abcdef", 0),
            Ok("plain".into()),
            "reason: lowered_table_not_read"
        );
    }

    #[test]
    fn three_urgent_letters_an_hour_then_plain() {
        let (_d, db) = db();
        let seat = "s_0123456789abcdef";
        db.perm_set(ranks::URGENT, Rank::Plain, "act:perm_set", AT)
            .unwrap();
        let now = 1_800_000_000;
        sent(&db, seat, Some(URGENT), now - 4000); // over an hour ago
        sent(&db, seat, None, now - 10);
        sent(&db, seat, Some(URGENT), now - 30);
        sent(&db, seat, Some(URGENT), now - 20);
        assert!(
            judge(&db, seat, now).is_ok(),
            "reason: cap_counts_old_or_plain"
        );
        sent(&db, seat, Some(URGENT), now - 10);
        assert_eq!(
            judge(&db, seat, now),
            Err(HOUR_CAP_REACHED),
            "reason: cap_missing"
        );
        // a hundred plain letters after them do not hide them (N1: not a page of the sent box)
        for _ in 0..aib_store::PAGE_MAX {
            sent(&db, seat, None, now - 5);
        }
        assert_eq!(
            judge(&db, seat, now),
            Err(HOUR_CAP_REACHED),
            "reason: cap_paged"
        );
        // another seat's urgent letters are not this seat's
        assert!(
            judge(&db, "s_1111111111111111", now).is_ok(),
            "reason: cap_not_per_seat"
        );
    }
}
