#!/usr/bin/env bash
# KE29 for the aib-expect tests' own folders (#789), kept out of aibd.yml (RULES §21, 519d0 11-16 23:5x:
# new knives go in crates/<crate>/knives/, the workflow only calls them). The "aib-expect knives (macOS)"
# step runs this file; aib-xtask knife-anchors reads crates/*/knives/*.sh, so the knife is still checked.
# Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# KE29 (f1fae 11-15, #775 job 132666834957) two test roots with one tag share a folder
python3 -I scripts/knife.py crates/aib-expect/tests/expect.rs \
  '        let n = NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);' \
  '        let n = NEXT.load(std::sync::atomic::Ordering::Relaxed); // knife' \
  --expect-fail two_test_roots_with_one_tag_never_share_a_folder \
  -- cargo test -p aib-expect --locked --test expect two_test_roots_with_one_tag_never_share_a_folder
