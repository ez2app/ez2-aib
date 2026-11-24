#!/usr/bin/env bash
# aib-knives: os=unix
# S79-rust 3b (DECISION-council-driveweb r3): aibd reads an act round's work orders for web
# seats from its stdout; each rule loosened turns its own test red. Run from the repo root.
set -euo pipefail
dp() { python3 -I scripts/knife.py crates/aibd/src/drive_plan.rs "$1" "$2" --expect-fail "drive_plan::tests::$3" \
        -- cargo test -p aibd --locked --lib "drive_plan::tests::$3"; }
# DP1 a plan to a seat aibd did not list is taken (drive picks who the system writes to)
dp '                    && web.contains(&p.to)' '                    && (web.contains(&p.to) || !p.to.is_empty())' \
   only_a_listed_web_seats_work_order_in_the_exact_shape_is_a_plan
# DP2 a plan that is not a work order is taken
dp '(s("action") == "dispatch").then(' '(!s("action").is_empty()).then(' \
   only_a_listed_web_seats_work_order_in_the_exact_shape_is_a_plan
# DP3 a plan with an extra field (an origin drive made up) is taken
dp '                o.len() == 4' '                o.len() >= 4' \
   only_a_listed_web_seats_work_order_in_the_exact_shape_is_a_plan
# DP4 the same key twice in one round is two plans
dp 'if r.plans.len() < ROUND_MAX && keys.insert(p.send_key.clone()) =>' \
   'if r.plans.len() < ROUND_MAX && (keys.insert(p.send_key.clone()) || r.refused < usize::MAX) =>' \
   only_a_listed_web_seats_work_order_in_the_exact_shape_is_a_plan
# DP5 a round gives any number of plans
dp 'if r.plans.len() < ROUND_MAX && keys.insert(p.send_key.clone()) =>' \
   'if r.plans.len() < ROUND_MAX * 99 && keys.insert(p.send_key.clone()) =>' \
   a_round_gives_at_most_round_max_plans
dw() { python3 -I scripts/knife.py crates/aibd/src/drive_wake.rs "$1" "$2" --expect-fail "drive_wake::tests::$3" \
        -- cargo test -p aibd --locked --lib "drive_wake::tests::$3"; }
# DW1 the act round is not told aibd reads it, or not its mark (drive writes every web seat's
# letter NotSent, or prints a mark aibd then refuses)
dw '.env(crate::drive_plan::BY_AIBD_ENV, &round.mark)' '.env(crate::drive_plan::BY_AIBD_ENV, round.mark.to_uppercase())' \
   an_act_round_aibd_reads_is_told_the_web_seats_and_its_plan_lines_come_back
# DW2 the act round's stdout is dropped (its plans never reach the executor)
dw '            .stdout(Stdio::piped());' '            .stdout(Stdio::null());' \
   an_act_round_aibd_reads_is_told_the_web_seats_and_its_plan_lines_come_back
de() { python3 -I scripts/knife.py crates/aibd/src/drive_send.rs "$1" "$2" --expect-fail "drive_send::tests::$3" \
        -- cargo test -p aibd --locked --lib "drive_send::tests::$3"; }
# DS1 a seat gets any number of work orders (no pace on aibd's side, L3)
de '            if *key != plan.send_key && now.saturating_duration_since(*at) < PACE {' \
   '            if *key != plan.send_key && now.saturating_duration_since(*at) < Duration::ZERO {' \
   a_seat_gets_one_work_order_in_thirty_minutes_and_its_own_key_again
# DS2 the same key again is paced (a restarted round's letter never reaches system_send's answer)
de '            if *key != plan.send_key && now.saturating_duration_since(*at) < PACE {' \
   '            if (*key != plan.send_key || !key.is_empty()) && now.saturating_duration_since(*at) < PACE {' \
   a_seat_gets_one_work_order_in_thirty_minutes_and_its_own_key_again
# DS3 a seat taken back is still told to drive as a web seat
python3 -I scripts/knife.py crates/aib-store/src/seat_moves.rs \
  '            "SELECT old_code FROM seat_moves WHERE unmoved_at IS NULL ORDER BY old_code",' \
  '            "SELECT DISTINCT old_code FROM seat_moves ORDER BY old_code",' \
  --expect-fail seat_moves::tests::the_moved_codes_are_the_live_moves_only \
  -- cargo test -p aib-store --locked --lib seat_moves::tests::the_moved_codes_are_the_live_moves_only
# DW3 (5d33a REVIEW-driverust3 M1) plan lines are handed on with the switch off: the child's
# word for the switch is taken (the program at bin is any same-account seat's to replace)
dw '    if switch != "on" {' '    if switch == "never" {' \
   plan_lines_are_dropped_unless_aibd_reads_the_switch_on
# DP6 (5d33a REVIEW-driverust3 L1) a plan line with another round's mark is taken (a key with
# a newline in it, printed by drive, makes a line that looks like a plan)
dp '            .filter(|(m, _)| *m == round.mark.as_bytes())' \
   '            .filter(|(m, _)| m.len() == round.mark.len())' \
   only_a_listed_web_seats_work_order_in_the_exact_shape_is_a_plan
