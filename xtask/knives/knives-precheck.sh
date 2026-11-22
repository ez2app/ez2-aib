#!/usr/bin/env bash
# aib-xtask knife-precheck knives (precheck gate 1: the knives a change touches must compile before the
# knife cells spend their hours). Run by the "aib-xtask knives (macOS)" step in aibd.yml (one line runs
# xtask/knives/*.sh). Run from the repo root.
#
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# FILE is written out in each function (not a variable): aib-xtask knife-anchors reads it
kp() { python3 -I scripts/knife.py xtask/src/knife_precheck.rs "$1" "$2" --expect-fail "$3" \
       -- cargo test -p aib-xtask --locked "$3"; }
kc() { python3 -I scripts/knife.py xtask/src/knife_compile.rs "$1" "$2" --expect-fail "$3" \
       -- cargo test -p aib-xtask --locked "$3"; }
# KP1 a knife whose target file changed (its script did not) is left out
kp '    changed.is_none_or(|c| c.contains(written_in(&k.at)) || c.contains(&k.file))' \
   '    changed.is_none_or(|c| c.contains(written_in(&k.at)))' \
   a_knife_is_in_scope_when_its_script_or_the_file_it_cuts_changed
# KP2 a range that is not known checks no knife (it must check every one)
kp '    changed.is_none_or(|c| c.contains(written_in(&k.at)) || c.contains(&k.file))' \
   '    changed.is_some_and(|c| c.contains(written_in(&k.at)) || c.contains(&k.file))' \
   a_knife_is_in_scope_when_its_script_or_the_file_it_cuts_changed
# KP3 a knife that cannot be read is dropped without a word
kp '            Err(why) => p.not_checked.push((k.at.clone(), why)),' \
   '            Err(_) => {} // knife: not said' \
   a_knife_that_cannot_be_read_is_said_and_not_checked_as_if_it_compiled
# KP4 an error of the wide check is an error at once: a knife its own command builds is blocked
kc $'                verdict.insert(i, Verdict::Ok);\n                cleared.push((i, wide));' '                cleared.push((i, wide)); // knife: stays an error' \
   only_an_error_both_ways_is_an_error_and_not_known_is_never_ok
# KP5 an error nobody could ask again about is let through as ok
kc '                verdict.insert(i, Verdict::Unsure(format!("{wide}; own command not known")));' \
   '                verdict.insert(i, Verdict::Ok); let _ = &wide; // knife' \
   only_an_error_both_ways_is_an_error_and_not_known_is_never_ok
