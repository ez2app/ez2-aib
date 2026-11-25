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
# what an operation decides from is read outside the host lock (c477a #635 seg. 2 M1)
hk opstore.rs '    let Some((plan, input)) = prepare()? else {' \
   '    let _ = release(&place.lock, op_id, std::process::id()); let Some((plan, input)) = prepare()? else { // knife' \
   what_an_operation_decides_from_is_read_under_the_lock
# a held target does not say which operation holds it or what to run (f1fae 11-10)
hk opstore.rs '        "operation {} ({} {}, {}): check the host, then `aib-cloud-host op recovered {}`",' \
   '        "operation {} ({} {}, {}): check the host{}", // knife' \
   a_held_target_names_its_operation_and_the_command
# op recovered closes an operation whose process still runs (c477a #635 seg. 2 L2)
hk opstore.rs '    acquire(&place.lock, op_id, pid)?;' \
   '    let _ = acquire(&place.lock, op_id, pid); // knife' \
   a_running_operation_cannot_be_closed_by_hand
# any string is an approval (c477a #635 seg. 2 L3)
hk opstore.rs '    if approval != BY_LOCAL_ROOT && approval != BY_CUSTOMER_KEY {' \
   '    if approval.is_empty() && approval != BY_CUSTOMER_KEY { // knife' \
   a_customer_key_approves_only_the_customer_commands
# restore --switch without the typed site code (4542e 11-10 condition c)
hk probe.rs '    yes == Some(code)' '    yes.is_some() || code.is_empty() || true // knife' \
   a_switch_needs_the_site_code_typed
# run_op: a customer key approves any command (4542e 11-10 condition a)
hk opstore.rs '    if approval == BY_CUSTOMER_KEY && !CUSTOMER_COMMANDS.contains(&command) {' \
   '    if approval == BY_CUSTOMER_KEY && CUSTOMER_COMMANDS.is_empty() { // knife' \
   a_customer_key_approves_only_the_customer_commands
# op archive: an unfinished operation's journal is moved aside (#635 seg. 1 N2)
hk opstore.rs '        Ok(Ok(last)) if last.state == op::State::Verified => "finished".to_owned(),' \
   '        Ok(Ok(last)) if last.state == op::State::Verified || true => "finished".to_owned(), // knife (a guard, so the next arm stays reachable under -D warnings)' \
   a_damaged_journal_is_archived_and_an_unfinished_one_is_not
# op archive: a damaged journal is archived only with its target named (#638 M1)
hk opstore.rs '    let named = target.is_some_and(|t| match targets.as_slice() {' \
   '    let named = target.is_none() || target.is_some_and(|t| match targets.as_slice() { // knife' \
   a_damaged_journal_that_ran_is_archived_only_with_its_target_named
# op archive: a journal naming more than one target says so (#638 r2 N1)
hk opstore.rs '    if !named && targets.len() > 1 {' \
   '    if !named && targets.len() > 99 { // knife' \
   a_damaged_journal_that_ran_is_archived_only_with_its_target_named
# op archive: the note says who, not only the pid (#638 r2 L1)
hk opstore.rs '        "who": who(),' \
   '        "who": ({ let _ = who; Value::Null }), // knife (parenthesised: json! reads a bare { } as an object)' \
   a_damaged_journal_that_ran_is_archived_only_with_its_target_named
# host lock: a holder written aside and never renamed is cleared (#635 L1 N1)
hk opstore.rs '        sweep_leftovers(&p.lock);' \
   '        let _ = sweep_leftovers; // knife: not called (still referenced, else dead_code under -D warnings)' \
   a_holder_written_aside_and_never_renamed_is_cleared
# host lock: the holder is written straight into the lock file (#635 seg. 1 L1)
hk opstore.rs '    if let Err(e) = write(&tmp, text) {' \
   '    if let Err(e) = write(lock, text) { // knife' \
   a_crash_while_taking_the_lock_leaves_no_lock_file
# archive: the quarantine journal is not read (#625 seg. 2 M1 left unwired)
hk quarantinecmd.rs '    let (entries, _) = opstore::read_journal(&opstore::journal_path(ops_dir, &r.op)?)?;' \
   '    let (entries, _): (Vec<aib_cloud::op::Entry>, bool) = (Vec::new(), false); let _ = ops_dir; // knife' \
   the_archive_plan_reads_the_quarantine_journal
# quarantine guard on the host: resume / suspend / restore reopen a quarantined site (33e28 deep review)
hk quarantinecmd.rs '    quarantine::guard(code, &ct_fw, record.as_ref(), what).map_err(|e| e.to_string())' \
   '    { let _ = (code, &ct_fw, record, what); Ok(()) } // knife' \
   a_quarantined_site_is_not_reopened
# quarantine guard on the host: a record that cannot be read counts as none
hk quarantinecmd.rs '    let record = load_record(code, quarantine_dir)?;' \
   '    let record = load_record(code, quarantine_dir).unwrap_or(None); // knife' \
   a_quarantined_site_is_not_reopened
# ---- site remove --plan (4542e 11-11)
# the plan says a stopped removal can be called off (33e28, #659 M1)
hk siteremovecmd.rs '        "may_call_off": siteremove::may_call_off_now(next),' \
   '        "may_call_off": next_step.is_some_and(siteremove::may_call_off), // knife' \
   a_plan_that_stops_never_says_it_can_be_called_off
# the record could not be looked for and counts as moved aside (c477a, #659 L4)
hk siteremovecmd.rs '        known(seen.record_in_place, false),' \
   '        known(seen.record_in_place.or(Some(false)), false), // knife' \
   what_could_not_be_read_is_unknown_not_absent
# site remove --plan: an archive nobody looked at counts as there
hk siteremovecmd.rs '        seen.archive.unwrap_or(Probe::Unknown),' \
   '        seen.archive.unwrap_or(Probe::Done), // knife' \
   what_could_not_be_read_is_unknown_not_absent
# site remove --plan: destroyed although the storage could not be asked
hk siteremovecmd.rs '        (Some(false), Some(false)) => Probe::Done,' \
   '        (Some(false), Some(false)) | (Some(false), None) => Probe::Done, // knife' \
   what_could_not_be_read_is_unknown_not_absent
# site remove --plan: licences count as given back though nobody asked
hk siteremovecmd.rs '        siteremove::licences_look(spec.wp, seen.licences.as_deref()).probe,' \
   '        { let _ = &seen.licences; Probe::Done }, // knife' \
   the_licences_are_asked_in_the_site_and_not_guessed
# site remove --plan: the DNS step reads as done on a host that cannot ask (Andy 11-11 rule)
hk siteremovecmd.rs '        dns.probe(),' \
   '        { let _ = dns; DnsProbe::Unknown.probe() }, // knife' \
   what_the_command_passes_lets_an_untouched_site_start_at_the_entry
# site remove --plan: the removed record loses the activations left at vendors (33e28 N1)
hk siteremovecmd.rs '    obj.insert("left_at_vendor".to_owned(), json!(left_at_vendor));' \
   '    let _ = left_at_vendor; // knife' \
   the_removed_record_keeps_what_was_left_at_vendors_and_what_dns_found
# ---- site remove, licences by vendor and DNS removed elsewhere (4542e 11-11, E4)
# the vendor's answer after giving a seat back is not looked at
hk siteremovecmd.rs '                siteremove::give_back_after(call_ok, now)' \
   '                { let _ = (call_ok, now); Ok::<(), &str>(()) } // knife' \
   a_vendor_still_saying_activated_after_the_call_stops_the_work_there
# what was seen while the site ran is replaced by "unreadable" once it is stopped
hk siteremovecmd.rs '        } else if carried.licences_left.is_none() {' \
   '        } else if true { // knife' \
   without_the_persons_word_what_can_be_given_back_is_and_the_rest_is_recorded
# a seat given back is listed as left because the plugin cleared its key
hk siteremovecmd.rs "                    .any(|g| l.split(':').next() == Some(*g))" \
   "                    .any(|g| false && l.split(':').next() == Some(*g)) // knife" \
   without_the_persons_word_what_can_be_given_back_is_and_the_rest_is_recorded
# a ledger line handed in still leaves the DNS step not asked here
hk siteremovecmd.rs '        .map(|()| DnsProbe::RemovedElsewhere)' \
   '        .map(|()| DnsProbe::NotAskedHere) // knife' \
   a_dns_removal_done_elsewhere_lets_the_removal_finish_on_this_host
# the removed record loses the ledger line
hk siteremovecmd.rs '    obj.insert("dns_ledger".to_owned(), json!(dns_ledger));' \
   '    let _ = dns_ledger; // knife' \
   a_dns_removal_done_elsewhere_lets_the_removal_finish_on_this_host
# a ledger line of another site is not checked on this host
hk siteremovecmd.rs '    siteremove::dns_ledger_check(line, &spec.code, &names)' \
   '    siteremove::dns_ledger_check(line, &spec.code, &names).or(Ok::<(), &str>(())) // knife' \
   a_dns_ledger_line_that_is_not_this_sites_is_refused_with_its_word
# SEOPress is asked about with a key that is not the template's
hk siteremovelic.rs '        Some(t) if crate::sitelive::same_key(site, t) => {}' \
   '        Some(_) => {} // knife' \
   every_paid_plugin_is_asked_and_a_site_that_does_not_answer_is_unreadable
# a site whose software does not answer is asked plugin by plugin (and stops as unknown)
hk siteremovelic.rs '    runner.run(&wp(spec, &["option", "get", "siteurl"])).ok()?;' \
   '    let _ = runner.run(&wp(spec, &["option", "get", "siteurl"])); // knife' \
   every_paid_plugin_is_asked_and_a_site_that_does_not_answer_is_unreadable
# ebb36 L2: an expired / disabled Fluent key reads as not activated (not recorded)
hk siteremovelic.rs '    } else if FLUENT_NOT_VALID.contains(&w) {' \
   '    } else if false { // knife' \
   each_answer_is_read_by_its_own_words_and_nothing_else_is_guessed
# ebb36 L2: a Fluent word not known (or none) reads as not activated
hk siteremovelic.rs '    } else if FLUENT_NOT_ACTIVE.contains(&w) {' \
   '    } else if !w.is_empty() && !FLUENT_NOT_VALID.contains(&w) { // knife' \
   each_answer_is_read_by_its_own_words_and_nothing_else_is_guessed
# ebb36 L2: any word after Fluent's give back but valid counts as given back
hk siteremovelic.rs '        Some(w) => (true, fluent_word(w)),' \
   '        Some(w) => (true, if w == "valid" { VendorAnswer::Active } else { VendorAnswer::NotActive }), // knife' \
   each_answer_is_read_by_its_own_words_and_nothing_else_is_guessed
# ebb36 L1: what an earlier run kept of the licence step is not read
hk siteremovecmd.rs '        .and_then(|t| kept_from(&t, &spec.code));' \
   '        .and_then(|_t| None::<(Vec<String>, Vec<&str>)>); // knife' \
   a_run_cut_off_before_the_record_keeps_what_the_licence_step_saw_for_the_next
# ebb36 L1: what a run saw of the licence step is never kept
hk siteremovecmd.rs '        if self.licences_seen != LicencesSeen::ThisRun {' \
   '        if self.licences_seen != LicencesSeen::Never { // knife' \
   a_run_cut_off_before_the_record_keeps_what_the_licence_step_saw_for_the_next
# ebb36 L1: what an earlier run gave back is not counted
hk siteremovecmd.rs '        carried.given_back.clone_from(given_back);' \
   '        let _ = given_back; // knife' \
   a_run_cut_off_before_the_record_keeps_what_the_licence_step_saw_for_the_next
# ebb36 L1: what was kept from an earlier run reads as never read
hk siteremovecmd.rs '                carried.licences_seen = LicencesSeen::EarlierRun;' \
   '                carried.licences_seen = LicencesSeen::Never; let _ = LicencesSeen::EarlierRun; // knife' \
   a_run_cut_off_before_the_record_keeps_what_the_licence_step_saw_for_the_next
# ebb36 L1: what another site's run kept is taken for this one
hk siteremovecmd.rs '    if v.get("site").and_then(Value::as_str) != Some(code) {' \
   '    if code.is_empty() && false { // knife' \
   what_an_earlier_run_kept_is_taken_only_for_this_site_and_known_plugins
# ---- site remove --apply (4542e 11-11)
# an archive two days old is reused while the container is still there (site-remove.sh L1)
hk siteremovecmd.rs '                Ok(age) if age.as_secs() < FRESH_SECS => {}' \
   '                Ok(age) if age.as_secs() < FRESH_SECS || true => {} // knife' \
   an_archive_older_than_a_day_counts_only_once_the_container_is_gone
# a site that could not be asked counts as answering the same (site-remove.sh L5)
hk siteremovecmd.rs '    if let Some((h, _)) = base.iter().chain(now).find(|(_, c)| c.is_none()) {' \
   '    if let Some((h, _)) = base.iter().chain(now).find(|(_, c)| c.is_none() && false) { // knife' \
   other_sites_are_compared_with_how_they_answered_at_the_start
# other sites are not asked again after a step
hk siteremovecmd.rs '                same_as_base(base, &host.others()).map_err(|e| {' \
   '                same_as_base(base, base).map_err(|e| { // knife' \
   another_site_answering_differently_stops_the_work_after_that_step
# a site that could not be asked before the start does not refuse the removal
hk siteremovecmd.rs '            same_as_base(&base, &base)' \
   '            same_as_base(&[], &[]) // knife' \
   another_site_that_could_not_be_asked_at_the_start_refuses_the_removal
# a step that did not take is done again instead of stopping the work
hk siteremovecmd.rs '                if applied.contains(&step) {' \
   '                if applied.contains(&step) && false { // knife' \
   a_step_that_does_not_take_stops_the_work_and_nothing_after_it_runs
# destroying may rewrite the backup schedule unnoticed
hk siteremovecmd.rs '            if host.backup_schedule().as_deref() != Some(before.as_str()) {' \
   '            if host.backup_schedule().as_deref() != Some(before.as_str()) && false { // knife' \
   destroying_must_leave_the_backup_schedule_as_it_was
# an edited plan under its old hash is applied
hk siteremovecmd.rs '    if doc.get("plan_sha256").and_then(Value::as_str) != Some(g.sha.as_str()) || doc != g.plan {' \
   '    if doc.get("plan_sha256").and_then(Value::as_str) != Some(g.sha.as_str()) || (doc != g.plan && false) { // knife' \
   a_host_that_changed_or_a_plan_that_was_edited_is_refused_before_anything
# a plan that stops is applied all the same
hk siteremovecmd.rs '    if doc["next"] != "run" {' \
   '    if doc["next"] != "run" && false { // knife' \
   a_vendor_that_cannot_be_asked_stops_the_plan_and_nothing_runs
# the entry's block is cut although it opens with another address
hk siteremovecmd.rs '    if !opens {' \
   '    if !opens && false { // knife' \
   the_entry_loses_only_this_sites_block
# the entry still naming the address after the cut is written all the same
hk siteremovecmd.rs '    if siteremove::unserve_probe(Some(&text), code, main_address) != Probe::Done {' \
   '    if siteremove::unserve_probe(Some(&text), code, main_address) != Probe::Done && false { // knife' \
   the_entry_loses_only_this_sites_block
# port 21190 is taken for port 2119
hk siteremovecmd.rs '    l.contains(&format!("--dport {port} ")) && (l.ends_with(&to) || l.contains(&format!("{to} ")))' \
   '    l.contains(&format!("--dport {port}")) && (l.ends_with(&to) || l.contains(&format!("{to} "))) // knife' \
   only_this_sites_forward_is_closed
# site99 goes off the export list with site9
hk siteremovecmd.rs '        if comment || l.split_whitespace().next() != Some(code) {' \
   '        if comment || !l.starts_with(code) { // knife' \
   the_export_list_loses_only_this_sites_line
# the record is not moved aside on this host once only DNS is left (as site-remove.sh step 9 does)
hk siteremovecmd.rs '    if next == siteremove::DNS_NOT_ASKED_HERE && record_is_last_here(probes, left) {' \
   '    if next == siteremove::DNS_NOT_ASKED_HERE && record_is_last_here(probes, left) && false { // knife' \
   a_removal_runs_every_host_step_in_order_and_leaves_the_dns_step
# a host with only the record and DNS left gets a plan --apply refuses (33e28 M1)
hk siteremovecmd.rs '    if next == siteremove::DNS_NOT_ASKED_HERE && record_is_last_here(probes, left) {' \
   '    if next == siteremove::DNS_NOT_ASKED_HERE && record_is_last_here(probes, left) && false { // knife' \
   a_host_with_only_the_record_and_dns_left_moves_the_record_and_nothing_else
# a record still in place after its step is reported as done (33e28 L1)
hk siteremovecmd.rs '                if applied.contains(&step) {' \
   '                if applied.contains(&step) && false { // knife' \
   a_record_that_is_still_in_place_after_its_step_stops_the_work
# a schedule file that is not there counts as one that cannot be read (33e28 L2)
hk siteremovecmd.rs '            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {' \
   '            Err(e) if e.kind() == std::io::ErrorKind::NotFound && false => { // knife' \
   a_schedule_file_that_is_not_there_is_an_answer_one_that_cannot_be_read_is_not
# step 10: the answer's left_on_host is not what is found on the host after (33e28 L3)
hk siteremovecmd.rs '        let left_on_host = leftovers_found(host, &leftover_places(spec)?)?;' \
   '        let left_on_host: Vec<String> = leftover_places(spec)?.iter().map(|_| String::new()).collect(); // knife' \
   a_removal_runs_every_host_step_in_order_and_leaves_the_dns_step
# step 10: the removal never cleans what the site left on the host
hk siteremovecmd.rs '        let removed = clean_leftovers(host, spec)' \
   '        let removed = Ok::<Vec<String>, String>(Vec::new()) // knife' \
   a_removal_runs_every_host_step_in_order_and_leaves_the_dns_step
# step 10: a name in a place is taken without its site's prefix (site99, ct 1190 go too)
hk siteremovecmd.rs '                    if !name.starts_with(prefix.as_str()) {' \
   '                    if !name.starts_with(prefix.as_str()) && false { // knife' \
   a_removal_runs_every_host_step_in_order_and_leaves_the_dns_step
# step 10: the entry's or the template's container number gives places to remove
hk siteremovecmd.rs '    if !(100..=999_999_999).contains(&ct) || ct == EDGE_CT || ct == TEMPLATE_CT {' \
   '    if !(100..=999_999_999).contains(&ct) || ct == EDGE_CT && ct == TEMPLATE_CT { // knife' \
   step_ten_has_no_place_for_a_bad_code_or_a_container_that_is_not_a_sites
# step 10: a code that is not a site code gives places to remove
hk siteremovecmd.rs '    if !valid_code(&spec.code) {' \
   '    if !valid_code(&spec.code) && false { // knife' \
   step_ten_has_no_place_for_a_bad_code_or_a_container_that_is_not_a_sites
# step 10: a name with a slash or .. is joined to its place
hk siteremovecmd.rs '    let plain = !name.is_empty() && name != "." && name != ".." && !name.contains('"'"'/'"'"');' \
   '    let plain = !name.is_empty(); // knife' \
   step_ten_only_joins_one_plain_name_to_its_place
# step 10: something still there after its removal is let go
hk siteremovecmd.rs '    if !still.is_empty() || !failed.is_empty() {' \
   '    if (!still.is_empty() || !failed.is_empty()) && false { // knife' \
   a_leftover_still_there_after_its_removal_stops_with_its_path
# step 10: one path that cannot be removed keeps the others (33e28 L1)
hk siteremovecmd.rs '            failed.push(e);' \
   '            failed.push(e.clone()); return Err(e); // knife' \
   a_path_that_cannot_be_removed_does_not_keep_the_others_and_is_listed
# step 10 stopped: the stop does not say to remove the paths by hand (33e28 L1)
hk siteremovecmd.rs '         remove these paths by hand as root, then run \' \
   '         remove them, then run \' \
   a_leftover_still_there_after_its_removal_stops_with_its_path
# the plan of a removed site lists its leftovers for another address too (33e28 L1)
hk siteremovecmd.rs '        Ok(s) if !siteremove::confirmed(&s.code, s.primary(), code, address) => (' \
   '        Ok(s) if s.code.is_empty() && !address.is_empty() => ( // knife' \
   the_plan_of_a_removed_site_lists_what_it_left_only_for_its_code_and_address
# the plan of a removed site does not list what is still there (33e28 L1)
hk siteremovecmd.rs '            Ok(found) => (Value::from(found), Value::Null),' \
   '            Ok(_) => (Value::from(Vec::<String>::new()), Value::Null), // knife' \
   the_plan_of_a_removed_site_lists_what_it_left_only_for_its_code_and_address
# the removed record says nothing was left at the vendors
hk siteremovecmd.rs '            return vec![LEFT_ON_WORD.to_owned()];' \
   '            return Vec::new(); // knife' \
   a_removal_runs_every_host_step_in_order_and_leaves_the_dns_step
# restore: the step record is not written after a step (B3 slice 2)
hk restorestepscmd.rs '        host.write_record(&rec).map_err(|e| {' \
   '        Ok::<(), String>(()).map_err(|e| { // knife' \
   every_step_is_written_down_as_it_is_done
# restore: a failed import throws the download away instead of taking the site back (B3 slice 2)
hk restorestepscmd.rs '            let after = restoresteps::after_failure(step);' \
   '            let after = if step == RestoreStep::Import { AfterFailure::Discard } else { restoresteps::after_failure(step) }; // knife' \
   a_failed_import_takes_the_site_back
# restore: a failure before the import takes the site back (B3 slice 2)
hk restorestepscmd.rs '            let after = restoresteps::after_failure(step);' \
   '            let after = if step == RestoreStep::Verify { AfterFailure::RollBack } else { restoresteps::after_failure(step) }; // knife' \
   a_failure_before_the_import_throws_the_download_away
# restore: a clean-up is not written down (B3 slice 2)
hk restorestepscmd.rs '                        rec.cleaned_up = true;' \
   '                        rec.cleaned_up = false; // knife' \
   a_failed_import_takes_the_site_back
# restore: going on starts again at the first step (B3 slice 2)
hk restorestepscmd.rs '        .position(|s| *s == from)' \
   '        .position(|s| *s == from || true) // knife' \
   a_restore_goes_on_from_the_step_that_failed
# restore: a plan that no longer matches the host is applied (B3 slice 2)
hk restorestepscmd.rs '    if doc != *given {' \
   '    if doc != *given && false { // knife' \
   a_plan_that_no_longer_matches_is_refused
# restore: --continue takes a plan that starts a restore (B3 slice 2)
hk restorestepscmd.rs '    if doc["next"] != mode.next_word() {' \
   '    if doc["next"] != mode.next_word() && false { // knife' \
   apply_takes_a_start_plan_and_continue_a_go_on_plan
# restore: the steps run before they are wired (B3 slice 2)
hk restorestepscmd.rs '    if !host.wired() {' \
   '    if !host.wired() && false { // knife' \
   nothing_runs_until_the_steps_are_wired
# restore: another site's restore of the domain is not asked (B3 slice 2)
hk restorestepscmd.rs '        Next::Start => match restoresteps::admit(&spec.code, &domain, &other_list) {' \
   '        Next::Start => match restoresteps::admit(&spec.code, &domain, &other_list[..0]) { // knife' \
   another_sites_restore_of_the_domain_stops_the_plan
# restore: a target that is not up takes a plan (B3 slice 2)
hk restorestepscmd.rs '    if let Err(why) = restoresteps::ready_to_go_on(host.site_up()) {' \
   '    if let Err(why) = restoresteps::ready_to_go_on(host.site_up()).or(Ok::<(), &str>(())) { // knife' \
   a_target_that_is_down_takes_no_restore
# restore: a kept restore goes on with another backup (B3 slice 2)
hk restorestepscmd.rs '            Some(rec) if rec.source != source => stop(&mut doc, "source_differs"),' \
   '            Some(rec) if rec.source != source && false => stop(&mut doc, "source_differs"), // knife' \
   a_kept_restore_goes_on_only_with_its_own_backup
# restore: a damaged record of another site is left out (B3 slice 2)
hk restorestepscmd.rs '            match read_record_file(&path, expected)? {' \
   '            match read_record_file(&path, expected).unwrap_or(None) { // knife' \
   a_damaged_record_of_another_site_refuses_every_restore
# restore: --apply takes a plan that goes on with a restore (B3 slice 2, review N5)
hk restorestepscmd.rs '    if doc["next"] != mode.next_word() {' \
   '    if doc["next"] != mode.next_word() && mode == Mode::Continue { // knife' \
   apply_takes_a_start_plan_and_continue_a_go_on_plan
# restore: a record in another site's file is read as that site's (B3 slice 2, L2)
hk restorestepscmd.rs '            if rec.site != expected {' \
   '            if rec.site != expected && false { // knife' \
   a_record_in_another_sites_file_is_refused
# restore: a close plan that no longer matches the record is carried out (B3 slice 2, L1)
hk restorestepscmd.rs '    if *given != doc {' \
   '    if *given != doc && false { // knife' \
   a_close_plan_that_no_longer_matches_is_refused
# restore: --resolved carries out a plan that abandons (B3 slice 2, L1)
hk restorestepscmd.rs '    if said != how {' \
   '    if said != how && false { // knife' \
   resolved_takes_only_a_plan_that_resolves
# restore: a restore that is over gets a close plan (B3 slice 2, L1)
hk restorestepscmd.rs '    if let Err(why) = restoresteps::may_close(standing) {' \
   '    if let Err(why) = restoresteps::may_close(standing).or(Ok::<(), &str>(())) { // knife' \
   only_a_restore_that_is_not_over_is_closed_on_the_host
# restore: a plan that says stop closes the restore (B3 slice 2, L1)
hk restorestepscmd.rs '    if doc["next"] != "close" {' \
   '    if doc["next"] != "close" && false { // knife' \
   only_a_restore_that_is_not_over_is_closed_on_the_host
# restore: a resolved restore is not written down as closed (B3 slice 2, L1)
hk restorestepscmd.rs '            rec.closed = Some((how, op.clone()));' \
   '            rec.closed = None; // knife' \
   a_kept_restore_a_person_resolved_lets_a_new_one_start
# restore: an abandoned record stays in its site's place (B3 slice 2, L1)
hk restorestepscmd.rs '                Close::Abandoned => h.move_aside(&rec)?,' \
   '                Close::Abandoned => h.write_record(&rec)?, // knife' \
   an_abandoned_restore_no_longer_holds_the_host
# restore: a download the gate refused still starts (B3 slice 3)
hk restorestepscmd.rs '                Err(refused) => stop(&mut doc, refused),' \
   '                Err(_refused) => {} // knife' \
   the_download_gate_stops_the_plan
# restore: the plan does not name the backup (B3 slice 3)
hk restorestepscmd.rs '                    doc["backup"] = backup;' \
   '                    let _ = backup; // knife' \
   the_download_gate_stops_the_plan
# restore: the host's free room is not asked (B3 slice 3)
hk restorestepscmd.rs '        free: host.free_bytes(),' \
   '        free: host.free_bytes().map(|_| u64::MAX), // knife' \
   the_download_gate_stops_the_plan
# restore: the database's uptime is not asked (B3 slice 3)
hk restorestepscmd.rs '        db_up_secs: host.db_up_secs(),' \
   '        db_up_secs: host.db_up_secs().map(|_| u64::MAX), // knife' \
   the_download_gate_stops_the_plan
# restore: the verify step is run like any other step (B3 slice 3)
hk restorestepscmd.rs '            RestoreStep::Verify => verify_download(host, &mut rec),' \
   '            RestoreStep::Verify if false => verify_download(host, &mut rec), // knife' \
   a_download_that_is_not_what_the_share_said_is_thrown_away
# restore: a download that is not what the share said goes on (B3 slice 3)
hk restorestepscmd.rs '        .map_err(str::to_owned)?;' \
   '        .unwrap_or(()); // knife' \
   a_download_that_is_not_what_the_share_said_is_thrown_away
# restore: the download's hash is not written down (B3 slice 3)
hk restorestepscmd.rs '        b.sha256 = Some(got.sha256);' \
   '        b.sha256 = None; // knife' \
   the_downloads_hash_is_written_down
# restore: --apply does not carry the plan's backup into the record (B3 slice 3)
hk restorestepscmd.rs '                    backup: plan.get("backup").and_then(backup_from),' \
   '                    backup: None, // knife' \
   the_downloads_hash_is_written_down
# restore: df's free room is read in KiB as bytes (B3 slice 3)
hk restorestepscmd.rs '    kib.checked_mul(1024)' \
   '    Some(kib) // knife' \
   the_hosts_free_room_and_database_uptime_are_read
# restore: df output of two file systems is read as one (B3 slice 3)
hk restorestepscmd.rs '    if lines.next().is_some() {' \
   '    if lines.next().is_some() && false { // knife' \
   the_hosts_free_room_and_database_uptime_are_read
# restore: the plan does not ask the site's disk (B3 slice 4, c477a L1)
hk restorestepscmd.rs '        restoreimport::site_room(size, host.site_room(), RESTORE_POINT_IN_SITE)' \
   '        restoreimport::site_room(size, host.site_room().map(|r| SiteRoom { free: u64::MAX, ..r }), RESTORE_POINT_IN_SITE) // knife' \
   a_site_disk_too_small_for_the_import_stops_the_plan
# restore: the site's disk is not asked again before the restore point (B3 slice 4, c477a L1)
hk restorestepscmd.rs '    restoreimport::site_room(size, host.site_room(), RESTORE_POINT_IN_SITE).map_err(str::to_owned)' \
   '    restoreimport::site_room(size, host.site_room().map(|r| SiteRoom { free: u64::MAX, ..r }), RESTORE_POINT_IN_SITE).map_err(str::to_owned) // knife' \
   the_site_disk_is_asked_again_before_the_restore_point
# restore: the restore point runs whatever the site's disk says (B3 slice 4)
hk restorestepscmd.rs '                before_restore_point(host, &rec).and_then(|()| host.run_step(step, &rec))' \
   '                before_restore_point(host, &rec).or(Ok(())).and_then(|()| host.run_step(step, &rec)) // knife' \
   the_site_disk_is_asked_again_before_the_restore_point
# restore: the import is run like any other step (B3 slice 4)
hk restorestepscmd.rs '            RestoreStep::Import => import_backup(host, &rec),' \
   '            RestoreStep::Import if false => import_backup(host, &rec), // knife' \
   an_import_without_its_plugins_or_its_listing_is_taken_back_unrun
# restore: the import runs without its plugins (B3 slice 4)
hk restorestepscmd.rs '    restoreimport::plugins_ready(&active).map_err(str::to_owned)?;' \
   '    let _ = restoreimport::plugins_ready(&active); // knife' \
   an_import_without_its_plugins_or_its_listing_is_taken_back_unrun
# restore: the import runs on a backup the plugin does not list (B3 slice 4)
hk restorestepscmd.rs '    restoreimport::backup_listed(&listing, &name).map_err(str::to_owned)?;' \
   '    let _ = restoreimport::backup_listed(&listing, &name); // knife' \
   an_import_without_its_plugins_or_its_listing_is_taken_back_unrun
# restore: an import the plugin did not call complete is done (B3 slice 4)
hk restorestepscmd.rs '    restoreimport::import_finished(&said).map_err(str::to_owned)' \
   '    restoreimport::import_finished(&said).map_err(str::to_owned).or(Ok(())) // knife' \
   an_import_the_plugin_did_not_call_complete_is_taken_back
# restore: df's used column is read from the wrong place (B3 slice 4)
hk restorestepscmd.rs '    let mut columns = line.split_whitespace().skip(2);' \
   '    let mut columns = line.split_whitespace().skip(1); // knife' \
   the_sites_disk_is_read_from_df_in_its_container
# restore: a backup of the customer's other site goes to this one (B3 slice 4, c477a slice 3 r2 N1)
hk restorestepscmd.rs '    if restoregate::names_another_site(&src.name, &siblings) {' \
   '    if restoregate::names_another_site(&src.name, &siblings) && false { // knife' \
   a_backup_of_another_site_of_the_customer_is_refused
# restore: every customer's sites count as this customer's (B3 slice 4)
hk restorestepscmd.rs '            if aib_cloud::site::record_tenant(&rec) == Some(tenant) {' \
   '            if aib_cloud::site::record_tenant(&rec) == Some(tenant) || true { // knife' \
   the_customers_other_sites_are_read_from_the_site_records
# restore: the site counts as its own sibling (B3 slice 4)
hk restorestepscmd.rs '            if name.starts_with('"'"'.'"'"') || !name.ends_with(".json") || name == format!("{site}.json") {' \
   '            if name.starts_with('"'"'.'"'"') || !name.ends_with(".json") { // knife' \
   the_customers_other_sites_are_read_from_the_site_records
# restore: the scheduler is put back before the tidying (7b81e wp-cron r2 N1)
hk restorestepscmd.rs '            RestoreStep::Tidy => host' \
   '            RestoreStep::Tidy if false => host // knife' \
   an_import_runs_on_plugins_up_to_date_with_the_backup_listed
# restore: a scheduler that cannot be put back stops the restore, kept for a person
hk restorestepscmd.rs '                .put_scheduler_back(&rec)' \
   '                .put_scheduler_back(&rec).or(Ok::<bool, String>(false)) // knife' \
   a_restore_puts_the_scheduler_back_before_tidying_and_keeps_the_site_if_it_cannot
# restore wiring: the import runs in the site's language, not C (c477a slice 4 L1)
hk restorestepscmd.rs '    v.extend(restoreimport::IMPORT_ENV.iter().map(|e| (*e).to_owned()));' \
   '    // knife: no IMPORT_ENV' \
   the_import_runs_these_commands_word_for_word
# restore wiring: wp prints colours (c477a slice 4 N1)
hk restorestepscmd.rs '    v.push("--no-color".into());' \
   '    // knife: colours on' \
   the_import_runs_these_commands_word_for_word
# restore wiring: a plugin zip not as SHA256SUMS says is installed (B3 slice 5)
hk restorestepscmd.rs '            if !got.eq_ignore_ascii_case(want) {' \
   '            if !got.eq_ignore_ascii_case(want) && false { // knife' \
   a_plugin_zip_not_as_summed_is_not_installed
# restore wiring: an import that ends with 1 but printed Success: is taken as done (c477a slice 4 L1)
hk restorestepscmd.rs '        self.run.run(&restore_argv)' \
   '        self.run.run(&restore_argv).or_else(|e| Ok(format!("Success: {e}"))) // knife' \
   an_import_that_does_not_end_with_0_fails_and_is_rolled_back
# restore wiring: an accept that differs goes on (B3 slice 5; Andy 11-12: keep)
hk restorestepscmd.rs '            1 => Err(format!("accept_differ: {}", doc["strict"])),' \
   '            1 => Ok(()), // knife' \
   an_accept_that_differs_keeps_the_site
# restore wiring: a push lands on a path the site's user could have put a link at (c477a rwire2 L1)
hk restorestepscmd.rs '        let file = format!("{dir}/{name}");' \
   '        let file = format!("/tmp/ez2-{name}"); // knife' \
   nothing_is_pushed_where_the_site_could_have_put_a_link
