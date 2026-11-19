#!/usr/bin/env bash
# aib-knives: os=any
# B1dm-notify (043dd 11-18): the 1.x notification levels in Rust. Each knife takes one rule away
# and must turn its own 1.x-carried test red. Run from the repo root.
set -eu
nk() { python3 -I scripts/knife.py crates/aib-notify/src/lib.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-notify --locked --test policy "$3"; }
# NT1 an AI-to-AI message is not locked blue
nk '    if is_ai_message(r, &f) {' '    if false && is_ai_message(r, &f) { // knife (keeps is_ai_message used: -D warnings)' \
   ai_to_ai_is_blue_without_structured_escalation
# NT2 the mildest rule wins instead of the strictest
nk '    let detected = matches.iter().map(|x| x.level).min().unwrap_or(3);' \
   '    let detected = matches.iter().map(|x| x.level).max().unwrap_or(3); // knife' \
   four_level_fixtures_and_strictest_wins
# NT3 an AI escalation without a reason goes through
nk '            if !escalation_reason(r).is_some_and(nonempty) {' '            if false && !escalation_reason(r).is_some_and(nonempty) { // knife (keeps escalation_reason used)' \
   ai_escalation_requires_nonblank_reason
# NT4 a level switched off still acts
nk '    let enabled = state.enabled && state.levels[i];' '    let enabled = state.levels[i]; // knife' \
   all_32_switch_combinations
