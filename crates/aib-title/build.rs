// SPDX-License-Identifier: Apache-2.0
//! Writes into aib-title the commit it is built from, whether the sources that go into it had
//! changes not committed, and the target it was built for (goal2 title-beat step 2,
//! PLAN-aibtitle-install-043dd-1113 r2 package 1). `aib-title build-info` prints them; the
//! installer reads them out of the file before it puts the binary where a pane runs it, so a
//! build from a PR head or a dirty tree cannot slip in (the #638 lesson, 11-11).
//!
//! Same rules as crates/aib-cloud-host/build.rs (copied: a build script cannot share code with
//! another crate's): only git is asked, no environment variable can set the commit or the dirty
//! bit; no git (a source tarball) gives `commit=unknown dirty=1`, which the installer refuses.
//! What `dirty=0` does not prove: cargo settings outside the repository (`~/.cargo/config.toml`),
//! RUSTFLAGS and ignored files are not seen; the receipt for a build names its host and flags.

use std::path::Path;
use std::process::Command;

fn git(dir: &Path, args: &[&str]) -> Option<String> {
    let mut c = Command::new("git");
    // no GIT_* from the environment chooses the repository or its settings (#650 N1)
    for (k, _) in std::env::vars_os() {
        if k.to_string_lossy().starts_with("GIT_") {
            c.env_remove(k);
        }
    }
    let out = c.arg("-C").arg(dir).args(args).output().ok()?;
    out.status
        .success()
        .then(|| String::from_utf8_lossy(&out.stdout).trim().to_owned())
}

fn emit(commit: &str, dirty: &str) {
    println!("cargo:rustc-env=AIB_TITLE_COMMIT={commit}");
    println!("cargo:rustc-env=AIB_TITLE_DIRTY={dirty}");
    // the triple cargo builds for (set by cargo for every build script; cross builds differ)
    let target = std::env::var("TARGET").unwrap_or_else(|_| "unknown".to_owned());
    println!("cargo:rustc-env=AIB_TITLE_TARGET={target}");
}

fn main() {
    let manifest = std::env::var("CARGO_MANIFEST_DIR").unwrap_or_default();
    println!("cargo:rerun-if-changed=build.rs");
    let Some(top) = git(Path::new(&manifest), &["rev-parse", "--show-toplevel"]) else {
        emit("unknown", "1");
        return;
    };
    let top = Path::new(&top);
    // What goes into the build: every crate, the workspace manifest, the lock, the pinned
    // toolchain and the repository's cargo settings (`.cargo/config.toml` can patch crates or
    // set flags; untracked it is still dirty, #650 N2). A change in any of them, or a new
    // commit (HEAD, the branch it points to, the index), runs this again. A path that does not
    // exist (usually `.cargo`) makes cargo run this every build: slower, never stale. Cargo
    // settings outside the repository (`~/.cargo/config.toml`) are not seen.
    const INPUTS: [&str; 5] = [
        "crates",
        "Cargo.toml",
        "Cargo.lock",
        "rust-toolchain.toml",
        ".cargo",
    ];
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
