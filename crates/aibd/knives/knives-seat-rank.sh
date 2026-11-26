#!/usr/bin/env bash
# aib-knives: os=unix
# RS1-RS3 who may mark urgent is set by the owner only (OPINION-role-rank-a9136-1121 r2, 5d33a PREREAD-inboxaibd F r2)
set -euo pipefail
rs() { python3 -I scripts/knife.py crates/aibd/src/seat_rank.rs "$1" "$2" --expect-fail "seat_rank::tests::$3" \
        -- cargo test -p aibd --locked --lib "seat_rank::tests::$3"; }
# RS1 a role is set to the owner
rs '            rank: rank().filter(|k| *k <= Rank::Lead)?,' '            rank: rank()?,' \
   only_the_four_shapes_are_taken
# RS2 taking a grant back needs the role still listed (N4)
rs '        Change::Role { .. } | Change::Grant { .. } if !f.role_listed => {' '        _ if !f.role_listed => {' \
   a_grant_needs_a_listed_role_and_a_live_seat
# RS3 the ticket does not bind the rank
rs '        Change::Perm { act, rank } => json!({"what": "perm", "act": act, "rank": rank.word()}),' \
   '        Change::Perm { act, rank: _ } => json!({"what": "perm", "act": act}),' \
   the_ticket_binds_the_exact_change
