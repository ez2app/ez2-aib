#!/usr/bin/env bash
# S79-p11 Windows knives (J0 refuse, timeout reads timeout, thermal unsupported), from the aibd Windows sessions knife step.
# Moved out of .github/workflows/aibd.yml word for word (519d0 11-16 23:5x: the workflow file is near
# its 450,000-byte guard; new knives live in crates/<crate>/knives/). aib-xtask knife-anchors reads
# crates/*/knives/*.sh, so every anchor here is still checked. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# S79-p11a (Windows acceptance 1.3): the test injection point no longer reaches the
# J0 step; aibd starts although "it could not join its Job" ⇒ the real-aibd test red
python3 -I scripts/knife.py crates/aibd/src/platform/lifecycle.rs \
  '        if j0_refused_by_test(|k| std::env::var(k).ok()) {' \
  '        if false && j0_refused_by_test(|k| std::env::var(k).ok()) { // knife' \
  --expect-fail a_refused_job_makes_aibd_refuse_to_start \
  -- cargo test -p aibd --locked --test j0_refuse_windows
# S79-p11b (acceptance 2.4): a run past its timeout_s is recorded as an API stop
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
  '            self.finish(id, stop, "timeout");' \
  '            self.finish(id, stop, "api"); // knife' \
  --expect-fail a_run_past_its_timeout_reads_timeout \
  -- cargo test -p aibd --locked --test sessions_windows a_run_past_its_timeout
# S79-p11c (acceptance 8): /api/v1/thermal answers as if this were macOS (not `unsupported`)
python3 -I scripts/knife.py crates/aibd/src/api.rs \
  '    Json(thermal_body(std::env::consts::OS, guard))' \
  '    Json(thermal_body("macos", guard)) // knife' \
  --expect-fail thermal_says_unsupported_on_windows \
  -- cargo test -p aibd --locked --test sessions_windows thermal_says
