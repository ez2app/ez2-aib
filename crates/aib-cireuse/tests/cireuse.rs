// SPDX-License-Identifier: Apache-2.0
//! S1 shadow: the positive case shaped like #567 (11-09) plus one knife per way a "reuse" could be fake.
#![allow(clippy::unwrap_used)]

use aib_cireuse::gh::{facts, paged, Source};
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
        ("cell not run on this PR".into(), if real { sk.clone() } else { ok.clone() }),
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
        main_runs: vec![run(1, CI, "push", T0, vec![job(11, "smoke (ubuntu-latest, 3.12)", T0 + 10, 1800, UB, None)])],
        cand_runs: vec![run(2, CI, "pull_request", pr_t, vec![job(21, "smoke (ubuntu-latest, 3.12)", pr_t + 10, 1790, UB, Some(TESTED))])],
        tested_trees: BTreeMap::from([(TESTED.to_string(), TREE.to_string())]),
    }
}

fn reasons(v: &Verdict) -> String {
    v.cells.iter().flat_map(|c| c.reasons.clone()).collect::<Vec<_>>().join(" | ")
}

#[test]
fn positive_same_tree_same_cell_could_reuse_with_measured_minutes() {
    let v = judge(&base());
    assert_eq!(v.state, "reusable", "{}", reasons(&v));
    assert!(v.cells.iter().all(|c| c.reusable));
    assert_eq!(v.save_minutes, Some(30.2)); // 10 s queue + 1800 s run, main's own wall clock
    assert!(v.shadow_false_green.is_empty());
    let j = verdict_json(&v);
    assert_eq!(j["writes_status"], false);
    assert_eq!(j["skips_cells"], false);
}

#[test]
fn knife_same_tree_different_workflow_file() {
    let mut f = base();
    f.cand_runs[0].path = ".github/workflows/aibd.yml".into();
    let v = judge(&f);
    assert_eq!(v.state, "not_reusable");
    assert!(reasons(&v).starts_with("no_candidate_run"), "{}", reasons(&v));
    assert_eq!(v.save_minutes, None);
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
    assert!(reasons(&v).contains("steps_differ: step 2"), "{}", reasons(&v));
}

#[test]
fn knife_empty_run_too_short() {
    let mut f = base();
    let j = &mut f.cand_runs[0].jobs[0];
    j.completed_at = Some(j.started_at.unwrap() + 3);
    let v = judge(&f);
    assert!(reasons(&v).contains("too_short: PR job ran 3 s"), "{}", reasons(&v));
    assert_eq!(v.state, "not_reusable");
}

#[test]
fn knife_wrong_attempt_latest_attempt_failed() {
    let mut f = base();
    f.cand_runs[0].run_attempt = 2;
    f.cand_runs[0].jobs[0].conclusion = Some("failure".into());
    let v = judge(&f);
    assert!(reasons(&v).contains("candidate_not_success") && reasons(&v).contains("attempt 2"), "{}", reasons(&v));
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
    assert!(reasons(&v).contains("candidate_not_success"), "{}", reasons(&v));
}

#[test]
fn knife_tree_differs_base_moved() {
    let mut f = base();
    f.tested_trees.insert(TESTED.into(), "1111111111111111111111111111111111111111".into());
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
    f.main_runs[0].jobs.push(job(12, "smoke (macos-latest, 3.9)", T0 + 10, 3000, "macos-15-arm64 20261028.1", None));
    let v = judge(&f);
    assert_eq!(v.state, "partial");
    assert_eq!(v.save_minutes, None);
}

#[test]
fn shadow_false_green_is_flagged_when_main_itself_failed() {
    let mut f = base();
    f.main_runs[0].jobs[0].conclusion = Some("failure".into());
    let v = judge(&f);
    assert_eq!(v.shadow_false_green, vec![format!("{CI} / smoke (ubuntu-latest, 3.12)")]);
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
    assert_eq!(parse_tested_sha(&two), None, "two different fetched commits = unknown");
    assert_eq!(parse_image("no group here"), None);
    assert_eq!(parse_time("2026-11-09T04:20:16Z"), Some(1_794_198_016));
    assert_eq!(parse_time("2026-11-09T04:20:16.123+00:00"), Some(1_794_198_016));
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
    assert!(line.starts_with("今天 1 次可沿用、估少等 30 分(判了 1 次"), "{line}");
    assert!(line.contains("帳本壞行 1"), "{line}");
}

struct Fake(HashMap<String, String>);

impl Source for Fake {
    fn get(&self, path: &str) -> Result<String, String> {
        self.0.get(path).cloned().ok_or_else(|| format!("404 {path}"))
    }
}

#[test]
fn knife_missing_page_is_an_error_not_fewer_runs() {
    let mut m = HashMap::new();
    m.insert("x?per_page=100&page=1".to_string(), r#"{"total_count":150,"jobs":[{"id":1}]}"#.to_string());
    m.insert("x?per_page=100&page=2".to_string(), r#"{"total_count":150,"jobs":[]}"#.to_string());
    let e = paged(&Fake(m), "x", "jobs").unwrap_err();
    assert!(e.starts_with("missing_page"), "{e}");
    let mut m = HashMap::new();
    m.insert("x?per_page=100&page=1".to_string(), r#"{"total_count":2,"jobs":[{"id":1}]}"#.to_string());
    m.insert("x?per_page=100&page=2".to_string(), r#"{"total_count":2,"jobs":[{"id":2}]}"#.to_string());
    assert_eq!(paged(&Fake(m), "x", "jobs").unwrap().len(), 2);
}

#[test]
fn facts_end_to_end_from_api_shapes() {
    let jobj = |id: u64, start: &str, end: &str| {
        format!(
            r#"{{"id":{id},"name":"smoke (ubuntu-latest, 3.12)","status":"completed","conclusion":"success","started_at":"{start}","completed_at":"{end}","steps":[{{"name":"smoke test","conclusion":"success"}}]}}"#
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
    put(format!("repos/{REPO}/commits/{MAIN}"), format!(r#"{{"sha":"{MAIN}","commit":{{"tree":{{"sha":"{TREE}"}}}},"parents":[{{"sha":"{P1}"}},{{"sha":"{P2}"}}]}}"#));
    put(
        format!("repos/{REPO}/actions/runs?head_sha={MAIN}&event=push&per_page=100&page=1"),
        format!(r#"{{"total_count":1,"workflow_runs":[{}]}}"#, runj(1, MAIN, "push", "2026-11-09T04:20:16Z", "2026-11-09T04:51:00Z")),
    );
    put(
        format!("repos/{REPO}/actions/runs/1/attempts/1/jobs?per_page=100&page=1"),
        format!(r#"{{"total_count":1,"jobs":[{}]}}"#, jobj(11, "2026-11-09T04:20:27Z", "2026-11-09T04:50:27Z")),
    );
    put(
        format!("repos/{REPO}/actions/runs?head_sha={P2}&event=pull_request&per_page=100&page=1"),
        format!(r#"{{"total_count":1,"workflow_runs":[{}]}}"#, runj(2, P2, "pull_request", "2026-11-09T02:53:03Z", "2026-11-09T03:24:00Z")),
    );
    put(
        format!("repos/{REPO}/actions/runs/2/attempts/1/jobs?per_page=100&page=1"),
        format!(r#"{{"total_count":1,"jobs":[{}]}}"#, jobj(21, "2026-11-09T02:53:16Z", "2026-11-09T03:23:20Z")),
    );
    put(format!("repos/{REPO}/actions/jobs/11/logs"), log(""));
    put(format!("repos/{REPO}/actions/jobs/21/logs"), log(&format!("x fetch origin +{TESTED}:refs/remotes/pull/567/merge\n")));
    put(format!("repos/{REPO}/git/commits/{TESTED}"), format!(r#"{{"tree":{{"sha":"{TREE}"}}}}"#));
    let f = facts(&Fake(m.clone()), REPO, MAIN).unwrap();
    let v = judge(&f);
    assert_eq!(v.state, "reusable", "{}", reasons(&v));
    assert_eq!(v.save_minutes, Some(30.7));
    // the tested commit cannot be read => not reuse (never "unreadable means fine")
    m.remove(&format!("repos/{REPO}/git/commits/{TESTED}"));
    let v = judge(&facts(&Fake(m.clone()), REPO, MAIN).unwrap());
    assert!(reasons(&v).contains("tested_tree_unknown"), "{}", reasons(&v));
    // a list read failing is an error (recorded as unknown by the CLI)
    m.remove(&format!("repos/{REPO}/actions/runs/2/attempts/1/jobs?per_page=100&page=1"));
    assert!(facts(&Fake(m), REPO, MAIN).is_err());
}
