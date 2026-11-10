#!/usr/bin/env bash
# aib-signlint knives (c477a 11-11, DEV 4.6 r2 §9 P2): each knife takes one rule's
# judgement away and must turn that rule's own test red. Run from the repo root.
set -eu
# FILE is written out (not a variable): aib-xtask knife-anchors (#590) reads every
# scripts/knife.py call before the push and only understands plain text and $1..$9.
k() { python3 -I scripts/knife.py crates/aib-signlint/src/lib.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-signlint --locked --test signlint "$3"; }
# K1 S1: a pipeline under pipefail ending in grep -q / head is let through
k '    early_stop(last) && !PRINTERS.contains(&producer.cmd.as_str())' \
  '    early_stop(last) && !PRINTERS.contains(&producer.cmd.as_str()) && false // knife' \
  s1_pipefail_with_an_early_stop
# K2 S2: `|| true` is not seen
k '                    if (st.cmd == "true" || st.cmd == ":")' \
  '                    if (st.cmd == "true" || st.cmd == ":") && false // knife' \
  s2_swallowed_failures
# K3 S2: `a && b || c` is not seen
k '                if !b_print && !c_stops {' \
  '                if !b_print && !c_stops && false { // knife' \
  s2_swallowed_failures
# K4 S3: $(…) inside a test bracket is not seen
k '                if is_test_cmd(&st.cmd) && has_subst(&st.text) {' \
  '                if is_test_cmd(&st.cmd) && has_subst(&st.text) && false { // knife' \
  s3_output_without_the_exit_code
# K5 S4: `if ! <ssh> test …; then <goes on>` is taken as stopping
k '                return self.branch_stops(i + 1);' \
  '                return true; // knife: the then-branch is taken as stopping' \
  s4_an_unreachable_host_is_not_an_absent_file
# K6 S4: on a restore path a missing backup may be skipped
k '                && !st.negated' \
  '                && !st.negated && false // knife' \
  s4_on_a_restore_path_a_missing_backup_is_not_skipped
# K7 S5: a restore that never reads back is let through
k '            if !read_after {' \
  '            if !read_after && false { // knife' \
  s5_a_restore_reads_back_what_it_wrote
# K8 U1: eval / source are read as if they were plain commands
k '                if matches!(st.cmd.as_str(), "eval" | "source" | ".") {' \
  '                if false && matches!(st.cmd.as_str(), "eval" | "source" | ".") { // knife' \
  u1_what_cannot_be_read_is_unknown
# K9 R1: a script without the rehearsal line is let through
k '    if rehearsal(text).is_none() {' \
  '    if rehearsal(text).is_none() && false { // knife' \
  r1_the_rehearsal_line_is_needed
# K10 heredocs fed to a shell are not read
k '        if d.shell {' \
  '        if d.shell && false { // knife' \
  a_heredoc_for_a_shell_is_its_own_script_and_others_are_data
# K11 U1 / R1 can be acked away
k '    RULES.iter().any(|(c, a, _)| *c == code && *a)' \
  '    RULES.iter().any(|(c, _a, _)| *c == code) // knife: every rule ackable' \
  acks_let_s_rules_through_and_never_u1_or_r1
echo "signlint knives: 11/11 bite"
