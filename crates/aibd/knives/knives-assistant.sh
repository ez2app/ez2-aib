#!/usr/bin/env bash
# AS-K (DESIGN-assistant-6770f-1123 r1 §1 §2 §4; f163c REVIEW-assistant r1): one assistant and only with a free slot, only its tools for it and never its tools for others, only listed acts and never dgrs / host seats, a kind field anywhere in a request is caught; the catalog drops routes, paths and addresses and marks data, suggestions are read strictly, an unknown uid is refused, and items become only listed requests. Each rule broken turns its test red.
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
# as-brainroute
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  '"brain" => &["uid", "name", "personas"],' \
  '"brain" => &["uid", "name", "personas", "route_key"],' \
  --expect-fail 'assistant::tests::as5_catalog_entries_drop_paths_routes_and_addresses' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::as5_catalog_entries_drop_paths_routes_and_addresses'
# as-hostaddr
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  '"host" => &["name"],' \
  '"host" => &["name", "addr"],' \
  --expect-fail 'assistant::tests::as5_catalog_entries_drop_paths_routes_and_addresses' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::as5_catalog_entries_drop_paths_routes_and_addresses'
# as-trust
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  'out.insert("trust".into(), Value::String("data".into()));' \
  'let _ = 0;' \
  --expect-fail 'assistant::tests::as5_catalog_entries_drop_paths_routes_and_addresses' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::as5_catalog_entries_drop_paths_routes_and_addresses'
# as-charcut
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  'while !s.is_char_boundary(end) {' \
  'while false {' \
  --expect-fail 'assistant::tests::a_card_text_keeps_its_lines_and_is_cut_on_a_char_boundary' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::a_card_text_keeps_its_lines_and_is_cut_on_a_char_boundary'
# as-count
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  'if items.is_empty() || items.len() > SUGGEST_MAX {' \
  'if items.is_empty() {' \
  --expect-fail 'assistant::tests::suggest_is_read_strictly' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::suggest_is_read_strictly'
# as-seatdraft
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  'if what == "seat" && (uid.is_some() || draft.is_none()) {' \
  'if false {' \
  --expect-fail 'assistant::tests::suggest_is_read_strictly' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::suggest_is_read_strictly'
# as-note
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  '.take(NOTE_MAX)' \
  '.take(usize::MAX)' \
  --expect-fail 'assistant::tests::suggest_is_read_strictly' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::suggest_is_read_strictly'
# as-unknown
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  'Some(u) if !known(&it.what, u) => Err("suggest_unknown_uid"),' \
  'Some(u) if false && !known(&it.what, u) => Err("suggest_unknown_uid"),' \
  --expect-fail 'assistant::tests::as7_labels_and_an_unknown_uid_is_refused' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::as7_labels_and_an_unknown_uid_is_refused'
# as-reqkind
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  'if carries_kind(&serde_json::json!({ "params": params })) {' \
  'if false {' \
  --expect-fail 'assistant::tests::as8_items_become_the_listed_requests_and_nothing_else' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::as8_items_become_the_listed_requests_and_nothing_else'
# as-reqmode
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  'may_request(act, &params)?;' \
  'let _ = may_request(act, &params);' \
  --expect-fail 'assistant::tests::as8_items_become_the_listed_requests_and_nothing_else' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::as8_items_become_the_listed_requests_and_nothing_else'
# as-fields
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  'if o.keys().any(|k| !SEAT_OPEN_FIELDS.contains(&k.as_str())) {' \
  'if false {' \
  --expect-fail 'assistant::tests::as3b_a_seat_request_carries_only_listed_fields_and_a_string_mode' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::as3b_a_seat_request_carries_only_listed_fields_and_a_string_mode'
# as-strmode
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  'Some(_) => return Err(RequestRefused::Mode),' \
  'Some(_) => "safe",' \
  --expect-fail 'assistant::tests::as3b_a_seat_request_carries_only_listed_fields_and_a_string_mode' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::as3b_a_seat_request_carries_only_listed_fields_and_a_string_mode'
# as-pageacts
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  'pub const ACTS_NOT_IN_MAIN_YET: [&str; 2] = ["persona_set", "perm_template_add"];' \
  'pub const ACTS_NOT_IN_MAIN_YET: [&str; 2] = ["persona_set", "persona_set"];' \
  --expect-fail 'assistant::tests::l2_every_request_act_is_a_page_act_or_named_as_not_in_main_yet' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::l2_every_request_act_is_a_page_act_or_named_as_not_in_main_yet'
# as-fmt
python3 -I scripts/knife.py crates/aibd/src/assistant.rs \
  '                        | '\''\u{2060}'\''..='\''\u{2064}'\''' \
  '                        | '\''\u{2061}'\''..='\''\u{2064}'\''' \
  --expect-fail 'assistant::tests::names_lose_every_format_character_of_the_a13_set' \
  -- cargo test -p aibd --locked --lib 'assistant::tests::names_lose_every_format_character_of_the_a13_set'
