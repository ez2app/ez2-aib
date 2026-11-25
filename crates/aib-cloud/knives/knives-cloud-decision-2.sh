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
# ARC: a raise goes past what the plan allows (4.4.1 23)
ck arc.rs '            let next = (limits.cpulimit_x100 + 100).min(cpu_max);' \
   '            let next = limits.cpulimit_x100 + 100; // knife' \
   never_past_the_plan
# ARC: each site gets the whole plan to itself (Andy 11-10: the envelope is the customer's)
ck arc.rs '    let memory_left = plan.memory_mb.saturating_sub(held.memory_mb);' \
   '    let memory_left = plan.memory_mb; // knife' \
   sites_of_one_customer_share_the_envelope
# ARC: a raise the host cannot back is proposed all the same
ck arc.rs '            if room.memory_mb < next - limits.memory_mb {' \
   '            if room.memory_mb == u32::MAX { // knife' \
   never_past_what_the_host_can_back
# usage: memory is judged on current use, cache included (measured 11-10: every site would be raised)
ck usage.rs '    u32::try_from(current.saturating_sub(inactive).div_ceil(1024 * 1024)).ok()' \
   '    u32::try_from((current + inactive * 0).div_ceil(1024 * 1024)).ok()' \
   the_working_set_leaves_the_cache_out
# customer side: a host that starts with a dash reaches ssh as an option
ck client.rs '        && edge(b.first())' \
   '        && b.first().is_some() // knife' \
   nothing_can_be_read_as_an_option
# mask: a cpuinfo field nobody named reaches the container
ck mask.rs '        _ => None,' \
   '        _ => Some(value.into()), // knife' \
   a_field_nobody_named_is_not_passed_on
# mask: the real model name reaches the container
ck mask.rs '        "model name" => Some(CPU_MODEL.into()),' \
   '        "model name" => Some(value.into()), // knife' \
   the_machine_is_not_named_in_cpuinfo
# mask: the platform template's appliance file is not on the list to remove (E6 S1)
ck mask.rs 'pub const PLATFORM_FILES: &[&str] = &["/etc/appliance.info"];' \
   'pub const PLATFORM_FILES: &[&str] = &[]; // knife' \
   no_platform_file_stays
# mask: under /sys something nobody named is left readable
ck mask.rs '            step.cover.push(path);' \
   '            step.descend.push(path); // knife' \
   under_sys_only_what_is_named_stays
# mask: a multicast address passes as a site's own (Andy 11-11)
ck mask.rs '    shaped && u8::from_str_radix(parts[0], 16).is_ok_and(|b| b & 0x03 == 0x02)' \
   '    shaped && u8::from_str_radix(parts[0], 16).is_ok_and(|b| b & 0x02 == 0x02) // knife' \
   a_site_hwaddr_is_local_and_unicast
# mask: a drawn address keeps the multicast bit it was drawn with
ck mask.rs '    let first = (raw[0] & 0xFC) | 0x02;' \
   '    let first = raw[0] | 0x02; // knife' \
   a_site_hwaddr_is_local_and_unicast
# mask plan: a second hook next to the wrapper is left in place
ck mask.rs '    let hook_ok = matches!(hooks.as_slice(), [one] if crate::site::mask_hook_ok(one));' \
   '    let hook_ok = matches!(hooks.as_slice(), [one, ..] if crate::site::mask_hook_ok(one)); // knife' \
   a_second_hook_or_a_foreign_one_is_not_the_mask
# mask plan: an IPv6 gateway alone stays on the CT
ck mask.rs '        if f.starts_with("ip6=") || f.starts_with("gw6=") {' \
   '        if f.starts_with("ip6=") && f.starts_with("gw6=") { // knife' \
   ipv6_alone_or_no_hwaddr_changes_net0
# mask plan: a CT without an address of its own keeps the one the platform draws
ck mask.rs '    if !had_hwaddr {' \
   '    if !had_hwaddr && false { // knife' \
   ipv6_alone_or_no_hwaddr_changes_net0
# mask plan: a search domain that names our network is kept
ck mask.rs '    if get("searchdomain") != Some(crate::verify::SEARCH_DOMAIN) {' \
   '    if get("searchdomain").is_none() { // knife' \
   what_an_unmasked_ct_needs_is_each_named
# mask plan: the tailnet's own resolver passes as a public one (c477a, ct-mask2 N1)
ck mask.rs '        let shared = a == 100 && (64..128).contains(&b);' \
   '        let shared = a == 100 && (64..128).contains(&b) && false; // knife' \
   a_resolver_of_ours_or_the_hosts_is_not_neutral
# mask plan: a reserved or benchmarking address passes as a public resolver (c477a, ct-mask2 N4)
ck mask.rs '        let special = a >= 240 || (a == 198 && (b & 0xFE) == 18);' \
   '        let special = (a >= 240 || (a == 198 && (b & 0xFE) == 18)) && false; // knife' \
   a_resolver_that_is_no_public_host_is_not_neutral
# mask plan: a second network interface goes unnamed (c477a, ct-mask2 N2)
ck mask.rs '                .is_some_and(|n| !n.is_empty() && n != "0" && n.bytes().all(|c| c.is_ascii_digit()))' \
   '                .is_some_and(|n| !n.is_empty() && n != "0" && n.bytes().all(|c| c.is_ascii_digit()) && false) // knife' \
   another_nic_is_named_for_a_person
# mask: a limit above zero reads as closed (c477a, ct-mask2 L2)
ck mask.rs '    proc_value?.trim().parse::<u64>().ok().map(|n| n == 0)' \
   '    proc_value?.trim().parse::<u64>().ok().map(|n| n < 100_000) // knife' \
   the_user_namespace_limit_is_read_back
# site verify S11: any resolver passes, our network's too (c477a, ct-mask2 N1)
ck verify.rs '    let outcome = if !get(&cfg, "nameserver").is_some_and(crate::mask::resolver_is_neutral) {' \
   '    let outcome = if get(&cfg, "nameserver").is_none() { // knife' \
   s10_to_s13_and_h15_the_site_is_masked
# site verify S12: any address of the right length passes
ck verify.rs '    let outcome = if crate::mask::hwaddr_is_local(mac) {' \
   '    let outcome = if mac.len() == 17 { // knife' \
   s10_to_s13_and_h15_the_site_is_masked
# customer wording: a row or a code the table does not know is shown all the same
ck wording.rs '        if let (Some(name), Some(sentence)) = (name, sentence) {' \
   '        if let (Some(name), Some(sentence)) = (name.or(Some("?")), sentence.or(Some("?"))) { // knife' \
   status_reads_as_sentences_and_shows_nothing_it_does_not_know
# customer wording: the neutral answer gives way to "could not be read"
ck wording.rs '            if said("provider_check") {' \
   '            if said("provider_check") && exit == 0 { // knife' \
   every_answer_has_its_sentence
# E1 N1 opened screen (HUMAN-PASTE-4542e-1112, Andy 11-12 04:03): the second
# administrator's half sentence is dropped, or kept when there is none
ck wording.rs '        Some(s) => format!("(另一位:{})", crate::svc::plain(s)),' \
   '        Some(_) => String::new(), // knife' \
   the_opened_screen_is_the_final_wording_word_for_word
ck wording.rs '        None => String::new(),' \
   '        None => "(另一位:)".to_owned(), // knife' \
   the_opened_screen_is_the_final_wording_word_for_word
# E1 N1 opened screen: a domain or a login reaches the terminal as it came
ck wording.rs '    let domain = crate::svc::plain(domain);' \
   '    let domain = String::from(domain); // knife' \
   the_opened_screen_shows_nothing_that_acts_on_the_terminal
ck wording.rs '    let login = crate::svc::plain(login);' \
   '    let login = String::from(login); // knife' \
   the_opened_screen_shows_nothing_that_acts_on_the_terminal
ck wording.rs '        Some(s) => format!("(另一位:{})", crate::svc::plain(s)),' \
   '        Some(s) => format!("(另一位:{})", String::from(s)), // knife' \
   the_opened_screen_shows_nothing_that_acts_on_the_terminal
# E1 N1 opened screen: the site's code reaches the terminal as it came
ck wording.rs '    let code = crate::svc::plain(code);' \
   '    let code = String::from(code); // knife' \
   the_opened_screen_shows_nothing_that_acts_on_the_terminal
# E1 N1 opened screen: an empty second login, or the first again, is a second one (c477a N1 / N2)
ck wording.rs '    let second = second.filter(|s| !s.is_empty() && *s != login);' \
   '    let second = second; // knife' \
   a_second_administrator_that_is_not_another_one_is_left_out
# login-link (Andy 11-12 04:3x): a failure's output, or more than one line, is shown as a link
ck wording.rs '        if exit == 0 && only.starts_with("https://") && crate::svc::plain(only) == only {' \
   '        if (exit == 0 || exit == 3) && only.starts_with("https://") { // knife (exit stays read: -D warnings)' \
   a_login_link_is_shown_alone_or_not_at_all
ck wording.rs '    if let [only] = lines.as_slice() {' \
   '    if let [only, ..] = lines.as_slice() { // knife' \
   a_login_link_is_shown_alone_or_not_at_all
# login-link: the word is not a request, is not offered, or is anyone's
ck customer.rs '        "login-link" => alone(Request::LoginLink),' \
   '        "login-link" => alone(Request::Status), // knife' \
   the_whole_grammar
ck customer.rs '            Request::Status | Request::Logs { .. } | Request::LoginLink' \
   '            Request::Status | Request::Logs { .. } // knife' \
   everything_else_is_refused_whole
# Andy 11-12: the login link is the owner's and the platform's, not an editor's
ck customer.rs '            Request::LoginLink => Operation::Administer,' \
   '            Request::LoginLink => Operation::Edit, // knife' \
   a_role_gives_what_the_table_says_and_no_more
ck customer.rs '            Operation::Administer => matches!(self, Owner | Admin),' \
   '            Operation::Administer => matches!(self, Owner | Admin | Editor), // knife' \
   a_role_gives_what_the_table_says_and_no_more
# opened view (f1fae 11-12): read from an answer that is not open, or without logins
ck customer.rs '    if v.get("state").and_then(Value::as_str) != Some("open") || !is_opening(reply) {' \
   '    if !is_opening(reply) { // knife' \
   an_opened_site_is_read_only_from_its_own_shape
ck customer.rs '        let a = a.as_str().filter(|a| !a.is_empty())?;' \
   '        let a = a.as_str()?; // knife' \
   an_opened_site_is_read_only_from_its_own_shape
ck customer.rs '    if admins.is_empty() {' \
   '    if admins.is_empty() && false { // knife' \
   an_opened_site_is_read_only_from_its_own_shape
# customer side: a just-opened site is not shown as the opened screen
python3 -I scripts/knife.py crates/aib-cloud-cli/src/main.rs \
  'aib_cloud::customer::opened_of(out)' \
  'aib_cloud::customer::opened_of("") /* knife */' \
  --expect-fail an_opening_is_shown_in_the_host_s_words \
  -- cargo test -p aib-cloud-cli --locked an_opening_is_shown_in_the_host_s_words
# customer side: a login-link that gave no link writes its word on standard output
python3 -I scripts/knife.py crates/aib-cloud-cli/src/main.rs \
  '    request != Request::LoginLink || code == 0' \
  '    request != Request::LoginLink || code < 255 // knife' \
  --expect-fail a_login_link_is_the_only_thing_on_standard_output \
  -- cargo test -p aib-cloud-cli --locked a_login_link_is_the_only_thing_on_standard_output
# customer side: a request not offered yet is sent (Andy 11-10: hidden)
ck client.rs '    if !asked.offered() {' \
   '    if !asked.offered() && false { // knife' \
   a_request_the_host_would_refuse_is_not_sent
# customer side: a request the host would refuse is sent all the same
ck client.rs '        _ => parse_request(line).map(Line::Site),' \
   '        _ => parse_request(line).or(Ok(Request::Status)).map(Line::Site), // knife' \
   a_request_the_host_would_refuse_is_not_sent
# customer side: under a customer key a request not offered yet is sent (4.5 section 4)
ck client.rs '                request.offered()' \
   '                request.offered() || matches!(self, Line::Owner(_)) // knife' \
   a_customer_keys_lines_are_checked_by_their_own_grammar
# customer side: any first word is read as a site code (site status site3 would name a site)
ck client.rs '                && parse_request(&rest.join(" ")).is_ok() =>' \
   '                && (parse_request(&rest.join(" ")).is_ok() || true) => // knife' \
   a_code_in_front_of_a_request_names_the_site
# roster: a tenant id that is a site code is read all the same (4.5 r4 section 14.1 #3)
ck roster.rs '    if site_codes.contains(&tenant_id) {' \
   '    if site_codes.contains(&tenant_id) && site_codes.is_empty() { // knife' \
   a_tenant_id_that_is_a_site_code_refuses_the_whole_roster
# roster: a revoked entry key still gets its line
ck roster.rs '            if k.usage == KeyUse::Entry && !k.revoked {' \
   '            if k.usage == KeyUse::Entry && (!k.revoked || true) { // knife' \
   a_revoked_key_or_member_writes_no_line
# roster: a member who may not administer the site (an editor) gets a login to it (Andy 11-12)
ck roster.rs '        if !reaches || !m.roles.iter().any(|r| r.may(Operation::Administer)) {' \
   '        if !reaches || !m.roles.iter().any(|r| r.may(Operation::Edit)) { // knife' \
   only_an_owner_or_admin_gets_a_login_into_the_site
# roster: a member who may only read gets a login to the site
ck roster.rs '        if !reaches || !m.roles.iter().any(|r| r.may(Operation::Administer)) {' \
   '        if !reaches || !m.roles.iter().any(|r| r.may(Operation::ReadPrivate)) { // knife' \
   one_roster_writes_both_kinds_of_lines
# roster: a field nobody named is skipped
ck roster.rs '    if map.keys().any(|k| !named.contains(&k.as_str())) {' \
   '    if map.keys().any(|k| !named.contains(&k.as_str()) && k.is_empty()) { // knife' \
   strict_reading
# entry sudoers: a comma in the arguments goes out bare and starts a second command (c477a, #648 N2)
ck roster.rs "        if matches!(c, '\\\\' | ',' | ':' | '=') {" \
   '        if false { // knife' \
   the_sudoers_file_names_each_forced_command_and_nothing_wider
# entry sudoers: two rosters of one tenant are both written
ck roster.rs '        if rosters[..i].iter().any(|o| o.tenant_id == r.tenant_id) {' \
   '        if false && rosters[..i].iter().any(|o| o.tenant_id == r.tenant_id) { // knife' \
   the_sudoers_file_names_each_forced_command_and_nothing_wider
# entry sudoers: a member whose entry keys are all revoked keeps a rule
ck roster.rs '                .any(|k| k.usage == KeyUse::Entry && !k.revoked)' \
   '                .any(|k| k.usage == KeyUse::Entry) // knife' \
   the_sudoers_file_names_each_forced_command_and_nothing_wider
# share gate (plan 14.7 #11): outer spaces stay, so a padded owner is not the registered one
ck share.rs '    let s = raw.trim();' '    let s = raw; // knife' \
   an_address_is_trimmed_and_lower_cased_and_nothing_else
# share gate: case is kept, so Owner@ and owner@ are two people
ck share.rs '    Some(s.to_ascii_lowercase())' '    Some(String::from(s)) // knife' \
   an_address_is_trimmed_and_lower_cased_and_nothing_else
# share gate: an address that is not ASCII is taken
ck share.rs '    if s.is_empty() || s.len() > ADDRESS_MAX || !s.is_ascii() {' \
   '    if s.is_empty() || s.len() > ADDRESS_MAX || (!s.is_ascii() && s.is_empty()) { // knife' \
   what_is_not_one_address_is_none
# share gate: an address longer than the limit is taken
ck share.rs '    if s.is_empty() || s.len() > ADDRESS_MAX || !s.is_ascii() {' \
   '    if s.is_empty() || (s.len() > ADDRESS_MAX && s.is_empty()) || !s.is_ascii() { // knife' \
   what_is_not_one_address_is_none
# share gate: a space inside an address is taken
ck share.rs '    b.is_ascii_whitespace() || b.is_ascii_control()' \
   '    b.is_ascii_control() // knife' \
   what_is_not_one_address_is_none
# share gate: a control character inside an address is taken
ck share.rs '    b.is_ascii_whitespace() || b.is_ascii_control()' \
   '    b.is_ascii_whitespace() // knife' \
   what_is_not_one_address_is_none
# share gate: a second @ is taken
ck share.rs '    if parts.next().is_some() || local.is_empty() {' \
   '    if local.is_empty() && parts.next().is_none() { // knife' \
   what_is_not_one_address_is_none
# share gate: an empty name before the @ is taken
ck share.rs '    if parts.next().is_some() || local.is_empty() {' \
   '    if parts.next().is_some() || (local.is_empty() && local.len() > 1) { // knife' \
   what_is_not_one_address_is_none
# share gate: a domain without a dot is taken
ck share.rs "    if !domain.contains('.') || domain.split('.').any(str::is_empty) {" \
   "    if domain.split('.').any(str::is_empty) { // knife" \
   what_is_not_one_address_is_none
# share gate: a domain with an empty label is taken
ck share.rs "    if !domain.contains('.') || domain.split('.').any(str::is_empty) {" \
   "    if !domain.contains('.') { // knife" \
   what_is_not_one_address_is_none
# share gate: a roster with no address registered judges the owner anyway
ck share.rs '    if tenant.is_empty() {' '    if tenant.is_empty() && owners.is_empty() { // knife' \
   no_owner_or_nothing_registered_is_unknown_not_allowed
# share gate: a file whose owner the drive did not name passes
ck share.rs '        [] => return Gate::Unknown(Why::NoOwner),' '        [] => return Gate::Allowed, // knife' \
   no_owner_or_nothing_registered_is_unknown_not_allowed
# share gate: of two owners the first is judged
ck share.rs '        _ => return Gate::Refused(Why::ManyOwners),' '        _ => owners[0], // knife' \
   more_than_one_owner_or_a_bad_owner_is_refused
# share gate: an owner who is not the tenant's passes
ck share.rs '        Some(false) => return Gate::Refused(Why::OwnerNotRegistered),' '        Some(false) => {} // knife' \
   another_owner_is_refused
# share gate: an owner that is not an address passes
ck share.rs '        None => return Gate::Refused(Why::NotAnAddress),' '        None => {} // knife' \
   more_than_one_owner_or_a_bad_owner_is_refused
# share gate: a sharer that is not an address passes
ck share.rs '            return Gate::Refused(Why::NotAnAddress);' '            return Gate::Allowed; // knife' \
   a_sharer_who_is_not_the_tenants_is_refused
# share gate: a sharer who is not the tenant's passes
ck share.rs '        if !ok {' '        if !ok && s.is_empty() { // knife' \
   a_sharer_who_is_not_the_tenants_is_refused
# share gate: "cannot tell" passes as if allowed
ck share.rs '        matches!(self, Gate::Allowed)' '        !matches!(self, Gate::Refused(_)) // knife' \
   no_owner_or_nothing_registered_is_unknown_not_allowed
# share gate: an owner another tenant also registers passes (c477a M1)
ck share.rs '    if theirs(owner) || sharer.is_some_and(theirs) {' \
   '    if theirs(owner) && sharer.is_some_and(theirs) { // knife' \
   an_address_another_tenant_also_registers_is_refused
# share gate: a sharer another tenant also registers passes (c477a M1)
ck share.rs '    if theirs(owner) || sharer.is_some_and(theirs) {' \
   '    if theirs(owner) || (sharer.is_some_and(theirs) && owners.is_empty()) { // knife' \
   an_address_another_tenant_also_registers_is_refused
# entry sudoers: two tenants registering one address are both written (c477a M1)
ck roster.rs '            .any(|o| o.emails.iter().any(|e| r.emails.contains(e)))' \
   '            .any(|o| o.emails.iter().any(|e| r.emails.contains(e) && e.is_empty()))' \
   the_sudoers_file_names_each_forced_command_and_nothing_wider
# roster emails: an entry is kept as written, not as compared
ck roster.rs '            .and_then(crate::share::normalize)' '            .map(String::from) // knife' \
   the_registered_addresses_are_read_normalized_and_optional
# roster emails: the same address twice is read
ck roster.rs '        if out.contains(&a) {' '        if out.contains(&a) && a.is_empty() { // knife' \
   the_registered_addresses_are_read_normalized_and_optional
# H14: a wildcard rule is not told apart (the hand-written customer *)
ck verify.rs "                if text.contains('*') {" \
   '                if false { // knife' \
   h14_the_entry_sudoers_file_is_the_generated_one
# H14: any rule that starts like ours passes, whatever its arguments
ck verify.rs '                        .is_some_and(entry_rule_args)' \
   '                        .is_some_and(|r| entry_rule_args(r) || true) // knife' \
   h14_the_entry_sudoers_file_is_the_generated_one
# H14: any line starting with # is taken for a comment (#include, #<uid>: 33e28, #655 H1)
ck verify.rs '                    .filter(|l| !l.is_empty() && *l != SUDOERS_HEADER)' \
   "                    .filter(|l| !l.is_empty() && *l != SUDOERS_HEADER && !l.starts_with('#')) // knife" \
   h14_the_entry_sudoers_file_is_the_generated_one
# H14: a rule for the entry account in another sudoers file is not looked at (33e28, #655 M1)
ck verify.rs '                } else if elsewhere == Ok(true) {' \
   '                } else if false && elsewhere == Ok(true) { // knife' \
   h14_the_entry_sudoers_file_is_the_generated_one
# H14: only the first name of a spaced user list (root, #uid) is read
ck verify.rs "        if !(w.ends_with(',') || words.peek().is_some_and(|n| n.starts_with(','))) {" \
   '        if true { // knife' \
   h14_the_entry_sudoers_file_is_the_generated_one
# H14: a User_Alias that comes down to the entry account is not expanded (33e28, #655 r2 M1)
ck verify.rs '                        Who::Alias(a) if ours.contains(a) => granted = true,' \
   '                        Who::Alias(a) if ours.contains(a) => granted |= false, // knife' \
   h14_the_entry_sudoers_file_is_the_generated_one
# H14: a Defaults line bound to the entry account in another file is skipped (33e28, #655 r2 L2)
ck verify.rs "                Some(':') => user_list(&rest[1..])," \
   "                Some(':') => continue, // knife" \
   h14_the_entry_sudoers_file_is_the_generated_one
# H14: a line not read word for word (+netgroup, %:group, …) counts as clean (33e28 #655 r3 M1; f1fae 06:5x)
ck verify.rs '    } else if shape_ok {' \
   '    } else if true || shape_ok { // knife' \
   h14_the_entry_sudoers_file_is_the_generated_one
# H14: rules from sss / ldap (nsswitch sudoers:) are not looked for (33e28, #655 r3 L1)
ck verify.rs '                } else if seen.nss_files_only != Some(true) {' \
   '                } else if false && seen.nss_files_only != Some(true) { // knife' \
   h14_the_entry_sudoers_file_is_the_generated_one
# H14: owner and mode are not looked at
ck verify.rs '            (Some(om), _) if om != (0, 0, 0o440) => Outcome::Red("sudoers_mode"),' \
   '            (Some(om), _) if false && om != (0, 0, 0o440) => Outcome::Red("sudoers_mode"), // knife' \
   h14_the_entry_sudoers_file_is_the_generated_one
# site record: a record that names no tenant belongs to the tenant called like the site
ck site.rs '    rec.get("tenant_id")?.as_str().filter(|o| valid_code(o))' \
   '    rec.get("tenant_id").or_else(|| rec.get("site"))?.as_str().filter(|o| valid_code(o)) // knife' \
   tenant_and_disk_are_optional_but_typed
# provision: only the literal "/" is the root inside a shell line (ebb36, #619 stack L1)
ck provision.rs "    value.starts_with('/') && value.bytes().all(|c| c == b'/' || c == b'.')" \
   '    value == "/" // knife' \
   root_spellings_and_dashes_in_prose
# site verify (c477a's list): one judgement out per knife
ck verify.rs '    let on_every_card = !cards.is_empty()' \
   '    let on_every_card = cards.first().is_some_and(|n| n.contains("firewall=1")) || !cards.is_empty()' \
   review_segment_3
ck verify.rs '                Outcome::Red("ct_policy_open")' \
   '                Outcome::Green // knife' \
   review_segment_3
ck verify.rs '        (SiteState::Active, false) => Outcome::Red("ct_down"),' \
   '        (SiteState::Active, false) => Outcome::Green, // knife' \
   s01_only_an_active_site_has_to_be_running
ck verify.rs '        (u.thin_meta, 80, "thin_meta_full"),' \
   '        (u.thin_meta, 101, "thin_meta_full"), // knife' \
   h04_disk_thresholds
ck verify.rs '        result("H05", Outcome::Red("inactive"))' \
   '        result("H05", Outcome::Green) // knife' \
   review_segment_3
ck verify.rs '        if in_options {' \
   '        if in_options || true { // knife' \
   h08_h09_firewall_is_on
ck verify.rs '        return result("H04", Outcome::Unknown("unread"));' \
   '        return result("H04", Outcome::Green); // knife' \
   h04_disk_thresholds
# background site create (33e28, site-create plan r2 M1): a creating site whose worker is gone
ck create.rs '            Some(_) => match lock(holder, boot_id, liveness) {' \
   '            Some(_) => match lock(holder, &boot_id[..0], liveness) { // knife: boot id ignored' \
   another_boot_is_interrupted
ck create.rs '                Lock::TakeOver => Verdict::Interrupted,' \
   '                Lock::TakeOver => Verdict::Creating, // knife' \
   gone_worker_is_interrupted
ck create.rs '                Lock::Busy if liveness == Liveness::Unknown => Verdict::Unknown,' \
   '                Lock::Busy if liveness == Liveness::Unknown => Verdict::Interrupted, // knife' \
   unknown_changes_nothing
ck create.rs '            None if now.saturating_sub(created_at) > START_GRACE_SECS => Verdict::Interrupted,' \
   '            None if now.saturating_sub(created_at) > 0 => Verdict::Interrupted, // knife' \
   missing_holder_waits_out_the_start_grace
ck create.rs '    now_state == CreateState::Creating && judged.0 == now_record.0 && judged.1 == now_record.1' \
   '    now_state == CreateState::Creating && judged.1 == now_record.1 // knife: holder not compared' \
   mark_only_the_same_attempt
# create: a late worker takes a record already marked unfinished / held by another (c477a L1)
ck create.rs '    now_state == CreateState::Creating && now_record.0.is_none() && now_record.1 == my_created_at' \
   '    (now_state == CreateState::Creating || true) && now_record.0.is_none() && now_record.1 == my_created_at // knife: state not checked' \
   a_late_worker_takes_only_its_own_unheld_attempt
ck create.rs '    now_state == CreateState::Creating && now_record.0.is_none() && now_record.1 == my_created_at' \
   '    now_state == CreateState::Creating && now_record.0.is_none() && (now_record.1 == my_created_at || true) // knife: attempt not compared' \
   a_late_worker_takes_only_its_own_unheld_attempt
ck create.rs '    now_state == CreateState::Creating && now_record.0.is_none() && now_record.1 == my_created_at' \
   '    now_state == CreateState::Creating && now_record.1 == my_created_at // knife: holder not checked' \
   a_late_worker_takes_only_its_own_unheld_attempt
# create: Unknown for too long is never told to us (c477a liveness N1)
ck create.rs '    v == Verdict::Unknown && now.saturating_sub(created_at) > UNKNOWN_ALERT_SECS' \
   '    v == Verdict::Unknown && now.saturating_sub(created_at) > UNKNOWN_ALERT_SECS * 100 // knife' \
   unknown_for_too_long_tells_us
ck create.rs '    v == Verdict::Unknown && now.saturating_sub(created_at) > UNKNOWN_ALERT_SECS' \
   '    v != Verdict::Ready && now.saturating_sub(created_at) > UNKNOWN_ALERT_SECS // knife' \
   only_unknown_alerts
# ---- B1 (customer opens a site) and site removal: the pure rules (4542e 11-11)
# opening a site: a probe that could not tell is read as not done
ck sitecreate.rs '            (Probe::Unknown, _) => return Next::Stop("probe_unknown", *step),' \
   '            (Probe::Unknown, _) if false => return Next::Stop("probe_unknown", *step), // knife' \
   a_probe_that_could_not_tell_stops_the_work
# opening a site: a later step done before an earlier one goes unnoticed
ck sitecreate.rs '            (Probe::Done, Some(_)) => return Next::Stop("steps_out_of_order", *step),' \
   '            (Probe::Done, Some(_)) if false => return Next::Stop("steps_out_of_order", *step), // knife' \
   a_later_step_done_before_an_earlier_one_stops_the_work
# opening a site: a failed licence step removes the site (and loses the activations)
ck sitecreate.rs '    if step < CreateStep::Licenses {' \
   '    if step <= CreateStep::Licenses { // knife' \
   a_failure_is_undone_only_until_something_outside_the_host_is_used_up
# opening a site: a blank site is asked for licences it does not have
ck sitecreate.rs '        !(blank && self == CreateStep::Licenses)' \
   '        !(blank && false && self == CreateStep::Licenses) // knife' \
   a_blank_site_passes_over_the_licenses
# numbers: a removed site's container number is given out again (ebb36)
ck sitecreate.rs '        .chain(removed_cts)' \
   '        .chain(removed_cts.iter().take(0)) // knife' \
   the_numbers_of_removed_sites_are_never_given_out_again
# numbers: a removed site's address is given out again (ebb36)
ck sitecreate.rs '.chain(removed_ips)' \
   '.chain(removed_ips.iter().take(0)) /* knife */' \
   the_numbers_of_removed_sites_are_never_given_out_again
# licences: activated although the plugin's own call failed
ck sitedefaults.rs '        (true, License::Active) => Ok(()),' \
   '        (_, License::Active) => Ok(()), // knife' \
   activated_means_the_call_and_the_vendor_both_say_so
# licences: no answer from the vendor counts as activated
ck sitedefaults.rs '        _ => License::Unknown,' \
   '        _ => License::Active, // knife' \
   no_answer_or_an_unknown_word_is_unknown_never_active
# licences: activation is tried with none left
ck sitedefaults.rs '            Some(0) => License::Quota,' \
   '            Some(0) => License::Inactive, // knife' \
   a_status_vendor_is_read_by_its_own_words
# licences: activated without the vendor having this site's record
ck sitedefaults.rs '            Some(true) => return License::Active,' \
   '            Some(true) => return License::Active, Some(false) if true => return License::Active, // knife' \
   an_activation_vendor_needs_its_own_record_of_this_site
# licences: a number the vendor was not asked about is activated again, one more activation used up (c477a, #659 M1)
ck sitedefaults.rs '            None => return License::Unknown,' \
   '            None => {} // knife' \
   a_number_the_vendor_was_not_asked_about_is_unknown_not_inactive
# licences: an activation is sent when the state is not known
ck sitedefaults.rs '        License::Inactive => LicenseStep::Activate,' \
   '        License::Inactive | License::Unknown => LicenseStep::Activate, // knife' \
   only_a_known_inactive_license_is_activated
# administrators: a taken login name is given again
ck sitedefaults.rs '    if free(base) {' \
   '    if free(base) || true { // knife' \
   a_taken_login_name_gets_a_number
# title: www counts as the first label
ck sitedefaults.rs '    let rest = host.strip_prefix("www.").unwrap_or(host);' \
   '    let rest = host; // knife' \
   the_title_is_the_first_label_and_www_does_not_count
# removing a site: the code alone is enough
ck siteremove.rs '        && record_main_address == typed_address' \
   '        && (record_main_address == typed_address || true) // knife' \
   the_site_and_its_address_must_both_be_named_exactly
