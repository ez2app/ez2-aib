#!/usr/bin/env bash
# OTE1–OTE5 (3e53b 11-23; 9918e 派工, FINDING-reporeg-ownercheck-ebab8-1123): aibd install never
# writes AIBD_OWNER_TOUCH=1 beside a pairing other than touchid (owner_ok.rs needs both), keeps
# the old plist's AIBD_* keys, and says when the fingerprint key is still to be made.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
T=install::tests::install_writes_owner_touch_only_with_touchid_and_keeps_the_aibd_keys
U=install::tests::owner_touch_needs_the_touchid_pairing_or_is_refused
# OTE1 the check is not called: the weak pairing named next to the switch is written
python3 -I scripts/knife.py crates/aibd/src/install.rs '        if owner_touch_env(&mut env, named, h.os)? {' '        if false && owner_touch_env(&mut env, named, h.os)? { // knife' \
  --expect-fail "$T" \
  -- cargo test -p aibd --locked --lib "$T"
# OTE2 a pairing named as something else is raised instead of refused
python3 -I scripts/knife.py crates/aibd/src/install.rs 'other if pairing_named => Err(format!(' 'other if pairing_named && false => Err(format!( // knife' \
  --expect-fail "$U" \
  -- cargo test -p aibd --locked --lib "$U"
# OTE3 a platform without fingerprint pairing gets the switch anyway
python3 -I scripts/knife.py crates/aibd/src/install.rs '    if os != "macos" {' '    if false { // knife' \
  --expect-fail "$U" \
  -- cargo test -p aibd --locked --lib "$U"
# OTE4 a reinstall drops the old plist's keys (AIBD_MAX_SESSIONS, the switch, the pairing)
python3 -I scripts/knife.py crates/aibd/src/install.rs '                env.insert(k.clone(), v.clone());' '                let _ = (k, v); // knife' \
  --expect-fail "$T" \
  -- cargo test -p aibd --locked --lib "$T"
# OTE5 no word that the fingerprint key is still to be made
python3 -I scripts/knife.py crates/aibd/src/install.rs '    (env.get(PAIRING_ENV).map(String::as_str) == Some(PAIRING_TOUCHID)' '    (false // knife' \
  --expect-fail "$T" \
  -- cargo test -p aibd --locked --lib "$T"
