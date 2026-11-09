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
use std::process::Command;

use serde_json::Value;

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

/// Remove `//` line comments and `/* … */` block comments. Block comments nest in Rust
/// (`/* /* */ still a comment */`), so the depth is counted (#532 r5 Y3). String literals
/// are not tracked: an inner attribute has to precede every item, so no string can sit in
/// front of a real `#![no_std]`; and [`declares_no_std`] only looks at the first non-empty
/// line, so a string constant containing the text later in the file does not count (Y1).
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
            continue;
        }
        // block comment: walk forward counting nested `/*` and `*/` until depth returns to 0
        let mut depth = 0usize;
        let mut i = cut;
        let bytes = rest.as_bytes();
        let mut end = rest.len();
        while i + 1 < bytes.len() {
            if bytes[i] == b'/' && bytes[i + 1] == b'*' {
                depth += 1;
                i += 2;
            } else if bytes[i] == b'*' && bytes[i + 1] == b'/' {
                depth -= 1;
                i += 2;
                if depth == 0 {
                    end = i;
                    break;
                }
            } else {
                i += 1;
            }
        }
        rest = &rest[end..];
    }
}

/// lib.rs must opt out of `std` for real: after comments are gone, the **first non-empty
/// line** has to be exactly `#![no_std]` (#532 r4 X2 / X7, r5 Y1: a commented-out copy,
/// `cfg_attr(test, no_std)` or the text inside a string constant later in the file does not
/// count). That is what turns every I/O path into a compile error.
fn declares_no_std(lib_rs: &str) -> bool {
    strip_comments(lib_rs)
        .lines()
        .map(str::trim)
        .find(|l| !l.is_empty())
        .is_some_and(|l| l == "#![no_std]")
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

/// A target table (`[lib]`, `[[bin]]`, `[[example]]`, `[[test]]`, `[[bench]]`) may not set
/// `path`: `[lib] path = "src/root2.rs"` swaps the crate root for a file without
/// `#![no_std]` and brings all of std back (#566 M1, ebb36). Tables are found on the
/// comment-stripped, whitespace-stripped manifest; `path=` anywhere inside such a table
/// (up to the next `[`) is refused. Any `[[bin]]` / `[[example]]` table at all is refused
/// too: this crate ships a library only.
fn manifest_target_path_violation(manifest: &str) -> Option<String> {
    let c = manifest_compact(manifest);
    for table in ["[[bin]]", "[[example]]"] {
        if c.contains(table) {
            return Some(format!("有 {table}(aib-cloud 只出 library)"));
        }
    }
    for table in ["[lib]", "[[test]]", "[[bench]]"] {
        let mut from = 0;
        while let Some(i) = c[from..].find(table) {
            let start = from + i + table.len();
            let end = c[start..].find('[').map_or(c.len(), |e| start + e);
            if c[start..end].contains("path=") {
                return Some(format!("{table} 設了 path=(可以換掉 crate 根)"));
            }
            from = start;
        }
    }
    None
}

/// `cargo metadata` for a manifest, as JSON (offline; the crate under test has no network).
/// `no_deps` keeps it to the workspace / crate itself.
fn cargo_metadata(manifest: &Path, no_deps: bool) -> Value {
    let mut cmd = Command::new(env!("CARGO"));
    cmd.args([
        "metadata",
        "--format-version",
        "1",
        "--offline",
        "--manifest-path",
    ])
    .arg(manifest);
    if no_deps {
        cmd.arg("--no-deps");
    }
    let out = cmd.output().expect("run cargo metadata");
    assert!(
        out.status.success(),
        "cargo metadata failed: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    serde_json::from_slice(&out.stdout).expect("cargo metadata json")
}

/// What cargo will actually build for a package (#566 r2, ebb36): exactly one `lib`
/// target whose source is `<crate>/src/lib.rs`, test targets only under `<crate>/tests/`,
/// and no bin / example / bench / custom-build target however the manifest spells it
/// (`[lib] path`, `lib = { path }`, `lib.path`, `["lib"]`, `"path" =`, `src/main.rs`,
/// top-level `bin = [...]`, `build = …` in any form). Reading cargo's own view instead of
/// the manifest text closes the whole class.
fn target_violation(meta: &Value, pkg_name: &str, crate_dir: &Path) -> Option<String> {
    // #566 r3 L1 (ebb36): a metadata document of an unexpected shape is a violation, not a
    // pass — every missing field below used to return None ("nothing wrong").
    let Some(pkgs) = meta["packages"].as_array() else {
        return Some("metadata shape: packages is not an array".to_owned());
    };
    let Some(pkg) = pkgs.iter().find(|p| p["name"] == pkg_name) else {
        return Some(format!("package {pkg_name} not in metadata"));
    };
    let Some(targets) = pkg["targets"].as_array() else {
        return Some(format!(
            "metadata shape: {pkg_name}.targets is not an array"
        ));
    };
    let canon = |p: &str| Path::new(p).canonicalize().ok();
    let Some(want_lib) = canon(&crate_dir.join("src").join("lib.rs").to_string_lossy()) else {
        return Some(format!("{}/src/lib.rs does not exist", crate_dir.display()));
    };
    let tests_dir = canon(&crate_dir.join("tests").to_string_lossy());
    let mut libs = 0;
    for t in targets {
        let name = t["name"].as_str().unwrap_or("?");
        let Some(src) = t["src_path"].as_str() else {
            return Some(format!("metadata shape: target {name} has no src_path"));
        };
        let Some(kinds) = t["kind"].as_array() else {
            return Some(format!("metadata shape: target {name} has no kind array"));
        };
        for k in kinds.iter().filter_map(Value::as_str) {
            match k {
                "lib" => {
                    libs += 1;
                    if canon(src).as_ref() != Some(&want_lib) {
                        return Some(format!(
                            "lib target {name} is rooted at {src}, not src/lib.rs"
                        ));
                    }
                }
                "test" => {
                    let ok =
                        matches!((&tests_dir, canon(src)), (Some(d), Some(s)) if s.starts_with(d));
                    if !ok {
                        return Some(format!("test target {name} outside tests/: {src}"));
                    }
                }
                other => {
                    return Some(format!(
                        "target kind {other} ({name}) is not allowed in aib-cloud"
                    ))
                }
            }
        }
    }
    if libs != 1 {
        return Some(format!("{libs} lib targets, want exactly 1"));
    }
    None
}

/// One `[[package]]` block of a Cargo.lock.
struct LockPkg {
    name: String,
    version: String,
    source: String,
    /// `dependencies = [...]` entries, each `name`, `name version` or `name version (source)`.
    deps: Vec<String>,
}

fn parse_lock(lock: &str) -> Vec<LockPkg> {
    lock.split("[[package]]")
        .skip(1)
        .filter_map(|block| {
            let field = |k: &str| {
                block
                    .lines()
                    .find_map(|l| {
                        l.trim()
                            .strip_prefix(k)
                            .and_then(|r| r.trim().strip_prefix('='))
                    })
                    .map(|v| v.trim().trim_matches('"').to_owned())
            };
            let deps = block
                .split_once("dependencies = [")
                .and_then(|(_, rest)| rest.split_once(']'))
                .map(|(list, _)| {
                    list.lines()
                        .map(|l| l.trim().trim_end_matches(',').trim_matches('"').to_owned())
                        .filter(|l| !l.is_empty())
                        .collect()
                })
                .unwrap_or_default();
            Some(LockPkg {
                name: field("name")?,
                version: field("version").unwrap_or_default(),
                source: field("source").unwrap_or_default(),
                deps,
            })
        })
        .collect()
}

/// Every package the crate pulls in — directly **or transitively** — must resolve to a
/// crates.io package (#566 r3 L2, ebb36): a workspace-root `[patch]` of `serde` or
/// `serde_core` would hand serde_json a local crate that is free to use std, and the direct
/// allowlist never sees it. Walks Cargo.lock from `root_pkg`'s block; only the root itself
/// (a workspace member, no `source`) is exempt. A dependency that names no package in the
/// lock is a violation too. Read from the lockfile so it works offline without downloading
/// the whole workspace graph.
fn dependency_source_violation(lock: &str, root_pkg: &str) -> Option<String> {
    let pkgs = parse_lock(lock);
    let find = |spec: &str| -> Option<&LockPkg> {
        let mut parts = spec.split_whitespace();
        let name = parts.next()?;
        let version = parts.next();
        pkgs.iter()
            .find(|p| p.name == name && version.is_none_or(|v| p.version == v))
    };
    let root = pkgs.iter().find(|p| p.name == root_pkg)?;
    if !root.source.is_empty() {
        return Some(format!(
            "{root_pkg} has a source ({}); it should be the workspace member",
            root.source
        ));
    }
    let mut todo: Vec<&LockPkg> = vec![root];
    let mut seen: Vec<(&str, &str)> = Vec::new();
    while let Some(p) = todo.pop() {
        for spec in &p.deps {
            let Some(d) = find(spec) else {
                return Some(format!(
                    "{}: dependency {spec:?} is not in Cargo.lock",
                    p.name
                ));
            };
            if seen.contains(&(d.name.as_str(), d.version.as_str())) {
                continue;
            }
            seen.push((&d.name, &d.version));
            if !d
                .source
                .starts_with("registry+https://github.com/rust-lang/crates.io-index")
            {
                return Some(format!(
                    "{} {} (needed by {}) resolves to source {:?}, not crates.io ([patch]/[replace]?)",
                    d.name, d.version, p.name, d.source
                ));
            }
            todo.push(d);
        }
    }
    None
}

/// Build a throwaway crate from a manifest body + files and ask cargo what it would build.
#[cfg(test)]
fn probe_manifest(tag: &str, manifest: &str, files: &[(&str, &str)]) -> Option<String> {
    let dir = std::env::temp_dir().join(format!("aib-cloud-boundary-{tag}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(dir.join("src")).expect("tmp dir");
    fs::write(
        dir.join("Cargo.toml"),
        format!(
            "{manifest}
[workspace]
"
        ),
    )
    .expect("manifest");
    for (rel, body) in files {
        let p = dir.join(rel);
        fs::create_dir_all(p.parent().expect("parent")).expect("dir");
        fs::write(p, body).expect("file");
    }
    let meta = cargo_metadata(&dir.join("Cargo.toml"), true);
    let v = target_violation(&meta, "k", &dir);
    let _ = fs::remove_dir_all(&dir);
    v
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
    if let Some(why) = manifest_target_path_violation(&m) {
        panic!("aib-cloud/Cargo.toml {why}(#566 M1:target path 能換掉 #![no_std] 的 crate 根)");
    }
    // #566 r2: ask cargo itself what it would build — closes every spelling at once.
    let meta = cargo_metadata(&crate_dir("aib-cloud").join("Cargo.toml"), true);
    if let Some(why) = target_violation(&meta, "aib-cloud", &crate_dir("aib-cloud")) {
        panic!("aib-cloud targets: {why}(#566 r2:只准一個 src/lib.rs 的 lib + tests/ 下的 test)");
    }
    let lock = fs::read_to_string(root().join("Cargo.lock")).expect("Cargo.lock");
    if let Some(why) = dependency_source_violation(&lock, "aib-cloud") {
        panic!("aib-cloud dependencies: {why}(#566 r3 L2:含遞移依賴)");
    }
    for mf in [
        crate_dir("aib-cloud").join("Cargo.toml"),
        root().join("Cargo.toml"),
    ] {
        // quotes dropped too: `["patch".crates-io]` is the same table (#566 r3 L2)
        let text = manifest_compact(&fs::read_to_string(&mf).expect("manifest")).replace('"', "");
        assert!(
            !text.contains("[patch")
                && !text.contains("[replace")
                && !text.contains("patch.")
                && !text.contains("replace."),
            "{} 有 [patch] / [replace](可以把 serde_json 換成本地 crate)",
            mf.display()
        );
    }
    assert!(
        !crate_dir("aib-cloud").join("src").join("bin").exists(),
        "aib-cloud 不准有 src/bin/(自動變 binary target,繞過 lib.rs 的 #![no_std];#566 M1)"
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
                                                            // r5 Y1: the attribute text inside a string constant, after the real attribute was removed
    assert!(!declares_no_std(
        "pub mod robot;\npub const KNIFE: &str = \"\\n#![no_std]\\n\";"
    ));
    assert!(!declares_no_std(
        "#[macro_use]\nextern crate alloc;\n#![no_std]"
    )); // not first
        // r5 Y3: nested block comment swallowing the attribute
    assert!(!declares_no_std("/* /* */\n#![no_std]\n*/\npub mod robot;"));
    assert!(!declares_no_std(
        "/* a /* b */ c */ /* d */\npub mod robot;\n#![no_std]"
    ));
    assert!(declares_no_std(
        "/* a /* b */ c */\n#![no_std]\npub mod robot;"
    ));
    assert_eq!(strip_comments("a // x\nb /* y\nz */ c"), "a \nb  c");
    assert_eq!(strip_comments("a /* 1 /* 2 */ 3 */ b"), "a  b");
    assert_eq!(strip_comments("a /* never closed"), "a ");
    // #566 M1: target tables that can swap the crate root
    for bad in [
        "[package]\nname = \"x\"\n\n[lib]\npath = \"src/root2.rs\"\n",
        "[package]\nname = \"x\"\n[lib]\nname=\"y\"\n  path=\"../evil.rs\"\n[dependencies]\n",
        "[lib]\n# comment\npath = \"src/other.rs\"\n",
        "[[bin]]\nname = \"tool\"\npath = \"src/tool.rs\"\n",
        "[[bin]]\nname = \"tool\"\n",
        "[[example]]\nname = \"e\"\n",
        "[[test]]\nname = \"t\"\npath = \"../t.rs\"\n",
        "[[bench]]\nname = \"b\"\npath = \"../b.rs\"\n",
    ] {
        assert!(
            manifest_target_path_violation(bad).is_some(),
            "{bad:?} 沒被抓到"
        );
    }
    for ok in [
        "[package]\nname = \"x\"\n\n[lib]\nname = \"aib_cloud\"\n\n[dependencies]\nserde_json = \"1\"\n",
        "[package]\nname = \"x\"\n# [lib] path = \"src/root2.rs\" (comment)\n[lints]\nworkspace = true\n",
        "[[test]]\nname = \"boundary\"\nharness = true\n",
        "[package]\nname = \"x\"\n[dependencies]\nfoo = { path = \"../foo\" }\n",
    ] {
        assert_eq!(manifest_target_path_violation(ok), None, "{ok:?} 不該被抓");
    }
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

// #566 r2 (ebb36): the manifest spellings that slipped past the text rules, judged by cargo.
#[test]
fn cargo_view_catches_every_target_spelling() {
    const PKG: &str = "[package]\nname = \"k\"\nversion = \"0.0.0\"\nedition = \"2021\"\n";
    type Sample = (&'static str, String, Vec<(&'static str, &'static str)>);
    let lib = ("src/lib.rs", "#![no_std]\n");
    let root2 = (
        "src/root2.rs",
        "pub fn leak() { let _ = std::net::TcpStream::connect(\"127.0.0.1:1\"); }\n",
    );
    let main_rs = ("src/main.rs", "fn main() {}\n");
    let b_rs = ("b.rs", "fn main() {}\n");
    // top-level keys (`lib = …`, `lib.path`, `bin = [...]`) have to come before any `[table]`
    let bad: Vec<Sample> = vec![
        (
            "lib-table-path",
            format!("{PKG}\n[lib]\npath = \"src/root2.rs\"\n"),
            vec![lib, root2],
        ),
        (
            "lib-inline-table",
            format!("lib = {{ path = \"src/root2.rs\" }}\n{PKG}"),
            vec![lib, root2],
        ),
        (
            "lib-dotted",
            format!("lib.path = \"src/root2.rs\"\n{PKG}"),
            vec![lib, root2],
        ),
        (
            "lib-quoted-table",
            format!("{PKG}\n[\"lib\"]\npath = \"src/root2.rs\"\n"),
            vec![lib, root2],
        ),
        (
            "lib-quoted-key",
            format!("{PKG}\n[lib]\n\"path\" = \"src/root2.rs\"\n"),
            vec![lib, root2],
        ),
        ("auto-main-rs", PKG.to_owned(), vec![lib, main_rs]),
        (
            "top-level-bin-array",
            format!("bin = [{{ name = \"t\", path = \"src/t.rs\" }}]\n{PKG}"),
            vec![lib, ("src/t.rs", "fn main() {}\n")],
        ),
        (
            "build-key",
            format!("{PKG}build = \"b.rs\"\n"),
            vec![lib, b_rs],
        ),
        (
            "build-quoted-key",
            format!("{PKG}\"build\" = \"b.rs\"\n"),
            vec![lib, b_rs],
        ),
        (
            "build-with-lib-table",
            format!("{PKG}build = \"b.rs\"\n\n[lib]\nname = \"k\"\n"),
            vec![lib, b_rs],
        ),
        (
            "auto-build-rs",
            PKG.to_owned(),
            vec![lib, ("build.rs", "fn main() {}\n")],
        ),
    ];
    for (tag, manifest, files) in &bad {
        let v = probe_manifest(tag, manifest, files);
        assert!(v.is_some(), "{tag}: cargo view did not flag it");
    }
    assert_eq!(probe_manifest("plain", PKG, &[lib]), None);
    assert_eq!(
        probe_manifest(
            "with-tests",
            PKG,
            &[lib, ("tests/t.rs", "#[test] fn t() {}\n")]
        ),
        None
    );
    assert_eq!(
        probe_manifest(
            "lib-name-only",
            &format!("{PKG}\n[lib]\nname = \"k\"\n"),
            &[lib]
        ),
        None
    );
}

// #566 r3 L1 / L2 knives (ebb36): a metadata document of the wrong shape must fail, and a
// transitive dependency without a crates.io source must be caught.
#[test]
fn metadata_shape_and_transitive_sources_fail_closed() {
    let dir = crate_dir("aib-cloud");
    for (tag, meta) in [
        ("no packages", serde_json::json!({})),
        ("packages not array", serde_json::json!({"packages": 1})),
        (
            "no targets",
            serde_json::json!({"packages": [{"name": "aib-cloud"}]}),
        ),
        (
            "target without kind",
            serde_json::json!({"packages": [{"name": "aib-cloud", "targets": [{"name": "aib_cloud", "src_path": "x"}]}]}),
        ),
        (
            "target without src_path",
            serde_json::json!({"packages": [{"name": "aib-cloud", "targets": [{"name": "aib_cloud", "kind": ["lib"]}]}]}),
        ),
    ] {
        assert!(
            target_violation(&meta, "aib-cloud", &dir).is_some(),
            "{tag}: shape slipped through"
        );
    }

    const REG: &str = "registry+https://github.com/rust-lang/crates.io-index";
    let lock = |serde_src: &str| {
        format!(
            "[[package]]\nname = \"aib-cloud\"\nversion = \"0.0.0\"\ndependencies = [\n \"serde_json\",\n]\n\n\
             [[package]]\nname = \"serde_json\"\nversion = \"1.0.154\"\nsource = \"{REG}\"\ndependencies = [\n \"serde\",\n \"serde_core 1.0.230\",\n]\n\n\
             [[package]]\nname = \"serde\"\nversion = \"1.0.230\"\n{serde_src}dependencies = [\n \"serde_core 1.0.230\",\n]\n\n\
             [[package]]\nname = \"serde_core\"\nversion = \"1.0.230\"\nsource = \"{REG}\"\n"
        )
    };
    // every package from crates.io: clean
    assert_eq!(
        dependency_source_violation(&lock(&format!("source = \"{REG}\"\n")), "aib-cloud"),
        None
    );
    // serde patched to a local path: no source line in the lock, two hops below aib-cloud
    let why = dependency_source_violation(&lock(""), "aib-cloud")
        .expect("transitive patch must be caught");
    assert!(why.contains("serde 1.0.230"), "{why}");
    // serde patched to a git fork
    let why = dependency_source_violation(
        &lock("source = \"git+https://example.com/serde#abc\"\n"),
        "aib-cloud",
    )
    .expect("git source must be caught");
    assert!(why.contains("git+"), "{why}");
    // a dependency the lock does not know
    let dangling = lock(&format!("source = \"{REG}\"\n")).replace(
        "[[package]]\nname = \"serde_core\"",
        "[[package]]\nname = \"serde_korea\"",
    );
    assert!(dependency_source_violation(&dangling, "aib-cloud").is_some());
    // the real lock is clean
    assert_eq!(
        dependency_source_violation(
            &fs::read_to_string(root().join("Cargo.lock")).expect("lock"),
            "aib-cloud"
        ),
        None
    );
    // quoted [patch] table spelling is seen by the manifest check
    assert!(
        manifest_compact("[\"patch\".crates-io]\nserde = { path = \"x\" }\n")
            .replace('"', "")
            .contains("[patch")
    );
}
