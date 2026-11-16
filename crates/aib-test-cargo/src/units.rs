//! Cargo units for `aib-test-rs` (519d0 11-17 03:35 影子安裝頭): one unit per workspace
//! package, with the folders it reads (its own folder, the folders of the workspace packages
//! it depends on by path, and the workspace files), read from `cargo metadata`.

use std::collections::{BTreeMap, BTreeSet};

/// The files every package reads (a change in any reruns everything).
pub const WORKSPACE_FILES: &[&str] = &[
    "Cargo.toml",
    "Cargo.lock",
    "rust-toolchain.toml",
    "rust-toolchain",
    ".cargo",
    ".config/nextest.toml",
];

/// One workspace package as a unit.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Package {
    pub name: String,
    /// its folder relative to the workspace root, `/`
    pub dir: String,
    /// workspace packages it depends on by path (normal, dev and build)
    pub path_deps: Vec<String>,
}

/// The workspace from `cargo metadata --no-deps --format-version 1`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Workspace {
    pub root: String,
    pub target_dir: String,
    pub packages: Vec<Package>,
}

fn rel(root: &str, p: &str) -> Option<String> {
    let root = root.trim_end_matches('/');
    let r = p.strip_prefix(root)?.trim_start_matches('/');
    Some(r.replace('\\', "/"))
}

/// Reads the metadata. Only workspace members are units; a path dependency outside the
/// workspace is not followed (it has no folder under the root to read).
pub fn parse_metadata(json: &str) -> Result<Workspace, String> {
    let v: serde_json::Value =
        serde_json::from_str(json).map_err(|e| format!("cargo metadata is not JSON: {e}"))?;
    let root = v
        .get("workspace_root")
        .and_then(|s| s.as_str())
        .ok_or("cargo metadata has no workspace_root")?
        .to_string();
    let target_dir = v
        .get("target_directory")
        .and_then(|s| s.as_str())
        .unwrap_or("target")
        .to_string();
    let members: BTreeSet<String> = v
        .get("workspace_members")
        .and_then(|m| m.as_array())
        .ok_or("cargo metadata has no workspace_members")?
        .iter()
        .filter_map(|x| x.as_str().map(str::to_string))
        .collect();
    let pkgs = v
        .get("packages")
        .and_then(|p| p.as_array())
        .ok_or("cargo metadata has no packages")?;
    let mut out = Vec::new();
    let mut names = BTreeSet::new();
    for p in pkgs {
        let id = p.get("id").and_then(|s| s.as_str()).unwrap_or("");
        if !members.contains(id) {
            continue;
        }
        let name = p
            .get("name")
            .and_then(|s| s.as_str())
            .ok_or("a package has no name")?
            .to_string();
        let manifest = p
            .get("manifest_path")
            .and_then(|s| s.as_str())
            .ok_or_else(|| format!("{name}: no manifest_path"))?;
        let dir = rel(
            &root,
            manifest
                .trim_end_matches("Cargo.toml")
                .trim_end_matches(['/', '\\']),
        )
        .ok_or_else(|| format!("{name}: manifest outside the workspace root"))?;
        let mut deps = Vec::new();
        for d in p
            .get("dependencies")
            .and_then(|d| d.as_array())
            .into_iter()
            .flatten()
        {
            if d.get("path").and_then(|s| s.as_str()).is_some() {
                if let Some(n) = d.get("name").and_then(|s| s.as_str()) {
                    deps.push(n.to_string());
                }
            }
        }
        deps.sort();
        deps.dedup();
        names.insert(name.clone());
        out.push(Package {
            name,
            dir,
            path_deps: deps,
        });
    }
    // a path dependency that is not a member is dropped (outside the workspace)
    for p in &mut out {
        p.path_deps.retain(|d| names.contains(d));
    }
    out.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(Workspace {
        root,
        target_dir,
        packages: out,
    })
}

impl Workspace {
    /// The folders and files a package's key reads: its own folder, every workspace
    /// package it reaches by path dependencies (closure), and the workspace files present.
    pub fn inputs(
        &self,
        name: &str,
        present: &dyn Fn(&str) -> bool,
    ) -> Result<Vec<String>, String> {
        let by: BTreeMap<&str, &Package> =
            self.packages.iter().map(|p| (p.name.as_str(), p)).collect();
        let mut seen = BTreeSet::new();
        let mut todo = vec![name.to_string()];
        while let Some(n) = todo.pop() {
            if !seen.insert(n.clone()) {
                continue;
            }
            let p = by
                .get(n.as_str())
                .ok_or_else(|| format!("{n}: not a workspace package"))?;
            todo.extend(p.path_deps.iter().cloned());
        }
        let mut v: Vec<String> = seen.iter().map(|n| by[n.as_str()].dir.clone()).collect();
        v.extend(
            WORKSPACE_FILES
                .iter()
                .filter(|f| present(f))
                .map(|f| f.to_string()),
        );
        v.sort();
        v.dedup();
        Ok(v)
    }
}

/// nextest names a binary `<package>` (the library) or `<package>::<target>`; the unit is
/// the package.
pub fn package_of_binary(binary_id: &str) -> &str {
    binary_id.split("::").next().unwrap_or(binary_id)
}

#[cfg(test)]
mod tests {
    use super::*;

    // shape of `cargo metadata --no-deps --format-version 1` (fields trimmed)
    const META: &str = r#"{"packages":[
 {"name":"a","version":"0.1.0","id":"path+file:///r/crates/a#0.1.0","manifest_path":"/r/crates/a/Cargo.toml",
  "dependencies":[{"name":"core","source":null,"req":"*","kind":null,"path":"/r/crates/core"},
                  {"name":"serde_json","source":"registry+https://github.com/rust-lang/crates.io-index","req":"^1","kind":null}]},
 {"name":"core","version":"0.1.0","id":"path+file:///r/crates/core#0.1.0","manifest_path":"/r/crates/core/Cargo.toml",
  "dependencies":[{"name":"outside","source":null,"req":"*","kind":"dev","path":"/elsewhere/outside"}]},
 {"name":"b","version":"0.1.0","id":"path+file:///r/crates/b#0.1.0","manifest_path":"/r/crates/b/Cargo.toml","dependencies":[]}],
 "workspace_members":["path+file:///r/crates/a#0.1.0","path+file:///r/crates/core#0.1.0","path+file:///r/crates/b#0.1.0"],
 "target_directory":"/r/target","workspace_root":"/r","version":1}"#;

    #[test]
    fn members_become_units_and_path_dependencies_close_over_the_workspace() {
        let w = parse_metadata(META).unwrap();
        let names: Vec<&str> = w.packages.iter().map(|p| p.name.as_str()).collect();
        assert_eq!(names, vec!["a", "b", "core"]);
        let a = &w.packages[0];
        assert_eq!(a.dir, "crates/a");
        assert_eq!(a.path_deps, vec!["core"]);
        assert!(
            w.packages[2].path_deps.is_empty(),
            "outside the workspace: dropped"
        );
        let present = |f: &str| f == "Cargo.toml" || f == "Cargo.lock";
        assert_eq!(
            w.inputs("a", &present).unwrap(),
            vec!["Cargo.lock", "Cargo.toml", "crates/a", "crates/core"]
        );
        assert_eq!(
            w.inputs("b", &present).unwrap(),
            vec!["Cargo.lock", "Cargo.toml", "crates/b"]
        );
        assert!(w.inputs("nope", &present).is_err());
    }

    #[test]
    fn a_binary_belongs_to_its_package() {
        assert_eq!(package_of_binary("aib-test-cargo"), "aib-test-cargo");
        assert_eq!(
            package_of_binary("aib-test-cargo::runs_through"),
            "aib-test-cargo"
        );
    }
}
