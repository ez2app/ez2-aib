//! The Job Object against the real kernel (Windows only).
//!
//! Helper processes are this test binary run again with `helper --exact`
//! and `AIB_SYS_HELPER=<mode>`; every other test then does nothing in them.
#![cfg(windows)]
#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

use aib_sys::job::{Job, Member, Process, WaitMode, BREAKAWAY_FLAGS, LIMIT_FLAGS};
use aib_sys::SysError;
use std::io::Write;
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Duration, Instant};

const MODE: &str = "AIB_SYS_HELPER";
const MARKER: &str = "AIB_SYS_MARKER";
const OUT: &str = "AIB_SYS_OUT";
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

/// Report one line to the test through the file named by `AIB_SYS_OUT`
/// (written to a temp name, then renamed, so the reader never sees half a
/// line). Not stdout: the test harness and inherited pipe handles on
/// Windows make a pipe unreliable for this (CI run 42435816408).
fn say(line: &str) {
    let out = PathBuf::from(std::env::var(OUT).unwrap());
    let tmp = out.with_extension("tmp");
    let mut f = std::fs::File::create(&tmp).unwrap();
    writeln!(f, "{line}").unwrap();
    f.sync_all().unwrap();
    drop(f);
    std::fs::rename(&tmp, &out).unwrap();
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
            let (mut cmd, out) = reporting("tree");
            let mut t = job.spawn(&mut cmd).unwrap();
            let g = read_tagged(&mut t, &out, "GPID ");
            say(&format!("GPID {g}"));
            std::thread::sleep(Duration::from_secs(120));
            drop(job);
        }
        m => panic!("unknown helper mode {m}"),
    }
}

/// A helper command whose report goes to a fresh file; returns both.
fn reporting(mode: &str) -> (Command, PathBuf) {
    let out = marker_path();
    let mut c = helper_cmd(mode);
    c.stdout(Stdio::null()).env(OUT, &out);
    (c, out)
}

/// The helper's report line starting with `tag` (rest of the line), waiting
/// at most 30 s. Fails early if the helper exited without reporting.
fn read_tagged(c: &mut Child, out: &Path, tag: &str) -> String {
    let end = Instant::now() + Duration::from_secs(30);
    while Instant::now() < end {
        if let Ok(s) = std::fs::read_to_string(out) {
            let _ = std::fs::remove_file(out);
            return s
                .lines()
                .find_map(|l| l.strip_prefix(tag))
                .unwrap_or_else(|| panic!("report without `{tag}`: {s:?}"))
                .trim()
                .to_owned();
        }
        if let Ok(Some(st)) = c.try_wait() {
            if !out.exists() {
                panic!("helper exited ({st}) without a `{tag}` report");
            }
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    panic!("no `{tag}` report within 30 s");
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
    let (mut cmd, out) = reporting("tree");
    cmd.env(MARKER, &marker);
    let mut ran_early = false;
    let r = job.spawn_with(&mut cmd, || {
        ran_early = wait_for(&marker, Duration::from_secs(3))
    });
    assert!(
        !ran_early,
        "the child ran before it was assigned (CREATE_SUSPENDED missing?)"
    );
    let mut mid = r.expect("spawn into the job");
    let gpid: u32 = read_tagged(&mut mid, &out, "GPID ").parse().unwrap();
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
    let (mut cmd, out) = reporting("owner");
    let mut owner = cmd.spawn().unwrap();
    let gpid: u32 = read_tagged(&mut owner, &out, "GPID ").parse().unwrap();
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
    let (mut cmd, out) = reporting("tree");
    let mut mid = job.spawn(&mut cmd).unwrap();
    let gpid: u32 = read_tagged(&mut mid, &out, "GPID ").parse().unwrap();
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
    let (mut cmd, out) = reporting("breakaway");
    let mut c = job.spawn(&mut cmd).unwrap();
    let r = read_tagged(&mut c, &out, "BREAKAWAY ");
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

/// ADR 0008: the member is recorded while the child is still suspended (it
/// cannot have written its marker yet), with the identity the kernel reports.
#[test]
fn the_member_is_recorded_before_the_child_runs() {
    let job = Job::new().unwrap();
    let marker = marker_path();
    let (mut cmd, out) = reporting("tree");
    cmd.env(MARKER, &marker);
    let mut got: Option<Member> = None;
    let mut ran_before_record = true;
    let mut mid = job
        .spawn_recorded(&mut cmd, |m| {
            // a running child writes the marker at once; give it the chance
            ran_before_record = wait_for(&marker, Duration::from_secs(2));
            got = Some(m);
            Ok(())
        })
        .unwrap();
    assert!(
        !ran_before_record,
        "the child ran before its member was recorded"
    );
    let m = got.expect("record was called");
    assert_eq!(m.pid, mid.id());
    let gpid: u32 = read_tagged(&mut mid, &out, "GPID ").parse().unwrap();
    assert!(
        wait_for(&marker, Duration::from_secs(10)),
        "child never ran"
    );
    let _ = std::fs::remove_file(&marker);
    let g = Process::open(gpid).unwrap();
    job.terminate(1).unwrap();
    assert!(job.wait_empty(Duration::from_secs(10)).unwrap());
    assert!(g.wait(Duration::from_secs(5)).unwrap());
    mid.wait().unwrap();
}

/// Reconciliation path: open with terminate rights, check identity, then
/// terminate through the same handle.
#[test]
fn a_terminable_handle_stops_its_process() {
    let mut c = helper_cmd("sleep").stdout(Stdio::null()).spawn().unwrap();
    let p = Process::open_terminable(c.id()).unwrap();
    assert!(p.created_100ns().unwrap() > 0);
    assert!(!p.wait(Duration::from_millis(100)).unwrap());
    p.terminate(1).unwrap();
    assert!(p.wait(Duration::from_secs(5)).unwrap());
    c.wait().unwrap();
    // a plain handle has no terminate right
    let mut d = helper_cmd("sleep").stdout(Stdio::null()).spawn().unwrap();
    let q = Process::open(d.id()).unwrap();
    assert_eq!(q.terminate(1), Err(SysError::PermissionDenied));
    d.kill().unwrap();
    d.wait().unwrap();
}
