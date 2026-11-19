//! knife-anchors' second check (7ebca 11-19): a knife's `--expect-fail` name must be a test the
//! command it runs can reach, or the knife can never bite.
//!
//! knife.py counts a bite when a failed test's name contains the `--expect-fail` text. A knife
//! whose command runs a test binary without that test runs nothing that fails, prints MISS, and
//! the miss reads as a weak test instead of a wrong knife: knives-35 PN6 / PN7 ran
//! `--test slots_persist` for a test in `slots.rs`'s unit tests (7ebca 11-19 09:5x, found before
//! the receipt).
//!
//! What is checked, for a command `cargo test -p <package> …`:
//! - `--test <name>`: the functions of `tests/<name>.rs` (or `tests/<name>/main.rs`) and of the
//!   files it pulls in with `#[path = "…"]`;
//! - `--lib`: the functions under `src/`;
//! - neither: the functions under `src/` and `tests/`.
//!
//! The name is found when some `fn` there has the last `::` part of the `--expect-fail` text in
//! its name. A command that is not `cargo test -p`, or names another target (`--bin`, `--tests`,
//! `--doc`, …), is counted as not checked, never guessed at.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

/// Which functions a knife's command can reach.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Target {
    /// `--test <name>`
    Test(String),
    /// `--lib`
    Lib,
    /// the package's `src/` and `tests/`
    All,
}

/// The package and target of `cmd`, or `None` when it is not checked.
pub fn target(cmd: &[String]) -> Option<(String, Target)> {
    if cmd.len() < 2 || cmd[0] != "cargo" || cmd[1] != "test" {
        return None;
    }
    let after = |flag: &str| {
        cmd.iter()
            .position(|a| a == flag)
            .and_then(|i| cmd.get(i + 1))
            .cloned()
    };
    let pkg = after("-p").or_else(|| after("--package"))?;
    let other = cmd.iter().any(|a| {
        matches!(
            a.as_str(),
            "--bins" | "--tests" | "--all-targets" | "--doc" | "--examples" | "--benches"
        ) || a == "--bin"
            || a.starts_with("--bin=")
            || a == "--example"
            || a == "--bench"
            || a.starts_with("--test=")
    });
    if other {
        return None;
    }
    let tests = cmd.iter().filter(|a| *a == "--test").count();
    if tests > 1 {
        return None;
    }
    if tests == 1 {
        return after("--test").map(|n| (pkg, Target::Test(n)));
    }
    if cmd.iter().any(|a| a == "--lib") {
        return Some((pkg, Target::Lib));
    }
    Some((pkg, Target::All))
}

/// The names after each `fn` keyword of `text` (not inside an identifier).
pub fn fn_names(text: &str) -> BTreeSet<String> {
    let b = text.as_bytes();
    let ident = |c: u8| c.is_ascii_alphanumeric() || c == b'_';
    let mut out = BTreeSet::new();
    let mut i = 0;
    while let Some(p) = text[i..].find("fn") {
        let at = i + p;
        i = at + 2;
        if at > 0 && ident(b[at - 1]) {
            continue;
        }
        let mut j = at + 2;
        if j >= b.len() || !b[j].is_ascii_whitespace() {
            continue;
        }
        while j < b.len() && b[j].is_ascii_whitespace() {
            j += 1;
        }
        let start = j;
        while j < b.len() && ident(b[j]) {
            j += 1;
        }
        if j > start {
            out.insert(text[start..j].to_owned());
        }
    }
    out
}

/// Whether some name holds the last `::` part of `expect`.
pub fn has_test(names: &BTreeSet<String>, expect: &str) -> bool {
    let last = expect.rsplit("::").next().unwrap_or(expect);
    !last.is_empty() && names.iter().any(|n| n.contains(last))
}

/// `[package] name` ⇒ its folder, for every `Cargo.toml` at the root, one level down, and in
/// `crates/*` (the workspace's shape).
pub fn packages(root: &Path) -> BTreeMap<String, PathBuf> {
    let mut dirs = vec![root.to_path_buf()];
    for base in [root.to_path_buf(), root.join("crates")] {
        if let Ok(rd) = std::fs::read_dir(&base) {
            dirs.extend(rd.flatten().map(|e| e.path()).filter(|p| p.is_dir()));
        }
    }
    let mut out = BTreeMap::new();
    for d in dirs {
        if let Ok(t) = std::fs::read_to_string(d.join("Cargo.toml")) {
            if let Some(n) = package_name(&t) {
                out.entry(n).or_insert(d);
            }
        }
    }
    out
}

/// The `name` of a manifest's `[package]` table.
pub fn package_name(toml: &str) -> Option<String> {
    let mut in_pkg = false;
    for l in toml.lines() {
        let l = l.trim();
        if l.starts_with('[') {
            in_pkg = l == "[package]";
            continue;
        }
        if !in_pkg {
            continue;
        }
        if let Some(v) = l.strip_prefix("name") {
            let v = v.trim_start().strip_prefix('=')?.trim();
            return v
                .strip_prefix('"')
                .and_then(|v| v.strip_suffix('"'))
                .map(str::to_owned);
        }
    }
    None
}

fn rs_under(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(rd) = std::fs::read_dir(dir) else {
        return;
    };
    let mut paths: Vec<PathBuf> = rd.flatten().map(|e| e.path()).collect();
    paths.sort();
    for p in paths {
        if p.is_dir() {
            rs_under(&p, out);
        } else if p.extension().is_some_and(|e| e == "rs") {
            out.push(p);
        }
    }
}

/// The files whose functions `t` reaches in the package at `dir`; empty when a `--test` file is
/// not there.
pub fn files_of(dir: &Path, t: &Target) -> Vec<PathBuf> {
    let mut out = Vec::new();
    match t {
        Target::Test(n) => {
            let main = [
                dir.join("tests").join(format!("{n}.rs")),
                dir.join("tests").join(n).join("main.rs"),
            ]
            .into_iter()
            .find(|p| p.is_file());
            let Some(main) = main else {
                return out;
            };
            let text = std::fs::read_to_string(&main).unwrap_or_default();
            let base = main.parent().map(Path::to_path_buf).unwrap_or_default();
            out.push(main);
            for rel in path_attrs(&text) {
                out.push(base.join(rel));
            }
        }
        Target::Lib => rs_under(&dir.join("src"), &mut out),
        Target::All => {
            rs_under(&dir.join("src"), &mut out);
            rs_under(&dir.join("tests"), &mut out);
        }
    }
    out
}

/// The `#[path = "…"]` values of a file.
pub fn path_attrs(text: &str) -> Vec<String> {
    text.lines()
        .filter_map(|l| {
            let l = l.trim();
            let rest = l.strip_prefix("#[path")?.trim_start().strip_prefix('=')?;
            let rest = rest.trim_start().strip_prefix('"')?;
            rest.split_once('"').map(|(p, _)| p.to_owned())
        })
        .collect()
}

/// What one knife's check found.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Found {
    Ok,
    /// not a `cargo test -p` of one target
    NotChecked,
    /// the package is not in the workspace
    NoPackage(String),
    /// `--test <name>` has no file
    NoTestFile(String),
    /// no function there holds the name
    NoTest(String),
}

/// Checks knives with a cache of each target's function names.
#[derive(Default)]
pub struct Checker {
    packages: BTreeMap<String, PathBuf>,
    names: BTreeMap<(String, String), Option<BTreeSet<String>>>,
}

impl Checker {
    pub fn new(root: &Path) -> Checker {
        Checker {
            packages: packages(root),
            names: BTreeMap::new(),
        }
    }

    pub fn check(&mut self, cmd: &[String], expect: &str) -> Found {
        let Some((pkg, t)) = target(cmd) else {
            return Found::NotChecked;
        };
        let Some(dir) = self.packages.get(&pkg).cloned() else {
            return Found::NoPackage(pkg);
        };
        let key = (pkg.clone(), format!("{t:?}"));
        let names = self
            .names
            .entry(key)
            .or_insert_with(|| {
                let files = files_of(&dir, &t);
                if files.is_empty() {
                    return None;
                }
                let mut all = BTreeSet::new();
                for f in files {
                    all.extend(fn_names(&std::fs::read_to_string(f).unwrap_or_default()));
                }
                Some(all)
            })
            .clone();
        let shown = match &t {
            Target::Test(n) => format!("{pkg} --test {n}"),
            Target::Lib => format!("{pkg} --lib"),
            Target::All => pkg,
        };
        match names {
            None => Found::NoTestFile(shown),
            Some(n) if has_test(&n, expect) => Found::Ok,
            Some(_) => Found::NoTest(shown),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(s: &str) -> Vec<String> {
        s.split_whitespace().map(str::to_owned).collect()
    }

    #[test]
    fn a_commands_target_is_its_package_and_test_binary() {
        assert_eq!(
            target(&v("cargo test -p aibd --locked --test slots_persist x")),
            Some(("aibd".into(), Target::Test("slots_persist".into())))
        );
        assert_eq!(
            target(&v("cargo test -p aibd --locked --lib x")),
            Some(("aibd".into(), Target::Lib))
        );
        assert_eq!(
            target(&v("cargo test -p aib-xtask --locked x")),
            Some(("aib-xtask".into(), Target::All))
        );
        assert_eq!(target(&v("cargo test -p aibd --bin aibd x")), None);
        assert_eq!(target(&v("cargo test -p aibd --tests x")), None);
        assert_eq!(target(&v("bash scripts/x.sh")), None);
        assert_eq!(
            target(&v("cargo test x")),
            None,
            "reason: no_package_checked"
        );
    }

    #[test]
    fn function_names_and_the_last_part_of_the_expected_name() {
        let n = fn_names("pub fn alpha() {}\n#[test]\nfn the_table_puts_each() {}\nlet affn = 1; fn\n  beta_x() {} // defn gamma");
        assert_eq!(
            n.into_iter().collect::<Vec<_>>(),
            ["alpha", "beta_x", "the_table_puts_each"],
            "reason: fn_names"
        );
        let names: BTreeSet<String> = ["the_table_puts_each_known_kind".to_owned()].into();
        assert!(has_test(&names, "the_table_puts_each_known_kind"));
        assert!(has_test(&names, "slots::tests::the_table_puts"));
        assert!(!has_test(&names, "a_dismissal_and_a_pin"));
        assert!(!has_test(&names, "x::"), "reason: empty_last_part_matched");
    }

    #[test]
    fn a_knife_for_a_unit_test_run_against_an_integration_binary_is_found() {
        let dir = std::env::temp_dir().join(format!("knife-testname-{}", std::process::id()));
        let c = dir.join("crates").join("aibd");
        std::fs::create_dir_all(c.join("src")).unwrap();
        std::fs::create_dir_all(c.join("tests").join("support")).unwrap();
        std::fs::write(
            c.join("Cargo.toml"),
            "[package]\nname = \"aibd\"\n[dependencies]\nname = \"x\"\n",
        )
        .unwrap();
        std::fs::write(
            c.join("src").join("slots.rs"),
            "#[test]\nfn the_table_puts() {}\n",
        )
        .unwrap();
        std::fs::write(
            c.join("tests").join("slots_persist.rs"),
            "#[path = \"support/h.rs\"]\nmod h;\n#[tokio::test]\nasync fn a_dismissal_outlives() {}\n",
        )
        .unwrap();
        std::fs::write(
            c.join("tests").join("support").join("h.rs"),
            "pub fn helper_in_path() {}\n",
        )
        .unwrap();
        let mut k = Checker::new(&dir);
        let run = |t: &str| v(&format!("cargo test -p aibd --locked {t} x"));
        assert_eq!(k.check(&run("--lib"), "the_table_puts"), Found::Ok);
        assert_eq!(
            k.check(&run("--test slots_persist"), "the_table_puts"),
            Found::NoTest("aibd --test slots_persist".into()),
            "reason: wrong_binary_passed"
        );
        assert_eq!(
            k.check(&run("--test slots_persist"), "a_dismissal_outlives"),
            Found::Ok
        );
        assert_eq!(
            k.check(&run("--test slots_persist"), "helper_in_path"),
            Found::Ok,
            "reason: path_attr_not_read"
        );
        assert_eq!(k.check(&run(""), "a_dismissal_outlives"), Found::Ok);
        assert_eq!(
            k.check(&run("--test nope"), "a_dismissal_outlives"),
            Found::NoTestFile("aibd --test nope".into())
        );
        assert_eq!(
            k.check(&v("cargo test -p other --lib x"), "x"),
            Found::NoPackage("other".into())
        );
        assert_eq!(
            k.check(&v("cargo test -p aibd --bin aibd x"), "x"),
            Found::NotChecked
        );
        let _ = std::fs::remove_dir_all(&dir);
    }
}
