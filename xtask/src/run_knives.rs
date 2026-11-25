//! crates/aib-knife/run-knives.sh run for real on a folder made here (S66-loop,
//! DESIGN-knives-loop-ebb36-1117 §刀): which scripts of a job run, which are skipped by OS,
//! that one red script does not stop the rest, that a bad header, an empty job and a script
//! over its limit are red, that a script returning 0 over a knife that missed is red, and that
//! `env=` reaches only its own script. Unix only: the
//! watchdog kills a process group, which Git Bash on Windows does not have the same way.
#![cfg(unix)]

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU32, Ordering};

static NEXT: AtomicU32 = AtomicU32::new(0);

fn runner() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("crates/aib-knife/run-knives.sh")
}

/// A repo root with `crates/<c>/knives/<f>` for each `(path, text)`; one folder per call.
fn repo(files: &[(&str, &str)]) -> PathBuf {
    let n = NEXT.fetch_add(1, Ordering::Relaxed);
    let r = std::env::temp_dir().join(format!("run-knives-{}-{n}", std::process::id()));
    let _ = std::fs::remove_dir_all(&r);
    for (p, t) in files {
        let f = r.join(p);
        std::fs::create_dir_all(f.parent().unwrap()).unwrap();
        std::fs::write(f, t).unwrap();
    }
    r
}

fn run(root: &Path, job: &str) -> (i32, String) {
    run_shard(root, job, None)
}

/// `shard` = AIB_KNIVES_SHARD; None = not set (also not taken from a sharded CI cell around the test)
fn run_shard(root: &Path, job: &str, shard: Option<&str>) -> (i32, String) {
    run_env(root, job, shard, &[])
}

/// `run_shard` with more variables set (AIB_KNIVES_PLAN, AIB_KNIVES_LIST); neither is taken from
/// the cell around the test.
fn run_env(root: &Path, job: &str, shard: Option<&str>, env: &[(&str, &str)]) -> (i32, String) {
    let mut c = Command::new("bash");
    c.arg(runner())
        .arg(job)
        .current_dir(root)
        .env("AIB_KNIVES_SECS_PER_MIN", "1")
        .env_remove("AIB_KNIVES_NO_SE")
        .env_remove("AIB_KNIVES_PLAN")
        .env_remove("AIB_KNIVES_LIST")
        .env_remove("AIB_KNIVES_LIMITS")
        .env_remove("AIB_SKIP_FILES")
        .env_remove("AIB_KNIVES_ONLY_OS")
        .env_remove("AIB_KNIVES_SHARD");
    if let Some(s) = shard {
        c.env("AIB_KNIVES_SHARD", s);
    }
    for (k, v) in env {
        c.env(k, v);
    }
    let o = c.output().unwrap();
    let _ = std::fs::remove_dir_all(root);
    (
        o.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&o.stdout).into_owned(),
    )
}

fn here_os() -> &'static str {
    if cfg!(target_os = "macos") {
        "macos"
    } else {
        "linux"
    }
}

#[test]
fn the_scripts_of_the_job_run_and_the_other_os_is_skipped() {
    let other = if here_os() == "macos" {
        "windows"
    } else {
        "macos"
    };
    let r = repo(&[
        ("crates/a/knives/k1.sh", "#!/usr/bin/env bash\n# aib-knives: os=any\necho ran-k1\n"),
        (
            "crates/b/knives/k2.sh",
            "#!/usr/bin/env bash\n# aib-knives: job=cloud-knives\n# aib-knives: os=any\necho ran-k2\n",
        ),
        (
            "crates/c/knives/k3.sh",
            &format!("#!/usr/bin/env bash\n# aib-knives: os={other}\necho ran-k3\n"),
        ),
    ]);
    let (rc, out) = run(&r, "rust");
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("ran-k1"), "{out}");
    assert!(!out.contains("ran-k2"), "another job's script ran: {out}");
    assert!(!out.contains("ran-k3"), "another OS's script ran: {out}");
    assert!(
        out.contains(&format!(
            "KNIVES-SCRIPT crates/c/knives/k3.sh: SKIPPED why=not_this_os os={other}"
        )),
        "{out}"
    );
    assert!(out.contains("KNIVES-OK job=rust scripts=2"), "{out}");
}

#[test]
fn one_red_script_does_not_stop_the_others_and_the_run_is_red() {
    let r = repo(&[
        (
            "crates/a/knives/k1.sh",
            "#!/usr/bin/env bash\n# aib-knives: os=any\nexit 3\n",
        ),
        (
            "crates/b/knives/k2.sh",
            "#!/usr/bin/env bash\n# aib-knives: os=any\necho ran-k2\n",
        ),
    ]);
    let (rc, out) = run(&r, "rust");
    assert_eq!(rc, 1, "{out}");
    assert!(
        out.contains("KNIVES-SCRIPT crates/a/knives/k1.sh: rc=3"),
        "{out}"
    );
    assert!(out.contains("ran-k2"), "{out}");
    assert!(out.contains("KNIVES-RED 1: crates/a/knives/k1.sh"), "{out}");
}

#[test]
fn a_bad_header_and_a_job_with_no_script_are_red() {
    // a good script of the job sits next to the bad one: without it the run is red for
    // "no script belongs to the job" alone, and a bad header that is not counted would pass
    let r = repo(&[
        (
            "crates/a/knives/k1.sh",
            "#!/usr/bin/env bash\n# aib-knives: os=linux\necho ran-k1\n",
        ),
        (
            "crates/b/knives/k2.sh",
            "#!/usr/bin/env bash\n# aib-knives: os=any\necho ran-k2\n",
        ),
    ]);
    let (rc, out) = run(&r, "rust");
    assert!(out.contains("ran-k2"), "the good script did not run: {out}");
    assert!(!out.contains("KNIVES-NONE"), "{out}");
    assert_eq!(rc, 1, "{out}");
    assert!(
        out.contains("KNIVES-HEADER-BAD crates/a/knives/k1.sh: os=linux"),
        "{out}"
    );
    assert!(!out.contains("ran-k1"), "{out}");

    let r = repo(&[(
        "crates/a/knives/k1.sh",
        "#!/usr/bin/env bash\n# aib-knives: os=any\n",
    )]);
    let (rc, out) = run(&r, "nobody");
    assert_eq!(rc, 1, "{out}");
    assert!(out.contains("KNIVES-NONE job=nobody"), "{out}");
}

#[test]
fn a_script_over_its_limit_is_killed_with_its_children_and_red() {
    // timeout=1m is one second here (AIB_KNIVES_SECS_PER_MIN=1)
    let r = repo(&[(
        "crates/a/knives/k1.sh",
        // the child lets go of stdout: holding it, it would keep output() waiting until it ends by
        // itself, and an unkilled child would look gone by the time it is checked
        "#!/usr/bin/env bash\n# aib-knives: os=any\n# aib-knives: timeout=1m\nsleep 30 >/dev/null 2>&1 &\necho $! > child.pid\nwait\n",
    )]);
    let pid_file = r.join("child.pid");
    let o = Command::new("bash")
        .arg(runner())
        .arg("rust")
        .current_dir(&r)
        .env("AIB_KNIVES_SECS_PER_MIN", "1")
        .env_remove("AIB_KNIVES_SHARD")
        .output()
        .unwrap();
    let out = String::from_utf8_lossy(&o.stdout).into_owned();
    let child = std::fs::read_to_string(&pid_file).unwrap_or_default();
    let _ = std::fs::remove_dir_all(&r);
    assert_eq!(o.status.code(), Some(1), "{out}");
    assert!(
        out.contains("KNIVES-SCRIPT crates/a/knives/k1.sh: not_run why_not=timeout"),
        "{out}"
    );
    let child = child.trim();
    assert!(!child.is_empty(), "the script did not start: {out}");
    // the script's own child was in its process group, so it is gone too (given two
    // seconds: a killed orphan may still be a zombie for a moment, and `kill -0` sees one)
    let alive = Command::new("bash")
        .arg("-c")
        .arg(format!(
            "for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 {child} 2>/dev/null || exit 1; sleep 0.2; done"
        ))
        .status()
        .unwrap()
        .success();
    assert!(!alive, "child {child} still running after the timeout");
}

#[test]
fn an_env_header_reaches_only_its_own_script() {
    let r = repo(&[
        (
            "crates/a/knives/k1.sh",
            "#!/usr/bin/env bash\n# aib-knives: os=any\n# aib-knives: env=AIB_KNIVES_NO_SE=1\necho k1-se=${AIB_KNIVES_NO_SE:-unset}\n",
        ),
        (
            "crates/b/knives/k2.sh",
            "#!/usr/bin/env bash\n# aib-knives: os=any\necho k2-se=${AIB_KNIVES_NO_SE:-unset}\n",
        ),
    ]);
    let (rc, out) = run(&r, "rust");
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("k1-se=1"), "{out}");
    assert!(out.contains("k2-se=unset"), "{out}");

    let r = repo(&[(
        "crates/a/knives/k1.sh",
        "#!/usr/bin/env bash\n# aib-knives: os=any\n# aib-knives: env=A=$(id)\n",
    )]);
    let (rc, out) = run(&r, "rust");
    assert_eq!(rc, 1, "{out}");
    assert!(
        out.contains("KNIVES-HEADER-BAD crates/a/knives/k1.sh: env=A=$(id)"),
        "{out}"
    );
}

/// Five scripts of the job that run here, one of another OS and one of another job.
/// A script that prints a knife that did not bite and still returns 0 (no `set -e`, or its
/// last command passed) is red: the line is what counts, not what the script returned
/// (5d33a 11-20, M: six scripts stayed green over a MISSES line).
#[test]
fn a_script_that_returns_0_over_a_knife_that_missed_is_red() {
    // the knife words are put together in the script, so this file never holds such a line itself
    let fake = |verdict: &str| {
        format!(
            "#!/usr/bin/env bash\n# aib-knives: os=any\nprintf 'KNI%s a_test: {verdict}\\n' FE\necho after-the-miss\nexit 0\n"
        )
    };
    for verdict in [
        "MISSES rc=0 failed=[]",
        "INVALID no_such_text",
        "pattern found 0 times in x.rs; nothing changed",
    ] {
        let r = repo(&[
            ("crates/a/knives/k1.sh", fake(verdict).as_str()),
            (
                "crates/b/knives/k2.sh",
                "#!/usr/bin/env bash\n# aib-knives: os=any\nprintf 'KNI%s b_test: BITES rc=101\\n' FE\n",
            ),
        ]);
        let (rc, out) = run(&r, "rust");
        assert_eq!(rc, 1, "{verdict}: {out}");
        assert!(
            out.contains("after-the-miss"),
            "the script's own output is shown: {out}"
        );
        assert!(
            out.contains("KNIVES-SCRIPT crates/a/knives/k1.sh: rc=0 secs="),
            "{out}"
        );
        assert!(
            out.contains("KNIVES-SCRIPT crates/a/knives/k1.sh: missed=1 rc=0"),
            "{verdict}: {out}"
        );
        assert!(
            out.contains("KNIVES-RED 1: crates/a/knives/k1.sh"),
            "only the script with the miss is red: {out}"
        );
        assert!(
            !out.contains("KNIVES-SCRIPT crates/b/knives/k2.sh: missed="),
            "a knife that bit is not a miss: {out}"
        );
    }
    // a compile-time knife (scripts/knife-build.py prints KNIFE-BUILD) that missed counts the same
    let r = repo(&[(
        "crates/a/knives/k1.sh",
        "#!/usr/bin/env bash\n# aib-knives: os=any\nprintf 'KNI%s c_test: MISSES rc=0\\n' FE-BUILD\nexit 0\n",
    )]);
    let (rc, out) = run(&r, "rust");
    assert_eq!(rc, 1, "a build knife that missed: {out}");
    assert!(
        out.contains("KNIVES-SCRIPT crates/a/knives/k1.sh: missed=1 rc=0"),
        "{out}"
    );
    // a line that only talks about a miss (not a knife line from its first column) is not one
    let r = repo(&[(
        "crates/a/knives/k1.sh",
        "#!/usr/bin/env bash\n# aib-knives: os=any\necho 'note: KNIFE x: MISSES was fixed'\n",
    )]);
    let (rc, out) = run(&r, "rust");
    assert_eq!(rc, 0, "{out}");
}

fn five_and_two_others() -> Vec<(String, String)> {
    let other = if here_os() == "macos" {
        "windows"
    } else {
        "macos"
    };
    let mut v: Vec<(String, String)> = (1..=5)
        .map(|i| {
            (
                format!("crates/a/knives/k{i}.sh"),
                format!("#!/usr/bin/env bash\n# aib-knives: os=any\necho ran-k{i}\n"),
            )
        })
        .collect();
    v.push((
        "crates/a/knives/k0.sh".into(),
        format!("#!/usr/bin/env bash\n# aib-knives: os={other}\necho ran-k0\n"),
    ));
    v.push((
        "crates/b/knives/k9.sh".into(),
        "#!/usr/bin/env bash\n# aib-knives: job=cloud-knives\n# aib-knives: os=any\necho ran-k9\n"
            .into(),
    ));
    v
}

fn ran(out: &str) -> Vec<u32> {
    (0..=9)
        .filter(|i| out.contains(&format!("ran-k{i}\n")))
        .collect()
}

#[test]
fn shards_take_turns_over_the_scripts_that_run_here_and_together_run_each_once() {
    // S88-split (Andy 11-20): N cells side by side, each with AIB_KNIVES_SHARD=k/N
    let files = five_and_two_others();
    let refs: Vec<(&str, &str)> = files
        .iter()
        .map(|(p, t)| (p.as_str(), t.as_str()))
        .collect();
    let (rc1, out1) = run_shard(&repo(&refs), "rust", Some("1/2"));
    let (rc2, out2) = run_shard(&repo(&refs), "rust", Some("2/2"));
    assert_eq!((rc1, rc2), (0, 0), "{out1}\n{out2}");
    // the other-OS script does not take a turn: k1..k5 alternate in name order
    assert_eq!(ran(&out1), vec![1, 3, 5], "{out1}");
    assert_eq!(ran(&out2), vec![2, 4], "{out2}");
    assert!(
        out1.contains("KNIVES-SCRIPT crates/a/knives/k2.sh: SKIPPED why=other_shard shard=1/2"),
        "{out1}"
    );
    assert!(out1.contains("SKIPPED why=not_this_os"), "{out1}");
    assert!(
        out1.contains("KNIVES-OK job=rust scripts=6 shard=1/2 ran=3"),
        "{out1}"
    );
    assert!(
        out2.contains("KNIVES-OK job=rust scripts=6 shard=2/2 ran=2"),
        "{out2}"
    );

    // not set and 1/1 are the same: every script, and the OK line of before
    let (rc, out) = run_shard(&repo(&refs), "rust", None);
    assert_eq!(rc, 0, "{out}");
    assert_eq!(ran(&out), vec![1, 2, 3, 4, 5], "{out}");
    assert!(out.contains("KNIVES-OK job=rust scripts=6\n"), "{out}");
    let (rc, out) = run_shard(&repo(&refs), "rust", Some("1/1"));
    assert_eq!(rc, 0, "{out}");
    assert_eq!(ran(&out), vec![1, 2, 3, 4, 5], "{out}");
}

#[test]
fn a_shard_that_gets_no_script_is_red_and_a_shard_not_k_of_n_is_refused() {
    let files = five_and_two_others();
    let refs: Vec<(&str, &str)> = files
        .iter()
        .map(|(p, t)| (p.as_str(), t.as_str()))
        .collect();
    let (rc, out) = run_shard(&repo(&refs), "rust", Some("6/6"));
    assert_eq!(rc, 1, "{out}");
    assert_eq!(ran(&out), Vec::<u32>::new(), "{out}");
    assert!(out.contains("KNIVES-NONE job=rust shard=6/6"), "{out}");
    for bad in ["0/3", "4/3", "3", "1/0", "a/b", "1/3 "] {
        let (rc, out) = run_shard(&repo(&refs), "rust", Some(bad));
        assert_eq!(rc, 2, "{bad}: {out}");
        assert_eq!(ran(&out), Vec::<u32>::new(), "{bad}: {out}");
        assert!(out.contains("is not k/N"), "{bad}: {out}");
    }
}

#[test]
fn the_scripts_do_not_see_the_shard() {
    // knives-run-knives.sh runs this file's tests from inside a sharded cell
    let r = repo(&[(
        "crates/a/knives/k1.sh",
        "#!/usr/bin/env bash\n# aib-knives: os=any\necho shard=${AIB_KNIVES_SHARD:-unset}\n",
    )]);
    let (rc, out) = run_shard(&r, "rust", Some("1/1"));
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("shard=unset"), "{out}");
}

/// `five_and_two_others` with `plan` written beside them as `plan.tsv` (the folder goes with the run).
fn repo_with_plan(plan: &str) -> PathBuf {
    let files = five_and_two_others();
    let mut refs: Vec<(&str, &str)> = files
        .iter()
        .map(|(p, t)| (p.as_str(), t.as_str()))
        .collect();
    refs.push(("plan.tsv", plan));
    repo(&refs)
}

fn k(i: u32) -> String {
    format!("crates/a/knives/k{i}.sh")
}

#[test]
fn a_plan_names_each_scripts_cell_and_the_cells_together_run_each_once() {
    // not the name-order turns (1 3 5 / 2 4): the plan puts k3 k5 in cell 1 and k1 k2 k4 in cell 2
    let plan = format!(
        "2\t{}\n2\t{}\n1\t{}\n2\t{}\n1\t{}\n",
        k(1),
        k(2),
        k(3),
        k(4),
        k(5)
    );
    let env = [("AIB_KNIVES_PLAN", "plan.tsv")];
    let (rc1, out1) = run_env(&repo_with_plan(&plan), "rust", Some("1/2"), &env);
    let (rc2, out2) = run_env(&repo_with_plan(&plan), "rust", Some("2/2"), &env);
    assert_eq!((rc1, rc2), (0, 0), "{out1}\n{out2}");
    assert_eq!(ran(&out1), vec![3, 5], "{out1}");
    assert_eq!(ran(&out2), vec![1, 2, 4], "{out2}");
    assert!(
        out1.contains("KNIVES-SCRIPT crates/a/knives/k1.sh: SKIPPED why=other_shard shard=1/2"),
        "{out1}"
    );
    // the other-OS script needs no line in the plan
    assert!(out1.contains("SKIPPED why=not_this_os"), "{out1}");
    assert!(
        out1.contains("KNIVES-OK job=rust scripts=6 shard=1/2 ran=2"),
        "{out1}"
    );
    assert!(
        out2.contains("KNIVES-OK job=rust scripts=6 shard=2/2 ran=3"),
        "{out2}"
    );
}

#[test]
fn a_script_that_runs_here_and_is_not_in_the_plan_is_red_in_every_cell() {
    // k4 has no line: without the red it would be in no cell at all and nobody would see it
    let plan = format!("1\t{}\n2\t{}\n1\t{}\n2\t{}\n", k(1), k(2), k(3), k(5));
    let env = [("AIB_KNIVES_PLAN", "plan.tsv")];
    for (shard, others) in [("1/2", vec![1, 3]), ("2/2", vec![2, 5])] {
        let (rc, out) = run_env(&repo_with_plan(&plan), "rust", Some(shard), &env);
        assert_eq!(rc, 1, "{shard}: {out}");
        assert!(
            out.contains("KNIVES-PLAN-MISSING crates/a/knives/k4.sh: the plan has no cell for it"),
            "{shard}: {out}"
        );
        assert!(
            out.contains("KNIVES-RED 1: crates/a/knives/k4.sh"),
            "{shard}: {out}"
        );
        // the scripts the plan does name still run where it says
        assert_eq!(ran(&out), others, "{shard}: {out}");
    }
}

#[test]
fn a_plan_that_is_not_read_or_not_understood_is_refused() {
    let good = format!(
        "1\t{}\n2\t{}\n1\t{}\n2\t{}\n1\t{}\n",
        k(1),
        k(2),
        k(3),
        k(4),
        k(5)
    );
    // no such file
    let (rc, out) = run_env(
        &repo_with_plan(&good),
        "rust",
        Some("1/2"),
        &[("AIB_KNIVES_PLAN", "no-such-plan.tsv")],
    );
    assert_eq!(rc, 2, "{out}");
    assert_eq!(ran(&out), Vec::<u32>::new(), "{out}");
    assert!(out.contains("cannot be read"), "{out}");
    // a cell number of 0, one above N, a space for the tab, no number, no path
    for bad in [
        format!("0\t{}", k(1)),
        format!("3\t{}", k(1)),
        format!("1 {}", k(1)),
        format!("x\t{}", k(1)),
        "1\t".to_string(),
        k(1),
    ] {
        let plan = format!("{good}{bad}\n");
        let (rc, out) = run_env(
            &repo_with_plan(&plan),
            "rust",
            Some("1/2"),
            &[("AIB_KNIVES_PLAN", "plan.tsv")],
        );
        assert_eq!(rc, 2, "{bad:?}: {out}");
        assert_eq!(ran(&out), Vec::<u32>::new(), "{bad:?}: {out}");
        assert!(
            out.contains("AIB_KNIVES_PLAN line is not"),
            "{bad:?}: {out}"
        );
    }
}

#[test]
fn list_mode_prints_the_scripts_that_would_run_here_and_runs_none() {
    let (rc, out) = run_env(
        &repo_with_plan(""),
        "rust",
        None,
        &[("AIB_KNIVES_LIST", "1")],
    );
    assert_eq!(rc, 0, "{out}");
    assert_eq!(ran(&out), Vec::<u32>::new(), "{out}");
    let listed: Vec<&str> = out
        .lines()
        .filter_map(|l| l.strip_prefix("KNIVES-ELIGIBLE "))
        .collect();
    // not k0 (another OS), not k9 (another job)
    assert_eq!(listed, vec![k(1), k(2), k(3), k(4), k(5)], "{out}");
}

#[test]
fn the_scripts_do_not_see_the_plan_or_the_list() {
    // knives-run-knives.sh runs this file's tests from inside a cell that has a plan
    let script = "#!/usr/bin/env bash\n# aib-knives: os=any\necho plan=${AIB_KNIVES_PLAN:-unset} list=${AIB_KNIVES_LIST:-unset}\n";
    let r = repo(&[
        ("crates/a/knives/k1.sh", script),
        ("plan.tsv", "1\tcrates/a/knives/k1.sh\n"),
    ]);
    let (rc, out) = run_env(&r, "rust", Some("1/1"), &[("AIB_KNIVES_PLAN", "plan.tsv")]);
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("plan=unset list=unset"), "{out}");
}

#[test]
fn the_plan_made_from_the_list_runs_every_script_once_over_the_cells() {
    // the two halves together, as the workflow step uses them: list ⇒ knife-shards plan ⇒ cells
    let (rc, out) = run_env(
        &repo_with_plan(""),
        "rust",
        None,
        &[("AIB_KNIVES_LIST", "1")],
    );
    assert_eq!(rc, 0, "{out}");
    let list: Vec<String> = out
        .lines()
        .filter_map(|l| l.strip_prefix("KNIVES-ELIGIBLE "))
        .map(str::to_string)
        .collect();
    // k2 and k4 are the heavy ones: name order would put both in cell 2 of 2
    let secs =
        crate::knife_shards::parse_secs(&format!("90\t{}\n80\t{}\n1\t{}\n", k(2), k(4), k(1)))
            .unwrap();
    let plan: String = crate::knife_shards::plan(&list, &secs, 2)
        .unwrap()
        .iter()
        .map(|(f, c)| format!("{c}\t{f}\n"))
        .collect();
    let env = [("AIB_KNIVES_PLAN", "plan.tsv")];
    let (rc1, out1) = run_env(&repo_with_plan(&plan), "rust", Some("1/2"), &env);
    let (rc2, out2) = run_env(&repo_with_plan(&plan), "rust", Some("2/2"), &env);
    assert_eq!((rc1, rc2), (0, 0), "{out1}\n{out2}");
    let (a, b) = (ran(&out1), ran(&out2));
    let mut all = [a.clone(), b.clone()].concat();
    all.sort_unstable();
    assert_eq!(all, vec![1, 2, 3, 4, 5], "{out1}\n{out2}");
    assert_ne!(
        a.contains(&2),
        a.contains(&4),
        "k2 and k4 apart: {a:?} {b:?}"
    );
}

#[test]
fn limits_file_overrides_default_timeout_handles_crlf_and_does_not_leak() {
    let r = repo(&[
        (
            "crates/a/knives/k1.sh",
            "#!/usr/bin/env bash\n# aib-knives: os=any\nsleep 30\n",
        ),
        (
            "crates/a/knives/k2.sh",
            "#!/usr/bin/env bash\n# aib-knives: os=any\n[ -z \"${AIB_KNIVES_LIMITS:-}\" ] && exit 0 || exit 9\n",
        ),
    ]);
    std::fs::write(r.join("limits.tsv"), "1\tcrates/a/knives/k1.sh\r\n").unwrap();
    let (rc, out) = run_env(&r, "rust", None, &[("AIB_KNIVES_LIMITS", "limits.tsv")]);
    assert_eq!(rc, 1, "{out}");
    assert!(
        out.contains("KNIVES-SCRIPT crates/a/knives/k1.sh: not_run why_not=timeout secs="),
        "{out}"
    );
    assert!(out.contains("limit=1m"), "{out}");
    assert!(
        out.contains("KNIVES-SCRIPT crates/a/knives/k2.sh: rc=0"),
        "{out}"
    );
}

#[test]
fn bad_or_unreadable_limits_file_is_refused_with_rc_2() {
    let r = repo(&[(
        "crates/a/knives/k1.sh",
        "#!/usr/bin/env bash\n# aib-knives: os=any\nexit 0\n",
    )]);
    let (rc, out) = run_env(&r, "rust", None, &[("AIB_KNIVES_LIMITS", "missing.tsv")]);
    assert_eq!(rc, 2, "{out}");

    for bad in [
        "0\tcrates/a/knives/k1.sh\n",
        "241\tcrates/a/knives/k1.sh\n",
        "999999999999999999999999\tcrates/a/knives/k1.sh\n",
        "not-a-limit\n",
    ] {
        let r = repo(&[(
            "crates/a/knives/k1.sh",
            "#!/usr/bin/env bash\n# aib-knives: os=any\nexit 0\n",
        )]);
        std::fs::write(r.join("limits.tsv"), bad).unwrap();
        let (rc, out) = run_env(&r, "rust", None, &[("AIB_KNIVES_LIMITS", "limits.tsv")]);
        assert_eq!(rc, 2, "bad={bad:?}\n{out}");
    }
}

#[test]
fn skip_files_skips_unaffected_scripts_and_does_not_leak() {
    let r = repo(&[
        (
            "crates/a/knives/k1.sh",
            "#!/usr/bin/env bash\n# aib-knives: os=any\nexit 9\n",
        ),
        (
            "crates/a/knives/k2.sh",
            "#!/usr/bin/env bash\n# aib-knives: os=any\n[ -z \"${AIB_SKIP_FILES:-}\" ] && exit 0 || exit 8\n",
        ),
    ]);
    let (rc, out) = run_env(
        &r,
        "rust",
        None,
        &[("AIB_SKIP_FILES", ",crates/a/knives/k1.sh,")],
    );
    assert_eq!(rc, 0, "{out}");
    assert!(
        out.contains("KNIVES-SCRIPT crates/a/knives/k1.sh: SKIPPED why=unaffected"),
        "{out}"
    );
    assert!(
        out.contains("KNIVES-SCRIPT crates/a/knives/k2.sh: rc=0"),
        "{out}"
    );
    assert!(out.contains("KNIVES-PARTIAL "), "{out}");
    assert!(
        !out.contains("KNIVES-OK"),
        "skip must never emit KNIVES-OK: {out}"
    );

    // When every script in a multi-shard run is skipped so ran=0, RK11 still fails (rc=1)
    let r2 = repo(&[
        (
            "crates/a/knives/k1.sh",
            "#!/usr/bin/env bash\n# aib-knives: os=any\nexit 9\n",
        ),
        (
            "crates/a/knives/k2.sh",
            "#!/usr/bin/env bash\n# aib-knives: os=any\nexit 9\n",
        ),
    ]);
    let (rc_all, out_all) = run_env(
        &r2,
        "rust",
        Some("1/2"),
        &[(
            "AIB_SKIP_FILES",
            ",crates/a/knives/k1.sh,crates/a/knives/k2.sh,",
        )],
    );
    assert_eq!(rc_all, 1, "{out_all}");
    assert!(out_all.contains("KNIVES-NONE"), "{out_all}");
}

/// E3: AIB_KNIVES_ONLY_OS=<os>[,<os>…] keeps the scripts whose header os is listed, so the Linux
/// cells (unix,any) and the macOS cells (macos) never take the same script; not set = as before.
#[test]
fn only_os_keeps_the_listed_oses_and_does_not_reach_the_scripts() {
    let files = [
        (
            "crates/a/knives/k-any.sh",
            "#!/usr/bin/env bash\n# aib-knives: os=any\necho only=${AIB_KNIVES_ONLY_OS:-unset}\n",
        ),
        (
            "crates/a/knives/k-unix.sh",
            "#!/usr/bin/env bash\n# aib-knives: os=unix\necho u\n",
        ),
        ("crates/a/knives/k-mac.sh", "#!/usr/bin/env bash\necho m\n"),
    ];
    let listed = |only: Option<&str>| -> Vec<String> {
        let mut env = vec![("AIB_KNIVES_LIST", "1")];
        if let Some(o) = only {
            env.push(("AIB_KNIVES_ONLY_OS", o));
        }
        let (rc, out) = run_env(&repo(&files), "rust", None, &env);
        assert_eq!(rc, 0, "{out}");
        out.lines()
            .filter_map(|l| l.strip_prefix("KNIVES-ELIGIBLE crates/a/knives/"))
            .map(str::to_string)
            .collect()
    };
    let mac_here: Vec<String> = if here_os() == "macos" {
        vec!["k-mac.sh".into()]
    } else {
        vec![]
    };
    let mut all = vec!["k-any.sh".to_string()];
    all.extend(mac_here.clone());
    all.push("k-unix.sh".into());
    assert_eq!(listed(None), all);
    assert_eq!(listed(Some("unix,any")), vec!["k-any.sh", "k-unix.sh"]);
    assert_eq!(listed(Some("any")), vec!["k-any.sh"]);
    assert_eq!(listed(Some("macos")), mac_here);
    // the cell's list is not passed on to the scripts it runs
    let (rc, out) = run_env(
        &repo(&files[..1]),
        "rust",
        None,
        &[("AIB_KNIVES_ONLY_OS", "any")],
    );
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("only=unset"), "{out}");
}
