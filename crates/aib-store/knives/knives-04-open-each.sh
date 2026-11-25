#!/usr/bin/env bash
# aib-knives: os=unix
# N-ws-slot-rest knives OE1-OE2 (7ebca 11-18, 5d33a REVIEW-ws-slots 7075ce31 N1): the slots list
# reads every seat's open mail in one query, the limit per receiver.
# Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
oe() { python3 -I scripts/knife.py crates/aib-store/src/messages.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-store --locked --test messages "$3"; }
# OE1 the limit counts all receivers together
oe '(PARTITION BY to_key ORDER BY created_unix, rowid) AS nth, rowid AS rid' \
   '(PARTITION BY thread_id ORDER BY created_unix, rowid) AS nth, rowid AS rid' \
   open_rows_to_each_receiver_in_one_query_match_asking_each
# OE2 one row more than the limit per receiver
oe 'WHERE nth <= ? ORDER BY to_key, created_unix, rid' \
   'WHERE nth <= ? + 1 ORDER BY to_key, created_unix, rid' \
   open_rows_to_each_receiver_in_one_query_match_asking_each
