// SPDX-License-Identifier: Apache-2.0
//! `doctor --pin`: a person names an ez2-ai repo and a ref; the ref is resolved to a full sha
//! once, a detached worktree is made at that sha, and PINNED records repo + sha + root. Every
//! later read checks the tree is still that sha and clean and that the marker script is there;
//! anything off is an error, never a fallback to scanning for some other checkout (Q5, a63ab
//! 11-10). Nothing here picks a version on its own.

use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::Command;

/// The file that proves a directory is the persona tools root (wrappers.BIRTH_CHECK_SCRIPT).
pub const MARKER: &str = "tools/ez2-role-birth-check.js";
pub const PINNED: &str = "PINNED.json";
pub const SCHEMA: &str = "aib-persona-tools-pin/v1";

fn git(dir: &Path, args: &[&str]) -> Result<String, String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(args)
        .output()
        .map_err(|e| format!("git: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "git {}: {}",
            args.join(" "),
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

fn full_sha(s: &str) -> bool {
    s.len() == 40
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Resolve `git_ref` in `repo` to a full sha, make (or reuse) `<pin_dir>/<sha12>` as a detached
/// worktree at that sha, check it, and write PINNED. Returns the PINNED document.
pub fn pin(repo: &Path, git_ref: &str, pin_dir: &Path, now_ms: u64) -> Result<Value, String> {
    if git_ref.is_empty() || git_ref.starts_with('-') {
        return Err(format!("bad --ref {git_ref:?}"));
    }
    let repo = std::fs::canonicalize(repo).map_err(|e| format!("{}: {e}", repo.display()))?;
    let sha = git(
        &repo,
        &[
            "rev-parse",
            "--verify",
            "--quiet",
            &format!("{git_ref}^{{commit}}"),
        ],
    )?;
    if !full_sha(&sha) {
        return Err(format!("{git_ref} resolved to {sha:?}, not a full sha"));
    }
    std::fs::create_dir_all(pin_dir).map_err(|e| format!("{}: {e}", pin_dir.display()))?;
    let root = pin_dir.join(&sha[..12]);
    if !root.exists() {
        let r = root.to_string_lossy().into_owned();
        git(&repo, &["worktree", "add", "--detach", &r, &sha])?;
    }
    let doc = json!({
        "schema": SCHEMA,
        "repo": repo.to_string_lossy(),
        "ref": git_ref,
        "sha": sha,
        "root": root.to_string_lossy(),
        "pinned_at_ms": now_ms,
    });
    verify(&doc)?;
    let tmp = pin_dir.join(format!("{PINNED}.tmp"));
    let body = serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())? + "\n";
    std::fs::write(&tmp, body).map_err(|e| format!("{}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, pin_dir.join(PINNED)).map_err(|e| format!("PINNED: {e}"))?;
    Ok(doc)
}

/// The root a PINNED document names, after checking: the tree is at the pinned sha, has no
/// changes, and holds the marker. Any failure is an error.
pub fn verify(doc: &Value) -> Result<PathBuf, String> {
    if doc.get("schema").and_then(Value::as_str) != Some(SCHEMA) {
        return Err("PINNED: unknown schema".into());
    }
    let s = |k: &str| {
        doc.get(k)
            .and_then(Value::as_str)
            .ok_or_else(|| format!("PINNED: missing {k}"))
    };
    let (sha, root) = (s("sha")?, PathBuf::from(s("root")?));
    if !full_sha(sha) {
        return Err("PINNED: sha is not a full sha".into());
    }
    if !root.is_dir() {
        return Err(format!(
            "pinned tree {} is gone — run doctor --pin again",
            root.display()
        ));
    }
    let head = git(&root, &["rev-parse", "HEAD"])?;
    if head != sha {
        return Err(format!(
            "pinned tree {} is at {head}, not the pinned {sha}",
            root.display()
        ));
    }
    let dirty = git(
        &root,
        &["status", "--porcelain", "--untracked-files=normal"],
    )?;
    if !dirty.is_empty() {
        return Err(format!("pinned tree {} has local changes", root.display()));
    }
    if !root.join(MARKER).is_file() {
        return Err(format!("pinned tree {} has no {MARKER}", root.display()));
    }
    Ok(root)
}

/// Read and check `<pin_dir>/PINNED.json`.
pub fn show(pin_dir: &Path) -> Result<(Value, PathBuf), String> {
    let p = pin_dir.join(PINNED);
    let text = std::fs::read_to_string(&p).map_err(|e| {
        format!(
            "{}: {e} — nothing pinned; run doctor --pin --repo <ez2-ai> --ref <ref>",
            p.display()
        )
    })?;
    let doc: Value = serde_json::from_str(&text).map_err(|e| format!("{}: {e}", p.display()))?;
    let root = verify(&doc)?;
    Ok((doc, root))
}
