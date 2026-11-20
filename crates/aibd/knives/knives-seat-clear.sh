#!/usr/bin/env bash
# SC1–SC14 (1a93b 11-20, DESIGN-seat-clear-1a93b-1120 r1; 519d0 23:38): the page's 清空 (act
# seat_clear) stops a seat that said its handoff is written and opens it on a new conversation
# whose first line is aibd's. Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per file
sc() { python3 -I scripts/knife.py crates/aibd/src/seat_clear.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
si() { python3 -I scripts/knife.py crates/aibd/src/seat_clear.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_clear "$3"; }
rv() { python3 -I scripts/knife.py crates/aibd/src/revive.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_clear "$3"; }
sm() { python3 -I scripts/knife.py crates/aib-store/src/seat_meta.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-store --locked --test seat_meta "$3"; }
# SC1 a note path with a `..` part is the seat's own (it reads another seat's file)
sc '    if p.split('"'"'/'"'"').any(|seg| seg == "." || seg == "..") {' \
   '    if false { // knife' \
   a_handoff_path_is_the_seats_own_or_refused
# SC2 another seat's HANDOFF-<code>- note in pending counts as this seat's
sc '        && name.starts_with(&format!("HANDOFF-{code}-"))' \
   '        && name.starts_with("HANDOFF-") // knife' \
   a_handoff_path_is_the_seats_own_or_refused
# SC3 the backslash form is not read as a separator (a Windows `..\` slips through)
sc '    let mut s = p.replace('"'"'\\'"'"', "/");' \
   '    let mut s = p.to_owned(); // knife' \
   a_handoff_path_is_the_seats_own_or_refused
# SC4 a handoff the page did not see is used
sc '    if h.at != asked_at {' \
   '    if h.at != asked_at && false { // knife' \
   a_clear_needs_the_handoff_the_page_saw_written_after_the_last_text_and_fresh
# SC5 a seat in a turn is cleared
sc '    if in_turn {' \
   '    if in_turn && false { // knife' \
   a_clear_needs_the_handoff_the_page_saw_written_after_the_last_text_and_fresh
# SC6 a handoff written before the seat's last text is used
sc '    let after_input = last_input.is_none_or(|t| h.at.as_str() > t);' \
   '    let after_input = last_input.is_some() || true; // knife' \
   a_clear_needs_the_handoff_the_page_saw_written_after_the_last_text_and_fresh
# SC7 a handoff over 30 minutes old is used
sc '    let fresh = crate::revive::unix_of(&h.at).is_some_and(|w| now_unix - w <= FRESH_S);' \
   '    let fresh = now_unix >= 0 || true; // knife' \
   a_clear_needs_the_handoff_the_page_saw_written_after_the_last_text_and_fresh
# SC8 the hash the seat gave is not said in the first line
sc '    let sha = sha256.map_or(String::new(), |h| format!("(sha256 {h})"));' \
   '    let sha = String::new(); // knife' \
   the_first_line_is_aibds_words_with_the_path_and_the_hash
# SC9 the clear resumes the old conversation instead of a new one
rv '        Some((id, false))' \
   '        Some((id, true)) // knife' \
   the_page_clears_a_seat_onto_a_new_conversation_that_reads_its_handoff
# SC10 the kept resume id is not swapped (重開 would go back to the old conversation)
si '                &new,' \
   '                &format!("{new}0"), // knife' \
   the_page_clears_a_seat_onto_a_new_conversation_that_reads_its_handoff
# SC11 the journal keeps the note's path as written
si '        "handoff": {"len": h.path.chars().count(), "sha256": sha_of(&h.path)},' \
   '        "handoff": {"len": h.path.chars().count(), "sha256": sha_of(&h.path), "path": h.path}, // knife' \
   the_page_clears_a_seat_onto_a_new_conversation_that_reads_its_handoff
# SC12 the same key clears a second time
si '        Ok(v) => store.idem_finish(&k, 200, v).map(|_| ()),' \
   '        Ok(_) => store.idem_release(&k).map(|_| ()), // knife' \
   the_page_clears_a_seat_onto_a_new_conversation_that_reads_its_handoff
# SC13 the swap writes over an id other than the one read before
sm '                "UPDATE seat_meta SET resume_id = ?2, updated_at = ?3 WHERE seat_id = ?1 AND resume_id IS ?4",' \
   '                "UPDATE seat_meta SET resume_id = ?2, updated_at = ?3 WHERE seat_id = ?1 AND (resume_id IS ?4 OR 1)", // knife' \
   the_resume_id_is_swapped_only_from_the_one_read_before
# SC14 a note outside the seat's places is used
si '        .is_some_and(|h| path_ours(&h.path, code, pending.as_deref(), &meta.cwd));' \
   '        .is_some_and(|h| path_ours(&h.path, code, pending.as_deref(), &meta.cwd) || true); // knife' \
   the_page_clears_a_seat_onto_a_new_conversation_that_reads_its_handoff
