#!/usr/bin/env bash
# aib-xtask knife-shards knives (Andy 11-22: crate-knives cells by each script's seconds, not by name order).
# Run by the "aib-xtask knives (macOS)" step in aibd.yml (one line runs xtask/knives/*.sh).
# Run from the repo root.
#
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ks() { python3 -I scripts/knife.py xtask/src/knife_shards.rs "$1" "$2" --expect-fail "$3" \
       -- cargo test -p aib-xtask --locked "knife_shards::tests::$3"; }
# KH1 lightest first: the two heavy scripts are placed last, wherever the light ones left room
ks '    order.sort_by(|a, b| b.0.cmp(&a.0).then(a.1.as_bytes().cmp(b.1.as_bytes())));' \
   '    order.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.as_bytes().cmp(b.1.as_bytes())));' \
   the_heaviest_scripts_go_to_different_cells_and_every_script_gets_one_cell
# KH2 the fullest cell takes the next script: everything lands in cell 1
ks '            if *l < load[k] {' \
   '            if *l > load[k] {' \
   the_heaviest_scripts_go_to_different_cells_and_every_script_gets_one_cell
# KH3 a script the table does not know weighs next to nothing instead of the median
ks '    median(&secs.values().copied().collect::<Vec<_>>())' \
   '    median(&secs.values().copied().collect::<Vec<_>>()).min(1)' \
   a_script_the_table_does_not_know_weighs_the_tables_median_and_a_stale_line_gets_no_cell
# KH4 a path that is in the table twice is taken (the later line wins without a word)
ks '        if out.insert(path.to_string(), secs).is_some() {' \
   '        if out.insert(path.to_string(), secs).is_some() && false {' \
   a_table_line_that_is_not_seconds_tab_path_is_refused_with_its_line
# KH5 seconds that are not digits are let through to the parser's own reading
ks "        if secs.is_empty() || !secs.bytes().all(|b| b.is_ascii_digit()) {" \
   "        if secs.is_empty() {" \
   a_table_line_that_is_not_seconds_tab_path_is_refused_with_its_line
# KH6 a script listed twice gets two cells
ks '        if !seen.insert(line.to_string()) {' \
   '        if !seen.insert(line.to_string()) && false {' \
   a_list_with_a_path_twice_or_no_path_is_refused
# KH7 red runs count towards a script's seconds
ks '            let Some((file, tail)) = rest.split_once(": rc=0 secs=") else {' \
   '            let Some((file, tail)) = rest.split_once(" secs=") else {' \
   the_table_is_the_median_of_the_green_runs_only
# KH-G1 a light script a few seconds over is said to have grown (every run would shout)
ks '            Some(&t) if n > t.saturating_mul(GREW_TIMES) && n >= t.saturating_add(GREW_MORE_S) => {' \
   '            Some(&t) if n > t.saturating_mul(GREW_TIMES) => {' \
   a_script_far_over_its_seconds_is_said_and_one_a_little_over_is_not
# KH-G2 a script exactly at twice its seconds is said to have grown
ks '            Some(&t) if n > t.saturating_mul(GREW_TIMES) && n >= t.saturating_add(GREW_MORE_S) => {' \
   '            Some(&t) if n >= t.saturating_mul(GREW_TIMES) && n >= t.saturating_add(GREW_MORE_S) => {' \
   a_script_far_over_its_seconds_is_said_and_one_a_little_over_is_not
# KH-G3 a heavy script the table does not know is not said
ks '            None if n >= GREW_MORE_S => Some(Grew::NewHeavy {' \
   '            None if n > GREW_MORE_S => Some(Grew::NewHeavy {' \
   a_script_far_over_its_seconds_is_said_and_one_a_little_over_is_not
# --- est_script_secs (90856 11-25, DECISION-knife-size-objective-08b0e-1125 §1) ---
# KES1 the same-test-target level is never used: a new page script is judged by aibd's 27s a knife
ks '    let (source, key, samples) = if by_target.len() >= EST_MIN_TARGET {' \
   '    let (source, key, samples) = if by_target.len() >= EST_MIN_TARGET + 99 {' \
   a_new_page_script_is_estimated_by_the_page_scripts_not_by_the_crate
# KES2 one crate sample is enough: two scripts decide a crate's speed
ks '    } else if by_crate.len() >= EST_MIN_CRATE {' \
   '    } else if by_crate.len() >= 1 {' \
   too_few_samples_fall_back_from_target_to_crate_to_repo_and_rounding_is_up
# KES3 the estimate is rounded down
ks '        secs: total_ms.div_ceil(1000),' \
   '        secs: total_ms / 1000,' \
   too_few_samples_fall_back_from_target_to_crate_to_repo_and_rounding_is_up
# KES4 plan / loads drop the estimate and weigh a new script as the median
ks '        .or_else(|| est.get(&norm_path(p)))' \
   '        .or_else(|| est.get(&norm_path(p)).filter(|_| false))' \
   plan_and_loads_weigh_a_new_script_by_its_estimate
# KES5 Windows separators are not folded: crates\aibd\… has no crate and no table match
ks "    path.replace('\\\\', \"/\")" \
   '    path.to_string()' \
   windows_separators_give_the_same_script_crate_and_estimate
# KES6 check_size does not refuse a script estimated over the limit
ks '            if e.secs > max_secs {' \
   '            if e.secs > max_secs.saturating_mul(10) {' \
   a_heavy_new_script_is_refused_with_its_source_samples_and_a_split
# KES7 the test name stays in the target: no two scripts share one
ks '            Some(t) => format!("cargo -p {pkg} --test {t}"),' \
   '            Some(t) => format!("cargo -p {pkg} --test {t} {:?}", cmd.last()),' \
   a_new_page_script_is_estimated_by_the_page_scripts_not_by_the_crate
# KES8 est lines are taken as samples: two guessed page lines decide a new page script
ks '        if est_rows.contains(path) {' \
   '        if est_rows.contains(path) && path.is_empty() {' \
   an_est_line_weighs_its_script_but_is_no_sample_and_a_bad_third_column_is_refused
# KES9 any third column is read as a measured line
ks '            Some(_) => return Err(bad()),' \
   '            Some((p, _)) => (p, false),' \
   an_est_line_weighs_its_script_but_is_no_sample_and_a_bad_third_column_is_refused
