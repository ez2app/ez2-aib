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
ex '            "name": "window", "hub": hub, "wid": wid, "from": from, "unknown": e,' '            "name": "window", "hub": hub, "wid": wid, "from": from, "unknown": format!("{e:.0}"), // knife' \
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
ba '            ms3.parse::<i64>().ok()?' '            ms3.parse::<i64>().ok().map(|_| 0)? // knife' \
   rfc3339_times_act_writes_read_back_to_the_same_ms_and_others_are_refused
# BA4 January and February count in the year before (civil days)
ba '    let mp = if mo > 2 { mo - 3 } else { mo + 9 };' '    let mp = if mo > 2 { mo - 3 } else { mo + 10 }; // knife' \
   rfc3339_times_act_writes_read_back_to_the_same_ms_and_others_are_refused
# BA5 (5d33a 03:07, X5) only review files (REVIEW_PREFIXES) count as a review at the head
ba '        .filter(|n| REVIEW_PREFIXES.iter().any(|p| n.starts_with(p)) && n.ends_with(".md"))' '        .filter(|n| n.ends_with(".md")) // knife' \
   a_basis_line_carries_its_rows_their_age_and_the_recipients_facts
# BA6 (5d33a 03:07, X4) a PR whose head moved since the plan says so
ba '                    "state": v["state"], "same": live.is_some() && live == row_head,' '                    "state": v["state"], "same": live.is_some(), // knife' \
   a_basis_line_carries_its_rows_their_age_and_the_recipients_facts
# SR1 (519d0 03:0x, row 6) a work row about a PR with no 40-hex head is not planned
ba '        if needs_head && head.is_none() {' '        if false && needs_head && head.is_none() { // knife' \
   a_review_row_whose_head_has_a_review_and_a_pr_row_with_no_head_are_not_planned
# SR2 (519d0 03:0x, row 54) a review row whose head already has another seat's review is not planned
ba '            if let Some(f) = by_other {' '            if let Some(f) = by_other.filter(|_| false) { // knife' \
   a_review_row_whose_head_has_a_review_and_a_pr_row_with_no_head_are_not_planned
# SR3 (5d33a r1 M2) a notify_fix row with neither PR nor head is not planned either
ba '        let needs_head = matches!(action, Some("notify_fix" | "open_review"))' '        let needs_head = matches!(action, Some("never"))' \
   a_review_row_whose_head_has_a_review_and_a_pr_row_with_no_head_are_not_planned
# SR4 (5d33a r1 M1) the author's own review does not stop a review being asked for
ba '                review_seat(f).is_some_and(|s| author.as_deref().is_some_and(|a| a != s))' '                review_seat(f).is_some_and(|s| author.as_deref().is_some_and(|a| !a.is_empty() || a != s))' \
   a_review_row_whose_head_has_a_review_and_a_pr_row_with_no_head_are_not_planned
# SR5 the seat is the branch part with a letter (a date is not a seat)
ba '        .rfind(|p| p.bytes().any(|b| b.is_ascii_alphabetic()))' '        .rfind(|p| !p.is_empty())' \
   the_author_is_the_seat_in_the_branch_name_and_a_review_names_its_reviewer
# SR6 an open_review row's author is never its reviewer (to)
ba '    (r.layer == 2 && r.kind == "fix")' '    (r.layer == 3 || r.layer == 2 && r.kind == "fix")' \
   the_author_is_the_seat_in_the_branch_name_and_a_review_names_its_reviewer
# SR7 (5d33a nohist M1) a branch row whose branch moved off the row's head is not planned
ba '                Some(Some(now)) if !now.eq_ignore_ascii_case(h) => Some(format!(' '                Some(Some(now)) if false && !now.eq_ignore_ascii_case(h) => Some(format!(' \
   a_branch_row_whose_branch_moved_or_is_gone_is_not_planned_and_a_check_file_is_a_review
# SR8 (5d33a nohist M1) a branch row whose branch is gone is not planned
ba '                Some(None) => Some(format!("branch {b} is gone")),' '                Some(None) => None::<String>.or(None),' \
   a_branch_row_whose_branch_moved_or_is_gone_is_not_planned_and_a_check_file_is_a_review
# SR9 (5d33a nohist M3) a CHECK- file bound to the head is a review
ba 'pub const REVIEW_PREFIXES: [&str; 3] = ["REVIEW-", "CHECK-", "RECHECK-"];' 'pub const REVIEW_PREFIXES: [&str; 3] = ["REVIEW-", "REVIEW-", "RECHECK-"];' \
   a_branch_row_whose_branch_moved_or_is_gone_is_not_planned_and_a_check_file_is_a_review
# branch_head_in runs git, so it lives in source.rs (act.rs runs no program: act_runs_no_outside_program)
bs() { python3 -I scripts/knife.py crates/aib-drive/src/source.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test basis "$3"; }
# SR10 only git's "no such ref" (rc 1) for both refs means gone; a branch only on origin is found
bs '            Some(1) => {}' '            Some(1) => return Some(None),' \
   a_branch_is_looked_up_in_a_real_clone_and_odd_names_are_not
# SR11 an option-like branch name is never handed to git
bs "    if branch.is_empty() || branch.starts_with('-') || branch.chars().any(char::is_whitespace) {" "    if branch.is_empty() || branch.chars().any(char::is_whitespace) {" \
   a_branch_is_looked_up_in_a_real_clone_and_odd_names_are_not
# from_sweep's review rows live in next.rs
bn() { python3 -I scripts/knife.py crates/aib-drive/src/next.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test next "$3"; }
# SR12 (5d33a nohist2) a draft PR is not asked to be reviewed
bn '        if let Some(p) = open.filter(|p| !reviewed && b.class != 5 && !p.draft && ready) {' '        if let Some(p) = open.filter(|p| !reviewed && b.class != 5 && !p.number.eq(&0) && ready) {' \
   a_draft_pr_or_a_head_without_a_green_receipt_gets_no_review_row
# SR13 (council 11-17 rule 0) a receipt for an older head is not a green receipt at this head
bn '        let ready = b.receipt.as_ref().is_some_and(|r| r.at_head && r.green);' '        let ready = b.receipt.as_ref().is_some_and(|r| r.green);' \
   a_draft_pr_or_a_head_without_a_green_receipt_gets_no_review_row
# SR14 (council 11-17 rule 0) a red receipt at the head does not open a review
bn '        let ready = b.receipt.as_ref().is_some_and(|r| r.at_head && r.green);' '        let ready = b.receipt.as_ref().is_some_and(|r| r.at_head);' \
   a_draft_pr_or_a_head_without_a_green_receipt_gets_no_review_row
# SR15 (council 11-17 rule 0) no receipt read is no green receipt
bn '        let ready = b.receipt.as_ref().is_some_and(|r| r.at_head && r.green);' '        let ready = b.receipt.as_ref().is_none_or(|r| r.at_head && r.green);' \
   a_draft_pr_or_a_head_without_a_green_receipt_gets_no_review_row
