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
# ---- S10-3b-2 (W-1b; 3e53b READ-winhello-wiring W7): pair-key init leaves nothing on failure
# WHP1 a key that failed the start-up check is not deleted
python3 -I scripts/knife.py crates/aibd/src/touch.rs \
  '        delete(&blob);' \
  '        delete(&blob[..0]); // knife: the wrong key deleted, the made one kept' \
  --expect-fail a_pair_key_that_fails_leaves_nothing \
  -- cargo test -p aibd --locked --lib a_pair_key_that_fails_leaves_nothing
# WHP2 a failed run keeps the key file it wrote
python3 -I scripts/knife.py crates/aibd/src/touch.rs \
  '        let _ = std::fs::remove_file(dir.join("key.blob"));' \
  '        let _ = dir.join("key.blob"); // knife: file kept' \
  --expect-fail a_pair_key_that_fails_leaves_nothing \
  -- cargo test -p aibd --locked --lib a_pair_key_that_fails_leaves_nothing
# WHP3 existing key files are not checked first (the run goes on and cleans up the owner's key)
python3 -I scripts/knife.py crates/aibd/src/touch.rs \
  '    if dir.join("key.blob").exists() || dir.join("key.pub").exists() {' \
  '    if dir.join("key.blob").exists() && dir.join("key.pub").exists() && false { // knife' \
  --expect-fail a_pair_key_that_fails_leaves_nothing \
  -- cargo test -p aibd --locked --lib a_pair_key_that_fails_leaves_nothing
