#!/bin/bash
# 5d33a PREREAD-codexfix-f36f26a1 N4 knife NM1 (7ebca 11-20): thread_send's to_not_a_member
# refusal says how a non-member is brought in (the person @s it in this thread).
set -eo pipefail
nm() { python3 -I scripts/knife.py crates/aibd/src/threads.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test threads "$3"; }
# NM1 the next step is dropped from the refusal
nm "; a seat that is not a member cannot be called in by a seat: ask the person in this thread to @ it in (the page puts it on the item)" \
   "." a_page_opens_a_thread_on_an_item_and_calls_one_member_in
