#!/usr/bin/env bash
# aib-knives: os=unix
# aib-plan fail FL1–FL4 (a9136 11-18, DECISION-council-gaps-519d0-1117 R2): the author cannot record its
# own failed check, the fixer defaults to the author, a pass clears the flag, and the event carries the
# key the drive dedupes on. Each rule, broken, turns its own test red. Run on macOS (the header default)
# by the shared crate-knives step. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
fl() { python3 -I scripts/knife.py crates/aib-plan/src/card.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-plan --locked --lib "$3"; }
# FL1 the author records a failed check on its own card
fl '                    return Err(format!("verifier: {seat} 是作者,不能替自己記驗不通過"));' \
   '                    let _ = seat; // knife' \
   a_failed_check_marks_the_card_for_its_fixer_and_a_pass_clears_it
# FL2 with no --fixer the card has no one to fix it
fl '            let Some(fixer) = fixer.clone().or_else(|| author(n)) else {' \
   '            let Some(fixer) = fixer.clone() else { // knife' \
   a_failed_check_marks_the_card_for_its_fixer_and_a_pass_clears_it
# FL3 a pass leaves the failed flag on the card
fl '                if pos == 6 && has_key(raw, "verify_failed") {' \
   '                if false && pos == 6 && has_key(raw, "verify_failed") { // knife' \
   a_failed_check_marks_the_card_for_its_fixer_and_a_pass_clears_it
# FL4 the event drops the issue number the drive dedupes on
fl '            m.insert("issue".into(), issue.clone().into());' \
   '            let _ = issue; // knife' \
   a_failed_check_logs_the_key_the_drive_dedupes_on
# FL5 (5d33a REVIEW-verify-fix N1) S (the whole card is not done, split it) is refused, so verify_fix never gets one
fl '            if !["H", "M", "L", "N", "S"].contains(&level.as_str()) {' \
   '            if !["H", "M", "L", "N"].contains(&level.as_str()) { // knife' \
   a_failed_check_marks_the_card_for_its_fixer_and_a_pass_clears_it
# FL6 (0eed7 REVIEW-plan-fail L1) a --fixer the card does not name never sees the card in mine
fl '                None if fixes_it => "修",' \
   '                None if false && fixes_it => "修", // knife' \
   a_failed_check_marks_the_card_for_its_fixer_and_a_pass_clears_it
