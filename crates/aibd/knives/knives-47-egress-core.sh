#!/usr/bin/env bash
# EG1–EG7 (3e53b 11-23; DESIGN-sandboxnet-3e53b-1123 r6 §3 step 0–5, §15.1, §15.4 N3 / N5 / N11 / N32;
# f163c REVIEW-sandboxnet r6 passed): the shapes the egress proxy takes and the verdict it gives.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
eg() { python3 -I scripts/knife.py crates/aibd/src/egress.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aibd --locked --lib "$3"; }
# EG1 a request from a web page (Origin header) is taken
eg '            "origin" => return Err("origin_header"),' '            "origin" => {} // knife' egress::tests::only_connect_absolute_form_and_socks_connect_are_taken
# EG2 a number in another spelling goes to the resolver
eg '    if labels.iter().all(|l| numeric(l)) || labels.last().is_some_and(|l| numeric(l)) {' '    if labels.is_empty() && labels.iter().all(|l| numeric(l)) { // knife' egress::tests::numbers_in_other_spellings_are_refused_not_resolved
# EG3 this computer passes
eg '        (Class::Own(_), _) => Verdict::Refuse("this_computer"),' '        (Class::Own(_), ip) => Verdict::Pass(ip), // knife' egress::tests::this_computer_is_refused_private_asks_and_public_passes
# EG4 only the first resolved address is judged
eg '        .min_by_key(|(c, _)| *c)' '        .take(1).min_by_key(|(c, _)| *c) // knife' egress::tests::this_computer_is_refused_private_asks_and_public_passes
# EG5 SOCKS5 BIND / UDP ASSOCIATE taken
eg '    if b[1] != 1 {' '    if b[1] > 3 { // knife' egress::tests::socks5_takes_connect_only_and_every_address_type_is_checked
# EG6 Content-Length beside chunked taken (request smuggling)
eg '    if length && chunked {' '    if length && chunked && false { // knife' egress::tests::only_connect_absolute_form_and_socks_connect_are_taken
# EG7 a private address passes without being on the seat's list
eg '                .all(|a| listed(*a, port))' '                .all(|a| listed(*a, port) || port != 0) // knife' egress::tests::this_computer_is_refused_private_asks_and_public_passes
# EG8 (16.2, Andy「5能上網」) a Windows work seat is said to be enforced
eg '        ("claude", "work") => "hint",' '        ("claude", "work") => "enforced", // knife' egress::tests::a_mode_says_enforced_only_where_the_sandbox_holds_and_the_proxy_is_there
# EG9 a mode is said to be enforced before the proxy is in the build
eg '        ("claude", "work") if !proxy => "off",' '        ("claude", "work") if !proxy && os == "none" => "off", // knife' egress::tests::a_mode_says_enforced_only_where_the_sandbox_holds_and_the_proxy_is_there
# EG10 a dgrs seat is said to be enforced
eg '        (_, "dgrs") => "hint",' '        (_, "dgrs") => "enforced", // knife' egress::tests::a_mode_says_enforced_only_where_the_sandbox_holds_and_the_proxy_is_there
# EG11 (A6d §18.2) the reserved range is not counted as aibd's
eg '    if AIBD_RESERVED.contains(&port) || own_binds.contains(&port) || preview_ports.contains(&port) {' '    if own_binds.contains(&port) || preview_ports.contains(&port) || AIBD_RESERVED.is_empty() { // knife' egress::tests::aibds_ports_are_the_reserved_range_its_own_and_the_previews_and_nothing_else_is
# EG12 the reserved range stops short of the previews
eg 'pub const AIBD_RESERVED: std::ops::RangeInclusive<u16> = 47770..=47799;' 'pub const AIBD_RESERVED: std::ops::RangeInclusive<u16> = 47770..=47780; // knife' egress::tests::aibds_ports_are_the_reserved_range_its_own_and_the_previews_and_nothing_else_is
