//! The Job Object against the real kernel (Windows only).
//!
//! Helper processes are this test binary run again with `helper --exact`
//! and `AIB_SYS_HELPER=<mode>`; every other test then does nothing in them.
#![cfg(windows)]
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_sys::job::{Job, Process, WaitMode, BREAKAWAY_FLAGS, LIMIT_FLAGS};
use aib_sys::SysError;
use std::io::{BufRead, BufReader, Write};
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Duration, Instant};

const MODE: &str = "AIB_SYS_HELPER";
const MARKER: &str = "AIB_SYS_MARKER";
/// `CREATE_BREAKAWAY_FROM_JOB`.
const BREAKAWAY: u32 = 0x0100_0000;

fn helper_cmd(mode: &str) -> Command {
    let mut c = Command::new(std::env::current_exe().unwrap());
    c.args(["helper", "--exact", "--nocapture", "--test-threads", "1"])
        .env(MODE, mode)
        .stdin(Stdio::null())
        .stderr(Stdio::null());
    c
}

fn say(line: &str) {
    let mut o = std::io::stdout().lock();
    writeln!(o, "{line}").unwrap();
    o.flush().unwrap();
}

/// The helper entry point. A no-op in a normal test run.
#[test]
fn helper() {
    let Ok(mode) = std::env::var(MODE) else {
        return;
    };
    match mode.as_str() {
        "sleep" => std::thread::sleep(Duration::from_secs(120)),
        // middle layer: start a grandchild, report it, exit first
        "tree" => {
            // left running on purpose: it is the grandchild under test
            #[allow(clippy::zombie_processes)]
            let g = helper_cmd("sleep").stdout(Stdio::null()).spawn().unwrap();
            if let Ok(m) = std::env::var(MARKER) {
                std::fs::write(m, g.id().to_string()).unwrap();
            }
            say(&format!("GPID {}", g.id()));
        }
        "breakaway" => {
            let r = helper_cmd("sleep")
                .stdout(Stdio::null())
                .creation_flags(BREAKAWAY)
                .spawn();
            match r {
                Ok(mut g) => {
                    say(&format!("BREAKAWAY ok {}", g.id()));
                    let _ = g.kill();
                }
                Err(e) => say(&format!("BREAKAWAY denied {:?}", e.raw_os_error())),
            }
            std::thread::sleep(Duration::from_secs(120));
        }
        // stands in for aibd: own a Job, start a tree in it, then wait to be
        // killed (the crash)
        "owner" => {
            let job = Job::new().unwrap();
            let mut t = job
                .spawn(helper_cmd("tree").stdout(Stdio::piped()))
                .unwrap();
            let g = read_tagged(&mut t, "GPID ");
            say(&format!("GPID {g}"));
            std::thread::sleep(Duration::from_secs(120));
            drop(job);
        }
        m => panic!("unknown helper mode {m}"),
    }
}

/// First stdout line starting with `tag`, rest of the line.
fn read_tagged(c: &mut Child, tag: &str) -> String {
    let out = c.stdout.take().unwrap();
    for line in BufReader::new(out).lines() {
        let line = line.unwrap();
        if let Some(rest) = line.strip_prefix(tag) {
            return rest.trim().to_owned();
        }
    }
    panic!("helper ended without a `{tag}` line");
}

fn marker_path() -> PathBuf {
    static N: AtomicUsize = AtomicUsize::new(0);
    std::env::temp_dir().join(format!(
        "aib-sys-job-{}-{}",
        std::process::id(),
        N.fetch_add(1, Ordering::Relaxed)
    ))
}

/// Barrier for `spawn_with`: wait until the child wrote `marker` (it can
/// only do that if it is running before the assign), at most `max`.
/// Returns whether it did.
fn wait_for(marker: &Path, max: Duration) -> bool {
    let end = Instant::now() + max;
    while Instant::now() < end {
        if marker.exists() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    false
}

#[test]
fn the_job_kills_on_close_and_allows_no_breakaway() {
    let job = Job::new().unwrap();
    let f = job.limit_flags().unwrap();
    assert_eq!(f & LIMIT_FLAGS, LIMIT_FLAGS, "flags {f:#x}");
    assert_eq!(f & BREAKAWAY_FLAGS, 0, "flags {f:#x}");
    assert_eq!(job.wait_mode(), WaitMode::CompletionPort);
    assert_eq!(job.active_processes().unwrap(), 0);
    assert!(job.members().unwrap().is_empty());
    assert!(job.wait_empty(Duration::from_millis(10)).unwrap());
}

/// The must-hit path (DECISION-8 item 2): the middle layer starts a
/// grandchild and exits first; the grandchild is still in the Job, and
/// terminate + wait_empty leave nothing behind. The barrier gives a child
/// that is *not* suspended every chance to start its grandchild outside the
/// Job, so dropping `CREATE_SUSPENDED` makes this fail every time.
#[test]
fn a_grandchild_whose_parent_exited_is_still_in_the_job_and_dies() {
    let job = Job::new().unwrap();
    let marker = marker_path();
    let mut cmd = helper_cmd("tree");
    cmd.stdout(Stdio::piped()).env(MARKER, &marker);
    let mut ran_early = false;
    let r = job.spawn_with(&mut cmd, || {
        ran_early = wait_for(&marker, Duration::from_secs(3))
    });
    assert!(
        !ran_early,
        "the child ran before it was assigned (CREATE_SUSPENDED missing?)"
    );
    let mut mid = r.expect("spawn into the job");
    let gpid: u32 = read_tagged(&mut mid, "GPID ").parse().unwrap();
    let g = Process::open(gpid).unwrap();
    assert!(mid.wait().unwrap().success(), "middle layer exits first");
    let _ = std::fs::remove_file(&marker);

    let members = job.members().unwrap();
    assert!(
        members.iter().any(|m| m.pid == gpid),
        "grandchild not in the job: {members:?}"
    );
    assert!(
        !members.iter().any(|m| m.pid == mid.id()),
        "exited middle still listed"
    );
    let me = members.iter().find(|m| m.pid == gpid).unwrap();
    assert_eq!(
        me.created_100ns,
        g.created_100ns().unwrap(),
        "identity = pid + creation time"
    );
    assert!(job.active_processes().unwrap() >= 1);

    job.terminate(1).unwrap();
    assert!(
        job.wait_empty(Duration::from_secs(10)).unwrap(),
        "job not empty after terminate"
    );
    assert!(job.members().unwrap().is_empty());
    assert!(
        g.wait(Duration::from_secs(5)).unwrap(),
        "grandchild survived terminate"
    );
}

/// DECISION-11 B: the owner dying (aibd crash) ends the whole tree.
#[test]
fn the_owner_crashing_kills_the_tree() {
    let mut owner = helper_cmd("owner").stdout(Stdio::piped()).spawn().unwrap();
    let gpid: u32 = read_tagged(&mut owner, "GPID ").parse().unwrap();
    let g = Process::open(gpid).unwrap();
    assert!(
        !g.wait(Duration::from_millis(200)).unwrap(),
        "grandchild should be running"
    );
    owner.kill().unwrap(); // TerminateProcess: no destructor runs
    owner.wait().unwrap();
    assert!(
        g.wait(Duration::from_secs(10)).unwrap(),
        "tree survived its owner (KILL_ON_JOB_CLOSE missing?)"
    );
}

#[test]
fn dropping_the_job_kills_the_tree() {
    let job = Job::new().unwrap();
    let mut mid = job
        .spawn(helper_cmd("tree").stdout(Stdio::piped()))
        .unwrap();
    let gpid: u32 = read_tagged(&mut mid, "GPID ").parse().unwrap();
    let g = Process::open(gpid).unwrap();
    mid.wait().unwrap();
    drop(job);
    assert!(
        g.wait(Duration::from_secs(10)).unwrap(),
        "tree survived the job handle"
    );
}

#[test]
fn a_member_cannot_break_away() {
    let job = Job::new().unwrap();
    let mut c = job
        .spawn(helper_cmd("breakaway").stdout(Stdio::piped()))
        .unwrap();
    let r = read_tagged(&mut c, "BREAKAWAY ");
    assert!(r.starts_with("denied"), "breakaway was allowed: {r}");
    job.terminate(1).unwrap();
    assert!(job.wait_empty(Duration::from_secs(10)).unwrap());
    c.wait().unwrap();
}

#[test]
fn ctrl_break_only_reaches_members() {
    let job = Job::new().unwrap();
    assert_eq!(job.ctrl_break(0), Err(SysError::BadPid));
    assert_eq!(job.ctrl_break(std::process::id()), Err(SysError::BadPid));
}

#[test]
fn wait_empty_times_out_while_a_member_runs() {
    let job = Job::new().unwrap();
    let mut c = job
        .spawn(helper_cmd("sleep").stdout(Stdio::null()))
        .unwrap();
    let t = Instant::now();
    assert!(!job.wait_empty(Duration::from_millis(300)).unwrap());
    assert!(
        t.elapsed() >= Duration::from_millis(250),
        "returned early: {:?}",
        t.elapsed()
    );
    job.terminate(1).unwrap();
    assert!(job.wait_empty(Duration::from_secs(10)).unwrap());
    c.wait().unwrap();
}

#[test]
fn a_reaped_child_is_gone() {
    let mut c = helper_cmd("none-such")
        .stdout(Stdio::null())
        .spawn()
        .unwrap();
    let pid = c.id();
    c.wait().unwrap();
    // pid reuse within this window is possible but rare; only Gone or a
    // different process (Ok) are acceptable, never PermissionDenied/Unknown
    match Process::open(pid) {
        Err(SysError::Gone) | Ok(_) => {}
        Err(e) => panic!("reaped child classified as {e:?}"),
    }
}
