// SPDX-License-Identifier: Apache-2.0
//! Writes into aib-cloud-host the commit it is built from and whether the sources that go into
//! it had changes not committed (DEV 4.6 r2 §9 P1: on 11-11 a build made from a PR head, with
//! the same version as main, went onto the host without #638 and nothing could tell).
//! `aib-cloud-host build-info` prints it; `aib-release host-build` reads it out of the file.
//!
//! Only git is asked; no environment variable can set it, so the record cannot say something
//! other than the tree it was built from. No git (a source tarball) ⇒ `commit=unknown dirty=1`,
//! which `aib-release host-build` refuses.

use std::path::Path;
use std::process::Command;

fn git(dir: &Path, args: &[&str]) -> Option<String> {
    let mut c = Command::new("git");
    for v in [
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
    ] {
        c.env_remove(v);
    }
    let out = c.arg("-C").arg(dir).args(args).output().ok()?;
    out.status
        .success()
        .then(|| String::from_utf8_lossy(&out.stdout).trim().to_owned())
}

fn emit(commit: &str, dirty: &str) {
    println!("cargo:rustc-env=AIB_CLOUD_HOST_COMMIT={commit}");
    println!("cargo:rustc-env=AIB_CLOUD_HOST_DIRTY={dirty}");
}

fn main() {
    let manifest = std::env::var("CARGO_MANIFEST_DIR").unwrap_or_default();
    println!("cargo:rerun-if-changed=build.rs");
    let Some(top) = git(Path::new(&manifest), &["rev-parse", "--show-toplevel"]) else {
        emit("unknown", "1");
        return;
    };
    let top = Path::new(&top);
    // What goes into the build: every crate, the workspace manifest, the lock and the pinned
    // toolchain. A change in any of them, or a new commit (HEAD, the branch it points to, the
    // index), runs this again.
    const INPUTS: [&str; 4] = ["crates", "Cargo.toml", "Cargo.lock", "rust-toolchain.toml"];
    for i in INPUTS {
        println!("cargo:rerun-if-changed={}", top.join(i).display());
    }
    let mut watch = vec!["HEAD".to_owned(), "index".to_owned()];
    if let Some(branch) = git(top, &["symbolic-ref", "-q", "HEAD"]) {
        watch.push(branch);
    }
    for w in &watch {
        // a packed ref has no file of its own: the path then does not exist and cargo reruns
        // this every build, which is slower but never stale
        if let Some(p) = git(
            top,
            &["rev-parse", "--path-format=absolute", "--git-path", w],
        ) {
            println!("cargo:rerun-if-changed={p}");
        }
    }
    let commit = git(top, &["rev-parse", "HEAD"])
        .filter(|c| c.len() == 40 && c.bytes().all(|b| b.is_ascii_hexdigit()))
        .map(|c| c.to_ascii_lowercase());
    let mut status = vec!["status", "--porcelain", "--untracked-files=normal", "--"];
    status.extend(INPUTS);
    let dirty = match git(top, &status) {
        Some(s) if s.is_empty() => "0",
        _ => "1",
    };
    emit(commit.as_deref().unwrap_or("unknown"), dirty);
}
