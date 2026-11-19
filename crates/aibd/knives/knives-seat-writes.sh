#!/usr/bin/env bash
# SW-N1–N4 (7b81e 11-20; ORDER-webonly-today 6「席能寫進度樹與狀態列」): a seat notes the progress
# tree (nodes/plan, op note) and sets its own line (act seat_now) through aibd's MCP server, with its
# own token and no seat named in the body. Each, broken, turns its own test red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cw() { python3 -I scripts/knife.py crates/aibd/src/ctl.rs "$1" "$2" \
  --expect-fail a_seat_notes_the_tree_and_sets_its_line_as_itself \
  -- cargo test -p aibd --locked --bin aibd a_seat_notes_the_tree_and_sets_its_line_as_itself; }
# N1 plan_note is not a tool the server answers
cw '                Some("plan_note") => plan_note(c, args),' \
   '                Some("plan_note_off") => plan_note(c, args), // knife'
# N2 the note goes somewhere other than the tree route
cw '        "/api/v1/nodes/plan",' \
   '        "/api/v1/act", // knife'
# N3 the status line drops what the seat is doing
cw '    for k in ["item", "done", "total", "state", "track", "why", "idea"] {' \
   '    for k in ["done", "total", "state", "track", "why", "idea"] { // knife'
# N4 the seat's own words may name another seat
cw '    for k in ["item", "done", "total", "state", "track", "why", "idea"] {' \
   '    for k in ["item", "done", "total", "state", "track", "why", "idea", "seat"] { // knife'
# SW-M1–M2 (DECISION-seat-writes-7b81e-1120 Q2): only a dgrs or work seat writes the tree
pm() { python3 -I scripts/knife.py crates/aibd/src/plan_route.rs "$1" "$2" \
  --expect-fail only_a_seat_opened_to_change_things_writes_the_tree \
  -- cargo test -p aibd --locked --lib plan_route::tests::only_a_seat_opened_to_change_things_writes_the_tree; }
ph() { python3 -I scripts/knife.py crates/aibd/src/plan_route.rs "$1" "$2" \
  --expect-fail a_safe_seat_cannot_note_the_tree_and_a_dgrs_seat_gets_past_the_check \
  -- cargo test -p aibd --locked --test plan_seat_mode a_safe_seat_cannot_note_the_tree_and_a_dgrs_seat_gets_past_the_check; }
# M1 a safe seat may write
pm '    matches!(mode, Some("dgrs") | Some("work"))' \
   '    matches!(mode, Some("dgrs") | Some("work") | Some("safe")) // knife'
# M2 the handler does not ask
ph '        if !seat_may_write(mode.as_deref()) {' \
   '        if !seat_may_write(mode.as_deref()) && false { // knife'
# SW-L1–L3 (5d33a PREREAD-seatwrites N1): a seat's fact is short and it writes at most 60 an hour
pl() { python3 -I scripts/knife.py crates/aibd/src/plan_route.rs "$1" "$2" \
  --expect-fail a_seat_writes_short_facts_and_not_too_often \
  -- cargo test -p aibd --locked --lib plan_route::tests::a_seat_writes_short_facts_and_not_too_often; }
# L1 any length is taken
pl '        .is_none_or(|f| f.chars().count() <= SEAT_FACT_MAX)' \
   '        .is_none_or(|_| true) // knife'
# L2 no hourly limit
pl '    if recent.len() >= SEAT_WRITES_PER_HOUR {' \
   '    if false { // knife'
# L3 an hour old write is still counted
pl '        .is_some_and(|t| now.saturating_duration_since(*t) >= HOUR)' \
   '        .is_some_and(|t| now.saturating_duration_since(*t) > HOUR) // knife'
