#!/usr/bin/env bash
# 3e53b REVIEW-winfirst M1: the Passport pairing key asks for the gesture every time (no PIN /
# gesture cache right after an answer). The rule and the source checks run on every receipt
# machine. Each knife, put in, must turn its own test red. Run from the repo root.
set -eu
# GR1 a key whose gesture flag reads other than 1 passes
python3 -I scripts/knife.py crates/aib-sys/src/hello.rs \
  '        Ok(1) => Ok(()),' \
  '        Ok(_) => Ok(()), // knife' \
  --expect-fail a_key_that_does_not_ask_every_time_is_weak \
  -- cargo test -p aib-sys --locked --lib a_key_that_does_not_ask_every_time_is_weak
# GR2 the probe no longer reads the gesture flag
python3 -I scripts/knife.py crates/aib-sys/src/hello_ncrypt.rs \
  '    gesture_required(read_u32(&k, NCRYPT_PIN_CACHE_IS_GESTURE_REQUIRED_PROPERTY))?;' \
  '    let _ = (gesture_required, &k); // knife' \
  --expect-fail the_passport_key_is_made_and_probed_with_the_gesture_required \
  -- cargo test -p aib-sys --locked --lib the_passport_key_is_made_and_probed_with_the_gesture_required
# GR3 the key is made without the gesture flag
python3 -I scripts/knife.py crates/aib-sys/src/hello_ncrypt.rs \
  '            NCRYPT_PIN_CACHE_IS_GESTURE_REQUIRED_PROPERTY,' \
  '            NCRYPT_USE_CONTEXT_PROPERTY, // knife' \
  --expect-fail the_passport_key_is_made_and_probed_with_the_gesture_required \
  -- cargo test -p aib-sys --locked --lib the_passport_key_is_made_and_probed_with_the_gesture_required
