#!/usr/bin/env bash
# aib-knives: os=unix   # PL2 / PL3 break the link check, whose test makes links (cfg(unix): a Windows link needs a privilege)
# PL1–PL7 (ebab8 11-26 12:14 C; 3e53b 12:16 conditions): the session policy's look also finds the copy aibd placed
# under <data>/providers/<provider>/<version>/ (L1 / L2), only for this platform's measured rows, after the
# user's own folders, never under a relative data folder or through a link, and named by its placed path as written. Each, broken, turns its own test red.
# Run from the repo root.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
pl() { python3 -I scripts/knife.py crates/aibd/src/session_policy_setup.rs "$1" "$2" --expect-fail "session_policy_setup::tests::$3" \
  -- cargo test -p aibd --locked --lib "session_policy_setup::tests::$3"; }
# PL1 a relative data folder adds its placed folder
pl '    if !data.is_absolute() {' '    if !data.is_absolute() && false { // knife' a_relative_data_folder_adds_no_placed_folder
# PL2 a link on the way down from data is followed
pl '        if link(&at) {' '        if link(&at) && false { // knife' a_placed_folder_or_program_that_is_a_link_is_not_looked_in
# PL3 a placed program that is a link is taken
pl '        .any(|f| link(&dir.join(f)))' '        .any(|f| link(&dir.join(f)) && false) // knife' a_placed_folder_or_program_that_is_a_link_is_not_looked_in
# PL4 every platform's row is a placed folder here
pl '        .filter(|m| PROVIDERS.contains(&m.provider) && m.platform == platform)' '        .filter(|m| PROVIDERS.contains(&m.provider) && (m.platform == platform || !platform.is_empty())) // knife' aibds_placed_copy_is_looked_for_after_the_users_folders_and_only_a_measured_one
# PL5 aibd's copy is looked for before the user's own folders
pl '    dirs.extend(placed);' '    dirs.splice(0..0, placed); // knife' aibds_placed_copy_is_looked_for_after_the_users_folders_and_only_a_measured_one
# PL6 a data folder that is itself a link is looked through
pl '    if link(data) {' '    if link(data) && false { // knife' a_placed_folder_or_program_that_is_a_link_is_not_looked_in
# PL7 the policy names the placed program by another spelling than its placed path
pl '            no_link_below(data, &d, m.provider).then_some(d)' '            no_link_below(data, &d, m.provider).then_some(d.join(".")) // knife' the_policy_names_the_placed_program_by_its_placed_path
echo "policy look placed knives: 7/7 bite"
