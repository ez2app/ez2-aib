#!/usr/bin/env bash
# S10-3b-1 (PLAN-ap26-windows-first-c2f0b-1125 W-1a; 3e53b READ-winhello-wiring W5): on Windows
# the desktop-session gate comes before the pairing key is read. The order is written for every
# platform, so these knives bite on every receipt machine. Each knife, put in, must turn its own
# test red. Run from the repo root.
set -eu
# WHS1 outside the desktop session the key is read and checked anyway
python3 -I scripts/knife.py crates/aibd/src/touch.rs \
  '    session?;' \
  '    let _ = session.is_ok(); // knife: no gate' \
  --expect-fail outside_the_desktop_session_the_key_is_never_read \
  -- cargo test -p aibd --locked --lib outside_the_desktop_session_the_key_is_never_read
# WHS2 the start-up check is skipped after the gate (a weak key is taken)
python3 -I scripts/knife.py crates/aibd/src/touch.rs \
  '    Ok(Arc::new(KeyPresence::new(dev, key)?))' \
  '    Ok(Arc::new(KeyPresence { dev, key })) // knife: no check' \
  --expect-fail outside_the_desktop_session_the_key_is_never_read \
  -- cargo test -p aibd --locked --lib outside_the_desktop_session_the_key_is_never_read
