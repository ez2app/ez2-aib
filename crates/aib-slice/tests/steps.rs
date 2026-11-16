//! RULES §25 (S79 C1a): the workflow steps main has must still be there after a merge. A
//! stale hand resolution (rerere) once dropped another PR's knife step without a conflict.

use aib_slice::{step_names, steps_lost};

const MAIN: &str = "jobs:\n  rust:\n    name: rust\n    steps:\n      - uses: actions/checkout@v4\n      - name: fmt\n        run: cargo fmt\n      - name: knives (macOS)\n        run: |\n          x\n      - name: knives (macOS)\n        run: y\n";

#[test]
fn step_names_are_the_dash_name_lines_in_order() {
    assert_eq!(
        step_names(MAIN),
        vec!["fmt", "knives (macOS)", "knives (macOS)"]
    );
    // a job's own `name:` is not a step
    assert!(step_names("jobs:\n  a:\n    name: not a step\n").is_empty());
}

#[test]
fn a_step_main_has_and_the_merge_does_not_is_lost() {
    // one more step, nothing lost
    let more = format!("{MAIN}      - name: mine\n        run: z\n");
    assert!(steps_lost(MAIN, Some(&more)).is_empty());
    // the same file: nothing lost
    assert!(steps_lost(MAIN, Some(MAIN)).is_empty());
    // fmt replaced by another step
    let cut = MAIN.replace("- name: fmt", "- name: other");
    assert_eq!(steps_lost(MAIN, Some(&cut)), vec!["fmt"]);
    // a name main uses twice must be there twice
    let once = MAIN.replace("      - name: knives (macOS)\n        run: y\n", "");
    assert_eq!(steps_lost(MAIN, Some(&once)), vec!["knives (macOS)"]);
    // the file is gone: every step is lost
    assert_eq!(steps_lost(MAIN, None).len(), 3);
}
