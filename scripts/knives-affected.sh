#!/usr/bin/env bash
# The "knife-affected knives (macOS)" step of aibd.yml (S66-4, 7b81e), moved here word for word
# (519d0 11-16 23:5x: the workflow file is near its 450,000-byte guard; new knives no longer go inline).
# aib-xtask knife-anchors reads scripts/*.sh. Run from the repo root.
set -eo pipefail
set -eu
kf() { python3 -I scripts/knife.py xtask/src/knife_affected.rs "$1" "$2" \
         --expect-fail "$3" -- cargo test -p aib-xtask --locked "$3"; }
# KF1 a push (not a pull request) is judged like one
kf '    if i.event != "pull_request" {' '    if i.event != "pull_request" && i.event != "push" { // knife' \
  what_is_not_a_pull_request_affects_every_knife
# KF2 a missing base alone is not noticed
kf '    if i.base.is_empty() || i.head.is_empty() {' '    if i.base.is_empty() && i.head.is_empty() { // knife' \
  unreadable_inputs_affect_every_knife
# KF3 one knife-contract problem is let through
kf '    if i.unresolved > 0 {' '    if i.unresolved > 1 { // knife' unreadable_inputs_affect_every_knife
# KF4 cargo metadata not read is not a reason for every knife
kf '        return Some(("metadata_unread", format!("cargo metadata not read: {e}")));' '        let _ = e; // knife' \
  unreadable_inputs_affect_every_knife
# KF5 the diff not read reads as "nothing changed"
kf '        Err(e) => return Some(("diff_unread", format!("changed paths not read: {e}"))),' \
  '        Err(_) => return None, // knife' unreadable_inputs_affect_every_knife
# KF6 a path git quotes is matched as if it were plain
kf "        if f.starts_with('\"') {" "        if f.starts_with(\"\\\"\\\"\") { // knife" \
  unreadable_inputs_affect_every_knife
# KF7 a fixed folder (scripts/, xtask/, .cargo/) only counts as itself
kf '                f.starts_with(x)' '                f.ends_with(x) // knife' the_fixed_set_affects_every_knife
# KF8 a fixed file other than Cargo.* is missed
kf '                f == x' '                f.starts_with("Cargo") // knife' the_fixed_set_affects_every_knife
# KF9 a command that is not cargo is read as cargo
kf '    if cmd.first().map(String::as_str) != Some("cargo") {' '    if cmd.is_empty() { // knife' \
  a_command_not_read_affects_its_knife
# KF10 --workspace next to -p is ignored
kf '        } else if a == "--workspace" || a == "--all" {' '        } else if a == "--all" { // knife' \
  cmd_packages_reads_the_p_forms
# KF11 --manifest-path PATH (two words) is ignored
kf '        } else if a == "--manifest-path" || a.starts_with("--manifest-path=") {' \
  '        } else if a.starts_with("--manifest-path=") { // knife' a_command_not_read_affects_its_knife
# KF12 a command without -p tests nothing
kf '    if out.is_empty() {' '    if out.is_empty() && cmd.len() > 99 { // knife' a_command_not_read_affects_its_knife
# KF13 a package outside the workspace is left out of the closure
kf '        let p = find(&n).ok_or(format!("{n} is not a workspace crate"))?;' \
  '        let Some(p) = find(&n) else { continue }; // knife' a_command_not_read_affects_its_knife
# KF14 a crate at the workspace root is a normal closure folder
kf "        if dir.is_empty() || dir.starts_with('/') {" "        if dir.starts_with('/') { // knife" \
  closure_folders_are_the_path_dependencies
# KF15 an unvetted build.rs passes
kf '        if root.join(&build).exists() && !VETTED_BUILD_RS.contains(&build.as_str()) {' \
  '        if root.join(&build).exists() && VETTED_BUILD_RS.contains(&build.as_str()) { // knife' \
  an_unvetted_build_rs_affects_its_knife
# KF16 the tested crate's own dev-dependencies are left out
kf '            if d["kind"].as_str() == Some("dev") && n != krate {' \
  '            if d["kind"].as_str() == Some("dev") { // knife' closure_folders_are_the_path_dependencies
# KF17 path dependencies are not followed
kf '            todo.push(name.to_string());' '            let _ = name; // knife' \
  closure_folders_are_the_path_dependencies
# KF18 the knife's own anchor file changing is not noticed
kf '    if let Some(f) = changed.iter().find(|f| **f == e.call.file || *f == src) {' \
  '    if let Some(f) = changed.iter().find(|f| *f == src) { // knife' its_anchor_file_or_script_changing_affects_it
# KF19 only a closure folder itself counts, not the files in it
kf '            if let Some(f) = changed.iter().find(|f| covers(d, f)) {' \
  '            if let Some(f) = changed.iter().find(|f| *f == d) { // knife' \
  a_change_in_the_closure_affects_and_outside_does_not
# KF20 the verdicts are swapped
kf '                "verdict": if j.affected { "cannot_reuse" } else { "could_reuse" },' \
  '                "verdict": if j.affected { "could_reuse" } else { "cannot_reuse" }, // knife' \
  ledger_rows_stay_out_of_the_cireuse_units
# KF21 rows land in aib-cireuse's cell units (its reasons summary would count them)
kf '                "unit": format!("knife:{}/{}", j.krate, j.name),' \
  '                "unit": format!("cell:{}/{}", j.krate, j.name), // knife' \
  ledger_rows_stay_out_of_the_cireuse_units
# KF22 --drop-step must match the whole step name
kf '                    .any(|d| e.knife.step.contains(d.as_str()))' '                    .any(|d| e.knife.step == *d) // knife' \
  select_by_job_source_and_dropped_steps
# KF23 (5d33a L1) what a crate reads outside its folder is left out of its closure
kf '                .filter(|(c, _)| *c == n)' '                .filter(|(c, _)| *c == "-") // knife' \
  what_a_crate_reads_outside_its_folder_is_in_its_closure
# KF24 a `*` pattern also covers deeper paths
kf '        return p.len() == f.len() && p.iter().zip(&f).all(|(a, b)| *a == "*" || a == b);' \
  '        return p.iter().zip(&f).all(|(a, b)| *a == "*" || a == b); // knife' \
  what_a_crate_reads_outside_its_folder_is_in_its_closure
# KF25 a crate that reads outside its folder and is not listed passes the repo check
kf '    ("aib-store", "migrations/"),' '    ("aib-storex", "migrations/"), // knife' \
  the_repo_crates_read_outside_only_what_is_listed
# S66-4 segment 1 (7b81e, council r2 11-17): the gate's skip list
# KS1 one affected knife does not keep its step: the step is skipped with it
kf '        u.0 &= !j.affected;' '        u.0 &= true; // knife' \
  a_step_or_script_is_skipped_only_when_none_of_its_knives_is_affected
# KS2 a reason that affects every knife (a push, a release, anything not read) still skips
kf '    if all.is_some() || entries.len() != judged.len() {' '    if entries.len() != judged.len() { // knife' \
  a_step_or_script_is_skipped_only_when_none_of_its_knives_is_affected
# KS3 a workflow change is reported as any fixed path (the ledger cannot tell why everything ran)
kf '                let code = if x.starts_with(".github/workflows/") {' '                let code = if false { // knife' \
  every_knife_affected_has_a_code_for_the_ledger
# KS4 the line counts skipped knives as run
kf '        total - skipped.min(total),' '        total, // knife' \
  the_affected_line_and_the_skip_output_have_their_fixed_shape
