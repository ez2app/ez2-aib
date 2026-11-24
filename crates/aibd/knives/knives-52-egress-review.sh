#!/usr/bin/env bash
# EV1–EV20 (f163c REVIEW-egress-3e53b-f163c-pre-2cf69f1a L1–L6): request heads read one way only,
# link-local never carded, Tailscale's resolver is this computer, the whole-machine ports of
# Windows and VNC (L6: codex work is not offered off a Mac, nothing to change); and the knives L5 found missing (ssh's pinned
# options, the test machines' list, a name that resolves back to this computer).
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
kn() { python3 -I scripts/knife.py "crates/aibd/src/$1" "$2" "$3" --expect-fail "$4" \
  -- cargo test -p aibd --locked --lib "$4"; }
# EV1 (L1) a lone CR or LF is taken as a line end
kn egress.rs '    if head.replace("\r\n", "").contains(['"'"'\r'"'"', '"'"'\n'"'"']) {' '    if head.replace("\r\n", "").contains(['"'"'\r'"'"', '"'"'\n'"'"']) && head.is_empty() { // knife' egress::tests::only_connect_absolute_form_and_socks_connect_are_taken
# EV2 (L1) a folded line is read as a header
kn egress.rs '        if l.starts_with(['"'"' '"'"', '"'"'\t'"'"']) {' '        if l.starts_with('"'"'\u{0}'"'"') { // knife' egress::tests::only_connect_absolute_form_and_socks_connect_are_taken
# EV3 (L1) Content-Length twice
kn egress.rs '            "content-length" if length => return Err("header_twice"),' '            "content-length" if length && method.is_empty() => return Err("header_twice"), // knife' egress::tests::only_connect_absolute_form_and_socks_connect_are_taken
# EV4 (L1) Transfer-Encoding twice
kn egress.rs '            "transfer-encoding" if chunked => return Err("header_twice"),' '            "transfer-encoding" if chunked && method.is_empty() => return Err("header_twice"), // knife' egress::tests::only_connect_absolute_form_and_socks_connect_are_taken
# EV5 (L2) link-local gets the high-risk card
kn proxy.rs '                .any(|ip| classify(*ip, here.own) == Class::Own("link_local"))' '                .any(|ip| classify(*ip, here.own) == Class::Own("link_locall")) // knife' proxy::tests::a_connection_gets_the_address_judged_a_card_or_a_refusal
# EV6 (N41) a name resolving back to aibd gets a card
kn proxy.rs '                return Conn::Refuse(why);' '                let _ = why; // knife' proxy::tests::a_connection_gets_the_address_judged_a_card_or_a_refusal
# EV7 (L3) Tailscale's resolver is an ordinary tailnet address
kn host_addr.rs '    if o == [100, 100, 100, 100] {' '    if o == [100, 100, 100, 99] { // knife' host_addr::tests::a_host_back_on_this_computer_or_a_reserved_one_is_refused
# EV8 (L3) its IPv6 form
kn host_addr.rs '    if s == [0xfd7a, 0x115c, 0xa1e0, 0, 0, 0, 0, 0x53] {' '    if s == [0xfd7a, 0x115c, 0xa1e0, 0, 0, 0, 0, 0x54] { // knife' host_addr::tests::a_host_back_on_this_computer_or_a_reserved_one_is_refused
# EV9 (L4) Windows remote desktop gets a card
kn egress.rs '    3389..=3389,' '    3388..=3388, // knife' egress::tests::aibds_ports_are_the_reserved_range_its_own_and_the_previews_and_nothing_else_is
# EV10 (L4) Windows remote shell over TLS gets a card
kn egress.rs '    5985..=5986,' '    5985..=5985, // knife' egress::tests::aibds_ports_are_the_reserved_range_its_own_and_the_previews_and_nothing_else_is
# EV11 (L4) VNC's second display gets a card
kn egress.rs '    5900..=5909,' '    5900..=5900, // knife' egress::tests::aibds_ports_are_the_reserved_range_its_own_and_the_previews_and_nothing_else_is
# EV13 (L5) ssh reads the user's config
kn host_ssh.rs '    let mut a: Vec<String> = ["-F", "none", "-T", "-e", "none"]' '    let mut a: Vec<String> = ["-T", "-T", "-T", "-e", "none"] // knife' host_ssh::tests::the_ssh_command_is_pinned_and_goes_to_the_checked_address
# EV14 (L5) a new host key is taken
kn host_ssh.rs '        "StrictHostKeyChecking=yes".into(),' '        "StrictHostKeyChecking=accept-new".into(), // knife' host_ssh::tests::the_ssh_command_is_pinned_and_goes_to_the_checked_address
# EV15 (L5) the agent goes along
kn host_ssh.rs '        "ForwardAgent=no".into(),' '        "ForwardAgent=yes".into(), // knife' host_ssh::tests::the_ssh_command_is_pinned_and_goes_to_the_checked_address
# EV16 (L5) forwardings from a config are kept
kn host_ssh.rs '        "ClearAllForwardings=yes".into(),' '        "ClearAllForwardings=no".into(), // knife' host_ssh::tests::the_ssh_command_is_pinned_and_goes_to_the_checked_address
# EV17 (L5, HM12) this computer is offered as a test machine
kn managed_test.rs '        Class::Own(_) => Some("this_computer"),' '        Class::Own(_) => None, // knife' managed_test::tests::candidates_say_name_os_and_why_not_never_where
# EV18 (L5, HM14) the candidates say where
kn managed_test.rs '                "os": h.os,' '                "os": h.os, "at": h.hostname, // knife' managed_test::tests::candidates_say_name_os_and_why_not_never_where
# EV19 (L5, HM15) a refused address is added anyway
kn managed_test.rs '        Some(_) => Err("host_addr_refused"),' '        Some(_) => Ok(h), // knife' managed_test::tests::an_add_names_a_trusted_machine_and_nothing_else
# EV20 (L5, HM15) a name is matched loosely
kn managed_test.rs '        .find(|h| h.name == name)' '        .find(|h| h.name.trim() == name.trim()) // knife' managed_test::tests::an_add_names_a_trusted_machine_and_nothing_else
# EV21 (c2f0b E2) the card asks again for a private address already on the list
kn egress.rs '                .find(|a| classify(**a, own) == Class::Private && !listed(**a, port))' '                .find(|a| classify(**a, own) == Class::Private && (!listed(**a, port) || true)) // knife' egress::tests::this_computer_is_refused_private_asks_and_public_passes
# EV22 (c2f0b E5) no interface address read: this computer's LAN address passes as private
kn egress.rs '    if own.is_empty() {' '    if own.is_empty() && ips.is_empty() { // knife' egress::tests::this_computer_is_refused_private_asks_and_public_passes
# EV23 (c2f0b E5) the same for a test machine
kn managed_test.rs '    if own.is_empty() {' '    if own.is_empty() && h.port == 0 { // knife' managed_test::tests::an_add_names_a_trusted_machine_and_nothing_else
# EV24 (c2f0b E6) SMB back into this computer gets a card
kn egress.rs '    445..=445,' '    446..=446, // knife' egress::tests::aibds_ports_are_the_reserved_range_its_own_and_the_previews_and_nothing_else_is
# EV25 (c2f0b E6) a Jupyter server on this computer gets a card
kn egress.rs '    8888..=8888,' '    8889..=8889, // knife' egress::tests::aibds_ports_are_the_reserved_range_its_own_and_the_previews_and_nothing_else_is
# EV26 (c2f0b E6) the kubelet gets a card
kn egress.rs '    10250..=10250,' '    10251..=10251, // knife' egress::tests::aibds_ports_are_the_reserved_range_its_own_and_the_previews_and_nothing_else_is
# EV27 (DEEP2 T1) this computer's ssh is looked for somewhere else than the system's place
kn host_ssh.rs '        Os::Mac => ssh_program(Os::Mac, Path::new("/")),' '        Os::Mac => ssh_program(Os::Mac, Path::new("/usr/local")), // knife' host_ssh::tests::this_computers_ssh_is_found_from_the_os_not_the_environment
