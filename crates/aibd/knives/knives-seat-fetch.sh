#!/usr/bin/env bash
# aib-knives: os=unix
# G4b / G8 (DESIGN-g4-7b81e-1121; 5d33a PREREAD-g4-7b81e-5d33a-1121). Run from the repo root.
set -euo pipefail
# G4B1 a web seat cannot switch branches in its own copy
python3 -I scripts/knife.py crates/aib-seatopen/src/lib.rs \
  '    "Bash(git switch *)", // G4b writes' \
  '    "Bash(git switch -c *)", // G4b writes' \
  --expect-fail a_work_seat_reads_git_anywhere_and_reaches_nothing_outside \
  -- cargo test -p aib-seatopen --locked --lib a_work_seat_reads_git_anywhere_and_reaches_nothing_outside
# G8R1 (L1) the registered repo's settings are not read as data before git runs there
python3 -I scripts/knife.py crates/aibd/src/seat_fetch.rs \
  '    repo_safe(repo).map_err(|_| No::RepoUnsafe)?;' \
  '    let _ = repo_safe(repo).map_err(|_| No::RepoUnsafe);' \
  --expect-fail a_repo_or_copy_with_settings_beyond_the_list_is_not_fetched_from \
  -- cargo test -p aibd --locked --lib a_repo_or_copy_with_settings_beyond_the_list_is_not_fetched_from
# G8S1 (L2) a seat's token fetches into another seat's copy
python3 -I scripts/knife.py crates/aibd/src/seat_fetch.rs \
  '        (Some(t), Some(b)) if b != t => Err((StatusCode::FORBIDDEN, "fetch_other_seat")),' \
  '        (Some(t), Some(b)) if b != t && t.is_empty() => Err((StatusCode::FORBIDDEN, "fetch_other_seat")),' \
  --expect-fail a_seats_token_fetches_for_that_seat_only \
  -- cargo test -p aibd --locked --lib a_seats_token_fetches_for_that_seat_only
# G8N1 no limit on how often a seat fetches
python3 -I scripts/knife.py crates/aibd/src/seat_fetch.rs \
  '    if q.len() >= PER_MINUTE {' \
  '    if q.len() >= PER_MINUTE * 2 {' \
  --expect-fail a_seat_fetches_at_most_thirty_times_a_minute \
  -- cargo test -p aibd --locked --lib a_seat_fetches_at_most_thirty_times_a_minute
# G8F1 a short name or branch name is fetched
python3 -I scripts/knife.py crates/aibd/src/seat_fetch.rs \
  '    if !crate::send_back::sha_ok(sha) {' \
  '    if !crate::send_back::sha_ok(sha) && sha.is_empty() {' \
  --expect-fail a_commit_of_the_registered_repo_comes_in_as_fetch_head_and_no_branch_moves \
  -- cargo test -p aibd --locked --lib a_commit_of_the_registered_repo_comes_in_as_fetch_head_and_no_branch_moves
