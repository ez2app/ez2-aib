#!/usr/bin/env bash
# NC1–NC5 (3e53b 11-23; DESIGN-sandboxnet r8 §4 §15.6, A6c first slice): the card's four longer
# yeses need the owner, a failed verification refuses, only 一直允許 writes the list, the list is
# exact ip:port, and taking one back closes that seat's tunnels to it.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
nc() { python3 -I scripts/knife.py crates/aibd/src/net_card.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
# NC1 15 分鐘 is taken without the owner
nc '        !matches!(self, Choice::Deny | Choice::Once)' '        !matches!(self, Choice::Deny | Choice::Once | Choice::Min15) // knife' net_card::tests::the_card_has_six_choices_and_four_need_the_owner
# NC2 a failed verification lets the yes through
nc '    if c.needs_owner() && owner != Owner::Yes {' '    if c.needs_owner() && owner == Owner::NotAsked { // knife' net_card::tests::a_yes_that_needs_the_owner_without_the_owner_refuses
# NC3 a yes other than 一直允許 is written to the list
nc '        yes.lasts == Lasts::Always && self.rows.insert(at)' '        (yes.lasts == Lasts::Always || true) && self.rows.insert(at) // knife' net_card::tests::only_always_writes_the_list_and_taking_back_closes_its_tunnels
# NC4 taking back closes tunnels to other addresses too
nc '        Some(open.iter().copied().filter(|o| *o == at).collect())' '        Some(open.iter().copied().filter(|o| *o == at || o.port() == 80).collect()) // knife' net_card::tests::only_always_writes_the_list_and_taking_back_closes_its_tunnels
# NC5 the list allows the address on any port
nc '        self.rows.contains(&SocketAddr::new(ip, port))' '        self.rows.iter().any(|r| r.ip() == ip && port > 0) // knife' net_card::tests::only_always_writes_the_list_and_taking_back_closes_its_tunnels
# NC6 (A6d §18.3) the high-risk card takes 到這一位關掉 / 一直允許
nc '    if !HIGH_CHOICES.contains(&c) {' '    if !HIGH_CHOICES.contains(&c) && c == Choice::Deny { // knife' net_card::tests::the_high_risk_card_has_four_choices_and_every_yes_needs_the_owner
# NC7 (N38) the high-risk 只這一次 is taken without the owner
nc '    if owner != Owner::Yes {' '    if owner == Owner::Failed { // knife' net_card::tests::the_high_risk_card_has_four_choices_and_every_yes_needs_the_owner
