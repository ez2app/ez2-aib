#!/usr/bin/env bash
# aib-xtask ci-scope knives (S66-scope s1, 56cda; 519d0 11-17 00:37: xtask knives live in xtask/knives/*.sh).
# Not run by CI yet: the generic knives loop (S66-loop, ebb36 widening #804's aib-drive loop) will pick
# this folder up; until then receipts run these. Run from the repo root.
#
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cs() { python3 -I scripts/knife.py xtask/src/ci_scope.rs "$1" "$2" --expect-fail "$3" \
       -- cargo test -p aib-xtask --locked "ci_scope::tests::$3"; }
# CS1 header change is not full
cs '        if bj.1 != hj.1 {' \
   '        if false && bj.1 != hj.1 {' \
   a_header_another_job_or_the_top_level_is_full
# CS2 top level change is not full
cs '    if b.top != h.top {' \
   '    if false && b.top != h.top {' \
   a_header_another_job_or_the_top_level_is_full
# CS3 another job's steps count as knife steps
cs '            if !KNIFE_JOBS.contains(&bj.0.as_str()) {' \
   '            if false && !KNIFE_JOBS.contains(&bj.0.as_str()) {' \
   a_header_another_job_or_the_top_level_is_full
# CS4 another workflow file is let through
cs '        .find(|f| f.starts_with(".github/") && **f != AIBD_YML)' \
   '        .find(|f| false && f.starts_with(".github/") && **f != AIBD_YML)' \
   the_file_rules_come_first_and_main_pushes_are_full
# CS5 a docs file tests read counts as docs
cs '    if !p.ends_with(".md") || DOC_EXCLUDE.contains(&p) {' \
   '    if !p.ends_with(".md") || false && DOC_EXCLUDE.contains(&p) {' \
   the_file_rules_come_first_and_main_pushes_are_full
# CS6 a 1.x yml reader dropped from the list
cs '    "tests/test_aib_prepr.py",' \
   '    "aib/tests/test-ci-catchup-1ced7-1108.py",' \
   every_1x_test_that_reads_aibd_yml_is_on_the_list
# CS7 a job-level key after a knife job's steps is read as steps (5d33a L1)
cs '        if indent_is(bare, 2) || (jobs.last().is_some_and(|j| j.3) && indent_is(bare, 4)) {' \
   '        if indent_is(bare, 2) || (false && jobs.last().is_some_and(|j| j.3) && indent_is(bare, 4)) {' \
   a_header_another_job_or_the_top_level_is_full
