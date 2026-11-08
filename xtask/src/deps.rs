//! `aib-xtask deps [--manifest-path P]`: only `aibd` may depend on `aib-sys`
//! (API list v0.2, section 9). The one crate with `unsafe` OS calls must not
//! leak into aib-core, aib-store or any other crate, directly, through a
//! dev / build / target-specific dependency, under a renamed key, or through
//! another workspace crate.

use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

pub const SYS_CRATE: &str = "aib-sys";
pub const SYS_ALLOWED: &[&str] = &["aibd"];

/// Workspace package name -> names of the packages it depends on (every
/// kind, every target; `name` is the package name even when renamed).
fn graph(meta: &Value) -> BTreeMap<String, BTreeSet<String>> {
    let mut g = BTreeMap::new();
    for p in meta["packages"].as_array().into_iter().flatten() {
        let Some(name) = p["name"].as_str() else {
            continue;
        };
        let deps = p["dependencies"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|d| d["name"].as_str().map(str::to_owned))
            .collect();
        g.insert(name.to_owned(), deps);
    }
    g
}

/// The chain from `from` to aib-sys, if any (e.g. `["aib-store", "x", "aib-sys"]`).
fn path_to_sys(g: &BTreeMap<String, BTreeSet<String>>, from: &str) -> Option<Vec<String>> {
    let mut stack = vec![vec![from.to_owned()]];
    let mut seen = BTreeSet::new();
    while let Some(path) = stack.pop() {
        let last = path.last()?.clone();
        if !seen.insert(last.clone()) {
            continue;
        }
        for d in g.get(&last).into_iter().flatten() {
            let mut next = path.clone();
            next.push(d.clone());
            if d == SYS_CRATE {
                return Some(next);
            }
            stack.push(next);
        }
    }
    None
}

/// Every violation, one line each. Empty = ok.
pub fn check(meta: &Value) -> Vec<String> {
    let g = graph(meta);
    let mut errs = Vec::new();
    for name in g.keys() {
        if name == SYS_CRATE || SYS_ALLOWED.contains(&name.as_str()) {
            continue;
        }
        if let Some(path) = path_to_sys(&g, name) {
            errs.push(format!(
                "`{name}` depends on {SYS_CRATE} ({}); only {} may",
                path.join(" -> "),
                SYS_ALLOWED.join(", ")
            ));
        }
    }
    errs
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn pkg(name: &str, deps: &[&str]) -> Value {
        json!({"name": name, "dependencies": deps.iter().map(|d| json!({"name": d})).collect::<Vec<_>>()})
    }

    fn meta(pkgs: Vec<Value>) -> Value {
        json!({ "packages": pkgs })
    }

    #[test]
    fn aibd_may_depend_on_sys() {
        let m = meta(vec![
            pkg("aib-core", &[]),
            pkg("aib-sys", &["libc"]),
            pkg("aibd", &["aib-core", "aib-sys"]),
        ]);
        assert!(check(&m).is_empty());
    }

    #[test]
    fn a_direct_dependency_is_refused() {
        let m = meta(vec![pkg("aib-core", &["aib-sys"]), pkg("aib-sys", &[])]);
        let e = check(&m);
        assert_eq!(e.len(), 1);
        assert!(
            e[0].contains("`aib-core` depends on aib-sys (aib-core -> aib-sys)"),
            "{e:?}"
        );
    }

    #[test]
    fn a_dependency_through_another_crate_is_refused() {
        let m = meta(vec![
            pkg("aib-store", &["aib-procwatch"]),
            pkg("aib-procwatch", &["aib-sys"]),
            pkg("aib-sys", &[]),
        ]);
        let e = check(&m);
        assert!(
            e.iter()
                .any(|l| l.contains("aib-store -> aib-procwatch -> aib-sys")),
            "{e:?}"
        );
    }

    #[test]
    fn depending_on_aibd_counts_too() {
        let m = meta(vec![
            pkg("tool", &["aibd"]),
            pkg("aibd", &["aib-sys"]),
            pkg("aib-sys", &[]),
        ]);
        assert!(check(&m).iter().any(|l| l.contains("`tool`")));
    }

    #[test]
    fn a_renamed_dev_or_target_dependency_is_still_seen() {
        // cargo metadata keeps the package name in `name` and puts the key in
        // `rename`; kind and target do not matter
        let m = json!({"packages": [
            {"name": "aib-store", "dependencies": [
                {"name": "aib-sys", "rename": "sys", "kind": "dev", "target": "cfg(windows)"}
            ]},
            {"name": "aib-sys", "dependencies": []}
        ]});
        assert_eq!(check(&m).len(), 1);
    }
}
