#!/usr/bin/env bash
# The "aib-cireuse knives (macOS)" step of aibd.yml's rust job, moved here word for word (11-16 裁定:
# 新刀放 crates/<套件>/knives/;519d0 IFACE-drive-shadow-519d0-1117 §5; same shape as #804's aib-drive).
# aibd.yml runs every crates/aib-cireuse/knives/*.sh in name order in one step; aib-xtask knife-anchors
# reads crates/*/knives/*.sh, so every knife here is still checked. Run from the repo root;
# -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
#
# aib-cireuse S68 ledger (b433b; 1ced7 S68 review M1 / L1) + S68d log errors (1ced7 M1 / L1 on 164f85da).
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
ck() { python3 -I scripts/knife.py crates/aib-cireuse/src/lib.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-cireuse --locked --test cireuse "$3"; }
# KC1 (M1) a later unknown / wait re-judge undoes a push already judged
ck '            (judged, s(r, "at"))' \
   '            (judged && false, s(r, "at")) // knife' \
   s68_week_summary_a_later_unknown_never_undoes_a_verdict
# KC2 (L1) a fork's run that calls its branch main is pulled
ck '                && own' \
   '                && (own || true) // knife' \
   s68_artifacts_to_pull_keeps_out_forks_expired_other_names_and_pulled
# KC3 (L1) an artifact from a branch other than main is pulled
ck '                && a.pointer("/workflow_run/head_branch")?.as_str()? == "main"' \
   '                && a.pointer("/workflow_run/head_branch")?.as_str()? != "-" // knife' \
   s68_artifacts_to_pull_keeps_out_forks_expired_other_names_and_pulled
# KC4 (L1) a ledger file name with other separators is taken
ck "                *c == b'-'" \
   "                *c == b'-' || *c == b'_' // knife" \
   s68_ledger_file_names_cannot_leave_the_folder
# KC5 (L1) a folder named like a ledger file is read as one
ck '        .filter(|e| e.file_type().is_ok_and(|t| t.is_file()))' \
   '        .filter(|e| e.file_type().is_ok()) // knife' \
   s68_ingest_appends_new_lines_once_and_records_the_artifact
# S68d (b433b 11-16; 1ced7 verifies): the observation ledger says why it could not read
# KC7 a PR job log that could not be fetched is dropped again (tested_sha_unknown, no why)
python3 -I scripts/knife.py crates/aib-cireuse/src/gh.rs \
  '                Err(e) => j.log_error = Some(crate::one_line(&e)),' \
  '                Err(_) => {} // knife' \
  --expect-fail facts_end_to_end_from_api_shapes \
  -- cargo test -p aib-cireuse --locked --test cireuse facts_end_to_end_from_api_shapes
# KC8 main's job log error is dropped again (image_unknown, no why)
python3 -I scripts/knife.py crates/aib-cireuse/src/gh.rs \
  "                Err(why) => j.log_error = Some(crate::one_line(&why)), // main's job log" \
  '                Err(_) => {} // knife' \
  --expect-fail facts_end_to_end_from_api_shapes \
  -- cargo test -p aib-cireuse --locked --test cireuse facts_end_to_end_from_api_shapes
# KC9 a cell the PR did not run reads as tested_sha / image unknown again
ck '    if pr_succeeded && cj.steps_known && mj.steps_known && checked_out(mj) && !checked_out(cj) {' \
   '    if pr_succeeded && cj.steps_known && mj.steps_known && checked_out(mj) && !checked_out(cj) && false { // knife' \
   knife_a_cell_the_pr_did_not_run_is_not_run_on_pr
# KC11 (1ced7 M1) a PR job that failed before its checkout reads as not run on the PR
ck '    let pr_succeeded = cj.conclusion.as_deref() == Some("success");' \
   '    let pr_succeeded = cj.conclusion.as_deref() != Some("never"); // knife' \
   knife_a_pr_job_that_failed_before_checkout_is_not_success_not_not_run
# KC12 (1ced7 L1) the PR side's image_unknown is dropped when only main's log failed
ck '                if (a.is_none() && cj.log_error.is_none())' \
   '                if (a.is_none() && cj.log_error.is_none() && false)' \
   an_unexplained_side_stays_image_unknown_when_only_the_other_log_failed
# KC10 a self-hosted runner's labels are not read (it reads as image_unknown)
ck '            .is_some_and(|a| a.iter().any(|l| l.as_str() == Some("self-hosted"))),' \
   '            .is_some_and(|a| a.iter().any(|l| l.as_str() == Some("self-hosted-knife"))),' \
   knife_a_self_hosted_runner_is_named_not_image_unknown
# KC6 (L3) a failed pull vanishes from the cockpit (gh not logged in / hung)
python3 -I scripts/knife.py crates/aib-cireuse/src/lib.rs \
  '    if v.get("ok").and_then(Value::as_bool) == Some(false) {' \
  '    if v.get("ok").and_then(Value::as_bool) == Some(false) && false { // knife' \
  --expect-fail a_failed_or_stale_pull_is_shown_even_with_no_ledger \
  -- cargo test -p aib-plan --locked --lib a_failed_or_stale_pull_is_shown_even_with_no_ledger
