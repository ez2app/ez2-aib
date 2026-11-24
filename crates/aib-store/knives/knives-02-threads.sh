#!/usr/bin/env bash
# aib-knives: os=unix
# N-workspace knives MS13–MS18 (a9136 11-18; SPEC-workspace-multiseat-519d0-1118 §1.3): one key writes one post
# even when two arrive together, a seat lists only its threads, the timeline holds posts only. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
tt() { python3 -I scripts/knife.py crates/aib-store/src/threads.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-store --locked --test messages "$3"; }
# MS13 the key is not checked inside the write
tt '                if let Some(id) = seen {' \
   '                if let Some(id) = seen.filter(|_| false) { // knife' \
   a_post_with_one_key_is_written_once_even_when_two_arrive_together
# MS14 a seat lists threads it is not in
tt '                                          WHERE m.thread_id = t.thread_id AND m.member = ?2))' \
   '                                          WHERE m.thread_id = t.thread_id AND (m.member = ?2 OR 1)))' \
   a_post_with_one_key_is_written_once_even_when_two_arrive_together
# MS15 the timeline holds the deliveries too
tt '            "SELECT {COLS} FROM messages WHERE thread_id = ?1 AND to_key = ?1' \
   '            "SELECT {COLS} FROM messages WHERE thread_id = ?1 AND (to_key = ?1 OR 1)' \
   a_post_with_one_key_is_written_once_even_when_two_arrive_together
# MS16 old page keys are not merged into the person's key
tt '                if is_human(k) && !keep.iter().any(|x| x == k) {' \
   '                if false && is_human(k) && !keep.iter().any(|x| x == k) { // knife' \
   old_page_keys_merge_into_the_persons_key
# MS17 a tree item gets a second thread
tt '                            "SELECT {THREAD_COLS} FROM threads WHERE node = ?1' \
   '                            "SELECT {THREAD_COLS} FROM threads WHERE node = ?1 AND 0' \
   a_tree_item_has_one_thread_and_opening_it_again_adds_members
# MS18 posts in one second come back by their random ids
tt '             ORDER BY rowid DESC LIMIT ?3"' \
   '             ORDER BY created_unix DESC, message_id DESC LIMIT ?3"' \
   posts_in_one_second_keep_the_order_written
# MS19 a free thread opened by title is always new (043dd 02:42)
tt '                None if by_title => tx' \
   '                None if by_title && false => tx' \
   a_tree_item_has_one_thread_and_opening_it_again_adds_members
