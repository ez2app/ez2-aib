#!/usr/bin/env bash
# The "aib-opinion knives (macOS)" step of aibd.yml's rust job, moved here word for word (519d0 11-16
# 23:5x: aibd.yml is red past 450,000 bytes; new knives live in crates/<crate>/knives/, the workflow
# keeps one call). aib-xtask knife-anchors reads crates/*/knives/*.sh, so every knife here is still
# checked. Run from the repo root; -eo pipefail is what GitHub's `shell: bash` gave the step inline.
set -eo pipefail
set -eu
ok() { python3 -I scripts/knife.py "crates/aib-opinion/src/$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-opinion --locked "$4"; }
# O1 the 結論 counts are not compared with the items
ok lib.rs '            if got != [h, m, l] {' '            if false { // knife' \
   the_declared_counts_must_match_the_items
# O2 a sign or a full-width digit is read as a count
ok lib.rs '        if s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()) {' \
   '        if s.is_empty() { // knife' the_declared_counts_must_match_the_items
# O3 an M item may leave the fix out
ok lib.rs '        matches!(self, Sev::H | Sev::M)' '        matches!(self, Sev::H) // knife' \
   each_malformed_item_line_is_named
# O4 a number used twice in one file
ok lib.rs '                if items.iter().any(|x| x.n == it.n) {' '                if false { // knife' \
   each_malformed_item_line_is_named
# O5 the owner: header is not read
ok lib.rs '            if let Some(v) = l.strip_prefix("owner:") {' \
   '            if let Some(v) = l.strip_prefix("owner_knife:") {' \
   the_seat_comes_from_the_header_else_from_the_file_name
# O6 a two-letter run counts as a seat
ok lib.rs '    s.len() >= 3' '    !s.is_empty() // knife' citations_need_a_whole_seat_and_a_whole_number
# O7 a seat that continues a longer word
ok lib.rs '            if is_seat(seat) && !word_before && !word_after {' \
   '            if is_seat(seat) && (word_before || true) && !word_after { // knife (still reads it)' citations_need_a_whole_seat_and_a_whole_number
# O8 a number that runs into a letter
ok lib.rs '            if is_seat(seat) && !word_before && !word_after {' \
   '            if is_seat(seat) && !word_before && (word_after || true) { // knife (still reads it)' citations_need_a_whole_seat_and_a_whole_number
# O9 the heading above is not followed
ok lib.rs '            heading = h.to_string();' '            heading = heading.clone(); let _ = h; // knife (keeps it mut)' \
   each_item_lands_under_the_heading_above_its_citation
# O10 不採 is not read
ok lib.rs '        let not_taken = l.contains("不採");' '        let not_taken = l.is_empty(); // knife' \
   each_item_lands_under_the_heading_above_its_citation
# O11 a citation of no item is dropped
ok lib.rs '                None => unknown.push((format!("{}-O{}", c.seat, c.n), line)),' \
   '                None => unknown.truncate(unknown.len()), // knife (keeps it mut)' each_item_lands_under_the_heading_above_its_citation
# O12 a note (N) must land too
ok lib.rs '        !matches!(self, Sev::N)' '        true // knife' \
   only_items_that_must_land_are_listed_as_unlanded
# O13 rows keep file order
ok lib.rs '    rows.sort_by(|a, b| (&a.0.seat, a.0.n).cmp(&(&b.0.seat, b.0.n)));' \
   '    rows.sort_by(|a, b| b.0.n.cmp(&a.0.n)); // knife' \
   rows_run_in_seat_then_number_order_and_cells_escape_bars
# O14 a | inside a cell is not escaped
ok lib.rs "    s.replace('|', \"\\\\|\")" '    s.to_string() // knife' \
   rows_run_in_seat_then_number_order_and_cells_escape_bars
# O15 check prints problems but exits 0
ok main.rs $'        eprintln!("aib-opinion: {problems} problem(s)");\n        return ExitCode::from(2);' \
   '        eprintln!("aib-opinion: {problems} problem(s)"); // knife' \
   check_passes_good_files_and_names_every_problem_by_file_and_line
# O16 table runs on opinions that have problems
ok main.rs $'(aib-opinion check)"\n        );\n        return ExitCode::from(2);' \
   $'(aib-opinion check)"\n        ); // knife' table_refuses_bad_opinions_and_two_files_from_one_seat
# O17 two files from one seat are merged
ok main.rs '    if let Some(w) = seats.windows(2).find(|w| w[0] == w[1]) {' \
   '    if let Some(w) = seats.windows(2).find(|w| w[0] == w[1] && w.is_empty()) { // knife' \
   table_refuses_bad_opinions_and_two_files_from_one_seat
# O18 --strict ignores a citation of no item
ok main.rs '    if strict && (!t.unlanded().is_empty() || !t.unknown.is_empty()) {' \
   '    if strict && !t.unlanded().is_empty() { // knife' \
   table_prints_each_landing_and_strict_fails_only_on_a_gap
