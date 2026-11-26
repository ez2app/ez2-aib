#!/usr/bin/env bash
# aib-knives: os=unix
# OTE1–OTE5 (3e53b 11-23; 9918e 派工, FINDING-reporeg-ownercheck-ebab8-1123): aibd install never
# writes AIBD_OWNER_TOUCH=1 beside a pairing other than touchid (owner_ok.rs needs both), keeps
# the old plist's AIBD_* keys, and makes or checks the fingerprint key itself (11-26 第四包).
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# OTE1 the check is not called: the weak pairing named next to the switch is written
python3 -I scripts/knife.py crates/aibd/src/install.rs '        if owner_touch_env(&mut env, named, h.os)? {' '        if false && owner_touch_env(&mut env, named, h.os)? { // knife' \
  --expect-fail install::tests::install_writes_owner_touch_only_with_touchid_and_keeps_the_aibd_keys \
  -- cargo test -p aibd --locked --lib install::tests::install_writes_owner_touch_only_with_touchid_and_keeps_the_aibd_keys
# OTE2 a pairing named as something else is raised instead of refused
python3 -I scripts/knife.py crates/aibd/src/install.rs 'other if pairing_named => Err(format!(' 'other if pairing_named && false => Err(format!( // knife' \
  --expect-fail install::tests::owner_touch_needs_the_touchid_pairing_or_is_refused \
  -- cargo test -p aibd --locked --lib install::tests::owner_touch_needs_the_touchid_pairing_or_is_refused
# OTE3 a platform without fingerprint pairing gets the switch anyway
python3 -I scripts/knife.py crates/aibd/src/install.rs '    if os != "macos" {' '    if false && os != "macos" { // knife (os stays used: -D warnings, 90856 TRIAGE-890 #2)' \
  --expect-fail install::tests::owner_touch_needs_the_touchid_pairing_or_is_refused \
  -- cargo test -p aibd --locked --lib install::tests::owner_touch_needs_the_touchid_pairing_or_is_refused
# OTE4 a reinstall drops the old plist's keys (AIBD_MAX_SESSIONS, the switch, the pairing)
python3 -I scripts/knife.py crates/aibd/src/install.rs '                env.insert(k.to_owned(), v.to_owned());' '                let _ = (k, v); // knife' \
  --expect-fail install::tests::install_writes_owner_touch_only_with_touchid_and_keeps_the_aibd_keys \
  -- cargo test -p aibd --locked --lib install::tests::install_writes_owner_touch_only_with_touchid_and_keeps_the_aibd_keys
# OTE5 the fingerprint pairing's key step is skipped (ebab8 11-26 第四包: install makes or checks the key itself)
python3 -I scripts/knife.py crates/aibd/src/install.rs '    } else if wants_key {' '    } else if false && wants_key { // knife' \
  --expect-fail install::tests::install_writes_owner_touch_only_with_touchid_and_keeps_the_aibd_keys \
  -- cargo test -p aibd --locked --lib install::tests::install_writes_owner_touch_only_with_touchid_and_keeps_the_aibd_keys
