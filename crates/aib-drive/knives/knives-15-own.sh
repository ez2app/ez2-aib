#!/usr/bin/env bash
# aib-drive own knives (S79-own o-1 own.rs list, o-2 own_ledger.rs done / progress / none; 56cda,
# DECISION-council-s79-own-519d0-1116 r1). Moved out of knives-08-next-local.sh so own's knives sit in one
# place (a9136 / 7b81e 11-17 00:19; o-4 can add here). aibd.yml runs every crates/aib-drive/knives/*.sh in
# name order in one step; aib-xtask knife-anchors reads crates/*/knives/*.sh. Run from the repo root.
#
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# S79-own o-1 (56cda, DECISION-council-s79-own-519d0-1116 r1): aib-drive own's list
ow() { python3 -I scripts/knife.py crates/aib-drive/src/own.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --lib "own::tests::$3"; }
# OW6a a node another seat claimed is listed
ow '                if inp.claimed.contains(id) {' \
   '                if inp.claimed.contains(id) && false { // knife' \
   a_dev_seat_gets_its_own_nodes_ready_to_push_most_unblocking_first
# OW6b a node waiting on Andy (gate) is listed
ow '                if !n.get("gate").is_none_or(Value::is_null) {' \
   '                if false { // knife' \
   a_dev_seat_gets_its_own_nodes_ready_to_push_most_unblocking_first
# OW6c a node whose after node is not done is listed
ow '                            Some(st) if *st == DONE_STATUS => {}' \
   '                            Some(st) if *st == DONE_STATUS || !st.is_empty() => {} // knife' \
   a_dev_seat_gets_its_own_nodes_ready_to_push_most_unblocking_first
# OW6d a dispatch dm marked waiting is listed
ow '                if !wait.is_empty() {' \
   '                if false && !wait.is_empty() { // knife' \
   plan_lines_of_its_own_and_open_dispatches_are_listed_waiting_ones_are_not
# OW6e another seat's plan line is listed as this seat's
ow '                if !l.starts_with(&own) {' \
   '                if !l.contains("自己") { // knife' \
   plan_lines_of_its_own_and_open_dispatches_are_listed_waiting_ones_are_not
# OW6f a review duty counts for a dev seat
ow '        "review" => role == "review",' \
   '        "review" => true, // knife' \
   a_dev_seat_gets_its_own_nodes_ready_to_push_most_unblocking_first
# OW6g a plan line's id is its position, not its text (moves when a line is added above)
ow '                    id: format!("plan:{}", &crate::next::sha256_hex(l)[..8]),' \
   '                    id: format!("plan:{}", txt.lines().position(|x| x.trim() == l).unwrap_or(0)), // knife' \
   plan_lines_of_its_own_and_open_dispatches_are_listed_waiting_ones_are_not
# S79-own o-2 (own done / progress / none, own_ledger.rs)
ol() { python3 -I scripts/knife.py crates/aib-drive/src/own_ledger.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --lib "own_ledger::tests::$3"; }
# OW10 a node change counts as done
ol '        "node" if kind != "progress" => {' '        "node" if false && kind != "progress" => { // knife' \
   a_node_is_progress_only_and_the_same_evidence_counts_once
# OW8 the same evidence counts twice
ol '    if used.contains(&ev.key()) {' '    if false && used.contains(&ev.key()) { // knife' \
   a_node_is_progress_only_and_the_same_evidence_counts_once
# OW11 a line with no turn_start is written as a real done (the gate would count it)
ol '        "kind": if turn.is_some() { req.kind } else { "manual" },' '        "kind": req.kind, // knife' \
   no_turn_start_makes_the_line_manual
# OW12 an id not listable now is written
ol '    let Some(item): Option<&Item> = list.all.iter().find(|i| i.id == id) else {' '    let Some(item): Option<&Item> = list.all.iter().find(|i| i.id == id).or(list.all.first()) else { // knife' \
   a_done_on_a_listed_item_with_evidence_is_written_under_the_last_turn
# OW13 a receipt with a red RC counts
ol '        if rc != "0" {' '        if false && rc != "0" { // knife' \
   evidence_shapes_are_checked
# OW14 none without a why is written
ol '        if why.is_empty() {' '        if false && why.is_empty() { // knife' \
   none_needs_a_why_and_keeps_the_list_snapshot
# OW15 a green receipt-run receipt (knives write RC knife_X=101) is taken as red (5d33a 2cc602ad M1)
ol '            if name.starts_with("knife_") {' '            if false && name.starts_with("knife_") { // knife' \
   evidence_shapes_are_checked
# OW16 a dm waiting as {"text","at"} is listed as doable (5d33a 2cc602ad M2)
ow '                    .and_then(|w| w.as_str().or_else(|| w.get("text").and_then(Value::as_str)))' \
   '                    .and_then(|w| w.as_str()) // knife' \
   plan_lines_of_its_own_and_open_dispatches_are_listed_waiting_ones_are_not
# OW17 a seat inside a longer hash counts as the owner (5d33a 2cc602ad N1)
ow '        if !word {' '        if !word && !p.contains(seat) { // knife' \
   a_part_starting_review_or_install_is_a_side_duty
# OW18 a cell-steps receipt with a knife that missed is green (a9136 IFACE r1 補定義)
ol '        if t.starts_with("KNIFE ") && (t.contains(" MISSES") || t.contains(" INVALID")) {' \
   '        if false && t.starts_with("KNIFE ") { // knife' \
   evidence_shapes_are_checked
