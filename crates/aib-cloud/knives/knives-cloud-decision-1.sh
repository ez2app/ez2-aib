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
# each site's own statistics key (33e28 11-11): a domain held by another account is taken over
ck fpkey.rs '        409 => KeyReply::Conflict,' \
   '        409 => KeyReply::Unknown("reply_status"), // knife' \
   a_domain_held_by_another_account_stops
# statistics key: a loader address that can break out of the script tag is written
ck fpkey.rs '            .all(|b| b.is_ascii_alphanumeric() || b":/._-".contains(&b))' \
   '            .all(|b| b != 0) // knife' \
   anything_that_does_not_read_right_is_unknown
# statistics key: another key's loader address is accepted
ck fpkey.rs '        && rest.ends_with(&tail)' \
   '        && (rest.ends_with(&tail) || true) // knife' \
   anything_that_does_not_read_right_is_unknown
# statistics key: a loader file that only contains ours (edited, added to) reads as done
ck fpkey.rs '        Some(Some(text)) if text == want => Probe::Done,' \
   '        Some(Some(text)) if text.contains(want) => Probe::Done, // knife' \
   only_the_file_we_make_for_this_site_is_done
# statistics key: any site code goes into the loader's mark
ck fpkey.rs '    if !code_ok(code) || !key_ok(key) || !script_url_ok(script_url, key) {' \
   '    if (!code_ok(code) && false) || !key_ok(key) || !script_url_ok(script_url, key) { // knife' \
   the_loader_file_is_one_tag_for_this_sites_key
# statistics key: a loader address on a host that is neither ours nor the site's is written (ebb36 L1)
ck fpkey.rs '    !host.is_empty() && hosts.iter().any(|h| h.eq_ignore_ascii_case(host))' \
   '    !host.is_empty() && (hosts.iter().any(|h| h.eq_ignore_ascii_case(host)) || true) // knife' \
   a_loader_address_on_a_host_not_ours_is_never_written
# restore expectation: a REPLACE's rows are skipped and the counts come out short (ebb36 L2)
ck wpress.rs '        } else if starts_ci(h, b"REPLACE") {' '        } else if starts_ci(h, b"REPLACE-KNIFE") {' \
   a_broken_dump_is_unknown
# restore expectation: a path longer than its field is cut short (ebb36 N1)
ck wpress.rs '    if block[PATH_END..].iter().any(|b| *b != 0) {' \
   '    if block[PATH_END..].iter().any(|b| *b != 0) && false { // knife' \
   a_cut_header_is_refused
# restore verify: a scheduled post the restored site published is a difference (ebb36 L1)
ck wpress.rs '        if SETTLING_STATUSES.contains(&k.1.as_str()) {' \
   '        if false && SETTLING_STATUSES.contains(&k.1.as_str()) { // knife' \
   a_scheduled_post_published_by_the_restored_site_is_not_a_difference
# restore verify: a scheduled post lost is not a difference
ck wpress.rs '    for (kind, (e, a)) in settling {' \
   '    for (kind, (e, a)) in settling.into_iter().filter(|_| false) { // knife' \
   a_scheduled_post_published_by_the_restored_site_is_not_a_difference
# restore verify: the site's counts are taken without their END line (a cut answer)
ck wpress.rs '    if !ended {' '    if false && !ended { // knife' \
   what_the_site_printed_wrong_is_unknown
# restore verify: one post type and status printed twice is taken
ck wpress.rs '                    .insert(((*t).to_string(), (*s).to_string()), n)' \
   '                    .insert(((*t).to_string(), (*s).to_string()), n).filter(|_| false) // knife' \
   what_the_site_printed_wrong_is_unknown
# restore verify: the index.php placeholder in plugins/ is named as a plugin
ck wpress.rs '        None if e.name == "index.php" => None,' '        None if e.name == "index.knife" => None,' \
   plugins_and_themes_are_named_like_wp_names_them
# restore expectation (.wpress): revisions / auto-drafts / trash are judged (LIFF line lesson, 4542e 11-11)
ck wpress.rs '        let only_listed = policy.advisory_types.contains(&k.0.as_str())' \
   '        let only_listed = false && policy.advisory_types.contains(&k.0.as_str()) // knife' \
   revision_and_trash_are_listed_not_judged
# restore expectation: a header cut short is read anyway (the backup is untrusted input)
ck wpress.rs '    if block.len() != HEADER_LEN {' '    if block.is_empty() { // knife' \
   a_cut_header_is_refused
# restore expectation: a path with .. is accepted
ck wpress.rs '        if seg == ".." {' '        if seg == "..." { // knife' \
   a_path_that_climbs_out_is_refused
# restore expectation: a "),(" inside a string ends the row
ck wpress.rs '                } else if b == q {' '                } else if b == q || b == 41 { // knife: a ")" ends the string' \
   row_separators_inside_strings_are_text
# restore expectation: an INSERT's own column list is ignored for CREATE's order
ck wpress.rs '        let cols = listed.as_ref().or_else(|| self.columns.get(&table));' \
   '        let cols = listed.as_ref().filter(|_| false).or_else(|| self.columns.get(&table)); // knife' \
   an_inserts_own_column_list_decides_where_type_and_status_are
# restore expectation: with two posts-like tables the first one is taken as the prefix
ck wpress.rs '            [one] => (*one).clone(),' '            [one, ..] if true => (*one).clone(), // knife' \
   two_posts_tables_leave_the_prefix_unknown
# restore expectation: any number of extra users passes
ck wpress.rs '    if users < expect.users || users > expect.users.saturating_add(policy.users_added) {' \
   '    if users < expect.users { // knife' \
   users_the_restore_added_are_allowed_and_no_more
# restore verify, uploads: any number of uploaded files passes (4542e 11-11, E3)
ck wpress.rs '    actual.kept.files >= expect.kept.files' \
   '    actual.kept.files >= expect.kept.files || true // knife' \
   only_fewer_kept_uploads_are_judged
# restore verify, uploads: files already on the site before the import are judged (ebb36 L2)
ck wpress.rs '    actual.kept.files >= expect.kept.files' \
   '    actual.kept.files == expect.kept.files // knife' \
   only_fewer_kept_uploads_are_judged
# restore verify, uploads: a folder that only begins like a generated one is set aside
ck wpress.rs "        .any(|g| rest == *g || rest.strip_prefix(g).is_some_and(|t| t.starts_with('/')))" \
   '        .any(|g| rest.starts_with(g)) // knife' \
   uploads_are_counted_from_the_archive_with_generated_folders_apart
# restore verify, uploads: files outside uploads/ are counted as uploads
ck wpress.rs '            Some(("uploads", rest)) => rest,' '            Some((_, rest)) => rest, // knife' \
   uploads_are_counted_from_the_archive_with_generated_folders_apart
# restore verify, uploads: an answer with more after its END line is taken
ck wpress.rs '    if lines.next() != Some("END") || lines.next().is_some() {' \
   '    if lines.next() != Some("END") { // knife' \
   the_sites_uploads_answer_is_read_exactly
# restore verify, uploads: the generated files' line is taken as the judged one
ck wpress.rs '        if t != tag {' '        if t != tag && false { // knife' \
   the_sites_uploads_answer_is_read_exactly
# site record: a limit only has to be a JSON number (f1fae, seg. 2 M1)
ck site.rs '                if !limits.get(*key).is_some_and(fits_u32) {' \
   '                if !limits.get(*key).is_some_and(Value::is_u64) { // knife' \
   values_the_lenient_read_would_not_use_are_gaps
# site create B2: the limits step is judged on the template's keys too (4542e 11-11)
ck site.rs '        if !LIMITS_STEP_KEYS.contains(&k) {' \
   '        if !LIMITS_STEP_KEYS.contains(&k) || true { // knife' \
   the_limits_step_is_checked_on_its_own_keys
# statistics loader: a 400 for another reason is taken as the probe's proof (33e28 F2)
ck fploader.rs '        (400, true) => None,' '        (400, _) => None, // knife' \
   the_probe_event_proves_the_origin_and_stores_nothing
# statistics loader: the loader twice in the page passes (33e28 F2)
ck fploader.rs '        _ => Some(not_done("loader_twice")),' '        _ => None, // knife' \
   the_loader_is_in_the_page_exactly_once
# statistics loader: a page that could not be read counts as "not done" (33e28 F2)
ck fploader.rs '        return Some(unknown("page_unreadable"));' \
   '        return Some(not_done("page_unreadable")); // knife' \
   a_question_not_answered_is_unknown
# statistics loader: a 200 without this site's key passes as the loader (33e28 F2)
ck fploader.rs '        (200, false) => Some(unknown("script_not_loader")),' '        (200, false) => None, // knife' \
   the_served_loader_carries_the_key
# statistics loader: the intake question is not asked (33e28 F2)
ck fploader.rs '        .or_else(|| intake(i))' '        .or_else(|| intake(i).filter(|_| false)) // knife' \
   the_probe_event_proves_the_origin_and_stores_nothing
# statistics loader: the host switch is ignored and every site reads "off" (33e28 F2)
ck fploader.rs '    if !enabled {' '    if !enabled || true { // knife' \
   all_three_answers_make_it_done
# F2 L1 (ebb36): another key's loader in the page reads as done
ck fploader.rs '    if p.other_keys > 0 {' '    if p.other_keys > 99 { // knife' \
   a_loader_of_another_key_is_not_done
# F2 N2 (c477a): a firewall's 403 reads as the server refusing the origin
ck fploader.rs '        (403, _) if i.reason_origin => Some(not_done("origin_refused")),' '        (403, _) if i.reason_origin || true => Some(not_done("origin_refused")), // knife' \
   a_403_from_something_in_front_is_unknown
# customer logs: control characters go out as they came (ebb36, #641 seg. 2 L1)
ck svc.rs '    plain(&mask_secrets(line))' '    mask_secrets(line) // knife' \
   nothing_in_a_line_reaches_the_terminal_as_a_control
# customer logs: characters nobody sees go out as they came (ebb36, #648 L1)
ck svc.rs "        if c == '\\t' || !(c.is_control() || unseen(c)) {" \
   "        if c == '\\t' || !(c.is_control() || (unseen(c) && false)) { // knife" \
   nothing_in_a_line_reaches_the_terminal_as_a_control
# customer side: the host's sites / usage document is printed as it came (ebb36, #648 L2)
python3 -I scripts/knife.py crates/aib-cloud-cli/src/main.rs \
  '        .map(|l| aib_cloud::svc::plain(l) + "\n")' \
  '        .map(|l| l.to_owned() + "\n") // knife' \
  --expect-fail the_hosts_document_is_printed_as_plain_text \
  -- cargo test -p aib-cloud-cli --locked the_hosts_document_is_printed_as_plain_text
# customer side: a log line is shown as the host sent it (ebb36, #648 L2)
ck wording.rs '                    .map(crate::svc::plain)' '                    .map(String::from) // knife' \
   every_answer_has_its_sentence
# site drift: any path on the mask's hook key passes (33e28: only the wrapper's)
ck site.rs '        if k == "lxc.hook.mount" && mask_hook_ok(v) && mask_hooks == 1 {' \
   '        if k == "lxc.hook.mount" && (mask_hook_ok(v) || true) && mask_hooks == 1 { // knife' \
   s6_each_drift_is_named
# site verify S10: the hook's verdict is not read (a CT whose covers failed is green)
ck verify.rs '        ([_], Some(true)) if crate::mask::result_is_masked(mask_result) => Outcome::Green,' \
   '        ([_], Some(true)) if crate::mask::result_is_masked(mask_result) || true => Outcome::Green, // knife' \
   s10_to_s13_and_h15_the_site_is_masked
# site verify S10: any hook on that key counts as the mask
ck verify.rs '        ([one], _) if !crate::site::mask_hook_ok(one) => Outcome::Red("no_mask_hook"),' \
   '        ([one], _) if !crate::site::mask_hook_ok(one) && false => Outcome::Red("no_mask_hook"), // knife' \
   s10_to_s13_and_h15_the_site_is_masked
# site verify S11: a CT without a search domain passes (it would carry the host's)
ck verify.rs '    } else if get(&cfg, "searchdomain") != Some(SEARCH_DOMAIN) {' \
   '    } else if get(&cfg, "searchdomain").is_some_and(|d| d != SEARCH_DOMAIN) { // knife' \
   s10_to_s13_and_h15_the_site_is_masked
# site verify S13: an IPv6 address on the CT is not seen
ck verify.rs '    } else if net0(&cfg, "ip6").is_some() || net0(&cfg, "gw6").is_some() {' \
   '    } else if net0(&cfg, "ip6").is_some() && net0(&cfg, "gw6").is_some() { // knife' \
   s10_to_s13_and_h15_the_site_is_masked
# site verify: cpulimit 0 is an ordinary number again (c477a, seg. 3 M1)
ck verify.rs '        .filter(|v| *v != Some(0));' \
   '        .filter(|v| v.is_some()); // knife' \
   review_segment_3
# host lock: any two different strings mean "another boot" (1ced7, seg. 4 M1)
ck op.rs '        Some(h) if valid_boot_id(&h.boot_id) && valid_boot_id(boot_id) && h.boot_id != boot_id => {' \
   '        Some(h) if h.boot_id != boot_id => { // knife' \
   slow_is_not_dead
# approval: a spent nonce is good for any operation (ebb36, seg. 4 M1)
ck op.rs '    } else if used_by(&approval.nonce).is_some_and(|other| other != op) {' \
   '    } else if used_by(&approval.nonce).is_some_and(|other| other != op && op.is_empty()) { // knife' \
   an_approval_covers_one_plan_for_one_operation
# quarantine: an old record is reused whatever the host looks like now (1ced7, seg. 5 M1)
ck quarantine.rs '            if r.prev_firewall != ct_fw' \
   '            if r.prev_firewall.is_empty()' \
   a_stale_record_is_not_reused
# quarantine: existing connections are left alone (33e28 K1)
ck quarantine.rs '    actions.push(cut.clone());' \
   '    let _ = cut.clone(); // knife: no drop' \
   plan_order_is_record_egress_connections_route_verify
# quarantine: no second drop after the route (33e28, seg. 5 M1)
ck quarantine.rs '    actions.push(cut);' \
   '    let _ = cut; // knife: one drop only' \
   review_segment_5
# quarantine: a CT that had no firewall file gets "no firewall" back (33e28, seg. 5 M2)
ck quarantine.rs '    let firewall_back = if record.prev_firewall.is_empty() {' \
   '    let firewall_back = if record.prev_firewall.is_empty() && false {' \
   review_segment_5
# quarantine: comment lines count as rules again (33e28, seg. 5 L1)
ck quarantine.rs "        .filter(|l| !l.is_empty() && !l.starts_with('#'))" \
   '        .filter(|l| !l.is_empty())' \
   review_segment_5
# quarantine guard: a record on disk no longer counts (33e28, seg. 5 r2 N)
ck quarantine.rs '    if record.is_some() || is_quarantined(ct_fw) {' \
   '    if record.is_some() && is_quarantined(ct_fw) { // knife' \
   review_segment_5
# quarantine: the record's edge block may hold more than this site's block (33e28 N)
ck quarantine.rs "        return refuse(\"the record's edge block is not exactly this site's block\");" \
   '        // knife: any block goes' \
   review_segment_5
# quarantine: a stale record is archived whatever the firewall is now (1ced7 L)
ck quarantine.rs '    if ct_fw != restored_firewall {' \
   '    if ct_fw.is_empty() && ct_fw != restored_firewall { // knife' \
   a_stale_record_can_be_archived_when_the_host_is_fully_restored
# quarantine: a stale record is archived whatever the journal says (1ced7, #625 seg. 2 M1)
ck quarantine.rs '        None => return refuse("no journal for the quarantine this record belongs to"),' \
   '        None => {} // knife' \
   a_stale_record_can_be_archived_when_the_host_is_fully_restored
# edge block: the live block stops stripping Server / Via / X-Powered-By (E6 C1, S3)
ck site.rs 'import ez2_whitelabel\n\theader {{\n\t\t-Server\n\t\t-Via\n\t\t-X-Powered-By\n\t}}\n\tencode zstd gzip' \
   'import ez2_whitelabel\n\tencode zstd gzip' \
   a_site_block_strips_server_headers
# edge block: the quarantine page stops stripping them (E6 C1, S3)
ck quarantine.rs '\t# ez2:quarantined\n\theader {{\n\t\t-Server\n\t\t-Via\n\t\t-X-Powered-By\n\t}}\n' \
   '\t# ez2:quarantined\n' \
   a_quarantined_block_strips_them_too
# provision: escape sequences reach the approver's terminal (ebb36, #625 seg. 5 L1)
ck provision.rs '        let unprintable = text.chars().any(char::is_control);' \
   '        let unprintable = false; // knife' \
   upward_paths_and_control_characters
# site verify: a CT nobody recorded goes unnoticed (c477a's list, H13)
ck verify.rs '        .any(|c| !record_cts.contains(c) && !platform.contains(c))' \
   '        .any(|c| !record_cts.contains(c) && !platform.contains(c) && c == &0)' \
   h13_every_ct_is_accounted_for
# site verify: a stale host-health file is fine (H01)
ck verify.rs '        Some(t) if now.saturating_sub(t) > 900 => Outcome::Red("stale"),' \
   '        Some(t) if now.saturating_sub(t) > 900 => Outcome::Green, // knife' \
   h01_h06_h07_h10_h12
# edge domain: an AAAA record no longer blocks a domain (4.4.1 6.2)
ck edge.rs '        out.push(NotReady::HasAaaa);' \
   '        // knife: AAAA ignored' \
   preflight_reads_a_aaaa_and_caa
# edge domain: a name already on another site is added again
ck edge.rs '            refuse("already on another site", name)' \
   '            Ok(alloc::vec![]) // knife' \
   add_refusals
# backup check: a CT missing from the off-site backup goes unnoticed (4.4.1 10)
ck backup.rs '        } else if missing {' \
   '        } else if missing && must_have.len() > 99 { // knife' \
   b02_every_ct_and_b03_config
# backup check: an unencrypted object off-site is fine
ck backup.rs '        if plain {' \
   '        if plain && newest.is_empty() { // knife' \
   b04_nothing_unencrypted_leaves_the_host
# backup before an update (E10): a stale backup lets the update through
ck fresh.rs '    matches!(f, Fresh::Fresh { .. })' \
   '    matches!(f, Fresh::Fresh { .. } | Fresh::Stale { .. }) // knife' \
   an_old_backup_does_not
# backup before an update: another CT's dump counts for this site
ck fresh.rs '        if dump_ct(f.name) != Some(ct) {' \
   '        if dump_ct(f.name).is_none_or(|c| c == 0 && c == ct) { // knife' \
   another_sites_backup_does_not_count
# backup before an update: a folder or a link with a dump's name counts
ck fresh.rs '        if !f.is_file {' '        if !f.is_file && f.name.is_empty() { // knife' \
   a_dump_still_being_written_or_empty_does_not_count
# backup before an update: an empty file counts as a backup
ck fresh.rs '        if size == 0 {' '        if size == 0 && mtime == 0 { // knife' \
   a_dump_still_being_written_or_empty_does_not_count
# backup before an update: a dump from the future is trusted
ck fresh.rs '        if mtime > now.saturating_add(CLOCK_SKEW_SECS) {' \
   '        if mtime > now.saturating_add(CLOCK_SKEW_SECS) && false { // knife' \
   a_dump_from_the_future_is_unknown
# backup before an update: an unreadable dump is skipped instead of spoiling the answer
ck fresh.rs '            return Fresh::Unknown("dump_unreadable");' '            continue; // knife' \
   what_cannot_be_read_is_unknown_never_allowed
# backup before an update: a folder that cannot be listed reads as "no backup"
ck fresh.rs '        return Fresh::Unknown("dump_folder_unreadable");' '        return Fresh::NoBackup; // knife' \
   what_cannot_be_read_is_unknown_never_allowed
# backup before an update: the oldest dump is taken instead of the newest
ck fresh.rs '        newest = Some(newest.map_or(mtime, |n| n.max(mtime)));' \
   '        newest = Some(newest.map_or(mtime, |n| n.min(mtime))); // knife' \
   a_backup_within_the_window_lets_the_update_through
# backup before an update: the window is doubled
ck fresh.rs '    if age <= max_secs {' '    if age <= max_secs.saturating_mul(2) { // knife' \
   an_old_backup_does_not
# backup before an update: a window of 0 minutes is accepted
ck fresh.rs '    (1..=MAX_MAX_MINUTES).contains(&minutes)' '    (0..=MAX_MAX_MINUTES).contains(&minutes) // knife' \
   the_window_is_one_minute_to_one_week
# backup before an update: a window longer than a week is accepted
ck fresh.rs 'pub const MAX_MAX_MINUTES: u32 = 10_080;' 'pub const MAX_MAX_MINUTES: u32 = 10_081; // knife' \
   the_window_is_one_minute_to_one_week
# backup before an update: the default is not a week (Andy 11-11 20:13)
ck fresh.rs 'pub const DEFAULT_MAX_MINUTES: u32 = 10_080;' 'pub const DEFAULT_MAX_MINUTES: u32 = 30; // knife' \
   the_window_is_one_minute_to_one_week
# backup before an update: a CT number with a sign is read as a CT
ck fresh.rs '    if ct.is_empty() || !ct.bytes().all(|c| c.is_ascii_digit()) {' \
   '    if ct.is_empty() { // knife' \
   only_finished_dumps_of_the_ct_are_named
# backup before an update: a malformed time stamp is read as a dump
ck fresh.rs '    if !stamp_ok {' '    if !stamp_ok && stamp.is_empty() { // knife' \
   only_finished_dumps_of_the_ct_are_named
# backup before an update: a dump left by an earlier site on the same CT number counts (ebb36 L1)
ck fresh.rs '        if mtime < created {' '        if mtime < created && created == 0 { // knife' \
   a_dump_older_than_the_site_was_left_by_an_earlier_one
# backup before an update: a site whose creation time cannot be read reads as "no backup"
ck fresh.rs '        return Fresh::Unknown("site_created_unreadable");' '        return Fresh::NoBackup; // knife' \
   a_dump_older_than_the_site_was_left_by_an_earlier_one
# backup before an update: an hour 24 or a minute 60 in the creation time is read
ck fresh.rs '    if h > 23 || m > 59 || sec > 59 {' '    if h > 24 || m > 60 || sec > 60 { // knife' \
   the_creation_time_is_read_as_the_shell_writes_it
# backup before an update: a site moved by restore --switch counts from its creation (ebb36 incr. L1)
ck fresh.rs '    Ok(ct_since.map_or(created, |s| s.max(created)))' '    Ok(ct_since.map_or(created, |_| created)) // knife' \
   a_site_moved_to_another_ct_counts_from_the_move
# backup before an update: an unreadable move time is taken as none
ck fresh.rs '                .ok_or("ct_since_unreadable")?,' '                .unwrap_or(0),' \
   a_site_moved_to_another_ct_counts_from_the_move
# quarantine: an unfinished quarantine is archived all the same (1ced7, #625 seg. 2 r2)
ck quarantine.rs '        Some(crate::op::State::Verified) => {}' \
   '        Some(_) if true => {} // knife' \
   a_stale_record_can_be_archived_when_the_host_is_fully_restored
# provision: a refused placeholder is echoed raw to the terminal (ebb36, #625 seg. 5 r2)
ck provision.rs '            return Err(format!("{name:?} between {{{{ }}}} is not a variable name"));' \
   '            return Err(format!("{name} is not a variable name"));' \
   a_refused_placeholder_is_quoted_in_the_finding
# site svc: a quarantined site can be started (4.4.1 1.3)
ck svc.rs '    if action != Svc::Stop {' \
   '    if action != Svc::Stop && action != Svc::Start { // knife' \
   a_quarantined_site_is_not_started_and_stopping_it_says_what_is_lost
# site logs: values after secret-looking keys are shown
ck svc.rs '                if end > 0 {' \
   '                if end > usize::MAX - 1 { // knife' \
   secrets_do_not_leave_with_the_logs
# site logs: only the first cookie of a header is masked, a Digest credential not at all (33e28, #631 seg. 5 M2)
ck svc.rs '                let whole = key == "cookie" || (key == "authorization" && scheme == 0);' \
   '                let whole = false && key == "cookie"; // knife' \
   secrets_do_not_leave_with_the_logs
# site logs wp: the WordPress log is read from somewhere else than the site's own fixed path (E10)
ck svc.rs '    alloc::format!("/var/www/{}/wp-content/debug.log", spec.code)' \
   '    alloc::format!("/var/www/{}/debug.log", spec.code) // knife' \
   the_wp_log_is_read_as_the_sites_user_from_a_fixed_path
# site logs wp: the WordPress log is read as another user than the site's (E10)
ck svc.rs '"sudo", "-u", &spec.code,' '"sudo", "-u", &spec.ct.to_string(),' \
   the_wp_log_is_read_as_the_sites_user_from_a_fixed_path
# site logs wp: a site without the log file is an error, not an empty log (E10)
ck svc.rs 'pub const WP_LOG_SH: &str = "f=\"$1\"; [ -f \"$f\" ] || exit 0; exec tail -n \"$2\" -- \"$f\"";' \
   'pub const WP_LOG_SH: &str = "f=\"$1\"; exec tail -n \"$2\" -- \"$f\""; // knife' \
   the_wp_log_is_read_as_the_sites_user_from_a_fixed_path
# site logs wp: the WordPress log is read through the journal (E10)
ck svc.rs '            LogSource::Wp => None,' '            LogSource::Wp => Some("nginx.service"), // knife' \
   the_wp_log_is_read_as_the_sites_user_from_a_fixed_path
# site logs: a host word is found inside a longer one (E6)
ck svc.rs '                at_start && lower[i..].starts_with(w.as_str()) && !word_char(i + w.len())' \
   '                at_start && lower[i..].starts_with(w.as_str()) // knife' \
   host_words_are_hidden_whole_and_counted
# site logs: a host word is found at the end of a longer one (E6)
ck svc.rs '            let at_start = i == 0 || !word_char(i - 1);' \
   '            let at_start = i == 0 || !word_char(i - 1) || i > 0; // knife' \
   host_words_are_hidden_whole_and_counted
# site logs: a host word is matched with its case (E6)
ck svc.rs '        let lower = line.to_ascii_lowercase();' '        let lower = line.to_owned(); // knife' \
   host_words_are_hidden_whole_and_counted
# site logs: two-letter words of the host's list cut ordinary text apart (E6)
ck svc.rs '            if w.len() >= HIDE_WORD_MIN && !words.contains(&w) {' \
   '            if w.len() >= 2 && !words.contains(&w) { // knife' \
   host_words_are_hidden_whole_and_counted
# site logs: a comment line of the host's list is taken as a word (E6)
ck svc.rs ".filter(|w| !w.starts_with('#'));" '.filter(|w| !w.is_empty()); // knife' \
   host_words_are_hidden_whole_and_counted
# site logs: "CT 112" is shown (E6)
ck svc.rs '            alloc::format!("ct {ct}"),' '            alloc::format!("ct-{ct}"), // knife' \
   host_words_are_hidden_whole_and_counted
# site logs: only the start of a host path is hidden, the rest shows (E6)
ck svc.rs '                i += p.len() + end;' '                i += p.len() + end.min(0); // knife' \
   host_paths_are_hidden_to_the_next_blank
# site logs: a host path is not hidden at all (E6)
ck svc.rs '            if let Some(p) = HOST_PATHS.iter().find(|p| lower[i..].starts_with(**p)) {' \
   '            if let Some(p) = HOST_PATHS.iter().find(|p| lower[i..].starts_with(**p) && i > usize::MAX - 1) { // knife' \
   host_paths_are_hidden_to_the_next_blank
# site logs: the line is cut before the host is hidden, half a host name goes out (33e28 L1)
ck svc.rs '    let (hidden, _) = hide.hide(&mask_line(line));' \
   '    let (hidden, _) = hide.hide(&mask_line(clip_line(line))); // knife' \
   a_host_word_across_the_cut_is_hidden_before_the_cut
# site logs: the domain of the host's full name is not hidden (33e28 L3)
ck svc.rs '        for n in core::iter::once(name).chain(first).chain(tails) {' \
   '        for n in core::iter::once(name).chain(first).chain(tails.filter(|_| false)) { // knife' \
   the_hosts_names_are_its_short_and_full_names_and_their_domain
# site logs: only the first tail of the host's full name is hidden, not the provider's two labels (33e28 r2 L1)
ck svc.rs "            .filter(|t| t.contains('.'));" "            .filter(|t| t.contains('.')).take(1); // knife" \
   the_hosts_names_are_its_short_and_full_names_and_their_domain
# site logs: "CT-112" is shown (33e28 N1)
ck svc.rs '            alloc::format!("ct-{ct}"),' '            alloc::format!("ct_{ct}"), // knife' \
   the_hosts_names_are_its_short_and_full_names_and_their_domain
# site logs: a CT's volume name is shown (33e28 N1)
ck svc.rs '            alloc::format!("vm-{ct}-disk"),' '            alloc::format!("vm-{ct}-diskx"), // knife' \
   the_hosts_names_are_its_short_and_full_names_and_their_domain
# customer entry: characters a shell reads get as far as a word (Andy 11-10: customers operate their own site)
ck customer.rs "            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-' || c == b' ')" \
   '            .all(|c| c != 0)' \
   everything_else_is_refused_whole
# customer status: a code nobody named is shown to the customer as it is (c477a 11-10)
ck customer.rs '            (_, _) => None,' \
   '            (_, Outcome::Red(c)) => Some(("red", Some(c))), (_, _) => None, // knife' \
   a_customer_is_not_told_what_is_wrong_on_our_side
# customer status: what is wrong on our side does not show at all
ck customer.rs '            None => provider = provider.max(rank(r.outcome)),' \
   '            None => provider = provider.max(rank(r.outcome) * 0), // knife' \
   a_customer_is_not_told_what_is_wrong_on_our_side
# roles: an editor may restart a site (the table is Ez2.PAGE's: live changes are the publisher's)
ck customer.rs '            Operation::Publish => matches!(self, Owner | Admin | Publisher),' \
   '            Operation::Publish => matches!(self, Owner | Admin | Publisher | Editor), // knife' \
   a_role_gives_what_the_table_says_and_no_more
# roles: restarting a site counts as editing it (so an editor could)
ck customer.rs '            Request::Restart => Operation::Publish,' \
   '            Request::Restart => Operation::Edit, // knife' \
   a_role_gives_what_the_table_says_and_no_more
# customer key: a site name that is not a site code gets as far as the host records
ck customer.rs '            if !crate::site::valid_code(site) {' \
   '            if site.is_empty() { // knife' \
   a_customer_key_names_the_site_and_nothing_else
# customer entry: a request may carry extra words (another site, say)
ck customer.rs '        if rest.is_empty() {' \
   '        if rest.len() < 9 { // knife' \
   everything_else_is_refused_whole
# customer key: `site create` takes more than its one name (a code or a size the customer chose) (Andy 11-11)
ck customer.rs '                    (Some("blank"), None) => true,' \
   '                    (Some(_), _) => true, // knife' \
   a_customer_key_opens_a_site_and_names_a_domain
# customer key: a new site's address is not checked to be a name (ebb36, #652 N1)
ck customer.rs '                let name = name.filter(|n| crate::edge::valid_domain(n));' \
   '                let name = name.filter(|n| !n.is_empty()); // knife' \
   a_customer_key_opens_a_site_and_names_a_domain
# customer key: a word of the request line is taken as a site's code (one table for parser, records and roster)
ck site.rs '    !RESERVED_CODES.contains(&code)' \
   '    !RESERVED_CODES.is_empty() // knife' \
   a_customer_key_opens_a_site_and_names_a_domain
# customer key: a domain that is not a name (an address, one label) gets as far as the host
ck customer.rs '                if !crate::edge::valid_domain(name) {' \
   '                if name.is_empty() { // knife' \
   a_customer_key_opens_a_site_and_names_a_domain
# roles: a publisher may open a site (it uses up the plan: the owner's alone)
ck customer.rs '            Operation::Archive | Operation::Create => matches!(self, Owner | Admin),' \
   '            Operation::Archive | Operation::Create => matches!(self, Owner | Admin | Publisher), // knife' \
   a_customer_key_opens_a_site_and_names_a_domain
# customer side: binding a domain is sent before the host can do it
ck customer.rs '            OwnerRequest::Domain { .. } => false,' \
   '            OwnerRequest::Domain { .. } => true, // knife' \
   a_customer_key_opens_a_site_and_names_a_domain
