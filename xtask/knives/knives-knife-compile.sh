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
# KC7–KC12 (N-kc-bisect, a9136 11-21; DESIGN-kc-bisect-a9136-1121): an error on no knife's lines
# splits the batch down to one knife; a knife is ok when it compiles alone; past the limit, unsure
# KC7 one knife that compiles alone is called an error
kc '        verdict.insert(*one, said.map_or(Verdict::Ok, Verdict::Error));' \
   '        verdict.insert(*one, Verdict::Error(said.unwrap_or_default()));' \
   two_knives_that_only_fail_together_are_both_ok_and_take_nobody_with_them
# KC8 a half with an error elsewhere is called ok without being split
kc '            Some(m) => settle(&rest, &m, used, look, verdict)?,' \
   '            Some(m) => { let _ = &m; for i in rest { verdict.insert(i, Verdict::Ok); } }' \
   a_knife_that_fails_alone_is_the_one_error_with_its_own_message
# KC9 the limit on checks does not hold for a half
kc '        let out_of_checks = *used >= CHECKS_MAX;' \
   '        let out_of_checks = *used >= CHECKS_MAX && false;' \
   past_the_limit_the_knives_left_are_unsure_and_never_ok
# KC10 a knife not checked past the limit is ok
kc '                Verdict::Unsure(format!("{why} (not settled: {CHECKS_MAX} checks made)")),' \
   '                { let _ = why; Verdict::Ok },' \
   past_the_limit_the_knives_left_are_unsure_and_never_ok
# KC11 an error on a knife's own lines in a half is not kept
kc '            verdict.insert(*knife, Verdict::Error(said.clone()));' \
   '            let _ = (knife, said);' \
   an_error_on_a_knifes_own_lines_is_that_knifes_and_the_rest_of_its_half_is_judged
# KC12 an unsure knife does not keep the run red
kc '    u8::from(err + anc + uns > 0)' \
   '    u8::from(err + anc > 0)' \
   past_the_limit_the_knives_left_are_unsure_and_never_ok
