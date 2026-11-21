#!/usr/bin/env bash
# GT1–GT7: a GitHub token pasted on the page is kept only when GitHub says it may push to that very repository, under that repository's own item, with the owner's fingerprint over its hash (DESIGN-gh-token-card-7b81e-1121 r1; 5d33a PREREAD-ghtokencard L2)
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ka() { python3 -I scripts/knife.py crates/aibd/src/pr_api.rs "$1" "$2" --expect-fail "pr_api::tests::$3" \
        -- cargo test -p aibd --locked --lib "pr_api::tests::$3"; }
kt() { python3 -I scripts/knife.py crates/aibd/src/gh_token.rs "$1" "$2" --expect-fail "gh_token::tests::$3" \
        -- cargo test -p aibd --locked --lib "gh_token::tests::$3"; }
ko() { python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs "$1" "$2" --expect-fail "gh_token::tests::$3" \
        -- cargo test -p aibd --locked --lib "gh_token::tests::$3"; }
# GT1 a token that may only read is kept
ka '            if v["permissions"]["push"] == Value::Bool(true) {' \
   '            if v["permissions"]["pull"] == Value::Bool(true) || v.is_object() {' \
   a_token_is_kept_only_when_github_says_it_may_push_to_that_repository
# GT2 a token that does not see the repository is kept
ka '        403 | 404 => Err(GH_NOT_SELECTED),' \
   '        403 | 404 => Ok(()),' \
   a_token_is_kept_only_when_github_says_it_may_push_to_that_repository
# GT3 a dash joins owner and name again: a-b/c and a/b-c share one item (5d33a L2)
ka '    Some((format!("{SERVICE}{owner}/{name}"), owner.to_owned()))' \
   '    Some((format!("{SERVICE}{owner}-{name}"), owner.to_owned()))' \
   a_token_is_the_item_named_after_the_repository_and_read_once_per_call
# GT4 the ticket holds the token itself
kt '        "token_sha256": crate::owner_ok::params_sha256(token.expose()),' \
   '        "token_sha256": String::from_utf8_lossy(token.expose()),' \
   the_ticket_holds_the_tokens_hash_never_the_token
# GT5 GitHub out of reach reads as a no of the token
kt '        let status = if why == crate::pr_api::GH_UNREACHABLE {' \
   '        let status = if why != crate::pr_api::GH_UNREACHABLE {' \
   a_token_is_checked_with_github_first_and_kept_under_its_repository
# GT6 the act is not on the owner check's list (every ask would be unavailable)
ko '    crate::gh_token::OP,            // repo.gh_token_set (DESIGN-gh-token-card)' \
   '    // crate::gh_token::OP, repo.gh_token_set' \
   the_ticket_holds_the_tokens_hash_never_the_token
# GT7 the window's plain words name another repository
ko '            "把 GitHub 權杖存進這台,給 {} 推分支與開 PR 用",' \
   '            "把 GitHub 權杖存進這台,給 {}{} 推分支與開 PR 用", "所有倉 ",' \
   the_ticket_holds_the_tokens_hash_never_the_token
