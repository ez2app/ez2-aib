#!/usr/bin/env bash
# aib-knives: os=unix
# N-workspace-c3 knives RK1–RK3 (7b81e 11-18; 519d0 r16 ruling 2: a record's kinds are aibd's, one rule set): a
# request is not a plain yes, a quoted yes is yes, and the card really carries the kinds. Each rule, broken, turns
# its own test red.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
ku() { python3 -I scripts/knife.py crates/aibd/src/record_kind.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "record_kind::tests::$3"; }
kw() { python3 -I scripts/knife.py crates/aibd/src/workspace.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "workspace::tests::$3"; }
# RK1 any agreement word followed by a long request counts as yes
ku '        let strong = ["同意", "准", "照"]' \
   '        let strong = true || ["同意", "准", "照"] // knife' \
   only_yes_takes_short_agreements_not_requests
# RK2 a quoted yes counts as the person's words
ku '                    if c.get(1).is_some_and(|w| self.only_yes(w.as_str())) {' \
   '                    if false && c.get(1).is_some_and(|w| self.only_yes(w.as_str())) { // knife' \
   the_persons_words_are_need_and_a_plain_yes_is_yes
# RK3 the card leaves the kinds out
kw '                let kinds = crate::record_kind::rules().kinds(e);' \
   '                let kinds = vec!["dev"]; // knife' \
   the_card_has_every_field_records_newest_first_and_the_items_right_under
