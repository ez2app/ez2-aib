#!/usr/bin/env bash
# §17-3 import knives IM1–IM4 (a9136 11-18, DESIGN-thread-cat §3 / 5d33a CHECKLIST-doc-link D3 D5):
# aibd import writes each kept file once, from the seat it names, never as a person, skipping a
# secret kind. Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
im() { python3 -I scripts/knife.py crates/aibd/src/import.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "import::tests::$3"; }
imd() { python3 -I scripts/knife.py crates/aibd/src/docs.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "import::tests::$3"; }
# IM1 a run again writes every file again (no idem key)
im '        idem_key: Some(format!("{category}:{file}")),' \
   '        idem_key: None, // knife' \
   an_import_writes_each_file_once_from_its_seat
# IM2 a secret kind is imported (5d33a r2 11-19: the import path needs its own knife on the
# import test; the import's second copy of resolve's check is gone, so this breaks the one guard)
imd '    SECRET_MARKS.iter().any(|m| low.contains(m))' \
    '    SECRET_MARKS.iter().any(|m| low.contains(m)) && false // knife (import path)' \
    an_import_writes_each_file_once_from_its_seat
# IM3 the post is written as the person's
im '    let from = format!("seat:{}", seat_of(file).unwrap_or_else(|| "import".into()));' \
   '    let from = String::from("human:0000000000000000"); // knife' \
   an_import_writes_each_file_once_from_its_seat
# IM4 a date is read as a seat
im '                && p.bytes().any(|b| b.is_ascii_alphabetic())' \
   '                && true // knife' \
   a_files_seat_and_title_are_read_from_it
# IM5 an imported post is not its thread's (to_key): not listed, its document not read (5d33a 4ad11ad7 M1)
python3 -I scripts/knife.py crates/aibd/src/import.rs \
   '        to_key: thread_id.to_owned(),' \
   '        to_key: String::new(), // knife' \
   --expect-fail an_imported_summary_lists_under_its_kind_and_its_document_reads \
   -- cargo test -p aibd --locked --test import an_imported_summary_lists_under_its_kind_and_its_document_reads
# IM6 --undo takes back another owner's post in the thread too (519d0 §17-3 可整批撤回)
python3 -I scripts/knife.py crates/aib-store/src/threads.rs \
   '                "DELETE FROM messages WHERE thread_id = ?1 AND idem_owner = ?2' \
   '                "DELETE FROM messages WHERE thread_id = ?1 AND (idem_owner = ?2 OR 1)' \
   --expect-fail import::tests::an_import_is_taken_back_whole \
   -- cargo test -p aibd --locked --lib import::tests::an_import_is_taken_back_whole
# IM7 an emptied thread stays after --undo (its kind still counted on the page)
python3 -I scripts/knife.py crates/aib-store/src/threads.rs \
   '            if left == 0 {' \
   '            if left < 0 { // knife' \
   --expect-fail import::tests::an_import_is_taken_back_whole \
   -- cargo test -p aibd --locked --lib import::tests::an_import_is_taken_back_whole
