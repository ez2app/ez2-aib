// SPDX-License-Identifier: Apache-2.0
//! Workspace boundary for `aib-cloud` (bb7dc, ADR draft §4.3): this crate must
//! not depend on `aib-sys` (directly or through another workspace crate), the
//! core / store crates must not depend on it, and its own dependency list is a
//! reviewed allowlist. Read from the manifests, no cargo invocation.

use std::fs;
use std::path::{Path, PathBuf};

/// Allowed `[dependencies]` of aib-cloud: name → version requirement.
const ALLOWED: &[(&str, &str)] = &[("serde_json", "1.0.154")];

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .expect("workspace root")
}

fn manifest(crate_dir: &str) -> String {
    fs::read_to_string(root().join("crates").join(crate_dir).join("Cargo.toml")).expect(crate_dir)
}

/// Dependency names mentioned in any `[...dependencies...]` table of a manifest.
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

#[test]
fn cloud_dependencies_are_the_allowlist() {
    let deps = dep_names(&manifest("aib-cloud"));
    let names: Vec<&str> = deps.iter().map(|(k, _)| k.as_str()).collect();
    let allowed: Vec<&str> = ALLOWED.iter().map(|(k, _)| *k).collect();
    assert_eq!(names, allowed, "aib-cloud 的依賴要跟 allowlist 一字不差");
    for (k, req) in ALLOWED {
        let v = &deps.iter().find(|(n, _)| n == k).expect(k).1;
        assert!(v.contains(req), "{k}: {v} 不是 {req}");
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
