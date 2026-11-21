#!/usr/bin/env bash
# 待審 1.5 期 急件一級 (DESIGN-web-inbox-aibd-7ebca-1121 r3 §F; DECISION-council-webinbox r2 誰能標急; 5d33a PREREAD r3 0/0/0/2)
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
ur() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" -- cargo test -p aibd --locked $5; }
# UR1 a seat with no lead grant marks urgent (the rank is not compared)
ur crates/aibd/src/urgent.rs \
   '    if rank < ranks::perm_min(db, ranks::URGENT) {' \
   '    if rank < Rank::Plain { // knife' \
   a_plain_role_cannot_mark_urgent \
   '--lib urgent::tests::a_plain_role_cannot_mark_urgent'
# UR2 a letter bridged in from a 1.x ledger shows urgent (its fields are the ledger file's)
ur crates/aibd/src/urgent.rs \
   '        && row.item.get("imported").and_then(Value::as_bool) != Some(true)' \
   '        && true // knife' \
   a_bridged_letter_is_never_urgent \
   '--lib urgent::tests::a_bridged_letter_is_never_urgent'
# UR3 變種 = 讀不到權限表就放行 (the least rank is taken as plain)
ur crates/aibd/src/urgent.rs \
   '    if rank < ranks::perm_min(db, ranks::URGENT) {' \
   '    if rank < ranks::perm_min(db, ranks::URGENT).min(Rank::Plain) { // knife' \
   a_missing_table_falls_back_to_supervisor \
   '--lib urgent::tests::a_missing_table_falls_back_to_supervisor'
# UR7 no hourly cap on urgent letters
ur crates/aibd/src/urgent.rs \
   '    if in_hour >= HOUR_CAP {' \
   '    if in_hour == u64::MAX - 1 { // knife' \
   three_urgent_letters_an_hour_then_plain \
   '--lib urgent::tests::three_urgent_letters_an_hour_then_plain'
# UR8 the hour window counts letters of any age
ur crates/aibd/src/urgent.rs \
   '        .sent_at_level_since(&format!("seat:{seat_id}"), URGENT, now_unix - 3600)' \
   '        .sent_at_level_since(&format!("seat:{seat_id}"), URGENT, i64::MIN) // knife' \
   three_urgent_letters_an_hour_then_plain \
   '--lib urgent::tests::three_urgent_letters_an_hour_then_plain'
# UR9 seat_send judges but never marks the row (the inbox does not say urgent)
ur crates/aibd/src/seat_send.rs \
   '    if let Ok(by) = &level {' \
   '    if let (false, Ok(by)) = (true, &level) { // knife' \
   an_urgent_letter_needs_a_granted_lead_role \
   '--test seat_send an_urgent_letter_needs_a_granted_lead_role'
# UR10 a level that is not one is taken as urgent
ur crates/aibd/src/urgent.rs \
   '        Some(Value::String(s)) if s == URGENT => Some(true),' \
   '        Some(Value::String(_)) => Some(true), // knife' \
   only_urgent_and_normal_are_levels \
   '--lib urgent::tests::only_urgent_and_normal_are_levels'
# UR11 the cap counts a page of the sent box (a hundred plain letters hide the urgent ones)
ur crates/aib-store/src/seat_moves.rs \
   '            "SELECT COUNT(*) FROM messages WHERE from_key = ?1 AND created_unix > ?2' \
   '            "SELECT COUNT(*) FROM (SELECT * FROM messages WHERE from_key = ?1 ORDER BY created_unix DESC LIMIT 100) WHERE from_key = ?1 AND created_unix > ?2 -- knife' \
   three_urgent_letters_an_hour_then_plain \
   '--lib urgent::tests::three_urgent_letters_an_hour_then_plain'
