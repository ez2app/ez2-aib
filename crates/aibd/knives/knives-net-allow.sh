#!/usr/bin/env bash
# A6c always-allow (IFACE-netallow-ticket-c2f0b-1123 r2 §5):
# signature verified, uid checked, 30-day cap enforced, classify before list, revoked refused.
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
# na-verify
python3 -I scripts/knife.py crates/aibd/src/net_allow.rs \
  '    verify_fn(&msg, &row.sig)' \
  '    let _ = (msg, row, verify_fn); true' \
  --expect-fail net_allow::tests::valid_signed_row_holds_and_forged_or_tampered_row_is_refused \
  -- cargo test -p aibd --locked --lib net_allow::tests::valid_signed_row_holds_and_forged_or_tampered_row_is_refused
# na-uid
python3 -I scripts/knife.py crates/aibd/src/net_allow.rs \
  '        if uid != persona_uid || canon != want_canon {' \
  '        if (uid.is_empty() && persona_uid.is_empty()) || canon != want_canon {' \
  --expect-fail net_allow::tests::valid_signed_row_holds_and_forged_or_tampered_row_is_refused \
  -- cargo test -p aibd --locked --lib net_allow::tests::valid_signed_row_holds_and_forged_or_tampered_row_is_refused
# na-30d
python3 -I scripts/knife.py crates/aibd/src/net_allow.rs \
  '            || until - issued_at > MAX_DAYS_SECS' \
  '            || false' \
  --expect-fail net_allow::tests::over_30_days_own_ip_and_revoked_tickets_are_refused \
  -- cargo test -p aibd --locked --lib net_allow::tests::over_30_days_own_ip_and_revoked_tickets_are_refused
# na-classify
python3 -I scripts/knife.py crates/aibd/src/net_allow.rs \
  '    if !matches!(' \
  '    if false && !matches!(' \
  --expect-fail net_allow::tests::over_30_days_own_ip_and_revoked_tickets_are_refused \
  -- cargo test -p aibd --locked --lib net_allow::tests::over_30_days_own_ip_and_revoked_tickets_are_refused
# na-revoked
python3 -I scripts/knife.py crates/aib-store/src/net_allow.rs \
  '            if revoked.is_some() {' \
  '            if revoked.is_none() && false {' \
  --expect-fail net_allow::tests::over_30_days_own_ip_and_revoked_tickets_are_refused \
  -- cargo test -p aibd --locked --lib net_allow::tests::over_30_days_own_ip_and_revoked_tickets_are_refused
# na-issued
python3 -I scripts/knife.py crates/aibd/src/net_allow.rs \
  '        if now < issued_at' \
  '        if false' \
  --expect-fail net_allow::tests::over_30_days_own_ip_and_revoked_tickets_are_refused \
  -- cargo test -p aibd --locked --lib net_allow::tests::over_30_days_own_ip_and_revoked_tickets_are_refused
# na-tunnel-revoke
python3 -I scripts/knife.py crates/aibd/src/net_allow.rs \
  '    db.net_allow_revoke(ticket, now as i64)?;' \
  '    let _ = (db, ticket, now);' \
  --expect-fail net_allow::tests::proxy_decide_uses_net_allow_holds_and_closes_tunnels_on_revoke_expire_and_inactive \
  -- cargo test -p aibd --locked --lib net_allow::tests::proxy_decide_uses_net_allow_holds_and_closes_tunnels_on_revoke_expire_and_inactive
# na-list-expired
python3 -I scripts/knife.py crates/aibd/src/net_card_web.rs \
  '            "expired"' \
  '            "ok"' \
  --expect-fail net_card_web::tests::db_allow_rows_emit_all_states_and_never_hide_invalid_rows \
  -- cargo test -p aibd --locked --lib net_card_web::tests::db_allow_rows_emit_all_states_and_never_hide_invalid_rows
# na-is-revoked
python3 -I scripts/knife.py crates/aibd/src/net_allow.rs \
  '        if db.net_allow_is_revoked(&row.ticket).unwrap_or(true) {' \
  '        if false {' \
  --expect-fail net_allow::tests::over_30_days_own_ip_and_revoked_tickets_are_refused \
  -- cargo test -p aibd --locked --lib net_allow::tests::over_30_days_own_ip_and_revoked_tickets_are_refused
# na-persona-active
python3 -I scripts/knife.py crates/aibd/src/net_allow.rs \
  '    if !persona_active {' \
  '    if false && !persona_active {' \
  --expect-fail net_allow::tests::valid_signed_row_holds_and_forged_or_tampered_row_is_refused \
  -- cargo test -p aibd --locked --lib net_allow::tests::valid_signed_row_holds_and_forged_or_tampered_row_is_refused
# na-list-sig
python3 -I scripts/knife.py crates/aibd/src/net_card_web.rs \
  '        } else if !crate::net_allow::row_sig_ok(r, verify_fn) {' \
  '        } else if false && !crate::net_allow::row_sig_ok(r, verify_fn) {' \
  --expect-fail net_card_web::tests::db_allow_rows_emit_all_states_and_never_hide_invalid_rows \
  -- cargo test -p aibd --locked --lib net_card_web::tests::db_allow_rows_emit_all_states_and_never_hide_invalid_rows
# na-choices-no-always
python3 -I scripts/knife.py crates/aibd/src/net_card.rs \
  '            .filter(|c| owner_avail || *c != Choice::Always)' \
  '            .filter(|_| true)' \
  --expect-fail net_card::tests::choices_with_owner_hides_always_when_unavailable \
  -- cargo test -p aibd --locked --lib net_card::tests::choices_with_owner_hides_always_when_unavailable
# na-restart-list-ip
python3 -I scripts/knife.py crates/aibd/src/net_card_web.rs \
  '            obj["ip"] = json!(sa.ip().to_string());' \
  '            let _ = sa;' \
  --expect-fail a_yes_finishes_only_with_the_owners_ticket_for_this_card_and_choice \
  -- cargo test -p aibd --locked --test net_card_owner a_yes_finishes_only_with_the_owners_ticket_for_this_card_and_choice
# na-dedup-allow
python3 -I scripts/knife.py crates/aibd/src/net_card_web.rs \
  '        if let Some(existing) = dup {' \
  '        if let Some(existing) = dup.filter(|_| false) {' \
  --expect-fail a_yes_finishes_only_with_the_owners_ticket_for_this_card_and_choice \
  -- cargo test -p aibd --locked --test net_card_owner a_yes_finishes_only_with_the_owners_ticket_for_this_card_and_choice
# c2f0b REVIEW-netallow-incr-c2f0b-e223e67e47 L-a..L-d + N1 (3e53b 11-25)
# na-key-signs
python3 -I scripts/knife.py crates/aibd/src/touch.rs \
  '        true // ask_until_keep keeps the signature of every yes' \
  '        false' \
  --expect-fail touch::tests::only_a_signature_that_checks_out_is_a_yes \
  -- cargo test -p aibd --locked --lib touch::tests::only_a_signature_that_checks_out_is_a_yes
# na-keyless-signs
python3 -I scripts/knife.py crates/aibd/src/touch.rs \
  '        false // a presence with no key signs nothing' \
  '        true' \
  --expect-fail touch::tests::only_a_signature_that_checks_out_is_a_yes \
  -- cargo test -p aibd --locked --lib touch::tests::only_a_signature_that_checks_out_is_a_yes
# na-owner-can-sign
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs \
  '        self.presence.signs()' \
  '        true' \
  --expect-fail owner_ok::tests::an_owner_without_a_key_cannot_sign \
  -- cargo test -p aibd --locked --lib owner_ok::tests::an_owner_without_a_key_cannot_sign
# na-shown-can-sign
python3 -I scripts/knife.py crates/aibd/src/net_card.rs \
  '            .choices_with_owner(crate::owner_ok::can_sign())' \
  '            .choices_with_owner(true)' \
  --expect-fail net_card::tests::the_cards_list_counts_and_ends \
  -- cargo test -p aibd --locked --lib net_card::tests::the_cards_list_counts_and_ends
# na-answer-can-sign
python3 -I scripts/knife.py crates/aibd/src/net_card.rs \
  '            Choice::parse(choice).filter(|c| card.choices_with_owner(can_sign).contains(c))' \
  '            Choice::parse(choice).filter(|c| card.choices_with_owner(can_sign || true).contains(c))' \
  --expect-fail net_card::tests::always_is_not_taken_when_nothing_can_sign_it \
  -- cargo test -p aibd --locked --lib net_card::tests::always_is_not_taken_when_nothing_can_sign_it
# na-list-class-holds
python3 -I scripts/knife.py crates/aibd/src/net_card_web.rs \
  '        crate::egress::Verdict::Card(_)' \
  '        crate::egress::Verdict::Pass(_)' \
  --expect-fail net_card_web::tests::db_allow_rows_emit_all_states_and_never_hide_invalid_rows \
  -- cargo test -p aibd --locked --lib net_card_web::tests::db_allow_rows_emit_all_states_and_never_hide_invalid_rows
# na-list-bad-params
python3 -I scripts/knife.py crates/aibd/src/net_card_web.rs \
  '            items.push(bad_params_row(r, seat_id));' \
  '            let _ = bad_params_row(r, seat_id);' \
  --expect-fail net_card_web::tests::db_allow_rows_emit_all_states_and_never_hide_invalid_rows \
  -- cargo test -p aibd --locked --lib net_card_web::tests::db_allow_rows_emit_all_states_and_never_hide_invalid_rows
# na-list-archived
python3 -I scripts/knife.py crates/aibd/src/net_card_web.rs \
  '        } else if is_archived(&uid) {' \
  '        } else if false && is_archived(&uid) {' \
  --expect-fail net_card_web::tests::db_allow_rows_emit_all_states_and_never_hide_invalid_rows \
  -- cargo test -p aibd --locked --lib net_card_web::tests::db_allow_rows_emit_all_states_and_never_hide_invalid_rows
# na-log-per-minute
python3 -I scripts/knife.py crates/aibd/src/net_card_web.rs \
  '        if self.in_minute >= PER_MINUTE {' \
  '        if self.in_minute >= PER_MINUTE + 1_000 {' \
  --expect-fail net_card_web::tests::bad_row_events_are_limited_and_never_flushed \
  -- cargo test -p aibd --locked --lib net_card_web::tests::bad_row_events_are_limited_and_never_flushed
# na-log-per-ticket
python3 -I scripts/knife.py crates/aibd/src/net_card_web.rs \
  '            .is_some_and(|&t| now_unix < t + PER_TICKET_SECS)' \
  '            .is_some_and(|&t| now_unix < t)' \
  --expect-fail net_card_web::tests::bad_row_events_are_limited_and_never_flushed \
  -- cargo test -p aibd --locked --lib net_card_web::tests::bad_row_events_are_limited_and_never_flushed
# na-log-keep-max
python3 -I scripts/knife.py crates/aibd/src/net_card_web.rs \
  '        if self.last.len() >= KEEP_MAX {' \
  '        if self.last.len() > KEEP_MAX {' \
  --expect-fail net_card_web::tests::bad_row_events_are_limited_and_never_flushed \
  -- cargo test -p aibd --locked --lib net_card_web::tests::bad_row_events_are_limited_and_never_flushed
