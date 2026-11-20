#!/usr/bin/env bash
# 要你決定卡 + 真人未審清單卡 knives CA1-CA11 (7ebca 11-19, 519d0 09:11; DECISION-council-ideas-2
# §2 §3): a press without the owner's fingerprint answers nothing and sees nothing, also after a
# restart; only an idea or a line waiting now is taken; a user folder page sees no words; the
# list's ids, rewrites and full-width folding.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ca() { python3 -I scripts/knife.py crates/aibd/src/consent_cards.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test consent_acts "$3"; }
cs() { python3 -I scripts/knife.py crates/aibd/src/slots.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test consent_acts "$3"; }
cu() { python3 -I scripts/knife.py crates/aibd/src/consent_cards.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
# CA1 an unproved press is written as an answer (gone after a restart)
ca '"idea.pressed",' \
   '"idea.answered",' \
   a_press_without_the_fingerprint_answers_nothing_and_a_user_page_sees_no_words
# CA2 any idea id is taken
ca 'waiting_idea(&c2)' \
   '{ let _ = &c2; true }' \
   a_press_without_the_fingerprint_answers_nothing_and_a_user_page_sees_no_words
# CA3 a batch with one line off the list is taken
ca 'i2.iter().all(|l| waiting_line(l))' \
   'i2.iter().any(|l| waiting_line(l))' \
   a_press_without_the_fingerprint_answers_nothing_and_a_user_page_sees_no_words
# CA4 a batch may name a line twice
ca '&& v.iter().collect::<BTreeSet<_>>().len() == v.len()' \
   '&& !v.is_empty()' \
   a_press_without_the_fingerprint_answers_nothing_and_a_user_page_sees_no_words
# CA5 a user folder page sees the list's words
cs 'crate::consent_cards::hide_list(v);' \
   'crate::consent_cards::hide_list(&mut serde_json::Value::Null);' \
   a_press_without_the_fingerprint_answers_nothing_and_a_user_page_sees_no_words
# CA6 a user folder page sees an idea's words
cs 'k.starts_with("dm.") || k.starts_with("ledger.") || k.starts_with("idea.")' \
   'k.starts_with("dm.") || k.starts_with("ledger.")' \
   a_press_without_the_fingerprint_answers_nothing_and_a_user_page_sees_no_words
# CA7 the ref after | is part of a line's id
cu 'let id = line_id(words);' \
   'let id = line_id(l);' \
   the_after_review_lines_are_one_list_and_a_seen_line_leaves_it
# CA8 a rewritten line forgets the ids it replaces
cu 'out[i].replaces.push(old.id);' \
   'let _ = old.id;' \
   the_structured_ledger_gives_its_own_ids_and_a_rewritten_line_names_the_old_one
# CA9 an idea that needs no person is a decide card too
cu '.filter(|i| i.status() == "needs_human")' \
   '.filter(|i| i.status() != "no")' \
   a_pending_needs_human_idea_is_a_decide_card_with_agree_and_disagree
# CA10 full-width letters make another line
cu "'\\u{FF01}'..='\\u{FF5E}' => char::from_u32(c as u32 - 0xFEE0).unwrap_or(c)," \
   "'\\u{FF01}'..='\\u{FF5E}' => c," \
   the_after_review_lines_are_one_list_and_a_seen_line_leaves_it
# CA11 a seen ledger line stays on the list
cu 'out.retain(|r| !seen.contains(&r.id) && ids.insert(r.id.clone()));' \
   'out.retain(|r| (!seen.contains(&r.id) || true) && ids.insert(r.id.clone()));' \
   the_structured_ledger_gives_its_own_ids_and_a_rewritten_line_names_the_old_one
# CA-ASCII a non-ASCII word where the date is panics the whole list (5d33a PREREAD-integws2 M1)
cu '        && date.is_ascii()' '        && true // knife' \
   a_line_with_non_ascii_where_the_date_or_time_is_is_no_decision_and_no_panic
