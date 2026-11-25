#!/usr/bin/env bash
# aib-knives: job=cloud-host-knives
# The "aib-cloud-host command knives" step of aibd.yml's cloud-host-knives job, moved here word for word (519d0 11-15
# 08:08, f1fae BLOCKER-aibd-yml-500kb: GitHub creates no run for a workflow file past 500 KB,
# and these two steps were about 255 KB of it). aib-xtask knife-anchors reads crates/*/knives/*.sh,
# so every knife here is still checked. Run from the repo root; -eo pipefail is what
# GitHub's `shell: bash` gave the step when it was inline.
set -eo pipefail
set -eu
hk() { python3 -I scripts/knife.py "crates/aib-cloud-host/src/$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-cloud-host --locked "$4"; }
hk sitescancmd.rs '        .map(|l| l.as_str().map(str::to_owned))' \
   '        .map(|l| Some(l.as_str().unwrap_or("").to_owned())) // knife' \
   the_expected_administrators_come_from_the_opening_record
# E1 customer site create: an opening is shown for a request other than status
hk customercmd.rs '            if request == Request::Status && in_scope {' \
   '            if in_scope { // knife' \
   a_site_still_opening_answers_how_far_it_got
# E1: a key for some of the sites sees an opening outside them
hk customercmd.rs '            let in_scope = scope.is_none_or(|list| list.contains(&site));' \
   '            let in_scope = scope.is_none_or(|_| true); // knife' \
   a_site_still_opening_answers_how_far_it_got
# E1: someone else's opening of the domain hands its code to whoever asks
hk sitecreaterun.rs '            let own = (owner.as_deref() == Some(tenant)).then_some(code);' \
   '            let own = (owner.as_deref() == Some(tenant) || true).then_some(code); // knife' \
   the_same_domain_while_it_is_opening_is_refused
# E1: an ask whose run never wrote itself down holds the domain for ever
hk sitecreaterun.rs '            if now.saturating_sub(at) < START_GRACE_S {' \
   '            if now.saturating_sub(at) < START_GRACE_S || true { // knife' \
   the_same_domain_while_it_is_opening_is_refused
# E1: an opening record of the domain does not hold it
hk sitecreaterun.rs '        .find(|r| named(first_host(r)))' \
   '        .find(|r| named(first_host(r)) && false) // knife' \
   the_same_domain_while_it_is_opening_is_refused
# E1: a full plan is told as our trouble, not as the customer's quota
hk sitecreaterun.rs '        "quota_exceeded" => Some("quota_exceeded"),' \
   '        "quota_exceeded" => None, // knife' \
   a_plan_that_stops_is_answered_with_a_fixed_word
# E1: a plan made for another customer is run
hk sitecreaterun.rs '    if plan["quota"]["tenant_id"].as_str() != Some(tenant)' \
   '    if plan["quota"]["tenant_id"].as_str() != Some(tenant) && false // knife' \
   a_plan_that_stops_is_answered_with_a_fixed_word
# E1: another customer follows an opening by its code
hk sitecreaterun.rs '        || aib_cloud::site::record_tenant(&ask) != Some(tenant)' \
   '        || (aib_cloud::site::record_tenant(&ask) != Some(tenant) && false) // knife' \
   progress_is_the_customer_s_own_only
# HUMAN-PASTE-4542e-1111 21: an abandoned opening does not say so, or any stop says so
hk sitecreaterun.rs '    if gave_up && state == "stopped" {' \
   '    if gave_up && state == "knife" {' \
   an_abandoned_ask_is_stopped_kept_and_its_code_given
hk sitecreaterun.rs '    if gave_up && state == "stopped" {' \
   '    if (gave_up || true) && state == "stopped" { // knife' \
   an_abandoned_ask_is_stopped_kept_and_its_code_given
# E1: an opening that stopped on a failure is run again by itself
hk sitecreaterun.rs '        (Some("waiting" | "running"), _) => {}' \
   '        (Some(_), _) => {} // knife' \
   a_failure_stops_where_it_was_and_only_the_step_is_shown
# E1: a run still going is run a second time
hk sitecreaterun.rs '        (Some("running"), "opening") => {' \
   '        (Some("running"), "never") => { // knife' \
   a_run_still_going_is_not_run_twice
# E1: a run cut off after the clone clones again
hk sitecreaterun.rs '    if !opening_path.exists() && !opened_path.exists() {' \
   '    if !opened_path.exists() { // knife' \
   a_run_cut_off_resumes_from_the_opening_record
# E1: a host lock held by another operation stops the opening at once
hk sitecreaterun.rs '            Err(e) if e.starts_with("host is locked by operation") => {' \
   '            Err(e) if e.starts_with("host is locked by operation") && false => { // knife' \
   a_busy_host_lock_is_waited_for_then_given_up
# E1: the code a customer was given is swapped for a taken one
hk sitecreatecmd.rs '        if aib_cloud::site::valid_code(code) && !taken.iter().any(|t| t == code) {' \
   '        if aib_cloud::site::valid_code(code) && (!taken.iter().any(|t| t == code) || true) { // knife' \
   a_customer_s_opening_is_cloned_under_the_code_they_were_given
# E1: the opening record does not name the customer
hk sitecreatecmd.rs '    if let Some(tenant) = &input.tenant {' \
   '    if let Some(tenant) = input.tenant.as_ref().filter(|_| false) { // knife' \
   a_customer_s_opening_is_cloned_under_the_code_they_were_given
# E1: the site's record does not name the customer, so their key never sees it
hk sitelive.rs '    if let Some(tenant) = aib_cloud::site::record_tenant(opening) {' \
   '    if let Some(tenant) = aib_cloud::site::record_tenant(opening).filter(|_| false) { // knife' \
   the_customer_of_the_opening_owns_the_site
# E1: a customer key may not approve the opening it asked for
# (the list is one entry a line since login-link joined it, 4542e 11-12)
hk opstore.rs '    "site.create",' '    "site.create-not", // knife' \
   a_customer_s_opening_is_cloned_under_the_code_they_were_given
# E1 (33e28 M1): a plan whose quota was not judged to fit starts an opening
hk sitecreaterun.rs '    if plan["quota"]["verdict"] != "fits" {' \
   '    if plan["quota"]["verdict"] != "fits" && false { // knife' \
   a_quota_not_judged_starts_nothing
# E1 (33e28 M2): the customer's ask for one of the platform's own domains is planned
hk sitecreaterun.rs '    if aib_cloud::sitecreate::is_platform(domain, &ours) {' \
   '    if aib_cloud::sitecreate::is_platform(domain, &ours) && false { // knife' \
   the_platform_s_own_domains_are_not_started
# E1 (33e28 M2): a list not read whole is read as no platform domains at all
hk sitecreaterun.rs '        .and_then(aib_cloud::sitecreate::platform_domains)' \
   '        .and_then(|t| aib_cloud::sitecreate::platform_domains(t).or(Some(Vec::new()))) // knife' \
   the_platform_s_own_domains_are_not_started
# site create --plan (33e28 M2): one of the platform's own domains is planned
hk sitecreatecmd.rs '= if taken.is_some() || platform == Some(true) {' \
   '= if taken.is_some() { // knife' \
   the_platform_s_own_domains_are_not_opened
# site create --plan (33e28 M2): a list not read whole stops nothing
hk sitecreatecmd.rs '    } else if platform.is_none() {' \
   '    } else if platform.is_none() && false {' \
   the_platform_s_own_domains_are_not_opened
# E1 audit #5: an opening a person carried on to the end (--continue) still reads as stopped
hk sitecreaterun.rs '    if opened {' \
   '    if opened && false { // knife' \
   a_stop_carried_on_by_a_person_reads_as_open
# E1 (33e28 N2): a unit started after its ask stopped waiting takes the ask up
hk sitecreaterun.rs '        (Some("waiting"), "stopped") => {' \
   '        (Some("waiting"), "never") => { // knife' \
   a_run_started_too_late_does_not_take_its_ask
# B5 DNS (4542e): a call off the list of five goes out
hk cfdns.rs '        if !sitedns::allowed_with(call.method(), &call.path(), body, self.entry.as_deref()) {' \
   '        if !sitedns::allowed_with(call.method(), &call.path(), body, self.entry.as_deref()) && false { // knife' \
   a_call_off_the_list_is_refused_and_written_down
# B5 DNS: a call made leaves no ledger line
hk cfdns.rs '        self.ledger_line(&mut file, call, json!(http))?;' \
   '        let _ = (&mut file, http); // knife' \
   a_free_name_gets_one_proxied_marked_record_read_back
# B5 DNS: the opening record does not say what is being added before the add
hk cfdns.rs '        crate::sitecreatecmd::write_record(&paths.creating, &spec.code, record)?;' \
   '        let _ = &paths.creating; // knife' \
   an_add_that_does_not_read_back_is_not_done_and_was_journaled
# B5 DNS: an add that does not read back counts as done
hk cfdns.rs '        if read_back != DnsPlan::Done {' \
   '        if read_back != DnsPlan::Done && false { // knife' \
   an_add_that_does_not_read_back_is_not_done_and_was_journaled
# E6 S5 (4542e): the host's entry name is not handed to the call check
hk cfdns.rs '    let mut cf = Cf::new(runner, creds, &paths.cf_ledger, &spec.code).with_entry(entry.clone());' \
   '    let mut cf = Cf::new(runner, creds, &paths.cf_ledger, &spec.code).with_entry(None); // knife' \
   a_host_with_an_entry_name_adds_cnames_to_it
# E6 S5: removing a site leaves its marked CNAME behind
python3 -I scripts/knife.py crates/aib-cloud/src/siteremove.rs \
  'pub const DNS_ADDRESS_TYPES: [&str; 3] = ["A", "AAAA", "CNAME"];' \
  'pub const DNS_ADDRESS_TYPES: [&str; 3] = ["A", "AAAA", "AAAA"]; // knife' \
  --expect-fail a_sites_marked_cname_is_removed_and_no_other \
  -- cargo test -p aib-cloud-host --locked a_sites_marked_cname_is_removed_and_no_other
# B5 DNS: a roster file that is another customer's is used
hk cfdns.rs '    if roster.tenant_id != tenant {' '    if roster.tenant_id != tenant && false { // knife' \
   a_customer_without_accounts_cannot_be_told
# B5: --continue passes over the DNS step
hk sitecreatecmd.rs '            CreateStep::Dns => crate::cfdns::dns_step(runner, paths, &spec, &mut record),' \
   '            CreateStep::Dns => crate::cfdns::dns_step(runner, paths, &spec, &mut record).or(Ok(())), // knife' \
   a_dns_step_that_cannot_ask_keeps_the_site_and_asks_nothing
# B5: the plan marks the wrong step unknown when the entry cannot be read
hk sitecreatecmd.rs '        if let Some(i) = at(CreateStep::Edge) {' \
   '        if let Some(i) = at(CreateStep::Health) { // knife' \
   an_unreadable_entry_stops_at_the_entry_step
# abandon (4542e): a name outside the customer's zone is removed under it
hk cfdns.rs '    if !names.iter().all(|n| sitedns::in_zone(n, &zone.name)) {' \
   '    if !names.iter().all(|n| sitedns::in_zone(n, &zone.name)) && false { // knife' \
   a_name_outside_the_zone_removes_nothing
# abandon: a delete answered but not done counts as removed
hk cfdns.rs '    if left.is_empty() {' \
   '    if left.is_empty() || true { // knife' \
   a_delete_that_does_not_take_is_not_removed
# abandon: a snapshot's hostname is taken for the container's
hk siteabandon.rs "        .take_while(|l| !l.starts_with('['))" \
   '        .take_while(|_| true) // knife' \
   the_hostname_is_the_main_sections
# abandon: the step destroys the container without looking at its name again
hk siteabandon.rs '        Some(Some(c)) if hostname(&c) == code => {}' \
   '        Some(Some(c)) if hostname(&c) == code || true => {} // knife' \
   the_container_is_looked_at_again_right_before_it_goes
# abandon: a container still there after the destroy counts as gone
hk siteabandon.rs '        (Ok(_), _) => Err(format!("CT {ct} still answers after pct destroy")),' \
   '        (Ok(_), _) => Ok(()), // knife' \
   a_container_that_is_still_there_after_destroy_stops
# abandon: the firewall file is left
hk siteabandon.rs '    match fs::remove_file(&fw) {' \
   '    match Ok::<(), std::io::Error>(()) { // knife' \
   applied_everything_goes_and_the_record_moves_to_the_abandoned
# abandon: the site's passwords are left
hk siteabandon.rs '            AbandonStep::Secrets => crate::siteidentity::drop_admin_secrets(&paths.secrets, code),' \
   '            AbandonStep::Secrets => Ok(()), // knife' \
   applied_everything_goes_and_the_record_moves_to_the_abandoned
# abandon: the record is removed without being kept among the abandoned
hk siteabandon.rs '    write_record(&to, code, record)?;' \
   '    let _ = (&to, record); // knife' \
   applied_everything_goes_and_the_record_moves_to_the_abandoned
# abandon: a step is done without the record saying so
hk siteabandon.rs $'            list.push(json!(step.as_str()));\n        }\n        write_record(&paths.creating' \
   $'            let _ = (list, step); // knife\n        }\n        write_record(&paths.creating' \
   a_stop_half_way_is_written_and_a_new_plan_carries_on
# abandon: DNS removed once counts as never touched
hk siteabandon.rs '    touched && rec["abandon"]["dns_removed"] != true' \
   '    touched // knife' \
   dns_already_removed_is_done
# abandon: an opening that was adding its DNS has none to remove
hk siteabandon.rs '    let touched = dns.get("adding").is_some() || dns.get("done").is_some();' \
   '    let touched = dns.get("done").is_some(); // knife' \
   a_half_opening_plans_the_container_first_and_every_step
# abandon: an opened site's record is not a site
hk siteabandon.rs '        (Some(a), Some(b)) => Some(a || b),' \
   '        (Some(a), Some(_)) => Some(a), // knife' \
   a_site_is_never_abandoned
# abandon: the opening of another domain is abandoned
hk siteabandon.rs '            && hosts(r).first().map(String::as_str) == Some(domain)' \
   '            && !hosts(r).is_empty() // knife' \
   the_opening_must_be_of_this_domain
# abandon: an unfinished operation on the domain is gone round
hk siteabandon.rs '        _ if seen.unfinished.as_ref().is_some_and(|u| !u.is_empty()) => Some(UNFINISHED_OPERATION),' \
   '        _ if seen.unfinished.as_ref().is_some_and(|u| !u.is_empty()) && false => Some(UNFINISHED_OPERATION), // knife' \
   what_cannot_be_read_stops_everything
# abandon: a journal not read counts as nothing unfinished
hk siteabandon.rs '        _ if seen.unfinished.is_none() => Some(JOURNAL_UNREADABLE),' \
   '        _ if seen.unfinished.is_none() && false => Some(JOURNAL_UNREADABLE), // knife' \
   what_cannot_be_read_stops_everything
# abandon: a plan the host no longer matches is applied
hk siteabandon.rs '    if doc.get("plan_sha256").and_then(Value::as_str) != Some(g.sha.as_str()) || doc != g.plan {' \
   '    if g.sha.is_empty() && g.plan.is_null() { // knife' \
   a_host_that_changed_after_the_plan_is_left_alone
# abandon: a plan that says stop is applied
hk siteabandon.rs '    if doc["next"] != "run" {' \
   '    if doc["next"] != "run" && false { // knife' \
   a_container_under_another_name_or_of_a_site_is_not_touched
# abandon: steps found done are done again
hk siteabandon.rs '        .filter(|(_, p)| *p == Probe::NotDone)' \
   '        .filter(|(_, p)| *p == Probe::NotDone || true) // knife' \
   applied_everything_goes_and_the_record_moves_to_the_abandoned
# abandon: an abandoned opening's numbers are given again
hk sitecreatecmd.rs '    out.extend(read_records(&crate::siteabandon::abandoned_dir(creating))?);' \
   '    out.extend(Vec::<Value>::new()); let _ = creating; // knife' \
   an_abandoned_openings_numbers_are_never_given_again
# abandon: an abandoned opening's code is given again
hk sitecreaterun.rs '        &abandoned,' \
   '        &abandoned.join("none"), // knife' \
   an_abandoned_openings_code_stays_given
# abandon L1 (c477a): the customer's own opening still going is taken down under it
hk siteabandon.rs '        _ if seen.customer_ask == Some(Some("opening")) => Some(CUSTOMER_RUN_GOING),' \
   '        _ if seen.customer_ask == Some(Some("opening")) => None, // knife' \
   a_customer_opening_still_going_is_not_abandoned
# abandon L1: an ask not read counts as none
hk siteabandon.rs '        _ if seen.customer_ask.is_none() => Some(siteabandon::PROBE_UNKNOWN),' \
   '        _ if seen.customer_ask.is_none() => None, // knife' \
   an_ask_that_cannot_be_read_stops
# abandon L1: the look at the ask answers "none" whatever it found
hk siteabandon.rs '        customer_ask: crate::sitecreaterun::ask_now(asks, code, crate::probe::unix_now()).ok(),' \
   '        customer_ask: crate::sitecreaterun::ask_now(asks, code, crate::probe::unix_now()).ok().map(|_| None), // knife' \
   a_customer_opening_still_going_is_not_abandoned
# abandon L1: an ask that cannot be read is told as none
hk sitecreaterun.rs '    let ask: Value = serde_json::from_str(&text).map_err(|_| ())?;' \
   '    let Ok(ask) = serde_json::from_str::<Value>(&text) else { return Ok(None) }; // knife' \
   an_ask_is_seen_opening_or_not_told
# abandon L1: the abandon leaves the customer's ask as it was (the call stays, on no ask)
hk siteabandon.rs '    crate::sitecreaterun::stop_ask_abandoned(asks, code, crate::probe::unix_now())?;' \
   '    crate::sitecreaterun::stop_ask_abandoned(asks, "s-zzzzzz", crate::probe::unix_now())?; // knife' \
   the_customers_ask_is_stopped_as_abandoned_and_kept
# abandon L1: the ask is not marked stopped
hk sitecreaterun.rs '    obj.insert("state".to_owned(), json!("stopped"));' \
   '    obj.insert("state".to_owned(), json!("running")); // knife' \
   an_abandoned_ask_is_stopped_kept_and_its_code_given
# abandon L1: the ask does not say why
hk sitecreaterun.rs '    obj.insert("why".to_owned(), json!("abandoned"));' \
   '    obj.insert("why".to_owned(), json!("start_late")); // knife' \
   an_abandoned_ask_is_stopped_kept_and_its_code_given
# abandon L1: the ask is removed, and its code with it
hk sitecreaterun.rs $'    // kept, not removed: the code stays given\n    rewrite_ask(&places.asked, code, &ask)\n' \
   $'    // kept, not removed: the code stays given\n    rewrite_ask(&places.asked, code, &ask).and_then(|()| fs::remove_file(places.asked.join(format!("{code}.json"))).map_err(|e| e.to_string())) // knife\n' \
   an_abandoned_ask_is_stopped_kept_and_its_code_given
# E1 e1a 11-12: the limits step gives net0 a locally administered address
hk sitecreatecmd.rs '    if net0_hwaddr(&text).is_some_and(|mac| !mac_is_local(&mac)) {' \
   '    if net0_hwaddr(&text).is_some_and(|mac| !mac_is_local(&mac)) && false { // knife' \
   the_limits_step_gives_net0_a_local_address
hk sitecreatecmd.rs '    if net0_hwaddr(&shown).is_some_and(|mac| !mac_is_local(&mac)) {' \
   '    if net0_hwaddr(&shown).is_some_and(|mac| !mac_is_local(&mac)) && false { // knife' \
   a_platform_address_shown_after_the_limits_is_refused
hk sitecreatecmd.rs '    let first = (raw[0] & 0xFC) | 0x02;' \
   '    let first = raw[0] | 0x02; // knife' \
   a_local_hardware_address_is_made_of_any_six_bytes
hk sitecreatecmd.rs '            main = false;' \
   '            main = !line.is_empty(); // knife' \
   only_the_main_net0_hwaddr_is_replaced
hk sitecreatecmd.rs '                            format!("{lead}hwaddr={mac}")' \
   '                            format!("{}hwaddr={mac}", lead.trim()) // knife' \
   only_the_main_net0_hwaddr_is_replaced
hk sitecreatecmd.rs "    if !text.ends_with('\\n') {" \
   '    if false { // knife' \
   only_the_main_net0_hwaddr_is_replaced
# E1 e1a 11-12: the installer's administrator lets go of the first administrator's address
hk siteidentity.rs '    if !admins.iter().any(|(_, l)| l == SETUP_ADMIN) && user_id(runner, spec, SETUP_ADMIN).is_some()' \
   '    if !admins.iter().any(|(_, l)| l == SETUP_ADMIN) && user_id(runner, spec, SETUP_ADMIN).is_some() && false // knife' \
   the_installer_s_address_is_let_go_before_the_first_administrator
hk siteidentity.rs '        let parked = format!("{SETUP_ADMIN}@{}.invalid", spec.code);' \
   '        let parked = format!("{SETUP_ADMIN}@{}.example", spec.code); // knife' \
   the_installer_s_address_is_let_go_before_the_first_administrator
hk siteidentity.rs '            .map_err(|e| format!("identity_unchecked: {SETUP_ADMIN} address: {e}"))?;' \
   '            .unwrap_or_default(); // knife' \
   the_installer_s_address_is_let_go_before_the_first_administrator
hk siteidentity.rs '                &["user", "update", SETUP_ADMIN, &flag, "--skip-email"],' \
   '                &["user", "update", SETUP_ADMIN, &flag], // knife' \
   the_installer_s_address_is_let_go_before_the_first_administrator
hk siteidentity.rs '        if back.trim() != parked {' \
   '        if back.trim() != parked && false { // knife' \
   the_installer_s_address_is_let_go_before_the_first_administrator
# E1 audit 11-12 #1/#9: the template package goes in before the installer, out after
hk siteidentity.rs '            .run(&in_ct(spec.ct, &["truncate", "-s", "0", PACKAGE_LIST]))' \
   '            .run(&in_ct(spec.ct, &["rm", "-f", PACKAGE_LIST])) // knife' \
   a_blank_install_drops_the_template_package_first
hk siteidentity.rs '    if !paths.package.join("plugins").is_dir() || !paths.package_conf.is_file() {' \
   '    if (!paths.package.join("plugins").is_dir() || !paths.package_conf.is_file()) && false { // knife' \
   the_template_package_goes_in_before_the_installer_and_out_after
hk siteidentity.rs '    if paths.keys.is_file() {' \
   '    if paths.keys.is_file() && false { // knife' \
   the_template_package_goes_in_before_the_installer_and_out_after
hk siteidentity.rs '    let dropped = runner.run(&owned(&["rm", "-f", &archive]));' \
   '    let dropped = runner.run(&owned(&["true", &archive])); // knife' \
   the_template_package_goes_in_before_the_installer_and_out_after
hk siteidentity.rs '    let cleaned = runner.run(&in_ct(spec.ct, &["sh", "-c", PACKAGE_CLEANUP]));' \
   '    let cleaned = runner.run(&in_ct(spec.ct, &["sh", "-c", "true"])); let _ = PACKAGE_CLEANUP; // knife' \
   the_template_package_goes_in_before_the_installer_and_out_after
# E1 audit 11-12 #3: health asks again until the certificate is there
hk sitelive.rs '    for attempt in 0..HEALTH_TRIES {' \
   '    for attempt in 0..1 { let _ = HEALTH_TRIES; // knife' \
   health_asks_again_until_the_certificate_is_there
hk sitelive.rs '                last = None;' \
   '                last = Some(String::new()); // knife' \
   health_asks_again_until_the_certificate_is_there
hk sitelive.rs '                .run(&owned(&["sleep", HEALTH_PAUSE_SECS]))' \
   '                .run(&owned(&["true", HEALTH_PAUSE_SECS])) // knife' \
   health_asks_again_until_the_certificate_is_there
# E1 audit 11-12 #6: a step that goes through clears kept
hk sitecreatecmd.rs '        fields.remove("kept");' \
   '        fields.remove("kept_"); // knife' \
   a_step_that_goes_through_clears_kept
# E1 audit 11-12 #8: a new opening goes into the current firewall group (v2)
hk sitecreatecmd.rs '    .and_then(|s| s.with_fw_group(group.unwrap_or(NEW_SITE_FW_GROUP)))' \
   '    .and_then(|s| s.with_fw_group(group.unwrap_or(aib_cloud::site::FW_GROUP))) // knife' \
   a_new_opening_uses_the_current_firewall_group
hk sitecreatecmd.rs '    let group = record.get("fw_group").and_then(Value::as_str);' \
   '    let group = record.get("fw_group_").and_then(Value::as_str); // knife' \
   a_new_opening_uses_the_current_firewall_group
# site login-link (Andy 11-11 23:1x: a one-time link logs straight in, not a password reset)
# LL1 the token's letters are folded onto the first ones past the even cut
hk loginlink.rs '        .filter(|b| usize::from(**b) < even)' \
   '        .filter(|b| usize::from(**b) < even || true) // knife' \
   tokens_are_even_and_43_long
# LL2 the site is given the token itself instead of its hash
hk loginlink.rs '    opstore::sha256_hex(token.as_bytes())' \
   '    opstore::sha256_hex(token.as_bytes()).map(|_| token.to_owned()) // knife' \
   the_token_is_never_in_an_argument_and_the_site_gets_only_its_hash
# LL3 the token goes into an argument list
hk loginlink.rs '        .run_input(&wp(spec, &["eval", &program]), hash.as_bytes())' \
   '        .run_input(&wp(spec, &["eval", &program, &token]), hash.as_bytes()) // knife' \
   the_token_is_never_in_an_argument_and_the_site_gets_only_its_hash
# LL4 a login that is not an administrator gets a link (the answer is changed, not the
# arms: an arm made unreachable fails to compile under -D warnings and bites nothing)
hk loginlink.rs '    match said.trim() {' \
   '    match said.trim().replace("not_admin", "admin").as_str() { // knife' \
   a_login_that_is_not_an_administrator_is_refused_with_a_fixed_code
# LL5 a site answering for another address gets a link (before)
hk loginlink.rs "    if home.trim().trim_end_matches('/') != want {" \
   "    if home.trim().trim_end_matches('/') != want && false { // knife" \
   a_site_answering_for_another_address_gets_no_link
# LL6 a site answering for another address gets a link (the mint's own answer)
hk loginlink.rs "    if home.trim_end_matches('/') != format!(\"https://{domain}\") {" \
   "    if home.trim_end_matches('/') != format!(\"https://{domain}\") && false { // knife" \
   a_site_answering_for_another_address_gets_no_link
# LL7 another file of the plugin's name is taken for ours
hk loginlink.rs '        Some(sha) if sha == MU_PLUGIN_SHA256 => Ok(MuState::Same),' \
   '        Some(sha) if sha == MU_PLUGIN_SHA256 || !sha.is_empty() => Ok(MuState::Same), // knife' \
   a_different_mu_plugin_is_left_alone
# LL8 a plugin that reads back otherwise is taken as written
hk loginlink.rs '    if back.split_whitespace().next() != Some(MU_PLUGIN_SHA256) {' \
   '    if back.split_whitespace().next() != Some(MU_PLUGIN_SHA256) && false { // knife' \
   a_mu_plugin_that_reads_back_otherwise_stops
# LL9 the key lives longer than ten minutes
hk loginlink.rs 'set_transient($k, $u->ID, {LINK_SECONDS});' \
   'set_transient($k, $u->ID, 6000); /* {LINK_SECONDS} knife */' \
   the_mint_program_checks_the_administrator_and_keeps_the_key_ten_minutes
# LL10 the mint does not ask again for an administrator
hk loginlink.rs "if (!\$u || !user_can(\$u, 'manage_options')) {{ echo" \
   'if (!$u) {{ /* knife */ echo' \
   the_mint_program_checks_the_administrator_and_keeps_the_key_ten_minutes
# LL11 the mint says issued without reading the key back
hk loginlink.rs 'if ((int) get_transient($k) !== (int) $u->ID) {{' \
   'if (false) {{ /* knife */' \
   the_mint_program_checks_the_administrator_and_keeps_the_key_ten_minutes
# LL12 the plugin goes on without having deleted the key itself (a link works more than once; c477a N1 / ebb36 L2)
hk ez2-onetime-login.php '    if (!delete_transient($key)) { return; }' \
   '    delete_transient($key); /* knife: goes on whoever deleted it */' \
   the_mu_plugin_logs_in_only_an_administrator_once
# LL13 the plugin logs in any user, not only an administrator
hk ez2-onetime-login.php "    if (!\$u || !user_can(\$u, 'manage_options')) { return; }" \
   '    if (!$u) { return; } /* knife */' \
   the_mu_plugin_logs_in_only_an_administrator_once
# LL14 the plugin in the repository is not the reviewed one
hk ez2-onetime-login.php '    nocache_headers();' \
   '    nocache_headers(); // knife' \
   the_mu_plugin_is_the_reviewed_one
# LL15 the identity leaves the plugin out
hk siteidentity.rs '    let plugin = crate::loginlink::ensure_mu_plugin(runner, spec)' \
   '    let plugin = Ok::<&str, String>("written") // knife' \
   the_identity_makes_the_site_its_own
# LL16 an opened site's link is for another administrator than the first
hk sitecreatecmd.rs '        .and_then(|a| a.first())' \
   '        .and_then(|a| a.last()) // knife' \
   the_opened_sites_link_is_for_the_first_administrator
# LL17 (Andy 11-12 04:3x) a customer's link is for the host's first administrator, not the opening's
hk loginlink.rs '            crate::sitecreatecmd::link_login(&rec)' \
   '            crate::sitecreatecmd::link_login(&rec).and(first_admin(&paths.site_admins)) // knife' \
   a_customer_s_link_is_for_their_site_s_first_administrator
# LL18 a customer's link is journaled as typed on the host
hk loginlink.rs '        opstore::BY_CUSTOMER_KEY,' \
   '        opstore::BY_LOCAL_ROOT, // knife' \
   a_customer_s_link_is_for_their_site_s_first_administrator
# LL19 a customer key may not approve a link
hk opstore.rs '    "site.login-link",' '    "site.login-link-not", // knife' \
   a_customer_s_link_is_for_their_site_s_first_administrator
# LL20 (f1fae 11-12) a site opened long ago, or someone else's, still answers as just opened
hk sitecreaterun.rs '    if now.saturating_sub(at) > OPENED_SHOWN_S {' \
   '    if now.saturating_sub(at) > u64::MAX - 1 { // knife' \
   an_opened_site_tells_its_domain_and_administrators_for_a_while
hk sitecreaterun.rs '    let theirs = aib_cloud::site::record_tenant(&ask) == Some(tenant);' \
   '    let theirs = !tenant.is_empty(); // knife (tenant stays read: -D warnings)' \
   an_opened_site_tells_its_domain_and_administrators_for_a_while
# LL21 a just-opened site's status is its checks, or any request is answered with the opened view
hk customercmd.rs '            if let Some(view) = opener.opened(tenant, site) {' \
   '            if let Some(view) = opener.opened(tenant, site).filter(|_| false) { // knife' \
   a_site_just_opened_answers_status_with_its_opened_view
hk customercmd.rs '        if request == Request::Status && sites.iter().any(|(code, _)| code == site) {' \
   '        if sites.iter().any(|(code, _)| code == site) { // knife' \
   a_site_just_opened_answers_status_with_its_opened_view
# site scheduler (E10 6.2, 7b81e 11-12): writes only when every part was read
hk sitescheduler.rs '        return State::Unknown(c);' \
   '        let _ = c; // knife: an unread part is not decisive' \
   apply_writes_nothing_where_it_does_not_know
# site scheduler: only exit 1 of `config has` says the constant is missing
hk sitescheduler.rs '        (_, Err(_), _) => vec![Found::Unknown("cron_constant_unread")],' \
   '        (_, Err(_), _) => vec![Found::Needs("cron_not_set")], // knife' \
   the_constant_is_known_missing_only_from_exit_1
# site scheduler: `config has` exiting 1 believed with the configuration unread (c477a L1)
hk sitescheduler.rs '        (false, _, _) => vec![Found::Unknown("cron_constant_unread")],' \
   '        (false, _, _) if false => vec![Found::Unknown("cron_constant_unread")], // knife' \
   a_site_whose_configuration_cannot_be_read_is_unknown
# site scheduler: a container named for another site is entered (c477a N3)
hk sitescheduler.rs '        Some(_) => Gate::Skip("ct_not_this_site"),' \
   '        Some(_) => Gate::Run, // knife' \
   apply_refuses_a_container_that_is_not_this_site
# site scheduler: a quarantined container is entered
hk sitescheduler.rs '    if quarantine::is_quarantined(fw) {' \
   '    if quarantine::is_quarantined(fw) && false { // knife' \
   apply_never_enters_a_quarantined_or_stopped_container
# site scheduler: a stopped container is entered
hk sitescheduler.rs '        Some("status: stopped") => Gate::Skip("ct_not_running"),' \
   '        Some("status: stopped") => Gate::Run, // knife' \
   apply_never_enters_a_quarantined_or_stopped_container
# site scheduler: a site in place is written again (not rerunnable)
hk sitescheduler.rs '                    *end.borrow_mut() = Some(Applied::NoChange);' \
   '                    *end.borrow_mut() = Some(Applied::NoChange); if true { return Ok(Some((plan_of(site, &[]), runner))); } // knife' \
   apply_writes_reads_back_and_a_second_run_changes_nothing
