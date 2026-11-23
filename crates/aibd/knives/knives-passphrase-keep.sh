#!/usr/bin/env bash
# PK1–PK8: the passphrase files and their one lock — the built-in page only reads the state, a record swapped while aibd runs is not taken, a missing tries file next to a record is locked, tries survive a restart, only the owner's own sentence is set from outside (DESIGN-persona-propose-osverify-c2f0b-1123 §11; f163c REVIEW-passphrase2 N1 N2, REVIEW-passphrase L2; ebab8 11-23 09:11 door test)
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
pk() { python3 -I scripts/knife.py crates/aibd/src/passphrase_keep.rs "$1" "$2" --expect-fail "passphrase_keep::tests::$3" \
        -- cargo test -p aibd --locked --lib "passphrase_keep::tests::$3"; }
door() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail the_passphrase_state_is_read_by_the_built_in_page_only \
        -- cargo test -p aibd --locked --test passphrase_door; }
# PK1 a user folder page reads the passphrase state
door crates/aibd/src/passphrase_keep.rs \
   '    if page.carried || crate::seat_input::held_as_user_page(page).await {' \
   '    if page.carried {'
# PK2 the built-in page cannot read it at all
door crates/aibd/src/seat_input.rs \
   '        Some(crate::passphrase_keep::STATE) => {' \
   '        Some("passphrase_state_gone") => {'
# PK3 deleting the tries file next to a record gives the tries back
pk '        Err(e) if e.kind() == std::io::ErrorKind::NotFound && !record_there => {' \
   '        Err(e) if e.kind() == std::io::ErrorKind::NotFound && (!record_there || true) => {' \
   no_tries_file_next_to_a_record_is_locked
# PK4 the known record is read from its file on every answer (swapped together = taken)
pk '        let (kdf, k) = (self.kdf.clone(), (*known).clone());' \
   '        let (kdf, k) = (self.kdf.clone(), read_known(&self.dir).or_else(|| (*known).clone()));' \
   a_record_swapped_while_aibd_runs_is_not_taken_even_with_its_known_file
# PK5 a record that does not read is "not set" instead of swapped
pk '            OnDisk::Bad => return Answer::Swapped,' \
   '            OnDisk::Bad => None,' \
   a_record_that_does_not_read_is_swapped
# PK6 the tries are not written back: a restart forgets the lock
pk '        if after != before {' \
   '        if after != before && false {' \
   set_answer_and_the_lock_survives_a_restart
# PK7 a sentence from outside is taken as aibd's own: no strength check
pk '                source: Source::Owner,' \
   '                source: Source::Generated,' \
   only_the_owners_own_sentence_is_set_and_never_where_the_system_check_works
# PK8 the page does not hear of a record that does not read
pk '                v["swapped"] = json!(true);' \
   '                v["swapped"] = json!(false);' \
   a_record_that_does_not_read_is_swapped
