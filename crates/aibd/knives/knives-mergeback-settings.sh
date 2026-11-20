#!/usr/bin/env bash
# N-webonly-c23: 合回 refuses commits that change files an AI loads as settings (5d33a H1)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
k() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --lib "$4"; }
# MS1 a .codex / .claude folder deeper in the repo is missed
k crates/aibd/src/seat_worktree.rs '    parts.iter().any(dir) || parts.last().is_some_and(file)' \
     '    parts.first().is_some_and(dir) || parts.last().is_some_and(file)' \
     settings_paths_are_found_at_any_depth_and_case
# MS2 case matters (.Claude on a case-insensitive folder)
k crates/aibd/src/seat_worktree.rs "    let parts: Vec<String> = path.split('/').map(str::to_ascii_lowercase).collect();" \
     "    let parts: Vec<String> = path.split('/').map(str::to_owned).collect(); // knife" \
     settings_paths_are_found_at_any_depth_and_case
# MS3 AGENTS.override.md is not looked at
k crates/aibd/src/seat_worktree.rs '            .chain(SETTINGS_FILES_MORE.iter())' \
     '            .chain(SETTINGS_FILES_MORE.iter().take(0)) // knife' \
     settings_paths_are_found_at_any_depth_and_case
# MS4 the refusal is never returned
k crates/aibd/src/seat_worktree.rs '        return Ok(MergeBack::SettingsTouched(touched));' \
     '        let _ = touched; // knife' \
     a_commit_touching_provider_settings_is_not_merged_back
# MS5 only the net change is read (a settings file added then removed slips through)
k crates/aibd/src/seat_worktree.rs '            "^HEAD",' \
     '            "^HEAD", "--max-count=1", // knife' \
     a_commit_touching_provider_settings_is_not_merged_back
# MS6 the card is not a decide card
k crates/aibd/src/slots.rs '        "seat.merge_settings" => Some(1),' \
     '        "seat.merge_settings" => Some(2), // knife' \
     a_merge_back_conflict_is_a_decide_card_naming_its_files
# MS7 a workflow (runs on push with the repo's secrets) is merged back (5d33a L1)
k crates/aibd/src/seat_worktree.rs '        .any(|w| w[0] == ".github" && GITHUB_RUNS.contains(&w[1].as_str()));' \
     '        .any(|w| w[0] == ".github" && GITHUB_RUNS.is_empty()); // knife' \
     settings_paths_are_found_at_any_depth_and_case
