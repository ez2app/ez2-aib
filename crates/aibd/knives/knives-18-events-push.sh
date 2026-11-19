#!/usr/bin/env bash
# S89-events-push knives EP1–EP4 (a9136 11-18; ADR 0003, ORDER-workspace-519d0-1118 第三波 2): a
# new event is pushed on the open stream with no new request; a reconnect starts after its last
# id; an open stream does not hold up aibd's stop. Each rule, broken, turns its own test red.
# Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
eu() { python3 -I scripts/knife.py crates/aibd/src/events_push.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
ei() { python3 -I scripts/knife.py crates/aibd/src/events_push.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test events_push "$3"; }
# EP1 a write does not wake the open streams
ei '    store.on_write(woke);' \
   '    let _ = store; // knife' \
   a_page_gets_new_events_pushed_and_resumes_after_its_last_id
# EP2 a reconnect starts from the beginning again
eu '        .get("last-event-id")' \
   '        .get("x-knife-none")' \
   a_reconnect_starts_after_the_last_id_it_saw
# EP3 an open stream holds up the stop
ei '    CLOSING.store(true, Ordering::SeqCst);' \
   '    CLOSING.store(false, Ordering::SeqCst); // knife' \
   a_page_gets_new_events_pushed_and_resumes_after_its_last_id
# EP4 open streams are not capped (5d33a f0b7d7d5 N1)
eu '            (n < STREAMS_MAX).then_some(n + 1)' \
   '            Some(n + 1) // knife' \
   open_streams_are_capped
