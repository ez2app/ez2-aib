#!/usr/bin/env bash
# aib-knives: os=unix
# plan fields through aibd (b4297 11-19, #862 receipt pm1: main's SPEC-plan-fields `fields` met the
# chain's aib-plan-via-aibd path): touches / branch / verifier go to aibd and back into aib-plan's
# writer. Each knife breaks one rule and must turn its own test red. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
pr() { python3 -I scripts/knife.py crates/aibd/src/plan_route.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aibd --locked --lib "plan_route::tests::$3"; }
va() { python3 -I scripts/knife.py crates/aib-plan/src/via_aibd.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-plan --locked --lib "via_aibd::tests::$3"; }
# PF1 aibd drops touches it was sent
pr '                touches: texts(body, "touches")?,' \
   '                touches: texts(body, "touches").map(|_| None)?, // knife' \
   a_set_carries_the_plan_fields_through_aibd
# PF2 a touches that is not a list is taken as not given
pr '        _ => Err(format!("{k} must be a list of at most 200 texts")),' \
   '        _ => Ok(None), // knife' \
   a_set_carries_the_plan_fields_through_aibd
# PF3 aib-plan does not send the branch to aibd
va '                v["branch"] = json!(b);' \
   '                let _ = b; // knife' \
   the_body_carries_the_op_and_the_seat_only_with_the_start_up_token
