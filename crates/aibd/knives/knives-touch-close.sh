#!/usr/bin/env bash
# TC1–TC5 a fingerprint window closes with its ticket and an unanswered one is not a no (N-webonly-c45; 0eed7 VERIFY-ownertouch-andy M1, 5d33a PREREAD-touchclose)
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ow() { python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs "$1" "$2" --expect-fail "owner_ok::tests::$3" \
        -- cargo test -p aibd --locked --lib "owner_ok::tests::$3"; }
tt() { python3 -I scripts/knife.py crates/aibd/src/touch.rs "$1" "$2" --expect-fail "touch::tests::$3" \
        -- cargo test -p aibd --locked --lib "touch::tests::$3"; }
# TC1 the window is given no limit, or another
ow '                    .ask_until(&why, &msg, std::time::Duration::from_secs(TICKET_SECS));' \
   '                    .ask_until(&why, &msg, std::time::Duration::from_secs(TICKET_SECS * 60));' \
   a_window_nobody_answers_closes_with_the_ticket_and_is_not_a_no
# TC2 a window nobody answered reads as a no
ow '            Err(crate::touch::TIMEOUT) => State::Expired,' \
   '            Err("never") => State::Expired,' \
   a_window_nobody_answers_closes_with_the_ticket_and_is_not_a_no
# TC3 a window nobody answered starts the cool-down
ow '            closing.yes = crate::touch::ends_quietly(&got);' \
   '            closing.yes = got.is_ok();' \
   a_window_nobody_answers_closes_with_the_ticket_and_is_not_a_no
# TC4 a timeout counts as a no everywhere
tt '    matches!(got, Ok(()) | Err(TIMEOUT))' \
   '    matches!(got, Ok(()))' \
   the_limit_reaches_the_device_and_a_timeout_is_not_a_no
# TC5 the limit does not reach the device
tt '        let sig = self.dev.sign_until(&self.key.blob, reason, msg, limit)?;' \
   '        let sig = self.dev.sign_until(&self.key.blob, reason, msg, limit / 1000)?;' \
   the_limit_reaches_the_device_and_a_timeout_is_not_a_no
