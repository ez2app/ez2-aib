#!/usr/bin/env bash
# N-web-worktree knives WT1–WT9 (b4297 11-19; DECISION-council-webonly-q12-519d0-1119 Q1 (a),
# BOUNDARY-webonly-5d33a-1119 §1 / r2 §1.4): a dgrs seat's own git worktree of a registered
# repo, opened with no git hooks, its project settings listed and checked before every open,
# and taken away only without losing work. Each rule, broken, turns its own test red.
# Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
wt() { python3 -I scripts/knife.py crates/aibd/src/seat_worktree.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
# WT1 git runs the repo's own hooks while aibd opens a seat's tree
wt '        .arg(format!("core.hooksPath={}", hooks.display()))' \
   '        .arg(format!("core.askPass={}", hooks.display())) // knife' \
   a_tree_opens_without_hooks_and_what_is_left_is_counted_before_it_goes
# WT2 the tree does not keep the empty hooks folder in its own config
wt '        &["config", "--worktree", "core.hooksPath", &hooks_s],' \
   '        &["config", "--worktree", "core.askPass", &hooks_s], // knife' \
   a_tree_opens_without_hooks_and_what_is_left_is_counted_before_it_goes
# WT3 a tree whose settings changed since the fingerprint still opens
wt '        Ok(now) if &now == registered => Ok(()),' \
   '        Ok(now) if &now == registered || true => Ok(()), // knife' \
   a_tree_whose_settings_moved_is_refused_and_an_equal_one_opens
# WT4 a top settings file linked to somewhere else is read through
wt '        if meta.file_type().is_symlink() {' \
   '        if meta.file_type().is_symlink() && false { // knife' \
   a_linked_settings_file_is_refused
# WT5 the hooks a settings file adds are left off the list the person sees
wt '    for k in WATCHED_KEYS {' \
   '    for k in WATCHED_KEYS.into_iter().filter(|k| *k != "hooks") { // knife' \
   the_list_names_every_settings_file_and_only_the_watched_keys
# WT6 files not committed are not counted before a tree goes
wt '        uncommitted: st.lines().filter(|l| !l.trim().is_empty()).count(),' \
   '        uncommitted: st.lines().filter(|l| !l.trim().is_empty()).count().min(0), // knife' \
   a_tree_opens_without_hooks_and_what_is_left_is_counted_before_it_goes
# WT7 a tree with uncommitted files is forced away
wt '    git(repo, hooks, &["worktree", "remove", &tree_s]).map(|_| ())' \
   '    git(repo, hooks, &["worktree", "remove", "--force", &tree_s]).map(|_| ()) // knife' \
   a_tree_with_uncommitted_files_is_not_taken_away
# WT8 a repo name may start with '-' (read by git as an option)
wt "        && !name.starts_with('-')" \
   "        && !name.starts_with('#') // knife" \
   names_paths_and_branches_have_one_shape
# WT9 the files under .claude/ are not listed
wt '        Ok(meta) if meta.is_dir() => walk(top, &d, 0, &mut m)?,' \
   '        Ok(meta) if meta.is_dir() && false => walk(top, &d, 0, &mut m)?, // knife' \
   the_list_names_every_settings_file_and_only_the_watched_keys
# WT10 a FIFO under .claude/ is read (and holds aibd's check open for ever) — 5d33a L1
wt '        if !ft.is_file() {' \
   '        if !ft.is_file() && false { // knife' \
   a_fifo_among_the_settings_is_refused_not_read
# WT11 git runs the fsmonitor program a seat wrote into the shared .git/config — 5d33a L2
wt '        .args(["-c", "core.fsmonitor=false", "-c", "protocol.allow=never"])' \
   '        .args(["-c", "protocol.allow=never"]) // knife' \
   an_fsmonitor_program_in_the_shared_config_never_runs
# WT12 a CLAUDE.md / AGENTS.md below the top is left off the list — 5d33a L3
wt "    let mut nested: Vec<&str> = listed.split('\\0').filter(|r| r.contains('/')).collect();" \
   "    let mut nested: Vec<&str> = listed.split('\\0').filter(|r| r.contains('/') && false).collect(); // knife" \
   a_claude_md_in_a_sub_folder_is_listed_and_a_new_one_is_caught
# WT13 the shared .git/config changing goes unseen — 5d33a L2
wt '    m.git_config = sha12(&config_but_aibds_own(&cfg));' \
   '    m.git_config = sha12(&config_but_aibds_own(&cfg)[..0]); // knife' \
   the_shared_git_config_changing_is_caught
# WT14 the key aibd writes into the shared config when it opens a tree counts as a seat's
# change, so the second seat of a repo is refused (b4297 11-19 wiring)
wt '        if t == "[extensions]" || t == "worktreeconfig=true" {' \
   '        if false && (t == "[extensions]" || t == "worktreeconfig=true") { // knife' \
   a_second_tree_of_the_same_repo_still_matches_what_was_registered
# WT15 an archived seat's tree is taken away with uncommitted work in it (b4297 11-20 collect)
wt '    if left.uncommitted > 0 {' \
   '    if false && left.uncommitted > 0 { // knife' \
   an_archived_seats_tree_goes_only_when_nothing_is_uncommitted_and_its_branch_stays
# WT16 a clean tree taken away is reported as kept
wt '    c.removed = true;' \
   '    c.removed = false; // knife' \
   an_archived_seats_tree_goes_only_when_nothing_is_uncommitted_and_its_branch_stays
# WT17 (5d33a REVIEW-gitinseatrepo M1) every settings key passes, a program key among them
wt '        if !ok {' \
   '        if false && !ok { // knife' \
   only_plain_settings_keys_pass_and_a_program_key_is_named
# WT18 git runs in the author's repo whatever its settings name (filter, alternateRefsCommand)
wt '    repo_safe(&base_dir)?;' \
   '    let _ = repo_safe(&base_dir); // knife' \
   an_authors_repo_naming_a_program_is_not_run_git_in
# WT19 (M2) a registered tree is run git in wherever its .git file points
wt '    repo_safe(repo_top)?;' \
   '    if repo_top.exists() { return Ok(()); } // knife' \
   a_registered_tree_pointed_elsewhere_is_not_run_git_in
# WT20 (5d33a M1' c18) a .git/commondir pointing at another repository's config passes
wt '    for f in ["commondir", "info/attributes", "objects/info/alternates"] {' \
   '    for f in ["info/attributes", "objects/info/alternates"] { // knife' \
   settings_pulled_from_elsewhere_make_a_repo_unsafe
