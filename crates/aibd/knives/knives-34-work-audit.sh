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
# WG3 the open no longer makes the folder a repository (the page's real entry, N-webonly-c4)
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
   '        if let Ok(Err(e)) = tokio::task::spawn_blocking(move || init_work_repo(&dir, &env)).await {' \
   '        if let Ok(Err(e)) = tokio::task::spawn_blocking(move || { let _ = (&dir, &env); Ok::<bool, String>(false) }).await { // knife' \
   --expect-fail a_work_seat_opens_in_another_work_seats_own_folder_and_no_other \
   -- cargo test -p aibd --locked --test seats_page_open_dgrs a_work_seat_opens_in_another_work_seats_own_folder_and_no_other
# WH1 inside a preview the stand-in HOME is the home, so the real home's .claude refuses a
# with_seat work seat (0eed7 VERIFY-realentry-79c88372 H)
wg '    let preview = lookup("AIBD_PREVIEW").as_deref() == Some("1");' \
   '    let preview = lookup("AIBD_PREVIEW").as_deref() == Some("never"); // knife' \
   inside_a_preview_the_handed_real_home_is_the_home

# WT1 a .claude holding only claude's own .cc-writes refuses the work seat's folder (0eed7 VERIFY-realentry-f68d3048 M1)
python3 scripts/knife.py crates/aibd/src/seats.rs '        if !CLAUDE_OWN_TRAIL.iter().any(|t| name == *t) {' '        if !CLAUDE_OWN_TRAIL.iter().any(|t| name == *t) || any || !any {' --expect-fail a_claude_folder_holding_only_claudes_own_trail_is_no_project_settings \
  -- cargo test -p aibd --locked --lib seats::tests::a_claude_folder_holding_only_claudes_own_trail_is_no_project_settings
# WT2 anything else in .claude is taken as the trail too
python3 scripts/knife.py crates/aibd/src/seats.rs '        if !CLAUDE_OWN_TRAIL.iter().any(|t| name == *t) {' '        if !CLAUDE_OWN_TRAIL.iter().any(|t| name == *t) && name.is_empty() {' --expect-fail a_claude_folder_holding_only_claudes_own_trail_is_no_project_settings \
  -- cargo test -p aibd --locked --lib seats::tests::a_claude_folder_holding_only_claudes_own_trail_is_no_project_settings

# WF1 a third work seat is let into a shared folder (DECISION-workseat-3plus-a9136-1120 3)
python3 -I scripts/knife.py crates/aibd/src/seats.rs '            Some((c, _)) if work_seats_in(&st, &c).await >= MAX_WORK_SEATS_PER_FOLDER => {' '            Some((c, _)) if work_seats_in(&st, &c).await > MAX_WORK_SEATS_PER_FOLDER => { // knife' --expect-fail a_work_seat_opens_in_another_work_seats_own_folder_and_no_other \
  -- cargo test -p aibd --locked --test seats_page_open_dgrs a_work_seat_opens_in_another_work_seats_own_folder_and_no_other
# WF2 own_tree is ignored: the third seat falls on the shared folder's cap
python3 -I scripts/knife.py crates/aibd/src/seats.rs '            Some((c, owner)) if req.own_tree => {' '            Some((c, owner)) if req.own_tree && c.is_empty() => { // knife' --expect-fail a_work_seat_with_its_own_tree_writes_beside_the_folder_not_in_it \
  -- cargo test -p aibd --locked --test seats_page_open_dgrs a_work_seat_with_its_own_tree_writes_beside_the_folder_not_in_it
# WP1 a work seat's PATH leaves the xcrun stub first (519d0 VERIFY-selftest L4)
python3 -I scripts/knife.py crates/aibd/src/seats.rs '    if !is_file(&bin.join("git")) {' '    if !is_file(&bin.join("git")) || bin.exists() || !bin.exists() { // knife' --expect-fail a_work_seats_git_skips_the_xcrun_stub_when_a_developer_git_is_there \
  -- cargo test -p aibd --locked --lib seats::tests::a_work_seats_git_skips_the_xcrun_stub_when_a_developer_git_is_there
# FO1 a seat sharing another seat's folder does not say whose it is
python3 -I scripts/knife.py crates/aibd/src/seats.rs '        return (name != me && is_seat(name))' '        return (name != me && is_seat(name) && false) // knife' --expect-fail a_seat_says_whose_folder_or_tree_it_works_in \
  -- cargo test -p aibd --locked --lib seats::tests::a_seat_says_whose_folder_or_tree_it_works_in
# FO2 a seat in its own tree off another seat's folder does not say whose
python3 -I scripts/knife.py crates/aibd/src/seats.rs '    let owner = base_author(p, read)?;' '    let owner = base_author(p, read).filter(|_| false)?; // knife' --expect-fail a_seat_says_whose_folder_or_tree_it_works_in \
  -- cargo test -p aibd --locked --lib seats::tests::a_seat_says_whose_folder_or_tree_it_works_in
# SA1 the page's seat_archive is not taken
python3 -I scripts/knife.py crates/aibd/src/seat_input.rs '        Some(ARCHIVE) => return seat_archive(st, page, body).await,' '        Some(ARCHIVE) if false => return seat_archive(st, page, body).await, // knife' --expect-fail the_page_archives_a_stopped_seat_and_not_a_running_one \
  -- cargo test -p aibd --locked --test seat_restart the_page_archives_a_stopped_seat_and_not_a_running_one
# WF3 a restart does not count the folder's writers (5d33a PREREAD-nx d523a5af L1)
python3 -I scripts/knife.py crates/aibd/src/seat_input.rs '    if crate::seats::restart_overfills(&st, seat_id).await {' '    if crate::seats::restart_overfills(&st, seat_id).await && false { // knife' --expect-fail restarting_a_seat_into_a_full_folder_is_refused \
  -- cargo test -p aibd --locked --test seats_page_open_dgrs restarting_a_seat_into_a_full_folder_is_refused
# TS1 a tree's state counts nothing not merged
python3 -I scripts/knife.py crates/aibd/src/seat_worktree.rs '    let present = git(' '    let present = true || git( // knife' --expect-fail a_seats_copy_is_its_own_clone_and_says_what_is_not_merged \
  -- cargo test -p aibd --locked --lib seat_worktree::tests::a_seats_copy_is_its_own_clone_and_says_what_is_not_merged
# TS2 the seat list leaves out a tree seat's state
python3 -I scripts/knife.py crates/aibd/src/seats.rs '        if v["folder_owner"]["how"] != "tree" {' '        if v["folder_owner"]["how"] != "tree" || v.is_object() { // knife' --expect-fail a_work_seat_with_its_own_tree_writes_beside_the_folder_not_in_it \
  -- cargo test -p aibd --locked --test seats_page_open_dgrs a_work_seat_with_its_own_tree_writes_beside_the_folder_not_in_it
# MB1 a conflict is not taken back (the author's folder is left half-merged)
python3 -I scripts/knife.py crates/aibd/src/seat_worktree.rs '        if git(&base_dir, hooks, &["merge", "--abort"]).is_err() {' '        if (&base_dir, hooks).1.exists() && false { // knife' --expect-fail a_seats_copy_merges_back_and_a_conflict_leaves_the_base_as_it_was \
  -- cargo test -p aibd --locked --lib seat_worktree::tests::a_seats_copy_merges_back_and_a_conflict_leaves_the_base_as_it_was
# MB2 a base with changes not committed is merged into
python3 -I scripts/knife.py crates/aibd/src/seat_worktree.rs '    if changed(&base_dir)? {' '    if changed(&base_dir)? && false { // knife' --expect-fail a_seats_copy_merges_back_and_a_conflict_leaves_the_base_as_it_was \
  -- cargo test -p aibd --locked --lib seat_worktree::tests::a_seats_copy_merges_back_and_a_conflict_leaves_the_base_as_it_was
# MB3 a running seat's tree is merged back
python3 -I scripts/knife.py crates/aibd/src/seats.rs '    if st.sessions.live_seats().contains(&seat_id) {' '    if st.sessions.live_seats().contains(&seat_id) && false { // knife' --expect-fail a_work_seat_with_its_own_tree_writes_beside_the_folder_not_in_it \
  -- cargo test -p aibd --locked --test seats_page_open_dgrs a_work_seat_with_its_own_tree_writes_beside_the_folder_not_in_it
# QS1 a seat waiting for a slot does not say so
python3 -I scripts/knife.py crates/aibd/src/sessions.rs '            .filter_map(|(i, q)| q.rec.seat_id.clone().map(|s| (s, i + 1)))' '            .filter_map(|(i, q)| q.rec.seat_id.clone().map(|s| (s, i + 1)).filter(|_| false))' --expect-fail a_seat_waiting_for_a_slot_says_its_place_and_the_limit \
  -- cargo test -p aibd --locked --test seat_restart a_seat_waiting_for_a_slot_says_its_place_and_the_limit
# MC1 a merge-back conflict is no decide card
python3 -I scripts/knife.py crates/aibd/src/slots.rs '        "seat.merge_conflict" => Some(1),' '        "seat.merge_conflict" => Some(6), // knife' --expect-fail a_merge_back_conflict_is_a_decide_card_naming_its_files \
  -- cargo test -p aibd --locked --lib slots::tests::a_merge_back_conflict_is_a_decide_card_naming_its_files
# AB1 a merge back goes into an author seat in a turn
python3 -I scripts/knife.py crates/aibd/src/seats.rs '    if !author.is_empty() && st.sessions.in_turn(&author) {' '    if !author.is_empty() && st.sessions.in_turn(&author) && false { // knife' --expect-fail a_work_seat_with_its_own_tree_writes_beside_the_folder_not_in_it \
  -- cargo test -p aibd --locked --test seats_page_open_dgrs a_work_seat_with_its_own_tree_writes_beside_the_folder_not_in_it
# VM1 /version does not say how many sessions run at once
python3 -I scripts/knife.py crates/aibd/src/workspace.rs '    body["max_sessions"] = json!(st.sessions.max_running());' '    body["max_sessions"] = json!(st.sessions.max_running() * 0 + 99); // knife' --expect-fail a_seat_waiting_for_a_slot_says_its_place_and_the_limit \
  -- cargo test -p aibd --locked --test seat_restart a_seat_waiting_for_a_slot_says_its_place_and_the_limit
# CL1 removed (b4297, 5d33a PREREAD-fileurl L2): the copy comes over a file:// URL, where git
# ignores --local and never hard-links, so the knife cannot bite; knives-27 FU2 guards the URL
# CL2 a copy's head is read through a branch name with ..
python3 -I scripts/knife.py crates/aibd/src/seat_worktree.rs '        && !branch.contains("..")' '        && !branch.is_empty() // knife' --expect-fail a_copys_head_is_read_without_git_and_a_bad_branch_name_is_not \
  -- cargo test -p aibd --locked --lib seat_worktree::tests::a_copys_head_is_read_without_git_and_a_bad_branch_name_is_not
# BU1 an author's repo naming a program in its config is merged into (5d33a REVIEW-gitinseatrepo M1)
python3 -I scripts/knife.py crates/aibd/src/seat_worktree.rs '    if let Err(why) = repo_safe(&base_dir) {' '    if let Err(why) = repo_safe(&base_dir).and(Err::<(), String>(String::new())).or(Ok::<(), String>(())) { // knife' --expect-fail an_authors_repo_naming_a_program_in_its_config_is_not_merged_into \
  -- cargo test -p aibd --locked --lib seat_worktree::tests::an_authors_repo_naming_a_program_in_its_config_is_not_merged_into
# BU4 the seat list fetches into the author's repo
python3 -I scripts/knife.py crates/aibd/src/seat_worktree.rs '    let unmerged = if present {' '    let _ = take_head(tree, hooks); let unmerged = if present { // knife' --expect-fail a_seats_copy_is_its_own_clone_and_says_what_is_not_merged \
  -- cargo test -p aibd --locked --lib seat_worktree::tests::a_seats_copy_is_its_own_clone_and_says_what_is_not_merged
# MU1 a refused 合回 for its settings is no 壞了 card
python3 -I scripts/knife.py crates/aibd/src/slots.rs '        "seat.merge_unsafe" => Some(2),' '        "seat.merge_unsafe" => Some(6), // knife' --expect-fail a_merge_back_conflict_is_a_decide_card_naming_its_files \
  -- cargo test -p aibd --locked --lib slots::tests::a_merge_back_conflict_is_a_decide_card_naming_its_files
