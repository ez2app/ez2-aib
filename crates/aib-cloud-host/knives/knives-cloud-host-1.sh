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
