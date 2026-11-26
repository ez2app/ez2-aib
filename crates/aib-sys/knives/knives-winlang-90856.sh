#!/usr/bin/env bash
# aib-knives: os=unix
# WL1: the AIB shortcut window's language (ebab8 11-26 12:39). winrun::lang_of is plain Rust, tested on every
# platform; the knife must turn its test red. Run from the repo root.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# WL1 Chinese Windows gets the English lines
python3 -I scripts/knife.py crates/aib-sys/src/winrun.rs \
  '        0x04 => "zh-TW",' \
  '        0x04 => "en",' \
  --expect-fail winrun::lang_tests::windows_languages_map_to_the_four_page_languages \
  -- cargo test -p aib-sys --locked --lib winrun::lang_tests::windows_languages_map_to_the_four_page_languages
