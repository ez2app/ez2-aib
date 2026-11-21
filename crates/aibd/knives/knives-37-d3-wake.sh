#!/usr/bin/env bash
# D3 閒著的網頁席收到信要被叫醒 (DESIGN-drive-d3-wake-7ebca-1121; DEV-drive-web-519d0-1121 D3)
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
dw() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" -- cargo test -p aibd --locked $5; }
# DW1 nothing not yet due is ever looked at again (an idle seat is never woken)
dw crates/aibd/src/mail_nudge.rs \
   '        .filter(|at| *at > now)' \
   '        .filter(|at| *at > now && false) // knife' \
   an_idle_seats_message_is_looked_at_again_when_it_comes_due \
   '--lib mail_nudge::tests::an_idle_seats_message_is_looked_at_again_when_it_comes_due'
# DW2 an urgent message waits the three minutes like any other
dw crates/aibd/src/mail_nudge.rs \
   '    if at_once(r) && nudged(r) == 0 {' \
   '    if at_once(r) && nudged(r) == -1 { // knife' \
   an_urgent_message_is_named_at_once_and_a_bridged_one_waits \
   '--lib mail_nudge::tests::an_urgent_message_is_named_at_once_and_a_bridged_one_waits'
# DW3 an urgent message, once named, is named again at once (no rest)
dw crates/aibd/src/mail_nudge.rs \
   '    if at_once(r) && nudged(r) == 0 {' \
   '    if at_once(r) { // knife' \
   an_urgent_message_is_named_at_once_and_a_bridged_one_waits \
   '--lib mail_nudge::tests::an_urgent_message_is_named_at_once_and_a_bridged_one_waits'
# DW4 a seat gets a second look-again for the same time
dw crates/aibd/src/mail_nudge.rs \
   '    if p.get(seat).is_some_and(|t| *t <= at) {' \
   '    if p.get(seat).is_some_and(|t| *t < at) { // knife' \
   a_seat_has_one_look_again_waiting \
   '--lib mail_nudge::tests::a_seat_has_one_look_again_waiting'
# DW5 a replaced look-again still fires
dw crates/aibd/src/mail_nudge.rs \
   '    if p.get(seat) == Some(&at) {' \
   '    if p.contains_key(seat) { // knife' \
   a_seat_has_one_look_again_waiting \
   '--lib mail_nudge::tests::a_seat_has_one_look_again_waiting'
# DW6 a work order is named a second time (DECISION-council-driveweb r1: once, then the card)
dw crates/aibd/src/mail_nudge.rs \
   '    !(crate::urgent::is_dispatch(r) && nudged(r) >= 1)' \
   '    true // knife' \
   a_work_order_is_named_at_once_and_only_once \
   '--lib mail_nudge::tests::a_work_order_is_named_at_once_and_only_once'
# DW7 any seat's 「派工:」 is marked a work order (the rank is not compared)
dw crates/aibd/src/urgent.rs \
   '    ranks::seat_rank(db, seat_id) >= ranks::perm_min(db, DISPATCH)' \
   '    ranks::seat_rank(db, seat_id) >= Rank::Plain // knife' \
   a_work_order_is_marked_only_for_a_lead_and_never_bridged \
   '--lib urgent::tests::a_work_order_is_marked_only_for_a_lead_and_never_bridged'
# DW8 「派工:」 anywhere in the text makes a work order
dw crates/aibd/src/urgent.rs \
   '    let first = text.trim_start().lines().next().unwrap_or("");' \
   '    let first = text.lines().find(|l| l.starts_with("派工")).unwrap_or(""); // knife' \
   a_work_order_is_marked_only_for_a_lead_and_never_bridged \
   '--lib urgent::tests::a_work_order_is_marked_only_for_a_lead_and_never_bridged'
# DW9 a letter bridged in from a 1.x ledger counts as a work order
dw crates/aibd/src/urgent.rs \
   '    row.item.get(DISPATCH).and_then(Value::as_bool) == Some(true) && bridged != Some(true)' \
   '    row.item.get(DISPATCH).and_then(Value::as_bool) == Some(true) && bridged != Some(false) // knife' \
   a_work_order_is_marked_only_for_a_lead_and_never_bridged \
   '--lib urgent::tests::a_work_order_is_marked_only_for_a_lead_and_never_bridged'
# DW10 seat_send marks no work order (an idle seat is never woken at once for one)
dw crates/aibd/src/seat_send.rs \
   '    if dispatch {' \
   '    if dispatch && false { // knife' \
   an_urgent_letter_needs_a_granted_lead_role \
   '--test seat_send an_urgent_letter_needs_a_granted_lead_role'
ss() { python3 -I scripts/knife.py crates/aibd/src/seat_send.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "seat_send::tests::$3"; }
# DW11 any row whose key reads system:drive is taken for aibd's work order
ss '    r.from_key == SYSTEM_FROM && r.item.get("sender_kind").and_then(Value::as_str) == Some("system")' \
   '    r.from_key == SYSTEM_FROM // knife' \
   a_system_work_order_waits_to_be_written_and_reads_as_the_drives
# DW12 a queued work order of the system is never written to its seat
ss '        && (matches!(s("sender_kind"), Some("seat" | "system")) || s("post_id").is_some())' \
   '        && (matches!(s("sender_kind"), Some("seat")) || s("post_id").is_some()) // knife' \
   a_system_work_order_waits_to_be_written_and_reads_as_the_drives
# DW13 the system's work order is not named by the reminder
dw crates/aibd/src/mail_nudge.rs \
   '    matches!(text_of(r, "sender_kind"), Some("seat" | "system"))' \
   '    matches!(text_of(r, "sender_kind"), Some("seat")) // knife' \
   a_work_order_from_the_system_is_named \
   '--lib mail_nudge::tests::a_work_order_from_the_system_is_named'
