#!/usr/bin/env bash
# TA1-TA6 (b4297 11-21; DESIGN-add-test-host-b4297-1121): the machines offered, the read of a
# machine, the pinned list, and the two fixed scripts. Every mutant keeps its names used.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ta() { python3 -I scripts/knife.py crates/aibd/src/test_host_add.rs "$1" "$2" \
  --expect-fail "test_host_add::tests::$3" \
  -- cargo test -p aibd --locked --lib "test_host_add::tests::$3"; }
# TA1 a pattern or an option is offered as a machine
ta '            if pick_ok(name) && !out.iter().any(|o| o == name) {' '            if (pick_ok(name) || !name.is_empty()) && !out.iter().any(|o| o == name) { // knife' \
   only_the_names_of_single_machines_are_offered
# TA2 any host key type is taken
ta '        (Some("ssh-ed25519"), Some(b)) => format!("ssh-ed25519 {b}"),' '        (Some(t), Some(b)) => format!("{t} {b}"), // knife' \
   the_read_gives_the_key_without_its_comment_and_refuses_a_machine_that_is_no_mac
# TA3 a machine added again is a second entry
ta '    if let Some(o) = out.iter_mut().find(|o| o.name == h.name) {' '    if let Some(o) = out.iter_mut().find(|o| o.name == h.name && o.port == 0) { // knife' \
   a_machine_is_added_once_replaced_by_name_and_taken_away
# TA4 the line is added again when it is there
ta 'grep -qxF -- "$L" "$F" || printf "%s\n" "$L" >> "$F" || exit 4' 'printf "%s\n" "$L" >> "$F" || exit 4' \
   the_line_is_written_once_after_a_last_newline_and_taken_away_alone
# TA5 a file without its last newline gets the line glued on
ta 'if [ -s "$F" ] && [ -n "$(tail -c1 "$F")" ]; then printf "\n" >> "$F" || exit 4; fi' 'if false; then printf "\n" >> "$F" || exit 4; fi' \
   the_line_is_written_once_after_a_last_newline_and_taken_away_alone
# TA6 the removal empties the file
ta 'grep -vxF -- "$L" "$F" > "$T"' ': > "$T"' \
   the_line_is_written_once_after_a_last_newline_and_taken_away_alone
