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
    /// `$N` (alone, quoted or not) ⇒ N.
    fn positional(&self) -> Option<usize> {
        match self.parts.as_slice() {
            [Part::Var(v)] => v.parse::<usize>().ok().filter(|n| (1..=9).contains(n)),
            _ => None,
        }
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
}

#[derive(Debug, Default)]
pub struct Scan {
    pub knives: Vec<Knife>,
    /// `file:line: why`
    pub unresolved: Vec<String>,
}

/// FILE and OLD templates of a function's knife call.
#[derive(Clone)]
struct Template {
    file: Word,
    old: Word,
}

fn resolve(w: &Word, args: &[Word]) -> Result<String, String> {
    if let Some(n) = w.positional() {
        let a = args.get(n - 1).ok_or(format!("argument ${n} missing"))?;
        return a
            .literal()
            .ok_or_else(|| format!("argument ${n} is not plain text"));
    }
    w.literal()
        .ok_or_else(|| "a variable that is not $1..$9 of the function".into())
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
    Ok(Template {
        file: file.clone(),
        old: old.clone(),
    })
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
    for (n, line) in logical_lines(text) {
        if starts_step(&line) {
            funcs.clear();
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

/// Run the check under `root`; prints one line per knife and a summary.
/// rc 0 = every anchor exactly once (and at least one knife), 1 = some
/// anchor not exactly once, 2 = something could not be read.
pub fn run(root: &Path) -> u8 {
    let mut files = Vec::new();
    files_in(&root.join(".github/workflows"), "yml", &mut files);
    files_in(&root.join(".github/workflows"), "yaml", &mut files);
    files_in(&root.join("scripts"), "sh", &mut files);
    let mut knives = Vec::new();
    let mut unresolved = Vec::new();
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
                let s = scan_text(&rel, &lf(&t));
                knives.extend(s.knives);
                unresolved.extend(s.unresolved);
            }
            Err(e) => unresolved.push(format!("{rel}: cannot read: {e}")),
        }
    }
    let mut bad = 0;
    let mut cache: BTreeMap<String, Result<String, String>> = BTreeMap::new();
    for k in &knives {
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
    for u in &unresolved {
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
    fn a_direct_call_reads_file_and_old() {
        let s = one("          python3 -I scripts/knife.py crates/a/src/x.rs \\\n            '    let a = 1;' '    let a = 2;' \\\n            --expect-fail t -- cargo test\n");
        assert!(s.unresolved.is_empty(), "{:?}", s.unresolved);
        assert_eq!(
            s.knives,
            vec![Knife {
                at: "w.yml:1".into(),
                file: "crates/a/src/x.rs".into(),
                old: "    let a = 1;".into()
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
}
