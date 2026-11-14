//! `aib-xtask knife-spec [--job ID] [--step NAME] [--source FILE] [--shard I/N] [--list] [ROOT]`:
//! the knife contract (S66-0b). Every `scripts/knife.py` call that `knife-anchors` finds in
//! the workflows and `scripts/*.sh` becomes one knife of an aib-knife spec (crates/aib-knife,
//! `spec::parse`), so the runner (S66-1) reads one checked JSON file instead of shell.
//!
//! - **Fields**: FILE OLD NEW `--expect-fail` NAME `--` CMD..., read the way knife.py's
//!   argparse reads them (`--expect-fail X` or `--expect-fail=X`, anywhere before `--`).
//!   Any other option, a missing or extra positional, no `--` or an empty command is
//!   `unresolved`, never guessed.
//! - **Name** (stable id, PLAN §9 M4): `k` + FNV-1a 64 of FILE, OLD, NEW and NAME (NUL
//!   between them), 16 hex digits; the second and later knives with the same four fields
//!   get `-2`, `-3`… in scan order, so two identical knives are never merged into one.
//!   The id does not change when a knife moves to another line, step or job.
//! - **Where**: job id (the key under `jobs:`), step name and `file:line` are printed by
//!   `--list`; the spec itself only holds what aib-knife reads.
//! - **Filters**: `--job`, `--step`, `--source` (the workflow / script path as printed in
//!   `at`, without `:line`) select knives; `--shard I/N` keeps the I-th of every N of the
//!   selected knives (0-based, in scan order). Selecting no knife is rc 2 (an empty spec
//!   is refused by aib-knife: zero knives can never be "all bit").
//! - **Fail closed**: anything `knife-anchors` could not read, or a knife whose call cannot
//!   be filled in, makes the whole run rc 2, whatever the filters; the contract is the
//!   whole list or nothing (a filter must not hide a broken knife).
//! - The printed JSON is parsed back with `aib_knife::spec::parse` before it is printed,
//!   so a spec this command writes is always one aib-knife accepts.
//!
//! Not converted (listed by `--list`, never counted as knives): build-time knives
//! (`scripts/knife-build.py`, `--expect-error`; their anchors are computed at run time
//! in `scripts/knives-errorcode.sh`) and knives that patch files their own way. aib-knife
//! has no `expect_error` yet; those keep running as they do today.
//!
//! The command runs at the repo root with no shell: a step's `env:`, `working-directory:`
//! or an earlier `cd` are not carried over (none of today's knife steps uses them; S66-1
//! checks the noop baseline before any knife, so a command that needs them fails there).

use crate::knife_anchors::{self, Knife};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::Path;

/// One knife call, its fields read the way knife.py reads them.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Call {
    pub file: String,
    pub old: String,
    pub new: String,
    pub expect_fail: String,
    pub cmd: Vec<String>,
}

/// knife.py's argv (everything after `scripts/knife.py`) ⇒ its fields.
pub fn parse_argv(argv: &[String]) -> Result<Call, String> {
    let cut = argv
        .iter()
        .position(|a| a == "--")
        .ok_or("no `--` before the command")?;
    let cmd = argv[cut + 1..].to_vec();
    if cmd.is_empty() {
        return Err("no command after `--`".into());
    }
    let mut pos = Vec::new();
    let mut expect: Option<String> = None;
    let mut i = 0;
    while i < cut {
        let a = &argv[i];
        if a == "--expect-fail" {
            let v = argv
                .get(i + 1)
                .filter(|_| i + 1 < cut)
                .ok_or("--expect-fail without a value")?;
            expect = Some(v.clone());
            i += 2;
            continue;
        }
        if let Some(v) = a.strip_prefix("--expect-fail=") {
            expect = Some(v.to_string());
            i += 1;
            continue;
        }
        if a.starts_with('-') && a != "-" {
            return Err(format!(
                "option {a:?} is not read (knife.py takes only --expect-fail)"
            ));
        }
        pos.push(a.clone());
        i += 1;
    }
    let [file, old, new] = <[String; 3]>::try_from(pos).map_err(|p| {
        format!(
            "{} positional arguments, knife.py takes FILE OLD NEW",
            p.len()
        )
    })?;
    let expect_fail = expect.ok_or("--expect-fail is missing")?;
    if expect_fail.is_empty() {
        return Err("--expect-fail is empty (it would match every red name)".into());
    }
    Ok(Call {
        file,
        old,
        new,
        expect_fail,
        cmd,
    })
}

/// FILE as a plain repo path: `.` dropped, `a/../` folded (knife.py opens it from the
/// repo root, so `crates/x/src/../../y/src/m.rs` is `crates/y/src/m.rs`; aib-knife refuses
/// any `..`). An absolute path, a backslash, or a `..` that leaves the repo is refused.
pub fn normal_path(file: &str) -> Result<String, String> {
    if file.starts_with('/') || file.contains('\\') || file.contains(':') {
        return Err(format!("FILE {file:?} is not a relative repo path"));
    }
    let mut out: Vec<&str> = Vec::new();
    for part in file.split('/') {
        match part {
            "" | "." => {}
            ".." => {
                out.pop()
                    .ok_or_else(|| format!("FILE {file:?} leaves the repo"))?;
            }
            p => out.push(p),
        }
    }
    if out.is_empty() {
        return Err(format!("FILE {file:?} names no file"));
    }
    Ok(out.join("/"))
}

/// FNV-1a 64: a stable id with no dependency (not a security hash; a collision only
/// makes two knives share a base name, and the `-2` suffix still keeps them apart).
fn fnv64(parts: &[&str]) -> u64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for (n, p) in parts.iter().enumerate() {
        if n > 0 {
            // a NUL byte between parts (xor with 0, then the multiply)
            h = h.wrapping_mul(0x0100_0000_01b3);
        }
        for b in p.as_bytes() {
            h ^= u64::from(*b);
            h = h.wrapping_mul(0x0100_0000_01b3);
        }
    }
    h
}

/// A knife of the contract: its stable name, where it was found, its fields.
#[derive(Debug, Clone)]
pub struct Entry {
    pub name: String,
    pub knife: Knife,
    pub call: Call,
}

/// Every knife's fields and stable name, in scan order; `Err` lists what could not be
/// read (scan problems and calls that cannot be filled in).
pub fn entries(knives: &[Knife]) -> (Vec<Entry>, Vec<String>) {
    let mut out = Vec::new();
    let mut bad = Vec::new();
    let mut seen: BTreeMap<String, usize> = BTreeMap::new();
    for k in knives {
        let call = match &k.argv {
            Ok(a) => parse_argv(a),
            Err(e) => Err(e.clone()),
        };
        let call = match call {
            Ok(c) if c.file != k.file || c.old != k.old => Err(format!(
                "FILE / OLD read as {:?} / {:?}, the anchor check read {:?} / {:?}",
                c.file, c.old, k.file, k.old
            )),
            Ok(mut c) => normal_path(&c.file).map(|f| {
                c.file = f;
                c
            }),
            other => other,
        };
        match call {
            Ok(call) => {
                let base = format!(
                    "k{:016x}",
                    fnv64(&[&call.file, &call.old, &call.new, &call.expect_fail])
                );
                let n = seen.entry(base.clone()).or_insert(0);
                *n += 1;
                let name = if *n == 1 { base } else { format!("{base}-{n}") };
                out.push(Entry {
                    name,
                    knife: k.clone(),
                    call,
                });
            }
            Err(e) => bad.push(format!("{}: {e}", k.at)),
        }
    }
    (out, bad)
}

/// The selection the command line asks for.
#[derive(Debug, Default, Clone)]
pub struct Select {
    pub job: Option<String>,
    pub step: Option<String>,
    pub source: Option<String>,
    /// (I, N): keep the I-th of every N, 0-based.
    pub shard: Option<(usize, usize)>,
}

impl Select {
    fn keeps(&self, e: &Entry) -> bool {
        let src = e
            .knife
            .at
            .rsplit_once(':')
            .map_or(e.knife.at.as_str(), |(s, _)| s);
        self.job.as_ref().is_none_or(|j| *j == e.knife.job)
            && self.step.as_ref().is_none_or(|s| *s == e.knife.step)
            && self.source.as_ref().is_none_or(|s| s == src)
    }

    pub fn apply<'a>(&self, all: &'a [Entry]) -> Vec<&'a Entry> {
        let picked: Vec<&Entry> = all.iter().filter(|e| self.keeps(e)).collect();
        match self.shard {
            Some((i, n)) => picked
                .into_iter()
                .enumerate()
                .filter(|(p, _)| p % n == i)
                .map(|(_, e)| e)
                .collect(),
            None => picked,
        }
    }
}

/// `I/N` with N ≥ 1 and I < N.
pub fn parse_shard(s: &str) -> Result<(usize, usize), String> {
    let (i, n) = s.split_once('/').ok_or("--shard is I/N")?;
    let i: usize = i.parse().map_err(|_| "--shard I is not a number")?;
    let n: usize = n.parse().map_err(|_| "--shard N is not a number")?;
    if n == 0 || i >= n {
        return Err(format!("--shard {i}/{n}: need N >= 1 and I < N"));
    }
    Ok((i, n))
}

/// The aib-knife spec of the selected knives (always with `noop: true`).
pub fn spec_json(sel: &[&Entry]) -> Value {
    let knives: Vec<Value> = sel
        .iter()
        .map(|e| {
            json!({
                "name": e.name,
                "file": e.call.file,
                "old": e.call.old,
                "new": e.call.new,
                "expect_fail": e.call.expect_fail,
                "cmd": e.call.cmd,
            })
        })
        .collect();
    json!({"noop": true, "knives": knives})
}

/// Workflow / script files that call `scripts/knife-build.py` outside a comment: build
/// knives, not converted (see the module note).
fn build_knife_files(root: &Path, files: &[std::path::PathBuf]) -> Vec<String> {
    let mut out = Vec::new();
    for f in files {
        let Ok(t) = std::fs::read_to_string(f) else {
            continue;
        };
        let used = t.lines().any(|l| {
            let l = l.trim_start();
            !l.starts_with('#') && l.contains("knife-build.py") && !l.contains("--self-test")
        });
        if used {
            let rel = f.strip_prefix(root).unwrap_or(f);
            out.push(rel.display().to_string().replace('\\', "/"));
        }
    }
    out
}

/// Run under `root`. rc 0 = spec (or list) printed, 2 = something not read, or nothing
/// selected.
pub fn run(root: &Path, sel: &Select, list: bool) -> u8 {
    let (files, knives, mut unresolved) = knife_anchors::collect(root);
    let (all, bad) = entries(&knives);
    unresolved.extend(bad);
    if !unresolved.is_empty() {
        for u in &unresolved {
            eprintln!("UNRESOLVED  {u}");
        }
        eprintln!(
            "knife-spec: {} knives, {} not read; the contract is the whole list or nothing",
            knives.len(),
            unresolved.len()
        );
        return 2;
    }
    let picked = sel.apply(&all);
    if picked.is_empty() {
        eprintln!("knife-spec: no knife selected (of {})", all.len());
        return 2;
    }
    if list {
        for e in &picked {
            println!(
                "{}\t{}\t{}\t{}\t{}",
                e.name, e.knife.job, e.knife.step, e.knife.at, e.call.expect_fail
            );
        }
        for f in build_knife_files(root, &files) {
            println!("# not converted (build knives, knife-build.py): {f}");
        }
        eprintln!("knife-spec: {} of {} knives", picked.len(), all.len());
        return 0;
    }
    let v = spec_json(&picked);
    let text = match serde_json::to_string_pretty(&v) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("knife-spec: {e}");
            return 2;
        }
    };
    if let Err(e) = aib_knife::spec::parse(&text) {
        eprintln!("knife-spec: aib-knife refuses the spec: {e}");
        return 2;
    }
    println!("{text}");
    eprintln!("knife-spec: {} of {} knives", picked.len(), all.len());
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(s: &[&str]) -> Vec<String> {
        s.iter().map(|x| x.to_string()).collect()
    }

    fn knife(at: &str, argv: &[&str]) -> Knife {
        Knife {
            at: at.into(),
            file: argv[0].into(),
            old: argv[1].into(),
            job: "j".into(),
            step: "s".into(),
            argv: Ok(v(argv)),
        }
    }

    #[test]
    fn knife_py_argv_is_read_like_argparse() {
        let c = parse_argv(&v(&[
            "f.rs",
            "o",
            "n",
            "--expect-fail",
            "t",
            "--",
            "cargo",
            "test",
        ]))
        .unwrap();
        assert_eq!(
            c,
            Call {
                file: "f.rs".into(),
                old: "o".into(),
                new: "n".into(),
                expect_fail: "t".into(),
                cmd: v(&["cargo", "test"])
            }
        );
        // the option may come first, or as --expect-fail=X; NEW may be empty
        let c = parse_argv(&v(&["--expect-fail=t", "f.rs", "o", "", "--", "c"])).unwrap();
        assert_eq!((c.new.as_str(), c.expect_fail.as_str()), ("", "t"));
        // the command keeps its own `--` and options
        let c = parse_argv(&v(&[
            "f",
            "o",
            "n",
            "--expect-fail",
            "t",
            "--",
            "c",
            "--",
            "-x",
        ]))
        .unwrap();
        assert_eq!(c.cmd, v(&["c", "--", "-x"]));
    }

    #[test]
    fn a_call_knife_py_would_refuse_is_unresolved() {
        for (argv, why) in [
            (&["f", "o", "n", "--expect-fail", "t"][..], "no `--`"),
            (
                &["f", "o", "n", "--expect-fail", "t", "--"][..],
                "no command",
            ),
            (
                &["f", "o", "--expect-fail", "t", "--", "c"][..],
                "positional",
            ),
            (
                &["f", "o", "n", "x", "--expect-fail", "t", "--", "c"][..],
                "positional",
            ),
            (&["f", "o", "n", "--", "c"][..], "missing"),
            (
                &["f", "o", "n", "--expect-fail", "", "--", "c"][..],
                "empty",
            ),
            (
                &["f", "o", "n", "--expect-fail", "--", "c"][..],
                "without a value",
            ),
            (
                &["f", "o", "n", "-v", "--expect-fail", "t", "--", "c"][..],
                "not read",
            ),
        ] {
            let e = parse_argv(&v(argv)).unwrap_err();
            assert!(e.contains(why), "{argv:?}: {e}");
        }
    }

    #[test]
    fn names_are_stable_and_duplicates_are_kept_apart() {
        let a = knife("w.yml:1", &["f", "o", "n", "--expect-fail", "t", "--", "c"]);
        let moved = knife(
            "w.yml:90",
            &["f", "o", "n", "--expect-fail", "t", "--", "other"],
        );
        let b = knife(
            "w.yml:2",
            &["f", "o", "n2", "--expect-fail", "t", "--", "c"],
        );
        let (es, bad) = entries(&[a.clone(), b, moved]);
        assert!(bad.is_empty(), "{bad:?}");
        assert_eq!(es.len(), 3);
        assert!(
            es[0].name.starts_with('k') && es[0].name.len() == 17,
            "{}",
            es[0].name
        );
        assert_ne!(es[0].name, es[1].name, "NEW is part of the id");
        // same four fields (the command is not one of them) ⇒ same base, -2 suffix
        assert_eq!(es[2].name, format!("{}-2", es[0].name));
        // a line move does not change the id
        let (again, _) = entries(&[knife(
            "x.sh:7",
            &["f", "o", "n", "--expect-fail", "t", "--", "c"],
        )]);
        assert_eq!(again[0].name, es[0].name);
        // field boundaries count: "ab"+"c" is not "a"+"bc"
        assert_ne!(fnv64(&["ab", "c"]), fnv64(&["a", "bc"]));
    }

    #[test]
    fn a_call_that_cannot_be_filled_in_is_unresolved() {
        let mut k = knife("w.yml:3", &["f", "o", "n", "--expect-fail", "t", "--", "c"]);
        k.argv = Err("argument $4 missing".into());
        let (es, bad) = entries(&[k]);
        assert!(es.is_empty());
        assert_eq!(bad, ["w.yml:3: argument $4 missing"]);
    }

    #[test]
    fn select_filters_then_shards_in_scan_order() {
        let mk = |n: usize, job: &str| {
            let mut k = knife(
                &format!("w.yml:{n}"),
                &["f", "o", &format!("n{n}"), "--expect-fail", "t", "--", "c"],
            );
            k.job = job.into();
            k
        };
        let (es, _) = entries(&[mk(1, "a"), mk(2, "b"), mk(3, "a"), mk(4, "a"), mk(5, "a")]);
        let only_a = Select {
            job: Some("a".into()),
            ..Select::default()
        };
        assert_eq!(only_a.apply(&es).len(), 4);
        let mut shards = Vec::new();
        for i in 0..3 {
            let s = Select {
                shard: Some((i, 3)),
                ..only_a.clone()
            };
            shards.extend(s.apply(&es).iter().map(|e| e.knife.at.clone()));
        }
        shards.sort();
        // every selected knife exactly once over the shards
        assert_eq!(shards, ["w.yml:1", "w.yml:3", "w.yml:4", "w.yml:5"]);
        let src = Select {
            source: Some("w.yml".into()),
            ..Select::default()
        };
        assert_eq!(src.apply(&es).len(), 5);
    }

    #[test]
    fn file_is_folded_to_a_plain_repo_path() {
        assert_eq!(
            normal_path("crates/aib-cloud-host/src/../../aib-cloud/src/mask.rs"),
            Ok("crates/aib-cloud/src/mask.rs".into())
        );
        assert_eq!(normal_path("./a//b.rs"), Ok("a/b.rs".into()));
        for bad in [
            "../x.rs",
            "a/../../x.rs",
            "/etc/x",
            "a\\b.rs",
            "C:/x.rs",
            ".",
            "",
        ] {
            assert!(normal_path(bad).is_err(), "{bad}");
        }
        // the folded path is what the spec holds (and the id is taken from it)
        let (es, bad) = entries(&[knife(
            "w.yml:1",
            &["a/b/../c.rs", "o", "n", "--expect-fail", "t", "--", "c"],
        )]);
        assert!(bad.is_empty(), "{bad:?}");
        assert_eq!(es[0].call.file, "a/c.rs");
    }

    #[test]
    fn shard_must_be_i_of_n() {
        assert_eq!(parse_shard("1/4"), Ok((1, 4)));
        for bad in ["4/4", "0/0", "1", "a/2", "1/b"] {
            assert!(parse_shard(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn the_spec_is_one_aib_knife_accepts() {
        let (es, _) = entries(&[knife(
            "w.yml:1",
            &[
                "crates/a/src/x.rs",
                "o",
                "",
                "--expect-fail",
                "t",
                "--",
                "cargo",
                "test",
            ],
        )]);
        let refs: Vec<&Entry> = es.iter().collect();
        let text = serde_json::to_string(&spec_json(&refs)).unwrap();
        let s = aib_knife::spec::parse(&text).unwrap();
        assert_eq!(s.knives.len(), 1);
        assert_eq!(s.knives[0].name, es[0].name);
        assert_eq!(s.knives[0].cmd, v(&["cargo", "test"]));
        assert!(s.noop);
    }

    /// The repo's own list: every knife of every workflow and script is read in full,
    /// names are unique, and the whole list is one spec aib-knife accepts.
    #[test]
    fn the_repo_knives_are_all_in_the_contract() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
        let (_, knives, unresolved) = knife_anchors::collect(&root);
        assert!(unresolved.is_empty(), "{unresolved:?}");
        let (es, bad) = entries(&knives);
        assert!(bad.is_empty(), "{bad:?}");
        assert!(
            es.len() > 500,
            "only {} knives: the scan is broken",
            es.len()
        );
        let mut names: Vec<&str> = es.iter().map(|e| e.name.as_str()).collect();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), es.len());
        let refs: Vec<&Entry> = es.iter().collect();
        let text = serde_json::to_string(&spec_json(&refs)).unwrap();
        aib_knife::spec::parse(&text).unwrap();
    }
}
