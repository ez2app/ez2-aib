#!/usr/bin/env bash
# aib-test-cargo knives (043dd, S69-u2): each rule of the nextest reader, broken on purpose,
# must turn its own test red. RUSTFLAGS has -D warnings: no knife may leave an unused or
# unreachable item behind (that would be a compile error, a fake bite). RULES §21: knives in crates/<crate>/knives/. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
tk() { python3 -I scripts/knife.py "crates/aib-test-cargo/src/lib.rs" "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-test-cargo --locked --lib "$3"; }
# N1 a picked test with no result counts as a pass
tk '    let met = !bad && missing.is_empty() && unexpected.is_empty() && ran > 0;' \
   '    let met = !bad && unexpected.is_empty() && ran > 0; // knife' \
   a_picked_test_with_no_result_is_not_a_pass
# N2 a result the list did not pick is let through
tk '                unexpected.push(k);' '                unexpected.truncate(0); let _ = k; // knife' \
   a_result_the_list_did_not_pick_means_they_disagree
# N3 a flaky pass reads as a pass
tk '        } else if body.contains("<flakyFailure") || body.contains("<flakyError") {' \
   '        } else if body.contains("<never-a-tag") { // knife' \
   a_failure_or_a_flaky_pass_is_not_met
# N4 a binary left out whole is dropped without a name
tk '                out.skipped_binaries.push(id.clone());' '                let _ = id; // knife' \
   a_binary_left_out_whole_is_named_and_an_unknown_status_is_an_error
# N7 nothing run reads as met
tk '    let met = !bad && missing.is_empty() && unexpected.is_empty() && ran > 0;' \
   '    let met = !bad && missing.is_empty() && unexpected.is_empty(); // knife' \
   nothing_picked_and_nothing_run_is_not_met
# N8 the list leaves the aib profile out
tk '        "--profile",' '        "--profile-was-here", // knife' \
   the_run_uses_the_aib_profile_and_the_list_the_same_selection
# N5 a partition counts as the user's own filter
tk '            LeftOut::Partition | LeftOut::DefaultFilter | LeftOut::Other(_) => {' \
   '            LeftOut::Partition => Some(SkipWhy::UserFilter), LeftOut::DefaultFilter | LeftOut::Other(_) => { // knife' \
   a_partition_or_default_filter_is_not_the_users_choice
# N6 the run retries (a second-try pass hides a failure)
tk 'retries = 0' 'retries = 2' the_run_uses_the_aib_profile_and_the_list_the_same_selection
