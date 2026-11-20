#!/usr/bin/env bash
# WD1-WD2 every operation an owner ask names as a constant is on owner_ok::WIRED (519d0 11-21;
# 0eed7 a82: repo.send_back was merged without its line, every send back was unavailable)
set -euo pipefail
wd() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail owner_ok::tests::every_operation_constant_is_wired \
        -- cargo test -p aibd --locked --lib owner_ok::tests::every_operation_constant_is_wired; }
# WD1 send back is not wired
wd crates/aibd/src/owner_ok.rs '    crate::send_back::OP_SEND_BACK,' '    // knife'
# WD2 an operation is listed twice
wd crates/aibd/src/owner_ok.rs '    crate::seat_trust::OP,' '    crate::seat_trust::OP, crate::seat_trust::OP, // knife'
