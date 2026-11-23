//! `aib-xtask knife-names [--list] [ROOT]`: a knife's name (the label people re-anchor it by,
//! like `RG11` or `mf-tail`) is used once (ebab8 11-23 08:00: page-chain knife names took
//! numbers Rust knives already had, RG11 / RG13 / TH3, and a re-anchor by name changed the
//! wrong file and nearly turned a required check red).
//!
//! - **Files**: `xtask/knives/*.sh` and `crates/*/knives/*.sh`, sorted, read as text with
//!   CRLF taken as LF (the same on macOS and Windows).
//! - **A knife's name**: the comment block right above a call line. A call line is any line
//!   that is not blank, not a comment and does not start with `set `, `umask`, `cd `,
//!   `export `, `NAME=` or `name() {` (a header or a helper is not a knife). In that block the
//!   first comment whose first word (a trailing `:` `,` `.` dropped) is a code, 1-6 of `A-Z`
//!   then 1-3 digits then an optional `a-z` (`RG11`, `AS4b`), or whose whole text is one
//!   kebab word (`mf-tail`), names it. A blank line ends a block.
//! - **Duplicates**: `cross NAME` = one name in more than one file; `same NAME FILE` = one
//!   name twice in one file.
//! - **Baseline** `xtask/knife-names-baseline.txt` (one duplicate a line, `#` comments): the
//!   duplicates main already had on 1ae3cecbdb (420 cross, 29 same). A duplicate not in it
//!   is `NEW` (rc 1); a line of it that is no longer a duplicate is `GONE` (rc 1: take the
//!   line out, the list only gets shorter). `--list` prints every duplicate and is rc 0.
//! - rc 2: a knife file or the baseline cannot be read.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

/// The baseline, from the repo root.
pub const BASELINE: &str = "xtask/knife-names-baseline.txt";

/// One named knife: name, file (repo path, `/`), 1-based line of its name.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Named {
    pub name: String,
    pub file: String,
    pub line: usize,
}

fn is_code(w: &str) -> bool {
    let b = w.as_bytes();
    let up = b.iter().take_while(|c| c.is_ascii_uppercase()).count();
    let dig = b[up..].iter().take_while(|c| c.is_ascii_digit()).count();
    let rest = &b[up + dig..];
    (1..=6).contains(&up)
        && (1..=3).contains(&dig)
        && (rest.is_empty() || (rest.len() == 1 && rest[0].is_ascii_lowercase()))
}

fn is_kebab(s: &str) -> bool {
    let mut parts = s.split('-');
    let first = parts.next().unwrap_or("");
    let rest: Vec<&str> = parts.collect();
    first.starts_with(|c: char| c.is_ascii_lowercase())
        && first
            .bytes()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
        && !rest.is_empty()
        && rest.iter().all(|p| {
            !p.is_empty()
                && p.bytes()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
        })
}

/// The name a comment's text gives, if any.
pub fn label(comment: &str) -> Option<String> {
    let first = comment.split([' ', '\t']).next().unwrap_or("");
    let w = first.strip_suffix([':', ',', '.']).unwrap_or(first);
    if is_code(w) {
        return Some(w.to_string());
    }
    (first == comment && is_kebab(comment)).then(|| comment.to_string())
}

fn is_call(line: &str) -> bool {
    if ["set ", "umask", "cd ", "export "]
        .iter()
        .any(|p| line.starts_with(p))
    {
        return false;
    }
    let id = line
        .bytes()
        .take_while(|c| c.is_ascii_alphanumeric() || *c == b'_')
        .count();
    let starts_ident = line.starts_with(|c: char| c.is_ascii_alphabetic() || c == '_');
    if starts_ident && line[id..].starts_with('=') {
        return false;
    }
    if starts_ident
        && line[id..].starts_with("()")
        && line[id + 2..].trim_start_matches(' ').starts_with('{')
    {
        return false;
    }
    true
}

/// The named knives of one file's text.
pub fn scan(file: &str, text: &str) -> Vec<Named> {
    let mut out = Vec::new();
    let mut block: Vec<(String, usize)> = Vec::new();
    for (i, raw) in text.split('\n').enumerate() {
        let line = raw.strip_suffix('\r').unwrap_or(raw);
        let t = line.trim_start_matches([' ', '\t']);
        if let Some(c) = t.strip_prefix('#') {
            block.push((c.trim_start_matches([' ', '\t']).to_string(), i + 1));
            continue;
        }
        if t.is_empty() {
            block.clear();
            continue;
        }
        if !block.is_empty() && is_call(line) {
            if let Some((n, at)) = block.iter().find_map(|(c, at)| label(c).map(|n| (n, *at))) {
                out.push(Named {
                    name: n,
                    file: file.to_string(),
                    line: at,
                });
            }
        }
        block.clear();
    }
    out
}

/// Every duplicate, as baseline lines (`cross NAME` / `same NAME FILE`), sorted, with where.
pub fn duplicates(all: &[Named]) -> BTreeMap<String, Vec<String>> {
    let mut by_name: BTreeMap<&str, Vec<&Named>> = BTreeMap::new();
    for n in all {
        by_name.entry(&n.name).or_default().push(n);
    }
    let mut out = BTreeMap::new();
    for (name, v) in by_name {
        let files: BTreeSet<&str> = v.iter().map(|n| n.file.as_str()).collect();
        let at = |n: &&Named| format!("{}:{}", n.file, n.line);
        if files.len() > 1 {
            out.insert(format!("cross {name}"), v.iter().map(at).collect());
        }
        for f in files {
            let here: Vec<&&Named> = v.iter().filter(|n| n.file == f).collect();
            if here.len() > 1 {
                out.insert(
                    format!("same {name} {f}"),
                    here.into_iter().map(at).collect(),
                );
            }
        }
    }
    out
}

/// The baseline's lines.
pub fn parse_baseline(text: &str) -> BTreeSet<String> {
    text.lines()
        .map(|l| l.trim_end_matches('\r').trim())
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .map(str::to_string)
        .collect()
}

/// (new duplicates, baseline lines no longer duplicated).
pub fn check(
    dups: &BTreeMap<String, Vec<String>>,
    base: &BTreeSet<String>,
) -> (Vec<String>, Vec<String>) {
    let new = dups
        .keys()
        .filter(|k| !base.contains(*k))
        .cloned()
        .collect();
    let gone = base
        .iter()
        .filter(|k| !dups.contains_key(*k))
        .cloned()
        .collect();
    (new, gone)
}

fn files(root: &Path) -> Vec<PathBuf> {
    let sh = |dir: PathBuf, out: &mut Vec<PathBuf>| {
        if let Ok(rd) = std::fs::read_dir(dir) {
            let mut v: Vec<PathBuf> = rd
                .filter_map(|e| e.ok().map(|e| e.path()))
                .filter(|p| p.extension().is_some_and(|x| x == "sh"))
                .collect();
            v.sort();
            out.extend(v);
        }
    };
    let mut out = Vec::new();
    sh(root.join("xtask/knives"), &mut out);
    if let Ok(rd) = std::fs::read_dir(root.join("crates")) {
        let mut cs: Vec<PathBuf> = rd.filter_map(|e| e.ok().map(|e| e.path())).collect();
        cs.sort();
        for c in cs {
            sh(c.join("knives"), &mut out);
        }
    }
    out
}

pub fn run(root: &Path, list: bool) -> u8 {
    let mut all = Vec::new();
    for f in files(root) {
        let rel = f
            .strip_prefix(root)
            .unwrap_or(&f)
            .display()
            .to_string()
            .replace('\\', "/");
        match std::fs::read_to_string(&f) {
            Ok(t) => all.extend(scan(&rel, &t)),
            Err(e) => {
                eprintln!("knife-names: {rel}: cannot read: {e}");
                return 2;
            }
        }
    }
    let dups = duplicates(&all);
    if list {
        for (k, at) in &dups {
            println!("{k}\t{}", at.join(" "));
        }
        println!(
            "knife-names: {} named knives, {} duplicates",
            all.len(),
            dups.len()
        );
        return 0;
    }
    let base = match std::fs::read_to_string(root.join(BASELINE)) {
        Ok(t) => parse_baseline(&t),
        Err(e) => {
            eprintln!("knife-names: {BASELINE}: cannot read: {e}");
            return 2;
        }
    };
    let (new, gone) = check(&dups, &base);
    for k in &new {
        println!("NEW {k}\t{}", dups[k].join(" "));
    }
    for k in &gone {
        println!("GONE {k}\t(no longer a duplicate: take this line out of {BASELINE})");
    }
    println!(
        "knife-names: {} named knives, {} duplicates, {} new, {} gone",
        all.len(),
        dups.len(),
        new.len(),
        gone.len()
    );
    u8::from(!new.is_empty() || !gone.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;

    const A: &str = "#!/usr/bin/env bash\n# RG11 header names a knife it does not run\nset -eu\n\
umask 022\nk() { python3 -I scripts/knife.py \"$1\" \"$2\" --expect-fail t -- cargo test; }\n\
# RG11 the first one\nk 'a' 'b'\n\n# a note\n# TH3: the second, its code on the second line\n\
k 'c' 'd'\n# mf-tail\nk 'e' 'f'\n";

    #[test]
    fn names_come_from_the_block_above_a_call_and_not_from_a_header() {
        let v = scan("crates/x/knives/a.sh", A);
        let names: Vec<(&str, usize)> = v.iter().map(|n| (n.name.as_str(), n.line)).collect();
        assert_eq!(names, vec![("RG11", 6), ("TH3", 10), ("mf-tail", 12)]);
    }

    #[test]
    fn a_crlf_file_reads_the_same() {
        let v = scan("a.sh", &A.replace('\n', "\r\n"));
        assert_eq!(v.len(), 3);
        assert_eq!(v[2].name, "mf-tail");
    }

    #[test]
    fn labels() {
        for (c, want) in [
            ("RG11 the page", Some("RG11")),
            ("AS4b: a", Some("AS4b")),
            ("GP1-GP7 range", None),
            ("S88: runners", Some("S88")),
            ("ABCDEFG1 too long", None),
            ("A1234 too many digits", None),
            ("mf-tail", Some("mf-tail")),
            ("mf-tail and words", None),
            ("tail", None),
            ("Mf-tail", None),
            ("", None),
        ] {
            assert_eq!(label(c).as_deref(), want, "{c}");
        }
    }

    #[test]
    fn helpers_and_settings_are_not_calls() {
        for l in [
            "set -eu",
            "umask 022",
            "cd \"$(git rev-parse)\"",
            "export X=1",
            "K=a.sh",
            "t_1=x",
            "k() {",
            "sv_1()  {",
        ] {
            assert!(!is_call(l), "{l}");
        }
        assert!(is_call("k 'a' 'b'"));
        assert!(is_call("python3 -I scripts/knife.py f a b"));
    }

    #[test]
    fn duplicates_across_files_and_in_one_file() {
        let n = |name: &str, file: &str, line| Named {
            name: name.into(),
            file: file.into(),
            line,
        };
        let all = vec![
            n("RG11", "a.sh", 1),
            n("RG11", "b.sh", 2),
            n("TH3", "a.sh", 3),
            n("TH3", "a.sh", 9),
            n("ok1", "a.sh", 4),
        ];
        let d = duplicates(&all);
        assert_eq!(
            d.keys().cloned().collect::<Vec<_>>(),
            vec!["cross RG11".to_string(), "same TH3 a.sh".to_string()]
        );
        assert_eq!(d["cross RG11"], vec!["a.sh:1", "b.sh:2"]);
    }

    #[test]
    fn only_new_duplicates_fail_and_a_fixed_one_must_leave_the_baseline() {
        let mut d = BTreeMap::new();
        d.insert("cross RG11".to_string(), vec![]);
        d.insert("cross TH3".to_string(), vec![]);
        let base = parse_baseline("# main 1ae3cecbdb\ncross TH3\r\ncross OLD1\n\n");
        let (new, gone) = check(&d, &base);
        assert_eq!(new, vec!["cross RG11"]);
        assert_eq!(gone, vec!["cross OLD1"]);
        let base = parse_baseline("cross RG11\ncross TH3\n");
        assert_eq!(check(&d, &base), (vec![], vec![]));
    }
}
