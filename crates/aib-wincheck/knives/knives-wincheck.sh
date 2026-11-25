#!/usr/bin/env bash
# aib-wincheck: the Windows hands-on check's pure rules (PLAN-windows-onsite-90856-1125 §3 A;
# c2f0b REVIEW-wincheck-gnu-rust M1)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
f=crates/aib-wincheck/src/lib.rs
k() { python3 -I scripts/knife.py "$f" "$1" "$2" --expect-fail "$3" -- cargo test -p aib-wincheck --locked --lib "$3"; }
# WC1 a variable of the person's own setup (a rustup mirror, RUSTFLAGS) reaches the child
k '        .filter(|(k, _)| KEPT_ENV.contains(&k.as_str()))' \
  '        .filter(|_| true) // knife' \
  a_child_keeps_only_the_listed_variables
# WC2 a sums line naming a file outside the pack is taken
k '        if leaves {' \
  '        if false && leaves {' \
  the_sums_file_is_read_strictly
# WC3 a file of the pack the sums do not name is run
k '        if !sums.contains_key(name) {' \
  '        if false {' \
  every_file_of_the_pack_must_be_the_one_in_the_sums
# WC4 a file that differs from its sum is run
k '            Some(h) if h != want => problems.push(format!("SUMS-DIFFER {name}")),' \
  '            Some(h) if false && h != want => problems.push(format!("SUMS-DIFFER {name}")),' \
  every_file_of_the_pack_must_be_the_one_in_the_sums
# WC5 a person-check step does not run its #[ignore] tests
k '    if s.ignored {' \
  '    if !s.ignored {' \
  a_step_passes_its_filter_exactly_and_runs_ignored_tests_only_when_asked
# WC6 a red check is written as OK
k '        if self.red.is_empty() {' \
  '        if true {' \
  the_result_file_ends_with_the_verdict_and_the_sum_of_what_is_above
# WC7 failed tests are not counted
k '                "failed" => c.failed += n,' \
  '                "failed" => {}' \
  libtest_results_are_added_up_and_failed_names_read
# WC8 a folder someone else made at the root is emptied (c2f0b REVIEW M1)
k '        (true, false) => Err("ROOT-NOT-OURS"),' \
  '        (true, false) => Ok(true),' \
  a_root_is_emptied_only_when_it_is_ours
# WC9 an executable changed after the check is run (c2f0b REVIEW M1)
k '        (Some(b), Some(w)) => *w == sha256_hex(b),' \
  '        (Some(_), Some(_)) => true,' \
  an_executable_runs_only_while_it_is_the_one_in_the_sums
# WC10 a DLL put beside a test executable is not seen (c2f0b REVIEW L1)
k '                && !sums.contains_key(&format!("{TREE}{f}"))' \
  '                && false' \
  a_file_in_the_root_the_pack_did_not_bring_is_found
