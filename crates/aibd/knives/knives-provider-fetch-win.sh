#!/usr/bin/env bash
# aib-knives: os=windows
# aib-knives: job=rust-windows
# PFW1 L1a provider_fetch on Windows (IFACE-provider-lifecycle-c2f0b-1126 §3.3, §7 knife 5;
# c2f0b 11-26 03:18 L1 / L2 接點): the checked handle lets others only read, so the file
# cannot be written, renamed or removed between the check and the run.
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
# PFW1 others may write while the checked handle is held
python3 -I scripts/knife.py crates/aibd/src/provider_fetch.rs \
  '        o.share_mode(FILE_SHARE_READ);' \
  '        o.share_mode(FILE_SHARE_READ | 0x2 | 0x4);' \
  --expect-fail provider_fetch::tests::a_checked_file_cannot_be_written_or_replaced_while_its_handle_is_held \
  -- cargo test -p aibd --locked --lib provider_fetch::tests::a_checked_file_cannot_be_written_or_replaced_while_its_handle_is_held
