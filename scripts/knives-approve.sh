#!/usr/bin/env bash
# aib-approve knives (ebb36 11-13, PLAN-b2 T135-a): each knife takes one check away
# and must turn its own test red. Run from the repo root.
set -eu
# FILE is written out (not a variable): aib-xtask knife-anchors reads every
# scripts/knife.py call and only understands plain text and $1..$9.
kc() { python3 -I scripts/knife.py crates/aib-approve/src/category.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-approve --locked --test policy "$3"; }
kt() { python3 -I scripts/knife.py crates/aib-approve/src/text.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-approve --locked --test policy "$3"; }
kp() { python3 -I scripts/knife.py crates/aib-approve/src/proof.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-approve --locked --test policy "$3"; }
kh() { python3 -I scripts/knife.py crates/aib-approve/src/protocol.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-approve --locked --test policy "$3"; }
# KA1 command keeps its 1.x 3 h window
kc '            Category::Outbound => 3 * H,' \
   '            Category::Outbound | Category::Command => 3 * H, // knife' \
   command_is_risk_3_in_2_0_and_loses_its_window
# KA2 the window takes the larger of category and cap
kc '    cat.max_window_s().min(WINDOW_CAP_S)' \
   '    cat.max_window_s().max(WINDOW_CAP_S) // knife' \
   high_risk_categories_never_get_a_window
# KA3 no cap
kc '    cat.max_window_s().min(WINDOW_CAP_S)' \
   '    cat.max_window_s() // knife' \
   no_window_is_longer_than_the_cap
# KA4 an unknown category falls to a low-risk one
kc '            .unwrap_or(Category::Unregistered)' \
   '            .unwrap_or(Category::Command) // knife' \
   an_unknown_category_is_unregistered_and_needs_a_hardware_signature
# KA5 every category accepts presence only
kc '            _ => ProofLevel::HardwareSigned,' \
   '            _ => ProofLevel::PresenceOnly, // knife' \
   only_seat_input_outbound_and_command_accept_presence_only
# KT1 zero-width characters pass
kt '        | 0x200b..=0x200f                   // zero width, LRM / RLM' \
   '        | 0x200c..=0x200f /* knife */' \
   invisible_and_control_characters_are_refused_not_cleaned
# KT2 bad characters are not checked at all
kt '        if is_bad_char(c) {' \
   '        if false && is_bad_char(c) { // knife' \
   invisible_and_control_characters_are_refused_not_cleaned
# KT3 too long passes
kt '    if len > max {' \
   '    if false && len > max { // knife' \
   too_long_is_refused_never_cut
# KP1 the nonce is never consumed
kp '    if !nonces.consume(&proof.binding.nonce) {' \
   '    if false && !nonces.consume(&proof.binding.nonce) { // knife' \
   a_signed_proof_for_exactly_this_action_is_accepted_once
# KP2 a proof for another action is accepted
kp '    if let Some(field) = proof.binding.first_difference(expect) {' \
   '    if let Some(field) = proof.binding.first_difference(expect).filter(|_| false) { // knife' \
   a_proof_for_anything_else_is_refused
# KP3 a weaker proof is accepted
kp '    if proof.level < need {' \
   '    if false && proof.level < need { // knife' \
   presence_only_is_too_weak_for_high_risk_and_enough_for_command
# KP4 a proof is still good at its expiry second
kp '    if now >= proof.binding.expiry {' \
   '    if now > proof.binding.expiry { // knife' \
   expired_and_future_proofs_are_refused
# KP5 a proof from the future is accepted
kp '    if proof.binding.issued > now.saturating_add(ISSUE_SKEW_S) {' \
   '    if false && proof.binding.issued > now.saturating_add(ISSUE_SKEW_S) { // knife' \
   expired_and_future_proofs_are_refused
# KP6 an untrusted key is accepted
kp '            None => return Err(Refusal::UntrustedKey),' \
   '            None => {} // knife' \
   hardware_signed_needs_a_signature_by_a_trusted_key
# KP7 a bad signature is accepted
kp '            Some(false) => return Err(Refusal::BadSig),' \
   '            Some(false) => {} // knife' \
   a_refused_proof_does_not_use_up_the_nonce
# KP8 a presence-only proof is read from outside
kp '            "presence_only" => return Err(Refusal::PresenceNotTransferable),' \
   '            "presence_only" => {} // knife' \
   a_presence_only_proof_cannot_arrive_from_outside
# KP9 `=` inside a field is allowed (two readings of the canonical form)
kp '    if v.is_empty() || v.chars().count() > FIELD_MAX || v.contains('"'"'='"'"') {' \
   '    if v.is_empty() || v.chars().count() > FIELD_MAX { // knife' \
   canonical_form_has_one_reading
# KH1 any rc 3 counts as "the person said no", whatever the code
kh '            Some(3) if in_set(RC3_CODES) => HelperOutcome::NotApproved(code.to_owned()),' \
   '            Some(3) => HelperOutcome::NotApproved(code.to_owned()), // knife' \
   helper_exit_codes_map_to_classes_and_mismatches_are_broken
# KH2 an unreadable exit counts as approved
kh '            _ => HelperOutcome::Broken("helper_failed".to_owned()),' \
   '            _ => HelperOutcome::Ok, // knife' \
   helper_exit_codes_map_to_classes_and_mismatches_are_broken
echo "aib-approve knives: all bit"
