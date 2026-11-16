//! The run context that goes into the key (r7 §1.1, SPEC r2 A2): OS, architecture, machine
//! kind, toolchain text, the environment variables that change a result, time zone and
//! language. Variables outside the list do not move the key, but their values are hashed into
//! the ledger so they can be checked afterwards.

use crate::key::{hash_map, Hex};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::process::Command;

/// Always in the key, whatever the adapter adds (time zone and language change output).
pub const BASE_VARS: &[&str] = &["TZ", "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES"];

/// What the context is made of. `vars` is the adapter's list (cargo: [`CARGO_VARS`]).
#[derive(Clone, Debug, Default)]
pub struct Context {
    pub machine_kind: String,
    /// The tool's own version text, whole (cargo: `rustc -vV` + `cargo -V`).
    pub toolchain: String,
    /// Names that change a result; a name ending in `*` is a prefix.
    pub vars: Vec<String>,
    /// The build output folder, and whether another tree shares it (then: never a source).
    pub target_dir: String,
    pub target_shared: bool,
}

/// The cargo adapter's variables (SPEC r2 A2; #822 added the incremental ones per knife step).
pub const CARGO_VARS: &[&str] = &[
    "RUSTFLAGS",
    "RUSTDOCFLAGS",
    "CARGO_*",
    "CI",
    "RUST_TEST_THREADS",
    "RUST_BACKTRACE",
];

fn wanted(name: &str, list: &[String]) -> bool {
    BASE_VARS.contains(&name)
        || list.iter().any(|w| match w.strip_suffix('*') {
            Some(pre) => name.starts_with(pre),
            None => name == w,
        })
}

fn sha(s: &str) -> Hex {
    let d = Sha256::digest(s.as_bytes());
    d.iter().map(|b| format!("{b:02x}")).collect()
}

impl Context {
    /// The entries that make the key part, from these environment variables.
    pub fn entries(&self, env: &[(String, String)]) -> BTreeMap<String, String> {
        let mut m = BTreeMap::new();
        m.insert("os".into(), std::env::consts::OS.into());
        m.insert("arch".into(), std::env::consts::ARCH.into());
        m.insert("machine".into(), self.machine_kind.clone());
        m.insert("toolchain".into(), sha(&self.toolchain));
        m.insert("target_dir".into(), self.target_dir.clone());
        for (k, v) in env {
            if wanted(k, &self.vars) {
                m.insert(format!("env:{k}"), v.clone());
            }
        }
        m
    }

    /// The context hash for the key.
    pub fn hash(&self, env: &[(String, String)]) -> Hex {
        hash_map(&self.entries(env))
    }

    /// Every variable's value as a hash, for the ledger only (secrets never stored plain,
    /// r7 §1.3). Not part of the key.
    pub fn all_vars_hashed(env: &[(String, String)]) -> BTreeMap<String, String> {
        env.iter().map(|(k, v)| (k.clone(), sha(v))).collect()
    }

    /// A result made with a shared build folder is never a source (two trees, one binary).
    pub fn may_be_source(&self) -> bool {
        !self.target_shared
    }
}

/// `rustc -vV` and `cargo -V`, whole. Empty when a tool cannot be run: the caller treats an
/// empty toolchain as "not measured".
pub fn cargo_toolchain() -> String {
    let run = |cmd: &str, arg: &str| {
        Command::new(cmd)
            .arg(arg)
            .output()
            .ok()
            .filter(|o| o.status.success())
            .map(|o| String::from_utf8_lossy(&o.stdout).into_owned())
    };
    match (run("rustc", "-vV"), run("cargo", "-V")) {
        (Some(r), Some(c)) => format!("{r}{c}"),
        _ => String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn env(p: &[(&str, &str)]) -> Vec<(String, String)> {
        p.iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }
    fn cargo_ctx() -> Context {
        Context {
            machine_kind: "mac-arm".into(),
            toolchain: "rustc 1.99.0\ncargo 1.99.0\n".into(),
            vars: CARGO_VARS.iter().map(|s| s.to_string()).collect(),
            target_dir: "target".into(),
            target_shared: false,
        }
    }

    #[test]
    fn a_listed_variable_moves_the_key_and_an_unlisted_one_does_not() {
        let c = cargo_ctx();
        let base = c.hash(&env(&[("RUSTFLAGS", ""), ("HOME", "/a")]));
        assert_ne!(
            base,
            c.hash(&env(&[("RUSTFLAGS", "-D warnings"), ("HOME", "/a")]))
        );
        assert_eq!(base, c.hash(&env(&[("RUSTFLAGS", ""), ("HOME", "/b")])));
    }

    #[test]
    fn a_prefix_catches_the_incremental_switches_and_time_zone_always_counts() {
        let c = cargo_ctx();
        let a = c.hash(&env(&[]));
        assert_ne!(a, c.hash(&env(&[("CARGO_INCREMENTAL", "1")])));
        assert_ne!(a, c.hash(&env(&[("CARGO_PROFILE_TEST_DEBUG", "0")])));
        let none = Context::default();
        assert_ne!(
            none.hash(&env(&[])),
            none.hash(&env(&[("TZ", "Asia/Taipei")]))
        );
    }

    #[test]
    fn the_toolchain_and_the_build_folder_are_in_the_key() {
        let a = cargo_ctx();
        let mut b = cargo_ctx();
        b.toolchain = "rustc 1.100.0\n".into();
        assert_ne!(a.hash(&[]), b.hash(&[]));
        let mut d = cargo_ctx();
        d.target_dir = "/shared/target".into();
        assert_ne!(a.hash(&[]), d.hash(&[]));
    }

    #[test]
    fn a_shared_build_folder_is_never_a_source_and_values_are_only_hashed() {
        let mut c = cargo_ctx();
        assert!(c.may_be_source());
        c.target_shared = true;
        assert!(!c.may_be_source());
        let h = Context::all_vars_hashed(&env(&[("GH_TOKEN", "secret-value")]));
        assert!(!h["GH_TOKEN"].contains("secret"));
        assert_eq!(h["GH_TOKEN"].len(), 64);
    }
}
