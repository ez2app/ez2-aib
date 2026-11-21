#!/usr/bin/env bash
# aib-tidy knives (519d0 11-21 14:27, DECISION-council-tidy-519d0-1121 r1): each rule loosened
# must turn its own test red. Run by crates/aib-knife/run-knives.sh; from the repo root.
#
set -eo pipefail
umask 022
set -eu
td() { python3 -I scripts/knife.py crates/aib-tidy/src/lib.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-tidy --locked --lib "tests::$3"; }
# TD1 a dependency a Rust file names is still listed
td '            if rs.iter().any(|t| names_crate(t, &id)) {' '            if rs.is_empty() {' \
   an_unnamed_dependency_is_listed_unverified
# TD2 a dependency whose comment says knife is not excluded (§4)
td '            let never = if note.contains("knife") || d.note.contains('"'"'刀'"'"') {' '            let never = if note.contains("knife!!") || d.note.contains("刀!!") {' \
   a_knifes_dependency_is_excluded
# TD3 a dependency a knife names is not excluded (§4)
td '            } else if knife_texts.iter().any(|t| names_crate(t, &id)) {' '            } else if knife_texts.is_empty() {' \
   a_knifes_dependency_is_excluded
# TD4 an excluded item still counts as unverified
td '        if self.never.is_some() {' '        if self.never.is_some() && self.mac {' \
   a_knifes_dependency_is_excluded
# TD5 a Python module imported by name has no caller
td '                    && (t.contains(&format!("import {stem}"))' '                    && (t.contains(&format!("import {stem}!!"))' \
   a_script_without_a_caller_is_listed_and_others_are_not
# TD6 a knives folder run by a wildcard is listed (§4)
td '            .then_some("glob_called");' '            .then_some("glob_called").filter(|_| false);' \
   a_script_without_a_caller_is_listed_and_others_are_not
# TD7 a crate-relative path in a workflow is listed
td '                if crates.iter().any(|c| c.join(&p).exists()) {' '                if crates.is_empty() {' \
   a_workflow_path_is_looked_for_in_the_crates_too
# TD8 one platform is enough to be a candidate (§2)
td '        } else if self.mac && self.win {' '        } else if self.mac || self.win {' \
   nothing_is_a_candidate_without_both_platforms
# TD9 a crate name inside a longer word counts
td '        if s > 0 && word(b[s - 1]) {' '        if s > 0 && word(b[s - 1]) && s == usize::MAX {' \
   crate_names_are_read_as_words
# TD10 an absolute path is read as a repository path
td '                && !w.starts_with('"'"'/'"'"')' '                && !w.starts_with("//")' \
   crate_names_are_read_as_words
