#!/usr/bin/env bash
# aib-slice top folder SC1 (a9136 11-17, S73l-incr M1 from 0eed7 VERIFY-S73l-incr-0eed7-a7cfc605):
# every git command runs at the repo's top folder, so a sweep from the aib/ subfolder sees a workflow
# over the limit and leaves no empty answer in the cache. Broken, its test turns red. Run on macOS (the
# header default) by the shared crate-knives step. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
ss() { python3 -I scripts/knife.py crates/aib-slice/src/scan.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-slice --locked --test sweep "$3"; }
# SC1 git runs in the folder it was given, not the top folder
ss '            git.repo = PathBuf::from(top);' \
   '            let _ = top; // knife' \
   a_sweep_from_a_subfolder_answers_like_one_from_the_top
