#!/usr/bin/env bash
# aib-knives: os=unix
# OM1–OM14 指紋或密碼 (ebab8 11-26 21:15, Andy 「要」; PLAN-owner-method-c2f0b-1126 r2 / r2b / r3, 3e53b pre-read): the
# password window only for a record signed by its old key, naming the current key, with the pin moved by that record;
# a pin written at a pairing never opens it; the pin moves only from the record's old key; a crash between the swap and
# the pin is mended at start only when consistent; a change cut short never takes the new key; each signature compares
# its key with the pin; the change is signed over its own words; the swap waits for a signature; owner.answer says how a
# yes was given; the set body names `to` only. Each knife keeps the code warning-free (RUSTFLAGS -D warnings).
set -uo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)" || exit 2
fail=0
k() { python3 -I scripts/knife.py "crates/aibd/src/$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --lib "$4" || fail=1; }
# OM1 a record's signature is not checked
k owner_method.rs '    let signed = verify(&r.old_pub, &r.message(), &r.sig);' '    let signed = verify(&r.old_pub, &r.message(), &r.sig) || true;' owner_method::tests::a_record_not_signed_by_its_old_key_is_the_fingerprint
# OM2 the pin moves from a key that is not the record's old key
k owner_method.rs '    let pinned_old = pin.pub_sha256 == sha256_hex(&r.old_pub);' '    let pinned_old = true || pin.pub_sha256 == sha256_hex(&r.old_pub);' owner_method::tests::the_pin_moves_only_from_the_records_old_key_to_the_current_one
# OM3 a pin written at a pairing opens the password window (r3)
k owner_method.rs '    let moved_by_it = pin.record_sha256.as_deref() == Some(r.sha().as_str());' '    let moved_by_it = pin.record_sha256.as_deref() == Some(r.sha().as_str()) || pin.record_sha256.is_none();' owner_method::tests::a_pin_written_at_a_pairing_never_opens_the_password_window
# OM4 a change cut short leaves pair-key.new to be taken
k owner_method.rs '    if is_dir(&new) {' '    if false && is_dir(&new) {' owner_method::tests::a_change_cut_short_puts_the_old_key_back_and_never_takes_the_new_one
# OM5 a crash between the swap and the pin is never mended
k owner_method.rs '    may_repin(pin, record, current_pub, verify).then(|| Pin {' '    (false && may_repin(pin, record, current_pub, verify)).then(|| Pin {' owner_method::tests::a_crash_between_the_swap_and_the_pin_is_mended_at_start_only_when_consistent
# OM6 the set body takes more than `to`
k owner_method.rs '    if o.keys().any(|k| !matches!(k.as_str(), "type" | "to")) {' '    if o.keys().any(|k| !matches!(k.as_str(), "type" | "to" | "acl")) {' owner_method::tests::a_set_body_names_to_and_nothing_else
# OM7 a signature takes the password with a key that is not the pin (3e53b r3 must-add)
k owner_method.rs '    chosen && pinned' '    chosen' owner_method::tests::each_signature_takes_the_password_only_with_the_pinned_key
# OM8 the change is not signed over its own words
k owner_method.rs '        .ask_until_keep(&c.reason, &msg, c.limit, &mut sig)' '        .ask_until_keep(&c.reason, b"", c.limit, &mut sig)' owner_method::tests::a_change_is_signed_by_the_pinned_key_then_swapped_and_pinned
# OM9 a chosen password opens the key's own window when Touch ID is not usable now
k touch.rs '    if password_chosen && now == crate::owner_probe::Class::Fingerprint {' '    if password_chosen {' touch::tests::a_chosen_password_opens_the_keys_own_window_only_where_touch_id_works
# OM10 a swap is not taken for the next signature
k touch.rs '        *g = (p, public);' '        *g = (p, if public == g.1 { public } else { g.1 });' touch::tests::a_swapped_key_signs_from_the_next_signature_on
# OM11 owner.answer does not say how a yes was given
k owner_ok.rs '            "method": crate::owner_method::method_word(),' '            "method_": crate::owner_method::method_word(),' owner_ok::tests::every_answer_and_every_used_ticket_is_journaled_once
# OM12 a record no change completed stays, and every later change is refused (3e53b REVIEW-ownermethod L1)
k owner_method.rs '    never_swapped || before_the_pairing' '    never_swapped && before_the_pairing' owner_method::tests::a_record_no_change_completed_is_cleared_and_the_owner_can_change_again
# OM13 a failed swap leaves the new record, and the key in use no longer matches it (f163c REVIEW-ownermethod L1)
k owner_method.rs '                let _ = crate::platform::write_private_file(&f, b);' '                let _ = crate::platform::write_private_file(&f, &b[..0]);' owner_method::tests::a_swap_that_fails_puts_the_record_there_back_and_can_be_tried_again
# OM14 the old folders are put back by their spelling, not their number (3e53b r2 N1)
k owner_method.rs '    olds.sort_by_key(num);' '    olds.sort_by_key(|p| (num(p), p.clone()).1);' owner_method::tests::a_change_cut_short_puts_the_old_key_back_and_never_takes_the_new_one
exit "$fail"
