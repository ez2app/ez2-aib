#!/usr/bin/env bash
# The "aib-drive wake knives (macOS)" step of aibd.yml's rust job, moved here word for word
# (RULES §21, Andy 11-15 #15, 519d0 S79-rule21; the aib-cloud-host knives went first, 11-15 08:08).
# aibd.yml runs every crates/aib-drive/knives/*.sh in name order in one step; aib-xtask knife-anchors
# reads crates/*/knives/*.sh, so every knife here is still checked. Run from the repo root;
# -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
#
# S79 n-4a wake (7b81e, KNIVES-wake.md): each event-writing rule, broken, turns its test red.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
wk() { python3 -I scripts/knife.py crates/aib-drive/src/wake.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test wake "$3"; }
# WK1
wk 'std::fs::rename(&tmp, &target)' \
   'std::fs::rename(&tmp, &tmp)' \
   an_event_is_one_json_line_put_in_place_by_rename
# WK2
wk 'if !drive.is_dir() {' \
   'if !drive.is_dir() && std::fs::create_dir_all(drive).is_err() {' \
   no_drive_folder_means_nothing_written_and_nothing_made
# WK3
wk 'let _ = std::fs::remove_file(&tmp);' \
   'let _ = &tmp;' \
   a_write_that_fails_is_exit_5_and_leaves_no_temp_file
# WK4
wk '"seat_free" => seat_code(r),' \
   '"seat_free" => seat_code(r) || true,' \
   each_kind_takes_only_its_own_ref
# WK5
wk '"tick" => false,' \
   '"tick" => true,' \
   each_kind_takes_only_its_own_ref
# WK6
wk '.or(by_env.filter(|b| !b.is_empty()))' \
   '.or(by_env.filter(|b| b.is_empty()))' \
   by_names_the_writer_and_a_consumer_skips_its_own
# WK7
wk 'Some(ev) => ev.reference.filter(|r| is_event(r)),' \
   'Some(_) => None,' \
   observe_reads_the_report_id_of_new_and_old_files
# WK8
wk '"merge" => r.starts_with("pr/") && is_event(r),' \
   '"merge" => is_event(r),' \
   each_kind_takes_only_its_own_ref
# WK9
wk 'ev.by == me' \
   'ev.by != me' \
   by_names_the_writer_and_a_consumer_skips_its_own
# S79 A2 (7b81e): aibd's 10-minute aib-drive wake (crates/aibd/src/drive_wake.rs)
dw() { python3 -I scripts/knife.py crates/aibd/src/drive_wake.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aibd --locked --lib "$3"; }
# DW1 the wake runs without AIBD_DRIVE_WAKE=1 (new behaviour must be off by default)
dw 'if env(ENV_ON).as_deref() != Some("1") {' 'if env(ENV_ON).as_deref() == Some("never") {' \
   the_drive_wake_is_off_unless_turned_on_with_1
# DW2 an empty AIB_DRIVE is taken as the program path
dw 'let set = |k: &str| env(k).filter(|v| !v.is_empty());' 'let set = |k: &str| env(k);' \
   the_drive_wake_runs_the_set_program_or_the_one_in_ez2_aib_bin
# DW3 the wake writes another kind than tick
dw '["event", "tick", "--by", BY]' '["event", "merge", "--by", BY]' \
   a_drive_wake_passes_tick_by_aibd_and_a_failing_program_is_not_ran
# DW4 a program that exits non-zero counts as a wake
dw 'Ok(Some(s)) => return exited(s),' 'Ok(Some(_)) => return Wake::Ran,' \
   a_drive_wake_passes_tick_by_aibd_and_a_failing_program_is_not_ran
# DW6 (5d33a A2 L1) a hung aib-drive is not cut at the wake's limit
dw 'begin(Some(Instant::now() + limit))' 'begin(Some(Instant::now() + limit * 1000))' \
   a_hung_drive_wake_is_killed_at_the_limit
# DW7 (5d33a A2 L2) the first failure makes no line
dw '1 => Line::FirstFail,' '1 => Line::None,' \
   failures_are_logged_once_then_every_six_hours_and_on_recovery
# DW8 only the first 6-hour mark is logged, not every one
dw 'n if n.is_multiple_of(FAIL_REPEAT) =>' 'n if n == FAIL_REPEAT =>' \
   failures_are_logged_once_then_every_six_hours_and_on_recovery
# 519d0 11-15 (idea A of 56cda): each wake period also runs act --auto
# DA1 the period runs no act round
dw '    let a = act_once(bin, ACT_LIMIT);' '    let a = Wake::Ran; // knife' \
   each_drive_wake_period_runs_the_tick_then_one_act_round
# DA2 the act round passes other arguments than act --auto
dw '    ["act", "--auto"]' '    ["act", "--once"] // knife' \
   each_drive_wake_period_runs_the_tick_then_one_act_round
# DW9 a recovery makes no line
dw 'return if n > 0 {' 'return if n > u64::MAX - 1 {' \
   failures_are_logged_once_then_every_six_hours_and_on_recovery
# DW10 (S79 B3-1) the refresh thread runs something other than refresh --auto
dw '    ["refresh", "--auto"]' '    ["refresh", "--full"]' \
   a_drive_refresh_passes_refresh_auto_and_has_a_scan_long_limit
# DW11 (S79 B3-1) a refresh is cut at a wake's 60 s, before aib-slice can finish
dw 'pub const REFRESH_LIMIT: Duration = Duration::from_secs(31 * 60);' \
   'pub const REFRESH_LIMIT: Duration = Duration::from_secs(60);' \
   a_drive_refresh_passes_refresh_auto_and_has_a_scan_long_limit
# DW5 the resident timer wakes more often than every 30 s
python3 -I scripts/knife.py crates/aibd/src/timers.rs 'secs(600),' 'secs(20),' \
  --expect-fail timers_follow_the_rules -- cargo test -p aibd --locked --lib timers_follow_the_rules
# BG2 (519d0 11-15 22:13 A) a child aibd starts stays in the darwin background tier (aib-slice 3.3x slower, killed at the limit)
# only from an unclamped shell: in the background tier the test fails with or without the knife (a fake bite)
if [ "$(ps -o pri= -p $$ | tr -d ' ')" -gt 4 ]; then
  dw '    aib_sys::background::foreground_child(&mut cmd);' '    let _ = &mut cmd; // knife' \
     a_child_aibd_starts_leaves_the_background_tier
else
  echo "::warning title=BG2 not run::this runner is in the darwin background tier; BG2 runs in receipts from a terminal"
fi
# u-4 unblock (56cda 11-15, DECISION-council-unblock-519d0-1115 §2): each rule loosened must turn its own test red
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
