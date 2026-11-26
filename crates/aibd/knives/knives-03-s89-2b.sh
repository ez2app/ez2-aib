#!/usr/bin/env bash
# aib-knives: os=macos   # 90856 11-25 E3 r2: a test its knives expect red already fails on ubuntu unmutated (false bite there)
# S89-2b (B4-1b-2b) knives SO19–SO26, WD1–WD7: POST /seats/open starts the seat's provider over pipes, the seat's
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
ks '            match start_session(&st, &seat_id, &card, argv, &cwd, &req.mode).await {' \
   '            match start_session(&st, &seat_id, &card, argv.and(Err(aib_seatopen::PipeRefused::Mode)), &cwd, &req.mode).await { // knife' \
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
# WD1 (S89-live-workdir) a folder with project settings is opened anyway
ks '            project_settings_in(std::path::Path::new(c), home.as_deref())' \
   '            project_settings_in(std::path::Path::new(c), home.as_deref()).filter(|_| false) // knife' \
   a_seat_without_a_folder_gets_its_own_and_a_project_folder_is_refused
# WD1b a folder with project settings is let through as if aibd had opened it as a seat's tree (b4297 11-19)
ks '        let listed = if req.tree {' \
   '        let listed = if req.tree || true { // knife' \
   a_seat_without_a_folder_gets_its_own_and_a_project_folder_is_refused
# WD2 a seat without a folder works in aibd's sessions folder instead of its own
ks '            let d = seat_home(st.sessions.private_dir(), &seat_id);' \
   '            let d = st.sessions.private_dir().to_path_buf(); // knife' \
   a_seat_without_a_folder_gets_its_own_and_a_project_folder_is_refused
# WD3 the settings search stops at the folder itself (a CLAUDE.md above it is missed)
kl '    for d in dir.ancestors() {' \
   '    for d in dir.ancestors().take(1) { // knife' \
   project_settings_in_the_folder_or_above_are_found_up_to_home
# WD4 ctl sends the current folder even without --here
python3 -I scripts/knife.py crates/aibd/src/ctl.rs \
   '            let cwd = if here {' \
   '            let cwd = if here || true { // knife' \
   --expect-fail a_persona_seat_open_sends_the_uid_with_a_fresh_key \
   -- cargo test -p aibd --locked --bin aibd a_persona_seat_open_sends_the_uid_with_a_fresh_key
# WD5 a link to a project folder is searched where it is, not where it leads (5d33a L on 257ac5ab)
kl '    let dir = std::fs::canonicalize(dir).unwrap_or_else(|_| dir.to_path_buf());' \
   '    let dir = dir.to_path_buf(); // knife' \
   project_settings_in_the_folder_or_above_are_found_up_to_home
# WD6 a project's .claude folder (agents / commands / skills) does not refuse the folder (0eed7 L1)
kl '        if !at_home && project_dir_loads(d) {' \
   '        if false && !at_home && project_dir_loads(d) { // knife' \
   project_settings_in_the_folder_or_above_are_found_up_to_home
# WD7 the search stops before the home folder's own CLAUDE.md (0eed7 N1)
kl '        let at_home = home.as_deref().is_some_and(|h| d == h);' \
   '        let at_home = home.as_deref().is_some_and(|h| d == h); if at_home { return None; } // knife' \
   project_settings_in_the_folder_or_above_are_found_up_to_home
