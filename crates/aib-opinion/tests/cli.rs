//! The `aib-opinion` binary end to end, on files in a folder whose name has a space.

use std::path::{Path, PathBuf};
use std::process::{Command, Output};

const OP_1CED7: &str = "---\nstatus: 意見\nowner: 1ced7\n---\n# 1ced7 意見\n結論: 有條件 H/M/L: 1/1/0\n## 第 1 題\n- O1 [H] A | at once contradicts the 2-hour rule → only record a candidate\n- O2 [M] B | keep the tree → clean target only\n- O3 [N] 題 3 | agree\n";
const OP_CODEX: &str = "結論: 同意 H/M/L: 0/0/1\n- O1 [L] 題 2 | name the stash rule\n";
const FINAL: &str = "# 定稿\n## 一、掛點\n- A 只登記(1ced7-O1)\n- 不採 1ced7-O2\n## 二、規則\n- stash 也算改動(codex-O1)\n";

struct Dir {
    _t: tempfile::TempDir,
    root: PathBuf,
}

impl Dir {
    fn new() -> Dir {
        let t = tempfile::tempdir().expect("temp dir");
        let root = t.path().join("council round");
        std::fs::create_dir(&root).expect("make the round folder");
        Dir { _t: t, root }
    }
    fn file(&self, name: &str, text: &str) -> String {
        let p = self.root.join(name);
        std::fs::write(&p, text).expect("write a fixture");
        p.to_string_lossy().into_owned()
    }
    fn run(&self, args: &[&str]) -> Output {
        Command::new(env!("CARGO_BIN_EXE_aib-opinion"))
            .args(args)
            .current_dir(&self.root)
            .output()
            .expect("run aib-opinion")
    }
}

fn out(o: &Output) -> String {
    String::from_utf8_lossy(&o.stdout).into_owned()
}
fn err(o: &Output) -> String {
    String::from_utf8_lossy(&o.stderr).into_owned()
}
fn rc(o: &Output) -> i32 {
    o.status.code().unwrap_or(-1)
}

fn round(d: &Dir) -> (String, String, String) {
    (
        d.file("OPINION-autoclean-1ced7-1115.md", OP_1CED7),
        d.file("OPINION-autoclean-codex-1115.md", OP_CODEX),
        d.file("DECISION-council-autoclean-519d0-1115.md", FINAL),
    )
}

#[test]
fn check_passes_good_files_and_names_every_problem_by_file_and_line() {
    let d = Dir::new();
    let (a, b, _) = round(&d);
    let o = d.run(&["check", &a, &b]);
    assert_eq!(rc(&o), 0, "{}", err(&o));
    assert_eq!(
        out(&o),
        format!("ok {a}: 3 item(s), seat 1ced7\nok {b}: 1 item(s), seat codex\n")
    );

    let bad = d.file(
        "OPINION-autoclean-ebb36-1115.md",
        "結論: 有條件 H/M/L: 1/0/0\n- O1 [H] A | no fix given\n- O2 [Q] B | x\n",
    );
    let o = d.run(&["check", &a, &bad]);
    assert_eq!(rc(&o), 2);
    assert_eq!(
        out(&o),
        "",
        "nothing printed as ok when any file has a problem"
    );
    let e = err(&o);
    assert!(
        e.contains(&format!("{bad}:2: O1: [H] needs `→ <fix>`\n")),
        "{e}"
    );
    assert!(
        e.contains(&format!("{bad}:3: O2: [Q] is not H, M, L or N\n")),
        "{e}"
    );
    assert!(
        e.contains(&format!(
            "{bad}:1: 結論 says H/M/L 1/0/0 but the items are 0/0/0\n"
        )),
        "{e}"
    );
    assert!(e.ends_with("aib-opinion: 3 problem(s)\n"), "{e}");
}

#[test]
fn table_prints_each_landing_and_strict_fails_only_on_a_gap() {
    let d = Dir::new();
    let (a, b, f) = round(&d);
    let o = d.run(&["table", "--final", &f, &a, &b, "--strict"]);
    assert_eq!(rc(&o), 0, "{}", err(&o));
    let t = out(&o);
    assert!(
        t.starts_with("| 來源 | 條 | 嚴重度 | 章節 | 意見 | 落點 |\n|---|---|---|---|---|---|\n"),
        "{t}"
    );
    assert!(t.contains("| 1ced7 | O1 | H | A | at once contradicts the 2-hour rule → only record a candidate | 一、掛點(第 3 行) |\n"), "{t}");
    assert!(t.contains("| 1ced7 | O2 | M | B | keep the tree → clean target only | 不採納:一、掛點(第 4 行) |\n"), "{t}");
    assert!(t.contains("| 1ced7 | O3 | N | 題 3 | agree | — |\n"), "{t}");
    assert!(
        t.contains("| codex | O1 | L | 題 2 | name the stash rule | 二、規則(第 6 行) |\n"),
        "{t}"
    );
    assert!(t.ends_with("\n未落點:無\n"), "{t}");

    let gap = d.file(
        "DECISION-gap.md",
        &FINAL.replace("(codex-O1)", "(codex-O7)"),
    );
    let o = d.run(&["table", "--final", &gap, &a, &b]);
    assert_eq!(rc(&o), 0, "without --strict a gap is listed, not failed");
    let t = out(&o);
    assert!(
        t.contains("\n未落點:1 條\n- codex-O1 [L] 題 2 | name the stash rule\n"),
        "{t}"
    );
    assert!(t.contains("\n引用不到:1 處\n- 第 6 行 codex-O7\n"), "{t}");
    let o = d.run(&["table", "--strict", "--final", &gap, &a, &b]);
    assert_eq!(rc(&o), 2);

    let typo_only = d.file("DECISION-typo.md", &format!("{FINAL}- 另見 1ced7-O9\n"));
    let o = d.run(&["table", "--final", &typo_only, &a, &b, "--strict"]);
    assert_eq!(
        rc(&o),
        2,
        "a citation that matches no item fails --strict on its own"
    );
}

#[test]
fn table_refuses_bad_opinions_and_two_files_from_one_seat() {
    let d = Dir::new();
    let (a, b, f) = round(&d);
    let bad = d.file("OPINION-x-ebb36-1115.md", "- O1 [L] A | x\n");
    let o = d.run(&["table", "--final", &f, &a, &bad]);
    assert_eq!(rc(&o), 2);
    assert_eq!(out(&o), "");
    assert!(
        err(&o).contains("fix the opinion files first (aib-opinion check)"),
        "{}",
        err(&o)
    );

    let again = d.file("OPINION-autoclean-codex-1116.md", OP_CODEX);
    let o = d.run(&["table", "--final", &f, &a, &b, &again]);
    assert_eq!(rc(&o), 2);
    assert!(
        err(&o).contains("two opinion files are both from seat codex"),
        "{}",
        err(&o)
    );
}

#[test]
fn table_json_lists_rows_unlanded_and_unknown() {
    let d = Dir::new();
    let (a, b, _) = round(&d);
    let gap = d.file(
        "DECISION-gap.md",
        &FINAL.replace("(codex-O1)", "(codex-O7)"),
    );
    let o = d.run(&["table", "--json", "--final", &gap, &a, &b]);
    assert_eq!(rc(&o), 0, "{}", err(&o));
    let j: serde_json::Value = serde_json::from_str(out(&o).trim()).unwrap();
    assert_eq!(j["rows"].as_array().unwrap().len(), 4);
    assert_eq!(j["rows"][0]["cite"], "1ced7-O1");
    assert_eq!(j["rows"][0]["landings"][0]["heading"], "一、掛點");
    assert_eq!(j["unlanded"], serde_json::json!(["codex-O1"]));
    assert_eq!(
        j["unknown"],
        serde_json::json!([{"cite": "codex-O7", "line": 6}])
    );
}

#[test]
fn usage_and_unreadable_files_have_their_own_codes() {
    let d = Dir::new();
    let (a, _, f) = round(&d);
    for args in [
        vec![],
        vec!["nope"],
        vec!["check"],
        vec!["check", "--json", a.as_str()],
        vec!["table", a.as_str()],
        vec!["table", "--final", f.as_str()],
        vec!["table", "--final"],
        vec![
            "table",
            "--final",
            f.as_str(),
            "--final",
            f.as_str(),
            a.as_str(),
        ],
        vec!["table", "--final", f.as_str(), "--wide", a.as_str()],
    ] {
        let o = d.run(&args);
        assert_eq!(rc(&o), 64, "{args:?}: {}", err(&o));
        assert!(err(&o).contains("usage: aib-opinion check"), "{args:?}");
    }
    let o = d.run(&["--help"]);
    assert_eq!(rc(&o), 0);
    assert!(out(&o).contains("- O<n> [H|M|L|N] <section> | <opinion> → <fix>"));

    let missing = Path::new(&d.root).join("missing.md");
    let missing = missing.to_string_lossy();
    let o = d.run(&["check", &missing]);
    assert_eq!(rc(&o), 4);
    let o = d.run(&["table", "--final", &missing, &a]);
    assert_eq!(rc(&o), 4);
}
