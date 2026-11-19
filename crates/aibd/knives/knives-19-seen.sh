#!/usr/bin/env bash
# N-workspace wave 3 knives SE1–SE5 (7b81e 11-18, ORDER-workspace §第三波 7b81e 3; SPEC §1.2 / §3): a seat on a
# node above sees every node under it; an event wakes the seats above only when named or for level ① / ②; one
# item (GET nodes/{id}) carries the rev node_set names and gives the person's words to the built-in page only.
# Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
su() { python3 -I scripts/knife.py crates/aibd/src/assign.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "assign::tests::$3"; }
se() { python3 -I scripts/knife.py crates/aibd/src/assign.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test nodes "$3"; }
sw() { python3 -I scripts/knife.py crates/aibd/src/workspace.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test nodes "$3"; }
# SE1 a seat above does not see the nodes under it
su '        match crate::workspace::select(tree, Some(top), None, false) {' \
   '        match crate::workspace::select(tree, Some(top), Some(0), false) { // knife' \
   a_seat_above_sees_the_whole_subtree_and_wakes_only_when_named_or_for_one_or_two
# SE2 a seat above wakes for every event (the copy floods it)
su '        if wakes(named.contains(&seat.as_str()), level) {' \
   '        if true || wakes(named.contains(&seat.as_str()), level) { // knife' \
   a_seat_above_sees_the_whole_subtree_and_wakes_only_when_named_or_for_one_or_two
# SE3 below=1 is not read
se '    let below = q.below == Some(1) && q.seat.is_some();' \
   '    let below = q.below == Some(9) && q.seat.is_some(); // knife' \
   the_page_adds_an_item_and_moves_it_only_through_the_entry_conditions
# SE4 an item does not show its rev
sw '            o.insert("rev".into(), json!(r.rev));' \
   '            o.insert("rev".into(), json!(0 * r.rev)); // knife' \
   the_page_adds_an_item_and_moves_it_only_through_the_entry_conditions
# SE5 one item gives the person's words to a user folder page
sw '    let words = gives_words(&c).await;' \
   '    let words = gives_words(&c).await || true; // knife' \
   the_page_adds_an_item_and_moves_it_only_through_the_entry_conditions
