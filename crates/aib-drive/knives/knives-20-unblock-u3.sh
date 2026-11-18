#!/usr/bin/env bash
# u-3 unblock knives (ebb36 11-15, DECISION-council-unblock-519d0-1115 §2; #809): each rule loosened
# must turn its own test red. Moved from the inline aibd.yml step of feat/drive-u3b-ebb36-1115 word
# for word (RULES §21 / 11-16: new knives live in crates/*/knives/*.sh; run by crates/aib-knife/run-knives.sh).
# Labels renamed UB1-UB18 -> U3K1-U3K18 so they do not read as knives-12-unblock.sh's UB1-UB6.
# Run from the repo root; -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
#
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
ub() { python3 -I scripts/knife.py crates/aib-drive/src/unblock_u3.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --lib "unblock_u3::tests::$3"; }
# U3K1 one ssh timeout counts as a machine down
ub 'pub const U3_ROUNDS: usize = 2;' 'pub const U3_ROUNDS: usize = 1; // knife' \
   a_machine_offline_two_rounds_in_a_row_is_u3_one_round_is_not
# U3K2 an unread disk counts as full
ub 'is_some_and(|kb| kb < U3_DISK_MIN_KB)' 'unwrap_or(0) < U3_DISK_MIN_KB' \
   a_machine_short_of_disk_two_rounds_is_u3_and_unknown_disk_is_not
# U3K3 two rounds of swap / a long-open seat already listed
ub 'pub const U9_ROUNDS: usize = 3;' 'pub const U9_ROUNDS: usize = 2; // knife' \
   swap_or_a_long_open_seat_three_rounds_in_a_row_is_u9
# U3K4 a receipt still running counts as stuck
ub 'if !r.done && !r.running && r.age_secs >= U19_STUCK_SECS {' \
   'if !r.done && r.age_secs >= U19_STUCK_SECS { // knife' \
   a_receipt_with_no_result_is_u19_and_a_running_or_finished_one_is_not
# U3K5 a lease pid that could not be checked counts as dead
ub 'Some(l) if l.pid_alive == Some(false) => vec![' 'Some(l) if l.pid_alive != Some(true) => vec![ /* knife */' \
   a_lease_whose_pid_is_gone_is_u19_and_an_unchecked_pid_is_not
# U3K6 a red test of the previous knife covers the next one
ub '        section.clear();' '        // knife: section kept' \
   a_red_test_in_the_previous_knife_does_not_cover_the_next_one
# U3K7 a FALLBACK receipt (END rc= only) listed as no result
ub ' || l.starts_with("END rc=")' ' /* knife */' \
   a_fallback_receipt_with_end_rc_has_a_result
# U3K8 two records seconds apart count as two rounds
ub 'l.as_of.saturating_add(ROUND_MIN_GAP_SECS)' 'l.as_of.saturating_add(0)' \
   a_round_too_close_to_the_last_is_not_recorded
# U3K9 disk_free_gb read as kB
ub '.map(|g| g.saturating_mul(1024 * 1024))' '.map(|g| g) // knife' \
   hosts_json_reads_disk_free_gb_and_low_disk
# U3K10 kill -0 EPERM read as a dead pid
ub 'stderr.contains("not permitted")' 'stderr.contains("knife-never")' \
   kill0_eperm_is_unknown
# U3K11 a colon survives into the v-2 subject (the id would split)
ub '        if c.is_ascii_alphanumeric() {' "        if c.is_ascii_alphanumeric() || c == ':' { // knife" \
   v2_subject_is_a_colon_free_slug
# U3K12 the first-seen time is reset every round
ub '"since_ms": since_ms.unwrap_or(now_ms),' '"since_ms": since_ms.map_or(now_ms, |_| now_ms), // knife' \
   v2_source_keeps_first_seen_and_never_writes_blocks
# U3K13 JSON on stdout is dropped when the exit code is not 0
ub '    serde_json::from_slice::<Value>(stdout).ok()' '    serde_json::from_slice::<Value>(stdout).ok().filter(|_| _rc == Some(0)) // knife' \
   json_is_read_whatever_the_exit_code
# U3K14 a machine with no disk field is not named
ub 'h.get(*k).is_none()' 'h.get(*k).is_none() && false' \
   a_host_with_no_disk_field_is_named
# U3K15 what could not be read is dropped from the v-2 source (r1 unreadable)
ub '.map(|t| unreadable_entry(t)).collect();' '.map(|t| unreadable_entry(t)).take(0).collect(); // knife' \
   v2_source_keeps_first_seen_and_never_writes_blocks
# U3K16 the fingerprint follows the round's time, so a slid window looks new (1ced7 u-3b M1)
ub '.with_state("offline".into()),' '.with_state(format!("offline {}", newest.as_of)), // knife' \
   the_same_state_two_rounds_on_keeps_its_fingerprint
# U3K17 the disk grade is not in the fingerprint, so a lower grade is not news
ub 'disk under {} GB", grade + 5' 'disk under {} GB", grade * 0 + 5' \
   the_same_state_two_rounds_on_keeps_its_fingerprint
# U3K18 the remote receipts not read hide U3 / U9 too, so a machine back online is never "solved" (v-2 r3)
ub '        vec!["U19", "U20"]' '        vec!["U3", "U9", "U19", "U20"] // knife' \
   unreadable_covers_only_the_codes_it_hides
# U3K19 (a9136 11-17 owner) the cell-steps OWNER line is not read, so its receipts name no seat
ub '.strip_prefix("OWNER ")' '.strip_prefix("OWNER-knife ")' \
   a_receipt_names_its_owner_by_owner_queue_or_lease_line
# U3K20 the v-2 row drops the owner the rule found (act_unblock skips a row with no owner)
ub '"owner": b.owner,' '"owner": "", // knife' \
   rows_carry_the_seat_the_facts_name_and_machines_name_none
# U3K21 anything is taken for a seat code (v-2 refuses the whole row)
ub "(s.len() == 5 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')))" '(!s.is_empty()) // knife' \
   rows_carry_the_seat_the_facts_name_and_machines_name_none
# U3K22 a long-open seat's U9 row names no seat
ub '.with_owner(Some(seat.clone())),' '.with_owner(None), // knife' \
   rows_carry_the_seat_the_facts_name_and_machines_name_none
# U3K23 a hanging lease's U19 row names no seat
ub '.with_owner(Some(l.owner.clone()))' '.with_owner(None) /* knife */' \
   rows_carry_the_seat_the_facts_name_and_machines_name_none
