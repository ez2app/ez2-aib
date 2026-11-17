//! `aib-xtask knife-anchors [ROOT]`: every knife's anchor text must occur
//! exactly once in its target file, checked before the push.
//!
//! A knife (`scripts/knife.py FILE OLD NEW ...`) changes nothing and fails
//! with "pattern found N times" when OLD is not in FILE exactly once. That
//! only shows up in CI, after the build and tests of that cell: #578 lost a
//! whole round (20+ minutes) because main had moved the code the two
//! Windows knives pointed at (merge-ops plan v2 §7.2, a63ab 11-09).
//!
//! What is read: every `.github/workflows/*.yml` / `*.yaml` and
//! `scripts/*.sh` line (not subdirectories of `scripts/`)
//! that runs `scripts/knife.py`, either directly
//! (`python3 -I scripts/knife.py FILE 'OLD' 'NEW' ...`) or through a shell
//! function defined in the same workflow step / script
//! (`k() { python3 -I scripts/knife.py FILE "$1" "$2" ...; }` then
//! `k 'OLD' 'NEW'`). Shell words are split the way bash does for the forms
//! used there: `'...'`, `"..."` with `\"` `\\` `\$` `` \` ``, `\x`,
//! bash ANSI-C `$'...'` with `\n` `\t` `\r` `\\` `\'` `\"` `\xHH` (#580's
//! multi-line anchors), backslash-newline (removed, as bash does), `#`
//! comments. CRLF is read as LF in both the workflow and the target, as
//! knife.py's text mode does (a Windows checkout). The count is Python's `str.count`
//! (non-overlapping), the same rule knife.py applies.
//!
//! Fail closed: a knife line whose FILE or OLD cannot be read as plain text
//! (a variable other than `$1`..`$9` of a known function, a `$'...'`
//! escape other than those above, an unterminated quote, a missing
//! argument, an option before FILE / OLD or an OLD starting with `-`) is
//! `unresolved`, never skipped;
//! finding no knife at all is an error too (the scan is broken, not clean).
//!
//! Not read: knives that patch files their own way (for example
//! `scripts/check-win-job-knives.sh`, an inline Python replace on a fixed
//! file). Those are listed in the PR, not guessed at here.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

const KNIFE: &str = "scripts/knife.py";

/// One piece of a shell word: literal text, or a `$NAME` / `${NAME}` / `$1`.
#[derive(Debug, Clone, PartialEq)]
enum Part {
    Lit(String),
    Var(String),
}

/// A shell word, or one of the operators `;` `|` `&` (`op` set).
#[derive(Debug, Clone, PartialEq)]
struct Word {
    parts: Vec<Part>,
    op: bool,
}

impl Word {
    /// The text if the word is literal only.
    fn literal(&self) -> Option<String> {
        let mut s = String::new();
        for p in &self.parts {
            match p {
                Part::Lit(t) => s.push_str(t),
                Part::Var(_) => return None,
            }
        }
        Some(s)
    }
    fn is_knife(&self) -> bool {
        self.literal()
            .is_some_and(|t| t == KNIFE || t.ends_with("/scripts/knife.py"))
    }
}

fn push_lit(parts: &mut Vec<Part>, c: char) {
    if let Some(Part::Lit(s)) = parts.last_mut() {
        s.push(c);
    } else {
        parts.push(Part::Lit(c.to_string()));
    }
}

/// `$...` after the `$`: `{NAME}`, a digit, or a NAME. Anything else (for
/// example `$'`, `$(`) is an error.
fn read_var(cs: &[char], i: &mut usize) -> Result<String, String> {
    match cs.get(*i) {
        Some('{') => {
            let start = *i + 1;
            let end = cs[start..]
                .iter()
                .position(|c| *c == '}')
                .ok_or("unterminated ${")?;
            *i = start + end + 1;
            Ok(cs[start..start + end].iter().collect())
        }
        Some(c) if c.is_ascii_digit() => {
            *i += 1;
            Ok(c.to_string())
        }
        Some(c) if c.is_ascii_alphabetic() || *c == '_' => {
            let start = *i;
            while cs
                .get(*i)
                .is_some_and(|c| c.is_ascii_alphanumeric() || *c == '_')
            {
                *i += 1;
            }
            Ok(cs[start..*i].iter().collect())
        }
        Some(c) => Err(format!("unsupported ${c}")),
        None => Err("lone $ at end of line".into()),
    }
}

/// Bash ANSI-C quoting, after the opening `$'`: the text up to the closing
/// `'`. Only the escapes knives use are read; any other is an error (fail
/// closed, never a guess).
fn read_ansi_c(cs: &[char], i: &mut usize, parts: &mut Vec<Part>) -> Result<(), String> {
    let before = parts.len();
    loop {
        match cs.get(*i) {
            None => return Err("unterminated $'...'".into()),
            Some('\'') => {
                *i += 1;
                break;
            }
            Some('\\') => {
                let e = cs.get(*i + 1).ok_or("unterminated $'...'")?;
                let ch = match e {
                    'n' => '\n',
                    't' => '\t',
                    'r' => '\r',
                    '\\' => '\\',
                    '\'' => '\'',
                    '"' => '"',
                    'x' => {
                        let mut v = 0u32;
                        let mut n = 0;
                        while n < 2 {
                            match cs.get(*i + 2 + n).and_then(|c| c.to_digit(16)) {
                                Some(d) => {
                                    v = v * 16 + d;
                                    n += 1;
                                }
                                None => break,
                            }
                        }
                        if n == 0 {
                            return Err("$'...' \\x without hex digits".into());
                        }
                        let c = char::from_u32(v).filter(char::is_ascii);
                        let c = c.ok_or("$'...' \\x above 7f is not read")?;
                        push_lit(parts, c);
                        *i += 2 + n;
                        continue;
                    }
                    other => return Err(format!("$'...' escape \\{other} is not read")),
                };
                push_lit(parts, ch);
                *i += 2;
            }
            Some(c) => {
                push_lit(parts, *c);
                *i += 1;
            }
        }
    }
    if parts.len() == before {
        parts.push(Part::Lit(String::new()));
    }
    Ok(())
}

/// Split one logical line (continuations already joined) into words.
fn words(line: &str) -> Result<Vec<Word>, String> {
    let cs: Vec<char> = line.chars().collect();
    let mut out = Vec::new();
    let mut i = 0;
    while i < cs.len() {
        let c = cs[i];
        if c.is_whitespace() {
            i += 1;
            continue;
        }
        if c == '#' {
            break; // a comment: '#' at the start of a word
        }
        if matches!(c, ';' | '|' | '&') {
            out.push(Word {
                parts: vec![Part::Lit(c.to_string())],
                op: true,
            });
            i += 1;
            continue;
        }
        let mut parts = Vec::new();
        while i < cs.len() {
            let c = cs[i];
            if c.is_whitespace() || matches!(c, ';' | '|' | '&') {
                break;
            }
            match c {
                '\'' => {
                    let end = cs[i + 1..]
                        .iter()
                        .position(|c| *c == '\'')
                        .ok_or("unterminated single quote")?;
                    let t: String = cs[i + 1..i + 1 + end].iter().collect();
                    for ch in t.chars() {
                        push_lit(&mut parts, ch);
                    }
                    if end == 0 && parts.is_empty() {
                        parts.push(Part::Lit(String::new()));
                    }
                    i += end + 2;
                }
                '"' => {
                    i += 1;
                    let mut closed = false;
                    let before = parts.len();
                    while i < cs.len() {
                        match cs[i] {
                            '"' => {
                                closed = true;
                                i += 1;
                                break;
                            }
                            '\\' if matches!(cs.get(i + 1), Some('"' | '\\' | '$' | '`')) => {
                                push_lit(&mut parts, cs[i + 1]);
                                i += 2;
                            }
                            '$' => {
                                i += 1;
                                parts.push(Part::Var(read_var(&cs, &mut i)?));
                            }
                            '`' => return Err("command substitution".into()),
                            ch => {
                                push_lit(&mut parts, ch);
                                i += 1;
                            }
                        }
                    }
                    if !closed {
                        return Err("unterminated double quote".into());
                    }
                    if parts.len() == before {
                        parts.push(Part::Lit(String::new()));
                    }
                }
                '\\' => {
                    let n = cs.get(i + 1).ok_or("backslash at end of line")?;
                    push_lit(&mut parts, *n);
                    i += 2;
                }
                '$' => {
                    i += 1;
                    if cs.get(i) == Some(&'\'') {
                        i += 1;
                        read_ansi_c(&cs, &mut i, &mut parts)?;
                        continue;
                    }
                    parts.push(Part::Var(read_var(&cs, &mut i)?));
                }
                '`' => return Err("command substitution".into()),
                ch => {
                    push_lit(&mut parts, ch);
                    i += 1;
                }
            }
        }
        out.push(Word { parts, op: false });
    }
    Ok(out)
}

/// Lines joined at a trailing backslash (backslash and newline removed, no
/// space added, as bash does); each keeps the number of its first line.
fn logical_lines(text: &str) -> Vec<(usize, String)> {
    let mut out = Vec::new();
    let mut cur: Option<(usize, String)> = None;
    for (n, raw) in text.lines().enumerate() {
        let (start, mut acc) = cur.take().unwrap_or((n + 1, String::new()));
        let body = raw.trim_end();
        if let Some(head) = body.strip_suffix('\\') {
            // an even number of backslashes before it means a literal "\\"
            let run = head.chars().rev().take_while(|c| *c == '\\').count();
            if run % 2 == 0 {
                acc.push_str(head);
                cur = Some((start, acc));
                continue;
            }
        }
        acc.push_str(raw);
        out.push((start, acc));
    }
    if let Some(last) = cur {
        out.push(last);
    }
    out
}

/// A knife found in a file: where, the target file, the anchor text.
#[derive(Debug, Clone, PartialEq)]
pub struct Knife {
    pub at: String,
    pub file: String,
    pub old: String,
    /// The workflow job id it sits in (the key under `jobs:`; empty in a script).
    pub job: String,
    /// The workflow step it sits in (`- name:` text; empty in a script).
    pub step: String,
    /// Every word after `scripts/knife.py` up to the end of its command, filled in
    /// (FILE OLD NEW --expect-fail T -- CMD...). `Err` = a word that cannot be known
    /// (a variable other than $1..$9): `aib-xtask knife run` refuses that knife; the
    /// anchor check does not need it (S66-1).
    pub argv: Result<Vec<String>, String>,
}

#[derive(Debug, Default)]
pub struct Scan {
    pub knives: Vec<Knife>,
    /// `file:line: why`
    pub unresolved: Vec<String>,
}

/// FILE and OLD templates of a function's knife call, and every word after
/// `scripts/knife.py` (for `knife run`).
#[derive(Clone)]
struct Template {
    file: Word,
    old: Word,
    rest: Vec<Word>,
}

/// The text of a FILE / OLD template: literal parts as they are, each `$1`..`$9`
/// replaced by that plain-text argument (so `"crates/x/src/$1"` works, a63ab
/// #590 / #623 `ck`); any other variable cannot be known and is unresolved.
fn resolve(w: &Word, args: &[Word]) -> Result<String, String> {
    let mut s = String::new();
    for p in &w.parts {
        match p {
            Part::Lit(t) => s.push_str(t),
            Part::Var(v) => {
                let n = v
                    .parse::<usize>()
                    .ok()
                    .filter(|n| (1..=9).contains(n))
                    .ok_or("a variable that is not $1..$9 of the function")?;
                let a = args.get(n - 1).ok_or(format!("argument ${n} missing"))?;
                let t = a
                    .literal()
                    .ok_or_else(|| format!("argument ${n} is not plain text"))?;
                s.push_str(&t);
            }
        }
    }
    Ok(s)
}

/// Knife FILE/OLD right after the `scripts/knife.py` word at `k`.
fn after_knife(ws: &[Word], k: usize) -> Result<Template, String> {
    let file = ws.get(k + 1).filter(|w| !w.op).ok_or("FILE missing")?;
    let old = ws.get(k + 2).filter(|w| !w.op).ok_or("OLD missing")?;
    // knife.py uses argparse: an option before FILE / OLD moves them, and an
    // OLD starting with `-` is taken as an option; neither is guessed at
    for (w, what) in [(file, "FILE"), (old, "OLD")] {
        if w.literal().is_some_and(|t| t.starts_with('-')) {
            return Err(format!(
                "{what} starts with '-' (an option before it, or argparse would read it as one)"
            ));
        }
    }
    // the rest of the command: up to an operator, or the `}` that closes a function body
    let rest = ws[k + 1..]
        .iter()
        .take_while(|w| !w.op && w.literal().as_deref() != Some("}"))
        .cloned()
        .collect();
    Ok(Template {
        file: file.clone(),
        old: old.clone(),
        rest,
    })
}

/// Every word after `scripts/knife.py`, filled in with the call's arguments.
fn resolve_all(rest: &[Word], args: &[Word]) -> Result<Vec<String>, String> {
    rest.iter().map(|w| resolve(w, args)).collect()
}

/// The `- name:` text of a step line, without quotes.
fn step_name(line: &str) -> Option<String> {
    let v = line.trim_start().strip_prefix("- name:")?.trim();
    let v = v
        .strip_prefix('"')
        .and_then(|s| s.strip_suffix('"'))
        .or_else(|| v.strip_prefix('\'').and_then(|s| s.strip_suffix('\'')))
        .unwrap_or(v);
    Some(v.to_string())
}

/// `  ID:` (exactly two spaces, nothing after the colon) ⇒ the job id.
fn job_key(line: &str) -> Option<String> {
    let rest = line.strip_prefix("  ")?;
    if rest.starts_with(' ') {
        return None;
    }
    let id = rest.trim_end().strip_suffix(':')?;
    (!id.is_empty()
        && id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-'))
    .then(|| id.to_string())
}

/// A YAML step (or a new file) starts a new shell scope.
fn starts_step(line: &str) -> bool {
    let t = line.trim_start();
    t.starts_with("- name:") || t.starts_with("- uses:") || t.starts_with("- id:")
}

/// Function definition `NAME() {` or `NAME () {` ⇒ (NAME, index of the
/// first body word).
fn func_def(ws: &[Word]) -> Option<(String, usize)> {
    let a = ws.first()?.literal()?;
    if let Some(name) = a.strip_suffix("()") {
        if is_name(name) && ws.get(1)?.literal()? == "{" {
            return Some((name.to_string(), 2));
        }
    }
    if is_name(&a) && ws.get(1)?.literal()? == "()" && ws.get(2)?.literal()? == "{" {
        return Some((a, 3));
    }
    None
}

fn is_name(s: &str) -> bool {
    let mut cs = s.chars();
    cs.next()
        .is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
        && cs.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

/// Find the knives in one workflow / script text. `name` is used in `at`.
pub fn scan_text(name: &str, text: &str) -> Scan {
    let mut scan = Scan::default();
    let mut funcs: BTreeMap<String, Template> = BTreeMap::new();
    let mut step = String::new();
    let mut job = String::new();
    let mut in_jobs = false;
    for (n, line) in logical_lines(text) {
        // a workflow's job id: a two-space key under the top-level `jobs:` (S66-0b:
        // job id, display name and step name are three different things)
        if !line.starts_with(' ') && !line.trim().is_empty() && !line.starts_with('#') {
            in_jobs = line.trim_end() == "jobs:";
            job.clear();
        } else if in_jobs {
            if let Some(id) = job_key(&line) {
                job = id;
                step.clear();
                funcs.clear();
            }
        }
        if starts_step(&line) {
            funcs.clear();
            step = step_name(&line).unwrap_or_default();
        }
        let at = format!("{name}:{n}");
        let ws = match words(&line) {
            Ok(ws) => ws,
            Err(e) => {
                let first = line.split_whitespace().next().unwrap_or("");
                let code = !line.trim_start().starts_with('#');
                if code && (line.contains("knife.py") || funcs.contains_key(first)) {
                    scan.unresolved.push(format!("{at}: {e}"));
                }
                continue;
            }
        };
        // outside comments only (a comment is gone from `ws`)
        let mentions = ws.iter().any(|w| {
            w.parts
                .iter()
                .any(|p| matches!(p, Part::Lit(t) if t.contains("knife.py")))
        });
        if let Some((fname, body)) = func_def(&ws) {
            match ws[body..].iter().position(Word::is_knife) {
                Some(k) => match after_knife(&ws, body + k) {
                    Ok(t) => {
                        funcs.insert(fname, t);
                    }
                    Err(e) => scan.unresolved.push(format!("{at}: function {fname}: {e}")),
                },
                None if mentions => scan.unresolved.push(format!(
                    "{at}: function {fname} mentions knife.py in a form not read"
                )),
                None => {
                    funcs.remove(&fname);
                }
            }
            continue;
        }
        // each command of the line: a direct knife, or a call of a known function
        let mut handled = false;
        for cmd in ws.split(|w| w.op) {
            let Some(first) = cmd.first() else { continue };
            if let Some(k) = cmd.iter().position(Word::is_knife) {
                handled = true;
                let found = after_knife(cmd, k).and_then(|t| {
                    Ok(Knife {
                        at: at.clone(),
                        file: resolve(&t.file, &[])?,
                        old: resolve(&t.old, &[])?,
                        job: job.clone(),
                        step: step.clone(),
                        argv: resolve_all(&t.rest, &[]),
                    })
                });
                match found {
                    Ok(kn) => scan.knives.push(kn),
                    Err(e) => scan.unresolved.push(format!("{at}: {e}")),
                }
                continue;
            }
            let Some(fname) = first.literal() else {
                continue;
            };
            if let Some(t) = funcs.get(&fname) {
                handled = true;
                let args = &cmd[1..];
                let found = resolve(&t.file, args).and_then(|file| {
                    Ok(Knife {
                        at: at.clone(),
                        file,
                        old: resolve(&t.old, args)?,
                        job: job.clone(),
                        step: step.clone(),
                        argv: resolve_all(&t.rest, args),
                    })
                });
                match found {
                    Ok(kn) => scan.knives.push(kn),
                    Err(e) => scan.unresolved.push(format!("{at}: call of {fname}: {e}")),
                }
            }
        }
        if mentions && !handled {
            scan.unresolved
                .push(format!("{at}: mentions knife.py in a form not read"));
        }
    }
    scan
}

/// Count of `old` in `text`, non-overlapping (Python `str.count`). An empty
/// anchor is never "exactly once".
pub fn count(text: &str, old: &str) -> usize {
    if old.is_empty() {
        return 0;
    }
    text.matches(old).count()
}

/// CRLF read as LF, as knife.py's text mode reads it.
fn lf(t: &str) -> String {
    t.replace("\r\n", "\n")
}

fn files_in(dir: &Path, ext: &str, out: &mut Vec<PathBuf>) {
    if let Ok(rd) = std::fs::read_dir(dir) {
        let mut v: Vec<PathBuf> = rd
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| p.extension().is_some_and(|x| x == ext))
            .collect();
        v.sort();
        out.extend(v);
    }
}

/// Which workflow step runs each knife script (S66-shared piece 1, SPEC-shadow-u0 §1.2.1):
/// a step whose lines name `crates/<c>/knives/<f>.sh` / `scripts/<f>.sh`, or the loop
/// `crates/<c>/knives/*.sh`, owns every knife of that script. `workflows` = (path, text),
/// `scripts` = repo paths of the knife scripts. A step calling
/// `crates/aib-knife/run-knives.sh <job>` (S66-loop) owns every crate knife script whose
/// header job ([`knives_job`]) is `<job>`; `loop_jobs` = script ⇒ that job. Returns
/// script ⇒ (job, step), and one line for each script a second step also names (the first
/// step keeps it).
pub fn script_owners(
    workflows: &[(String, String)],
    scripts: &[String],
    loop_jobs: &BTreeMap<String, String>,
) -> (BTreeMap<String, (String, String)>, Vec<String>) {
    let mut own: BTreeMap<String, (String, String)> = BTreeMap::new();
    let mut twice = Vec::new();
    for (name, text) in workflows {
        let (mut job, mut step, mut in_jobs) = (String::new(), String::new(), false);
        for (n, line) in text.lines().enumerate() {
            if !line.starts_with(' ') && !line.trim().is_empty() && !line.starts_with('#') {
                in_jobs = line.trim_end() == "jobs:";
                job.clear();
            } else if in_jobs {
                if let Some(id) = job_key(line) {
                    job = id;
                    step.clear();
                }
            }
            if starts_step(line) {
                step = step_name(line).unwrap_or_default();
            }
            if step.is_empty() || line.trim_start().starts_with('#') {
                continue;
            }
            let seps = |c: char| c.is_whitespace() || matches!(c, ';' | '"' | '\'' | '(' | ')');
            let toks: Vec<&str> = line.split(seps).filter(|t| !t.is_empty()).collect();
            for (i, tok) in toks.iter().enumerate() {
                let tok = tok.strip_prefix("./").unwrap_or(tok);
                let hits: Vec<&String> = match tok.strip_suffix("*.sh") {
                    _ if tok == RUN_KNIVES => match toks.get(i + 1) {
                        Some(want) => scripts
                            .iter()
                            .filter(|s| loop_jobs.get(*s).is_some_and(|j| j.as_str() == *want))
                            .collect(),
                        None => Vec::new(),
                    },
                    Some(dir) if dir.ends_with("/knives/") => scripts
                        .iter()
                        .filter(|s| s.strip_prefix(dir).is_some_and(|f| !f.contains('/')))
                        .collect(),
                    _ => scripts.iter().filter(|s| s.as_str() == tok).collect(),
                };
                for s in hits {
                    match own.get(s) {
                        None => {
                            own.insert(s.clone(), (job.clone(), step.clone()));
                        }
                        Some((j, st)) if *j == job && *st == step => {}
                        Some((j, st)) => twice.push(format!(
                            "{name}:{}: {s} is also run by {job} / {step} (kept {j} / {st})",
                            n + 1
                        )),
                    }
                }
            }
        }
    }
    (own, twice)
}

/// The one knife step's runner (S66-loop): `bash crates/aib-knife/run-knives.sh <job>`.
pub const RUN_KNIVES: &str = "crates/aib-knife/run-knives.sh";

/// The job a crate knife script belongs to, from its header (first 10 lines,
/// `# aib-knives: job=<job>`; not written = `rust`), read like run-knives.sh reads it: a
/// value it would refuse is an error here too, so the two never disagree.
pub fn knives_job(text: &str) -> Result<String, String> {
    let mut job = "rust".to_string();
    for line in text.lines().take(10) {
        let Some(kv) = line.strip_prefix("# aib-knives: ") else {
            continue;
        };
        let kv = kv.split_whitespace().next().unwrap_or("");
        let (k, v) = kv.split_once('=').unwrap_or((kv, ""));
        let ok = match k {
            "os" => matches!(v, "macos" | "windows" | "unix" | "any"),
            "job" => {
                let good = v.starts_with(|c: char| c.is_ascii_lowercase())
                    && v.chars().all(|c| {
                        c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '-'
                    });
                if good {
                    job = v.to_string();
                }
                good
            }
            "env" => v.split_once('=').is_some_and(|(n, val)| {
                n.starts_with(|c: char| c.is_ascii_uppercase() || c == '_')
                    && n.chars()
                        .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == '_')
                    && val.chars().all(|c| {
                        c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '/' | ':' | '-')
                    })
            }),
            "timeout" => v
                .strip_suffix('m')
                .filter(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
                .and_then(|n| n.parse::<u32>().ok())
                .is_some_and(|n| (1..=240).contains(&n)),
            _ => false,
        };
        if !ok {
            return Err(format!("header `{kv}` not known"));
        }
    }
    Ok(job)
}

/// Run the check under `root`; prints one line per knife and a summary.
/// rc 0 = every anchor exactly once (and at least one knife), 1 = some
/// anchor not exactly once, 2 = something could not be read.
pub fn run(root: &Path) -> u8 {
    let (files, knives, unresolved) = collect(root);
    check(&files, &knives, &unresolved, root)
}

/// Every workflow / script file read, the knives found and what could not be read.
pub fn collect(root: &Path) -> (Vec<PathBuf>, Vec<Knife>, Vec<String>) {
    let mut files = Vec::new();
    files_in(&root.join(".github/workflows"), "yml", &mut files);
    files_in(&root.join(".github/workflows"), "yaml", &mut files);
    files_in(&root.join("scripts"), "sh", &mut files);
    // a crate's own knife scripts (519d0 11-15 08:11: the two cloud knife steps moved out of
    // aibd.yml, which went past GitHub's 500 KB; crates/** already runs the cells)
    if let Ok(rd) = std::fs::read_dir(root.join("crates")) {
        let mut crates: Vec<PathBuf> = rd.filter_map(|e| e.ok().map(|e| e.path())).collect();
        crates.sort();
        for c in crates {
            files_in(&c.join("knives"), "sh", &mut files);
        }
    }
    let mut knives = Vec::new();
    let mut unresolved = Vec::new();
    let mut workflows = Vec::new();
    let mut scripts = Vec::new();
    let mut loop_jobs = BTreeMap::new();
    for f in &files {
        // `/` on every platform, so the two cells print the same lines
        let rel = f
            .strip_prefix(root)
            .unwrap_or(f)
            .display()
            .to_string()
            .replace('\\', "/");
        match std::fs::read_to_string(f) {
            Ok(t) => {
                let t = lf(&t);
                let s = scan_text(&rel, &t);
                knives.extend(s.knives);
                unresolved.extend(s.unresolved);
                if rel.starts_with(".github/workflows/") {
                    workflows.push((rel, t));
                } else {
                    if rel.starts_with("crates/") {
                        match knives_job(&t) {
                            Ok(j) => {
                                loop_jobs.insert(rel.clone(), j);
                            }
                            Err(e) => unresolved.push(format!("{rel}: {e}")),
                        }
                    }
                    scripts.push(rel);
                }
            }
            Err(e) => unresolved.push(format!("{rel}: cannot read: {e}")),
        }
    }
    // a script's knives belong to the step that runs the script (S66-shared piece 1)
    // a script a second step also runs would have its knives' results kept under the first
    // step: unresolved, so knife-anchors is red (1ced7 kspec M1)
    let (own, twice) = script_owners(&workflows, &scripts, &loop_jobs);
    unresolved.extend(twice);
    for k in knives.iter_mut().filter(|k| k.step.is_empty()) {
        let file = k.at.rsplit_once(':').map_or(k.at.as_str(), |(f, _)| f);
        if let Some((job, step)) = own.get(file) {
            k.job.clone_from(job);
            k.step.clone_from(step);
        }
    }
    (files, knives, unresolved)
}

fn check(files: &[PathBuf], knives: &[Knife], unresolved: &[String], root: &Path) -> u8 {
    let mut bad = 0;
    let mut cache: BTreeMap<String, Result<String, String>> = BTreeMap::new();
    for k in knives {
        let text = cache.entry(k.file.clone()).or_insert_with(|| {
            std::fs::read_to_string(root.join(&k.file))
                .map(|t| lf(&t))
                .map_err(|e| e.to_string())
        });
        let shown: String = k.old.trim().chars().take(70).collect();
        match text {
            Ok(t) => {
                let n = count(t, &k.old);
                if n == 1 {
                    println!("ok    {}  {}  {shown:?}", k.at, k.file);
                } else {
                    bad += 1;
                    println!("FAIL  {}  {}  found {n} times: {shown:?}", k.at, k.file);
                }
            }
            Err(e) => {
                bad += 1;
                println!("FAIL  {}  {}  cannot read target: {e}", k.at, k.file);
            }
        }
    }
    for u in unresolved {
        println!("UNRESOLVED  {u}");
    }
    println!(
        "knife-anchors: {} knives in {} files, {} not exactly once, {} unresolved",
        knives.len(),
        files.len(),
        bad,
        unresolved.len()
    );
    if !unresolved.is_empty() || knives.is_empty() {
        if knives.is_empty() {
            println!("knife-anchors: no knife found; the scan is broken, not clean");
        }
        2
    } else if bad > 0 {
        1
    } else {
        0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn one(text: &str) -> Scan {
        scan_text("w.yml", text)
    }

    #[test]
    fn a_script_belongs_to_the_step_that_runs_it() {
        let wf = "on: push\njobs:\n  rust:\n    steps:\n      - name: one\n        run: umask 022; ./scripts/knives-web.sh\n      - name: loop\n        run: for f in crates/aib-drive/knives/*.sh; do bash \"$f\"; done\n      # crates/aib-x/knives/knives-01-a.sh (a comment names nothing)\n      - name: other\n        run: echo crates/aib-drive/knives/sub/x.sh\n  win:\n    steps:\n      - name: again\n        run: ./scripts/knives-web.sh\n";
        let scripts: Vec<String> = [
            "scripts/knives-web.sh",
            "crates/aib-drive/knives/knives-01-decide.sh",
            "crates/aib-drive/knives/knives-12-unblock.sh",
            "crates/aib-x/knives/knives-01-a.sh",
            "scripts/knives-presence.sh",
        ]
        .map(String::from)
        .to_vec();
        let (own, twice) =
            script_owners(&[("w.yml".into(), wf.into())], &scripts, &BTreeMap::new());
        let at = |s: &str| own.get(s).map(|(j, st)| format!("{j}/{st}"));
        assert_eq!(at("scripts/knives-web.sh").as_deref(), Some("rust/one"));
        assert_eq!(
            at("crates/aib-drive/knives/knives-01-decide.sh").as_deref(),
            Some("rust/loop")
        );
        assert_eq!(
            at("crates/aib-drive/knives/knives-12-unblock.sh").as_deref(),
            Some("rust/loop")
        );
        // a comment and a script nobody runs have no step
        assert_eq!(at("crates/aib-x/knives/knives-01-a.sh"), None);
        assert_eq!(at("scripts/knives-presence.sh"), None);
        assert_eq!(twice.len(), 1, "{twice:?}");
        assert!(twice[0].contains("win / again"), "{twice:?}");
    }

    #[test]
    fn a_direct_call_reads_file_and_old() {
        let s = one("          python3 -I scripts/knife.py crates/a/src/x.rs \\\n            '    let a = 1;' '    let a = 2;' \\\n            --expect-fail t -- cargo test\n");
        assert!(s.unresolved.is_empty(), "{:?}", s.unresolved);
        assert_eq!(
            s.knives,
            vec![Knife {
                at: "w.yml:1".into(),
                file: "crates/a/src/x.rs".into(),
                old: "    let a = 1;".into(),
                job: String::new(),
                step: String::new(),
                argv: Ok(vec![
                    "crates/a/src/x.rs".into(),
                    "    let a = 1;".into(),
                    "    let a = 2;".into(),
                    "--expect-fail".into(),
                    "t".into(),
                    "--".into(),
                    "cargo".into(),
                    "test".into(),
                ]),
            }]
        );
    }

    #[test]
    fn a_function_call_fills_its_positionals() {
        let s = one(concat!(
            "      - name: knives\n",
            "        run: |\n",
            "          k() { python3 -I scripts/knife.py crates/b.rs \"$1\" \"$2\" \\\n",
            "                  --expect-fail t -- cargo test; }\n",
            "          k '        self.job.terminate(1)' '        Ok(()) // knife'\n",
            "          k \"say \\\"hi\\\" \\$x\" 'new'\n",
        ));
        assert!(s.unresolved.is_empty(), "{:?}", s.unresolved);
        let olds: Vec<&str> = s.knives.iter().map(|k| k.old.as_str()).collect();
        assert_eq!(olds, vec!["        self.job.terminate(1)", "say \"hi\" $x"]);
        assert!(s.knives.iter().all(|k| k.file == "crates/b.rs"));
        assert_eq!(s.knives[0].at, "w.yml:5");
    }

    /// a63ab #590: #623's `ck` builds FILE from a prefix and `$1`.
    #[test]
    fn a_positional_inside_a_word_is_filled_in() {
        let s = one(concat!(
            "      - name: knives\n",
            "        run: |\n",
            "          ck() { python3 -I scripts/knife.py \"crates/c/src/$1\" \"$2\" \"$3\" \\\n",
            "                   --expect-fail \"$4\" -- cargo test \"$4\"; }\n",
            "          ck site.rs '    old' '    new' t\n",
            "          ck \"a$X.rs\" 'o' 'n' t\n",
        ));
        assert_eq!(s.knives.len(), 1, "{s:?}");
        assert_eq!(s.knives[0].file, "crates/c/src/site.rs");
        assert_eq!(s.knives[0].old, "    old");
        // an argument that is not plain text stays unresolved, never guessed
        assert_eq!(s.unresolved.len(), 1, "{:?}", s.unresolved);
        assert!(
            s.unresolved[0].contains("argument $1 is not plain text"),
            "{:?}",
            s.unresolved
        );
    }

    #[test]
    fn a_new_step_forgets_the_functions_of_the_last_one() {
        let s = one(concat!(
            "      - name: a\n",
            "        run: |\n",
            "          k() { python3 -I scripts/knife.py a.rs \"$1\" \"$2\"; }\n",
            "      - name: b\n",
            "        run: |\n",
            "          k() { python3 -I scripts/knife.py b.rs \"$1\" \"$2\"; }\n",
            "          k 'x' 'y'\n",
        ));
        assert_eq!(s.knives.len(), 1);
        assert_eq!(s.knives[0].file, "b.rs");
    }

    #[test]
    fn what_cannot_be_read_is_unresolved_not_skipped() {
        for text in [
            "python3 -I scripts/knife.py \"$F\" 'old' 'new'\n",
            "python3 -I scripts/knife.py a.rs $'old\\q' 'new'\n",
            "python3 -I scripts/knife.py a.rs 'old\n",
            "python3 -I scripts/knife.py a.rs\n",
            "k() { python3 -I scripts/knife.py a.rs \"$1\" \"$2\"; }\nk \"$OLD\" new\n",
            "k() { python3 -I scripts/knife.py a.rs \"$2\" \"$3\"; }\nk only-one\n",
            "python3 -I knife.py a.rs 'old' 'new'\n",
            "python3 -I scripts/knife.py --expect-fail t a.rs 'old' 'new'\n",
            "python3 -I scripts/knife.py a.rs '-old' 'new'\n",
            "python3 -I scripts/knife.py a.rs $'old 'new'\n",
        ] {
            let s = one(text);
            assert!(s.knives.is_empty(), "{text:?} gave {:?}", s.knives);
            assert_eq!(s.unresolved.len(), 1, "{text:?} gave {:?}", s.unresolved);
        }
    }

    #[test]
    fn comments_and_other_lines_are_not_knives() {
        let s = one(concat!(
            "        # No -q: this knife.py reads \"test x ... FAILED\" lines.\n",
            "          k '  x' 'y'  # a knife.py call in a comment after code\n",
            "        name: don't trip on a YAML apostrophe\n",
            "        run: echo k 'x'\n",
        ));
        assert!(s.knives.is_empty() && s.unresolved.is_empty(), "{s:?}");
    }

    #[test]
    fn exactly_once_is_counted_like_python() {
        assert_eq!(count("aXa aXa", "aXa"), 2);
        assert_eq!(count("aaa", "aa"), 1); // non-overlapping, as str.count
        assert_eq!(count("abc", "zz"), 0);
        assert_eq!(count("abc", ""), 0);
    }

    #[test]
    fn a_doubled_backslash_at_line_end_does_not_join() {
        let ls = logical_lines("a \\\\\nb \\\nc\nd\\\ne\n");
        assert_eq!(
            ls,
            vec![
                (1, "a \\\\".to_string()),
                (2, "b c".to_string()),
                (4, "de".to_string()) // removed, no space added (bash)
            ]
        );
    }

    /// #580's K5 as it is in main's aibd.yml (1ced7 #590 H1): ANSI-C
    /// multi-line anchors through a function.
    #[test]
    fn ansi_c_quoted_anchors_are_read_like_bash() {
        let s = one(concat!(
            "      - name: procwatch knives\n",
            "        run: |\n",
            "          fc() { python3 -I scripts/knife.py \"$1\" \"$2\" \"$3\" --expect-fail \"$4\" \\\n",
            "                   -- cargo test; }\n",
            "          fc crates/aib-forkcount/src/main.rs \\\n",
            "            $'        if self.adopted_not_created > 0 {\\n            r.push(\"adopted_not_created\");' \\\n",
            "            $'        if false {\\n            r.push(\"adopted_not_created\"); // knife' \\\n",
            "            calibrate_one_missed_and_one_foreign_is_unverified\n",
            "          python3 -I scripts/knife.py a.rs $'tab\\there \\'q\\' \\\\ \\x41\\x7e' 'new'\n",
        ));
        assert!(s.unresolved.is_empty(), "{:?}", s.unresolved);
        assert_eq!(s.knives.len(), 2, "{:?}", s.knives);
        assert_eq!(s.knives[0].file, "crates/aib-forkcount/src/main.rs");
        assert_eq!(
            s.knives[0].old,
            "        if self.adopted_not_created > 0 {\n            r.push(\"adopted_not_created\");"
        );
        assert_eq!(s.knives[0].at, "w.yml:5");
        assert_eq!(s.knives[1].old, "tab\there 'q' \\ A~");
    }

    #[test]
    fn crlf_is_read_as_lf_in_workflow_and_target() {
        let wf = "      - name: k\r\n        run: |\r\n          python3 -I scripts/knife.py src/a.rs $'    let x = 1;\\n    let y = 2;' 'z' -- true\r\n";
        let r = root_with(
            "crlf",
            wf,
            "fn f() {\r\n    let x = 1;\r\n    let y = 2;\r\n}\r\n",
        );
        let rc = run(&r);
        let _ = std::fs::remove_dir_all(&r);
        assert_eq!(rc, 0);
    }

    /// A throwaway root with one workflow and one target file.
    fn root_with(tag: &str, workflow: &str, target: &str) -> PathBuf {
        let r = std::env::temp_dir().join(format!("knife-anchors-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&r);
        std::fs::create_dir_all(r.join(".github/workflows")).unwrap();
        std::fs::create_dir_all(r.join("src")).unwrap();
        std::fs::write(r.join(".github/workflows/w.yml"), workflow).unwrap();
        std::fs::write(r.join("src/a.rs"), target).unwrap();
        r
    }

    #[test]
    fn run_is_red_when_an_anchor_moved_and_green_when_it_is_there() {
        let wf = "      - name: k\n        run: |\n          python3 -I scripts/knife.py src/a.rs '    let x = 1;' 'y' -- true\n";
        let ok = root_with("ok", wf, "fn f() {\n    let x = 1;\n}\n");
        let moved = root_with("moved", wf, "fn f() {\n    let x = 2;\n}\n");
        let twice = root_with("twice", wf, "    let x = 1;\n    let x = 1;\n");
        let none = root_with("none", "      - name: k\n        run: echo\n", "");
        let rcs = [run(&ok), run(&moved), run(&twice), run(&none)];
        for r in [&ok, &moved, &twice, &none] {
            let _ = std::fs::remove_dir_all(r);
        }
        assert_eq!(rcs, [0, 1, 1, 2]);
    }

    /// A knife in `crates/<crate>/knives/*.sh` is read and checked like one in scripts/.
    #[test]
    fn a_crates_own_knife_script_is_read() {
        let call = "python3 -I scripts/knife.py src/a.rs '    let x = 1;' 'y' -- true\n";
        let at = |tag: &str, target: &str| {
            let r = root_with(tag, "      - name: k\n        run: echo\n", target);
            std::fs::create_dir_all(r.join("crates/c/knives")).unwrap();
            std::fs::write(r.join("crates/c/knives/k.sh"), call).unwrap();
            r
        };
        let ok = at("crate-ok", "    let x = 1;\n");
        let moved = at("crate-moved", "    let x = 2;\n");
        let found = collect(&ok).1.len();
        let rcs = [run(&ok), run(&moved)];
        for r in [&ok, &moved] {
            let _ = std::fs::remove_dir_all(r);
        }
        assert_eq!((found, rcs), (1, [0, 1]));
    }

    #[test]
    fn a_knife_scripts_header_names_its_job_like_run_knives_reads_it() {
        assert_eq!(knives_job("#!/usr/bin/env bash\necho\n"), Ok("rust".into()));
        let h = "#!/usr/bin/env bash\n# aib-knives: os=any   # why\n# aib-knives: job=cloud-knives\n# aib-knives: timeout=240m\n# aib-knives: env=AIB_KNIVES_NO_SE=1\n# aib-knives: env=X_2=a.b/c:d-e\n";
        assert_eq!(knives_job(h), Ok("cloud-knives".into()));
        for bad in [
            "os=linux",
            "job=Cloud",
            "timeout=241m",
            "timeout=+30m",
            "timeout=30",
            "retries=2",
            "env=lower=1",
            "env=NO_VALUE",
            "env=A=$(x)",
        ] {
            let t = format!("#!/usr/bin/env bash\n# aib-knives: {bad}\n");
            assert!(knives_job(&t).is_err(), "{bad}");
        }
        // only the first 10 lines are the header, as run-knives.sh reads them
        let late = format!("{}# aib-knives: job=x\n", "#\n".repeat(10));
        assert_eq!(knives_job(&late), Ok("rust".into()));
    }

    #[test]
    fn the_run_knives_step_owns_the_scripts_of_its_job() {
        let wf = "jobs:\n  rust:\n    steps:\n      - name: crate knives\n        run: bash crates/aib-knife/run-knives.sh rust\n  cloud-knives:\n    steps:\n      - name: cloud\n        run: bash crates/aib-cloud/knives/k.sh\n";
        let scripts: Vec<String> = [
            "crates/a/knives/k1.sh",
            "crates/b/knives/k2.sh",
            "crates/aib-cloud/knives/k.sh",
        ]
        .map(String::from)
        .to_vec();
        let jobs: BTreeMap<String, String> = [
            ("crates/a/knives/k1.sh", "rust"),
            ("crates/b/knives/k2.sh", "rust"),
            ("crates/aib-cloud/knives/k.sh", "cloud-knives"),
        ]
        .into_iter()
        .map(|(a, b)| (a.to_string(), b.to_string()))
        .collect();
        let (own, twice) = script_owners(&[("w.yml".into(), wf.into())], &scripts, &jobs);
        let at = |s: &str| own.get(s).map(|(j, st)| format!("{j}/{st}"));
        assert_eq!(
            at("crates/a/knives/k1.sh").as_deref(),
            Some("rust/crate knives")
        );
        assert_eq!(
            at("crates/b/knives/k2.sh").as_deref(),
            Some("rust/crate knives")
        );
        assert_eq!(
            at("crates/aib-cloud/knives/k.sh").as_deref(),
            Some("cloud-knives/cloud")
        );
        assert!(twice.is_empty(), "{twice:?}");
        // a cloud script left at the default job is run by both steps: reported
        let mut both = jobs.clone();
        both.insert("crates/aib-cloud/knives/k.sh".into(), "rust".into());
        let (_, twice) = script_owners(&[("w.yml".into(), wf.into())], &scripts, &both);
        assert_eq!(twice.len(), 1, "{twice:?}");
    }

    #[test]
    fn a_script_two_steps_run_is_unresolved() {
        let call = "python3 -I scripts/knife.py src/a.rs '    let x = 1;' 'y' -- true\n";
        let wf = "jobs:\n  mac:\n    steps:\n      - name: one\n        run: bash crates/c/knives/k.sh\n  win:\n    steps:\n      - name: two\n        run: bash crates/c/knives/k.sh\n";
        let r = root_with("two-steps", wf, "    let x = 1;\n");
        std::fs::create_dir_all(r.join("crates/c/knives")).unwrap();
        std::fs::write(r.join("crates/c/knives/k.sh"), call).unwrap();
        let (_, knives, unresolved) = collect(&r);
        let rc = run(&r);
        let _ = std::fs::remove_dir_all(&r);
        assert_eq!(knives.len(), 1);
        assert_eq!(unresolved.len(), 1, "{unresolved:?}");
        assert!(
            unresolved[0].contains("also run by win / two"),
            "{unresolved:?}"
        );
        assert_eq!(rc, 2, "unresolved is rc 2, like a line it cannot read");
    }

    /// S66-1: `knife run` needs the whole call, filled in, and the step it sits in.
    #[test]
    fn a_function_call_gives_the_whole_argv_and_its_step() {
        let s = one(concat!(
            "      - name: \"my knives\"\n",
            "        run: |\n",
            "          ck() { python3 -I scripts/knife.py \"crates/c/src/$1\" \"$2\" \"$3\" --expect-fail \"$4\" \\\n",
            "                   -- cargo test -p c --locked \"$4\"; }\n",
            "          ck a.rs 'old' 'new' some_test\n",
            "          ck a.rs 'old2' 'new2'\n",
        ));
        assert!(s.unresolved.is_empty(), "{:?}", s.unresolved);
        assert_eq!(s.knives.len(), 2);
        let k = &s.knives[0];
        assert_eq!(k.step, "my knives");
        assert_eq!(
            k.argv.as_deref().unwrap(),
            [
                "crates/c/src/a.rs",
                "old",
                "new",
                "--expect-fail",
                "some_test",
                "--",
                "cargo",
                "test",
                "-p",
                "c",
                "--locked",
                "some_test"
            ]
        );
        // a missing argument: the anchor is still checked, the run refuses it
        assert_eq!(s.knives[1].old, "old2");
        assert!(s.knives[1].argv.is_err(), "{:?}", s.knives[1].argv);
    }

    /// S66-0b: the job id (key under `jobs:`) is kept apart from the step's display name,
    /// and a new job starts with no step and no functions.
    #[test]
    fn a_knife_knows_its_job_id_and_step_name() {
        let s = one(concat!(
            "on:\n",
            "  push:\n",
            "jobs:\n",
            "  cloud-knives:\n",
            "    name: Cloud knives (display)\n",
            "    steps:\n",
            "      - name: \"pk knives\"\n",
            "        run: |\n",
            "          pk() { python3 -I scripts/knife.py \"crates/c/src/$1\" \"$2\" \"$3\" --expect-fail \"$4\" -- cargo test -p c \"$4\"; }\n",
            "          pk a.rs 'old' 'new' t1\n",
            "  rust:\n",
            "    steps:\n",
            "      - run: pk a.rs 'old' 'new' t2\n",
            "      - name: direct\n",
            "        run: python3 -I scripts/knife.py crates/r/src/b.rs 'o' 'n' --expect-fail t3 -- cargo test\n",
        ));
        assert!(s.unresolved.is_empty(), "{:?}", s.unresolved);
        assert_eq!(
            s.knives.len(),
            2,
            "pk is not known in the next job: {:?}",
            s.knives
        );
        assert_eq!(
            (s.knives[0].job.as_str(), s.knives[0].step.as_str()),
            ("cloud-knives", "pk knives")
        );
        assert_eq!(
            (s.knives[1].job.as_str(), s.knives[1].step.as_str()),
            ("rust", "direct")
        );
        // a script has no job
        let t = scan_text(
            "scripts/k.sh",
            "python3 -I scripts/knife.py a.rs 'o' 'n' --expect-fail t -- c\n",
        );
        assert_eq!(t.knives[0].job, "");
    }
}
