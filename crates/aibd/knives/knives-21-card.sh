#!/usr/bin/env bash
# N-workspace wave 4 knives WC1–WC4 (7b81e 11-18, ORDER-workspace §第四波 7b81e; SPEC §15): GET nodes/{id}
# gives the card window's fields — records newest first, the items right under only, why / record words
# to the built-in page only, and the handler really adds them. Each rule, broken, turns its own test red.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
cu() { python3 -I scripts/knife.py crates/aibd/src/workspace.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "workspace::tests::$3"; }
ce() { python3 -I scripts/knife.py crates/aibd/src/workspace.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test nodes "$3"; }
# WC1 records oldest first
cu '    records.reverse();' \
   '    records.sort_by_key(|r| r.0); // knife' \
   the_card_has_every_field_records_newest_first_and_the_items_right_under
# WC2 the items under the items are listed too
cu '        .filter(|k| str_of(k, "parent") == Some(id) && str_of(k, "id") != Some(id))' \
   '        .filter(|k| str_of(k, "id") != Some(id) && k.get("parent").is_some_and(|p| !p.is_null())) // knife' \
   the_card_has_every_field_records_newest_first_and_the_items_right_under
# WC3 why and record words go to a user folder page
cu '    if words {' \
   '    if words || true { // knife' \
   a_user_folder_page_gets_no_words_of_why_or_records
# WC4 the handler leaves the card fields out
ce '                    if let (Value::Object(m), Some(more)) = (&mut n, card(&t, &id, words)) {' \
   '                    if let (Value::Object(m), Some(more)) = (&mut n, None::<Map<String, Value>>) { // knife' \
   the_page_adds_an_item_and_moves_it_only_through_the_entry_conditions
