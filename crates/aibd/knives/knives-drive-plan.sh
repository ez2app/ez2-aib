#!/usr/bin/env bash
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
# DW1 the act round is not told aibd reads it (drive writes every web seat's letter NotSent)
dw '.env(crate::drive_plan::BY_AIBD_ENV, "1")' '.env(crate::drive_plan::BY_AIBD_ENV, "0")' \
   an_act_round_aibd_reads_is_told_the_web_seats_and_its_plan_lines_come_back
# DW2 the act round's stdout is dropped (its plans never reach the executor)
dw '            .stdout(Stdio::piped());' '            .stdout(Stdio::null());' \
   an_act_round_aibd_reads_is_told_the_web_seats_and_its_plan_lines_come_back
