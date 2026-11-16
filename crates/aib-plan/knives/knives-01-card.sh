#!/usr/bin/env bash
# aib-plan card knives CD1–CD12 (a9136 11-17, DECISION-council-board-coop-519d0-1117 §2.1 mine / set / note / add):
# each rule broken on purpose must turn its own test red. Run by the "aib-plan knives (macOS)" step in
# aibd.yml (one line runs crates/aib-plan/knives/*.sh); new knives live here, not inline (RULES §21).
# Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
kd() { python3 -I scripts/knife.py crates/aib-plan/src/card.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-plan --locked --lib "$3"; }
# CD1 a review written by the card's own author lets it into 審過未推
kd '        } else if reviewer == author {' '        } else if false && reviewer == author { // knife' set_refuses_a_station_whose_entry_is_missing_and_says_what
# CD2 a receipt for another head counts
kd '            if h.trim() != head {' '            if false && h.trim() != head { // knife' set_refuses_a_station_whose_entry_is_missing_and_says_what
# CD3 a red RC line in the receipt counts as green
kd '            if !r.trim_end().ends_with("=0") {' '            if false && !r.trim_end().ends_with("=0") { // knife' set_refuses_a_station_whose_entry_is_missing_and_says_what
# CD4 the knife lines (want non-0) are read as red: a green receipt is refused
kd '            if r.starts_with("knife_") {' '            if false && r.starts_with("knife_") { // knife' set_refuses_a_station_whose_entry_is_missing_and_says_what
# CD5 "can't tell whether the head is in origin/main" lets the card into 未驗
kd '                    None => unknown = true,' '                    None => return Vec::new(), // knife' set_refuses_a_station_whose_entry_is_missing_and_says_what
# CD6 the author verifies their own card
kd '                Some(v) if Some(v) == a.as_deref() => out.push("驗的人是作者".into()),' \
   '                Some(v) if false && Some(v) == a.as_deref() => out.push("驗的人是作者".into()), // knife' set_refuses_a_station_whose_entry_is_missing_and_says_what
# CD7 staying in the same station re-runs the entry check
kd '                if n.status.as_deref() != Some(k) {' '                if n.status.as_deref() != Some(k) || !k.is_empty() { // knife' set_refuses_a_station_whose_entry_is_missing_and_says_what
# CD8 the record is overwritten instead of getting one more segment at its tail
kd '        Some(o) => format!("{o} | {seg}"),' '        Some(o) => format!("{seg}{}", &o[..0]), // knife' \
   note_appends_a_stamped_segment_and_touches_only_that_node
# CD9 a change that adds a check error is written anyway
kd '    if fresh.is_empty() {' '    if fresh.is_empty() || !fresh.is_empty() { // knife' \
   verify_refuses_a_change_that_adds_a_check_error
# CD10 a write that changes the node count is not refused
kd '    if b.nodes.len() != want {' '    if false && b.nodes.len() != want { // knife' \
   add_opens_a_card_under_a_parent_with_a_free_id
# CD11 a held lock is not reported as locked (rc 4)
kd '            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {' \
   '            Err(e) if false && e.kind() == std::io::ErrorKind::AlreadyExists => { // knife' \
   write_takes_the_lock_writes_whole_and_logs_one_event
# CD12 a card waiting on another node is listed as pushable
kd '            let pushable = waiting.is_none() && role != "協作";' '            let pushable = role != "協作"; // knife' \
   mine_puts_pushable_and_right_first_and_says_stop_when_none
echo "aib-plan card knives: all bit"
