#!/usr/bin/env bash
# N-webonly 第 4 件 knives RM1–RM4 (7ebca 11-20): the seat_receipt tool sends a seat's receipt
# acts with its own token and passes on only the fields of each `do`. Run from the repo root.
set -euo pipefail
rmk() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked "$4"; }
# RM1 an ask passes a run_id on too (a field of another do)
rmk crates/aibd/src/ctl.rs \
   '        Some("ask") => (aibd::receipt_run::REQUEST, &["head", "scope", "tests"]),' \
   '        Some("ask") => (aibd::receipt_run::REQUEST, &["head", "scope", "tests", "run_id"]), // knife' \
   a_seat_asks_for_and_reads_its_receipts_as_itself
# RM2 a list names a seat
rmk crates/aibd/src/ctl.rs \
   '        Some("list") => (aibd::receipt_run::LIST, &[]),' \
   '        Some("list") => (aibd::receipt_run::LIST, &["seat_id"]), // knife' \
   a_seat_asks_for_and_reads_its_receipts_as_itself
# RM3 an unknown do is sent as a withdrawal
rmk crates/aibd/src/ctl.rs \
   '        Some("withdraw") => (aibd::receipt_run::WITHDRAW, &["run_id"]),' \
   '        Some("withdraw") | Some("run") => (aibd::receipt_run::WITHDRAW, &["run_id"]), // knife' \
   a_seat_asks_for_and_reads_its_receipts_as_itself
# RM4 a seat is not allowed the tool
rmk crates/aibd/src/seats.rs \
   '    "mcp__aibd__seat_receipt",' \
   '    "mcp__aibd__seat_receipt_", // knife' \
   a_seat_gets_aibds_one_server_and_its_two_tools_only
