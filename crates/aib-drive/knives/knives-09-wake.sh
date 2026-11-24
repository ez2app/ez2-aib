#!/usr/bin/env bash
# aib-knives: os=unix
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
