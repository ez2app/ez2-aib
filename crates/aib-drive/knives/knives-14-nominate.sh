#!/usr/bin/env bash
# aib-knives: os=unix
# The S79 nominate-x1b knives (043dd 268edfec, 5d33a bd1a762b N1): u13-exclude.txt marks a seat system
# whatever its role; the reviewers file still changes only dev seats. They sat in the "aib-drive unblock
# knives (macOS)" step; moved here word for word (519d0 11-16 23:5x: aibd.yml is red past 450,000 bytes).
# UX1 / NK28 / NK29 here REPLACE the inst13 (ba50f5b5) lines of the same name: mark_role gained a
# role filter in 268edfec, so the inst13 anchors no longer exist; knives-12-unblock.sh must not keep them.
# aibd.yml runs every crates/aib-drive/knives/*.sh in name order in one step; aib-xtask knife-anchors
# reads crates/*/knives/*.sh. Run from the repo root; -eo pipefail is what GitHub's `shell: bash` gave it.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
uk() { python3 -I scripts/knife.py crates/aib-drive/src/unblock.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --lib "$3"; }
# UX1 (519d0 11-16) a seat in u13-exclude.txt keeps its role and can be nominated
uk '    mark_role(seats, list, "system", |_| true)' '    mark_role(seats, list, "dev", |_| true) // knife' \
   a_seat_in_the_exclude_file_is_marked_system_and_comments_are_ignored
# NK28 (a9136 M1) the reviewers file marks no one
uk '    mark_role(seats, list, "reviewer", |r| r == "dev")' '    mark_role(seats, list, "dev", |r| r == "dev") // knife' \
   a_seat_in_the_reviewers_file_is_marked_reviewer_and_none_without_it
# NK29 (a9136 r2 N1) the reviewers file turns a system seat into a reviewer
uk '    mark_role(seats, list, "reviewer", |r| r == "dev")' '    mark_role(seats, list, "reviewer", |r| r != "system") // knife' \
   a_seat_in_the_reviewers_file_is_marked_reviewer_and_none_without_it
# NK31 (5d33a bd1a762b N1) the exclude file marks only dev seats
uk '    mark_role(seats, list, "system", |_| true)' '    mark_role(seats, list, "system", |r| r == "dev") // knife' \
   a_seat_in_the_exclude_file_is_marked_system_and_comments_are_ignored
