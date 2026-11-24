#!/usr/bin/env bash
# aib-knives: os=unix
# a seat's program gets only stdio (5d33a PREREAD-stdioonly L1)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
# SS1 the session spawn does not mark the rest close-on-exec
python3 -I scripts/knife.py crates/aibd/src/platform/tree.rs \
  '    aib_sys::stdio_only::stdio_only(&mut c);' \
  '    let _ = &mut c; // knife' \
  --expect-fail a_session_gets_only_its_stdio \
  -- cargo test -p aibd --locked --test tree a_session_gets_only_its_stdio
