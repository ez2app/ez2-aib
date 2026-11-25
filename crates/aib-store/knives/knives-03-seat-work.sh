#!/usr/bin/env bash
# aib-knives: os=unix
# migration 0006 (a9136 11-19, 0eed7 a2cd0ccc4 H): seat_meta takes a work seat and keeps its rows.
# Each rule, broken, turns its own test red. Run from the repo root; -eo pipefail like GitHub's `shell: bash`.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
mg() { python3 -I scripts/knife.py migrations/0006_seat_work.sql "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-store --locked --lib "migrate::tests::$3"; }
# MG1 work is still refused after 0006
mg "    mode         TEXT    CHECK (mode IS NULL OR mode IN ('dgrs', 'safe', 'work'))," \
   "    mode         TEXT    CHECK (mode IS NULL OR mode IN ('dgrs', 'safe'))," \
   rebuilding_seat_meta_for_work_keeps_its_rows
# MG2 the rows are not carried over
mg '    FROM seat_meta;' '    FROM seat_meta WHERE 0;' rebuilding_seat_meta_for_work_keeps_its_rows
# MG3 the resume index is not made again
mg 'CREATE INDEX seat_meta_resume ON seat_meta (resume_id) WHERE resume_id IS NOT NULL;' \
   'SELECT 1; -- knife: no index' rebuilding_seat_meta_for_work_keeps_its_rows
