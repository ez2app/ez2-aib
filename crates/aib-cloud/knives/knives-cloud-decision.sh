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
# abandon DNS: a record without this site's mark can be deleted
ck sitedns.rs '        && r.comment == dns_marker(code))' \
   '        && !r.comment.is_empty()) // knife' \
   only_a_listed_record_with_this_sites_mark_can_be_deleted
# abandon DNS: a mail or text record can be deleted
ck sitedns.rs '        && DNS_ADDRESS_TYPES.contains(&r.kind.as_str())' \
   '        && (DNS_ADDRESS_TYPES.contains(&r.kind.as_str()) || true) // knife' \
   only_a_listed_record_with_this_sites_mark_can_be_deleted
# abandon DNS: unmarked records are on the list to remove
ck sitedns.rs '        .filter(|r| r.comment == mark)' \
   '        .filter(|r| r.comment == mark || true) // knife' \
   only_marked_address_records_under_the_names_are_removed
# abandon DNS: marked records under other names are on the list to remove
ck sitedns.rs '        .filter(|r| names.iter().any(|n| same_name(&r.name, n)))' \
   '        .filter(|r| names.iter().any(|n| same_name(&r.name, n)) || true) // knife' \
   only_marked_address_records_under_the_names_are_removed
# verify S15 (E6 D1): a grey (DNS only) record under the site's names reads as proxied
ck sitedns.rs '        } else if here.iter().any(|r| !r.proxied) {' \
   '        } else if here.iter().any(|r| !r.proxied) && false { // knife' \
   a_grey_record_under_the_site_is_a_fail
# verify S15 (E6 D1): a zone listing not read whole reads as all proxied
ck sitedns.rs '        return Proxied::Unchecked(UNREADABLE);' \
   '        return Proxied::All; // knife' \
   an_unreadable_zone_is_unchecked_not_pass
# 80 / 443 only from Cloudflare (E6 C3, S4): an empty list goes through, as if it let everyone in
ck cfonly.rs '    if out.is_empty() {' \
   '    if out.is_empty() && false { // knife' \
   a_malformed_range_list_is_refused
# 80 / 443 only from Cloudflare (E6 C3, S4): 0.0.0.0/0 is not refused by name
ck cfonly.rs '        if line == "0.0.0.0/0" {' \
   '        if line == "0.0.0.0/0" && false { // knife' \
   a_malformed_range_list_is_refused
# 80 / 443 only from Cloudflare (E6 C3, S4): the accept rule loses its source set
ck cfonly.rs '    "IN ACCEPT -source +guest/ez2-cloudflare-v4 -p tcp -dport 80,443 -log nolog",' \
   '    "IN ACCEPT -p tcp -dport 80,443 -log nolog", // knife' \
   only_cloudflare_ranges_are_let_in
# 80 / 443 only from Cloudflare (E6 C3, S4): another rule before ours reads back as fine
ck cfonly.rs '    if rules.get(..2) != Some(&RULES[..]) {' \
   '    if rules.get(..2) != Some(&RULES[..]) && false { // knife' \
   only_cloudflare_ranges_are_let_in
# 80 / 443 only from Cloudflare (E6 C3, S4): an extra range on disk reads back as fine
ck cfonly.rs '            for h in have.iter().filter(|h| !want_s.contains(h)) {' \
   '            for h in have.iter().filter(|h| !want_s.contains(h) && false) { // knife' \
   a_read_back_set_must_match_exactly
# 80 / 443 only from Cloudflare (E6 C3, S4; c477a M1): a card of the edge without the firewall reads back as fine
ck cfonly.rs '        Outcome::Red("fw_not_on_card") => found.push("nic_firewall_off".to_string()),' \
   '        Outcome::Red("fw_not_on_card") => {} // knife' \
   every_card_of_the_edge_must_carry_the_firewall
# S18 (E10 6.1, 7b81e 11-13): cron stopped or failed is judged green
ck usercron.rs '        "inactive" | "failed" | "deactivating" => return Outcome::Red("user_cron_off"),' \
   '        "inactive" | "failed" | "deactivating" => {} // knife' \
   cron_not_there_off_or_stopped_is_red
# S18: an allow file that does not name the user lets it in
ck usercron.rs '        (File::Omits, _) => true,' '        (File::Omits, _) => false, // knife' \
   an_allow_file_decides_alone_when_it_is_there
# S18: the deny file is read though an allow file names the user
ck usercron.rs '        (File::Names, _) => false,' '        (File::Names, _) => seen.deny == File::Names, // knife' \
   an_allow_file_decides_alone_when_it_is_there
# S18: without an allow file, a deny file naming the user does not keep it out
ck usercron.rs '        (File::Absent, deny) => deny == File::Names,' '        (File::Absent, deny) => deny == File::Names && false, // knife' \
   without_an_allow_file_the_deny_file_decides
# S18 (c477a L1): a name with blanks around it in the deciding file is taken as the user
ck usercron.rs '            return Outcome::Unknown("user_cron_padded")' \
   '            return Outcome::Green // knife' \
   a_name_with_blanks_around_it_in_the_deciding_file_is_unknown
# S18: what was not read as expected is green
ck usercron.rs '        Some(None) => Outcome::Unknown("user_cron_unparsed"),' \
   '        Some(None) => Outcome::Green, // knife' \
   what_was_not_read_is_never_green
# S18: a line calling wp-cron.php over the network is not flagged
ck usercron.rs '        Some((_, self_url)) if self_url > 0 => Outcome::Warn("user_cron_self_url"),' \
   '        Some((_, self_url)) if self_url > 0 && false => Outcome::Warn("user_cron_self_url"), // knife' \
   a_line_calling_wp_cron_over_the_network_is_a_warning
# E10 7.6 cache flush (c477a L7: never a cache other sites share)
# CF1 a clear that names more than its hosts (purge_everything with them) is let through
ck sitedns.rs '    m.len() == 1' '    m.len() >= 1 // knife' \
   a_cache_clear_names_hosts_and_never_the_whole_zone
# CF2 a clear of any body (the whole zone, files, prefixes) is let through
ck sitedns.rs '        ("POST", ["", "zones", z, "purge_cache"], None, Some(b)) => valid_id(z) && purge_body_ok(b),' \
   '        ("POST", ["", "zones", z, "purge_cache"], None, _) => valid_id(z), // knife' \
   a_cache_clear_names_hosts_and_never_the_whole_zone
# CF3 a clear of more names than one call may carry is let through
ck sitedns.rs '        && names.len() <= PURGE_HOSTS_MAX' \
   '        && (names.len() <= PURGE_HOSTS_MAX || true) // knife' \
   a_cache_clear_names_hosts_and_never_the_whole_zone
# CF4 a name twice is let through
ck sitedns.rs '        && seen.len() == names.len()' \
   '        && (seen.len() == names.len() || true) // knife' \
   a_cache_clear_names_hosts_and_never_the_whole_zone
# CF5 a name of another zone goes into the clear
ck sitedns.rs '    let mine: Vec<&str> = names.iter().copied().filter(|n| in_zone(n, zone)).collect();' \
   '    let mine: Vec<&str> = names.iter().copied().filter(|n| in_zone(n, zone) || true).collect(); // knife' \
   a_cache_clear_body_keeps_to_the_zone
# CF6 a clear is the publisher's and not the editor's
ck customer.rs '            Request::PhpReload | Request::CacheFlush => Operation::Edit,' \
   '            Request::PhpReload => Operation::Edit, Request::CacheFlush => Operation::Publish, // knife' \
   a_role_gives_what_the_table_says_and_no_more
# SS1 the customer ssh cell probes a port other than 2000+ct
ck isolation.rs '        let port = site::customer_ssh_port(*ct).ok_or(Error::CtId(*ct))?;' \
   '        let port = site::customer_ssh_port(*ct + 1).ok_or(Error::CtId(*ct))?; // knife' \
   i6_a_site_with_customer_ssh_gets_one_public_open_cell_on_2000_plus_ct
# SS2 one source may expect the same target both open and blocked
ck isolation.rs '                && x.expect != c.expect' \
   '                && x.expect != c.expect && false // knife' \
   i6_a_site_with_customer_ssh_gets_one_public_open_cell_on_2000_plus_ct
