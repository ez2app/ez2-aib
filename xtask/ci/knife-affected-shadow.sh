#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# S66-4 shadow (7b81e, 519d0 11-15): after a cell's knife steps ran every knife, print which of
# them this pull request would have had to run, and append one ledger row per knife.
# Nothing is skipped. The calling step is continue-on-error, has a 3-minute timeout and is
# never a required cell.
#
#   knife-affected-shadow.sh <cell name> <aib-xtask knife-affected selection options...>
#
# Reads: BASE / HEAD (the pull request's base and head sha), EVENT, AIB_SHADOW_KNIFE_T0 (set by
# the "knife timer" step right before the knife steps), AIB_SHADOW_SKIP_AFTER_S (the step's own
# budget, 5d33a S66-4 M1: a job past its timeout-minutes turns red whatever continue-on-error
# says, so when the knife steps took longer than this the shadow prints one line and stops),
# GITHUB_*.
# Writes: $GITHUB_STEP_SUMMARY, reuse-ledger/YYYY-MM-DD.jsonl (aib-cireuse's ledger file name).
set -u -o pipefail
cell=${1:?cell name}
shift
summary=${GITHUB_STEP_SUMMARY:-/dev/null}
if [ -z "${AIB_SHADOW_KNIFE_T0:-}" ] || [ -z "${AIB_SHADOW_SKIP_AFTER_S:-}" ]; then
  echo "knife-affected (shadow): no knife timer or budget; not run" | tee -a "$summary"
  exit 0
fi
span=$(($(date +%s) - AIB_SHADOW_KNIFE_T0))
if [ "$span" -gt "$AIB_SHADOW_SKIP_AFTER_S" ]; then
  echo "knife-affected (shadow): knife steps took ${span} s (> ${AIB_SHADOW_SKIP_AFTER_S} s); not run, so the job stays inside its timeout" | tee -a "$summary"
  exit 0
fi
# base...head needs a merge base: trees only, a bounded depth (5d33a N1); not found ⇒ the diff
# fails and every knife counts as affected
if [ -n "${BASE:-}" ] && [ -n "${HEAD:-}" ]; then
  git fetch -q --no-tags --filter=blob:none --depth=200 origin "$BASE" "$HEAD" 2>/dev/null ||
    echo "::notice::knife-affected: base / head not fetched; every knife counts as affected"
fi
mkdir -p reuse-ledger
cargo run -q --locked -p aib-xtask -- knife-affected \
  --event "${EVENT:-}" --base "${BASE:-}" --head "${HEAD:-}" --span-seconds "$span" \
  --ledger "reuse-ledger/$(date -u +%Y-%m-%d).jsonl" \
  --repo "${GITHUB_REPOSITORY:-}" --run-id "${GITHUB_RUN_ID:-0}" --check "$cell" "$@" \
  | tee -a "$summary"
