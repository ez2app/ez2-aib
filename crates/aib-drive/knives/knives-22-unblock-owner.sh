#!/usr/bin/env bash
# aib-knives: os=unix
# aib-drive unblock owner UO1–UO6 (a9136 11-17, DECISION-council-nextstep-519d0-1117 §4-1; 5d33a found
# every unblock.json row with an empty owner, so act sent nothing): a row with no owner falls to the
# nearest live ancestor owner, then the live coordinator. Each rule, broken, turns its own test red. Run
# on macOS (the header default) by the shared crate-knives step. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
ko() { python3 -I scripts/knife.py crates/aib-drive/src/unblock_drive.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --lib "$3"; }
# UO1 the card's own owner (a system seat) is taken as its fallback
ko '        if hops > 0 {' '        if hops >= 0 { // knife' a_row_with_no_owner_falls_to_the_parent_then_the_coordinator
# UO2 a dead or system seat above the card is taken
ko '                && !matches!(str_of(x, "role").as_str(), "system")' '                && !str_of(x, "role").is_empty() // knife' a_row_with_no_owner_falls_to_the_parent_then_the_coordinator
# UO3 the fallback is any live seat, not the coordinator
ko '            .filter(|x| str_of(x, "role") == "coordinator")' '            .filter(|x| !str_of(x, "role").is_empty()) // knife' a_row_with_no_owner_falls_to_the_parent_then_the_coordinator
# UO4 the owner is found but not written
ko '            row["owner"] = json!(seat);' '            row["owner"] = json!(""); let _ = &seat; // knife' a_row_with_no_owner_falls_to_the_parent_then_the_coordinator
ku() { python3 -I scripts/knife.py crates/aib-drive/src/unblock.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --lib "$3"; }
# UO5 a u-3 (or any other source's) row with no owner is left empty
ku '                crate::unblock_drive::assign_owner(r, plan, seats);' '                let _ = (r, plan, seats); // knife' rows_of_every_source_with_no_owner_get_one
ko2() { python3 -I scripts/knife.py crates/aib-drive/src/unblock_drive.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --lib "$3"; }
# UO6 a 審 / 驗 / 疊 segment of the owner above is taken as the doer (5d33a L1)
ko2 '        .filter(|seg| !["審", "驗", "疊"].iter().any(|w| seg.contains(w)))' \
    '        .filter(|seg| !seg.is_empty()) // knife' \
    a_row_with_no_owner_falls_to_the_parent_then_the_coordinator
