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
# act basis lines (a9136 11-17, METHOD-drive-hitrate-5d33a r1 columns 2–8)
ba() { python3 -I scripts/knife.py crates/aib-drive/src/act.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test basis "$3"; }
# BA1 a basis line carries only the rows of its own dm
ba '        .filter(|r| w.ranks.contains(&r.rank))' '        .filter(|_| true) // knife' \
   a_basis_line_carries_its_rows_their_age_and_the_recipients_facts
# BA2 the facts' age is in minutes
ba '            let age = rfc3339_to_ms(&r.data_at).map(|t| now_ms.saturating_sub(t) / 60_000);' '            let age = rfc3339_to_ms(&r.data_at).map(|t| now_ms.saturating_sub(t) / 1000); // knife' \
   a_basis_line_carries_its_rows_their_age_and_the_recipients_facts
# BA3 the fraction of a second is read
ba '            ms3.parse::<i64>().ok()?' '            0 // knife' \
   rfc3339_times_act_writes_read_back_to_the_same_ms_and_others_are_refused
# BA4 January and February count in the year before (civil days)
ba '    let mp = if mo > 2 { mo - 3 } else { mo + 9 };' '    let mp = if mo > 2 { mo - 3 } else { mo + 10 }; // knife' \
   rfc3339_times_act_writes_read_back_to_the_same_ms_and_others_are_refused
# BA5 (5d33a 03:07, X5) only REVIEW-*.md files count as a review at the head
ba '        .filter(|n| n.starts_with("REVIEW-") && n.ends_with(".md"))' '        .filter(|n| n.ends_with(".md")) // knife' \
   a_basis_line_carries_its_rows_their_age_and_the_recipients_facts
# BA6 (5d33a 03:07, X4) a PR whose head moved since the plan says so
ba '                    "state": v["state"], "same": live.is_some() && live == row_head,' '                    "state": v["state"], "same": live.is_some(), // knife' \
   a_basis_line_carries_its_rows_their_age_and_the_recipients_facts
# SR1 (519d0 03:0x, row 6) a work row about a PR with no 40-hex head is not planned
ba '        if let (Some(_), Some(n), None) = (action, r.pr, head) {' '        if let (Some(_), Some(n), None) = (None::<&str>, r.pr, head) { // knife' \
   a_review_row_whose_head_has_a_review_and_a_pr_row_with_no_head_are_not_planned
# SR2 (519d0 03:0x, row 54) a review row whose head already has a review is not planned
ba '            if let Some(f) = have.first() {' '            if let Some(f) = have.first().filter(|_| false) { // knife' \
   a_review_row_whose_head_has_a_review_and_a_pr_row_with_no_head_are_not_planned
