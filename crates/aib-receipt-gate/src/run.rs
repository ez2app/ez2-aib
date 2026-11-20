//! Slice 3b-1 (DESIGN-g1-receipt-origacct-b4297-1120 r3 §4 §5): where one run lives on the
//! test machine and what the gate says of it. File names and reading only; starting and
//! stopping programs is slice 3b-2.
//!
//! One run is `<home>/wt.noindex/<run id>/` (the run folder: its tree, target, temporary
//! files and the `.out` receipt-run writes; the only place the sandboxed programs write) and
//! a few files beside it that the gate alone writes, outside what the sandbox may touch: the
//! bundle as received, the profile, the empty folder the fetch starts in, the supervisor's
//! pid, its note (one word a line: how far the run got, or why it was refused) and the kept
//! `.out`. So the tested code can forge what its `.out` says (a signal, not evidence: the
//! merge gate reads CI) but not the pid a cancel stops nor the reason the gate gives.
//!
//! The `.out` in the run folder is never handed back from there: the tested code could make
//! it a link to any file of the account, and the gate (outside the sandbox) would read that
//! file into the receipt. When the sandbox has ended [`keep_out`] copies it beside the run
//! folder, opened without following a link and only when it is a plain file under the cap;
//! `fetch` reads that copy alone.
//!
//! No path is taken from a request: every one is made here from a run id that passed
//! [`crate::run_id_ok`].

use std::path::{Path, PathBuf};

/// The largest `.out` handed back (the same cap aibd reads with).
pub const MAX_OUT: u64 = 1024 * 1024;
/// The largest note read.
const MAX_NOTE: u64 = 64 * 1024;

/// The words a note may hold, in the order a run goes through them.
pub const STEPS: [&str; 4] = ["received", "queued", "fetch", "run"];
/// A note line that ends a run before it has an `.out`: `refused <code>`.
const REFUSED: &str = "refused ";

/// Where the runs of one account are.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Layout {
    runs: PathBuf,
}

impl Layout {
    /// `home` is the account's home; runs are under its `wt.noindex`.
    pub fn new(home: &Path) -> Self {
        Self {
            runs: home.join("wt.noindex"),
        }
    }

    fn beside(&self, run_id: &str, ext: &str) -> PathBuf {
        self.runs.join(format!("{run_id}.{ext}"))
    }

    /// The run folder.
    pub fn work(&self, run_id: &str) -> PathBuf {
        self.runs.join(run_id)
    }
    pub fn tree(&self, run_id: &str) -> PathBuf {
        self.work(run_id).join("tree")
    }
    pub fn tmp(&self, run_id: &str) -> PathBuf {
        self.work(run_id).join("tmp")
    }
    /// Written by receipt-run inside the sandbox.
    pub fn out(&self, run_id: &str) -> PathBuf {
        self.work(run_id).join(format!("{run_id}.out"))
    }
    pub fn done(&self, run_id: &str) -> PathBuf {
        self.work(run_id).join(format!("{run_id}.done"))
    }
    pub fn bundle(&self, run_id: &str) -> PathBuf {
        self.beside(run_id, "bundle")
    }
    pub fn profile(&self, run_id: &str) -> PathBuf {
        self.beside(run_id, "sb")
    }
    pub fn empty(&self, run_id: &str) -> PathBuf {
        self.beside(run_id, "empty")
    }
    pub fn pid(&self, run_id: &str) -> PathBuf {
        self.beside(run_id, "pid")
    }
    pub fn note(&self, run_id: &str) -> PathBuf {
        self.beside(run_id, "gate")
    }
    /// The `.out` as the gate copied it out ([`keep_out`]): the only one handed back.
    pub fn kept_out(&self, run_id: &str) -> PathBuf {
        self.beside(run_id, "out")
    }
}

/// How one run stands.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum State {
    /// No run of this id was ever received here.
    None,
    /// Its supervisor is alive; the word is the last of [`STEPS`] it wrote.
    UnderWay(&'static str),
    /// Its `.out` was kept ([`keep_out`]).
    Done,
    /// The gate refused it before anything of the tree ran, and why.
    Refused(String),
    /// It was received, has no `.out`, and its supervisor is gone.
    Lost,
}

fn plain_file(p: &Path) -> bool {
    std::fs::symlink_metadata(p).is_ok_and(|m| m.is_file())
}

#[cfg(unix)]
fn open_nofollow(p: &Path) -> std::io::Result<std::fs::File> {
    use std::os::unix::fs::OpenOptionsExt;
    std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(p)
}

#[cfg(windows)]
fn open_nofollow(p: &Path) -> std::io::Result<std::fs::File> {
    use std::os::windows::fs::OpenOptionsExt;
    // FILE_FLAG_OPEN_REPARSE_POINT: a link is opened as itself, and is then not a plain file
    const OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(OPEN_REPARSE_POINT)
        .open(p)
}

/// Copies the run folder's `.out` beside it, for `fetch`. The file is opened without
/// following a link and judged by the opened handle (a plain file), read up to one byte past
/// [`MAX_OUT`] (more than the cap is refused, however it grew), and written to a new file
/// (never one that exists). The run
/// folder itself must be a real folder under the runs (not a link put in its place).
/// `out_unreadable`: any of these fails; nothing is kept then.
pub fn keep_out(l: &Layout, run_id: &str) -> Result<(), &'static str> {
    use std::io::{Read, Write};
    const NO: &str = "out_unreadable";
    let work = l.work(run_id);
    if !std::fs::symlink_metadata(&work).is_ok_and(|m| m.is_dir()) {
        return Err(NO);
    }
    let f = open_nofollow(&l.out(run_id)).map_err(|_| NO)?;
    let meta = f.metadata().map_err(|_| NO)?;
    if !meta.is_file() {
        return Err(NO);
    }
    let mut bytes = Vec::new();
    f.take(MAX_OUT + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| NO)?;
    if bytes.len() as u64 > MAX_OUT {
        return Err(NO);
    }
    let kept = l.kept_out(run_id);
    // a copy that is there stays as it is: kept once
    let mut k = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&kept)
        .map_err(|_| NO)?;
    if k.write_all(&bytes).and_then(|()| k.sync_all()).is_err() {
        let _ = std::fs::remove_file(&kept);
        return Err(NO);
    }
    Ok(())
}

fn read_small(p: &Path, max: u64) -> Option<String> {
    let meta = std::fs::symlink_metadata(p).ok()?;
    if !meta.is_file() || meta.len() > max {
        return None;
    }
    std::fs::read_to_string(p).ok()
}

/// A refusal code as it is written and said: lower-case letters, digits and `_`, at most 40.
pub fn code_ok(code: &str) -> bool {
    (1..=40).contains(&code.len())
        && code
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
}

/// What a note's text says: the refusal on any line (a refused run stays refused), else the
/// last step word. `None`: no line of ours.
pub fn note_says(text: &str) -> Option<Result<&'static str, String>> {
    let mut last = None;
    for line in text.lines() {
        if let Some(code) = line.strip_prefix(REFUSED) {
            return Some(Err(if code_ok(code) {
                code.to_owned()
            } else {
                "refused".to_owned()
            }));
        }
        if let Some(step) = STEPS.iter().find(|s| **s == line) {
            last = Some(Ok(*step));
        }
    }
    last
}

/// The state of `run_id`, read from its files. `alive` says whether a pid is a live process.
pub fn state(l: &Layout, run_id: &str, alive: &dyn Fn(u32) -> bool) -> State {
    let Some(note) = read_small(&l.note(run_id), MAX_NOTE) else {
        return State::None;
    };
    let says = note_says(&note);
    if let Some(Err(code)) = says {
        return State::Refused(code);
    }
    if plain_file(&l.kept_out(run_id)) {
        return State::Done;
    }
    let pid = read_small(&l.pid(run_id), 32).and_then(|t| t.trim().parse::<u32>().ok());
    match (pid, says) {
        (Some(p), Some(Ok(step))) if p > 1 && alive(p) => State::UnderWay(step),
        (Some(p), None) if p > 1 && alive(p) => State::UnderWay(STEPS[0]),
        _ => State::Lost,
    }
}

/// The answer to `status`: the first line `OK status`, then `state <word>` and, for a run
/// under way or refused, one more line. No path, user or machine name.
pub fn status_text(s: &State) -> String {
    let rest = match s {
        State::None => "state none\n".to_owned(),
        State::UnderWay(step) => format!("state under_way\nstep {step}\n"),
        State::Done => "state done\n".to_owned(),
        State::Refused(code) => format!("state refused\nreason {code}\n"),
        State::Lost => "state lost\n".to_owned(),
    };
    format!("OK status\n{rest}")
}

/// The answer to `fetch`: `OK fetch` and the kept `.out` only for a finished run; a refused
/// one answers its reason, one under way `not_done`, a lost one `lost_run`, none
/// `no_such_run`. Only the gate's own copy is read ([`keep_out`]).
pub fn fetch_bytes(l: &Layout, run_id: &str, s: &State) -> Vec<u8> {
    let err = |code: &str| format!("ERR {code}\n").into_bytes();
    match s {
        State::None => err("no_such_run"),
        State::UnderWay(_) => err("not_done"),
        State::Lost => err("lost_run"),
        State::Refused(code) => err(code),
        State::Done => {
            let path = l.kept_out(run_id);
            let plain =
                std::fs::symlink_metadata(&path).is_ok_and(|m| m.is_file() && m.len() <= MAX_OUT);
            match std::fs::read(&path) {
                Ok(bytes) if plain && bytes.len() as u64 <= MAX_OUT => {
                    let mut all = b"OK fetch\n".to_vec();
                    all.extend_from_slice(&bytes);
                    all
                }
                _ => err("out_unreadable"),
            }
        }
    }
}

/// Appends one line to the run's note (made when it is not there, never through a link).
pub fn note(l: &Layout, run_id: &str, line: &str) -> Result<(), &'static str> {
    use std::io::Write;
    let p = l.note(run_id);
    if std::fs::symlink_metadata(&p).is_ok_and(|m| !m.is_file()) {
        return Err("cannot_write");
    }
    let mut f = std::fs::OpenOptions::new()
        .append(true)
        .create(true)
        .open(&p)
        .map_err(|_| "cannot_write")?;
    writeln!(f, "{line}").map_err(|_| "cannot_write")
}

/// Writes a refusal to the note: the run ends here.
pub fn refuse(l: &Layout, run_id: &str, code: &str) -> Result<(), &'static str> {
    let code = if code_ok(code) { code } else { "refused" };
    note(l, run_id, &format!("{REFUSED}{code}"))
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    const ID: &str = "web-a9136-012345678";

    fn home() -> (tempfile::TempDir, Layout) {
        let d = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(d.path().join("wt.noindex")).unwrap();
        let l = Layout::new(d.path());
        (d, l)
    }

    fn wrote(l: &Layout, out: &[u8]) {
        std::fs::create_dir_all(l.work(ID)).unwrap();
        std::fs::write(l.out(ID), out).unwrap();
        std::fs::write(l.done(ID), b"").unwrap();
    }

    #[test]
    fn what_the_sandbox_writes_and_what_the_gate_writes_are_apart() {
        let l = Layout::new(Path::new("/Users/owner"));
        let work = l.work(ID);
        assert_eq!(
            work,
            Path::new("/Users/owner/wt.noindex/web-a9136-012345678")
        );
        for inside in [l.tree(ID), l.tmp(ID), l.out(ID), l.done(ID)] {
            assert!(inside.starts_with(&work), "reason: {inside:?} outside");
        }
        // the pid a cancel stops, the reason the gate gives, the profile and the received
        // bundle are not where the tested code writes
        for beside in [
            l.pid(ID),
            l.note(ID),
            l.profile(ID),
            l.bundle(ID),
            l.empty(ID),
            l.kept_out(ID),
        ] {
            assert!(
                !beside.starts_with(&work),
                "reason: {beside:?} writable_by_the_run"
            );
            assert_eq!(beside.parent(), work.parent());
        }
    }

    #[test]
    fn a_note_says_the_last_step_or_the_refusal() {
        assert_eq!(note_says(""), None);
        assert_eq!(note_says("hello\n"), None);
        assert_eq!(note_says("received\n"), Some(Ok("received")));
        assert_eq!(note_says("received\nqueued\nfetch\n"), Some(Ok("fetch")));
        // a refusal on any line ends it, whatever is written after
        assert_eq!(
            note_says("received\nrefused tree_refused\nrun\n"),
            Some(Err("tree_refused".into())),
            "reason: refusal_overwritten"
        );
        // a reason that is not a plain code is not said as it is
        assert_eq!(
            note_says("refused /Users/owner/x y\n"),
            Some(Err("refused".into())),
            "reason: path_said"
        );
        assert!(code_ok("disk_low") && !code_ok("") && !code_ok("Disk") && !code_ok("a b"));
        assert!(!code_ok(&"a".repeat(41)));
    }

    #[test]
    fn a_runs_state_is_read_from_its_files() {
        let (_d, l) = home();
        let alive = |_: u32| true;
        let dead = |_: u32| false;
        assert_eq!(state(&l, ID, &alive), State::None);
        note(&l, ID, "received").unwrap();
        // no pid yet, or a pid that is gone: lost, never "under way"
        assert_eq!(state(&l, ID, &alive), State::Lost);
        std::fs::write(l.pid(ID), "4242\n").unwrap();
        assert_eq!(
            state(&l, ID, &dead),
            State::Lost,
            "reason: dead_pid_under_way"
        );
        assert_eq!(state(&l, ID, &alive), State::UnderWay("received"));
        note(&l, ID, "queued").unwrap();
        note(&l, ID, "run").unwrap();
        assert_eq!(state(&l, ID, &alive), State::UnderWay("run"));
        // pid 0 / 1 / not a number is nobody's supervisor
        for bad in ["0", "1", "-5", "x", ""] {
            std::fs::write(l.pid(ID), bad).unwrap();
            assert_eq!(state(&l, ID, &alive), State::Lost, "reason: pid {bad:?}");
        }
        // what the tested code wrote in the run folder does not make it done: the gate's
        // copy does
        wrote(&l, b"RC fmt=0\n");
        assert_eq!(
            state(&l, ID, &dead),
            State::Lost,
            "reason: run_folder_out_done"
        );
        keep_out(&l, ID).unwrap();
        assert_eq!(state(&l, ID, &dead), State::Done);
        // a refusal stands even when the tested code left an .out behind
        refuse(&l, ID, "tree_refused").unwrap();
        assert_eq!(
            state(&l, ID, &alive),
            State::Refused("tree_refused".into()),
            "reason: forged_out_beats_refusal"
        );
    }

    #[test]
    fn status_and_fetch_say_fixed_words_and_the_out_only_when_done() {
        let (_d, l) = home();
        assert_eq!(status_text(&State::None), "OK status\nstate none\n");
        assert_eq!(
            status_text(&State::UnderWay("fetch")),
            "OK status\nstate under_way\nstep fetch\n"
        );
        assert_eq!(
            status_text(&State::Refused("disk_low".into())),
            "OK status\nstate refused\nreason disk_low\n"
        );
        assert_eq!(fetch_bytes(&l, ID, &State::None), b"ERR no_such_run\n");
        assert_eq!(
            fetch_bytes(&l, ID, &State::UnderWay("run")),
            b"ERR not_done\n",
            "reason: half_out_handed"
        );
        assert_eq!(fetch_bytes(&l, ID, &State::Lost), b"ERR lost_run\n");
        assert_eq!(
            fetch_bytes(&l, ID, &State::Refused("tree_refused".into())),
            b"ERR tree_refused\n"
        );
        // the run folder's .out is not what is handed back
        wrote(&l, b"RC fmt=0\nRECEIPT-SHA256 x\n");
        assert_eq!(
            fetch_bytes(&l, ID, &State::Done),
            b"ERR out_unreadable\n",
            "reason: run_folder_out_handed"
        );
        keep_out(&l, ID).unwrap();
        assert_eq!(
            fetch_bytes(&l, ID, &State::Done),
            b"OK fetch\nRC fmt=0\nRECEIPT-SHA256 x\n"
        );
        // kept once: a second copy never replaces it
        std::fs::write(l.out(ID), b"LATER\n").unwrap();
        assert_eq!(
            keep_out(&l, ID),
            Err("out_unreadable"),
            "reason: kept_twice"
        );
        assert_eq!(
            fetch_bytes(&l, ID, &State::Done),
            b"OK fetch\nRC fmt=0\nRECEIPT-SHA256 x\n"
        );
    }

    #[test]
    fn an_out_over_the_cap_is_not_kept() {
        let (_d, l) = home();
        let big = vec![b'x'; usize::try_from(MAX_OUT).unwrap() + 1];
        wrote(&l, &big);
        assert_eq!(
            keep_out(&l, ID),
            Err("out_unreadable"),
            "reason: big_out_kept"
        );
        assert!(!l.kept_out(ID).exists());
        std::fs::write(l.out(ID), &big[..big.len() - 1]).unwrap();
        assert_eq!(keep_out(&l, ID), Ok(()));
        // no .out at all
        let (_d2, l2) = home();
        assert_eq!(keep_out(&l2, ID), Err("out_unreadable"));
    }

    #[cfg(unix)]
    #[test]
    fn an_out_that_is_a_link_is_not_followed() {
        let (d, l) = home();
        let secret = d.path().join("secret");
        std::fs::write(&secret, b"TOKEN\n").unwrap();
        std::fs::create_dir_all(l.work(ID)).unwrap();
        std::os::unix::fs::symlink(&secret, l.out(ID)).unwrap();
        std::fs::write(l.done(ID), b"").unwrap();
        note(&l, ID, "run").unwrap();
        assert_eq!(
            keep_out(&l, ID),
            Err("out_unreadable"),
            "reason: link_followed"
        );
        assert!(!l.kept_out(ID).exists());
        // the run folder itself put aside for a link to a folder that has such a file
        let (d3, l3) = home();
        let other = d3.path().join("other");
        std::fs::create_dir_all(&other).unwrap();
        std::fs::write(other.join(format!("{ID}.out")), b"TOKEN\n").unwrap();
        std::os::unix::fs::symlink(&other, l3.work(ID)).unwrap();
        assert_eq!(
            keep_out(&l3, ID),
            Err("out_unreadable"),
            "reason: linked_folder_followed"
        );
        assert!(!l3.kept_out(ID).exists());
        // and a note that is a link is not written through
        let (d2, l2) = home();
        std::os::unix::fs::symlink(d2.path().join("elsewhere"), l2.note(ID)).unwrap();
        assert_eq!(note(&l2, ID, "received"), Err("cannot_write"));
        assert!(
            !d2.path().join("elsewhere").exists(),
            "reason: wrote_through_link"
        );
    }
}
