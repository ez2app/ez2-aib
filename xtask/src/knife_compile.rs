//! `aib-xtask knife-compile <knife-tsv> [ROOT]` (b4297 11-20; ORDER-autodrive-webdev-519d0-1120
//! b4297 第 3 項): before a receipt spends its whole test run and then each knife's own
//! `cargo test`, check cheaply that every knife still compiles. A knife that does not compile
//! comes back from the receipt as `INVALID` (no test went red, so it bit nothing), after the
//! receipt already used its machine for a full round; on 11-19 three seats each lost a round so.
//!
//! The knife file is the receipt's (receipt-run.sh): one knife per line, six tab-separated fields
//! read raw (no unescaping, as `read -r` reads them): name, old, new, test filter, file, crate.
//!
//! How (one `cargo check --tests` for many knives, not one build per knife):
//! 1. `cargo check` without knives must be clean (else `BASELINE-RED`, nothing is judged).
//! 2. Knives are put together into as few batches as can be: a knife joins a batch when its
//!    anchor is still exactly once in that batch's copy of its file (two knives on one line go
//!    to two batches). A knife whose anchor is not exactly once in the file is `anchor`.
//! 3. Each batch is written, checked, and the files are put back (also when this program ends
//!    early: the originals are kept under `target/knife-compile-backup/` first and put back by
//!    the next run). An error on the lines a knife wrote is that knife's.
//! 4. An error on no knife's lines (a knife removed something used elsewhere, or two knives of
//!    the batch together left a name unused): the knives of that batch not already judged are
//!    split in halves and each half is checked, down to one knife ([`settle`]). A knife is
//!    `ok` when it compiles alone, which is how the receipt's knife step runs it; two knives
//!    that only fail together are both `ok`. Past [`CHECKS_MAX`] checks in all, `unsure`.
//!
//! `cargo check` inherits the environment: the receipt exports `RUSTFLAGS="-D warnings"`, so a
//! knife that only leaves a warning (a function it made unused) is an error here as it is there.
//!
//! Output, one line per knife: `KNIFE-COMPILE ok|error|anchor|unsure <name> [file:line message]`,
//! a `KNIFE-COMPILE-SPLIT batch= knives= checks=` line for each batch that was split, then `KNIFE-COMPILE-SUMMARY knives= ok= error= anchor= unsure= checks=`. Exit 0 when every
//! knife compiles, 1 when any is error / anchor / unsure, 2 for a bad call, 3 for a red baseline.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Command;

/// The most checks one run makes after its batches (the splitting of [`settle`]); the knives
/// still open then are `unsure`, which keeps the receipt red.
pub const CHECKS_MAX: usize = 120;
const BACKUP: &str = "target/knife-compile-backup";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Knife {
    pub name: String,
    pub old: String,
    pub new: String,
    pub file: String,
    pub krate: String,
}

/// The receipt's knife file: six fields per line, raw; blank lines skipped.
pub fn parse_tsv(text: &str) -> Result<Vec<Knife>, String> {
    let mut v = Vec::new();
    for (i, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let f: Vec<&str> = line.split('\t').collect();
        if f.len() != 6 || f[0].is_empty() || f[1].is_empty() || f[4].is_empty() {
            return Err(format!(
                "line {}: need 6 tab-separated fields (name old new filter file crate), got {}",
                i + 1,
                f.len()
            ));
        }
        v.push(Knife {
            name: f[0].into(),
            old: f[1].into(),
            new: f[2].into(),
            file: f[4].replace('\\', "/"),
            krate: f[5].into(),
        });
    }
    Ok(v)
}

/// Where one knife's new text sits in its batch's copy of the file (1-based lines).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Placed {
    pub knife: usize,
    pub file: String,
    pub first: usize,
    pub last: usize,
}

#[derive(Debug, Default)]
pub struct Batch {
    /// file ⇒ its text with this batch's knives in.
    pub files: BTreeMap<String, String>,
    /// file ⇒ (byte offset of a knife's new text, its length, knife index), kept in step.
    spots: BTreeMap<String, Vec<(usize, usize, usize)>>,
}

impl Batch {
    /// The lines each knife of this batch wrote.
    pub fn placed(&self) -> Vec<Placed> {
        let mut v = Vec::new();
        for (file, spots) in &self.spots {
            let text = &self.files[file];
            for &(at, len, knife) in spots {
                let first = text[..at].matches('\n').count() + 1;
                let last = first + text[at..at + len].matches('\n').count();
                v.push(Placed {
                    knife,
                    file: file.clone(),
                    first,
                    last,
                });
            }
        }
        v.sort_by_key(|p| p.knife);
        v
    }

    fn try_put(&mut self, k: &Knife, i: usize, original: &str) -> bool {
        let text = self
            .files
            .get(&k.file)
            .cloned()
            .unwrap_or_else(|| original.to_owned());
        if text.matches(k.old.as_str()).count() != 1 {
            return false;
        }
        let Some(at) = text.find(k.old.as_str()) else {
            return false;
        };
        // a knife whose anchor overlaps another knife's new text goes to another batch
        let spots = self.spots.entry(k.file.clone()).or_default();
        let end = at + k.old.len();
        if spots.iter().any(|&(a, l, _)| at < a + l && a < end) {
            return false;
        }
        let mut out = String::with_capacity(text.len() + k.new.len());
        out.push_str(&text[..at]);
        out.push_str(&k.new);
        out.push_str(&text[end..]);
        let grow = k.new.len() as isize - k.old.len() as isize;
        for s in spots.iter_mut() {
            if s.0 > at {
                s.0 = (s.0 as isize + grow) as usize;
            }
        }
        spots.push((at, k.new.len(), i));
        self.files.insert(k.file.clone(), out);
        true
    }
}

/// Knives into batches; the second list is the knives whose anchor is not exactly once in the
/// file as it is (or whose file cannot be read).
pub fn plan(knives: &[Knife], read: &dyn Fn(&str) -> Option<String>) -> (Vec<Batch>, Vec<usize>) {
    let mut batches: Vec<Batch> = Vec::new();
    let mut anchor = Vec::new();
    let mut originals: BTreeMap<String, Option<String>> = BTreeMap::new();
    for (i, k) in knives.iter().enumerate() {
        let original = originals
            .entry(k.file.clone())
            .or_insert_with(|| read(&k.file))
            .clone();
        let Some(original) = original else {
            anchor.push(i);
            continue;
        };
        if original.matches(k.old.as_str()).count() != 1 {
            anchor.push(i);
            continue;
        }
        if !batches.iter_mut().any(|b| b.try_put(k, i, &original)) {
            let mut b = Batch::default();
            if b.try_put(k, i, &original) {
                batches.push(b);
            } else {
                anchor.push(i);
            }
        }
    }
    (batches, anchor)
}

/// One compiler error (`--message-format=short`): file (`/` between names), line, message.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Diag {
    pub file: String,
    pub line: usize,
    pub msg: String,
}

/// The errors (not warnings) of `cargo check --message-format=short` output.
pub fn errors(out: &str) -> Vec<Diag> {
    let mut v = Vec::new();
    for l in out.lines() {
        let Some(at) = l.find(": error") else {
            continue;
        };
        let (head, tail) = (&l[..at], &l[at + 2..]);
        let mut parts = head.rsplitn(3, ':');
        let (Some(_col), Some(line), Some(file)) = (parts.next(), parts.next(), parts.next())
        else {
            continue;
        };
        let Ok(line) = line.parse::<usize>() else {
            continue;
        };
        let file = file.replace('\\', "/");
        let file = file.strip_prefix("./").unwrap_or(&file).to_owned();
        v.push(Diag {
            file,
            line,
            msg: tail.to_owned(),
        });
    }
    v
}

/// Each error on a knife's lines is that knife's (first one kept); the rest are on no knife.
pub fn attribute(placed: &[Placed], diags: &[Diag]) -> (BTreeMap<usize, String>, Vec<Diag>) {
    let mut mine = BTreeMap::new();
    let mut rest = Vec::new();
    for d in diags {
        let hit = placed.iter().find(|p| {
            (d.file == p.file || d.file.ends_with(&format!("/{}", p.file)))
                && (p.first..=p.last).contains(&d.line)
        });
        match hit {
            Some(p) => {
                mine.entry(p.knife)
                    .or_insert_with(|| format!("{}:{} {}", d.file, d.line, d.msg));
            }
            None => rest.push(d.clone()),
        }
    }
    (mine, rest)
}

/// Files as they were before a batch went in; put back when dropped (and kept on disk under
/// [`BACKUP`] meanwhile, for [`recover`] after a kill).
struct Applied {
    root: PathBuf,
    files: Vec<(String, String)>,
}

impl Applied {
    fn put(root: &Path, files: &BTreeMap<String, String>) -> std::io::Result<Applied> {
        let dir = root.join(BACKUP);
        std::fs::create_dir_all(&dir)?;
        let mut a = Applied {
            root: root.to_path_buf(),
            files: Vec::new(),
        };
        for (n, (rel, text)) in files.iter().enumerate() {
            let p = root.join(rel);
            let orig = std::fs::read_to_string(&p)?;
            std::fs::write(dir.join(format!("{n}.path")), rel)?;
            std::fs::write(dir.join(format!("{n}.orig")), &orig)?;
            a.files.push((rel.clone(), orig));
            std::fs::write(&p, text)?;
        }
        Ok(a)
    }
}

impl Drop for Applied {
    fn drop(&mut self) {
        for (rel, orig) in &self.files {
            let _ = std::fs::write(self.root.join(rel), orig);
        }
        let _ = std::fs::remove_dir_all(self.root.join(BACKUP));
    }
}

/// Put back what a run that was killed left in (`<n>.path` + `<n>.orig` pairs). How many.
pub fn recover(root: &Path) -> usize {
    let dir = root.join(BACKUP);
    let Ok(rd) = std::fs::read_dir(&dir) else {
        return 0;
    };
    let mut n = 0;
    for e in rd.flatten() {
        let p = e.path();
        if p.extension().is_some_and(|x| x == "path") {
            let (Ok(rel), Ok(orig)) = (
                std::fs::read_to_string(&p),
                std::fs::read_to_string(p.with_extension("orig")),
            ) else {
                continue;
            };
            if std::fs::write(root.join(rel.trim()), orig).is_ok() {
                n += 1;
            }
        }
    }
    let _ = std::fs::remove_dir_all(&dir);
    n
}

fn check(root: &Path, crates: &[String]) -> Result<String, String> {
    let mut c = Command::new(std::env::var("CARGO").unwrap_or_else(|_| "cargo".into()));
    c.current_dir(root)
        .args(["check", "--locked", "--tests", "--message-format=short"]);
    for k in crates {
        c.args(["-p", k]);
    }
    let o = c
        .output()
        .map_err(|e| format!("cargo did not start: {e}"))?;
    Ok(format!(
        "{}{}",
        String::from_utf8_lossy(&o.stdout),
        String::from_utf8_lossy(&o.stderr)
    ))
}

fn crates_of(knives: &[Knife], which: &[usize]) -> Vec<String> {
    let mut v: Vec<String> = which
        .iter()
        .map(|&i| knives[i].krate.clone())
        .filter(|c| !c.is_empty())
        .collect();
    v.sort();
    v.dedup();
    v
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    Ok,
    Error(String),
    Anchor,
    Unsure(String),
}

/// What one check of some knives together showed: the errors on a knife's own lines, and the
/// first error on no knife's lines. `unplaced`: the knives could not be written in together
/// at all, so nothing was checked.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Seen {
    pub mine: BTreeMap<usize, String>,
    pub elsewhere: Option<String>,
    pub unplaced: bool,
}

/// Judge the knives `open` after a check of them together showed `why`, an error on no knife's
/// lines. One knife is checked alone: clean is `ok`, any error is its `error`. More are split
/// in halves and each half is checked: an error on a knife's lines is that knife's, a half with
/// no other error is `ok`, and a half with one is split again. `used` counts the checks; at
/// [`CHECKS_MAX`] the knives not yet judged are `unsure`. `look` checks the knives it is given
/// together (an `Err` ends the run).
pub fn settle(
    open: &[usize],
    why: &str,
    used: &mut usize,
    look: &mut dyn FnMut(&[usize]) -> Result<Seen, String>,
    verdict: &mut BTreeMap<usize, Verdict>,
) -> Result<(), String> {
    let over = |v: &mut BTreeMap<usize, Verdict>, which: &[usize]| {
        for &i in which {
            v.insert(
                i,
                Verdict::Unsure(format!("{why} (not settled: {CHECKS_MAX} checks made)")),
            );
        }
    };
    if let [one] = open {
        if *used >= CHECKS_MAX {
            over(verdict, open);
            return Ok(());
        }
        *used += 1;
        let seen = look(open)?;
        // 5d33a N1 (REVIEW-kcbisect f93fca49): one knife that cannot be written in alone is an
        // anchor fault, as before the split
        if seen.unplaced {
            verdict.insert(*one, Verdict::Anchor);
            return Ok(());
        }
        let said = seen.mine.get(one).cloned().or(seen.elsewhere);
        verdict.insert(*one, said.map_or(Verdict::Ok, Verdict::Error));
        return Ok(());
    }
    let (a, b) = open.split_at(open.len() / 2);
    for half in [a, b] {
        if half.is_empty() {
            continue;
        }
        let out_of_checks = *used >= CHECKS_MAX;
        if out_of_checks {
            over(verdict, half);
            continue;
        }
        *used += 1;
        let seen = look(half)?;
        for (knife, said) in &seen.mine {
            verdict.insert(*knife, Verdict::Error(said.clone()));
        }
        let rest: Vec<usize> = half
            .iter()
            .copied()
            .filter(|i| !seen.mine.contains_key(i))
            .collect();
        match seen.elsewhere {
            None => {
                for i in rest {
                    verdict.insert(i, Verdict::Ok);
                }
            }
            Some(m) => settle(&rest, &m, used, look, verdict)?,
        }
    }
    Ok(())
}

pub fn run(root: &Path, tsv: &Path) -> u8 {
    let knives = match std::fs::read_to_string(tsv)
        .map_err(|e| e.to_string())
        .and_then(|t| parse_tsv(&t))
    {
        Ok(k) => k,
        Err(e) => {
            eprintln!("knife-compile: {}: {e}", tsv.display());
            return 2;
        }
    };
    run_knives(root, &knives, None)
}

/// Does `knife`, written in alone, still build under its own command (`cargo test …` with
/// `--no-run` added)? The wide check ([`check`]: `cargo check --tests` of the crate) also builds
/// the crate without its tests, where a knife that takes away a value only tests still make is a
/// dead-code error; the knife's own command (`--lib`, one `--test`) never builds that.
/// Err: the command is not `cargo test`, the anchor is not there once, or nothing could be run.
pub fn builds_alone(root: &Path, knife: &Knife, cmd: &[String]) -> Result<bool, String> {
    if cmd.len() < 2 || cmd[0] != "cargo" || cmd[1] != "test" {
        return Err("its command is not `cargo test`".into());
    }
    let text = std::fs::read_to_string(root.join(&knife.file))
        .map_err(|e| format!("{}: {e}", knife.file))?;
    if text.matches(knife.old.as_str()).count() != 1 {
        return Err(format!(
            "{}: the anchor is not there exactly once",
            knife.file
        ));
    }
    let mut files = BTreeMap::new();
    files.insert(
        knife.file.clone(),
        text.replacen(knife.old.as_str(), &knife.new, 1),
    );
    let _put =
        Applied::put(root, &files).map_err(|e| format!("could not write the knife in: {e}"))?;
    let o = Command::new(std::env::var("CARGO").unwrap_or_else(|_| "cargo".into()))
        .current_dir(root)
        .args(&cmd[1..])
        .arg("--no-run")
        .output()
        .map_err(|e| format!("cargo: {e}"))?;
    Ok(o.status.success())
}

/// What the caller knows beyond the wide check: for knife `i`, `Some(Ok(true))` = it builds under
/// its own command, `Some(Ok(false))` = it does not, `Some(Err)` / `None` = not known.
pub type Confirm<'a> = &'a dyn Fn(usize) -> Option<Result<bool, String>>;

/// [`run`] for knives already read (`aib-xtask knife-precheck` takes them from the knife scripts,
/// where an anchor may span lines and so has no place in the receipt's one-line fields).
/// Every `Error` of the wide check asked again ([`Confirm`]): builds under its own command ⇒
/// `Ok` (returned with what the wide check said, to be printed); does not ⇒ stays `Error`; not
/// known ⇒ `Unsure`, never `Ok`. Other verdicts are not asked about.
pub fn ask_again(
    verdict: &mut BTreeMap<usize, Verdict>,
    confirm: Confirm<'_>,
) -> Vec<(usize, String)> {
    let errors: Vec<(usize, String)> = verdict
        .iter()
        .filter_map(|(i, v)| match v {
            Verdict::Error(m) => Some((*i, m.clone())),
            _ => None,
        })
        .collect();
    let mut cleared = Vec::new();
    for (i, wide) in errors {
        match confirm(i) {
            Some(Ok(true)) => {
                verdict.insert(i, Verdict::Ok);
                cleared.push((i, wide));
            }
            Some(Ok(false)) => {}
            Some(Err(e)) => {
                verdict.insert(i, Verdict::Unsure(format!("{wide}; own command: {e}")));
            }
            None => {
                verdict.insert(i, Verdict::Unsure(format!("{wide}; own command not known")));
            }
        }
    }
    cleared
}

/// With `confirm`, a knife the wide check calls an error is asked again under its own command
/// before it is called one: only an error both ways is an error (a wrong block costs more than
/// a late red). Not known either way ⇒ `unsure`, never `ok`.
pub fn run_knives(root: &Path, knives: &[Knife], confirm: Option<Confirm<'_>>) -> u8 {
    let back = recover(root);
    if back > 0 {
        println!("KNIFE-COMPILE-RECOVERED {back} file(s) a killed run left with knives in");
    }
    let all: Vec<usize> = (0..knives.len()).collect();
    let crates = crates_of(knives, &all);
    let mut checks = 1;
    match check(root, &crates) {
        Ok(out) if errors(&out).is_empty() => {}
        Ok(out) => {
            for d in errors(&out).iter().take(10) {
                println!("BASELINE-RED {}:{} {}", d.file, d.line, d.msg);
            }
            return 3;
        }
        Err(e) => {
            eprintln!("knife-compile: {e}");
            return 2;
        }
    }
    let read = |rel: &str| std::fs::read_to_string(root.join(rel)).ok();
    let (batches, anchor) = plan(knives, &read);
    let mut verdict: BTreeMap<usize, Verdict> = BTreeMap::new();
    for i in anchor {
        verdict.insert(i, Verdict::Anchor);
    }
    // the checks made by splitting, over all batches (held to CHECKS_MAX)
    let mut split_checks = 0;
    for (n, b) in batches.iter().enumerate() {
        let placed = b.placed();
        let ids: Vec<usize> = placed.iter().map(|p| p.knife).collect();
        let out = {
            let _put = match Applied::put(root, &b.files) {
                Ok(a) => a,
                Err(e) => {
                    eprintln!("knife-compile: could not write the knives in: {e}");
                    return 2;
                }
            };
            checks += 1;
            check(root, &crates_of(knives, &ids))
        };
        let out = match out {
            Ok(o) => o,
            Err(e) => {
                eprintln!("knife-compile: {e}");
                return 2;
            }
        };
        let (mine, rest) = attribute(&placed, &errors(&out));
        for (k, m) in &mine {
            verdict.insert(*k, Verdict::Error(m.clone()));
        }
        let open: Vec<usize> = ids
            .iter()
            .copied()
            .filter(|i| !verdict.contains_key(i))
            .collect();
        if rest.is_empty() {
            for i in open {
                verdict.insert(i, Verdict::Ok);
            }
            continue;
        }
        // an error on no knife's lines: the open knives are split until each is judged
        let first = format!("{}:{} {}", rest[0].file, rest[0].line, rest[0].msg);
        let mut look = |ids: &[usize]| -> Result<Seen, String> {
            let some: Vec<Knife> = ids.iter().map(|&i| knives[i].clone()).collect();
            let (together, unplaced) = plan(&some, &read);
            // knives of one batch stand together; if these do not, say so and let them be split
            let (Some(one), true) = (together.first(), together.len() == 1 && unplaced.is_empty())
            else {
                return Ok(Seen {
                    mine: BTreeMap::new(),
                    elsewhere: Some("these knives could not be written in together".into()),
                    unplaced: true,
                });
            };
            let placed: Vec<Placed> = one
                .placed()
                .into_iter()
                .map(|p| Placed {
                    knife: ids[p.knife],
                    ..p
                })
                .collect();
            let out = {
                let _put = Applied::put(root, &one.files)
                    .map_err(|e| format!("could not write the knives in: {e}"))?;
                check(root, &crates_of(knives, ids))?
            };
            let (mine, rest) = attribute(&placed, &errors(&out));
            Ok(Seen {
                mine,
                elsewhere: rest
                    .first()
                    .map(|d| format!("{}:{} {}", d.file, d.line, d.msg)),
                unplaced: false,
            })
        };
        let mut used = split_checks;
        if let Err(e) = settle(&open, &first, &mut used, &mut look, &mut verdict) {
            eprintln!("knife-compile: {e}");
            return 2;
        }
        println!(
            "KNIFE-COMPILE-SPLIT batch={} knives={} checks={}",
            n + 1,
            open.len(),
            used - split_checks
        );
        checks += used - split_checks;
        split_checks = used;
    }
    if let Some(confirm) = confirm {
        for (i, wide) in ask_again(&mut verdict, confirm) {
            println!(
                "KNIFE-COMPILE-OWN ok {} (its own command builds; the wide check said: {wide})",
                knives[i].name
            );
        }
    }
    let (mut ok, mut err, mut anc, mut uns) = (0, 0, 0, 0);
    for (i, k) in knives.iter().enumerate() {
        match verdict
            .get(&i)
            .unwrap_or(&Verdict::Unsure("not checked".into()))
        {
            Verdict::Ok => {
                ok += 1;
                println!("KNIFE-COMPILE ok {}", k.name);
            }
            Verdict::Error(m) => {
                err += 1;
                println!("KNIFE-COMPILE error {} {m}", k.name);
            }
            Verdict::Anchor => {
                anc += 1;
                println!("KNIFE-COMPILE anchor {} {}", k.name, k.file);
            }
            Verdict::Unsure(m) => {
                uns += 1;
                println!("KNIFE-COMPILE unsure {} {m}", k.name);
            }
        }
    }
    println!(
        "KNIFE-COMPILE-SUMMARY knives={} ok={ok} error={err} anchor={anc} unsure={uns} checks={checks}",
        knives.len()
    );
    u8::from(err + anc + uns > 0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn k(name: &str, old: &str, new: &str, file: &str) -> Knife {
        Knife {
            name: name.into(),
            old: old.into(),
            new: new.into(),
            file: file.into(),
            krate: "c".into(),
        }
    }

    /// A stand-in for the compiler: `alone` are knives that do not compile by themselves (the
    /// error is on no knife's line), `pairs` only fail when both are in, `lined` fail on their
    /// own lines. Returns the verdicts and how many checks were made.
    fn settled(
        n: usize,
        alone: &[usize],
        pairs: &[(usize, usize)],
        lined: &[usize],
    ) -> (BTreeMap<usize, Verdict>, usize) {
        let mut look = |ids: &[usize]| -> Result<Seen, String> {
            let has = |i: &usize| ids.contains(i);
            let mine = lined
                .iter()
                .filter(|i| has(i))
                .map(|i| (*i, format!("line of {i}")))
                .collect();
            let bad = alone.iter().find(|i| has(i)).map(|i| format!("alone {i}"));
            let both = pairs
                .iter()
                .find(|(a, b)| has(a) && has(b))
                .map(|(a, b)| format!("pair {a} {b}"));
            Ok(Seen {
                mine,
                elsewhere: bad.or(both),
                unplaced: false,
            })
        };
        let open: Vec<usize> = (0..n).collect();
        let (mut verdict, mut used) = (BTreeMap::new(), 0);
        settle(
            &open,
            "the batch's error",
            &mut used,
            &mut look,
            &mut verdict,
        )
        .expect("settle");
        (verdict, used)
    }

    /// The seventh case (receipt a9136-r4d-3b449ce0e): LH1 and LH2 each compile alone and leave
    /// `preview` unused together; 38 knives in the batch. Nobody is `unsure`, everybody is `ok`.
    #[test]
    fn two_knives_that_only_fail_together_are_both_ok_and_take_nobody_with_them() {
        let (v, used) = settled(38, &[], &[(15, 16)], &[]);
        assert_eq!(v.len(), 38, "reason: a_knife_was_left_unjudged");
        assert!(
            v.values().all(|x| *x == Verdict::Ok),
            "reason: a_collision_is_somebodys_error {v:?}"
        );
        assert!(used <= 12, "reason: {used}_checks_for_one_collision");
        // in the two halves from the start: two checks settle it
        let (v, used) = settled(38, &[], &[(0, 37)], &[]);
        assert!(v.values().all(|x| *x == Verdict::Ok));
        assert_eq!(used, 2, "reason: clean_halves_were_split_again");
        // one knife left with an error that was on no knife's lines, clean when checked alone:
        // the error was not its own (k receipt a9136-kcb-f1c592e93: KC7 did not bite)
        let mut v = BTreeMap::new();
        let mut used = 0;
        let mut clean = |_: &[usize]| -> Result<Seen, String> { Ok(Seen::default()) };
        settle(&[4], "an error elsewhere", &mut used, &mut clean, &mut v).unwrap();
        assert_eq!(
            (v.get(&4), used),
            (Some(&Verdict::Ok), 1),
            "reason: a_knife_clean_alone_is_an_error"
        );
        // one knife that cannot be written in alone is an anchor fault (5d33a N1, REVIEW-kcbisect)
        let mut v = BTreeMap::new();
        let mut used = 0;
        let mut unplaced = |_: &[usize]| -> Result<Seen, String> {
            Ok(Seen {
                elsewhere: Some("these knives could not be written in together".into()),
                unplaced: true,
                ..Seen::default()
            })
        };
        settle(&[4], "an error elsewhere", &mut used, &mut unplaced, &mut v).unwrap();
        assert_eq!(
            v.get(&4),
            Some(&Verdict::Anchor),
            "reason: a_knife_not_written_in_is_not_an_anchor_fault"
        );
    }

    #[test]
    fn a_knife_that_fails_alone_is_the_one_error_with_its_own_message() {
        let (v, _) = settled(38, &[9], &[(15, 16)], &[]);
        for (i, x) in &v {
            let want = if *i == 9 {
                Verdict::Error("alone 9".into())
            } else {
                Verdict::Ok
            };
            assert_eq!(*x, want, "reason: knife_{i}_misjudged");
        }
        assert_eq!(v.len(), 38);
        // every knife fails alone: every knife is an error, none is ok by being with others
        let all: Vec<usize> = (0..8).collect();
        let (v, _) = settled(8, &all, &[], &[]);
        assert!(
            v.values().all(|x| matches!(x, Verdict::Error(_))) && v.len() == 8,
            "reason: a_broken_knife_passed {v:?}"
        );
        // one knife, one check: its error is its own even when it is on no knife's line
        let (v, used) = settled(1, &[0], &[], &[]);
        assert_eq!(
            (v.get(&0), used),
            (Some(&Verdict::Error("alone 0".into())), 1)
        );
    }

    #[test]
    fn an_error_on_a_knifes_own_lines_is_that_knifes_and_the_rest_of_its_half_is_judged() {
        // 3 fails on its own lines; 5 fails alone elsewhere; the others are clean
        let (v, _) = settled(8, &[5], &[], &[3]);
        assert_eq!(v.get(&3), Some(&Verdict::Error("line of 3".into())));
        assert_eq!(v.get(&5), Some(&Verdict::Error("alone 5".into())));
        for i in [0, 1, 2, 4, 6, 7] {
            assert_eq!(v.get(&i), Some(&Verdict::Ok), "reason: knife_{i}_misjudged");
        }
    }

    /// 5d33a PREREAD-kcbisect: a half that shows an error on a knife's lines and another on no
    /// knife's lines is split again; the rest of it is not called ok for the first error's sake.
    #[test]
    fn a_half_with_both_kinds_of_error_is_still_split() {
        // in the first half of 8: 1 fails on its own lines, 2 fails alone elsewhere
        let (v, _) = settled(8, &[2], &[], &[1]);
        assert_eq!(v.get(&1), Some(&Verdict::Error("line of 1".into())));
        assert_eq!(
            v.get(&2),
            Some(&Verdict::Error("alone 2".into())),
            "reason: the_other_error_of_the_half_was_dropped"
        );
        for i in [0, 3, 4, 5, 6, 7] {
            assert_eq!(v.get(&i), Some(&Verdict::Ok), "reason: knife_{i}_misjudged");
        }
        // the same with a pair that only fails together beside the lined one
        let (v, _) = settled(8, &[], &[(0, 3)], &[1]);
        assert_eq!(v.get(&1), Some(&Verdict::Error("line of 1".into())));
        for i in [0, 2, 3, 4, 5, 6, 7] {
            assert_eq!(v.get(&i), Some(&Verdict::Ok), "reason: knife_{i}_misjudged");
        }
    }

    /// 5d33a PREREAD-kcbisect: the same knife file judged twice gives the same verdicts with
    /// the same number of checks (nothing in the splitting depends on anything but its input).
    #[test]
    fn the_same_knives_are_judged_the_same_way_twice() {
        for (n, alone, pairs, lined) in [
            (38, vec![9], vec![(15, 16)], vec![]),
            (38, vec![], vec![(0, 37), (4, 5)], vec![20]),
            (
                4 * CHECKS_MAX,
                (0..4 * CHECKS_MAX).collect(),
                vec![],
                vec![],
            ),
        ] {
            let one = settled(n, &alone, &pairs, &lined);
            let two = settled(n, &alone, &pairs, &lined);
            assert_eq!(one, two, "reason: judged_differently_the_second_time");
        }
    }

    #[test]
    fn past_the_limit_the_knives_left_are_unsure_and_never_ok() {
        // every pair of neighbours collides and every knife fails alone: far more checks than
        // the limit allows
        let n = 4 * CHECKS_MAX;
        let all: Vec<usize> = (0..n).collect();
        let (v, used) = settled(n, &all, &[], &[]);
        assert_eq!(used, CHECKS_MAX, "reason: the_limit_is_not_kept");
        assert_eq!(v.len(), n, "reason: a_knife_was_left_unjudged");
        let unsure = v
            .values()
            .filter(|x| matches!(x, Verdict::Unsure(m) if m.contains("not settled")))
            .count();
        assert!(unsure > 0, "reason: nothing_unsure_past_the_limit");
        assert!(
            !v.values().any(|x| *x == Verdict::Ok),
            "reason: a_knife_not_checked_is_ok"
        );
        // and an unsure knife keeps the run red (the exit code counts it)
        let src = include_str!("knife_compile.rs").replace("\r\n", "\n");
        assert!(
            src.contains(concat!("    u8::from(err + anc + ", "uns > 0)\n")),
            "reason: unsure_does_not_keep_the_run_red"
        );
    }

    #[test]
    fn only_an_error_both_ways_is_an_error_and_not_known_is_never_ok() {
        let mut v: BTreeMap<usize, Verdict> = BTreeMap::new();
        v.insert(0, Verdict::Error("dead code".into()));
        v.insert(1, Verdict::Error("no such field".into()));
        v.insert(2, Verdict::Error("x".into()));
        v.insert(3, Verdict::Error("y".into()));
        v.insert(4, Verdict::Ok);
        v.insert(5, Verdict::Anchor);
        let asked = std::cell::RefCell::new(Vec::new());
        let confirm = |i: usize| {
            asked.borrow_mut().push(i);
            match i {
                0 => Some(Ok(true)),
                1 => Some(Ok(false)),
                2 => Some(Err("its command is not `cargo test`".to_string())),
                _ => None,
            }
        };
        let cleared = ask_again(&mut v, &confirm);
        // only the wide check's errors are asked about
        assert_eq!(*asked.borrow(), vec![0, 1, 2, 3]);
        assert_eq!(cleared, vec![(0, "dead code".to_string())]);
        assert_eq!(v[&0], Verdict::Ok);
        assert_eq!(v[&1], Verdict::Error("no such field".into()));
        assert!(
            matches!(&v[&2], Verdict::Unsure(m) if m.contains("not `cargo test`")),
            "{:?}",
            v[&2]
        );
        assert!(
            matches!(&v[&3], Verdict::Unsure(m) if m.contains("not known")),
            "{:?}",
            v[&3]
        );
        assert_eq!((&v[&4], &v[&5]), (&Verdict::Ok, &Verdict::Anchor));
    }

    #[test]
    fn the_knife_file_is_read_raw_six_fields_a_line() {
        let t = "k1\t    a('\\0');\t    a('\\1'); // knife\tt_x\tsrc\\lib.rs\taibd\n\n";
        let v = parse_tsv(t).expect("parse");
        assert_eq!(v.len(), 1);
        assert_eq!(v[0].old, "    a('\\0');", "reason: unescaped");
        assert_eq!(v[0].file, "src/lib.rs", "reason: windows_separator_kept");
        assert!(
            parse_tsv("k1\ta\tb\tt\tf\n").is_err(),
            "reason: five_fields_taken"
        );
    }

    #[test]
    fn knives_share_a_batch_unless_they_touch_the_same_text() {
        let src = "fn a() {\n    one();\n    two();\n}\n";
        let read = |f: &str| (f == "a.rs").then(|| src.to_owned());
        let knives = vec![
            k("k1", "    one();", "    // knife\n    one2();", "a.rs"),
            k("k2", "    two();", "    two2();", "a.rs"),
            k("k3", "    two();", "    three();", "a.rs"),
            k("k4", "    gone();", "x", "a.rs"),
            k("k5", "x", "y", "missing.rs"),
        ];
        let (b, anchor) = plan(&knives, &read);
        assert_eq!(anchor, vec![3, 4], "reason: anchor_not_judged");
        assert_eq!(b.len(), 2, "reason: same_line_knives_in_one_batch");
        let p = b[0].placed();
        // k1 grew by a line, so k2 (below it) moved down one
        assert_eq!(
            p,
            vec![
                Placed {
                    knife: 0,
                    file: "a.rs".into(),
                    first: 2,
                    last: 3
                },
                Placed {
                    knife: 1,
                    file: "a.rs".into(),
                    first: 4,
                    last: 4
                },
            ],
            "reason: lines_not_in_step {}",
            b[0].files["a.rs"]
        );
        assert_eq!(b[1].placed()[0].knife, 2);
        // the other order: k1 goes in above k2 after it, so k2's place moves down with it
        let (b, _) = plan(&[knives[1].clone(), knives[0].clone()], &read);
        let lines: Vec<(usize, usize, usize)> = b[0]
            .placed()
            .iter()
            .map(|p| (p.knife, p.first, p.last))
            .collect();
        assert_eq!(lines, vec![(0, 4, 4), (1, 2, 3)], "reason: place_not_moved");
    }

    #[test]
    fn an_error_on_a_knifes_lines_is_that_knifes_and_warnings_are_not_errors() {
        let out = "\
crates/aibd/src/a.rs:4:9: error[E0425]: cannot find value `x`
crates\\aibd\\src\\a.rs:10:1: error: unused thing
crates/aibd/src/a.rs:4:1: warning: unused variable
error: could not compile `aibd` (lib) due to 2 previous errors
";
        let d = errors(out);
        assert_eq!(d.len(), 2, "reason: warning_or_summary_taken {d:?}");
        assert_eq!(d[1].file, "crates/aibd/src/a.rs", "reason: backslash_path");
        let placed = vec![Placed {
            knife: 7,
            file: "crates/aibd/src/a.rs".into(),
            first: 3,
            last: 4,
        }];
        let (mine, rest) = attribute(&placed, &d);
        assert!(
            mine[&7].contains("E0425"),
            "reason: not_attributed {mine:?}"
        );
        assert_eq!(rest.len(), 1, "reason: line_10_attributed");
    }

    #[test]
    fn the_files_are_put_back_after_a_batch_and_after_a_kill() {
        let d = tempfile_dir();
        std::fs::create_dir_all(d.join("src")).expect("mk");
        std::fs::write(d.join("src/a.rs"), "orig").expect("w");
        let mut files = BTreeMap::new();
        files.insert("src/a.rs".to_string(), "knifed".to_string());
        {
            let _a = Applied::put(&d, &files).expect("put");
            assert_eq!(
                std::fs::read_to_string(d.join("src/a.rs")).expect("r"),
                "knifed"
            );
        }
        assert_eq!(
            std::fs::read_to_string(d.join("src/a.rs")).expect("r"),
            "orig",
            "reason: not_put_back"
        );
        assert!(!d.join(BACKUP).exists(), "reason: backup_left");
        // a killed run: the backup is there, the file still has the knife in
        let a = Applied::put(&d, &files).expect("put");
        std::mem::forget(a);
        assert_eq!(recover(&d), 1, "reason: not_recovered");
        assert_eq!(
            std::fs::read_to_string(d.join("src/a.rs")).expect("r"),
            "orig"
        );
        let _ = std::fs::remove_dir_all(&d);
    }

    fn tempfile_dir() -> PathBuf {
        use std::sync::atomic::{AtomicU32, Ordering};
        static N: AtomicU32 = AtomicU32::new(0);
        let d = std::env::temp_dir().join(format!(
            "knife-compile-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).expect("mk");
        d
    }
}
