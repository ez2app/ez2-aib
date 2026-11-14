#!/usr/bin/env bash
# aib-reflect R4 knives (043dd 11-12, PRDRAFT-aib-reflect-R4 §5 item 2): each knife
# takes one gate check away and must turn that check's own test red. Run from the repo root.
set -eu
# FILE is written out (not a variable): aib-xtask knife-anchors reads every
# scripts/knife.py call before the push and only understands plain text and $1..$9.
k() { python3 -I scripts/knife.py crates/aib-reflect/src/gate.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-reflect --locked --lib "$3"; }
p() { python3 -I scripts/knife.py crates/aib-policy/src/lib.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-policy --locked --lib "$3"; }
# 1 paused
k '    if c.paused {' '    if c.paused && false { // knife' x10_pause_comes_first_but_lists_the_rest
# 2 a newer unreadable line (ledger checklist L3)
k '    if !s.readable {' '    if false { // knife' x28_a_newer_unreadable_line_hides_an_older_pass
# 3 forbidden areas
k '    for a in areas.iter().filter(|a| !a.is_self_modify()) {' \
  '    for a in areas.iter().filter(|_| false) { // knife' x05_to_x09_forbidden_and_self_modify_ask_andy_in_every_mode
# 4 self modification
k '    for a in areas.iter().filter(|a| a.is_self_modify()) {' \
  '    for a in areas.iter().filter(|_| false) { // knife' x05_to_x09_forbidden_and_self_modify_ask_andy_in_every_mode
# 5 one work item per revision
k '    if s.dispatched {' '    if false { // knife' x23_x24_one_work_item_per_revision
# 6 only checkable evidence counts
k '    let has_evidence = s.evidence.iter().any(Evidence::checkable);' \
  '    let has_evidence = !s.evidence.is_empty(); // knife' x03_x04_no_checkable_evidence_caps_the_total
# 6 the 60 cap
k '        s.total.min(NO_EVIDENCE_CAP)' '        s.total // knife' x03_x04_no_checkable_evidence_caps_the_total
# 7 necessary conditions
k '    if !s.node_active {' '    if false { // knife' necessary_conditions_each_hold
k '    if s.baseline.as_deref().is_none_or(|b| b.trim().is_empty()) {' \
  '    if s.baseline.is_none() { // knife' necessary_conditions_each_hold
k '    if s.acceptance.as_deref().is_none_or(|b| b.trim().is_empty()) {' \
  '    if false { // knife' necessary_conditions_each_hold
k '    if !s.risk_classified {' '    if false { // knife' necessary_conditions_each_hold
k '    if s.goal_sha_scored != s.goal_sha_now {' '    if false { // knife' x16_a_changed_goal_voids_the_score
k '    if now.saturating_sub(s.created_at) > i64::from(c.expire_hours) * 3600 {' \
  '    if now.saturating_sub(s.created_at) >= i64::from(c.expire_hours) * 3600 { // knife' \
  x17_seventy_two_hours_exactly_is_still_valid
# 8 mainline
k '    if s.mainline < MAINLINE_MIN {' '    if false { // knife' x15_mainline_under_10_is_never_automatic
# 9 threshold: 75 itself passes
k '    if total < c.threshold {' '    if total <= c.threshold { // knife' x01_x02_threshold_is_at_least_75
# 10 the blind review
k '    if !s.reviewer_available {' '    if false { // knife' x11_x14_the_blind_review_is_required_and_verified
k '        None => r.push(hold("review_missing")),' '        None => {} // knife' \
  x11_x14_the_blind_review_is_required_and_verified
k '            if rv.by.is_none() {' '            if false { // knife' x11_x14_the_blind_review_is_required_and_verified
k '            if rv.total < c.threshold {' '            if false { // knife' x11_x14_the_blind_review_is_required_and_verified
k '            if rv.at > now.saturating_add(FUTURE_SLACK_S) {' '            if false { // knife' \
  x29_a_review_from_the_future_does_not_count
# 11 disagreement: 15 is not a disagreement
k '            if s.human.abs_diff(rv.human) > c.human_diff_max {' \
  '            if s.human.abs_diff(rv.human) >= c.human_diff_max { // knife' x12_x13_human_parts_may_differ_by_15_not_16
# 12 owner
k '        None => r.push(hold("owner_check_missing")),' '        None => {} // knife' x25_two_crates_need_the_owner
# 13 bias of either scorer
k '    if s.proposer_bias > c.bias_max || reviewer_bias > c.bias_max {' \
  '    if s.proposer_bias > c.bias_max || (reviewer_bias > c.bias_max && false) { // knife' \
  x26_a_biased_scorer_triggers_nothing
# 14 resources
k '    if w.mainline_red {' '    if false { // knife' x18_a_warm_machine_starts_nothing
k '    if w.cooldown >= Cooldown::Warn {' '    if w.cooldown > Cooldown::Warn { // knife' x18_a_warm_machine_starts_nothing
k '    if w.seat_has_waiting_pr {' '    if false { // knife' x18_a_warm_machine_starts_nothing
# 15 caps
k '    if w.seat_inflight >= c.per_seat_inflight {' '    if w.seat_inflight > c.per_seat_inflight { // knife' x19_x20_caps
k '        .filter(|&&t| local_day(t, c.tz_offset_min) == today)' '        .filter(|_| today == today) // knife' \
  x27_the_day_turns_at_taipei_midnight
# 16 modes
k '    if c.mode == Mode::Off {' '    if false { // knife' x21_x22_modes
k '                Mode::On => (Kind::Auto, "mode_on"),' '                Mode::On | Mode::Shadow => (Kind::Auto, "mode_on"), // knife' x21_x22_modes
# config
k '                _ => return Err(format!("config_unknown_key: {k}")),' '                _ => {} // knife' \
  c1_to_c4_config_rejects_what_it_does_not_know
k '                "threshold" => c.threshold = int_in(x, k, 50, 100)? as u32,' \
  '                "threshold" => c.threshold = int_in(x, k, 0, 1000)? as u32, // knife' \
  c1_to_c4_config_rejects_what_it_does_not_know
# 10 a review by the proposer, or of another revision / goal (1ced7 R4 L1 L2)
k '            if rv.by.as_ref().is_some_and(|b| b.who() == s.seat) {' '            if false { // knife' \
  x31_the_proposer_does_not_review_itself
k '            if rv.revision != s.revision || rv.goal_sha != s.goal_sha_now {' \
  '            if rv.goal_sha != s.goal_sha_now { // knife' x30_a_review_of_another_revision_or_goal_does_not_count
# 7 created in the future (1ced7 R4 L6)
k '    if s.created_at > now.saturating_add(FUTURE_SLACK_S) {' '    if false { // knife' \
  x32_a_suggestion_created_in_the_future_holds
# config: a repeated key (1ced7 R4 L5)
k '        if let Some(w) = keys.windows(2).find(|w| w[0] == w[1]) {' \
  '        if let Some(w) = keys.windows(2).find(|w| w[0] == w[1] && false) { // knife' \
  c1_to_c4_config_rejects_what_it_does_not_know
# aib-policy
p '    if p.starts_with('"'"'/'"'"') || p.contains('"'"':'"'"') {' \
  '    if p.starts_with('"'"'/'"'"') && false { // knife' paths_that_climb_out_are_never_harmless
p '            ".." => return None,' '            ".." => segs.push(String::new()), // knife' paths_that_climb_out_are_never_harmless
# normalising: ./, // and case (1ced7 R4 M1)
p '            "" | "." => {}' '            "" => {} // knife' other_spellings_of_a_path_fall_in_the_same_areas
p '            s => segs.push(s.to_ascii_lowercase()),' '            s => segs.push(s.to_string()), // knife' \
  other_spellings_of_a_path_fall_in_the_same_areas
# the repo's own gates (1ced7 R4 M2)
p '    (Match::Suffix(".py"), Area::FrozenScript),' '    // knife: the .py rule removed' \
  the_repo_gates_and_frozen_scripts_fall_in_their_areas
p '    (Match::Prefix("scripts/knives-"), Area::SelfGate),' '    // knife: the knives rule removed' \
  the_repo_gates_and_frozen_scripts_fall_in_their_areas
p '    (Match::Under("crates/aib-consent/"), Area::SignConsent),' '    // knife: the consent rule removed' \
  the_repo_gates_and_frozen_scripts_fall_in_their_areas
# owner check
p '    crates.len() >= 2' '    crates.len() >= 3 // knife' owner_check_for_shared_core_or_two_crates
p '            None => true,' '            None => false, // knife' owner_check_reads_the_normalised_path
echo "reflect knives: all bit"
