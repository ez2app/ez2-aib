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
//!    its sha256 (`failed sign_hash_changed`), linted from that copy (`not_allowed sign_lint`);
//!    the copy is what is shown and what runs;
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
    pub staged: Option<Map<String, Value>>,
    /// SIGN: the copy that runs (`/bin/bash <copy>`).
    pub sign_copy: Option<(PathBuf, String)>,
    /// The folder holding the copies; removed on drop unless kept by the run.
    pub scratch: Option<Scratch>,
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

/// 1.x `type(t) is int`: an integer (not a bool, not a float), any size serde can hold.
fn int_t(v: Option<&Value>) -> Option<i128> {
    let n = v?.as_number()?;
    n.as_i64()
        .map(i128::from)
        .or_else(|| n.as_u64().map(i128::from))
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

/// 1.x `run_signlint` on the copy, through the library. `Err(why)` only where 1.x could
/// not run the lint at all (`signlint_rc:2`: not UTF-8).
struct Lint {
    ok: bool,
    findings: Vec<Value>,
    findings_more: usize,
    acked: Vec<Value>,
    acked_more: usize,
    rehearsal: Option<Value>,
}

fn run_lint(copy: &Path, acks: &[String]) -> Result<Lint, &'static str> {
    let text = fs::read(copy)
        .ok()
        .and_then(|b| String::from_utf8(b).ok())
        .ok_or("signlint_rc:2")?;
    let name = copy.file_name().and_then(|n| n.to_str()).unwrap_or("");
    let set: BTreeSet<String> = acks.iter().cloned().collect();
    let (left, acked) = aib_signlint::split_acked(aib_signlint::lint(&text, name), &set);
    let rehearsal = aib_signlint::rehearsal(&text).map(|r| {
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
    let fresh = int_t(req.get("t")).is_some_and(|t| {
        let t = t as f64;
        !(now - t > EXPIRE_S as f64 || t - now > AHEAD_S)
    });
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
    let lint = match run_lint(&copy, &acks) {
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
