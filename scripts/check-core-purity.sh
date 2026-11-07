#!/usr/bin/env bash
# aib-core must stay free of I/O, async runtimes, process spawning and
# platform switches (ADR 0001 / spec section 3). Exit 1 and list offenders.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
pat='cfg\((target_os|unix|windows)|std::(fs|net|process)|tokio|std::env::var|Command::new'
hits=$(grep -rnE "$pat" "$root/crates/aib-core/src" | grep -vE '^[^:]+:[0-9]+:[[:space:]]*//' || true)
if [ -n "$hits" ]; then
  printf '%s\n' "$hits"
  echo "aib-core purity check failed: the lines above belong in a Platform Adapter" >&2
  exit 1
fi
deps=$(sed -n '/^\[dependencies\]/,/^\[/p' "$root/crates/aib-core/Cargo.toml" | grep -E '^[a-z]' | cut -d' ' -f1 | sort | tr '\n' ' ')
allowed="serde thiserror "
if [ "$deps" != "$allowed" ]; then
  echo "aib-core dependencies changed: [$deps] (allowed: [$allowed]); update this check on purpose" >&2
  exit 1
fi
echo "aib-core purity: ok"
