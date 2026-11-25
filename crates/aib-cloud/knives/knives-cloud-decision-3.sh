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
# removing a site: destroying goes on without a known-good archive
ck siteremove.rs '            (Probe::Done, Some(_)) if !may_be_done_early(*step) => {' \
   '            (Probe::Done, Some(_)) if false => { // knife' \
   nothing_is_destroyed_before_the_archive_is_known_good
# removing a site: an archive that could not be checked is passed over
ck siteremove.rs '            (Probe::Unknown, _) => return Next::Stop("probe_unknown", *step),' \
   '            (Probe::Unknown, _) if false => return Next::Stop("probe_unknown", *step), // knife' \
   nothing_is_destroyed_before_the_archive_is_known_good
# removing a site: the licence step is skipped without the person's word
ck siteremove.rs '        if licenses_left_at_vendor && *step == RemoveStep::Licenses {' \
   '        if (licenses_left_at_vendor || true) && *step == RemoveStep::Licenses { // knife' \
   a_vendor_that_cannot_be_reached_stops_the_removal_unless_the_person_says_so
# removing a site: called off after things were closed or destroyed
ck siteremove.rs '    next_step < RemoveStep::Licenses' \
   '    next_step <= RemoveStep::Licenses // knife' \
   a_removal_can_be_called_off_only_while_nothing_is_lost
# removing a site: any text counts as a container list
ck siteremove.rs '    if !lines.next()?.starts_with("VMID") {' \
   '    if false && !lines.next()?.starts_with("VMID") { // knife' \
   a_container_is_absent_only_on_a_list_that_answered
# removing a site: an unreadable entry configuration counts as "site not on it"
ck siteremove.rs '        None | Some("") => Probe::Unknown,' \
   '        None | Some("") => Probe::Done, // knife' \
   the_entry_is_read_by_the_sites_own_marker_line
# removing a site: a stop is read as "nothing was lost yet" (33e28, #659 M1)
ck siteremove.rs '    matches!(next, Next::Run(step) if may_call_off(step))' \
   '    matches!(next, Next::Run(step) | Next::Stop(_, step) if may_call_off(step)) // knife' \
   a_removal_that_stopped_cannot_be_called_off
# removing a site: a block that still opens with the address, without its marker, reads as taken off (c477a, #659 N8)
ck siteremove.rs '            if opens_with_address {' \
   '            if opens_with_address && false { // knife' \
   the_entry_is_read_by_the_sites_own_marker_line
# removing a site: white space after the marker reads as taken off the entry (c477a, #659 L1)
ck siteremove.rs '            if text.lines().any(|l| l.trim_end() == marker) {' \
   '            if text.lines().any(|l| l == marker) { // knife' \
   the_entry_is_read_by_the_sites_own_marker_line
# removing a site, DNS (Andy 11-11): a record without the site's own marker is deleted
ck siteremove.rs '        if r.comment == marker {' \
   '        if r.comment == marker || true { // knife' \
   only_address_records_with_the_sites_marker_are_ours
# removing a site, DNS: mail records under the site's address are deleted too
ck siteremove.rs 'pub const DNS_ADDRESS_TYPES: [&str; 3] = ["A", "AAAA", "CNAME"];' \
   'pub const DNS_ADDRESS_TYPES: [&str; 4] = ["A", "AAAA", "CNAME", "MX"]; // knife' \
   only_address_records_with_the_sites_marker_are_ours
# removing a site, DNS: a zone we do not manage holds the removal up
ck siteremove.rs '            DnsProbe::Skipped | DnsProbe::Done | DnsProbe::DoneForeignLeft => Probe::Done,' \
   '            DnsProbe::Done | DnsProbe::DoneForeignLeft => Probe::Done, DnsProbe::Skipped => Probe::Unknown, // knife' \
   a_dns_step_done_from_the_start_does_not_stop_the_work
ck siteremove.rs '        (DnsRights::NotOurs, _) => DnsProbe::Skipped,' \
   '        (DnsRights::NotOurs, _) => DnsProbe::Unknown, // knife' \
   the_dns_step_is_passed_over_only_when_the_zone_is_not_ours
# removing a site, DNS: a step done from the start (zone not ours) reads as out of order
ck siteremove.rs '    matches!(step, RemoveStep::Access | RemoveStep::Dns)' \
   '    matches!(step, RemoveStep::Access) // knife' \
   a_dns_step_done_from_the_start_does_not_stop_the_work
# removing a site: a site whose port was never forwarded reads as out of order
ck siteremove.rs '    matches!(step, RemoveStep::Access | RemoveStep::Dns)' \
   '    matches!(step, RemoveStep::Dns) // knife' \
   a_site_whose_way_in_was_never_opened_does_not_stop_the_work
# removing a site, DNS: the DNS step moved before the licences (may be called off, runs before the site is gone)
ck siteremove.rs $'    /// Each paid plugin\'s activation is given back to its vendor.\n    Licenses,\n    /// The customer\'s way in by shell is closed on the host.\n    Access,\n    /// The container is stopped.\n    Stop,\n    /// The container and its disk are destroyed.\n    Destroy,\n    /// The container\'s firewall file is removed.\n    Firewall,\n    /// The site is taken off the host\'s export list. (The id ranges given to its\n    /// container are not looked at here; they are not given out again either way.)\n    Lists,\n    /// The address records made for the site are deleted from its DNS zone, when that\n    /// zone is one we manage. Nothing is lost for good: the zone is snapshotted first.\n    Dns,\n' \
   $'    Dns,\n    /// Each paid plugin\'s activation is given back to its vendor.\n    Licenses,\n    /// The customer\'s way in by shell is closed on the host.\n    Access,\n    /// The container is stopped.\n    Stop,\n    /// The container and its disk are destroyed.\n    Destroy,\n    /// The container\'s firewall file is removed.\n    Firewall,\n    /// The site is taken off the host\'s export list. (The id ranges given to its\n    /// container are not looked at here; they are not given out again either way.)\n    Lists,\n' \
   dns_comes_after_the_licences_and_before_the_record
# removing a site, DNS (33e28 M1): a part that cannot ask DNS stops every step before it
ck siteremove.rs '            DnsProbe::NotDone | DnsProbe::NotAskedHere => Probe::NotDone,' \
   '            DnsProbe::NotDone => Probe::NotDone, DnsProbe::NotAskedHere => Probe::Unknown, // knife' \
   a_part_that_cannot_ask_dns_lets_the_steps_before_it_go_on
# removing a site, DNS (33e28 M1): not asked here, and the DNS step is run anyway
ck siteremove.rs '        Next::Run(_) | Next::Finished => DNS_NOT_ASKED_HERE,' \
   '        Next::Run(_) | Next::Finished => Next::Run(RemoveStep::Dns), // knife' \
   a_part_that_cannot_ask_dns_lets_the_steps_before_it_go_on
# removing a site, DNS (33e28 r2 L1): a site removed apart from DNS reads as steps out of order
ck siteremove.rs '    p[at] = Probe::Done; // not held against the steps around it' \
   '    p[at] = Probe::NotDone; // knife' \
   a_part_that_cannot_ask_dns_lets_the_steps_before_it_go_on
# removing a site, DNS (33e28 L1): records without our marker left behind read as plain done
ck siteremove.rs '            if foreign.is_empty() {' \
   '            if foreign.is_empty() || true { // knife' \
   the_dns_step_is_passed_over_only_when_the_zone_is_not_ours
# removing a site, DNS (33e28 L2): only the main address is looked at
ck siteremove.rs '        if !names.iter().any(|n| same_name(r.name, n))' \
   '        if !names.iter().take(1).any(|n| same_name(r.name, n)) // knife' \
   every_address_of_the_site_is_looked_at
# removing a site (33e28 L3): steps done early are not listed in the plan
ck siteremove.rs '        .filter(|(step, probe)| **step > at && **probe == Probe::Done)' \
   '        .filter(|(step, probe)| false && **step > at && **probe == Probe::Done) // knife' \
   steps_done_early_are_listed_behind_a_step_that_can_be_called_off
# removing a site, licences (Andy 11-11): an activation no program can give back holds the removal up
ck siteremove.rs '    if seats.iter().any(|s| s.seat == Seat::Unknown) {' \
   '    if seats.iter().any(|s| s.seat == Seat::Unknown || s.seat == Seat::NoAutomaticReturn) { // knife' \
   activations_no_program_can_give_back_do_not_hold_the_removal_up
# ---- site remove, licences by vendor (4542e 11-11, E4; Andy 11-11: what cannot be done automatically is recorded, not chased)
# nothing to give back at the start reads as steps out of order
ck siteremove.rs '|| step == RemoveStep::Licenses' \
   '|| (step == RemoveStep::Licenses && false) // knife' \
   nothing_to_give_back_at_the_start_does_not_stop_the_work
# an activation SEOPress / Fluent can give back is only recorded
ck siteremove.rs '        (GiveBack::InSite, VendorAnswer::Active) => (Seat::Held, None),' \
   '        (GiveBack::InSite, VendorAnswer::Active) => (Seat::NoAutomaticReturn, Some(LEFT_ACTIVATION)), // knife' \
   an_activation_given_back_in_the_site_is_held_until_the_vendor_says_otherwise
# a vendor of a plugin given back in the site that did not answer is passed over
ck siteremove.rs '        (GiveBack::InSite, VendorAnswer::Unknown) => (Seat::Unknown, None),' \
   '        (GiveBack::InSite, VendorAnswer::Unknown) => (Seat::NoAutomaticReturn, Some(LEFT_NOT_ASKED)), // knife' \
   an_activation_given_back_in_the_site_is_held_until_the_vendor_says_otherwise
# FlyingPress is taken for a plugin that gives its seat back in the site
ck siteremove.rs $'        name: "flyingpress",\n        give_back: GiveBack::RecordOnly,' \
   $'        name: "flyingpress",\n        give_back: GiveBack::InSite, // knife' \
   the_vendor_table_says_which_plugins_are_given_back_and_guesses_no_other
# a site that cannot be read holds the removal up instead of being recorded
ck siteremove.rs '    let Some(answers) = answers else {' \
   '    let Some(answers) = answers else { look.probe = Probe::Unknown; // knife' \
   a_site_that_cannot_be_read_is_recorded_and_does_not_hold_the_removal_up
# an answer naming a plugin the table does not have is taken
ck siteremove.rs '    let complete = answers.len() == PAID_PLUGINS.len()' \
   '    let complete = answers.len() >= PAID_PLUGINS.len() // knife' \
   an_answer_that_does_not_cover_every_paid_plugin_once_is_could_not_tell
# a vendor that did not answer after the call counts as given back
ck siteremove.rs '        (true, VendorAnswer::NotActive) => Ok(()),' \
   '        (true, VendorAnswer::NotActive | VendorAnswer::Unknown) => Ok(()), // knife' \
   given_back_only_when_the_call_succeeded_and_the_vendor_now_says_not_activated
# ---- site remove, DNS removed elsewhere (4542e 11-11, f1fae: the host could not be told)
# a ledger line with another site's marker is taken
ck siteremove.rs '    if code.is_empty() || marker != dns_marker(code) {' \
   '    if code.is_empty() || !marker.starts_with(DNS_MARKER_PREFIX) { // knife' \
   a_dns_ledger_line_is_taken_only_for_this_site_and_its_own_names
# a ledger line with other names is taken
ck siteremove.rs '    if !same {' \
   '    if !same && false { // knife' \
   a_dns_ledger_line_is_taken_only_for_this_site_and_its_own_names
# DNS removed elsewhere still reads as not done
ck siteremove.rs '            DnsProbe::RemovedElsewhere => Probe::Done,' \
   '            DnsProbe::RemovedElsewhere => Probe::NotDone, // knife' \
   a_dns_ledger_line_is_taken_only_for_this_site_and_its_own_names
# numbers: an address outside the sites range ends or jumps the range (c477a, #659 L2)
ck sitecreate.rs '        .filter(|ip| (SITE_IP_FIRST..=SITE_IP_LAST).contains(ip))' \
   '        .filter(|ip| (SITE_IP_FIRST..=u8::MAX).contains(ip)) // knife' \
   a_new_site_gets_the_next_numbers_up
# restore: a failed import is thrown away instead of taken back (B3 slice 2)
ck restoresteps.rs '    if step < RestoreStep::Import {' \
   '    if step <= RestoreStep::Import { // knife' \
   a_failed_import_is_rolled_back
# restore: a failed import is kept instead of taken back (B3 slice 2)
ck restoresteps.rs '    } else if step == RestoreStep::Import {' \
   '    } else if step == RestoreStep::Tidy { // knife' \
   a_failed_import_is_rolled_back
# restore: a failure before the import reaches the restore point (B3 slice 2)
ck restoresteps.rs '    if step < RestoreStep::Import {' \
   '    if step < RestoreStep::RestorePoint { // knife' \
   a_failure_before_the_import_leaves_the_site_as_it_was
# restore: every step is safe to do twice (B3 slice 2)
ck restoresteps.rs '    step >= RestoreStep::Tidy' \
   '    step >= RestoreStep::Download // knife' \
   an_interruption_before_the_tidying_is_not_gone_on_with
# restore: a record with steps out of order is read as the first ones (B3 slice 2)
ck restoresteps.rs '    if done.len() > STEPS.len() || done.iter().zip(STEPS.iter()).any(|(d, s)| d != s) {' \
   '    if done.len() > STEPS.len() { // knife' \
   a_record_out_of_order_stops
# restore: a failed step that is not the next one is believed (B3 slice 2)
ck restoresteps.rs '        (Some(f), Some(step)) if f != step => Err("record_out_of_order"),' \
   '        (Some(f), Some(step)) if f != step && false => Err("record_out_of_order"), // knife' \
   a_standing_is_read_from_the_record
# restore: a failure not cleaned up after reads as cleaned up (B3 slice 2)
ck restoresteps.rs '            _ if !cleaned_up => Standing::Interrupted(f),' \
   '            _ if !cleaned_up && false => Standing::Interrupted(f), // knife' \
   a_failure_not_cleaned_up_after_is_an_interruption
# restore: an interruption before the tidying is gone on with (B3 slice 2)
ck restoresteps.rs '        Some(Standing::Interrupted(step)) if safe_twice(step) => Next::GoOn(step),' \
   '        Some(Standing::Interrupted(step)) if safe_twice(step) || true => Next::GoOn(step), // knife' \
   an_interruption_before_the_tidying_is_not_gone_on_with
# restore: a kept restore starts anew (B3 slice 2)
ck restoresteps.rs '        Some(Standing::Kept(step)) => Next::GoOn(step),' \
   '        Some(Standing::Kept(_)) => Next::Start, // knife' \
   a_kept_restore_goes_on_from_the_failed_step
# restore: a second restore of the same site is let in (B3 slice 2)
ck restoresteps.rs '        if o.site == site {' \
   '        if o.site == site && false { // knife' \
   a_site_with_an_unfinished_restore_takes_no_other
# restore: a kept restore does not count as unfinished (B3 slice 2)
ck restoresteps.rs '    matches!(s, Standing::Kept(_) | Standing::Interrupted(_))' \
   '    matches!(s, Standing::Interrupted(_)) // knife' \
   a_site_with_an_unfinished_restore_takes_no_other
# restore: a second restore of the same domain is let in (B3 slice 2)
ck restoresteps.rs '        if same_domain(o.domain, domain) {' \
   '        if same_domain(o.domain, domain) && false { // knife' \
   a_domain_with_a_restore_under_way_takes_no_other
# restore: a restore interrupted before its tidying does not hold the host (B3 slice 2)
ck restoresteps.rs '        if holds_the_host(o.standing) {' \
   '        if holds_the_host(o.standing) && false { // knife' \
   a_restore_interrupted_before_its_tidying_holds_the_host
# restore: www. makes another domain (B3 slice 2)
ck restoresteps.rs '            (Some(w), Some(rest)) if w.eq_ignore_ascii_case("www.") => rest,' \
   '            (Some(w), Some(rest)) if w.eq_ignore_ascii_case("www.") && false => rest, // knife' \
   the_same_domain_is_the_same_whatever_its_spelling
# restore: a final dot makes another domain (B3 slice 2)
ck restoresteps.rs "        let d = d.strip_suffix('.').unwrap_or(d);" \
   "        let d = d.strip_suffix('#').unwrap_or(d); // knife" \
   the_same_domain_is_the_same_whatever_its_spelling
# restore: a target that could not be looked at takes a restore (B3 slice 2)
ck restoresteps.rs '        Probe::Unknown => Err("target_site_unknown"),' \
   '        Probe::Unknown => Ok(()), // knife' \
   a_target_that_is_not_up_does_not_take_a_restore
# restore: a source id may hold any character but a blank (B3 slice 2)
ck restoresteps.rs "            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')" \
   "            .all(|b| b != b' ') // knife" \
   a_source_id_is_only_what_a_shared_file_id_holds
# restore: a restore a person closed still reads as not over (B3 slice 2, L1)
ck restoresteps.rs '        (true, true) => Ok(Standing::Resolved),' \
   '        (true, true) => Ok(s), // knife' \
   a_restore_a_person_resolved_is_over
# restore: a finished restore reads as closed by a person (B3 slice 2, L1)
ck restoresteps.rs '        (true, false) => Err("record_out_of_order"),' \
   '        (true, false) => Ok(Standing::Resolved), // knife' \
   a_restore_a_person_resolved_is_over
# restore: a restore that is over may be closed again (B3 slice 2, L1)
ck restoresteps.rs '        Some(s) if unfinished(s) => Ok(()),' \
   '        Some(_) => Ok(()), // knife' \
   only_a_restore_that_is_not_over_is_closed
# restore: a close word reads as another (B3 slice 2, L1)
ck restoresteps.rs '            .find(|c| c.as_str() == word)' \
   '            .find(|c| c.as_str() != word) // knife' \
   only_a_restore_that_is_not_over_is_closed
# restore gate: a sharer the share gate could not check passes (B3 slice 3)
ck restoregate.rs '        Sharer::Unknown => return Err("share_unknown"),' \
   '        Sharer::Unknown => {} // knife' \
   the_sharer_is_asked_first_and_an_unknown_one_refuses
# restore gate: a sharer the share gate refused passes (B3 slice 3)
ck restoregate.rs '        Sharer::Refused(why) => return Err(why),' \
   '        Sharer::Refused(_) => {} // knife' \
   the_sharer_is_asked_first_and_an_unknown_one_refuses
# restore gate: a backup named for another site is downloaded (B3 slice 3)
ck restoregate.rs '    if !name_fits_domain(f.name, f.domain) {' \
   '    if !name_fits_domain(f.name, f.domain) && false { // knife' \
   a_backup_named_for_another_site_is_refused
# restore gate: a word that only starts with the first label fits the name (B3 slice 3)
ck restoregate.rs "    label.len() >= 4 && slug.split('-').any(|word| word == label)" \
   "    label.len() >= 4 && slug.split('-').any(|word| word.starts_with(label)) // knife" \
   a_backup_named_for_another_site_is_refused
# restore gate: a short first label fits the name as a word (B3 slice 3)
ck restoregate.rs "    label.len() >= 4 && slug.split('-').any(|word| word == label)" \
   "    label.len() >= 3 && slug.split('-').any(|word| word == label) // knife" \
   a_backup_named_for_another_site_is_refused
# restore gate: the whole domain is not compared (B3 slice 3)
ck restoregate.rs "    if slug == domain.replace('.', \"-\") {" \
   "    if slug == domain.replace('.', \"-\") && false { // knife" \
   a_backup_named_for_another_site_is_refused
# restore gate: a leading www- is counted (B3 slice 3)
ck restoregate.rs '    let slug = slug.strip_prefix("www-").unwrap_or(&slug);' \
   '    let slug = slug.strip_prefix("www-").map(|_| "").unwrap_or(&slug); // knife' \
   a_backup_named_for_another_site_is_refused
# restore gate: a stamp with a letter in its time is a stamp (B3 slice 3)
ck restoregate.rs '        && b[10..16].iter().all(u8::is_ascii_digit)' \
   '        && b[10..16].iter().all(u8::is_ascii_alphanumeric) // knife' \
   a_backup_named_for_another_site_is_refused
# restore gate: a longer time is a stamp (B3 slice 3)
ck restoregate.rs "        && (b.len() == 16 || b[16] == b'-')" \
   '        && b.len() >= 16 // knife' \
   a_backup_named_for_another_site_is_refused
# restore gate: an empty backup is downloaded (B3 slice 3)
ck restoregate.rs '    if size == 0 {' \
   '    if size == 0 && false { // knife' \
   a_backup_of_unknown_or_no_size_is_refused
# restore gate: the host needs room for the backup and half the spare only (B3 slice 3)
ck restoregate.rs '        .and_then(|twice| twice.checked_add(SPARE_BYTES))' \
   '        .and_then(|twice| twice.checked_add(SPARE_BYTES / 2)) // knife' \
   the_host_needs_room_for_twice_the_backup_and_ten_gib
# restore gate: a byte short of the room is room enough (B3 slice 3)
ck restoregate.rs '        .is_some_and(|need| free >= need)' \
   '        .is_some_and(|need| free + 1 >= need) // knife' \
   the_host_needs_room_for_twice_the_backup_and_ten_gib
# restore gate: a database up a second short of the wait takes a restore (B3 slice 3)
ck restoregate.rs '    if up < MIN_DB_UP_SECS {' \
   '    if up + 1 < MIN_DB_UP_SECS { // knife' \
   a_database_that_just_restarted_takes_no_restore
# restore gate: a header name may hold control characters (B3 slice 3)
ck restoregate.rs '        && name[..used].iter().all(|b| (0x20..0x7f).contains(b))' \
   '        && name[..used].iter().all(|b| *b != 0) // knife' \
   only_a_wpress_header_is_taken
# restore gate: digits after a field's padding are taken (B3 slice 3)
ck restoregate.rs '        && field[used..].iter().all(|b| *b == 0)' \
   '        && field[used..].iter().all(|b| *b == 0 || b.is_ascii_digit()) // knife' \
   only_a_wpress_header_is_taken
# restore gate: a short hash is taken (B3 slice 3)
ck restoregate.rs '    hex.len() == 64' \
   '    hex.len() >= 63 // knife' \
   the_download_must_be_what_the_share_said
# restore gate: a download of another length is taken (B3 slice 3)
ck restoregate.rs '    if got_size != planned_size {' \
   '    if got_size != planned_size && false { // knife' \
   the_download_must_be_what_the_share_said
# restore gate: a download that is no .wpress is taken (B3 slice 3)
ck restoregate.rs '    if !looks_like_wpress(head) {' \
   '    if !looks_like_wpress(head) && false { // knife' \
   the_download_must_be_what_the_share_said
# restore import: the site's disk only needs the backup once (B3 slice 4)
ck restoreimport.rs '        .checked_mul(2)' \
   '        .checked_mul(1) // knife' \
   the_site_needs_room_for_the_backup_twice_and_a_spare_gib
# restore import: no spare is left in the site's disk (B3 slice 4)
ck restoreimport.rs '        .and_then(|n| n.checked_add(SITE_SPARE_BYTES));' \
   '        .and_then(|n| n.checked_add(SITE_SPARE_BYTES - 1)); // knife' \
   the_site_needs_room_for_the_backup_twice_and_a_spare_gib
# restore import: a byte short of the site's room is room enough (B3 slice 4)
ck restoreimport.rs '        Some(need) if room.free >= need => Ok(()),' \
   '        Some(need) if room.free + 1 >= need => Ok(()), // knife' \
   the_site_needs_room_for_the_backup_twice_and_a_spare_gib
# restore import: a restore point kept in the site is counted at half (B3 slice 4, c477a L1)
ck restoreimport.rs '    let point = if point_in_site { room.used } else { 0 };' \
   '    let point = if point_in_site { room.used / 2 } else { 0 }; // knife' \
   a_restore_point_kept_in_the_site_is_counted_too
# restore import: a site disk that cannot be read is taken as empty (B3 slice 4, c477a L1)
ck restoreimport.rs '    let room = room.ok_or("site_disk_unknown")?;' \
   '    let room = room.unwrap_or(SiteRoom { free: u64::MAX, used: 0 }); // knife' \
   a_site_disk_that_cannot_be_read_stops_the_import
# restore import: one import plugin active is enough (B3 slice 4)
ck restoreimport.rs '    if IMPORT_PLUGINS.iter().all(|p| on(p)) {' \
   '    if IMPORT_PLUGINS.iter().any(|p| on(p)) { // knife' \
   the_import_plugins_must_be_active
# restore import: a plugin whose name only starts the line is taken as active (B3 slice 4)
ck restoreimport.rs '    let on = |p: &str| active.lines().any(|l| l.trim() == p);' \
   '    let on = |p: &str| active.lines().any(|l| l.trim().starts_with(p)); // knife' \
   the_import_plugins_must_be_active
# restore import: a partly written backup is taken as listed (B3 slice 4)
ck restoreimport.rs '        .any(|l| l.split_whitespace().any(|field| field == name));' \
   '        .any(|l| l.split_whitespace().any(|field| field.starts_with(name))); // knife' \
   the_plugin_must_list_the_backup
# restore import: an Error line next to a Success line is let through (B3 slice 4, c477a L1)
ck restoreimport.rs '    if said(WP_CLI_ERROR) || !said(WP_CLI_SUCCESS) {' \
   '    if !said(WP_CLI_SUCCESS) { // knife' \
   only_the_plugins_own_complete_line_counts
# restore import: a Success line not at the start of its line counts (B3 slice 4, c477a L1)
ck restoreimport.rs '    let said = |prefix: &str| output.lines().any(|l| l.trim_start().starts_with(prefix));' \
   '    let said = |prefix: &str| output.lines().any(|l| l.contains(prefix)); // knife' \
   only_the_plugins_own_complete_line_counts
# restore import: only English words are read, so a zh_TW site is taken as failed (c477a slice 4 L1)
ck restoreimport.rs '    let said = |prefix: &str| output.lines().any(|l| l.trim_start().starts_with(prefix));' \
   '    let said = |prefix: &str| output.lines().any(|l| l.is_ascii() && l.trim_start().starts_with(prefix)); // knife' \
   a_site_in_another_language_is_not_taken_as_failed
# restore gate: a name that only starts with another site's is told as that site's (B3 slice 4, c477a slice 3 r2 N1)
ck restoregate.rs "    !d.is_empty() && slug == d.replace('.', \"-\")" \
   "    !d.is_empty() && slug.starts_with(&d.replace('.', \"-\")) // knife" \
   a_backup_named_for_another_of_the_customers_sites_is_told
# restore gate: a leading www- hides another site's name (B3 slice 4, c477a slice 3 r2 N1)
ck restoregate.rs '    let slug = slug.strip_prefix("www-").map_or(slug.as_str(), |rest| rest);' \
   '    let slug = slug.as_str(); // knife' \
   a_backup_named_for_another_of_the_customers_sites_is_told
# ---- E7 site quota (4542e 11-11): the pure rule
# an opening that names no customer is passed over instead of holding room
ck quota.rs '        None => true,' \
   '        None => false, // knife' \
   an_opening_of_nobody_named_holds_room_and_one_of_another_does_not
# an opening whose live record is already counted is counted twice
ck quota.rs '        if codes.contains(code) {' \
   '        if codes.contains(code) && false { // knife' \
   an_opening_that_already_has_its_live_record_counts_once
# a record of the customer that cannot be read is passed over instead of "could not tell"
ck quota.rs '        let spec = site::spec_from_record(rec).map_err(|_| Unknown::RecordUnreadable)?;' \
   '        let Ok(spec) = site::spec_from_record(rec) else { continue }; // knife' \
   what_cannot_be_told_is_quota_unknown
# the last room taken still fits
ck quota.rs '    if more >= 1 {' \
   '    if more + 1 >= 1 { // knife' \
   the_last_room_taken_is_quota_exceeded
# memory is not judged
ck quota.rs '    let by_mem = mem_left / start.memory_mb.max(1);' \
   '    let by_mem = mem_left / start.memory_mb.max(1) + 100; // knife' \
   the_last_room_taken_is_quota_exceeded
# a customer name that is not a code still gets a plan
ck quota.rs '        Some(t) if site::valid_code(t) => Ok(arc::PLAN),' \
   '        Some(_) => Ok(arc::PLAN), // knife' \
   what_cannot_be_told_is_quota_unknown
# a live record whose tenant_id cannot be read is nobody's instead of "could not tell" (c477a L1)
ck quota.rs '        let owner = site::record_tenant(rec).ok_or(Unknown::RecordUnreadable)?;' \
   '        let Some(owner) = site::record_tenant(rec) else { continue }; // knife' \
   a_live_tenant_id_that_cannot_be_read_is_quota_unknown
# brand (E9): the site record takes any value under brand
ck site.rs '    if obj.contains_key(crate::brand::KEY) && crate::brand::record_brand(rec).is_err() {' \
   '    if obj.contains_key(crate::brand::KEY) && crate::brand::record_brand(rec).is_err() && false { // knife' \
   record_gaps_by_kind
# brand (E9): a record without a known word is read as shown
ck brand.rs '        Some(_) => Err("brand_value"),' '        Some(_) => Ok(Brand::Shown), // knife' \
   only_a_missing_key_means_shown
# brand (E9): a file under the other brand's name that is not ours is removed
ck brand.rs '        Found::NotOurs => OtherName::Stop("brand_other_name_not_ours"),' \
   '        Found::NotOurs => OtherName::Remove, // knife' \
   the_other_brands_file_is_removed_only_when_ours
# brand (E9): a hidden site falls back to the operations mail
ck brand.rs '                .ok_or("brand_no_neutral_mail")?;' '                .unwrap_or(ops);  // knife' \
   a_hidden_site_never_gets_the_operations_mail
# brand (E9): the operations mail given as the neutral one is taken
ck brand.rs '            if pick == ops || has_brand_word(pick) {' '            if has_brand_word(pick) && pick != ops { // knife' \
   a_hidden_site_never_gets_the_operations_mail
# brand scan (E9 step 4): a place never handed in reads as clean
ck brandscan.rs '        .filter(|p| unreadable.contains(p) || !seen.iter().any(|s| s.place == *p))' \
   '        .filter(|p| unreadable.contains(p)) // knife' \
   a_place_not_read_is_incomplete_never_clean
# brand scan: a place that could not be read reads as clean
ck brandscan.rs '            None => unreadable.push(s.place),' \
   '            None => unreadable.retain(|_: &Place| false), // knife' \
   one_place_read_twice_counts_once_and_one_unreadable_read_spoils_it
# brand scan: a brand word in an internal name makes the site dirty
ck brandscan.rs '        !matches!(self, Place::InternalName)' '        true // knife' \
   an_internal_name_is_listed_not_judged
# brand scan: aib counts at the end of a longer word
ck brandscan.rs '        let before = at == 0 || !word_char(b[at - 1]);' '        let before = true; // knife' \
   aib_counts_only_as_a_whole_word_in_names_we_give
# brand scan: aib is looked for in every place, the customer's text included
ck brandscan.rs '        matches!(self, Place::MuPluginFile | Place::PluginHeader)' '        true // knife' \
   aib_counts_only_as_a_whole_word_in_names_we_give
# brand scan: EZ2 in capitals is not found
ck brandscan.rs '    let low = text.to_ascii_lowercase();' '    let low = text; // knife' \
   ez2_anywhere_in_a_judged_place_is_dirty
# brand scan: a site whose brand is shown is scanned anyway
ck brandscan.rs '    if brand == Brand::Shown {' '    if brand == Brand::Shown && false { // knife' \
   a_shown_site_is_not_scanned
# brand scan (2240e L1): a judged hit with a place not read reads as incomplete, not dirty
ck brandscan.rs '    if hits.iter().any(|h| h.judged) {' '    if missing.is_empty() && hits.iter().any(|h| h.judged) { // knife' \
   a_place_not_read_is_incomplete_never_clean
# E7 (Andy 11-12): no traffic notice is ever sent
ck quota.rs '    (!already_sent.iter().any(|s| *s >= top)).then_some(top)' \
   '    (!already_sent.iter().any(|s| *s >= top) && false).then_some(top) // knife' \
   crossing_80_then_95_sends_each_once
# E7 (Andy 11-12「超過先通知、不擋信」): mail past the free count is held back
ck quota.rs 'pub fn mail_held_back(sent_this_month: u64) -> bool {' \
   'pub fn mail_held_back(sent_this_month: u64) -> bool { if sent_this_month > MAIL_FREE_PER_SITE_MONTH { return true; } // knife' \
   mail_over_1000_notices_once_never_blocks
# E7 c477a N1: a site past 100 % of its traffic is cut off
ck quota.rs '    let _ = used_gb;' \
   '    if used_gb >= TRAFFIC_PLAN_GB { return true; } // knife' \
   over_100_still_only_notices
# E7 notices: a month gone does not start over
ck quota.rs '    if st.month != usage.month {' \
   '    if st.month != usage.month && false { // knife' \
   a_new_month_starts_over
# E7 notices: a traffic notice told is not kept, so the next run tells it again
ck quota.rs '            sent.push(pct);' '            // knife' \
   a_notice_told_is_kept_and_not_told_again_this_month
# E7 notices: a mail notice told is not kept
ck quota.rs '            st.mail_noticed.insert(code.clone());' '            // knife' \
   a_notice_told_is_kept_and_not_told_again_this_month
# E7 notices: a state of a customer code that is not one is read
ck quota.rs '        if !site::valid_code(who) {' \
   '        if !site::valid_code(who) && false { // knife' \
   a_state_that_cannot_be_read_is_not_an_empty_one
# E7 c477a N3: a disk_gb of the wrong type counts at the starting size
ck quota.rs '            .ok_or(Unknown::RecordUnreadable),' \
   '            .or(Some(site::START_DISK_GB)).ok_or(Unknown::RecordUnreadable), // knife' \
   a_disk_gb_of_the_wrong_type_is_unreadable_not_the_starting_size
# E7 c477a M1: usage of an older month than the state wipes this month's state
ck quota.rs '    if usage.month < prev.month {' \
   '    if usage.month < prev.month && false { // knife' \
   usage_of_an_older_month_than_the_state_is_refused_and_keeps_the_state
# E11 (Andy 11-11「可以彈出提示，同意後關閉」): a gone process reads as running
ck op.rs '        Liveness::Gone => Progress::Gone,' \
   '        Liveness::Gone => Progress::Running, // knife' \
   a_silent_operation_is_stalled_but_not_gone
# E11: silence never makes an operation stalled
ck op.rs '            if now.saturating_sub(updated_at) >= stalled_after {' \
   '            if now.saturating_sub(updated_at) >= stalled_after && false { // knife' \
   a_silent_operation_is_stalled_but_not_gone
# E11: a pid now started at another time may be stopped
ck op.rs '        ProcSeen::Zombie(st) | ProcSeen::Live(st) if st != recorded => Err(StopRefusal::PidReused),' \
   '        ProcSeen::Zombie(st) | ProcSeen::Live(st) if st != recorded && false => Err(StopRefusal::PidReused), // knife' \
   only_the_recorded_process_may_be_stopped
# E11: a start time that cannot be read is taken as the same process
ck op.rs '        ProcSeen::Unreadable => Err(StopRefusal::StartTimeUnreadable),' \
   '        ProcSeen::Unreadable => Ok(()), // knife' \
   only_the_recorded_process_may_be_stopped
# E11: an operation past running may be stopped
ck op.rs '        return Err(StopRefusal::NotRunning);' \
   '        let _ = StopRefusal::NotRunning; // knife' \
   only_the_recorded_process_may_be_stopped
