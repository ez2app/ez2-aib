//! `aib-xtask knife-precheck --base SHA --head SHA [ROOT]`: before the knife cells spend their
//! hours, check that the knives this change touches still compile (precheck gate 1 of the
//! time-saving council, 11-21: the largest waste of the week was a receipt or a cell run to
//! its end only to find a knife that did not compile, which bites nothing).
//!
//! - **Which knives**: every knife of the workflows and knife scripts (`knife-anchors`' own
//!   scan), kept when its script or workflow file changed between base and head, or when the
//!   file it cuts changed. A knife neither side touched compiled last time and is left to the
//!   cells. No base / head, or a diff that cannot be read: every knife is in scope (said on the
//!   first line), never fewer.
//! - **Checked how**: `knife-compile`'s way (knives put together into as few `cargo check
//!   --tests` as can be, an error on a knife's own lines is that knife's, the rest is split),
//!   and a knife that fails there is asked again alone under its own command with `--no-run`.
//!   Only an error both ways is an error: the wide check also builds the crate without its
//!   tests, which a knife's `cargo test --lib` never does (11-23, first run on main: two macOS
//!   knives green in CI were errors the wide way), and a wrong block costs more than a late red.
//! - **Not checked** is said, one line each, and is not green and not red here: a knife whose
//!   words hold a variable this scan cannot fill in, whose command is not `cargo … -p <one
//!   package>`, or that has no `--`. The cells still run it.
//!
//! Output: `KNIFE-PRECHECK scope=<n> of=<all> check=<n> not_checked=<n> range=<base...head|all:why>`,
//! a `KNIFE-PRECHECK-NOT-CHECKED <at>: <why>` line each, then `knife-compile`'s lines.
//! Exit: 0 every checked knife compiles (or none is in scope), 1 a knife does not, 2 a bad
//! call or the scan itself failed, 3 the tree does not compile without knives.

use crate::knife_anchors::Knife as Found;
use crate::knife_compile::Knife;
use std::collections::BTreeSet;
use std::path::Path;
use std::process::Command;

/// The file a knife is written in: `at` is `<file>:<line>`.
pub fn written_in(at: &str) -> &str {
    at.rsplit_once(':').map_or(at, |(f, _)| f)
}

/// In scope: the knife's own file or the file it cuts is among the changed paths.
/// `changed` None = the range is not known = every knife.
pub fn in_scope(k: &Found, changed: Option<&BTreeSet<String>>) -> bool {
    changed.is_none_or(|c| c.contains(written_in(&k.at)) || c.contains(&k.file))
}

/// What is handed to the compile check, and what is said instead.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Picked {
    pub check: Vec<Knife>,
    /// The own command of each knife in `check`, same order.
    pub cmds: Vec<Vec<String>>,
    /// (where, why) of each knife in scope that cannot be checked here.
    pub not_checked: Vec<(String, String)>,
    pub in_scope: usize,
}

/// A found knife as the compile check reads one, or why it cannot be read as one.
pub fn readable(k: &Found) -> Result<(Knife, Vec<String>), String> {
    let argv = k
        .argv
        .as_ref()
        .map_err(|e| format!("a word is not known: {e}"))?;
    let call = crate::knife_spec::parse_argv(argv)?;
    let packages = crate::knife_affected::cmd_packages(&call.cmd)?;
    let [krate] = packages.as_slice() else {
        return Err(format!(
            "the command names {} packages, not one",
            packages.len()
        ));
    };
    let knife = Knife {
        name: k.at.clone(),
        old: call.old,
        new: call.new,
        file: call.file.replace('\\', "/"),
        krate: krate.clone(),
    };
    Ok((knife, call.cmd))
}

pub fn pick(all: &[Found], changed: Option<&BTreeSet<String>>) -> Picked {
    let mut p = Picked::default();
    for k in all.iter().filter(|k| in_scope(k, changed)) {
        p.in_scope += 1;
        match readable(k) {
            Ok((knife, cmd)) => {
                p.check.push(knife);
                p.cmds.push(cmd);
            }
            Err(why) => p.not_checked.push((k.at.clone(), why)),
        }
    }
    p
}

/// `git diff --no-renames --name-only base...head`, `/` paths; Err = not read.
pub(crate) fn changed(root: &Path, base: &str, head: &str) -> Result<BTreeSet<String>, String> {
    let o = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["diff", "--no-renames", "--name-only", "-z"])
        .arg(format!("{base}...{head}"))
        .output()
        .map_err(|e| format!("git: {e}"))?;
    if !o.status.success() {
        return Err(String::from_utf8_lossy(&o.stderr).trim().to_string());
    }
    let text = String::from_utf8(o.stdout).map_err(|_| "a path that is not UTF-8".to_string())?;
    Ok(text
        .split('\0')
        .filter(|p| !p.is_empty())
        .map(str::to_string)
        .collect())
}

pub const USAGE: &str = "aib-xtask knife-precheck [--base SHA --head SHA] [ROOT]";

pub fn run(args: &[String]) -> u8 {
    let (mut base, mut head, mut root) = (None, None, None);
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let slot = match a.as_str() {
            "--base" => &mut base,
            "--head" => &mut head,
            o if o.starts_with("--") => {
                eprintln!("knife-precheck: {o}: not an option\nusage: {USAGE}");
                return 2;
            }
            _ if root.is_none() => {
                root = Some(a.clone());
                continue;
            }
            _ => {
                eprintln!("usage: {USAGE}");
                return 2;
            }
        };
        match it.next() {
            Some(v) if slot.is_none() && !v.is_empty() => *slot = Some(v.clone()),
            _ => {
                eprintln!("knife-precheck: {a} needs one value, once\nusage: {USAGE}");
                return 2;
            }
        }
    }
    let root = root.unwrap_or_else(|| ".".into());
    let root = Path::new(&root);
    let (_, all, unresolved) = crate::knife_anchors::collect(root);
    if all.is_empty() {
        eprintln!(
            "knife-precheck: no knife found under {} (wrong folder?)",
            root.display()
        );
        return 2;
    }
    let (set, range) = match (&base, &head) {
        (Some(b), Some(h)) => match changed(root, b, h) {
            Ok(s) => (Some(s), format!("{b}...{h}")),
            Err(e) => (None, format!("all:diff_not_read ({e})")),
        },
        _ => (None, "all:no_base_or_head".to_string()),
    };
    let p = pick(&all, set.as_ref());
    println!(
        "KNIFE-PRECHECK scope={} of={} check={} not_checked={} range={range}",
        p.in_scope,
        all.len(),
        p.check.len(),
        p.not_checked.len() + unresolved.len()
    );
    for (at, why) in &p.not_checked {
        println!("KNIFE-PRECHECK-NOT-CHECKED {at}: {why}");
    }
    for u in &unresolved {
        println!("KNIFE-PRECHECK-NOT-CHECKED {u}");
    }
    if p.check.is_empty() {
        return 0;
    }
    let own = |i: usize| {
        Some(crate::knife_compile::builds_alone(
            root,
            &p.check[i],
            &p.cmds[i],
        ))
    };
    crate::knife_compile::run_knives(root, &p.check, Some(&own))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn found(at: &str, file: &str, cmd: &[&str]) -> Found {
        let mut argv = vec![file.to_string(), "old".into(), "new".into()];
        argv.extend([
            "--expect-fail".to_string(),
            "t".to_string(),
            "--".to_string(),
        ]);
        argv.extend(cmd.iter().map(|s| s.to_string()));
        Found {
            at: at.into(),
            file: file.into(),
            old: "old".into(),
            job: String::new(),
            step: String::new(),
            argv: Ok(argv),
        }
    }
    fn set(paths: &[&str]) -> BTreeSet<String> {
        paths.iter().map(|p| p.to_string()).collect()
    }
    const CARGO: [&str; 5] = ["cargo", "test", "-p", "aibd", "--locked"];

    #[test]
    fn a_knife_is_in_scope_when_its_script_or_the_file_it_cuts_changed() {
        let k = found("crates/aibd/knives/k.sh:12", "crates/aibd/src/a.rs", &CARGO);
        assert_eq!(written_in(&k.at), "crates/aibd/knives/k.sh");
        assert!(in_scope(&k, Some(&set(&["crates/aibd/knives/k.sh"]))));
        assert!(in_scope(&k, Some(&set(&["crates/aibd/src/a.rs"]))));
        // another file of the same crate is not this knife's
        assert!(!in_scope(
            &k,
            Some(&set(&["crates/aibd/src/b.rs", "README.md"]))
        ));
        assert!(!in_scope(&k, Some(&set(&[]))));
        // the range is not known: every knife, never fewer
        assert!(in_scope(&k, None));
    }

    #[test]
    fn a_knife_that_cannot_be_read_is_said_and_not_checked_as_if_it_compiled() {
        let good = found("s.sh:1", "crates/aibd/src/a.rs", &CARGO);
        let two = found(
            "s.sh:2",
            "crates/aibd/src/a.rs",
            &["cargo", "test", "-p", "aibd", "-p", "aib-sys"],
        );
        let not_cargo = found("s.sh:3", "crates/aibd/src/a.rs", &["bash", "run.sh"]);
        let mut unknown = found("s.sh:4", "crates/aibd/src/a.rs", &CARGO);
        unknown.argv = Err("$FILE".into());
        let mut no_dashes = found("s.sh:5", "crates/aibd/src/a.rs", &CARGO);
        no_dashes.argv = Ok(vec![
            "crates/aibd/src/a.rs".into(),
            "old".into(),
            "new".into(),
        ]);
        let out_of_scope = found("other.sh:1", "crates/aibd/src/z.rs", &CARGO);
        let all = [good, two, not_cargo, unknown, no_dashes, out_of_scope];
        let p = pick(&all, Some(&set(&["s.sh"])));
        assert_eq!(p.in_scope, 5);
        assert_eq!(p.cmds, vec![CARGO.map(String::from).to_vec()]);
        assert_eq!(
            p.check,
            vec![Knife {
                name: "s.sh:1".into(),
                old: "old".into(),
                new: "new".into(),
                file: "crates/aibd/src/a.rs".into(),
                krate: "aibd".into(),
            }]
        );
        assert_eq!(
            p.not_checked
                .iter()
                .map(|(at, _)| at.as_str())
                .collect::<Vec<_>>(),
            vec!["s.sh:2", "s.sh:3", "s.sh:4", "s.sh:5"]
        );
        assert!(
            p.not_checked[0].1.contains("2 packages"),
            "{:?}",
            p.not_checked
        );
        // no range: all six are in scope
        assert_eq!(pick(&all, None).in_scope, 6);
    }
}
