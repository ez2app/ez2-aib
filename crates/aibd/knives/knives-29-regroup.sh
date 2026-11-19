#!/usr/bin/env bash
# 舊資料照卡分 (import_regroup.rs; 519d0 NOTE-import-regroup-519d0-1119 §1 §2 §4): each rule,
# broken, turns its own test red. Every new text keeps the names it replaces used (-D warnings).
set -u
cd "$(git rev-parse --show-toplevel)"
rg() { python3 -I scripts/knife.py crates/aibd/src/import_regroup.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "import_regroup::tests::$3"; }
# RG1 a word two cards write names one of them
rg '            if cards.len() != 1 {' \
   '            if cards.is_empty() { // knife' \
   a_word_two_cards_write_names_neither
# RG2 a one letter card id (V) is looked for in text
rg '            if id.chars().count() > 1 {' \
   '            if !id.is_empty() { // knife' \
   a_word_two_cards_write_names_neither
# RG3 a reply does not follow the mail it answers
rg '        .or_else(|| answered.map(|c| (c.to_owned(), By::Reply)))' \
   '        .or_else(|| answered.filter(|_| false).map(|c| (c.to_owned(), By::Reply))) // knife' \
   each_rule_puts_a_record_and_says_which
# RG4 mail of another day follows the one before it
rg '                    let near = tpe_day(*t) == tpe_day(it.at)' \
   '                    let near = (tpe_day(*t) == tpe_day(it.at) || *t <= it.at)' \
   context_follows_only_an_anchor_near_in_time_and_count
# RG5 a document's whole text is posted
rg '        if body.len() == DOC_LINES {' \
   '        if body.len() == usize::MAX { // knife' \
   a_document_is_one_short_post_with_its_file
# RG6 the sample writes every card
rg '            if !r.item.card.as_ref().is_some_and(|k| set.contains(k)) {' \
   '            if set.is_empty() && !r.item.card.as_ref().is_some_and(|k| set.contains(k)) { // knife' \
   records_go_into_their_cards_own_thread_and_undo_takes_them_back
# RG7 a mail naming a document's file does not follow that document's card
rg '        .find_map(|w| plan.files.get(&w).cloned())' \
   '        .find_map(|w| plan.files.get(&w).cloned().filter(|_| false)) // knife' \
   a_mail_naming_a_documents_file_follows_that_documents_card
# RG8 a 判斷 naming a card is not put on it
rg '            let card = card_of(&e.item, plan).map(|(c, _)| c);' \
   '            let card = card_of(&e.item, plan).map(|(c, _)| c).filter(|_| e.item.is_empty()); // knife' \
   each_judgement_is_one_self_marked_post_and_a_return_is_a_correction
# RG9 a review grade standing alone in a text is a card (519d0 05:1x card:M1 3,801)
rg '    id.chars().count() <= 2 || numbered(b"HMLN") || numbered(b"R")' \
   '    id.chars().count() <= 2 && numbered(b"HMLN") && numbered(b"R") // knife' \
   review_grades_and_rounds_are_a_card_only_where_a_card_is_said
# RG10 「卡 X」 does not say a card
rg '        ["卡 ", "卡:", "卡:", "node: ", "node:"]' \
   '        ["\u{0}"] // knife' \
   review_grades_and_rounds_are_a_card_only_where_a_card_is_said
# RG11 a letter naming another letter's id does not follow it
rg '                    .find_map(|q| by_id.get(&q).cloned());' \
   '                    .find_map(|q| by_id.get(&q).cloned().filter(|_| false));' \
   a_letter_naming_another_letters_id_follows_its_card
# RG12 a letter goes on a card its seat marked later that day
rg '            .find(|(t, _)| *t <= at && tpe_day(*t) == day)' \
   '            .find(|(t, _)| (*t <= at || *t > at) && tpe_day(*t) == day)' \
   a_letter_goes_on_the_card_its_seat_last_marked_that_day
# RG13 the old cockpit row counts any 判斷 change as a 校正, not off → back
rg '                .filter(|w| w[0] < ON_TRACK_MIN && w[1] >= ON_TRACK_MIN)' \
   '                .filter(|w| w[0] != w[1] || ON_TRACK_MIN == 0) // knife' \
   a_seat_day_has_the_old_cockpit_row
# RG16 a 判斷 that stays on the main line is a 校正 too
rg '                .filter(|_| pct >= ON_TRACK_MIN);' \
   '                .filter(|_| pct >= ON_TRACK_MIN || pct < ON_TRACK_MIN);' \
   each_judgement_is_one_self_marked_post_and_a_return_is_a_correction
# RG14 a hand-over rewritten in the same round counts twice
rg '        rounds.entry(day).or_default().insert(round.to_bits());' \
   '        rounds.entry(day).or_default().insert(round.to_bits() ^ ts.to_bits()); // knife' \
   a_seat_day_has_the_old_cockpit_row
# RG15 a seat's day row goes on a card thread, not its seat's own thread
rg '        return format!("seat_day:{seat}");' \
   '        return format!("seat_day:{seat}").replace(seat, "") + &format!("card:{seat}")[..0]; // knife' \
   a_seat_day_has_the_old_cockpit_row
# RG17 a system letter is counted as unassigned
rg '                            _ if system => (None, By::System),' \
   '                            _ if system && false => (None, By::System),' \
   a_letter_without_a_sender_seat_finds_it_or_is_a_system_letter
# RG18 a letter's sender label is not read (the ledger read, import_more)
python3 -I scripts/knife.py crates/aibd/src/import_regroup.rs \
   "    let after = &label[label.rfind('@')? + 1..];" \
   "    let after = &label[label.rfind('@').filter(|_| false)? + 1..];" \
   --expect-fail old_mail_goes_into_its_pairs_thread_once \
   -- cargo test -p aibd --locked --lib import_more::tests::old_mail_goes_into_its_pairs_thread_once
# RG20 a letter goes by its sender seat field, not the seat it says it is from
rg '                        let said = s("said_seat");' \
   '                        let said = String::new(); let _ = s("said_seat");' \
   a_letter_goes_on_the_card_its_seat_last_marked_that_day
# RG19 a seat's day thread gets a kind the store refuses (519d0 06:08)
python3 -I scripts/knife.py crates/aib-store/src/threads.rs \
   '    "council", "summary", "handoff", "drive", "shadow", "seat_day", "system",' \
   '    "council", "summary", "handoff", "drive", "shadow", "seat_dax", "system",' \
   --expect-fail a_real_write_opens_each_kind_of_thread_and_posts_once \
   -- cargo test -p aibd --locked --lib import_regroup::tests::a_real_write_opens_each_kind_of_thread_and_posts_once
# RG21 an all-digit seat code (34396, 87167) is refused again (519d0 06:17)
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
   "            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))" \
   "            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) && s.bytes().any(|b| b.is_ascii_alphabetic())" \
   --expect-fail a_card_is_the_first_node_id_named \
   -- cargo test -p aibd --locked --lib import_more::tests::a_card_is_the_first_node_id_named
# RG22 a post's document is not shown (0eed7 VERIFY-oneline2 M1)
python3 -I scripts/knife.py crates/aibd/src/threads.rs \
   '        if let Some(d) = doc_of(p, with_body) {' \
   '        if let Some(d) = doc_of(p, with_body).filter(|_| false) {' \
   --expect-fail an_imported_documents_post_names_its_file \
   -- cargo test -p aibd --locked --lib threads::tests::an_imported_documents_post_names_its_file
# RG23 a document the doc wall refuses is still listed (0eed7 r2 N2)
python3 -I scripts/knife.py crates/aibd/src/threads.rs \
   '            let file = d.get("file")?.as_str().filter(|f| crate::docs::listed(f))?;' \
   '            let file = d.get("file")?.as_str()?;' \
   --expect-fail an_imported_documents_post_names_its_file \
   -- cargo test -p aibd --locked --lib threads::tests::an_imported_documents_post_names_its_file
# RG24 the user page is given a document's file name (5d33a CHECK-userpage-docname)
python3 -I scripts/knife.py crates/aibd/src/threads.rs \
   '            if words {' \
   '            if words || true {' \
   --expect-fail an_imported_documents_post_names_its_file \
   -- cargo test -p aibd --locked --lib threads::tests::an_imported_documents_post_names_its_file
# RG25 a seat's carded letter is not remembered for its next letters that day (519d0 07:29)
rg '                    seat_last.insert(x.to_owned(), (it.at, c.clone(), 0));' \
   '                    let _ = (x, &seat_last);' \
   a_seats_letters_that_day_and_the_pairs_next_letter_give_a_card
# RG26 a pair's letter does not take the card of the pair's next letter that day
rg '                    r.by = By::Near;' \
   '                    r.by = By::Near; r.item.card = None;' \
   a_seats_letters_that_day_and_the_pairs_next_letter_give_a_card
# RG27 the documents a letter names are not read for their card (519d0 07:29: file was 0)
rg '            out.insert(name, c);' \
   '            let _ = (name, c);' \
   a_letter_naming_a_document_takes_the_documents_card
# RG28 context follows a pair's anchor past CONTEXT_MAX letters (519d0 08:11: T13 2,930)
rg '                        && CONTEXT_MAX >= *n' \
   '                        && usize::MAX >= *n' \
   context_follows_only_an_anchor_near_in_time_and_count
# RG29 context follows a pair's anchor past CONTEXT_SECS
rg '                        && it.at - *t <= CONTEXT_SECS' \
   '                        && it.at - *t <= i64::MAX' \
   context_follows_only_an_anchor_near_in_time_and_count
# RG30 a seat's letters follow its anchor past CONTEXT_MAX letters
rg '                                            && *n <= CONTEXT_MAX' \
   '                                            && *n <= usize::MAX' \
   context_follows_only_an_anchor_near_in_time_and_count
# RG31 a card got from a pair or seat is passed on (the chain)
rg '        let anchor = matches!(by, By::Text | By::Work | By::File | By::Reply);' \
   '        let anchor = by != By::None;' \
   context_follows_only_an_anchor_near_in_time_and_count
# RG32 a letter takes a pair's anchor far after it
rg '                        && *t - r.item.at <= CONTEXT_SECS' \
   '                        && *t - r.item.at <= i64::MAX' \
   context_follows_only_an_anchor_near_in_time_and_count
# RG33 a seat's letters follow its anchor past CONTEXT_SECS
rg '                                            && it.at <= *t + CONTEXT_SECS' \
   '                                            && it.at <= i64::MAX' \
   context_follows_only_an_anchor_near_in_time_and_count
# RG34 a letter takes its receiver's last anchor (519d0 08:18: seat_day 9.4%)
rg '                        let seats = [from];' \
   '                        let seats = [from, s("to")];' \
   context_follows_only_an_anchor_near_in_time_and_count
# RG35 a document's card is read past its opening lines (519d0 08:18: 「T13 +88」)
rg '            .take(DOC_HEAD_LINES)' '            .take(usize::MAX)' \
   a_letter_naming_a_document_takes_the_documents_card
# RG36 a seat read off a letter's opening words takes that seat's day (519d0 08:27)
rg '                        let guessed = s("said_from") == "opening";' \
   '                        let guessed = false;' \
   a_letter_goes_on_the_card_its_seat_last_marked_that_day
# RG37 a letter naming its own work takes its pair's anchor (519d0 08:32 B)
rg '                        && !elsewhere;' '                        && true;' \
   context_follows_only_an_anchor_near_in_time_and_count
# RG38 a letter naming its own work takes the pair's next anchor
rg '                        && !names_other_work(&r.item.body)' '                        && true' \
   context_follows_only_an_anchor_near_in_time_and_count
# RG39 a letter naming its own work takes its seat's day
rg '                        match (!guessed && !elsewhere).then(day).flatten() {' \
   '                        match (!guessed).then(day).flatten() {' \
   context_follows_only_an_anchor_near_in_time_and_count
# RG40 a letter with only a head no card has counts as about other work (519d0 08:39)
rg '    work_words(text).iter().any(|w| !is_head(w))' '    !work_words(text).is_empty()' \
   context_follows_only_an_anchor_near_in_time_and_count
# RG41 an old letter without a card is imported (Andy 11-19 a50-1)
rg '            || r.item.card.is_some()' '            || true' \
   old_cardless_letters_stay_out_and_new_ones_go_under_the_senders_main_item
# RG42 a cardless letter is not put in its sender's bucket (a50-2)
rg '                it.source["bucket_seat"] = json!(from);' '                let _ = &from;' \
   old_cardless_letters_stay_out_and_new_ones_go_under_the_senders_main_item
# RG43 the bucket is not under the sender's main item of then
rg '                        .find(|(t, _)| *t <= it.at)' '                        .find(|(t, _)| *t > it.at)' \
   old_cardless_letters_stay_out_and_new_ones_go_under_the_senders_main_item
# RG44 a seat with no mark before the letter has no main item though it anchored a card (519d0 08:49)
rg '                    .or_else(|| seat_last.get(&from).map(|(_, c, _)| c.clone()));' \
   '                    .or_else(|| None::<String>);' \
   old_cardless_letters_stay_out_and_new_ones_go_under_the_senders_main_item
# RG45 a seat read off the opening words has no bucket (519d0 08:49)
rg '            if im::seat_code(&from).is_some() {' \
   '            if im::seat_code(&from).is_some() && it.source.get("said_from").is_none() {' \
   old_cardless_letters_stay_out_and_new_ones_go_under_the_senders_main_item
# RG46 an idea with no node is put on a card its text names (519d0 09:11: bucket)
rg '            None if kind == "idea" => (None, By::None),' '            None if kind == "idea_knife" => (None, By::None),' \
   each_idea_is_one_post_on_its_card_or_in_its_seats_bucket
# RG47 an idea needing a person is not flagged for slot 1
rg '            source["needs_human"] = json!(true);' '            let _ = &source;' \
   each_idea_is_one_post_on_its_card_or_in_its_seats_bucket
# RG48 a review does not change the idea's state
rg '                        state.insert(id.to_owned(), line.clone());' '                        let _ = (id, &line);' \
   each_idea_is_one_post_on_its_card_or_in_its_seats_bucket
# RG49 an idea already decided is put before Andy again (519d0 09:17)
rg '        if human && !decided.contains(&id) {' '        if human {' \
   each_idea_is_one_post_on_its_card_or_in_its_seats_bucket
