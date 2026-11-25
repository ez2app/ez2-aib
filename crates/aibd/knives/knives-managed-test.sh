#!/usr/bin/env bash
# A5 managed test host second slice (HM17-HM19): partner line validation, revoke snapshot diff,
# and dedicated partner_test_ed25519 key.
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
# hm17-line
python3 -I scripts/knife.py crates/aibd/src/managed_test.rs \
  '    if parts.len() != 5 {' \
  '    if parts.is_empty() {' \
  --expect-fail managed_test::tests::hm17_partner_line_is_strictly_validated \
  -- cargo test -p aibd --locked --lib managed_test::tests::hm17_partner_line_is_strictly_validated
# hm17-ip
python3 -I scripts/knife.py crates/aibd/src/managed_test.rs \
  '    if ip_str.parse::<std::net::IpAddr>().is_err() {' \
  '    if ip_str.is_empty() && false {' \
  --expect-fail managed_test::tests::hm17_partner_line_is_strictly_validated \
  -- cargo test -p aibd --locked --lib managed_test::tests::hm17_partner_line_is_strictly_validated
# hm18-diff
python3 -I scripts/knife.py crates/aibd/src/managed_test.rs \
  '        if !before.contains(h.as_str()) {' \
  '        if false && !before.contains(h.as_str()) {' \
  --expect-fail managed_test::tests::hm18_revoke_removes_partner_line_and_lists_added_lines \
  -- cargo test -p aibd --locked --lib managed_test::tests::hm18_revoke_removes_partner_line_and_lists_added_lines
# hm19-key
python3 -I scripts/knife.py crates/aibd/src/managed_test.rs \
  'pub const PARTNER_KEY_FILE: &str = "partner_test_ed25519";' \
  'pub const PARTNER_KEY_FILE: &str = "delegate_ed25519";' \
  --expect-fail managed_test::tests::hm19_test_host_uses_dedicated_partner_key \
  -- cargo test -p aibd --locked --lib managed_test::tests::hm19_test_host_uses_dedicated_partner_key
