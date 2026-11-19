#!/usr/bin/env bash
# aib-xtask knife-compile knives KC1–KC6 (b4297 11-20; ORDER-autodrive-webdev-519d0-1120 b4297 第 3 項):
# the receipt's knife file is read raw, knives batch with their lines kept in step, an error is
# a knife's only on its own lines, warnings are not errors, and the files always go back.
# Run by the "aib-xtask knives (macOS)" step in aibd.yml (xtask/knives/*.sh). Run from the repo root.
#
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
kc() { python3 -I scripts/knife.py xtask/src/knife_compile.rs "$1" "$2" --expect-fail "$3" \
       -- cargo test -p aib-xtask --locked "knife_compile::tests::$3"; }
# KC1 a line with five fields is taken
kc '        if f.len() != 6 || f[0].is_empty() || f[1].is_empty() || f[4].is_empty() {' \
   '        if false && f.len() != 6 || f[0].is_empty() || f[1].is_empty() || f[4].is_empty() {' \
   the_knife_file_is_read_raw_six_fields_a_line
# KC2 a knife put in above another leaves the other's lines where they were
kc '                s.0 = (s.0 as isize + grow) as usize;' \
   '                let _ = grow; // knife' \
   knives_share_a_batch_unless_they_touch_the_same_text
# KC3 an error anywhere in the knife's file is the knife's
kc '                && (p.first..=p.last).contains(&d.line)' \
   '                && true // knife' \
   an_error_on_a_knifes_lines_is_that_knifes_and_warnings_are_not_errors
# KC4 a warning counts as an error
kc '        let Some(at) = l.find(": error") else {' \
   '        let Some(at) = l.find(": ") else { // knife' \
   an_error_on_a_knifes_lines_is_that_knifes_and_warnings_are_not_errors
# KC5 a batch's files are not put back
kc '            let _ = std::fs::write(self.root.join(rel), orig);' \
   '            let _ = (&self.root, rel, orig); // knife' \
   the_files_are_put_back_after_a_batch_and_after_a_kill
# KC6 a killed run's files are not put back
kc '            if std::fs::write(root.join(rel.trim()), orig).is_ok() {' \
   '            if false && std::fs::write(root.join(rel.trim()), orig).is_ok() {' \
   the_files_are_put_back_after_a_batch_and_after_a_kill
