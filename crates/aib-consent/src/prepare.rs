//! The checks before anyone is asked (B2 T136-3a): the first half of 1.x `decide`
//! (consent.py :709–:790), up to the point where Touch ID would be asked.
//!
//! In order, each one stopping with the 1.x result and `why` words:
//! 1. `t` an integer, not older than [`EXPIRE_S`], not more than 60 s ahead ⇒ else `expired`;
//! 2. [`classify`] ⇒ `not_allowed` + its word;
//! 3. the summary cleaned like 1.x `_clean_summary(str(x or ""))` ⇒ empty: `invalid summary_missing`;
//! 4. a command longer than [`REASON_CMD_MAX`] shown makes the request strong;
//! 5. SIGN: the acks (`invalid lint_ack_code`), the file read with `O_NOFOLLOW` up to
//!    256 KB (`failed sign_unreadable`), copied into a fresh 0700 folder and checked against
//!    its sha256 (`failed sign_hash_changed`), linted (`not_allowed sign_lint`) from the
//!    same bytes that were hashed and shown, never read again from disk (ebb36 T136-3a L1);
//!    the copy is what runs, after T136-3b checks it against the sha once more;
//! 6. shared program: pin / review shape (`invalid generic_pin_or_review`), then
//!    [`params::stage`] into a fresh 0700 folder (`failed` for stage_dir / stage_write /
//!    stage_hash_changed / stage_output, `not_allowed` for every other word).
//!
//! What 1.x does after this (the long Touch ID reason may also make it strong, the dialog,
//! Touch ID) is T136-3b; it gets a [`Ready`].
//!
//! Known differences from 1.x (it started programs; this calls the libraries):
//! - `signlint_missing`, `signlint_error:*`, `signlint_output`, `consent_run_missing`,
//!   `stage_error:*`, `stage_rc:*` cannot happen. A SIGN copy that is not UTF-8 still gives
//!   `failed signlint_rc:2`, the word 1.x got from the command refusing it.
//! - The 1.x long-reason rule (`_reason_full` over 160 characters) needs the dialog text
//!   (T135-b) and is applied in T136-3b.
//! - A pin with a trailing line break passed 1.x `_PIN_RE` (`$` matches before `\n`) and
//!   failed later as a sha mismatch (`not_allowed`); here it is `invalid
//!   generic_pin_or_review` straight away.
//! - A summary that is a non-zero float, or a non-empty list / object, was printed by
//!   Python (`repr`) and shown; here it is `invalid summary_missing` (`aib consent ask`
//!   always writes a string).
//! - Nested JSON objects come out with their keys sorted (serde_json without
//!   `preserve_order`); the values are the same.
//! - A scratch folder that cannot be made: 1.x raised out of `decide` (`mkdtemp` was
//!   outside its `try`); here SIGN gives `failed sign_hash_changed` and a shared program
//!   `failed stage_dir` (also when 8 random names all exist, which does not happen).
//! - 1.x linted the copy by path (the command read it), so a same-user program could swap
//!   the copy between the hash check and the lint; here the hashed bytes are linted.
//! - A `~other/…` path that cannot be expanded (1.x `expanduser` raised) cannot get here:
//!   [`classify`] already refuses it (`sign_missing` / `generic_missing`).

use crate::params::{self, Refused};
use crate::request::{self, Kind, EXPIRE_S, MAX_SIGN_BYTES};
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;
use std::fs;
use std::io::Write;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::{Path, PathBuf};

/// 1.x `REASON_CMD_MAX`: a command longer than this (as shown) makes the request strong.
pub const REASON_CMD_MAX: usize = 120;
/// 1.x `SIGN_HEAD_LINES`: lines of a SIGN script shown in the dialog.
pub const SIGN_HEAD_LINES: usize = 12;
/// 1.x `LINT_SHOW_MAX`: findings kept per list.
pub const LINT_SHOW_MAX: usize = 20;
/// How far ahead of now `t` may be, in seconds (1.x `t - now > 60`).
pub const AHEAD_S: f64 = 60.0;

/// 1.x `_STAGE_FAILED`: stage words that are a failure on this side, not a refusal of
/// the program.
const STAGE_FAILED: [&str; 5] = [
    "stage_dir",
    "stage_write",
    "stage_hash_changed",
    "stage_output",
    "stage_refused",
];

/// A fresh 0700 folder under the temp dir, removed on drop unless kept.
#[derive(Debug)]
pub struct Scratch {
    path: PathBuf,
    keep: bool,
}

impl Scratch {
    /// 1.x `tempfile.mkdtemp(prefix=…)` + `chmod 0700`.
    pub fn new(prefix: &str) -> std::io::Result<Scratch> {
        let base = std::env::temp_dir();
        for _ in 0..8 {
            let p = base.join(format!("{prefix}{}", request::token_hex(4)?));
            match fs::DirBuilder::new().mode(0o700).create(&p) {
                Ok(()) => {
                    return Ok(Scratch {
                        path: p,
                        keep: false,
                    })
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(e) => return Err(e),
            }
        }
        Err(std::io::Error::from(std::io::ErrorKind::AlreadyExists))
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Hand the folder on (the run removes it when done).
    pub fn keep(mut self) -> PathBuf {
        self.keep = true;
        self.path.clone()
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        if !self.keep {
            let _ = fs::remove_dir_all(&self.path);
        }
    }
}

/// Stopped before anyone is asked: the 1.x result word and the detail for the done record.
#[derive(Debug, PartialEq)]
pub struct Stop {
    pub result: &'static str,
    pub detail: Map<String, Value>,
}

/// Passed every check; T136-3b asks the person with `view`.
#[derive(Debug)]
pub struct Ready {
    pub kind: Kind,
    /// The request as shown: 1.x `view` (summary cleaned, strong, seat; `sign` / `lint` /
    /// `program` added).
    pub view: Map<String, Value>,
    pub strong: bool,
    /// What goes into the done record whatever the answer (1.x `lint_rec`).
    pub lint_rec: Map<String, Value>,
    /// SIGN: the checked copy and its sha256. Shared program: the staged program / params.
    /// The folder is 0700 but the same user can still write it: right before the run,
    /// read each copy again with [`params::read_staged`] against these sha256 and use those
    /// bytes (ebb36 T136-3a N3).
    pub staged: Option<Map<String, Value>>,
    /// SIGN: the copy that runs and its sha256; read it back with [`params::read_staged`]
    /// right before the run, never run the path as it is.
    pub sign_copy: Option<(PathBuf, String)>,
    /// The folder holding the copies; removed on drop unless kept by the run.
    pub scratch: Option<Scratch>,
}

/// What runs, read again right before the run (ebb36 T136-3a N3).
#[derive(Debug, PartialEq, Eq)]
pub struct StagedBytes {
    /// The SIGN script or the shared program.
    pub program: Vec<u8>,
    /// The shared program's parameter file.
    pub params: Option<Vec<u8>>,
}

impl Ready {
    /// Read the copies again and check them against the sha256 taken here; these bytes
    /// are what runs, never the path. `Ok(None)` for kinds with no copy (`aib`, `claude
    /// plugin`). `staged_missing` / `staged_hash_changed` mean: do not run.
    pub fn staged_bytes(&self) -> Result<Option<StagedBytes>, Refused> {
        match self.kind {
            Kind::SignScript => {
                let (copy, sha) = self.sign_copy.as_ref().ok_or(Refused {
                    code: "staged_missing",
                    line: 0,
                })?;
                Ok(Some(StagedBytes {
                    program: params::read_staged(copy, sha, MAX_SIGN_BYTES)?,
                    params: None,
                }))
            }
            Kind::SharedProgram => {
                let st = self.staged.as_ref().ok_or(Refused {
                    code: "staged_missing",
                    line: 0,
                })?;
                let get = |path: &str, sha: &str, limit: u64| match (
                    st.get(path).and_then(Value::as_str),
                    st.get(sha).and_then(Value::as_str),
                ) {
                    (Some(p), Some(h)) => params::read_staged(Path::new(p), h, limit),
                    _ => Err(Refused {
                        code: "staged_missing",
                        line: 0,
                    }),
                };
                Ok(Some(StagedBytes {
                    program: get("program_copy", "program_sha", params::PROGRAM_MAX_BYTES)?,
                    params: Some(get(
                        "params_copy",
                        "params_sha",
                        params::PARAMS_MAX_BYTES as u64,
                    )?),
                }))
            }
            _ => Ok(None),
        }
    }
}

fn stop(result: &'static str, detail: Value) -> Stop {
    Stop {
        result,
        detail: match detail {
            Value::Object(m) => m,
            _ => Map::new(),
        },
    }
}

/// 1.x `TA.clean`: drop the invisible / reordering characters, turn control characters
/// and line / paragraph separators into spaces, trim, cut to `limit` code points with `…`.
pub fn ta_clean(text: &str, limit: usize) -> String {
    let s: String = text
        .chars()
        .filter(|c| {
            !matches!(*c as u32,
                0x00AD | 0x061C | 0x200B..=0x200F | 0x202A..=0x202E | 0x2060..=0x2069 | 0xFEFF)
        })
        .map(|c| {
            if matches!(c as u32, 0x00..=0x1F | 0x7F..=0x9F | 0x2028 | 0x2029) {
                ' '
            } else {
                c
            }
        })
        .collect();
    let t = s.trim_matches(py_space);
    if t.chars().count() <= limit {
        t.to_owned()
    } else {
        let mut out: String = t.chars().take(limit.saturating_sub(1)).collect();
        out.push('…');
        out
    }
}

/// Python `str.isspace` for `strip()`.
fn py_space(c: char) -> bool {
    c.is_whitespace() || matches!(c as u32, 0x1C..=0x1F)
}

/// 1.x `_plain`: characters [`request::has_bad_char`] refuses are dropped, then [`ta_clean`].
pub fn plain(text: &str, limit: usize) -> String {
    let kept: String = text
        .chars()
        .filter(|c| !aib_approve::text::is_bad_char(*c))
        .collect();
    ta_clean(&kept, limit)
}

/// Python `str.splitlines()`.
fn py_splitlines(s: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut it = s.char_indices().peekable();
    while let Some((i, c)) = it.next() {
        let br = matches!(
            c,
            '\n' | '\r'
                | '\u{0B}'
                | '\u{0C}'
                | '\u{1C}'
                | '\u{1D}'
                | '\u{1E}'
                | '\u{85}'
                | '\u{2028}'
                | '\u{2029}'
        );
        if br {
            out.push(&s[start..i]);
            let mut next = i + c.len_utf8();
            if c == '\r' {
                if let Some(&(j, '\n')) = it.peek() {
                    it.next();
                    next = j + 1;
                }
            }
            start = next;
        }
    }
    if start < s.len() {
        out.push(&s[start..]);
    }
    out
}

/// 1.x `str(x or "")` for the summary. `None` where Python would print a `repr` this does
/// not copy (see the module's Known differences).
fn summary_text(v: Option<&Value>) -> Option<String> {
    Some(match v {
        None | Some(Value::Null) | Some(Value::Bool(false)) => String::new(),
        Some(Value::Bool(true)) => "True".into(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Array(a)) if a.is_empty() => String::new(),
        Some(Value::Object(o)) if o.is_empty() => String::new(),
        Some(Value::Number(n)) => {
            if let Some(i) = n.as_i64() {
                if i == 0 {
                    String::new()
                } else {
                    i.to_string()
                }
            } else if let Some(u) = n.as_u64() {
                u.to_string()
            } else if n.as_f64() == Some(0.0) {
                String::new()
            } else {
                return None;
            }
        }
        Some(_) => return None,
    })
}

/// 1.x `_sign_info`: the bytes, their sha256 and the first lines as shown.
struct SignInfo {
    raw: Vec<u8>,
    sha256: String,
    head: String,
}

fn sign_info(path: &Path) -> Option<SignInfo> {
    let raw = request::read_regular(path, MAX_SIGN_BYTES)?;
    let text = String::from_utf8_lossy(&raw);
    let head = py_splitlines(&text)
        .into_iter()
        .take(SIGN_HEAD_LINES)
        .map(|l| ta_clean(l, 160))
        .collect::<Vec<_>>()
        .join("\n");
    Some(SignInfo {
        sha256: params::sha256_hex(&raw),
        head,
        raw,
    })
}

/// Write `bytes` as a new 0600 file and read it back: equal to `sha256`?
fn copy_checked(path: &Path, bytes: &[u8], sha256: &str) -> bool {
    let wrote = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(nix::libc::O_NOFOLLOW)
        .open(path)
        .and_then(|mut f| f.write_all(bytes));
    wrote.is_ok() && fs::read(path).is_ok_and(|b| params::sha256_hex(&b) == sha256)
}

fn finding_json(f: &aib_signlint::Finding) -> Value {
    json!({"rule": f.rule, "line": f.line, "state": f.state.as_str(), "msg": f.msg})
}

/// 1.x `run_signlint`'s result, through the library.
struct Lint {
    ok: bool,
    findings: Vec<Value>,
    findings_more: usize,
    acked: Vec<Value>,
    acked_more: usize,
    rehearsal: Option<Value>,
}

/// The lint of `raw` (the bytes already hashed) as a file called `name`. `Err` only where
/// the 1.x command refused the file (`signlint_rc:2`: not UTF-8).
fn lint_bytes(raw: &[u8], name: &str, acks: &[String]) -> Result<Lint, &'static str> {
    let text = std::str::from_utf8(raw).map_err(|_| "signlint_rc:2")?;
    let set: BTreeSet<String> = acks.iter().cloned().collect();
    let (left, acked) = aib_signlint::split_acked(aib_signlint::lint(text, name), &set);
    let rehearsal = aib_signlint::rehearsal(text).map(|r| {
        json!({"env": plain(&r.env, aib_signlint::REHEARSAL_ENV_MAX),
               "receipt": plain(&r.receipt, aib_signlint::REHEARSAL_RECEIPT_MAX)})
    });
    Ok(Lint {
        ok: left.is_empty(),
        findings_more: left.len().saturating_sub(LINT_SHOW_MAX),
        acked_more: acked.len().saturating_sub(LINT_SHOW_MAX),
        findings: left.iter().take(LINT_SHOW_MAX).map(finding_json).collect(),
        acked: acked.iter().take(LINT_SHOW_MAX).map(finding_json).collect(),
        rehearsal,
    })
}

fn stage_failed(why: &str) -> bool {
    STAGE_FAILED.contains(&why)
}

/// 1.x `stage_program`'s output check, kept although the library makes this shape:
/// interpreter, copies inside `dir`, program sha equal to the pin.
fn stage_shape_ok(st: &Value, pin: &str, dir: &Path) -> bool {
    let prog = &st["program"];
    let par = &st["params"];
    let argv: Option<Vec<&str>> = prog["argv"]
        .as_array()
        .and_then(|a| a.iter().map(Value::as_str).collect());
    let in_dir = |v: &Value| {
        v.as_str()
            .is_some_and(|s| Path::new(s).parent() == Some(dir))
    };
    match argv {
        Some(a) if !a.is_empty() => {
            matches!(a[0], "/bin/bash" | "/usr/bin/python3")
                && Some(a[a.len() - 1]) == prog["copy"].as_str()
                && prog["sha256"].as_str() == Some(pin)
                && par["sha256"].as_str().is_some_and(params::pin_ok)
                && in_dir(&prog["copy"])
                && in_dir(&par["copy"])
        }
        _ => false,
    }
}

/// The checks of 1.x `decide` before Touch ID. `home` is the person's home, `now` unix
/// seconds (1.x `time.time()`).
pub fn prepare(req: &Map<String, Value>, home: &Path, now: f64) -> Result<Ready, Stop> {
    let fresh = request::t_int(req.get("t"))
        .is_some_and(|t| !(now - t > EXPIRE_S as f64 || t - now > AHEAD_S));
    if !fresh {
        return Err(stop("expired", json!({})));
    }
    let argv_v = req.get("argv").cloned().unwrap_or(Value::Null);
    let mut strong = req.get("strong") == Some(&Value::Bool(true));
    let kind = match request::classify(&argv_v, strong, home) {
        Ok(k) => k,
        Err(why) => return Err(stop("not_allowed", json!({"why": why}))),
    };
    // classify accepted it, so argv is a list of strings
    let argv: Vec<String> = argv_v
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default();
    let summary = summary_text(req.get("summary"))
        .map(|s| request::clean_summary(&s))
        .unwrap_or_default();
    if summary.is_empty() {
        return Err(stop("invalid", json!({"why": "summary_missing"})));
    }
    let mut view = req.clone();
    view.insert("summary".into(), json!(summary));
    view.insert(
        "seat".into(),
        json!(request::seat(req.get("seat").unwrap_or(&Value::Null))),
    );
    if request::display_cmd(&argv).chars().count() > REASON_CMD_MAX {
        strong = true;
    }
    view.insert("strong".into(), json!(strong));
    let mut ready = Ready {
        kind,
        view,
        strong,
        lint_rec: Map::new(),
        staged: None,
        sign_copy: None,
        scratch: None,
    };
    match kind {
        Kind::SignScript => sign_script(&mut ready, req, &argv, home)?,
        Kind::SharedProgram => shared_program(&mut ready, req, &argv, home)?,
        _ => {}
    }
    Ok(ready)
}

fn sign_script(
    ready: &mut Ready,
    req: &Map<String, Value>,
    argv: &[String],
    home: &Path,
) -> Result<(), Stop> {
    let Some(acks) = request::lint_acks(req.get("lint_ack").unwrap_or(&Value::Null)) else {
        return Err(stop("invalid", json!({"why": "lint_ack_code"})));
    };
    let unreadable = || stop("failed", json!({"why": "sign_unreadable"}));
    let path = request::expand_user(&argv[1], home)
        .and_then(|p| fs::canonicalize(p).ok())
        .ok_or_else(unreadable)?;
    let sign = sign_info(&path).ok_or_else(unreadable)?;
    ready.view.insert(
        "sign".into(),
        json!({"sha256": sign.sha256, "head": sign.head}),
    );
    let changed = || stop("failed", json!({"why": "sign_hash_changed"}));
    let scratch = Scratch::new("aib-consent-sign-").map_err(|_| changed())?;
    let name = Path::new(&argv[1])
        .file_name()
        .map(PathBuf::from)
        .ok_or_else(changed)?;
    let copy = scratch.path().join(name);
    if !copy_checked(&copy, &sign.raw, &sign.sha256) {
        return Err(changed());
    }
    // 1.x gives the same detail whether the lint refused or could not run
    let lint_name = copy.file_name().and_then(|n| n.to_str()).unwrap_or("");
    let lint = match lint_bytes(&sign.raw, lint_name, &acks) {
        Ok(l) if l.ok => l,
        Ok(l) => {
            return Err(stop(
                "not_allowed",
                json!({"why": "sign_lint", "sha256": sign.sha256,
                       "lint": {"findings": l.findings, "findings_more": l.findings_more,
                                "acks": acks}}),
            ))
        }
        Err(why) => {
            return Err(stop(
                "failed",
                json!({"why": why, "sha256": sign.sha256,
                       "lint": {"findings": [], "findings_more": 0, "acks": acks}}),
            ))
        }
    };
    let mut rec = Map::new();
    rec.insert("acks".into(), json!(acks));
    rec.insert("acked".into(), json!(lint.acked));
    rec.insert("acked_more".into(), json!(lint.acked_more));
    if let Some(r) = lint.rehearsal {
        rec.insert("rehearsal".into(), r);
    }
    ready.view.insert("lint".into(), Value::Object(rec.clone()));
    ready.lint_rec.insert("lint".into(), Value::Object(rec));
    let mut staged = Map::new();
    staged.insert("program_copy".into(), json!(copy));
    staged.insert("program_sha".into(), json!(sign.sha256));
    ready.staged = Some(staged);
    ready.sign_copy = Some((copy, sign.sha256));
    ready.scratch = Some(scratch);
    Ok(())
}

fn shared_program(
    ready: &mut Ready,
    req: &Map<String, Value>,
    argv: &[String],
    home: &Path,
) -> Result<(), Stop> {
    let pin = req.get("pin").and_then(Value::as_str);
    let review = req.get("review").and_then(Value::as_str);
    let (Some(pin), Some(review)) = (pin, review) else {
        return Err(stop("invalid", json!({"why": "generic_pin_or_review"})));
    };
    if !params::pin_ok(pin)
        || review.is_empty()
        || request::has_bad_char(review)
        || !review.starts_with('/')
    {
        return Err(stop("invalid", json!({"why": "generic_pin_or_review"})));
    }
    let scratch = Scratch::new("aib-consent-stage-")
        .map_err(|_| stop("failed", json!({"why": "stage_dir", "line": Value::Null})))?;
    let refusal = |r: Refused| {
        let why = plain(r.code, 60);
        let result = if stage_failed(&why) {
            "failed"
        } else {
            "not_allowed"
        };
        stop(result, json!({"why": why, "line": r.line}))
    };
    let program = request::expand_user(&argv[1], home).ok_or_else(|| {
        refusal(Refused {
            code: "program_unreadable",
            line: 0,
        })
    })?;
    let pfile = request::expand_user(&argv[3], home).ok_or_else(|| {
        refusal(Refused {
            code: "params_unreadable",
            line: 0,
        })
    })?;
    let st = params::stage(
        home,
        &program,
        pin,
        &pfile,
        Path::new(review),
        scratch.path(),
    )
    .map_err(refusal)?;
    if !stage_shape_ok(&st, pin, scratch.path()) {
        return Err(stop(
            "failed",
            json!({"why": "stage_output", "line": Value::Null}),
        ));
    }
    let prog = &st["program"];
    let par = &st["params"];
    let keys: Vec<String> = par["pairs"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|p| p.get(0).and_then(Value::as_str))
                .map(|k| plain(k, 40))
                .collect()
        })
        .unwrap_or_default();
    ready.lint_rec.insert(
        "program".into(),
        json!({"real": plain(prog["real"].as_str().unwrap_or(""), 300), "sha256": pin,
               "params_sha256": par["sha256"],
               "review": plain(st["review"].as_str().unwrap_or(""), 300),
               "keys": keys}),
    );
    let mut staged = Map::new();
    staged.insert("program_copy".into(), prog["copy"].clone());
    staged.insert("program_sha".into(), prog["sha256"].clone());
    staged.insert("params_copy".into(), par["copy"].clone());
    staged.insert("params_sha".into(), par["sha256"].clone());
    ready.staged = Some(staged);
    ready.view.insert("program".into(), st);
    ready.scratch = Some(scratch);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shape(dir: &Path, copy: &str, sha: &str) -> Value {
        json!({"program": {"argv": ["/bin/bash", copy], "copy": copy, "sha256": sha},
               "params": {"sha256": "a".repeat(64), "copy": dir.join("params.env")}})
    }

    // ebb36 T136-3a: the stage output check is kept, so it gets a test
    #[test]
    fn the_stage_output_must_stay_in_the_folder_and_match_the_pin() {
        let dir = Path::new("/tmp/aib-x");
        let pin = "b".repeat(64);
        let inside = dir.join("p.sh");
        let inside = inside.to_str().unwrap_or("");
        assert!(stage_shape_ok(&shape(dir, inside, &pin), &pin, dir));
        assert!(!stage_shape_ok(&shape(dir, "/etc/p.sh", &pin), &pin, dir));
        assert!(!stage_shape_ok(
            &shape(dir, inside, &"c".repeat(64)),
            &pin,
            dir
        ));
        let mut v = shape(dir, inside, &pin);
        v["program"]["argv"] = json!(["/bin/sh", inside]);
        assert!(!stage_shape_ok(&v, &pin, dir));
        v["program"]["argv"] = json!([]);
        assert!(!stage_shape_ok(&v, &pin, dir));
    }

    // ebb36 T136-3a L1: what is linted is the bytes given, whatever the file holds now
    #[test]
    fn the_lint_reads_the_bytes_it_is_given() {
        let clean = "#!/bin/bash\n# 可丟環境: ct-900 收據: r.json\nset -euo pipefail\necho hi\n";
        assert!(lint_bytes(clean.as_bytes(), "SIGN-a.sh", &[]).is_ok_and(|l| l.ok));
        assert!(lint_bytes(b"#!/bin/bash\necho hi\n", "SIGN-a.sh", &[]).is_ok_and(|l| !l.ok));
        assert_eq!(
            lint_bytes(b"\xff", "SIGN-a.sh", &[]).err(),
            Some("signlint_rc:2")
        );
    }
}
