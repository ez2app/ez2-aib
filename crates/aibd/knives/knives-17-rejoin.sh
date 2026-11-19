#!/usr/bin/env bash
# N-revive knives RJ1–RJ12 (a9136 11-18; ORDER-workspace-519d0-1118 第三波 1): after aibd stops on
# purpose and starts again, a seat that was running comes back with its own conversation
# (--resume <its id>); a seat the person stopped, a crash-cut one and an archived one do not.
# Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
ru() { python3 -I scripts/knife.py crates/aibd/src/revive.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
ri() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --test rejoin "$4"; }
# RJ1 a seat whose last session ended any way comes back (stopped by the person, crash-cut)
ru '    if last.ended_by.as_deref() != Some("shutdown") {' \
   '    if last.ended_by.is_none() { // knife' \
   only_a_seat_running_when_aibd_stopped_is_picked_up
# RJ7 a seat whose stop was not confirmed comes back (two processes on one conversation; 5d33a M1)
ru '        "killed" | "interrupted" => Ok(()),' \
   '        "killed" | "interrupted" => Ok(()), "stop_unconfirmed" if true => Ok(()), // knife' \
   only_a_seat_running_when_aibd_stopped_is_picked_up
# RJ2 an archived seat comes back
ru '    if seat.state == aib_store::ARCHIVED {' \
   '    if seat.state == aib_store::ARCHIVED && false { // knife' \
   only_a_seat_running_when_aibd_stopped_is_picked_up
# RJ3 a seat comes back as a new conversation, not its own
ri crates/aib-seatopen/src/pipe.rs \
   '    argv_with(provider, mode, model, RESUME_FLAG, session_id)' \
   '    argv_with(provider, mode, model, NEW_FLAG, session_id) // knife' \
   a_running_seat_comes_back_with_its_conversation_after_aibd_restarts
# RJ4 the conversation id is not kept at open
ri crates/aibd/src/seats.rs \
   '        resume_id: argv.is_ok().then(|| session_uuid.clone()),' \
   '        resume_id: None, // knife' \
   a_running_seat_comes_back_with_its_conversation_after_aibd_restarts
# RJ5 nothing is picked up at start
ri crates/aibd/src/server.rs \
   '    if rejoin {' \
   '    if rejoin && false { // knife' \
   a_running_seat_comes_back_with_its_conversation_after_aibd_restarts
# RJ6 a dgrs seat comes back where a dgrs seat takes no input (5d33a wave-3 G1)
ru '    if mode == "dgrs" && !dgrs_on {' \
   '    if mode == "dgrs" && !dgrs_on && false { // knife' \
   a_dgrs_seat_comes_back_only_where_dgrs_takes_input
# RJ8 a seat that never had a turn is resumed (claude refuses --resume of a conversation it never kept)
ru '        return Some((id.to_owned(), has(id)));' \
   '        return Some((id.to_owned(), has(id) || true)); // knife (has stays used: -D warnings)' \
   a_seat_resumes_only_a_conversation_claude_kept
# RJ9 an unverified stop whose root end was never seen is picked up (macOS rule too wide)
ru '                && (last.exit_code.is_some() || last.signal.is_some()) =>' \
   '                && true => // knife' \
   only_a_seat_running_when_aibd_stopped_is_picked_up
# RJ10 a seat opened before this slice in a folder given at open takes that folder's newest talk (5d33a 63e75bff M1)
ru '        .filter(|_| own_folder)' \
   '        .filter(|_| own_folder || true) // knife (own_folder stays used: -D warnings)' \
   a_seat_resumes_only_a_conversation_claude_kept
# RJ11 a seat whose session ended with an older stop comes back (f1fae 13:10 swap)
ru '    if ended < last_stop? - STOP_WINDOW {' \
   '    if ended < last_stop? - i64::MAX / 2 { // knife' \
   only_the_last_stop_brings_a_seat_back
# RJ12 claude's folder is named from the cwd as written, not its real path (0eed7 eb0bf164 M2)
ru '    let real = std::fs::canonicalize(cwd)' \
   '    let real = std::fs::canonicalize("/nonexistent-knife").and(std::fs::canonicalize(cwd)) // knife' \
   claudes_folder_is_found_through_a_link
rs() { python3 -I scripts/knife.py crates/aibd/src/revive.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_restart "$3"; }
# RS1 a seat that runs is started again on the page's 重開 (043dd 4b09556d1)
rs '    if runs {' \
   '    if false && runs { // knife' \
   the_page_restarts_a_stopped_seat_once
# RS2 the page's 重開 does not start the seat
rs '    rejoin(st, &seat, meta).await.map_err(NotRestarted::Skip)' \
   '    let _ = (meta, &seat); Err(NotRestarted::Unread) // knife' \
   the_page_restarts_a_stopped_seat_once
# RJ13 a seat that exits 1 right after coming back is not told as failed (0eed7 L7)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
   '    by == "root_exit" && after < REJOIN_FAIL_WINDOW && (exit_code != Some(0) || signal.is_some())' \
   '    by == "root_exit" && after < REJOIN_FAIL_WINDOW && ((exit_code != Some(0) && false) || signal.is_some()) // knife (exit_code stays used: -D warnings)' \
   --expect-fail a_seat_that_ends_soon_after_coming_back_failed_to \
   -- cargo test -p aibd --locked --lib a_seat_that_ends_soon_after_coming_back_failed_to
