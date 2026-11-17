#!/usr/bin/env bash
# S89-2b (B4-1b-2b) knives SO19–SO26: POST /seats/open starts the seat's provider over pipes, the seat's
# own env names are set by aibd and never taken from a request. Written inline in aibd.yml on
# feat/b4-1b-2b-pipe-wire-a9136-1114 (1638ef7d8); moved here word for word (RULES §21, new knives live in
# crates/<crate>/knives/). Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ks() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seats_open "$3"; }
kl() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
ka() { python3 -I scripts/knife.py crates/aibd/src/allowlist.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
kq() { python3 -I scripts/knife.py crates/aibd/src/sessions.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seats_open "$3"; }
# SO19 (B4-1b-2b K2) the open no longer starts the seat's provider
ks '            match start_session(&st, &seat_id, &card, argv, &cwd).await {' \
   '            match start_session(&st, &seat_id, &card, argv.and(Err(aib_seatopen::PipeRefused::Mode)), &cwd).await { // knife' \
   an_open_starts_the_provider_and_a_retry_starts_nothing
# SO20 (K3) a session that cannot start fails the open (the caller opens a second seat)
ks '                Err(e) => response["session_error"] = e,' \
   '                Err(e) => return (StatusCode::BAD_REQUEST, Json(e)).into_response(), // knife' \
   a_session_that_cannot_start_leaves_the_seat_open
# SO21 (K4) the key is given back after the start: a retry starts a second process
ks '                    store.idem_finish(&k, StatusCode::CREATED.as_u16(), &answer)' \
   '                    store.idem_release(&k).map(|b| b && !answer.is_null()) // knife' \
   an_open_starts_the_provider_and_a_retry_starts_nothing
# SO22 the key is written finished before the start: a retry replays "no session"
ks '                aib_store::IN_PROGRESS,' \
   '                StatusCode::CREATED.as_u16(), // knife' \
   an_open_starts_the_provider_and_a_retry_starts_nothing
# SO23 the program's name is passed as is, not the path the policy lists
ks '    argv[0] = path;' \
   '    argv[0] = argv[0].clone(); let _ = path; // knife: argv stays mut-used (-D warnings)' \
   an_open_starts_the_provider_and_a_retry_starts_nothing
# SO24 (K5) the provider is not told its persona card
kl '        (uid.to_owned(), card.uid.clone()),' \
   '        (uid.to_owned(), String::new()), // knife' \
   a_seat_s_provider_gets_its_seat_and_card_by_env
# SO25 a request may set a seat's own env names when the policy lists them
ka '    if names.any(|k| seat_owned(k) || !p.env.iter().any(|a| env_name_eq(a, k))) {' \
   '    if names.any(|k| !p.env.iter().any(|a| env_name_eq(a, k))) { // knife' \
   a_seat_s_own_env_names_are_never_allowed_from_a_request
# SO26 a queued seat session is refused at dequeue for the env aibd set itself
kq '            .filter(|(k, _)| !allowlist::seat_owned(k))' \
   '            .filter(|(k, _)| !k.is_empty()) // knife' \
   a_queued_seat_session_starts_when_a_slot_frees
