#!/usr/bin/env bash
# aib-knives: os=unix
# a preview starts with stdio only (519d0 11-20, #867 macOS fd_inherited)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
# SO1 descriptors above 2 are left inheritable
python3 -I scripts/knife.py crates/aib-sys/src/stdio_only.rs \
  '        let _ = unsafe { libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC) };' \
  '        let _ = unsafe { libc::fcntl(fd, libc::F_GETFD, libc::FD_CLOEXEC) }; // knife' \
  --expect-fail a_descriptor_opened_without_cloexec_does_not_reach_the_child \
  -- cargo test -p aib-sys --locked --lib stdio_only::tests::a_descriptor_opened_without_cloexec_does_not_reach_the_child
