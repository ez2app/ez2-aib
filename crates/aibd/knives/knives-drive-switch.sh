#!/usr/bin/env bash
# D7a dispatch switch knives (DECISION-council-driveweb r1 §2, 5d33a PREREAD-d7d5): each rule
# loosened must turn its own test red. From the repo root; -eo pipefail like GitHub's shell.
set -euo pipefail
ds() { python3 -I scripts/knife.py crates/aibd/src/drive_switch.rs "$1" "$2" --expect-fail "drive_switch::tests::$3" \
        -- cargo test -p aibd --locked --lib "drive_switch::tests::$3"; }
# DSK1 a ticket is taken with off / paused
ds '    if ticket.is_some() && to != "on" {' '    if ticket.is_some() && to == "never" {' a_toggle_takes_three_states_and_a_reason
# DSK2 a reason with a control character is taken
ds '    (1..=WHY_MAX).contains(&n) && !why.chars().any(char::is_control)' '    (1..=WHY_MAX).contains(&n) && !why.is_empty()' a_toggle_takes_three_states_and_a_reason
# DSK3 the page names who moved the switch
ds '        .any(|k| !matches!(k.as_str(), "type" | "to" | "why" | "ticket"))' '        .any(|k| !matches!(k.as_str(), "type" | "to" | "why" | "ticket" | "by"))' a_toggle_takes_three_states_and_a_reason
# DSK4 a switch of another shape is read
ds '    let Some(v) = v.filter(|v| v["schema"] == SCHEMA) else {' '    let Some(v) = v.filter(|v| v["schema"] == SCHEMA || v.is_object()) else {' a_missing_or_bad_switch_is_off
# DSK5 an unknown state is read
ds '    if !STATES.contains(&to.as_str()) {' '    if !STATES.contains(&to.as_str()) && to.is_empty() {' a_missing_or_bad_switch_is_off
# DSK6 (5d33a Q1) an on with any signature counts as signed
ds '    crate::owner_ok::verify(&crate::owner_ok::binding(&s.ticket, &ask), &sig)' '    crate::owner_ok::verify(&crate::owner_ok::binding(&s.ticket, &ask), &sig) || !sig.is_empty()' an_on_without_a_checked_signature_is_unsigned
# DSK7 the page may turn on without the owner check
ds '        "can_on": owner_check && s.to != "on",' '        "can_on": (owner_check || s.at == 0) && s.to != "on",' an_on_without_a_checked_signature_is_unsigned
