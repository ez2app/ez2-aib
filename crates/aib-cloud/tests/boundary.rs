// SPDX-License-Identifier: Apache-2.0
//! Workspace boundary for `aib-cloud` (bb7dc, ADR draft §4.3; #532 M4): this
//! crate must not depend on `aib-sys` (directly or through another workspace
//! crate), the core / store crates must not depend on it, its dependency list
//! is a reviewed allowlist, and — because "no I/O" is a promise about `std`
//! too — its sources must not reach for the network, processes or the file
//! system, and it must not have a build script. Read from the manifests and
//! sources only; no cargo invocation.

use std::fs;
use std::path::{Path, PathBuf};

/// Allowed `[dependencies]` of aib-cloud: name → exact version requirement.
const ALLOWED: &[(&str, &str)] = &[("serde_json", "\"1.0.154\"")];

/// `std` paths that would let this crate do I/O or read the environment. Matched on
/// the whitespace-stripped source (comments included on purpose: a reviewer should
/// not have to wonder), so `std :: net` and line breaks do not hide them.
const FORBIDDEN_STD: &[&str] = &[
    "std::net",
    "std::process",
    "std::fs",
    "std::env",
    "std::os::unix::net",
    "std::os::windows",
    "tokio::",
    "libc::",
];

/// Spellings that would let a grouped `use` or a crate alias reach the same modules
/// without ever writing `std::net` (#532 r2 M1, ebb36 knives B2b–B2d). The crate is
/// small enough to simply forbid them.
const FORBIDDEN_FORMS: &[&str] = &["std::{", "externcrate", "::std::{"];

/// `Some(reason)` when a source text breaks the no-I/O promise. Whitespace is removed
/// first so neither spacing nor line breaks can split a forbidden path.
fn io_violation(text: &str) -> Option<String> {
    let compact: String = text.chars().filter(|c| !c.is_whitespace()).collect();
    for needle in FORBIDDEN_STD {
        if compact.contains(needle) {
            return Some(format!("用了 {needle}"));
        }
    }
    for form in FORBIDDEN_FORMS {
        if compact.contains(form) {
            return Some(format!(
                "用了被禁的寫法 {form}(分組 use / crate 別名會繞過路徑比對)"
            ));
        }
    }
    None
}

/// `[lints] workspace = true` must stay in the manifest: the FFI-socket knife (B2f) is
/// stopped only by the workspace `unsafe_code = "forbid"` lint.
fn lints_inherit_workspace(manifest: &str) -> bool {
    let compact: String = manifest.chars().filter(|c| !c.is_whitespace()).collect();
    compact.contains("[lints]workspace=true")
}

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .expect("workspace root")
}

fn crate_dir(name: &str) -> PathBuf {
    root().join("crates").join(name)
}

fn manifest(name: &str) -> String {
    fs::read_to_string(crate_dir(name).join("Cargo.toml")).expect(name)
}

/// `(key, value)` pairs of every `[...dependencies...]` table of a manifest.
fn dep_names(manifest: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    let mut in_deps = false;
    for line in manifest.lines() {
        let t = line.trim();
        if t.starts_with('[') {
            in_deps = t.contains("dependencies");
            continue;
        }
        if !in_deps || t.is_empty() || t.starts_with('#') {
            continue;
        }
        if let Some((k, v)) = t.split_once('=') {
            out.push((k.trim().to_owned(), v.trim().to_owned()));
        }
    }
    out
}

fn rust_sources(dir: &Path, out: &mut Vec<PathBuf>) {
    for entry in fs::read_dir(dir).expect("read_dir") {
        let p = entry.expect("entry").path();
        if p.is_dir() {
            rust_sources(&p, out);
        } else if p.extension().is_some_and(|e| e == "rs") {
            out.push(p);
        }
    }
}

#[test]
fn cloud_dependencies_are_the_allowlist() {
    let deps = dep_names(&manifest("aib-cloud"));
    let names: Vec<&str> = deps.iter().map(|(k, _)| k.as_str()).collect();
    let allowed: Vec<&str> = ALLOWED.iter().map(|(k, _)| *k).collect();
    assert_eq!(names, allowed, "aib-cloud 的依賴要跟 allowlist 一字不差");
    for (k, req) in ALLOWED {
        let v = &deps.iter().find(|(n, _)| n == k).expect(k).1;
        assert_eq!(v, req, "{k}: 版本要求要完全相等(#532 L3)");
    }
}

#[test]
fn cloud_never_reaches_aib_sys() {
    let deps = dep_names(&manifest("aib-cloud"));
    for (k, v) in &deps {
        assert!(
            !k.contains("aib-sys") && !v.contains("aib-sys"),
            "aib-cloud 不准碰 aib-sys:{k} = {v}"
        );
        // Only non-workspace crates are allowed, so nothing can pull aib-sys in transitively
        // through another workspace member (aibd is the single allowed consumer, xtask checks it).
        assert!(
            !v.contains("path ="),
            "aib-cloud 不准依賴 workspace 內的 crate(M-R1):{k} = {v}"
        );
    }
}

#[test]
fn core_and_store_do_not_depend_on_cloud() {
    for c in ["aib-core", "aib-store", "aib-sys"] {
        for (k, v) in dep_names(&manifest(c)) {
            assert!(
                !k.contains("aib-cloud") && !v.contains("aib-cloud"),
                "{c} 反向依賴 aib-cloud:{k} = {v}"
            );
        }
    }
}

// #532 M4: "no I/O" also means no std::net / std::process / std::fs / std::env in src/,
// no grouped `std::{…}` or `extern crate` alias that could hide them, and no build script.
#[test]
fn cloud_sources_do_no_io() {
    let mut files = Vec::new();
    rust_sources(&crate_dir("aib-cloud").join("src"), &mut files);
    assert!(!files.is_empty());
    for f in files {
        let text = fs::read_to_string(&f).expect("source");
        if let Some(why) = io_violation(&text) {
            panic!("{} {why}:aib-cloud 的 src/ 不做 I/O(#532 M4)", f.display());
        }
    }
    let m = manifest("aib-cloud");
    assert!(!m.contains("build ="), "aib-cloud 不准有 build script");
    assert!(
        !crate_dir("aib-cloud").join("build.rs").exists(),
        "aib-cloud 不准有 build.rs"
    );
    assert!(
        lints_inherit_workspace(&m),
        "aib-cloud 的 Cargo.toml 要留著 [lints] workspace = true(unsafe FFI 靠它擋,#532 r2 M1)"
    );
}

// #532 r2 M1 knives B2b–B2e: the checker itself must catch the bypasses, independent of
// what the real sources currently contain.
#[test]
fn io_checker_catches_grouped_use_alias_env_and_spacing() {
    let samples = [
        ("B2b 分組 use", "use core::fmt;\nuse std::{net::TcpStream};\npub fn leak() { let _ = TcpStream::connect(\"127.0.0.1:1\"); }"),
        ("B2c 分組 use 別名", "use std::{fs as f, process as p};\npub fn leak() { let _ = f::read(\"/etc/hosts\"); let _ = p::Command::new(\"true\").status(); }"),
        ("B2d extern crate 別名", "extern crate std as s;\npub fn leak() { let _ = s::net::TcpStream::connect(\"127.0.0.1:1\"); }"),
        ("B2e std::env", "pub fn leak() -> Option<String> { std::env::var(\"HETZNER_ROBOT_PASS\").ok() }"),
        ("空白拆開", "pub fn leak() { let _ = std :: net\n:: TcpStream::connect(\"127.0.0.1:1\"); }"),
        ("絕對路徑分組", "use ::std::{fs};"),
        ("直接寫", "fn f() { std::process::Command::new(\"true\"); }"),
    ];
    for (name, src) in samples {
        assert!(io_violation(src).is_some(), "{name} 沒被抓到");
    }
    for ok in [
        "use core::fmt;\nuse serde_json::Value;",
        "// the standard network module is not used here",
        "fn x() -> std::fmt::Result { Ok(()) }",
    ] {
        assert_eq!(io_violation(ok), None, "{ok:?} 不該被抓");
    }
    assert!(lints_inherit_workspace(
        "[package]\nname = \"x\"\n\n[lints]\nworkspace = true\n"
    ));
    assert!(!lints_inherit_workspace("[package]\nname = \"x\"\n"));
    assert!(!lints_inherit_workspace("[lints]\nworkspace = false\n"));
}
