#!/usr/bin/env bash
# 送回倉 knives SB1-SB10 (a9136 11-20, N-webonly-c6-merge; DESIGN-web-merge-a9136-1120 r2,
# 5d33a PREREAD-webmerge M1 L1 L2): a taken branch is never moved; a link among the copy's
# object files is not followed; objects/info is not read; git's object check stays on; settings,
# a submodule file and a link are refused; the count is against every ref of the repo.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
sb() { python3 -I scripts/knife.py crates/aibd/src/send_back.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
# SB1 a taken branch is moved (update-ref without the all-zero old value)
sb 'sha, ZERO]) {' \
   'sha, ZERO]).or_else(|_| git(repo, hooks, &["update-ref", &full, sha])) {' \
   a_taken_name_is_never_moved_the_next_number_is_used
# SB2 an object file that is a link is followed
sb '.custom_flags(nix::fcntl::OFlag::O_NOFOLLOW.bits())' \
   '.custom_flags(nix::fcntl::OFlag::empty().bits())' \
   an_object_file_that_is_a_link_is_not_copied
# SB3 objects/info (alternates) is walked too
sb 'if !(is_pack || hex_name(name, 2)) {' \
   'if !(is_pack || hex_name(name, 2) || name == "info") {' \
   the_copy_budget_refuses_more_files_or_bytes
# SB4 git's object check is off
sb '"fetch.fsckObjects=true",' \
   '"fetch.fsckObjects=false",' \
   a_tree_naming_dot_git_is_refused_by_the_object_check
# SB5 a link goes back
sb '|| new_mode == SYMLINK_MODE' \
   '|| (new_mode == SYMLINK_MODE && false)' \
   settings_a_workflow_a_submodule_file_and_a_link_are_refused_by_name
# SB6 .gitmodules / .lfsconfig go back
sb '|| CHECKOUT_SETTINGS.contains(&base_name(path).as_str())' \
   '|| (CHECKOUT_SETTINGS.contains(&base_name(path).as_str()) && false)' \
   settings_a_workflow_a_submodule_file_and_a_link_are_refused_by_name
# SB7 provider settings and workflows go back
sb '    loads_as_settings(path)' \
   '    (loads_as_settings(path) && false)' \
   settings_a_workflow_a_submodule_file_and_a_link_are_refused_by_name
# SB8 counted against the checked-out branch only: a head already sent is sent again
sb '&["rev-list", "--count", sha, "--not", "--all"]' \
   '&["rev-list", "--count", sha, "^HEAD"]' \
   a_head_the_repo_already_reaches_is_nothing_to_send
# SB9 nothing to send is a plan
sb 'if commits == 0 {' \
   'if commits == 0 && false {' \
   a_head_the_repo_already_reaches_is_nothing_to_send
# SB10 the copy budget is not kept
sb 'if left.files == 0 || meta.len() > left.bytes {' \
   'if (left.files == 0 || meta.len() > left.bytes) && false {' \
   the_copy_budget_refuses_more_files_or_bytes
