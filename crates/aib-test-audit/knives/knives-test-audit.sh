#!/usr/bin/env bash
# aib-test-audit knives (S69-u4, 1ced7 11-17; SPEC-shadow-u4 r1 §7, 5d33a verifies). Run from the repo root
# by the "aib-test-audit knives (macOS)" step in aibd.yml (moved out of aibd.yml: it is near its
# 450,000-byte line). Each knife takes one rule away and must turn its own test red; RUSTFLAGS has
# -D warnings, so every knife keeps the code building.
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
ta() { python3 -I scripts/knife.py crates/aib-test-audit/src/lib.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-test-audit --locked --test audit "$3"; }
# TA1 (§7 1) a different outcome alone is not a mismatch
ta '    if audit.outcome != source.outcome' '    if false' \
   compare_names_what_differs
# TA2 (§7 1) a revoked unit is reused again
ta '        ("unit", ask.unit),' '        ("unit", ""), // knife' \
   a_forged_source_is_a_mismatch_and_the_unit_is_revoked
# TA3 (§7 2) every source under the rule version is suspected, not only the same invocation's
ta '                && s.invocation_id == bad.invocation_id' '                && !s.invocation_id.is_empty() // knife' \
   sources_written_beside_a_bad_one_are_suspect_until_lifted
# TA4 (§7 3) a retry after a red counts as flaky evidence
ta '    for r in runs.iter().filter(|r| !r.retry_after_red) {' \
   '    for r in runs.iter().filter(|r| !r.retry_after_red || true) { // knife' \
   flaky_needs_both_results_on_the_same_key_and_environment
# TA5 (§7 4) a side effect is reusable
ta '    if d.side_effect {' '    if d.side_effect && false { // knife' \
   each_never_reuse_class_is_named_and_reads_back
# TA6 (§7 5) a round exactly one period old is not due
ta '    if age >= period_days * DAY {' '    if age > period_days * DAY { // knife' \
   the_defense_round_is_due_when_overdue_first_or_after_a_change
# TA7 (§7 6) the defense round reports what matched and hides what did not
ta '            (c != Compare::Same).then(|| (unit.clone(), c))' \
   '            (c == Compare::Same).then(|| (unit.clone(), c)) // knife' \
   the_defense_round_finds_a_unit_that_would_have_reused_a_stale_result
# TA8 (§7 7) the draw ignores the seed, so the ledger's seed cannot redraw it
ta '    let x = mix(seed ^ fnv(unit));' '    let x = mix(seed.wrapping_mul(0) ^ fnv(unit)); // knife' \
   the_same_seed_draws_the_same_audits
# TA9 (§7 8) an unreadable table allows every reuse
ta '        Err(why) => return Some(format!("run_source_revoked:table_unreadable:{why}")),' \
   '        Err(_why) => return None, // knife' \
   a_broken_table_refuses_every_reuse
# TA10 (§7 9) a kind audited far below the others is not forced
ta '        Some((x, y)) if *y > 0 => (*x as f64 / *y as f64) < overall / 2.0,' \
   '        Some((x, y)) if *y > 0 => (*x as f64 / *y as f64) < overall / 200.0, // knife' \
   forced_audits_cover_streaks_age_rare_kinds_and_unfinished_audits
# TA11 (5d33a f6c37623 M1) a forced unit skips the random draw, so the sample misses the units reused longest
ta '    if draw(seed, unit) < p {' '    if forced.is_empty() && draw(seed, unit) < p { // knife' \
   a_forced_unit_can_still_be_drawn_into_the_random_sample
