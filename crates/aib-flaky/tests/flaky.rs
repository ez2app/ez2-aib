// SPDX-License-Identifier: Apache-2.0
//! Each rule of the suspected-flaky decision has a case that holds and one that does not.
//! Knife ids (K1–K12) are the ones in DESIGN-flaky-suspect-1ced7-1109.md.

use aib_cireuse::gh::Source;
use aib_flaky::gh::{b64, gather};
use aib_flaky::*;
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet};

const R: &str = "o/r";
const HEAD: &str = "h1";

fn step(name: &str, c: &str) -> Step {
    Step {
        name: name.into(),
        conclusion: Some(c.into()),
    }
}

fn att(
    run: u64,
    a: u32,
    job_id: u64,
    concl: &str,
    log: Option<&str>,
    steps: Vec<Step>,
) -> JobAttempt {
    JobAttempt {
        run_id: run,
        attempt: a,
        workflow: ".github/workflows/ci.yml".into(),
        job_id,
        job: "test (ubuntu-latest, 3.12)".into(),
        conclusion: Some(concl.into()),
        steps,
        log: log.map(str::to_string),
    }
}

const PY_RED: &str = "2026-11-09T02:53:17.6272667Z ##[error]aib/tests/test-x.py failed or hung\n";

fn changed(paths: &[(&str, &str)]) -> Vec<Changed> {
    paths
        .iter()
        .map(|(p, s)| Changed {
            path: (*p).into(),
            status: (*s).into(),
        })
        .collect()
}

fn facts(changed: Vec<Changed>, attempts: Vec<JobAttempt>) -> Facts {
    Facts {
        repo: R.into(),
        pr: Some(7),
        head: HEAD.into(),
        base: "b".into(),
        changed,
        attempts,
        rust: None,
        py_sources: BTreeMap::from([(
            "aib/tests/test-x.py".to_string(),
            Some("import unittest\nfrom aib_knife_copy import knife\n".to_string()),
        )]),
    }
}

fn red_then_green() -> Vec<JobAttempt> {
    vec![
        att(
            1,
            1,
            10,
            "failure",
            Some(PY_RED),
            vec![step("Python suite", "failure")],
        ),
        att(
            1,
            2,
            11,
            "success",
            None,
            vec![step("Python suite", "success")],
        ),
    ]
}

fn prior(head: &str, fp: &str) -> Vec<Entry> {
    vec![Entry {
        repo: R.into(),
        pr: Some(3),
        head: head.into(),
        base: "b0".into(),
        workflow: ".github/workflows/ci.yml".into(),
        job: "test (ubuntu-latest, 3.12)".into(),
        run_id: 99,
        attempt: 1,
        job_id: 990,
        failed_steps: vec![],
        fingerprints: vec![fp.into()],
        coarse: false,
        verdict: "unclassified".into(),
        reason: "no_precedent".into(),
        cell_attempts: vec![],
        precedent_ok: true,
    }]
}

fn one(f: &Facts, p: &[Entry]) -> (String, String) {
    let e = judge(f, p);
    assert_eq!(e.len(), 1, "{e:?}");
    (e[0].verdict.clone(), e[0].reason.clone())
}

// ---------- the decision ----------

#[test]
fn all_seven_hold_is_suspected_flaky() {
    let f = facts(
        changed(&[
            ("README.md", "modified"),
            ("aib/tests/test-y.py", "modified"),
        ]),
        red_then_green(),
    );
    let (v, r) = one(&f, &prior("other", "py:aib/tests/test-x.py"));
    assert_eq!(v, "suspected_flaky", "{r}");
}

#[test]
fn k1_no_green_rerun_on_same_head() {
    let f = facts(
        changed(&[("README.md", "modified")]),
        vec![red_then_green().remove(0)],
    );
    let (v, r) = one(&f, &prior("other", "py:aib/tests/test-x.py"));
    assert_eq!((v.as_str(), r.as_str()), ("unclassified", "no_green_rerun"));
}

#[test]
fn k1_green_in_another_run_of_same_cell_counts() {
    // a second run on the same head (e.g. ready_for_review) that went green
    let mut a = red_then_green();
    a[1].run_id = 2;
    a[1].attempt = 1;
    let f = facts(changed(&[("README.md", "modified")]), a);
    assert_eq!(
        one(&f, &prior("other", "py:aib/tests/test-x.py")).0,
        "suspected_flaky"
    );
}

#[test]
fn k1_green_in_another_job_name_does_not_count() {
    let mut a = red_then_green();
    a[1].job = "test (macos-latest, 3.12)".into();
    let f = facts(changed(&[("README.md", "modified")]), a);
    let e = judge(&f, &prior("other", "py:aib/tests/test-x.py"));
    assert_eq!(e[0].reason, "no_green_rerun");
}

#[test]
fn k2_k11_precedent_must_be_another_head() {
    let f = facts(changed(&[("README.md", "modified")]), red_then_green());
    let (v, r) = one(&f, &[]);
    assert_eq!(v, "unclassified");
    assert_eq!(r, "no_precedent: py:aib/tests/test-x.py");
    // same head in the register is not a precedent
    assert_eq!(one(&f, &prior(HEAD, "py:aib/tests/test-x.py")).1, r);
    // another fingerprint is not a precedent
    assert_eq!(one(&f, &prior("other", "py:aib/tests/test-z.py")).1, r);
}

#[test]
fn k3_k4_python_impact_is_conservative() {
    for (p, overlap) in [
        ("aib/src/aib/cli.py", true),
        ("aib/tests/_helpers.py", true),
        ("aib/tests/sub/test-q.py", true),
        ("crates/aib-core/src/lib.rs", true),
        (".github/workflows/ci.yml", true),
        ("aib/tests/test-x.py", true),
        ("docs/a/b.txt", false),
        ("CHANGELOG.md", false),
        ("tests/test-other.py", false),
    ] {
        let f = facts(changed(&[(p, "modified")]), red_then_green());
        let (v, r) = one(&f, &prior("other", "py:aib/tests/test-x.py"));
        if overlap {
            assert_eq!(r, "impact_overlap: py:aib/tests/test-x.py", "{p}");
        } else {
            assert_eq!(v, "suspected_flaky", "{p}: {r}");
        }
    }
}

#[test]
fn k9_test_added_by_this_pr() {
    let f = facts(
        changed(&[("aib/tests/test-x.py", "added")]),
        red_then_green(),
    );
    assert_eq!(
        one(&f, &prior("other", "py:aib/tests/test-x.py")).1,
        "pr_new_test: aib/tests/test-x.py"
    );
}

#[test]
fn k7_setup_step_is_infra_other_steps_are_not() {
    let log = "2026-11-09T02:53:17.6272667Z ##[error]Request failed with status code 504\n";
    for (st, want) in [
        ("Run actions/setup-python@v5", "infra_unverified"),
        ("Set up job", "infra_unverified"),
        ("install (pip)", "unclassified"),
        ("fmt", "unclassified"),
    ] {
        let a = vec![att(
            1,
            1,
            10,
            "failure",
            Some(log),
            vec![
                step("Run actions/checkout@v4", "success"),
                step(st, "failure"),
            ],
        )];
        let (v, r) = one(&facts(vec![], a), &[]);
        assert_eq!(v, want, "{st}: {r}");
        if want == "unclassified" {
            assert_eq!(r, "no_fingerprint");
        }
    }
    // a setup step red together with a test step = not infra
    let a = vec![att(
        1,
        1,
        10,
        "failure",
        Some(log),
        vec![
            step("Run actions/checkout@v4", "failure"),
            step("test", "failure"),
        ],
    )];
    assert_eq!(one(&facts(vec![], a), &[]).1, "no_fingerprint");
    // no red step listed at all = not infra
    let a = vec![att(1, 1, 10, "failure", Some(log), vec![])];
    assert_eq!(one(&facts(vec![], a), &[]).1, "no_fingerprint");
}

#[test]
fn k12_shard_problem_blocks() {
    let log = format!(
        "{PY_RED}2026-11-09T02:53:18.0000000Z ##[error]ci-shard missing aib/tests: test-q.py\n"
    );
    let mut a = red_then_green();
    a[0].log = Some(log);
    let f = facts(changed(&[("README.md", "modified")]), a);
    let (v, r) = one(&f, &prior("other", "py:aib/tests/test-x.py"));
    assert_eq!(v, "unclassified");
    assert!(r.starts_with("shard_problem: missing aib/tests"), "{r}");
}

#[test]
fn log_unreadable_is_unclassified() {
    let mut a = red_then_green();
    a[0].log = None;
    let f = facts(changed(&[("README.md", "modified")]), a);
    assert_eq!(
        one(&f, &prior("other", "py:aib/tests/test-x.py")).1,
        "log_unreadable"
    );
}

#[test]
fn every_failing_fingerprint_must_hold() {
    let log = format!("{PY_RED}2026-11-09T02:53:18.0000000Z ##[error]aib/tests/test-w.py still running after 600 s (killed with its children)\n");
    let mut a = red_then_green();
    a[0].log = Some(log);
    let f = facts(changed(&[("README.md", "modified")]), a);
    // precedent only for test-x
    assert_eq!(
        one(&f, &prior("other", "py:aib/tests/test-x.py")).1,
        "no_precedent: py:aib/tests/test-w.py"
    );
}

#[test]
fn denominator_lists_every_attempt_of_the_cell() {
    let f = facts(changed(&[("README.md", "modified")]), red_then_green());
    let e = judge(&f, &[]);
    assert_eq!(e[0].cell_attempts, vec!["1/1:failure", "1/2:success"]);
    assert_eq!(e[0].failed_steps, vec!["Python suite"]);
}

#[test]
fn cancelled_and_green_attempts_are_not_judged() {
    let a = vec![
        att(1, 1, 10, "cancelled", None, vec![]),
        att(1, 2, 11, "success", None, vec![]),
    ];
    assert!(judge(&facts(vec![], a), &[]).is_empty());
}

#[test]
fn main_push_is_recorded_as_precedent_only() {
    let mut f = facts(vec![], red_then_green());
    f.pr = None;
    f.head = "m1".into();
    let e = judge(&f, &[]);
    assert_eq!(e[0].reason, "main_push_recorded");
    // and it is a precedent for a PR on another head
    let pr = facts(changed(&[("README.md", "modified")]), red_then_green());
    assert_eq!(one(&pr, &e).0, "suspected_flaky");
}

// ---------- Rust ----------

const RS_RED: &str = "2026-11-09T02:53:17.1Z      Running tests/store.rs (target/debug/deps/store-0a1b2c3d)\n2026-11-09T02:53:17.2Z test opens_twice ... FAILED\n";

fn rust_map_fixture() -> RustMap {
    let files: Vec<String> = [
        "Cargo.toml",
        "crates/core/Cargo.toml",
        "crates/core/src/lib.rs",
        "crates/store/Cargo.toml",
        "crates/store/src/lib.rs",
        "crates/store/tests/store.rs",
        "crates/other/Cargo.toml",
        "crates/other/src/lib.rs",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    let read = |p: &str| -> Option<String> {
        Some(
            match p {
                "crates/core/Cargo.toml" => "[package]\nname = \"core\"\n",
                "crates/store/Cargo.toml" => "[package]\nname = \"store-x\"\n[dependencies]\ncore = { path = \"../core\" }\nserde = \"1\"\n",
                "crates/other/Cargo.toml" => "[package]\nname = \"other\"\n",
                _ => return None,
            }
            .to_string(),
        )
    };
    rust_map(&files, &read).expect("map")
}

fn rust_facts(paths: &[&str], map: Option<RustMap>) -> Facts {
    let mut a = red_then_green();
    a[0].log = Some(RS_RED.into());
    let mut f = facts(
        paths
            .iter()
            .map(|p| Changed {
                path: (*p).into(),
                status: "modified".into(),
            })
            .collect(),
        a,
    );
    f.rust = map;
    f
}

#[test]
fn rust_fingerprint_uses_crate_closure() {
    let fp = "rs:store::opens_twice";
    let p = prior("other", fp);
    for (path, overlap) in [
        ("crates/store/src/lib.rs", true),
        ("crates/core/src/lib.rs", true),
        ("Cargo.lock", true),
        ("Cargo.toml", true),
        (".cargo/config.toml", true),
        (".github/workflows/aibd.yml", true),
        ("crates/other/src/lib.rs", false),
        ("aib/src/x.py", false),
        ("crates/core2/src/lib.rs", false),
    ] {
        let (v, r) = one(&rust_facts(&[path], Some(rust_map_fixture())), &p);
        if overlap {
            assert_eq!(r, format!("impact_overlap: {fp}"), "{path}");
        } else {
            assert_eq!(v, "suspected_flaky", "{path}: {r}");
        }
    }
}

#[test]
fn k5_no_tree_is_unknown() {
    let (_, r) = one(
        &rust_facts(&["README.md"], None),
        &prior("other", "rs:store::opens_twice"),
    );
    assert!(
        r.starts_with("impact_unknown: rs:store::opens_twice"),
        "{r}"
    );
}

#[test]
fn k8_binary_in_two_crates_is_unknown() {
    let mut m = rust_map_fixture();
    m.bins
        .get_mut("store")
        .expect("bin")
        .insert("crates/other".into());
    let (_, r) = one(
        &rust_facts(&["README.md"], Some(m)),
        &prior("other", "rs:store::opens_twice"),
    );
    assert_eq!(
        r,
        "impact_unknown: rs:store::opens_twice: binary matches 2 crates"
    );
}

#[test]
fn not_understood_dependency_is_unknown() {
    let mut m = rust_map_fixture();
    m.deps.insert("crates/core".into(), None);
    let (_, r) = one(
        &rust_facts(&["README.md"], Some(m)),
        &prior("other", "rs:store::opens_twice"),
    );
    assert!(
        r.contains("crates/core: a dependency entry was not understood"),
        "{r}"
    );
}

#[test]
fn unseen_binary_is_unknown() {
    let mut f = rust_facts(&["README.md"], Some(rust_map_fixture()));
    f.attempts[0].log = Some("test opens_twice ... FAILED\n".into());
    let (_, r) = one(&f, &prior("other", "rs:?::opens_twice"));
    assert!(r.starts_with("impact_unknown: rs:?::opens_twice"), "{r}");
}

// ---------- parsing ----------

#[test]
fn parse_log_reads_both_kinds() {
    let log = "\
2026-11-09T02:53:17.1Z ##[group]aib/tests/test-a.py (1.0 s)
2026-11-09T02:53:17.1Z ##[error]aib/tests/test-a.py failed or hung
::error::tests/test-b.py still running after 600 s (killed with its children)
##[error]some other error with spaces failed or hung
     Running unittests src/lib.rs (target\\debug\\deps\\aib_store-0123abcd.exe)
test db::opens ... FAILED
test db::ok ... ok
     Running tests/x.rs (target/debug/deps/x-zz)
test y ... FAILED
##[error]ci-shard empty tests(這個根一支測試都沒有)
";
    let p = parse_log(log);
    // "x-zz" has a non-hex hash, so the binary is not taken and the old one stays
    assert_eq!(
        p.fingerprints,
        vec![
            "py:aib/tests/test-a.py",
            "py:tests/test-b.py",
            "rs:aib_store::db::opens",
            "rs:aib_store::y"
        ]
    );
    assert_eq!(p.shard_problems, vec!["empty tests(這個根一支測試都沒有)"]);
    assert!(p.coarse);
    assert!(!p.runner_lost);
}

const PY_FINE: &str = "\
2026-11-09T02:53:17.1Z ##[group]aib/tests/test-x.py (3.2 s)
2026-11-09T02:53:17.1Z FAIL: test_cursor (__main__.P0.test_cursor)
2026-11-09T02:53:17.1Z ERROR: test_open (__main__.P1)
2026-11-09T02:53:17.1Z ##[endgroup]
2026-11-09T02:53:17.1Z ##[group]aib/tests/test-y.py (1.0 s)
2026-11-09T02:53:17.1Z FAIL: test_other (__main__.Q.test_other)
2026-11-09T02:53:17.1Z ##[endgroup]
2026-11-09T02:53:17.2Z ##[error]aib/tests/test-x.py failed or hung
";

#[test]
fn l1_python_fingerprint_names_the_test() {
    let p = parse_log(PY_FINE);
    // test-y's FAIL lines are not taken: test-y has no error line (it passed in the end)
    assert_eq!(
        p.fingerprints,
        vec![
            "py:aib/tests/test-x.py::P0.test_cursor",
            "py:aib/tests/test-x.py::P1.test_open"
        ]
    );
    assert!(!p.coarse);
    // a file-level precedent does not match a test-level fingerprint, and vice versa
    let mut a = red_then_green();
    a[0].log = Some(PY_FINE.into());
    let f = facts(changed(&[("README.md", "modified")]), a);
    assert_eq!(
        one(&f, &prior("other", "py:aib/tests/test-x.py")).1,
        "no_precedent: py:aib/tests/test-x.py::P0.test_cursor"
    );
    let mut p2 = prior("other", "py:aib/tests/test-x.py::P0.test_cursor");
    p2[0]
        .fingerprints
        .push("py:aib/tests/test-x.py::P1.test_open".into());
    let e = judge(&f, &p2);
    assert_eq!(e[0].verdict, "suspected_flaky", "{}", e[0].reason);
    assert!(!e[0].coarse);
}

#[test]
fn l2_lost_runner_is_infra() {
    for msg in [
        "The runner has received a shutdown signal. This can happen when",
        "The self-hosted runner: x lost communication with the server.",
    ] {
        let log = format!("2026-11-09T02:53:17.1Z ##[error]{msg}\n");
        let a = vec![att(
            1,
            1,
            10,
            "failure",
            Some(&log),
            vec![step("Python suite", "failure")],
        )];
        assert_eq!(
            one(&facts(vec![], a), &[]),
            ("infra_unverified".to_string(), "runner_lost".to_string())
        );
    }
}

#[test]
fn l3_cancelled_or_notice_only_is_not_a_green_rerun() {
    let f = |sib: JobAttempt| {
        let mut a = red_then_green();
        a[1] = sib;
        facts(changed(&[("README.md", "modified")]), a)
    };
    let p = prior("other", "py:aib/tests/test-x.py");
    // cancelled
    let c = att(
        1,
        2,
        11,
        "cancelled",
        None,
        vec![step("Python suite", "cancelled")],
    );
    assert_eq!(one(&f(c), &p).1, "no_green_rerun");
    // success, but the red step never ran (a cell that only printed a notice)
    let n = att(
        1,
        2,
        11,
        "success",
        None,
        vec![
            step("cell not run on this PR", "success"),
            step("Python suite", "skipped"),
        ],
    );
    assert_eq!(one(&f(n), &p).1, "no_green_rerun");
    // success with the red step green = a rerun
    let g = att(
        1,
        2,
        11,
        "success",
        None,
        vec![step("Python suite", "success")],
    );
    assert_eq!(one(&f(g), &p).0, "suspected_flaky");
}

#[test]
fn k4b_fixture_loaded_from_another_test_file_overlaps() {
    let other = "aib/tests/test-dm-hold-notify-abb89-1015.py";
    for src in [
        "spec_from_file_location('h', HERE / 'test-dm-hold-notify-abb89-1015.py')",
        "import test_dm_hold_notify_abb89_1015",
    ] {
        let mut f = facts(changed(&[(other, "modified")]), red_then_green());
        f.py_sources
            .insert("aib/tests/test-x.py".into(), Some(src.into()));
        assert_eq!(
            one(&f, &prior("other", "py:aib/tests/test-x.py")).1,
            "impact_overlap: py:aib/tests/test-x.py",
            "{src}"
        );
    }
    // source not readable while another test file changed = unknown
    let mut f = facts(changed(&[(other, "modified")]), red_then_green());
    f.py_sources.insert("aib/tests/test-x.py".into(), None);
    assert!(one(&f, &prior("other", "py:aib/tests/test-x.py"))
        .1
        .starts_with("impact_unknown: "));
    // not mentioned = unrelated
    let f = facts(changed(&[(other, "modified")]), red_then_green());
    assert_eq!(
        one(&f, &prior("other", "py:aib/tests/test-x.py")).0,
        "suspected_flaky"
    );
}

#[test]
fn k11b_only_screened_reds_are_precedent() {
    let f = facts(changed(&[("README.md", "modified")]), red_then_green());
    let mut p = prior("other", "py:aib/tests/test-x.py");
    p[0].precedent_ok = false;
    assert_eq!(one(&f, &p).1, "no_precedent: py:aib/tests/test-x.py");
    // what judge records: overlap = not a precedent; rules 1–5 held = precedent
    let over = facts(changed(&[("aib/src/x.py", "modified")]), red_then_green());
    assert!(!judge(&over, &[])[0].precedent_ok);
    let fine = facts(
        changed(&[("README.md", "modified")]),
        vec![red_then_green().remove(0)],
    );
    let e = judge(&fine, &[]);
    assert_eq!(e[0].reason, "no_green_rerun");
    assert!(e[0].precedent_ok);
    // main push: only with a green rerun on the same main head
    let mut m = facts(vec![], vec![red_then_green().remove(0)]);
    m.pr = None;
    assert!(!judge(&m, &[])[0].precedent_ok);
    m.attempts = red_then_green();
    assert!(judge(&m, &[])[0].precedent_ok);
}

#[test]
fn manifest_parsing() {
    let m = parse_manifest(
        "[package]\nname = \"aib-sys\"\n[lib]\nname = \"sysx\"\n[[bin]]\nname = \"tool-a\"\n\
         [dependencies]\nthiserror = \"2\"\na = { path = \"../a\" }\n\
         [target.'cfg(target_os = \"macos\")'.dependencies]\nlibc = \"0.2\"\n\
         [target.'cfg(windows)'.dependencies]\nwindows-sys = { version = \"=0.61.2\", features = [\n  \"Win32_Foundation\",\n  \"Win32_IO\",\n] }\n\
         [dev-dependencies.b]\npath = \"../b\"\n[lints]\nworkspace = true\n",
    );
    assert!(m.understood);
    assert_eq!(m.package.as_deref(), Some("aib-sys"));
    assert_eq!(m.lib.as_deref(), Some("sysx"));
    assert_eq!(m.bins, vec!["tool-a"]);
    assert_eq!(m.path_deps, vec!["../a", "../b"]);
    for bad in [
        "[package]\nname = \"p\"\n[dependencies]\na.workspace = true\n",
        "[package]\nname = \"p\"\n[dependencies]\na = { workspace = true }\n",
        "[package]\nname = \"p\"\n[dependencies.a]\nworkspace = true\n",
        "[package]\nname = \"p\"\n[dependencies]\na = { path = 3 }\n",
        "[package]\nname = \"p\"\n[dependencies]\na = { version = \"1\", features = [\n",
    ] {
        assert!(!parse_manifest(bad).understood, "{bad}");
    }
}

#[test]
fn rust_map_bins_and_deps() {
    let m = rust_map_fixture();
    assert_eq!(
        m.bins["store_x"],
        BTreeSet::from(["crates/store".to_string()])
    );
    assert_eq!(
        m.bins["store"],
        BTreeSet::from(["crates/store".to_string()])
    );
    assert_eq!(
        m.deps["crates/store"],
        Some(BTreeSet::from(["crates/core".to_string()]))
    );
    assert!(!m.bins.contains_key("Cargo"));
    // an unreadable manifest = the whole map fails
    let files = vec!["crates/a/Cargo.toml".to_string()];
    assert!(rust_map(&files, &|_| None).is_err());
    assert_eq!(join_rel("crates/a", "../../.."), None);
}

// ---------- register ----------

#[test]
fn k10_register_keeps_one_line_per_attempt() {
    let f = facts(changed(&[("README.md", "modified")]), red_then_green());
    let e = judge(&f, &[]);
    let fresh = new_entries(&[], e.clone());
    assert_eq!(fresh.len(), 1);
    assert!(new_entries(&fresh, e.clone()).is_empty());
    // round trip through JSON
    let text: String = fresh
        .iter()
        .map(|x| format!("{}\n", entry_json(x, "t")))
        .collect();
    assert_eq!(read_register(&text).expect("read"), fresh);
    assert!(read_register("{\"repo\": 1}\n").is_err());
    assert!(read_register("not json\n").is_err());
}

#[test]
fn suggest_needs_repeat_and_two_heads() {
    let mk = |head: &str, job_id: u64, job: &str, verdict: &str| {
        let mut e = prior(head, "py:t").remove(0);
        e.job_id = job_id;
        e.job = job.into();
        e.verdict = verdict.into();
        e
    };
    // 3 reds, 1 head: no
    let one_head = vec![
        mk("a", 1, "u", "unclassified"),
        mk("a", 2, "u", "unclassified"),
        mk("a", 3, "u", "unclassified"),
    ];
    assert!(suggest(&one_head).is_empty());
    // 3 reds over 2 heads: yes
    let s = suggest(&[
        mk("a", 1, "u", "unclassified"),
        mk("a", 2, "u", "unclassified"),
        mk("b", 3, "u", "unclassified"),
    ]);
    assert_eq!(s.len(), 1);
    assert_eq!(s[0].reds, 3);
    // 2 platforms over 2 heads: yes
    assert_eq!(
        suggest(&[
            mk("a", 1, "u", "unclassified"),
            mk("b", 2, "m", "unclassified")
        ])
        .len(),
        1
    );
    // 2 reds, 1 platform: no
    assert!(suggest(&[
        mk("a", 1, "u", "unclassified"),
        mk("b", 2, "u", "unclassified")
    ])
    .is_empty());
    // infra entries never count
    assert!(suggest(&[
        mk("a", 1, "u", "infra_unverified"),
        mk("b", 2, "m", "infra_unverified")
    ])
    .is_empty());
    // coarse (file-level) reds are not counted towards the 3
    let mut c = vec![
        mk("a", 1, "u", "unclassified"),
        mk("a", 2, "u", "unclassified"),
        mk("b", 3, "u", "unclassified"),
    ];
    c[2].coarse = true;
    assert!(suggest(&c).is_empty());
}

// ---------- reading GitHub (offline map) ----------

struct Map(BTreeMap<String, String>);

impl Source for Map {
    fn get(&self, path: &str) -> Result<String, String> {
        self.0
            .get(path)
            .cloned()
            .ok_or_else(|| format!("not recorded: {path}"))
    }
}

fn gh_map(changed_files: u64) -> Map {
    let mut m = BTreeMap::new();
    let mut put = |k: &str, v: serde_json::Value| {
        m.insert(k.to_string(), v.to_string());
    };
    put(
        "repos/o/r/pulls/7",
        json!({"head": {"sha": HEAD}, "base": {"sha": "b"}, "changed_files": changed_files}),
    );
    put(
        "repos/o/r/pulls/7/files?per_page=100&page=1",
        json!([{"filename": "README.md", "status": "modified"}]),
    );
    put(
        "repos/o/r/actions/runs?head_sha=h1&per_page=100&page=1",
        json!({"total_count": 2, "workflow_runs": [
            {"id": 1, "head_sha": HEAD, "path": ".github/workflows/ci.yml", "run_attempt": 2},
            {"id": 5, "head_sha": "zzz", "path": ".github/workflows/ci.yml", "run_attempt": 1}
        ]}),
    );
    put(
        "repos/o/r/actions/runs/1/attempts/1/jobs?per_page=100&page=1",
        json!({"total_count": 1, "jobs": [
            {"id": 10, "name": "test (ubuntu-latest, 3.12)", "conclusion": "failure",
             "steps": [{"name": "Python suite", "conclusion": "failure", "number": 5}]}
        ]}),
    );
    put(
        "repos/o/r/actions/runs/1/attempts/2/jobs?per_page=100&page=1",
        json!({"total_count": 1, "jobs": [
            {"id": 11, "name": "test (ubuntu-latest, 3.12)", "conclusion": "success",
             "steps": [{"name": "Python suite", "conclusion": "success", "number": 5}]}
        ]}),
    );
    m.insert("repos/o/r/actions/jobs/10/logs".into(), PY_RED.into());
    Map(m)
}

#[test]
fn gather_reads_every_attempt_and_red_logs_only() {
    let f = gather(&gh_map(1), R, Some(7), "").expect("gather");
    assert_eq!(f.head, HEAD);
    assert_eq!(f.attempts.len(), 2);
    assert_eq!(f.attempts[0].log.as_deref(), Some(PY_RED));
    assert_eq!(f.attempts[1].log, None);
    assert!(f.rust.is_none());
    // the failing test's source was asked for (not recorded here = None)
    assert_eq!(f.py_sources.get("aib/tests/test-x.py"), Some(&None));
    assert_eq!(
        one(&f, &prior("other", "py:aib/tests/test-x.py")).0,
        "suspected_flaky"
    );
}

#[test]
fn k6_file_count_must_match_changed_files() {
    let e = gather(&gh_map(2), R, Some(7), "").expect_err("short file list");
    assert!(
        e.starts_with("missing_page: PR #7 files gave 1 of 2"),
        "{e}"
    );
    assert!(gather(&gh_map(3001), R, Some(7), "").is_err());
}

#[test]
fn gather_missing_run_page_is_an_error() {
    let mut m = gh_map(1);
    m.0.remove("repos/o/r/actions/runs/1/attempts/2/jobs?per_page=100&page=1");
    assert!(gather(&m, R, Some(7), "").is_err());
}

#[test]
fn gather_unreadable_log_is_none() {
    let mut m = gh_map(1);
    m.0.remove("repos/o/r/actions/jobs/10/logs");
    let f = gather(&m, R, Some(7), "").expect("gather");
    assert_eq!(f.attempts[0].log, None);
}

#[test]
fn gather_rust_tree_truncated_or_missing_is_none() {
    let mut m = gh_map(1);
    m.0.insert("repos/o/r/actions/jobs/10/logs".into(), RS_RED.into());
    let tree = |trunc: Option<bool>| {
        let mut t = json!({"tree": [
            {"path": "crates/store/Cargo.toml", "type": "blob"},
            {"path": "crates/store/tests/store.rs", "type": "blob"}
        ]});
        if let Some(b) = trunc {
            t["truncated"] = json!(b);
        }
        t.to_string()
    };
    let manifest =
        json!({"encoding": "base64", "content": "W3BhY2thZ2VdCm5h\nbWUgPSAic3RvcmUiCg=="})
            .to_string();
    m.0.insert(
        "repos/o/r/contents/crates/store/Cargo.toml?ref=h1".into(),
        manifest,
    );
    m.0.insert(
        "repos/o/r/git/trees/h1?recursive=1".into(),
        tree(Some(false)),
    );
    let f = gather(&m, R, Some(7), "").expect("gather");
    let rust = f.rust.expect("rust map");
    assert!(rust.bins.contains_key("store"));
    for t in [Some(true), None] {
        m.0.insert("repos/o/r/git/trees/h1?recursive=1".into(), tree(t));
        assert!(
            gather(&m, R, Some(7), "").expect("gather").rust.is_none(),
            "{t:?}"
        );
    }
}

#[test]
fn base64_decoder() {
    assert_eq!(
        b64("W3BhY2thZ2VdCm5h\nbWUgPSAic3RvcmUiCg==").expect("b64"),
        b"[package]\nname = \"store\"\n"
    );
    assert_eq!(b64("aGk="), Some(b"hi".to_vec()));
    assert_eq!(b64("a*b"), None);
}
