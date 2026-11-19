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
# 匯入其餘四類 (import_more.rs, Andy 11-19 01:1x)
im() { python3 -I scripts/knife.py crates/aibd/src/import_more.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "import_more::tests::$3"; }
# IM8 a deleted mail is imported
im '            if field("status") == "deleted" || field("id").is_empty() {' \
   '            if field("id").is_empty() { // knife' \
   old_mail_goes_into_its_pairs_thread_once
# IM9 a word containing a node id names that card
im '        .find(|w| !w.is_empty() && ids.contains(*w))' \
   '        .find(|w| !w.is_empty() && ids.iter().any(|i| w.contains(i.as_str()))) // knife' \
   a_card_is_the_first_node_id_named
# IM10 a drive mail sent twice is posted twice
im '            if id.is_empty() || !seen.insert(id.to_owned()) {' \
   '            if id.is_empty() || (!seen.insert(id.to_owned()) && false) { // knife' \
   drive_mail_is_read_from_the_ledger_with_its_card
# IM11 an unknown / claimed sender is posted as the seat it named (7ebca CHECK-importdm M1)
im '            let sender = (field("sender_kind") == "seat")' \
   '            let sender = (field("sender_kind") == "seat" || true) // knife' \
   old_mail_goes_into_its_pairs_thread_once
# IM12 direction / escape characters reach the page (N2)
im "        if c == '\\n' || c == '\\t' || !aib_approve::text::is_bad_char(c) {" \
   "        if true { // knife" \
   old_mail_goes_into_its_pairs_thread_once
# IM13 a gone relay file is not marked (N1)
im '        .unwrap_or_else(|| format!("{RELAY_GONE}{msg}"))' \
   '        .unwrap_or_else(|| msg.to_owned()) // knife' \
   old_mail_goes_into_its_pairs_thread_once
# IM14 an unproven sender's label keeps its line breaks (5d33a CHECK-dm-import-key L1)
im "        .map(|c| if c == '\\n' || c == '\\t' { ' ' } else { c })" \
   "        .map(|c| c) // knife" \
   old_mail_goes_into_its_pairs_thread_once
# IM16 a person's mail is not put in the seat ↔ hub thread (7ebca 02:10)
im '            let person = field("sender_kind") == "human" && !field("human_id").is_empty();' \
   '            let person = false; // knife' \
   old_mail_goes_into_its_pairs_thread_once
# IM17 mail to the person's hub code is not put in the hub thread (7ebca 9583ec32)
im '        let to = if is_hub(&to) { "hub".to_owned() } else { to };' \
   '        let to = to; // knife' \
   old_mail_goes_into_its_pairs_thread_once
# IM18 drive mail goes through the page document kind list again (7ebca 05:09: 0 letters)
im '                crate::docs::resolve_in(&r.state.join("dm-queue").join(to), &format!("{id}.json"))' \
   '                crate::docs::resolve(&r.state.join("dm-queue").join(to), &format!("{id}.json"))' \
   drive_mail_is_read_from_the_ledger_with_its_card
