#!/usr/bin/env bash
# aib-knives: os=unix
# A15 (DESIGN-a15-quota-08de5-1123 r1, f163c REVIEW-a15 r0/r1): each brand's account limits from its own seats, whitelisted fields only
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# a15-word
python3 -I scripts/knife.py crates/aibd/src/usage.rs \
  '            (1..=32).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_lowercase() || b == b'\''_'\'')' \
  '            (1..=32).contains(&s.len())' \
  --expect-fail 'usage::tests::odd_words_and_numbers_are_dropped' \
  -- cargo test -p aibd --locked --lib 'usage::tests::odd_words_and_numbers_are_dropped'
# a15-num
python3 -I scripts/knife.py crates/aibd/src/usage.rs \
  '        .filter(|n| n.is_finite() && (lo..=hi).contains(n))' \
  '        .filter(|n| n.is_finite() && *n >= lo && hi.is_finite())' \
  --expect-fail 'usage::tests::odd_words_and_numbers_are_dropped' \
  -- cargo test -p aibd --locked --lib 'usage::tests::odd_words_and_numbers_are_dropped'
# a15-claude
python3 -I scripts/knife.py crates/aibd/src/usage.rs \
  '    if v["type"] == "rate_limit_event" {' \
  '    if v["type"] == "rate_limit_events" {' \
  --expect-fail 'usage::tests::claude_says_its_limits_and_only_the_listed_fields_are_kept' \
  -- cargo test -p aibd --locked --lib 'usage::tests::claude_says_its_limits_and_only_the_listed_fields_are_kept'
# a15-plan
python3 -I scripts/knife.py crates/aibd/src/usage.rs \
  '    if let Some(p) = word(&r["planType"]) {' \
  '    if let Some(p) = r["credits"]["balance"].as_str().map(str::to_owned) {' \
  --expect-fail 'usage::tests::codex_says_its_limits_and_never_its_balance' \
  -- cargo test -p aibd --locked --lib 'usage::tests::codex_says_its_limits_and_never_its_balance'
# a15-unseen
python3 -I scripts/knife.py crates/aibd/src/usage.rs \
  '                    None => json!({"quota": null, "quota_reason": "not_seen_yet"}),' \
  '                    None => json!({"quota": null}),' \
  --expect-fail 'usage::tests::a_brand_not_heard_yet_says_so' \
  -- cargo test -p aibd --locked --lib 'usage::tests::a_brand_not_heard_yet_says_so'
# a15-ask
python3 -I scripts/knife.py crates/aibd/src/codex_wire.rs \
  '        &json!({"jsonrpc": "2.0", "id": QUOTA_ID, "method": "account/rateLimits/read"}),' \
  '        &json!({"jsonrpc": "2.0", "id": QUOTA_ID, "method": "initialized"}),' \
  --expect-fail 'codex_wire::tests::the_opening_carries_the_persona_in_the_protocol' \
  -- cargo test -p aibd --locked --lib 'codex_wire::tests::the_opening_carries_the_persona_in_the_protocol'
# a15-wordonly (f163c pre L1)
python3 -I scripts/knife.py crates/aibd/src/usage.rs \
  '        if let Some((brand, mut q)) = limit {' \
  '        if let Some((brand, mut q)) = limit.or(Some(("claude", json!({})))) {' \
  --expect-fail 'usage::tests::an_answer_naming_rate_limits_still_moves_the_usage' \
  -- cargo test -p aibd --locked --lib 'usage::tests::an_answer_naming_rate_limits_still_moves_the_usage'
# a15-seen (f163c a15page pre: seen_at in Unix seconds like resets_at)
python3 -I scripts/knife.py crates/aibd/src/usage.rs \
  '        .map_or(0, |d| d.as_secs())' \
  '        .map_or(0, |d| d.as_secs() / 1_000_000)' \
  --expect-fail 'usage::tests::a_limit_line_is_kept_with_seen_at_in_seconds' \
  -- cargo test -p aibd --locked --lib 'usage::tests::a_limit_line_is_kept_with_seen_at_in_seconds'
