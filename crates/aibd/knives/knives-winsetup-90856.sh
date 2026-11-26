#!/usr/bin/env bash
# aib-knives: os=macos
# WS1–WS18, WS22–WS27, WR1–WR6: the Windows setup (aibd setup / AIB-Setup.exe; PLAN-win-customer-today-c2f0b-1126 rows 2–5; COND-win-installer-3e53b-1126 r1 §6 + r1 knives; 90856 11-26)
# every knife keeps the code warning-free (`&& false`): RUSTFLAGS has -D warnings in CI
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ws() { python3 -I scripts/knife.py crates/aibd/src/winsetup.rs "$1" "$2" --expect-fail "winsetup::tests::$3" \
        -- cargo test -p aibd --locked --lib "winsetup::tests::$3"; }
wr() { python3 -I scripts/knife.py crates/aib-sys/src/winrun.rs "$1" "$2" --expect-fail "winrun::tests::$3" \
        -- cargo test -p aib-sys --locked --lib "winrun::tests::$3"; }
# WS1 (COND 6') the copy is moved in without comparing its hash with what was read
ws '    if got != want {' \
   '    if got != want && false {' \
   a_copy_whose_hash_differs_is_not_moved_in_and_nothing_after_runs
# WS2 (COND 11) a running installed aibd is overwritten
ws '    if ops.exists(&p.exe) && ops.healthy() {' \
   '    if ops.exists(&p.exe) && ops.healthy() && false {' \
   a_running_installed_aibd_is_not_overwritten
# WS3 (COND 12) no pairing key, and the Run value is written and aibd started anyway
ws '            if let Err(e) = ops.pair_key_init() {' \
   '            if let Err(e) = ops.pair_key_init().or(Ok::<(), String>(())) {' \
   no_pairing_key_means_no_run_value_and_nothing_started
# WS4 (COND 7) a pairing key already made is made again
ws '    match ops.pair_key() {' \
   '    match Some(ops.pair_key()).filter(|_| false).unwrap_or(PairKey::None) {' \
   a_key_already_made_is_kept
# WS13 (3e53b L1) a key aibd would refuse is kept and the setup goes on
ws '            return Err(format!("installer_pair_key_bad: {why}; {KEY_BAD_HOW}"));' \
   '            let _ = (&why, KEY_BAD_HOW);' \
   a_key_aibd_would_refuse_stops_the_setup_without_deleting_it
# WS5 (COND 5) an elevated setup goes on
ws '    if ops.elevated()? {' \
   '    if ops.elevated()? && false {' \
   an_elevated_setup_stops_before_touching_anything
# WS6 (COND 4) a link / junction on the way is followed
ws '        if ops.is_link(layer) {' \
   '        if ops.is_link(layer) && false {' \
   a_link_or_junction_on_the_way_stops_the_setup
# WS7 (COND 10) somebody else's AIB Run value is overwritten
ws '        if v != command {' \
   '        if v != command && false {' \
   somebody_elses_run_value_is_left_and_the_setup_stops
# WS8 (COND 3) the Run value is not read back
ws '    if ops.run_get(RUN_NAME)?.as_deref() != Some(command) {' \
   '    if ops.run_get(RUN_NAME)?.as_deref() != Some(command) && false {' \
   a_run_value_that_reads_back_different_is_removed_and_the_setup_stops
# WS9 (COND 8) uninstall removes a Run value that is not ours
ws '        Some(v) if v == command => {' \
   '        Some(v) if v == command || !v.is_empty() => {' \
   uninstall_removes_only_our_run_value
# WS10 the page opens before aibd answers
ws '        while !ops.healthy() {' \
   '        while !ops.healthy() && false {' \
   an_aibd_that_never_answers_ends_the_setup_with_the_wait
# WS11 any program with aib-setup in its name installs itself instead of running
ws '.is_some_and(|s| s.to_ascii_lowercase().starts_with(SETUP_STEM))' \
   '.is_some_and(|s| s.to_ascii_lowercase().contains(SETUP_STEM))' \
   the_setup_program_is_known_by_its_file_name
# WS12 a relative LOCALAPPDATA is taken (aibd lands wherever the setup was started)
ws '    if !base.is_absolute() {' \
   '    if !base.is_absolute() && false {' \
   the_install_folder_is_aib_under_an_absolute_local_app_data
# WR1 (COND 1) the Run value is written without quotes
wr '    Ok(format!("\"{path}\""))' \
   '    Ok(path.to_string())' \
   a_run_command_is_the_whole_path_in_quotes
# WR2 (COND 13) the Run value carries a flag after the path
wr '    Ok(format!("\"{path}\""))' \
   '    Ok(format!("\"{path}\" setup"))' \
   a_run_command_is_the_whole_path_in_quotes
# WR3 a path with a quote is written as it is (the command line can be split)
wr "    if path.contains(['\"', '\\0', '\\r', '\\n']) {" \
   "    if path.contains(['\\0']) {" \
   a_path_that_cannot_be_quoted_is_refused
# WR4 a relative or %VAR% path is written (resolved against whatever folder Windows starts in)
wr '    if !drive && !path.starts_with(r"\\") {' \
   '    if !drive && !path.starts_with(r"\\") && false {' \
   a_path_that_cannot_be_quoted_is_refused
# WR5 (COND 14) the registry source guard lets a machine-root / other registry call through
wr '            if text.contains(n.as_str()) {' \
   '            if text.contains(n.as_str()) && false {' \
   the_registry_guard_sees_a_planted_name
# --- W2 (ebab8 04:31): Settings > Apps entry, fuller uninstall, rollback ---
# WS14 (COND §4-2) uninstall deletes the program while AIB is running from it
ws '    let running = ops.healthy();' \
   '    let running = ops.healthy() && false;' \
   uninstall_removes_the_program_only_when_aib_is_not_running
# WS15 rollback swaps the program while AIB is running from it
ws '    let busy = ops.exists(&p.exe) && ops.healthy();' \
   '    let busy = ops.exists(&p.exe) && ops.healthy() && false;' \
   rollback_swaps_in_the_previous_program_but_not_while_running
# WS16 somebody else's Settings > Apps entry is overwritten
ws '        if u != ours {' \
   '        if u != ours && false {' \
   somebody_elses_settings_entry_is_left_and_the_setup_goes_on
# WS17 (COND §4-2) uninstall removes a Settings > Apps entry that is not ours
ws '    if listed_ours {' \
   '    if listed_ours || ops.settings_get("DisplayName")?.is_some() {' \
   uninstall_removes_only_our_settings_entry
# WS18 the Settings uninstall command loses its quotes / flag
ws '    format!("{command} setup --uninstall")' \
   '    format!("{command}")' \
   the_setup_lists_aib_in_settings_with_an_uninstall_command
# WR6 values outside the Settings list are written
wr '    UNINSTALL_NAMES.contains(&name)' \
   '    !name.is_empty()' \
   only_the_listed_settings_entry_values_are_written
# --- 3e53b REVIEW-winsetup-w2 (5b59ffdf) L1 / N1 ---
# WS22 (L1) uninstall says the program was removed while the running aibd.exe is still there
ws '            .filter(|f| ops.exists(f))' \
   '            .filter(|f| ops.exists(f) && false)' \
   uninstall_does_not_say_removed_when_the_running_program_stays
# WS23 (N1) a rollback that fails half way leaves no aibd.exe
ws '        let back = ops.rename(&p.new, &p.exe);' \
   '        let back: Result<(), String> = Ok(());' \
   a_rollback_that_fails_half_way_puts_the_current_program_back
# WS24 (3e53b Na, 80d4c947) a rollback with no aibd.exe before says the program is in a .new that is not there
ws '        if !moved {' \
   '        if !moved && false {' \
   a_rollback_with_no_program_before_does_not_point_at_a_missing_copy
# WS25 (ebab8 10:01, laptop se_error) every key error is told to set a PIN, also an se_error on a machine that has one
ws '    } else if has("no_biometry") || has("unavailable") {' \
   '    } else if has("no_biometry") || has("unavailable") || !why.is_empty() {' \
   only_a_machine_without_hello_is_told_to_set_a_pin
# WS26 the error line drops what Windows said (the HRESULT of an se_error)
ws '    format!("{} ({e})", e.code())' \
   '    format!("{} ({})", e.code(), "")' \
   a_hello_error_line_keeps_the_hresult
# WS27 not at the desktop session (unavailable from the session check) is told to set a PIN
ws '    if why.contains(DESKTOP_ONLY) {' \
   '    if why.contains(DESKTOP_ONLY) && false {' \
   only_a_machine_without_hello_is_told_to_set_a_pin
