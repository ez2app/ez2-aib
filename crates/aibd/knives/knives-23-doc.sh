#!/usr/bin/env bash
# aib-knives: os=unix
# N-ws-doc-link knives DL1–DL7 (7b81e 11-18; SPEC §17 point 4; 5d33a CHECKLIST-doc-link D1–D3): only a file in
# the pending folder, never a secret kind or one over the limit, never by a path the request names, never to a user
# folder page, never a document a post registered itself. Each rule, broken, turns its own test red.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
du() { python3 -I scripts/knife.py crates/aibd/src/docs.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "docs::tests::$3"; }
de() { python3 -I scripts/knife.py crates/aibd/src/docs.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test doc_link "$3"; }
dt() { python3 -I scripts/knife.py crates/aibd/src/threads.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test doc_link "$3"; }
# DL1 a file outside the folder is read
du '    if !real.starts_with(&base) || real == base {' \
   '    if real == base { // knife' \
   nothing_outside_the_folder_or_of_a_secret_kind_is_read
# DL2 a secret kind is read
du '    SECRET_MARKS.iter().any(|m| low.contains(m))' \
   '    SECRET_MARKS.iter().any(|m| low.contains(m)) && false // knife' \
   nothing_outside_the_folder_or_of_a_secret_kind_is_read
# DL7 a plain name linked into a secret-named folder is read (5d33a N2)
du '        .any(|c| secret_name(&c.as_os_str().to_string_lossy()))' \
   '        .any(|c| secret_name(&c.as_os_str().to_string_lossy()) && false) // knife' \
   nothing_outside_the_folder_or_of_a_secret_kind_is_read
# DL3 a file over the limit is read
du '    if meta.len() > DOC_MAX {' \
   '    if meta.len() > u64::MAX - 1 { // knife' \
   a_file_over_the_limit_is_too_large
# DL4 a user folder page reads a document
de '    if crate::seat_input::held_as_user_page(&page).await {' \
   '    if false && crate::seat_input::held_as_user_page(&page).await { // knife' \
   only_the_built_in_page_reads_a_posts_document_and_never_by_path
# DL5 the request may name a path
de '        .any(|k| !matches!(k.as_str(), "thread_id" | "message_id" | "n" | "file"))' \
   '        .any(|k| k.is_empty()) // knife' \
   only_the_built_in_page_reads_a_posts_document_and_never_by_path
# DL6 a post registers its own document
dt '    if body.get("doc").is_some() {' \
   '    if body.get("doc").is_some() && false { // knife' \
   only_the_built_in_page_reads_a_posts_document_and_never_by_path
# IM15 a mail address reaches the page (5d33a CHECK-docroot 條件 4, docs::mask_mail; a9136 02:26: carried here, not in knives-24-import.sh)
python3 -I scripts/knife.py crates/aibd/src/docs.rs \
  '        if a < i && tld_ok {' \
  '        if false && tld_ok { // knife' \
  --expect-fail docs::tests::mail_addresses_are_masked_and_seat_calls_are_not \
  -- cargo test -p aibd --locked --lib docs::tests::mail_addresses_are_masked_and_seat_calls_are_not
