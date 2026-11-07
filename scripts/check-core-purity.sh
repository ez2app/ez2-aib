#!/usr/bin/env bash
# aib-core purity gate (ADR 0001 / spec section 3).
#
# What enforces what:
#   compiler  #![no_std] — std::fs / process / net / env / thread do not exist,
#             however they are spelled (aliases, grouped `use`, `::std::`).
#   compiler  #![forbid(unsafe_code)] — no FFI back door.
#   xtask     syntax tree + token stream (xtask/src/main.rs; comments do not
#             exist there, macro bodies are scanned too):
#             `extern crate std` only as a real `#[cfg(test)]` item (the one
#             way back to std); cfg predicates limited to test /
#             debug_assertions / doc / feature; no include! and no #[path].
#   this      full dependency tree == allow-list (cargo tree), so I/O cannot come
#             in through a new crate.
# Not covered (stated, not hidden): a proc-macro dependency doing I/O at build
# time, and code in an allowed dependency that is itself impure.
#
#   scripts/check-core-purity.sh             check the real crate
#   scripts/check-core-purity.sh --self-test  prove each rule fails on a bypass
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
xtask_target="${CARGO_TARGET_DIR:-$root/target}"
ALLOWED_TREE="aib-core proc-macro2 quote serde serde_core serde_derive syn thiserror thiserror-impl unicode-ident"

check() {  # $1 = workspace root, $2 = crate dir
  local ws="$1" crate="$2" fail=0
  # the xtask is built from the real workspace, never from the copy under test
  (cd "$root" && CARGO_TARGET_DIR="$xtask_target" cargo run -q --locked -p aib-xtask -- purity "$crate") || fail=1
  local tree
  tree=$(cd "$ws" && cargo tree -p aib-core -e normal,build --prefix none --format '{p}' 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ' | sed 's/ $//')
  [ "$tree" = "$ALLOWED_TREE" ] || { echo "purity: dependency tree is [$tree], allowed [$ALLOWED_TREE]" >&2; fail=1; }
  (cd "$ws" && cargo build -q -p aib-core --lib >/dev/null 2>&1) || { echo "purity: aib-core does not build (no_std, forbid(unsafe_code))" >&2; fail=1; }
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
case_run() {  # name, expected reason, file-relative-to-crate, text appended
  local name="$1" want="$2"; shift 2
  set -- "$name" "$@"
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
  if ! printf '%s\n' "$why" | grep -qF -- "$want"; then
    echo "SELF-TEST $1: MISSED (failed for another reason, wanted: $want): $why"; return 1
  fi
  echo "SELF-TEST $1: caught — $want"
}
mk; check "$tmp/ws" "$tmp/ws/crates/aib-core" >/dev/null 2>&1 || { echo "SELF-TEST baseline: clean copy fails the check"; exit 1; }
echo "SELF-TEST baseline: clean copy passes"
rc=0
case_run grouped-alias-import "aib-core does not build" src/ids.rs 'use std::{fs, process::Command as Run};
pub fn bypass() { let _ = fs::read("/tmp/probe"); let _ = Run::new("true"); }' || rc=1
case_run absolute-std-path "aib-core does not build" src/ids.rs 'pub fn bypass() { let _ = ::std::fs::read("/tmp/probe"); }' || rc=1
case_run extern-crate-std "extern crate std outside #[cfg(test)]" src/ids.rs 'extern crate std as s;
pub fn bypass() { let _ = s::env::var("HOME"); }' || rc=1
case_run comment-disguised-cfg-test "extern crate std outside #[cfg(test)]" src/ids.rs '/* #[cfg(test)] */
extern crate std as sneaky;
pub fn review_probe() { let _ = sneaky::fs::read("/tmp/review-probe"); }' || rc=1
case_run macro-emitted-extern-std "extern crate std emitted by a macro" src/ids.rs 'macro_rules! back { () => { extern crate std as s2; } }
back!();' || rc=1
case_run cfg-target-arch "platform-dependent cfg predicate \`target_arch\`" src/ids.rs '#[cfg(target_arch = "aarch64")]
pub fn arm_only() {}' || rc=1
case_run cfg-target-os "platform-dependent cfg predicate \`target_os\`" src/ids.rs '#[cfg(target_os = "macos")]
pub fn mac_only() {}' || rc=1
case_run cfg-macro-windows "platform-dependent cfg predicate \`windows\`" src/ids.rs 'pub fn w() -> bool { cfg!(windows) }' || rc=1
case_run cfg-any-unix "platform-dependent cfg predicate \`unix\`" src/ids.rs '#[cfg(any(test, unix))]
pub fn u() {}' || rc=1
case_run cfg-inside-macro "platform-dependent cfg predicate \`panic\`" src/ids.rs 'macro_rules! p { () => { #[cfg(panic = "unwind")] pub fn pu() {} } }
p!();' || rc=1
case_run include-macro "include! pulls in code" src/ids.rs 'include!("/tmp/outside.rs");' || rc=1
case_run unsafe-ffi "aib-core does not build" src/ids.rs 'pub fn f() { unsafe { core::hint::unreachable_unchecked() } }' || rc=1
case_run new-dependency "dependency tree is" Cargo.toml 'libc = "0.2"' || rc=1
exit $rc
