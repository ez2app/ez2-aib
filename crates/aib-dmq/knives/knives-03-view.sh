#!/usr/bin/env bash
# B1dm-S4 knives VS1–VS14 (7ebca 11-18, 519d0 08:06): reading a seat's mail translated from
# dm_queue.py (tabs, waiting order, stale, expiry, list, show) keeps the old answers. Each rule,
# broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
vs() { python3 -I scripts/knife.py crates/aib-dmq/src/view.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-dmq --locked --test view "$3"; }
# VS1 an undelivered message no longer goes first in the waiting tab
vs 'u8::from(row_status(item) == "delivered"),' \
   'u8::from(row_status(item) != "delivered"),' \
   the_waiting_tab_puts_undelivered_human_and_decide_first
# VS2 a decide message no longer goes before the rest
vs 'u8::from(norm::kind(item.get("kind")) != "decide"),' \
   'u8::from(norm::kind(item.get("kind")) == "decide"),' \
   the_waiting_tab_puts_undelivered_human_and_decide_first
# VS3 the last tie-break (id) turns around
vs '.then_with(|| id(a).cmp(id(b)))' \
   '.then_with(|| id(b).cmp(id(a)))' \
   every_fixture_set_reads_like_the_python
# VS4 exactly four hours untouched counts as stale
vs 'touched_at(item).is_some_and(|t| now.saturating_sub(t) > STALE_WAIT_S)' \
   'touched_at(item).is_some_and(|t| now.saturating_sub(t) >= STALE_WAIT_S)' \
   a_read_message_untouched_for_four_hours_reminds_by_whether_it_says_what_it_waits_for
# VS5 a message expiring this second is not expired yet
vs 'int_of(exp).is_some_and(|e| e <= now)' \
   'int_of(exp).is_some_and(|e| e < now)' \
   an_unread_message_older_than_the_minutes_is_stale_and_an_expired_one_is_not_unread
# VS6 an expired message counts as unread
vs 'UNREAD_STATUSES.contains(&row_status(item)) && !expired_now(item, now)' \
   'UNREAD_STATUSES.contains(&row_status(item)) && !expired_now(item, now.min(i64::MIN / 2))' \
   every_fixture_set_reads_like_the_python
# VS7 stale starts an hour early
vs 'int_of(i.get("sent_at")).is_some_and(|s| s < cutoff)' \
   'int_of(i.get("sent_at")).is_some_and(|s| s < cutoff + 3600)' \
   an_unread_message_older_than_the_minutes_is_stale_and_an_expired_one_is_not_unread
# VS8 the "may be out of date" line is never written
vs 'if age >= MAYBE_STALE_MINUTES {' \
   'if false {' \
   every_fixture_set_reads_like_the_python
# VS9 the box order loses the delivery order
vs 'Some(n) => *n as u64,' \
   'Some(_) => 0,' \
   every_fixture_set_reads_like_the_python
# VS10 a claimed message is listed
vs 'Some("claimed" | "deleted")' \
   'Some("deleted")' \
   every_fixture_set_reads_like_the_python
# VS11 Taipei time is an hour off
vs 'let local = epoch.saturating_add(TAIPEI_OFFSET_S);' \
   'let local = epoch.saturating_add(TAIPEI_OFFSET_S + 3600);' \
   show_adds_the_age_line_only_when_asked_and_ends_with_a_newline
# VS12 of two at the same second the later one is the oldest
vs 'Some(b) if int_or_zero(b, "ts") <= int_or_zero(r, "ts") => Some(b),' \
   'Some(b) if int_or_zero(b, "ts") < int_or_zero(r, "ts") => Some(b),' \
   every_fixture_set_reads_like_the_python
# VS13 read rows without a wait are counted the other way round
vs '.filter(|r| !has_wait_text(r))' \
   '.filter(|r| has_wait_text(r))' \
   a_wait_counts_only_when_it_is_an_object_with_text
# VS14 an extreme sent_at overflows the age (5d33a N1)
vs 'int_of(sent).map(|s| now.saturating_sub(s).div_euclid(60).max(0))' \
   'int_of(sent).map(|s| (now - s).div_euclid(60).max(0))' \
   an_extreme_time_neither_panics_nor_wraps_around
