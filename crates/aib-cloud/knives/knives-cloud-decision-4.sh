#!/usr/bin/env bash
# aib-knives: job=cloud-knives
# The "aib-cloud decision knives" step of aibd.yml's cloud-knives job, moved here word for word (519d0 11-15
# 08:08, f1fae BLOCKER-aibd-yml-500kb: GitHub creates no run for a workflow file past 500 KB,
# and these two steps were about 255 KB of it). aib-xtask knife-anchors reads crates/*/knives/*.sh,
# so every knife here is still checked. Run from the repo root; -eo pipefail is what
# GitHub's `shell: bash` gave the step when it was inline.
set -eo pipefail
set -eu
ck() { python3 -I scripts/knife.py "crates/aib-cloud/src/$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-cloud --locked --lib "$4"; }
# E11: a process from before a reboot may be stopped
ck op.rs '        return Err(StopRefusal::OtherBoot);' \
   '        let _ = StopRefusal::OtherBoot; // knife' \
   only_the_recorded_process_may_be_stopped
# E11: a pid is signalled again whatever its start time is now
ck op.rs '    matches!(seen, ProcSeen::Live(st) if st == planned_start)' \
   '    matches!(seen, ProcSeen::Live(st) if st == planned_start || st != planned_start) // knife' \
   only_the_recorded_process_may_be_stopped
# E11: a step name with - or . gives no stop code
ck op.rs "            '-' | '.' => out.push('_')," \
   "            '-' | '.' => return None, // knife" \
   a_stop_names_the_step_it_stopped_at
# E11: any character goes into the journal's stop code
ck op.rs "            'a'..='z' | '0'..='9' | '_' => out.push(c)," \
   "            'a'..='z' | '0'..='9' | '_' | 'A'..='Z' | ' ' => out.push(c), // knife" \
   a_stop_names_the_step_it_stopped_at
# E11 L1 (f1fae 11-11): work still writing its progress is not marked not_stuck
ck op.rs '    p == Progress::Running' \
   '    p == Progress::Stalled // knife' \
   a_stop_of_work_still_moving_needs_even_if_running
# E11 L1: a not_stuck plan is carried out without --even-if-running
ck op.rs '    if planned_not_stuck && !even_if_running {' \
   '    if planned_not_stuck && !even_if_running && false { // knife' \
   a_stop_of_work_still_moving_needs_even_if_running
# S14 site software's scheduled events (E10 class 6, 4542e 11-11): an event waiting past the limit is red
ck verify.rs '        (_, Ok(late)) if late > red_after => Outcome::Red("overdue"),' \
   '        (_, Ok(late)) if late > red_after => Outcome::Green, // knife' \
   s14_an_event_waiting_past_the_limit_is_red
# S14: an event not yet due is not late
ck verify.rs '            .filter(|t| **t <= now)' \
   '            .filter(|t| **t <= now || true) // knife' \
   s14_an_event_waiting_past_the_limit_is_red
# S14: no timer is red
ck verify.rs '        Some(Some(CronTimer::Missing)) => return result("S14", Outcome::Red("no_timer")),' \
   '        Some(Some(CronTimer::Missing)) => return result("S14", Outcome::Green), // knife' \
   s14_the_timer_must_be_there_enabled_and_active
# S14: a timer there but not enabled and active is off
ck verify.rs '        ("loaded" | "masked" | "bad-setting" | "error", _, _) => Some(CronTimer::Off),' \
   '        ("loaded" | "masked" | "bad-setting" | "error", _, _) => Some(CronTimer::On), // knife' \
   s14_the_timer_must_be_there_enabled_and_active
# S14: a timer state not understood is unknown
ck verify.rs '        Some(None) => Err("timer_unparsed"),' \
   '        Some(None) => Ok(()), // knife' \
   s14_the_timer_must_be_there_enabled_and_active
# S14: a timer that could not be read is unknown, never green
ck verify.rs '        None => Err("timer_unread"),' \
   '        None => Ok(()), // knife' \
   s14_what_cannot_be_read_is_unknown_never_green
# S14: events that could not be read are unknown, never green
ck verify.rs '        None => Err("events_unread"),' \
   '        None => Ok(0), // knife' \
   s14_what_cannot_be_read_is_unknown_never_green
# S14: an event list not understood is unknown, never green
ck verify.rs '        Some(None) => Err("events_unparsed"),' \
   '        Some(None) => Ok(0), // knife' \
   s14_what_cannot_be_read_is_unknown_never_green
# S14: a line of the list that is not a number is not read past
ck verify.rs '        if !line.bytes().all(|b| b.is_ascii_digit()) {' \
   '        if !line.bytes().all(|b| b.is_ascii_digit()) && false { // knife' \
   s14_what_cannot_be_read_is_unknown_never_green
# S17 (E6 S7, 4542e 11-12): server_tokens off counts only straight inside http
ck verify.rs '                        } else if (path == MAIN && stack.len() == 1 && stack[0] == "http")' \
   '                        } else if (path == MAIN && !stack.is_empty()) // knife' \
   a_template_without_server_tokens_off_is_flagged
# S17: no http-level server_tokens off is red
ck verify.rs '    file.map(|_| off_at_http && !other)' \
   '    file.map(|_| (off_at_http || true) && !other) // knife' \
   a_template_without_server_tokens_off_is_flagged
# S17: a PHP-FPM with expose_php On is red
ck verify.rs '        Some(true) if php.contains(&Some(true)) || pool_on => Outcome::Red("expose_php_on"),' \
   '        Some(true) if (php.contains(&Some(true)) && false) || pool_on => Outcome::Red("expose_php_on"), // knife' \
   a_php_that_names_its_version_is_flagged
# S17: no PHP-FPM, or one whose setting was not read, is unknown, never green
ck verify.rs '        _ if !in_pools || php.is_empty() || php.iter().any(Option::is_none) => {' \
   '        _ if !in_pools && false => { // knife' \
   a_php_that_names_its_version_is_flagged
# S17 (c477a S7 N1): a pool that turns expose_php on is red, though php-fpm -i says Off
ck verify.rs '        Some(true) if php.contains(&Some(true)) || pool_on => Outcome::Red("expose_php_on"),' \
   '        Some(true) if php.contains(&Some(true)) || (pool_on && false) => Outcome::Red("expose_php_on"), // knife' \
   a_pool_that_turns_expose_php_on_is_flagged
# S17: a listing cut before its end line is unknown, never green
ck verify.rs '    if lines[last].trim() != S17_END || at > last {' \
   '    if at > last { // knife' \
   s17_what_cannot_be_read_is_unknown_never_green
# site scan (4542e 11-11, E10 cat. 9): read-only scan of one site; each rule out turns its test red
ck scan.rs '        if other > self {' '        if other > self && false { // knife' \
   the_scan_is_its_worst_check
ck scan.rs '    if checks.is_empty() {' '    if checks.is_empty() && false { // knife' \
   the_scan_is_its_worst_check
ck scan.rs '    if rest.is_empty() || !rest.bytes().all(|b| b.is_ascii_digit()) {' \
   '    if rest.is_empty() || (!rest.bytes().all(|b| b.is_ascii_digit()) && false) { // knife' \
   the_exit_code_is_read_from_the_last_marker
ck scan.rs '        Some((b, rc)) if !s.cut => (b, Some(rc)),' '        Some((b, rc)) => (b, Some(rc)), // knife' \
   a_core_success_needs_exit_zero_and_its_line
ck scan.rs "        None if path.starts_with('/') => return OUTSIDE.to_owned()," \
   "        None if path.starts_with('/') && false => return OUTSIDE.to_owned(), // knife" \
   a_path_outside_the_site_is_not_shown
ck scan.rs $'    if rel.is_empty() || rel.split(\'/\').any(|c| c == "..") {' \
   $'    if rel.is_empty() || (rel.split(\'/\').any(|c| c == "..") && false) { // knife' \
   a_path_outside_the_site_is_not_shown
ck scan.rs "    if name.split('/').any(|part| part.starts_with('.')) {" \
   "    if name.split('/').any(|part| part.starts_with('.')) && false { // knife" \
   a_dot_file_under_uploads_is_listed
ck scan.rs '    if inner_extensions(name).any(|e| EXEC_EXT.contains(&e.as_str())) {' \
   '    if inner_extensions(name).last().is_some_and(|e| EXEC_EXT.contains(&e.as_str())) { // knife' \
   an_inner_extension_counts
ck scan.rs '    a.extend(["-o".to_owned(), "-ctime".to_owned(), days]);' \
   '    a.extend(["-o".to_owned(), "-mtime".to_owned(), days]); // knife' \
   a_change_time_set_back_still_counts
ck scan.rs '            c.push(site_relative(root, p), "checksum_mismatch");' \
   '            let _ = (site_relative(root, p), "checksum_mismatch"); // knife' \
   a_core_file_that_differs_is_a_finding
ck scan.rs '            other = true;' '            other = says_trouble(""); // knife' \
   a_core_warning_nobody_knows_is_unknown
ck scan.rs $'    if c.total > 0 {\n        return c.with(Outcome::Finding, "files_differ");\n    }\n    if rc.is_none() {' \
   $'    if c.total > 0 {\n        return c.with(Outcome::Finding, "files_differ");\n    }\n    if rc.is_none() && false { // knife' \
   a_core_success_needs_exit_zero_and_its_line
ck scan.rs $'    if other || !success {\n        return c.with(Outcome::Unknown, "unrecognized");\n    }\n    if rc != Some(0) {' \
   $'    if other || !success {\n        return c.with(Outcome::Unknown, "unrecognized");\n    }\n    if rc != Some(0) && false { // knife' \
   a_core_success_needs_exit_zero_and_its_line
ck scan.rs '    if differ > 0 {' '    if differ > 0 && false { // knife' \
   a_plugin_file_that_differs_is_a_finding
ck scan.rs $'    if differ > 0 {\n        return c.with(Outcome::Finding, "files_differ");\n    }\n    if rc.is_none() {' \
   $'    if differ > 0 {\n        return c.with(Outcome::Finding, "files_differ");\n    }\n    if rc.is_none() && false { // knife' \
   a_plugin_run_without_a_success_line_is_unknown
ck scan.rs '    if bad {' '    if bad && false { // knife' \
   a_plugin_run_without_a_success_line_is_unknown
ck scan.rs '    let Some((true, Some(skipped))) = closing else {' \
   '    let Some((_, Some(skipped))) = closing.or(Some((true, Some(0)))) else { // knife' \
   a_plugin_run_without_a_success_line_is_unknown
ck scan.rs $'        return c.with(Outcome::Unknown, "not_verified");\n    };\n    if rc != Some(0) {' \
   $'        return c.with(Outcome::Unknown, "not_verified");\n    };\n    if rc != Some(0) && false { // knife' \
   a_plugin_run_without_a_success_line_is_unknown
ck scan.rs '    if skipped != without {' '    if skipped != without && false { // knife' \
   a_skip_count_that_does_not_match_the_warnings_is_unknown
ck scan.rs '    if without > 0 {' '    if without > 0 && false { // knife' \
   a_paid_plugin_without_checksums_is_unknown_never_ok
ck scan.rs '        names.pop();' '        names.truncate(names.len()); // knife' \
   uploads_findings_and_their_limits
ck scan.rs '            _ => strange = true,' '            _ => strange = rel.is_empty(), // knife' \
   uploads_findings_and_their_limits
ck scan.rs $'        return c.with(Outcome::Finding, "files_found");\n    }\n    if rc.is_none() {' \
   $'        return c.with(Outcome::Finding, "files_found");\n    }\n    if rc.is_none() && false { // knife' \
   uploads_findings_and_their_limits
ck scan.rs $'    if strange {\n        return c.with(Outcome::Unknown, "unrecognized");\n    }\n    if rc != Some(0) {' \
   $'    if strange {\n        return c.with(Outcome::Unknown, "unrecognized");\n    }\n    if rc != Some(0) && false { // knife' \
   uploads_findings_and_their_limits
ck scan.rs '    if rc != 0 {' '    if rc != 0 && false { // knife' \
   without_an_opening_record_administrators_are_unknown
ck scan.rs '    if logins.is_empty() {' '    if logins.is_empty() && false { // knife' \
   without_an_opening_record_administrators_are_unknown
ck scan.rs '        return c.with(Outcome::Unknown, "no_opening_record");' \
   '        return c.with(Outcome::Ok, "no_opening_record"); // knife' \
   without_an_opening_record_administrators_are_unknown
ck scan.rs '    if !extra.is_empty() {' '    if !extra.is_empty() && false { // knife' \
   an_administrator_nobody_opened_needs_a_person
# 33e28 review L1 / N3: known protective files are shown, not findings; a file in a hidden folder is listed
ck scan.rs '    if KNOWN_PROTECTIVE.contains(&name) {' '    if KNOWN_PROTECTIVE.contains(&name) && false { // knife' \
   a_known_protective_file_is_shown_but_not_a_finding
ck scan.rs '                if kind != PROTECTIVE {' '                if kind != PROTECTIVE || true { // knife' \
   a_known_protective_file_is_shown_but_not_a_finding
ck scan.rs '        ["-o", "-name", ".*", "-o", "-path", "*/.*", ")", "-print0"]' \
   '        ["-o", "-name", ".*", "-o", "-name", "*/.*", ")", "-print0"] // knife' \
   a_dot_file_under_uploads_is_listed
# E1 customer site create: opening a site is not offered to the customer's side
ck customer.rs '            OwnerRequest::Create { .. } => true, // E1' \
   '            OwnerRequest::Create { .. } => false, // knife' \
   a_customer_key_opens_a_site_and_names_a_domain
# E1: anything with a state is read as an opening, a site's checks among them
ck customer.rs '            v.get("step_done").is_some() && v.get("state").is_some_and(Value::is_string)' \
   '            v.get("state").is_some_and(Value::is_string) // knife' \
   an_opening_is_told_from_the_site_s_checks
# E1 (33e28 M2): a name under one of the platform's own domains is a customer's
ck sitecreate.rs "        name == ours || head.is_some_and(|h| h.ends_with('.'))" \
   "        name == ours || head.is_some_and(|h| h.ends_with('.') && false) // knife" \
   the_platform_s_own_domains_are_never_a_customer_s
# E1 (33e28 M2): the bare name of a listed www name is a customer's
ck sitecreate.rs '        .any(|ours| under(bare, ours) || ours.strip_prefix("www.") == Some(bare))' \
   '        .any(|ours| under(bare, ours)) // knife' \
   the_platform_s_own_domains_are_never_a_customer_s
# E1 (33e28 M2): a line that is not a domain still makes a list
ck sitecreate.rs '        if !crate::edge::valid_domain(line) {' \
   '        if !crate::edge::valid_domain(line) && false { // knife' \
   the_platform_s_own_domains_are_never_a_customer_s
# E1 (33e28 M2): an empty list is a list
ck sitecreate.rs '    (!list.is_empty()).then_some(list)' \
   '    Some(list) // knife' \
   the_platform_s_own_domains_are_never_a_customer_s
# E1: the progress of an opening is worded as a site's checks
python3 -I scripts/knife.py crates/aib-cloud-cli/src/main.rs \
  '        ) if aib_cloud::customer::is_opening(out) => match aib_cloud::customer::opened_of(out) {' \
  '        ) if aib_cloud::customer::is_opening(out) && false => match aib_cloud::customer::opened_of(out) { // knife' \
  --expect-fail an_opening_is_shown_in_the_host_s_words \
  -- cargo test -p aib-cloud-cli --locked an_opening_is_shown_in_the_host_s_words
# HUMAN-PASTE-4542e-1111 17 to 21 (final: Andy 11-12 12:1x): one character of a
# sentence changed is red, word for word as the customer reads it
# 17 opening: one character changed
python3 -I scripts/knife.py crates/aib-cloud/src/wording.rs \
  '站碼 {code}。大約需要幾分鐘' \
  '站號 {code}。大約需要幾分鐘' \
  --expect-fail the_opening_sentences_are_the_final_wording_word_for_word \
  -- cargo test -p aib-cloud-cli --locked the_opening_sentences_are_the_final_wording_word_for_word
# 18 already_opening: one character changed
python3 -I scripts/knife.py crates/aib-cloud/src/wording.rs \
  '已經在開站中(站碼' \
  '已經在開站中(站號' \
  --expect-fail the_opening_sentences_are_the_final_wording_word_for_word \
  -- cargo test -p aib-cloud-cli --locked the_opening_sentences_are_the_final_wording_word_for_word
# 13 / 19 domain_taken: one character changed
python3 -I scripts/knife.py crates/aib-cloud/src/wording.rs \
  '不能重複綁定。' \
  '不能重覆綁定。' \
  --expect-fail the_opening_sentences_are_the_final_wording_word_for_word \
  -- cargo test -p aib-cloud-cli --locked the_opening_sentences_are_the_final_wording_word_for_word
# 20 opened, no administrator named: one character changed
python3 -I scripts/knife.py crates/aib-cloud/src/wording.rs \
  '會另外提供給您。' \
  '會另外提供給你。' \
  --expect-fail the_opening_sentences_are_the_final_wording_word_for_word \
  -- cargo test -p aib-cloud-cli --locked the_opening_sentences_are_the_final_wording_word_for_word
# 21 abandoned: one character changed
python3 -I scripts/knife.py crates/aib-cloud/src/wording.rs \
  '做到一半的部分清掉了' \
  '做到一半的部份清掉了' \
  --expect-fail the_opening_sentences_are_the_final_wording_word_for_word \
  -- cargo test -p aib-cloud-cli --locked the_opening_sentences_are_the_final_wording_word_for_word
# 18: the customer's own opening loses its code
python3 -I scripts/knife.py crates/aib-cloud-cli/src/main.rs \
  'wording::already_opening(&own, name)' \
  'wording::already_opening(&own[..0], name)' \
  --expect-fail the_opening_sentences_are_the_final_wording_word_for_word \
  -- cargo test -p aib-cloud-cli --locked the_opening_sentences_are_the_final_wording_word_for_word
# 18 / 13: already_opening is shown as its code
python3 -I scripts/knife.py crates/aib-cloud-cli/src/main.rs \
  '(Some("already_opening"), OwnerRequest::Create { name, .. }) if code == 2 => {' \
  '(Some("already_opening_knife"), OwnerRequest::Create { name, .. }) if code == 2 => {' \
  --expect-fail the_opening_sentences_are_the_final_wording_word_for_word \
  -- cargo test -p aib-cloud-cli --locked the_opening_sentences_are_the_final_wording_word_for_word
# 19: domain_taken is shown as its code
python3 -I scripts/knife.py crates/aib-cloud-cli/src/main.rs \
  '(Some("domain_taken"), OwnerRequest::Create { .. }) if code == 2 => {' \
  '(Some("domain_taken_knife"), OwnerRequest::Create { .. }) if code == 2 => {' \
  --expect-fail the_opening_sentences_are_the_final_wording_word_for_word \
  -- cargo test -p aib-cloud-cli --locked the_opening_sentences_are_the_final_wording_word_for_word
# 17: the opening started is shown as the host's document
python3 -I scripts/knife.py crates/aib-cloud-cli/src/main.rs \
  '        (_, OwnerRequest::Create { name, .. }) if code == 0 => {' \
  '        (_, OwnerRequest::Create { name, .. }) if code == 9 => { // knife' \
  --expect-fail the_opening_sentences_are_the_final_wording_word_for_word \
  -- cargo test -p aib-cloud-cli --locked the_opening_sentences_are_the_final_wording_word_for_word
# 21: an abandoned opening is shown as the host's document
python3 -I scripts/knife.py crates/aib-cloud-cli/src/main.rs \
  'aib_cloud::customer::abandoned_domain_of(out)' \
  'aib_cloud::customer::abandoned_domain_of("") /* knife */' \
  --expect-fail the_opening_sentences_are_the_final_wording_word_for_word \
  -- cargo test -p aib-cloud-cli --locked the_opening_sentences_are_the_final_wording_word_for_word
# 20: an opened site with no administrator named is shown as the host's document
python3 -I scripts/knife.py crates/aib-cloud-cli/src/main.rs \
  'aib_cloud::customer::opened_without_login_of(out)' \
  'aib_cloud::customer::opened_without_login_of("") /* knife */' \
  --expect-fail the_opening_sentences_are_the_final_wording_word_for_word \
  -- cargo test -p aib-cloud-cli --locked the_opening_sentences_are_the_final_wording_word_for_word
# 17 to 21: what fills a sentence in reaches the terminal as it came
ck wording.rs '    crate::svc::plain(word)' \
   '    String::from(word) // knife' \
   the_opening_sentences_show_nothing_that_acts_on_the_terminal
# 17 to 21: the answers are read from any shape
ck customer.rs '    if !stopped || !abandoned || !is_opening(reply) {' \
   '    if !stopped || !(abandoned || true) || !is_opening(reply) { // knife' \
   the_opening_answers_are_read_only_from_their_own_shape
ck customer.rs '        .is_some_and(|a| !a.is_empty());' \
   '        .is_some_and(|_| false); // knife' \
   the_opening_answers_are_read_only_from_their_own_shape
ck customer.rs '    let known = crate::site::valid_code(site);' \
   '    let known = !site.is_empty(); // knife' \
   the_opening_answers_are_read_only_from_their_own_shape
# B5 DNS (4542e): only a zone of an account the roster names is used
ck sitedns.rs '                same_name(&z.name, &name) && z.status == "active" && accounts.contains(&z.account)' \
   '                same_name(&z.name, &name) && z.status == "active" && (accounts.contains(&z.account) || true) // knife' \
   only_an_active_zone_of_the_customers_account_is_used
# B5 DNS: a zone that is not active is used
ck sitedns.rs '                same_name(&z.name, &name) && z.status == "active" && accounts.contains(&z.account)' \
   '                same_name(&z.name, &name) && (z.status == "active" || true) && accounts.contains(&z.account) // knife' \
   of_two_zones_with_one_name_only_the_active_one_counts
# B5 DNS: of two active zones with one name the first is taken
ck sitedns.rs '            _ => return Err(ZONE_AMBIGUOUS),' \
   '            _ => return Ok((*found[0]).clone()), // knife' \
   of_two_zones_with_one_name_only_the_active_one_counts
# B5 DNS: a roster without accounts reads as "any account"
ck sitedns.rs '    let accounts = accounts.filter(|a| !a.is_empty()).ok_or(OWNER_UNKNOWN)?;' \
   '    let accounts = accounts.unwrap_or(&[]); // knife' \
   a_roster_without_accounts_cannot_tell
# B5 DNS: a call off the list of five is let through
ck sitedns.rs '        _ => false,' '        _ => true, // knife' \
   only_the_five_calls_are_allowed
# abandon (4542e): a delete of something that is not one record by its id is let through
ck sitedns.rs '        ("DELETE", ["", "zones", z, "dns_records", r], None, None) => valid_id(z) && valid_id(r),' \
   '        ("DELETE", ["", "zones", z, "dns_records", ..], None, None) => valid_id(z), // knife' \
   only_the_five_calls_are_allowed
# B5 DNS (ebb36 L1): an add of an AAAA is let through
ck sitedns.rs '            Some("A") => m["content"].as_str().is_some_and(valid_origin),' \
   '            Some("A" | "AAAA") => m["content"].as_str().is_some_and(valid_origin), // knife' \
   an_add_must_carry_a_sites_mark_and_be_proxied
# B5 DNS (ebb36 L1): an add to anything but a public address is let through
ck sitedns.rs '            Some("A") => m["content"].as_str().is_some_and(valid_origin),' \
   '            Some("A") => m["content"].is_string(), // knife' \
   an_add_must_carry_a_sites_mark_and_be_proxied
# B5 DNS (ebb36 L2): a name outside the zone is added under it
ck sitedns.rs '    if !names.iter().all(|n| in_zone(n, zone)) {' \
   '    if !names.iter().all(|n| in_zone(n, zone)) && false { // knife' \
   a_name_outside_the_zone_adds_nothing
# B5 DNS: an add without a site's mark is let through
ck sitedns.rs '            .is_some_and(crate::site::valid_code)' \
   '            .is_some_and(|_| true) // knife' \
   an_add_must_carry_a_sites_mark_and_be_proxied
# B5 DNS: an add that is not proxied is let through
ck sitedns.rs '        && m["proxied"] == Value::Bool(true)' \
   '        && (m["proxied"] == Value::Bool(true) || true) // knife' \
   an_add_must_carry_a_sites_mark_and_be_proxied
# B5 DNS: a name answered by a record without this site's mark is written over
ck sitedns.rs '        if here.iter().any(|r| r.comment != mark) {' \
   '        if here.iter().any(|r| r.comment != mark) && false { // knife' \
   a_name_answered_elsewhere_is_left_alone
# E6 S12 adopt (4542e): a record pointing elsewhere is edited
ck sitedns.rs '                _ if r.kind != "A" || r.content != origin => Adopt::Elsewhere,' \
   '                _ if r.kind != "A" || (r.content != origin && false) => Adopt::Elsewhere, // knife' \
   adopting_edits_only_a_records_to_this_host
# E6 S12 adopt: a proxied, marked record is edited again
ck sitedns.rs '                Some(_) if r.proxied => Adopt::Unchanged,' \
   '                Some(_) if r.proxied && false => Adopt::Unchanged, // knife' \
   adopting_edits_only_a_records_to_this_host
# E6 S12 adopt (c477a M1): an empty old comment becomes the bare mark, so site remove deletes it
ck sitedns.rs '        ADOPTED_TAIL' '        return mark; // knife' \
   an_adopted_comment_is_never_the_bare_mark_so_site_remove_keeps_it
# E6 S12 adopt (c477a L1): a grey record already marked for this site is marked twice
ck sitedns.rs '    if marked_by(old) == Some(code) {' \
   '    if marked_by(old) == Some(code) && false { // knife' \
   adopting_a_grey_record_already_marked_for_this_site_keeps_its_comment
# E6 S12 adopt: an SSL mode that is not full lets the adopt through
ck sitedns.rs '    ssl?;' '    let _ = ssl; // knife' \
   adopt_needs_full_ssl_and_a_challenge_the_proxy_passes
# E6 S12 adopt: an Edge proved by TLS-ALPN alone lets the adopt through
ck sitedns.rs '            Some(["tls-alpn-01"]) => return Err(ACME_ALPN_ONLY),' \
   '            Some(["tls-alpn-01"]) => {} // knife' \
   adopt_needs_full_ssl_and_a_challenge_the_proxy_passes
# E6 S12 adopt: an edit that is not proxied is let through
ck sitedns.rs '        && m.get("proxied") == Some(&Value::Bool(true))' \
   '        && (m.get("proxied") == Some(&Value::Bool(true)) || true) // knife' \
   an_adopting_edit_is_allowed_only_proxied_and_marked
# E6 S12 adopt: a policy that turns HTTP-01 off is not seen
ck sitedns.rs '                if !off("http") {' '                if !off("http") || true { // knife' \
   the_edges_challenges_are_read_from_its_merged_config
# B5 DNS: a record with the mark counts as done whatever it points at
ck sitedns.rs '            [ours] if target.is(ours) && ours.proxied => continue,' \
   '            [ours] if ours.comment == mark => continue, // knife' \
   this_sites_record_is_the_step_done
# E6 S5 (4542e): a CNAME to the host's entry name is not let through
ck sitedns.rs '            Some("CNAME") => m["content"].as_str().is_some_and(to_entry),' \
   '            Some("CNAME") => m["content"].as_str().is_some_and(to_entry) && false, // knife' \
   the_add_is_a_cname_to_the_entry
# E6 S5: a CNAME to any name is let through
ck sitedns.rs '            Some("CNAME") => m["content"].as_str().is_some_and(to_entry),' \
   '            Some("CNAME") => m["content"].as_str().is_some_and(|c| !c.is_empty() || to_entry(c)), // knife' \
   a_cname_elsewhere_is_refused
# E6 S5: this site's CNAME pointing elsewhere counts as the step done
ck sitedns.rs '                (r.kind == "CNAME" && same_name(&r.content, name))' \
   '                (r.kind == "CNAME" && !name.is_empty()) // knife' \
   a_cname_elsewhere_is_refused
# E6 S5: an entry name that is one of the site's own names is used
ck sitedns.rs '        if !crate::edge::valid_domain(name) || names.iter().any(|n| same_name(n, name)) {' \
   '        if !crate::edge::valid_domain(name) { // knife' \
   a_cname_elsewhere_is_refused
# E6 S5 (c477a L1): a zone in another account than the entry gets the CNAME (1014)
ck sitedns.rs '    if entry_account != Some(zone.account.as_str()) {' \
   '    if entry_account != Some(zone.account.as_str()) && false { // knife' \
   a_zone_in_another_account_falls_back_to_a
# E6 S5 (c477a L1): no entry account set still lets the CNAME be judged
ck sitedns.rs '    if !entry_account.is_some_and(valid_id) {' \
   '    if !entry_account.is_some_and(valid_id) && false { // knife' \
   no_entry_account_means_no_cname
# E6 S5 (c477a N1): a name with a TXT / MX that is not the site's gets a CNAME
ck sitedns.rs '    if records.iter().any(in_use) {' \
   '    if records.iter().any(in_use) && false { // knife' \
   a_name_in_use_falls_back_to_a
# B5 DNS: a wildcard that answers for the name today is passed over
ck sitedns.rs '            .any(|r| wildcard_covers(&r.name, name))' \
   '            .any(|r| wildcard_covers(&r.name, name) && false) // knife' \
   a_wildcard_that_answers_today_stops
# B5 DNS: mail and text records count as address records
ck sitedns.rs '    let address = |r: &&Record| DNS_ADDRESS_TYPES.contains(&r.kind.as_str());' \
   '    let address = |r: &&Record| !r.kind.is_empty() || DNS_ADDRESS_TYPES.is_empty(); // knife' \
   mail_and_text_records_are_not_looked_at
# B5 DNS: a listing short of its count reads as whole
ck sitedns.rs '    (all.len() == usize::try_from(first.total_count).ok()?).then_some(all)' \
   '    let _ = first.total_count; Some(all) // knife' \
   a_listing_is_whole_only_when_its_pages_and_count_agree
# B5 DNS: a flexible SSL mode is taken
ck sitedns.rs '        Some("off" | "flexible") => Err(SSL_MODE_NOT_FULL),' \
   '        Some("off" | "flexible") => Ok(()), // knife' \
   the_ssl_mode_must_reach_the_host_over_https
# B5 DNS: a private address is taken for the host's own
ck sitedns.rs '    !private && o[3] != 0' '    let _ = private; o[3] != 0 // knife' \
   an_unreadable_listing_or_no_origin_adds_nothing
# B5 DNS: a refusal on an account's listing is not told as access taken away
ck sitedns.rs '        403 if listing_an_account => ACCESS_REVOKED,' \
   '        403 if listing_an_account && false => ACCESS_REVOKED, // knife' \
   a_refusal_is_told_apart_only_where_it_means_something
# B5 roster: cloudflare_accounts read leniently
ck roster.rs '        Some(v) => Some(cloudflare_accounts(v).ok_or(Bad::CloudflareAccounts)?),' \
   '        Some(v) => cloudflare_accounts(v), // knife' \
   the_customers_cloudflare_accounts_are_read_strictly
# B5 roster: an account id twice is accepted
ck roster.rs '        if out.iter().any(|x| x == id) {' \
   '        if out.iter().any(|x| x == id) && false { // knife' \
   the_customers_cloudflare_accounts_are_read_strictly
# rescue API origin (f1fae 11-11, §16): plain http is refused again
ck robot.rs '        let rest = raw.strip_prefix("https://").ok_or(Error::BadOrigin)?;' \
   '        let rest = raw.strip_prefix("https://").or_else(|| raw.strip_prefix("http://")).ok_or(Error::BadOrigin)?; // knife' \
   api_origin_rejects_everything_else
# rescue API origin: an approval no longer names where the request goes
ck robot.rs '            self.origin.as_str(),' \
   '            "", // knife' \
   plan_hash_changes_with_request
# rescue API origin: an IPv4 address passes as a host name again (c477a N1)
ck robot.rs "            !host.is_empty() && host.len() <= 253 && host.split('.').all(label_ok) && tld_ok;" \
   "            !host.is_empty() && host.len() <= 253 && host.split('.').all(label_ok) && (tld_ok || true); // knife" \
   api_origin_rejects_everything_else
# abandon (4542e): a site is abandoned
ck siteabandon.rs '        Some(true) => return Some(ALREADY_A_SITE),' \
   '        Some(true) => {} // knife' \
   a_site_is_never_abandoned
# abandon: an opening of another code or domain is abandoned
ck siteabandon.rs '    if !g.opening_matches {' \
   '    if !g.opening_matches && false { // knife' \
   only_the_opening_of_this_code_and_domain
# abandon: a live site's container is destroyed
ck siteabandon.rs '        Some(true) => return Some(CT_OF_A_SITE),' \
   '        Some(true) => {} // knife' \
   only_a_container_named_for_the_code_and_no_sites
# abandon: a container under another name is destroyed
ck siteabandon.rs '        Some(Some(h)) if h != code => Some(CT_NOT_THIS_OPENING),' \
   '        Some(Some(h)) if h != code && false => Some(CT_NOT_THIS_OPENING), // knife' \
   only_a_container_named_for_the_code_and_no_sites
# abandon: a container that could not be looked at counts as none
ck siteabandon.rs '        None => Some(PROBE_UNKNOWN),' \
   '        None => None, // knife' \
   anything_in_doubt_stops_everything
# abandon: one probe not read goes on with the others
ck siteabandon.rs '    if probes.contains(&Probe::Unknown) {' \
   '    if probes.contains(&Probe::Unknown) && false { // knife' \
   anything_in_doubt_stops_everything
# abandon: a short list of probes goes on
ck siteabandon.rs '    if probes.len() != STEPS.len() {' \
   '    if probes.len() != STEPS.len() && false { // knife' \
   anything_in_doubt_stops_everything
