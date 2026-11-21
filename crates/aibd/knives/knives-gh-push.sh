#!/usr/bin/env bash
# aib-knives: os=macos
# GB1–GB8: a push of a registered GitHub repository goes with aibd's own one-shot git helper, which answers only its host and path from the one descriptor it was given; a missing or refused token is said before the window, GitHub out of reach is not (DESIGN-gh-token-card-7b81e-1121 r1 L1, §4; 5d33a PREREAD-ghtokencard 4, L1)
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
kc() { python3 -I scripts/knife.py crates/aibd/src/git_cred.rs "$1" "$2" --expect-fail "git_cred::tests::$3" \
        -- cargo test -p aibd --locked --lib "git_cred::tests::$3"; }
kt() { python3 -I scripts/knife.py crates/aibd/src/gh_token.rs "$1" "$2" --expect-fail "gh_token::tests::$3" \
        -- cargo test -p aibd --locked --lib "gh_token::tests::$3"; }
ks() { python3 -I scripts/knife.py crates/aib-sys/src/stdio_only.rs "$1" "$2" --expect-fail "stdio_only::tests::$3" \
        -- cargo test -p aib-sys --locked --lib "stdio_only::tests::$3"; }
# GB1 the helper answers whatever host git names
kc '            "host" => (1, host),' \
   '            "host" => (1, if host.is_empty() { host } else { value }),' \
   the_helper_answers_get_for_its_own_host_and_path_only
# GB2 the platform's helpers stay on git's list
kc '        "credential.helper=".into(),' \
   '        "credential.useHttpPath=true".into(),' \
   git_forgets_every_other_helper_and_the_token_is_in_no_argument
# GB3 a token GitHub refuses for the repository goes on to the window
kt '        Err((_, why)) if why == crate::pr_api::GH_UNREACHABLE => Ok(()),' \
   '        Err(_) => Ok(()),' \
   a_push_says_a_missing_or_refused_token_before_its_window_and_goes_with_its_own
# GB4 GitHub out of reach stops the push
kt '        Err((_, why)) if why == crate::pr_api::GH_UNREACHABLE => Ok(()),' \
   '        Err((_, why)) if why == crate::pr_api::GH_UNREACHABLE && why.is_empty() => Ok(()),' \
   a_push_says_a_missing_or_refused_token_before_its_window_and_goes_with_its_own
# GB5 an ssh remote is asked for a token too
kt '    if !remote.starts_with("https://") {' \
   '    if remote.is_empty() {' \
   a_push_says_a_missing_or_refused_token_before_its_window_and_goes_with_its_own
# GB6 the descriptor is not handed over
ks '    let r = unsafe { libc::dup2(from, to) };' \
   '    let r = from - from;' \
   a_given_descriptor_reaches_the_child_and_only_it
# GB7 every other descriptor reaches the child too
ks '    mark_rest()?;' \
   '    let _ = mark_rest;' \
   a_given_descriptor_reaches_the_child_and_only_it
# GB8 the helper answers whatever repository path git names (5d33a PREREAD-ghtokencard r1 N5)
kc '            "path" => (2, path),' \
   '            "path" => (2, if path.is_empty() { path } else { value }),' \
   the_helper_answers_get_for_its_own_host_and_path_only
