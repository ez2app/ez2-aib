#!/usr/bin/env bash
# aib-knives: os=unix
# A15-repocfg knives RC1–RC2 (a9136 11-18, DECISION-council-split-519d0-1118): the repo is read from the
# settings before the built-in default, and an empty setting is unset. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
rc() { python3 -I scripts/knife.py crates/aib-drive/src/source.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --lib "source::sender_tests::$3"; }
# RC1 an empty setting is taken as the repo
rc '        .find_map(|k| get(k).filter(|v| !v.trim().is_empty()))' \
   '        .find_map(|k| get(k).filter(|v| true || !v.trim().is_empty())) // knife' \
   the_repo_is_read_from_the_settings_before_the_default
# RC2 AIB_REPO wins over the drive's own setting
rc '    ["AIB_DRIVE_REPO", "AIB_REPO"]' \
   '    ["AIB_REPO", "AIB_DRIVE_REPO"] // knife' \
   the_repo_is_read_from_the_settings_before_the_default
