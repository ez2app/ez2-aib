#!/usr/bin/env bash
# HG1–HG16: how long an owner-approved "beyond this mode" ask stays open — the hold is in the window and the approval, the table is keyed by risk class (loopback / this machine never "until the seat ends"; back to aibd not offered), grants end on time, once is used once, and a seat's grants end when it starts again or ends (DESIGN-persona-propose-osverify-c2f0b-1123 r1 §2; f163c PREREAD r0 M2, L1)
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
gr() { python3 -I scripts/knife.py crates/aibd/src/grants.rs "$1" "$2" --expect-fail "grants::tests::$3" \
        -- cargo test -p aibd --locked --lib "grants::tests::$3"; }
ow() { python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs "$1" "$2" --expect-fail "owner_ok::tests::$3" \
        -- cargo test -p aibd --locked --lib "owner_ok::tests::$3"; }
se() { python3 -I scripts/knife.py crates/aibd/src/sessions.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_sessions "$3"; }
# HG1 loopback / this machine may stay open until the seat ends
gr '    (OP_NET_LOCAL_SELF, &[Hold::Once, Hold::Min15, Hold::Hour1]),' \
   '    (OP_NET_LOCAL_SELF, &[Hold::Once, Hold::Min15, Hold::Hour1, Hold::UntilSeatEnds]),' \
   local_self_never_stays_until_the_seat_ends
# HG2 back to aibd is offered in the first version
gr '    (OP_NET_AIBD_SELF, &[]),' \
   '    (OP_NET_AIBD_SELF, &[Hold::Once]),' \
   back_to_aibd_takes_no_hold_in_the_first_version
# HG3 an operation that takes no hold carries one anyway
gr '            Some(_) => Err(NOT_ALLOWED),' \
   '            Some(_) => Ok(None),' \
   an_operation_not_in_the_table_takes_no_hold
# HG4 give does not check the table again
gr '        if !row(operation).is_some_and(|r| r.contains(&hold)) {' \
   '        if row(operation).is_none() {' \
   give_checks_the_table_again
# HG5 a timed grant never ends
gr '        l.retain(|g| g.until.is_none_or(|u| now < u)); // past its end: gone' \
   '        l.retain(|_| { let _ = now; true }); // past its end: gone' \
   a_timed_grant_ends_on_time
# HG6 once is not used up
gr '        if l[i].hold == Hold::Once {' \
   '        if l[i].hold == Hold::Once && i == usize::MAX {' \
   once_is_used_by_the_first_use
# HG7 the seat's grants outlive it
gr '        self.list().retain(|g| g.seat_id != seat_id);' \
   '        self.list().retain(|_| { let _ = seat_id; true });' \
   until_the_seat_ends_ends_when_it_ends_or_starts_again
# HG8 revoke takes nothing back
gr '        l.retain(|g| g.id != id);' \
   '        l.retain(|_| { let _ = id; true });' \
   revoke_takes_it_back
# HG9 retired: the window's third line (3e53b touch_hud) says how long, from grants::hold_in; top_words no longer does (f163c REVIEW-touchhud M1)
# HG10 a hold not given still raises a window
ow '        if let Err(why) = crate::grants::hold_in(ask.operation, &ask.params) {' \
   '        if let Some(why) = None::<&'"'"'static str> {' \
   a_hold_not_given_raises_no_window
# HG11 a seat that starts again keeps its grants (f163c L1)
se '        if let Some(seat) = seat_id.as_deref() {' \
   '        if let Some(seat) = None::<&str> {' \
   a_seats_grants_end_when_it_starts_again_and_when_it_ends
# HG12 a seat that ends keeps its grants
se '        if let Some(ended_seat) = rec.seat_id.as_deref() {' \
   '        if let Some(ended_seat) = None::<&str> {' \
   a_seats_grants_end_when_it_starts_again_and_when_it_ends
# HG13 a once-grant counts as a standing yes when judging (PREREAD-sandboxnet19 G1 / G2)
gr '                g.hold != Hold::Once' \
   '                true' \
   holds_does_not_use_a_grant_up_and_skips_once
# HG14 judging keeps a grant past its end
gr '                    && g.until.is_none_or(|u| now < u)' \
   '                    && g.until.is_none_or(|u| now < u || true)' \
   holds_does_not_use_a_grant_up_and_skips_once
# HG15 an IPv4-mapped address is written apart from its IPv4 one (G4)
gr '        IpAddr::V6(x) => x.to_ipv4_mapped().map_or(IpAddr::V6(x), IpAddr::V4),' \
   '        IpAddr::V6(x) => IpAddr::V6(x),' \
   canon_writes_one_address_one_way
# HG16 the address is written as it came (scope, mapped form)
gr '    SocketAddr::new(ip, a.port()).to_string()' \
   '    { let _ = ip; a.to_string() }' \
   canon_writes_one_address_one_way
