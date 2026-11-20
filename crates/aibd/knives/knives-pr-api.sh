#!/usr/bin/env bash
# PA1–PA11, PO11–PO12: the pull request call without its wire (DESIGN-pr-api-7b81e-1121; 519d0 11-21 00:38)
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
pa() { python3 -I scripts/knife.py crates/aibd/src/pr_api.rs "$1" "$2" --expect-fail "pr_api::tests::$3" \
        -- cargo test -p aibd --locked --lib "pr_api::tests::$3"; }
po() { python3 -I scripts/knife.py crates/aibd/src/pr_open.rs "$1" "$2" --expect-fail "pr_open::tests::$3" \
        -- cargo test -p aibd --locked --lib "pr_open::tests::$3"; }
# PA1 a quote or a backslash in a token
pa '                .all(|&b| b.is_ascii_graphic() && !matches!(b, b'"'"'"'"'"' | b'"'"'\'"'"''"'"' | b'"'"'\\'"'"'));' \
   '                .all(|&b| b.is_ascii_graphic() && !matches!(b, b'"'"'"'"'"' | b'"'"'\'"'"''"'"'));' \
   a_token_is_one_plain_word
# PA2 a token of any length
pa '        let ok = (1..=TOKEN_MAX).contains(&t.0.len())' \
   '        let ok = (1..=TOKEN_MAX * 2).contains(&t.0.len())' \
   a_token_is_one_plain_word
# PA3 one item whatever the repository
pa '    Some((format!("{SERVICE}{name}"), owner.to_owned()))' \
   '    Some((format!("{SERVICE}demo{}", &name[..0]), owner.to_owned()))' \
   a_token_is_the_item_named_after_the_repository_and_read_once_per_call
# PA4 one account whatever the owner
pa '    Some((format!("{SERVICE}{name}"), owner.to_owned()))' \
   '    Some((format!("{SERVICE}{name}"), owner[..0].to_owned() + "ez2app"))' \
   a_token_is_the_item_named_after_the_repository_and_read_once_per_call
# PA5 ready whatever the store has
pa '        item_of(slug).is_some_and(|(service, account)| self.secrets.has(&service, &account))' \
   '        item_of(slug).is_some_and(|(service, account)| self.secrets.has(&service, &account) || true)' \
   a_token_is_the_item_named_after_the_repository_and_read_once_per_call
# PA6 an answer that is not "created" read as an opened pull request
pa '        201 => {' \
   '        200 | 201 => {' \
   each_answer_reads_as_what_it_is
# PA7 number 0 taken
pa '                .filter(|n| *n > 0);' \
   '                .filter(|n| *n < u64::MAX);' \
   each_answer_reads_as_what_it_is
# PA8 any 422 is "exists"
pa '        422 if exists(answer) => Err(PrError::Exists(None)),' \
   '        422 if exists(answer) || answer.len() < usize::MAX => Err(PrError::Exists(None)),' \
   each_answer_reads_as_what_it_is
# PA9 a refused token not told apart
pa '        401 => failed(REFUSED),' \
   '        401 => failed(OTHER),' \
   each_answer_reads_as_what_it_is
# PA10 the branch put into the address as written
pa '            pct(&format!("{owner}:{head}"))' \
   '            format!("{owner}:{head}")' \
   a_branch_that_has_one_is_answered_with_its_number
# PA11 a number read off an answer that is not 200
pa '                    Ok((200, list)) => found(&list),' \
   '                    Ok((_, list)) => found(&list),' \
   a_branch_that_has_one_is_answered_with_its_number
# PO11 a refused token answered as any failure
po '            crate::pr_api::REFUSED => no(StatusCode::BAD_GATEWAY, "pr_token_refused"),' \
   '            crate::pr_api::REFUSED => no(StatusCode::BAD_GATEWAY, "pr_failed"),' \
   the_base_is_main_and_the_pull_request_a_draft_whatever_is_asked
# PO12 no token answered as GitHub failing
po '            crate::pr_api::NO_TOKEN => no(StatusCode::SERVICE_UNAVAILABLE, "pr_open_unavailable"),' \
   '            crate::pr_api::NO_TOKEN => no(StatusCode::BAD_GATEWAY, "pr_failed"),' \
   the_base_is_main_and_the_pull_request_a_draft_whatever_is_asked
