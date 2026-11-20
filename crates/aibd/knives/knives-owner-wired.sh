#!/usr/bin/env bash
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
