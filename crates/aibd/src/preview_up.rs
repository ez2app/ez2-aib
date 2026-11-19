//! 上預覽 P2: merge an item's heads into one preview's own worktree (N-pv-up; DESIGN-pv-up-a9136
//! -1119 r1 §1.3; 5d33a CHECK-pv-up-5d33a-1119 M2 / M3 / L3).
//!
//! - The repository is the one this aibd was set up with (`AIBD_PREVIEW_REPO`), never one named
//!   by the tree or the page (M3). Its objects are used as they are: no fetch (M2).
//! - The worktree is `<data>/previews/<id>/src`, aibd's own; the person's trees are not touched.
//! - Every git call runs with a cleared environment (PATH, and HOME = the preview's home), no
//!   system config and hooks pointed at an empty folder (M2), so nothing in the repository runs.
//! - Heads are merged dependencies first (the tree's order, reversed). A conflict is resolved
//!   by itself only when every block is two sides each adding lines to nothing (diff3 base
//!   empty, §23 9.); that is written down (L3). Any other conflict aborts the merge: state
//!   `conflict`, with the node, its head, its owner and the files.
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// The repository previews are merged from (set by the install, not by a page or the tree).
pub const ENV_REPO: &str = "AIBD_PREVIEW_REPO";

/// A git call's environment (5d33a M2, L2): only these, written here.
pub fn git_env(home: &Path) -> Vec<(String, String)> {
    let mut out = vec![
        ("HOME".to_owned(), home.to_string_lossy().into_owned()),
        ("GIT_CONFIG_NOSYSTEM".to_owned(), "1".to_owned()),
        ("GIT_TERMINAL_PROMPT".to_owned(), "0".to_owned()),
        ("LC_ALL".to_owned(), "C".to_owned()),
    ];
    if let Ok(p) = std::env::var("PATH") {
        out.push(("PATH".to_owned(), p));
    }
    if cfg!(windows) {
        out.push((
            "USERPROFILE".to_owned(),
            home.to_string_lossy().into_owned(),
        ));
        for k in ["SystemRoot", "TEMP", "TMP"] {
            if let Ok(v) = std::env::var(k) {
                out.push((k.to_owned(), v));
            }
        }
    }
    out
}

/// Run git in `cwd` with the fixed environment and no hooks; stdout or why not.
fn git(cwd: &Path, home: &Path, hooks: &Path, args: &[&str]) -> Result<String, String> {
    let out = Command::new("git")
        .env_clear()
        .envs(git_env(home))
        .arg("-C")
        .arg(cwd)
        .arg("-c")
        .arg(format!("core.hooksPath={}", hooks.display()))
        .args([
            "-c",
            "core.fsmonitor=false",
            "-c",
            "protocol.allow=never",
            "-c",
            "user.name=aibd",
            "-c",
            "user.email=aibd@localhost",
            "-c",
            "merge.conflictStyle=diff3",
            "-c",
            "commit.gpgSign=false",
        ])
        .args(args)
        .stdin(Stdio::null())
        .output()
        .map_err(|e| e.to_string())?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    } else {
        Err(String::from_utf8_lossy(&out.stderr)
            .lines()
            .next()
            .unwrap_or("git failed")
            .to_owned())
    }
}

/// One conflicted file's text with every block resolved as ours-then-theirs, or `None` when a
/// block is more than both sides adding lines to nothing (diff3 base empty).
pub fn both_added(text: &str) -> Option<(String, usize)> {
    let mut out = String::new();
    let mut blocks = 0;
    let mut lines = text.split_inclusive('\n');
    while let Some(l) = lines.next() {
        if !l.starts_with("<<<<<<< ") {
            out.push_str(l);
            continue;
        }
        let (mut ours, mut base, mut theirs) = (String::new(), String::new(), String::new());
        let mut part = 0;
        let (mut closed, mut saw_base) = (false, false);
        for l in lines.by_ref() {
            if l.starts_with("||||||| ") || l.trim_end() == "|||||||" {
                part = 1;
                saw_base = true;
            } else if l.trim_end() == "=======" {
                part = 2;
            } else if l.starts_with(">>>>>>> ") || l.trim_end() == ">>>>>>>" {
                closed = true;
                break;
            } else {
                [&mut ours, &mut base, &mut theirs][part].push_str(l);
            }
        }
        if !closed || !saw_base || part != 2 || !base.is_empty() {
            return None;
        }
        out.push_str(&ours);
        out.push_str(&theirs);
        blocks += 1;
    }
    Some((out, blocks))
}

/// What a merge run gives back.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Merged {
    /// The worktree's head after every head went in, and the files resolved by themselves.
    Done { head: String, resolved: Vec<Value> },
    /// A head did not go in: which node, which head, why, which files.
    Conflict {
        node: String,
        head: String,
        why: String,
        files: Vec<String>,
    },
}

/// Merge `heads` (`[{id, head}]`, dependencies last in the list) on top of `base` in
/// `<dir>/src` from `repo`. Blocking.
pub fn merge(repo: &Path, dir: &Path, base: &str, heads: &[Value]) -> Result<Merged, String> {
    let home = dir.join("home");
    let hooks = dir.join("no-hooks");
    std::fs::create_dir_all(&home).map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&hooks).map_err(|e| e.to_string())?;
    let src = dir.join("src");
    let ok = |s: &str| crate::preview_tree::is_head(s);
    if !ok(base) {
        return Err("base_bad".into());
    }
    git(
        repo,
        &home,
        &hooks,
        &["cat-file", "-e", &format!("{base}^{{commit}}")],
    )
    .map_err(|_| "base_missing".to_owned())?;
    if src.join(".git").exists() {
        git(&src, &home, &hooks, &["merge", "--abort"]).ok();
        git(&src, &home, &hooks, &["reset", "--hard", base])?;
        git(&src, &home, &hooks, &["clean", "-fdx", "-e", "target"])?;
    } else {
        let s = src.to_string_lossy().into_owned();
        git(
            repo,
            &home,
            &hooks,
            &["worktree", "add", "--detach", &s, base],
        )?;
    }
    let mut resolved = Vec::new();
    for h in heads.iter().rev() {
        let (node, head) = (
            h["id"].as_str().unwrap_or("").to_owned(),
            h["head"].as_str().unwrap_or("").to_owned(),
        );
        if !ok(&head) {
            continue;
        }
        if git(
            &src,
            &home,
            &hooks,
            &["cat-file", "-e", &format!("{head}^{{commit}}")],
        )
        .is_err()
        {
            return Ok(Merged::Conflict {
                node,
                head,
                why: "head_missing".into(),
                files: Vec::new(),
            });
        }
        if git(
            &src,
            &home,
            &hooks,
            &["merge-base", "--is-ancestor", &head, "HEAD"],
        )
        .is_ok()
        {
            continue;
        }
        let msg = format!("preview: merge {node} {head}");
        if git(
            &src,
            &home,
            &hooks,
            &["merge", "--no-ff", "-m", &msg, &head],
        )
        .is_ok()
        {
            continue;
        }
        let files: Vec<String> = git(
            &src,
            &home,
            &hooks,
            &["diff", "--name-only", "--diff-filter=U"],
        )
        .unwrap_or_default()
        .lines()
        .map(str::to_owned)
        .collect();
        let mut fixed = Vec::new();
        for f in &files {
            let p = src.join(f);
            let text = std::fs::read_to_string(&p).unwrap_or_default();
            match both_added(&text) {
                Some((t, n)) if n > 0 => {
                    if std::fs::write(&p, t).is_err() {
                        break;
                    }
                    fixed.push(json!({"file": f, "blocks": n, "node": node, "head": head}));
                }
                _ => break,
            }
        }
        if files.is_empty() || fixed.len() != files.len() {
            git(&src, &home, &hooks, &["merge", "--abort"]).ok();
            return Ok(Merged::Conflict {
                node,
                head,
                why: "conflict".into(),
                files,
            });
        }
        git(&src, &home, &hooks, &["add", "--", "."])?;
        git(&src, &home, &hooks, &["commit", "--no-edit", "-m", &msg])?;
        resolved.extend(fixed);
    }
    let head = git(&src, &home, &hooks, &["rev-parse", "HEAD"])?
        .trim()
        .to_owned();
    Ok(Merged::Done { head, resolved })
}

/// The base heads go on: the repository's `origin/main`, else `main` (no fetch, M2).
pub fn base_of(repo: &Path, dir: &Path) -> Result<String, String> {
    let (home, hooks) = (dir.join("home"), dir.join("no-hooks"));
    std::fs::create_dir_all(&home).map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&hooks).map_err(|e| e.to_string())?;
    ["refs/remotes/origin/main", "refs/heads/main"]
        .iter()
        .find_map(|r| {
            git(
                repo,
                &home,
                &hooks,
                &["rev-parse", "--verify", "--quiet", r],
            )
            .ok()
            .map(|o| o.trim().to_owned())
        })
        .filter(|h| crate::preview_tree::is_head(h))
        .ok_or_else(|| "base_missing".to_owned())
}

/// The build's environment: the git list plus where cargo and rustup keep their things (the
/// person's own, since HOME is the preview's), and the target folder.
pub fn build_env(dir: &Path) -> Vec<(String, String)> {
    let mut out = git_env(&dir.join("home"));
    let real_home =
        std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(PathBuf::from);
    for (k, sub) in [("CARGO_HOME", ".cargo"), ("RUSTUP_HOME", ".rustup")] {
        let v = std::env::var_os(k)
            .map(PathBuf::from)
            .or_else(|| real_home.as_ref().map(|h| h.join(sub)));
        if let Some(v) = v {
            out.push((k.to_owned(), v.to_string_lossy().into_owned()));
        }
    }
    out.push((
        "CARGO_TARGET_DIR".to_owned(),
        dir.join("target").to_string_lossy().into_owned(),
    ));
    out
}

/// Build aibd in `<dir>/src` (`cargo build --release --locked -p aibd`); the built file or the
/// first error line. Blocking, can take minutes.
pub fn build(dir: &Path) -> Result<PathBuf, String> {
    let out = Command::new("cargo")
        .env_clear()
        .envs(build_env(dir))
        .current_dir(dir.join("src"))
        .args(["build", "--release", "--locked", "-p", "aibd"])
        .stdin(Stdio::null())
        .output()
        .map_err(|e| format!("cargo: {e}"))?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        let first = err
            .lines()
            .find(|l| l.starts_with("error"))
            .or_else(|| err.lines().last())
            .unwrap_or("build failed");
        return Err(first.chars().take(300).collect());
    }
    let bin = dir
        .join("target")
        .join("release")
        .join(crate::preview::bin_name());
    if bin.is_file() {
        Ok(bin)
    } else {
        Err("build_missing".into())
    }
}

/// The VERSION text placed next to a build: `<head12> preview`, the summary, `fix` / `feature`,
/// then one `一句 | 作者席 | 卡號` per node head.
pub fn version_text(head: &str, summary: &str, kind: &str, heads: &[Value]) -> String {
    let short: String = head.chars().take(12).collect();
    let mut t = format!(
        "{short} preview\n{}\n{}\n",
        summary.replace(['\n', '\r'], " "),
        if kind == "feature" { "feature" } else { "fix" }
    );
    for h in heads {
        let field = |k: &str| h[k].as_str().unwrap_or("").replace(['\n', '\r', '|'], " ");
        let text = match field("title") {
            t if t.is_empty() => field("id"),
            t => t,
        };
        t.push_str(&format!("{text} | {} | {}\n", field("owner"), field("id")));
    }
    t
}

/// Put `built` as the preview's build (`<dir>/aibd` through `.next` and a rename) and its
/// VERSION; the list check (GET previews) then notes it and swaps a fix in by itself.
pub fn place(dir: &Path, built: &Path, version: &str) -> Result<(), String> {
    let name = crate::preview::bin_name();
    let next = dir.join(format!("{name}.next"));
    std::fs::copy(built, &next).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&next, std::fs::Permissions::from_mode(0o755))
            .map_err(|e| e.to_string())?;
    }
    std::fs::rename(&next, dir.join(name)).map_err(|e| e.to_string())?;
    let vnext = dir.join("VERSION.next");
    std::fs::write(&vnext, version).map_err(|e| e.to_string())?;
    std::fs::rename(&vnext, dir.join(crate::preview::VERSION_FILE)).map_err(|e| e.to_string())
}

/// Items being put up now: preview id → state (merging / building).
static RUNNING: std::sync::Mutex<std::collections::BTreeMap<String, &'static str>> =
    std::sync::Mutex::new(std::collections::BTreeMap::new());

/// The state of a preview being put up, if one is.
pub fn running(id: &str) -> Option<&'static str> {
    RUNNING
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .get(id)
        .copied()
}

fn set_running(id: &str, state: Option<&'static str>) {
    let mut m = RUNNING.lock().unwrap_or_else(|e| e.into_inner());
    match state {
        Some(s) => m.insert(id.to_owned(), s),
        None => m.remove(id),
    };
}

/// Merge → build → place for one preview (blocking). A failure is written in the preview's
/// version record as `conflict` / `build_failed` with what and whose (P5 sends it on).
pub fn run(repo: &Path, dir: &Path, id: &str, heads: &[Value], summary: &str, kind: &str) {
    use crate::preview::{append_version, NodeHead, Version};
    let nodes: Vec<NodeHead> = heads
        .iter()
        .map(|h| NodeHead {
            id: h["id"].as_str().unwrap_or("").to_owned(),
            head: h["head"].as_str().unwrap_or("").to_owned(),
        })
        .collect();
    let fail = |state: &str, error: String, head: String| {
        let _ = append_version(
            dir,
            Version {
                head,
                nodes: nodes.clone(),
                at: crate::sessions::now_rfc3339(),
                state: state.into(),
                summary: summary.into(),
                kind: kind.into(),
                error: Some(error),
                ..Default::default()
            },
        );
    };
    set_running(id, Some("merging"));
    let merged = base_of(repo, dir).and_then(|b| merge(repo, dir, &b, heads));
    let head = match merged {
        Ok(Merged::Done { head, resolved }) => {
            if !resolved.is_empty() {
                let what = Value::from(resolved).to_string();
                tracing::info!(preview = id, resolved = %what, "preview: resolved by itself");
            }
            head
        }
        Ok(Merged::Conflict {
            node,
            head,
            why,
            files,
        }) => {
            fail(
                "conflict",
                format!("{why}: {node} {head} {}", files.join(" ")),
                head,
            );
            set_running(id, None);
            return;
        }
        Err(e) => {
            fail("conflict", e, String::new());
            set_running(id, None);
            return;
        }
    };
    set_running(id, Some("building"));
    let placed =
        build(dir).and_then(|bin| place(dir, &bin, &version_text(&head, summary, kind, heads)));
    if let Err(e) = placed {
        fail("build_failed", e, head);
    }
    set_running(id, None);
}

/// `short` as the full 40-hex commit it names in `repo` (5d33a CHECK-pv-up-p23 M1: a 7-hex head
/// could be stood in for by another commit with the same start); `None` when it names none or
/// more than one.
pub fn full_head(repo: &Path, short: &str, scratch: &Path) -> Option<String> {
    if !crate::preview_tree::is_head(short) {
        return None;
    }
    let (home, hooks) = (scratch.join("home"), scratch.join("no-hooks"));
    std::fs::create_dir_all(&home).ok()?;
    std::fs::create_dir_all(&hooks).ok()?;
    let out = git(
        repo,
        &home,
        &hooks,
        &[
            "rev-parse",
            "--verify",
            "--quiet",
            &format!("{short}^{{commit}}"),
        ],
    )
    .ok()?;
    let full = out.trim().to_ascii_lowercase();
    (full.len() == 40 && full.starts_with(&short.to_ascii_lowercase())).then_some(full)
}

/// The tree's heads written out in full from `repo`, and the agreement's hash taken over them;
/// a head that names no single commit stays as it is and is listed in `unresolved`.
pub fn expand_heads(v: &mut Value, repo: Option<&Path>) {
    let mut unresolved = Vec::new();
    // 5d33a CHECK-pv-up-p23 r1 N: git's home is in aibd's own folder, not the shared temp
    let scratch = crate::workspace::data_file(crate::preview::DIR).map(|d| d.join(".rev"));
    let full = |h: &str| {
        repo.zip(scratch.as_deref())
            .and_then(|(r, sc)| full_head(r, h, sc))
    };
    if let Some(nodes) = v.get_mut("nodes").and_then(Value::as_array_mut) {
        for n in nodes {
            let h = n["head"].as_str().unwrap_or("").to_owned();
            if !crate::preview_tree::is_head(&h) {
                continue;
            }
            match full(&h) {
                Some(f) => n["head"] = json!(f),
                None => unresolved.push(json!({"id": n["id"], "head": h})),
            }
        }
    }
    if let Some(heads) = v.get_mut("heads").and_then(Value::as_array_mut) {
        for x in heads.iter_mut() {
            if let Some(f) = full(x["head"].as_str().unwrap_or("")) {
                x["head"] = json!(f);
            }
        }
    }
    let heads: Vec<Value> = v["heads"].as_array().cloned().unwrap_or_default();
    v["heads_sha256"] = json!(crate::preview_tree::heads_digest(&heads));
    v["unresolved"] = json!(unresolved);
}

/// The repository from this aibd's own setting (M3); `None` when it is not a folder.
pub fn repo() -> Option<PathBuf> {
    std::env::var_os(ENV_REPO)
        .map(PathBuf::from)
        .filter(|p| p.is_dir())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_two_sides_adding_lines_to_nothing_is_resolved_by_itself() {
        let added = "a\n<<<<<<< ours\nX\n||||||| base\n=======\nY\n>>>>>>> theirs\nz\n";
        assert_eq!(both_added(added), Some(("a\nX\nY\nz\n".to_owned(), 1)));
        let changed = "<<<<<<< ours\nX\n||||||| base\nold\n=======\nY\n>>>>>>> theirs\n";
        assert_eq!(both_added(changed), None, "reason: changed_line_resolved");
        let no_base = "<<<<<<< ours\nX\n=======\nY\n>>>>>>> theirs\n";
        assert_eq!(
            both_added(no_base).map(|r| r.1),
            None,
            "reason: two_way_resolved"
        );
        assert_eq!(both_added("plain\n"), Some(("plain\n".to_owned(), 0)));
    }

    #[test]
    fn the_version_text_carries_each_nodes_line_author_and_card() {
        let heads = vec![json!({"id": "N-a", "head": "a", "title": "搜尋|快", "owner": "a9136"})];
        let t = version_text("0123456789abcdef", "多行\n摘要", "feature", &heads);
        let lines: Vec<&str> = t.lines().collect();
        assert_eq!(lines[0], "0123456789ab preview");
        assert_eq!(lines[1], "多行 摘要", "reason: summary_breaks_lines");
        assert_eq!(lines[2], "feature");
        assert_eq!(
            lines[3], "搜尋 快 | a9136 | N-a",
            "reason: change_line_lost"
        );
        assert_eq!(version_text("h", "", "x", &[]).lines().nth(2), Some("fix"));
    }

    #[test]
    fn a_placed_build_is_what_the_list_finds() {
        let d = tempfile::tempdir().unwrap();
        let built = d.path().join("built");
        std::fs::write(&built, b"bin-2").unwrap();
        let v = version_text("abcdef012345", "s", "fix", &[]);
        place(d.path(), &built, &v).unwrap();
        let f = crate::preview::find(d.path()).expect("reason: placed_not_found");
        assert_eq!(f.version, "abcdef012345 preview");
        assert_eq!(f.sha256, crate::preview::sha256_hex(b"bin-2"));
        assert!(!d.path().join("aibd.next").exists() && !d.path().join("VERSION.next").exists());
    }

    #[test]
    fn the_build_gets_cargo_homes_and_its_own_target() {
        let env = build_env(Path::new("/p"));
        let get = |k: &str| env.iter().find(|(n, _)| n == k).map(|(_, v)| v.clone());
        assert_eq!(
            get("CARGO_TARGET_DIR"),
            Some(
                Path::new("/p")
                    .join("target")
                    .to_string_lossy()
                    .into_owned()
            )
        );
        assert_eq!(
            get("HOME"),
            Some(Path::new("/p").join("home").to_string_lossy().into_owned())
        );
    }

    #[test]
    fn git_gets_only_the_fixed_environment() {
        let env = git_env(Path::new("/p/home"));
        let names: Vec<&str> = env.iter().map(|(k, _)| k.as_str()).collect();
        assert!(names.contains(&"GIT_CONFIG_NOSYSTEM"));
        assert!(
            names.iter().all(|k| [
                "HOME",
                "GIT_CONFIG_NOSYSTEM",
                "GIT_TERMINAL_PROMPT",
                "LC_ALL",
                "PATH",
                "USERPROFILE",
                "SystemRoot"
            ]
            .contains(k)),
            "reason: env_leaks {names:?}"
        );
        assert_eq!(
            env.iter()
                .find(|(k, _)| k == "HOME")
                .map(|(_, v)| v.as_str()),
            Some("/p/home")
        );
    }

    /// A real repository: two branches each adding a line at the same place go in by
    /// themselves; a changed line stops the merge and leaves the worktree clean.
    #[test]
    fn heads_merge_into_the_previews_own_worktree() {
        let d = tempfile::tempdir().unwrap();
        let repo = d.path().join("repo");
        std::fs::create_dir_all(&repo).unwrap();
        let home = d.path().join("h");
        let hooks = d.path().join("nh");
        std::fs::create_dir_all(&home).unwrap();
        std::fs::create_dir_all(&hooks).unwrap();
        let g = |args: &[&str]| git(&repo, &home, &hooks, args).unwrap();
        g(&["init", "-q", "-b", "main"]);
        std::fs::write(repo.join("f"), "top\nend\n").unwrap();
        g(&["add", "f"]);
        g(&["commit", "-q", "-m", "base"]);
        let base = g(&["rev-parse", "HEAD"]).trim().to_owned();
        let branch = |name: &str, text: &str| {
            g(&["checkout", "-q", "-b", name, &base]);
            std::fs::write(repo.join("f"), text).unwrap();
            g(&["commit", "-q", "-am", name]);
            g(&["rev-parse", "HEAD"]).trim().to_owned()
        };
        let a = branch("a", "top\nA\nend\n");
        let b = branch("b", "top\nB\nend\n");
        let c = branch("c", "TOP\nend\n");
        // M1: a short head is written out in full; one naming nothing is not
        let full =
            full_head(&repo, &a[..9], &d.path().join("rev")).expect("reason: short_not_expanded");
        assert_eq!(full, a, "reason: wrong_commit");
        assert_eq!(full_head(&repo, "0000000", &d.path().join("rev")), None);
        let pv = d.path().join("pv");
        let heads = vec![json!({"id": "B", "head": b}), json!({"id": "A", "head": a})];
        match merge(&repo, &pv, &base, &heads).unwrap() {
            Merged::Done { resolved, .. } => {
                assert_eq!(resolved.len(), 1, "reason: both_added_not_resolved");
                let f = std::fs::read_to_string(pv.join("src").join("f")).unwrap();
                assert!(f.contains("A\n") && f.contains("B\n"), "{f}");
            }
            other => panic!("reason: merge_failed {other:?}"),
        }
        let heads = vec![json!({"id": "C", "head": c}), json!({"id": "A", "head": a})];
        match merge(&repo, &pv, &base, &heads).unwrap() {
            Merged::Conflict { node, files, .. } => {
                assert_eq!((node.as_str(), files.len()), ("C", 1));
            }
            other => panic!("reason: conflict_merged {other:?}"),
        }
        let st = git(&pv.join("src"), &home, &hooks, &["status", "--porcelain"]).unwrap();
        assert!(st.trim().is_empty(), "reason: worktree_left_dirty {st}");
    }
}
