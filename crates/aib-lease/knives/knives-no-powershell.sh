#!/usr/bin/env bash
# aib-knives: os=unix
# S101 no-PowerShell knives (PS1 aib-lease hosts.rs, PS2 aib-plan aib-hosts.rs); run by the crate knives step (run-knives.sh rust).
# Moved out of .github/workflows/aibd.yml word for word (519d0 11-16 23:5x: the workflow file is near
# its 450,000-byte guard; new knives live in crates/<crate>/knives/). aib-xtask knife-anchors reads
# crates/*/knives/*.sh. Run from the repo root.
set -eo pipefail
set -eu
# same helper as the aib-lease knife step (this script runs in its own shell)
dk() { python3 -I scripts/knife.py crates/aib-lease/src/hosts.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-lease --locked --lib "$3"; }
# S101 (7b81e, Andy 11-15 only Rust): the Windows probe is our own Rust exe; each knife puts
# the old shell back into one of the two sources and its source-scan test turns red
# PS1 aib-lease hosts.rs runs a PowerShell one-liner again
dk 'pub const WINDOWS_PROBE: &str = r".ez2-aib\bin\aib-lease.exe probe";' \
   'pub const WINDOWS_PROBE: &str = "powershell -NoProfile -Command Get-CimInstance"; // knife' \
   the_lease_sources_hold_no_banned_shell_word_in_code
# PS2 aib-plan aib-hosts.rs runs a PowerShell one-liner again
python3 -I scripts/knife.py crates/aib-plan/src/bin/aib-hosts.rs \
  'const WIN_REMOTE: &str = aib_lease::hosts::WINDOWS_PROBE;' \
  'const WIN_REMOTE: &str = "pwsh -NoProfile -Command Get-CimInstance"; // knife' \
  --expect-fail the_hosts_source_holds_no_banned_shell_word_in_code \
  -- cargo test -p aib-plan --locked --bin aib-hosts the_hosts_source_holds_no_banned_shell_word_in_code
