//! `aib-release host-build` (DEV 4.6 r2 §9 P1): before a build of
//! `aib-cloud-host` goes onto a host, say from git alone whether it may.
//!
//! On 11-11 a build made from a PR head went onto the host. Its version was
//! the same as main's, it lacked #638, and nothing could tell. A build now
//! carries the commit it was made from and whether the sources that went into
//! it had uncommitted changes (`aib-cloud-host build-info`, also readable out
//! of the file itself, see [`info_in_binary`]). The host has no git, so the
//! check runs here, where the repository is:
//!
//! 1. the candidate's commit is known and it was built clean
//!    (`candidate_unknown`, `candidate_dirty`);
//! 2. the candidate is on main: an ancestor of [`MAIN_REF`] (`not_on_main`).
//!    **There is no flag for this one**, and the main line cannot be named
//!    from the command line: a PR head never goes onto a host;
//! 3. the candidate is a descendant of what the host runs now
//!    (`not_a_descendant`), unless a downgrade is asked for with its reason;
//! 4. a host build made before build info existed, or one that says
//!    `commit=unknown`, is `current_unknown`, passed only when asked for with
//!    its reason.
//!
//! A reason given is printed in the answer, so it can go onto the approval
//! the person reads; a reason given but not needed is listed too
//! (`unused_allow`). Nothing is fetched and nothing is written: the
//! repository is only asked `rev-parse`, `cat-file -e` and
//! `merge-base --is-ancestor`. It is trusted: run it in our own clone, right
//! after `git fetch origin main` (a stale main only refuses more). Exit: 0 may
//! go on, 2 refused, 3 git could not answer, 64 usage.

use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::Command;

/// The main line a host build must be on. Fixed: not a flag, not an
/// environment variable (2240e, #650 M1: `--main <PR head>` passed a PR head).
pub const MAIN_REF: &str = "refs/remotes/origin/main";

/// What starts the build info in `aib-cloud-host build-info` and in the file.
pub const MARK: &str = "aib-build-info ";

/// A build as it describes itself. `commit` is `None` for `commit=unknown`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BuildInfo {
    pub version: String,
    pub commit: Option<String>,
    pub dirty: bool,
}

fn full_sha(s: &str) -> bool {
    s.len() == 40
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// `aib-build-info version=V commit=C dirty=D;` (the mark and the `;` may be
/// left out). Every key once, nothing else; `C` is 40 lowercase hex or
/// `unknown`; `D` is `0` or `1`.
pub fn parse_info(line: &str) -> Option<BuildInfo> {
    let s = line.trim();
    let s = s.strip_prefix(MARK).unwrap_or(s);
    let s = s.strip_suffix(';').unwrap_or(s);
    let (mut version, mut commit, mut dirty) = (None, None, None);
    for part in s.split(' ') {
        let (k, v) = part.split_once('=')?;
        let slot = match k {
            "version" => &mut version,
            "commit" => &mut commit,
            "dirty" => &mut dirty,
            _ => return None,
        };
        if slot.replace(v).is_some() || v.is_empty() {
            return None;
        }
    }
    let commit = match commit? {
        "unknown" => None,
        c if full_sha(c) => Some(c.to_owned()),
        _ => return None,
    };
    let dirty = match dirty? {
        "0" => false,
        "1" => true,
        _ => return None,
    };
    let version = version?;
    if !version
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'-')
    {
        return None;
    }
    Some(BuildInfo {
        version: version.to_owned(),
        commit,
        dirty,
    })
}

/// sha256 of a file's bytes, lowercase hex: what `shasum -a 256` / `sha256sum` print.
pub fn sha256_hex(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// The build info written into a built file (any platform's: it is read as
/// bytes, never run). Every copy in the file must say the same thing.
pub fn info_in_binary(bytes: &[u8]) -> Result<BuildInfo, String> {
    let mark = MARK.as_bytes();
    let mut found: Option<BuildInfo> = None;
    let mut i = 0;
    while let Some(at) = bytes[i..].windows(mark.len()).position(|w| w == mark) {
        let start = i + at;
        let rest = &bytes[start..];
        let end = rest
            .iter()
            .take(200)
            .position(|b| *b == b';')
            .ok_or("build info without its end")?;
        let line = std::str::from_utf8(&rest[..=end]).map_err(|_| "build info is not text")?;
        let info =
            parse_info(line).ok_or_else(|| format!("build info not understood: {line:?}"))?;
        match &found {
            Some(f) if *f != info => {
                return Err("the file carries two different build infos".into())
            }
            _ => found = Some(info),
        }
        i = start + mark.len();
    }
    found.ok_or_else(|| "no build info in the file (built before build info existed?)".into())
}

/// The git questions the check asks. [`GitCli`] is the real one.
pub trait Git {
    /// `ref` as a full commit SHA.
    fn resolve(&self, r: &str) -> Result<String, String>;
    /// Whether the repository has this commit.
    fn has_commit(&self, sha: &str) -> Result<bool, String>;
    /// Whether `a` is an ancestor of `b` (or the same commit).
    fn is_ancestor(&self, a: &str, b: &str) -> Result<bool, String>;
}

/// What the host runs now.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Current {
    /// It printed (or its file carries) this build info.
    Known(BuildInfo),
    /// Built before build info existed: nothing to compare with.
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Request {
    pub candidate: BuildInfo,
    /// sha256 of the candidate file, when it was read from one (`--candidate-bin`): the
    /// approval names this file, and the swap step installs only a file with the same hash
    /// (4542e, #650 L1: otherwise A is checked and B goes on).
    pub candidate_sha256: Option<String>,
    pub current: Current,
    /// The main line; [`Request::for_host`] sets [`MAIN_REF`].
    pub main: String,
    /// `--allow-downgrade <why>`.
    pub allow_downgrade: Option<String>,
    /// `--allow-unknown-current <why>`.
    pub allow_unknown_current: Option<String>,
}

impl Request {
    /// The request the command line makes: the main line is always [`MAIN_REF`].
    pub fn for_host(candidate: BuildInfo, current: Current) -> Self {
        Self {
            candidate,
            candidate_sha256: None,
            current,
            main: MAIN_REF.to_owned(),
            allow_downgrade: None,
            allow_unknown_current: None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    Ok,
    Refused,
    Error,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Report {
    pub verdict: Verdict,
    /// A fixed word: `ok`, the refusal, or `git`.
    pub code: &'static str,
    pub detail: String,
    pub main: Option<String>,
    /// The flags that let it through, each with its reason.
    pub allowed: Vec<(&'static str, String)>,
}

fn report(verdict: Verdict, code: &'static str, detail: String) -> Report {
    Report {
        verdict,
        code,
        detail,
        main: None,
        allowed: Vec::new(),
    }
}

/// The check, in the order of the module doc; the first thing that is not
/// right decides.
pub fn check(git: &dyn Git, req: &Request) -> Report {
    let Some(cand) = req.candidate.commit.as_deref() else {
        return report(
            Verdict::Refused,
            "candidate_unknown",
            "the candidate does not say which commit it was built from".into(),
        );
    };
    if req.candidate.dirty {
        return report(
            Verdict::Refused,
            "candidate_dirty",
            format!("the candidate was built from {cand} with uncommitted changes"),
        );
    }
    let main = match git.resolve(&req.main) {
        Ok(m) => m,
        Err(e) => return report(Verdict::Error, "git", e),
    };
    let rep = |verdict, code, detail| Report {
        main: Some(main.clone()),
        ..report(verdict, code, detail)
    };
    match git.has_commit(cand) {
        Ok(true) => {}
        Ok(false) => {
            return rep(
                Verdict::Error,
                "git",
                format!("{cand} is not in this repository (fetch first)"),
            )
        }
        Err(e) => return rep(Verdict::Error, "git", e),
    }
    match git.is_ancestor(cand, &main) {
        Ok(true) => {}
        Ok(false) => {
            return rep(
                Verdict::Refused,
                "not_on_main",
                format!(
                    "{cand} is not on {} ({main}): only main goes onto a host",
                    req.main
                ),
            )
        }
        Err(e) => return rep(Verdict::Error, "git", e),
    }
    let mut allowed = Vec::new();
    // what the host runs, when it says; `None` for no build info or `commit=unknown`
    let current = match &req.current {
        Current::Known(info) => info.commit.as_deref(),
        Current::Unknown => None,
    };
    match current {
        None => match &req.allow_unknown_current {
            Some(why) => allowed.push(("allow_unknown_current", why.clone())),
            None => {
                return rep(
                    Verdict::Refused,
                    "current_unknown",
                    "the host's build does not say which commit it was built from".into(),
                )
            }
        },
        Some(cur) => {
            match git.has_commit(cur) {
                Ok(true) => {}
                Ok(false) => {
                    return rep(
                        Verdict::Error,
                        "git",
                        format!("the host's {cur} is not in this repository (fetch first)"),
                    )
                }
                Err(e) => return rep(Verdict::Error, "git", e),
            }
            match git.is_ancestor(cur, cand) {
                Ok(true) => {}
                Ok(false) => match &req.allow_downgrade {
                    Some(why) => allowed.push(("allow_downgrade", why.clone())),
                    None => {
                        return rep(
                            Verdict::Refused,
                            "not_a_descendant",
                            format!(
                                "{cand} does not contain the host's {cur}: it would take something away"
                            ),
                        )
                    }
                },
                Err(e) => return rep(Verdict::Error, "git", e),
            }
        }
    }
    Report {
        allowed,
        ..rep(
            Verdict::Ok,
            "ok",
            format!("{cand} is on main and contains what the host runs"),
        )
    }
}

pub fn exit_code(rep: &Report) -> i32 {
    match rep.verdict {
        Verdict::Ok => 0,
        Verdict::Refused => 2,
        Verdict::Error => 3,
    }
}

pub fn to_json(req: &Request, rep: &Report) -> Value {
    let info = |i: &BuildInfo| json!({"version": i.version, "commit": i.commit, "dirty": i.dirty});
    json!({
        "verdict": match rep.verdict {
            Verdict::Ok => "ok",
            Verdict::Refused => "refused",
            Verdict::Error => "error",
        },
        "code": rep.code,
        "detail": rep.detail,
        "main_ref": req.main,
        "main": rep.main,
        "candidate": info(&req.candidate),
        "candidate_sha256": req.candidate_sha256,
        "current": match &req.current {
            Current::Known(i) => info(i),
            Current::Unknown => Value::Null,
        },
        "allowed": rep.allowed.iter().map(|(f, why)| json!({"flag": f, "why": why})).collect::<Vec<_>>(),
        // a reason given that this check did not need: someone tried, the approval shows it
        "unused_allow": unused_allow(req, rep),
    })
}

fn unused_allow(req: &Request, rep: &Report) -> Vec<Value> {
    [
        ("allow_downgrade", &req.allow_downgrade),
        ("allow_unknown_current", &req.allow_unknown_current),
    ]
    .into_iter()
    .filter_map(|(flag, why)| {
        let why = why.as_ref()?;
        (!rep.allowed.iter().any(|(f, _)| *f == flag)).then(|| json!({"flag": flag, "why": why}))
    })
    .collect()
}

/// The real git, asked about one repository with no repository chosen by the
/// caller's environment.
pub struct GitCli {
    dir: PathBuf,
}

impl GitCli {
    pub fn new(dir: &Path) -> Self {
        Self {
            dir: dir.to_owned(),
        }
    }

    fn git(&self, args: &[&str]) -> Result<std::process::Output, String> {
        let mut c = Command::new("git");
        // nothing from the caller's environment chooses the repository, its objects
        // or its settings: every GIT_* goes (GIT_DIR, GIT_CONFIG_COUNT / KEY_n /
        // VALUE_n, GIT_CONFIG_PARAMETERS from `git -c`, …) (2240e, #650 N1)
        for (k, _) in std::env::vars_os() {
            if k.to_string_lossy().starts_with("GIT_") {
                c.env_remove(k);
            }
        }
        // refs/replace in the repository would change the ancestry merge-base sees (2240e r2 N2)
        c.env("GIT_NO_REPLACE_OBJECTS", "1");
        c.arg("-C").arg(&self.dir).args(args);
        c.output()
            .map_err(|e| format!("git {}: {e}", args.join(" ")))
    }
}

impl GitCli {
    /// When this clone last fetched: the time of its `FETCH_HEAD` (seconds since the epoch),
    /// or None when it never fetched or git cannot say. Printed next to a verdict judged
    /// against the local origin/main, so a stale snapshot shows (binsync).
    pub fn fetched_at(&self) -> Option<u64> {
        let out = self
            .git(&[
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "FETCH_HEAD",
            ])
            .ok()?;
        if !out.status.success() {
            return None;
        }
        let p = PathBuf::from(String::from_utf8_lossy(&out.stdout).trim());
        std::fs::metadata(p)
            .ok()?
            .modified()
            .ok()?
            .duration_since(std::time::UNIX_EPOCH)
            .ok()
            .map(|d| d.as_secs())
    }
}

impl Git for GitCli {
    fn resolve(&self, r: &str) -> Result<String, String> {
        if r.starts_with('-') {
            return Err(format!("{r:?} is not a ref"));
        }
        let spec = format!("{r}^{{commit}}");
        let out = self.git(&["rev-parse", "--verify", "--quiet", &spec])?;
        let sha = String::from_utf8_lossy(&out.stdout).trim().to_owned();
        if out.status.success() && full_sha(&sha) {
            Ok(sha)
        } else {
            Err(format!("{r} is not a commit in {}", self.dir.display()))
        }
    }

    fn has_commit(&self, sha: &str) -> Result<bool, String> {
        if !full_sha(sha) {
            return Err(format!("{sha:?} is not a full SHA"));
        }
        let out = self.git(&["cat-file", "-e", &format!("{sha}^{{commit}}")])?;
        Ok(out.status.success())
    }

    fn is_ancestor(&self, a: &str, b: &str) -> Result<bool, String> {
        if !full_sha(a) || !full_sha(b) {
            return Err("is-ancestor takes full SHAs only".into());
        }
        let out = self.git(&["merge-base", "--is-ancestor", a, b])?;
        match out.status.code() {
            Some(0) => Ok(true),
            Some(1) => Ok(false),
            _ => Err(format!(
                "git merge-base --is-ancestor {a} {b}: {}",
                String::from_utf8_lossy(&out.stderr).trim()
            )),
        }
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)] // test code: a panic is the failure report
mod tests {
    use super::*;

    const A: &str = "0123456789abcdef0123456789abcdef01234567";

    #[test]
    fn build_info_reads_only_the_exact_shape() {
        let line = format!("aib-build-info version=0.2.0-preview.3 commit={A} dirty=0;");
        assert_eq!(
            parse_info(&line),
            Some(BuildInfo {
                version: "0.2.0-preview.3".into(),
                commit: Some(A.into()),
                dirty: false
            })
        );
        assert_eq!(
            parse_info("version=1.0.0 commit=unknown dirty=1")
                .unwrap()
                .commit,
            None
        );
        for bad in [
            String::new(),
            format!("version=1 commit={A}"),
            format!("version=1 commit={A} dirty=2"),
            format!("version=1 commit={} dirty=0", A.to_uppercase()),
            format!("version=1 commit={} dirty=0", &A[..12]),
            format!("version=1 commit={A} dirty=0 dirty=0"),
            format!("version=1 commit={A} dirty=0 extra=1"),
            format!("version=1;rm commit={A} dirty=0"),
        ] {
            assert_eq!(parse_info(&bad), None, "{bad}");
        }
    }

    #[test]
    fn build_info_is_read_out_of_the_file_and_must_agree() {
        let line = format!("aib-build-info version=1.0.0 commit={A} dirty=0;");
        let mut bin = b"\x7fELF\0\0junk".to_vec();
        bin.extend_from_slice(line.as_bytes());
        bin.extend_from_slice(b"\0more\0");
        assert_eq!(info_in_binary(&bin).unwrap().commit.as_deref(), Some(A));
        // the same twice is fine; two different ones are not
        let mut twice = bin.clone();
        twice.extend_from_slice(line.as_bytes());
        assert!(info_in_binary(&twice).is_ok());
        let mut two = bin.clone();
        two.extend_from_slice(line.replace("dirty=0", "dirty=1").as_bytes());
        assert!(info_in_binary(&two).unwrap_err().contains("two different"));
        assert!(info_in_binary(b"\x7fELF no info here")
            .unwrap_err()
            .contains("no build info"));
    }
}
