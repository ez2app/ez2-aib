#!/usr/bin/env bash
# G3a-G3g a moved web seat reads and closes its 1.x letters through aibd (PREREAD-seat-g1write-g3-a9136-1121 r2, 5d33a 0/0/0/3)
set -euo pipefail
dm() { python3 -I scripts/knife.py crates/aibd/src/seat_dm.rs "$1" "$2" --expect-fail "seat_dm::tests::$3" \
        -- cargo test -p aibd --locked --lib "seat_dm::tests::$3"; }
# G3a the id is not checked
dm '        return bad("dm_id", "id is q_ and 16 lowercase hex digits");' '        eprintln!("knife"); // knife' \
   a_letter_is_closed_only_at_its_rev_with_checkable_evidence
# G3b the rev is not compared
dm '    if cur != rev {' '    if cur == i64::MIN && rev == i64::MIN { // knife' \
   a_letter_is_closed_only_at_its_rev_with_checkable_evidence
# G3e any evidence goes to the ledger (a commit would run git there)
dm '    Err("dm_evidence_unverifiable")' '    Ok(text.to_owned()) // knife' \
   a_letter_is_closed_only_at_its_rev_with_checkable_evidence
# G3e2 a pending name not checked as a listed plain file
dm '            if std::fs::symlink_metadata(&p).is_ok_and(|m| m.file_type().is_file()) {' '            if !p.as_os_str().is_empty() { // knife' \
   a_letter_is_closed_only_at_its_rev_with_checkable_evidence
# G3f the seat code comes from the request
dm '            _ => false,' '            _ => true, // knife' \
   the_seat_code_is_never_taken_from_the_request
# G3g a long message comes without the wall
dm '        "relay_text": relay.map(|t| format!("{}\n---\n{}", crate::seat_pending::RELAY_NOTE, t)),' '        "relay_text": relay, // knife' \
   a_long_message_is_shown_behind_the_wall
# G3h waiting lists letters already done
dm '            if !WAIT_STATUSES.contains(&status) {' '            if status.is_empty() { // knife' \
   the_waiting_letters_are_listed_in_order
# G3i an undone or missing move still gives a code
dm '        .filter(|c| aib_dmq_store::store::seat_ok(c))' '        .or(Some("a9136".to_owned())) // knife' \
   the_old_code_is_the_recorded_live_move
# G3e3 (5d33a REVIEW-g3dm L1) the ledger runs git for a seat's evidence
st() { python3 -I scripts/knife.py crates/aib-dmq-store/src/store.rs "$1" "$2" --expect-fail "seat_dm::tests::$3" \
        -- cargo test -p aibd --locked --lib "seat_dm::tests::$3"; }
st '        if !self.store.git_evidence {' '        if self.store.repo.as_os_str().is_empty() { // knife' \
   the_ledger_never_runs_git_for_a_seat
