#!/usr/bin/env bash
# PX1–PX12 (DESIGN-sandboxnet r10 §19, N42–N50): the proxy's deciding — ports from the reserved
# range only, the address judged is the one connected, grants end, tunnels close.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
px() { python3 -I scripts/knife.py crates/aibd/src/proxy.rs "$1" "$2" --expect-fail "$3" \
  -- cargo test -p aibd --locked --lib "$3"; }
# PX1 (N42) a full range gives a port past it
px '        Err("proxy_ports_full")' '        Ok(47800) // knife' proxy::tests::a_seat_gets_one_port_of_the_reserved_range_never_aibds_and_none_past_it
# PX2 (N43) aibd's own bind is given to a seat
px '            if own_binds.contains(&p) || self.seat_of.contains_key(&p) {' '            if self.seat_of.contains_key(&p) || own_binds.is_empty() { // knife' proxy::tests::a_seat_gets_one_port_of_the_reserved_range_never_aibds_and_none_past_it
# PX3 (N44) a port no seat has is served as some seat's
px '    let Some(seat) = ports.seat(port) else {' '    let Some(seat) = ports.seat(port).or(Some("a1b2c")) else { // knife' proxy::tests::a_connection_gets_the_address_judged_a_card_or_a_refusal
# PX4 (N45) another address than the one judged is connected
px '        Verdict::Pass(ip) => return Conn::Pass(SocketAddr::new(ip, dest_port)),' '        Verdict::Pass(ip) => return Conn::Pass(SocketAddr::new(*ips.last().unwrap_or(&ip), dest_port)), // knife' proxy::tests::a_connection_gets_the_address_judged_a_card_or_a_refusal
# PX5 (N46) a grant for a while never ends
px '            .find(|h| h.secs() == Some(d.as_secs())),' '            .find(|h| h.secs() == Some(d.as_secs())).map(|_| Hold::UntilSeatEnds), // knife' proxy::tests::a_grant_holds_for_its_while_then_a_card_again_and_its_tunnels_close
# PX6 (N47) the ended grant's tunnel is kept, the once tunnel swept
px '                    matches!(by, By::Held | By::HeldLocal)' '                    matches!(by, By::Once) // knife' proxy::tests::a_grant_holds_for_its_while_then_a_card_again_and_its_tunnels_close
# PX7 (N48) taking always back closes what the list still holds
px '                        && !allowed(s, at.ip(), at.port())' '                        && (!allowed(s, at.ip(), at.port()) || true) // knife' proxy::tests::taking_always_back_closes_what_rests_on_it_only
# PX8 (N49) a second waiting card is raised
px '        if waiting > 0 {' '        if waiting > 1 { // knife' proxy::tests::a_connection_gets_the_address_judged_a_card_or_a_refusal
# PX9 (c2f0b G1) 只這一次 leaves a grant behind
px '        Lasts::Once | Lasts::Always => None,' '        Lasts::Once => Some(Hold::Once), Lasts::Always => None, // knife' proxy::tests::a_grant_holds_for_its_while_then_a_card_again_and_its_tunnels_close
# PX10 (16.7) a seventh card in ten minutes
px '        if r.len() >= CARDS_PER_WINDOW {' '        if r.len() > CARDS_PER_WINDOW { // knife' proxy::tests::six_cards_in_ten_minutes_then_later
# PX11 (A6d) a service on this computer gets the ordinary card
px '            (at, true)' '            (at, false) // knife' proxy::tests::a_connection_gets_the_address_judged_a_card_or_a_refusal
# PX12 (f163c REVIEW-sandboxnet19 L3) a port taken back goes to the next seat at once
px '                .is_some_and(|t| now < t.saturating_add(PORT_COOLDOWN))' '                .is_some_and(|t| now < *t) // knife' proxy::tests::a_seat_gets_one_port_of_the_reserved_range_never_aibds_and_none_past_it
# PX13 a high-risk yes is written as a LAN grant
px '    if high {' '    if !high { // knife' proxy::tests::a_grant_holds_for_its_while_then_a_card_again_and_its_tunnels_close
# PX14 (f163c REVIEW-egress-ff804ceb L1) a LAN grant lets a service of this computer through
px '            if holds(grants, seat, at, true, here.now) {' '            if holds(grants, seat, at, false, here.now) { // knife' proxy::tests::a_grant_holds_for_its_while_then_a_card_again_and_its_tunnels_close
# PX15 (same) a tunnel to this computer's service is kept by a LAN grant
px '                        && !holds(grants, s, *at, *by == By::HeldLocal, now)' '                        && !holds(grants, s, *at, false, now) // knife' proxy::tests::a_grant_holds_for_its_while_then_a_card_again_and_its_tunnels_close
# PX16 (SPEC r2 U8 跨席冒用) one seat's grant lets another seat through
python3 -I scripts/knife.py crates/aibd/src/grants.rs '                    && g.seat_id == seat_id' '                    && !g.seat_id.is_empty() // knife' --expect-fail proxy::tests::a_seat_never_rides_on_another_seats_grant \
  -- cargo test -p aibd --locked --lib proxy::tests::a_seat_never_rides_on_another_seats_grant
# PX17 (same) the always list is asked for some other seat
px '        allowed(seat, ip, p) || holds(grants, seat, SocketAddr::new(ip, p), false, here.now)' '        allowed("a1b2c", ip, p) || holds(grants, seat, SocketAddr::new(ip, p), false, here.now) // knife' proxy::tests::a_seat_never_rides_on_another_seats_grant
