#!/usr/bin/env bash
# aib-knives: os=unix
# The "aib-sys hello knives" step of aibd.yml's rust job, moved here word for word (RULES §21; 519d0 11-16 23:50:
# new knives live in crates/<crate>/knives/*.sh). The crate knives step (crates/aib-knife/run-knives.sh rust)
# runs it; os=unix keeps it off the Windows cell as the old step did. aib-xtask knife-anchors reads
# crates/*/knives/*.sh. Run from the repo root.
#
# S102 W1a (56cda 11-16, DESIGN-S102-windows-hello-56cda-1115 r1): Windows Hello rules that need no prompt,
# plain Rust tested on every platform. Each knife must turn its own test red.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
wh() { python3 -I scripts/knife.py crates/aib-sys/src/hello.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-sys --locked --lib "hello::tests::$3"; }
# WH1 the owner pressing cancel (or the prompt timing out) is read as something other than cancel
wh '        | hr::SCARD_W_CANCELLED_BY_USER => HelloError::Cancel,' \
   '        | hr::SCARD_W_CANCELLED_BY_USER => HelloError::Os(code), // knife' \
   cancel_and_timeout_are_cancel_and_unknown_codes_are_errors
# WH5 a key that signs without the owner is taken as good
wh '        0 => Err(HelloError::WeakKey),' '        0 => Ok(()), // knife' \
   a_key_that_signs_silently_is_weak
# WH7 (a9136 S83 r1) aibd in session 0 (started over ssh) still asks for Hello
wh '        Some(s) if s != 0 && console != NO_CONSOLE && s == console => Ok(()),' \
   '        Some(s) if console != NO_CONSOLE && s == console => Ok(()), // knife' \
   only_the_active_console_session_may_ask_for_hello
# WH8 a needless leading zero in a DER integer is accepted (two encodings of one signature)
wh '    if len > 1 && v[0] == 0 && v[1] & 0x80 == 0 {' '    if len > 1 && v[0] == 0 && v[1] & 0x80 == 0 && false { // knife' \
   signatures_go_between_raw_and_der_exactly
# WH8b (5d33a ca33269b L1) a negative DER integer is taken as the same r as its padded form
wh '    if v[0] & 0x80 != 0 {' '    if v[0] & 0x80 != 0 && false { // knife' \
   signatures_go_between_raw_and_der_exactly
# WH9 a public key blob with the wrong magic is read as a P-256 key
wh '    if word(0) != ECS1 || word(4) != 32 {' '    if word(4) != 32 { // knife' \
   public_keys_go_between_the_ncrypt_blob_and_x963
# WH10 prompt text with a NUL goes through (the owner sees it cut short)
wh "    if reason.contains('\\0') {" "    if reason.contains('\\0') && false { // knife" \
   the_prompt_text_is_whole_utf16_or_refused
