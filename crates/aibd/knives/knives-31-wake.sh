#!/usr/bin/env bash
# aib-knives: os=macos
# N-web-oneline 1 knives WK1–WK3 (7b81e 11-19; 519d0 11-19 01:2x, SURVEY-webonly-519d0-1119 §6; Andy「終端機只有那一行
# 叫起網頁服務用」): `aibd ctl web` starts aibd through launchd only when its job file is there and only on macOS,
# and stops when launchctl could not start it. Each, broken, turns its own test red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# WK1 launchd is asked although aibd was never installed
python3 -I scripts/knife.py crates/aibd/src/wake.rs \
  '    if !exists(&plist) {' \
  '    if !exists(&plist) && false { // knife' \
  --expect-fail wake::tests::nothing_is_started_without_the_job_file_or_off_macos \
  -- cargo test -p aibd --locked --lib wake::tests::nothing_is_started_without_the_job_file_or_off_macos
# WK2 launchd is asked off macOS
python3 -I scripts/knife.py crates/aibd/src/wake.rs \
  '    if !macos {' \
  '    if !macos && false { // knife' \
  --expect-fail wake::tests::nothing_is_started_without_the_job_file_or_off_macos \
  -- cargo test -p aibd --locked --lib wake::tests::nothing_is_started_without_the_job_file_or_off_macos
# WK3 a refused kickstart is taken as started
python3 -I scripts/knife.py crates/aibd/src/ctl.rs \
  '        started = launch(&a);' \
  '        started = true; let _ = (&launch, &a); // knife' \
  --expect-fail ctl::tests::web_starts_an_installed_aibd_through_launchd_and_only_then \
  -- cargo test -p aibd --locked --bin aibd ctl::tests::web_starts_an_installed_aibd_through_launchd_and_only_then
