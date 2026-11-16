#!/usr/bin/env bash
# The "aib-drive observe knives (macOS)" step of aibd.yml's rust job, moved here word for word
# (RULES §21, Andy 11-15 #15, 519d0 S79-rule21; the aib-cloud-host knives went first, 11-15 08:08).
# aibd.yml runs every crates/aib-drive/knives/*.sh in name order in one step; aib-xtask knife-anchors
# reads crates/*/knives/*.sh, so every knife here is still checked. Run from the repo root;
# -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
#
# aib-drive observe (PR-C, 1ced7 VERIFY-PLAN K2 / K3 / K5 / K6 + lock, backoff, quota):
# each guard, broken on purpose, must turn its own test red. K1 / K4 (never executes, never
# writes outside the state root): aib-drive has no executor and no write call to break; the
# end-to-end test with fake gh / aib on PATH checks every program call is a read, and the
# quota-read knives run against it.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
ok() { python3 -I scripts/knife.py crates/aib-drive/src/observe.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test observe "$3"; }
# once while paused runs anyway
ok '        return Err(Refused::Paused(format!("暫停中:{}", paths.pause.display())));' \
   '        let _ = paths; // knife' \
   a_round_while_paused_is_refused_and_writes_nothing
# --watch ignores the pause file
ok '        if paths.pause.exists() {' \
   '        if false && paths.pause.exists() { // knife' \
   watch_marks_the_pause_and_the_resume_once_each
# the journal is rewritten, not appended
ok '        .append(true)' \
   '        .write(true).truncate(true) // knife' \
   two_rounds_append_two_lines_and_decide_the_same
# EZ2_AIB_STATE_ROOT is ignored
ok '    if let Some(r) = env_root.filter(|r| !r.is_empty()) {' \
   '    if let Some(r) = env_root.filter(|r| r.is_empty() && !r.is_empty()) { // knife' \
   the_state_root_is_the_env_root_or_the_home_default
# EZ2_AIB_HOME is ignored (two roots, two locks)
ok '    if let Some(h) = aib_home.filter(|h| !h.is_empty()) {' \
   '    if let Some(h) = aib_home.filter(|h| h.is_empty() && !h.is_empty()) { // knife' \
   the_state_root_is_the_env_root_or_the_home_default
# a held lock is taken over
ok '    match f.try_lock() {' \
   '    match Ok::<(), TryLockError>(()).or(f.try_lock()) { // knife' \
   a_second_observe_is_refused_with_the_holders_pid
# the backoff has no cap
ok '            return cap;' \
   '            return d; // knife' \
   failed_rounds_back_off_to_the_cap_and_a_good_one_goes_back
# failures are not counted
ok '            failures.saturating_add(1)' \
   '            failures.saturating_mul(0) // knife' \
   failed_rounds_back_off_to_the_cap_and_a_good_one_goes_back
# the reason of a failed snapshot is dropped
ok '            Some(clean(&e, MAX_REASON)),' \
   '            Some(clean(&e, 0)), // knife' \
   a_failed_snapshot_is_recorded_with_its_reason
# outside text keeps its control characters
ok '            if c.is_control() || is_format(c) {' \
   '            if is_format(c) { // knife' \
   outside_text_is_cleaned_and_cut
# a right-to-left override is kept
ok '            | '"'"'\u{202a}'"'"'..='"'"'\u{202e}'"'"'' \
   '            | '"'"'\u{202a}'"'"'..='"'"'\u{202d}'"'"' // knife' \
   format_characters_are_cleaned_too
# --interval has no floor
ok '    if d < MIN_INTERVAL {' \
   '    if d < Duration::ZERO { // knife' \
   an_interval_under_60_seconds_is_refused
# a last line longer than the first read is never found
ok '        if want >= len {' \
   '        if true { // knife' \
   the_last_heads_are_read_from_the_end_of_a_long_journal
# the holder's pid is read from the locked file (unreadable on Windows)
ok '            let who = std::fs::read_to_string(pid).unwrap_or_default();' \
   '            let who = std::fs::read_to_string(path).unwrap_or_default(); // knife' \
   a_second_observe_is_refused_with_the_holders_pid
# a round runs below the quota floor
ok '        Ok(n) if n >= floor => Ok(()),' \
   '        Ok(n) if n >= floor.min(1) => Ok(()), // knife' \
   a_round_needs_the_quota_floor_left
# the quota read becomes another call
ob() { python3 -I scripts/knife.py crates/aib-drive/src/observe.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test observe_bin "$3"; }
ob '    vec!["api".to_owned(), "rate_limit".to_owned()]' \
   '    vec!["api".to_owned(), "graphql".to_owned()] // knife' \
   the_program_runs_only_reads_and_prints_one_line_and_json
# the program skips the quota gate
om() { python3 -I scripts/knife.py crates/aib-drive/src/main.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test observe_bin "$3"; }
om '        observe::quota_gate(left, observe::QUOTA_FLOOR)?;' \
   '        let _ = (left, observe::QUOTA_FLOOR); // knife' \
   the_program_runs_only_reads_and_prints_one_line_and_json
# V8 real data (#661 / #667 / #678): only the first open PR is read through
vb() { python3 -I scripts/knife.py crates/aib-drive/src/snapshot.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test observe_bin "$3"; }
vb '    for p in list {' \
   '    for p in list.iter().take(1) { // knife' \
   real_prs_661_667_678_are_read_through_and_get_no_merge
# V8 real data: review first lines are not matched to the PR's commits
vs() { python3 -I scripts/knife.py crates/aib-drive/src/snapshot.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test snapshot "$3"; }
vs '        Known::Known(c) => lines_for(files, c),' \
   '        Known::Known(c) => lines_for(&files[..0], c), // knife' \
   real_prs_661_667_678_carry_their_own_review_lines
