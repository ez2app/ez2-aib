#!/usr/bin/env bash
# aib-knives: os=unix
# The "unblock_notify" knives of #803 (S79-u5, 043dd 11-15), moved out of aibd.yml when #803 took
# main 1f9a749c (RULES §21; main had moved every aib-drive knife here, f7528547 / 3b307d36).
# IDs are UN (not UB) so they do not collide with knives-12-unblock.sh, which is a9136's unblock.rs.
# aibd.yml runs every crates/aib-drive/knives/*.sh in name order in one step; aib-xtask knife-anchors
# reads crates/*/knives/*.sh. Run from the repo root.
#
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
ak() { python3 -I scripts/knife.py crates/aib-drive/src/act.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked "$3"; }
# S79-unblock u-5 (043dd 11-15): 「卡點:」 dms from unblock.json, and U18 (a switch on that this version never sends)
uk() { python3 -I scripts/knife.py crates/aib-drive/src/act_unblock.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --lib "$3"; }
# UN1 a stale unblock.json is acted on
uk '        Some(a) if (0..=FRESH_MS).contains(&a) => {}' '        Some(a) if a >= 0 => {} // knife' \
   a_stale_or_undated_file_holds_back_every_row
# UN2 the same evidence is told again
uk '        if told {' '        if told && false { // knife' \
   the_same_evidence_is_told_once_and_new_evidence_again
# UN3 new evidence (a new fingerprint) is not told
uk '        r.code, r.subject, r.fingerprint, r.owner' '        r.code, r.subject, "-", r.owner // knife' \
   the_same_evidence_is_told_once_and_new_evidence_again
# UN4 a seat on deck in the merge queue is told
uk '        if seat.capacity.on_deck == Some(true) {' '        if seat.capacity.on_deck == Some(true) && false { // knife' \
   the_seat_rules_of_act_apply
# UN5 a key part with : or @ is let through (the key would not split)
uk $'    !s.trim().is_empty() && !s.contains([\':\', \'@\', \'\\n\', \'\\r\'])' '    !s.trim().is_empty() // knife' \
   a_row_whose_key_would_not_split_is_refused
# UN6 a dry row counts toward the 30-minute seat gap
uk '            throttled: live,' '            throttled: true, // knife' \
   a_dry_row_never_counts_toward_the_seat_gap_nor_rides_in_a_work_dm
# UN7 a dry row rides along in a work dm (and is sent with it)
uk '                && (w.action == ACTION || (live && crate::act::WORK.contains(&w.action.as_str())))' '                && (w.action == ACTION || crate::act::WORK.contains(&w.action.as_str())) // knife' \
   a_dry_row_never_counts_toward_the_seat_gap_nor_rides_in_a_work_dm
# UN10 the row that holds up more does not go first
uk '    rows.sort_by_key(|r| std::cmp::Reverse(r.blocks));' '    rows.sort_by_key(|r| r.blocks); // knife' \
   the_row_that_holds_up_more_goes_first_and_candidates_are_only_listed
# UN11 the candidates for an escalation are not shown
uk '        if let Some(n) = candidates_line(&r.candidates) {' '        if let Some(n) = candidates_line(&r.candidates).filter(|_| false) { // knife' \
   the_row_that_holds_up_more_goes_first_and_candidates_are_only_listed
# UN12 (IFACE v2) a stale row inside a fresh file is told
uk '            Some(age) if (0..=FRESH_MS).contains(&age) => {}' '            Some(age) if age >= 0 => {} // knife' \
   a_v2_row_needs_its_own_fresh_time_an_owner_and_a_matching_id
# UN13 (IFACE v2) a row whose id is not <code>:<subject> is told
uk '        if !r.id.is_empty() && r.id != id {' '        if r.id == "never-a-knife" { // knife' \
   a_v2_row_needs_its_own_fresh_time_an_owner_and_a_matching_id
# UN14 (IFACE v2) a row with no owner is not said to have none
uk '        if r.owner.is_empty() {' '        if r.owner.is_empty() && false { // knife' \
   a_v2_row_needs_its_own_fresh_time_an_owner_and_a_matching_id
ak2() { python3 -I scripts/knife.py crates/aib-drive/src/act.rs "$1" "$2" --expect-fail "$3" \
          -- cargo test -p aib-drive --locked --lib "$3"; }
# U18a a switch whose actions this version never sends is taken as sending
ak2 '        name == "paused" || Self::actions(name).iter().any(|a| SENDABLE.contains(a))' '        name == "paused" || !Self::actions(name).is_empty() // knife' \
    u18_a_switch_this_version_never_sends_for_is_named
# U18b switches that are off are listed as on but dry
ak2 '            .filter(|n| self.get(n) && !Self::sends(n))' '            .filter(|n| !Self::sends(n)) // knife' \
    u18_a_switch_this_version_never_sends_for_is_named
# UN8 a pause does not hold back 「卡點:」 dms
ak2 '        "dispatch" | "open_review" | "notify_fix" | "unblock_notify"' '        "dispatch" | "open_review" | "notify_fix" // knife' \
    a_pause_holds_back_unblock_dms
# UN9 a 「卡點:」 dm to a seat that is not there is retried (a new U8 every round)
ak2 '            || s.base_key.starts_with(crate::act_unblock::ACTION))' '            || s.base_key.starts_with("never"))  // knife' \
    an_unblock_dm_to_a_seat_that_is_not_there_is_final_not_retried
# U18c turning on a switch this version never sends for says nothing
ak '    if on && !sends {' '    if on && !sends && false { // knife' \
   turning_on_a_switch_this_version_never_sends_for_says_so_and_records_it
