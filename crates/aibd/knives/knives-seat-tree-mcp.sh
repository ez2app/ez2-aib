#!/usr/bin/env bash
# aib-knives: os=unix
# The reviewer's seat_tree tool (0eed7 RETRACT-T4 M1, a9136 11-20): reads another seat's tree
# through aibd's tree read with the seat's own token. Run from the repo root.
set -euo pipefail  # a knife that does not bite fails the file (#869 knives 3/3: a MISS here still ended rc 0)
st() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked "$4"; }
# ST1 a seat that is not one path word reaches another route
st crates/aibd/src/ctl.rs \
   '    b.is_ascii_alphanumeric() || b == b'"'"'_'"'"' || b == b'"'"'-'"'"'' \
   '    b != b'"'"' '"'"' // knife' \
   a_reviewer_reads_the_authors_tree_through_aibd_as_itself
# ST2 the path is sent raw (a & or = becomes another query field)
st crates/aibd/src/ctl.rs \
   '            if b.is_ascii_alphanumeric() || b"-_.~/".contains(&b) {' \
   '            if b != b'"'"'\n'"'"' { // knife' \
   a_reviewer_reads_the_authors_tree_through_aibd_as_itself
# ST3 a seat is not allowed the tool
st crates/aibd/src/seats.rs \
   '    "mcp__aibd__seat_tree",' \
   '    "mcp__aibd__seat_now_", // knife' \
   a_seat_gets_aibds_one_server_and_its_two_tools_only
# ST4 diff goes to the log route
st crates/aibd/src/ctl.rs \
   '        Some("diff") => format!("/api/v1/seats/{seat}/tree/diff"),' \
   '        Some("diff") => format!("/api/v1/seats/{seat}/tree/log"), // knife' \
   a_reviewer_reads_the_authors_tree_through_aibd_as_itself
# ST5 a safe seat made reviewer reads the author's tree (5d33a PREREAD-seattree L1)
st crates/aibd/src/tree_read.rs \
   '    matches!(mode, Some("work" | "dgrs"))' \
   '    !matches!(mode, Some("never")) // knife' \
   only_a_work_or_dgrs_seat_reads_another_seats_tree
