#!/usr/bin/env bash
# S89-talk-2b knives CT1–CT16 (7b81e 11-18; file renamed from 07, 5d33a N2, IFACE-S89-talk-2b-7b81e-1118 §5): the contacts file stays
# private and is never overwritten when broken, every write names its revision, and nothing the person wrote
# reaches the journal. Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
cu() { python3 -I scripts/knife.py crates/aibd/src/contacts.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "contacts::tests::$3"; }
ce() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --test contacts "$4"; }
cw() { python3 -I scripts/knife.py crates/aibd/src/seat_input.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test contacts_userpage "$3"; }
# CT1 the file is written by a plain write (mode from the umask, not 0600)
cu '        crate::platform::write_private_file(&self.path, &bytes).map_err(BookError::Io)' \
   '        std::fs::write(&self.path, &bytes).map_err(BookError::Io) // knife' \
   the_file_is_private_and_a_write_needs_the_revision_it_read
# CT2 a broken file reads as empty, and the next write overwrites it
cu '            .map_err(|e| BookError::Unreadable(format!("{CONTACTS_FILE}: {e}")))?;' \
   '            .unwrap_or_default(); // knife' \
   a_broken_file_is_refused_and_left_as_it_is
# CT3 the revision is not compared
cu '        if old.revision != expected {' \
   '        if false && old.revision != expected { // knife' \
   the_file_is_private_and_a_write_needs_the_revision_it_read
# CT4 an unknown field is dropped instead of refused
cu '            _ => return Err(unknown_field(k)),' \
   '            _ => drop(unknown_field(k)), // knife' \
   an_unknown_key_is_404_and_an_unknown_field_is_refused
# CT5 a blocked contact stays a friend
cu '        if e.blocked {' \
   '        if false && e.blocked { // knife' \
   blocking_takes_the_friend_away_and_an_old_mute_is_over
# CT6 a mute whose time is over still counts
cu '        self.muted && self.mute_until.as_deref().is_none_or(|u| u > now)' \
   '        self.muted && (now.is_empty() || true) // knife' \
   blocking_takes_the_friend_away_and_an_old_mute_is_over
# CT7 any slider value is taken
cu '            .filter(|n| *n <= 100 && n % 10 == 0)' \
   '            .filter(|n| *n <= 255) // knife' \
   the_sliders_take_only_tens_from_0_to_100_and_only_one_name
# CT8 any preference name is taken
cu '        Some(FX) => {}' \
   '        Some(_) => {} // knife' \
   the_sliders_take_only_tens_from_0_to_100_and_only_one_name
# CT9 the note reaches the event journal
ce crates/aibd/src/contacts_api.rs \
   '                payload: json!({"key": key, "fields": changed, "revision": entry.revision}),' \
   '                payload: json!({"key": key, "fields": changed, "revision": entry.revision, "note": entry.note}), // knife' \
   a_page_lists_the_seat_and_sets_it_with_the_revision_it_read
# CT10 the flow window is not clamped
ce crates/aibd/src/contacts_api.rs \
   '    let window = q.window.unwrap_or(600).clamp(60, 3600);' \
   '    let window = q.window.unwrap_or(600).max(60); // knife' \
   the_sliders_and_the_flows_answer_and_the_file_is_private
# CT11 (S89-talk-2c 043dd 05:04) the page's own row is missing: the page cannot tell "me"
ce crates/aibd/src/contacts_api.rs \
   '            rows.push(contacts::self_row(me, &e, &now, &m));' \
   '            rows.retain(|_| true); let _ = (me, &e, &m); // knife' \
   a_page_lists_the_seat_and_sets_it_with_the_revision_it_read
# CT12 (043dd 04:54) an archived contact is listed without ?all=1
ce crates/aibd/src/contacts.rs \
   '        .filter(|r| q.all || r["archived_at"].is_null() || r["self"] == true)' \
   '        .filter(|r| q.all || true || r["self"] == true) // knife' \
   a_page_lists_the_seat_and_sets_it_with_the_revision_it_read
# CT13 a page cannot set its own row
ce crates/aibd/src/contacts_api.rs \
   '            None => me.as_deref() == Some(key.as_str()),' \
   '            None => me.is_some() && false, // knife' \
   a_page_lists_the_seat_and_sets_it_with_the_revision_it_read
# CT14 a user folder page reads the person's notes (5d33a L1)
ce crates/aibd/src/contacts_api.rs \
   '                r.remove("note");' \
   '                let _ = &r; // knife' \
   a_user_page_neither_reads_nor_writes_a_note
# CT15 a user folder page writes a note (5d33a L1)
ce crates/aibd/src/seat_input.rs \
   '            if body.get("note").is_some() && held_as_user_page(&page).await {' \
   '            if false && body.get("note").is_some() && held_as_user_page(&page).await { // knife' \
   a_user_page_neither_reads_nor_writes_a_note
# CT16 only the credential's mark counts, not a user page serving `/` now (5d33a L1 預讀)
cw '        || tokio::task::spawn_blocking(|| crate::static_files::web_source() == "user")' \
   '        || false && tokio::task::spawn_blocking(|| crate::static_files::web_source() == "user") // knife' \
   a_built_in_credential_reads_no_note_while_a_user_page_serves
