#!/usr/bin/env bash
# aib-knives: os=unix
# OT1 (b4297 11-21; 0eed7 VERIFY-pv6 M3, 519d0 派工): the parent's AIBD_OWNER_TOUCH goes over to
# the preview; dropped, the page's fingerprint asks in the preview say unavailable.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# OT1 the owner's fingerprint switch is not handed to the preview
python3 -I scripts/knife.py crates/aibd/src/preview.rs '    crate::owner_ok::SWITCH,' '    "AIBD_NO_SUCH_SWITCH", // knife' \
  --expect-fail preview::tests::the_owner_fingerprint_switch_goes_to_the_preview_only_from_a_parent_that_has_it \
  -- cargo test -p aibd --locked --lib preview::tests::the_owner_fingerprint_switch_goes_to_the_preview_only_from_a_parent_that_has_it
