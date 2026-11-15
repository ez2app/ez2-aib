#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# S66-4 shadow (7b81e, 519d0 11-15): after a cell's knife steps ran every knife, print which of
# them this pull request would have had to run, and append one ledger row per knife.
# Nothing is skipped. The calling step is continue-on-error and never a required cell.
#
#   knife-affected-shadow.sh <cell name> <aib-xtask knife-affected selection options...>
#
# Reads: BASE / HEAD (the pull request's base and head sha), EVENT, KNIFE_T0 (set by the
# "knife timer" step right before the knife steps; missing ⇒ no could_save), GITHUB_*.
# Writes: $GITHUB_STEP_SUMMARY, reuse-ledger/YYYY-MM-DD.jsonl (aib-cireuse's ledger file name).
set -u -o pipefail
cell=${1:?cell name}
shift
span=()
if [ -n "${KNIFE_T0:-}" ]; then
  span=(--span-seconds "$(($(date +%s) - KNIFE_T0))")
fi
# the checkout is one merge commit deep; base...head needs both histories (trees only)
if [ -n "${BASE:-}" ] && [ -n "${HEAD:-}" ]; then
  git fetch -q --no-tags --filter=blob:none --unshallow origin "$BASE" "$HEAD" 2>/dev/null ||
    git fetch -q --no-tags --filter=blob:none origin "$BASE" "$HEAD" 2>/dev/null ||
    echo "::notice::knife-affected: base / head not fetched; every knife counts as affected"
fi
mkdir -p reuse-ledger
cargo run -q --locked -p aib-xtask -- knife-affected \
  --event "${EVENT:-}" --base "${BASE:-}" --head "${HEAD:-}" ${span[@]+"${span[@]}"} \
  --ledger "reuse-ledger/$(date -u +%Y-%m-%d).jsonl" \
  --repo "${GITHUB_REPOSITORY:-}" --run-id "${GITHUB_RUN_ID:-0}" --check "$cell" "$@" \
  | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
