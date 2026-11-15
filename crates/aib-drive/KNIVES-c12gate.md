---
status: 定稿
rev: r0
node: S79-c12gate
owner: b433b
---
# S79-c12gate knives (node gate: biometric / human_present)

CG1–CG4 in `crates/aib-drive/src/next_local.rs` (`cargo test -p aib-drive --test next_local <test>`), CG5 in `crates/aib-plan/src/gate.rs` (`cargo test -p aib-plan --lib <test>`). Every anchor occurs once in its file. For the integration seat to put into aibd.yml (RULES §21).

| knife | what goes wrong | old | new | test |
|---|---|---|---|---|
| CG1 | a biometric / human_present node is handed out as a row | `Some((g, why)) => Some(((*g).to_owned(), (*why).to_owned())),` | `Some(_) => None,` | a_gated_node_is_never_a_row_and_goes_to_andy_with_the_reason |
| CG2 | an unknown gate word is taken as a known one | `Ok(w) => match GATES.iter().find(\|(g, _)\| g == w) {` | `Ok(w) => match GATES.iter().find(\|(g, _)\| g == w \|\| !w.is_empty()) {` | a_gate_that_is_not_a_known_word_holds_the_node_too_and_no_gate_changes_nothing |
| CG3 | a gate that is not a string is ignored and the node handed out | `Some(other) => Some(Err(other.to_string().chars().take(40).collect())),` | `Some(_) => None,` | a_gate_that_is_not_a_known_word_holds_the_node_too_and_no_gate_changes_nothing |
| CG4 | layer 5 never reads the gate | `if let Some((kind, why)) = gate_hold(n.gate.as_ref()) {` | `if let Some((kind, why)) = gate_hold(n.gate.as_ref().filter(\|_\| false)) {` | a_gated_node_is_never_a_row_and_goes_to_andy_with_the_reason |
| CG5 | aib-plan --check takes any gate value | `if !g.as_str().is_some_and(\|w\| GATES.contains(&w)) {` | `if false && !g.as_str().is_some_and(\|w\| GATES.contains(&w)) {` | gate::tests::check_takes_the_two_gate_words_and_reports_any_other_value |

## 5d33a M1 on 5b7abf1d: the gate also holds layers 6 and 7 (one check, `node_gate`)

| 刀 | 改 | 該紅的測試 |
|---|---|---|
| CG6 | `node_gate` 永遠回 None(共用檢查拿掉) | `a_tidy_duplicate_hung_on_a_gated_node_is_skipped_with_the_reason` |
| CG7 | 第 6 層不看 gate | `an_idea_hung_on_a_gated_node_is_skipped_with_the_reason` |
| CG8 | 第 7 層不看 gate | `a_tidy_duplicate_hung_on_a_gated_node_is_skipped_with_the_reason` |
