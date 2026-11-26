#!/usr/bin/env bash
# aib-knives: os=unix
# aib-pending knives PK1–PK30 (S81 p-1a / p-1b / p-1c, b433b; redone on main by 7b81e 11-19 per f1fae
# TRIAGE-pr815): the rules for what may never be moved out of pending, the index / list / show command, and
# check / new. Moved word for word from #798's inline aibd.yml step (11-16 ruling, IFACE-1117 §5); the
# crates/*/knives step runs it. Each anchor occurs once in its file. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
pp() { python3 -I scripts/knife.py crates/aib-pending/src/lib.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-pending --locked --lib "$3"; }
# PK1 a subfolder counts as movable
pp '    if e.is_dir {' \
   '    if e.is_dir && e.name.is_empty() { // knife' \
   subfolders_push_freeze_and_own_files_are_never_moved
# PK2 PUSH-FREEZE counts as movable
pp '    if p == "PUSH" && e.name.to_ascii_uppercase().starts_with("PUSH-FREEZE") {' \
   '    if p == "PUSH" && e.name.to_ascii_uppercase().starts_with("PUSH-FREEZE") && false { // knife' \
   subfolders_push_freeze_and_own_files_are_never_moved
# PK3 a seat's latest HANDOFF counts as movable
pp '    if ctx.latest_handoff.contains(&e.name) {' \
   '    if ctx.latest_handoff.contains(&e.name) && false { // knife' \
   each_seats_latest_handoff_is_kept_and_older_ones_are_records
# PK4 a file an open PR reads counts as movable
pp '            Source::Known(refs) if refs.contains(&key) => return Some(Reason::OpenPrRef),' \
   '            Source::Known(refs) if refs.contains(&key) => {} // knife' \
   a_file_an_open_pr_reads_is_kept_and_an_unknown_pr_list_keeps_everything_head_bound
# PK5 an unreadable open-PR list counts as no PR open
pp '            Source::Unknown => return Some(Reason::OpenPrUnknown),' \
   '            Source::Unknown => {} // knife' \
   a_file_an_open_pr_reads_is_kept_and_an_unknown_pr_list_keeps_everything_head_bound
# PK6 a review whose head is not in main counts as movable
pp '                Some(HeadState::Open) => return Some(Reason::HeadOpen),' \
   '                Some(HeadState::Open) => {} // knife' \
   a_review_whose_head_is_not_in_main_or_unknown_is_kept
# PK7 a head that was not looked up counts as settled
pp '                None => return Some(Reason::HeadUnknown),' \
   '                None => {} // knife' \
   a_review_whose_head_is_not_in_main_or_unknown_is_kept
# PK8 an exclusion table with no entries counts as nothing excluded
pp '    if patterns.is_empty() {' \
   '    if patterns.is_empty() && false { // knife' \
   the_table_reads_paths_drops_notes_and_an_empty_table_is_an_error
# PK9 an unreadable header counts as no header
pp '        HeaderRead::Unreadable(why) => return Some(Reason::HeaderUnreadable(why.clone())),' \
   '        HeaderRead::Unreadable(why) => { let _ = why; } // knife' \
   an_unreadable_header_keeps_the_file_and_no_header_does_not
# PK10 a status: 常駐 header counts as movable
pp '        HeaderRead::Present(h) if h.get("status") == Some("常駐") => {' \
   '        HeaderRead::Present(h) if h.get("status") == Some("常駐") && false => { // knife' \
   a_resident_header_and_the_table_keep_a_file
# PK11 an unreadable head list counts as every head settled
pp '            Source::Unknown => return Some(Reason::HeadUnknown),' \
   '            Source::Unknown => {} // knife' \
   bundles_are_bound_to_their_head_and_an_unreadable_head_list_keeps_them
# p-1b (b433b): index.rs and the aib-pending command
pi() { python3 -I scripts/knife.py crates/aib-pending/src/index.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-pending --locked --lib "$3"; }
pm() { python3 -I scripts/knife.py crates/aib-pending/src/main.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-pending --locked --test cli "$3"; }
# PK12 an unbalanced paren glues its note onto the pattern (1ced7 p-1a L1)
pp "        s.replace(['(', ')', '\u{ff08}', '\u{ff09}'], \" \")" \
   '        s.to_string() // knife' \
   an_unbalanced_paren_keeps_the_rest_of_the_line
# PK13 a stray closing paren counts as balanced
pp "            ')' | '\u{ff09}' if depth == 0 => stray_close = true," \
   "            ')' | '\u{ff09}' if depth == 0 => stray_close |= false, // knife" \
   an_unbalanced_paren_keeps_the_rest_of_the_line
# PK14 a file counts as referring to itself
pi '                if *target != f.entry.name {' \
   '                if !target.is_empty() { // knife' \
   refs_count_other_files_once_by_name_or_id_and_never_the_file_itself
# PK15 a d_ id is not resolved as a reference
pi '            return self.by_id.get(t);' \
   '            return None; // knife' \
   refs_count_other_files_once_by_name_or_id_and_never_the_file_itself
# PK16 supersedes is not read back into superseded_by
pi '                if *old != f.entry.name {' \
   '                if old.is_empty() { // knife' \
   superseded_by_is_read_back_from_supersedes_and_the_chain_walks_both_ways
# PK17 a file that changed during the scan does not mark it partial
pi '        .filter(|(k, v)| after.get(*k) != Some(v))' \
   '        .filter(|(k, _)| !after.contains_key(*k)) // knife' \
   a_file_that_changed_or_appeared_during_the_scan_marks_it_partial
# PK18 a status change is not listed in changes
pi '            Some(old) if old != status => {' \
   '            Some(old) if old != status && false => { // knife' \
   changes_list_added_removed_and_status_changed
# PK19 a held INDEX.lock does not stop a second index
pm '    match f.try_lock() {' \
   '    match Ok::<(), TryLockError>(()) { // knife' \
   a_held_lock_refuses_a_second_index_and_writes_nothing
# PK20 INDEX.lock / INDEX.json are indexed as pending files
pm '        } else if ft.is_file() && !own_file(&name) {' \
   '        } else if ft.is_file() && (!own_file(&name) || true) { // knife' \
   index_writes_rows_refs_chain_and_patterns_and_skips_folders_and_its_own_files
# PK21 index with an unreadable exclusion table ends rc 0 (1ced7 p-1b L1)
pm '    match doc["exclude_error"].as_str() {' \
   '    match None::<&str> { // knife' \
   patterns_prints_the_table_and_an_unreadable_table_is_rc_3_for_patterns_and_index
# p-1c (b433b): check (report only) and new (a new file only)
pc() { python3 -I scripts/knife.py crates/aib-pending/src/check.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-pending --locked --lib "$3"; }
# PK22 an evolving file with no header is not reported
pc '            "absent" if r.kind == "evolving" => out.push(Finding::EvolvingNoHeader(r.name.clone())),' \
   '            "absent" if r.kind == "evolving" && false => out.push(Finding::EvolvingNoHeader(r.name.clone())), // knife' \
   an_evolving_file_with_no_header_and_an_unreadable_header_are_reported
# PK23 a deprecated file nobody replaced is not reported
pc '            && r.superseded_by.is_empty()' \
   '            && r.superseded_by.len() > 99 // knife' \
   deprecated_needs_a_successor_or_a_reason
# PK24 two finals of one kind on one node are not reported
pc '            out.push(Finding::TwoFinals(node, p, names));' \
   '            let _ = (node, p, names); // knife' \
   two_finals_of_one_kind_on_one_node_are_reported_but_reviews_are_not
# PK25 a duplicate id is not reported (1ced7 p-1b N1)
pc '            out.push(Finding::DuplicateId(id.to_string(), names));' \
   '            let _ = (id, names); // knife' \
   a_duplicate_id_is_reported
# PK26 a supersedes naming a missing file is not reported
pc '                if !found {' \
   '                if !found && false { // knife' \
   a_supersedes_that_names_a_missing_file_or_id_is_reported
# PK27 a resident file missing from the table is not reported
pc '                if t.matches(&r.name, false).is_none() {' \
   '                if t.matches(&r.name, false).is_some() { // knife' \
   a_resident_file_missing_from_the_table_and_unknown_prefixes_are_reported
# PK28 new overwrites a file that exists
pm '        .create_new(true)' \
   '        .create(true).truncate(true) // knife' \
   new_issues_an_id_writes_only_a_new_file_and_never_overwrites
# PK29 new goes on without the lock
pm '    let held = take_lock(dir)?;' \
   '    let held = take_lock(dir).ok(); // knife' \
   new_waits_for_no_one_a_held_lock_refuses_it
# PK30 new takes a file name with a newline or NUL in it (1ced7 p-1c L1)
pc '        || name.chars().any(char::is_control)' \
   '        || name.chars().any(char::is_control) && false // knife' \
   ids_are_six_base32_letters_and_new_only_takes_a_top_level_evolving_name
