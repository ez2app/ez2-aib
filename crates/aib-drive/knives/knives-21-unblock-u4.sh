#!/usr/bin/env bash
# aib-knives: os=unix
# u-4 unblock knives (56cda 11-15, DECISION-council-unblock-519d0-1115 §2; #807): each rule loosened must
# turn its own test red. UF1-UF20 moved word for word out of knives-09-wake.sh (where the #804 restack had put
# them) into their own script, so they get their own watchdog in crates/aib-knife/run-knives.sh.
# Run from the repo root; -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
#
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
uf() { python3 -I scripts/knife.py crates/aib-drive/src/unblock_u4.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --lib "unblock_u4::tests::$3"; }
# UF1 a test red on one PR is listed as red on many
uf 'pub const U6_MIN_SUBJECTS: usize = 2;' 'pub const U6_MIN_SUBJECTS: usize = 1; // knife' \
   one_test_red_at_one_place_on_two_prs_is_one_row
# UF2 a knife's own red (after the test phase) is read as a test red
uf '            in_test = false;' '            in_test = !line.is_empty(); // knife' \
   a_receipt_gives_its_head_and_only_the_test_phase_reds
# UF3 the panic place is dropped from the key: one name failing at two places is clustered
uf '            by.entry((name.clone(), loc.clone()))' '            by.entry((name.clone(), loc.get(..0).unwrap_or_default().to_owned())) // knife' \
   one_test_red_at_one_place_on_two_prs_is_one_row
# UF4 a receipt of an old head (the red may be fixed) still counts
uf '        let Some(subject) = current.get(&r.head) else {' '        let Some(subject) = current.get(&r.head).or(Some(&r.rid)) else { // knife' \
   one_test_red_at_one_place_on_two_prs_is_one_row
# UF5 a review read 15 minutes ago is already untaken
uf '        if waited < U11_AFTER_SECS {' '        if waited < U11_AFTER_SECS / 2 { // knife' \
   a_review_read_thirty_minutes_ago_with_no_review_of_its_head_is_a_row
# UF6 an unread review dispatch (act nudges it) is U11 too
uf '        let Some(read) = a.read_at else { continue };' '        let read = a.read_at.unwrap_or(a.sent_at); // knife' \
   a_review_read_thirty_minutes_ago_with_no_review_of_its_head_is_a_row
# UF7 a review naming the short head is not taken for a review of that head
uf '            .any(|r| r.starts_with(head.as_str()) || head.starts_with(r.as_str()))' \
   '            .any(|r| r.starts_with(head.as_str()) && head.starts_with(r.as_str())) // knife' \
   a_review_read_thirty_minutes_ago_with_no_review_of_its_head_is_a_row
# UF8 a dispatch taken back (superseded) is still read
uf '    if matches!(status, "superseded" | "deleted" | "expired") {' '    if matches!(status, "deleted" | "expired") { // knife' \
   only_a_review_dispatch_in_force_is_read
# UF9 a dispatch older than a day is still U11
uf '        if now.saturating_sub(a.sent_at) > DAY_SECS {' '        if now.saturating_sub(a.sent_at) > DAY_SECS * 2 { // knife' \
   a_review_read_thirty_minutes_ago_with_no_review_of_its_head_is_a_row
# UF10 act's retry lines are read as gone
uf '        if v["kind"].as_str() != Some("gone") {' '        if v["kind"].as_str() == Some("x") { // knife' \
   act_gone_lines_of_the_last_day_are_one_row_per_seat
# UF11 a gone line older than a day is still U8
uf '        if now.saturating_sub(at) > DAY_SECS {' '        if now.saturating_sub(at) > DAY_SECS * 2 { // knife' \
   act_gone_lines_of_the_last_day_are_one_row_per_seat
# UF12 (U21) with the reviews unreadable every read dispatch is judged untaken
python3 -I scripts/knife.py crates/aib-drive/src/unblock_u4.rs '        ask.clear();' '        ask.truncate(ask.len()); // knife' \
  --expect-fail what_cannot_be_read_is_listed_not_taken_for_no_blocker \
  -- cargo test -p aib-drive --locked --test unblock_u4_bin what_cannot_be_read_is_listed_not_taken_for_no_blocker
# UF13 (IFACE-unblock-json-v2) a row seen in the last write starts again at this round
uf '        Some(&ms) => ms,' '        Some(&_) => now_ms, // knife' \
   a_row_has_the_v2_shape_and_keeps_when_it_was_first_seen
# UF14 (IFACE-unblock-json-v2) a subject keeps its capitals
uf '            out.push(c.to_ascii_lowercase());' '            out.push(c); // knife' \
   a_row_has_the_v2_shape_and_keeps_when_it_was_first_seen
# UF15 (5d33a 1091fbc2 M1) the first knife's reds right after TESTS-TOTAL are read as test reds
uf '        if line.starts_with("RC ") || line.starts_with(TESTS_TOTAL) {' '        if line.starts_with("RC ") { // knife' \
   a_receipt_gives_its_head_and_only_the_test_phase_reds
# UF16 (M2) a long dm may point outside the state root
uf '    (inside && name.starts_with("RELAY-") && name.ends_with(".txt")).then(|| p.to_path_buf())' \
   '    (inside | true && name.starts_with("RELAY-") && name.ends_with(".txt")).then(|| p.to_path_buf()) // knife' \
   a_long_dm_points_to_a_relay_file_under_the_state_root
# UF17 (N1) a base named after the head is taken for the head
uf '        head: named_head(msg).or_else(|| longest_hex(msg)),' '        head: longest_hex(msg), // knife' \
   only_a_review_dispatch_in_force_is_read
# UF18 (M2) a long dm is read as its pointer line, never as the dispatch in its file
python3 -I scripts/knife.py crates/aib-drive/src/unblock_u4.rs '                    Some(text) => item["msg"] = Value::String(text),' \
  '                    Some(text) => item["msg"] = Value::String(text.replace("派工", "")), // knife' \
  --expect-fail the_three_rules_come_from_the_files_and_write_keeps_them \
  -- cargo test -p aib-drive --locked --test unblock_u4_bin the_three_rules_come_from_the_files_and_write_keeps_them
# UF19 (M2) a long dm that cannot be read is not said
python3 -I scripts/knife.py crates/aib-drive/src/unblock_u4.rs '    if lost > 0 {' '    if lost > usize::MAX - 1 { // knife' \
  --expect-fail what_cannot_be_read_is_listed_not_taken_for_no_blocker \
  -- cargo test -p aib-drive --locked --test unblock_u4_bin what_cannot_be_read_is_listed_not_taken_for_no_blocker
# UF20 (5d33a 131efab8 N1) u-5's unblock_notify gone lines are read as nudges (its code taken for a dm id)
uf '        let Some(key) = v["key"].as_str().filter(|k| k.starts_with(NUDGE_KEY)) else {' \
   '        let Some(key) = v["key"].as_str() else { // knife' \
   act_gone_lines_of_the_last_day_are_one_row_per_seat
