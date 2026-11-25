#!/usr/bin/env bash
# aib-knives: os=unix
# PT-S (DESIGN-brainnote-08de5-1123 §2b): a template name is unique among live templates. Broken, its test turns red.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# pt-storename
python3 -I scripts/knife.py crates/aib-store/src/perm_templates.rs \
  'WHERE name = ?1 AND archived_at IS NULL' \
  'WHERE name = ?1 AND 0' \
  --expect-fail 'perm_templates::tests::a_name_is_unique_among_live_templates' \
  -- cargo test -p aib-store --locked --lib 'perm_templates::tests::a_name_is_unique_among_live_templates'
