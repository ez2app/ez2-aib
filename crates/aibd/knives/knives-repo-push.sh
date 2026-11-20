#!/usr/bin/env bash
# RP21–RP37 G2: a registered repo's web/ branch is pushed from aibd's own repository, with the owner's fingerprint (DESIGN-repo-push-g2-7b81e-1120; 5d33a PREREAD-repopush-g2 L1 L2)
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
pu() { python3 -I scripts/knife.py crates/aibd/src/repo_push.rs "$1" "$2" --expect-fail "repo_push::tests::$3" \
        -- cargo test -p aibd --locked --lib "repo_push::tests::$3"; }
# RP21 a branch with a wrong date or number is pushed
pu '    code_ok && date_ok && n_ok' \
   '    code_ok && (date_ok || n_ok)' \
   only_a_web_branch_of_the_made_shape_is_pushed
# RP22 an option-looking remote is taken
pu "    if remote.starts_with('-') || remote.chars().any(char::is_whitespace) {" \
   "    if remote.is_empty() {" \
   only_an_ssh_or_https_remote_is_one
# RP23 git's HOME is not aibd's own folder
pu '        .env("HOME", home)' \
   '        .env("HOME", home.join(".."))' \
   the_network_git_is_spelled_whole_and_reads_no_ones_config
# RP24 git is looked up on PATH
pu 'pub const GIT: &str = "/usr/bin/git";' \
   'pub const GIT: &str = "git";' \
   the_network_git_is_spelled_whole_and_reads_no_ones_config
# RP25 the agent is forwarded
pu '    "ForwardAgent=no", // the agent stays here' \
   '    "ForwardAgent=yes", // the agent stays here' \
   the_network_git_is_spelled_whole_and_reads_no_ones_config
# RP26 ssh reads its config file
pu '    "none",' \
   '    "~/.ssh/config",' \
   the_network_git_is_spelled_whole_and_reads_no_ones_config
# RP27 an unknown host key is taken
pu '    "StrictHostKeyChecking=yes", // only a host key already known' \
   '    "StrictHostKeyChecking=accept-new", // only a host key already known' \
   the_network_git_is_spelled_whole_and_reads_no_ones_config
# RP28 the system config is read
pu '        .env("GIT_CONFIG_NOSYSTEM", "1")' \
   '        .env("GIT_CONFIG_NOSYSTEM", "0")' \
   the_network_git_is_spelled_whole_and_reads_no_ones_config
# RP29 a hook could run
pu '        .arg(format!("core.hooksPath={}", hooks.display()))' \
   '        .arg(format!("core.hooksPath={}", scratch.display()))' \
   the_network_git_is_spelled_whole_and_reads_no_ones_config
# RP30 the ticket does not bind the head
pu '        "repo": t.repo, "remote": t.remote, "branch": t.branch, "sha": t.sha,' \
   '        "repo": t.repo, "remote": t.remote, "branch": t.branch, "sha": "",' \
   the_window_keeps_where_it_goes
# RP31 the ticket does not bind the remote
pu '        "repo": t.repo, "remote": t.remote, "branch": t.branch, "sha": t.sha,' \
   '        "repo": t.repo, "remote": "", "branch": t.branch, "sha": t.sha,' \
   the_window_keeps_where_it_goes
# RP32 a branch moved after the fingerprint is pushed
pu '    if now != *approved {' \
   '    if now.repo != approved.repo {' \
   what_changed_since_the_fingerprint_is_not_pushed
# RP33 a .github change is pushed
pu '    if github_tree(&scratch.0, hooks, HEAD_REF) != github_tree(&scratch.0, hooks, MAIN_REF) {' \
   '    if github_tree(&scratch.0, hooks, HEAD_REF) != github_tree(&scratch.0, hooks, HEAD_REF) {' \
   a_github_change_a_leak_or_a_rewound_branch_is_not_pushed
# RP34 a leak is pushed
pu '        Some(1) => Err(no(StatusCode::CONFLICT, "push_leak")),' \
   '        Some(1) => Ok(()),' \
   a_github_change_a_leak_or_a_rewound_branch_is_not_pushed
# RP35 a scan that cannot run lets the push through
pu '        _ => Err(no(StatusCode::SERVICE_UNAVAILABLE, "push_leak_unavailable")),' \
   '        _ => Ok(()),' \
   a_github_change_a_leak_or_a_rewound_branch_is_not_pushed
# RP36 the push is forced
pu '    let spec = format!("{}:refs/heads/{}", now.sha, now.branch);' \
   '    let spec = format!("+{}:refs/heads/{}", now.sha, now.branch);' \
   a_github_change_a_leak_or_a_rewound_branch_is_not_pushed
# RP37 the one-push repository is left behind
pu '        let _ = std::fs::remove_dir_all(&self.0);' \
   '        let _ = &self.0; // knife' \
   the_branch_goes_to_the_registered_remote_from_aibds_own_repository
# RP38 a machine without git and ssh is asked for a fingerprint anyway
pu '    git.is_absolute() && ssh.is_absolute() && git.is_file() && ssh.is_file()' \
   '    git.is_absolute() && ssh.is_absolute() && git.is_file() || ssh.is_file()' \
   a_machine_without_the_two_programs_is_told_so
