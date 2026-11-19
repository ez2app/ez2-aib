//! N-web-worktree: a seat that may change code (dgrs) gets its own git worktree of a repo
//! aibd registered, never the person's own tree and never a folder a page names
//! (DECISION-council-webonly-q12-519d0-1119 Q1 (a); BOUNDARY-webonly-5d33a-1119 §1, r2 §1.4).
//!
//! This file is the part that needs no fingerprint: the list of project settings a seat
//! would load from the tree (taken at registration, taken again before every open and
//! compared), where a tree goes and what its branch is called, opening a tree with no git
//! hooks, and what is left unmerged before a tree is taken away. Registering a repo and
//! opening a dgrs seat each ask for a fingerprint first; that call lives with the act.
//!
//! A worktree is not a sandbox: a same-user AI can still reach other folders, so no page
//! text built from here says "isolated" (decision Q1 point 5).

use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// Top-level files the providers load as project settings (the same names as
/// [`crate::seats::PROJECT_SETTINGS`]) plus every file under [`crate::seats::PROJECT_DIR`].
const TOP_FILES: [&str; 4] = crate::seats::PROJECT_SETTINGS;

/// The keys of a settings file that change what a seat may run (BOUNDARY r2 §1.4 item 1):
/// only their names are listed, never their values.
const WATCHED_KEYS: [&str; 3] = ["hooks", "permissions", "mcpServers"];

/// Files under `.claude/` deeper than this are refused rather than walked forever.
const MAX_DEPTH: usize = 8;
/// More files than this under `.claude/` ⇒ refused (a window cannot list them all).
const MAX_FILES: usize = 200;

/// What a tree would load: `files` is `(path with '/', first 12 hex of SHA-256)`, sorted;
/// `keys` is the watched key names, sorted (`.mcp.json:server:<name>`,
/// `<file>:hooks.<event>`, `<file>:permissions`, …).
#[derive(Debug, Clone, PartialEq, Eq, Default, serde::Serialize, serde::Deserialize)]
pub struct Manifest {
    pub files: Vec<(String, String)>,
    pub keys: Vec<String>,
    /// First 12 hex of SHA-256 of the repo's shared `.git/config` (5d33a L2 on 71b294f6: a
    /// tree shares it with the person's repo, so a seat can add `core.fsmonitor` there); empty
    /// when the list was taken without git ([`manifest`]).
    #[serde(default)]
    pub git_config: String,
}

impl Manifest {
    pub fn is_empty(&self) -> bool {
        self.files.is_empty()
    }
}

fn sha12(bytes: &[u8]) -> String {
    let d = Sha256::digest(bytes);
    d.iter().take(6).map(|b| format!("{b:02x}")).collect()
}

/// `a/b` whatever the platform wrote (Windows gives `a\b`).
fn slash(p: &Path) -> String {
    p.components()
        .map(|c| c.as_os_str().to_string_lossy().into_owned())
        .collect::<Vec<_>>()
        .join("/")
}

fn json_keys(rel: &str, bytes: &[u8], keys: &mut Vec<String>) {
    let Ok(v) = serde_json::from_slice::<serde_json::Value>(bytes) else {
        // a settings file the provider cannot parse still counts by its hash; say so
        keys.push(format!("{rel}:unreadable"));
        return;
    };
    let Some(o) = v.as_object() else { return };
    for k in WATCHED_KEYS {
        let Some(inner) = o.get(k) else { continue };
        match inner.as_object() {
            Some(m) if !m.is_empty() => {
                for name in m.keys() {
                    keys.push(format!("{rel}:{k}.{name}"));
                }
            }
            _ => keys.push(format!("{rel}:{k}")),
        }
    }
}

fn walk(top: &Path, dir: &Path, depth: usize, m: &mut Manifest) -> Result<(), &'static str> {
    if depth > MAX_DEPTH {
        return Err("settings_too_deep");
    }
    let mut entries: Vec<_> = std::fs::read_dir(dir)
        .map_err(|_| "settings_unreadable")?
        .filter_map(Result::ok)
        .collect();
    entries.sort_by_key(|e| e.file_name());
    for e in entries {
        let p = e.path();
        let ft = e.file_type().map_err(|_| "settings_unreadable")?;
        if ft.is_symlink() {
            // the provider follows it to a file outside the tree that the list cannot pin
            return Err("settings_symlink");
        }
        if ft.is_dir() {
            walk(top, &p, depth + 1, m)?;
            continue;
        }
        if !ft.is_file() {
            // a FIFO or a device would hold the read open for ever (5d33a L1 on 71b294f6)
            return Err("settings_not_file");
        }
        if m.files.len() >= MAX_FILES {
            return Err("settings_too_many");
        }
        let bytes = std::fs::read(&p).map_err(|_| "settings_unreadable")?;
        let rel = slash(p.strip_prefix(top).unwrap_or(&p));
        if rel.ends_with(".json")
            && rel
                .rsplit('/')
                .next()
                .is_some_and(|n| n.starts_with("settings"))
        {
            json_keys(&rel, &bytes, &mut m.keys);
        }
        m.files.push((rel, sha12(&bytes)));
    }
    Ok(())
}

/// The project settings at the top of `top` (a repo or a tree), read by aibd itself
/// (BOUNDARY r2 §1.4 item 4: no hash a page or a seat sends is taken).
pub fn manifest(top: &Path) -> Result<Manifest, &'static str> {
    let mut m = Manifest::default();
    for f in TOP_FILES {
        let p = top.join(f);
        let Ok(meta) = std::fs::symlink_metadata(&p) else {
            continue;
        };
        if meta.file_type().is_symlink() {
            return Err("settings_symlink");
        }
        if !meta.is_file() {
            return Err("settings_not_file");
        }
        let bytes = std::fs::read(&p).map_err(|_| "settings_unreadable")?;
        if f == ".mcp.json" {
            match serde_json::from_slice::<serde_json::Value>(&bytes) {
                Ok(v) => {
                    if let Some(s) = v.get("mcpServers").and_then(|s| s.as_object()) {
                        m.keys
                            .extend(s.keys().map(|n| format!(".mcp.json:server:{n}")));
                    }
                }
                Err(_) => m.keys.push(".mcp.json:unreadable".into()),
            }
        }
        m.files.push((f.to_string(), sha12(&bytes)));
    }
    let d = top.join(crate::seats::PROJECT_DIR);
    match std::fs::symlink_metadata(&d) {
        Ok(meta) if meta.file_type().is_symlink() => return Err("settings_symlink"),
        Ok(meta) if meta.is_dir() => walk(top, &d, 0, &mut m)?,
        _ => {}
    }
    m.files.sort();
    m.keys.sort();
    Ok(m)
}

/// Before every open (and every re-open, rejoin, or base move): the tree's list must be
/// the one the person's fingerprint covered; any file more, less or changed ⇒ refused
/// (BOUNDARY r2 §1.4 item 2: a dgrs seat can rewrite its own CLAUDE.md or settings, and
/// the next open would load what it wrote).
/// File names a provider also loads from a folder below the top (5d33a L3 on 71b294f6).
const NESTED_FILES: [&str; 3] = ["CLAUDE.md", "CLAUDE.local.md", "AGENTS.md"];

/// [`manifest`] plus every [`NESTED_FILES`] anywhere in the tree (committed or not, as git
/// lists them) and the shared `.git/config` hash: what registration shows and what every
/// open compares.
pub fn manifest_for(tree: &Path, hooks: &Path) -> Result<Manifest, &'static str> {
    let mut m = manifest(tree)?;
    let mut spec: Vec<String> = vec![
        "ls-files".into(),
        "-z".into(),
        "-c".into(),
        "-o".into(),
        "--".into(),
    ];
    spec.extend(NESTED_FILES.iter().map(|f| format!(":(glob)**/{f}")));
    let refs: Vec<&str> = spec.iter().map(String::as_str).collect();
    let listed = git(tree, hooks, &refs).map_err(|_| "settings_unreadable")?;
    let mut nested: Vec<&str> = listed.split('\0').filter(|r| r.contains('/')).collect();
    nested.sort_unstable();
    nested.dedup();
    for rel in nested {
        if m.files.len() >= MAX_FILES {
            return Err("settings_too_many");
        }
        let p = tree.join(rel);
        let meta = std::fs::symlink_metadata(&p).map_err(|_| "settings_unreadable")?;
        if meta.is_symlink() {
            return Err("settings_symlink");
        }
        if !meta.is_file() {
            return Err("settings_not_file");
        }
        let bytes = std::fs::read(&p).map_err(|_| "settings_unreadable")?;
        m.files.push((rel.to_string(), sha12(&bytes)));
    }
    m.files.sort();
    m.files.dedup();
    let common = git(
        tree,
        hooks,
        &["rev-parse", "--path-format=absolute", "--git-common-dir"],
    )
    .map_err(|_| "settings_unreadable")?;
    let cfg =
        std::fs::read(Path::new(&common).join("config")).map_err(|_| "settings_unreadable")?;
    m.git_config = sha12(&config_but_aibds_own(&cfg));
    Ok(m)
}

/// The shared config without the one key aibd itself writes there when it opens a tree
/// ([`open_tree`] sets `extensions.worktreeConfig`): without this, the first seat's tree
/// changed the hash and every later open of the same repo was refused as
/// `project_settings_changed`. An `[extensions]` header line is left out too (it is there only
/// for that key); any other extension a seat adds still changes the hash.
fn config_but_aibds_own(cfg: &[u8]) -> Vec<u8> {
    let text = String::from_utf8_lossy(cfg);
    let mut out = String::new();
    for line in text.lines() {
        let t: String = line
            .chars()
            .filter(|c| !c.is_whitespace())
            .collect::<String>()
            .to_ascii_lowercase();
        if t == "[extensions]" || t == "worktreeconfig=true" {
            continue;
        }
        out.push_str(line);
        out.push('\n');
    }
    out.into_bytes()
}

pub fn check_unchanged(
    registered: &Manifest,
    tree: &Path,
    hooks: &Path,
) -> Result<(), &'static str> {
    match manifest_for(tree, hooks) {
        Ok(now) if &now == registered => Ok(()),
        Ok(_) => Err("project_settings_changed"),
        Err(e) => Err(e),
    }
}

/// A registered repo's name: 1–40 of a-z 0-9 -, not starting with `-`.
pub fn repo_name_ok(name: &str) -> bool {
    (1..=40).contains(&name.len())
        && !name.starts_with('-')
        && name
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

/// `<data>/seat-work/<seat>/<repo>`: under aibd's own folder, never the person's tree.
pub fn tree_path(seat_home: &Path, repo: &str) -> PathBuf {
    seat_home.join(repo)
}

/// `seat/<seat code>-<MMDD>`.
pub fn branch_name(seat_code: &str, mmdd: &str) -> String {
    format!("seat/{seat_code}-{mmdd}")
}

/// A git call's environment, the same allow-list as preview_up (5d33a L2 on 71b294f6): no
/// system or user config (HOME is aibd's own folder), no prompts.
fn git_env(home: &Path) -> Vec<(String, String)> {
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

/// Every git call: fixed environment, no hooks, no fsmonitor program, no network, stdin
/// closed. `hooks` is aibd's empty folder; its parent is aibd's own HOME for git.
pub(crate) fn git(dir: &Path, hooks: &Path, args: &[&str]) -> Result<String, String> {
    let home = hooks.parent().unwrap_or(hooks);
    let out = Command::new("git")
        .env_clear()
        .envs(git_env(home))
        .arg("-C")
        .arg(dir)
        .arg("-c")
        .arg(format!("core.hooksPath={}", hooks.display()))
        .args(["-c", "core.fsmonitor=false", "-c", "protocol.allow=never"])
        .args(args)
        .stdin(Stdio::null())
        .output()
        .map_err(|e| format!("git did not start: {e}"))?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
    } else {
        Err(String::from_utf8_lossy(&out.stderr).trim().to_string())
    }
}

/// The repo's top folder, or why not (a registered path must be a repo's top).
pub fn repo_top(path: &Path, hooks: &Path) -> Result<PathBuf, String> {
    let top = git(path, hooks, &["rev-parse", "--show-toplevel"])?;
    Ok(PathBuf::from(top))
}

/// Open the seat's tree on `base` with a new branch; every git call here runs with
/// `core.hooksPath` at aibd's empty folder, and the tree keeps that in its own config
/// (BOUNDARY §1; CHECK-pv-up M2). Returns the head the tree starts at.
pub fn open_tree(
    repo: &Path,
    tree: &Path,
    branch: &str,
    base: &str,
    hooks: &Path,
) -> Result<String, String> {
    std::fs::create_dir_all(hooks).map_err(|e| format!("hooks folder: {e}"))?;
    if let Some(parent) = tree.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("seat folder: {e}"))?;
    }
    let tree_s = tree.to_string_lossy().into_owned();
    git(
        repo,
        hooks,
        &["worktree", "add", "-b", branch, &tree_s, base],
    )?;
    git(
        tree,
        hooks,
        &["config", "extensions.worktreeConfig", "true"],
    )?;
    let hooks_s = hooks.to_string_lossy().into_owned();
    git(
        tree,
        hooks,
        &["config", "--worktree", "core.hooksPath", &hooks_s],
    )?;
    if cfg!(windows) {
        git(
            tree,
            hooks,
            &["config", "--worktree", "core.longpaths", "true"],
        )?;
    }
    git(tree, hooks, &["rev-parse", "HEAD"])
}

/// What would be lost if the tree went away: files not committed, and commits on the
/// seat's branch not in `base`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Left {
    pub uncommitted: usize,
    pub unmerged: usize,
}

pub fn left_in(tree: &Path, base: &str, hooks: &Path) -> Result<Left, String> {
    let st = git(tree, hooks, &["status", "--porcelain"])?;
    let range = format!("{base}..HEAD");
    let ahead = git(tree, hooks, &["rev-list", "--count", &range])?;
    Ok(Left {
        uncommitted: st.lines().filter(|l| !l.trim().is_empty()).count(),
        unmerged: ahead
            .parse()
            .map_err(|_| format!("rev-list gave {ahead}"))?,
    })
}

/// Beside a seat's own copy (`seat-work/<wt>/`, outside the copy the seat writes in): which
/// folder it was copied from and whose it is, written by aibd when it opened the copy.
pub const BASE_FILE: &str = "aibd-base.json";
/// Where aibd keeps, in the author's repository, the last head it took from a seat's copy.
pub const TAKEN_REF: &str = "refs/aibd/seat/";

/// COUNCIL-worktree-gitdir-7ebca-1120 (a): a seat's own copy is a separate clone made with
/// `--no-hardlinks`, not a worktree: a worktree writes the author's `.git` (refs, `config`, so
/// `core.fsmonitor` / `core.hooksPath` would run the seat's program in the author's next git)
/// and hard-linked objects could be rewritten in place. The copy is on `branch` from the
/// folder's HEAD; [`BASE_FILE`] beside it names the folder and its `author`. The head copied.
pub fn open_clone(
    src: &Path,
    tree: &Path,
    branch: &str,
    author: &str,
    hooks: &Path,
) -> Result<String, String> {
    std::fs::create_dir_all(hooks).map_err(|e| format!("hooks folder: {e}"))?;
    let parent = tree
        .parent()
        .ok_or_else(|| "the copy has no folder above it".to_owned())?;
    std::fs::create_dir_all(parent).map_err(|e| format!("seat folder: {e}"))?;
    let (src_s, tree_s) = (
        src.to_string_lossy().into_owned(),
        tree.to_string_lossy().into_owned(),
    );
    git(
        parent,
        hooks,
        &[
            "-c",
            "protocol.file.allow=always",
            "clone",
            "--no-hardlinks",
            "--no-tags",
            "-q",
            &src_s,
            &tree_s,
        ],
    )?;
    let head = git(tree, hooks, &["rev-parse", "HEAD"])?;
    git(tree, hooks, &["checkout", "-q", "-b", branch])?;
    let hooks_s = hooks.to_string_lossy().into_owned();
    git(tree, hooks, &["config", "core.hooksPath", &hooks_s])?;
    if cfg!(windows) {
        git(tree, hooks, &["config", "core.longpaths", "true"])?;
    }
    let base = serde_json::json!({"base": src_s, "author": author}).to_string();
    std::fs::write(parent.join(BASE_FILE), base).map_err(|e| format!("base file: {e}"))?;
    Ok(head)
}

/// The folder a copy came off and its author, from [`BASE_FILE`] beside the copy.
pub fn base_of(tree: &Path) -> Option<(PathBuf, String)> {
    let raw = std::fs::read_to_string(tree.parent()?.join(BASE_FILE)).ok()?;
    let v: serde_json::Value = serde_json::from_str(&raw).ok()?;
    Some((
        PathBuf::from(v["base"].as_str()?),
        v["author"].as_str()?.to_owned(),
    ))
}

/// The copy's branch and head read as plain files (no git run in the copy: its own config,
/// which the seat writes, could name a filter or fsmonitor program).
pub fn clone_head(tree: &Path) -> Option<(String, String)> {
    let dot = tree.join(".git");
    let head = std::fs::read_to_string(dot.join("HEAD")).ok()?;
    let r = head.trim().strip_prefix("ref: ")?.to_owned();
    let branch = r.strip_prefix("refs/heads/")?.to_owned();
    let ok_name = !branch.is_empty()
        && !branch.contains("..")
        && branch
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-_./".contains(&b));
    if !ok_name {
        return None;
    }
    let sha = std::fs::read_to_string(dot.join(&r))
        .ok()
        .map(|s| s.trim().to_owned())
        .or_else(|| {
            let packed = std::fs::read_to_string(dot.join("packed-refs")).ok()?;
            packed.lines().find_map(|l| {
                let (sha, name) = l.split_once(' ')?;
                (name == r).then(|| sha.to_owned())
            })
        })?;
    (sha.len() == 40 && sha.bytes().all(|b| b.is_ascii_hexdigit())).then_some((branch, sha))
}

/// What the page shows under a seat in its own copy (043dd 11-20 round 2): its branch, the
/// branch of the folder it came off (`base`), commits on the seat's branch not in `base`;
/// `dirty` is not counted for a copy (it would mean running git in it), `None`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TreeState {
    pub branch: String,
    pub base: String,
    pub unmerged: usize,
    pub dirty: Option<usize>,
}

/// 5d33a REVIEW-gitinseatrepo M1 / M2 (11-20): a repository aibd runs git in is one a seat can
/// write (the author's folder; a registered repo's tree points at it with a `.git` file the seat
/// can rewrite). Settings there can name programs git runs on its own — a clean / smudge filter
/// (status, diff, merge), a merge driver, `core.alternateRefsCommand` (fetch), `include` of
/// another file — and no `-c` turns those off as a class (b4297 probes 11-20: filter on status,
/// smudge on merge, alternateRefsCommand on fetch and a redirected `.git` file on diff all ran).
/// So before any git there the settings are read as plain text and only these keys may appear.
const CONFIG_OK: &[&str] = &[
    "core.repositoryformatversion",
    "core.filemode",
    "core.bare",
    "core.logallrefupdates",
    "core.ignorecase",
    "core.precomposeunicode",
    "core.symlinks",
    "core.autocrlf",
    "core.longpaths",
    "core.hookspath",
    "extensions.objectformat",
    "extensions.worktreeconfig",
    "user.name",
    "user.email",
    "pull.rebase",
    "pull.ff",
    "init.defaultbranch",
];
/// Keys allowed under a named section (`[remote "origin"]`, `[branch "main"]`).
const CONFIG_OK_SUB: &[(&str, &[&str])] = &[
    ("remote", &["url", "fetch", "pushurl", "tagopt", "prune"]),
    ("branch", &["remote", "merge", "rebase"]),
];

/// The keys of a git settings file that are not on the list, names only (never values), sorted.
/// A value continued on the next line counts as one not on the list.
pub fn config_keys_not_ok(text: &str) -> Vec<String> {
    let mut bad = Vec::new();
    let (mut sect, mut sub) = (String::new(), false);
    for raw in text.lines() {
        let l = raw.trim();
        if l.is_empty() || l.starts_with('#') || l.starts_with(';') {
            continue;
        }
        if let Some(h) = l.strip_prefix('[') {
            let (head, rest) = h.split_once(']').unwrap_or((h, ""));
            let head = head.trim();
            let cut = head.find([' ', '"', '.']);
            sect = cut.map_or(head, |i| &head[..i]).trim().to_ascii_lowercase();
            sub = cut.is_some();
            let rest = rest.trim();
            if !(rest.is_empty() || rest.starts_with('#') || rest.starts_with(';')) {
                bad.push(format!("{sect}.(same line)"));
            }
            continue;
        }
        if l.ends_with('\\') {
            bad.push(format!("{sect}.(continued)"));
        }
        let key = l.split('=').next().unwrap_or(l).trim().to_ascii_lowercase();
        let ok = if sub {
            CONFIG_OK_SUB
                .iter()
                .any(|(s, ks)| *s == sect && ks.contains(&key.as_str()))
        } else {
            CONFIG_OK.contains(&format!("{sect}.{key}").as_str())
        };
        if !ok {
            bad.push(format!("{sect}.{key}"));
        }
    }
    bad.sort();
    bad.dedup();
    bad
}

/// A settings file (when there) has only listed keys; it must be a plain file.
fn config_file_ok(f: &Path, what: &str) -> Result<(), String> {
    let Ok(md) = std::fs::symlink_metadata(f) else {
        return Ok(());
    };
    if !md.file_type().is_file() {
        return Err(format!("repo_unsafe: {what} is not a plain file"));
    }
    let text = std::fs::read_to_string(f).map_err(|_| format!("repo_unsafe: {what} unreadable"))?;
    let bad = config_keys_not_ok(&text);
    if bad.is_empty() {
        Ok(())
    } else {
        Err(format!("repo_unsafe: {what} {}", bad.join(" ")))
    }
}

/// The repository at `top` may have git run in it: its `.git` is a real folder (not a file or
/// link pointing elsewhere), `config` / `config.worktree` hold only listed keys, and there is no
/// `info/attributes` or `objects/info/alternates`. Read as files; no git runs. `Err` starts
/// `repo_unsafe:` and names what is wrong (key names, never values).
pub fn repo_safe(top: &Path) -> Result<(), String> {
    let dot = top.join(".git");
    let md = std::fs::symlink_metadata(&dot).map_err(|_| "repo_unsafe: no .git".to_owned())?;
    if !md.file_type().is_dir() {
        return Err("repo_unsafe: .git is not a folder".into());
    }
    for f in ["info/attributes", "objects/info/alternates"] {
        if std::fs::symlink_metadata(dot.join(f)).is_ok() {
            return Err(format!("repo_unsafe: .git/{f}"));
        }
    }
    config_file_ok(&dot.join("config"), ".git/config")?;
    config_file_ok(&dot.join("config.worktree"), ".git/config.worktree")
}

/// A registered repo's tree may have git run in it: [`repo_safe`] for the repo, and the tree's
/// `.git` file names a folder right under `<repo>/.git/worktrees` whose `commondir` is the usual
/// `../..` and whose `config.worktree` holds only listed keys (5d33a M2: the seat can rewrite the
/// `.git` file to point at a repository it made).
pub fn registered_tree_safe(tree: &Path, repo_top: &Path) -> Result<(), String> {
    repo_safe(repo_top)?;
    let dot = tree.join(".git");
    let md =
        std::fs::symlink_metadata(&dot).map_err(|_| "repo_unsafe: tree has no .git".to_owned())?;
    if !md.file_type().is_file() {
        return Err("repo_unsafe: the tree's .git is not a file".into());
    }
    let text = std::fs::read_to_string(&dot)
        .map_err(|_| "repo_unsafe: tree .git unreadable".to_owned())?;
    let g = text
        .trim()
        .strip_prefix("gitdir:")
        .ok_or("repo_unsafe: tree .git has no gitdir")?
        .trim();
    let gd =
        std::fs::canonicalize(g).map_err(|_| "repo_unsafe: tree .git points nowhere".to_owned())?;
    let wts = std::fs::canonicalize(repo_top.join(".git").join("worktrees"))
        .map_err(|_| "repo_unsafe: repo has no worktrees folder".to_owned())?;
    if gd.parent() != Some(wts.as_path()) {
        return Err("repo_unsafe: tree .git points outside the registered repo".into());
    }
    let common = std::fs::read_to_string(gd.join("commondir")).unwrap_or_default();
    if common.trim() != "../.." {
        return Err("repo_unsafe: tree commondir moved".into());
    }
    config_file_ok(&gd.join("config.worktree"), "tree config.worktree")
}

/// The copy's head brought into the author's repository (objects and a hidden ref
/// `refs/aibd/seat/<wt>` only: nothing checked out, no branch moved), run in the author's
/// repository; the copy only serves the fetch. `(base folder, sha, base branch)`.
pub(crate) fn take_head(tree: &Path, hooks: &Path) -> Result<(PathBuf, String, String), String> {
    let (base_dir, _) = base_of(tree).ok_or("no base file beside the copy")?;
    // 5d33a M1: the author's folder is one a seat writes; no git there unless its settings are plain
    repo_safe(&base_dir)?;
    let (branch, sha) = clone_head(tree).ok_or("the copy's head is not readable")?;
    let have = git(
        &base_dir,
        hooks,
        &["cat-file", "-e", &format!("{sha}^{{commit}}")],
    )
    .is_ok();
    if !have {
        let wt = tree
            .parent()
            .and_then(Path::file_name)
            .and_then(|n| n.to_str())
            .ok_or("no seat folder name")?;
        let tree_s = tree.to_string_lossy().into_owned();
        let spec = format!("+refs/heads/{branch}:{TAKEN_REF}{wt}");
        git(
            &base_dir,
            hooks,
            &[
                "-c",
                "protocol.file.allow=always",
                "fetch",
                "-q",
                "--no-tags",
                &tree_s,
                &spec,
            ],
        )?;
    }
    let base = git(&base_dir, hooks, &["rev-parse", "--abbrev-ref", "HEAD"])?;
    Ok((base_dir, sha, base))
}

/// [`TreeState`] of the copy at `tree`.
pub fn tree_state(tree: &Path, hooks: &Path) -> Result<TreeState, String> {
    let (base_dir, sha, base) = take_head(tree, hooks)?;
    let n = git(&base_dir, hooks, &["rev-list", "--count", &sha, "^HEAD"])?;
    let branch = clone_head(tree).map(|(b, _)| b).unwrap_or_default();
    let unmerged = n.trim().parse::<usize>().map_err(|e| e.to_string())?;
    Ok(TreeState {
        branch,
        base,
        unmerged,
        dirty: None,
    })
}

/// What [`merge_back`] did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MergeBack {
    /// `n` commits merged into the base folder, which is now at `head`.
    Merged { n: usize, head: String },
    /// The seat's branch has nothing the base does not.
    Nothing,
    /// The base folder has files not committed: nothing is touched there.
    BaseDirty,
    /// The merge stopped on these files; it was taken back (`merge --abort`), the base folder
    /// is as it was.
    Conflict(Vec<String>),
}

/// 043dd 11-20 round 2 (合回): merge the copy's committed head into the folder it came off,
/// on that folder's own branch, as a merge commit by `who` (`name`, `mail`), all run in the
/// author's repository ([`take_head`]); files the seat did not commit stay in its copy. Only
/// when the base has no tracked change; a conflict is taken back at once, so the base folder
/// (where its owner seat works) is never left half-merged.
pub fn merge_back(tree: &Path, hooks: &Path, name: &str, mail: &str) -> Result<MergeBack, String> {
    let st = tree_state(tree, hooks)?;
    if st.unmerged == 0 {
        return Ok(MergeBack::Nothing);
    }
    let (base_dir, sha, _) = take_head(tree, hooks)?;
    // tracked changes only: an untracked file is neither carried nor lost by a merge (git
    // refuses to overwrite one, which comes back as a conflict with no file named)
    let changed = |dir: &Path| -> Result<bool, String> {
        let out = git(
            dir,
            hooks,
            &["status", "--porcelain", "--untracked-files=no"],
        )?;
        Ok(out.lines().any(|l| !l.trim().is_empty()))
    };
    if changed(&base_dir)? {
        return Ok(MergeBack::BaseDirty);
    }
    let who_name = format!("user.name={name}");
    let who_mail = format!("user.email={mail}");
    let msg = format!("合回 {} → {}", st.branch, st.base);
    let merged = git(
        &base_dir,
        hooks,
        &[
            "-c",
            &who_name,
            "-c",
            &who_mail,
            "-c",
            "commit.gpgsign=false",
            "merge",
            "--no-ff",
            "-m",
            &msg,
            &sha,
        ],
    );
    if merged.is_err() {
        let files = git(
            &base_dir,
            hooks,
            &["diff", "--name-only", "--diff-filter=U"],
        )
        .unwrap_or_default();
        let _ = git(&base_dir, hooks, &["merge", "--abort"]);
        return Ok(MergeBack::Conflict(
            files.lines().map(str::to_owned).take(50).collect(),
        ));
    }
    let head = git(&base_dir, hooks, &["rev-parse", "HEAD"])?;
    Ok(MergeBack::Merged {
        n: st.unmerged,
        head,
    })
}

/// Take the tree away, keeping the branch and every commit (BOUNDARY r1: the person
/// pressed after seeing [`left_in`]). No `--force`: git itself refuses a tree with
/// uncommitted files, so those are never dropped here.
pub fn remove_tree(repo: &Path, tree: &Path, hooks: &Path) -> Result<(), String> {
    let tree_s = tree.to_string_lossy().into_owned();
    git(repo, hooks, &["worktree", "remove", &tree_s]).map(|_| ())
}

/// What became of a seat's tree when the seat was archived.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Collected {
    pub branch: String,
    /// `None` for a seat's own copy: counting would run git in it (its config is the seat's).
    pub uncommitted: Option<usize>,
    pub unmerged: usize,
    /// The tree folder was taken away (only a registered repo's tree with nothing uncommitted).
    pub removed: bool,
}

/// The seat is archived (no session runs in it): take its tree away when nothing in it is
/// uncommitted (untracked files count), else leave it for the person (design r1 §1.3). The
/// branch and every commit always stay; `unmerged` is how many commits since `base` it holds.
/// The repo is found from the tree itself (its shared git folder).
///
/// A seat's own copy (a separate clone, a9136 04a7975c0) is never run git in and never taken
/// away here: its head is fetched into the author's repository (`refs/aibd/seat/<wt>`, so the
/// commits are safe there) and the copy stays, since what it holds uncommitted cannot be
/// counted without running git in it; 合回 or the person clears it.
pub fn collect(
    tree: &Path,
    base: &str,
    repo: Option<&Path>,
    hooks: &Path,
) -> Result<Collected, String> {
    if base_of(tree).is_some() {
        let s = tree_state(tree, hooks)?;
        return Ok(Collected {
            branch: s.branch,
            uncommitted: None,
            unmerged: s.unmerged,
            removed: false,
        });
    }
    // 5d33a M2: a registered repo's tree, only while its .git points into that repo
    registered_tree_safe(
        tree,
        repo.ok_or("repo_unsafe: no registered repo for this tree")?,
    )?;
    let left = left_in(tree, base, hooks)?;
    let branch = git(tree, hooks, &["rev-parse", "--abbrev-ref", "HEAD"])?;
    let mut c = Collected {
        branch,
        uncommitted: Some(left.uncommitted),
        unmerged: left.unmerged,
        removed: false,
    };
    if left.uncommitted > 0 {
        return Ok(c);
    }
    let common = git(
        tree,
        hooks,
        &["rev-parse", "--path-format=absolute", "--git-common-dir"],
    )?;
    let repo = Path::new(&common)
        .parent()
        .ok_or_else(|| format!("no repo above {common}"))?;
    remove_tree(repo, tree, hooks)?;
    c.removed = true;
    Ok(c)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn g(dir: &Path, args: &[&str]) {
        let ok = Command::new("git")
            .arg("-C")
            .arg(dir)
            .args(["-c", "user.name=t", "-c", "user.email=t@example.invalid"])
            .args([
                "-c",
                "commit.gpgsign=false",
                "-c",
                "init.defaultBranch=main",
            ])
            .args(args)
            .status()
            .unwrap()
            .success();
        assert!(ok, "git {args:?}");
    }

    fn repo_with(files: &[(&str, &str)]) -> tempfile::TempDir {
        let d = tempfile::tempdir().unwrap();
        g(d.path(), &["init", "-q"]);
        for (p, body) in files {
            let f = d.path().join(p);
            std::fs::create_dir_all(f.parent().unwrap()).unwrap();
            std::fs::write(f, body).unwrap();
        }
        std::fs::write(d.path().join("README"), "r").unwrap();
        g(d.path(), &["add", "-A"]);
        g(d.path(), &["commit", "-q", "-m", "first"]);
        d
    }

    #[test]
    fn a_seats_copy_is_its_own_clone_and_says_what_is_not_merged() {
        let r = repo_with(&[]);
        let data = tempfile::tempdir().unwrap();
        let hooks = data.path().join("no-hooks");
        let tree = tree_path(&data.path().join("seat-work").join("wt1"), "repo");
        open_clone(r.path(), &tree, "seat/wt1-1120", "seat_aaaa1111", &hooks).unwrap();
        // COUNCIL-worktree-gitdir (a): its own .git, nothing of it in the author's
        assert!(tree.join(".git").is_dir(), "reason: copy_is_a_worktree");
        assert!(
            !r.path().join(".git").join("worktrees").exists(),
            "reason: author_git_written"
        );
        // and no object file shared with the author's (a hard link could be rewritten in place)
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            let mut dirs = vec![tree.join(".git").join("objects")];
            let mut seen = 0;
            while let Some(d) = dirs.pop() {
                for e in std::fs::read_dir(&d).unwrap().flatten() {
                    let m = e.metadata().unwrap();
                    if m.is_dir() {
                        dirs.push(e.path());
                    } else {
                        seen += 1;
                        assert_eq!(
                            m.nlink(),
                            1,
                            "reason: object_hardlinked {}",
                            e.path().display()
                        );
                    }
                }
            }
            assert!(seen > 0, "reason: no_objects_looked_at");
        }
        assert_eq!(
            base_of(&tree),
            Some((r.path().to_path_buf(), "seat_aaaa1111".to_owned()))
        );
        let s = tree_state(&tree, &hooks).unwrap();
        assert_eq!(
            s,
            TreeState {
                branch: "seat/wt1-1120".into(),
                base: "main".into(),
                unmerged: 0,
                dirty: None
            }
        );
        std::fs::write(tree.join("a.txt"), "a").unwrap();
        g(&tree, &["add", "-A"]);
        g(&tree, &["commit", "-q", "-m", "a"]);
        // what the seat writes in its copy's config does not reach the author's
        g(&tree, &["config", "core.fsmonitor", "/bin/false"]);
        let cfg = std::fs::read_to_string(r.path().join(".git").join("config")).unwrap();
        assert!(!cfg.contains("fsmonitor"), "reason: author_config_written");
        let s = tree_state(&tree, &hooks).unwrap();
        assert_eq!(s.unmerged, 1, "reason: counts {s:?}");
    }

    #[test]
    fn a_seats_copy_merges_back_and_a_conflict_leaves_the_base_as_it_was() {
        let r = repo_with(&[("x.txt", "0")]);
        let data = tempfile::tempdir().unwrap();
        let hooks = data.path().join("no-hooks");
        let tree = tree_path(&data.path().join("seat-work").join("wt2"), "repo");
        open_clone(r.path(), &tree, "seat/wt2-1120", "seat_aaaa1111", &hooks).unwrap();
        let mb = |t: &Path| merge_back(t, &hooks, "aibd", "aibd@aibd.invalid").unwrap();
        assert_eq!(mb(&tree), MergeBack::Nothing);
        std::fs::write(tree.join("a.txt"), "a").unwrap();
        g(&tree, &["add", "-A"]);
        g(&tree, &["commit", "-q", "-m", "a"]);
        // a file not committed stays in the copy
        std::fs::write(tree.join("wip.txt"), "w").unwrap();
        std::fs::write(r.path().join("x.txt"), "wip").unwrap();
        assert_eq!(mb(&tree), MergeBack::BaseDirty, "reason: dirty_base_merged");
        g(r.path(), &["checkout", "--", "x.txt"]);
        std::fs::write(r.path().join("notes.log"), "n").unwrap();
        let MergeBack::Merged { n, head } = mb(&tree) else {
            panic!("reason: clean_not_merged")
        };
        assert_eq!(n, 1);
        assert!(r.path().join("a.txt").is_file(), "reason: not_in_base");
        assert!(
            !r.path().join("wip.txt").exists(),
            "reason: uncommitted_carried"
        );
        assert_eq!(head.len(), 40);
        assert_eq!(mb(&tree), MergeBack::Nothing, "reason: merged_twice");
        // both change x.txt: a conflict, taken back
        std::fs::write(tree.join("x.txt"), "tree").unwrap();
        g(&tree, &["commit", "-qam", "t"]);
        std::fs::write(r.path().join("x.txt"), "base").unwrap();
        g(r.path(), &["commit", "-qam", "b"]);
        let before = git(r.path(), &hooks, &["rev-parse", "HEAD"]).unwrap();
        assert_eq!(mb(&tree), MergeBack::Conflict(vec!["x.txt".into()]));
        assert_eq!(
            git(r.path(), &hooks, &["rev-parse", "HEAD"]).unwrap(),
            before
        );
        assert_eq!(
            git(
                r.path(),
                &hooks,
                &["status", "--porcelain", "--untracked-files=no"]
            )
            .unwrap(),
            "",
            "reason: base_left_half_merged"
        );
    }

    #[test]
    fn a_copys_head_is_read_without_git_and_a_bad_branch_name_is_not() {
        let d = tempfile::tempdir().unwrap();
        let dot = d.path().join(".git");
        std::fs::create_dir_all(dot.join("refs/heads/seat")).unwrap();
        let sha = "a".repeat(40);
        std::fs::write(dot.join("HEAD"), "ref: refs/heads/seat/w-1120\n").unwrap();
        std::fs::write(dot.join("refs/heads/seat/w-1120"), format!("{sha}\n")).unwrap();
        assert_eq!(
            clone_head(d.path()),
            Some(("seat/w-1120".to_owned(), sha.clone()))
        );
        std::fs::write(dot.join("HEAD"), "ref: refs/heads/../../x\n").unwrap();
        assert_eq!(clone_head(d.path()), None, "reason: dotdot_read");
        std::fs::write(dot.join("HEAD"), format!("{sha}\n")).unwrap();
        assert_eq!(clone_head(d.path()), None, "reason: detached_read");
    }

    #[test]
    fn the_list_names_every_settings_file_and_only_the_watched_keys() {
        let r = repo_with(&[
            ("CLAUDE.md", "be kind"),
            (".mcp.json", r#"{"mcpServers":{"db":{"command":"x"}}}"#),
            (
                ".claude/settings.json",
                r#"{"hooks":{"Stop":[]},"permissions":{"allow":["Bash"]},"model":"x"}"#,
            ),
            (".claude/agents/a.md", "agent"),
        ]);
        let m = manifest(r.path()).unwrap();
        let names: Vec<&str> = m.files.iter().map(|(p, _)| p.as_str()).collect();
        assert_eq!(
            names,
            [
                ".claude/agents/a.md",
                ".claude/settings.json",
                ".mcp.json",
                "CLAUDE.md"
            ]
        );
        assert!(m.files.iter().all(|(_, h)| h.len() == 12));
        assert_eq!(
            m.keys,
            [
                ".claude/settings.json:hooks.Stop",
                ".claude/settings.json:permissions.allow",
                ".mcp.json:server:db"
            ]
        );
        // values never appear: "Bash", "x"
        assert!(!format!("{m:?}").contains("Bash"));
    }

    #[test]
    fn a_tree_whose_settings_moved_is_refused_and_an_equal_one_opens() {
        let r = repo_with(&[(".claude/settings.json", r#"{"hooks":{}}"#)]);
        let hk = tempfile::tempdir().unwrap();
        let h = hk.path().join("no-hooks");
        std::fs::create_dir_all(&h).unwrap();
        let reg = manifest_for(r.path(), &h).unwrap();
        assert_eq!(reg.git_config.len(), 12);
        assert_eq!(check_unchanged(&reg, r.path(), &h), Ok(()));
        std::fs::write(
            r.path().join(".claude/settings.json"),
            r#"{"hooks":{"Stop":[]}}"#,
        )
        .unwrap();
        assert_eq!(
            check_unchanged(&reg, r.path(), &h),
            Err("project_settings_changed")
        );
        std::fs::write(r.path().join(".claude/settings.json"), r#"{"hooks":{}}"#).unwrap();
        std::fs::write(r.path().join(".mcp.json"), "{}").unwrap();
        assert_eq!(
            check_unchanged(&reg, r.path(), &h),
            Err("project_settings_changed")
        );
        std::fs::remove_file(r.path().join(".mcp.json")).unwrap();
        std::fs::remove_file(r.path().join(".claude/settings.json")).unwrap();
        assert_eq!(
            check_unchanged(&reg, r.path(), &h),
            Err("project_settings_changed")
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_linked_settings_file_is_refused() {
        let r = repo_with(&[]);
        let other = tempfile::tempdir().unwrap();
        std::fs::write(other.path().join("x.md"), "x").unwrap();
        std::os::unix::fs::symlink(other.path().join("x.md"), r.path().join("CLAUDE.md")).unwrap();
        assert_eq!(manifest(r.path()), Err("settings_symlink"));
    }

    #[test]
    fn names_paths_and_branches_have_one_shape() {
        assert!(repo_name_ok("ez2-aib"));
        for bad in ["", "-x", "Ez2", "a/b", "a b", &"x".repeat(41)] {
            assert!(!repo_name_ok(bad), "{bad}");
        }
        assert_eq!(branch_name("b4297", "1119"), "seat/b4297-1119");
        let p = tree_path(
            Path::new("data").join("seat-work").join("s1").as_path(),
            "ez2-aib",
        );
        assert_eq!(slash(&p), "data/seat-work/s1/ez2-aib");
        // Windows writes the other separator; the list compares on '/'
        assert_eq!(slash(Path::new("a").join("b").as_path()), "a/b");
    }

    #[test]
    fn a_tree_opens_without_hooks_and_what_is_left_is_counted_before_it_goes() {
        let r = repo_with(&[]);
        let hooks_repo = r.path().join(".git").join("hooks");
        std::fs::create_dir_all(&hooks_repo).unwrap();
        // a repo hook that would leave a mark if git ran it
        let mark = r.path().join("hook-ran");
        let hook = hooks_repo.join("post-checkout");
        std::fs::write(&hook, format!("#!/bin/sh\ntouch '{}'\n", mark.display())).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&hook, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        let data = tempfile::tempdir().unwrap();
        let hooks = data.path().join("no-hooks");
        let tree = tree_path(&data.path().join("seat-work").join("s1"), "repo");
        let head = open_tree(r.path(), &tree, &branch_name("s1", "1119"), "HEAD", &hooks).unwrap();
        assert_eq!(head.len(), 40);
        assert!(!mark.exists(), "a repo hook ran while the tree opened");
        let cfg = git(&tree, &hooks, &["config", "--worktree", "core.hooksPath"]).unwrap();
        assert_eq!(cfg, hooks.to_string_lossy());

        assert_eq!(
            left_in(&tree, &head, &hooks).unwrap(),
            Left {
                uncommitted: 0,
                unmerged: 0
            }
        );
        std::fs::write(tree.join("new.txt"), "n").unwrap();
        assert_eq!(left_in(&tree, &head, &hooks).unwrap().uncommitted, 1);
        g(&tree, &["add", "-A"]);
        g(
            &tree,
            &[
                "-c",
                &format!("core.hooksPath={}", hooks.display()),
                "commit",
                "-q",
                "-m",
                "seat work",
            ],
        );
        assert_eq!(
            left_in(&tree, &head, &hooks).unwrap(),
            Left {
                uncommitted: 0,
                unmerged: 1
            }
        );

        remove_tree(r.path(), &tree, &hooks).unwrap();
        assert!(!tree.exists());
        // the branch and its commit stay
        let kept = git(r.path(), &hooks, &["rev-list", "--count", "seat/s1-1119"]).unwrap();
        assert_eq!(kept, "2");
    }

    #[test]
    fn an_archived_seats_tree_goes_only_when_nothing_is_uncommitted_and_its_branch_stays() {
        let r = repo_with(&[]);
        let data = tempfile::tempdir().unwrap();
        let hooks = data.path().join("no-hooks");
        let tree = tree_path(&data.path().join("seat-work").join("s3"), "repo");
        let base = open_tree(r.path(), &tree, "seat/s3-1120", "HEAD", &hooks).unwrap();
        std::fs::write(tree.join("wip.txt"), "w").unwrap();
        let kept = collect(&tree, &base, Some(r.path()), &hooks).unwrap();
        assert!(!kept.removed, "reason: tree_with_work_taken");
        assert_eq!(kept.uncommitted, Some(1));
        assert!(tree.join("wip.txt").exists());
        g(&tree, &["add", "-A"]);
        g(
            &tree,
            &[
                "-c",
                &format!("core.hooksPath={}", hooks.display()),
                "commit",
                "-q",
                "-m",
                "seat work",
            ],
        );
        let gone = collect(&tree, &base, Some(r.path()), &hooks).unwrap();
        assert_eq!(
            gone,
            Collected {
                branch: "seat/s3-1120".into(),
                uncommitted: Some(0),
                unmerged: 1,
                removed: true
            }
        );
        assert!(!tree.exists(), "reason: clean_tree_left");
        let n = git(r.path(), &hooks, &["rev-list", "--count", "seat/s3-1120"]).unwrap();
        assert_eq!(n, "2", "reason: branch_or_commit_lost");
    }

    #[test]
    fn only_plain_settings_keys_pass_and_a_program_key_is_named() {
        let plain = "[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n\
                     \tlogallrefupdates = true\n\tignorecase = true\n\tprecomposeunicode = true\n\
                     [remote \"origin\"]\n\turl = https://example.invalid/r.git\n\
                     \tfetch = +refs/heads/*:refs/remotes/origin/*\n[branch \"main\"]\n\
                     \tremote = origin\n\tmerge = refs/heads/main\n[extensions]\n\
                     \tworktreeConfig = true\n# a comment\n";
        assert!(
            config_keys_not_ok(plain).is_empty(),
            "{:?}",
            config_keys_not_ok(plain)
        );
        for (text, want) in [
            ("[filter \"x\"]\n\tclean = sh\n", "filter.clean"),
            ("[merge \"x\"]\n\tdriver = sh\n", "merge.driver"),
            (
                "[core]\n\talternateRefsCommand = sh\n",
                "core.alternaterefscommand",
            ),
            ("[core]\n\tsshCommand = sh\n", "core.sshcommand"),
            ("[include]\n\tpath = elsewhere\n", "include.path"),
            (
                "[includeIf \"gitdir:/\"]\n\tpath = elsewhere\n",
                "includeif.path",
            ),
            ("[remote \"/x\"]\n\tuploadpack = sh\n", "remote.uploadpack"),
            ("[gpg]\n\tprogram = sh\n", "gpg.program"),
            ("[core] fsmonitor = sh\n", "core.(same line)"),
            ("[user]\n\tname = a \\\n\tb\n", "user.(continued)"),
        ] {
            let bad = config_keys_not_ok(text);
            assert!(
                bad.iter().any(|b| b == want),
                "reason: key_passed {want} {bad:?}"
            );
        }
    }

    /// 5d33a REVIEW-gitinseatrepo M1: a program named in the author's settings never runs from
    /// aibd's git there; reading the copy is refused, saying which key.
    #[test]
    fn an_authors_repo_naming_a_program_is_not_run_git_in() {
        let r = repo_with(&[]);
        let data = tempfile::tempdir().unwrap();
        let hooks = data.path().join("no-hooks");
        let tree = data.path().join("seat-work").join("wt9").join("repo");
        open_clone(r.path(), &tree, "seat/wt9-1120", "seat_aaaa1111", &hooks).unwrap();
        assert!(
            tree_state(&tree, &hooks).is_ok(),
            "reason: plain_author_refused"
        );
        let marker = data.path().join("ran");
        let touch = format!("sh -c 'touch {}; cat' --", marker.display());
        g(r.path(), &["config", "filter.x.clean", &touch]);
        g(r.path(), &["config", "core.alternateRefsCommand", &touch]);
        std::fs::write(r.path().join(".git/info/attributes"), "* filter=x\n").unwrap();
        let e = tree_state(&tree, &hooks).unwrap_err();
        assert!(e.starts_with("repo_unsafe:"), "reason: author_run {e}");
        let e2 = collect(&tree, "0", None, &hooks).unwrap_err();
        assert!(
            e2.starts_with("repo_unsafe:"),
            "reason: author_collected {e2}"
        );
        assert!(!marker.exists(), "reason: author_program_ran");
        // the folder only (not the file): a .git that is a file pointing elsewhere is refused too
        let other = tempfile::tempdir().unwrap();
        std::fs::write(other.path().join(".git"), "gitdir: /elsewhere\n").unwrap();
        assert!(repo_safe(other.path()).is_err(), "reason: git_file_passed");
    }

    /// 5d33a REVIEW-gitinseatrepo M2: a registered repo's tree whose .git file the seat pointed
    /// at a repository it made (whose diff would run its filter) is refused before any git.
    #[test]
    fn a_registered_tree_pointed_elsewhere_is_not_run_git_in() {
        let r = repo_with(&[]);
        let data = tempfile::tempdir().unwrap();
        let hooks = data.path().join("no-hooks");
        let tree = tree_path(&data.path().join("seat-work").join("s9"), "repo");
        let base = open_tree(r.path(), &tree, "seat/s9-1120", "HEAD", &hooks).unwrap();
        assert!(
            registered_tree_safe(&tree, r.path()).is_ok(),
            "reason: own_tree_refused"
        );
        let fake = repo_with(&[]);
        let marker = data.path().join("ran");
        g(
            fake.path(),
            &[
                "config",
                "filter.x.clean",
                &format!("sh -c 'touch {}; cat' --", marker.display()),
            ],
        );
        std::fs::write(fake.path().join(".git/info/attributes"), "* filter=x\n").unwrap();
        std::fs::write(
            tree.join(".git"),
            format!("gitdir: {}\n", fake.path().join(".git").display()),
        )
        .unwrap();
        std::fs::write(tree.join("README"), "changed").unwrap();
        let w = crate::tree_read::read_where(&tree, Some(r.path()), &hooks).unwrap_err();
        assert!(
            w.starts_with("repo_unsafe:"),
            "reason: redirected_tree_read {w}"
        );
        let e = collect(&tree, &base, Some(r.path()), &hooks).unwrap_err();
        assert!(
            e.starts_with("repo_unsafe:"),
            "reason: redirected_tree_run {e}"
        );
        assert!(!marker.exists(), "reason: redirected_program_ran");
    }

    #[test]
    fn a_tree_with_uncommitted_files_is_not_taken_away() {
        let r = repo_with(&[]);
        let data = tempfile::tempdir().unwrap();
        let hooks = data.path().join("no-hooks");
        let tree = tree_path(&data.path().join("seat-work").join("s2"), "repo");
        open_tree(r.path(), &tree, "seat/s2-1119", "HEAD", &hooks).unwrap();
        std::fs::write(tree.join("wip.txt"), "w").unwrap();
        assert!(remove_tree(r.path(), &tree, &hooks).is_err());
        assert!(tree.join("wip.txt").exists());
    }

    fn hooks_dir() -> (tempfile::TempDir, PathBuf) {
        let d = tempfile::tempdir().unwrap();
        let h = d.path().join("no-hooks");
        std::fs::create_dir_all(&h).unwrap();
        (d, h)
    }

    #[test]
    fn a_claude_md_in_a_sub_folder_is_listed_and_a_new_one_is_caught() {
        let r = repo_with(&[("crates/x/CLAUDE.md", "sub"), ("AGENTS.md", "top")]);
        let (_d, h) = hooks_dir();
        let reg = manifest_for(r.path(), &h).unwrap();
        let names: Vec<&str> = reg.files.iter().map(|(p, _)| p.as_str()).collect();
        assert_eq!(names, ["AGENTS.md", "crates/x/CLAUDE.md"]);
        // one the seat writes and never commits is caught too
        std::fs::create_dir_all(r.path().join("docs")).unwrap();
        std::fs::write(r.path().join("docs/AGENTS.md"), "new").unwrap();
        assert_eq!(
            check_unchanged(&reg, r.path(), &h),
            Err("project_settings_changed")
        );
    }

    #[test]
    fn a_second_tree_of_the_same_repo_still_matches_what_was_registered() {
        let r = repo_with(&[]);
        let data = tempfile::tempdir().unwrap();
        let hooks = data.path().join("no-hooks");
        std::fs::create_dir_all(&hooks).unwrap();
        let reg = manifest_for(r.path(), &hooks).unwrap();
        let work = data.path().join("seat-work");
        open_tree(
            r.path(),
            &tree_path(&work.join("s1"), "repo"),
            "seat/s1-1119",
            "HEAD",
            &hooks,
        )
        .unwrap();
        assert_eq!(
            check_unchanged(&reg, r.path(), &hooks),
            Ok(()),
            "reason: own_worktree_config_counted"
        );
        g(r.path(), &["config", "extensions.noop", "true"]);
        assert_eq!(
            check_unchanged(&reg, r.path(), &hooks),
            Err("project_settings_changed"),
            "reason: other_extension_missed"
        );
    }

    #[test]
    fn the_shared_git_config_changing_is_caught() {
        let r = repo_with(&[]);
        let (_d, h) = hooks_dir();
        let reg = manifest_for(r.path(), &h).unwrap();
        g(r.path(), &["config", "core.fsmonitor", "/bin/true"]);
        assert_eq!(
            check_unchanged(&reg, r.path(), &h),
            Err("project_settings_changed")
        );
    }

    #[cfg(unix)]
    #[test]
    fn an_fsmonitor_program_in_the_shared_config_never_runs() {
        let r = repo_with(&[]);
        let data = tempfile::tempdir().unwrap();
        let hooks = data.path().join("no-hooks");
        let tree = tree_path(&data.path().join("seat-work").join("s3"), "repo");
        let head = open_tree(r.path(), &tree, "seat/s3-1119", "HEAD", &hooks).unwrap();
        let mark = data.path().join("fsmonitor-ran");
        let prog = data.path().join("fsm.sh");
        std::fs::write(&prog, format!("#!/bin/sh\ntouch '{}'\n", mark.display())).unwrap();
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&prog, std::fs::Permissions::from_mode(0o755)).unwrap();
        g(
            r.path(),
            &["config", "core.fsmonitor", &prog.to_string_lossy()],
        );
        left_in(&tree, &head, &hooks).unwrap();
        assert!(
            !mark.exists(),
            "aibd ran the fsmonitor program a seat wrote"
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_fifo_among_the_settings_is_refused_not_read() {
        let r = repo_with(&[(".claude/settings.json", "{}")]);
        let ok = Command::new("mkfifo")
            .arg(r.path().join(".claude/pipe"))
            .status()
            .unwrap()
            .success();
        assert!(ok);
        // a FIFO that is opened blocks until a writer comes: the check runs on its own thread
        // and a read that hangs is a failure, not a test that never ends (Pro 13 wwt2: the
        // WT10 knife held the receipt two hours)
        let top = r.path().to_path_buf();
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let _ = tx.send(manifest(&top).map(|_| ()));
        });
        // a test's bound on a read that may never return, not a daemon timer (ADR 0005
        // named exception)
        #[allow(clippy::disallowed_methods)]
        let got = rx.recv_timeout(std::time::Duration::from_secs(10));
        match got {
            Ok(got) => assert_eq!(got, Err("settings_not_file")),
            Err(_) => {
                // let the blocked reader go before failing
                let _ = std::fs::OpenOptions::new()
                    .write(true)
                    .open(r.path().join(".claude/pipe"));
                panic!("reason: fifo_read_blocks");
            }
        }
    }
}
