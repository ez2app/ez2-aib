#!/usr/bin/env bash
# Time-saved events (Andy 11-22: the precheck gates and shadow rerun show what they saved from one book;
# aib_ledger::timesave). Each rule, broken, turns its own test in tests/timesave.rs red. Run on macOS (the
# header default) by the shared crate-knives step (crates/aib-knife/run-knives.sh rust). Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
tk() { python3 -I scripts/knife.py crates/aib-ledger/src/timesave.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-ledger --locked --test timesave "$3"; }
# TS1 an observe-mode saving (a prediction that stopped nothing) is added to the totals
tk '        if !one.real {' '        if !one.real && false { // knife' \
   confirmed_and_estimated_are_apart_and_observe_mode_is_in_no_total
# TS2 an estimate nobody checked is added to the confirmed number
tk '                if one.confirmed {' '                if one.confirmed || true { // knife' \
   confirmed_and_estimated_are_apart_and_observe_mode_is_in_no_total
# TS3 a saving that was a wrong block stays in the total
tk '        if one.wrong {' '        if one.wrong && false { // knife' \
   a_checked_estimate_becomes_confirmed_and_a_wrong_block_is_taken_out_with_its_penalty
# TS4 a checked estimate stays an estimate
tk '            None if !one.confirmed => one.confirmed = true,' '            None if !one.confirmed => one.confirmed = false, // knife' \
   a_checked_estimate_becomes_confirmed_and_a_wrong_block_is_taken_out_with_its_penalty
# TS5 a verdict of the other system is written (a gate that "reuses")
tk '    if !verdict_fits(s.system, s.verdict) {' '    if !verdict_fits(s.system, s.verdict) && false { // knife' \
   a_saving_says_which_system_what_it_avoided_and_where_its_number_comes_from
# TS6 a saving is written without where its number comes from
tk '        return Err("basis is empty: a saving says where its number comes from".into());' '        let _ = (); // knife: written anyway' \
   a_saving_says_which_system_what_it_avoided_and_where_its_number_comes_from
# TS7 the same id twice is taken as two events (the second replaces the first without a word)
tk '                Some((id, one)) if one.at <= latest && !saved.contains_key(&id) => {' '                Some((id, one)) if one.at <= latest => { // knife' \
   what_cannot_be_read_is_counted_and_never_added
# TS8 a saving whose fields do not fit is read anyway (no basis)
tk '    str_of(o, "basis").filter(|s| !s.trim().is_empty())?;' '    str_of(o, "basis")?; // knife: any basis' \
   what_cannot_be_read_is_counted_and_never_added
# TS9 the week starts on Sunday
tk '    let week = day - (day + 3).rem_euclid(7);' '    let week = day - (day + 4).rem_euclid(7); // knife' \
   a_box_holds_what_happened_in_it_and_the_week_starts_on_monday_on_the_callers_clock
# TS10 the day is UTC'"'"'s whatever clock the caller gave
tk '    let local = now_ms.saturating_add(off);' '    let local = now_ms; // knife' \
   a_box_holds_what_happened_in_it_and_the_week_starts_on_monday_on_the_callers_clock
# TS11 a line that is not JSON is dropped without being counted
tk '                    _ => bad += 1,' '                    _ => {} // knife: not counted' \
   the_summary_is_read_from_every_programs_month_files_and_a_broken_line_is_counted
# TS12 a unit'"'"'s time is written without its machine
tk '    if runner.is_empty() {' '    if runner.is_empty() && false { // knife' \
   a_units_time_says_which_machine_and_how_big
# TS13 a confirm that is not a whole event (no id, no time) still turns an estimate into a fact
tk '                Some((r, at)) if at <= latest => marks.push((r, None)),' '                _ if str_of(o, "ref").is_some() => marks.push((str_of(o, "ref").unwrap_or("").to_string(), None)), // knife' \
   a_confirm_or_a_wrong_that_is_not_a_whole_event_changes_nothing
# TS14 a saving dated next year is in today'"'"'s box
tk '    let latest = now_ms.saturating_add(FUTURE_SLACK_MS);' '    let latest = i64::MAX; // knife: any time' \
   an_event_dated_after_the_readers_clock_is_in_no_box
# TS15 a wrong block that had been checked before is not taken out
tk '            Some((at, p)) if !one.wrong => {' '            Some((at, p)) if !one.wrong && !one.confirmed => { // knife' \
   a_saving_that_was_checked_can_still_be_found_wrong
# TS16 an events folder that is there and cannot be read looks like a program with no events
tk '            Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,' '            Err(_) if true => continue, // knife' \
   a_folder_that_is_there_and_cannot_be_read_is_counted
