#!/usr/bin/env bash
# clean-stop knives (0eed7 VERIFY-humanpath-0eed7-d95f4ab3f L1 r1): each rule loosened must turn its own test red.
# Run by crates/aib-knife/run-knives.sh (macOS); from the repo root; -eo pipefail like GitHub's `shell: bash`.
#
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
sc() { python3 -I scripts/knife.py crates/aibd/src/platform/session.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aibd --locked --bin aibd "ctl::tests::$3"; }
# SC1 an unreadable candidate that is listed is let go
sc '        && c["unreadable"].as_array().is_none_or(Vec::is_empty)' '        && true' \
   a_stop_with_nothing_left_seen_is_done_and_anything_left_is_not
# SC3 unreadable candidates past the list are let go
sc '        && c["unreadable_more"].as_u64().unwrap_or(0) == 0' '        && true' \
   a_stop_with_nothing_left_seen_is_done_and_anything_left_is_not
