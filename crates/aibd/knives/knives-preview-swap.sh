#!/usr/bin/env bash
# PW1-PW12 aibd ctl preview swap (Andy 11-21 06:1x; PREREAD-preview-swap-ctl-a9136-1121, 5d33a PREREAD-previewswap 0/0/2/2)
set -euo pipefail
ps() { python3 -I scripts/knife.py crates/aibd/src/preview_swap.rs "$1" "$2" --expect-fail "preview_swap::tests::$3" \
        -- cargo test -p aibd --locked --lib "preview_swap::tests::$3"; }
pv() { python3 -I scripts/knife.py crates/aibd/src/preview.rs "$1" "$2" --expect-fail "preview::tests::$3" \
        -- cargo test -p aibd --locked --lib "preview::tests::$3"; }
# PW1 a caller other than the start-up token asks
ps '        return Some((StatusCode::FORBIDDEN, "preview_swap_terminal_only"));' '        let _ = c; // knife' \
   only_the_start_up_token_asks_and_only_with_a_sha
# PW2 a build not on a ready line goes in
ps '    found_sha == Some(asked) && ready.iter().any(|s| s == asked)' '    found_sha == Some(asked) || ready.iter().any(|s| s == asked) // knife' \
   only_the_ready_build_in_the_folder_goes_in
# PW3 the swap goes on before every asked seat wrote its handoff
ps '        self.waiting.is_empty() && self.asked.iter().all(|s| self.done.contains(s))' '        self.waiting.is_empty() // knife' \
   the_swap_waits_for_each_seats_handoff_and_names_who_did_not
# PW4 a seat not yet typed the words is not named as missing
ps '            .chain(self.waiting.iter())' '            .chain(self.waiting.iter().take(0)) // knife' \
   the_swap_waits_for_each_seats_handoff_and_names_who_did_not
# PW5 no cooling after a swap
ps '    lock(&ENDED).get(id).is_some_and(|t| t.elapsed() < COOL)' '    lock(&ENDED).get(id).is_some_and(|t| t.elapsed() > COOL) // knife' \
   a_swap_cools_for_a_minute_after_it_ended
# PW6 a terminal swap is not named as the terminal's
pv '            } else if w.by == BY_CTL {' '            } else if w.by == "never" { // knife' \
   a_terminal_swap_says_it_was_the_terminal
# PW7 any round is taken
ps '    (8..=64).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_hexdigit())' '    !s.is_empty() // knife' \
   the_handoff_words_are_fixed_and_a_round_is_all_it_takes
# PW8 the old build is not put back
pv '    std::fs::rename(kept, &back).ok()?;' '    let _ = kept; // knife' \
   a_swap_that_does_not_start_puts_the_old_build_back
# PW9 the token file is read through a link
ps '        o.custom_flags(nix::libc::O_NOFOLLOW);' '        o.custom_flags(0); // knife' \
   the_previews_token_file_is_read_as_untrusted
# PW10 an old handoff note counts for this round
ps '        .filter(|(_, x)| crate::revive::unix_of(&x.at).is_some_and(|t| t >= began))' '        .filter(|(_, x)| crate::revive::unix_of(&x.at).is_some_and(|t| t >= began - 3600)) // knife' \
   the_swap_waits_for_each_seats_handoff_and_names_who_did_not
# PW11 a kept copy that is not the running build is kept
pv '    if hash_file(&kept).as_deref() != Some(sha256) {' '    if hash_file(&kept).is_none() && !sha256.is_empty() { // knife' \
   a_swap_that_does_not_start_puts_the_old_build_back
# PW12 a field other than sha256 is taken
ps '    if o.keys().any(|k| k != "sha256") {' '    if o.keys().any(|k| k == "round") { // knife' \
   only_the_start_up_token_asks_and_only_with_a_sha
