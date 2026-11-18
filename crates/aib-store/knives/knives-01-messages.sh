#!/usr/bin/env bash
# S89-talk-2a knives MS10–MS12 (a9136 11-18; DECISION-council-dmq-519d0-1118, MAP-tests-dmq-7b81e-1118 r1): a message
# library file a newer aibd wrote is not read; the next page of a thread starts where the last one ended. Run from the repo root.
# (split from crates/aibd/knives/knives-12-messages.sh: one crate per knives file)
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
ms() { python3 -I scripts/knife.py crates/aib-store/src/messages.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-store --locked --test messages "$3"; }
# MS10 a library file a newer aibd wrote is read
ms '        newer => Err(StoreError::UnknownMigration(newer)),' \
   '        _ => Ok(()), // knife' \
   a_newer_library_is_refused_and_this_builds_opens_again
# MS11 the next page starts at the newest row again
ms '                Some(r) if r.thread_id == thread_id => Some((r.created_unix, r.message_id)),' \
   '                Some(r) if r.thread_id == thread_id => { let _ = r; None } // knife' \
   an_inbox_page_continues_where_the_last_one_ended
# MS12 a read message still counts as unread
ms "                    SUM(CASE WHEN json_extract(item, '\$.status') IN ('queued', 'delivered')" \
   "                    SUM(CASE WHEN json_extract(item, '\$.status') IN ('queued', 'delivered', 'read')" \
   unread_counts_are_per_sender_and_only_for_the_reader
