#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# S66-4 segment 1 (7b81e, council r2 11-17 §2 item 1): which knife steps and knife scripts of a
# cell this pull request may skip. aib-xtask knife-affected --skip-out writes skip_steps /
# skip_files; this script copies them to $GITHUB_OUTPUT only when the command finished with rc 0.
# Anything else leaves the outputs empty, and an empty output skips nothing: the knife steps read
# `skip_steps || '[]'`. The calling step runs only when the repository variable AIB_KNIFE_SKIP is
# 'on' (unset = off) and only on a pull request; it is continue-on-error with its own timeout.
#
#   knife-affected-gate.sh <cell name> <aib-xtask knife-affected selection options...>
#
# Every crates/*/knives/*.sh and scripts/knives-*.sh is added with --source, so a script's knives
# are judged as one unit (the aib-drive loop and the call-only steps read skip_files).
# Reads: BASE / HEAD / EVENT, GITHUB_OUTPUT, GITHUB_STEP_SUMMARY, GITHUB_REPOSITORY,
# GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT.
set -u -o pipefail
cell=${1:?cell name}
shift
summary=${GITHUB_STEP_SUMMARY:-/dev/null}
out=${GITHUB_OUTPUT:?no GITHUB_OUTPUT}
if [ -n "${BASE:-}" ] && [ -n "${HEAD:-}" ]; then
  git fetch -q --no-tags --filter=blob:none --depth=200 origin "$BASE" "$HEAD" 2>/dev/null ||
    echo "::notice::knife-affected gate: base / head not fetched; nothing is skipped"
fi
src=()
for f in crates/*/knives/*.sh scripts/knives-*.sh; do
  [ -f "$f" ] && src+=(--source "$f")
done
tmp=$(mktemp)
if cargo run -q --locked -p aib-xtask -- knife-affected \
  --event "${EVENT:-}" --base "${BASE:-}" --head "${HEAD:-}" \
  --repo "${GITHUB_REPOSITORY:-}" --run-id "${GITHUB_RUN_ID:-0}" --attempt "${GITHUB_RUN_ATTEMPT:-1}" \
  --check "$cell" --skip-out "$tmp" "$@" "${src[@]}" | tee -a "$summary"; then
  cat "$tmp" >> "$out"
  { echo; echo "skip outputs:"; cat "$tmp"; } | tee -a "$summary"
else
  echo "knife-affected gate: the command did not finish with rc 0; nothing is skipped" | tee -a "$summary"
fi
rm -f "$tmp"
