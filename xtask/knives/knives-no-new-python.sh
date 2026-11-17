#!/usr/bin/env bash
# aib-xtask no-new-python + ui-words knives (S59, 043dd; 519d0 11-17 20:14: xtask knives live in xtask/knives/*.sh).
# Run by the "aib-xtask knives (macOS)" step in aibd.yml (one line runs xtask/knives/*.sh).
# Run from the repo root. Each knife must turn its own test red.
#
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
np() { python3 -I scripts/knife.py xtask/src/no_new_python.rs "$1" "$2" --expect-fail "$3" \
       -- cargo test -p aib-xtask --locked "no_new_python::tests::$3"; }
# NP1 an added file is let through
np "            b'A' | b'M' | b'T' => {" \
   "            b'A' => { let _ = path()?; } b'M' | b'T' => {" \
   an_added_python_file_is_refused
# NP2 a changed file outside the list is let through
np "            b'A' | b'M' | b'T' => {" \
   "            b'M' => { let _ = path()?; } b'A' | b'T' => {" \
   a_changed_python_file_outside_the_list_is_refused
# NP3 a deleted file is refused
np '                let _gone = path()?;' \
   '                out.push((String::new(), path()?));' \
   a_deleted_python_file_is_not_refused
# NP4 an exception covers the path for good, not one version
np '        if list.iter().any(|e| e.path == p && e.blob == blob) {' \
   '        if list.iter().any(|e| e.path == p) {' \
   the_approved_version_passes_and_the_next_change_is_refused_again
# NP5 the extension is compared with case
np '    let l = path.to_ascii_lowercase();' \
   '    let l = path.to_string();' \
   an_upper_case_extension_is_python_too
# NP6 the list is read from the change itself
np '    let from = from.unwrap_or_else(|| base.clone());' \
   '    let from = from.unwrap_or_else(|| head.clone());' \
   a_change_cannot_approve_its_own_python
# NP7 (ebb36 N1) two dots instead of the merge base: main's own changes count against the branch
np '    let range = format!("{base}...{head}");' \
   '    let range = format!("{base}..{head}");' \
   what_base_gained_meanwhile_does_not_count
uw() { python3 -I scripts/knife.py xtask/src/ui_words.rs "$1" "$2" --expect-fail "$3" \
       -- cargo test -p aib-xtask --locked "ui_words::tests::$3"; }
# UW1 a one-character old word counts inside other words
uw '            let alone = n > 1' \
   '            let alone = n > 0' \
   a_one_character_old_word_counts_only_alone
# UW2 an old word inside its approved new word is a hit
uw '                .any(|(x, y)| *x <= a && b <= *y && (y - x) > n);' \
   '                .any(|(x, y)| *x <= a && b <= *y && (y - x) > n + 99);' \
   an_old_word_inside_its_new_word_is_not_a_hit
# UW3 doc comments are read as strings
uw "    fn visit_attribute(&mut self, _: &'ast syn::Attribute) {}" \
   "    fn visit_attribute(&mut self, a: &'ast syn::Attribute) { syn::visit::visit_attribute(self, a) }" \
   comments_doc_comments_and_test_code_are_not_literals
# UW4 #[test] functions are read
uw '        a.path().is_ident("test")' \
   '        a.path().is_ident("test_knife")' \
   comments_doc_comments_and_test_code_are_not_literals
# UW5 strings already at the merge base count as new
uw '                if *n > 0 {' \
   '                if *n > 99 {' \
   only_new_strings_count_and_old_ones_are_the_renaming_list
# UW6 the glossary is read from the change itself
uw '    let from = from.unwrap_or_else(|| base.clone());' \
   '    let from = from.unwrap_or_else(|| head.clone());' \
   the_glossary_is_read_from_base
# UW7 (ebb36 L1) a renamed file is read at the merge base by its new name
uw '            if let Some(old) = show(repo, mb, was)? {' \
   '            if let Some(old) = show(repo, mb, if was.is_empty() { was.as_str() } else { p })? {' \
   a_renamed_file_keeps_its_old_strings
