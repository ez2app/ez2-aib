#!/usr/bin/env bash
# aib-xtask knife-names knives KN1-KN6 (6770f 11-23; ebab8 08:00: a knife name is used once).
# Run by the "aib-xtask knives (macOS)" step in aibd.yml (one line runs xtask/knives/*.sh).
# Run from the repo root. Each knife must turn its own test red.
#
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
kn() { python3 -I scripts/knife.py xtask/src/knife_names.rs "$1" "$2" --expect-fail "$3" \
       -- cargo test -p aib-xtask --locked "knife_names::tests::$3"; }
# KN1 a name in two files is not a duplicate
kn '        if files.len() > 1 {' \
   '        if files.len() > 2 {' \
   duplicates_across_files_and_in_one_file
# KN2 a name twice in one file is not a duplicate
kn '            if here.len() > 1 {' \
   '            if here.len() > 2 {' \
   duplicates_across_files_and_in_one_file
# KN3 a fixed duplicate may stay in the baseline
kn '        .filter(|k| !dups.contains_key(*k))' \
   '        .filter(|_| false)' \
   only_new_duplicates_fail_and_a_fixed_one_must_leave_the_baseline
# KN4 a header above set -eu names a knife
kn '    if ["set ", "umask", "cd ", "export "]' \
   '    if ["umask", "cd ", "export "]' \
   names_come_from_the_block_above_a_call_and_not_from_a_header
# KN5 one plain word is a kebab name
kn '        && !rest.is_empty()' \
   '        && true' \
   labels
# KN6 a seven-letter code is a name
kn '    (1..=6).contains(&up)' \
   '    (1..=7).contains(&up)' \
   labels
