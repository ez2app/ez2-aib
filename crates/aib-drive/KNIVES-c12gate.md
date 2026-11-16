---
status: 定稿
rev: r1
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

Same file and command as CG1–CG4 (`cargo test -p aib-drive --test next_local <test>`). These are the exact strings the receipt ran; each occurs once in the file.

| knife | what goes wrong | old | new | test |
|---|---|---|---|---|
| CG6 | the shared check never finds a gate (layers 6 and 7 both send) | `.and_then(\|n\| gate_hold(n.gate.as_ref()))` | `.and_then(\|n\| gate_hold(n.gate.as_ref()).filter(\|_\| false))` | a_tidy_duplicate_hung_on_a_gated_node_is_skipped_with_the_reason |
| CG7 | layer 6 does not look at the gate | `node_gate(&ns, node)` | `node_gate(&ns, node).filter(\|_\| false)` | an_idea_hung_on_a_gated_node_is_skipped_with_the_reason |
| CG8 | layer 7 does not look at the gate | `node_gate(&ns, n.id)` | `node_gate(&ns, n.id).filter(\|_\| false)` | a_tidy_duplicate_hung_on_a_gated_node_is_skipped_with_the_reason |

> 11-15 RULES §21(新版):aib-drive 的刀已從 aibd.yml 搬到 `crates/aib-drive/knives/knives-NN-<step>.sh`(一個舊 step 一支),aibd.yml 只留一行「aib-drive knives (macOS)」照檔名順序跑全部。新刀加在對應那支腳本，不再加進 aibd.yml。
