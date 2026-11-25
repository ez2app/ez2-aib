#!/usr/bin/env bash
# OP1–OP3: aibd ctl owner-check probe only reads what LocalAuthentication says — it never asks the owner, and its first reading keeps "no biometry" apart from "a fingerprint" and "not enrolled" (DESIGN-persona-propose-osverify-c2f0b-1123 r3 §8; f163c REVIEW r3 N1)
# OP4–OP8 (S10-2b-1, DESIGN-s10-2b-nofinger-c2f0b-1125 r1 §4a §7a): the login-password pairing key only where no Touch ID is reported; a Touch ID Mac signs through the finger-only window; Touch ID not usable now is a refusal, never the password
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
tt() { python3 -I scripts/knife.py crates/aibd/src/touch.rs "$1" "$2" --expect-fail "touch::tests::$3" \
        -- cargo test -p aibd --locked --lib "touch::tests::$3"; }
# OP4 Touch ID not usable now makes the login-password key
op '        Class::NotNow => Err("owner_check_finger_not_now"),' \
   '        Class::NotNow => Ok(KeyKind::LoginPassword),' \
   the_password_key_only_where_there_is_no_touch_id
# OP5 a Touch ID Mac gets the login-password key
op '        Class::Fingerprint => Ok(KeyKind::Finger),' \
   '        Class::Fingerprint => Ok(KeyKind::LoginPassword),' \
   the_password_key_only_where_there_is_no_touch_id
# OP6 a Touch ID Mac signs through the key's own window (a swapped password key takes the password)
op '        KeyKind::Finger => Route::FingerOnly,' \
   '        KeyKind::Finger => Route::AsTheKeyAllows,' \
   a_touch_id_mac_signs_with_the_finger_only
# OP7 the finger-only route opens the key's own window
tt '        Route::FingerOnly => finger_only(),' \
   '        Route::FingerOnly => { let _ = finger_only; as_the_key_allows() }' \
   a_signature_goes_the_way_the_owner_check_reads_now
# OP8 Touch ID not usable now falls back to the key's own window (the password)
tt '    match crate::owner_probe::route(now)? {' \
   '    match crate::owner_probe::route(now).unwrap_or(Route::AsTheKeyAllows) {' \
   a_signature_goes_the_way_the_owner_check_reads_now
