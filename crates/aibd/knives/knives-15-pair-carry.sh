#!/usr/bin/env bash
# aib-knives: os=unix
# S89-pair-carry knives PC-K1–PC-K14 (043dd 11-18; DECISION-council-pair-persist-519d0-1118 §4,
# 5d33a CHECKLIST-workspace-wave3 F): a confirmed page pairing survives an update once. Each rule,
# broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
pl() { python3 -I scripts/knife.py crates/aibd/src/pairing.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "pairing::tests::$3"; }
pc() { python3 -I scripts/knife.py crates/aibd/src/pair_carry.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test pair_carry "$3"; }
pu() { python3 -I scripts/knife.py crates/aibd/src/pair_carry.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "pair_carry::tests::$3"; }
si() { python3 -I scripts/knife.py crates/aibd/src/seat_input.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test pair_carry "$3"; }
sv() { python3 -I scripts/knife.py crates/aibd/src/server.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test pair_carry "$3"; }
# PC-K1 a credential carried once is written again (5d33a F2)
pl '            if p.carry.is_some() || p.state(now) != PairState::Confirmed {' \
   '            if p.state(now) != PairState::Confirmed { // knife' \
   a_carried_credential_is_confirmed_marked_and_not_carried_again
# PC-K2 a revoked / unconfirmed / expired credential is written (DECISION §4: revoke ⇒ 401)
pl '            if p.carry.is_some() || p.state(now) != PairState::Confirmed {' \
   '            if false && p.carry.is_some() { // knife' \
   carry_out_takes_confirmed_credentials_only
# PC-K3 a carried credential nobody uses lives past the 10-minute window
pl '            return if now >= c.ends || (!c.used && now >= c.use_by) {' \
   '            return if now >= c.ends { // knife' \
   a_carried_credential_must_be_used_within_the_window
# PC-K4 using it in time does not keep it (the window ends it anyway)
pl '                    c.used = true;' '                    let _ = c; // knife' \
   a_carried_credential_must_be_used_within_the_window
# PC-K5 the user-page mark is lost on the way (5d33a F3)
pl '                    user_page: c.user_page,' '                    user_page: false, // knife' \
   a_carried_credential_is_confirmed_marked_and_not_carried_again
# PC-K6 a carried pairing types to a dgrs seat (DECISION §2)
si '    if page.carried && meta.mode.as_deref() != Some("safe") {' \
   '    if false && page.carried && meta.mode.as_deref() != Some("safe") { // knife' \
   an_update_carries_the_page_once_and_for_safe_seats_only
# PC-K7 a restart with the same build writes the carry file (DECISION §4: manual restart ⇒ 401)
pc '    if on_disk == started_exe {' '    if false && on_disk == started_exe { // knife' \
   the_carry_file_never_holds_the_credential
# PC-K8 the carry file stays after the start (taken twice)
pc '    let _ = std::fs::remove_file(&p);' '    let _ = &p; // knife' \
   an_update_carries_the_page_once_and_for_safe_seats_only
# PC-K9 a carry file older than 10 minutes is taken
pu '    if now.saturating_sub(at) > MAX_AGE {' '    if false && now.saturating_sub(at) > MAX_AGE { // knife' \
   old_future_other_build_and_unknown_version_are_not_taken
# PC-K10 a carry file for another build (or put back) is taken (5d33a F2)
pc '    if new != running_exe || old == running_exe {' \
   '    if false && (new != running_exe || old == running_exe) { // knife' \
   an_old_or_foreign_carry_file_is_removed_and_not_taken
# PC-K11 a used carry file put back is taken again: the claim is not asked (0eed7 M1 on 73d1acf2)
pc '    let list: Vec<CarriedCred> = list.into_iter().filter(|c| claim(&c.digest)).collect();' \
   '    let list: Vec<CarriedCred> = list.into_iter().filter(|c| c.left > 0 || claim(&c.digest)).collect(); // knife' \
   a_used_carry_file_put_back_is_not_taken_again
# PC-K12 the same, the claim answers yes whatever the database says (0eed7 M1)
sv '                    .is_ok()' '                    .map_or(true, |_| true) // knife' \
   a_used_carry_file_put_back_is_not_taken_again
# PC-K13 a carry confirmed by another channel is taken (5d33a batch-1 L1)
pu '    if channel != pairing.channel().as_str() {' '    if false && channel != pairing.channel().as_str() { // knife' \
   a_carry_confirmed_by_another_channel_is_not_taken
# PC-K14 the carry file does not say which channel confirmed it (5d33a L1)
pc '    doc["channel"] = Value::from(pairing.channel().as_str());' '    doc["channel"] = Value::from("off"); // knife' \
   the_carry_file_never_holds_the_credential
