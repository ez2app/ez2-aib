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
k '    let total = if has_evidence { s.total } else { s.total.min(NO_EVIDENCE_CAP) };' \
  '    let total = s.total; // knife' x03_x04_no_checkable_evidence_caps_the_total
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
  '    if s.proposer_bias > c.bias_max { // knife' x26_a_biased_scorer_triggers_nothing
# 14 resources
k '    if w.mainline_red {' '    if false { // knife' x18_a_warm_machine_starts_nothing
k '    if w.cooldown >= Cooldown::Warn {' '    if w.cooldown > Cooldown::Warn { // knife' x18_a_warm_machine_starts_nothing
k '    if w.seat_has_waiting_pr {' '    if false { // knife' x18_a_warm_machine_starts_nothing
# 15 caps
k '    if w.seat_inflight >= c.per_seat_inflight {' '    if w.seat_inflight > c.per_seat_inflight { // knife' x19_x20_caps
k '        .filter(|&&t| local_day(t, c.tz_offset_min) == today)' '        .filter(|_| true) // knife' \
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
# aib-policy
p '    if p.starts_with('"'"'/'"'"') || p.split('"'"'/'"'"').any(|seg| seg == "..") || p.contains('"'"':'"'"') {' \
  '    if false { // knife' paths_that_climb_out_are_never_harmless
p '    (Match::UnderWithSuffix("aib/", ".py"), Area::FrozenScript),' '    // knife: frozen .py rule removed' \
  paths_fall_in_their_areas
p '    crates.len() >= 2' '    crates.len() >= 3 // knife' owner_check_for_shared_core_or_two_crates
echo "reflect knives: all bit"
