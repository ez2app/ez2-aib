#!/usr/bin/env bash
# EG1–EG7 (3e53b 11-23; DESIGN-sandboxnet-3e53b-1123 r6 §3 step 0–5, §15.1, §15.4 N3 / N5 / N11 / N32;
# f163c REVIEW-sandboxnet r6 passed): the shapes the egress proxy takes and the verdict it gives.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
N=egress::tests::numbers_in_other_spellings_are_refused_not_resolved
H=egress::tests::only_connect_absolute_form_and_socks_connect_are_taken
K=egress::tests::socks5_takes_connect_only_and_every_address_type_is_checked
V=egress::tests::this_computer_is_refused_private_asks_and_public_passes
eg() { python3 -I scripts/knife.py crates/aibd/src/egress.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aibd --locked --lib "$3"; }
# EG1 a request from a web page (Origin header) is taken
eg '            "origin" => return Err("origin_header"),' '            "origin" => {} // knife' "$H"
# EG2 a number in another spelling goes to the resolver
eg '    if labels.iter().all(|l| numeric(l)) || labels.last().is_some_and(|l| numeric(l)) {' '    if labels.is_empty() && labels.iter().all(|l| numeric(l)) { // knife' "$N"
# EG3 this computer passes
eg '        (Class::Own(_), _) => Verdict::Refuse("this_computer"),' '        (Class::Own(_), ip) => Verdict::Pass(ip), // knife' "$V"
# EG4 only the first resolved address is judged
eg '        .min_by_key(|(c, _)| *c)' '        .take(1).min_by_key(|(c, _)| *c) // knife' "$V"
# EG5 SOCKS5 BIND / UDP ASSOCIATE taken
eg '    if b[1] != 1 {' '    if b[1] > 3 { // knife' "$K"
# EG6 Content-Length beside chunked taken (request smuggling)
eg '    if length && chunked {' '    if length && chunked && false { // knife' "$H"
# EG7 a private address passes without being on the seat's list
eg '                .all(|a| listed(*a, port))' '                .all(|a| listed(*a, port) || port != 0) // knife' "$V"
