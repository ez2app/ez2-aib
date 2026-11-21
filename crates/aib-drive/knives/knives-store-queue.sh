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
