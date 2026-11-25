#!/usr/bin/env bash
# aib-knives: os=unix
# MS1 (7b81e 11-20; ORDER-webonly-today 3「同時開九席」): the seats run in the preview, so the
# parent's AIBD_MAX_SESSIONS goes over to it; dropped, the preview stays at the memory default (4).
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# MS1 the running limit is not handed to the preview
python3 -I scripts/knife.py crates/aibd/src/preview.rs '    "AIBD_MAX_SESSIONS",' '    "AIBD_NO_SUCH_SETTING", // knife' \
  --expect-fail preview::tests::the_preview_gets_its_own_home_data_and_port_and_no_other_aibd_setting \
  -- cargo test -p aibd --locked --lib preview::tests::the_preview_gets_its_own_home_data_and_port_and_no_other_aibd_setting
