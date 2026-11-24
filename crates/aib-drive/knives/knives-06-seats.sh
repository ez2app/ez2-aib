#!/usr/bin/env bash
# aib-knives: os=unix
# The "aib-drive seats knives (macOS)" step of aibd.yml's rust job, moved here word for word
# (RULES §21, Andy 11-15 #15, 519d0 S79-rule21; the aib-cloud-host knives went first, 11-15 08:08).
# aibd.yml runs every crates/aib-drive/knives/*.sh in name order in one step; aib-xtask knife-anchors
# reads crates/*/knives/*.sh, so every knife here is still checked. Run from the repo root;
# -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
#
# S79 sig (b433b, KNIVES-sig.md): each seat-fact rule, broken on purpose, must turn its own test red.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
sg() { python3 -I scripts/knife.py crates/aib-drive/src/seats.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test seats "$3"; }
# SG1
sg '        (_, Some(u), _) if u >= UNREAD_LOST => Fact::new("no", format!("unread {u}")),' \
   '        (_, Some(u), _) if u >= UNREAD_LOST * 1000 => Fact::new("no", format!("unread {u}")), // knife' \
   ten_unread_a_gone_window_or_two_quiet_hours_is_lost_not_idle
# SG2
sg '        (Known::Known(Some(_)), Some(_)) if is_queued => {' \
   '        (Known::Known(Some(_)), Some(_)) if is_queued && false => { // knife' \
   recent_output_or_a_queued_dm_means_the_seat_is_in_a_turn
# SG3
sg '    let dispatchable = if role == "coordinator" {' \
   '    let dispatchable = if role == "coordinator" && false { // knife' \
   the_coordinator_is_never_development_capacity
# SG4
sg '    } else if alive.v == "unknown" || hands.v == "unknown" {' \
   '    } else if alive.v == "unknown" && hands.v == "unknown" && false { // knife' \
   what_cannot_be_read_is_unknown_and_never_free
# SG5
sg '        (Known::Known(None), _, _) => Fact::new("no", "window gone"),' \
   '        (Known::Known(None), _, _) => Fact::new("yes", "window gone"), // knife' \
   ten_unread_a_gone_window_or_two_quiet_hours_is_lost_not_idle
# SG6
sg '    let waiting_on = if holds_lease {' \
   '    let waiting_on = if holds_lease && false { // knife' \
   waiting_on_comes_from_the_lease_and_the_state_and_sets_the_size
# SG7
sg '        (_, _, Some(t)) if now.saturating_sub(t) > LOST_HOURS * 3600 => Fact::new(' \
   '        (_, _, Some(t)) if now.saturating_sub(t) > LOST_HOURS * 3600 * 1000 => Fact::new( // knife' \
   ten_unread_a_gone_window_or_two_quiet_hours_is_lost_not_idle
# SG8
sg '            if status == "archived" {' \
   '            if status == "archived" && false { // knife' \
   files_tmux_and_lease_parse_as_written
# SB1
sg '        ("yes", _) if wip_full => "review",' \
   '        ("yes", _) if false && wip_full => "review",' \
   capacity_comes_from_the_plan_tree_and_the_open_prs
# SB2
sg 'ON_DECK_STATUS.contains(&s)' \
   '!ON_DECK_STATUS.contains(&s)' \
   capacity_comes_from_the_plan_tree_and_the_open_prs
# SB3
sg '.filter(|b| branch_of_seat(b, seat))' \
   '.filter(|b| b.contains(seat))' \
   capacity_comes_from_the_plan_tree_and_the_open_prs
# SB4
sg 'if q >= HANDS_FREE_MIN =>' \
   'if q >= HANDS_FREE_MIN - 1 =>' \
   four_quiet_minutes_is_still_a_turn_five_is_free
# SB5
sg '                _ if matches!(lease, Known::Unknown(_)) => "unknown",' \
   '                _ if false => "unknown",' \
   a_lease_that_cannot_be_read_makes_waiting_on_unknown
# SB6
sg '    let where_ = match shared.wins.as_ref_known().map(|m| m.get(seat).cloned()) {' \
   '    let where_ = match Known::<Option<(String, String)>>::Known(None) {' \
   the_seat_file_window_wins_over_the_latest_dm
# SB7
sg '        .filter(|s| prev.get(&s.seat).map(String::as_str) != Some("free"))' \
   '        .filter(|s| prev.len() < usize::MAX || s.role.is_empty())' \
   newly_free_names_a_seat_once_per_free_stretch
# SB8
sg '    t.ahead == 0' \
   '    t.ahead < u64::MAX' \
   first_is_iface_codes_and_the_detail_lists_dm_commit_push_tidy
# SB9
sg '    if !uuid.ends_with(seat) || !is_seat_code(seat) {' \
   '    if uuid.is_empty() || !is_seat_code(seat) {' \
   the_seat_file_window_wins_over_the_latest_dm
# SB10
sg '    if let Known::Unknown(e) = &shared.nodes {' \
   '    if let Known::Unknown(e) = &Known::<Vec<PlanNode>>::Known(vec![]) {' \
   capacity_that_cannot_be_read_is_null_and_partial_not_a_block
# SB11
sg '.filter(|s| s.alive.v != "no")' \
   '.filter(|s| !s.alive.v.is_empty())' \
   a_lost_seat_does_not_make_the_round_incomplete
# SB12
sg '    root.join("drive").join("idle.jsonl")' \
   '    root.join("state").join("drive").join("idle.jsonl")' \
   idle_is_under_drive_and_only_the_json_round_writes_it
# SB13
sg '    if as_json {' \
   '    if as_json || !as_json {' \
   idle_is_under_drive_and_only_the_json_round_writes_it
# SB14
sg '.all(facts_read);' \
   '.all(facts_read) && partial.is_empty();' \
   capacity_that_cannot_be_read_is_null_and_partial_not_a_block
# SB15
sg '            .is_some_and(|c| now.saturating_sub(c) >= CLEAN_AFTER_H * 3600)' \
   '            .is_some_and(|c| c > 0 || now > 0)' \
   a_fresh_tree_is_not_collected
# SC1 (b433b sig-c): another seat's 未驗 node counts as this seat's plan_node
sg 'Known::Known(n) => owned(n, seat, |s| s == PLAN_UNVERIFIED),' \
   'Known::Known(n) => owned(n, "a9136", |s| s != PLAN_UNVERIFIED),' \
   first_lists_all_four_codes_in_iface_order_each_only_when_due
# SC2 (b433b sig-c): memory_tidy fires at exactly 90 lines (the tidy limit is over 90)
sg 'if memory.is_some_and(|n| n > MEMORY_LINES_MAX) {' \
   'if memory.is_some_and(|n| n >= MEMORY_LINES_MAX) {' \
   first_codes_stay_off_at_the_limit_and_when_a_source_cannot_be_read
# SC3 (b433b sig-c): an open handoff is not listed
sg '    if handoff.is_some() {' \
   '    if handoff.is_some() && handoff.is_none() {' \
   first_lists_all_four_codes_in_iface_order_each_only_when_due
# SC4 (b433b sig-c): a receipt written in the same second as the collect leaves the handoff open
sg 'Some(r) if r >= collected => None,' \
   'Some(r) if r > collected => None,' \
   a_handoff_is_open_only_while_no_receipt_is_as_new_as_the_collect
# SC5 (b433b sig-c): the first receipt line is taken instead of the last
sg '        .rev()' \
   '        .skip(0)' \
   a_handoff_is_open_only_while_no_receipt_is_as_new_as_the_collect
# SC6 (b433b sig-c): any github path is taken as OWNER/NAME
sg 'is_owner_name(rest).then(|| rest.to_string())' \
   'Some(rest.to_string())' \
   repo_names_come_from_a_github_remote_and_nothing_else
# SC7 (b433b sig-c): a repo folder is handed to gh as it is (B3: open_prs never read)
sg '    if !p.is_dir() {' \
   '    if !p.is_dir() || p.is_dir() {' \
   a_repo_folder_is_turned_into_owner_name_before_gh_sees_it
# SC8 (b433b sig-c): a seat with a live dm queue but no seat-now file is never listed (B1: c477a / 2240e lost but unseen)
sg '.is_some_and(|t| now.saturating_sub(t.as_secs()) <= LIST_DAYS * 86_400);' \
   '.is_some_and(|t| now.saturating_sub(t.as_secs()) > LIST_DAYS * 86_400);' \
   a_seat_with_no_seat_now_file_but_a_live_dm_queue_is_listed_and_judged_lost
# SC9 (b433b sig-c): a queue-only seat with no seat-now file stays unknown, the round goes rc 3 (87167 M1)
sg 'Known::Unknown(_) => src.seat_now_gone(seat),' \
   'Known::Unknown(_) => false,' \
   a_seat_with_no_seat_now_for_two_days_is_lost_even_when_window_and_unread_cannot_say
# SC10 (b433b sig-c): no memory file is taken as unreadable (87167 L1)
sg 'Err(e) if e.kind() == std::io::ErrorKind::NotFound => Known::Known(0),' \
   'Err(e) if e.kind() == std::io::ErrorKind::NotFound && false => Known::Known(0),' \
   the_file_source_reads_memory_lines_and_an_open_handoff
# SC11 (b433b sig-c): a fresh beat is ignored and the seat called lost
sg '} && beat.is_none_or(stale);' \
   '};' \
   a_seat_with_no_seat_now_for_two_days_is_lost_even_when_window_and_unread_cannot_say
# SD1 (b433b sig-d; 87167 M1 on 8f70c25e): open PRs that cannot be read leave the seat's reasons silent
sg 'reasons.push(format!("open_prs 讀不到:{e}"));' 'let _ = e; // knife' \
   open_prs_that_cannot_be_read_are_named_in_the_seats_reasons
