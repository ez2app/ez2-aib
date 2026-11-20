#!/usr/bin/env bash
# 送回倉 second slice knives SBT1-SBT10, SB11-SB12 (a9136 11-20, N-webonly-c6-merge; 519d0 22:19:
# sent → pushed → pr_opened, a failed step is asked again, main is reached through a pull
# request only; 5d33a N4: one send asks the fingerprint at one place).
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
kn() { python3 -I scripts/knife.py "crates/aibd/src/$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --lib "$4"; }
# SBT1 a step that failed moves the trail on
kn sent_trail.rs '        e.failed = Some(Failed {' \
   '        e.state = State::PrOpened; e.failed = Some(Failed {' \
   sent_trail::tests::a_failed_step_moves_nothing_and_is_asked_again
# SBT2 a later push takes the pull request back
kn sent_trail.rs '        e.state = e.state.max(State::Pushed);' \
   '        e.state = State::Pushed;' \
   sent_trail::tests::a_sent_branch_is_pushed_then_has_its_pull_request_and_never_goes_back
# SBT3 a push that went through keeps the old failure
kn sent_trail.rs '        e.failed = None; // the push went through: what failed before is over' \
   '        let _ = &e.failed; // the push went through: what failed before is over' \
   sent_trail::tests::a_failed_step_moves_nothing_and_is_asked_again
# SBT4 a name already on the trail is recorded again
kn sent_trail.rs '        if entry_of(t, repo, branch).is_some() {' \
   '        if entry_of(t, repo, branch).is_some() && false {' \
   sent_trail::tests::only_a_branch_a_seat_sent_is_on_the_trail_and_each_seat_sees_its_own
# SBT5 a seat is shown what another seat sent
kn sent_trail.rs '        .filter(|(_, e)| e.seat_id == seat_id)' \
   '        .filter(|(_, e)| e.seat_id == seat_id || true)' \
   sent_trail::tests::only_a_branch_a_seat_sent_is_on_the_trail_and_each_seat_sees_its_own
# SBT6 the same branch name in another repo is taken for this one
kn sent_trail.rs '        .find(|e| e.repo == repo && e.branch == branch)' \
   '        .find(|e| (e.repo == repo || true) && e.branch == branch)' \
   sent_trail::tests::only_a_branch_a_seat_sent_is_on_the_trail_and_each_seat_sees_its_own
# SBT7 the file grows without end
kn sent_trail.rs '        t.sent.drain(..over);' \
   '        let _ = over;' \
   sent_trail::tests::the_list_is_newest_first_and_bounded_and_says_no_path
# SBT8 the push lands on main (repo_push's destination)
kn repo_push.rs 'let spec = format!("{}:refs/heads/{}", now.sha, now.branch);' \
   'let spec = format!("{}:refs/heads/{}", now.sha, "main");' \
   sent_trail::tests::whole_way::a_sent_branch_goes_all_the_way_and_main_never_moves
# SBT9 the pull request is not into main
kn pr_open.rs 'match api.open(slug, &t.branch, BASE, title, body, DRAFT) {' \
   'match api.open(slug, &t.branch, "release", title, body, DRAFT) {' \
   sent_trail::tests::whole_way::a_sent_branch_goes_all_the_way_and_main_never_moves
# SBT10 a send that waits for a step is pushed out of the list by later ones
kn sent_trail.rs '    let mut kept: Vec<&(usize, &Entry)> = waiting.chain(over).take(SHOWN).collect();' \
   '    let mut kept: Vec<&(usize, &Entry)> = over.chain(waiting).take(SHOWN).collect();' \
   sent_trail::tests::a_send_that_waits_for_a_step_stays_on_the_list
# SB11 送回倉 makes a branch outside web/
kn send_back.rs '    if !stem.starts_with("web/") {' \
   '    if !stem.starts_with("web/") && false {' \
   send_back::tests::a_branch_outside_web_is_never_made
# SB12 the fingerprint that cannot be asked is asked a second time (5d33a N4)
kn send_back.rs '            crate::owner_ok::Asked::Unavailable(why) => (' \
   '            crate::owner_ok::Asked::Unavailable(_) if true => crate::repos::asked(&ask), crate::owner_ok::Asked::Unavailable(why) => (' \
   send_back::tests::one_send_asks_the_fingerprint_at_one_place
