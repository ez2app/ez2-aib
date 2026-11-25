#!/usr/bin/env bash
# aib-wincheck knives WCK1–WCK16 (90856 PLAN-windows-onsite §3 A; c2f0b / f163c REVIEW-wincheck): the
# Windows hands-on check's rules, each broken, turns its own test red.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
wl() { python3 -I scripts/knife.py crates/aib-wincheck/src/lib.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-wincheck --locked --lib "tests::$3"; }
# WCK1 a variable of the person's own setup (a rustup mirror, RUSTFLAGS) reaches the child (c2f0b gnu-rust M1)
wl '        .filter(|(k, _)| KEPT_ENV.contains(&k.as_str()))' \
   '        .filter(|(k, _)| !k.is_empty()) // knife' \
   a_child_keeps_only_the_listed_variables
# WCK2 a sums line naming a file outside the pack is taken
wl '        if leaves {' \
   '        if false && leaves { // knife' \
   the_sums_file_is_read_strictly
# WCK3 a file of the pack the sums do not name is run
wl '        if !sums.contains_key(name) {' \
   '        if sums.is_empty() && !sums.contains_key(name) { // knife' \
   every_file_of_the_pack_must_be_the_one_in_the_sums
# WCK4 a file that differs from its sum is run
wl '            Some(h) if h != want => problems.push(format!("SUMS-DIFFER {name}")),' \
   '            Some(h) if false && h != want => problems.push(format!("SUMS-DIFFER {name}")), // knife' \
   every_file_of_the_pack_must_be_the_one_in_the_sums
# WCK5 a person-check step does not run its #[ignore] tests
wl '    if s.ignored {' \
   '    if !s.ignored { // knife' \
   a_step_passes_its_filter_exactly_and_runs_ignored_tests_only_when_asked
# WCK6 a red check is written as OK
wl '        if !self.red.is_empty() {' \
   '        if false && !self.red.is_empty() { // knife' \
   the_result_file_ends_with_the_verdict_and_the_sum_of_what_is_above
# WCK7 failed tests are not counted
wl '                "failed" => c.failed += n,' \
   '                "failed" => c.failed += 0 * n, // knife' \
   libtest_results_are_added_up_and_failed_names_read
# WCK8 a folder someone else made at the root is emptied (c2f0b REVIEW M1)
wl '        (true, false) => Err("ROOT-NOT-OURS"),' \
   '        (true, false) => Ok(true), // knife' \
   a_root_is_emptied_only_when_it_is_ours
# WCK9 an executable changed after the check is run (c2f0b REVIEW M1)
wl '        (Some(b), Some(w)) => *w == sha256_hex(b),' \
   '        (Some(b), Some(w)) => !b.is_empty() || !w.is_empty(), // knife' \
   an_executable_runs_only_while_it_is_the_one_in_the_sums
# WCK10 a DLL put beside a test executable is not seen (c2f0b REVIEW L1)
wl '                && !sums.contains_key(&format!("{TREE}{f}"))' \
   '                && sums.is_empty() // knife' \
   a_file_in_the_root_the_pack_did_not_bring_is_found
# WCK11 placing the tree ignores whose the root is: a folder someone else made is emptied (f163c REVIEW L1)
wl '    match may_clear(root.exists(), root.join(MARK).is_file()) {' \
   '    match may_clear(root.exists(), true) { // knife' \
   a_root_someone_else_made_is_left_as_it_was
# WCK12 a placed executable changed after the check is run (f163c REVIEW L1)
wl '    if !still_same(&root.join(exe), &format!("{TREE}{exe}"), sums) {' \
   '    if sums.is_empty() && !still_same(&root.join(exe), &format!("{TREE}{exe}"), sums) { // knife' \
   a_placed_executable_changed_or_given_a_neighbour_is_not_run
# WCK13 the root's rights are not checked again before a run (f163c REVIEW L2)
wl '    if let Err(e) = (g.check)(root) {' \
   '    if let Err(e) = (g.check)(root).or(Ok::<(), String>(())) { // knife' \
   a_folder_that_cannot_be_made_ours_stops_everything
# WCK14 another account's entry in the saved access list is let through (f163c REVIEW L2; c2f0b f0c0df9a L2: by SID)
wl '        if !sid.eq_ignore_ascii_case(me) {' \
   '        if false && !sid.eq_ignore_ascii_case(me) { // knife' \
   whoami_and_icacls_are_read_strictly
# WCK15 a deny entry is read as this account's full control (c2f0b REVIEW f0c0df9a N1)
wl '        if kind != "A" {' \
   '        if false && kind != "A" { // knife' \
   whoami_and_icacls_are_read_strictly
# WCK16 a folder that still inherits rights (C:\ gives Authenticated Users modify) is let through
wl '    if !flags.contains('\''P'\'') {' \
   '    if false && !flags.contains('\''P'\'') { // knife' \
   whoami_and_icacls_are_read_strictly
# WCK17 a red test that needs a tool is let off even where the tool is there (ebab8 11-25 19:04)
wl '        .filter(|n| !has(n.tool))' \
   '        .filter(|n| !has(n.tool) || true) // knife' \
   a_red_test_that_needs_a_missing_tool_is_named_not_counted
# WCK18 the same test names in another package are let off too
wl '        .find(|n| n.package == package && n.suite == suite && n.test == test)' \
   '        .find(|n| (n.package == package || true) && n.suite == suite && n.test == test) // knife' \
   a_red_test_that_needs_a_missing_tool_is_named_not_counted
# WCK19 a red test's output is not cut at max lines
wl '        .take(max)' \
   '        .take(max.max(usize::MAX)) // knife' \
   a_red_tests_own_output_is_taken_from_libtests_report
# WCK20 a red test's output runs on into the next test's
wl '        .take_while(|l| !l.starts_with("---- ") && l.trim_end() != "failures:")' \
   '        .take_while(|l| (!l.starts_with("---- ") || true) && l.trim_end() != "failures:") // knife' \
   a_red_tests_own_output_is_taken_from_libtests_report
# WCK21 skipped steps (--no-ask) end WINCHECK-OK
wl '        } else if !self.skipped.is_empty() {' \
   '        } else if false && !self.skipped.is_empty() { // knife' \
   a_skipped_step_makes_the_result_incomplete_not_ok_and_red_still_wins
# WCK22 incomplete even when something is red
wl '        self.red.is_empty() && !self.skipped.is_empty()' \
   '        !self.skipped.is_empty() // knife' \
   a_skipped_step_makes_the_result_incomplete_not_ok_and_red_still_wins
# WCK23 tests that need a missing tool are not counted above the verdict (f163c 3cfd N1)
wl '        if self.needs > 0 {' \
   '        if self.needs > 9999 { // knife' \
   tests_that_need_a_missing_tool_are_counted_above_the_verdict
# WCK24 System32's bash.exe (the WSL launcher) counts as having bash (ebab8 19:50)
wl '    d.ends_with("\\windows\\system32") || d.ends_with("\\windowsapps")' \
   '    d.ends_with("\\windows\\system32-knife") || d.ends_with("\\windowsapps")' \
   the_wsl_launcher_folders_do_not_count_as_having_bash
# WCK25 the separator is not unified, so C:/Windows/System32 is not seen (f163c f05a L1: anchor after rustfmt)
wl '        .replace('"'"'/'"'"', "\\")' \
   '        .replace('"'"'\\'"'"', "\\") // knife' \
   the_wsl_launcher_folders_do_not_count_as_having_bash
# WCK26 bash is probed with --version (WSL launcher may answer it) instead of running a command
wl '        "bash" => &["-c", "exit 0"],' \
   '        "bash" => &["--version"], // knife' \
   the_wsl_launcher_folders_do_not_count_as_having_bash
