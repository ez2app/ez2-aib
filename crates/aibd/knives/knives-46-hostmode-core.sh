#!/usr/bin/env bash
# HM1–HM9 (3e53b 11-23; DESIGN-hostmode-3e53b-1123 r3 §2–§5 H6 / H7 / W1 / W2, 8b09a REVIEW-hostnet-pre
# r2 passed): which addresses aibd connects to for a seat, and the one pinned ssh command.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
A=host_addr::tests::a_host_back_on_this_computer_or_a_reserved_one_is_refused
M=host_addr::tests::one_refused_address_among_several_refuses_the_name
S=host_ssh::tests::the_ssh_command_is_pinned_and_goes_to_the_checked_address
P=host_ssh::tests::the_ssh_program_is_the_systems_and_never_one_on_path
ha() { python3 -I scripts/knife.py crates/aibd/src/host_addr.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aibd --locked --lib "$3"; }
hs() { python3 -I scripts/knife.py crates/aibd/src/host_ssh.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aibd --locked --lib "$3"; }
# HM1 an IPv6 address carrying 127.0.0.1 is judged by its outside only
ha '                w = w.min(v4(inner));' '                w = w.min(Class::Public); let _ = inner; // knife' "$A"
# HM2 this computer's own interface addresses are not looked at
ha '        worst = worst.min(Class::Own("this_computer"));' '        worst = worst.min(Class::Public); // knife' "$A"
# HM3 loopback IPv4 goes through
ha '    if o[0] == 127 {' '    if o[0] == 127 && false { // knife' "$A"
# HM4 only the first resolved address is checked
ha '    ips.iter().try_for_each(|ip| host_ok(*ip, own))' '    ips.iter().take(1).try_for_each(|ip| host_ok(*ip, own)) // knife' "$M"
# HM5 Teredo's client is read without inverting
ha '        out.push(Ipv4Addr::new(!b[12], !b[13], !b[14], !b[15]));' '        out.push(Ipv4Addr::new(b[12], b[13], b[14], b[15])); // knife' "$A"
# HM6 ssh may take a proxy from somewhere
hs '        "ProxyCommand=none".into(),' '        "Compression=no".into(), // knife' "$S"
# HM7 the stored name, not the checked IP, is connected to
hs '    a.push(t.ip.to_string());' '    a.push(t.id.to_owned()); // knife' "$S"
# HM8 a user that reads as an option is passed on
hs '    if !user_ok(t.user) {' '    if !user_ok(t.user) && false { // knife' "$S"
# HM9 the ssh program is taken without being there
hs '    if p.is_file() {' '    if true { // knife' "$P"
