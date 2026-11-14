// SPDX-License-Identifier: Apache-2.0
//! Real processes, short limits. Each test that stops something checks that a grandchild
//! in the job's group is gone afterwards.
#![allow(clippy::disallowed_methods)] // tests poll a pid file with a short sleep (ADR 0005 named exception)
#![cfg(unix)]

use aib_consent::supervise::{supervise, Job};
use nix::errno::Errno;
use nix::sys::signal::kill;
use nix::unistd::Pid;
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

fn scratch(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!(
        "aib-consent-test-{}-{}-{}",
        name,
        std::process::id(),
        Instant::now().elapsed().as_nanos()
    ));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).expect("scratch");
    d
}

fn job(dir: &Path, script: &str, ask: &[&str]) -> Job {
    Job {
        id: "t1".into(),
        argv: vec!["/bin/sh".into(), "-c".into(), script.into()],
        env: vec![("ONLY".into(), "this".into())],
        cwd: dir.to_path_buf(),
        log: dir.join("job.log"),
        state: dir.join("job.state.json"),
        stop_request: dir.join("job.stop"),
        ask: ask.iter().map(|s| (*s).to_owned()).collect(),
        quiet: Duration::from_secs(60),
        ask_after: Duration::from_secs(60),
        ask_every: Duration::from_secs(60),
        hard: Duration::from_secs(20),
        grace: Duration::from_millis(500),
        ask_limit: Duration::from_secs(60),
        tick: Duration::from_millis(50),
        script: None,
    }
}

fn gone(pid_file: &Path) -> bool {
    let deadline = Instant::now() + Duration::from_secs(3);
    let pid: i32 = loop {
        if let Ok(s) = std::fs::read_to_string(pid_file) {
            if let Ok(p) = s.trim().parse() {
                break p;
            }
        }
        assert!(Instant::now() < deadline, "no pid written");
        std::thread::sleep(Duration::from_millis(20));
    };
    matches!(kill(Pid::from_raw(pid), None), Err(Errno::ESRCH))
}

fn s<'a>(r: &'a Value, k: &str) -> &'a str {
    r[k].as_str().unwrap_or("")
}

const YES: &[&str] = &["/bin/sh", "-c", "exit 0"];
const NO: &[&str] = &["/bin/sh", "-c", "exit 1"];
const HANG: &[&str] = &["/bin/sh", "-c", "sleep 60"];

#[test]
fn a_job_that_ends_is_recorded_with_its_output() {
    let d = scratch("ends");
    let r = supervise(&job(&d, "echo one; echo two >&2; exit 3", NO));
    assert_eq!(s(&r, "result"), "exited");
    assert_eq!(r["rc"], 3);
    assert_eq!(r["asks"], 0);
    let log = std::fs::read_to_string(d.join("job.log")).expect("log");
    assert!(log.contains("one") && log.contains("two"), "{log}");
    let st: Value =
        serde_json::from_str(&std::fs::read_to_string(d.join("job.state.json")).expect("state"))
            .expect("json");
    assert_eq!(st["phase"], "done");
}

// knife: the inherited environment is cleared
#[test]
fn the_job_sees_only_the_environment_it_is_given() {
    let d = scratch("env");
    std::env::set_var("AIB_CONSENT_LEAK_CHECK", "leaked");
    let r = supervise(&job(&d, "/usr/bin/env", NO));
    assert_eq!(s(&r, "result"), "exited");
    let log = std::fs::read_to_string(d.join("job.log")).expect("log");
    assert!(log.contains("ONLY=this"), "{log}");
    assert!(!log.contains("AIB_CONSENT_LEAK_CHECK"), "{log}");
    assert!(!log.contains("HOME="), "{log}");
}

// knife: a stop signals the whole group, not just the leader
#[test]
fn a_yes_to_the_quiet_question_stops_the_whole_group() {
    let d = scratch("quiet");
    let pidf = d.join("grandchild.pid");
    let mut j = job(
        &d,
        &format!("sleep 30 & echo $! > '{}'; wait", pidf.display()),
        YES,
    );
    j.quiet = Duration::from_millis(300);
    let t = Instant::now();
    let r = supervise(&j);
    assert_eq!(s(&r, "result"), "stopped", "{r}");
    assert_eq!(s(&r, "stopped_by"), "person");
    assert_eq!(s(&r, "remote"), "not_confirmed");
    assert!(t.elapsed() < Duration::from_secs(10));
    assert!(gone(&pidf), "the grandchild is still running");
}

// knife: "keep waiting" is not a stop
#[test]
fn a_no_keeps_the_job_running() {
    let d = scratch("no");
    let mut j = job(&d, "sleep 2; echo done", NO);
    j.quiet = Duration::from_millis(300);
    let r = supervise(&j);
    assert_eq!(s(&r, "result"), "exited", "{r}");
    assert_eq!(r["rc"], 0);
    assert!(r["asks"].as_u64().unwrap_or(0) >= 1, "{r}");
    let log = std::fs::read_to_string(d.join("job.log")).expect("log");
    assert!(log.contains("done"));
}

// knife: the hard limit does not wait for an open question
#[test]
fn the_hard_limit_holds_while_a_question_is_open() {
    let d = scratch("hard");
    let pidf = d.join("grandchild.pid");
    let mut j = job(
        &d,
        &format!("sleep 30 & echo $! > '{}'; wait", pidf.display()),
        HANG,
    );
    j.quiet = Duration::from_millis(200);
    j.hard = Duration::from_secs(2);
    let t = Instant::now();
    let r = supervise(&j);
    assert_eq!(s(&r, "result"), "hard_timeout", "{r}");
    assert!(t.elapsed() < Duration::from_secs(8));
    assert!(gone(&pidf));
}

// knife: a seat's stop request leads to the question (and only then to a stop)
#[test]
fn a_stop_request_asks_the_person() {
    let d = scratch("request");
    let mut j = job(&d, "sleep 30", YES);
    std::fs::write(&j.stop_request, b"").expect("request");
    j.quiet = Duration::from_secs(60);
    let r = supervise(&j);
    assert_eq!(s(&r, "result"), "stopped", "{r}");
    assert!(!j.stop_request.exists());

    let d = scratch("request-no");
    let mut j = job(&d, "sleep 1; echo fine", NO);
    std::fs::write(&j.stop_request, b"").expect("request");
    j.quiet = Duration::from_secs(60);
    let r = supervise(&j);
    assert_eq!(s(&r, "result"), "exited", "{r}");
}

// knife: members left behind by a leader that ended are stopped too
#[test]
fn what_the_leader_leaves_behind_is_stopped() {
    let d = scratch("leftovers");
    let pidf = d.join("grandchild.pid");
    let r = supervise(&job(
        &d,
        &format!(
            "sleep 30 >/dev/null 2>&1 & echo $! > '{}'; exit 0",
            pidf.display()
        ),
        NO,
    ));
    assert_eq!(s(&r, "result"), "exited", "{r}");
    assert_eq!(s(&r, "stopped_by"), "leftovers");
    assert!(gone(&pidf));
}

// knife: KILL follows when TERM is ignored
#[test]
fn kill_follows_an_ignored_term() {
    let d = scratch("ignore");
    let mut j = job(&d, "trap '' TERM; sleep 30", YES);
    j.quiet = Duration::from_millis(300);
    let t = Instant::now();
    let r = supervise(&j);
    assert_eq!(s(&r, "result"), "stopped", "{r}");
    assert_eq!(r["kill_needed"], true);
    // TERM is ignored by the shell and by sleep (an ignored signal stays ignored across exec):
    // only the KILL ends it this soon
    assert!(t.elapsed() < Duration::from_secs(10), "{r}");
}

// output without a line end still counts as the job being alive
#[test]
fn bytes_without_a_line_end_are_not_quiet() {
    let d = scratch("bytes");
    let mut j = job(
        &d,
        "i=0; while [ $i -lt 15 ]; do printf x; sleep 0.1; i=$((i+1)); done",
        YES,
    );
    j.quiet = Duration::from_millis(600);
    let r = supervise(&j);
    assert_eq!(s(&r, "result"), "exited", "{r}");
    assert_eq!(r["asks"], 0, "{r}");
}

// knife: after "keep waiting", a seat's requests are dropped for a while, not asked again
#[test]
fn requests_right_after_keep_waiting_are_not_asked() {
    let d = scratch("cooldown");
    let mut j = job(&d, "sleep 2; echo fine", NO);
    std::fs::write(&j.stop_request, b"").expect("request");
    j.quiet = Duration::from_secs(60);
    let req = j.stop_request.clone();
    let poker = std::thread::spawn(move || {
        let until = Instant::now() + Duration::from_millis(1500);
        while Instant::now() < until {
            let _ = std::fs::write(&req, b"");
            std::thread::sleep(Duration::from_millis(100));
        }
    });
    let r = supervise(&j);
    let _ = poker.join();
    assert_eq!(s(&r, "result"), "exited", "{r}");
    assert_eq!(r["asks"], 1, "{r}");
    assert!(r["requests_ignored"].as_u64().unwrap_or(0) >= 1, "{r}");
}

// knife: a pinned program runs from the checked bytes, not from a path that can change
#[test]
fn the_checked_script_runs_from_a_pipe() {
    let d = scratch("script");
    let mut j = job(&d, "unused", NO);
    j.argv = vec!["/bin/sh".into(), aib_consent::supervise::SCRIPT_ARG.into()];
    j.script = Some(b"echo from-the-bytes; echo \"$0\"\n".to_vec());
    let r = supervise(&j);
    assert_eq!(s(&r, "result"), "exited", "{r}");
    assert_eq!(r["rc"], 0, "{r}");
    let log = std::fs::read_to_string(d.join("job.log")).expect("log");
    assert!(log.contains("from-the-bytes"), "{log}");
    assert!(log.contains("/dev/fd/"), "{log}");
    // without the bytes the placeholder is never run as a file name
    let mut j = job(&d, "unused", NO);
    j.argv = vec!["/bin/sh".into(), aib_consent::supervise::SCRIPT_ARG.into()];
    let r = supervise(&j);
    assert_eq!(s(&r, "why"), "script_missing", "{r}");
}

// a script far larger than a pipe's buffer (2240e #661 r2): written by its own thread, never stuck
#[test]
fn a_script_larger_than_the_pipe_buffer_runs_to_its_end() {
    let d = scratch("big-script");
    let mut j = job(&d, "unused", NO);
    j.argv = vec![
        "/bin/bash".into(),
        aib_consent::supervise::SCRIPT_ARG.into(),
    ];
    let mut body = Vec::new();
    for i in 0..4000 {
        body.extend_from_slice(
            format!("# filler line {i:05} ......................................\n").as_bytes(),
        );
    }
    assert!(body.len() > 200 * 1024);
    body.extend_from_slice(b"echo the-last-line\n");
    j.script = Some(body);
    let t = Instant::now();
    let r = supervise(&j);
    assert_eq!(s(&r, "result"), "exited", "{r}");
    assert_eq!(r["rc"], 0, "{r}");
    assert!(t.elapsed() < Duration::from_secs(10), "{r}");
    let log = std::fs::read_to_string(d.join("job.log")).expect("log");
    assert!(log.contains("the-last-line"), "{log}");
}
