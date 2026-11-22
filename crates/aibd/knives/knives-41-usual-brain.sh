#!/usr/bin/env bash
# UB1–UB6 (a9136 11-23; SPEC-ai-partner-open-ebab8-1122 §7 4, CHECKLIST-aipartner-aibd-9918e-1122;
# f163c REVIEW-s4a M1 / N1 / N2): a persona's usual second brain is the one most of its seats use,
# a tie goes to the newest then the smaller uid, an odd stamp never looks newer, and the brain list
# and the persona list say it. Each, broken, turns its own test red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ub() { python3 -I scripts/knife.py crates/aibd/src/registry.rs "$1" "$2" --expect-fail "registry::tests::a_persona_s_usual_brain_is_the_one_its_seats_use_most" \
  -- cargo test -p aibd --locked --lib "registry::tests::a_persona_s_usual_brain_is_the_one_its_seats_use_most"; }
uw() { python3 -I scripts/knife.py crates/aibd/src/workspace.rs "$1" "$2" --expect-fail "registry::tests::a_persona_s_usual_brain_is_the_one_its_seats_use_most" \
  -- cargo test -p aibd --locked --lib "registry::tests::a_persona_s_usual_brain_is_the_one_its_seats_use_most"; }
ut() { python3 -I scripts/knife.py crates/aibd/src/registry.rs "$1" "$2" --expect-fail "registry::tests::a_tie_at_the_same_moment_is_the_smaller_uid_and_odd_stamps_are_oldest" \
  -- cargo test -p aibd --locked --lib "registry::tests::a_tie_at_the_same_moment_is_the_smaller_uid_and_odd_stamps_are_oldest"; }
# UB1 the newest use is not kept, so a tie is not decided by time
ub '        t.1 = t.1.max(utc_stamp(&m.updated_at));' \
   '        t.1 = t.1.max(utc_stamp("")); // knife'
# UB2 the first brain read wins whatever the count
ub '            Some((u, uat)) => (n, at) > (u.seats, *uat),' \
   '            Some(_) => false,'
# UB3 the brain list says no persona it is usual for
ub '                .filter(|(_, u)| u.brain == e.uid)' \
   '                .filter(|_| false)'
# UB4 the persona list never says its usual brain
uw '                "usual_brain": usual.get(uid).map(|u| u.brain.as_str())}))' \
   '                "usual_brain": usual.get("").map(|u| u.brain.as_str())}))'
# UB5 a tie at the same moment goes to the later uid (N1)
ut '            Some((u, uat)) => (n, at) > (u.seats, *uat),' \
   '            Some((u, uat)) => (n, at) >= (u.seats, *uat),'
# UB6 any stamp is compared as text, so an offset one looks newer (N2)
ut '    if shaped {' \
   '    if shaped || !s.is_empty() {'
