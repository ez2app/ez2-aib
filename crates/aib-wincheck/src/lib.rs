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
    /// the test executable's name in the pack's `tests` folder, without `-<hash>.exe`
    pub exe: &'static str,
    /// the test-name filter passed to libtest (`None` = every test of the executable)
    pub filter: Option<&'static str>,
    /// run the #[ignore] tests (`--ignored`)
    pub ignored: bool,
    /// a question the person answers y / n after the step (what only eyes can see)
    pub ask: Option<&'static str>,
}

/// The steps, in order. Changing a step is a change of code and goes through review; nothing
/// is read from a file the person (or an AI on that machine) could edit.
pub const STEPS: &[Step] = &[];

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
