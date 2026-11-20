#!/usr/bin/env bash
# G13 席的回答回到共同對話 (reply_back.rs; IFACE-reply-in-thread-a9136-1119)
set -u
cd "$(git rev-parse --show-toplevel)"
rb() { python3 -I scripts/knife.py crates/aibd/src/reply_back.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "reply_back::tests::$3"; }
# RB1 a turn that ended in an error is brought back
rb '    if v.get("type").and_then(Value::as_str) != Some("result") || v["is_error"] == json!(true) {' \
   '    if v.get("type").and_then(Value::as_str) != Some("result") { // knife' \
   the_answer_is_the_result_text_of_a_turn_that_did_not_fail
# RB2 the sender calls itself / a non-member is called
rb '        if !id.is_empty() && key != sender && members.contains(&key) && !out.contains(&id) {' \
   '        if !id.is_empty() && (key != sender || members.contains(&key) || true) && !out.contains(&id) { // knife' \
   at_calls_only_members_other_than_the_sender
# RB3 an earlier auto answer counts as the seat's own post
rb '        .any(|p| p.from_key == key && p.created_unix >= at && p.item.get("auto").is_none())' \
   '        .any(|p| p.from_key == key && p.created_unix >= at) // knife' \
   a_seat_that_posted_itself_since_the_call_gets_nothing_brought_back
# RB4 a brought-back answer does not show as one on the page (0eed7 VERIFY-g13 L1)
python3 -I scripts/knife.py crates/aibd/src/threads.rs '        for k in ["auto", "reply_to", "failed", "not_running"] {' \
   '        for k in [] as [&str; 0] {' \
   --expect-fail a_brought_back_answer_shows_auto_and_reply_to \
   -- cargo test -p aibd --locked --lib threads::tests::a_brought_back_answer_shows_auto_and_reply_to
# RB5 a new thread does not say existing:false (043dd 02:48)
python3 -I scripts/knife.py crates/aibd/src/threads.rs '        Json(json!({ "thread": o, "existing": false })),' \
   '        Json(json!({ "thread": o })),' \
   --expect-fail a_page_opens_a_thread_on_an_item_and_calls_one_member_in \
   -- cargo test -p aibd --locked --test threads a_page_opens_a_thread_on_an_item_and_calls_one_member_in
# RB6 a turn not done is no 注意 card (519d0 05:2x; 0eed7 VERIFY-brandhome 80b0cade)
python3 -I scripts/knife.py crates/aibd/src/slots.rs '        "seat.turn_failed" => Some(2),' \
   '        "seat.turn_failed" => None,' \
   --expect-fail a_turn_not_done_is_a_notice_card_with_its_reason \
   -- cargo test -p aibd --locked --lib slots::tests::a_turn_not_done_is_a_notice_card_with_its_reason
# RB7 a key-like word is shown in a failure reason
python3 -I scripts/knife.py crates/aibd/src/reply_back.rs '        if keyish >= 24 && keyish * 10 >= w.chars().count() * 9 {' \
   '        if keyish >= 24 && keyish * 10 >= w.chars().count() * 9 && w.is_empty() {' \
   --expect-fail a_reason_keeps_no_secret_path_or_query \
   -- cargo test -p aibd --locked --lib reply_back::tests::a_reason_keeps_no_secret_path_or_query
# RB8 a turn that ended in an error gives no reason
python3 -I scripts/knife.py crates/aibd/src/reply_back.rs '    if v.get("type").and_then(Value::as_str) != Some("result") || v["is_error"] != json!(true) {' \
   '    if v.get("type").and_then(Value::as_str) != Some("result") || v["is_error"] == json!(true) {' \
   --expect-fail a_turn_that_ended_in_an_error_gives_its_cleaned_reason \
   -- cargo test -p aibd --locked --lib reply_back::tests::a_turn_that_ended_in_an_error_gives_its_cleaned_reason
# RB9 the page is not told why a turn was not done
python3 -I scripts/knife.py crates/aibd/src/threads.rs '        for k in ["auto", "reply_to", "failed", "not_running"] {' \
   '        for k in ["auto", "reply_to"] {' \
   --expect-fail a_brought_back_answer_shows_auto_and_reply_to \
   -- cargo test -p aibd --locked --lib threads::tests::a_brought_back_answer_shows_auto_and_reply_to
# RB10 a called seat that is not running is not said in the thread (0eed7 VERIFY-turnfail L1)
python3 -I scripts/knife.py crates/aibd/src/threads.rs '            .filter(|id| !live.contains(id))' \
   '            .filter(|id| !live.contains(id) && live.is_empty())' \
   --expect-fail calling_a_seat_that_is_not_running_says_so_in_the_thread \
   -- cargo test -p aibd --locked --test threads calling_a_seat_that_is_not_running_says_so_in_the_thread
# RB11 a turn-not-done card does not open its thread (0eed7 VERIFY-turnfail L2)
python3 -I scripts/knife.py crates/aibd/src/slots.rs '        open: (e.event_type == "seat.turn_failed")' \
   '        open: (e.event_type == "seat.turn_failed" && e.global_seq < 0)' \
   --expect-fail a_turn_not_done_is_a_notice_card_with_its_reason \
   -- cargo test -p aibd --locked --lib slots::tests::a_turn_not_done_is_a_notice_card_with_its_reason
