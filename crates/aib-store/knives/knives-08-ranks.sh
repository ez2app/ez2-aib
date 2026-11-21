#!/usr/bin/env bash
# who may mark urgent: ranks only from the owner's grants (OPINION-role-rank-a9136-1121 r2; 5d33a PREREAD-inboxaibd F r2)
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
rk() { python3 -I scripts/knife.py crates/aib-store/src/ranks.rs "$1" "$2" --expect-fail "$3" \
          -- cargo test -p aib-store --locked --lib "ranks::tests::$3"; }
# RK1 a grant taken back still counts
rk '             WHERE g.seat_id = ?1 AND g.revoked_at IS NULL",' \
   '             WHERE g.seat_id = ?1",' \
   a_seat_has_the_highest_rank_the_owner_granted
# RK2 a seat reads as the owner
rk '    Ok(Rank::of_num(n.unwrap_or(0)).min(Rank::Lead))' \
   '    Ok(Rank::of_num(n.unwrap_or(0))) // knife' \
   a_seat_is_never_the_owner
# RK2b a role is set to owner
rk '            rank <= Rank::Lead,' \
   '            rank <= Rank::Owner,' \
   a_seat_is_never_the_owner
# RK3 an act with no least rank is open to all (Q5)
rk 'pub const DEFAULT_MIN: Rank = Rank::Lead;' \
   'pub const DEFAULT_MIN: Rank = Rank::Plain;' \
   an_act_with_no_least_rank_needs_a_lead
