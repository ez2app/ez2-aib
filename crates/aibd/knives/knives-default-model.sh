#!/usr/bin/env bash
# DM1–DM6 (1a93b 11-21, 519d0 06:45 point 1): an open that names no model runs on the brand's
# default; only a brand and a plain model name are kept; a user folder page may not set it.
# Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
du() { python3 -I scripts/knife.py crates/aibd/src/default_model.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
di() { python3 -I scripts/knife.py crates/aibd/src/default_model.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test default_model "$3"; }
ds() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test default_model "$3"; }
# DM1 a brand outside AIB2_BRANDS is kept
du '    aib_seatopen::AIB2_BRANDS.contains(&provider)' \
   '    (aib_seatopen::AIB2_BRANDS.contains(&provider) || true) // knife' \
   only_a_brand_and_a_plain_model_name_are_kept
# DM2 a long name is kept
du '        && model.len() <= MAX_LEN' \
   '        && model.len() <= MAX_LEN * 4 // knife' \
   only_a_brand_and_a_plain_model_name_are_kept
# DM3 a named model loses to the default
du '    named.or_else(|| defaults.get(provider).cloned())' \
   '    defaults.get(provider).cloned().or(named) // knife' \
   an_open_with_no_model_takes_the_brands_default_and_a_named_one_wins
# DM4 a user folder page sets the default
di '    if page.carried || crate::seat_input::held_as_user_page(&page).await {' \
   '    if page.carried && crate::seat_input::held_as_user_page(&page).await { // knife' \
   a_seat_opened_with_no_model_runs_on_the_brands_default
# DM5 the open does not take the default
ds '    req.model = crate::default_model::pick(' \
   '    let _knife = crate::default_model::pick( // knife' \
   a_seat_opened_with_no_model_runs_on_the_brands_default
# DM6 the change is not journaled
di '    crate::seat_send::journal_event(&st, EVENT, json!({ "provider": provider, "model": model }))' \
   '    crate::seat_send::journal_event(&st, "aibd.default_model_unseen", json!({ "provider": provider, "model": model })) // knife' \
   a_seat_opened_with_no_model_runs_on_the_brands_default
