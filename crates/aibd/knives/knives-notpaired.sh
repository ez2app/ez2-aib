#!/usr/bin/env bash
# S10-2b-4 (DECISION-andymini-password-c2f0b-1125): a Mac that never paired a Touch ID keyboard
# (-12) uses the login password; a paired keyboard that is away (-13) stays not-now. Pure rule,
# bites on every receipt machine. Each knife, put in, must turn its own test red. Run from the
# repo root.
set -eu
# NP1 a never-paired Mac is not given the login password
python3 -I scripts/knife.py crates/aibd/src/owner_probe.rs \
  '        (_, Err(NOT_PAIRED), Ok(())) => Class::PasswordOnly,' \
  '        (_, Err(NOT_PAIRED), Ok(())) => Class::NotNow, // knife' \
  --expect-fail never_paired_is_the_login_password_and_away_is_not_now \
  -- cargo test -p aibd --locked --lib never_paired_is_the_login_password_and_away_is_not_now
# NP2 a paired keyboard that is away gets the login password too
python3 -I scripts/knife.py crates/aibd/src/owner_probe.rs \
  '        (_, Err(NOT_PAIRED), Ok(())) => Class::PasswordOnly,' \
  '        (_, Err(NOT_PAIRED | DISCONNECTED), Ok(())) => Class::PasswordOnly, // knife' \
  --expect-fail never_paired_is_the_login_password_and_away_is_not_now \
  -- cargo test -p aibd --locked --lib never_paired_is_the_login_password_and_away_is_not_now
