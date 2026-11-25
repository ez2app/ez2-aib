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

/// Where the pack keeps the source tree and the build, relative to the pack: the same layout
/// as on the CI machine, which built them at [`WINDOWS_ROOT`].
pub const TREE: &str = "aibw/ez2-aib/";

/// Where CI built the test executables, and where they must sit to run: a test executable
/// holds absolute paths of the machine that built it (`CARGO_BIN_EXE_*`, `CARGO_MANIFEST_DIR`
/// fixtures), so it is not moved; the tree is put back where it was built.
pub const WINDOWS_ROOT: &str = r"C:\aibw\ez2-aib";

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
        say: None,
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
