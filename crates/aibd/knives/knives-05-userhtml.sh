#!/usr/bin/env bash
# S107-userhtml knives UH1–UH3 (a9136 11-18): pages in <data dir>/web are served only from inside that
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
uh '    if ty.starts_with("text/html") {' \
   '    if false && ty.starts_with("text/html") { // knife' \
   a_user_page_is_served_with_the_page_policy_and_read_each_time
