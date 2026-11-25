#!/usr/bin/env bash
# aib-knives: os=unix
# N-workspace wave 2 knives AS1–AS10 (7b81e 11-18, ORDER-workspace §第二波; 5d33a CHECKLIST-workspace-wave2 A1 / A2):
# a user folder page and a seat token never assign; six roles only; the seat and (with a tree) the node must
# exist; every new row leaves its event; a node inherits from every node above it and a loop ends; a seat is
# woken only when named or for level ① / ②. Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
au() { python3 -I scripts/knife.py crates/aibd/src/assign.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "assign::tests::$3"; }
ae() { python3 -I scripts/knife.py crates/aibd/src/assign.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test assign "$3"; }
ai() { python3 -I scripts/knife.py crates/aibd/src/seat_input.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test assign "$3"; }
# AS1 (A2, M) a user folder page assigns
ai '            if held_as_user_page(&page).await {' \
   '            if held_as_user_page(&page).await && false { // knife' \
   a_page_assigns_seats_to_nodes_and_a_node_sees_the_seats_above_it
# AS2 (A1, M) a seat token's act reaches the assignments
ai '            return crate::seat_send::act(st, key, body).await;' \
   '            return crate::assign::act(st, Caller::Seat(key), body).await; // knife' \
   a_page_assigns_seats_to_nodes_and_a_node_sees_the_seats_above_it
# AS3 a role outside the six is taken
au '    if !ROLES.contains(&role) && !known(role) {' \
   '    if false && !ROLES.contains(&role) && !known(role) { // knife' \
   an_assignment_names_a_node_a_seat_and_one_of_six_roles
# AS3b a role the registry does not list is taken
au '    if !ROLES.contains(&role) && !known(role) {' \
   '    if !ROLES.contains(&role) && (!known(role) && false) { // knife' \
   a_role_the_person_added_is_taken_when_the_registry_lists_it
# AS4 a seat aibd does not know is assigned
ae '            if st.store.seat(&seat).map_err(|_| Fail::Internal)?.is_none() {' \
   '            if false && st.store.seat(&seat).map_err(|_| Fail::Internal)?.is_none() { // knife' \
   a_page_assigns_seats_to_nodes_and_a_node_sees_the_seats_above_it
# AS5 a node the tree does not have is assigned
ae '            if tree.is_some_and(|t| !has_node(&t, &node)) {' \
   '            if false && tree.is_some_and(|t| !has_node(&t, &node)) { // knife' \
   a_page_assigns_seats_to_nodes_and_a_node_sees_the_seats_above_it
# AS6 a new row leaves no event
ae '        let journaled = !changed || {' \
   '        let journaled = true || !changed || { // knife' \
   a_page_assigns_seats_to_nodes_and_a_node_sees_the_seats_above_it
# AS7 a node does not see the seats above it
ae '        for up in &ups {' \
   '        for up in ups.iter().take(0) { // knife' \
   a_page_assigns_seats_to_nodes_and_a_node_sees_the_seats_above_it
# AS8 a loop above a node is walked up to the limit
au '        if p == id || out.iter().any(|o| o == p) || out.len() >= ANCESTORS_MAX {' \
   '        if p == id || out.len() >= ANCESTORS_MAX { // knife' \
   a_node_inherits_from_every_parent_up_and_a_loop_ends
# AS9 a seat is woken for level ③
au '    named || matches!(level, Some(1 | 2))' \
   '    named || matches!(level, Some(1 | 2 | 3)) // knife' \
   a_seat_wakes_only_when_named_or_for_a_decision_or_a_break
# AS10 a seat is woken for anything when unnamed
au '    named || matches!(level, Some(1 | 2))' \
   '    named || level.is_none() || matches!(level, Some(1 | 2)) // knife' \
   a_seat_wakes_only_when_named_or_for_a_decision_or_a_break
