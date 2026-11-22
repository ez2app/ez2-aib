#!/usr/bin/env bash
# AS-K (DESIGN-assistant-6770f-1123 r1 §1 §2 §4; f163c REVIEW-assistant r1): one assistant and only with a free slot, only its tools for it and never its tools for others, only listed acts and never dgrs / host seats, and a kind field anywhere in a request is caught. Each rule broken turns its test red.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# as-one
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  'if let Some(s) = existing {' \
  'if let Some(s) = existing.filter(|_| false) {' \
  --expect-fail 'assistant::tests::as6_one_assistant_and_it_needs_a_free_slot' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::as6_one_assistant_and_it_needs_a_free_slot'
# as-slot
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  'if busy >= slots {' \
  'if busy > slots {' \
  --expect-fail 'assistant::tests::as6_one_assistant_and_it_needs_a_free_slot' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::as6_one_assistant_and_it_needs_a_free_slot'
# as-own
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  'if kind == Some(KIND) {' \
  'if kind != Some(KIND) {' \
  --expect-fail 'assistant::tests::as1_the_assistant_gets_only_its_tools_and_others_never_get_them' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::as1_the_assistant_gets_only_its_tools_and_others_never_get_them'
# as-others
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  '!ASSISTANT_ONLY.contains(&bare)' \
  'true' \
  --expect-fail 'assistant::tests::as1_the_assistant_gets_only_its_tools_and_others_never_get_them' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::as1_the_assistant_gets_only_its_tools_and_others_never_get_them'
# as-args
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  'TOOLS.join(","),' \
  '"mcp__aibd__seat_send".to_string() + "," + &TOOLS.join(","),' \
  --expect-fail 'assistant::tests::as1_its_claude_arguments_list_only_its_tools' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::as1_its_claude_arguments_list_only_its_tools'
# as-acts
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  'if !REQUEST_ACTS.contains(&act) {' \
  'if act.is_empty() {' \
  --expect-fail 'assistant::tests::as2_as3_it_asks_only_for_listed_acts_and_never_for_dgrs_or_host' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::as2_as3_it_asks_only_for_listed_acts_and_never_for_dgrs_or_host'
# as-mode
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  'if !matches!(mode, "safe" | "work") {' \
  'if !matches!(mode, "safe" | "work" | "dgrs" | "host") {' \
  --expect-fail 'assistant::tests::as2_as3_it_asks_only_for_listed_acts_and_never_for_dgrs_or_host' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::as2_as3_it_asks_only_for_listed_acts_and_never_for_dgrs_or_host'
# as-kind
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  'body.get("kind").is_some() || body.get("params").and_then(|p| p.get("kind")).is_some()' \
  'body.get("kind").is_some()' \
  --expect-fail 'assistant::tests::as10_a_kind_field_anywhere_a_request_puts_it_is_caught' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::as10_a_kind_field_anywhere_a_request_puts_it_is_caught'
