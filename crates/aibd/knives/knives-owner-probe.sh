#!/usr/bin/env bash
# OP1–OP3: aibd ctl owner-check probe only reads what LocalAuthentication says — it never asks the owner, and its first reading keeps "no biometry" apart from "a fingerprint" and "not enrolled" (DESIGN-persona-propose-osverify-c2f0b-1123 r3 §8; f163c REVIEW r3 N1)
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
op() { python3 -I scripts/knife.py crates/aibd/src/owner_probe.rs "$1" "$2" --expect-fail "owner_probe::tests::$3" \
        -- cargo test -p aibd --locked --lib "owner_probe::tests::$3"; }
# OP1 the probe asks the owner (evaluatePolicy)
op '    let ty = d.biometry_type();' \
   '    let ty = { let _ = d.evaluate(); d.biometry_type() };' \
   a_probe_never_asks_the_owner
# OP2 no biometry reads as a fingerprint
op '        (0, _, Ok(())) => Class::PasswordOnly,' \
   '        (0, _, Ok(())) => Class::Fingerprint,' \
   first_readings
# OP3 not enrolled reads as not usable now
op '        (_, Err(NOT_ENROLLED), Ok(())) => Class::NotEnrolled,' \
   '        (_, Err(NOT_ENROLLED), Ok(())) => Class::NotNow,' \
   first_readings
