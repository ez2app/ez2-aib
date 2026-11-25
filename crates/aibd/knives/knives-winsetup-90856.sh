#!/usr/bin/env bash
# aib-knives: os=macos
# WS1–WS6, WR1–WR2: the Windows setup (aibd setup / AIB-Setup.exe; PLAN-win-customer-today-c2f0b-1126 rows 2–5, 90856 11-26)
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ws() { python3 -I scripts/knife.py crates/aibd/src/winsetup.rs "$1" "$2" --expect-fail "winsetup::tests::$3" \
        -- cargo test -p aibd --locked --lib "winsetup::tests::$3"; }
wr() { python3 -I scripts/knife.py crates/aib-sys/src/winrun.rs "$1" "$2" --expect-fail "winrun::tests::$3" \
        -- cargo test -p aib-sys --locked --lib "winrun::tests::$3"; }
# WS1 the Run value is the bare path (a folder with spaces reads as another program)
ws 'let command = aib_sys::winrun::quoted(&p.exe.to_string_lossy())?;' \
   'let command = p.exe.to_string_lossy().into_owned();' \
   the_setup_copies_pairs_writes_the_quoted_run_value_starts_and_opens_in_that_order
# WS2 a pairing key already made is made again (a second Hello, and the old key refused)
ws '    if ops.pair_key_exists() {' \
   '    if false && ops.pair_key_exists() {' \
   a_key_already_made_is_kept_and_a_running_aibd_is_not_started_again
# WS3 no Windows Hello yet ends the setup instead of going on to the page
ws '                done.pair_key_later = Some(e);' \
   '                return Err(e);' \
   no_hello_yet_does_not_stop_the_setup
# WS4 the setup does not wait for aibd to answer before opening the page
ws '        while !ops.healthy() {' \
   '        while false && !ops.healthy() {' \
   an_aibd_that_never_answers_ends_the_setup_with_the_wait
# WS5 any program with aib-setup in its name installs itself instead of running
ws '.is_some_and(|s| s.to_ascii_lowercase().starts_with(SETUP_STEM))' \
   '.is_some_and(|s| s.to_ascii_lowercase().contains(SETUP_STEM))' \
   the_setup_program_is_known_by_its_file_name
# WS6 a relative LOCALAPPDATA is taken (aibd lands wherever the setup was started)
ws '    if !base.is_absolute() {' \
   '    if false {' \
   the_install_folder_is_aib_under_an_absolute_local_app_data
# WR1 a path with a quote is written as it is (the command line can be split)
wr "    if path.contains(['\"', '\\0', '\\r', '\\n']) {" \
   "    if path.contains(['\\0']) {" \
   a_path_that_cannot_be_quoted_is_refused
# WR2 the Run value is written without quotes
wr '    Ok(format!("\"{path}\""))' \
   '    Ok(path.to_string())' \
   a_run_command_is_the_whole_path_in_quotes
