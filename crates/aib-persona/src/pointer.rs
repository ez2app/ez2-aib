// SPDX-License-Identifier: Apache-2.0
//! What an evidence pointer may be, and the check made when it is written.
//!
//! | kind      | shape                                   | checked at write                       |
//! |-----------|-----------------------------------------|----------------------------------------|
//! | commit    | 7–40 lower hex                          | shape only                             |
//! | event     | `ev_` + 64 lower hex                    | shape only                             |
//! | message   | `msg|dm|acb|msg_id` + `:`/`_`/`-` + id  | shape only (older writers)             |
//! | dm        | `q_` + 16 lower hex                     | exists in the dm queue (incl. retired) |
//! | pr        | `pr:<owner>/<repo>#<n>`                 | `gh api` finds it                      |
//! | receipt   | a path to a .json/.jsonl/.md/.txt file  | real path under an allowed root        |
//! | file_line | a path + `:<line>`                      | same, and the line exists (≥ 1)        |
//!
//! Paths may be absolute, `~/…` or relative to the working directory. They are resolved
//! (symlinks and `..` included) before the root check, the file must be a regular file of at most
//! 8 MiB, and it is read once: the line count and the sha256 come from the same bytes.
//! "Exists" is not "relevant": a dm id or a PR that exists still says nothing about the trait.

use crate::sha256;
use std::io::Read;
use std::path::{Path, PathBuf};

pub const MAX_RECEIPT_BYTES: u64 = 8 * 1024 * 1024;
const RECEIPT_EXTS: [&str; 4] = ["json", "jsonl", "md", "txt"];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Commit,
    Event,
    Message,
    Dm,
    Pr,
    Receipt,
    FileLine,
}

impl Kind {
    pub fn name(self) -> &'static str {
        match self {
            Kind::Commit => "commit",
            Kind::Event => "event",
            Kind::Message => "message",
            Kind::Dm => "dm",
            Kind::Pr => "pr",
            Kind::Receipt => "receipt",
            Kind::FileLine => "file_line",
        }
    }

    pub fn from_stored(s: &str) -> Option<Kind> {
        [
            Kind::Commit,
            Kind::Event,
            Kind::Message,
            Kind::Dm,
            Kind::Pr,
            Kind::Receipt,
            Kind::FileLine,
        ]
        .into_iter()
        .find(|k| k.name() == s)
    }
}

fn lower_hex(s: &str) -> bool {
    s.bytes()
        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn seg_ok(s: &str) -> bool {
    !s.is_empty()
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._@-".contains(&b))
}

fn is_commit(p: &str) -> bool {
    (7..=40).contains(&p.len()) && lower_hex(p)
}

fn is_event(p: &str) -> bool {
    p.strip_prefix("ev_")
        .is_some_and(|h| h.len() == 64 && lower_hex(h))
}

/// `^(msg|dm|acb|msg_id)[:_-][A-Za-z0-9._:@-]{4,120}$`
fn is_message(p: &str) -> bool {
    ["msg_id", "msg", "dm", "acb"].iter().any(|pre| {
        p.strip_prefix(pre).is_some_and(|r| {
            let mut c = r.chars();
            matches!(c.next(), Some(':' | '_' | '-'))
                && (4..=120).contains(&c.as_str().len())
                && c.as_str()
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"._:@-".contains(&b))
        })
    })
}

/// `q_` + 16 lower hex.
pub fn is_dm_id(p: &str) -> bool {
    p.strip_prefix("q_")
        .is_some_and(|h| h.len() == 16 && lower_hex(h))
}

/// `pr:<owner>/<repo>#<n>` → (repo, n).
pub fn pr_ref(p: &str) -> Option<(String, u64)> {
    let r = p.strip_prefix("pr:")?;
    let (repo, n) = r.split_once('#')?;
    let (owner, name) = repo.split_once('/')?;
    let ok = |s: &str| {
        !s.is_empty()
            && s.len() <= 100
            && s.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
    };
    if !ok(owner)
        || !ok(name)
        || n.is_empty()
        || n.len() > 7
        || !n.bytes().all(|b| b.is_ascii_digit())
    {
        return None;
    }
    let n: u64 = n.parse().ok()?;
    (n >= 1).then(|| (repo.to_string(), n))
}

/// The kind the Python v1 writer would have given (gates._POINTER_RES), for reading old lines.
pub fn legacy_kind(p: &str) -> Option<Kind> {
    let p = p.trim();
    if p.is_empty() || p.contains(' ') || p.len() > 400 {
        return None;
    }
    if is_commit(p) {
        return Some(Kind::Commit);
    }
    if is_event(p) {
        return Some(Kind::Event);
    }
    if is_message(p) {
        return Some(Kind::Message);
    }
    let file_ok = |f: &str, exts: &dyn Fn(&str) -> bool| {
        f.rsplit_once('.')
            .is_some_and(|(stem, ext)| seg_ok(stem) && exts(ext))
            && seg_ok(f)
    };
    // receipt: ^(?:seg/)+seg\.(json|jsonl|md|txt)$
    let parts: Vec<&str> = p.split('/').collect();
    if parts.len() >= 2
        && parts[..parts.len() - 1].iter().all(|s| seg_ok(s))
        && file_ok(parts[parts.len() - 1], &|e| RECEIPT_EXTS.contains(&e))
    {
        return Some(Kind::Receipt);
    }
    // file_line: ^(?:seg/)*seg\.[A-Za-z0-9]{1,8}:\d{1,7}$
    if let Some((path, line)) = p.rsplit_once(':') {
        let parts: Vec<&str> = path.split('/').collect();
        if (1..=7).contains(&line.len())
            && line.bytes().all(|b| b.is_ascii_digit())
            && parts[..parts.len() - 1].iter().all(|s| seg_ok(s))
            && file_ok(parts[parts.len() - 1], &|e| {
                (1..=8).contains(&e.len()) && e.bytes().all(|b| b.is_ascii_alphanumeric())
            })
        {
            return Some(Kind::FileLine);
        }
    }
    None
}

/// What a v2 writer stores beside the pointer.
#[derive(Debug, Clone, PartialEq)]
pub struct Checked {
    pub kind: Kind,
    /// Real path of a receipt / file_line pointer.
    pub resolved: Option<String>,
    /// Full sha256 of that file when it was checked.
    pub sha256: Option<String>,
    pub line: Option<u64>,
}

/// What the check needs from outside: the dm queue and GitHub.
pub struct World<'a> {
    pub cwd: &'a Path,
    pub home: &'a Path,
    /// Allowed roots for receipt / file_line (resolved before comparing; missing roots skipped).
    pub roots: &'a [PathBuf],
    pub dm_exists: &'a dyn Fn(&str) -> bool,
    /// Ok(true) found, Ok(false) GitHub says no such PR, Err could not ask.
    pub pr_exists: &'a dyn Fn(&str, u64) -> Result<bool, String>,
}

fn expand(p: &str, w: &World) -> PathBuf {
    if let Some(rest) = p.strip_prefix("~/") {
        w.home.join(rest)
    } else {
        let pb = PathBuf::from(p);
        if pb.is_absolute() {
            pb
        } else {
            w.cwd.join(pb)
        }
    }
}

/// Split `path:123` → (path, Some(123)); a path without a numeric suffix → (path, None).
fn split_line(p: &str) -> Result<(&str, Option<u64>), String> {
    if let Some((path, n)) = p.rsplit_once(':') {
        if !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()) {
            if n.len() > 7 {
                return Err(format!("line number {n} too long"));
            }
            let n: u64 = n.parse().map_err(|_| format!("bad line number {n}"))?;
            if n == 0 {
                return Err("line numbers start at 1".into());
            }
            return Ok((path, Some(n)));
        }
    }
    Ok((p, None))
}

fn check_path(p: &str, w: &World) -> Result<Checked, String> {
    let (path, line) = split_line(p)?;
    if line.is_none() {
        let ext = Path::new(path)
            .extension()
            .and_then(|e| e.to_str())
            .unwrap_or("");
        if !RECEIPT_EXTS.contains(&ext) {
            return Err(format!(
                "a receipt is a .json/.jsonl/.md/.txt file; for any other file give path:line ({p})"
            ));
        }
    }
    let real = std::fs::canonicalize(expand(path, w))
        .map_err(|e| format!("{path}: cannot resolve ({e})"))?;
    let under = w
        .roots
        .iter()
        .filter_map(|r| std::fs::canonicalize(r).ok())
        .any(|r| real.starts_with(&r));
    if !under {
        return Err(format!(
            "{} is not under an allowed root ({})",
            real.display(),
            w.roots
                .iter()
                .map(|r| r.display().to_string())
                .collect::<Vec<_>>()
                .join(", ")
        ));
    }
    let f = std::fs::File::open(&real).map_err(|e| format!("{}: {e}", real.display()))?;
    let md = f
        .metadata()
        .map_err(|e| format!("{}: {e}", real.display()))?;
    if !md.is_file() {
        return Err(format!("{} is not a regular file", real.display()));
    }
    if md.len() > MAX_RECEIPT_BYTES {
        return Err(format!(
            "{} is over {MAX_RECEIPT_BYTES} bytes",
            real.display()
        ));
    }
    let mut bytes = vec![];
    f.take(MAX_RECEIPT_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| format!("{}: {e}", real.display()))?;
    if bytes.len() as u64 > MAX_RECEIPT_BYTES {
        return Err(format!(
            "{} grew past {MAX_RECEIPT_BYTES} bytes",
            real.display()
        ));
    }
    if let Some(n) = line {
        let lines = bytes.iter().filter(|b| **b == b'\n').count() as u64
            + u64::from(bytes.last().is_some_and(|b| *b != b'\n'));
        if n > lines {
            return Err(format!("{} has {lines} lines, not {n}", real.display()));
        }
    }
    Ok(Checked {
        kind: if line.is_some() {
            Kind::FileLine
        } else {
            Kind::Receipt
        },
        resolved: Some(real.to_string_lossy().into_owned()),
        sha256: Some(sha256::hex(&bytes)),
        line,
    })
}

/// Check a pointer for a new (v2) line.
pub fn check(p: &str, w: &World) -> Result<Checked, String> {
    let p = p.trim();
    if p.is_empty() || p.len() > 400 || p.chars().any(char::is_whitespace) {
        return Err("a pointer is one token of at most 400 characters (no spaces)".into());
    }
    let plain = |kind| {
        Ok(Checked {
            kind,
            resolved: None,
            sha256: None,
            line: None,
        })
    };
    if is_commit(p) {
        return plain(Kind::Commit);
    }
    if is_event(p) {
        return plain(Kind::Event);
    }
    if is_dm_id(p) {
        return if (w.dm_exists)(p) {
            plain(Kind::Dm)
        } else {
            Err(format!("{p}: not in the dm queue"))
        };
    }
    if p.starts_with("pr:") {
        let (repo, n) = pr_ref(p).ok_or_else(|| format!("{p}: expected pr:<owner>/<repo>#<n>"))?;
        return match (w.pr_exists)(&repo, n) {
            Ok(true) => plain(Kind::Pr),
            Ok(false) => Err(format!("{p}: GitHub has no such pull request")),
            Err(e) => Err(format!("{p}: could not ask GitHub ({e})")),
        };
    }
    if is_message(p) {
        return plain(Kind::Message);
    }
    if p.contains('/') || p.starts_with('~') {
        return check_path(p, w);
    }
    Err(format!(
        "{p:?} is not a pointer: commit, ev_…, q_…, pr:<owner>/<repo>#<n>, a receipt path or path:line"
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn legacy_shapes_match_the_python_regexes() {
        for (p, k) in [
            ("deadbee", Some(Kind::Commit)),
            ("DEADBEE", None),
            ("dm:abcd", Some(Kind::Message)),
            ("msg_id-abcd", Some(Kind::Message)),
            ("q_0123456789abcdef", None),
            ("docs/x.md", Some(Kind::Receipt)),
            ("x.md", None),
            ("x.rs:12", Some(Kind::FileLine)),
            ("a/b/x.rs:12", Some(Kind::FileLine)),
            ("/abs/x.md", None),
            ("a b", None),
        ] {
            assert_eq!(legacy_kind(p), k, "{p}");
        }
    }

    #[test]
    fn pr_refs() {
        assert_eq!(
            pr_ref("pr:ez2app/ez2-aib#604"),
            Some(("ez2app/ez2-aib".into(), 604))
        );
        for bad in [
            "pr:ez2app#1",
            "pr:a/b#0",
            "pr:a/b#",
            "pr:a/b#x",
            "pr:a b/c#1",
            "pr:/b#1",
        ] {
            assert_eq!(pr_ref(bad), None, "{bad}");
        }
    }

    #[test]
    fn line_suffix() {
        assert_eq!(split_line("a/b.md:3"), Ok(("a/b.md", Some(3))));
        assert_eq!(split_line("a/b.md"), Ok(("a/b.md", None)));
        assert!(split_line("a/b.md:0").is_err());
        assert!(split_line("a/b.md:12345678").is_err());
    }
}
