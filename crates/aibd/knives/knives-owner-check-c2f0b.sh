#!/usr/bin/env bash
# aib-knives: os=any
# OC1–OC2 (ebab8 11-26 03:28): the page reads this machine's owner-check class from GET /api/v1/owner-check.
# Each, broken, turns its own test red. Run from the repo root.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# OC1 the route is not mounted: the page's GET finds nothing
python3 -I scripts/knife.py crates/aibd/src/api.rs \
  '        OWNER_CHECK => get(crate::owner_probe_win::state).fallback(nope),' \
  '        OWNER_CHECK => return None,' \
  --expect-fail a_paired_page_reads_the_owner_check_class_and_nothing_else_does \
  -- cargo test -p aibd --locked --test owner_check_get
# OC2 the class is not given: the page cannot tell which steps to show
python3 -I scripts/knife.py crates/aibd/src/owner_probe_win.rs \
  '        "class": class.map(WinClass::word),' \
  '        "class": Option::<&str>::None,' \
  --expect-fail owner_probe_win::tests::the_page_reads_this_machines_class_and_aibds_own_words \
  -- cargo test -p aibd --locked --lib owner_probe_win::tests::the_page_reads_this_machines_class_and_aibds_own_words
