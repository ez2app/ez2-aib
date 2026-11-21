#!/usr/bin/env bash
# N-workspace event slots knives SL1-SL16, NI1-NI3, MQ1-MQ3, ST1-ST3, PG1-PG4, MU1-MU3, RS1-RS2, AO1-AO2 (7ebca 11-18, ORDER-workspace-519d0-1118): each slot
# rule (the table, the notify fallback, the order and caps, decision_answer and slot_dismiss
# checks, the journal keeping lengths and hashes only), broken, turns its own test red.
# Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
sl() { python3 -I scripts/knife.py crates/aibd/src/slots.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
se() { python3 -I scripts/knife.py crates/aibd/src/slots.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test slots "$3"; }
st() { python3 -I scripts/knife.py crates/aib-store/src/lib.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-store --locked --test store "$3"; }
# SL1
sl '| "session.timeout" => Some(2),' \
   '| "session.timeout" => Some(6),' \
   slots::tests::
# SL2
sl '.is_some_and(|c| c != 0)' \
   '.is_some_and(|c| c == 0)' \
   slots::tests::
# SL3
sl '"thermal.would_nogo" | "thermal.would_reap" => Some(3),' \
   '"thermal.would_nogo" | "thermal.would_reap" => Some(4),' \
   slots::tests::
# SL4
sl '"session.queued" => Some(4),' \
   '"session.queued" => Some(6),' \
   slots::tests::
# SL5
sl '"red" => Some(2),' \
   '"red" => Some(6),' \
   slots::tests::
# SL6
sl '"yellow" => Some(1),' \
   '"yellow" => Some(2),' \
   slots::tests::
# SL7
sl 'm.insert("signal_type".into(), sig.clone());' \
   'let _ = (m, sig);' \
   slots::tests::
# SL8
sl '.filter(|(_, n)| n.state == "decide")' \
   '.filter(|(_, n)| n.state == "reply")' \
   slots::tests::
# SL9
sl '.filter(|c| !taken.contains(&c.card_id))' \
   '.filter(|c| taken.is_empty() || !c.card_id.is_empty())' \
   slots::tests::
# SL10
sl 'done <= KEEP_DONE' \
   'done <= KEEP_DONE + 100' \
   slots::tests::
# SL11
se 'if !taken().take(&card) {' \
   'if !taken().take(&card) && card.is_empty() {' \
   a_page_answers_the_seat_waiting_for_it_and_the_card_leaves
# SL12
se 'Some(c) if c.as_str() == Some(card.as_str()) => {}' \
   'Some(c) if c.is_string() => {}' \
   a_page_answers_the_seat_waiting_for_it_and_the_card_leaves
# SL13
se '.filter(|n| n.state == "decide")' \
   '.filter(|n| n.state != "clear")' \
   a_page_answers_the_seat_waiting_for_it_and_the_card_leaves
# SL14
se '&& valid_card_id(c))' \
   '&& !c.is_empty())' \
   a_page_answers_the_seat_waiting_for_it_and_the_card_leaves
# SL15
sl 'if g.len() >= MAX_TAKEN && !g.contains_key(card_id) {' \
   'if g.len() >= MAX_TAKEN * 4 && !g.contains_key(card_id) {' \
   slots::tests::
# SL16
st 'FROM (SELECT * FROM events ORDER BY global_seq DESC LIMIT ?1)' \
   'FROM (SELECT * FROM events ORDER BY global_seq ASC LIMIT ?1)' \
   the_last_events_are_the_newest_few_oldest_first
# NI1
sl 'let edge = |c: Option<char>| c.is_none_or(|c| !(c.is_ascii_alphanumeric() || c == '"'"'-'"'"'));' \
   'let edge = |c: Option<char>| c.is_none_or(|c| !c.is_ascii_alphanumeric());' \
   a_seat_card_takes_the_node
# NI2
sl 'edge(text[..i].chars().next_back()) && edge(text[i + m.len()..].chars().next())' \
   'edge(text[..i].chars().next_back()) && !m.is_empty()' \
   a_seat_card_takes_the_node
# NI3
sl 'for c in cards.iter_mut().filter(|c| c.card.is_none()) {' \
   'for c in cards.iter_mut().filter(|_| false) {' \
   a_seat_card_takes_the_node
# MQ1
sl '.filter(|r| crate::seat_send::waits_to_be_written(r))' \
   '.filter(|_| true)' \
   mail_waiting_past_the_cutoff
# MQ2
sl '(oldest.created_unix < cutoff_unix).then(|| Card {' \
   'true.then(|| Card {' \
   mail_waiting_past_the_cutoff
# MQ3
sl '(a.created_unix, &a.message_id).cmp(&(b.created_unix, &b.message_id))' \
   '(b.created_unix, &b.message_id).cmp(&(a.created_unix, &a.message_id))' \
   mail_waiting_past_the_cutoff
# SL-R1 a seat whose rejoin failed is no card on GET /api/v1/slots (0eed7 VERIFY-chain 8843ef5f N1)
sl '        "seat.rejoin_failed" => Some(2),' \
   '        "seat.rejoin_failed_knife" => Some(2),' \
   the_table_puts_each_known_kind_in_its_slot
# SL-R2 a seat back after a failed rejoin still shows the card (5d33a PREREAD 04e74d1c L2)
sl '        .filter(|c| !back.contains(&c.card_id))' \
   '        .filter(|c| c.slot > 0 || !back.contains(&c.card_id)) // knife' \
   a_failed_rejoin_is_no_card_once_the_seat_is_back
# SL-R3 a failure after the seat came back is hidden too
sl '                .is_some_and(|b| *b > e.global_seq)' \
   '                .is_some() // knife' \
   a_failed_rejoin_is_no_card_once_the_seat_is_back
# ST1 an edit in place is a card
sl '"node.set" => text_of(payload.get("to")).map(|_| 6),' \
   '"node.set" => Some(Some(6)).flatten(),' \
   a_station_move_is_a_slot_6_card
# ST2 a station move names no item
sl 'let (card, item) = moved.map_or((None, None), |(n, i)| (Some(n), Some(i)));' \
   'let (card, item) = moved.map_or((None, None), |(_n, i)| (None::<String>, Some(i)));' \
   a_station_move_is_a_slot_6_card
# ST3 the card drops the station it left
sl 'Some((node, format!("換站:{from} → {to}")))' \
   'Some((node, format!("換站:{to}{}", &from[..0])))' \
   a_station_move_is_a_slot_6_card
# PG1 other class on the page
sl '.filter(|c| p.slot.is_none_or(|s| c.slot == s))' \
   '.filter(|c| p.slot.is_none_or(|_s| c.slot > 0))' \
   a_class_pages_by_ten
# PG2 new cards move a page
sl '.filter(|c| c.at.as_str() <= as_of.as_str())' \
   '.filter(|c| !c.at.is_empty())' \
   a_class_pages_by_ten
# PG3 every page is page 1
sl '.skip((page - 1).saturating_mul(p.per))' \
   '.skip(0)' \
   a_class_pages_by_ten
# PG4 a signed number taken
sl 'Ok(n) if (lo..=hi).contains(&n) && t.bytes().all(|b| b.is_ascii_digit()) => {' \
   'Ok(n) if (lo..=hi).contains(&n) => {' \
   a_bad_slots_query
# MU1 a read message is carded
sl 'let delivered = r.item.get("status").and_then(Value::as_str) == Some("delivered");' \
   'let delivered = r.item.get("status").and_then(Value::as_str) != Some("queued");' \
   mail_written_and_unread_past_the_cutoff
# MU2 a fresh message is carded
sl '(*at < cutoff_unix).then(|| Card {' \
   '(*at > 0 || cutoff_unix == i64::MIN).then(|| Card {' \
   mail_written_and_unread_past_the_cutoff
# MU3 unread mail is not on the list
sl '.chain(unread_cards(rows, cutoff_unix))' \
   '.chain(Vec::<Card>::new())' \
   mail_written_and_unread_past_the_cutoff
# RS1 an answered card is put back
sl 'if g.get(card_id) == Some(&true) {' \
   'if g.contains_key(card_id) {' \
   a_dismissed_card_is_restored_once
# RS2 answered cards on the dismissed list
sl '.filter(|(_, d)| **d)' \
   '.filter(|(_, _d)| true)' \
   a_dismissed_card_is_restored_once
# AO1 an update or close moves the card's time (0eed7 VERIFY-slots d9e6d5133 M1)
sl '.filter(|e| s(e, "outcome").as_deref() == Some("open"))' \
   '.filter(|e| s(e, "outcome").is_some())' \
   an_andy_card_is_timed_by_the_event_that_opened_it
# AO2 the card keeps the month-day since (0eed7 VERIFY-slots d9e6d5133 M1)
sl '.get(&i.item)' \
   '.get(&String::new())' \
   an_andy_card_is_timed_by_the_event_that_opened_it

# OH1 / OH2 the 1.x system's cards are history, apart and not counted as waiting on the person (a9136 ORDER-autodrive 3)
#   (1a93b 11-20 16:1x: 0210983009 made an open andy.open item now, so OH1 now drops the dm.* half
#   of the line instead of andy.open; OH4 puts andy.open back into history and must bite too)
python3 -I scripts/knife.py crates/aibd/src/slots.rs '    let old = kind.starts_with("dm.") || matches!(kind, "andy.review_list" | "idea.consent");' '    let old = matches!(kind, "andy.review_list" | "idea.consent"); // knife' --expect-fail old_system_cards_are_history_and_not_counted_as_waiting \
  -- cargo test -p aibd --locked --lib slots::tests::old_system_cards_are_history_and_not_counted_as_waiting
# OH4 an open item of the 待人決策 list (andy.open) is now, not history (519d0 11-20 12:36, a9136 12:38)
python3 -I scripts/knife.py crates/aibd/src/slots.rs '    let old = kind.starts_with("dm.") || matches!(kind, "andy.review_list" | "idea.consent");' '    let old = kind.starts_with("dm.") || matches!(kind, "andy.open" | "andy.review_list" | "idea.consent"); // knife' --expect-fail old_system_cards_are_history_and_not_counted_as_waiting \
  -- cargo test -p aibd --locked --lib slots::tests::old_system_cards_are_history_and_not_counted_as_waiting
python3 -I scripts/knife.py crates/aibd/src/slots.rs '            .filter(|c| c.slot == s && c.origin() == "now")' '            .filter(|c| c.slot == s && !c.origin().is_empty()) // knife' --expect-fail old_system_cards_are_history_and_not_counted_as_waiting \
  -- cargo test -p aibd --locked --lib slots::tests::old_system_cards_are_history_and_not_counted_as_waiting
# OH3 a 1.x terminal seat's decide line counts as waiting on the person (5d33a N2)
python3 -I scripts/knife.py crates/aibd/src/slots.rs '                .is_some_and(|s| s.starts_with("seat_"));' '                .is_some_and(|s| !s.is_empty()); // knife' --expect-fail old_system_cards_are_history_and_not_counted_as_waiting \
  -- cargo test -p aibd --locked --lib slots::tests::old_system_cards_are_history_and_not_counted_as_waiting
# CU1 (5d33a N7) an archive's tree not collected over repo settings opens no 壞了 card
python3 -I scripts/knife.py crates/aibd/src/slots.rs '        "seat.collect_unsafe" => Some(2),' '        "seat.collect_unsafe" => None, // knife' --expect-fail a_merge_back_conflict_is_a_decide_card_naming_its_files \
  -- cargo test -p aibd --locked --lib slots::tests::a_merge_back_conflict_is_a_decide_card_naming_its_files
# CU2 the card does not say to fix the repo settings first
python3 -I scripts/knife.py crates/aibd/src/slots.rs '                    .map(|w| format!("收樹停住:先修好倉的 git 設定再收({w})"))' '                    .map(|w| format!("收樹停住({w})")) // knife' --expect-fail a_merge_back_conflict_is_a_decide_card_naming_its_files \
  -- cargo test -p aibd --locked --lib slots::tests::a_merge_back_conflict_is_a_decide_card_naming_its_files
# CU3 a copy whose .git names no readable head is taken for a git failure (no card; 5d33a N7 L1)
python3 -I scripts/knife.py crates/aibd/src/seat_worktree.rs 'pub const COPY_HEAD_UNREADABLE: &str = "repo_unsafe: the copy' 'pub const COPY_HEAD_UNREADABLE: &str = "the copy' --expect-fail a_collect_refused_over_repo_settings_is_told_apart_from_a_git_failure \
  -- cargo test -p aibd --locked --lib seats::tests::a_collect_refused_over_repo_settings_is_told_apart_from_a_git_failure
