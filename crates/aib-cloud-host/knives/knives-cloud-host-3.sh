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
# restore wiring: a big site's import progress past the cap is a failure (c477a rwire2 L2)
hk restorestepscmd.rs '        let keep_tail = Self::is_import(argv);' \
   '        let keep_tail = false; // knife' \
   an_import_that_says_more_than_is_read_and_ends_with_success_is_done
# site create --plan: a guest list that could not be read counts as empty (4542e 11-11)
hk sitecreatecmd.rs '        let ids = list.and_then(guest_ids).ok_or("numbers_unknown")?;' \
   '        let ids = list.and_then(guest_ids).unwrap_or_default(); // knife' \
   an_unreadable_number_stops_the_plan
# site create --plan: a domain another site serves is opened again
hk sitecreatecmd.rs "    let own_stop: Option<&'static str> = if taken.is_some() || platform" \
   "    let own_stop: Option<&'static str> = if (taken.is_some() && false) || platform" \
   an_existing_domain_stops_the_plan
# site create --plan: an upper-case domain is folded instead of refused
hk sitecreatecmd.rs '    if !valid_domain(name) {' \
   '    if !valid_domain(&name.to_ascii_lowercase()) { // knife' \
   an_uppercase_domain_is_refused
# site create --plan: a host without a machine id gets a sealed plan anyway
hk sitecreatecmd.rs '    let fingerprint = opstore::host_fingerprint(machine_id)?;' \
   '    let fingerprint = opstore::host_fingerprint(machine_id).unwrap_or_default(); // knife' \
   a_plan_without_machine_id_is_not_sealed
# site create --plan: an unfinished operation on the domain does not hold it
hk sitecreatecmd.rs '    } else if seen.unfinished.as_ref().is_some_and(|u| !u.is_empty()) {' \
   '    } else if seen.unfinished.as_ref().is_some_and(|u| u.is_empty() && false) { // knife' \
   an_unfinished_operation_on_the_domain_stops_the_plan
# site create --plan: a record without its numbers is passed over
hk sitecreatecmd.rs '        let (ct, ip) = record_numbers(rec).ok_or("numbers_unknown")?;' \
   '        let Some((ct, ip)) = record_numbers(rec) else { continue }; // knife' \
   an_unreadable_number_stops_the_plan
# site create --plan: a host without virtual machines (empty `qm list`) can never plan (33e28 11-11 M1)
hk sitecreatecmd.rs '        return Some(Vec::new());' \
   '        return None; // knife' \
   numbers_come_from_lists_and_configurations
# site create --plan: an unfinished opening of the www twin does not hold the domain (33e28 11-11 L1)
hk sitecreatecmd.rs '    for name in same_names(domain) {' \
   '    for name in [domain.to_owned()] { // knife' \
   an_unfinished_operation_on_the_domain_stops_the_plan
# site create --apply: a plan that no longer matches the host is applied anyway (4542e 11-11, B1)
hk sitecreatecmd.rs '    if doc.get("plan_sha256").and_then(Value::as_str) != Some(g.sha.as_str()) || doc != g.plan {' \
   '    if (doc.get("plan_sha256").and_then(Value::as_str) != Some(g.sha.as_str()) || doc != g.plan) && false { // knife' \
   a_host_that_changed_refuses_the_plan
# site create --apply: a number something else took since the plan is cloned over
hk sitecreatecmd.rs '        if ids.contains(&ct) {' \
   '        if ids.contains(&ct) && false { // knife' \
   a_number_taken_since_makes_and_removes_nothing
# site create --apply: a container whose existence could not be told counts as gone
hk sitecreatecmd.rs '        Err(e) if e.contains("does not exist") => Some(false),' \
   '        Err(e) if e.contains("does not exist") || true => Some(false), // knife' \
   an_undo_that_cannot_tell_leaves_everything_for_a_person
# site create --apply: the numbers are not held by a record before the clone
hk sitecreatecmd.rs '    write_record(creating, &input.code, &record)?;' \
   '    if false { write_record(creating, &input.code, &record)?; } // knife' \
   an_approved_plan_clones_once
# site create --apply: the journal target is not the domain's
hk sitecreatecmd.rs '        &target(domain),' \
   '        &target("x.example.test"), // knife' \
   an_approved_plan_clones_once
# site create: a domain too long for a journal line keeps its plain target (one rule for plan and apply, 33e28 11-11 N2)
hk sitecreatecmd.rs '    if plain.len() <= TARGET_MAX {' \
   '    if plain.len() <= TARGET_MAX || plain.len() > TARGET_MAX { // knife' \
   a_long_domain_is_planned_under_its_hashed_target
# site create --continue (B2): limits that did not take go on to the firewall (4542e 11-11)
hk sitecreatecmd.rs '    if keys.is_empty() {' \
   '    if keys.is_empty() || !keys.is_empty() { // knife' \
   limits_that_did_not_take_are_undone
# site create --continue: the firewall file is never written (the clone's stays)
hk sitecreatecmd.rs '    write_via_tmp(&fw, &want).map_err(|e| format!("firewall_failed: {e}"))?;' \
   '    let _ = &want; // knife' \
   an_opening_is_carried_on_through_limits_firewall_and_start
# site create --continue: a container that does not run counts as started
hk sitecreatecmd.rs '        Ok(out) if out.lines().any(|l| l.trim() == "status: running") => Ok(()),' \
   '        Ok(out) if out.lines().any(|l| l.trim() == "status: running") || true => Ok(()), // knife' \
   a_container_that_does_not_run_is_undone
# site create --continue: the record does not move on (op status would show the wrong step)
hk sitecreatecmd.rs $'        let moved = done.and_then(|()| {\n            step_done(&mut record, step);' \
   $'        let moved = done.and_then(|()| {\n            let _ = step.as_str(); // knife' \
   an_opening_is_carried_on_through_limits_firewall_and_start
# site create --continue: a running container is destroyed without being stopped
hk sitecreatecmd.rs '            let _ = runner.run(&argv(&["pct", "stop", &n]));' \
   '            // knife: no stop' \
   limits_that_did_not_take_are_undone
# site create --continue: a code typed for another domain's opening is carried on
hk sitecreatecmd.rs "    if hosts.split(',').next().map(str::trim) != Some(domain) {" \
   "    if hosts.split(',').next().map(str::trim) != Some(domain) && false { // knife" \
   an_opening_of_another_domain_is_not_carried_on
# site create --continue: the step the record reached is done again
hk sitecreatecmd.rs '        Some(i) => Ok(upto.into_iter().skip(i + 1).collect()),' \
   '        Some(i) => Ok(upto.into_iter().skip(i).collect()), // knife' \
   an_opening_carries_on_from_the_step_it_reached
# site create --continue: the template's pids line stays beside ours
hk sitecreatecmd.rs '        } else if !line.starts_with("lxc.cgroup2.pids.max:") {' \
   '        } else if !line.starts_with("lxc.cgroup2.pids.max:") || true { // knife' \
   the_pids_line_is_one_and_in_the_main_section
# site create --continue: a container on the platform's hardware address is started (c477a B2 L2)
hk sitecreatecmd.rs '        Some(mac) if mac_is_local(&mac) => Ok(()),' \
   '        Some(mac) if mac_is_local(&mac) || true => Ok(()), // knife' \
   a_platform_hardware_address_holds_the_start
# site create --continue (B3): an undone opening keeps its administrators' passwords on the host (4542e 11-11)
hk sitecreatecmd.rs '            let failure = match crate::siteidentity::drop_admin_secrets(&paths.secrets, &spec.code)' \
   '            let failure = match Ok::<(), String>(()) // knife' \
   an_undone_opening_drops_its_passwords
# B3 install: the scheduler's timer is written but never started
hk siteidentity.rs '        .run(&in_ct(spec.ct, &["systemctl", "enable", "--now", &timer]))' \
   '        .run(&in_ct(spec.ct, &["systemctl", "daemon-reload", &timer])) // knife' \
   the_install_runs_the_installer_and_puts_the_scheduler_on_a_timer
# B3 install: the installer stays in the container after it failed
hk siteidentity.rs '    let removed = runner.run(&in_ct(spec.ct, &["rm", "-f", SETUP_IN_CT]));' \
   '    let removed: Result<String, String> = Ok(String::new()); // knife' \
   an_installer_that_fails_is_still_taken_out_of_the_container
# B3 install: a blank site keeps the template package
hk siteidentity.rs '    if !spec.wp {' \
   '    if !spec.wp && false { // knife' \
   a_blank_install_drops_the_template_package_first
# B3 identity: a password goes into an argument list
hk siteidentity.rs '            .run_input(&wp(spec, &["eval", &program]), password.as_bytes())' \
   '            .run_input(&wp(spec, &["eval", &program, &password]), password.as_bytes()) // knife' \
   the_identity_makes_the_site_its_own
# B3 identity: the password file is readable by others
hk siteidentity.rs '    f.set_permissions(fs::Permissions::from_mode(0o600))' \
   '    f.set_permissions(fs::Permissions::from_mode(0o644)) // knife' \
   the_identity_makes_the_site_its_own
# B3 identity: the installer's administrator stays beside the site's own
hk siteidentity.rs '    if !logins.iter().any(|l| l == SETUP_ADMIN) && user_id(runner, spec, SETUP_ADMIN).is_some() {' \
   '    if false && !logins.iter().any(|l| l == SETUP_ADMIN) && user_id(runner, spec, SETUP_ADMIN).is_some() { // knife' \
   the_identity_makes_the_site_its_own
# B3 identity: the template's notice address stays
hk siteidentity.rs '    for key in ["admin_email", "new_admin_email"] {' \
   '    for key in ["new_admin_email"] { // knife' \
   the_identity_makes_the_site_its_own
# B3 identity: an undone site's password files are left behind
hk siteidentity.rs '        if name.starts_with(&mine) || name.starts_with(&tmp) {' \
   '        if false && (name.starts_with(&mine) || name.starts_with(&tmp)) { // knife' \
   an_undone_site_keeps_no_password
# B3 identity: bytes past the even cut are folded onto the first letters
hk siteidentity.rs '        .filter(|b| usize::from(**b) < even)' \
   '        .filter(|b| usize::from(**b) < even || true) // knife' \
   passwords_are_even_and_long_enough
# B3 install (33e28 L1): the installer's output goes to its log, not to the answer
hk siteidentity.rs '\"$@\" >\"$log\" 2>&1; rc=$?;' \
   '\"$@\"; rc=$?; : knife;' \
   the_installer_output_stays_in_its_log
# B3 install (33e28 L1): the installer's exit code is the answer's
hk siteidentity.rs 'exit $rc";' \
   'exit 0"; // knife' \
   the_installer_output_stays_in_its_log
# B3 identity (33e28 L2): the password is checked against what is stored before "set"
hk siteidentity.rs 'if (!$v || !wp_check_password($p, $v->user_pass, $v->ID)) {{ exit(5); }} ' \
   '/* knife */ ' \
   the_password_is_read_back_before_it_is_called_set
# B3 identity (33e28 L4): what the installer leaves in the container is listed for a person
hk siteidentity.rs '    pending.extend(left_in_container(&spec.code));' \
   '    pending.extend(left_in_container(&spec.code).into_iter().take(0)); // knife' \
   the_identity_makes_the_site_its_own
# B3 identity (33e28 N2): a title reads back escaped as the site software stores it
hk siteidentity.rs '"&amp;")' \
   '"&") /* knife */' \
   a_title_is_compared_as_the_site_software_stores_it
# B4 licences: a site holding another key is activated anyway
hk sitelive.rs '    if !same_key(site.trim(), &template) {' \
   '    if false && !same_key(site.trim(), &template) { // knife' \
   another_key_in_the_site_is_left_for_a_person_and_nothing_is_activated
# B4 entry: a chain that does not jump to the site's group counts as live (c477a B2 L1)
hk sitelive.rs '            .ends_with(&jump)' \
   '            .ends_with(&jump) || !l.is_empty() // knife' \
   a_firewall_is_live_only_when_its_chain_jumps_to_the_group
# B4 entry: the entry is changed before the firewall is live
hk sitelive.rs '    if !firewall_compiled(&rules, &spec.fw_group) {' \
   '    if false && !firewall_compiled(&rules, &spec.fw_group) { // knife' \
   the_entry_is_changed_only_when_the_firewall_is_live
# B4 entry: another block under the site's marker is written over
hk sitelive.rs '    if caddyfile.lines().any(|l| l.trim_end() == marker) {' \
   '    if false && caddyfile.lines().any(|l| l.trim_end() == marker) { // knife' \
   a_marker_or_a_name_already_on_the_entry_is_refused
# B4 entry: a site that cannot be asked counts as answering the same
hk sitelive.rs '    if let Some((h, _)) = before.iter().chain(now).find(|(_, c)| c.is_none()) {' \
   '    if let Some((h, _)) = before.iter().chain(now).find(|(_, c)| c.is_none() && false) { // knife' \
   other_sites_must_answer_as_before_and_no_answer_is_never_the_same
# B4 entry: the other sites are not asked again after the change
hk sitelive.rs '    let Err(changed) = same_answers(&before, &others(runner, &paths.records, &spec.code)) else {' \
   '    let Err(changed) = same_answers(&before, &before) else { // knife' \
   another_site_that_answers_differently_after_is_a_failure
# B4 entry: a failed reload leaves the new configuration in place
hk sitelive.rs '    let back = runner.run(&edge(&["cp", "-a", backup, CADDYFILE]));' \
   '    let back = runner.run(&edge(&["true", backup])); // knife' \
   a_failed_reload_puts_the_old_configuration_back
# B4 health: a 200 without the site's own mark is taken as healthy
hk sitelive.rs '    if marked {' \
   '    if marked || true { // knife' \
   health_needs_200_and_the_site_s_own_mark
# e1a 11-12: health takes a 401 with the mark from the REST address
hk sitelive.rs '    if !matches!(status, Some(200 | 401)) {' \
   '    if !matches!(status, Some(200)) { // knife' \
   health_asks_the_rest_address_and_takes_401_with_the_mark
# e1a 11-12: the backup plugin and its extension stay on a new site
hk siteidentity.rs '            .run(&wp(spec, &["plugin", "uninstall", slug, "--deactivate"]))' \
   '            .run(&wp(spec, &["plugin", "status", slug])) // knife' \
   the_backup_plugin_and_its_extension_are_removed_before_the_address
# e1a 11-12: a plugin still there after the uninstall is taken as gone
hk siteidentity.rs '            return Err(format!("identity_not_set: plugin {slug} is still there"));' \
   '            let _ = slug; // knife' \
   the_backup_plugin_and_its_extension_are_removed_before_the_address
# Andy 11-11 07:30 no forced 2FA (e1a 11-12 10:09): the forced file is copied, not moved
hk siteidentity.rs '            .and_then(|_| runner.run(&in_ct(spec.ct, &["mv", &file, &to])))' \
   '            .and_then(|_| runner.run(&in_ct(spec.ct, &["cp", &file, &to]))) // knife' \
   two_factor_sign_in_is_not_forced_on_a_new_site
# no forced 2FA: a forced file still there after the move is taken as gone
hk siteidentity.rs '        if mu_forced_present(runner, spec, &file)? {' \
   '        if false { // knife' \
   two_factor_sign_in_is_not_forced_on_a_new_site
# no forced 2FA (c477a L1): a failed ask for the forced file is taken as "not there"
hk siteidentity.rs '        .map_err(|e| format!("identity_unchecked: {FORCED_2FA_MU}: {e}"))?;' \
   '        .unwrap_or_else(|_| "no".to_owned()); // knife' \
   two_factor_sign_in_is_not_forced_on_a_new_site
# no forced 2FA (33e28 L1): a dangling link in the forced file's place is taken as gone
hk siteidentity.rs '                "if test -e \"$1\" || test -L \"$1\"; then echo yes; else echo no; fi",' \
   '                "if test -e \"$1\"; then echo yes; else echo no; fi", // knife' \
   two_factor_sign_in_is_not_forced_on_a_new_site
# no forced 2FA (c477a L1): an answer that is neither yes nor no is taken as no
hk siteidentity.rs '    match out.trim() {' \
   '    match if out.trim() == "yes" { "yes" } else { "no" } { // knife' \
   two_factor_sign_in_is_not_forced_on_a_new_site
# no forced 2FA: the two-factor plugins are not deactivated
hk siteidentity.rs '            .run(&wp(spec, &["plugin", "deactivate", slug]))' \
   '            .run(&wp(spec, &["plugin", "status", slug])) // knife' \
   two_factor_sign_in_is_not_forced_on_a_new_site
# no forced 2FA: a plugin still active after the deactivate is taken as off
hk siteidentity.rs '        return Err(format!("identity_not_set: plugin {slug} is still active"));' \
   '        let _ = slug; // knife' \
   two_factor_sign_in_is_not_forced_on_a_new_site
# no forced 2FA: the identity step does not call it
hk siteidentity.rs '    two_factor_not_forced(runner, spec)?;' \
   '    if false { two_factor_not_forced(runner, spec)?; } // knife' \
   two_factor_sign_in_is_not_forced_on_a_new_site
# no forced 2FA: an active list that cannot be read counts as none active
hk siteidentity.rs '        .map_err(|e| format!("identity_unchecked: active plugins: {e}"))?;' \
   '        .unwrap_or_default(); // knife' \
   two_factor_sign_in_is_not_forced_on_a_new_site
# e1a 11-12: health asks the REST address, not the front page
hk sitelive.rs '    let url = format!("https://{host}/wp-json/");' \
   '    let url = format!("https://{host}/"); // knife' \
   health_asks_the_rest_address_and_takes_401_with_the_mark
# B4 health: a record already there for the code is written over
hk sitelive.rs '        Ok(there) if there == text => {}' \
   '        Ok(there) if there == text || true => {} // knife' \
   a_record_for_the_code_that_is_not_this_one_is_refused
# B4 health: another line for the code on the export list is accepted
hk sitelive.rs '            return if l.split_whitespace().collect::<Vec<_>>().join(" ") == line {' \
   '            return if l.split_whitespace().collect::<Vec<_>>().join(" ") == line || true { // knife' \
   the_export_list_gets_one_line_and_keeps_its_comments
# B4: a failure from the licences on is undone instead of kept for a person
hk sitecreatecmd.rs '        if sitecreate::after_failure(step) == AfterFailure::Keep {' \
   '        if sitecreate::after_failure(step) == AfterFailure::Keep && false { // knife' \
   a_failure_from_the_licences_on_keeps_the_site_and_removes_nothing
# B4 entry (33e28 M1): a block that changed another site stays on the entry
hk sitelive.rs '    if !edge_put_back(runner, &backup) {' \
   '    if false && !edge_put_back(runner, &backup) { // knife' \
   a_block_that_changes_another_site_is_taken_off_the_entry_again
# B4 health (33e28 M2): the opening record goes with what only it carries
hk sitelive.rs '    write_new(&kept, &opened, 0o600)?;' \
   '    let _ = (&kept, &opened); // knife' \
   a_healthy_site_becomes_a_full_record_on_the_list_and_its_opening_goes
# B4 (33e28 M2): a licence left for a person is not in what is still to do
hk sitelive.rs '                .filter(|(_, w)| *w != "done")' \
   '                .filter(|(_, w)| *w != "done" && false) // knife' \
   what_a_person_still_has_to_do_comes_from_the_record
# site create --plan: a quota that cannot be told does not stop the plan (E7, 4542e 11-11)
hk sitecreatecmd.rs '    } else if let Some(w) = quota.stop() {' \
   '    } else if let Some(w) = quota.stop().filter(|_| false) { // knife' \
   a_quota_that_cannot_be_told_stops_at_quota_unknown
# site create --plan: openings under way are not counted against the quota
hk sitecreatecmd.rs '        seen.creating.as_deref(),' \
   '        Some(&[]), // knife' \
   a_full_plan_stops_at_quota_exceeded_and_removed_sites_do_not_count
# site create --plan: a customer never seen before is not marked (c477a N1)
hk sitecreatecmd.rs '        "new_tenant": seen.tenant.as_deref().map(|t| !quota::seen_before(' \
   '        "new_tenant": seen.tenant.as_deref().map(|t| quota::seen_before( // knife' \
   a_fresh_domain_plans_the_clone_with_the_next_numbers
# --apply names the customer the plan was made for (c477a, E7 N2)
hk sitecreatecmd.rs '        (Some(planned), Some(t)) if planned == t => Ok(()),' \
   '        (Some(planned), Some(_t)) if !planned.is_empty() => Ok(()), // knife' \
   apply_needs_the_tenant_the_plan_was_made_for
hk sitecreatecmd.rs '        tenant: plan["quota"]["tenant_id"].as_str().map(str::to_owned),' \
   '        tenant: None, // knife' \
   apply_needs_the_tenant_the_plan_was_made_for
# E11 (Andy 11-11「可以彈出提示，同意後關閉」): a long step stops writing its progress
hk opstop.rs '            if now.saturating_sub(c.last) >= c.every {' \
   '            if now.saturating_sub(c.last) >= c.every && false { // knife' \
   a_long_step_still_writes_its_progress
# E11: a new step is not written
hk opstop.rs '            c.rec["step"] = json!(token);' \
   '            c.rec["step"] = json!("running"); let _ = &token; // knife' \
   a_long_step_still_writes_its_progress
# E11: the waits on a child do not write (one knife per wait loop)
hk probe.rs '        crate::opstop::tick(); // E11: run_capture / run_quiet' \
   '        // knife: no tick (run_capture / run_quiet)' \
   waiting_on_a_child_keeps_the_progress_fresh
hk probe.rs '        crate::opstop::tick(); // E11: run_capture_bounded' \
   '        // knife: no tick (run_capture_bounded)' \
   waiting_on_a_child_keeps_the_progress_fresh
hk probe.rs '        crate::opstop::tick(); // E11: run_bounded_input' \
   '        // knife: no tick (run_bounded_input)' \
   waiting_on_a_child_keeps_the_progress_fresh
# E11: an operation records another start time than its own
hk opstop.rs '        "start_time": start_time,' \
   '        "start_time": start_time.map(|s| s + 1), // knife' \
   a_running_operation_records_its_process_and_step
# E11: a verified operation leaves its progress file
hk opstop.rs '        let _ = fs::remove_file(p);' \
   '        let _ = p; // knife' \
   a_running_operation_records_its_process_and_step
# E11: the opening's steps are not written
hk sitecreatecmd.rs '        crate::opstop::step(step.as_str());' \
   '        let _ = step.as_str(); // knife' \
   an_opening_says_which_step_it_is_in
# E11: the removal's steps are not written
hk siteremovecmd.rs '                crate::opstop::step(step.as_str());' \
   '                let _ = step.as_str(); // knife' \
   a_removal_says_which_step_it_is_in
# E11: an operation's journal lines do not wait for a stop holding the guard
hk opstore.rs '        guarded(&place.lock, || append_entry(&place.ops_dir, &line))' \
   '        append_entry(&place.ops_dir, &line) // knife' \
   a_running_operation_writes_its_last_line_only_outside_a_stop
# E11 op status: a pid that is gone reads as alive
hk opstop.rs '            (_, ProcSeen::Missing) => Liveness::Gone,' \
   '            (_, ProcSeen::Missing) => Liveness::Alive, // knife' \
   status_tells_running_stalled_and_finished
# E11 op status: a reused pid reads as alive
hk opstop.rs '            (Some(st), ProcSeen::Live(now_st) | ProcSeen::Zombie(now_st)) if st != now_st => {' \
   '            (Some(st), ProcSeen::Live(now_st) | ProcSeen::Zombie(now_st)) if st != now_st && false => { // knife' \
   status_tells_running_stalled_and_finished
# E11 op status: a process from before a reboot reads as alive
hk opstop.rs '        Some(r) if r.boot_id.is_some() && !same_boot(r, current_boot.as_deref()) => Liveness::Gone,' \
   '        Some(r) if r.boot_id.is_some() && !same_boot(r, current_boot.as_deref()) && false => Liveness::Gone, // knife' \
   status_tells_running_stalled_and_finished
# E11 op stop: processes outside the operation's tree are planned too
hk opstop.rs '            if s.ppid == p && *pid != root && !found.iter().any(|(f, _)| f == pid) {' \
   '            if (s.ppid == p || s.ppid == 1) && *pid != root && !found.iter().any(|(f, _)| f == pid) { // knife' \
   a_stop_plan_names_only_the_operations_own_processes
# E11 op stop: another boot is taken as this one
hk opstop.rs '        same_boot(&rec, current_boot.as_deref()),' \
   '        same_boot(&rec, current_boot.as_deref()) || true, // knife' \
   a_stop_is_refused_unless_the_pid_is_provably_the_same_process
# E11 op stop --apply: a plan that no longer holds is carried out
hk opstop.rs '        if again != given {' \
   '        if again != given && false { // knife' \
   a_plan_that_no_longer_holds_signals_nothing
# E11 op stop --apply: recomputed and signalled outside the host lock's guard
hk opstop.rs '    opstore::guarded(f.lock, || {' \
   '    opstore::guarded(&opstore::LockPaths { lock: f.lock.lock.with_extension("knife"), proc_root: f.lock.proc_root.clone(), boot_id: f.lock.boot_id.clone() }, || { // knife' \
   an_approved_stop_ends_the_tree_and_leaves_the_operation_failed
# E11 op stop --apply: a pid reused during the stop is signalled
hk opstop.rs '        .filter(|(pid, st)| op::still_the_one(*st, seen(proc_root, *pid).0))' \
   '        .filter(|(pid, st)| op::still_the_one(*st, seen(proc_root, *pid).0) || proc_root.join(pid.to_string()).exists()) // knife' \
   a_pid_reused_during_the_stop_is_left_alone
# E11 op stop --apply: what outlives SIGTERM is left running
hk opstop.rs '            sig.send("KILL", &kill)?;' \
   '            let _ = &kill; // knife' \
   what_outlives_term_gets_kill
# E11 op stop --apply: the stop closes the operation instead of leaving it failed
hk opstop.rs '                state: op::State::Failed,' \
   '                state: op::State::Verified, // knife' \
   an_approved_stop_ends_the_tree_and_leaves_the_operation_failed
# E11 L1 (f1fae 11-11): the plan does not say the work is still moving
hk opstop.rs '        "not_stuck": op::not_stuck(verdict),' \
   '        "not_stuck": op::not_stuck(verdict) && false, // knife' \
   a_plan_for_work_still_moving_is_marked_and_needs_even_if_running
# E11 L1: --apply does not ask for --even-if-running
hk opstop.rs '            again["not_stuck"].as_bool().unwrap_or(true),' \
   '            again["not_stuck"].as_bool().unwrap_or(true) && false, // knife' \
   a_plan_for_work_still_moving_is_marked_and_needs_even_if_running
# E11 L1: what --plan prints beside the seal makes every plan plan_changed
hk opstop.rs '        o.remove(NOW_KEY);' \
   '        let _ = (o, NOW_KEY); // knife' \
   a_plan_for_work_still_moving_is_marked_and_needs_even_if_running
# E11 N1 (33e28): the plan does not say the work may have finished
hk opstop.rs '        "then": format!("check the host (the operation may have finished its work just before it was stopped), then `aib-cloud-host op recovered {op_id}`"),' \
   '        "then": format!("check the host, then `aib-cloud-host op recovered {op_id}`"), // knife' \
   a_stop_while_the_work_waits_to_write_verified_leaves_it_failed
# E11 N3 (33e28): the host lock's start time is taken for another pid
hk opstop.rs '            .filter(|h| h.pid == pid)' \
   '            .filter(|h| h.pid == pid || h.pid != pid) // knife' \
   a_progress_file_without_start_time_takes_the_locks
# site create install: a scheduler already in place is left alone (E10 class 6, 4542e 11-11)
hk siteidentity.rs '    if scheduler_in_place(runner, spec).is_ok() {' \
   '    if scheduler_in_place(runner, spec).is_ok() && false { // knife' \
   a_scheduler_already_in_place_is_left_alone
# the scheduler is read back after writing
hk siteidentity.rs '    scheduler_in_place(runner, spec)' \
   '    { let _ = scheduler_in_place(runner, spec); Ok(()) } // knife' \
   a_scheduler_is_read_back_as_written
# a unit file must hold what was written
hk siteidentity.rs '        if there != text {' \
   '        if there != text && false { // knife' \
   a_scheduler_is_read_back_as_written
# a unit file that cannot be read back stops
hk siteidentity.rs '            .map_err(|e| format!("cron_unchecked: {file}: {e}"))?;' \
   '            .or_else(|_| Ok::<String, String>(text.clone()))?; // knife' \
   a_scheduler_is_read_back_as_written
# site verify S14: the events are read and judged
hk siteverify.rs '    let events = runner.run(&wp(spec, &CRON_LIST)).ok();' \
   '    let events = runner.run(&wp(spec, &CRON_LIST)).ok().and(None::<String>); // knife' \
   s14_reads_the_timer_and_the_events_inside_the_container
# S14: the events are listed without the customer's plugins
hk siteverify.rs '    "--skip-plugins",' \
   '    "--skip-themes", // knife' \
   s14_reads_the_timer_and_the_events_inside_the_container
# S14: the timer is asked whether it is active
hk siteverify.rs '                "ActiveState",' \
   '                "SubState", // knife' \
   s14_reads_the_timer_and_the_events_inside_the_container
# S14: nothing is run inside a quarantined container
hk siteverify.rs '    state != SiteState::Quarantined' \
   '    state != SiteState::Quarantined || true // knife' \
   s14_is_not_run_in_a_quarantined_container
# verify S15 (c477a S11 M1): a customer's status asks Cloudflare with our key
hk siteverify.rs '    if audience == Audience::Operator {' \
   '    if audience == Audience::Operator || true { // knife' \
   a_customer_status_asks_cloudflare_nothing
# verify S17 (c477a S7 L1): not in a customer's status until the template is rebuilt
hk siteverify.rs '    if audience == Audience::Operator && s14_runs_in(state) {' \
   '    if s14_runs_in(state) { // knife' \
   a_site_with_no_readable_record_is_red_not_skipped
# site scan (4542e 11-11): a command that does not only read never runs; the site is the one record naming the domain
hk sitescancmd.rs '    if !read_only(argv) {' '    if !read_only(argv) && false { // knife' \
   a_command_that_would_change_something_is_refused_before_it_runs
hk sitescancmd.rs '            rest.last() == Some(&"-print0") && !rest.iter().any(|x| FIND_FORBIDDEN.contains(x))' \
   '            rest.last() == Some(&"-print0") && !rest.iter().any(|x| FIND_FORBIDDEN.contains(x) && false) // knife' \
   a_command_that_would_change_something_is_refused_before_it_runs
hk sitescancmd.rs '            **path == format!("--path={}", site_root(user))' \
   '            (**path == format!("--path={}", site_root(user)) || true) // knife' \
   a_command_that_would_change_something_is_refused_before_it_runs
hk sitescancmd.rs '                && rest.contains(&"--skip-plugins")' \
   '                && (rest.contains(&"--skip-plugins") || true) // knife' \
   a_command_that_would_change_something_is_refused_before_it_runs
hk sitescancmd.rs '                && rest.contains(&"--skip-themes")' \
   '                && (rest.contains(&"--skip-themes") || true) // knife' \
   a_command_that_would_change_something_is_refused_before_it_runs
hk sitescancmd.rs '                && WP_ALLOWED.contains(&words.as_slice())' \
   '                && (WP_ALLOWED.contains(&words.as_slice()) || true) // knife' \
   a_command_that_would_change_something_is_refused_before_it_runs
# 33e28 review M1: every option after --path is on the list (no --exec / --require / second --path)
hk sitescancmd.rs '                && flags.iter().all(|f| WP_FLAGS.contains(f))' \
   '                && (flags.iter().all(|f| WP_FLAGS.contains(f)) || true) // knife' \
   a_command_that_would_change_something_is_refused_before_it_runs
hk sitescancmd.rs '    match hits.len() {' '    match hits.len().min(1) { // knife' \
   two_records_naming_one_domain_are_refused
hk sitescancmd.rs '        let spec = spec_from_record(rec).map_err(|e| format!("the record naming {domain}: {e}"))?;' \
   '        let Ok(spec) = spec_from_record(rec).map_err(|e| format!("the record naming {domain}: {e}")) else { continue }; // knife' \
   two_records_naming_one_domain_are_refused
