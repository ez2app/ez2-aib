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
kl() { python3 -I scripts/knife.py crates/aib-approve/src/proof.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-approve --locked --lib "$3"; }
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
# KT1 a Mongolian free variation selector (1.x extra list) passes
kt '        | 0x180b..=0x180f' \
   '        | 0x180c..=0x180f /* knife */' \
   invisible_and_control_characters_are_refused_not_cleaned
# KT1b a no-break or ideographic space passes
kt "        G::SpaceSeparator => c != ' '," \
   '        G::SpaceSeparator => false, // knife' \
   invisible_and_control_characters_are_refused_not_cleaned
# KT1c the Unicode category table is not consulted (format, private use, unassigned pass)
kt '    match get_general_category(c) {' \
   "    match get_general_category('a') { // knife" \
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
kp '    if !nonces.consume(&expect.nonce) {' \
   '    if false && !nonces.consume(&expect.nonce) { // knife' \
   a_signed_proof_for_exactly_this_action_is_accepted_once
# KP2 a proof for another action is accepted
kp '    if let Some(field) = got.first_difference(expect) {' \
   '    if let Some(field) = got.first_difference(expect).filter(|_| false) { // knife' \
   a_proof_for_anything_else_is_refused
# KP3 a weaker proof is accepted
kl '    if have < need {' \
   '    if false && have < need { // knife' \
   presence_only_is_too_weak_for_high_risk_and_enough_for_command
# KP4 a proof is still good at its expiry second
kp '    if now >= expect.expiry {' \
   '    if now > expect.expiry { // knife' \
   expired_and_future_proofs_are_refused
# KP5 a proof from the future is accepted
kp '    if expect.issued > now.saturating_add(ISSUE_SKEW_S) {' \
   '    if false && expect.issued > now.saturating_add(ISSUE_SKEW_S) { // knife' \
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
# KP10 a proof with a later expiry than the caller allows is accepted (Codex T135-a H1)
kp '        if self.expiry != other.expiry {' \
   '        if false && self.expiry != other.expiry { // knife' \
   a_proof_for_anything_else_is_refused
# KP11 a presence check for a later expiry is accepted (unit test)
kl '        if self.expiry != other.expiry {' \
   '        if false && self.expiry != other.expiry { // knife' \
   presence_for_another_action_or_after_expiry_is_refused
# KH1 any rc 3 counts as "the person said no", whatever the code
kh '            Some(3) if in_set(RC3_CODES) => HelperOutcome::NotApproved(code.to_owned()),' \
   '            Some(3) => HelperOutcome::NotApproved(code.to_owned()), // knife' \
   helper_exit_codes_map_to_classes_and_mismatches_are_broken
# KH2 an unreadable exit counts as approved
kh '            _ => HelperOutcome::Broken("helper_failed".to_owned()),' \
   '            _ => HelperOutcome::Ok, // knife' \
   helper_exit_codes_map_to_classes_and_mismatches_are_broken
echo "aib-approve knives: all bit"
