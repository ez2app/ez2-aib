#!/usr/bin/env bash
# aib-knives: os=unix
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
# UG5 removed (5d33a cc1: did not bite): kill(0, None) signals the caller's own process group,
# which is never ESRCH, so pid 0 reads as not gone with or without the raw > 0 guard (the
# guard stays: it keeps 0 and negative pids from meaning a group at all)
# UG6 any error (EPERM: it exists) reads as gone
ug '                == Err(nix::errno::Errno::ESRCH)' '                != Ok(()) // knife' \
   only_a_pid_that_no_longer_exists_reads_as_gone
