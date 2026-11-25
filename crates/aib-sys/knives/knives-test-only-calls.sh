#!/usr/bin/env bash
# AP26 W-4 (PRE-READ-w4-winsys-features-c2f0b-1125; 3e53b condition 2): screen capture, synthetic
# input and CredUI are named only by the test-only measurement file. Each knife, put in, must turn
# its own test red. Run from the repo root.
set -eu
# TG1 a capture / input name in non-test code goes unnoticed
python3 -I scripts/knife.py crates/aib-sys/src/hello.rs \
  '/// S10-3b-2: `NCRYPT_IMPL_HARDWARE_FLAG` in `NCRYPT_IMPL_TYPE_PROPERTY`.' \
  '/// S10-3b-2: `NCRYPT_IMPL_HARDWARE_FLAG` in `NCRYPT_IMPL_TYPE_PROPERTY` (not SendInput).' \
  --expect-fail only_the_measurement_tests_name_capture_input_and_credui_calls \
  -- cargo test -p aib-sys --locked --lib only_the_measurement_tests_name_capture_input_and_credui_calls
# TG2 the measurement file is compiled into the product
python3 -I scripts/knife.py crates/aib-sys/src/lib.rs \
  'mod hello_measure;' \
  'pub mod hello_measure;' \
  --expect-fail only_the_measurement_tests_name_capture_input_and_credui_calls \
  -- cargo test -p aib-sys --locked --lib only_the_measurement_tests_name_capture_input_and_credui_calls
