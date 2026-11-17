#!/usr/bin/env bash
# andy-open knives (S107-andyopen, DECISION-council-gaps-519d0-1117 R4): each rule loosened must turn its own test red.
# Run by crates/aib-knife/run-knives.sh (macOS); from the repo root; -eo pipefail like GitHub's `shell: bash`.
#
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
ao() { python3 -I scripts/knife.py crates/aib-drive/src/andy_open.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --lib "andy_open::tests::$3"; }
# AO1 a done row is imported without its close
ao 'out.push(event(ctx, "close", at_ms, &id, own, None));' 'let _ = own; // knife' \
   the_replay_from_empty_equals_every_old_row
# AO2 a second open of an open item is taken
ao 'if r.get(&item).is_some_and(|i| !i.done) {' 'if false {' \
   open_update_close_fold_in_file_order_and_bad_steps_are_named
# AO3 close does not end the item
ao 'it.done = outcome == "close";' 'it.done = false; // knife' \
   the_replay_from_empty_equals_every_old_row
# AO4 the item fields are not kept
ao 'it.fields.insert(k.to_owned(), v.to_owned());' 'let _ = (k, v); // knife' \
   open_update_close_fold_in_file_order_and_bad_steps_are_named
# AO5 the card is not kept
ao 'it.card = c.to_owned();' 'let _ = c; // knife' \
   the_replay_from_empty_equals_every_old_row
# AO6 a repeated id in the old file is taken
ao 'if out.iter().any(|(_, o)| text_of(o, "id") == id) {' 'if false {' \
   old_rows_refuse_a_repeated_id_or_a_row_without_kind
# AO7 check ignores a changed field
ao 'if got[k] != json!(text_of(r, k)) {' 'if { let _ = &got; false } {' \
   check_names_a_row_that_changed_after_the_import
# AO8 check ignores done
ao 'if it.done != want_done {' 'if { let _ = want_done; false } {' \
   check_names_a_row_that_changed_after_the_import
# AO9 list shows closed items
ao '.filter(|i| f.contains_key("all") || !i.done)' '.filter(|_| true) /* knife */' \
   import_writes_once_then_list_and_check_agree_and_a_second_import_is_refused
# AO10 a second import is taken
ao 'if let Some(it) = rows.iter().find_map(|(_, r)| rep.get(&text_of(r, "id"))) {' 'if let Some(it) = None::<&Item> {' \
   import_writes_once_then_list_and_check_agree_and_a_second_import_is_refused
# AO11 --dry-run writes
ao 'if !f.contains_key("dry-run") {' 'if true {' \
   import_writes_once_then_list_and_check_agree_and_a_second_import_is_refused
# AO12 open of an open item is written
ao 'if sub == "open" && open_now {' 'if false {' \
   open_twice_update_closed_or_empty_are_refused
# AO13 an empty update is written
ao 'if sub == "update" && own.is_empty() && !f.contains_key("card") {' 'if false {' \
   open_twice_update_closed_or_empty_are_refused
# AO14 update / close of a closed item is applied in the replay
ao 'let Some(it) = r.get_mut(&item).filter(|i| !i.done) else {' 'let Some(it) = r.get_mut(&item) else {' \
   open_update_close_fold_in_file_order_and_bad_steps_are_named
# AO15 the old kind is written over the common kind (build drops it, so item_kind is lost)
ao 'own.insert("item_kind".into(), json!(text_of(r, "kind")));' 'own.insert("kind".into(), json!(text_of(r, "kind")));' \
   the_old_fields_never_overwrite_the_common_ones
