#!/usr/bin/env bash
# N-workspace wave 3 knives HO1–HO4 (7b81e 11-18, ORDER-workspace §第三波 7b81e 2; DESIGN-workspace-hosts r0,
# a9136 12:03): a user folder page does not see the machines; one answer is kept a minute; every ssh connects
# within 5 s; a holder's label is cut. Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
hu() { python3 -I scripts/knife.py crates/aibd/src/hosts.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "hosts::tests::$3"; }
he() { python3 -I scripts/knife.py crates/aibd/src/hosts.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test hosts "$3"; }
# HO1 a user folder page sees the machines
he '        Caller::Page(p) => !crate::seat_input::held_as_user_page(p).await,' \
   '        Caller::Page(p) => !crate::seat_input::held_as_user_page(p).await || true, // knife' \
   the_page_sees_each_machine_by_nickname_and_one_answer_a_minute
# HO2 every request asks every machine again
he '        if at.elapsed() < CACHE {' \
   '        if at.elapsed() < Duration::ZERO { // knife' \
   the_page_sees_each_machine_by_nickname_and_one_answer_a_minute
# HO3 ssh waits 20 s to connect
hu 'pub const CONNECT_S: u32 = 5;' \
   'pub const CONNECT_S: u32 = 20; // knife' \
   every_ssh_is_batch_with_no_forwarding_and_a_five_second_connect
# HO4 a holder's label is not cut
hu '        .map(|w| w.chars().take(WHAT_MAX).collect::<String>());' \
   '        .map(|w| w.chars().take(usize::MAX).collect::<String>()); // knife' \
   a_long_holder_label_is_cut
