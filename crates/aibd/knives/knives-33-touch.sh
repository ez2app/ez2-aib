#!/usr/bin/env bash
# aib-knives: os=macos
# Fingerprint pairing knives TK1–TK17 (7b81e 11-19; DESIGN-pair-touchid-7b81e-1119 r3, 5d33a CHECK-pair-touchid-5d33a-1119,
# BOUNDARY-webonly r5, REVIEW-touchid-7b81e-5d33a-8ec571dc M1 / L1): with AIBD_PAIRING=touchid only aibd's own fingerprint
# window confirms a page; the key must ask for the owner, be the stored one and be this account's; the window says only
# aibd's values; a carried pairing goes up in place only by the finger, within limits; windows come one at a time, cool
# after a no and are limited over the whole aibd. Each, broken, turns its own test red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# TK1 the terminal y confirms a fingerprint pairing
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '        if self.channel == ConfirmChannel::TouchId {' \
  '        if self.channel == ConfirmChannel::TouchId && false { // knife' \
  --expect-fail pairing::tests::touchid_confirms_only_by_the_owners_fingerprint \
  -- cargo test -p aibd --locked --lib pairing::tests::touchid_confirms_only_by_the_owners_fingerprint
# TK2 the box on the page confirms a fingerprint pairing
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '        let confirm_in_page = confirm_in_page && self.channel != ConfirmChannel::TouchId;' \
  '        let confirm_in_page = confirm_in_page; // knife' \
  --expect-fail pairing::tests::touchid_confirms_only_by_the_owners_fingerprint \
  -- cargo test -p aibd --locked --lib pairing::tests::touchid_confirms_only_by_the_owners_fingerprint
# TK3 a cancelled fingerprint still confirms
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '        if let Err(e) = asked {' \
  '        if let Err(e) = asked.or(Ok::<(), &'"'"'static str>(())) { // knife' \
  --expect-fail pairing::tests::no_key_or_a_cancelled_fingerprint_revokes_the_pairing \
  -- cargo test -p aibd --locked --lib pairing::tests::no_key_or_a_cancelled_fingerprint_revokes_the_pairing
# TK4 a key that signs without the owner is taken
python3 -I scripts/knife.py crates/aibd/src/touch.rs \
  '    dev.probe(&k.blob).map_err(|_| "pair_key_weak")' \
  '    dev.probe(&k.blob).or(Ok(())) // knife' \
  --expect-fail touch::tests::a_key_that_signs_without_the_owner_or_is_not_the_stored_one_is_refused \
  -- cargo test -p aibd --locked --lib touch::tests::a_key_that_signs_without_the_owner_or_is_not_the_stored_one_is_refused
# TK5 a signature that does not check out is a yes
python3 -I scripts/knife.py crates/aibd/src/touch.rs \
  '        if !self.dev.verify(&self.key.public, msg, &sig) {' \
  '        if !self.dev.verify(&self.key.public, msg, &sig) && false { // knife' \
  --expect-fail touch::tests::only_a_signature_that_checks_out_is_a_yes \
  -- cargo test -p aibd --locked --lib touch::tests::only_a_signature_that_checks_out_is_a_yes
# TK6 the window carries a request's text (5d33a L2)
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '        let why = crate::touch::window(' \
  '        let why = nonce_hex.clone() + &crate::touch::window( // knife' \
  --expect-fail pairing::tests::touchid_confirms_only_by_the_owners_fingerprint \
  -- cargo test -p aibd --locked --lib pairing::tests::touchid_confirms_only_by_the_owners_fingerprint
# TK7 a key file others may write is taken
python3 -I scripts/knife.py crates/aibd/src/touch.rs \
  '        if m.uid() != nix::unistd::getuid().as_raw() || m.mode() & 0o022 != 0 {' \
  '        if m.uid() != nix::unistd::getuid().as_raw() { // knife' \
  --expect-fail touch::tests::key_files_must_be_this_accounts_and_not_writable_by_others \
  -- cargo test -p aibd --locked --lib touch::tests::key_files_must_be_this_accounts_and_not_writable_by_others
# TK8 more than 3 re-confirm windows in 10 minutes (5d33a §6-2)
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '            if short >= RECONFIRM_SHORT_MAX || asks.len() >= RECONFIRM_LONG_MAX {' \
  '            if asks.len() >= RECONFIRM_LONG_MAX { let _ = short; // knife' \
  --expect-fail pairing::tests::reconfirm_windows_are_limited_per_carried_pairing \
  -- cargo test -p aibd --locked --lib pairing::tests::reconfirm_windows_are_limited_per_carried_pairing
# TK9 more than 6 re-confirm windows in an hour (5d33a §6-2)
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '            if short >= RECONFIRM_SHORT_MAX || asks.len() >= RECONFIRM_LONG_MAX {' \
  '            if short >= RECONFIRM_SHORT_MAX { // knife' \
  --expect-fail pairing::tests::reconfirm_windows_are_limited_per_carried_pairing \
  -- cargo test -p aibd --locked --lib pairing::tests::reconfirm_windows_are_limited_per_carried_pairing
# TK10 a cancelled re-confirm still lifts the carried pairing
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '        asked?;' \
  '        let _ = asked; // knife' \
  --expect-fail pairing::tests::a_no_or_no_key_leaves_a_carried_pairing_weak \
  -- cargo test -p aibd --locked --lib pairing::tests::a_no_or_no_key_leaves_a_carried_pairing_weak
# TK11 a yes leaves the pairing weak (the page is stuck)
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '                    c.reconfirmed = true;' \
  '                    c.reconfirmed = false; // knife' \
  --expect-fail pairing::tests::a_carried_pairing_reconfirmed_by_the_finger_works_as_this_runs \
  -- cargo test -p aibd --locked --lib pairing::tests::a_carried_pairing_reconfirmed_by_the_finger_works_as_this_runs
# TK12 the re-confirm window hides that the whole page goes up (5d33a REVIEW-touchid L1)
python3 -I scripts/knife.py crates/aibd/src/touch.rs \
  '            "aibd 網頁重新確認:升級這頁到本輪完整權限,到 {until};起因:{act};換版前延續的配對,給 {to};由瀏覽器頁要求。{OWNER_CHECK_WORD}"' \
  '            "aibd 網頁重新確認:{act}{until:.0};換版前延續的配對,給 {to};由瀏覽器頁要求。{OWNER_CHECK_WORD}" // knife' \
  --expect-fail touch::tests::the_window_says_the_code_where_it_goes_and_who_asked \
  -- cargo test -p aibd --locked --lib touch::tests::the_window_says_the_code_where_it_goes_and_who_asked
# TK13 an action not on aibd's list gets a window
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '        let Some(&(_, words)) = RECONFIRM_ACTS.iter().find(|(k, _)| *k == act) else {' \
  '        let Some(&(_, words)) = RECONFIRM_ACTS.iter().find(|(k, _)| *k == act).or(Some(&("", "?"))) else { // knife' \
  --expect-fail pairing::tests::a_carried_pairing_reconfirmed_by_the_finger_works_as_this_runs \
  -- cargo test -p aibd --locked --lib pairing::tests::a_carried_pairing_reconfirmed_by_the_finger_works_as_this_runs
# TK14 a re-confirmed pairing still answers as carried
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '        let carried = p.carry.is_some_and(|c| !c.reconfirmed);' \
  '        let carried = p.carry.is_some(); // knife' \
  --expect-fail pairing::tests::a_carried_pairing_reconfirmed_by_the_finger_works_as_this_runs \
  -- cargo test -p aibd --locked --lib pairing::tests::a_carried_pairing_reconfirmed_by_the_finger_works_as_this_runs
# TK15 two fingerprint windows at once (5d33a REVIEW-touchid M1)
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '        if inner.touch_busy {' \
  '        if inner.touch_busy && false { // knife' \
  --expect-fail pairing::tests::fingerprint_windows_come_one_at_a_time_cool_after_a_no_and_are_limited \
  -- cargo test -p aibd --locked --lib pairing::tests::fingerprint_windows_come_one_at_a_time_cool_after_a_no_and_are_limited
# TK16 a window right after a no (5d33a REVIEW-touchid M1)
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '        if inner.touch_cool_until.is_some_and(|t| now < t) {' \
  '        if false && inner.touch_cool_until.is_some_and(|t| now < t) { // knife' \
  --expect-fail pairing::tests::fingerprint_windows_come_one_at_a_time_cool_after_a_no_and_are_limited \
  -- cargo test -p aibd --locked --lib pairing::tests::fingerprint_windows_come_one_at_a_time_cool_after_a_no_and_are_limited
# TK17 a redeem's windows without the 3 / 10 min, 6 / h limit (5d33a REVIEW-touchid M1)
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
  '            let full = short >= RECONFIRM_SHORT_MAX || asks.len() >= RECONFIRM_LONG_MAX;' \
  '            let full = false; let _ = short; // knife' \
  --expect-fail pairing::tests::fingerprint_windows_come_one_at_a_time_cool_after_a_no_and_are_limited \
  -- cargo test -p aibd --locked --lib pairing::tests::fingerprint_windows_come_one_at_a_time_cool_after_a_no_and_are_limited
