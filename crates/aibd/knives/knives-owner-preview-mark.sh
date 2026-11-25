#!/usr/bin/env bash
# aib-knives: os=unix
# PM1-PM3 (b4297 11-21; 5d33a PREREAD-pvtouch-rrmerge L1): a preview aibd's fingerprint window
# starts with 「預覽:」, the real one's does not. Every mutant keeps its names used (knife-compile).
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
pm() { python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs "$1" "$2" \
  --expect-fail owner_ok::tests::a_previews_window_says_preview_first_and_the_real_ones_does_not \
  -- cargo test -p aibd --locked --lib owner_ok::tests::a_previews_window_says_preview_first_and_the_real_ones_does_not; }
# PM1 the preview gets the real one's words
pm '    if preview {' '    if !preview { // knife'
# PM2 the window drops the owner's mark
pm '        let why = format!("{}{words}", self.mark);' '        let why = format!("{}{words}", &self.mark[..0]); // knife'
# PM3 marking an owner keeps nothing
pm '        self.mark = mark;' '        self.mark = &mark[..0]; // knife'
