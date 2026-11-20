#!/usr/bin/env bash
# GP1-GP7 the built-in page reads a pull request through aibd's own gh (a9136 11-21, ORDER-today
# fourth item; 7b81e's points 11-21 00:37): its own switch, never under a user page, the one
# rate for the machine, the page-only door, its own event.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
gp() { python3 -I scripts/knife.py "crates/aibd/src/$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked $5 "$4"; }
# GP1 the page asks with its switch off
gp gh_view.rs '        (false, _) => Err("page_gh_view_off"),' \
   '        (false, true) => Err("page_gh_view_off"), (false, false) => Ok(()),' \
   gh_view::tests::the_page_asks_only_with_its_own_switch_and_never_under_a_user_page --lib
# GP2 the page asks while a user folder page serves this aibd
gp gh_view.rs '        (true, true) => Err("gh_view_user_page"),' \
   '        (true, true) => Ok(()),' \
   gh_view::tests::the_page_asks_only_with_its_own_switch_and_never_under_a_user_page --lib
# GP3 the seats' switch opens the page
gp gh_view.rs '        let page_switch_on = crate::bridge_cfg::switch_on(&dir, PAGE_SWITCH);' \
   '        let page_switch_on = crate::bridge_cfg::switch_on(&dir, PAGE_SWITCH) || crate::bridge_cfg::load(&dir).trusted_seats;' \
   the_built_in_page_reads_a_pr_through_aibds_own_gh '--test gh_view_page'
# GP4 a switch is on as anything but the JSON true
gp bridge_cfg.rs '            .is_some_and(|v| v.get(name).and_then(Value::as_bool) == Some(true))' \
   '            .is_some_and(|v| v.get(name).is_some_and(|x| !x.is_null()))' \
   gh_view::tests::the_page_asks_only_with_its_own_switch_and_never_under_a_user_page --lib
# GP5 the page's ask is journaled as a seat's
gp gh_view.rs '    asked(st, by, ("page_gh_view", PAGE_EVENT, "pairing"), body, data_dir).await' \
   '    asked(st, by, ("page_gh_view", EVENT, "seat"), body, data_dir).await' \
   the_built_in_page_reads_a_pr_through_aibds_own_gh '--test gh_view_page'
# GP6 the page's ask is not counted (its key never held to the gap)
gp gh_view.rs '    said[who] = json!(seat_id.strip_prefix("page:").unwrap_or(seat_id));' \
   '    said[who] = json!(seat_id.strip_prefix("page:").unwrap_or(seat_id)); if who == "pairing" { RATE.lock().unwrap_or_else(std::sync::PoisonError::into_inner).take(); }' \
   the_built_in_page_reads_a_pr_through_aibds_own_gh '--test gh_view_page'
# GP7 a user folder page reads GitHub
gp seat_input.rs '            if held_as_user_page(&page).await {' \
   '            if held_as_user_page(&page).await && false {' \
   seat_input::tests::github_is_not_read_from_a_user_folder_page --lib
