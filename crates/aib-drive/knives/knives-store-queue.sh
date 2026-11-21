#!/usr/bin/env bash
# S79-rust slice 2: aib-drive reads the dm queue in process (StoreQueue), no `aib dm-queue list`.
set -euo pipefail
sq() { python3 -I scripts/knife.py crates/aib-drive/src/source.rs "$1" "$2" --expect-fail "source::store_queue_tests::$3" \
        -- cargo test -p aib-drive --locked --lib "source::store_queue_tests::$3"; }
# SQK1 an empty status is not read as queued
sq '                ("status", Value::from(status.unwrap_or("queued"))),' '                ("status", Value::from(status.unwrap_or(""))),' the_queue_read_in_process_gives_the_rows_drive_reads
# SQK2 priority as written (" URGENT " never reads as urgent: busy seats go unseen)
sq '            let pri = aib_dmq::norm::priority(raw.get("priority"), "normal");' '            let pri = raw["priority"].as_str().unwrap_or("normal");' the_queue_read_in_process_gives_the_rows_drive_reads
# SQK3 the tab is not filtered
sq '            if tab.is_some_and(|t| t != aib_dmq::view::tab_of_status(status)) {' '            if tab.is_some_and(|t| t == "never") {' the_queue_read_in_process_gives_the_rows_drive_reads
# SQK4 no state root reads as an empty queue
sq '        self.queue.as_ref().map_err(Clone::clone)?.all(seat)' '        self.queue.as_ref().map_or(Ok(Value::Array(Vec::new())), |q| q.all(seat))' no_state_root_is_an_error_not_an_empty_queue
# S79-rust slice 3a: aib-drive writes its letters into the 1.x ledger itself (StoreDm), no aib dm / shell
sd() { python3 -I scripts/knife.py crates/aib-drive/src/source.rs "$1" "$2" --expect-fail "source::store_dm_tests::$3" \
        -- cargo test -p aib-drive --locked --lib "source::store_dm_tests::$3"; }
# SDK1 the text is not checked (R-30, header-shaped lines)
sd '        letter::check_text(text, DM_TEXT_MAX)' '        letter::check_text("x", DM_TEXT_MAX)' what_cannot_be_written_is_not_sent_and_writes_nothing
# SDK2 a seat with no folder gets one made
sd '        if !has_folder {' '        if !has_folder && seat.is_empty() {' what_cannot_be_written_is_not_sent_and_writes_nothing
# SDK3 the letter is not the system's
sd '            ("sender_kind", Value::from("system")),' '            ("sender_kind", Value::from("seat")),' a_letter_is_queued_in_the_ledger_as_the_systems
# SDK4 the letter claims the recipient's seat as its sender
sd '            ("sender_seat", Value::Null),' '            ("sender_seat", Value::from(seat)),' a_letter_is_queued_in_the_ledger_as_the_systems
# SDK5 two cabins with the same short code: the first is taken
sd '            [one] => Some(one.clone()),' '            [one, ..] => Some(one.clone()),' the_cabin_is_found_from_its_short_code_only_when_one_matches
# SDK6 a seat with no folder is a busy moment, not gone (its nudge backs off round after round)
python3 -I scripts/knife.py crates/aib-drive/src/act.rs \
  '    why.starts_with("aib dm stage resolve,") || why.ends_with(crate::source::NO_SEAT_FOLDER)' \
  '    why.starts_with("aib dm stage resolve,") || why.ends_with(crate::source::NO_SEAT_FOLDER) && why.is_empty()' \
  --expect-fail source::store_dm_tests::what_cannot_be_written_is_not_sent_and_writes_nothing \
  -- cargo test -p aib-drive --locked --lib source::store_dm_tests::what_cannot_be_written_is_not_sent_and_writes_nothing
