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
const ALLOWED: &[(&str, &str)] = &[(
    "serde_json",
    "{ version = \"1.0.154\", default-features = false, features = [\"alloc\"] }",
)];

/// What the compiler cannot check once the crate is `#![no_std]` (#532 r3 M1, ebb36):
/// ways to bring `std` back or to pull code / values from outside `src/` at compile
/// time. Matched on the whitespace-stripped source, comments included on purpose.
const FORBIDDEN_FORMS: &[&str] = &[
    "externcrate",  // any `extern crate` other than the two allowed spellings below
    "#[path",       // N4: `#[path = "../x.rs"] mod m;` reads a file outside src/
    "include!",     // N5: `include!("../x.rs")`
    "include_str!", //     same idea, as a string
    "include_bytes!",
    "env!",         // N6: `env!` / `option_env!` bake build-time environment into the binary
    "macro_rules!", // X3–X6 (r4): a local macro can reassemble any of the above from split tokens
    "cfg_attr",     // X1 / X7 (r4): `cfg_attr(…, path = …)` / `cfg_attr(test, no_std)`
];

/// The only `extern crate` lines a `#![no_std]` crate needs: `alloc` for String / Vec, and
/// `std` for the unit-test harness only (`#[cfg(test)]` code never ships).
const ALLOWED_EXTERN: &[&str] = &[
    "#[macro_use]externcratealloc;",
    "#[cfg(test)]externcratestd;",
];

fn compact(text: &str) -> String {
    text.chars().filter(|c| !c.is_whitespace()).collect()
}

/// `Some(reason)` when a source text breaks the no-I/O promise in a way the compiler
/// would not catch. Whitespace is removed first so spacing / line breaks cannot split a
/// forbidden form; `option_env!` is covered because it contains `env!`.
fn io_violation(text: &str) -> Option<String> {
    let mut c = compact(text);
    for ok in ALLOWED_EXTERN {
        c = c.replace(ok, "");
    }
    FORBIDDEN_FORMS
        .iter()
        .find(|f| c.contains(*f))
        .map(|f| format!("用了 {f}(no_std 之外的逃生口)"))
}

/// Remove `//` line comments and `/* … */` block comments (string literals are not
/// tracked: an inner attribute has to precede every item, so no string can sit in front
/// of a real `#![no_std]`).
fn strip_comments(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    loop {
        let Some(cut) = [rest.find("//"), rest.find("/*")]
            .into_iter()
            .flatten()
            .min()
        else {
            out.push_str(rest);
            return out;
        };
        out.push_str(&rest[..cut]);
        if rest[cut..].starts_with("//") {
            rest = rest[cut..].find('\n').map_or("", |e| &rest[cut + e..]);
        } else {
            rest = rest[cut + 2..]
                .find("*/")
                .map_or("", |e| &rest[cut + 2 + e + 2..]);
        }
    }
}

/// lib.rs must opt out of `std` for real: after comments are gone, one line has to be
/// exactly `#![no_std]` (#532 r4 X2 / X7: a commented-out copy or `cfg_attr(test, no_std)`
/// does not count). That is what turns every I/O path into a compile error.
fn declares_no_std(lib_rs: &str) -> bool {
    strip_comments(lib_rs)
        .lines()
        .any(|l| l.trim() == "#![no_std]")
}

/// Manifest lines that count: comments (`#…`) are dropped first (#532 r3 N11), then the
/// rest is whitespace-stripped (N10: `build="x"` without spaces).
fn manifest_compact(manifest: &str) -> String {
    manifest
        .lines()
        .map(|l| l.split('#').next().unwrap_or(""))
        .collect::<Vec<_>>()
        .join("\n")
        .chars()
        .filter(|c| !c.is_whitespace())
        .collect()
}

/// `[lints] workspace = true` must stay in the manifest (as a real table, not a comment):
/// the FFI-socket knife (B2f) is stopped only by the workspace `unsafe_code = "forbid"` lint.
fn lints_inherit_workspace(manifest: &str) -> bool {
    manifest_compact(manifest).contains("[lints]workspace=true")
}

/// Any `build = …` key, however spaced, means a build script.
fn has_build_key(manifest: &str) -> bool {
    manifest_compact(manifest).contains("build=")
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

// #532 M4 / r3 M1: "no I/O" is a compiler guarantee (`#![no_std]`), plus the few text checks
// the compiler cannot make: no way back to std, nothing pulled from outside src/ at compile
// time, no build script, and the workspace lints (unsafe_code = forbid) still inherited.
#[test]
fn cloud_sources_do_no_io() {
    let src = crate_dir("aib-cloud").join("src");
    let lib = fs::read_to_string(src.join("lib.rs")).expect("lib.rs");
    assert!(
        declares_no_std(&lib),
        "aib-cloud/src/lib.rs 要有 #![no_std](#532 r3 M1:I/O 由編譯器擋)"
    );
    let mut files = Vec::new();
    rust_sources(&src, &mut files);
    assert!(!files.is_empty());
    for f in files {
        let text = fs::read_to_string(&f).expect("source");
        if let Some(why) = io_violation(&text) {
            panic!("{} {why}:aib-cloud 的 src/ 不做 I/O(#532 M4)", f.display());
        }
    }
    let m = manifest("aib-cloud");
    assert!(
        !has_build_key(&m),
        "aib-cloud 不准有 build script(任何寫法的 build=)"
    );
    assert!(
        !crate_dir("aib-cloud").join("build.rs").exists(),
        "aib-cloud 不准有 build.rs"
    );
    assert!(
        lints_inherit_workspace(&m),
        "aib-cloud 的 Cargo.toml 要留著 [lints] workspace = true(unsafe FFI 靠它擋,#532 r2 M1)"
    );
    assert!(
        manifest_compact(&m).contains("default-features=false"),
        "serde_json 要 default-features = false(alloc-only),不然 std 從依賴回來"
    );
}

// #532 r2/r3 M1 knives: the text checker must catch every escape hatch the compiler cannot,
// independent of what the real sources currently contain. (N1 `use std as q`, N2 `use std::*`,
// N3 macro `std::$m`, N7 `std::os::unix::fs`, N9 `std::io::stdin` and the old B2b–B2e are now
// compile errors under `#![no_std]`; they need no text rule.)
#[test]
fn io_checker_catches_escape_hatches() {
    let caught = [
        ("extern crate std", "extern crate std;\npub fn leak() { let _ = std::net::TcpStream::connect(\"127.0.0.1:1\"); }"),
        ("extern crate 別名(B2d)", "extern crate std as s;\npub fn leak() { let _ = s::net::TcpStream::connect(\"127.0.0.1:1\"); }"),
        ("extern crate 拆空白", "extern\n  crate   std;"),
        ("N4 #[path] 讀 src 外", "#[path = \"../knife_evil.rs\"]\npub mod knife_evil;"),
        ("N4 #[path 拆空白", "# [ path = \"../x.rs\" ] mod m;"),
        ("N5 include!", "include!(\"../knife_inc.rs\");"),
        ("N5b include_str!", "const X: &str = include_str!(\"../secret.txt\");"),
        ("N5c include_bytes!", "const X: &[u8] = include_bytes!(\"../secret.bin\");"),
        ("N6 option_env!", "pub fn leak() -> Option<&'static str> { option_env!(\"HETZNER_ROBOT_PASS\") }"),
        ("N6b env!", "const P: &str = env!(\"HETZNER_ROBOT_PASS\");"),
        ("X1 cfg_attr path", "#[cfg_attr(all(), path = \"../knife_evil.rs\")]\npub mod knife_evil;"),
        ("X3 巨集拼 extern crate", "macro_rules! e { ($a:tt, $b:tt) => { $a $b std as s; } }\ne!(extern, crate);"),
        ("X4 巨集拼 option_env", "macro_rules! m { ($n:ident) => { $n!(\"HETZNER_ROBOT_PASS\") } }\nm!(option_env);"),
        ("X5 巨集拼 include", "macro_rules! m { ($n:ident) => { $n!(\"../knife_inc.rs\"); } }\nm!(include);"),
        ("X6 巨集拼 #[path]", "macro_rules! m { ($a:ident) => { #[$a = \"../knife_evil.rs\"] mod k; } }\nm!(path);"),
        ("X7 cfg_attr no_std", "#![cfg_attr(test, no_std)]"),
        ("macro_rules 拆空白", "macro_rules !\n x { () => {} }"),
    ];
    for (name, src) in caught {
        assert!(io_violation(src).is_some(), "{name} 沒被抓到");
    }
    let fine = [
        "#[macro_use]\nextern crate alloc;\n#[cfg(test)]\nextern crate std;\npub mod robot;",
        "use core::fmt;\nuse alloc::string::String;\nuse serde_json::Value;",
        "// the standard network module is not used here; `std::net` would not compile under no_std",
        "fn x() -> core::fmt::Result { Ok(()) }",
        "let environment = 1; // the word env alone is fine",
    ];
    for ok in fine {
        assert_eq!(io_violation(ok), None, "{ok:?} 不該被抓");
    }
    assert!(declares_no_std("//! doc\n#![no_std]\nextern crate alloc;"));
    assert!(declares_no_std("/* block */\n  #![no_std]  // trailing\n"));
    assert!(!declares_no_std("#! [ no_std ]")); // not the exact spelling the compiler sees
    assert!(!declares_no_std("//! says no_std in a comment only"));
    assert!(!declares_no_std("// #![no_std]\npub mod robot;")); // X2: commented out
    assert!(!declares_no_std("/*\n#![no_std]\n*/\npub mod robot;")); // X2 via block comment
    assert!(!declares_no_std(
        "#![cfg_attr(test, no_std)]\n// #![no_std]"
    )); // X7
    assert!(!declares_no_std("#![no_std] pub mod robot;")); // must be a line of its own
    assert_eq!(strip_comments("a // x\nb /* y\nz */ c"), "a \nb  c");
    // manifest: N10 `build="x"` without spaces, N11 `[lints]` only inside a comment
    assert!(has_build_key(
        "[package]\nname = \"x\"\nbuild=\"knife/gen.rs\"\n"
    ));
    assert!(has_build_key("[package]\nbuild = \"build.rs\"\n"));
    assert!(!has_build_key(
        "[package]\nname = \"x\"\n# build = \"no\"\n"
    ));
    assert!(lints_inherit_workspace(
        "[package]\nname = \"x\"\n\n[lints]\nworkspace = true\n"
    ));
    assert!(!lints_inherit_workspace(
        "[package]\nname = \"x\"\n# [lints] workspace = true\n"
    ));
    assert!(!lints_inherit_workspace(
        "[package]\nname = \"x\"\n#[lints]\n#workspace = true\n"
    ));
    assert!(!lints_inherit_workspace("[lints]\nworkspace = false\n"));
}
