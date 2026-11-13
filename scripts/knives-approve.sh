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
kw() { python3 -I scripts/knife.py crates/aib-approve/src/hwkey.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-approve --locked --test hwkey "$3"; }
km() { python3 -I scripts/knife.py crates/aib-approve/src/macos.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-approve --locked --lib "$3"; }
kr() { python3 -I scripts/knife.py crates/aib-approve/src/registry.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-approve --locked --test registry "$3"; }
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
# KP12 no lifetime cap: a proof signed for a year is accepted (c477a T135-a L1)
kp '    if expect.expiry - expect.issued > max_lifetime_s(expect.category) {' \
   '    if false && expect.expiry - expect.issued > max_lifetime_s(expect.category) { // knife' \
   a_proof_cannot_live_longer_than_its_category_allows
# KP13 the cap ignores the single-action limit
kp '    SINGLE_ACTION_MAX_S.max(crate::category::window_allowed_s(cat))' \
   '    (365 * 24 * 3600u64).max(crate::category::window_allowed_s(cat)) // knife' \
   a_proof_cannot_live_longer_than_its_category_allows
# KW1 a signature the registered key does not verify still becomes a proof
kw '        _ => return Err(KeyError::NotRegistered),' \
   '        _ => {} // knife' \
   a_signature_the_registered_key_does_not_verify_never_becomes_a_proof
# KW2 no owner check before signing
kw '    dev.probe(&key.blob).map_err(KeyError::Device)?;' \
   '    // knife: no probe' \
   the_owner_check_runs_before_every_signature
# KW3 a weak key is kept at enroll
kw '    dev.probe(&blob).map_err(KeyError::Device)?;' \
   '    let _ = dev.probe(&blob); // knife' \
   a_key_that_does_not_ask_for_the_owner_is_never_kept
# KW4 load does not compare the reported public key
kw '    if reported != kf.public {' \
   '    if false && reported != kf.public { // knife' \
   loading_refuses_a_key_the_device_reports_differently
# KW5 the key id is not bound to the public key
kw '        if key_id != key_id_of(&public) {' \
   '        if false && key_id != key_id_of(&public) { // knife' \
   the_key_id_is_bound_to_the_public_key
# KW6 the reason text is not checked
kw '    text::check(reason, text::SE_SIGN_MAX).map_err(KeyError::Reason)?;' \
   '    let _ = text::check(reason, text::SE_SIGN_MAX); // knife' \
   a_bad_reason_or_binding_is_refused_before_the_device_is_asked
# KW7 (macOS) the registered check ignores the key id
km '        let (_, public) = self.keys.iter().find(|(k, _)| k == key_id)?;' \
   '        let (_, public) = self.keys.iter().find(|(k, _)| k == key_id || !key_id.is_empty())?; // knife' \
   a_registered_key_verifies_only_its_own_signature
# KW8 a device error at load is taken for a format change (c477a slice 2 N1)
kw '    let reported = dev.public_key(&kf.blob).map_err(KeyError::Device)?;' \
   '    let reported = dev.public_key(&kf.blob).map_err(|_| KeyError::FormatChanged)?; // knife' \
   loading_refuses_a_key_the_device_reports_differently
# KR1 entries are not checked against the root (DECISION registry item 4a)
kr '        if !self.verify.verify(&self.root.public, &msg, &e.sig) {' \
   '        if false && !self.verify.verify(&self.root.public, &msg, &e.sig) { // knife' \
   signed_entries_count_and_a_bad_signature_does_not_exist
# KR2 an unsigned new entry is trusted (any signature passes; item 4b)
kr '        if !self.verify.verify(&self.root.public, &msg, &e.sig) {' \
   '        if e.sig.is_empty() || msg.is_empty() { // knife' \
   an_unsigned_new_entry_is_not_trusted
# KR3 a new root is trusted without the old root's signature (item 4c)
kr '            if !verify.verify(&p.public, &msg, &p.sig) {' \
   '            if false && !verify.verify(&p.public, &msg, &p.sig) { // knife' \
   a_new_root_needs_the_old_roots_signature
# KR4 the root is trusted without a probe (item 8)
kr '        match dev.probe(&root.blob) {' \
   '        match Ok::<(), &str>(()) { // knife' \
   a_root_that_signs_without_the_owner_voids_everything
# KR5 sequence numbers are not checked (an old signed entry can be replayed)
kr '        if e.seq <= self.last_seq {' \
   '        if false && e.seq <= self.last_seq { // knife' \
   an_old_entry_replayed_out_of_order_does_not_count
# KR6 a revoke does not remove the key
kr '                self.keys.retain(|(k, _)| *k != id);' \
   '                // knife: no retain' \
   a_revoke_removes_the_key
# KR7 the root line is not added to the window (item 7)
kr '        let full = format!("{} · {action}", self.window_line());' \
   '        let full = action.to_owned(); // knife' \
   every_window_carries_the_root_fingerprint_and_date
# KR8 the device reporting another root public key is ignored
kr '        if reported != root.public {' \
   '        if false && reported != root.public { // knife' \
   a_root_the_device_reports_differently_voids_everything
# KR9 the root (or a registry_root key) counts as a trusted key (c477a slice 3 L1)
kr '        if e.kind == KeyKind::RegistryRoot || e.public == self.root.public {' \
   '        if false && (e.kind == KeyKind::RegistryRoot || e.public == self.root.public) { // knife' \
   the_root_is_never_a_trusted_key
# KR10 any kind of key verifies an approval proof (c477a slice 3 L1)
kr '        if t.kind != kind || t.provider != provider || t.alg != alg {' \
   '        if (t.kind != kind && kind == KeyKind::Grant) || t.provider != provider || t.alg != alg { // knife' \
   a_grant_key_cannot_sign_an_approval_proof
# KR11 a pointless entry still asks the owner (c477a slice 3 N1)
kr '            Op::Add if known => return Err(RegistryError::Pointless("already_trusted")),' \
   '            Op::Add if known && false => return Err(RegistryError::Pointless("already_trusted")), // knife' \
   an_entry_that_would_be_dropped_is_refused_before_the_owner_is_asked
echo "aib-approve knives: all bit"
