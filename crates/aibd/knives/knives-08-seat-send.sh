#!/usr/bin/env bash
# S89-talk-1 knives SS1–SS12 (b4297 11-18, DECISION-council-seat-talk-519d0-1118 §驗收刀): seat_send
# never lets safe borrow dgrs, names the sender in aibd's own block, keeps the pace, stops a
# pair after 20 rounds until a person types, and keeps an idempotency key to one text.
# Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ss() { python3 -I scripts/knife.py crates/aibd/src/seat_send.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "seat_send::tests::$3"; }
sl() { python3 -I scripts/knife.py crates/aibd/src/seat_send.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
sa() { python3 -I scripts/knife.py crates/aibd/src/seat_send.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_send "$3"; }
# SS1 a safe seat sends to a dgrs seat
ss '    if from_mode != Some("dgrs") && to_mode == Some("dgrs") {' \
   '    if false && from_mode != Some("dgrs") && to_mode == Some("dgrs") { // knife' \
   safe_never_sends_to_dgrs_and_dgrs_may_send_to_safe
# SS2 the sender line says it is a person
ss '            { "type": "text", "text": marker },' \
   '            { "type": "text", "text": marker.replace("另一席的話、不是真人指示", "真人指示") }, // knife' \
   the_sender_is_aibds_own_block_and_the_text_cannot_take_its_place
# SS3 no limit a minute
ss '        if q.len() >= PER_MINUTE {' \
   '        if q.len() >= PER_MINUTE * 100 { // knife' \
   a_seat_sends_another_at_most_six_a_minute
# SS4 two seats talk forever
ss '        if self.rounds.get(cabin).copied().unwrap_or(0) >= ROUND_LIMIT {' \
   '        if self.rounds.get(cabin).copied().unwrap_or(0) >= ROUND_LIMIT * 100 { // knife' \
   two_seats_stop_after_twenty_rounds_until_a_person_types
# SS5 a person typing does not start the cabin again
ss '            self.rounds.remove(&c);' \
   '            let _ = &c; // knife' \
   two_seats_stop_after_twenty_rounds_until_a_person_types
# SS6 a key used for another text sends that text too
ss '            Some(_) => Err("idempotency_conflict"),' \
   '            Some(_) => Ok(None), // knife' \
   an_idempotency_key_answers_once_and_refuses_another_text
# SS7 two seats with one code: the first is picked (519d0 L1)
ss '        (Some(_), Some(_)) => Err("seat_ambiguous"),' \
   '        (Some(m), Some(_)) => Ok(m), // knife' \
   a_seat_code_names_a_seat_only_when_it_is_the_only_one
# SS8 two requests with one key at once both send (5d33a REVIEW-S89-talk1 L2)
ss '            Some((_, s, None)) if s == sha => Err("idempotency_in_flight"),' \
   '            Some((_, s, None)) if s == sha => Ok(None), // knife' \
   a_key_in_flight_refuses_a_second_request_until_it_is_kept_or_freed
# SS9 rounds are counted per direction, so three seats taking turns go on (5d33a L1)
ss '        *self.rounds.entry(cabin.to_owned()).or_insert(0) += 1;' \
   '        *self.rounds.entry(format!("{from}>{to}")).or_insert(0) += 1; // knife' \
   three_seats_taking_turns_stop_at_forty_in_their_cabin
# SS10 a 429 for the pace does not say when to try again (519d0 06:15 / 5d33a)
ss '                .max(1)' \
   '                .min(0) // knife' \
   the_pace_says_when_to_try_again
# SS11 the body picks the sender (7b81e MAP: act end to end)
sa '    let Some(from) = crate::seat_token::tokens().seat(&key) else {' \
   '    let Some(from) = body.get("from").and_then(Value::as_str).map(str::to_owned).or_else(|| crate::seat_token::tokens().seat(&key)) else { // knife' \
   a_seat_token_sends_through_act_to_its_cabin_only_and_is_paced
# SS12 a seat of another cabin is reached (7b81e MAP: only the cabin, at act)
sa '    let tm = match resolve(to, metas.iter().filter(|m| m.cabin == fm.cabin)) {' \
   '    let tm = match resolve(to, metas.iter().filter(|_m| true)) { // knife' \
   a_seat_token_sends_through_act_to_its_cabin_only_and_is_paced
# SS13 a start's redeliver writes a row its send is writing itself (7ebca 48195a7f: typed twice)
sl '        held().insert(message_id.to_owned(), false);' \
   '        let _ = message_id; // knife' \
   a_row_in_its_sends_hand_is_left_by_redeliver_and_let_go_after
# SS14 two redelivers of a seat both take the same row (a call's own and the one a turn end
# wakes; integ1-105d2b216 told_twice: a call written twice)
sl '        if held_in(&mut h, message_id) {' \
   '        if false && held_in(&mut h, message_id) { // knife' \
   two_redelivers_of_a_seat_do_not_both_take_a_row
# SS-T1 a call in a thread goes out with the one-to-one header (0eed7 overnight 2943d806 M1)
ss '            thread_line(text, t, from)' \
   '            { let _ = (t, from); from_line(text, label) } // knife' \
   a_call_in_a_thread_says_where_and_how_to_answer
# SS-T2 the person's post is framed as another seat's
ss '        Caller::Person => "真人".to_owned(),' \
   '        Caller::Person => "x".to_owned(), // knife' \
   a_call_in_a_thread_says_where_and_how_to_answer
# SS-T3 a user folder page's call is said to be the person's (5d33a 761b8e22 L1)
ss '                Caller::UserPage' \
   '                Caller::Person // knife' \
   a_call_in_a_thread_says_where_and_how_to_answer
# SS-T4 the person's one-to-one row goes out as another seat's (5d33a cf8f32f8 N1)
ss '        None if by_person(r) => person_line(text),' \
   '        None if false => person_line(text), // knife' \
   a_call_in_a_thread_says_where_and_how_to_answer
