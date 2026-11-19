#!/usr/bin/env bash
# landed fix rows (a9136 11-18, 519d0 22:51; Andy 22:23 「還是有 壞了」): a fix row whose head is
# already in main or a package, or listed twice, is no fix. Each rule loosened turns its test red.
# Run by crates/aib-knife/run-knives.sh (macOS); from the repo root; -eo pipefail like GitHub's `shell: bash`.
#
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
ld() { python3 -I scripts/knife.py crates/aib-drive/src/next.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test next "$3"; }
# LD1 a landed head is still a fix row
ld 'let why = match policy.landed.get(&d.head) {' 'let why = match policy.landed.get("") { // knife' \
   a_fix_row_already_landed_or_twice_listed_is_no_fix
# LD2 a head listed twice among the fix rows is two fix rows
ld 'None if others.contains(&d.head) || !seen.insert(d.head.clone()) => {' \
   'None if others.contains(&d.head) || (!seen.insert(d.head.clone()) && false) => { // knife' \
   a_fix_row_already_landed_or_twice_listed_is_no_fix
# LD3 a head already in main is not seen as landed
ld 'if git(&["merge-base", "--is-ancestor", head, main]).is_ok() {' \
   'if false && git(&["merge-base", "--is-ancestor", head, main]).is_ok() { // knife' \
   landed_heads_reads_main_packages_and_content
# LD4 the row's own package branch counts as where it landed
ld '.find(|r| !r.is_empty() && *r != branch.as_str())' '.find(|r| !r.is_empty()) // knife' \
   landed_heads_reads_main_packages_and_content
# LD5 a commit only in the head (cherry +) is taken as landed
ld ".is_ok_and(|o| o.lines().all(|l| !l.trim_start().starts_with('+')))" \
   ".is_ok_and(|o| o.lines().all(|l| !l.trim_start().is_empty() || true)) // knife" \
   landed_heads_reads_main_packages_and_content
# LD6 a card at 已定未做 does not hold its head
ld 'if field("status") == "已定未做" || said {' 'if said { // knife' \
   a_card_holds_its_head_back_by_its_status_or_its_owners_last_word
# LD7 anyone's 先放 holds the head
ld 'let said = last.contains("先放") && (writer == field("owner") || writer == "519d0");' \
   'let said = last.contains("先放") && (!writer.is_empty() || writer == "519d0"); // knife' \
   a_card_holds_its_head_back_by_its_status_or_its_owners_last_word
# LD8 an older line's 先放 holds the head
ld 'let last = field("evidence").rsplit(" | ").next().unwrap_or("");' \
   'let last = field("evidence").split(" | ").next().unwrap_or(""); // knife' \
   a_card_holds_its_head_back_by_its_status_or_its_owners_last_word
# LD9 a held head is still a fix row
ld 'None if card.is_some() => {' 'None if card.is_some() && false => { // knife' \
   a_card_holds_its_head_back_by_its_status_or_its_owners_last_word
# LD10 an older head under another row's head is still a fix row (f1fae 11-19 00:35)
ld '.find(|(_, h)| git(&["merge-base", "--is-ancestor", head, h]).is_ok());' \
   '.find(|(_, h)| git(&["merge-base", "--is-ancestor", h, head]).is_ok()); // knife' \
   an_older_head_under_another_rows_head_is_no_fix
