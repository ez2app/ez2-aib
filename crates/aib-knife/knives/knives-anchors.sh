#!/usr/bin/env bash
# aib-knives: os=unix
# The "knife anchors knives (macOS)" step of aibd.yml's rust job, moved here word for word
# (519d0 11-16 23:5x: aibd.yml was 448,728 B of the 450,000 its line 44 allows; new knives go in
# crates/<c>/knives/, the workflow keeps the call). These knives break aib-xtask knife-anchors
# (xtask/src/knife_anchors.rs); they sit with aib-knife, the knife crate, because xtask/ is not a
# Rust CI path and crates/** is. Run from the repo root; -eo pipefail is what GitHub's
# `shell: bash` gave the step when it was inline.
#
# the checker itself: a moved anchor must stay red, and a knife line it
# cannot read must never be skipped. Each knife must hit its own test.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
ka() { python3 -I scripts/knife.py xtask/src/knife_anchors.rs "$1" "$2" \
         --expect-fail "$3" -- cargo test -p aib-xtask --locked; }
ka '    text.matches(old).count()' '    let _ = (text, old); 1 // knife: always once' \
  run_is_red_when_an_anchor_moved_and_green_when_it_is_there
ka '                    Err(e) => scan.unresolved.push(format!("{at}: {e}")),' \
  '                    Err(_) => {} // knife: skip what cannot be read' \
  what_cannot_be_read_is_unresolved_not_skipped
# KA3 (1ced7 #590 L3) a line that mentions knife.py in a form not read is skipped
ka '        if mentions && !handled {' '        if mentions && !handled && false { // knife' \
  what_cannot_be_read_is_unresolved_not_skipped
# KA4 (1ced7 #590 L3) no knife found reads as clean
ka '    if !unresolved.is_empty() || knives.is_empty() {' \
  '    if !unresolved.is_empty() || (knives.is_empty() && false) { // knife' \
  run_is_red_when_an_anchor_moved_and_green_when_it_is_there
# KA5 (1ced7 #590 H1) $'...' escapes not decoded (this line is itself $'...')
ka $'                    \'n\' => \'\\n\',' $'                    \'n\' => \'n\', // knife' \
  ansi_c_quoted_anchors_are_read_like_bash
# KA6 (a63ab #590 / #623 ck) a positional inside a word is not filled in
ka '                s.push_str(&t);' '                let _ = &t; // knife: argument dropped' \
  a_positional_inside_a_word_is_filled_in
# KA7 (519d0 11-15 08:11) the knife scripts under crates/*/knives/ are not read
ka '            files_in(&c.join("knives"), "sh", &mut files);' \
  '            let _ = &c; // knife' \
  a_crates_own_knife_script_is_read
# KA8 (ebb36 S66-shared 1) a loop over crates/<c>/knives/*.sh owns none of its scripts
ka '                    Some(dir) if dir.ends_with("/knives/") => scripts' \
  '                    Some(dir) if dir.ends_with("/knives/") && false => scripts // knife' \
  a_script_belongs_to_the_step_that_runs_it
# KA9 a script's knives keep an empty step (the ledger cannot key them by step)
ka '            k.step.clone_from(step);' \
  '            let _ = step; // knife' \
  the_repo_knives_each_have_a_step
# KA10 a script a second step also runs is not reported
ka '                        Some((j, st)) if *j == job && *st == step => {}' \
  '                        Some((j, st)) if *j == job || *st != step => {} // knife' \
  a_script_belongs_to_the_step_that_runs_it
# KA11 a comment line naming a script makes that step its owner
ka '            if step.is_empty() || line.trim_start().starts_with('\''#'\'') {' \
  '            if step.is_empty() { // knife' \
  a_script_belongs_to_the_step_that_runs_it
# KA12 (1ced7 kspec M1) a script a second step also runs is found and then dropped
ka '    unresolved.extend(twice);' '    let _ = twice; // knife' \
  a_script_two_steps_run_is_unresolved
# KA13 (7ebca 11-19) a knife whose test is not in the binary it runs passes
ka '    } else if bad > 0 || missing > 0 {' '    } else if bad > 0 { // knife' \
  run_is_red_when_a_knifes_test_is_not_in_the_binary_it_runs
kt() { python3 -I scripts/knife.py xtask/src/knife_testname.rs "$1" "$2" \
         --expect-fail "$3" -- cargo test -p aib-xtask --locked; }
# KT1 a --test binary is read as the whole package
kt '        return after("--test").map(|n| (pkg, Target::Test(n)));' \
   '        return Some((pkg, Target::All)); // knife' \
  a_knife_for_a_unit_test_run_against_an_integration_binary_is_found
# KT2 the files a test binary pulls in by #[path] are not read
kt '            for rel in path_attrs(&text) {' \
   '            for rel in path_attrs(&text).into_iter().take(0) {' \
  a_knife_for_a_unit_test_run_against_an_integration_binary_is_found
# KT3 the module path of the expected name is matched whole
kt '    let last = expect.rsplit("::").next().unwrap_or(expect);' \
   '    let last = expect;' \
  function_names_and_the_last_part_of_the_expected_name
# KT4 a word ending in fn starts a name
kt '        if at > 0 && ident(b[at - 1]) {' \
   '        if false && at > 0 && ident(b[at - 1]) {' \
  function_names_and_the_last_part_of_the_expected_name
# KT5 a module filter must declare its last part (7b81e 11-19: slots::tests:: knives were NOTEST)
kt '        (is_first || declares(&text, first)) && (parts.len() == 1 || declares(&text, last))' \
   '        (is_first || declares(&text, first)) && (parts.len() == 1 || true)' \
  function_names_and_the_last_part_of_the_expected_name
# KT6 a module filter must be that file (or declared by it)
kt '        (is_first || declares(&text, first)) && (parts.len() == 1 || declares(&text, last))' \
   '        (is_first || true || declares(&text, first)) && (parts.len() == 1 || declares(&text, last))' \
  function_names_and_the_last_part_of_the_expected_name
