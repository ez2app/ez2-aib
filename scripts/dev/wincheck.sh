#!/bin/sh
# wincheck.sh — run the Windows (x86_64-pc-windows-msvc) clippy for aibd on a Mac / Linux box.
#
# aibd pulls bundled SQLite; its build script wants a C compiler for the MSVC target. A fake
# MSVC compiler / archiver (fakecc-msvc.sh next to this file) only creates the requested output files, so `cargo clippy`
# (which never links) gets through. This is TYPE CHECK + CLIPPY ONLY: nothing is compiled for
# real and no Windows test runs. Windows tests still need Windows CI or a Windows machine.
#
# usage: scripts/dev/wincheck.sh [--changed <base>] [-p <crate>]... [-- <extra cargo args>]
#   --changed <base>  only run when `git diff <base>...HEAD` touches a source / manifest of aibd or the
#                     crates it builds on (aib-sys, aib-core, aib-store) or a workspace build file; otherwise print
#                     "skip" and exit 0 (meant for an optional, warn-only pre-push step)
#   -p <crate>        crate to check (repeatable; default aibd, which also covers aib-sys)
# exit: 0 green or skipped, 1 clippy red, 2 not verified (target missing / setup failed)
# Report wording: "Windows clippy(fakecc)綠;測試仍靠 CI" — never "Windows green".
set -u
TARGET=x86_64-pc-windows-msvc
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
base=""
pkgs=""
while [ $# -gt 0 ]; do
  case "$1" in
    --changed) [ $# -ge 2 ] || { echo "wincheck: --changed needs <base>" >&2; exit 2; }; base=$2; shift 2;;
    -p) [ $# -ge 2 ] || { echo "wincheck: -p needs <crate>" >&2; exit 2; }; pkgs="$pkgs -p $2"; shift 2;;
    --) shift; break;;
    -h|--help) sed -n '2,16p' "$0"; exit 0;;
    *) echo "wincheck: unknown argument: $1" >&2; exit 2;;
  esac
done
[ -n "$pkgs" ] || pkgs="-p aibd"

if [ -n "$base" ]; then
  files=$(git -C "$root" diff --name-only "$base"...HEAD 2>/dev/null) || {
    echo "wincheck: 沒驗 — git diff $base...HEAD failed" >&2; exit 2; }
  # Shared code is compiled for Windows too, so a file name or a grep for cfg(windows) is not enough
  # (a9136 #547: a change to crates/aibd/src/main.rs alone was red on Windows). Skip only when the diff
  # touches no source or manifest of aibd and the crates it builds on, and no workspace-wide build input.
  hit=$(printf '%s\n' "$files" | grep -E '^crates/(aibd|aib-sys|aib-core|aib-store)/(.*\.rs|Cargo\.toml|build\.rs)$|^(Cargo\.toml|Cargo\.lock|rust-toolchain\.toml|clippy\.toml|\.cargo/.*)$' || true)
  [ -n "$hit" ] || { echo "wincheck: skip — nothing aibd builds from changed since $base"; exit 0; }
fi

if ! command -v cargo >/dev/null 2>&1; then
  echo "wincheck: 沒驗 — cargo not found (Homebrew rustup: export PATH=/opt/homebrew/opt/rustup/bin:\$PATH)" >&2
  exit 2
fi
if ! rustup target list --installed 2>/dev/null | grep -qx "$TARGET"; then
  echo "wincheck: 沒驗 — Rust target $TARGET is not installed" >&2
  echo "  install: rustup target add $TARGET" >&2
  exit 2
fi

fakecc="$here/fakecc-msvc.sh"
[ -x "$fakecc" ] || { echo "wincheck: 沒驗 — $fakecc missing or not executable" >&2; exit 2; }

echo "wincheck: cargo clippy $pkgs --target $TARGET (fakecc; check only, no Windows tests)"
# shellcheck disable=SC2086
CC_x86_64_pc_windows_msvc="$fakecc" AR_x86_64_pc_windows_msvc="$fakecc" \
CARGO_TARGET_DIR="${WINCHECK_TARGET_DIR:-$root/target-wincheck.noindex}" \
  cargo clippy --manifest-path "$root/Cargo.toml" $pkgs --target "$TARGET" --all-targets "$@" -- -D warnings
rc=$?
if [ "$rc" = 0 ]; then
  echo "wincheck: Windows clippy(fakecc)綠;測試仍靠 CI"
  exit 0
fi
echo "wincheck: Windows clippy(fakecc)紅 rc=$rc"
exit 1
