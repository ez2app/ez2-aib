#!/usr/bin/env bash
# FP1–FP22: footprint install, pure logic (DESIGN-fp-install-rust-3e53b-1124 r1 F1; f163c PREREAD r0):
# only what is decided is hashed, nothing is written past a stop or in a read-only step, the
# Cloudflare rule is added and never put over, found by its expression, every read-back judged.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
fp() { python3 -I scripts/knife.py "crates/aib-fp/src/$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-fp --locked --lib "$4"; }
# FP1 (M1) what is only seen goes into the hash
fp plan.rs '            let vals: Vec<String> = row.values.iter().map(|(k, v)| format!("{k}={v}")).collect();' '            let vals: Vec<String> = row.values.iter().chain(row.seen.iter()).map(|(k, v)| format!("{k}={v}")).collect(); // knife' plan::tests::an_observed_value_changing_keeps_the_plan_and_a_decided_one_stops_it
# FP2 (M1) what is decided stays out of the hash
fp plan.rs '                vals.join(";")' '                vals.len() // knife' plan::tests::an_observed_value_changing_keeps_the_plan_and_a_decided_one_stops_it
# FP3 apply writes past a stop
fp plan.rs '            .take_while(|r| !matches!(r.state, State::Stop(_)))' '            .take_while(|_| true) // knife' plan::tests::apply_writes_only_steps_to_do_up_to_the_first_stop
# FP4 apply writes in a read-only step
fp plan.rs '            .filter(|r| r.state == State::Todo && r.step.writes())' '            .filter(|r| r.state == State::Todo) // knife' plan::tests::apply_writes_only_steps_to_do_up_to_the_first_stop
# FP5 a value with a line break forges a line of the hashed form
fp plan.rs "    !s.contains(['\\n', '\\r', '\\t'])" "    !s.contains('\\t') // knife" plan::tests::the_steps_are_all_there_once_and_in_order
# FP6 (2b) rules already in the zone are put over
fp cf.rs '        (0, _) => (State::Todo, Some(Write::Post)),' '        (0, _) => (State::Todo, Some(Write::Put)), // knife' cf::tests::rules_already_there_are_added_to_never_put_over
# FP7 (2b) a zone that was not read is written
fp cf.rs '            state: State::Stop("cf_rules_unreadable".into()),' '            state: State::Todo, // knife' cf::tests::rules_already_there_are_added_to_never_put_over
# FP8 (L2) the rule is found by its name
fp cf.rs '    rule["expression"]' '    rule["description"] // knife' cf::tests::the_rule_is_found_by_its_expression_not_its_name
# FP9 (L1) a switched-off rule counts as the standard one
fp cf.rs '        && rule["enabled"] != false' '        && rule["enabled"] != "x" // knife' cf::tests::a_rule_with_other_settings_is_shown_and_not_changed
# FP10 (M4) a page cache rule in the zone is missed
fp cf.rs '            && r["action_parameters"]["cache"] != false' '            && r["action_parameters"]["cache"] == false // knife' cf::tests::rules_already_there_are_added_to_never_put_over
# FP11 (2b) a 404 with the right max-age passes
fp judge.rs '    status(headers) == Some(200) && max_age(headers) == Some(300)' '    max_age(headers) == Some(300) // knife' judge::tests::the_e2_scripts_keep_the_engines_five_minutes
# FP12 (2b) the first response of a redirect is read
fp judge.rs '        .last()?;' '        .next()?; // knife' judge::tests::the_e2_scripts_keep_the_engines_five_minutes
# FP13 (4b) the engine twice on a page passes
fp judge.rs '        _ => State::Stop("engine_tag_twice".into()),' '        _ => State::Done, // knife' judge::tests::a_page_carries_the_engine_once
# FP14 (M3) the provider is not read back
fp judge.rs '    v["enabled"] == 1 && v["provider"] == "claude" && v["claude_has_key"] == 1' '    v["enabled"] == 1 && v["claude_has_key"] == 1 // knife' judge::tests::the_ai_is_ready_only_with_all_three_read_back
# FP15 (Q2) the script Cloudflare serves without the AI loader passes
fp judge.rs '    if !has_ai_loader(cdn_script) {' '    if !has_ai_loader(cdn_script) && cdn_script.is_empty() { // knife' judge::tests::questions_count_only_with_the_engine_on_the_page_and_the_loader_in_the_cdn_script
# FP16 (7/8) another engine's answer is taken
fp judge.rs '    v["ok"] == 1 && v["n"].as_str() == Some(n) && n.len() >= 16' '    v["ok"] == 1 && n.len() >= 16 // knife' judge::tests::the_answer_host_echoes_our_number
# FP17 (M4) a cooling cache is a failure
fp judge.rs '        4 => State::Wait("cache_cooldown"),' '        4 => State::Stop("cache_clear_exit_4".into()), // knife' judge::tests::no_visitors_and_a_cooling_cache_wait_and_are_not_failures
# FP18 (M2) no visitors yet is a pass
fp judge.rs '    if recent > 0 {' '    if recent > 0 || recent == 0 { // knife' judge::tests::no_visitors_and_a_cooling_cache_wait_and_are_not_failures
# FP19 (6) LINE blocked asks a person instead of stopping
fp judge.rs '        Some(403) => State::Stop("line_webhook_blocked".into()),' '        Some(403) => State::Warn("press_verify_in_line_developers"), // knife' judge::tests::line_blocked_stops_and_line_answered_asks_a_person
# FP20 (Q5) a name another tenant has is taken
fp judge.rs '        if t.slug != slug && hosts.iter().any(|h| t.domains.contains(h)) {' '        if t.slug != slug && hosts.is_empty() { // knife' judge::tests::a_name_or_slug_another_tenant_has_stops_the_install
# FP21 (Q5) a slug held for another domain is taken over
fp judge.rs '        Some(_) => State::Stop("slug_taken_for_other_domain".into()),' '        Some(_) => State::Todo, // knife' judge::tests::a_name_or_slug_another_tenant_has_stops_the_install
# FP22 www. asked for as the domain
fp request.rs '    if s.is_empty() || s.len() > 253 || s.starts_with("www.") {' '    if s.is_empty() || s.len() > 253 { // knife' request::tests::a_request_is_checked_before_any_step
