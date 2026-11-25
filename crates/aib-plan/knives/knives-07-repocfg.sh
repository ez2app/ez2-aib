#!/usr/bin/env bash
# aib-knives: os=unix
# A15-repocfg knife RC3 (a9136 11-18): aib-plan's repo setting skips an empty value. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
rp() { python3 -I scripts/knife.py crates/aib-plan/src/cireuse_view.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-plan --locked --lib "cireuse_view::tests::$3"; }
# RC3 an empty setting is taken as the repo
rp '        .find_map(|k| get(k).filter(|v| !v.trim().is_empty()))' \
   '        .find_map(|k| get(k).filter(|v| true || !v.trim().is_empty())) // knife' \
   the_repo_is_read_from_the_settings_before_the_default
