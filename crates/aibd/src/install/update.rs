//! T97-3m (043dd, IFACE-T97-3m-043dd-1117.md r2; 5d33a REVIEW-iface-T97-3m-5d33a-r2.md 0/0/0/1):
//! `aibd update --file <new>` on macOS.
//!
//! The installed aibd decides: it reads the new file (never runs it) for its sha256, signer and
//! version mark, asks for a hardware-signed approval of exactly those, and verifies the proof
//! against a binding computed again from the file right before the swap. Then stop → back up the
//! database (after the stop, with -wal / -shm) → swap (copy to a temp file, sha256 once more,
//! rename) → start → selfcheck; any failure after the swap puts `.prev` and the database back.
//! A swap left half-done is finished or undone by the next `aibd start` / `aibd update`
//! (the installed CLI, not the new build).
//!
//! What this slice does not defend: an AI running as the same user can write the install folder
//! directly (IFACE r2 §4, GUIDE-same-user-ai-threat-model); this only makes `aibd update` itself
//! ask.

use super::*;

/// The transaction record next to the install record.
pub const UPDATE_FILE: &str = "update.json";
/// The one kept previous executable: `<exe>.prev`.
pub const PREV_SUFFIX: &str = "prev";
/// aibd's database in its data folder (server.rs) and the files sqlite keeps beside it.
pub const DB_FILE: &str = "aibd.sqlite";
pub const DB_SIDECARS: [&str; 2] = ["-wal", "-shm"];
/// Database copies taken after the stop: `<data>/install/db.prev/`.
pub const DB_PREV_DIR: &str = "db.prev";

/// The version mark every aibd build carries, read from the file without running it
/// (5d33a r1 M1). The two halves are kept apart here so this scanner's own constants never
/// spell a whole mark.
pub const MARK_HEAD: &[u8] = b"\x01AIBD";
pub const MARK_TAIL: &[u8] = b"-VERSION\x02";
pub const MARK_END: u8 = 0x03;
/// This build's mark. `main` names it once so the linker keeps it.
pub static VERSION_MARK: &str = concat!("\x01AIBD-VERSION\x02", env!("CARGO_PKG_VERSION"), "\x03");
const MARK_MAX: usize = 64;

pub const NO_HW: &str = "no_hw_approval: aibd update needs a hardware-signed approval, which this platform does not have yet (Windows Hello / TPM later); not falling back to Touch ID";
const ADHOC: &str = "the installed aibd is ad-hoc signed, so no new build can match its signer; run aibd install again with a signed build, then update";

/// Every version mark in `bytes`; exactly one distinct one is the version (else an error).
pub fn version_in(bytes: &[u8]) -> Result<String, String> {
    let mut found: Vec<String> = Vec::new();
    let mut i = 0;
    while let Some(off) = find(&bytes[i..], MARK_HEAD) {
        let at = i + off + MARK_HEAD.len();
        i = i + off + 1;
        let Some(rest) = bytes.get(at..) else { break };
        if !rest.starts_with(MARK_TAIL) {
            continue;
        }
        let body = &rest[MARK_TAIL.len()..];
        let Some(end) = body.iter().take(MARK_MAX + 1).position(|b| *b == MARK_END) else {
            continue;
        };
        let v = &body[..end];
        let ok = !v.is_empty()
            && v.iter()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'-' | b'+'));
        if ok {
            let v = String::from_utf8_lossy(v).into_owned();
            if !found.contains(&v) {
                found.push(v);
            }
        }
    }
    match found.as_slice() {
        [one] => Ok(one.clone()),
        [] => Err(
            "no aibd version mark in the file (not an aibd build, or one from before T97-3m)"
                .into(),
        ),
        many => Err(format!(
            "{} different version marks in the file: {many:?}",
            many.len()
        )),
    }
}

fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).position(|w| w == needle)
}

// ---------------------------------------------------------------- injected tools

/// `/usr/bin/codesign`, behind a trait so tests never sign anything.
pub trait Codesign {
    /// The designated requirement of `p` as `codesign -d -r-` prints it. `Ok(None)` = only an
    /// implicit one (`# designated => cdhash …`: ad-hoc signed).
    fn requirement_of(&self, p: &Path) -> Result<Option<String>, String>;
    /// `codesign -v -R=<requirement> <p>`.
    fn verify(&self, p: &Path, requirement: &str) -> Result<(), String>;
}

/// Read `codesign -d -r-` output (this Mac, 11-17: a signed file prints
/// `designated => identifier "com.apple.ls" and anchor apple`; an ad-hoc one prints
/// `# designated => cdhash H"…"`).
pub fn parse_requirement(stdout: &str) -> Result<Option<String>, String> {
    for line in stdout.lines() {
        if line.starts_with("# designated => ") {
            return Ok(None);
        }
        if let Some(r) = line.strip_prefix("designated => ") {
            let r = r.trim();
            if r.is_empty() {
                break;
            }
            return Ok(Some(r.to_string()));
        }
    }
    Err("codesign printed no designated requirement".into())
}

/// What the approval binds (IFACE r2 §1 step 2), computed by the installed aibd from the file.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UpdateBinding {
    pub old_version: String,
    pub new_version: String,
    pub sha256: String,
    pub tool: String,
    pub platform: String,
    pub arch: String,
}

impl UpdateBinding {
    /// The parameter bytes: a JSON object with its keys in a fixed (sorted) order.
    pub fn digest_json(&self) -> String {
        let mut m = BTreeMap::new();
        m.insert("arch", &self.arch);
        m.insert("new_version", &self.new_version);
        m.insert("old_version", &self.old_version);
        m.insert("platform", &self.platform);
        m.insert("sha256", &self.sha256);
        m.insert("tool", &self.tool);
        serde_json::to_string(&m).unwrap_or_default()
    }
    /// The one line the approval window shows: the same six things.
    pub fn line(&self) -> String {
        format!(
            "update {} {} -> {} ({}-{}, sha256 {})",
            self.tool, self.old_version, self.new_version, self.platform, self.arch, self.sha256
        )
    }
}

/// The hardware-signed approval (aib-approve `Category::AutoUpdate`, HardwareSigned).
pub trait Approve {
    /// Ask the person; the proof is opaque to aibd.
    fn approve(&self, b: &UpdateBinding, reason: &str) -> Result<String, String>;
    /// Only `Ok(())` lets the update go on.
    fn verify(&self, proof: &str, expect: &UpdateBinding) -> Result<(), String>;
}

/// Until T97-3m-b wires aib-approve's Secure Enclave signing in, every update is refused.
pub struct NoApprove;

impl Approve for NoApprove {
    fn approve(&self, _: &UpdateBinding, _: &str) -> Result<String, String> {
        Err(
            "hardware-signed approval is not wired into aibd yet (T97-3m-b); nothing changed"
                .into(),
        )
    }
    fn verify(&self, _: &str, _: &UpdateBinding) -> Result<(), String> {
        Err("no approval".into())
    }
}

/// `aibd selfcheck --json` of the installed (new) build, run after the swap and start.
pub trait SelfCheck {
    fn check(&self, exe: &Path) -> Result<(), String>;
}

/// Runs the installed executable's selfcheck (only after the approval, the swap and a start).
pub struct ExeSelfCheck;

impl SelfCheck for ExeSelfCheck {
    fn check(&self, exe: &Path) -> Result<(), String> {
        match std::process::Command::new(exe)
            .args(["selfcheck", "--json"])
            .stdin(std::process::Stdio::null())
            .output()
        {
            Ok(o) if o.status.success() => Ok(()),
            Ok(o) => Err(format!(
                "selfcheck exited {}",
                o.status
                    .code()
                    .map_or("by a signal".into(), |c| c.to_string())
            )),
            Err(e) => Err(format!("cannot run {}: {e}", exe.display())),
        }
    }
}

/// Not macOS: never reached (run_update refuses first), but the type exists everywhere.
pub struct NoCodesign;

impl Codesign for NoCodesign {
    fn requirement_of(&self, _: &Path) -> Result<Option<String>, String> {
        Err(NO_HW.into())
    }
    fn verify(&self, _: &Path, _: &str) -> Result<(), String> {
        Err(NO_HW.into())
    }
}

pub struct Upd<'a> {
    pub cs: &'a dyn Codesign,
    pub ap: &'a dyn Approve,
    pub check: &'a dyn SelfCheck,
}

// ---------------------------------------------------------------- the transaction record

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
pub struct Txn {
    pub v: u32,
    pub at: String,
    /// approved | stopped | backed_up | swapped | started | done | rolled_back
    pub step: String,
    pub exe: String,
    pub old_version: String,
    pub new_version: String,
    pub sha256: String,
    pub db_backed_up: bool,
}

fn txn_path(data: &Path) -> PathBuf {
    data.join(RECORD_DIR).join(UPDATE_FILE)
}

fn read_txn(data: &Path) -> Result<Option<Txn>, String> {
    let p = txn_path(data);
    match std::fs::read(&p) {
        Ok(b) => serde_json::from_slice(&b)
            .map(Some)
            .map_err(|e| format!("cannot read {}: {e}", p.display())),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(format!("cannot read {}: {e}", p.display())),
    }
}

fn write_txn(data: &Path, t: &Txn) -> Result<(), String> {
    let dir = data.join(RECORD_DIR);
    crate::platform::create_private_dir(&dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    let body = serde_json::to_vec_pretty(t).map_err(|e| e.to_string())?;
    crate::platform::write_private_file(&txn_path(data), &body)
        .map_err(|e| format!("write {}: {e}", txn_path(data).display()))
}

fn step(data: &Path, t: &mut Txn, s: &str) -> Result<(), String> {
    t.step = s.into();
    write_txn(data, t)
}

pub fn prev_of(exe: &Path) -> PathBuf {
    let mut s = exe.as_os_str().to_owned();
    s.push(".");
    s.push(PREV_SUFFIX);
    PathBuf::from(s)
}

fn db_prev(data: &Path) -> PathBuf {
    data.join(RECORD_DIR).join(DB_PREV_DIR)
}

fn db_files(data: &Path) -> Vec<(PathBuf, String)> {
    let mut v = vec![(data.join(DB_FILE), DB_FILE.to_string())];
    for s in DB_SIDECARS {
        let n = format!("{DB_FILE}{s}");
        v.push((data.join(&n), n));
    }
    v
}

/// Copy the database and its sidecars (only after the stop: 5d33a r1 L2).
fn back_up_db(data: &Path) -> Result<bool, String> {
    if running(data) {
        return Err("aibd still holds its lock; the database is not copied while it runs".into());
    }
    let dir = db_prev(data);
    let _ = std::fs::remove_dir_all(&dir);
    crate::platform::create_private_dir(&dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    let mut any = false;
    for (src, name) in db_files(data) {
        if src.exists() {
            std::fs::copy(&src, dir.join(&name))
                .map_err(|e| format!("copy {}: {e}", src.display()))?;
            any = true;
        }
    }
    Ok(any)
}

/// Put the copies back, the three files together (a sidecar the copy did not have is removed).
fn restore_db(data: &Path) -> Result<(), String> {
    let dir = db_prev(data);
    for (dst, name) in db_files(data) {
        let src = dir.join(&name);
        if src.exists() {
            std::fs::copy(&src, &dst).map_err(|e| format!("restore {}: {e}", dst.display()))?;
        } else if dst.exists() {
            std::fs::remove_file(&dst).map_err(|e| format!("remove {}: {e}", dst.display()))?;
        }
    }
    Ok(())
}

/// Copy `src` next to `exe`, check its sha256 once more, check the copy's signer (5d33a
/// ce253f6a L1: what goes in place is what was checked), rename it into place.
fn swap_in(
    src: &Path,
    exe: &Path,
    want: &str,
    signer: &dyn Fn(&Path) -> Result<(), String>,
) -> Result<(), String> {
    let tmp = exe.with_extension(format!("tmp-{}", std::process::id()));
    std::fs::copy(src, &tmp).map_err(|e| format!("copy {}: {e}", src.display()))?;
    let got = sha256_file(&tmp).map_err(|e| format!("read {}: {e}", tmp.display()))?;
    if got != want {
        let _ = std::fs::remove_file(&tmp);
        return Err(format!(
            "the file changed after the approval (sha256 {got}, approved {want}); not swapped"
        ));
    }
    if let Err(e) = signer(&tmp) {
        let _ = std::fs::remove_file(&tmp);
        return Err(format!(
            "the copy to install failed the signer check: {e}; not swapped"
        ));
    }
    make_executable(&tmp).map_err(|e| format!("chmod {}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, exe).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        format!("rename into {}: {e}", exe.display())
    })
}

/// Undo a swap: launchd out, `.prev` back, the database copies back, start the old build.
/// `restart`: start the old build again (update); `start` starts it itself after `recover`.
fn roll_back(
    rec: &Record,
    data: &Path,
    t: &mut Txn,
    svc: &Svc,
    out: &mut Out,
    restart: bool,
) -> Result<(), String> {
    let exe = PathBuf::from(&rec.exe);
    let _ = unload(&rec.label, data, svc, out, true);
    let prev = prev_of(&exe);
    if prev.exists() {
        std::fs::rename(&prev, &exe).map_err(|e| format!("put {} back: {e}", prev.display()))?;
        out.say(format!("RESTORED {}", exe.display()));
    }
    if t.db_backed_up {
        restore_db(data)?;
        out.say("RESTORED database (to before the update; what the new build wrote is gone)");
    }
    step(data, t, "rolled_back")?;
    if !restart {
        return Ok(());
    }
    if let Loaded::No = loaded(&svc.ld.print(&rec.label)) {
        if register(Path::new(&rec.plist), data, svc, out).is_err() {
            out.say("WARN the old aibd did not answer after the roll back; run aibd status");
        }
    }
    Ok(())
}

/// A transaction left half-done: before the swap there is nothing to undo; from the swap on,
/// the old build goes back (5d33a r1 L3: the installed CLI does this, not the new build).
pub(super) fn recover(rec: &Record, data: &Path, svc: &Svc, out: &mut Out) -> Result<(), String> {
    let Some(mut t) = read_txn(data)? else {
        return Ok(());
    };
    match t.step.as_str() {
        "done" | "rolled_back" => Ok(()),
        "approved" | "stopped" | "backed_up" => {
            out.say(format!(
                "UPDATE-ABANDONED at {} (nothing was swapped)",
                t.step
            ));
            step(data, &mut t, "rolled_back")
        }
        _ => {
            out.say(format!(
                "UPDATE-UNFINISHED at {}: putting the old build back",
                t.step
            ));
            roll_back(rec, data, &mut t, svc, out, false)
        }
    }
}

// ---------------------------------------------------------------- the command

fn binding_for(file: &Path, old_version: &str, h: &Host) -> Result<UpdateBinding, String> {
    let bytes = std::fs::read(file).map_err(|e| format!("read {}: {e}", file.display()))?;
    Ok(UpdateBinding {
        old_version: old_version.to_string(),
        new_version: version_in(&bytes)?,
        sha256: sha256_file(file).map_err(|e| format!("read {}: {e}", file.display()))?,
        tool: "aibd".into(),
        platform: h.os.into(),
        arch: h.arch.into(),
    })
}

/// The installed build's version: its mark, else the record's version line.
fn installed_version(rec: &Record) -> String {
    std::fs::read(&rec.exe)
        .ok()
        .and_then(|b| version_in(&b).ok())
        .unwrap_or_else(|| rec.version.clone())
}

pub fn update(h: &Host, o: &Opts, svc: &Svc, u: &Upd) -> Result<Out, String> {
    not_root(h)?;
    let Some(file) = o.file.clone() else {
        return Err("update needs --file <new aibd>".into());
    };
    let (rec, data, _) = owned(h, o)?;
    let exe = PathBuf::from(&rec.exe);
    if same_path(&file.to_string_lossy(), &rec.exe) {
        return Err("--file is the installed executable itself".into());
    }
    let mut out = Out::default();
    recover(&rec, &data, svc, &mut out)?;

    // 1. the installed aibd reads the new file; it never runs it (5d33a r1 M1)
    let old_version = installed_version(&rec);
    let first = binding_for(&file, &old_version, h)?;
    let installed_sha = sha256_file(&exe).map_err(|e| format!("read {}: {e}", exe.display()))?;
    if first.sha256 == installed_sha {
        out.say(format!("ALREADY-CURRENT {}", first.new_version));
        return Ok(out);
    }
    let Some(req) = u.cs.requirement_of(&exe)? else {
        return Ok(Out::fail(RC_REFUSED, ADHOC));
    };
    if let Err(e) = u.cs.verify(&file, &req) {
        return Ok(Out::fail(
            RC_REFUSED,
            format!("the new file is not signed by the installed aibd's signer ({req}): {e}"),
        ));
    }
    // 2. hardware-signed approval of exactly this file, verified against a binding computed
    // again right before anything changes
    let proof = match u.ap.approve(&first, &first.line()) {
        Ok(p) => p,
        Err(e) => return Ok(Out::fail(RC_REFUSED, format!("not approved: {e}"))),
    };
    let expect = binding_for(&file, &old_version, h)?;
    if let Err(e) = u.ap.verify(&proof, &expect) {
        return Ok(Out::fail(RC_REFUSED, format!("approval refused: {e}")));
    }
    out.say(format!("APPROVED {}", expect.line()));
    let mut t = Txn {
        v: 1,
        at: rfc3339(h.now_unix),
        step: String::new(),
        exe: rec.exe.clone(),
        old_version,
        new_version: expect.new_version.clone(),
        sha256: expect.sha256.clone(),
        db_backed_up: false,
    };
    step(&data, &mut t, "approved")?;
    // 3. stop
    if let Err(e) = unload(&rec.label, &data, svc, &mut out, true) {
        let _ = step(&data, &mut t, "rolled_back");
        return Ok(keep_lines(out, Err(e)));
    }
    // 4. back up the database, now that aibd is stopped; 5. the record says swapped before the
    // rename (5d33a ce253f6a M1: a stop in between leaves .prev for recover to put back)
    let prev = prev_of(&exe);
    let pre = step(&data, &mut t, "stopped")
        .and_then(|()| back_up_db(&data))
        .and_then(|b| {
            t.db_backed_up = b;
            step(&data, &mut t, "backed_up")
        })
        .and_then(|()| {
            let _ = std::fs::remove_file(&prev);
            step(&data, &mut t, "swapped")
        });
    if let Err(e) = pre {
        // 5d33a ce253f6a L2: nothing was swapped; start the old build again, not later
        let _ = step(&data, &mut t, "rolled_back");
        let again = register(Path::new(&rec.plist), &data, svc, &mut out).is_ok();
        let tail = if again {
            "the old build was started again"
        } else {
            "the old build did not answer; run aibd start"
        };
        return Ok(keep_lines(
            out,
            Err(Out::fail(RC_REFUSED, format!("aibd update: {e}; {tail}"))),
        ));
    }
    std::fs::rename(&exe, &prev).map_err(|e| format!("rename {} to .prev: {e}", exe.display()))?;
    let signer = |p: &Path| u.cs.verify(p, &req);
    let after: Result<(), String> = swap_in(&file, &exe, &expect.sha256, &signer)
        .and_then(|()| {
            // 6. start, 7. selfcheck
            register(Path::new(&rec.plist), &data, svc, &mut out)
                .map_err(|e| e.stderr.trim().to_string())
        })
        .and_then(|()| step(&data, &mut t, "started"))
        .and_then(|()| u.check.check(&exe));
    if let Err(e) = after {
        out.say(format!("UPDATE-FAILED {e}"));
        roll_back(&rec, &data, &mut t, svc, &mut out, true)?;
        return Ok(keep_lines(
            out,
            Err(Out::fail(
                RC_REFUSED,
                format!("aibd update: {e}; the old build is back"),
            )),
        ));
    }
    let mut r = rec.clone();
    r.at = rfc3339(h.now_unix);
    r.version = expect.new_version.clone();
    r.build_sha256 = expect.sha256.clone();
    write_record(&data, &r)?;
    step(&data, &mut t, "done")?;
    out.say(format!("UPDATED {} -> {}", t.old_version, t.new_version));
    Ok(out)
}

/// `aibd update`: a platform without a hardware-signed approval refuses first (no Touch ID
/// fallback).
pub fn run_update(args: &[&str], h: &Host, svc: &Svc, u: &Upd) -> Out {
    if h.os != "macos" {
        return Out::fail(RC_UNSUPPORTED, format!("aibd update: {NO_HW}"));
    }
    let o = match parse("update", args) {
        Ok(o) => o,
        Err(e) => return Out::fail(RC_USAGE, format!("aibd {e}\n{USAGE}")),
    };
    update(h, &o, svc, u).unwrap_or_else(|e| Out::fail(RC_REFUSED, format!("aibd update: {e}")))
}

#[cfg(test)]
mod tests {
    use super::super::tests::{print_missing, print_running, FakeHealth, FakeLd, Mac};
    use super::*;
    use std::cell::RefCell;

    fn mark(ver: &str) -> Vec<u8> {
        let mut v = MARK_HEAD.to_vec();
        v.extend_from_slice(MARK_TAIL);
        v.extend_from_slice(ver.as_bytes());
        v.push(MARK_END);
        v
    }
    fn build(ver: &str, body: &str) -> Vec<u8> {
        let mut v = body.as_bytes().to_vec();
        v.extend(mark(ver));
        v.extend_from_slice(b"tail");
        v
    }

    #[derive(Default)]
    struct FakeCs {
        adhoc: bool,
        other_signer: bool,
        /// the copy next to the installed file (`*.tmp-<pid>`) fails the check
        copy_fails: bool,
        /// when the copy is checked: the record's step (data folder) and whether the installed
        /// file's .prev was there
        data: Option<(PathBuf, PathBuf)>,
        at_copy: RefCell<Option<(String, bool)>>,
        calls: RefCell<Vec<String>>,
    }
    impl Codesign for FakeCs {
        fn requirement_of(&self, _: &Path) -> Result<Option<String>, String> {
            self.calls.borrow_mut().push("requirement".into());
            Ok(
                (!self.adhoc)
                    .then(|| "identifier \"app.ez2.aibd\" and anchor apple generic".into()),
            )
        }
        fn verify(&self, p: &Path, _: &str) -> Result<(), String> {
            self.calls.borrow_mut().push("verify".into());
            let copy = p.to_string_lossy().contains(".tmp-");
            if copy {
                if let Some((d, exe)) = &self.data {
                    let step = read_txn(d).unwrap().map(|t| t.step).unwrap_or_default();
                    let prev = prev_of(exe).exists();
                    *self.at_copy.borrow_mut() = Some((step, prev));
                }
                if self.copy_fails {
                    return Err("codesign -v rc 3: the copy changed".into());
                }
            }
            if self.other_signer {
                Err("codesign -v rc 3: test-requirement: code failed to satisfy specified code requirement(s)".into())
            } else {
                Ok(())
            }
        }
    }

    /// Approves what it is shown; verify compares with the approved binding.
    #[derive(Default)]
    struct FakeAp {
        cancel: bool,
        verify_err: bool,
        /// write these bytes to this file while the window is open (a swap after approval)
        tamper: Option<(PathBuf, Vec<u8>)>,
        approved: RefCell<Option<UpdateBinding>>,
        calls: RefCell<Vec<String>>,
    }
    impl Approve for FakeAp {
        fn approve(&self, b: &UpdateBinding, reason: &str) -> Result<String, String> {
            self.calls.borrow_mut().push(format!("approve {reason}"));
            if self.cancel {
                return Err("canceled by the person".into());
            }
            *self.approved.borrow_mut() = Some(b.clone());
            if let Some((p, bytes)) = &self.tamper {
                std::fs::write(p, bytes).unwrap();
            }
            Ok("proof".into())
        }
        fn verify(&self, proof: &str, expect: &UpdateBinding) -> Result<(), String> {
            self.calls.borrow_mut().push("verify".into());
            if self.verify_err {
                return Err("nonce already used".into());
            }
            match &*self.approved.borrow() {
                Some(b) if proof == "proof" && b == expect => Ok(()),
                _ => Err("Mismatch(parameter_digest)".into()),
            }
        }
    }

    #[derive(Default)]
    struct FakeCheck {
        fail: bool,
        /// written to the database when the check runs (a migration the new build did)
        db_write: Option<PathBuf>,
        seen: RefCell<Vec<Vec<u8>>>,
    }
    impl SelfCheck for FakeCheck {
        fn check(&self, exe: &Path) -> Result<(), String> {
            self.seen.borrow_mut().push(std::fs::read(exe).unwrap());
            if let Some(db) = &self.db_write {
                std::fs::write(db, b"new db").unwrap();
            }
            if self.fail {
                Err("selfcheck exited 1".into())
            } else {
                Ok(())
            }
        }
    }

    fn installed(m: &Mac, f: &FakeLd, hl: &FakeHealth) {
        assert_eq!(m.run("install", &["--no-start"], &f.svc(hl)).code, RC_OK);
        f.calls.borrow_mut().clear();
    }
    fn new_file(m: &Mac, ver: &str) -> PathBuf {
        let p = m.p(&format!("new-{ver}/aibd"));
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(&p, build(ver, &format!("build {ver}"))).unwrap();
        p
    }
    fn upd(m: &Mac, file: &Path, svc: &Svc, u: &Upd) -> Out {
        let a = m.args(&["--file", &file.display().to_string()]);
        let a: Vec<&str> = a.iter().map(String::as_str).collect();
        run_update(&a, &m.h, svc, u)
    }
    fn txn(m: &Mac) -> Txn {
        serde_json::from_slice(&std::fs::read(m.p("data/install/update.json")).unwrap()).unwrap()
    }

    #[test]
    fn update_swaps_after_approval_and_keeps_one_prev() {
        let m = Mac::new();
        let f = FakeLd::default();
        let hl = FakeHealth::answering(&[Some("aaaa"), Some("bbbb")]);
        installed(&m, &f, &hl);
        let (cs, ap, ck) = (FakeCs::default(), FakeAp::default(), FakeCheck::default());
        let u = Upd {
            cs: &cs,
            ap: &ap,
            check: &ck,
        };
        let new = new_file(&m, "0.3.0");
        f.prints.borrow_mut().push_back(print_running(10));
        let o = upd(&m, &new, &f.svc(&hl), &u);
        assert_eq!(o.code, RC_OK, "{o:?}");
        assert!(
            o.stdout.contains("UPDATED aibd 0 macos-aarch64 -> 0.3.0"),
            "{}",
            o.stdout
        );
        // the new file before the approval, the copy right before it goes in place (L1)
        assert_eq!(*cs.calls.borrow(), vec!["requirement", "verify", "verify"]);
        assert_eq!(ap.calls.borrow().len(), 2);
        assert!(
            ap.calls.borrow()[0].contains("-> 0.3.0"),
            "{:?}",
            ap.calls.borrow()
        );
        // the new build ran once, after the swap, as the installed file
        assert_eq!(*ck.seen.borrow(), vec![build("0.3.0", "build 0.3.0")]);
        let c = f.calls();
        assert_eq!(c[0], "print gui/501/app.ez2.aibd.unit");
        assert_eq!(c[1], "bootout gui/501/app.ez2.aibd.unit");
        assert!(c[2].starts_with("bootstrap gui/501 "), "{c:?}");
        assert_eq!(
            std::fs::read(m.p("bin/aibd")).unwrap(),
            build("0.3.0", "build 0.3.0")
        );
        assert_eq!(std::fs::read(m.p("bin/aibd.prev")).unwrap(), b"this build");
        assert_eq!(txn(&m).step, "done");
        let rec: Record =
            serde_json::from_slice(&std::fs::read(m.p("data/install/record.json")).unwrap())
                .unwrap();
        assert_eq!(rec.version, "0.3.0");
        // a second update keeps only the build it replaced
        let hl2 = FakeHealth::answering(&[Some("bbbb"), Some("cccc")]);
        let new2 = new_file(&m, "0.3.1");
        f.prints.borrow_mut().push_back(print_running(11));
        let o = upd(&m, &new2, &f.svc(&hl2), &u);
        assert_eq!(o.code, RC_OK, "{o:?}");
        assert!(o.stdout.contains("UPDATED 0.3.0 -> 0.3.1"), "{}", o.stdout);
        assert_eq!(
            std::fs::read(m.p("bin/aibd.prev")).unwrap(),
            build("0.3.0", "build 0.3.0")
        );
        let prevs = std::fs::read_dir(m.p("bin"))
            .unwrap()
            .filter(|e| {
                e.as_ref()
                    .unwrap()
                    .file_name()
                    .to_string_lossy()
                    .contains("prev")
            })
            .count();
        assert_eq!(prevs, 1);
    }

    fn untouched(m: &Mac, f: &FakeLd) {
        assert_eq!(std::fs::read(m.p("bin/aibd")).unwrap(), b"this build");
        assert!(!m.p("bin/aibd.prev").exists());
        assert!(f.calls().is_empty(), "{:?}", f.calls());
    }

    #[test]
    fn a_cancelled_approval_changes_nothing() {
        let m = Mac::new();
        let (f, hl) = (FakeLd::default(), FakeHealth::default());
        installed(&m, &f, &hl);
        let ap = FakeAp {
            cancel: true,
            ..Default::default()
        };
        let (cs, ck) = (FakeCs::default(), FakeCheck::default());
        let o = upd(
            &m,
            &new_file(&m, "0.3.0"),
            &f.svc(&hl),
            &Upd {
                cs: &cs,
                ap: &ap,
                check: &ck,
            },
        );
        assert_eq!(o.code, RC_REFUSED, "{o:?}");
        assert!(o.stderr.contains("not approved: canceled"), "{o:?}");
        untouched(&m, &f);
        assert!(ck.seen.borrow().is_empty());
    }

    #[test]
    fn a_file_changed_after_approval_is_refused() {
        // IFACE r2 knife 1: the expect is computed again from the file, not the first binding
        let m = Mac::new();
        let (f, hl) = (FakeLd::default(), FakeHealth::default());
        installed(&m, &f, &hl);
        let new = new_file(&m, "0.3.0");
        let ap = FakeAp {
            tamper: Some((new.clone(), build("0.3.0", "evil"))),
            ..Default::default()
        };
        let (cs, ck) = (FakeCs::default(), FakeCheck::default());
        let o = upd(
            &m,
            &new,
            &f.svc(&hl),
            &Upd {
                cs: &cs,
                ap: &ap,
                check: &ck,
            },
        );
        assert_eq!(o.code, RC_REFUSED, "{o:?}");
        assert!(o.stderr.contains("approval refused: Mismatch"), "{o:?}");
        untouched(&m, &f);
    }

    #[test]
    fn a_refused_verify_stops_the_update() {
        let m = Mac::new();
        let (f, hl) = (FakeLd::default(), FakeHealth::default());
        installed(&m, &f, &hl);
        let ap = FakeAp {
            verify_err: true,
            ..Default::default()
        };
        let (cs, ck) = (FakeCs::default(), FakeCheck::default());
        let o = upd(
            &m,
            &new_file(&m, "0.3.0"),
            &f.svc(&hl),
            &Upd {
                cs: &cs,
                ap: &ap,
                check: &ck,
            },
        );
        assert_eq!(o.code, RC_REFUSED, "{o:?}");
        assert!(o.stderr.contains("nonce already used"), "{o:?}");
        untouched(&m, &f);
    }

    #[test]
    fn an_ad_hoc_installed_build_is_refused_with_a_reinstall_hint() {
        // 5d33a r2 N1: an ad-hoc DR is only a cdhash, so no update can ever match it
        let m = Mac::new();
        let (f, hl) = (FakeLd::default(), FakeHealth::default());
        installed(&m, &f, &hl);
        let cs = FakeCs {
            adhoc: true,
            ..Default::default()
        };
        let (ap, ck) = (FakeAp::default(), FakeCheck::default());
        let o = upd(
            &m,
            &new_file(&m, "0.3.0"),
            &f.svc(&hl),
            &Upd {
                cs: &cs,
                ap: &ap,
                check: &ck,
            },
        );
        assert_eq!(o.code, RC_REFUSED, "{o:?}");
        assert!(o.stderr.contains("run aibd install again"), "{o:?}");
        assert!(ap.calls.borrow().is_empty());
        untouched(&m, &f);
    }

    #[test]
    fn a_build_from_another_signer_is_refused_before_the_approval() {
        let m = Mac::new();
        let (f, hl) = (FakeLd::default(), FakeHealth::default());
        installed(&m, &f, &hl);
        let cs = FakeCs {
            other_signer: true,
            ..Default::default()
        };
        let (ap, ck) = (FakeAp::default(), FakeCheck::default());
        let o = upd(
            &m,
            &new_file(&m, "0.3.0"),
            &f.svc(&hl),
            &Upd {
                cs: &cs,
                ap: &ap,
                check: &ck,
            },
        );
        assert_eq!(o.code, RC_REFUSED, "{o:?}");
        assert!(
            o.stderr
                .contains("not signed by the installed aibd's signer"),
            "{o:?}"
        );
        assert!(ap.calls.borrow().is_empty());
        untouched(&m, &f);
    }

    #[test]
    fn a_failed_selfcheck_puts_the_old_build_and_database_back() {
        let m = Mac::new();
        let f = FakeLd::default();
        let hl = FakeHealth::answering(&[Some("aaaa"), Some("bbbb"), Some("bbbb"), Some("cccc")]);
        installed(&m, &f, &hl);
        std::fs::create_dir_all(m.p("data")).unwrap();
        std::fs::write(m.p("data/aibd.sqlite"), b"old db").unwrap();
        std::fs::write(m.p("data/aibd.sqlite-wal"), b"old wal").unwrap();
        let ck = FakeCheck {
            fail: true,
            db_write: Some(m.p("data/aibd.sqlite")),
            ..Default::default()
        };
        let (cs, ap) = (FakeCs::default(), FakeAp::default());
        f.prints.borrow_mut().push_back(print_running(10)); // the stop
        f.prints.borrow_mut().push_back(print_running(12)); // the roll back's stop
        let o = upd(
            &m,
            &new_file(&m, "0.3.0"),
            &f.svc(&hl),
            &Upd {
                cs: &cs,
                ap: &ap,
                check: &ck,
            },
        );
        assert_eq!(o.code, RC_REFUSED, "{o:?}");
        assert!(o.stderr.contains("the old build is back"), "{o:?}");
        assert!(o.stdout.contains("RESTORED database"), "{}", o.stdout);
        assert_eq!(std::fs::read(m.p("bin/aibd")).unwrap(), b"this build");
        assert!(!m.p("bin/aibd.prev").exists());
        assert_eq!(std::fs::read(m.p("data/aibd.sqlite")).unwrap(), b"old db");
        assert_eq!(
            std::fs::read(m.p("data/aibd.sqlite-wal")).unwrap(),
            b"old wal"
        );
        assert_eq!(txn(&m).step, "rolled_back");
        let boots = f
            .calls()
            .iter()
            .filter(|c| c.starts_with("bootstrap "))
            .count();
        assert_eq!(boots, 2, "{:?}", f.calls());
    }

    #[test]
    fn a_new_build_that_never_answers_is_rolled_back() {
        // 5d33a r1 L3: KeepAlive would restart a broken build forever
        let m = Mac::new();
        let f = FakeLd::default();
        let hl = FakeHealth::answering(&[Some("aaaa")]);
        installed(&m, &f, &hl);
        let (cs, ap, ck) = (FakeCs::default(), FakeAp::default(), FakeCheck::default());
        f.prints.borrow_mut().push_back(print_running(10));
        f.prints.borrow_mut().push_back(print_running(12));
        let o = upd(
            &m,
            &new_file(&m, "0.3.0"),
            &f.svc(&hl),
            &Upd {
                cs: &cs,
                ap: &ap,
                check: &ck,
            },
        );
        assert_eq!(o.code, RC_REFUSED, "{o:?}");
        assert_eq!(std::fs::read(m.p("bin/aibd")).unwrap(), b"this build");
        assert!(ck.seen.borrow().is_empty());
        let outs = f
            .calls()
            .iter()
            .filter(|c| c.starts_with("bootout "))
            .count();
        assert_eq!(outs, 2, "{:?}", f.calls());
    }

    #[test]
    fn a_half_done_swap_is_undone_by_start() {
        let m = Mac::new();
        let (f, hl) = (
            FakeLd::default(),
            FakeHealth::answering(&[None, Some("dddd")]),
        );
        installed(&m, &f, &hl);
        std::fs::rename(m.p("bin/aibd"), m.p("bin/aibd.prev")).unwrap();
        std::fs::write(m.p("bin/aibd"), b"half new").unwrap();
        let t = Txn {
            v: 1,
            at: rfc3339(0),
            step: "swapped".into(),
            exe: m.p("bin/aibd").display().to_string(),
            old_version: "aibd 0 macos-aarch64".into(),
            new_version: "0.3.0".into(),
            sha256: "x".into(),
            db_backed_up: false,
        };
        write_txn(&m.p("data"), &t).unwrap();
        let o = m.run("start", &[], &f.svc(&hl));
        assert!(o.stdout.contains("UPDATE-UNFINISHED at swapped"), "{o:?}");
        assert_eq!(std::fs::read(m.p("bin/aibd")).unwrap(), b"this build");
        assert_eq!(txn(&m).step, "rolled_back");
    }

    #[test]
    fn the_record_says_swapped_before_the_installed_file_moves() {
        // 5d33a ce253f6a M1: when the installed file is already .prev, the record says swapped
        let m = Mac::new();
        let f = FakeLd::default();
        let hl = FakeHealth::answering(&[Some("aaaa"), Some("bbbb")]);
        installed(&m, &f, &hl);
        let cs = FakeCs {
            data: Some((m.p("data"), m.p("bin/aibd"))),
            ..Default::default()
        };
        let (ap, ck) = (FakeAp::default(), FakeCheck::default());
        f.prints.borrow_mut().push_back(print_running(10));
        let o = upd(
            &m,
            &new_file(&m, "0.3.0"),
            &f.svc(&hl),
            &Upd {
                cs: &cs,
                ap: &ap,
                check: &ck,
            },
        );
        assert_eq!(o.code, RC_OK, "{o:?}");
        assert_eq!(*cs.at_copy.borrow(), Some(("swapped".to_string(), true)));
    }

    #[test]
    fn a_stop_after_the_move_puts_the_old_build_back_on_start() {
        // the installed file moved to .prev and nothing in its place: start puts it back
        let m = Mac::new();
        let (f, hl) = (
            FakeLd::default(),
            FakeHealth::answering(&[None, Some("dddd")]),
        );
        installed(&m, &f, &hl);
        std::fs::rename(m.p("bin/aibd"), m.p("bin/aibd.prev")).unwrap();
        let t = Txn {
            v: 1,
            at: rfc3339(0),
            step: "swapped".into(),
            exe: m.p("bin/aibd").display().to_string(),
            old_version: "aibd 0 macos-aarch64".into(),
            new_version: "0.3.0".into(),
            sha256: "x".into(),
            db_backed_up: false,
        };
        write_txn(&m.p("data"), &t).unwrap();
        let o = m.run("start", &[], &f.svc(&hl));
        assert!(o.stdout.contains("UPDATE-UNFINISHED at swapped"), "{o:?}");
        assert_eq!(std::fs::read(m.p("bin/aibd")).unwrap(), b"this build");
        assert!(!m.p("bin/aibd.prev").exists());
    }

    #[test]
    fn a_copy_that_fails_the_signer_check_is_not_installed() {
        // 5d33a ce253f6a L1
        let m = Mac::new();
        let f = FakeLd::default();
        let hl = FakeHealth::answering(&[Some("aaaa"), Some("bbbb")]);
        installed(&m, &f, &hl);
        let cs = FakeCs {
            copy_fails: true,
            ..Default::default()
        };
        let (ap, ck) = (FakeAp::default(), FakeCheck::default());
        f.prints.borrow_mut().push_back(print_running(10));
        f.prints.borrow_mut().push_back(print_running(12));
        let o = upd(
            &m,
            &new_file(&m, "0.3.0"),
            &f.svc(&hl),
            &Upd {
                cs: &cs,
                ap: &ap,
                check: &ck,
            },
        );
        assert_eq!(o.code, RC_REFUSED, "{o:?}");
        assert!(o.stderr.contains("failed the signer check"), "{o:?}");
        assert_eq!(std::fs::read(m.p("bin/aibd")).unwrap(), b"this build");
        assert!(ck.seen.borrow().is_empty());
        assert_eq!(txn(&m).step, "rolled_back");
    }

    #[test]
    fn a_failure_after_the_stop_starts_the_old_build_again() {
        // 5d33a ce253f6a L2: the database copy fails (a folder where the database file is)
        let m = Mac::new();
        let f = FakeLd::default();
        let hl = FakeHealth::answering(&[Some("aaaa"), Some("bbbb")]);
        installed(&m, &f, &hl);
        std::fs::create_dir_all(m.p("data/aibd.sqlite")).unwrap();
        let (cs, ap, ck) = (FakeCs::default(), FakeAp::default(), FakeCheck::default());
        f.prints.borrow_mut().push_back(print_running(10));
        let o = upd(
            &m,
            &new_file(&m, "0.3.0"),
            &f.svc(&hl),
            &Upd {
                cs: &cs,
                ap: &ap,
                check: &ck,
            },
        );
        assert_eq!(o.code, RC_REFUSED, "{o:?}");
        assert!(
            o.stderr.contains("the old build was started again"),
            "{o:?}"
        );
        let c = f.calls();
        assert!(c.iter().any(|x| x.starts_with("bootout ")), "{c:?}");
        let out_at = c.iter().position(|x| x.starts_with("bootout ")).unwrap();
        assert!(
            c[out_at..].iter().any(|x| x.starts_with("bootstrap ")),
            "{c:?}"
        );
        assert_eq!(std::fs::read(m.p("bin/aibd")).unwrap(), b"this build");
        assert_eq!(txn(&m).step, "rolled_back");
    }

    #[test]
    fn update_off_macos_refuses_no_hw_approval_before_anything() {
        let mut m = Mac::new();
        m.h.os = "windows";
        let (f, hl) = (FakeLd::default(), FakeHealth::default());
        let (cs, ap, ck) = (FakeCs::default(), FakeAp::default(), FakeCheck::default());
        let o = upd(
            &m,
            &m.p("x"),
            &f.svc(&hl),
            &Upd {
                cs: &cs,
                ap: &ap,
                check: &ck,
            },
        );
        assert_eq!(o.code, RC_UNSUPPORTED, "{o:?}");
        assert!(o.stderr.contains("no_hw_approval"), "{o:?}");
        assert!(cs.calls.borrow().is_empty() && ap.calls.borrow().is_empty());
        assert!(f.calls().is_empty());
    }

    #[test]
    fn the_version_is_read_from_the_file_mark() {
        assert_eq!(
            version_in(&build("0.3.0-preview.8", "x")).unwrap(),
            "0.3.0-preview.8"
        );
        assert!(version_in(b"no mark here").is_err());
        let mut two = build("0.3.0", "a");
        two.extend(build("0.4.0", "b"));
        assert!(version_in(&two).unwrap_err().contains("2 different"));
        let mut same = build("0.3.0", "a");
        same.extend(build("0.3.0", "b"));
        assert_eq!(version_in(&same).unwrap(), "0.3.0");
        // a mark with a bad character or no end byte is not a version
        let mut bad = MARK_HEAD.to_vec();
        bad.extend_from_slice(MARK_TAIL);
        bad.extend_from_slice(b"0.3 0\x03");
        assert!(version_in(&bad).is_err());
        // this test binary carries its own build's mark
        let me = std::fs::read(std::env::current_exe().unwrap()).unwrap();
        assert!(VERSION_MARK.len() > MARK_HEAD.len());
        assert_eq!(version_in(&me).unwrap(), env!("CARGO_PKG_VERSION"));
    }

    #[test]
    fn codesign_requirement_output_reads_both_real_shapes() {
        // this Mac, 11-17: /bin/ls and the ad-hoc ~/.ez2-aib/bin/aibd
        assert_eq!(
            parse_requirement("designated => identifier \"com.apple.ls\" and anchor apple\n")
                .unwrap(),
            Some("identifier \"com.apple.ls\" and anchor apple".into())
        );
        assert_eq!(
            parse_requirement(
                "# designated => cdhash H\"b744a20a9a44ae85f9774c8138573e9995423985\"\n"
            )
            .unwrap(),
            None
        );
        assert!(parse_requirement("").is_err());
    }

    #[test]
    fn the_database_is_not_copied_while_aibd_holds_its_lock() {
        let d = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(d.path().join("run")).unwrap();
        let lock = std::fs::File::create(d.path().join("run/aibd.lock")).unwrap();
        lock.try_lock().unwrap();
        assert!(back_up_db(d.path()).unwrap_err().contains("holds its lock"));
        drop(lock);
        std::fs::write(d.path().join("aibd.sqlite"), b"db").unwrap();
        assert!(back_up_db(d.path()).unwrap());
    }

    #[test]
    fn the_swap_checks_the_sha256_once_more() {
        let d = tempfile::tempdir().unwrap();
        let (src, exe) = (d.path().join("new"), d.path().join("aibd"));
        std::fs::write(&src, b"new").unwrap();
        std::fs::write(&exe, b"old").unwrap();
        let ok = |_: &Path| Ok(());
        let e = swap_in(&src, &exe, "0000", &ok).unwrap_err();
        assert!(e.contains("changed after the approval"), "{e}");
        assert_eq!(std::fs::read(&exe).unwrap(), b"old");
        let want = sha256_file(&src).unwrap();
        swap_in(&src, &exe, &want, &ok).unwrap();
        assert_eq!(std::fs::read(&exe).unwrap(), b"new");
        let _ = print_missing();
    }
}
