#!/usr/bin/env bash
# aib-knives: os=macos
# #806 win-accept4 knives (a9136 11-15, 1ced7 preview.5 Windows run) that run on macOS, moved out of the
# "seats / sessions / output knives (macOS)" and "aib web knives (macOS)" steps word for word (RULES §21;
# run by the shared crate-knives step, crates/aib-knife/run-knives.sh rust, main #836). Each knife, put in,
# must turn its own test red. Run from the repo root.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# WIN4-1b (a9136 11-15, 1ced7 Windows run) a Windows line keeps its \r
python3 -I scripts/knife.py crates/aibd/src/session_output.rs \
  '    if let Some(x) = b.strip_suffix(b"\r") {' \
  '    if let Some(x) = b.strip_suffix(b"\r").filter(|_| false) { // knife' \
  --expect-fail session_output::tests::windows_line_ends_are_dropped_from_the_stored_line \
  -- cargo test -p aibd --locked --lib session_output::tests::windows_line_ends_are_dropped_from_the_stored_line
# WIN4-3 Windows policy init takes a claude.exe that is really a node script
python3 -I scripts/knife.py crates/aibd/src/ctl.rs \
  '                            Err("runs through node; install the native build".to_string())' \
  '                            Ok(()) // knife: a node script is taken' \
  --expect-fail ctl::tests::windows_policy_refuses_shims_and_non_native_files \
  -- cargo test -p aibd --locked --bin aibd ctl::tests::windows_policy_refuses_shims_and_non_native_files
# WIN4-4 (a9136 11-15) a build without the web page still makes a code for a 404
python3 -I scripts/knife.py crates/aibd/src/ctl.rs \
  '        if e.contains("web_not_built") {' '        if e.contains("web_not_built") && false { // knife' \
  --expect-fail ctl::tests::web_without_a_page_says_so_and_makes_no_code \
  -- cargo test -p aibd --locked --bin aibd ctl::tests::web_without_a_page_says_so_and_makes_no_code
