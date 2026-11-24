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
