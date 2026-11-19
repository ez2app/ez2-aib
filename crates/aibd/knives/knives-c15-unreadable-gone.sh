#!/usr/bin/env bash
# c15 (a9136 11-20): a stop held only by unreadable processes is taken up at the next start
# when every one it listed is gone (519d0 self test: three restarts, three outcomes). Each
# rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ug() { python3 -I scripts/knife.py crates/aibd/src/revive.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "revive::tests::$3"; }
# UG1 one unreadable process still there is let go
ug '        if !gone(pid) {' '        if false && !gone(pid) { // knife' \
   a_stop_held_only_by_unreadable_processes_now_gone_is_taken_up
# UG2 a cut-short list is trusted
ug '    if c["unreadable_more"].as_u64().unwrap_or(0) > 0 {' '    if false { // knife' \
   a_stop_held_only_by_unreadable_processes_now_gone_is_taken_up
# UG3 a survivor is ignored
ug '        .is_none_or(|a| !a.is_empty())' '        .is_none_or(|_| false) // knife' \
   a_stop_held_only_by_unreadable_processes_now_gone_is_taken_up
# UG4 an empty list reads as all gone
ug '    let listed = c["unreadable"].as_array().filter(|a| !a.is_empty())?;' \
   '    let listed = c["unreadable"].as_array()?; // knife' \
   a_stop_held_only_by_unreadable_processes_now_gone_is_taken_up
# UG5 pid 0 reads as gone
ug '        raw > 0' '        raw >= 0' only_a_pid_that_no_longer_exists_reads_as_gone
# UG6 any error (EPERM: it exists) reads as gone
ug '                == Err(nix::errno::Errno::ESRCH)' '                != Ok(()) // knife' \
   only_a_pid_that_no_longer_exists_reads_as_gone
