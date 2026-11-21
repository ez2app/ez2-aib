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
# DSK8 an on without a signature aibd checks is handed to aib-drive as on
ds '        "on" if signed(&s) => "on",' '        "on" => "on",' drive_gets_an_unsigned_on_as_off
dv() { python3 -I scripts/knife.py crates/aibd/src/drive_view.rs "$1" "$2" --expect-fail "drive_view::tests::$3" \
        -- cargo test -p aibd --locked --lib "drive_view::tests::$3"; }
# DV1 (D7b) a nudge is shown as a work order sent
dv '                    if i.get("action").and_then(Value::as_str) == Some("dispatch") {' \
   '                    if i.get("action").and_then(Value::as_str).is_some() {' \
   the_view_shows_next_the_work_orders_sent_and_what_holds_the_drive
# DV2 the oldest work order first
dv '    out.reverse();' '    out.truncate(usize::MAX);' \
   the_view_shows_next_the_work_orders_sent_and_what_holds_the_drive
# DV3 every next row and every field in it (a file any AI writes, shown whole)
dv '            .map(|a| a.iter().take(ROWS_MAX).map(|r| pick(r, keys)).collect())' \
   '            .map(|a| a.iter().take(usize::MAX).map(|r| if keys.is_empty() { pick(r, keys) } else { r.clone() }).collect())' \
   the_view_shows_next_the_work_orders_sent_and_what_holds_the_drive
