#!/usr/bin/env bash
# SM1–SM6 (1a93b 11-21, 519d0 06:45 point 2): a usage card says when it comes back; a seat moves
# to another model from the built-in page only, pinned, and goes on in its own conversation.
# Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
mu() { python3 -I scripts/knife.py crates/aibd/src/seat_model.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
mi() { python3 -I scripts/knife.py crates/aibd/src/seat_model.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test default_model "$3"; }
su() { python3 -I scripts/knife.py crates/aibd/src/slots.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
st() { python3 -I scripts/knife.py crates/aib-store/src/seat_meta.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-store --locked --test seat_meta "$3"; }
# SM1 a flag passes as a model
mu '        || !aib_seatopen::model_shape(model)' \
   '        || false // knife' \
   only_a_seat_a_plain_model_and_a_key_are_taken
# SM2 a user folder page moves a seat's model
mi '    if page.carried || crate::seat_input::held_as_user_page(&page).await {' \
   '    if page.carried && crate::seat_input::held_as_user_page(&page).await { // knife' \
   a_seat_opened_with_no_model_runs_on_the_brands_default
# SM3 the seat is not started again
mi '    match crate::revive::restart(&st, &seat_id, by).await {' \
   '    let _ = &by; match Err::<String, crate::revive::NotRestarted>(crate::revive::NotRestarted::Unread) { // knife' \
   a_seat_opened_with_no_model_runs_on_the_brands_default
# SM4 a bad hour is read as a time
su '            let hour_ok = digits(h, 1..=2) && h.parse::<u8>().is_ok_and(|v| v <= 23);' \
   '            let hour_ok = digits(h, 1..=2) && h.parse::<u8>().is_ok_and(|v| v <= 99); // knife' \
   the_reset_time_is_only_the_time_after_reset
# SM5 the usage card does not say when it comes back
su '        c.state = Some(format!("{t} 恢復;可換模型接著做"));' \
   '        c.state = Some(format!("{t}")); // knife' \
   a_session_that_ended_for_its_usage_or_a_login_is_low_power
# SM6 the model is not pinned
st '                "UPDATE seat_meta SET model = ?2, model_pinned = 1, updated_at = ?3 WHERE seat_id = ?1",' \
   '                "UPDATE seat_meta SET model = ?2, model_pinned = 0, updated_at = ?3 WHERE seat_id = ?1", // knife' \
   a_seats_model_is_changed_pinned_and_journaled
