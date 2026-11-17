#!/usr/bin/env bash
# S107-userhtml knives UH1–UH9 (a9136 11-18): pages in <data dir>/web are served only from inside that
# folder, only up to the size limit, always with the page policy. Each rule, broken, turns its own test red.
# Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
uh() { python3 -I scripts/knife.py crates/aibd/src/static_files.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "static_files::user_tests::$3"; }
# UH1 a link that leads out of the folder is followed
uh '    if !real.starts_with(&root) {' \
   '    if false && !real.starts_with(&root) { // knife' \
   a_link_that_leads_out_of_the_folder_falls_back
# UH2 a file over the size limit is served
uh '    if body.len() as u64 > MAX_USER_FILE {' \
   '    if false { // knife' \
   paths_outside_the_folder_unknown_types_and_big_files_fall_back
# UH3 a user page gets no page policy
uh '    if ty.starts_with("text/html") || ty.starts_with("image/svg+xml") {' \
   '    if ty.starts_with("image/svg+xml") { // knife' \
   a_user_page_is_served_with_the_page_policy_and_read_each_time
# UH4 a user SVG gets no page policy (it runs its scripts when opened on its own)
uh '    if ty.starts_with("text/html") || ty.starts_with("image/svg+xml") {' \
   '    if ty.starts_with("text/html") { // knife' \
   a_user_svg_gets_the_page_policy
# UH5 a credential redeemed while a user page served `/` is not marked (the page deleted, it drives dgrs)
python3 -I scripts/knife.py crates/aibd/src/pairing.rs \
   '            p.user_page = user_page;' \
   '            p.user_page = false && user_page; // knife' \
   --expect-fail a_user_page_credential_types_only_to_safe_seats_even_after_the_page_is_gone \
   -- cargo test -p aibd --locked --test seat_input_userpage a_user_page_credential_types_only_to_safe_seats_even_after_the_page_is_gone
# UH6 a user page credential types to a dgrs seat
python3 -I scripts/knife.py crates/aibd/src/seat_input.rs \
   '    if user_page && meta.mode.as_deref() != Some("safe") {' \
   '    if false && user_page { // knife' \
   --expect-fail a_user_page_credential_types_only_to_safe_seats_even_after_the_page_is_gone \
   -- cargo test -p aibd --locked --test seat_input_userpage a_user_page_credential_types_only_to_safe_seats_even_after_the_page_is_gone
# UH7 act takes another type (an approval would go through a credential a user page can hold)
python3 -I scripts/knife.py crates/aibd/src/seat_input.rs \
   '    if body.get("type").and_then(Value::as_str) != Some(KIND) {' \
   '    if false { // knife' \
   --expect-fail act_takes_only_seat_input_never_an_approval \
   -- cargo test -p aibd --locked --test seat_input act_takes_only_seat_input_never_an_approval
# UH8 the exchange page sends a built-in pairing to `/` (where a user page would read it)
python3 -I scripts/knife.py crates/aibd/src/pair_api.rs \
   '    let home = if to_user { "/" } else { BUILTIN_HOME };' \
   '    let home = if to_user || true { "/" } else { BUILTIN_HOME }; // knife' \
   --expect-fail the_credential_goes_to_the_built_in_page_unless_the_user_page_asked \
   -- cargo test -p aibd --locked --lib pair_api::tests::the_credential_goes_to_the_built_in_page_unless_the_user_page_asked
# UH9 a user file takes the place of a built-in file the built-in page loads
uh '    rel != "index.html" && assets.iter().any(|a| a.path == rel)' \
   '    false && rel != "index.html" && assets.iter().any(|a| a.path == rel) // knife' \
   the_built_in_page_has_its_own_prefix_and_its_files_cannot_be_shadowed
