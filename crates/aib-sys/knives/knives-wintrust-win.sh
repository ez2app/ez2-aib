#!/usr/bin/env bash
# aib-knives: os=windows
# aib-knives: job=rust-windows
# L1b Windows Authenticode (08de5 11-26; IFACE-provider-lifecycle-c2f0b-1126 §3.2 S, §7 knife 3;
# ebab8 03:3x: split out for 6770f's L1a fd7e2ca0c6): a file Windows does not trust is never
# "signed", and the Signer-shaped check says not_trusted for it. The leaf-O compare itself needs
# a signed file of a known publisher and is checked on the real machine (UNVERIFIED in the file).
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
# WT1 a refused verify read as signed
python3 -I scripts/knife.py crates/aib-sys/src/wintrust.rs \
  '    let out = if rc != 0 {' \
  '    let out = if rc == i32::MIN {' \
  --expect-fail wintrust::tests::an_unsigned_file_is_not_trusted \
  -- cargo test -p aib-sys --locked --lib wintrust::tests::an_unsigned_file_is_not_trusted
# WT2 the Signer-shaped check lets a refused file through
python3 -I scripts/knife.py crates/aib-sys/src/wintrust.rs \
  '        Trust::Refused(_) => Err("not_trusted"),' \
  '        Trust::Refused(_) => Ok(()),' \
  --expect-fail wintrust::tests::an_unsigned_file_is_not_trusted \
  -- cargo test -p aib-sys --locked --lib wintrust::tests::an_unsigned_file_is_not_trusted
