#!/usr/bin/env bash
# aib-knives: os=windows
# #806 win-accept4 knives (a9136 11-15, 1ced7 preview.5 Windows run) that run on Windows, moved out of the
# "aibd Windows session adapter knives (Windows)" step word for word (RULES §21; run by the shared
# crate-knives step, crates/aib-knife/run-knives.sh rust, main #836). Each knife, put in, must turn its
# own test red. Run from the repo root.
set -eu
# WIN4-1 (a9136 11-15, 1ced7 Windows run: output always empty) the session hands
# over no pipes, or the root's stdout is null again
python3 -I scripts/knife.py crates/aibd/src/platform/session_windows.rs \
  '        let (out, err) = self.0.take_output()?;' \
  '        let (out, err) = self.0.take_output().filter(|_| false)?; // knife: no output' \
  --expect-fail session_output_arrives_on_windows \
  -- cargo test -p aibd --locked --test sessions_windows session_output_arrives_on_windows
python3 -I scripts/knife.py crates/aib-sys/src/session.rs \
  '            .stdout(Stdio::piped())' '            .stdout(Stdio::null()) // knife: stdout not captured' \
  --expect-fail session_output_arrives_on_windows \
  -- cargo test -p aibd --locked --test sessions_windows session_output_arrives_on_windows
# WIN4-2 windows_job: a process the kernel does not list in J0 passes, or the
# real J0 check reads the wrong pid (selfcheck must not say ok)
python3 -I scripts/knife.py crates/aibd/src/selfcheck.rs \
  '        Some(J0Seen::Joined { listed: false, .. }) => fail(' \
  '        Some(J0Seen::Joined { listed: false, breakaway: true, .. }) => fail( // knife' \
  --expect-fail selfcheck::tests::windows_job_says_what_the_j0_check_saw \
  -- cargo test -p aibd --locked --lib selfcheck::tests::windows_job_says_what_the_j0_check_saw
python3 -I scripts/knife.py crates/aibd/src/selfcheck.rs \
  '            listed: m.iter().any(|m| m.pid == me),' \
  '            listed: m.iter().any(|m| m.pid == me.wrapping_add(1)), // knife' \
  --expect-fail selfcheck_json_lists_every_row_and_process_management_is_a_real_run \
  -- cargo test -p aibd --locked --test cli selfcheck_json_lists_every_row_and_process_management_is_a_real_run
