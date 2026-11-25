#!/usr/bin/env bash
# aib-knives: os=unix
# B1dm-S3c knife MI1 (b4297 11-18): a seat's inbox holds what is still open to it, not what it closed.
# Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
mi() { python3 -I scripts/knife.py crates/aib-store/src/messages.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-store --locked --test messages "$3"; }
# MI1 a done message stays in the inbox
mi "             AND json_extract(item, '\$.status') IN ('queued', 'delivered', 'read')" \
   "             AND json_extract(item, '\$.status') IN ('queued', 'delivered', 'read', 'done') -- knife" \
   the_open_rows_to_a_receiver_are_its_inbox_oldest_first
# MI2 rows of one second come back by their random ids, not in the order they were written
mi "             ORDER BY created_unix, rowid LIMIT ?2\"" \
   "             ORDER BY created_unix, message_id LIMIT ?2\" // knife" \
   open_rows_of_one_second_keep_the_order_they_were_written_in
