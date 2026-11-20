//! Slice 3b-2b (DESIGN-g1-receipt-origacct-b4297-1120 r3 §3 §4 §7): serving a request. This
//! is the part that starts programs and writes files; what it starts is [`crate::plan`]'s
//! data and what it says is [`crate::run`]'s words.
//!
//! - `run` checks the machine (the pinned settings, receipt-run's digest, the toolchain,
//!   sandbox-exec, free disk), receives the bundle from stdin, starts the supervisor (this
//!   same program with `--supervise`, in its own process group, nothing inherited) and
//!   answers at once. The same run id again answers how that run stands; it is never
//!   started twice.
//! - The supervisor takes the machine's lease like any receipt, brings the tree, judges it
//!   before anything of it runs, fetches crates with the pinned cargo, runs receipt-run whole
//!   inside the sandbox, keeps the `.out` ([`crate::run::keep_out`]) and removes the run
//!   folder. Whatever step fails, the reason is written to the run's note and the run ends.
//! - `cancel` stops the supervisor's process group (the pid file is beside the run folder,
//!   where the tested code cannot write) and cleans up as the supervisor would have.
//!
//! macOS only: the profile is `sandbox-exec`'s. On another system `run` answers
//! `no_sandbox` (design r3 §8: Windows results are GitHub CI's).

use crate::plan::{self, Config, Repo};
use crate::run::{self, Layout, State};
use crate::sandbox::{self, Places, Step};
use crate::Request;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// The pinned settings, under the home.
pub const CONF: &str = ".ez2-aib/receipt-gate.conf";
/// The base bare repository, under the runs.
pub const BASE: &str = "web-base.git";
/// A run is not started with less free disk than this (KiB): a cold build is about 20 GB.
pub const MIN_FREE_KB: u64 = 30 * 1024 * 1024;
/// receipt-run is stopped after this long.
pub const RUN_SECS: u64 = 3 * 3600;
/// The lease is given up after this long in the queue.
pub const QUEUE_SECS: u64 = 8 * 3600;
const SANDBOX_EXEC: &str = "/usr/bin/sandbox-exec";

fn command(s: &Step) -> Command {
    let mut c = Command::new(&s.argv[0]);
    c.args(&s.argv[1..])
        .env_clear()
        .envs(s.env.iter().map(|(k, v)| (k.as_str(), v.as_str())))
        .current_dir(&s.cwd)
        .stdin(Stdio::null());
    c
}

fn ok(s: &Step) -> bool {
    command(s)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|x| x.success())
}

fn printed(s: &Step) -> Option<Vec<u8>> {
    let o = command(s).stderr(Stdio::null()).output().ok()?;
    o.status.success().then_some(o.stdout)
}

fn quiet(program: &str, args: &[&str]) -> bool {
    Command::new(program)
        .args(args)
        .env_clear()
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|x| x.success())
}

/// Whether `pid` is a live process of this account.
pub fn alive(pid: u32) -> bool {
    quiet("/bin/kill", &["-0", &pid.to_string()])
}

fn sha256_of(p: &Path) -> Option<String> {
    use sha2::{Digest, Sha256};
    let bytes = std::fs::read(p).ok()?;
    Some(
        Sha256::digest(&bytes)
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect(),
    )
}

/// The free KiB `df -k` prints for the file system of `dir` (the fourth field of its last
/// line).
pub fn free_kb_of(df: &str) -> Option<u64> {
    df.lines()
        .last()?
        .split_whitespace()
        .nth(3)?
        .parse::<u64>()
        .ok()
}

fn free_kb(dir: &Path) -> Option<u64> {
    let o = Command::new("/bin/df")
        .arg("-k")
        .arg(dir)
        .env_clear()
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    free_kb_of(std::str::from_utf8(&o.stdout).ok()?)
}

#[allow(clippy::disallowed_methods)] // not aibd: the gate's supervisor is its own process on a test machine (ADR 0005 is aibd's timers)
fn wait_secs(secs: u64) {
    std::thread::sleep(std::time::Duration::from_secs(secs));
}

/// What one run is made of on this machine, as words for [`plan`] and [`sandbox`].
struct Site {
    home: String,
    layout: Layout,
    cfg: Config,
    toolchain: String,
    base: String,
}

fn word(p: &Path) -> Result<String, &'static str> {
    p.to_str()
        .filter(|s| sandbox::place_ok(s))
        .map(str::to_owned)
        .ok_or("bad_place")
}

/// Reads and checks the machine: the home is a plain path, the settings are whole,
/// receipt-run is the pinned one, the toolchain and the sandbox are there.
fn site(home: &Path) -> Result<Site, &'static str> {
    let home_s = word(home)?;
    let text = std::fs::read_to_string(home.join(CONF)).map_err(|_| "config_bad")?;
    let cfg = plan::config(&text)?;
    if sha256_of(Path::new(&cfg.receipt_run)).as_deref() != Some(cfg.receipt_run_sha256.as_str()) {
        return Err("receipt_tool_changed");
    }
    let toolchain = format!("{home_s}/.rustup/toolchains/{}", cfg.toolchain);
    if !Path::new(&toolchain).join("bin").join("cargo").is_file() {
        return Err("no_toolchain");
    }
    if !cfg!(target_os = "macos") || !Path::new(SANDBOX_EXEC).is_file() {
        return Err("no_sandbox");
    }
    let layout = Layout::new(home);
    let base = word(&home.join("wt.noindex").join(BASE))?;
    Ok(Site {
        home: home_s,
        layout,
        cfg,
        toolchain,
        base,
    })
}

fn gh_of() -> Option<String> {
    ["/opt/homebrew/bin/gh", "/usr/local/bin/gh"]
        .iter()
        .find(|p| Path::new(p).is_file())
        .map(|p| (*p).to_owned())
}

/// Serves one request; what is returned goes to stdout as it is. `me` is this program's own
/// path (the supervisor is started from it).
pub fn serve(req: &Request, home: &Path, me: &Path, stdin: &mut dyn Read) -> Vec<u8> {
    let layout = Layout::new(home);
    match req {
        Request::Status { run_id } => {
            run::status_text(&run::state(&layout, run_id, &alive)).into_bytes()
        }
        Request::Fetch { run_id } => {
            let s = run::state(&layout, run_id, &alive);
            run::fetch_bytes(&layout, run_id, &s)
        }
        Request::Cancel { run_id } => cancel(home, run_id).into_bytes(),
        Request::Run {
            run_id,
            head,
            krate,
            bundle_len,
            bundle_sha256,
            knives_len,
            ..
        } => {
            let got = start(
                home,
                me,
                run_id,
                head,
                krate,
                *bundle_len,
                bundle_sha256,
                *knives_len,
                stdin,
            );
            match got {
                Ok(text) => text.into_bytes(),
                Err(code) => format!("ERR {code}\n").into_bytes(),
            }
        }
    }
}

#[allow(clippy::too_many_arguments)] // the fields of one request, already checked by parse
fn start(
    home: &Path,
    me: &Path,
    run_id: &str,
    head: &str,
    krate: &str,
    bundle_len: u64,
    bundle_sha256: &str,
    knives_len: u64,
    stdin: &mut dyn Read,
) -> Result<String, &'static str> {
    let layout = Layout::new(home);
    // asked again (a retry after a timeout): how it stands, never a second start
    let now = run::state(&layout, run_id, &alive);
    if now != State::None {
        return Ok(run::status_text(&now).replacen("OK status", "OK run", 1));
    }
    // first version: the whole workspace and no knife table (knives are CI's)
    if krate != "ws" || knives_len != 0 {
        return Err("only_ws_no_knives");
    }
    let site = site(home)?;
    if !free_kb(home).is_some_and(|kb| kb >= MIN_FREE_KB) {
        return Err("disk_low");
    }
    run::note(&layout, run_id, run::STEPS[0])?;
    if let Err(code) = crate::recv::receive(
        stdin,
        bundle_len,
        bundle_sha256,
        &site.layout.bundle(run_id),
    ) {
        let _ = run::refuse(&layout, run_id, code);
        return Err(code);
    }
    let mut sup = Command::new(me);
    sup.args(["--supervise", run_id, head])
        .env_clear()
        .env("HOME", home)
        .current_dir(home)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        sup.process_group(0);
    }
    let Ok(child) = sup.spawn() else {
        let _ = run::refuse(&layout, run_id, "not_started");
        return Err("not_started");
    };
    if std::fs::write(layout.pid(run_id), format!("{}\n", child.id())).is_err() {
        let _ = quiet("/bin/kill", &["-KILL", &child.id().to_string()]);
        let _ = run::refuse(&layout, run_id, "not_started");
        return Err("not_started");
    }
    Ok(format!("OK run\nSTARTED {run_id}\n"))
}

fn remove(p: &Path) {
    let is_dir = std::fs::symlink_metadata(p).is_ok_and(|m| m.is_dir());
    let _ = if is_dir {
        std::fs::remove_dir_all(p)
    } else {
        std::fs::remove_file(p)
    };
}

/// Removes what a run leaves: its folder (tree, target, temporary files), the bundle, the
/// profile, the empty folder and the pid file. The note and the kept `.out` stay.
fn clean(l: &Layout, run_id: &str) {
    for p in [
        l.work(run_id),
        l.bundle(run_id),
        l.profile(run_id),
        l.empty(run_id),
        l.pid(run_id),
    ] {
        remove(&p);
    }
}

fn cancel(home: &Path, run_id: &str) -> String {
    let layout = Layout::new(home);
    match run::state(&layout, run_id, &alive) {
        State::None => return "ERR no_such_run\n".to_owned(),
        State::Done | State::Refused(_) => return "OK cancel\nstate ended\n".to_owned(),
        State::UnderWay(_) | State::Lost => {}
    }
    // the supervisor with what it started outside the sandbox is one process group,
    // receipt-run with everything inside the sandbox another
    let groups: Vec<String> = run::pids(&layout, run_id)
        .iter()
        .map(|p| format!("-{p}"))
        .collect();
    for g in &groups {
        let _ = quiet("/bin/kill", &["-TERM", "--", g]);
    }
    if !groups.is_empty() {
        wait_secs(2);
    }
    for g in &groups {
        let _ = quiet("/bin/kill", &["-KILL", "--", g]);
    }
    let _ = run::refuse(&layout, run_id, "withdrawn");
    if let Ok(site) = site(home) {
        let [_, _, release] = plan::lease_steps(&site.cfg, &site.home, run_id, 0);
        let mut off = release.clone();
        off.argv = vec![
            site.cfg.aib.clone(),
            "lease".into(),
            "queue".into(),
            "remove".into(),
            "--run-id".into(),
            run_id.to_owned(),
        ];
        let _ = ok(&off);
        let _ = ok(&release);
    }
    clean(&layout, run_id);
    "OK cancel\nstate withdrawn\n".to_owned()
}

/// The supervisor of one run (`--supervise <run id> <head>`): every step in order; the
/// first that fails writes its reason to the note. Always cleans up and releases the lease.
pub fn supervise(home: &Path, run_id: &str, head: &str) {
    let layout = Layout::new(home);
    let site = match site(home) {
        Ok(s) => s,
        Err(code) => {
            let _ = run::refuse(&layout, run_id, code);
            clean(&layout, run_id);
            return;
        }
    };
    let [add, acquire, release] =
        plan::lease_steps(&site.cfg, &site.home, run_id, std::process::id());
    let leased = queue(&layout, run_id, &add, &acquire);
    let ended = if leased {
        steps(&site, run_id, head)
    } else {
        Err("lost")
    };
    if let Err(code) = ended {
        let _ = run::refuse(&layout, run_id, code);
    }
    clean(&layout, run_id);
    let _ = ok(&release);
}

/// Waits for the machine: registers, asks every two minutes, registers again every half
/// hour (a registration that expired drops the run from the queue without a word).
fn queue(l: &Layout, run_id: &str, add: &Step, acquire: &Step) -> bool {
    let _ = run::note(l, run_id, run::STEPS[1]);
    let mut waited = 0;
    loop {
        if waited % 1800 == 0 {
            let _ = ok(add);
        }
        if ok(acquire) {
            return true;
        }
        if waited >= QUEUE_SECS {
            return false;
        }
        wait_secs(120);
        waited += 120;
    }
}

fn steps(site: &Site, run_id: &str, head: &str) -> Result<(), &'static str> {
    let l = &site.layout;
    let work = word(&l.work(run_id))?;
    let tree = word(&l.tree(run_id))?;
    let bundle = word(&l.bundle(run_id))?;
    let empty = word(&l.empty(run_id))?;
    let profile_file = word(&l.profile(run_id))?;
    let gh = gh_of();
    let places = Places {
        home: &site.home,
        work: &work,
        toolchain: &site.toolchain,
        gh: gh.as_deref(),
    };
    let repo = Repo {
        base: &site.base,
        tree: &tree,
        bundle: &bundle,
        empty: &empty,
    };
    let profile = sandbox::profile(&places)?;
    let _ = run::note(l, run_id, run::STEPS[2]);
    std::fs::create_dir(&empty).map_err(|_| "cannot_write")?;
    std::fs::create_dir(&work).map_err(|_| "cannot_write")?;
    // the base is rebuilt when it is not as the gate made it (the last run could not write
    // there, but a base nobody checked is not used)
    if !crate::recv::base_clean(Path::new(&site.base)) {
        remove(Path::new(&site.base));
        if !plan::base_init(&repo).iter().all(ok) {
            return Err("base_unavailable");
        }
    }
    let bring = plan::bring(&repo);
    if !ok(&bring[0]) {
        return Err("base_unavailable");
    }
    if !ok(&bring[1]) || !ok(&bring[2]) {
        return Err("bundle_refused");
    }
    let got = printed(&plan::head_of(&repo)).ok_or("bundle_refused")?;
    if std::str::from_utf8(&got).map(str::trim) != Ok(head) {
        return Err("bundle_not_head");
    }
    // judged from git's objects, before anything of the tree is on disk or run
    let changed = printed(&plan::changed(&repo)).ok_or("tree_refused")?;
    sandbox::changed_refused(plan::paths(&changed)?)?;
    let lock = printed(&plan::lock_at(&repo)).ok_or("tree_refused")?;
    if !std::str::from_utf8(&lock).is_ok_and(sandbox::lock_ok) {
        return Err("tree_refused");
    }
    if !ok(&plan::checkout(&repo)) {
        return Err("tree_refused");
    }
    if !ok(&sandbox::fetch(&places, &empty)) {
        return Err("fetch_failed");
    }
    std::fs::write(&profile_file, profile).map_err(|_| "cannot_write")?;
    std::fs::create_dir(l.tmp(run_id)).map_err(|_| "cannot_write")?;
    let _ = run::note(l, run_id, run::STEPS[3]);
    run_inside(
        &plan::receipt(&site.cfg, &places, &profile_file, run_id),
        &l.pid(run_id),
    );
    run::keep_out(l, run_id)
}

/// Runs receipt-run inside the sandbox in a process group of its own (written as the second
/// line of the pid file), waits for it (at most [`RUN_SECS`]) and then stops whatever of the
/// group is left.
fn run_inside(s: &Step, pid_file: &Path) {
    let mut c = command(s);
    c.stdout(Stdio::null()).stderr(Stdio::null());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        c.process_group(0);
    }
    let Ok(mut child) = c.spawn() else {
        return;
    };
    let group = format!("-{}", child.id());
    // a cancel stops this group too (second line of the pid file)
    {
        use std::io::Write;
        let _ = std::fs::OpenOptions::new()
            .append(true)
            .open(pid_file)
            .and_then(|mut f| writeln!(f, "{}", child.id()));
    }
    let mut waited = 0;
    while waited < RUN_SECS && matches!(child.try_wait(), Ok(None)) {
        wait_secs(5);
        waited += 5;
    }
    let _ = quiet("/bin/kill", &["-KILL", "--", &group]);
    let _ = child.wait();
}

/// The place this program's supervisor finds things: the home sshd gave.
pub fn home_of(env_home: Option<std::ffi::OsString>) -> Option<PathBuf> {
    let p = PathBuf::from(env_home?);
    (p.is_absolute() && p.is_dir()).then_some(p)
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    const ID: &str = "web-a9136-012345678";
    const HEAD: &str = "0123456789abcdef0123456789abcdef01234567";
    const SHA: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

    fn home() -> tempfile::TempDir {
        let d = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(d.path().join("wt.noindex")).unwrap();
        d
    }

    fn run_req(krate: &str, knives: u64) -> Request {
        Request::Run {
            run_id: ID.into(),
            head: HEAD.into(),
            krate: krate.into(),
            bundle_len: 3,
            bundle_sha256: SHA.into(),
            knives_len: knives,
            knives_sha256: SHA.into(),
        }
    }

    fn says(d: &tempfile::TempDir, req: &Request) -> String {
        let me = d.path().join("no-such-gate");
        let mut stdin: &[u8] = b"abc";
        String::from_utf8(serve(req, d.path(), &me, &mut stdin)).unwrap()
    }

    #[test]
    fn df_is_read_by_its_free_column() {
        let df = "Filesystem   1024-blocks      Used Available Capacity iused      ifree %iused  Mounted on\n/dev/disk3s1   482797652 184239508 248632876    43% 2797292 2486328760    0%   /System/Volumes/Data\n";
        assert_eq!(free_kb_of(df), Some(248_632_876));
        assert_eq!(free_kb_of(""), None);
        assert_eq!(free_kb_of("Filesystem 1024-blocks Used\n"), None);
        assert!(MIN_FREE_KB > 20 * 1024 * 1024);
    }

    #[test]
    fn a_machine_that_is_not_set_up_runs_nothing() {
        let d = home();
        // no pinned settings: refused, and nothing was received or noted
        // (a temp folder whose path holds characters a place cannot is bad_place: a refusal too)
        let got = says(&d, &run_req("ws", 0));
        assert!(
            ["ERR config_bad\n", "ERR bad_place\n"].contains(&got.as_str()),
            "reason: unpinned_ran ({got})"
        );
        assert!(!Layout::new(d.path()).note(ID).exists());
        assert!(
            !Layout::new(d.path()).bundle(ID).exists(),
            "reason: bundle_taken"
        );
        // settings that pin another receipt-run than the one on disk
        std::fs::create_dir_all(d.path().join(".ez2-aib")).unwrap();
        let rr = d.path().join("rr.sh");
        std::fs::write(&rr, b"echo other\n").unwrap();
        // (the test's temp folder may hold characters a place cannot: then it is bad_place,
        // which is a refusal too)
        let conf = format!(
            "receipt_run={}\nreceipt_run_sha256={SHA}\ntoolchain=1.99.1-x\naib=/usr/bin/true\nbash=/bin/bash\n",
            rr.display()
        );
        std::fs::write(d.path().join(CONF), conf).unwrap();
        let got = says(&d, &run_req("ws", 0));
        assert!(
            [
                "ERR receipt_tool_changed\n",
                "ERR bad_place\n",
                "ERR config_bad\n"
            ]
            .contains(&got.as_str()),
            "reason: changed_tool_ran ({got})"
        );
        assert!(!Layout::new(d.path()).bundle(ID).exists());
    }

    #[test]
    fn the_first_version_takes_the_whole_workspace_and_no_knives() {
        let d = home();
        assert_eq!(says(&d, &run_req("aibd", 0)), "ERR only_ws_no_knives\n");
        assert_eq!(says(&d, &run_req("ws", 10)), "ERR only_ws_no_knives\n");
    }

    #[test]
    fn a_run_asked_again_is_told_how_it_stands_and_not_started() {
        let d = home();
        let l = Layout::new(d.path());
        run::note(&l, ID, "received").unwrap();
        run::refuse(&l, ID, "tree_refused").unwrap();
        // no settings on this machine: a second start would answer config_bad
        assert_eq!(
            says(&d, &run_req("ws", 0)),
            "OK run\nstate refused\nreason tree_refused\n",
            "reason: started_twice"
        );
        assert_eq!(
            says(&d, &Request::Status { run_id: ID.into() }),
            "OK status\nstate refused\nreason tree_refused\n"
        );
        assert_eq!(
            says(&d, &Request::Fetch { run_id: ID.into() }),
            "ERR tree_refused\n"
        );
        assert_eq!(
            says(&d, &Request::Cancel { run_id: ID.into() }),
            "OK cancel\nstate ended\n"
        );
        let other = "web-a9136-987654321";
        assert_eq!(
            says(
                &d,
                &Request::Cancel {
                    run_id: other.into()
                }
            ),
            "ERR no_such_run\n"
        );
        assert_eq!(
            says(
                &d,
                &Request::Fetch {
                    run_id: other.into()
                }
            ),
            "ERR no_such_run\n"
        );
    }

    #[test]
    fn cleaning_leaves_the_note_and_the_kept_out() {
        let d = home();
        let l = Layout::new(d.path());
        std::fs::create_dir_all(l.tree(ID)).unwrap();
        std::fs::write(l.out(ID), b"x\n").unwrap();
        run::keep_out(&l, ID).unwrap();
        run::note(&l, ID, "run").unwrap();
        for f in [l.bundle(ID), l.profile(ID), l.pid(ID)] {
            std::fs::write(f, b"x").unwrap();
        }
        std::fs::create_dir_all(l.empty(ID)).unwrap();
        clean(&l, ID);
        for gone in [
            l.work(ID),
            l.bundle(ID),
            l.profile(ID),
            l.pid(ID),
            l.empty(ID),
        ] {
            assert!(!gone.exists(), "reason: {gone:?} left");
        }
        assert!(
            l.note(ID).is_file() && l.kept_out(ID).is_file(),
            "reason: receipt_removed"
        );
        assert_eq!(run::state(&l, ID, &alive), State::Done);
    }

    #[test]
    fn a_home_is_an_absolute_folder_that_is_there() {
        let d = home();
        assert_eq!(home_of(Some(d.path().into())), Some(d.path().to_path_buf()));
        assert_eq!(home_of(None), None);
        assert_eq!(home_of(Some("relative".into())), None);
        assert_eq!(home_of(Some(d.path().join("missing").into())), None);
    }
}
