#!/usr/bin/env bash
# aib-core purity gate (ADR 0001 / spec section 3).
#
# What enforces what:
#   compiler  #![no_std] — std::fs / process / net / env / thread do not exist,
#             however they are spelled (aliases, grouped `use`, `::std::`).
#   compiler  #![forbid(unsafe_code)] — no FFI back door.
#   this      `extern crate std` outside `#[cfg(test)]` (the one way back to std).
#   this      platform switches: cfg(...) / cfg!(...) / cfg_attr naming
#             target_os, target_family, target_vendor, unix, windows.
#   this      full dependency tree == allow-list (cargo tree), so I/O cannot come
#             in through a new crate.
# Not covered (stated, not hidden): a proc-macro dependency doing I/O at build
# time, and code in an allowed dependency that is itself impure.
#
#   scripts/check-core-purity.sh             check the real crate
#   scripts/check-core-purity.sh --self-test  prove each rule fails on a bypass
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
ALLOWED_TREE="aib-core proc-macro2 quote serde serde_core serde_derive syn thiserror thiserror-impl unicode-ident"

check() {  # $1 = workspace root, $2 = crate dir
  local ws="$1" crate="$2" fail=0 lib="$2/src/lib.rs"
  grep -q '^#!\[no_std\]' "$lib" || { echo "purity: $lib lost #![no_std]" >&2; fail=1; }
  grep -q '^#!\[forbid(unsafe_code)\]' "$lib" || { echo "purity: $lib lost #![forbid(unsafe_code)]" >&2; fail=1; }
  # extern crate std must be directly under #[cfg(test)]
  local bad
  bad=$(awk 'FNR==1{prev=""} /^[[:space:]]*\/\// {next} /extern[[:space:]]+crate[[:space:]]+std/ && prev !~ /#\[cfg\(test\)\]/ {print FILENAME":"FNR": "$0} {if ($0 !~ /^[[:space:]]*$/) prev=$0}' $(find "$crate/src" -name '*.rs'))
  [ -z "$bad" ] || { printf '%s\n' "$bad" >&2; echo "purity: extern crate std outside #[cfg(test)]" >&2; fail=1; }
  bad=$(grep -rnE 'cfg(_attr)?!?[[:space:]]*\(([^)]*[^a-z_])?(target_os|target_family|target_vendor|unix|windows)\b' "$crate/src" | grep -vE '^[^:]+:[0-9]+:[[:space:]]*//' || true)
  [ -z "$bad" ] || { printf '%s\n' "$bad" >&2; echo "purity: platform cfg in aib-core" >&2; fail=1; }
  local tree
  tree=$(cd "$ws" && cargo tree -p aib-core -e normal,build --prefix none --format '{p}' 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ' | sed 's/ $//')
  [ "$tree" = "$ALLOWED_TREE" ] || { echo "purity: dependency tree is [$tree], allowed [$ALLOWED_TREE]" >&2; fail=1; }
  (cd "$ws" && cargo build -q -p aib-core --lib >/dev/null 2>&1) || { echo "purity: aib-core does not build as no_std" >&2; fail=1; }
  return $fail
}

if [ "${1:-}" != "--self-test" ]; then
  check "$root" "$root/crates/aib-core" && echo "aib-core purity: ok"
  exit $?
fi

# --self-test: copy aib-core into a scratch workspace, apply one bypass at a
# time, and require the check to fail for every one of them.
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
export CARGO_TARGET_DIR="$tmp/target"
mk() {
  rm -rf "$tmp/ws"; mkdir -p "$tmp/ws/crates"
  cp -R "$root/crates/aib-core" "$tmp/ws/crates/"
  cp "$root/Cargo.lock" "$root/rust-toolchain.toml" "$tmp/ws/"
  sed 's#^members = .*#members = ["crates/aib-core"]#' "$root/Cargo.toml" > "$tmp/ws/Cargo.toml"
}
case_run() {  # name, file-relative-to-crate, text appended
  mk
  local f="$tmp/ws/crates/aib-core/$2"
  if [ "$2" = Cargo.toml ]; then
    printf '%s\n' "$3" >> "$f"
  else
    printf '\n%s\n' "$3" >> "$f"
  fi
  local why
  if why=$(check "$tmp/ws" "$tmp/ws/crates/aib-core" 2>&1 >/dev/null); then
    echo "SELF-TEST $1: MISSED (check passed on a bypass)"; return 1
  fi
  echo "SELF-TEST $1: caught — $(printf '%s\n' "$why" | grep '^purity:' | head -2 | tr '\n' ' ')"
}
mk; check "$tmp/ws" "$tmp/ws/crates/aib-core" >/dev/null 2>&1 || { echo "SELF-TEST baseline: clean copy fails the check"; exit 1; }
echo "SELF-TEST baseline: clean copy passes"
rc=0
case_run grouped-alias-import src/ids.rs 'use std::{fs, process::Command as Run};
pub fn bypass() { let _ = fs::read("/tmp/probe"); let _ = Run::new("true"); }' || rc=1
case_run absolute-std-path src/ids.rs 'pub fn bypass() { let _ = ::std::fs::read("/tmp/probe"); }' || rc=1
case_run extern-crate-std src/ids.rs 'extern crate std as s;
pub fn bypass() { let _ = s::env::var("HOME"); }' || rc=1
case_run cfg-target-os src/ids.rs '#[cfg(target_os = "macos")]
pub fn mac_only() {}' || rc=1
case_run cfg-macro-windows src/ids.rs 'pub fn w() -> bool { cfg!(windows) }' || rc=1
case_run cfg-any-unix src/ids.rs '#[cfg(any(test, unix))]
pub fn u() {}' || rc=1
case_run unsafe-ffi src/ids.rs 'pub fn f() { unsafe { core::hint::unreachable_unchecked() } }' || rc=1
case_run new-dependency Cargo.toml 'libc = "0.2"' || rc=1
exit $rc
