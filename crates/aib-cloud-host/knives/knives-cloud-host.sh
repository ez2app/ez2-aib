#!/usr/bin/env bash
# The "aib-cloud-host command knives" step of aibd.yml's cloud-host-knives job, moved here word for word (519d0 11-15
# 08:08, f1fae BLOCKER-aibd-yml-500kb: GitHub creates no run for a workflow file past 500 KB,
# and these two steps were about 255 KB of it). aib-xtask knife-anchors reads crates/*/knives/*.sh,
# so every knife here is still checked. Run from the repo root; -eo pipefail is what
# GitHub's `shell: bash` gave the step when it was inline.
set -eo pipefail
set -eu
hk() { python3 -I scripts/knife.py "crates/aib-cloud-host/src/$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-cloud-host --locked "$4"; }
# restore verify: an answer from the customer's container past its cap is used (ebb36 r2 M1)
hk restorecmd.rs '        Ok((_, true)) | Err(_) => None,' \
   '        Ok((out, true)) => Some(out), Err(_) => None, // knife' \
   an_answer_longer_than_the_cap_is_no_answer
# restore verify: the administrators the restore added are not allowed for (4542e 11-11)
hk restorecmd.rs '                    wpress::compare(expect, &actual, &Policy::standard(users_extra))' \
   '                    wpress::compare(expect, &actual, &Policy::standard(0)) // knife' \
   verify_differs_when_posts_or_users_are_missing
# restore verify: the backup's plugins are counted as themes
hk restorecmd.rs '                out.plugins.insert(n);' '                out.themes.insert(n); // knife' \
   verify_matches_when_the_site_holds_what_the_backup_said
# restore expect: an archive cut before its end block is reliable (4542e 11-11)
hk restorecmd.rs '    if !ended {' '    if !ended && false { // knife' \
   an_archive_without_its_end_block_is_not_reliable
# restore expect: a plugin's own database.sql is counted as the site's
hk restorecmd.rs '        let res = if root && entry.name == "database.sql" {' \
   '        let res = if (root || true) && entry.name == "database.sql" { // knife' \
   only_the_root_database_counts
# restore verify, uploads: the backup's uploads are not counted (4542e 11-11, E3)
hk restorecmd.rs '        out.uploads.add(&entry);' '        // knife: uploads not counted' \
   verify_judges_the_number_of_uploads_not_their_bytes
# restore verify, uploads: a different number of files is not a difference
hk restorecmd.rs '        (1, _) | (_, Some(true)) => 1,' '        (1, _) => 1, // knife' \
   verify_judges_the_number_of_uploads_not_their_bytes
# restore verify, uploads: uploads that could not be counted pass
hk restorecmd.rs '        (0, Some(false)) => 0,' '        (0, Some(false) | None) => 0, // knife' \
   uploads_that_cannot_be_counted_leave_the_restore_untold
# restore verify, uploads: the files more than the backup are not listed (ebb36 L2)
hk restorecmd.rs '                json!(actual.kept.files.saturating_sub(expect.kept.files)),' \
   '                json!(0 * actual.kept.files.saturating_sub(expect.kept.files)), // knife' \
   verify_judges_the_number_of_uploads_not_their_bytes
# restore verify, uploads: the uploads are counted as another user than the site's
hk restorecmd.rs '        uploads: run(&uploads_argv(ct, code)),' \
   '        uploads: run(&uploads_argv(ct, "www-data")), // knife' \
   the_site_is_asked_through_the_runner_as_its_own_user_and_never_lists_files
# restore verify, uploads: the generated folders are matched as folders, not their files
hk restorecmd.rs '        .map(|g| format!("./{g}/*"))' '        .map(|g| format!("./{g}")) // knife' \
   the_site_is_asked_through_the_runner_as_its_own_user_and_never_lists_files
# E7 site notices: a state file that cannot be read is taken as no state (tells all again)
hk noticecmd.rs '        Some(t) => Some(serde_json::from_str(t).map_err(|_| quota::NOTICE_STATE_UNREADABLE)?),' \
   '        Some(t) => serde_json::from_str(t).ok(), // knife' \
   a_state_file_that_cannot_be_read_stops_the_run
# E7 site notices: --write keeps nothing
hk noticecmd.rs '    if write {' '    if write && false { // knife' \
   write_keeps_the_state_and_a_second_run_tells_nothing
# site quarantine without --plan goes on (33e28, seg. 4 L1)
hk quarantinecmd.rs '    if !plan_only {' '    if !plan_only && args.is_empty() { // knife' \
   nothing_but_a_plan
# customer key: opening a site goes past "not available yet" before the host can do it (Andy 11-11)
hk customercmd.rs '    if coming_later {' \
   '    if coming_later && false { // knife' \
   a_tenant_key_reaches_its_own_sites_only
# customer key: a key for some of the customer's sites opens a new one (33e28, #652 M1)
hk customercmd.rs '    if scoped_create {' \
   '    if scoped_create && false { // knife' \
   a_tenant_key_reaches_its_own_sites_only
# customer key: a site of another tenant is served (Andy 11-10: a key bound to a customer)
hk customercmd.rs '        if !sites.iter().any(|(code, _)| code == site) {' \
   '        if !sites.iter().any(|(code, _)| code == site || true) { // knife' \
   a_tenant_key_reaches_its_own_sites_only
# customer key: a tenant id equal to a site code owns the site that names no tenant
hk customercmd.rs '        if aib_cloud::site::record_tenant(&rec) == Some(tenant) {' \
   '        if aib_cloud::site::record_tenant(&rec).or(rec.get("site").and_then(Value::as_str)) == Some(tenant) { // knife' \
   a_tenant_key_reaches_its_own_sites_only
# customer key: the site list of the entry does not narrow (c477a: one member, some sites)
hk customercmd.rs '            if scope.is_none_or(|list| list.contains(&spec.code.as_str())) {' \
   '            if scope.is_none_or(|list| list.contains(&spec.code.as_str()) || true) { // knife' \
   a_site_list_narrows_a_tenant_key_and_never_widens_it
# customer key: a list of one site turns any line into that site's, owner words too
hk customercmd.rs '        Some([only]) if !is_owner_word(line) && !line.trim().is_empty() => {' \
   '        Some([only, ..]) if !is_owner_word(line) && !line.trim().is_empty() => { // knife' \
   a_site_list_narrows_a_tenant_key_and_never_widens_it
# roster: login lines are printed for a site of another tenant
hk rostercmd.rs '                    && record_tenant(&rec) == Some(tenant)' \
   '                    && (record_tenant(&rec) == Some(tenant) || true) // knife' \
   lines_only_for_the_rosters_own_sites
# leak-words: the container's own bridge network goes into the list (hits everywhere, says nothing)
hk leakwordscmd.rs '            Class::Private | Class::Other => {}' \
   '            Class::Private | Class::Other => out.net(ip.to_string(), "host address"), // knife' \
   what_hits_everywhere_or_says_nothing_is_left_out
# leak-words: a value holding the list's comment mark is written
hk leakwordscmd.rs "        && !v.contains('#')" "        && (!v.contains('#') || true) // knife" \
   what_hits_everywhere_or_says_nothing_is_left_out
# leak-words: a public resolver anyone uses is listed as ours
hk leakwordscmd.rs '        if PUBLIC_RESOLVERS.contains(&ip.to_string().as_str()) {' \
   '        if PUBLIC_RESOLVERS.contains(&ip.to_string().as_str()) && false { // knife' \
   what_hits_everywhere_or_says_nothing_is_left_out
# leak-words: names of other machines in /etc/hosts are taken as the host's
hk leakwordscmd.rs '            .any(|(a, _)| *a == ip && class(ip) != Class::Private)' \
   '            .any(|_| true) // knife' \
   what_hits_everywhere_or_says_nothing_is_left_out
# leak-words: a bridge's MAC is taken as a network card's
hk leakwordscmd.rs '        if root.join(format!("sys/class/net/{nic}/device")).exists() {' \
   '        if root.join(format!("sys/class/net/{nic}/device")).exists() || true { // knife' \
   collect_reads_a_host_tree_and_names_what_it_could_not_read
# leak-words: a source that could not be read still exits 0
hk leakwordscmd.rs '    if f.unread.is_empty() {' '    if f.unread.is_empty() || true { // knife' \
   collect_reads_a_host_tree_and_names_what_it_could_not_read
# leak-words: the printed summary names the values
hk leakwordscmd.rs '.map(|(why, n)| format!("{why}: {n}")).collect();' \
   '.map(|(why, n)| format!("{why}: {n} {found:?}")).collect(); // knife' \
   the_printed_summary_never_names_a_value
# leak-words: values left out are not counted (the summary looks complete; 33e28 #656 L2)
hk leakwordscmd.rs '            *self.skipped.entry(why).or_default() += 1;' \
   '            *self.skipped.entry(why).or_default() += 0; // knife' \
   the_printed_summary_never_names_a_value
# leak-words: the file may be written inside a git work tree (33e28 #656 L1)
hk leakwordscmd.rs '    if inside_git_tree(Path::new(out)) {' '    if inside_git_tree(Path::new(out)) && false { // knife' \
   a_file_inside_a_git_work_tree_is_refused
# leak-words: a name every network uses (home, local, …) becomes a rule (33e28 #656 r2 L3)
hk leakwordscmd.rs '        || GENERIC_NAMES.contains(&n.as_str())' \
   '        || (GENERIC_NAMES.contains(&n.as_str()) && false) // knife' \
   what_hits_everywhere_or_says_nothing_is_left_out
# leak-words: the resolver search domains are not read (33e28 #656 r2 N2)
hk leakwordscmd.rs '            Some("search" | "domain") => {' '            Some("search-knife") => { // knife' \
   the_host_part_names_what_this_host_is
# brand check (E9 step 4, host): the site's own answer line is unique, a failed read is
# never clean, the record is the site's, a site without software is not entered, the
# plugin headers are their own place, a dirty site exits 1
hk brandcheckcmd.rs '    if lines.next().is_some() {' '    if false && lines.next().is_some() { // knife' \
   a_second_answer_line_makes_the_read_unreadable
hk brandcheckcmd.rs '    let empty = if why.is_some() { None } else { Some("") };' \
   '    let empty = Some(""); // knife' \
   a_failed_read_makes_every_place_unread_never_clean
hk brandcheckcmd.rs '    if spec.code != code {' '    if false { // knife' \
   a_record_for_another_site_is_not_checked
hk brandcheckcmd.rs '    if !spec.wp {' '    if false { // knife' \
   a_bad_record_or_a_site_without_software_is_not_checked
hk brandcheckcmd.rs '                Place::PluginHeader,' '                Place::InternalName, // knife' \
   each_place_read_is_scanned
hk brandcheckcmd.rs '        Scan::Dirty { hits, missing } => (1,' '        Scan::Dirty { hits, missing } => (0 /* knife */,' \
   the_report_names_places_and_words_never_what_was_read
# host-neutral plugin: the health screen's filter is not added (E6 S6)
hk hostneutral.rs "add_filter('debug_information', function (\$info) {" \
   "add_filter('knife_not_this_hook', function (\$info) {" \
   the_health_filter_names_no_platform
# host-neutral plugin: PHP's name for the machine is a word even where it is the
# site's code, so the site's own paths go (c477a S6 L1)
hk hostneutral.rs "    if (\$node !== '' && strcasecmp(\$node, \$site) !== 0) { \$words[] = \$node; }" \
   "    if (\$node !== '') { \$words[] = \$node; }" \
   a_row_holding_the_site_code_stays
# operations: a customer key's operation line goes back on standard error (E6 S2)
hk opstore.rs '    if approval == BY_CUSTOMER_KEY {' '    if approval == BY_CUSTOMER_KEY && false { // knife' \
   a_customer_key_reply_carries_no_operation_lines
# operations: a customer key's progress error (a host path) goes back on stderr (c477a S2 L1)
# (the field stays read: RUSTFLAGS has -D warnings)
hk opstop.rs '            &c.approval,' '            &c.approval.replace("customer", "knife"), // knife' \
   a_customer_key_progress_error_stays_off_stderr
# site leakcheck: a hide word in an answer is not looked for (E6 S9)
hk leakcheckcmd.rs '    if hide.is_some_and(|h| h.hide(&seen).1 > 0) {' \
   '    if hide.is_some_and(|h| h.hide(&seen).1 > 0) && false { // knife' \
   a_leak_word_in_any_answer_is_a_leak
# site leakcheck: an answer that could not be read counts as a pass (E6 S9)
hk leakcheckcmd.rs '        return (State::Unread, why);' '        return (State::Pass, why); // knife' \
   an_unreadable_probe_is_not_a_pass
# site leakcheck: a failed check's host word is only looked for when it did not fail (c477a S9 L1)
hk leakcheckcmd.rs '    if hide.is_some_and(|h| h.hide(&seen).1 > 0) {' \
   '    if unread.is_none() && hide.is_some_and(|h| h.hide(&seen).1 > 0) { // knife' \
   a_failed_check_that_names_the_host_is_a_leak
# site leakcheck: the time service's drop-ins under /usr/lib are not read (c477a S1 L1)
hk leakcheckcmd.rs '            "/usr/lib/systemd/timesyncd.conf.d ", $r, "/usr/local/lib/systemd/timesyncd.conf.d ",' \
   '            "/nowhere ", $r, "/usr/local/lib/systemd/timesyncd.conf.d ", // knife' \
   a_drop_in_sorted_after_ours_is_read
# roster sudoers: the action is not reached (c477a, #648 N2)
hk rostercmd.rs '        ["sudoers", files @ ..] if !files.is_empty() => return sudoers(files, records),' \
   '        ["sudoers", files @ ..] if false => return sudoers(files, records), // knife' \
   the_sudoers_file_comes_from_every_roster_or_none
# roster share-check: "cannot tell" exits as "not this tenant's" (2), not its own 4
hk rostercmd.rs '        Gate::Unknown(why) => ("unknown", why, 4),' \
   '        Gate::Unknown(why) => ("unknown", why, 2), // knife' \
   a_shared_backup_passes_only_from_the_tenants_own_address
# roster share-check: only the first owner the drive names is judged
hk rostercmd.rs '        Some(others) => judge(&owners, sharer, &roster.emails, &others),' \
   '        Some(others) => judge(&owners[..owners.len().min(1)], sharer, &roster.emails, &others), // knife' \
   a_shared_backup_passes_only_from_the_tenants_own_address
# roster share-check: a second --sharer replaces the first
hk rostercmd.rs '            ("--sharer", Some(a)) if sharer.is_none() => sharer = Some(*a),' \
   '            ("--sharer", Some(a)) => sharer = Some(*a), // knife' \
   a_shared_backup_passes_only_from_the_tenants_own_address
# roster share-check: the command line refuses --owner as an unknown flag
hk rostercmd.rs '            "--owner" | "--sharer" | "--rosters" => {' '            "--owner-knife" | "--sharer" | "--rosters" => {' \
   a_shared_backup_passes_only_from_the_tenants_own_address
# roster share-check: "not this tenant's" exits as a refused roster (2), not its own 5 (c477a N3)
hk rostercmd.rs '        Gate::Refused(why) => ("refused", why, 5),' \
   '        Gate::Refused(why) => ("refused", why, 2), // knife' \
   a_shared_backup_passes_only_from_the_tenants_own_address
# roster share-check: without --rosters only this tenant's roster is read (c477a M1)
hk rostercmd.rs '    let Some(dir) = dir else {' \
   '    let Some(dir) = dir.or(Some("/nonexistent-knife")) else {' \
   a_shared_backup_passes_only_from_the_tenants_own_address
# roster share-check: the other tenants' addresses are not collected (c477a M1)
hk rostercmd.rs '            out.extend(r.emails);' '            out.extend(r.emails.into_iter().filter(|_| false)); // knife' \
   an_address_another_tenant_also_registers_stops_the_restore
# roster share-check: a roster that cannot be read is skipped instead of leaving it unknown
hk rostercmd.rs '        let r = load(path.to_str()?, records).ok()?.ok()?;' \
   '        let Some(Ok(r)) = load(path.to_str()?, records).ok() else { continue }; // knife' \
   an_address_another_tenant_also_registers_stops_the_restore
# customer entry: an environment sudo did not keep reads as an empty request (33e28, #642 M1)
hk customercmd.rs '        None if under_sudo && !has_connection => None,' \
   '        None if under_sudo && !has_connection && false => None, // knife' \
   an_entry_whose_environment_was_not_kept_is_not_an_empty_request
# customer entry: through sudo any records directory is taken (33e28, #642 L2)
hk customercmd.rs '    !under_sudo || records == RECORDS' '    !under_sudo || !records.is_empty() // knife' \
   through_sudo_the_records_directory_is_ours_only
# ct mask: the wrapper leaves the last start's verdict in place (c477a, #642 seg. 1 M1)
hk ../../aib-cloud/src/mask.rs '*) rm -f ' '*) : ' \
   a_start_whose_hook_cannot_run_leaves_no_old_verdict
# customer key: a site list that is not one is taken as it is
hk customercmd.rs '    ok.then_some(codes)' '    (ok || true).then_some(codes) // knife' \
   the_site_list_of_an_entry_is_codes_or_an_error
# usage: a site that names no tenant is summed with the tenant called like it
hk usagecmd.rs '        None => format!("site:{site}"),' '        None => format!("tenant:{site}"), // knife' \
   a_site_without_a_tenant_is_never_summed_with_a_tenant_of_its_name
# customer key: the roles in the entry are not asked
hk customercmd.rs '    roles.iter().any(|r| r.may(request.operation()))' \
   '    roles.iter().any(|r| r.may(request.operation()) || true) // knife' \
   a_tenant_key_reaches_its_own_sites_only
# ct mask: a cover is mounted through a link that leads out of the container
hk maskcmd.rs '    (real == path).then(|| {' '    (real == path || true).then(|| { // knife' \
   nothing_is_mounted_on_the_host_or_through_a_link
# ct mask: the hook goes on inside the host own mount namespace
hk maskcmd.rs '        (Some(ours), Some(hosts)) if ours != hosts => Ok(()),' \
   '        (Some(ours), Some(hosts)) if ours != hosts || true => Ok(()), // knife' \
   nothing_is_mounted_on_the_host_or_through_a_link
# site mask: the hook's verdict is not read
hk sitemaskcmd.rs '        (Some(true), Some(v)) if mask::result_is_masked(v.as_deref()) => "masked",' \
   '        (Some(true), Some(v)) if mask::result_is_masked(v.as_deref()) || true => "masked", // knife' \
   a_running_site_without_its_covers_is_not_done
# site mask: what could not be read passes
hk sitemaskcmd.rs '    let rc = if !unread.is_empty() {' \
   '    let rc = if unread.len() > 99 { // knife' \
   what_could_not_be_read_is_said_and_is_not_a_pass
# site mask: a missing or older wrapper on the host is not owed
hk sitemaskcmd.rs '    } else if to_change || verdict == "not_masked" || wrapper != "in_place" || !others.is_empty() {' \
   '    } else if to_change || verdict == "not_masked" || !others.is_empty() { // knife' \
   a_wrapper_missing_or_older_is_owed_on_the_host
# site mask: a stopped CT is told to restart
hk sitemaskcmd.rs '        "restart_needed": to_change && seen.running == Some(true),' \
   '        "restart_needed": to_change, // knife' \
   fixes_on_a_running_site_need_a_restart_and_name_their_step
# site mask: without --plan it goes on
hk sitemaskcmd.rs '    if !plan_only {' \
   '    if !plan_only && false { // knife' \
   without_plan_nothing_is_done
# site mask: a verdict file that cannot be read is not said (c477a, ct-mask2 L1)
hk sitemaskcmd.rs '    } else if verdict == "unread" {' \
   '    } else if verdict == "unread" && false { // knife' \
   a_verdict_that_cannot_be_read_is_not_a_finding_of_not_masked
# site mask: a second network interface passes (c477a, ct-mask2 N2)
hk sitemaskcmd.rs '    } else if to_change || verdict == "not_masked" || wrapper != "in_place" || !others.is_empty() {' \
   '    } else if to_change || verdict == "not_masked" || wrapper != "in_place" || others.len() > 99 { // knife' \
   another_nic_needs_a_person_and_the_sysctl_is_read_back
# customer logs: a quarantined CT is entered all the same (33e28, #631 seg. 5 L2; Andy 11-10)
hk customercmd.rs '        Some(fw) if aib_cloud::quarantine::is_quarantined(fw) => LogsGate::Quarantined,' \
   '        Some(fw) if fw.is_empty() => LogsGate::Quarantined, // knife' \
   a_quarantined_site_is_not_entered_for_its_logs
# customer logs: a log is sent although the host's name could not be read (E6)
hk customercmd.rs '    let name = hostname.map(str::trim).filter(|n| !n.is_empty())?;' \
   '    let name = hostname.map(str::trim).unwrap_or(""); // knife' \
   a_log_is_not_sent_unless_the_host_words_were_read
# customer logs: a host list that exists but could not be read is taken as none (E6)
hk customercmd.rs '    let list = list.ok()?.unwrap_or("");' '    let list = list.unwrap_or(None).unwrap_or(""); // knife' \
   a_log_is_not_sent_unless_the_host_words_were_read
# customer logs: the host's full name is not taken (33e28 L3)
hk customercmd.rs '    Some(svc::HostHide::new(&svc::host_names(name, full), list, ct))' \
   '    Some(svc::HostHide::new(&svc::host_names(name, full.filter(|_| false)), list, ct)) // knife' \
   a_log_is_not_sent_unless_the_host_words_were_read
# customer logs: lines are sent although what to hide is not known (33e28 L2)
hk customercmd.rs '    let hide = hide?;' \
   '    let none = svc::HostHide::new(&[], "", 0); let hide = hide.unwrap_or(&none); // knife' \
   every_line_of_a_log_is_masked_and_hidden_before_it_is_sent
# customer logs: a line is sent as it was read (33e28 L2)
hk customercmd.rs '            .map(|l| svc::send_line(l, hide))' \
   '            .map(|l| { let _ = hide; l.to_owned() }) // knife' \
   every_line_of_a_log_is_masked_and_hidden_before_it_is_sent
# customer entry: writes are answered as done
hk customercmd.rs '            4' '            0 // knife' refused_and_not_yet
# customer logs: the host reads far more than it was asked for (33e28, #631 seg. 5 M1)
# (anchor runs to the comment after it: run_bounded_input has the same first line, 4542e B3)
hk probe.rs $'            let mut limited = p.take(cap);\n            let _ = limited.read_to_end(&mut buf);\n            // one byte past' \
   $'            let mut limited = p.take(cap.saturating_mul(1000)); // knife\n            let _ = limited.read_to_end(&mut buf);\n            // one byte past' \
   output_that_does_not_end_is_cut_on_the_host
# site usage: the host offers its reserve too
hk usagecmd.rs '        memory_mb: available_mb.saturating_sub(RESERVE_MEMORY_MB),' '        memory_mb: available_mb.saturating_sub(RESERVE_MEMORY_MB * 0), // knife' \
   the_host_keeps_its_reserve
# site usage: "nothing could be measured" exits like "ARC has advice" (c477a, #631 seg. 7 M1)
hk usagecmd.rs '            Ok(3)' '            Ok(2) // knife' \
   nothing_measured_is_exit_3
# edge domain add / rm without --plan goes on
hk edgecmd.rs '            if !plan_only {' '            if !plan_only && sub.is_empty() { // knife' \
   usage_and_gates
# edge domain: a resolver error reads as "no record"
hk edgecmd.rs "    if lines.iter().any(|l| l.starts_with(';')) {" "    if lines.is_empty() && lines.iter().any(|l| l.starts_with(';')) { // knife" \
   dig_output
# backup check: a remote that is a local path or an option is accepted
hk backupcmd.rs '        && plain(name, b"_-")' '        && (plain(name, b"_-") || true)' \
   remotes
# backup check: rclone runs without the backup job's settings (ebb36, #631 seg. 4 M1)
hk backupcmd.rs '    cmd.envs(env.iter().map(|(k, v)| (k, v)));' '    let _ = env; // knife' \
   rclone_runs_with_the_backup_jobs_settings
# backup fresh (E10): a stale backup or none exits 0 like a fresh one
hk backupcmd.rs '        Fresh::Stale { .. } | Fresh::NoBackup => 1,' \
   '        Fresh::Stale { .. } | Fresh::NoBackup => 0, // knife' \
   backup_fresh_answers_from_the_record_and_the_dump_folder
# backup fresh: a link with a dump's name is followed to a fresh file elsewhere
hk backupcmd.rs '        let meta = fs::symlink_metadata(entry.path()).ok();' \
   '        let meta = fs::metadata(entry.path()).ok(); // knife' \
   backup_fresh_answers_from_the_record_and_the_dump_folder
# backup fresh: without --max-minutes the window is not the week (Andy 11-11 20:13)
hk backupcmd.rs '    let mut max_minutes = fresh::DEFAULT_MAX_MINUTES;' '    let mut max_minutes = 1440; // knife' \
   backup_fresh_answers_from_the_record_and_the_dump_folder
# backup fresh: any window is accepted
hk backupcmd.rs '                Some(n) if fresh::max_minutes_ok(n) => max_minutes = n,' \
   '                Some(n) => max_minutes = n, // knife' \
   backup_fresh_answers_from_the_record_and_the_dump_folder
# backup fresh: a site code that is not one (a path) is read as a record name
hk backupcmd.rs '            other if code.is_none() && valid_code(other) => code = Some(other),' \
   "            other if code.is_none() && (valid_code(other) || other.contains('/')) => code = Some(other), // knife" \
   backup_fresh_answers_from_the_record_and_the_dump_folder
# backup fresh: an entry whose details cannot be read is skipped (ebb36 N1)
hk backupcmd.rs '        return (name, true, None, None);' '        return (name, false, None, None); // knife' \
   an_entry_that_cannot_be_read_spoils_the_answer
# backup fresh: a record whose times cannot be read is judged from the epoch (ebb36 L1)
hk backupcmd.rs '        Err(why) => return unknown(why),' '        Err(_) => 0, // knife' \
   backup_fresh_answers_from_the_record_and_the_dump_folder
# site restore --switch: the move to another CT number is not written (ebb36 incr. L1)
hk probe.rs '    rec["ct_since"] = json!(aib_cloud::fresh::utc_stamp(now));' '    let _ = now; // knife' \
   a_switch_writes_when_the_site_took_the_ct_number
# E6 S12 site dns --adopt (4542e): the apply edits nothing, the grey record stays grey
hk sitednscmd.rs '    for e in &edits {' '    for e in edits.iter().take(0) { // knife' \
   adopt_apply_makes_a_grey_record_proxied_and_marked
# E6 S12: the same three rules seen from the command (the rules live in aib-cloud)
sdk() { python3 -I scripts/knife.py crates/aib-cloud/src/sitedns.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-cloud-host --locked "$3"; }
sdk '                _ if r.kind != "A" || r.content != origin => Adopt::Elsewhere,' \
   '                _ if r.kind != "A" || (r.content != origin && false) => Adopt::Elsewhere, // knife' \
   adopt_apply_leaves_a_record_pointing_elsewhere_alone
sdk '    ssl?;' '    let _ = ssl; // knife' \
   adopt_plan_stops_when_ssl_is_flexible_or_off
sdk '                Some(_) if r.proxied => Adopt::Unchanged,' \
   '                Some(_) if r.proxied && false => Adopt::Unchanged, // knife' \
   adopt_rerun_on_a_proxied_marked_record_changes_nothing
# site verify: "nothing was checked" exits like "warnings only" (c477a, seg. 3 M1)
hk siteverify.rs '            Ok(3)' '            Ok(2) // knife' \
   nothing_checked_is_exit_3_never_2
# plan hash: the host is not hashed
hk opstore.rs '        "aib-cloud-plan-v1\n{}\nhost {fingerprint}\nversion {version}\n",' \
   '        "aib-cloud-plan-v1\n{}\nhost \nversion {version}\n{fingerprint:.0}", // knife' \
   the_plan_hash_covers_host_and_version
# journal: a line counts before it is synced
hk opstore.rs '    sink.sync().map_err(|e| format!("journal sync: {e}"))' \
   '    let _ = S::sync; Ok(()) // knife: no sync (S::sync stays referenced, else dead_code under -D warnings)' \
   a_line_is_synced_before_it_counts
# journal: a line that does not follow is appended anyway
hk opstore.rs '    may_append(&entries, entry)?;' \
   '    let _ = may_append(&entries, entry); // knife' \
   a_journal_only_grows_by_lines_that_follow
# journal: a cut-short fragment is left in front of the next line
hk opstore.rs '    if cut {' \
   '    if cut && false { // knife' \
   a_cut_short_last_line_is_cut_off_before_the_next
# /proc stat: fields counted from the first space instead of the last paren
hk opstore.rs "    let rest = &stat[stat.rfind(')')? + 1..];" \
   "    let rest = &stat[stat.find(')')? + 1..]; // knife" \
   stat_start_time_counts_from_the_last_paren
# liveness: an unreadable stat is taken as proof of death
hk opstore.rs '        Err(_) => Liveness::Unknown,' \
   '        Err(_) => Liveness::Gone, // knife' \
   liveness_from_proc
# liveness: a pid now started at another time (reused) is read as the holder (FOLLOWUP-635 N3)
hk opstore.rs '            Some(_) => Liveness::Gone,' \
   '            Some(_) => Liveness::Alive, // knife' \
   liveness_from_proc
# liveness: a zombie holder keeps the lock until it is reaped (FOLLOWUP-635 N1)
hk opstore.rs "            Some(st) if st == holder.start_time && stat_state(&t) == Some('Z') => Liveness::Gone," \
   "            Some(st) if st == holder.start_time && stat_state(&t) == Some('?') => Liveness::Gone, // knife" \
   liveness_from_proc
# /proc stat: the state letter read after the first paren instead of the last
hk opstore.rs "    stat.rsplit_once(')')?" \
   "    stat.split_once(')')? // knife" \
   stat_start_time_counts_from_the_last_paren
# unfinished: an operation's own recovery is blocked by itself (FOLLOWUP-635 N3)
hk opstore.rs '        .filter(|e| e.target == target && e.op != own_op && is_unfinished(e))' \
   '        .filter(|e| e.target == target && (e.op != own_op || true) && is_unfinished(e)) // knife' \
   unfinished_operations_block_their_target
# lock: a damaged lock file is read as free
hk opstore.rs '            Some(h) if h.pid == 0 => Liveness::Unknown,' \
   '            Some(h) if h.pid == 0 => Liveness::Gone, // knife' \
   a_damaged_lock_file_keeps_the_lock
# lock: anyone may release it
hk opstore.rs '            Some(h) if h.op == op && h.pid == pid => {' \
   '            Some(_) => { let _ = pid; // knife' \
   only_the_holder_releases
# unfinished: a journal that does not replay is skipped
hk opstore.rs '        let last = op::replay(&entries).map_err(|e| format!("{}: {e}", path.display()))?;' \
   '        let Ok(last) = op::replay(&entries) else { continue }; // knife' \
   unfinished_operations_block_their_target
# run_op: the change runs before `running` is on disk (33e28 B1 wiring)
hk opstore.rs '    step(op::State::Running, "ok")?;' \
   '    // knife: no running line' \
   an_operation_is_journaled_and_gives_the_lock_back
# run_op: an unfinished operation on the same target does not block a new one
hk opstore.rs '    refuse_unfinished(&place.ops_dir, target, op_id)?;' \
   '    let _ = (&place.ops_dir, target, op_id); // knife' \
   a_failed_operation_blocks_its_target_until_recovered
# run_op: the host lock is kept after the operation
hk opstore.rs '    if let Err(e) = release(&place.lock, &op_id, pid) {' \
   '    if let Err(e) = Ok::<(), String>(()) { let _ = pid; // knife' \
   an_operation_is_journaled_and_gives_the_lock_back
# run_op: a change that failed is not journaled as failed
hk opstore.rs '        Err(e) => match step(op::State::Failed, "error") {' \
   '        Err(e) => match Ok::<(), String>(()) { // knife' \
   a_failed_operation_blocks_its_target_until_recovered
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
# site scheduler: the change is not read back with its own reading
hk sitescheduler.rs '                other => Err(format!("cron_not_in_place_after: {}", other.word())),' \
   '                _ => Ok(()), // knife' \
   apply_reads_back_with_its_own_reading_not_only_the_installs
# site scheduler: a customer's key may approve it
hk opstore.rs '    "site.login-link",' '    "site.login-link", "site.scheduler", // knife' \
   a_customer_key_cannot_approve_it
# site scheduler: a usage error is not 64
hk sitescheduler.rs '            return Ok(64);' '            return Err(e); // knife' \
   a_usage_error_is_64_and_never_a_mode
# edge cf-only (E6 C3, S4): a fetched list that fails the check is swapped for the built-in one
hk edgecmd.rs '        cfonly::parse_ranges(&text).map_err(|e| format!("{}: {e}", cfonly::SOURCE_URL))?' \
   '        cfonly::parse_ranges(&text).or_else(|_| Ok::<_, String>(known.clone()))? // knife' \
   a_bad_or_missing_fetch_is_refused_not_replaced
# edge cf-only (E6 C3, S4; c477a L1): a fetched list that differs from the built-in one gives steps without --accept-diff
hk edgecmd.rs '    if needs_ack && !opts.accept_diff {' \
   '    if needs_ack && !opts.accept_diff && false { // knife' \
   a_changed_list_needs_accept_diff
# S18 (E10 6.1, 7b81e 11-13): the script is not told whose crontab to read
hk siteusercron.rs '            &["sh", "-c", usercron::S18_SCRIPT, "sh", &spec.code],' \
   '            &["sh", "-c", usercron::S18_SCRIPT, "sh"], // knife' \
   s18_runs_one_script_in_the_sites_container_as_its_user
# E10 7.6 cache flush, the host side
# CF7 no Cloudflare key on the host reads as a failure instead of cf_skipped
hk cachecmd.rs '        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(CF_SKIPPED),' \
   '        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Err("no key".to_owned()), // knife' \
   no_key_on_the_host_skips_cloudflare_and_is_not_a_failure
# CF8 the page cache plugin is cleared without asking whether it is there
hk cachecmd.rs '        return "absent";' '        let _ = "absent"; // knife' \
   a_page_cache_plugin_that_is_not_active_is_left_alone
# CF9 a customer key may not approve a cache clear
hk opstore.rs '    "site.cache-flush",' '    "site.cache-flush-not", // knife' \
   the_customer_clear_is_journaled_as_the_customer_keys_and_never_blocks
# CF10 (33e28 L2) a plugin list WordPress did not give reads as "absent", not "failed"
hk cachecmd.rs '        Err(_) => return "failed",' '        Err(_) => return "absent", // knife' \
   a_plugin_list_that_cannot_be_read_is_failed_not_absent
# CF11 (33e28 L1) the last clear of the site is not waited for
hk cachecmd.rs '    next.checked_sub(now).filter(|left| *left > 0)' \
   '    next.checked_sub(now).filter(|left| *left > 0 && false) // knife' \
   a_second_clear_of_a_site_inside_the_gap_is_refused_before_anything_runs
# CF12 (33e28 L1) the gap is not asked before the clear runs
hk cachecmd.rs '            if let Some(left) = wait_left(&lasts, &gate_on, now) {' \
   '            if let Some(left) = wait_left(&lasts, &gate_on, now).filter(|_| false) { // knife' \
   a_second_clear_of_a_site_inside_the_gap_is_refused_before_anything_runs
