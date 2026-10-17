#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Wait until PyPI serves <version> as the newest ez2-aib to an unpinned install, the way `pipx upgrade` and
# `pip install --upgrade` resolve it. Right after an upload the CDN can still answer with the previous release for
# a few minutes; testing upgrades in that window fails with "did not reach" although the upload is fine
# (0.1.11, 0.1.14 and 0.1.15 each needed a manual rerun).
# usage: wait_pypi_latest.sh <version>      env: TRIES (default 30), SLEEP seconds (default 20), PY (default python)
set -euo pipefail
v=$1
tries=${TRIES:-30} pause=${SLEEP:-20} py=${PY:-python}
tmp=$(mktemp -d "${RUNNER_TEMP:-/tmp}/aib-wait-XXXXXX")
for i in $(seq 1 "$tries"); do
  rm -rf "$tmp/dl"; mkdir -p "$tmp/dl"
  got=""
  if "$py" -m pip download -q --no-deps --no-cache-dir -d "$tmp/dl" ez2-aib >/dev/null 2>&1; then
    f=$(ls "$tmp/dl" | head -1)
    got=$(printf '%s' "$f" | sed -E 's/^ez2[_-]aib-([^-]+)(-.*|\.tar\.gz)$/\1/')
  fi
  if [ "$got" = "$v" ]; then echo "PyPI serves ez2-aib $v as the newest ($i/$tries)"; exit 0; fi
  echo "PyPI still serves ez2-aib ${got:-?} as the newest, waiting for $v ($i/$tries)"
  [ "$i" = "$tries" ] || sleep "$pause"
done
echo "::error::PyPI did not serve ez2-aib $v as the newest after $tries tries"
exit 1
