#!/usr/bin/env bash
# Only aibd may depend on aib-sys (API list v0.2, section 9). The check is
# `aib-xtask deps` over `cargo metadata` (every dependency kind and target,
# renamed keys, and chains through other workspace crates).
#
#   scripts/check-sys-deps.sh             check the real workspace
#   scripts/check-sys-deps.sh --self-test  add aib-sys to a copy in each
#                                          forbidden way; every one must fail
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
xtask_target="${CARGO_TARGET_DIR:-$root/target}"

deps() {  # $1 = manifest path; xtask is always built from the real workspace
  (cd "$root" && CARGO_TARGET_DIR="$xtask_target" cargo run -q --locked -p aib-xtask -- deps --manifest-path "$1")
}

if [ "${1:-}" != "--self-test" ]; then
  deps "$root/Cargo.toml"
  exit $?
fi

tmp=$(mktemp -d "${TMPDIR:-/tmp}/aib-sys-deps.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
pass=0 fail=0
case_run() {  # name, expect (fail|ok), expected text, crate, toml to append
  local name="$1" expect="$2" want="$3" crate="$4" add="$5" ws="$tmp/$1" out rc
  mkdir -p "$ws"
  cp "$root/Cargo.toml" "$root/Cargo.lock" "$ws/"
  (cd "$root" && tar --exclude target -cf - crates xtask) | (cd "$ws" && tar -xf -)
  printf '\n%s\n' "$add" >> "$ws/crates/$crate/Cargo.toml"
  out=$(deps "$ws/Cargo.toml" 2>&1); rc=$?
  if [ "$expect" = fail ] && [ $rc -ne 0 ] && grep -qF -- "$want" <<<"$out"; then
    echo "  ok   $name"; pass=$((pass+1))
  elif [ "$expect" = ok ] && [ $rc -eq 0 ]; then
    echo "  ok   $name"; pass=$((pass+1))
  else
    echo "  FAIL $name (rc $rc): $out"; fail=$((fail+1))
  fi
}
sys='{ path = "../aib-sys" }'
case_run core-normal fail '`aib-core` depends on aib-sys' aib-core "[target.'cfg(all())'.dependencies]
aib-sys = $sys"
case_run store-dev fail '`aib-store` depends on aib-sys' aib-store "[target.'cfg(unix)'.dev-dependencies]
aib-sys = $sys"
case_run store-renamed-windows fail '`aib-store` depends on aib-sys' aib-store "[target.'cfg(windows)'.dependencies]
sys = { package = \"aib-sys\", path = \"../aib-sys\" }"
case_run store-build fail '`aib-store` depends on aib-sys' aib-store "[target.'cfg(all())'.build-dependencies]
aib-sys = $sys"
case_run aibd-allowed ok '' aibd "[target.'cfg(all())'.dependencies]
aib-sys = $sys"
echo "aib-sys deps self-test: $pass passed, $fail failed"
[ $fail -eq 0 ]
