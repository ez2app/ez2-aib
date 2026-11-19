#!/usr/bin/env bash
# what a work seat does, journaled; each seat commits as itself (a9136 11-19, W1 condition 6,
# 0eed7 VERIFY N2). Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
wa() { python3 -I scripts/knife.py crates/aibd/src/work_audit.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "work_audit::tests::$3"; }
# WA1 the after hash is the before hash
wa '        let after = sha12(p);' '        let after = o.before.clone(); // knife' \
   a_work_seats_file_change_and_command_are_each_one_record
# WA2 a failed command is recorded as passed
wa '               "command": scrub(command, home), "failed": failed, "secs": secs}),' \
   '               "command": scrub(command, home), "failed": false, "secs": secs}), // knife' \
   a_work_seats_file_change_and_command_are_each_one_record
# WA3 a read tool is recorded as a change
wa '    let key = FILE_TOOLS.iter().find(|(t, _)| *t == name)?.1;' \
   '    let key = FILE_TOOLS.iter().find(|(t, _)| *t == name).map_or("file_path", |x| x.1); // knife' \
   a_work_seats_file_change_and_command_are_each_one_record
# WA4 every work seat commits under one name
wa '    let who = format!("{name} ({short})");' '    let who = format!("trial ({short})"); let _ = name; // knife' \
   each_work_seat_commits_as_itself
# WA5 the home folder is written into a record
wa '        t = t.replace(h, "~");' '        t = t.replace(h, h); // knife' \
   a_record_says_no_home_folder_and_no_key_shape
# WA6 a key-like run is kept
wa '        if run.chars().count() >= SECRET_RUN {' '        if run.chars().count() >= usize::MAX { // knife' \
   a_record_says_no_home_folder_and_no_key_shape
# WG1 / WG2 a work seat's folder is a repository with one commit by the seat, made once
# (0eed7 VERIFY-mom-preview-entry r1)
wg() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "seats::tests::$3"; }
wg '    git(&["init", "-q"])?;' '    // knife' \
   a_work_seats_folder_is_a_repository_with_one_commit_by_the_seat
wg '    if dir.join(".git").exists() {' '    if false { // knife' \
   a_work_seats_folder_is_a_repository_with_one_commit_by_the_seat
