#!/usr/bin/env bash
# aib-knives: os=any
# WD1–WD2 (Andy 11-26 03:1x, PLAN-win-customer-today-c2f0b-1126): with AIBD_PAIRING / AIBD_OWNER_TOUCH unset, a
# Windows aibd pairs through Windows Hello and lets the page ask it, so a customer starts aibd.exe with no `set`;
# any value given still decides; elsewhere unset stays off. Each, broken, turns its own test red. Run from the
# repo root.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function: one wrapper per file and test target
wp() { python3 -I scripts/knife.py crates/aibd/src/pairing.rs "$1" "$2" --expect-fail "pairing::tests::$3" \
  -- cargo test -p aibd --locked --lib "pairing::tests::$3"; }
wo() { python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs "$1" "$2" --expect-fail "owner_ok::tests::$3" \
  -- cargo test -p aibd --locked --lib "owner_ok::tests::$3"; }
# WD1 a Windows aibd with AIBD_PAIRING unset stays off (the customer has to type `set`)
wp '            None if windows => ConfirmChannel::TouchId,' \
   '            None if windows => ConfirmChannel::Off,' \
   a_windows_aibd_pairs_through_hello_unless_told_otherwise
# WD2 a Windows aibd with AIBD_OWNER_TOUCH unset keeps the page's owner asks off
wo '        None => windows,' \
   '        None => false,' \
   the_owners_yes_approves_one_act_for_one_page_once
