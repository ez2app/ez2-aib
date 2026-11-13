#!/usr/bin/env bash
# aib-sys presence_macos knives (ebb36 11-13, PLAN-t135b): each knife takes one check
# away and must turn its own test red. macOS with a Secure Enclave only: the tests named
# with "SE" below are #[ignore]d in CI and run here with --include-ignored (keys without
# an owner ACL; an owner key cannot be made over ssh). Run from the repo root.
set -eu
# FILE is written out (not a variable): aib-xtask knife-anchors reads every
# scripts/knife.py call and only understands plain text and $1..$9.
ks() { python3 -I scripts/knife.py crates/aib-sys/src/presence_macos.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-sys --locked --lib "$3" -- --include-ignored; }
# KS1 (SE) a key that signs without asking passes the probe
ks '        Ok(_) => Err(SeError::WeakKey),' \
   '        Ok(_) => Ok(()), // knife' \
   probe_calls_a_key_without_an_owner_acl_weak
# KS2 the probe lets an unknown failure through (here: the key does not restore)
ks '    let key = restore(blob, Some(&*ctx)).map_err(|_| SeError::KeyProbeFailed)?;' \
   '    let Ok(key) = restore(blob, Some(&*ctx)) else { return Ok(()) }; // knife' \
   a_blob_that_gives_no_key_fails_the_probe_and_has_no_public_key
# KS3 app cancel is not a cancel
ks '        ("com.apple.LocalAuthentication", -2 | -3 | -4 | -9) => SeError::Cancel,' \
   '        ("com.apple.LocalAuthentication", -2 | -3 | -4) => SeError::Cancel, // knife' \
   errors_map_like_the_1x_helper
# KS4 an unknown LocalAuthentication error is not a refusal
ks '        ("com.apple.LocalAuthentication", _) => SeError::AuthFailed,' \
   '        ("com.apple.LocalAuthentication", _) => SeError::KeyProbeFailed, // knife' \
   errors_map_like_the_1x_helper
# KS5 an empty reason is signed
ks '    if reason.trim().is_empty() {' \
   '    if false && reason.trim().is_empty() { // knife' \
   bad_arguments_are_refused_before_the_secure_enclave_is_asked
# KS6 no size cap on what is signed
ks '    if msg.is_empty() || msg.len() > SIGN_MAX {' \
   '    if msg.is_empty() { // knife' \
   bad_arguments_are_refused_before_the_secure_enclave_is_asked
# KS7 (SE) every signature verifies
ks '    Ok(ok != 0)' \
   '    Ok(ok != 0 || true) // knife' \
   a_signature_verifies_only_for_the_signed_message
# KS8 errSecAuthFailed falls to se_error (c477a T135-b L1)
ks '        ("NSOSStatusErrorDomain", -25293) => SeError::AuthFailed,' \
   '        ("NSOSStatusErrorDomain", -25292) => SeError::AuthFailed, // knife' \
   errors_map_like_the_1x_helper
echo "aib-sys presence knives: all bit"
