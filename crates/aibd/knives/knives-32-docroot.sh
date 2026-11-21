#!/usr/bin/env bash
# aib-knives: os=macos
# N-web-oneline 3 knives DR1–DR4 (7b81e 11-19; 5d33a CHECK-docroot-5d33a-1119 條件 6): a document reaches the page only
# when its name is a listed kind, one piece in the pending folder, not a symbolic link, and a RELAY- / RECEIPT- text
# has its e-mail addresses masked. Each, broken, turns its own test red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# DR1 a kind not on the list is given
python3 -I scripts/knife.py crates/aibd/src/docs.rs \
  '    if !listed(file) {' \
  '    if !listed(file) && false { // knife' \
  --expect-fail docs::tests::only_listed_kinds_in_the_folder_itself_are_given \
  -- cargo test -p aibd --locked --lib docs::tests::only_listed_kinds_in_the_folder_itself_are_given
# DR2 a file in a sub-folder is given
python3 -I scripts/knife.py crates/aibd/src/docs.rs \
  "    !file.contains(['/', '\\\\']) && DOC_KINDS.iter().any(|k| file.starts_with(k))" \
  '    DOC_KINDS.iter().any(|k| file.starts_with(k)) // knife' \
  --expect-fail docs::tests::only_listed_kinds_in_the_folder_itself_are_given \
  -- cargo test -p aibd --locked --lib docs::tests::only_listed_kinds_in_the_folder_itself_are_given
# DR3 a symbolic link inside the folder is followed
python3 -I scripts/knife.py crates/aibd/src/docs.rs \
  '        Ok(m) if m.file_type().is_symlink() => return Err(DocErr::Refused),' \
  '        Ok(m) if m.file_type().is_symlink() && false => return Err(DocErr::Refused), // knife' \
  --expect-fail docs::tests::a_link_inside_the_folder_is_not_followed \
  -- cargo test -p aibd --locked --lib docs::tests::a_link_inside_the_folder_is_not_followed
# DR4 a RELAY- text reaches the page with its e-mail addresses
python3 -I scripts/knife.py crates/aibd/src/docs.rs \
  '    file.starts_with("RELAY-") || file.starts_with("RECEIPT-")' \
  '    file.starts_with("RECEIPT-") // knife' \
  --expect-fail docs::tests::mail_addresses_in_a_relay_or_receipt_are_masked \
  -- cargo test -p aibd --locked --lib docs::tests::mail_addresses_in_a_relay_or_receipt_are_masked
