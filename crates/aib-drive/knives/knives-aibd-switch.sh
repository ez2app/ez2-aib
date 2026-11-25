#!/usr/bin/env bash
# aib-knives: os=unix
# D7a: aibd's dispatch switch over act.json (DECISION-council-driveweb r1 §2).
set -euo pipefail
as() { python3 -I scripts/knife.py crates/aib-drive/src/act.rs "$1" "$2" --expect-fail "act::tests::$3" \
        -- cargo test -p aib-drive --locked --lib "act::tests::$3"; }
# DO1 a round nobody started from aibd (or off) still dispatches
as '        _ => s.dispatch = false,' '        _ => {}' aibds_switch_decides_dispatch
# DO2 aibd's paused is not a pause
as '        Some("paused") => s.paused = true,' '        Some("paused") => {}' aibds_switch_decides_dispatch
