#!/usr/bin/env bash
# `POST /api/v1/seats/open` and the seat rows (B4-1b-1, a9136 11-14): each knife takes
# one rule away and must turn its own test red; every knife still builds under
# -D warnings. Run from the repo root.
set -eu
# FILE is written out (not a variable): aib-xtask knife-anchors reads every
# scripts/knife.py call and only understands plain text and $1..$9.
ks() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seats_open "$3"; }
kc() { python3 -I scripts/knife.py crates/aibd/src/ctl.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --bin aibd "$3"; }
# SO1 a dry run writes the seat and its event (519d0 r2 (b))
ks '    if req.dry_run {' \
   '    if req.dry_run && req.cwd.is_empty() { // knife: a dry run falls through and writes' \
   a_dry_run_answers_the_plan_and_writes_nothing
# SO2 the seat is written without its persona card found
ks '    let Some(card) = cards.into_iter().find(|c| c.uid == req.persona_uid) else {' \
   '    let Some(card) = cards.into_iter().find(|c| c.uid == req.persona_uid || !c.uid.is_empty()) else { // knife' \
   bad_opens_are_refused_and_write_nothing
# SO3 the journal carries where the seat works
ks '    payload["seat_id"] = serde_json::Value::String(seat_id.clone());' \
   '    payload["seat_id"] = serde_json::Value::String(seat_id.clone()); payload["cwd"] = req.cwd.clone().into(); // knife' \
   a_seat_opens_by_persona_and_shows_persona_provider_and_mode
# SO4 an open without an Idempotency-Key is taken (a retry opens a second seat)
ks '    if key.is_none() && !req.dry_run {' \
   '    if key.is_none() && !req.dry_run && req.cwd.is_empty() { // knife' \
   bad_opens_are_refused_and_write_nothing
# SO5 unreadable cards read as "no such persona" (400), not 503
ks '        Ok(Err(e)) => return unavailable(&e),' \
   '        Ok(Err(e)) => if e.to_string().is_empty() { return unavailable(&e) } else { Vec::new() }, // knife: unreadable cards read as no cards; unavailable stays used (-D warnings)' \
   cards_that_cannot_be_read_are_503_and_the_list_still_answers
# SO6 the second seat of a persona shows no seat code (Andy 11-14 10:44)
ks '            let dup = uid.is_some_and(|u| !seen.insert(u));' \
   '            let dup = uid.is_some_and(|u| !seen.insert(u) && u.is_empty()); // knife' \
   the_second_seat_of_a_persona_shows_its_seat_code
# SO7 the shown name is the stored one, not the card's now (7b81e §2-1: read when shown)
ks '        name.unwrap_or(&s.name),' \
   '        name.and(Some(s.name.as_str())).unwrap_or(&s.name), // knife' \
   a_seat_opens_by_persona_and_shows_persona_provider_and_mode
# SC1 the CLI sends the persona's name, not its uid (7b81e: the API takes only the uid)
kc '        "persona_uid": spec.persona.uid,' \
   '        "persona_uid": spec.persona.name, // knife' \
   a_persona_seat_open_sends_the_uid_with_a_fresh_key
# SC2 --again is sent instead of refused
kc '        (spec.again, "--again"),' \
   '        (spec.again && spec.dry_run, "--again"), // knife' \
   what_2_0_cannot_open_yet_sends_nothing
# SC3 the candidates of a shared name do not say their uids (519d0 r2 (a))
kc '        .map(|c| format!("{} · {}", c.name, c.uid))' \
   '        .map(|c| format!("{} · {}", c.name, c.uid.len())) // knife' \
   a_shared_persona_name_lists_the_uids_and_sends_nothing
# SC4 `seat list` is not a command (519d0 11-14: aibd ctl seat list prints the display rows)
kc '        ["seats"] | ["seat", "list"] => return seats(c),' \
   '        ["seats"] => return seats(c), // knife: seat list is not a command' \
   seat_rows_show_the_display_line
