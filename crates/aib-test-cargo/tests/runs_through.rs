//! The "runs through" acceptance as a test (NOWAIT-519d0-1117-0300 043dd 2): one cargo unit
//! per package, keyed by the core from the files on disk. Run twice and the second run reuses
//! every unit; change one file and only that package runs again; change a file every
//! package reads, or the toolchain, and all of them run again.
//!
//! The ledger here is the smallest stand-in (unit → key of its last passing run); the real
//! ledger and its source checks are the 帳 part (IFACE-drive-shadow §1). Each package's
//! inputs are given by the caller: its own folder plus the workspace files; reading the
//! dependency closure from `cargo metadata` comes in the next slice.

use aib_test_core::context::Context;
use aib_test_core::key::{self, FileCache, Hex};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

struct Tmp(PathBuf);
impl Drop for Tmp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn workspace(name: &str) -> Tmp {
    let d = std::env::temp_dir().join(format!("aib-test-cargo-{name}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&d);
    for p in ["crates/a/src", "crates/b/src"] {
        fs::create_dir_all(d.join(p)).unwrap();
    }
    let files = [
        (
            "Cargo.toml",
            "[workspace]\nmembers = [\"crates/a\", \"crates/b\"]\n",
        ),
        ("Cargo.lock", "version = 4\n"),
        ("crates/a/Cargo.toml", "[package]\nname = \"a\"\n"),
        ("crates/a/src/lib.rs", "pub fn a() {}\n"),
        ("crates/b/Cargo.toml", "[package]\nname = \"b\"\n"),
        ("crates/b/src/lib.rs", "pub fn b() {}\n"),
        (".gitignore", "target/\n"),
    ];
    for (p, c) in files {
        fs::write(d.join(p), c).unwrap();
    }
    let ok = Command::new("git")
        .arg("-C")
        .arg(&d)
        .args(["init", "-q"])
        .status()
        .unwrap()
        .success();
    assert!(ok);
    Tmp(d)
}

/// The two packages, each with the folders it reads.
fn units() -> Vec<(&'static str, Vec<String>)> {
    let shared = ["Cargo.toml", "Cargo.lock"];
    ["a", "b"]
        .into_iter()
        .map(|n| {
            let mut p: Vec<String> = shared.iter().map(|s| s.to_string()).collect();
            p.push(format!("crates/{n}"));
            (n, p)
        })
        .collect()
}

fn ctx(toolchain: &str) -> Context {
    Context {
        machine_kind: "mac-arm".into(),
        toolchain: toolchain.into(),
        vars: Vec::new(),
        target_dir: "target".into(),
        target_shared: false,
    }
}

/// One `aib test` round: which units run (key not in the ledger) and which are reused.
/// Every unit that runs passes here, so its key goes into the ledger.
fn round(
    root: &Path,
    c: &Context,
    ledger: &mut BTreeMap<String, Hex>,
    cache: &mut FileCache,
) -> (Vec<String>, Vec<String>) {
    let all = key::wide_paths(root).unwrap();
    let context = c.hash(&[]).unwrap();
    let (mut ran, mut reused) = (Vec::new(), Vec::new());
    for (name, prefixes) in units() {
        let paths = key::declared(&all, &prefixes).unwrap();
        let inputs = key::input_list(root, &paths, cache).unwrap();
        let unit_def = format!("cargo test -p {name}");
        let k = key::key(&unit_def, &context, &inputs);
        if ledger.get(name) == Some(&k) {
            reused.push(name.to_string());
        } else {
            ran.push(name.to_string());
            ledger.insert(name.to_string(), k);
        }
    }
    (ran, reused)
}

#[test]
fn the_second_run_reuses_everything_and_one_edit_reruns_only_its_package() {
    let w = workspace("through");
    let c = ctx("rustc 1.99.0\ncargo 1.99.0\n");
    let mut ledger = BTreeMap::new();
    let mut cache = FileCache::default();

    let (ran, reused) = round(&w.0, &c, &mut ledger, &mut cache);
    assert_eq!((ran, reused), (vec!["a".into(), "b".into()], vec![]));

    let (ran, reused) = round(&w.0, &c, &mut ledger, &mut cache);
    assert_eq!(
        (ran, reused),
        (vec![], vec!["a".to_string(), "b".to_string()]),
        "nothing changed: every unit is reused"
    );

    fs::write(w.0.join("crates/a/src/lib.rs"), "pub fn a() { 1; }\n").unwrap();
    let (ran, reused) = round(&w.0, &c, &mut ledger, &mut cache);
    assert_eq!(
        (ran, reused),
        (vec!["a".to_string()], vec!["b".to_string()]),
        "one file in a changed: only a runs"
    );
}

#[test]
fn a_shared_file_or_a_new_toolchain_reruns_every_package() {
    let w = workspace("shared");
    let c = ctx("rustc 1.99.0\n");
    let mut ledger = BTreeMap::new();
    let mut cache = FileCache::default();
    round(&w.0, &c, &mut ledger, &mut cache);

    fs::write(w.0.join("Cargo.lock"), "version = 4\n# changed\n").unwrap();
    let (ran, reused) = round(&w.0, &c, &mut ledger, &mut cache);
    assert_eq!(
        (ran.len(), reused.len()),
        (2, 0),
        "the lock file is everyone's"
    );

    let newer = ctx("rustc 1.100.0\n");
    let (ran, reused) = round(&w.0, &newer, &mut ledger, &mut cache);
    assert_eq!(
        (ran.len(), reused.len()),
        (2, 0),
        "a new toolchain reuses nothing"
    );
}

#[test]
fn a_new_untracked_file_in_a_package_reruns_that_package() {
    let w = workspace("untracked");
    let c = ctx("rustc 1.99.0\n");
    let mut ledger = BTreeMap::new();
    let mut cache = FileCache::default();
    round(&w.0, &c, &mut ledger, &mut cache);

    fs::write(w.0.join("crates/b/src/new.rs"), "pub fn n() {}\n").unwrap();
    // build output is ignored and must not move any key
    fs::create_dir_all(w.0.join("target")).unwrap();
    fs::write(w.0.join("target/out"), "o\n").unwrap();
    let (ran, reused) = round(&w.0, &c, &mut ledger, &mut cache);
    assert_eq!(
        (ran, reused),
        (vec!["b".to_string()], vec!["a".to_string()])
    );
}
