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
k '                        ((st.cmd == "true" || st.cmd == ":") && st.args.is_empty()) || zero;' \
  '                        ((st.cmd == "true" || st.cmd == ":") && false) || zero; // knife' \
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
# K12 a `set` inside a function body leaks into the lines after the definition
k '            if body != Some(o.body) {' \
  '            if body != Some(o.body) && false { // knife' \
  a_set_inside_a_function_body_does_not_leak_out
# K13 U1: a variable as the command is let through (ebb36 #653 L1)
k '                    && !shape.is_readable_var_cmd(c)' \
  '                    && false && !shape.is_readable_var_cmd(c) // knife' \
  u1_a_command_that_is_built_at_run_time_is_unknown
# K14 U1: a here-string into a shell is let through
k '                if shell && st.text.contains("<<<") {' \
  '                if shell && st.text.contains("<<<") && false { // knife' \
  u1_a_command_that_is_built_at_run_time_is_unknown
# K15 S2: `|| exit 0` is not seen (L2)
k '                        && st.args[0] == "0";' \
  '                        && st.args[0] == "0" && false; // knife' \
  s2_exit_0_and_a_bare_bang_swallow_the_failure
# K16 S2: a bare `! cmd` is not seen (L3)
k '            && first.words.first().map(String::as_str) == Some("!")' \
  '            && first.words.first().map(String::as_str) == Some("!") && false // knife' \
  s2_exit_0_and_a_bare_bang_swallow_the_failure
# K17 S4: `<(remote …)` is not seen (L4)
k '        if s.code.split("<(").skip(1).any(|r| {' \
  '        if false && s.code.split("<(").skip(1).any(|r| { // knife' \
  s4_a_remote_call_in_a_process_substitution
# K18 R1: a rehearsal line with bidi controls or a long value counts (M1)
k '        if !shown_ok(env, REHEARSAL_ENV_MAX) || !shown_ok(receipt, REHEARSAL_RECEIPT_MAX) {' \
  '        if false && (!shown_ok(env, REHEARSAL_ENV_MAX) || !shown_ok(receipt, REHEARSAL_RECEIPT_MAX)) { // knife' \
  r1_a_rehearsal_line_that_would_mislead_the_dialog_does_not_count
echo "signlint knives: 18/18 bite"
