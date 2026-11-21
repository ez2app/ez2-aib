#!/usr/bin/env bash
# WN1–WN3: a work seat may add a file by name, and a claude work seat's system prompt starts with
# every command it may run (519d0 11-21 M6; 0eed7 VERIFY-pv8 r2 M6: Andy's seat had
# `git add README.md` refused and stopped, not knowing what it may run)
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# WN1 a file is not added by name
python3 -I scripts/knife.py crates/aib-seatopen/src/lib.rs \
  '    "Bash(git add *)", // a file by name (519d0 11-21 M6)' \
  '    "Bash(git add -A -- *)", // a file by name (519d0 11-21 M6)' \
  --expect-fail a_work_seat_adds_files_by_name_and_is_told_what_it_may_run \
  -- cargo test -p aib-seatopen --locked --lib a_work_seat_adds_files_by_name_and_is_told_what_it_may_run
# WN2 the note leaves the commands out
python3 -I scripts/knife.py crates/aib-seatopen/src/lib.rs \
  '        .map(|c| format!("- `{}`", c.replace(" *", " …")))' \
  '        .map(|c| format!("- {}", c.replace(" *", " …")))' \
  --expect-fail a_work_seat_adds_files_by_name_and_is_told_what_it_may_run \
  -- cargo test -p aib-seatopen --locked --lib a_work_seat_adds_files_by_name_and_is_told_what_it_may_run
# WN3 a claude work seat opens without the note
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
  '    provider == "claude" && mode == "work"' \
  '    provider == "claude" && mode == "safe"' \
  --expect-fail a_work_seat_is_told_its_commands_before_its_card \
  -- cargo test -p aibd --locked --lib a_work_seat_is_told_its_commands_before_its_card
