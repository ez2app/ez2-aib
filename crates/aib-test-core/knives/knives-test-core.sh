#!/usr/bin/env bash
# aib-test-core knives (043dd, 1ced7 u1 M2): each rule of the key / skip / exit core, broken on
# purpose, must turn its own test red. RULES §21: knives live in crates/<crate>/knives/;
# aib-xtask knife-anchors reads crates/*/knives/*.sh. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
tk() { python3 -I scripts/knife.py "crates/aib-test-core/src/$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-test-core --locked --lib "$4"; }
# E1 a pass that tested nothing (tests_ran None) counts as a pass
tk exit.rs '                if tests_ran.unwrap_or(0) > 0 {' '                if tests_ran.unwrap_or(1) > 0 { // knife' \
   a_pass_with_no_test_run_is_a_failure
# E2 a failure that ran now is covered by a reused pass
tk exit.rs '            Outcome::Ran { met: false, .. } => e = e.worse(Exit::Failed),' \
   '            Outcome::Ran { met: false, .. } => {} // knife' \
   a_reused_pass_does_not_cover_a_failure_that_ran_now
# E3 a spot check that differs outranks a failure
tk exit.rs '            Exit::SpotCheckDiffers => 6,' '            Exit::SpotCheckDiffers => 8, // knife' \
   the_worst_code_wins_in_the_written_order
# S1 "not this OS" with no OS named is harmless
tk skip.rs '            SkipWhy::NotThisOs { os } => !os.trim().is_empty(),' \
   '            SkipWhy::NotThisOs { os } => !os.is_empty() || os.is_empty(), // knife' \
   the_os_and_event_skips_are_harmless_anywhere
# S2 the user's own filter is harmless in evidence too
tk skip.rs '            SkipWhy::UserFilter => used == Use::Local,' \
   '            SkipWhy::UserFilter => used == Use::Local || used == Use::Evidence, // knife' \
   a_user_filter_is_harmless_locally_but_not_as_evidence
# S3 "not this OS" naming the OS the run is on is harmless
tk skip.rs '            if os.trim().eq_ignore_ascii_case(run.trim()) {' \
   '            if os.trim() == "never-an-os" && !run.is_empty() { // knife' \
   the_os_and_event_skips_are_harmless_anywhere
# K1 a file changed inside the racy window is cached
tk key.rs '        .is_none_or(|age| age < RACY);' '        .is_none_or(|age| age < Duration::ZERO); // knife' \
   a_same_size_rewrite_keeping_the_old_time_is_still_seen
# K2 the cache trusts the length alone
tk key.rs '            if old == st {' '            if old.len == st.len { // knife' \
   an_old_unchanged_file_comes_from_the_cache_and_a_rewrite_is_read_again
# K3 a declared path that matched nothing reads as unchanged
tk key.rs '        if hit.is_empty() {' '        if hit.is_empty() && pre == "never" { // knife' \
   a_declared_path_that_matches_nothing_is_not_measured
# C1 a variable's value goes into the entries as written (a registry token on disk)
tk context.rs '                m.insert(format!("env:{k}"), sha(v));' \
   '                m.insert(format!("env:{k}"), v.clone()); // knife' \
   a_registry_token_never_shows_in_the_entries_and_still_moves_the_key
# C2 an empty toolchain still gives a key
tk context.rs '        if self.toolchain.trim().is_empty() {' '        if self.toolchain.trim() == "never" { // knife' \
   an_empty_toolchain_or_machine_kind_is_not_measured
