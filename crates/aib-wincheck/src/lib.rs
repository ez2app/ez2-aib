//! aib-wincheck — the Windows hands-on check (PLAN-windows-onsite-90856-1125 §1, §3 A).
//!
//! The laptop it runs on cannot build the workspace (no VS Build Tools), so CI builds the
//! test executables and packs them with this program and a `SHA256SUMS` file. On the laptop
//! a person double-clicks it; it:
//!
//! 1. checks every file in the pack against `SHA256SUMS` before running anything (a file
//!    that is missing, extra or different is red, never skipped);
//! 2. runs each test executable directly (no cargo) and adds up libtest's `test result:`
//!    lines;
//! 3. runs the named steps of [`STEPS`] (one test executable, one filter, `--ignored` or not,
//!    the output kept whole) and asks the person the few questions only eyes can answer;
//! 4. writes one result file of machine lines, the same words a receipt uses, ending with
//!    `WINCHECK-SHA256` over everything above it.
//!
//! Knives are not run here: a knife changes a line and rebuilds, and this machine cannot
//! build. They stay in CI's rust-windows cell.
//!
//! Every child gets a cleared environment with only [`KEPT_ENV`] put back (c2f0b
//! REVIEW-wincheck-gnu-rust M1): a variable from the person's own setup (a rustup mirror,
//! RUSTFLAGS, a cargo home) never reaches what is run.
//!
//! This file is pure (no processes, no files) so it is tested on every OS.

use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::path::Path;

/// Where the pack keeps the source tree and the build, relative to the pack: the same layout
/// as on the CI machine, which built them at [`WINDOWS_ROOT`].
pub const TREE: &str = "aibw/ez2-aib/";

/// Where CI built the test executables, and where they must sit to run: a test executable
/// holds absolute paths of the machine that built it (`CARGO_BIN_EXE_*`, `CARGO_MANIFEST_DIR`
/// fixtures), so it is not moved; the tree is put back where it was built.
pub const WINDOWS_ROOT: &str = r"C:\aibw\ez2-aib";

/// The marker that says a root folder is this program's own: a folder without it is never
/// emptied.
pub const MARK: &str = ".aib-wincheck";

/// Whether the root may be emptied before the tree is put there: `Ok(false)` = there is nothing
/// to empty, `Ok(true)` = it is ours (it holds [`MARK`]), `Err` = someone else's folder, so
/// nothing is written (c2f0b REVIEW-wincheck M1).
pub fn may_clear(exists: bool, marked: bool) -> Result<bool, &'static str> {
    match (exists, marked) {
        (false, _) => Ok(false),
        (true, true) => Ok(true),
        (true, false) => Err("ROOT-NOT-OURS"),
    }
}

/// Whether a file may be run: its bytes, read right before, are the ones its sum names. A file
/// that cannot be read or a name without a sum is never run (c2f0b 16:19 L; REVIEW M1).
pub fn run_allowed(bytes: Option<&[u8]>, name: &str, sums: &BTreeMap<String, String>) -> bool {
    match (bytes, sums.get(name)) {
        (Some(b), Some(w)) => *w == sha256_hex(b),
        _ => false,
    }
}

/// Files found in the root (relative to it, with `/`) that the pack did not bring: anything but
/// [`MARK`], what tests write under `target/tmp/`, and the tree's own files. Windows loads a DLL
/// from the executable's folder first, so one put there by another account would run inside
/// the tests (c2f0b REVIEW L1).
pub fn extras(found: &[String], sums: &BTreeMap<String, String>) -> Vec<String> {
    found
        .iter()
        .filter(|f| {
            f.as_str() != MARK
                && !f.starts_with("target/tmp/")
                && !sums.contains_key(&format!("{TREE}{f}"))
        })
        .cloned()
        .collect()
}

/// The list of test executables, at the pack's root (not inside the tree: on a file system that
/// ignores case, which Windows' and macOS' do, `TESTS` there is the source's `tests` folder).
pub const TESTS_LIST: &str = "wincheck-tests.tsv";

/// One test executable of the pack's [`TESTS_LIST`]: the executable and the package folder it
/// runs in (cargo runs a test in its package's folder, with `CARGO_MANIFEST_DIR` set to it).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TestExe {
    /// relative to the tree, with `/`
    pub exe: String,
    /// relative to the tree, with `/` (`""` = the tree's root)
    pub package: String,
}

/// A path inside the tree: relative, `/`, no `..`, no drive, no empty part.
fn inside(p: &str) -> bool {
    !(p.starts_with('/')
        || p.starts_with('\\')
        || p.contains(':')
        || p.contains('\\')
        || p.split('/').any(|x| x == ".." || x.is_empty()))
}

/// The pack's [`TESTS_LIST`]: one `<exe>\t<package folder>` line per test executable, both
/// relative to the tree. Anything else, a path that leaves the tree, or an executable listed
/// twice is refused.
pub fn parse_tests(text: &str) -> Result<Vec<TestExe>, String> {
    let mut v: Vec<TestExe> = Vec::new();
    for (n, line) in text.lines().enumerate() {
        let line = line.trim_end_matches('\r');
        if line.is_empty() {
            continue;
        }
        let (exe, package) = line
            .split_once('\t')
            .ok_or(format!("TESTS line {}: not '<exe>\\t<package>'", n + 1))?;
        if !inside(exe) || !(package.is_empty() || inside(package)) {
            return Err(format!("TESTS line {}: a path outside the tree", n + 1));
        }
        if v.iter().any(|t| t.exe == exe) {
            return Err(format!("TESTS line {}: {exe} twice", n + 1));
        }
        v.push(TestExe {
            exe: exe.to_string(),
            package: package.to_string(),
        });
    }
    if v.is_empty() {
        return Err("TESTS is empty".into());
    }
    Ok(v)
}

/// The variables a child keeps; everything else is cleared. Windows needs `SystemRoot` to
/// load system DLLs and `TEMP` / `TMP` for temporary files; `USERPROFILE` / `HOME` say whose
/// folders a test may use; `PATH` lets a test find system programs.
pub const KEPT_ENV: &[&str] = &[
    "SystemRoot",
    "SYSTEMROOT",
    "SystemDrive",
    "windir",
    "WINDIR",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOME",
    "LOCALAPPDATA",
    "APPDATA",
    "PATH",
    "Path",
];

/// The pairs of `vars` a child keeps (names compared as written; Windows names are not case
/// sensitive, so both spellings that occur are listed in [`KEPT_ENV`]).
pub fn kept_env<I: IntoIterator<Item = (String, String)>>(vars: I) -> Vec<(String, String)> {
    vars.into_iter()
        .filter(|(k, _)| KEPT_ENV.contains(&k.as_str()))
        .collect()
}

/// One named step: a test executable of the pack run with a filter (c2f0b W-8: the #[ignore]
/// person-checks of aib-sys), its output kept whole in the result file.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Step {
    /// the name in the result file (`STEP-BEGIN <name>`)
    pub name: &'static str,
    /// the test executable, named as cargo names its target (`hello`, `aib_sys`): the file
    /// in the pack's [`TESTS_LIST`] without `-<hash>` and `.exe`
    pub exe: &'static str,
    /// the test-name filter passed to libtest (`None` = every test of the executable)
    pub filter: Option<&'static str>,
    /// run the #[ignore] tests (`--ignored`)
    pub ignored: bool,
    /// a question the person answers y / n after the step (what only eyes can see)
    pub ask: Option<&'static str>,
    /// a sentence shown before the step runs (what the person is about to be asked to do);
    /// with someone there, the step waits for Enter after it
    pub say: Option<&'static str>,
}

/// The steps, in order. Changing a step is a change of code and goes through review; nothing
/// is read from a file the person (or an AI on that machine) could edit.
/// c2f0b W-8 (11-25 16:28): aib-sys' Windows Hello checks, all #[ignore], in the lib test
/// target. W0-1 to W0-3 run in one go and in this order (W0-3 deletes the key W0-1 made).
pub const STEPS: &[Step] = &[
    Step {
        name: "W0-1",
        exe: "aib_sys",
        filter: Some("hello_ncrypt::w0::w0_1_create_a_p256_passport_key"),
        ignored: true,
        ask: None,
        say: None,
    },
    Step {
        name: "W0-2",
        exe: "aib_sys",
        filter: Some("hello_ncrypt::w0::w0_2_the_window_shows_the_reason"),
        ignored: true,
        ask: Some("剛剛的 Windows Hello 窗裡,有沒有看到『W0-2 測試』那行字?"),
        say: Some("等一下 Hello 窗會跳兩次:第一次照常通過,第二次請按取消。"),
    },
    Step {
        name: "W0-3",
        exe: "aib_sys",
        filter: Some("hello_ncrypt::w0::w0_3_cancel_is_cancel"),
        ignored: true,
        ask: None,
        say: Some("下一個 Hello 窗請按取消。"),
    },
    Step {
        name: "W4-M1",
        exe: "aib_sys",
        filter: Some("hello_measure::w4_m1_credui_secure_prompt"),
        ignored: true,
        ask: None,
        say: Some("畫面會變暗,請打 test1234 按確定,不要打你的密碼。"),
    },
    Step {
        name: "W4-M2",
        exe: "aib_sys",
        filter: Some("hello_measure::w4_m2_own_desktop_window"),
        ignored: true,
        ask: None,
        say: Some("會切到另一個畫面,請打 test1234 按 Enter。"),
    },
];

/// The questions about the workbench page asked at the end (G1 §1 step 4).
pub const SCREEN_ASKS: &[&str] = &[
    "工作台「⏱ 節省時間」五格卡片顯示正常,而且「已確認」與「預估」分開標示?",
    "「省時續跑檔次」切換正常(超出上限時有 dropped 提醒)?",
    "外掛區有 aib-skipstop,版本對,而且可以開關?",
];

/// libtest arguments for a step: the filter (exact), `--ignored`, output not captured, one
/// thread (the person-checks open windows one at a time).
pub fn step_args(s: &Step) -> Vec<String> {
    let mut a = Vec::new();
    if let Some(f) = s.filter {
        a.push(f.to_string());
        a.push("--exact".to_string());
    }
    if s.ignored {
        a.push("--ignored".to_string());
    }
    a.push("--nocapture".to_string());
    a.push("--test-threads=1".to_string());
    a
}

/// `SHA256SUMS` as `name → sha256`: lines `<64 hex>  <name>` (or ` *<name>`, binary mode).
/// A line that is not that, a name written twice, or a path that leaves the pack is refused.
pub fn parse_sums(text: &str) -> Result<BTreeMap<String, String>, String> {
    let mut m = BTreeMap::new();
    for (n, line) in text.lines().enumerate() {
        let line = line.trim_end_matches('\r');
        if line.trim().is_empty() {
            continue;
        }
        let (hex, rest) = line
            .split_once(' ')
            .ok_or(format!("SHA256SUMS line {}: no name", n + 1))?;
        let name = rest
            .strip_prefix(" ")
            .or_else(|| rest.strip_prefix("*"))
            .ok_or(format!("SHA256SUMS line {}: not '<sum>  <name>'", n + 1))?;
        if hex.len() != 64 || !hex.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')) {
            return Err(format!("SHA256SUMS line {}: not a sha256", n + 1));
        }
        let leaves = name.is_empty()
            || name.starts_with('/')
            || name.starts_with('\\')
            || name.contains(':')
            || name.split(['/', '\\']).any(|p| p == ".." || p.is_empty());
        if leaves {
            return Err(format!(
                "SHA256SUMS line {}: {name:?} is not a file of the pack",
                n + 1
            ));
        }
        if m.insert(name.replace('\\', "/"), hex.to_string()).is_some() {
            return Err(format!("SHA256SUMS line {}: {name} twice", n + 1));
        }
    }
    if m.is_empty() {
        return Err("SHA256SUMS is empty".into());
    }
    Ok(m)
}

/// sha256 of `bytes`, lower-case hex.
pub fn sha256_hex(bytes: &[u8]) -> String {
    let d = Sha256::digest(bytes);
    let mut s = String::with_capacity(64);
    for b in d {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

/// The pack against its sums: `files` are the pack's files as `(name, sha256)` (`SHA256SUMS`
/// itself left out). Returns one problem per file that is missing, extra or different; an
/// empty list = every file is the one CI built.
pub fn check_pack(sums: &BTreeMap<String, String>, files: &[(String, String)]) -> Vec<String> {
    let mut problems = Vec::new();
    let have: BTreeMap<&str, &str> = files
        .iter()
        .map(|(n, h)| (n.as_str(), h.as_str()))
        .collect();
    for (name, want) in sums {
        match have.get(name.as_str()) {
            None => problems.push(format!("SUMS-MISSING {name}")),
            Some(h) if h != want => problems.push(format!("SUMS-DIFFER {name}")),
            Some(_) => {}
        }
    }
    for (name, _) in files {
        if !sums.contains_key(name) {
            problems.push(format!("SUMS-EXTRA {name}"));
        }
    }
    problems
}

/// What libtest said in one run: the `test result:` lines added up.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Counts {
    pub results: u32,
    pub passed: u64,
    pub failed: u64,
    pub ignored: u64,
}

impl Counts {
    pub fn add(&mut self, o: Counts) {
        self.results += o.results;
        self.passed += o.passed;
        self.failed += o.failed;
        self.ignored += o.ignored;
    }
}

/// The `test result:` lines of `out` added up (`test result: ok. 3 passed; 0 failed; 1 ignored;
/// 0 measured; 0 filtered out; finished in 0.01s`).
pub fn parse_results(out: &str) -> Counts {
    let mut c = Counts::default();
    for line in out.lines() {
        let Some(rest) = line.trim_start().strip_prefix("test result: ") else {
            continue;
        };
        c.results += 1;
        for part in rest.split(';') {
            let mut w = part.split_whitespace().rev();
            let (Some(word), Some(n)) = (w.next(), w.next()) else {
                continue;
            };
            let Ok(n) = n.parse::<u64>() else {
                continue;
            };
            match word {
                "passed" => c.passed += n,
                "failed" => c.failed += n,
                "ignored" => c.ignored += n,
                _ => {}
            }
        }
    }
    c
}

/// The names of the tests that failed (`test a::b ... FAILED`).
pub fn failed_names(out: &str) -> Vec<String> {
    out.lines()
        .filter_map(|l| {
            l.trim_end()
                .strip_prefix("test ")?
                .strip_suffix(" ... FAILED")
                .map(str::to_owned)
        })
        .collect()
}

/// A test executable's suite name: `aibd-0123abcd.exe` / `tree-0123abcd.exe` → `aibd` /
/// `tree` (cargo's `-<16 hex>` dropped).
pub fn suite_name(file: &str) -> &str {
    let stem = file.strip_suffix(".exe").unwrap_or(file);
    match stem.rsplit_once('-') {
        Some((s, h)) if h.len() == 16 && h.bytes().all(|b| b.is_ascii_hexdigit()) => s,
        _ => stem,
    }
}

/// The result file: lines kept in order; [`Report::finish`] adds the verdict and the sha256
/// of everything above it.
#[derive(Debug, Default)]
pub struct Report {
    lines: Vec<String>,
    red: Vec<String>,
}

impl Report {
    pub fn line(&mut self, l: impl Into<String>) {
        self.lines.push(l.into());
    }
    /// A line that makes the check red.
    pub fn red(&mut self, l: impl Into<String>) {
        let l = l.into();
        self.red.push(l.clone());
        self.lines.push(l);
    }
    pub fn is_red(&self) -> bool {
        !self.red.is_empty()
    }
    pub fn lines(&self) -> &[String] {
        &self.lines
    }
    /// The whole file: `WINCHECK-OK` or `WINCHECK-RED <n>`, then `WINCHECK-SHA256 <sum of the
    /// text above>`.
    pub fn finish(&self) -> String {
        let mut t = String::new();
        for l in &self.lines {
            t.push_str(l);
            t.push('\n');
        }
        if self.red.is_empty() {
            t.push_str("WINCHECK-OK\n");
        } else {
            t.push_str(&format!("WINCHECK-RED {}\n", self.red.len()));
        }
        let sum = sha256_hex(t.as_bytes());
        t.push_str(&format!("WINCHECK-SHA256 {sum}\n"));
        t
    }
}

/// How the folders the tree goes into are made one account's alone: `lock` right after a folder
/// is made (Windows: icacls, inheritance off, only this account), `check` on a folder that is
/// already there and before every run (Windows: icacls lists only this account). Both are no-ops
/// where there is nothing to set (a Mac try). f163c REVIEW-wincheck L2.
pub struct Guard<'a> {
    pub lock: &'a dyn Fn(&Path) -> Result<(), String>,
    pub check: &'a dyn Fn(&Path) -> Result<(), String>,
}

/// `file` is still the one its sum names, read again right before it is used.
pub fn still_same(file: &Path, name: &str, sums: &BTreeMap<String, String>) -> bool {
    run_allowed(std::fs::read(file).ok().as_deref(), name, sums)
}

/// The files under `dir` (all levels when `deep`), relative to `root` with `/`.
pub fn files_in(root: &Path, dir: &Path, deep: bool, out: &mut Vec<String>) -> std::io::Result<()> {
    for e in std::fs::read_dir(dir)? {
        let e = e?;
        if e.file_type()?.is_dir() {
            if deep {
                files_in(root, &e.path(), deep, out)?;
            }
        } else if let Ok(rel) = e.path().strip_prefix(root) {
            out.push(rel.to_string_lossy().replace('\\', "/"));
        }
    }
    Ok(())
}

/// Put the pack's tree at `root` and check every file written against its sum. The folder above
/// the root is made this account's (or, when it is there, must already be); the root is emptied
/// only when it holds [`MARK`], then made anew (never one someone else made in between) and
/// locked before anything is written; afterwards nothing the pack did not bring may be in it.
/// Returns the problems; empty = the tree is in place.
pub fn place_tree(
    pack: &Path,
    root: &Path,
    sums: &BTreeMap<String, String>,
    g: &Guard,
) -> Vec<String> {
    if let Some(base) = root.parent() {
        let made = if base.exists() {
            (g.check)(base)
                .map_err(|e| format!("ROOT-NOT-OURS {}: {e}", base.display()))
                .and_then(|()| {
                    // taken over as well: its owner could change the rights at any time
                    (g.lock)(base).map_err(|e| format!("ROOT-ACL {}: {e}", base.display()))
                })
        } else {
            std::fs::create_dir_all(base)
                .map_err(|e| format!("ROOT-MAKE {}: {e}", base.display()))
                .and_then(|()| {
                    (g.lock)(base).map_err(|e| format!("ROOT-ACL {}: {e}", base.display()))
                })
        };
        if let Err(e) = made {
            return vec![e];
        }
    }
    match may_clear(root.exists(), root.join(MARK).is_file()) {
        Err(why) => {
            return vec![format!(
                "{why} {} is there and was not made by aib-wincheck; nothing is written",
                root.display()
            )]
        }
        Ok(true) => {
            if let Err(e) = std::fs::remove_dir_all(root) {
                return vec![format!("ROOT-CLEAR {}: {e}", root.display())];
            }
        }
        Ok(false) => {}
    }
    // create_dir, not create_dir_all: a root someone made after the clear is refused
    if let Err(e) = std::fs::create_dir(root) {
        return vec![format!("ROOT-MAKE {}: {e}", root.display())];
    }
    if let Err(e) = (g.lock)(root) {
        return vec![format!("ROOT-ACL {}: {e}", root.display())];
    }
    if let Err(e) = std::fs::write(root.join(MARK), "") {
        return vec![format!("ROOT-MAKE {}: {e}", root.join(MARK).display())];
    }
    let mut problems = Vec::new();
    for name in sums.keys() {
        let Some(rel) = name.strip_prefix(TREE) else {
            continue;
        };
        let to = root.join(rel);
        let copied = to
            .parent()
            .map_or(Ok(()), std::fs::create_dir_all)
            .and_then(|()| std::fs::copy(pack.join(name), &to));
        if let Err(e) = copied {
            problems.push(format!("ROOT-COPY {rel}: {e}"));
        } else if !still_same(&to, name, sums) {
            problems.push(format!("SUMS-CHANGED {rel} (differs once copied)"));
        }
    }
    // env!("CARGO_TARGET_TMPDIR") of integration tests
    if let Err(e) = std::fs::create_dir_all(root.join("target").join("tmp")) {
        problems.push(format!("ROOT-MAKE target/tmp: {e}"));
    }
    // nothing else got in while the tree was written (c2f0b REVIEW L1)
    let mut found = Vec::new();
    match files_in(root, root, true, &mut found) {
        Ok(()) => problems.extend(
            extras(&found, sums)
                .into_iter()
                .map(|f| format!("ROOT-EXTRA {f}")),
        ),
        Err(e) => problems.push(format!("ROOT-LIST {}: {e}", root.display())),
    }
    problems
}

/// Whether the test executable `exe` (relative to `root`) may run now: the root is still this
/// account's alone, the executable is still the one in the sums, and its folder (where Windows
/// looks for a DLL first) holds nothing the pack did not bring. `Err` = why not.
pub fn may_run(
    root: &Path,
    exe: &str,
    sums: &BTreeMap<String, String>,
    g: &Guard,
) -> Result<(), Vec<String>> {
    if let Err(e) = (g.check)(root) {
        return Err(vec![format!("ROOT-ACL {}: {e}", root.display())]);
    }
    if !still_same(&root.join(exe), &format!("{TREE}{exe}"), sums) {
        return Err(vec![format!(
            "SUMS-CHANGED {exe} (changed after the check)"
        )]);
    }
    let mut found = Vec::new();
    let listed = root
        .join(exe)
        .parent()
        .map(|d| files_in(root, d, false, &mut found));
    if !matches!(listed, Some(Ok(()))) {
        return Err(vec![format!(
            "ROOT-EXTRA {exe} (its folder cannot be listed)"
        )]);
    }
    let beside = extras(&found, sums);
    if beside.is_empty() {
        Ok(())
    } else {
        Err(beside
            .into_iter()
            .map(|f| format!("ROOT-EXTRA {f} (beside {exe})"))
            .collect())
    }
}

/// `whoami /user /fo csv /nh`: `"<domain>\<name>","<SID>"` ⇒ (name, SID).
pub fn whoami_user(out: &str) -> Option<(String, String)> {
    let line = out.lines().map(str::trim).find(|l| !l.is_empty())?;
    let (name, sid) = line
        .strip_prefix('"')?
        .strip_suffix('"')?
        .split_once("\",\"")?;
    (!name.is_empty() && sid.starts_with("S-1-") && !sid.contains('"'))
        .then(|| (name.to_string(), sid.to_string()))
}

/// icacls arguments that make `path` this account's alone: inherited entries removed, every
/// explicit entry replaced by full control for the SID, passed down to what is inside.
pub fn icacls_lock_args(path: &str, sid: &str) -> Vec<String> {
    vec![
        path.to_string(),
        "/inheritance:r".to_string(),
        "/grant:r".to_string(),
        format!("*{sid}:(OI)(CI)F"),
    ]
}

/// icacls arguments that make this account's SID the owner of `path` (c2f0b REVIEW f0c0df9a L1:
/// an owner can change the rights at any time, so a folder that was already there is taken over;
/// full control includes the right to do so, and when it fails the check is red).
pub fn icacls_owner_args(path: &str, sid: &str) -> Vec<String> {
    vec![path.to_string(), "/setowner".to_string(), format!("*{sid}")]
}

/// What `icacls <path> /save <file>` wrote, as text: UTF-16LE (how icacls writes it), else UTF-8.
pub fn decode_saved(b: &[u8]) -> String {
    let b = b.strip_prefix(&[0xFF, 0xFE]).unwrap_or(b);
    if b.len() >= 2 && b.len().is_multiple_of(2) && b[1] == 0 {
        let u: Vec<u16> = b
            .as_chunks::<2>()
            .0
            .iter()
            .map(|c| u16::from_le_bytes(*c))
            .collect();
        String::from_utf16_lossy(&u)
    } else {
        String::from_utf8_lossy(b).into_owned()
    }
}

/// The access list `icacls /save` wrote (SDDL, `D:<flags>(<ace>)…`) is this account's alone: not
/// inheriting (`P`), and every entry an allow entry (`A`) for `me`'s SID, one of them full
/// control (`FA`). SIDs are compared, not names: icacls prints names in the console code page,
/// where two non-ASCII names can read the same (c2f0b REVIEW f0c0df9a L2); a deny entry is never
/// taken for full control (N1).
pub fn sddl_only(saved: &str, me: &str) -> Result<(), String> {
    let d = saved
        .lines()
        .map(|l| l.trim_matches(|c: char| c == '\r' || c == '\0' || c == '\u{feff}' || c == ' '))
        .find_map(|l| l.strip_prefix("D:"))
        .ok_or("icacls /save wrote no access list")?;
    let (flags, aces) = d.split_at(d.find('(').unwrap_or(d.len()));
    if !flags.contains('P') {
        return Err(format!("the folder still inherits rights (D:{flags})"));
    }
    let mut full = false;
    let mut n = 0;
    for ace in aces.split('(').filter(|a| !a.is_empty()) {
        let ace = ace
            .strip_suffix(')')
            .ok_or_else(|| format!("cannot read entry ({ace}"))?;
        let f: Vec<&str> = ace.split(';').collect();
        let [kind, _, rights, _, _, sid] = f[..] else {
            return Err(format!("cannot read entry ({ace})"));
        };
        if kind != "A" {
            return Err(format!("an entry that is not an allow entry: ({ace})"));
        }
        if !sid.eq_ignore_ascii_case(me) {
            return Err(format!("another account has rights: ({ace})"));
        }
        full |= rights == "FA";
        n += 1;
    }
    if n == 0 || !full {
        return Err(format!("{me} has no full control: D:{d}"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const H: &str = "9a61d753ae5a857586020a1956d07a241fed9283007fe3f7d34255f1f22ca4b9";

    #[test]
    fn a_child_keeps_only_the_listed_variables() {
        let env = [
            ("SystemRoot", "C:\\Windows"),
            ("TEMP", "C:\\t"),
            ("PATH", "C:\\Windows\\system32"),
            // c2f0b M1: these would make rustup or cargo fetch or build from somewhere else
            ("RUSTUP_DIST_SERVER", "https://mirror.example"),
            ("RUSTUP_UPDATE_ROOT", "https://mirror.example/rustup"),
            ("RUSTFLAGS", "-C linker=evil"),
            ("CARGO_HOME", "C:\\x"),
            ("RUSTC_WRAPPER", "evil.exe"),
            ("AIB_SKIPSTOP_MODE", "real"),
        ]
        .map(|(k, v)| (k.to_string(), v.to_string()));
        let kept: Vec<String> = kept_env(env).into_iter().map(|(k, _)| k).collect();
        assert_eq!(kept, ["SystemRoot", "TEMP", "PATH"]);
    }

    #[test]
    fn a_step_passes_its_filter_exactly_and_runs_ignored_tests_only_when_asked() {
        let s = Step {
            name: "W0-1",
            exe: "held",
            filter: Some("w0_1_a_window_opens"),
            ignored: true,
            ask: None,
            say: None,
        };
        assert_eq!(
            step_args(&s),
            [
                "w0_1_a_window_opens",
                "--exact",
                "--ignored",
                "--nocapture",
                "--test-threads=1"
            ]
        );
        let all = Step {
            filter: None,
            ignored: false,
            ..s
        };
        assert_eq!(step_args(&all), ["--nocapture", "--test-threads=1"]);
    }

    /// A folder of its own under the temp folder, emptied when the test ends.
    struct Tmp(std::path::PathBuf);
    impl Tmp {
        fn new(name: &str) -> Tmp {
            let d =
                std::env::temp_dir().join(format!("aib-wincheck-{}-{name}", std::process::id()));
            let _ = std::fs::remove_dir_all(&d);
            std::fs::create_dir_all(&d).unwrap();
            Tmp(d)
        }
    }
    impl Drop for Tmp {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    const OPEN: Guard = Guard {
        lock: &|_| Ok(()),
        check: &|_| Ok(()),
    };

    /// A pack with one test executable, and its sums.
    fn pack(t: &Tmp) -> (std::path::PathBuf, BTreeMap<String, String>) {
        let p = t.0.join("pack");
        let exe = "target/debug/deps/title-0123456789abcdef";
        std::fs::create_dir_all(p.join(TREE).join("target/debug/deps")).unwrap();
        std::fs::write(p.join(TREE).join(exe), b"MZ test").unwrap();
        let sums = BTreeMap::from([(format!("{TREE}{exe}"), sha256_hex(b"MZ test"))]);
        (p, sums)
    }

    #[test]
    fn a_root_someone_else_made_is_left_as_it_was() {
        let t = Tmp::new("notours");
        let (p, sums) = pack(&t);
        let root = t.0.join("aibw").join("ez2-aib");
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join("theirs.txt"), b"keep").unwrap();
        let problems = place_tree(&p, &root, &sums, &OPEN);
        assert_eq!(problems.len(), 1, "{problems:?}");
        assert!(problems[0].starts_with("ROOT-NOT-OURS"), "{problems:?}");
        let mut left = Vec::new();
        files_in(&root, &root, true, &mut left).unwrap();
        assert_eq!(left, ["theirs.txt"]);
    }

    #[test]
    fn a_placed_executable_changed_or_given_a_neighbour_is_not_run() {
        let t = Tmp::new("placed");
        let (p, sums) = pack(&t);
        let root = t.0.join("aibw").join("ez2-aib");
        assert!(place_tree(&p, &root, &sums, &OPEN).is_empty());
        let exe = "target/debug/deps/title-0123456789abcdef";
        assert_eq!(may_run(&root, exe, &sums, &OPEN), Ok(()));
        // a second run empties its own root and places it again
        assert!(place_tree(&p, &root, &sums, &OPEN).is_empty());
        std::fs::write(root.join("target/debug/deps/version.dll"), b"x").unwrap();
        let why = may_run(&root, exe, &sums, &OPEN).unwrap_err();
        assert!(
            why[0].starts_with("ROOT-EXTRA target/debug/deps/version.dll"),
            "{why:?}"
        );
        std::fs::remove_file(root.join("target/debug/deps/version.dll")).unwrap();
        std::fs::write(root.join(exe), b"MZ tesT").unwrap();
        let why = may_run(&root, exe, &sums, &OPEN).unwrap_err();
        assert!(why[0].starts_with("SUMS-CHANGED"), "{why:?}");
    }

    #[test]
    fn a_folder_that_cannot_be_made_ours_stops_everything() {
        let t = Tmp::new("acl");
        let (p, sums) = pack(&t);
        let root = t.0.join("aibw").join("ez2-aib");
        let refused = Guard {
            lock: &|_| Err("icacls rc=Some(5)".into()),
            check: &|_| Err("others have rights".into()),
        };
        let problems = place_tree(&p, &root, &sums, &refused);
        assert!(problems[0].starts_with("ROOT-ACL"), "{problems:?}");
        assert!(!root.exists());
        // the folder above is there now but not ours
        let problems = place_tree(&p, &root, &sums, &refused);
        assert!(problems[0].starts_with("ROOT-NOT-OURS"), "{problems:?}");
        assert!(place_tree(&p, &root, &sums, &OPEN).is_empty());
        let why = may_run(
            &root,
            "target/debug/deps/title-0123456789abcdef",
            &sums,
            &refused,
        );
        assert!(why.unwrap_err()[0].starts_with("ROOT-ACL"));
    }

    #[test]
    fn whoami_and_icacls_are_read_strictly() {
        assert_eq!(
            whoami_user("\"desktop-7q\\andy\",\"S-1-5-21-1-2-3-1001\"\r\n"),
            Some(("desktop-7q\\andy".into(), "S-1-5-21-1-2-3-1001".into()))
        );
        for bad in [
            "",
            "desktop\\andy S-1-5",
            "\"a\",\"x\"",
            "\"\",\"S-1-5-21\"",
        ] {
            assert_eq!(whoami_user(bad), None, "{bad:?}");
        }
        assert_eq!(
            icacls_lock_args(r"C:\aibw", "S-1-5-21-9"),
            [
                r"C:\aibw",
                "/inheritance:r",
                "/grant:r",
                "*S-1-5-21-9:(OI)(CI)F"
            ]
        );
        assert_eq!(
            icacls_owner_args(r"C:\aibw", "S-1-5-21-9"),
            [r"C:\aibw", "/setowner", "*S-1-5-21-9"]
        );
        let me = "S-1-5-21-1-2-3-1001";
        // as icacls /save writes it: UTF-16LE, the folder's name, then its SDDL
        let saved: Vec<u8> = "aibw\r\nD:PAI(A;OICI;FA;;;S-1-5-21-1-2-3-1001)\r\n"
            .encode_utf16()
            .flat_map(u16::to_le_bytes)
            .collect();
        assert_eq!(sddl_only(&decode_saved(&saved), me), Ok(()));
        // a name that is not ASCII does not matter: the SID is compared
        let zh = "測試 專案\nD:PAI(A;OICI;FA;;;S-1-5-21-1-2-3-1001)\n";
        assert_eq!(sddl_only(&decode_saved(zh.as_bytes()), me), Ok(()));
        for (bad, why) in [
            ("aibw\nD:AI(A;OICI;FA;;;S-1-5-21-1-2-3-1001)\n", "inherits"),
            (
                "aibw\nD:PAI(A;OICI;FA;;;S-1-5-21-1-2-3-1001)(A;OICIIO;0x1301bf;;;AU)\n",
                "another account",
            ),
            (
                "aibw\nD:PAI(D;OICI;FA;;;S-1-5-21-1-2-3-1001)\n",
                "not an allow",
            ),
            (
                "aibw\nD:PAI(A;OICI;0x1200a9;;;S-1-5-21-1-2-3-1001)\n",
                "no full control",
            ),
            (
                "aibw\nD:PAI(A;OICI;FA;;S-1-5-21-1-2-3-1001)\n",
                "cannot read",
            ),
            ("aibw\n", "no access list"),
            ("aibw\nD:P\n", "no full control"),
        ] {
            let e = sddl_only(bad, me).unwrap_err();
            assert!(e.contains(why), "{bad:?}: {e}");
        }
    }

    #[test]
    fn a_root_is_emptied_only_when_it_is_ours() {
        assert_eq!(may_clear(false, false), Ok(false));
        assert_eq!(may_clear(false, true), Ok(false));
        assert_eq!(may_clear(true, true), Ok(true));
        assert_eq!(may_clear(true, false), Err("ROOT-NOT-OURS"));
    }

    #[test]
    fn an_executable_runs_only_while_it_is_the_one_in_the_sums() {
        let exe = b"MZ test executable".as_slice();
        let sums = BTreeMap::from([(
            "aibw/ez2-aib/target/debug/deps/title-0123456789abcdef.exe".to_string(),
            sha256_hex(exe),
        )]);
        let name = "aibw/ez2-aib/target/debug/deps/title-0123456789abcdef.exe";
        assert!(run_allowed(Some(exe), name, &sums));
        let mut changed = exe.to_vec();
        changed[0] ^= 1;
        assert!(!run_allowed(Some(&changed), name, &sums));
        assert!(!run_allowed(None, name, &sums));
        assert!(!run_allowed(Some(exe), "aibw/ez2-aib/other.exe", &sums));
    }

    #[test]
    fn a_file_in_the_root_the_pack_did_not_bring_is_found() {
        let sums = BTreeMap::from([(
            "aibw/ez2-aib/target/debug/deps/title-0123456789abcdef.exe".to_string(),
            H.to_string(),
        )]);
        let found: Vec<String> = [
            MARK,
            "target/tmp/a/b.txt",
            "target/debug/deps/title-0123456789abcdef.exe",
            "target/debug/deps/version.dll",
        ]
        .map(String::from)
        .to_vec();
        assert_eq!(extras(&found, &sums), ["target/debug/deps/version.dll"]);
        assert!(extras(&found[..3], &sums).is_empty());
    }

    #[test]
    fn the_steps_have_unique_names_and_w0_runs_in_order() {
        let names: Vec<_> = STEPS.iter().map(|s| s.name).collect();
        let mut u = names.clone();
        u.sort_unstable();
        u.dedup();
        assert_eq!(u.len(), names.len());
        let w0: Vec<_> = names.iter().filter(|n| n.starts_with("W0-")).collect();
        assert_eq!(w0, [&"W0-1", &"W0-2", &"W0-3"]);
        assert!(STEPS.iter().all(|s| s.filter.is_some() && s.ignored));
    }

    #[test]
    fn the_sums_file_is_read_strictly() {
        let m = parse_sums(&format!(
            "{H}  aib-wincheck.exe\r\n{H} *tests/tree-0123456789abcdef.exe\n\n"
        ))
        .unwrap();
        assert_eq!(
            m.keys().collect::<Vec<_>>(),
            ["aib-wincheck.exe", "tests/tree-0123456789abcdef.exe"]
        );
        for bad in [
            String::new(),
            format!("{H} aib.exe"),
            format!("{}  aib.exe", &H[..63]),
            format!("{}  aib.exe", H.to_uppercase()),
            format!("{H}  ../aib.exe"),
            format!("{H}  C:\\aib.exe"),
            format!("{H}  /aib.exe"),
            format!("{H}  tests//a.exe"),
            format!("{H}  a.exe\n{H}  a.exe"),
        ] {
            assert!(parse_sums(&bad).is_err(), "{bad:?}");
        }
    }

    #[test]
    fn every_file_of_the_pack_must_be_the_one_in_the_sums() {
        let sums = parse_sums(&format!("{H}  a.exe\n{H}  tests/b.exe\n")).unwrap();
        let ok = [("a.exe", H), ("tests/b.exe", H)].map(|(n, h)| (n.to_string(), h.to_string()));
        assert!(check_pack(&sums, &ok).is_empty());
        let other = "0".repeat(64);
        let bad = [("a.exe", other.as_str()), ("tests/c.exe", H)]
            .map(|(n, h)| (n.to_string(), h.to_string()));
        assert_eq!(
            check_pack(&sums, &bad),
            [
                "SUMS-DIFFER a.exe",
                "SUMS-MISSING tests/b.exe",
                "SUMS-EXTRA tests/c.exe"
            ]
        );
    }

    #[test]
    fn libtest_results_are_added_up_and_failed_names_read() {
        let out = "running 3 tests\n\
                   test a::one ... ok\n\
                   test a::two ... FAILED\n\
                   test a::three ... ignored\n\
                   test result: FAILED. 1 passed; 1 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.01s\n\
                   \n\
                   test result: ok. 4 passed; 0 failed; 0 ignored; 0 measured; 2 filtered out; finished in 0.00s\n";
        assert_eq!(
            parse_results(out),
            Counts {
                results: 2,
                passed: 5,
                failed: 1,
                ignored: 1
            }
        );
        assert_eq!(failed_names(out), ["a::two"]);
        assert_eq!(parse_results("no tests here"), Counts::default());
    }

    #[test]
    fn the_tests_list_names_executables_and_package_folders_inside_the_tree() {
        let t = parse_tests(
            "target/debug/deps/tree-0123456789abcdef.exe\tcrates/aibd\r\n\
             target/debug/deps/aib_xtask-0123456789abcdef.exe\txtask\n",
        )
        .unwrap();
        assert_eq!(t.len(), 2);
        assert_eq!(t[0].package, "crates/aibd");
        assert_eq!(
            parse_tests("target/debug/deps/a.exe\t").unwrap()[0].package,
            ""
        );
        for bad in [
            "",
            "target/debug/deps/a.exe",
            "../a.exe\tcrates/x",
            "target/a.exe\t../x",
            "C:/a.exe\tcrates/x",
            "target\\a.exe\tcrates/x",
            "/a.exe\tx",
            "a.exe\tx\na.exe\ty",
        ] {
            assert!(parse_tests(bad).is_err(), "{bad:?}");
        }
    }

    #[test]
    fn a_suite_is_named_without_cargos_hash() {
        assert_eq!(suite_name("tree-0123456789abcdef.exe"), "tree");
        assert_eq!(suite_name("aib_sys-0123456789abcdef.exe"), "aib_sys");
        assert_eq!(suite_name("a-b-0123456789abcdef"), "a-b");
        assert_eq!(suite_name("plain.exe"), "plain");
        assert_eq!(suite_name("x-0123.exe"), "x-0123");
    }

    #[test]
    fn the_result_file_ends_with_the_verdict_and_the_sum_of_what_is_above() {
        let mut r = Report::default();
        r.line("HEAD 0123");
        let t = r.finish();
        assert!(t.ends_with(&format!(
            "WINCHECK-OK\nWINCHECK-SHA256 {}\n",
            sha256_hex(b"HEAD 0123\nWINCHECK-OK\n")
        )));
        r.red("TEST-RED a::two");
        assert!(r.is_red());
        assert!(r.finish().contains("\nWINCHECK-RED 1\n"));
        assert_eq!(
            sha256_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
    }
}
