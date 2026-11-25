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
