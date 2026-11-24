#!/usr/bin/env bash
# aib-knives: os=unix
# N-workspace knives TQ1–TQ5 (b4297 11-18, ORDER-workspace-519d0-1118): a message one seat sends
# another waits while that seat is in a turn and is written when the provider ends the turn;
# a result line ends a turn, even one cut at the reader's cap.
# Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
tq() { python3 -I scripts/knife.py crates/aibd/src/sessions.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_send "$3"; }
tu() { python3 -I scripts/knife.py crates/aibd/src/turns.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "turns::tests::$3"; }
# TQ1 a seat in a turn is written to anyway
tq '        if self.turns.in_turn(seat_id) {' \
   '        if false && self.turns.in_turn(seat_id) { // knife' \
   a_message_to_a_seat_in_a_turn_waits_for_its_end
# TQ2 the end of a turn writes nothing that waited
tq '                        let _ = tx.send(seat.clone());' \
   '                        let _ = (tx, &seat); // knife' \
   a_message_to_a_seat_in_a_turn_waits_for_its_end
# TQ3 a write does not start a turn
tq '        self.turns.began(seat_id);' \
   '        let _ = seat_id; // knife' \
   a_message_to_a_seat_in_a_turn_waits_for_its_end
# TQ4 a result line does not end a turn
tu '                .map(|t| t == "result")' \
   '                .map(|t| t == "results") // knife' \
   only_a_result_line_ends_a_turn
# TQ5 a result line cut at the cap does not end a turn
tu '        return line.starts_with(RESULT_START);' \
   '        return line.starts_with(RESULT_START) && !cut; // knife' \
   only_a_result_line_ends_a_turn
rd() { python3 -I scripts/knife.py crates/aibd/src/seat_send.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_send_redeliver_dgrs "$3"; }
# TQ6 a queued message is written by what was allowed when it was sent (5d33a PREREAD-seatmsg L1)
rd '                crate::seat_input::dgrs_input_on(),' \
   '                true, // knife' \
   a_queued_message_is_judged_again_when_it_is_written
pw() { python3 -I scripts/knife.py crates/aibd/src/seat_send.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_send "$3"; }
# TQ7 a post that names a seat is never written (a9136 thread_send rows)
pw '        && (matches!(s("sender_kind"), Some("seat" | "system")) || s("post_id").is_some())' \
   '        && (matches!(s("sender_kind"), Some("seat" | "system"))) // knife' \
   a_post_naming_a_seat_is_written_after_its_turn_as_the_persons
# TQ8 the person's post is written as another seat's
pw '            let from = if !by_person(r) {' \
   '            let from = if true || !by_person(r) { // knife' \
   a_post_naming_a_seat_is_written_after_its_turn_as_the_persons
cu() { python3 -I scripts/knife.py crates/aibd/src/catch_up.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test threads_catchup "$3"; }
cm() { python3 -I scripts/knife.py crates/aibd/src/seat_send.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test threads_catchup "$3"; }
cl() { python3 -I scripts/knife.py crates/aibd/src/catch_up.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
# CU1 a seat not called is told nothing at its next turn
cu '        if n == 0 {' \
   '        if n < usize::MAX { // knife' \
   a_seat_not_called_reads_the_conversation_at_its_next_turn
# CU2 a post that called the seat is told again in the block
cu '                .filter(|p| !called.contains(&p.message_id))' \
   '                .filter(|p| called.contains(&p.message_id) || true) // knife' \
   a_seat_not_called_reads_the_conversation_at_its_next_turn
# CU3 the read mark never moves (the same posts every turn)
cu '            if let Err(e) = self.0.set_read_mark(t, &me, m) {' \
   '            if let Err(e) = self.0.set_read_mark(t, &me.replace(me.as_str(), "seat:knife"), m) { // knife' \
   a_seat_not_called_reads_the_conversation_at_its_next_turn
cg() { python3 -I scripts/knife.py crates/aibd/src/catch_up.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test threads_catchup_dgrs "$3"; }
# CU4 a dgrs seat is told what a safe seat posted without calling it (5d33a PREREAD a71bbbdb M1)
cg '    !to_dgrs || (dgrs_on && flag(p, "sender_safe") == Some(false))' \
   '    !to_dgrs || (dgrs_on && flag(p, "sender_safe") == Some(false)) || true // knife (dgrs_on stays used: -D warnings)' \
   a_dgrs_seat_is_not_told_what_a_safe_seat_posted
# CU5 a dgrs seat hears the person with the switch off (5d33a CHECKLIST-wave3 E1)
cl '    !to_dgrs || (dgrs_on && flag(p, "sender_safe") == Some(false))' \
   '    !to_dgrs || ((dgrs_on || true) && flag(p, "sender_safe") == Some(false)) // knife' \
   a_dgrs_seat_hears_only_unsafe_senders_and_only_with_the_switch_on
# CU6 a seat named at open but not assigned on the item catches up on its thread (a9136 b1a39a86, 5d33a 7bfb32ab N1)
cu '        let threads = crate::threads::threads_of(&self.0, Some(&me), None, 20).ok()?;' \
   '        let threads = self.0.threads_list(None, Some(&me), 20).ok()?; // knife' \
   an_items_thread_is_caught_up_on_by_its_assigned_seats_only
# CU7 a call queued before the assignment was removed is written at the seat's start (5d33a 7bfb32ab N2)
cm '                    Ok(Ok(true)) => Ok(()),' \
   '                    Ok(Ok(_)) => Ok(()), // knife' \
   a_call_queued_before_the_assignment_was_removed_is_not_written
cs() { python3 -I scripts/knife.py crates/aibd/src/sessions.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test threads_catchup "$3"; }
# CU8 a written text leaves the seat's read marks where they were (the next text carries the same
# block again; 5d33a catch-up N1, integ1-2cf949fe1 told_twice)
cs '                let carried_marks_moved = ok;' \
   '                let carried_marks_moved = false && ok; // knife' \
   a_seat_not_called_reads_the_conversation_at_its_next_turn
