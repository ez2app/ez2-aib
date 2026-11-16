#!/usr/bin/env bash
# S79-explain knives (a9136 11-17): `aib-drive explain <seat>` says each fact's source, time and how
# long it holds, and what blocks a dm from act. aibd.yml runs every crates/aib-drive/knives/*.sh in
# name order in one step; aib-xtask knife-anchors reads crates/*/knives/*.sh. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
ex() { python3 -I scripts/knife.py crates/aib-drive/src/explain.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test explain "$3"; }
# EX1 alive yes holds LOST_HOURS after the last sign, not one hour
ex '            last.map(|t| t + LOST_HOURS * 3600),' '            last.map(|t| t + 3600), // knife' \
   a_free_seat_says_alive_until_two_hours_after_its_last_sign_and_nothing_blocks_a_dm
# EX2 in_turn may turn free HANDS_FREE_MIN after the last output, not at it
ex '            act_at.map(|t| t + HANDS_FREE_MIN * 60),' '            act_at, // knife' \
   a_seat_in_its_turn_says_when_it_may_turn_free
# EX3 the 30-minute gap blocks inside the gap only
ex '                if now_ms < next {' '                if now_ms > next { // knife' \
   a_seat_sent_a_dm_ten_minutes_ago_is_blocked_until_the_gap_has_passed
# EX4 unread at the limit blocks (act sends only below UNREAD_MAX)
ex '        Some(u) if (u as u64) < UNREAD_MAX => {}' '        Some(u) if (u as u64) <= UNREAD_MAX => {} // knife' \
   ten_unread_dms_say_lost_until_read_and_block_a_dm
# EX5 paused is a blocker whatever the other switches say
ex '            if sw.paused {' '            if sw.paused && !sw.nudge { // knife' \
   paused_and_an_unreadable_window_are_said_not_guessed
# EX6 an unreadable window says why, not a guess
ex '            "name": "window", "hub": hub, "wid": wid, "from": from, "unknown": e,' '            "name": "window", "hub": hub, "wid": wid, "from": from, // knife' \
   paused_and_an_unreadable_window_are_said_not_guessed
