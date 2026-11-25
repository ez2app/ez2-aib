#!/usr/bin/env bash
# aib-knives: os=unix
# WD1-WD2 every operation an owner ask names as a constant is on owner_ok::WIRED (519d0 11-21;
# 0eed7 a82: repo.send_back was merged without its line, every send back was unavailable)
set -euo pipefail
wd() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail owner_ok::tests::every_operation_constant_is_wired \
        -- cargo test -p aibd --locked --lib owner_ok::tests::every_operation_constant_is_wired; }
# WD1 send back is not wired
wd crates/aibd/src/owner_ok.rs '    crate::send_back::OP_SEND_BACK, // repo.send_back (0eed7 a82)' '    // knife'
# WD2 an operation is listed twice
wd crates/aibd/src/owner_ok.rs '    crate::seat_trust::OP, // seat.trust' '    crate::seat_trust::OP, crate::seat_trust::OP, // knife'
# WN1-WN3 the scan reads every folder and refuses an operation it cannot read (5d33a N1 N2, REVIEW-wired aba125e2)
wn() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail owner_ok::tests::the_scan_reads_sub_folders_and_refuses_what_it_cannot_read \
        -- cargo test -p aibd --locked --lib owner_ok::tests::the_scan_reads_sub_folders_and_refuses_what_it_cannot_read; }
# WN1 only the top folder is read
wn crates/aibd/src/owner_ok.rs '                    dirs.push(path);' '                    drop(path); // knife'
# WN2 a written-out operation is passed over
wn crates/aibd/src/owner_ok.rs "                if name.starts_with('\"') || name.contains(\"::\") {" "                if name.is_empty() && name.contains(\"::\") { // knife"
# WN3 a path to another file's constant is passed over
wn crates/aibd/src/owner_ok.rs "                if name.starts_with('\"') || name.contains(\"::\") {" "                if name.starts_with('\"') { // knife"
# WW1-WW2 every wired operation has its words on top, and a merge's undo says it is an undo (stack10, 6770f: the top line said merge.brain)
ww() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "owner_ok::tests::$4" \
        -- cargo test -p aibd --locked --lib "owner_ok::tests::$4"; }
# WW1 merge.brain has no words (its row names merge.persona twice)
ww crates/aibd/src/owner_ok.rs '    (crate::merge_acts::OP_BRAIN, "合併相似的第二大腦"),' '    (crate::merge_acts::OP_PERSONA, "合併相似的第二大腦"), // knife' every_wired_operation_has_words
# WW2 an undo reads as a run
ww crates/aibd/src/owner_ok.rs '            if v.get("batch_id").is_some() {' '            if v.get("batch_id").is_some() && what.is_empty() { // knife' a_merge_and_its_undo_say_which_on_top
