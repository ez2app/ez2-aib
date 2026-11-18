#!/usr/bin/env bash
# N-workspace wave 3 knives NW1–NW8 (7b81e 11-18, ORDER-workspace §第三波 7b81e 1; 5d33a PREREAD-assign L1):
# the page writes tree items only once the tree is in aibd; a user folder page never writes; a station moves only
# through aib-plan's entry conditions; a write from an old rev is refused; the evidence keeps every earlier
# segment; an assignment says when its node was not checked. Each rule, broken, turns its own test red. Run from
# the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
wu() { python3 -I scripts/knife.py crates/aibd/src/nodes.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "nodes::tests::$3"; }
we() { python3 -I scripts/knife.py crates/aibd/src/nodes.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test nodes "$3"; }
wi() { python3 -I scripts/knife.py crates/aibd/src/seat_input.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test nodes "$3"; }
ws() { python3 -I scripts/knife.py crates/aib-store/src/workspace.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-store --locked --lib "workspace::tests::$3"; }
wa() { python3 -I scripts/knife.py crates/aibd/src/assign.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test assign "$3"; }
# NW1 a user folder page writes the tree
wi '            let from_user_folder = held_as_user_page(&page).await;' \
   '            let from_user_folder = false && held_as_user_page(&page).await; // knife' \
   the_page_adds_an_item_and_moves_it_only_through_the_entry_conditions
# NW2 a station moves without aib-plan's entry conditions
wu '            let miss = card::entry_problems(pos, &fact, &node, f);' \
   '            let miss = card::entry_problems(0, &fact, &node, f); // knife' \
   a_station_moves_only_through_aib_plans_entry_conditions
# NW3 a write from an old rev goes over the newer one
ws '            if rev != if_rev {' \
   '            if false && rev != if_rev { // knife' \
   a_node_is_added_once_at_the_end_and_set_only_from_the_rev_it_was_read_at
# NW4 the page writes while the tree is still aib-plan's file
we '        if !crate::workspace::tree_is_in_aibd() {' \
   '        if false && !crate::workspace::tree_is_in_aibd() { // knife' \
   the_page_adds_an_item_and_moves_it_only_through_the_entry_conditions
# NW5 a write drops the earlier evidence
wu '        extra.get("evidence").and_then(Value::as_str),' \
   '        None, // knife' \
   a_station_moves_only_through_aib_plans_entry_conditions
# NW6 an id that is taken is added again
ws '            if taken > 0 {' \
   '            if taken > 1 { // knife' \
   a_node_is_added_once_at_the_end_and_set_only_from_the_rev_it_was_read_at
# NW7 (5d33a L1) an assignment with no tree says its node was checked
wa '            node_checked = tree.is_some();' \
   '            node_checked = true || tree.is_some(); // knife' \
   a_page_assigns_seats_to_nodes_and_a_node_sees_the_seats_above_it
# NW8 (wave-3 H1, M) a seat token's act reaches the tree writes
wi '            return crate::seat_send::act(st, key, body).await;' \
   '            return crate::nodes::act(st, Caller::Seat(key), body).await; // knife' \
   the_page_adds_an_item_and_moves_it_only_through_the_entry_conditions
