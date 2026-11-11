// SPDX-License-Identifier: Apache-2.0
//! S1 shadow: the positive case shaped like #567 (11-09) plus one knife per way a "reuse" could be fake.
#![allow(clippy::unwrap_used)]

use aib_cireuse::gh::{facts, paged, required_checks, Source};
use aib_cireuse::*;
use std::collections::{BTreeMap, HashMap};

const REPO: &str = "ez2app/ez2-aib";
const MAIN: &str = "0c01f314aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const P1: &str = "20be5f5cf948902e5d94757fafa7ac19d44f030c";
const P2: &str = "64b2908f4e1ea30df5afd7c1ee001d7667665999";
const TESTED: &str = "9be112bb99f45ecb9e6d513f915bd21c69cb8165";
const TREE: &str = "3928365a6ae75e47c1f9aad0730b73bcbfe71fd0";
const T0: i64 = 1_778_300_000;

fn steps(real: bool) -> Vec<(String, Option<String>)> {
    let ok = Some("success".to_string());
    let sk = Some("skipped".to_string());
    vec![
        ("Set up job".into(), ok.clone()),
        (
            "cell not run on this PR".into(),
            if real { sk.clone() } else { ok.clone() },
        ),
        ("smoke test".into(), if real { ok.clone() } else { sk }),
        ("Complete job".into(), ok),
    ]
}

fn job(id: u64, name: &str, start: i64, secs: i64, image: &str, tested: Option<&str>) -> Job {
    Job {
        id,
        name: name.into(),
        status: "completed".into(),
        conclusion: Some("success".into()),
        started_at: Some(start),
        completed_at: Some(start + secs),
        steps: steps(true),
        steps_known: true,
        image: Some(image.into()),
        tested_sha: tested.map(str::to_string),
    }
}

fn run(id: u64, path: &str, event: &str, created: i64, jobs: Vec<Job>) -> Run {
    let end = jobs.iter().filter_map(|j| j.completed_at).max();
    Run {
        id,
        path: path.into(),
        event: event.into(),
        status: "completed".into(),
        conclusion: Some("success".into()),
        run_attempt: 1,
        created_at: created,
        run_started_at: Some(created),
        updated_at: end,
        head_repo: REPO.into(),
        jobs,
    }
}

const CI: &str = ".github/workflows/ci.yml";
const UB: &str = "ubuntu-26.04 20261101.186.1";

fn base() -> Facts {
    let pr_t = T0 - 3600 * 2;
    Facts {
        repo: REPO.into(),
        main_sha: MAIN.into(),
        main_tree: TREE.into(),
        parents: vec![P1.into(), P2.into()],
        main_runs: vec![run(
            1,
            CI,
            "push",
            T0,
            vec![job(
                11,
                "smoke (ubuntu-latest, 3.12)",
                T0 + 10,
                1800,
                UB,
                None,
            )],
        )],
        cand_runs: vec![run(
            2,
            CI,
            "pull_request",
            pr_t,
            vec![job(
                21,
                "smoke (ubuntu-latest, 3.12)",
                pr_t + 10,
                1790,
                UB,
                Some(TESTED),
            )],
        )],
        tested_trees: BTreeMap::from([(TESTED.to_string(), TREE.to_string())]),
        required: vec!["smoke (ubuntu-latest, 3.12)".into()],
    }
}

fn reasons(v: &Verdict) -> String {
    v.cells
        .iter()
        .flat_map(|c| c.reasons.clone())
        .collect::<Vec<_>>()
        .join(" | ")
}

#[test]
fn positive_same_tree_same_cell_could_reuse() {
    let v = judge(&base());
    assert_eq!(v.state, "reusable", "{}", reasons(&v));
    assert!(v.cells.iter().all(|c| c.reusable));
    // observed only (10 s queue + 1800 s run, main's own wall clock), never reported as a saving
    assert_eq!(v.observed_main_wall_minutes, Some(30.2));
    assert_eq!(v.shadow_false_green, Some(vec![]));
    assert_eq!(v.compared_cells, 1);
    let j = verdict_json(&v);
    assert!(j["saved_minutes"].is_null(), "{j}");
    assert_eq!(j["writes_status"], false);
    assert_eq!(j["skips_cells"], false);
}

#[test]
fn knife_same_tree_different_workflow_file() {
    let mut f = base();
    f.cand_runs[0].path = ".github/workflows/aibd.yml".into();
    let v = judge(&f);
    assert_eq!(v.state, "not_reusable");
    assert!(
        reasons(&v).starts_with("no_candidate_run"),
        "{}",
        reasons(&v)
    );
    assert!(
        reasons(&v).contains("main_workflow_missing"),
        "{}",
        reasons(&v)
    );
}

#[test]
fn knife_same_tree_different_runner_image() {
    let mut f = base();
    f.cand_runs[0].jobs[0].image = Some("ubuntu-24.04 20261028.624".into());
    let v = judge(&f);
    assert_eq!(v.state, "not_reusable");
    assert!(reasons(&v).contains("image_differs"), "{}", reasons(&v));
}

#[test]
fn knife_same_tree_different_event_path_printed_not_run() {
    // #567's macOS 3.9 / 3.14 cells on the PR: only "cell not run on this PR", about 3 s.
    let mut f = base();
    f.cand_runs[0].jobs[0].steps = steps(false);
    let v = judge(&f);
    assert_eq!(v.state, "not_reusable");
    assert!(
        reasons(&v).contains("steps_differ: step 2"),
        "{}",
        reasons(&v)
    );
}

#[test]
fn knife_empty_run_too_short() {
    let mut f = base();
    let j = &mut f.cand_runs[0].jobs[0];
    j.completed_at = Some(j.started_at.unwrap() + 3);
    let v = judge(&f);
    assert!(
        reasons(&v).contains("too_short: PR job ran 3 s"),
        "{}",
        reasons(&v)
    );
    assert_eq!(v.state, "not_reusable");
}

#[test]
fn knife_wrong_attempt_latest_attempt_failed() {
    let mut f = base();
    f.cand_runs[0].run_attempt = 2;
    f.cand_runs[0].jobs[0].conclusion = Some("failure".into());
    let v = judge(&f);
    assert!(
        reasons(&v).contains("candidate_not_success") && reasons(&v).contains("attempt 2"),
        "{}",
        reasons(&v)
    );
}

#[test]
fn knife_newer_run_failed_older_green_is_not_used() {
    let mut f = base();
    let mut newer = f.cand_runs[0].clone();
    newer.id = 3;
    newer.created_at += 60;
    newer.jobs[0].conclusion = Some("failure".into());
    f.cand_runs.push(newer);
    let v = judge(&f);
    assert!(
        reasons(&v).contains("candidate_not_success"),
        "{}",
        reasons(&v)
    );
}

#[test]
fn knife_tree_differs_base_moved() {
    let mut f = base();
    f.tested_trees.insert(
        TESTED.into(),
        "1111111111111111111111111111111111111111".into(),
    );
    let v = judge(&f);
    assert!(reasons(&v).contains("tree_differs"), "{}", reasons(&v));
}

#[test]
fn knife_unreadable_tested_commit_or_log_is_not_reuse() {
    let mut f = base();
    f.tested_trees.clear();
    assert!(reasons(&judge(&f)).contains("tested_tree_unknown"));
    let mut f = base();
    f.cand_runs[0].jobs[0].tested_sha = None;
    assert!(reasons(&judge(&f)).contains("tested_sha_unknown"));
    let mut f = base();
    f.main_runs[0].jobs[0].image = None;
    assert!(reasons(&judge(&f)).contains("image_unknown"));
}

#[test]
fn knife_fork_and_old_result() {
    let mut f = base();
    f.cand_runs[0].head_repo = "someone/ez2-aib".into();
    assert!(reasons(&judge(&f)).contains("fork"));
    let mut f = base();
    let j = &mut f.cand_runs[0].jobs[0];
    j.started_at = Some(T0 - 30 * 3600);
    j.completed_at = Some(T0 - 29 * 3600);
    f.main_runs[0].jobs[0].completed_at = Some(T0 + 10 + 3600);
    assert!(reasons(&judge(&f)).contains("older_than_24h"));
}

#[test]
fn main_unfinished_waits_and_non_merge_is_named() {
    let mut f = base();
    f.main_runs[0].jobs[0].status = "in_progress".into();
    assert_eq!(judge(&f).state, "wait");
    let mut f = base();
    f.parents.pop();
    assert_eq!(judge(&f).state, "not_merge");
    let mut f = base();
    f.main_runs.clear();
    assert_eq!(judge(&f).state, "unknown");
}

#[test]
fn partial_reuse_shows_no_minutes() {
    let mut f = base();
    f.main_runs[0].jobs.push(job(
        12,
        "smoke (macos-latest, 3.9)",
        T0 + 10,
        3000,
        "macos-15-arm64 20261028.1",
        None,
    ));
    let v = judge(&f);
    assert_eq!(v.state, "partial");
    assert!(verdict_json(&v)["saved_minutes"].is_null());
}

#[test]
fn shadow_false_green_is_flagged_when_main_itself_failed() {
    let mut f = base();
    f.main_runs[0].jobs[0].conclusion = Some("failure".into());
    let v = judge(&f);
    assert_eq!(
        v.shadow_false_green,
        Some(vec![format!("{CI} / smoke (ubuntu-latest, 3.12)")])
    );
    assert_eq!(v.compared_cells, 1);
}

#[test]
fn parsers_read_real_log_shapes() {
    let log = "2026-11-09T02:53:17.6266278Z ##[group]Runner Image Provisioner\n\
               2026-11-09T02:53:17.6267256Z Version: 20261028.624\n\
               2026-11-09T02:53:17.6270000Z ##[endgroup]\n\
               2026-11-09T02:53:17.6272325Z ##[group]Runner Image\n\
               2026-11-09T02:53:17.6272667Z Image: ubuntu-26.04\n\
               2026-11-09T02:53:17.6273045Z Version: 20261101.186.1\n\
               2026-11-09T02:53:17.6274000Z ##[endgroup]\n\
               2026-11-09T02:53:18.5947793Z [command]/usr/bin/git -c protocol.version=2 fetch --no-tags origin +9be112bb99f45ecb9e6d513f915bd21c69cb8165:refs/remotes/pull/567/merge\n";
    assert_eq!(parse_image(log).as_deref(), Some(UB));
    assert_eq!(parse_tested_sha(log).as_deref(), Some(TESTED));
    let two = format!("{log}x +{}:refs/remotes/pull/567/merge\n", "a".repeat(40));
    assert_eq!(
        parse_tested_sha(&two),
        None,
        "two different fetched commits = unknown"
    );
    assert_eq!(parse_image("no group here"), None);
    assert_eq!(parse_time("2026-11-09T04:20:16Z"), Some(1_794_198_016));
    assert_eq!(
        parse_time("2026-11-09T04:20:16.123+00:00"),
        Some(1_794_198_016)
    );
    assert_eq!(parse_time("2026-11-09 04:20:16"), None);
    assert_eq!(day_key(1_794_198_016, 8), "2026-11-09");
    assert_eq!(day_key(1_794_198_016 - 5 * 3600, 0), "2026-11-08");
}

#[test]
fn day_line_last_line_per_commit_wins() {
    let mut a = verdict_json(&judge(&base()));
    let lines = vec![
        {
            a["state"] = "wait".into();
            a.to_string()
        },
        verdict_json(&judge(&base())).to_string(),
        "garbage".to_string(),
    ];
    let line = day_line(&lines);
    assert!(line.starts_with("今天 1 次全可沿用(判了 1 次"), "{line}");
    assert!(line.contains("影子假綠 0 / 可比較 1 格"), "{line}");
    assert!(
        line.contains("main 執行牆鐘合計 30 分(觀察值,不是省下的時間)"),
        "{line}"
    );
    assert!(!line.contains("省 ") && !line.contains("估少等"), "{line}");
    assert!(line.contains("帳本壞行 1"), "{line}");
}

struct Fake(HashMap<String, String>);

impl Source for Fake {
    fn get(&self, path: &str) -> Result<String, String> {
        self.0
            .get(path)
            .cloned()
            .ok_or_else(|| format!("404 {path}"))
    }
}

#[test]
fn knife_missing_page_is_an_error_not_fewer_runs() {
    let mut m = HashMap::new();
    m.insert(
        "x?per_page=100&page=1".to_string(),
        r#"{"total_count":150,"jobs":[{"id":1}]}"#.to_string(),
    );
    m.insert(
        "x?per_page=100&page=2".to_string(),
        r#"{"total_count":150,"jobs":[]}"#.to_string(),
    );
    let e = paged(&Fake(m), "x", "jobs").unwrap_err();
    assert!(e.starts_with("missing_page"), "{e}");
    let mut m = HashMap::new();
    m.insert(
        "x?per_page=100&page=1".to_string(),
        r#"{"total_count":2,"jobs":[{"id":1}]}"#.to_string(),
    );
    m.insert(
        "x?per_page=100&page=2".to_string(),
        r#"{"total_count":2,"jobs":[{"id":2}]}"#.to_string(),
    );
    assert_eq!(paged(&Fake(m), "x", "jobs").unwrap().len(), 2);
}

#[test]
fn facts_end_to_end_from_api_shapes() {
    let jobj = |id: u64, start: &str, end: &str| {
        format!(
            r#"{{"id":{id},"name":"smoke (ubuntu-latest, 3.12)","status":"completed","conclusion":"success","started_at":"{start}","completed_at":"{end}","steps":[{{"number":1,"name":"smoke test","conclusion":"success"}}]}}"#
        )
    };
    let runj = |id: u64, sha: &str, event: &str, at: &str, end: &str| {
        format!(
            r#"{{"id":{id},"head_sha":"{sha}","path":"{CI}","event":"{event}","status":"completed","conclusion":"success","run_attempt":1,"created_at":"{at}","run_started_at":"{at}","updated_at":"{end}","head_repository":{{"full_name":"{REPO}"}}}}"#
        )
    };
    let log = |tested: &str| {
        format!("2026-11-09T02:53:17Z ##[group]Runner Image\n2026-11-09T02:53:17Z Image: ubuntu-26.04\n2026-11-09T02:53:17Z Version: 20261101.186.1\n2026-11-09T02:53:17Z ##[endgroup]\n{tested}")
    };
    let mut m = HashMap::new();
    let mut put = |k: String, v: String| {
        m.insert(k, v);
    };
    put(
        format!("repos/{REPO}/commits/{MAIN}"),
        format!(
            r#"{{"sha":"{MAIN}","commit":{{"tree":{{"sha":"{TREE}"}}}},"parents":[{{"sha":"{P1}"}},{{"sha":"{P2}"}}]}}"#
        ),
    );
    put(
        format!("repos/{REPO}/actions/runs?head_sha={MAIN}&event=push&per_page=100&page=1"),
        format!(
            r#"{{"total_count":1,"workflow_runs":[{}]}}"#,
            runj(
                1,
                MAIN,
                "push",
                "2026-11-09T04:20:16Z",
                "2026-11-09T04:51:00Z"
            )
        ),
    );
    put(
        format!("repos/{REPO}/actions/runs/1/attempts/1/jobs?per_page=100&page=1"),
        format!(
            r#"{{"total_count":1,"jobs":[{}]}}"#,
            jobj(11, "2026-11-09T04:20:27Z", "2026-11-09T04:50:27Z")
        ),
    );
    put(
        format!("repos/{REPO}/actions/runs?head_sha={P2}&event=pull_request&per_page=100&page=1"),
        format!(
            r#"{{"total_count":1,"workflow_runs":[{}]}}"#,
            runj(
                2,
                P2,
                "pull_request",
                "2026-11-09T02:53:03Z",
                "2026-11-09T03:24:00Z"
            )
        ),
    );
    put(
        format!("repos/{REPO}/actions/runs/2/attempts/1/jobs?per_page=100&page=1"),
        format!(
            r#"{{"total_count":1,"jobs":[{}]}}"#,
            jobj(21, "2026-11-09T02:53:16Z", "2026-11-09T03:23:20Z")
        ),
    );
    put(format!("repos/{REPO}/actions/jobs/11/logs"), log(""));
    put(
        format!("repos/{REPO}/actions/jobs/21/logs"),
        log(&format!(
            "x fetch origin +{TESTED}:refs/remotes/pull/567/merge\n"
        )),
    );
    put(
        format!("repos/{REPO}/git/commits/{TESTED}"),
        format!(r#"{{"tree":{{"sha":"{TREE}"}}}}"#),
    );
    put(
        format!("repos/{REPO}/rulesets?per_page=100"),
        r#"[{"id":7,"enforcement":"active","target":"branch"},{"id":8,"enforcement":"active","target":"tag"},{"id":9,"enforcement":"disabled","target":"branch"}]"#.into(),
    );
    put(
        format!("repos/{REPO}/rulesets/7"),
        r#"{"id":7,"conditions":{"ref_name":{"include":["~DEFAULT_BRANCH"],"exclude":[]}},"rules":[{"type":"pull_request"},{"type":"required_status_checks","parameters":{"required_status_checks":[{"context":"smoke (ubuntu-latest, 3.12)"}]}}]}"#.into(),
    );
    let f = facts(&Fake(m.clone()), REPO, MAIN).unwrap();
    assert_eq!(f.required, vec!["smoke (ubuntu-latest, 3.12)".to_string()]);
    // the ruleset list cannot be read => no facts at all (fail closed, verdict unknown upstream)
    let mut broken = m.clone();
    broken.remove(&format!("repos/{REPO}/rulesets/7"));
    assert!(facts(&Fake(broken), REPO, MAIN).is_err());
    let v = judge(&f);
    assert_eq!(v.state, "reusable", "{}", reasons(&v));
    assert_eq!(v.observed_main_wall_minutes, Some(30.7));
    // the tested commit cannot be read => not reuse (never "unreadable means fine")
    m.remove(&format!("repos/{REPO}/git/commits/{TESTED}"));
    let v = judge(&facts(&Fake(m.clone()), REPO, MAIN).unwrap());
    assert!(
        reasons(&v).contains("tested_tree_unknown"),
        "{}",
        reasons(&v)
    );
    // a list read failing is an error (recorded as unknown by the CLI)
    m.remove(&format!(
        "repos/{REPO}/actions/runs/2/attempts/1/jobs?per_page=100&page=1"
    ));
    assert!(facts(&Fake(m), REPO, MAIN).is_err());
}

#[test]
fn offline_file_source_reads_only_what_was_recorded() {
    use aib_cireuse::gh::FileSource;
    let d = std::env::temp_dir().join(format!("aib-cireuse-off-{}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    std::fs::write(d.join("a"), "{\"ok\":1}").unwrap();
    std::fs::write(d.join("index.json"), r#"{"repos/x/commits/y":"a"}"#).unwrap();
    let f = FileSource::open(&d).unwrap();
    assert_eq!(f.get("repos/x/commits/y").unwrap(), "{\"ok\":1}");
    let e = f.get("repos/x/commits/z").unwrap_err();
    assert!(e.contains("not recorded"), "{e}");
    // aib-flaky depends on this prefix (ebb36 #666 N1)
    assert!(e.starts_with(aib_cireuse::gh::OFFLINE_PREFIX), "{e}");
    std::fs::write(d.join("index.json"), r#"{"p":"../etc/passwd"}"#).unwrap();
    assert!(
        FileSource::open(&d).is_err(),
        "file names may not leave the directory"
    );
    std::fs::remove_dir_all(&d).unwrap();
}

// ---------- bb7dc #575 review (11-09): four holes, one counterexample each ----------

fn names(v: &Verdict) -> Vec<String> {
    v.cells
        .iter()
        .map(|c| format!("{}|{}|{}", c.job, c.reusable, c.reasons.join(";")))
        .collect()
}

#[test]
fn r1_pr_workflow_missing_on_main_is_not_complete() {
    let mut f = base();
    let mut other = f.cand_runs[0].clone();
    other.id = 3;
    other.path = ".github/workflows/aibd.yml".into();
    // its own job name: reusing the required smoke name would make that check two jobs on the PR
    // side, which is unknown by itself (r1b) and would hide what this test is about
    other.jobs[0].id = 31;
    other.jobs[0].name = "rust (macos-latest)".into();
    f.cand_runs.push(other);
    let v = judge(&f);
    assert_eq!(v.state, "partial", "{:?}", names(&v));
    assert!(
        reasons(&v).contains("main_workflow_missing"),
        "{:?}",
        names(&v)
    );
}

#[test]
fn r1_pr_matrix_cell_missing_on_main_is_not_complete() {
    let mut f = base();
    let extra = f.cand_runs[0].jobs[0].clone();
    f.cand_runs[0].jobs.push(Job {
        id: 22,
        name: "smoke (ubuntu-latest, 3.14)".into(),
        ..extra
    });
    let v = judge(&f);
    assert_eq!(v.state, "partial", "{:?}", names(&v));
    assert!(reasons(&v).contains("main_job_missing"), "{:?}", names(&v));
}

#[test]
fn r1_same_count_one_swapped_is_not_complete() {
    let mut f = base();
    let mj = f.main_runs[0].jobs[0].clone();
    f.main_runs[0].jobs.push(Job {
        id: 12,
        name: "docs".into(),
        ..mj
    });
    let cj = f.cand_runs[0].jobs[0].clone();
    f.cand_runs[0].jobs.push(Job {
        id: 22,
        name: "tip".into(),
        ..cj
    });
    let v = judge(&f);
    assert_eq!(v.state, "partial", "{:?}", names(&v));
    let r = reasons(&v);
    assert!(
        r.contains("no_candidate_job") && r.contains("main_job_missing"),
        "{r}"
    );
}

#[test]
fn r1_empty_main_jobs_and_duplicate_main_job_are_not_reuse() {
    let mut f = base();
    f.main_runs[0].jobs.clear();
    assert_eq!(judge(&f).state, "unknown");
    let mut f = base();
    let dup = f.main_runs[0].jobs[0].clone();
    f.main_runs[0].jobs.push(Job { id: 12, ..dup });
    let v = judge(&f);
    // the required check is now two jobs on main's side => unknown, and each cell says why
    assert_eq!(v.state, "unknown", "{:?}", names(&v));
    assert!(v.note.contains("main 2"), "{}", v.note);
    assert!(reasons(&v).contains("ambiguous_main_job"));
    assert!(v.cells.iter().all(|c| !c.reusable));
}

#[test]
fn r2_all_reusable_still_claims_no_saved_minutes() {
    let v = judge(&base());
    assert_eq!(v.state, "reusable");
    let j = verdict_json(&v);
    assert!(j["saved_minutes"].is_null(), "{j}");
    assert!(j.get("save_minutes").is_none(), "{j}");
    let line = day_line(&[j.to_string()]);
    assert!(!line.contains("估少等"), "{line}");
}

#[test]
fn r3_false_green_needs_a_comparable_cell() {
    // main cancelled the only cell: nothing comparable => unknown, never "0"
    let mut f = base();
    f.main_runs[0].jobs[0].conclusion = Some("cancelled".into());
    let v = judge(&f);
    assert_eq!(v.shadow_false_green, None);
    assert_eq!(v.compared_cells, 0);
    assert_eq!(v.uncomparable_cells.len(), 1);
    let line = day_line(&[verdict_json(&v).to_string()]);
    assert!(line.contains("影子假綠 UNKNOWN"), "{line}");
    assert!(line.contains("未可比較 1 格"), "{line}");
    // no reusable cell at all (missing candidate) => unknown too
    let mut f = base();
    f.cand_runs.clear();
    let v = judge(&f);
    assert_eq!(v.shadow_false_green, None);
    assert!(day_line(&[verdict_json(&v).to_string()]).contains("影子假綠 UNKNOWN"));
    // real main failure is counted; real success is 0 of 1
    let mut f = base();
    f.main_runs[0].jobs[0].conclusion = Some("failure".into());
    let line = day_line(&[verdict_json(&judge(&f)).to_string()]);
    assert!(line.contains("影子假綠 1 / 可比較 1 格"), "{line}");
    let line = day_line(&[verdict_json(&judge(&base())).to_string()]);
    assert!(line.contains("影子假綠 0 / 可比較 1 格"), "{line}");
}

#[test]
fn r4_candidate_finished_after_main_started_is_refused() {
    let mut f = base();
    let start = f.main_runs[0].run_started_at.unwrap();
    let j = &mut f.cand_runs[0].jobs[0];
    j.started_at = Some(start - 1000);
    j.completed_at = Some(start + 1);
    let r = reasons(&judge(&f));
    assert!(r.contains("finished_after_main"), "{r}");
    // boundaries: 0 s and exactly 24 h before are fine, 24 h + 1 s is not
    for (gap, ok) in [(0, true), (MAX_AGE_S, true), (MAX_AGE_S + 1, false)] {
        let mut f = base();
        let j = &mut f.cand_runs[0].jobs[0];
        j.completed_at = Some(start - gap);
        j.started_at = Some(start - gap - 1790);
        let v = judge(&f);
        assert_eq!(v.state == "reusable", ok, "gap {gap}: {}", reasons(&v));
    }
}

#[test]
fn r4_rerun_attempt_uses_its_own_start_and_records_the_candidate_attempt() {
    let mut f = base();
    // main re-run: attempt 2 started 30 h after the run was created
    f.main_runs[0].run_attempt = 2;
    let start = f.main_runs[0].created_at + 30 * 3600;
    f.main_runs[0].run_started_at = Some(start);
    let mj = &mut f.main_runs[0].jobs[0];
    mj.started_at = Some(start + 10);
    mj.completed_at = Some(start + 1810);
    f.main_runs[0].updated_at = Some(start + 1810);
    let v = judge(&f);
    assert!(reasons(&v).contains("older_than_24h"), "{}", reasons(&v));
    assert!(reasons(&v).contains("attempt 2"), "{}", reasons(&v));
    let mut f = base();
    f.cand_runs[0].run_attempt = 3;
    let v = judge(&f);
    assert_eq!(v.cells[0].cand_run_attempt, Some(3));
    assert_eq!(verdict_json(&v)["cells"][0]["cand_run_attempt"], 3);
}

// ---------- bb7dc #575 re-review (af9fe60d): what both sides lack, and missing steps ----------

#[test]
fn r1b_both_sides_lack_a_required_matrix_cell_is_unknown() {
    let mut f = base();
    f.required.push("smoke (macos-latest, 3.9)".into());
    let v = judge(&f);
    assert_eq!(v.state, "unknown", "{:?}", names(&v));
    assert!(
        v.note.contains("smoke (macos-latest, 3.9) (PR 0, main 0)"),
        "{}",
        v.note
    );
}

#[test]
fn r1b_both_sides_lack_a_required_workflow_is_unknown() {
    let mut f = base();
    f.required.push("rust (macos-latest)".into()); // an aibd.yml job; neither side ran aibd.yml
    let v = judge(&f);
    assert_eq!(v.state, "unknown", "{:?}", names(&v));
    assert!(
        v.note.contains("rust (macos-latest) (PR 0, main 0)"),
        "{}",
        v.note
    );
}

#[test]
fn r1b_empty_required_list_is_unknown() {
    let mut f = base();
    f.required.clear();
    let v = judge(&f);
    assert_eq!(v.state, "unknown");
    assert!(v.note.starts_with("required_list_empty"), "{}", v.note);
}

#[test]
fn r1b_missing_steps_are_never_equal() {
    for (pr, main, side) in [
        (false, false, "both sides"),
        (false, true, "the PR side"),
        (true, false, "main's side"),
    ] {
        let mut f = base();
        let cj = &mut f.cand_runs[0].jobs[0];
        cj.steps_known = pr;
        if !pr {
            cj.steps.clear();
        }
        let mj = &mut f.main_runs[0].jobs[0];
        mj.steps_known = main;
        if !main {
            mj.steps.clear();
        }
        let v = judge(&f);
        assert_eq!(v.state, "not_reusable", "{side}: {}", reasons(&v));
        assert!(
            reasons(&v).contains(&format!(
                "steps_unknown: step list missing or incomplete on {side}"
            )),
            "{}",
            reasons(&v)
        );
    }
    // the API shape: no "steps" key / an empty list => steps_known false
    let j =
        job_from_json(&serde_json::json!({"id": 1, "name": "x", "status": "completed"})).unwrap();
    assert!(!j.steps_known);
    let j = job_from_json(&serde_json::json!({"id": 1, "name": "x", "steps": []})).unwrap();
    assert!(!j.steps_known);
}

// ---------- bb7dc #575 3rd review (3c5254a0): parsing is all-or-nothing ----------

fn rulesets(list: &str, details: &[(u64, &str)]) -> Fake {
    let mut m = HashMap::new();
    m.insert(
        format!("repos/{REPO}/rulesets?per_page=100"),
        list.to_string(),
    );
    for (id, body) in details {
        m.insert(format!("repos/{REPO}/rulesets/{id}"), body.to_string());
    }
    Fake(m)
}

const GOOD: &str = r#"{"id":7,"conditions":{"ref_name":{"include":["~DEFAULT_BRANCH"],"exclude":[]}},"rules":[{"type":"required_status_checks","parameters":{"required_status_checks":[{"context":"smoke (ubuntu-latest, 3.12)"}]}}]}"#;
const SECOND: &str = r#"{"id":8,"conditions":{"ref_name":{"include":["refs/heads/main"],"exclude":[]}},"rules":[{"type":"required_status_checks","parameters":{"required_status_checks":[{"context":"rust (macos-latest)"}]}}]}"#;

#[test]
fn r1c_two_good_rulesets_union_and_known_skips() {
    let list = r#"[{"id":7,"enforcement":"active","target":"branch"},{"id":8,"enforcement":"active","target":"branch"},{"id":9,"enforcement":"evaluate","target":"branch"},{"id":10,"enforcement":"active","target":"tag"}]"#;
    let got = required_checks(&rulesets(list, &[(7, GOOD), (8, SECOND)]), REPO).unwrap();
    assert_eq!(
        got,
        vec!["rust (macos-latest)", "smoke (ubuntu-latest, 3.12)"]
    );
    // a ruleset that names main in exclude does not cover main
    let excl = r#"{"id":8,"conditions":{"ref_name":{"include":["~ALL"],"exclude":["refs/heads/main"]}},"rules":[{"type":"required_status_checks","parameters":{"required_status_checks":[{"context":"x"}]}}]}"#;
    let list2 = r#"[{"id":7,"enforcement":"active","target":"branch"},{"id":8,"enforcement":"active","target":"branch"}]"#;
    let got = required_checks(&rulesets(list2, &[(7, GOOD), (8, excl)]), REPO).unwrap();
    assert_eq!(got, vec!["smoke (ubuntu-latest, 3.12)"]);
}

#[test]
fn r1c_one_good_one_unreadable_ruleset_is_an_error_not_a_smaller_list() {
    let good = r#"{"id":7,"enforcement":"active","target":"branch"}"#;
    let bad_lists = [
        ("no enforcement", r#"{"id":8,"target":"branch"}"#),
        (
            "enforcement not a string",
            r#"{"id":8,"enforcement":1,"target":"branch"}"#,
        ),
        (
            "unknown enforcement",
            r#"{"id":8,"enforcement":"maybe","target":"branch"}"#,
        ),
        ("no target", r#"{"id":8,"enforcement":"active"}"#),
        (
            "unknown target",
            r#"{"id":8,"enforcement":"active","target":"repository"}"#,
        ),
    ];
    for (why, second) in bad_lists {
        let list = format!("[{good},{second}]");
        let r = required_checks(&rulesets(&list, &[(7, GOOD), (8, SECOND)]), REPO);
        assert!(r.is_err(), "{why}: {r:?}");
    }
    let list = format!(r#"[{good},{{"id":8,"enforcement":"active","target":"branch"}}]"#);
    let bad_details = [
        (
            "rule without type",
            r#"{"id":8,"conditions":{"ref_name":{"include":["~DEFAULT_BRANCH"],"exclude":[]}},"rules":[{"parameters":{"required_status_checks":[{"context":"rust (macos-latest)"}]}}]}"#,
        ),
        (
            "check without context",
            r#"{"id":8,"conditions":{"ref_name":{"include":["~DEFAULT_BRANCH"],"exclude":[]}},"rules":[{"type":"required_status_checks","parameters":{"required_status_checks":[{"integration_id":1}]}}]}"#,
        ),
        (
            "no exclude list",
            r#"{"id":8,"conditions":{"ref_name":{"include":["~DEFAULT_BRANCH"]}},"rules":[]}"#,
        ),
        (
            "glob include",
            r#"{"id":8,"conditions":{"ref_name":{"include":["refs/heads/ma*"],"exclude":[]}},"rules":[]}"#,
        ),
        (
            "include entry not a string",
            r#"{"id":8,"conditions":{"ref_name":{"include":[1],"exclude":[]}},"rules":[]}"#,
        ),
        (
            "no rules",
            r#"{"id":8,"conditions":{"ref_name":{"include":["~DEFAULT_BRANCH"],"exclude":[]}}}"#,
        ),
    ];
    for (why, second) in bad_details {
        let r = required_checks(&rulesets(&list, &[(7, GOOD), (8, second)]), REPO);
        assert!(r.is_err(), "{why}: {r:?}");
    }
}

#[test]
fn r1c_a_step_without_number_name_or_conclusion_is_unknown() {
    let ok = serde_json::json!({"id": 1, "name": "x", "steps": [
        {"number": 1, "name": "Set up job", "conclusion": "success"},
        {"number": 2, "name": "smoke test", "conclusion": "skipped"}]});
    assert!(job_from_json(&ok).unwrap().steps_known);
    for (why, bad) in [
        (
            "no number",
            serde_json::json!({"name": "smoke test", "conclusion": "success"}),
        ),
        (
            "no name",
            serde_json::json!({"number": 2, "conclusion": "success"}),
        ),
        (
            "empty name",
            serde_json::json!({"number": 2, "name": "", "conclusion": "success"}),
        ),
        (
            "no conclusion",
            serde_json::json!({"number": 2, "name": "smoke test"}),
        ),
        (
            "conclusion null",
            serde_json::json!({"number": 2, "name": "smoke test", "conclusion": null}),
        ),
    ] {
        let mut j = ok.clone();
        j["steps"][1] = bad;
        assert!(!job_from_json(&j).unwrap().steps_known, "{why}");
    }
    // both sides malformed the same way is still not "equal"
    let mut f = base();
    for j in [&mut f.cand_runs[0].jobs[0], &mut f.main_runs[0].jobs[0]] {
        j.steps_known = false;
    }
    let v = judge(&f);
    assert!(
        reasons(&v).contains("steps_unknown: step list missing or incomplete on both sides"),
        "{}",
        reasons(&v)
    );
}
