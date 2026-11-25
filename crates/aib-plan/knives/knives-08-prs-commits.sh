#!/usr/bin/env bash
# aib-knives: os=unix
# aib-prs knife PC1 (a9136 11-18, 0eed7 VERIFY-S73-ready M1): the PR list must not ask GitHub for commits
# (--limit 100 × commits is over GitHub's 500,000-node cap, so the whole list is refused). Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
# PC1 commits back in the gh fields
python3 -I scripts/knife.py crates/aib-plan/src/bin/aib-prs.rs \
  '    "number,title,headRefName,headRefOid,baseRefName,mergeable,statusCheckRollup";' \
  '    "number,title,headRefName,headRefOid,baseRefName,mergeable,statusCheckRollup,commits"; // knife' \
  --expect-fail the_pr_list_does_not_ask_github_for_commits \
  -- cargo test -p aib-plan --locked --bin aib-prs gh_fields_tests::the_pr_list_does_not_ask_github_for_commits
