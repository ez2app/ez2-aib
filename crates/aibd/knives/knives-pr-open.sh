#!/usr/bin/env bash
# PO1–PO8 G3: a draft pull request into main, asked by the built-in page with the owner's fingerprint (DESIGN-pr-open-g3-7b81e-1120; 5d33a PREREAD-propen-g3 L2; 519d0 11-20 22:30)
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
po() { python3 -I scripts/knife.py crates/aibd/src/pr_open.rs "$1" "$2" --expect-fail "pr_open::tests::$3" \
        -- cargo test -p aibd --locked --lib "pr_open::tests::$3"; }
# PO1 the base is not main
po '    match api.open(slug, &t.branch, BASE, title, body, DRAFT) {' \
   '    match api.open(slug, &t.branch, &t.branch, title, body, DRAFT) {' \
   the_base_is_main_and_the_pull_request_a_draft_whatever_is_asked
# PO2 the pull request is not a draft
po '    match api.open(slug, &t.branch, BASE, title, body, DRAFT) {' \
   '    match api.open(slug, &t.branch, BASE, title, body, !DRAFT) {' \
   the_base_is_main_and_the_pull_request_a_draft_whatever_is_asked
# PO3 a pull request with no number counts as opened
po '        Ok(o) if o.number > 0 => Ok(o),' \
   '        Ok(o) if o.number < u64::MAX => Ok(o),' \
   the_base_is_main_and_the_pull_request_a_draft_whatever_is_asked
# PO4 a name or an issue number reaches GitHub as written
po '        Some(re) => re.replace_all(&plain, "`$0`").into_owned(),' \
   '        Some(re) => re.replace_all(&plain, "$0").into_owned(),' \
   a_name_or_an_issue_number_in_the_words_is_quoted
# PO5 the body is sent uncleaned
po '    let b = quoted(&crate::dm_import::clean(body));' \
   '    let b = quoted(body);' \
   a_name_or_an_issue_number_in_the_words_is_quoted
# PO6 a title of several lines is taken
po '    if title.contains(TITLE_BREAKS) {' \
   '    if title.contains(TITLE_BREAKS) && title.is_empty() {' \
   a_name_or_an_issue_number_in_the_words_is_quoted
# PO7 the ticket does not bind the title
po '        "base": BASE, "draft": DRAFT, "title": title, "body_sha256": sha12(body.as_bytes()),' \
   '        "base": BASE, "draft": DRAFT, "title": "", "body_sha256": sha12(body.as_bytes()),' \
   the_ticket_binds_the_words_the_branch_and_where_it_opens
# PO8 the ticket does not bind the body
po '        "base": BASE, "draft": DRAFT, "title": title, "body_sha256": sha12(body.as_bytes()),' \
   '        "base": BASE, "draft": DRAFT, "title": title, "body_sha256": sha12(&body.as_bytes()[..0]),' \
   the_ticket_binds_the_words_the_branch_and_where_it_opens
