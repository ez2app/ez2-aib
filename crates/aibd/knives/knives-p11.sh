#!/usr/bin/env bash
# aib-knives: os=unix
# S79-p11 knives (aibd lifecycle J0 switch, session timeout_s, thermal answer), from the shared aibd knife step.
# Moved out of .github/workflows/aibd.yml word for word (519d0 11-16 23:5x: the workflow file is near
# its 450,000-byte guard; new knives live in crates/<crate>/knives/). aib-xtask knife-anchors reads
# crates/*/knives/*.sh, so every anchor here is still checked. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# S79-p11a (Windows acceptance 1.3) the test switch must be exactly "1", nothing looser
python3 -I scripts/knife.py crates/aibd/src/platform/lifecycle.rs \
  '    lookup(J0_REFUSE_ENV).as_deref() == Some("1")' \
  '    lookup(J0_REFUSE_ENV).is_some() // knife' \
  --expect-fail only_the_value_1_makes_the_j0_step_refuse \
  -- cargo test -p aibd --locked --lib platform::lifecycle
# S79-p11b (acceptance 2.4) KT1 timeout_s range check gone (0 and 86401 accepted)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '        Some(t) if (1..=MAX_TIMEOUT_S).contains(&t) => Some(Duration::from_secs(t)),' \
  '        Some(t) if (1..=MAX_TIMEOUT_S).contains(&t) || t > 0 => Some(Duration::from_secs(t)), // knife' \
  --expect-fail sessions::tests::timeout_s_is_one_second_to_one_day \
  -- cargo test -p aibd --locked --lib sessions::tests::timeout_s_is_one_second_to_one_day
# KT2 the run that ran out of time is recorded as an API stop, not `timeout`
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '            self.finish(id, stop, "timeout");' \
  '            self.finish(id, stop, "api"); // knife' \
  --expect-fail a_run_past_its_timeout_is_stopped_and_says_timeout \
  -- cargo test -p aibd --locked --test sessions a_run_past_its_timeout
# KT3 no deadline thread: the run is never stopped
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '                    .spawn(move || me.watch_timeout(&tid, t))' \
  '                    .spawn(move || if false { me.watch_timeout(&tid, t) }) // knife' \
  --expect-fail a_run_past_its_timeout_is_stopped_and_says_timeout \
  -- cargo test -p aibd --locked --test sessions a_run_past_its_timeout
# KT4 `aibd ctl session start --timeout-s` range check gone
python3 -I scripts/knife.py crates/aibd/src/ctl.rs \
  '        Ok(t) if (1..=aibd::sessions::MAX_TIMEOUT_S).contains(&t) => Ok(t),' \
  '        Ok(t) => Ok(t), // knife' \
  --expect-fail ctl::tests::the_timeout_option_takes_one_second_to_one_day \
  -- cargo test -p aibd --locked --bin aibd ctl::tests::the_timeout_option_takes_one_second_to_one_day
# S79-p11c (acceptance 8) KH1 a platform with no sensor is no longer said `unsupported`
python3 -I scripts/knife.py crates/aibd/src/api.rs \
  '        None if os != "macos" => serde_json::json!({' \
  '        None if false => serde_json::json!({ // knife' \
  --expect-fail api::tests::the_thermal_answer_says_unsupported_where_there_is_no_sensor \
  -- cargo test -p aibd --locked --lib api::tests::the_thermal_answer_says_unsupported_where_there_is_no_sensor
