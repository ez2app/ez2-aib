//! `aib-xtask knife-affected`: which knives of a cell would a pull request have to run, if
//! a knife whose inputs did not change could be skipped (S66-4, shadow only: 519d0 11-15).
//! Nothing is skipped by this command; a workflow step prints what it says next to the knife
//! step, which still runs every knife.
//!
//! ```text
//! aib-xtask knife-affected --event NAME --base SHA --head SHA
//!     [--job ID]... [--source FILE]... [--drop-step TEXT]...
//!     [--metadata FILE] [--changed FILE] [--span-seconds N]
//!     [--ledger FILE] [--repo OWNER/NAME] [--run-id N] [--attempt N] [--check CELL]
//!     [--skip-out FILE] [ROOT]
//! ```
//!
//! - **Knives**: the knife contract (`knife-spec`, the same scan). `--job` / `--source` pick
//!   knives the way `knife-spec` does, several of each are a union; `--drop-step` leaves out
//!   knives whose step name contains that text (the Windows steps of the rust job).
//! - **A knife is affected** when a changed path (`git diff --no-renames --name-only
//!   base...head`) is inside the closure of a package its command tests: that package's
//!   folder and the folders of every workspace crate it reaches through path dependencies
//!   (normal and build, transitively; its own dev-dependencies too, since its tests build
//!   them). Its anchor file, and the workflow / script it is written in, count as well.
//! - **Every knife is affected** (`why_all`), never guessed the other way: an event other
//!   than `pull_request`; no base / head; cargo metadata, the diff or the knife contract not
//!   fully read; a path git quotes; a changed path in [`FIXED`].
//! - **One knife is affected** when its command is not read: not `cargo`, no `-p`, or
//!   `--workspace` / `--all` / `--manifest-path`; a package not in the workspace; a crate in
//!   the closure at the workspace root, or with a `build.rs` not on [`VETTED_BUILD_RS`].
//! - [`FIXED`] and [`VETTED_BUILD_RS`] are the lists of #767's `xtask/ci/closure-changed.sh`
//!   (S66-0d), word for word: one rule for "what a cell's crate is built from" (ebb36 11-15).
//!   On top of #767, [`EXTRA_INPUTS`] adds what a crate in the closure reads outside its folder.
//!
//! **Segment 1 (S66-4, 519d0 council r2 11-17)**: with `--skip-out FILE` (only the gate step
//! passes it, only when the repository variable `AIB_KNIFE_SKIP` is `on`, default off) it
//! appends GITHUB_OUTPUT lines naming the knife steps and knife scripts none of whose knives
//! is affected ([`skip_units`]); the workflow skips exactly those. Every reason in
//! [`why_all_code`] (a push to main, a release, anything not read) gives an empty list, so
//! everything runs. Without `--skip-out` nothing changes: the command still skips nothing.
//! Either way one `KNIVES-AFFECTED …` line ([`affected_line`]) follows the summary.
//!
//! Prints a short Markdown summary (for `$GITHUB_STEP_SUMMARY`). `--ledger FILE` appends one
//! row per knife to FILE in the `ez2-reuse-ledger.v1` format of aib-cireuse (#770, b433b
//! 11-15): `producer` `knife-shadow`, `unit` `knife:<crate>/<name>`, so its week / reasons
//! summaries (which read `unit` `push` and `cell:…`) never count these rows. `could_save_s`
//! is `--span-seconds` (the knife steps' time) shared evenly over the selected knives: an
//! upper bound, not a measured saving.
//!
//! rc 0 = printed (whatever it found); 1 = the ledger could not be written; 2 = usage.

use crate::knife_anchors;
use crate::knife_spec::{self, Entry};
use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::io::Write as _;
use std::path::Path;

pub const LEDGER_SCHEMA: &str = "ez2-reuse-ledger.v1";
pub const KIND_PREDICTION: &str = "reuse_prediction";
pub const PRODUCER: &str = "knife-shadow";
/// aib-cireuse's own sentence (b433b 11-15: copy it, one wording for the whole ledger).
pub const COULD_SAVE_KIND: &str = "upper bound: runner seconds main spent on cells predicted reusable; not wall clock, not a measured saving";

/// A change here affects every knife (#767 closure-changed.sh `FIXED`; a trailing `/` is a
/// folder).
pub const FIXED: &[&str] = &[
    "Cargo.toml",
    "Cargo.lock",
    "rust-toolchain.toml",
    ".cargo/",
    ".github/workflows/aibd.yml",
    "scripts/",
    "xtask/",
    "aib/tools/release/docs_only.py",
];

/// A build.rs may read anything in the repo; only these were read and found to stamp the
/// commit alone (#767 closure-changed.sh `VETTED_BUILD_RS`).
pub const VETTED_BUILD_RS: &[&str] = &["crates/aib-cloud-host/build.rs"];

/// What a crate reads outside its own folder (`include_str!("../../…")`, a test that opens a
/// repo file), so a change there affects its knives too (5d33a S66-4 review L1). `*` is one
/// path segment, a trailing `/` a folder. `the_repo_crates_read_outside_only_what_is_listed`
/// turns red when a crate starts reading outside and is not listed here.
pub const EXTRA_INPUTS: &[(&str, &str)] = &[
    ("aib-cloud", "crates/*/Cargo.toml"),
    ("aib-dmq", "aib/tests/fixtures/dm-spec/"),
    ("aib-lease", "aib/tools/governance/host_lease.py"),
    ("aib-plan", "aib/src/cockpit/"),
    ("aib-seatopen", "aib/src/cockpit/"),
    ("aib-seatops", "aib/src/cockpit/"),
    ("aib-store", "migrations/"),
    ("aib-title", "aib/src/cockpit/"),
    ("aibd", "docs/perf/timers.md"),
];

/// Does a closure entry (a crate folder or an [`EXTRA_INPUTS`] pattern) cover `path`?
pub fn covers(pattern: &str, path: &str) -> bool {
    if pattern.contains('*') {
        let p: Vec<&str> = pattern.split('/').collect();
        let f: Vec<&str> = path.split('/').collect();
        return p.len() == f.len() && p.iter().zip(&f).all(|(a, b)| *a == "*" || a == b);
    }
    if pattern.ends_with('/') {
        path.starts_with(pattern)
    } else {
        path == pattern
    }
}

/// The command line.
#[derive(Debug, Default, Clone)]
pub struct Opts {
    pub event: String,
    pub base: String,
    pub head: String,
    pub jobs: Vec<String>,
    pub sources: Vec<String>,
    pub drop_steps: Vec<String>,
    pub metadata: Option<String>,
    pub changed: Option<String>,
    pub span_seconds: Option<u64>,
    pub ledger: Option<String>,
    pub repo: String,
    pub run_id: Option<u64>,
    pub check: String,
    pub root: String,
    /// `--skip-out FILE`: append `skip_steps=` / `skip_files=` (GITHUB_OUTPUT lines) naming the
    /// knife steps and knife scripts with no affected knife (S66-4 segment 1). Only the gate
    /// step passes it, only when the repository switch is on.
    pub skip_out: Option<String>,
    /// The run attempt, for the `KNIVES-AFFECTED` line (b433b: a head has several runs).
    pub attempt: Option<u64>,
}

pub fn parse_args(args: &[String]) -> Result<Opts, String> {
    let mut o = Opts::default();
    let mut root: Option<String> = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let mut val = |what: &str| it.next().cloned().ok_or(format!("{what} needs a value"));
        match a.as_str() {
            "--event" => o.event = val(a)?,
            "--base" => o.base = val(a)?,
            "--head" => o.head = val(a)?,
            "--job" => o.jobs.push(val(a)?),
            "--source" => o.sources.push(val(a)?),
            "--drop-step" => o.drop_steps.push(val(a)?),
            "--metadata" => o.metadata = Some(val(a)?),
            "--changed" => o.changed = Some(val(a)?),
            "--span-seconds" => {
                let v = val(a)?;
                o.span_seconds = Some(v.parse().map_err(|_| format!("--span-seconds {v}"))?);
            }
            "--ledger" => o.ledger = Some(val(a)?),
            "--repo" => o.repo = val(a)?,
            "--run-id" => {
                let v = val(a)?;
                o.run_id = Some(v.parse().map_err(|_| format!("--run-id {v}"))?);
            }
            "--check" => o.check = val(a)?,
            "--skip-out" => o.skip_out = Some(val(a)?),
            "--attempt" => {
                let v = val(a)?;
                o.attempt = Some(v.parse().map_err(|_| format!("--attempt {v}"))?);
            }
            x if x.starts_with('-') => return Err(format!("unknown option {x}")),
            r if root.is_none() => root = Some(r.to_string()),
            r => return Err(format!("one ROOT only, got another: {r}")),
        }
    }
    o.root = root.unwrap_or_else(|| ".".into());
    Ok(o)
}

/// The packages a knife's command tests. `Err` = not read (the knife is affected).
pub fn cmd_packages(cmd: &[String]) -> Result<Vec<String>, String> {
    if cmd.first().map(String::as_str) != Some("cargo") {
        return Err(format!(
            "command {:?} is not cargo",
            cmd.first().map_or("", String::as_str)
        ));
    }
    let mut out = Vec::new();
    let mut it = cmd[1..].iter();
    while let Some(a) = it.next() {
        let a = a.as_str();
        if a == "--" {
            break;
        } else if a == "--workspace" || a == "--all" {
            return Err(format!("{a}: every package"));
        } else if a == "--manifest-path" || a.starts_with("--manifest-path=") {
            return Err("--manifest-path: package not read".into());
        } else if a == "-p" || a == "--package" {
            let p = it.next().ok_or(format!("{a} without a package"))?;
            out.push(p.clone());
        } else if let Some(p) = a.strip_prefix("--package=") {
            out.push(p.to_string());
        } else if let Some(p) = a
            .strip_prefix("-p")
            .filter(|p| !p.is_empty() && !a.starts_with("--"))
        {
            out.push(p.to_string());
        }
    }
    if out.is_empty() {
        return Err("no -p: the package is the folder it runs in".into());
    }
    Ok(out)
}

/// The closure folders of `krate` (repo paths ending in `/`). `Err` = not read.
pub fn closure(meta: &Value, krate: &str, root: &Path) -> Result<Vec<String>, String> {
    let ws = meta["workspace_root"]
        .as_str()
        .ok_or("metadata has no workspace_root")?
        .replace('\\', "/");
    let pkgs = meta["packages"]
        .as_array()
        .ok_or("metadata has no packages")?;
    let find = |n: &str| pkgs.iter().find(|p| p["name"].as_str() == Some(n));
    let mut todo = vec![krate.to_string()];
    let mut seen: BTreeSet<String> = BTreeSet::new();
    let mut dirs = Vec::new();
    while let Some(n) = todo.pop() {
        if !seen.insert(n.clone()) {
            continue;
        }
        let p = find(&n).ok_or(format!("{n} is not a workspace crate"))?;
        let manifest = p["manifest_path"]
            .as_str()
            .ok_or(format!("{n} has no manifest_path"))?
            .replace('\\', "/");
        let dir = manifest
            .strip_prefix(&format!("{ws}/"))
            .and_then(|m| m.strip_suffix("Cargo.toml"))
            .ok_or(format!("{n}: {manifest} is not under the workspace"))?;
        if dir.is_empty() || dir.starts_with('/') {
            return Err(format!(
                "{n} sits at the workspace root: its closure is the whole repo"
            ));
        }
        let build = format!("{dir}build.rs");
        if root.join(&build).exists() && !VETTED_BUILD_RS.contains(&build.as_str()) {
            return Err(format!("{build} may read outside {dir} and is not vetted"));
        }
        dirs.push(dir.to_string());
        dirs.extend(
            EXTRA_INPUTS
                .iter()
                .filter(|(c, _)| *c == n)
                .map(|(_, p)| p.to_string()),
        );
        for d in p["dependencies"].as_array().into_iter().flatten() {
            if d["path"].is_null() {
                continue;
            }
            if d["kind"].as_str() == Some("dev") && n != krate {
                continue;
            }
            let name = d["name"]
                .as_str()
                .ok_or(format!("{n}: a path dependency without a name"))?;
            todo.push(name.to_string());
        }
    }
    dirs.sort();
    dirs.dedup();
    Ok(dirs)
}

/// One knife's answer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Judged {
    pub name: String,
    pub at: String,
    pub krate: String,
    pub affected: bool,
    pub why: String,
}

/// What every knife depends on; any `Err` makes every knife affected.
pub struct Inputs<'a> {
    pub event: &'a str,
    pub base: &'a str,
    pub head: &'a str,
    pub meta: Result<&'a Value, String>,
    pub changed: Result<&'a [String], String>,
    /// Knife contract problems (`knife-anchors` unresolved, calls not filled in).
    pub unresolved: usize,
}

/// Why every knife is affected, if one reason applies to all of them.
pub fn why_all(i: &Inputs) -> Option<String> {
    why_all_code(i).map(|(_, w)| w)
}

/// [`why_all`] with a short code for the `KNIVES-AFFECTED` line's `all_why=` (b433b 11-17:
/// the ledger tells apart why every knife ran): `event_not_pr`, `base_unknown`,
/// `contract_unread`, `metadata_unread`, `diff_unread`, `quoted_path`, `workflow_changed`
/// (`.github/workflows/aibd.yml`), `fixed_path` (the rest of [`FIXED`]).
pub fn why_all_code(i: &Inputs) -> Option<(&'static str, String)> {
    if i.event != "pull_request" {
        let e = if i.event.is_empty() { "none" } else { i.event };
        return Some(("event_not_pr", format!("event {e} is not a pull request")));
    }
    if i.base.is_empty() || i.head.is_empty() {
        return Some(("base_unknown", "no base / head".into()));
    }
    if i.unresolved > 0 {
        return Some((
            "contract_unread",
            format!(
                "the knife contract is not fully read ({} problems)",
                i.unresolved
            ),
        ));
    }
    if let Err(e) = &i.meta {
        return Some(("metadata_unread", format!("cargo metadata not read: {e}")));
    }
    let changed = match &i.changed {
        Ok(c) => c,
        Err(e) => return Some(("diff_unread", format!("changed paths not read: {e}"))),
    };
    for f in changed.iter() {
        if f.starts_with('"') {
            return Some((
                "quoted_path",
                format!("{f} is quoted by git (unusual characters)"),
            ));
        }
        for x in FIXED {
            let hit = if x.ends_with('/') {
                f.starts_with(x)
            } else {
                f == x
            };
            if hit {
                let code = if x.starts_with(".github/workflows/") {
                    "workflow_changed"
                } else {
                    "fixed_path"
                };
                return Some((code, format!("{f} is in the fixed set ({x})")));
            }
        }
    }
    None
}

fn source_of(at: &str) -> &str {
    at.rsplit_once(':').map_or(at, |(s, _)| s)
}

/// Judge the selected knives.
pub fn judge(entries: &[&Entry], i: &Inputs, root: &Path) -> (Option<String>, Vec<Judged>) {
    let all = why_all(i);
    let mut out = Vec::new();
    for e in entries {
        let pk = cmd_packages(&e.call.cmd);
        let krate = pk
            .as_ref()
            .ok()
            .and_then(|p| p.first().cloned())
            .unwrap_or_else(|| "unknown".into());
        let (affected, why) = match (&all, &i.meta, &i.changed) {
            (Some(w), _, _) => (true, w.clone()),
            (None, Ok(meta), Ok(changed)) => one(e, &pk, meta, changed, root),
            _ => (true, "inputs not read".into()),
        };
        out.push(Judged {
            name: e.name.clone(),
            at: e.knife.at.clone(),
            krate,
            affected,
            why,
        });
    }
    (all, out)
}

fn one(
    e: &Entry,
    pk: &Result<Vec<String>, String>,
    meta: &Value,
    changed: &[String],
    root: &Path,
) -> (bool, String) {
    let pk = match pk {
        Ok(p) => p,
        Err(w) => return (true, format!("command not read: {w}")),
    };
    let src = source_of(&e.knife.at);
    if let Some(f) = changed.iter().find(|f| **f == e.call.file || *f == src) {
        return (
            true,
            format!("{f} is its anchor file or where it is written"),
        );
    }
    let mut folders = Vec::new();
    for p in pk {
        let dirs = match closure(meta, p, root) {
            Ok(d) => d,
            Err(w) => return (true, format!("closure of {p} not read: {w}")),
        };
        for d in &dirs {
            if let Some(f) = changed.iter().find(|f| covers(d, f)) {
                return (true, format!("{f} is in the closure of {p} ({d})"));
            }
        }
        folders.extend(dirs);
    }
    (
        false,
        format!(
            "no change in the closure ({}) or the fixed set",
            folders.join(" ")
        ),
    )
}

/// The knives `opts` selects, in scan order.
pub fn select<'a>(all: &'a [Entry], o: &Opts) -> Vec<&'a Entry> {
    all.iter()
        .filter(|e| {
            let picked = (o.jobs.is_empty() && o.sources.is_empty())
                || o.jobs.contains(&e.knife.job)
                || o.sources.iter().any(|s| s == source_of(&e.knife.at));
            picked
                && !o
                    .drop_steps
                    .iter()
                    .any(|d| e.knife.step.contains(d.as_str()))
        })
        .collect()
}

/// What the gate may skip: knife steps (by `- name:`) and knife scripts (by path) none of
/// whose selected knives is affected. Empty whenever one reason affects every knife.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Skip {
    pub steps: Vec<String>,
    pub files: Vec<String>,
    /// knives inside the skipped steps and scripts
    pub knives: usize,
}

/// Group the judged knives by where they run: a knife in a workflow step belongs to that
/// step, a knife in a script (`step` empty) to the script. A step or script is skipped only
/// when every one of its selected knives is not affected; one affected knife keeps the
/// whole step (a step runs its knives together).
///
/// A step in `called` (its `run:` also calls a knife script, [`steps_calling_scripts`]) is
/// never skipped: skipping it would skip that script's knives with it, whatever they say.
pub fn skip_units(
    entries: &[&Entry],
    judged: &[Judged],
    all: Option<&str>,
    called: &BTreeSet<String>,
) -> Skip {
    let mut out = Skip::default();
    if all.is_some() || entries.len() != judged.len() {
        return out;
    }
    // key: (is a step, name) -> (every knife free, how many)
    let mut units: std::collections::BTreeMap<(bool, String), (bool, usize)> =
        std::collections::BTreeMap::new();
    for (e, j) in entries.iter().zip(judged) {
        let key = if e.knife.step.is_empty() {
            (false, source_of(&e.knife.at).to_string())
        } else {
            (true, e.knife.step.clone())
        };
        let calls = key.0 && called.contains(&key.1);
        let u = units.entry(key).or_insert((true, 0));
        u.0 &= !j.affected;
        u.0 &= !calls;
        u.1 += 1;
    }
    for ((is_step, name), (free, n)) in units {
        if !free {
            continue;
        }
        out.knives += n;
        if is_step {
            out.steps.push(name);
        } else {
            out.files.push(name);
        }
    }
    out
}

/// The steps of a workflow text whose `run:` calls a knife script: a line (not a comment)
/// naming `…/knives/….sh` or `scripts/knives-….sh` inside the step.
pub fn steps_calling_scripts(text: &str) -> BTreeSet<String> {
    let mut out = BTreeSet::new();
    let mut step = String::new();
    for line in text.lines() {
        if knife_anchors::starts_step(line) {
            step = knife_anchors::step_name(line).unwrap_or_default();
            continue;
        }
        let t = line.trim_start();
        if step.is_empty() || t.starts_with('#') || !t.contains(".sh") {
            continue;
        }
        if t.contains("/knives/") || t.contains("scripts/knives-") {
            out.insert(step.clone());
        }
    }
    out
}

/// [`steps_calling_scripts`] of every workflow file under `root`; a file that cannot be read
/// is `Err` (the gate then skips nothing).
fn called_steps(root: &Path) -> Result<BTreeSet<String>, String> {
    let dir = root.join(".github/workflows");
    let mut out = BTreeSet::new();
    let rd = std::fs::read_dir(&dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    for f in rd {
        let p = f.map_err(|e| e.to_string())?.path();
        let yml = p.extension().is_some_and(|x| x == "yml" || x == "yaml");
        if yml {
            let t = std::fs::read_to_string(&p).map_err(|e| format!("{}: {e}", p.display()))?;
            out.extend(steps_calling_scripts(&t));
        }
    }
    Ok(out)
}

/// The one fixed line b433b's ledger reads (11-17): `KNIVES-AFFECTED ran=<n> of=<n>
/// skipped=<n> all_why=<code|-> run=<run_id>/<attempt> head=<sha> base=<sha>`. `skipped` is
/// what the gate skips (0 when it does not run); `ran` the rest.
pub fn affected_line(o: &Opts, code: Option<&str>, total: usize, skipped: usize) -> String {
    format!(
        "KNIVES-AFFECTED ran={} of={total} skipped={skipped} all_why={} run={}/{} head={} base={}",
        total - skipped.min(total),
        code.unwrap_or("-"),
        o.run_id.unwrap_or(0),
        o.attempt.unwrap_or(1),
        if o.head.is_empty() {
            "-"
        } else {
            o.head.as_str()
        },
        if o.base.is_empty() {
            "-"
        } else {
            o.base.as_str()
        },
    )
}

/// The GITHUB_OUTPUT lines of a skip: `skip_steps=<JSON array>` (for `contains(fromJSON(..),
/// name)` in a step's `if:`) and `skip_files=<,path,path,>` (for a script loop's `case`).
pub fn skip_output(skip: &Skip) -> String {
    let files: String = skip.files.iter().map(|f| format!("{f},")).collect();
    format!(
        "skip_steps={}\nskip_files=,{files}\n",
        Value::from(skip.steps.clone())
    )
}

/// Seconds a knife took on average, if the span is known and anything was selected.
fn per_knife(span: Option<u64>, n: usize) -> Option<f64> {
    match (span, n) {
        (Some(s), n) if n > 0 => Some((s as f64 / n as f64 * 10.0).round() / 10.0),
        _ => None,
    }
}

/// `YYYY-MM-DDTHH:MM:SSZ` of a Unix time (civil-from-days, no time zone).
pub fn iso_utc(t: i64) -> String {
    let days = t.div_euclid(86_400);
    let secs = t.rem_euclid(86_400);
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        secs / 3600,
        secs % 3600 / 60,
        secs % 60
    )
}

/// One ledger row per knife (aib-cireuse `ez2-reuse-ledger.v1`, its own `unit` space).
pub fn ledger_rows(
    judged: &[Judged],
    o: &Opts,
    tree: Option<&str>,
    at: &str,
    each: Option<f64>,
) -> Vec<Value> {
    judged
        .iter()
        .map(|j| {
            json!({
                "schema": LEDGER_SCHEMA,
                "kind": KIND_PREDICTION,
                "producer": PRODUCER,
                "mode": "shadow",
                "repo": o.repo,
                "head_sha": o.head,
                "tree": tree,
                "at": at,
                "main_created_at": Value::Null,
                "unit": format!("knife:{}/{}", j.krate, j.name),
                "key": j.name,
                "could_save_kind": COULD_SAVE_KIND,
                "check": o.check,
                "verdict": if j.affected { "cannot_reuse" } else { "could_reuse" },
                "result": Value::Null,
                "rerun_why": [{"input": "reason", "new": j.why}],
                "reused_from": Value::Null,
                "could_save_s": if j.affected { Some(0.0) } else { each },
                "spent_s": each,
                "runner": Value::Null,
                "run_id": o.run_id,
                "base_sha": o.base,
                "knife_at": j.at,
            })
        })
        .collect()
}

/// The Markdown summary.
pub fn summary(o: &Opts, all: Option<&str>, judged: &[Judged], each: Option<f64>) -> String {
    let n = judged.len();
    let hit = judged.iter().filter(|j| j.affected).count();
    let free = n - hit;
    let mut s = format!(
        "### knife-affected (shadow: nothing skipped, history only)\n\n{}: `{}`…`{}`: **{hit} of {n}** knives affected, {free} not.\n",
        if o.check.is_empty() { "knives" } else { o.check.as_str() },
        o.base.get(..12).unwrap_or(&o.base),
        o.head.get(..12).unwrap_or(&o.head),
    );
    if let Some(e) = each {
        s.push_str(&format!(
            "Could save at most {:.0} s ({free} × {e} s a knife; {COULD_SAVE_KIND}).\n",
            free as f64 * e
        ));
    }
    if let Some(w) = all {
        s.push_str(&format!("Every knife affected: {w}.\n"));
    } else {
        for j in judged.iter().filter(|j| j.affected).take(10) {
            s.push_str(&format!("- `{}` {}: {}\n", j.name, j.at, j.why));
        }
        if hit > 10 {
            s.push_str(&format!("- … and {} more\n", hit - 10));
        }
    }
    s
}

fn read_lines(path: &str) -> Result<Vec<String>, String> {
    std::fs::read_to_string(path)
        .map(|t| {
            t.lines()
                .filter(|l| !l.is_empty())
                .map(String::from)
                .collect()
        })
        .map_err(|e| format!("{path}: {e}"))
}

fn git(root: &Path, args: &[&str]) -> Result<String, String> {
    match std::process::Command::new("git")
        .arg("-C")
        .arg(root)
        .args(args)
        .output()
    {
        Ok(o) if o.status.success() => Ok(String::from_utf8_lossy(&o.stdout).into_owned()),
        Ok(o) => Err(format!("git {} rc {:?}", args.join(" "), o.status.code())),
        Err(e) => Err(format!("git: {e}")),
    }
}

fn metadata(root: &Path, file: Option<&str>) -> Result<Value, String> {
    let text = match file {
        Some(f) => std::fs::read_to_string(f).map_err(|e| format!("{f}: {e}"))?,
        None => {
            let o = std::process::Command::new(
                std::env::var("CARGO").unwrap_or_else(|_| "cargo".into()),
            )
            .args([
                "metadata",
                "--no-deps",
                "--format-version",
                "1",
                "--offline",
            ])
            .current_dir(root)
            .output()
            .map_err(|e| format!("cargo metadata: {e}"))?;
            if !o.status.success() {
                return Err(format!("cargo metadata rc {:?}", o.status.code()));
            }
            String::from_utf8_lossy(&o.stdout).into_owned()
        }
    };
    serde_json::from_str(&text).map_err(|e| format!("metadata JSON: {e}"))
}

pub fn run(o: &Opts) -> u8 {
    let root = Path::new(&o.root);
    let (_, knives, mut unresolved) = knife_anchors::collect(root);
    let (all, bad) = knife_spec::entries(&knives);
    unresolved.extend(bad);
    let picked = select(&all, o);
    let meta = metadata(root, o.metadata.as_deref());
    let changed = match (&o.changed, o.base.is_empty() || o.head.is_empty()) {
        (Some(f), _) => read_lines(f),
        (None, true) => Err("no base / head".into()),
        (None, false) => git(
            root,
            &[
                "-c",
                "core.quotePath=false",
                "diff",
                "--no-renames",
                "--name-only",
                &format!("{}...{}", o.base, o.head),
            ],
        )
        .map(|t| {
            t.lines()
                .filter(|l| !l.is_empty())
                .map(String::from)
                .collect()
        }),
    };
    let inputs = Inputs {
        event: &o.event,
        base: &o.base,
        head: &o.head,
        meta: meta.as_ref().map_err(Clone::clone),
        changed: changed.as_deref().map_err(Clone::clone),
        unresolved: unresolved.len(),
    };
    let code = why_all_code(&inputs).map(|(c, _)| c);
    let (why, judged) = judge(&picked, &inputs, root);
    let each = per_knife(o.span_seconds, judged.len());
    print!("{}", summary(o, why.as_deref(), &judged, each));
    // segment 1 (S66-4): only the gate step asks for a skip; without --skip-out nothing is
    // skipped and the line says skipped=0
    let skip = match (&o.skip_out, called_steps(root)) {
        (Some(_), Ok(called)) => skip_units(&picked, &judged, why.as_deref(), &called),
        (Some(_), Err(e)) => {
            eprintln!("knife-affected: workflow steps not read ({e}); nothing is skipped");
            Skip::default()
        }
        (None, _) => Skip::default(),
    };
    println!("{}", affected_line(o, code, judged.len(), skip.knives));
    if let Some(f) = &o.skip_out {
        let w = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(f)
            .and_then(|mut h| h.write_all(skip_output(&skip).as_bytes()));
        if let Err(e) = w {
            // the gate's output stays empty, so every knife runs
            eprintln!("knife-affected: skip output {f}: {e}");
            return 1;
        }
    }
    let Some(path) = &o.ledger else {
        return 0;
    };
    let tree = git(root, &["rev-parse", &format!("{}^{{tree}}", o.head)])
        .ok()
        .map(|t| t.trim().to_string());
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs() as i64);
    let rows = ledger_rows(&judged, o, tree.as_deref(), &iso_utc(now), each);
    let write = || -> std::io::Result<()> {
        let mut f = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)?;
        for r in &rows {
            writeln!(f, "{r}")?;
        }
        Ok(())
    };
    match write() {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("knife-affected: ledger {path}: {e}");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::knife_anchors::Knife;

    fn v(s: &[&str]) -> Vec<String> {
        s.iter().map(|x| x.to_string()).collect()
    }

    /// a ← b (normal), a → build dep c, a dev-dep d; b dev-dep e (not a's closure).
    fn meta() -> Value {
        let dep = |n: &str, kind: Value| json!({"name": n, "path": format!("/w/crates/{n}"), "kind": kind});
        let pkg = |n: &str, deps: Vec<Value>| json!({"name": n, "manifest_path": format!("/w/crates/{n}/Cargo.toml"), "dependencies": deps});
        json!({
            "workspace_root": "/w",
            "packages": [
                pkg("a", vec![dep("b", Value::Null), dep("c", json!("build")), dep("d", json!("dev")),
                              json!({"name": "serde", "path": null, "kind": null})]),
                pkg("b", vec![dep("e", json!("dev"))]),
                pkg("c", vec![dep("f", Value::Null)]),
                pkg("d", vec![]),
                pkg("e", vec![]),
                pkg("f", vec![]),
                pkg("g", vec![]),
                json!({"name": "root", "manifest_path": "/w/Cargo.toml", "dependencies": []}),
                pkg("h", vec![dep("outside", Value::Null)]),
            ]
        })
    }

    fn entry(name: &str, file: &str, cmd: &[&str]) -> Entry {
        Entry {
            name: name.into(),
            knife: Knife {
                at: "crates/a/knives/k.sh:3".into(),
                file: file.into(),
                old: "o".into(),
                job: String::new(),
                step: String::new(),
                argv: Ok(vec![]),
            },
            call: knife_spec::Call {
                file: file.into(),
                old: "o".into(),
                new: "n".into(),
                expect_fail: "t".into(),
                cmd: v(cmd),
            },
        }
    }

    fn judge_one(e: &Entry, changed: &[&str]) -> Judged {
        let m = meta();
        let c = v(changed);
        let i = Inputs {
            event: "pull_request",
            base: "b",
            head: "h",
            meta: Ok(&m),
            changed: Ok(&c),
            unresolved: 0,
        };
        judge(&[e], &i, Path::new("/nonexistent-aib-xtask"))
            .1
            .remove(0)
    }

    fn knife_a() -> Entry {
        entry(
            "ka",
            "crates/a/src/x.rs",
            &["cargo", "test", "-p", "a", "--locked", "t"],
        )
    }

    #[test]
    fn a_change_in_the_closure_affects_and_outside_does_not() {
        let k = knife_a();
        for f in [
            "crates/a/src/y.rs",
            "crates/b/src/lib.rs",
            "crates/c/build.rs.txt",
            "crates/f/src/lib.rs",
            "crates/d/src/lib.rs",
        ] {
            let j = judge_one(&k, &[f]);
            assert!(j.affected, "{f}: {j:?}");
            assert!(j.why.contains("in the closure of a"), "{f}: {}", j.why);
        }
        // e is only b's dev-dependency, g nobody's; docs are outside every crate
        for f in ["crates/e/src/lib.rs", "crates/g/src/lib.rs", "docs/x.md"] {
            let j = judge_one(&k, &[f]);
            assert!(!j.affected, "{f}: {j:?}");
        }
        assert_eq!(judge_one(&k, &[]).krate, "a");
    }

    #[test]
    fn closure_folders_are_the_path_dependencies() {
        let m = meta();
        let d = closure(&m, "a", Path::new("/nonexistent-aib-xtask")).unwrap();
        assert_eq!(
            d,
            v(&[
                "crates/a/",
                "crates/b/",
                "crates/c/",
                "crates/d/",
                "crates/f/"
            ])
        );
        let d = closure(&m, "b", Path::new("/nonexistent-aib-xtask")).unwrap();
        assert_eq!(d, v(&["crates/b/", "crates/e/"]));
        // a crate at the workspace root would make every path its own
        let e = closure(&m, "root", Path::new("/nonexistent-aib-xtask")).unwrap_err();
        assert!(e.contains("workspace root"), "{e}");
    }

    #[test]
    fn its_anchor_file_or_script_changing_affects_it() {
        // the anchor sits outside the tested crate's closure
        let k = entry(
            "kg",
            "crates/g/src/x.rs",
            &["cargo", "test", "-p", "a", "--locked"],
        );
        let j = judge_one(&k, &["crates/g/src/x.rs"]);
        assert!(j.affected && j.why.contains("anchor file"), "{j:?}");
        let mut k = knife_a();
        k.knife.at = "ops/knives.sh:9".into();
        let j = judge_one(&k, &["ops/knives.sh"]);
        assert!(j.affected && j.why.contains("where it is written"), "{j:?}");
    }

    #[test]
    fn the_fixed_set_affects_every_knife() {
        for f in [
            "Cargo.lock",
            "Cargo.toml",
            "scripts/knife.py",
            "xtask/src/main.rs",
            ".github/workflows/aibd.yml",
            ".cargo/config.toml",
            "rust-toolchain.toml",
            "aib/tools/release/docs_only.py",
        ] {
            let j = judge_one(&knife_a(), &[f]);
            assert!(j.affected && j.why.contains("fixed set"), "{f}: {j:?}");
        }
        // only the exact file, or inside a listed folder
        let j = judge_one(&knife_a(), &["crates/g/Cargo.toml", "scriptsx/a"]);
        assert!(!j.affected, "{j:?}");
    }

    #[test]
    fn what_is_not_a_pull_request_affects_every_knife() {
        let m = meta();
        let c: Vec<String> = vec![];
        for ev in ["push", "", "workflow_dispatch"] {
            let i = Inputs {
                event: ev,
                base: "b",
                head: "h",
                meta: Ok(&m),
                changed: Ok(&c),
                unresolved: 0,
            };
            let (all, j) = judge(&[&knife_a()], &i, Path::new("."));
            assert!(all.is_some() && j[0].affected, "{ev}: {all:?}");
        }
    }

    #[test]
    fn unreadable_inputs_affect_every_knife() {
        let m = meta();
        let none: Vec<String> = vec![];
        let quoted = v(&["\"crates/g/a\\tb.rs\""]);
        let cases: Vec<(&str, Inputs)> = vec![
            (
                "no base",
                Inputs {
                    event: "pull_request",
                    base: "",
                    head: "h",
                    meta: Ok(&m),
                    changed: Ok(&none),
                    unresolved: 0,
                },
            ),
            (
                "no head",
                Inputs {
                    event: "pull_request",
                    base: "b",
                    head: "",
                    meta: Ok(&m),
                    changed: Ok(&none),
                    unresolved: 0,
                },
            ),
            (
                "metadata",
                Inputs {
                    event: "pull_request",
                    base: "b",
                    head: "h",
                    meta: Err("x".into()),
                    changed: Ok(&none),
                    unresolved: 0,
                },
            ),
            (
                "diff",
                Inputs {
                    event: "pull_request",
                    base: "b",
                    head: "h",
                    meta: Ok(&m),
                    changed: Err("x".into()),
                    unresolved: 0,
                },
            ),
            (
                "quoted",
                Inputs {
                    event: "pull_request",
                    base: "b",
                    head: "h",
                    meta: Ok(&m),
                    changed: Ok(&quoted),
                    unresolved: 0,
                },
            ),
            (
                "contract",
                Inputs {
                    event: "pull_request",
                    base: "b",
                    head: "h",
                    meta: Ok(&m),
                    changed: Ok(&none),
                    unresolved: 1,
                },
            ),
        ];
        for (what, i) in cases {
            let (all, j) = judge(&[&knife_a()], &i, Path::new("."));
            assert!(all.is_some() && j[0].affected, "{what}: {all:?} {j:?}");
        }
    }

    #[test]
    fn a_command_not_read_affects_its_knife() {
        for cmd in [
            &["python3", "-m", "pytest"][..],
            &["cross", "test", "-p", "g"],
            &[
                "cargo",
                "test",
                "-p",
                "g",
                "--manifest-path",
                "x/Cargo.toml",
            ],
            &["cargo", "test", "--workspace"],
            &["cargo", "test", "--all"],
            &["cargo", "test", "--locked", "t"],
            &["cargo", "test", "--manifest-path", "crates/g/Cargo.toml"],
            &["cargo", "test", "-p", "nope"],
            &["cargo", "test", "-p", "root"],
            &["cargo", "test", "-p", "h"],
            &["cargo", "test", "-p"],
        ] {
            let k = entry("kx", "crates/g/src/x.rs", cmd);
            let j = judge_one(&k, &["docs/x.md"]);
            assert!(j.affected, "{cmd:?}: {j:?}");
        }
    }

    #[test]
    fn cmd_packages_reads_the_p_forms() {
        let p = |c: &[&str]| cmd_packages(&v(c));
        assert_eq!(p(&["cargo", "test", "-p", "a"]).unwrap(), v(&["a"]));
        assert_eq!(p(&["cargo", "test", "--package", "a"]).unwrap(), v(&["a"]));
        assert_eq!(p(&["cargo", "test", "--package=a"]).unwrap(), v(&["a"]));
        assert_eq!(
            p(&["cargo", "test", "-pa", "-p", "b"]).unwrap(),
            v(&["a", "b"])
        );
        // after `--` the words go to the test binary
        assert!(p(&["cargo", "test", "--", "-p", "a"]).is_err());
        assert!(p(&["cargo", "test", "-p", "a", "--workspace"]).is_err());
    }

    #[test]
    fn an_unvetted_build_rs_affects_its_knife() {
        let d = std::env::temp_dir().join(format!("aib-xtask-ka-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(d.join("crates/f")).unwrap();
        std::fs::write(d.join("crates/f/build.rs"), "fn main() {}\n").unwrap();
        let m = meta();
        let e = closure(&m, "a", &d).unwrap_err();
        assert!(e.contains("crates/f/build.rs"), "{e}");
        // g does not reach f
        assert!(closure(&m, "g", &d).is_ok());
        let c = v(&["docs/x.md"]);
        let i = Inputs {
            event: "pull_request",
            base: "b",
            head: "h",
            meta: Ok(&m),
            changed: Ok(&c),
            unresolved: 0,
        };
        let (_, j) = judge(&[&knife_a()], &i, &d);
        assert!(j[0].affected && j[0].why.contains("not vetted"), "{j:?}");
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn select_by_job_source_and_dropped_steps() {
        let mk = |job: &str, step: &str, at: &str| {
            let mut e = knife_a();
            e.knife.job = job.into();
            e.knife.step = step.into();
            e.knife.at = at.into();
            e
        };
        let all = vec![
            mk("rust", "x knives (macOS)", ".github/workflows/aibd.yml:10"),
            mk(
                "rust",
                "y knives (Windows)",
                ".github/workflows/aibd.yml:20",
            ),
            mk("", "", "scripts/knives-web.sh:4"),
            mk("other", "z", ".github/workflows/aibd.yml:30"),
        ];
        let o = Opts {
            jobs: v(&["rust"]),
            sources: v(&["scripts/knives-web.sh"]),
            drop_steps: v(&["(Windows)"]),
            ..Opts::default()
        };
        let got: Vec<&str> = select(&all, &o)
            .iter()
            .map(|e| e.knife.at.as_str())
            .collect();
        assert_eq!(
            got,
            [".github/workflows/aibd.yml:10", "scripts/knives-web.sh:4"]
        );
        assert_eq!(select(&all, &Opts::default()).len(), 4);
    }

    #[test]
    fn ledger_rows_stay_out_of_the_cireuse_units() {
        let j = vec![
            judge_one(&knife_a(), &["docs/x.md"]),
            judge_one(&knife_a(), &["crates/a/src/x.rs"]),
        ];
        let o = Opts {
            base: "b".into(),
            head: "h".into(),
            check: "cloud-host command knives (macOS)".into(),
            run_id: Some(7),
            ..Opts::default()
        };
        let each = per_knife(Some(90), 2);
        assert_eq!(each, Some(45.0));
        let r = ledger_rows(&j, &o, Some("t"), "2026-11-15T03:00:00Z", each);
        for x in &r {
            assert_eq!(x["schema"], LEDGER_SCHEMA);
            assert_eq!(x["kind"], KIND_PREDICTION);
            assert_eq!(x["producer"], PRODUCER);
            assert_eq!(x["mode"], "shadow");
            assert_eq!(x["could_save_kind"], COULD_SAVE_KIND);
            let unit = x["unit"].as_str().unwrap();
            assert!(unit.starts_with("knife:a/"), "{unit}");
        }
        assert_eq!(r[0]["verdict"], "could_reuse");
        assert_eq!(r[0]["could_save_s"], 45.0);
        assert_eq!(r[1]["verdict"], "cannot_reuse");
        assert_eq!(r[1]["could_save_s"], 0.0);
        let s = summary(&o, None, &j, each);
        assert!(
            s.contains("**1 of 2**") && s.contains("at most 45 s"),
            "{s}"
        );
    }

    #[test]
    fn what_a_crate_reads_outside_its_folder_is_in_its_closure() {
        let mut m = meta();
        for (n, dir) in [("aib-store", "aib-store"), ("aib-cloud", "aib-cloud")] {
            m["packages"].as_array_mut().unwrap().push(json!({
                "name": n, "manifest_path": format!("/w/crates/{dir}/Cargo.toml"), "dependencies": []
            }));
        }
        let none = Path::new("/nonexistent-aib-xtask");
        let d = closure(&m, "aib-store", none).unwrap();
        assert_eq!(d, v(&["crates/aib-store/", "migrations/"]));
        let k = entry(
            "ks",
            "crates/aib-store/src/x.rs",
            &["cargo", "test", "-p", "aib-store"],
        );
        let c = v(&["migrations/0006_x.sql"]);
        let i = Inputs {
            event: "pull_request",
            base: "b",
            head: "h",
            meta: Ok(&m),
            changed: Ok(&c),
            unresolved: 0,
        };
        let j = judge(&[&k], &i, none).1.remove(0);
        assert!(j.affected, "{j:?}");
        // `*` is one segment: another crate's manifest, not a file deeper down
        assert!(covers("crates/*/Cargo.toml", "crates/aib-x/Cargo.toml"));
        assert!(!covers(
            "crates/*/Cargo.toml",
            "crates/aib-x/src/Cargo.toml"
        ));
        assert!(!covers("crates/*/Cargo.toml", "crates/aib-x/Cargo.toml/x"));
        assert!(covers("migrations/", "migrations/a.sql"));
        assert!(!covers("docs/perf/timers.md", "docs/perf/timers.md.bak"));
    }

    /// Every crate whose src / tests open a path outside its folder is on EXTRA_INPUTS (or
    /// reads only a path dependency): a new outside read must be listed before the shadow
    /// may count its knives as skippable.
    #[test]
    fn the_repo_crates_read_outside_only_what_is_listed() {
        /// Crates that read outside their folder only from crates they already depend on by path.
        const OUTSIDE_IS_A_DEP: &[&str] = &["aib-reflect"];
        fn walk(d: &Path, out: &mut Vec<std::path::PathBuf>) {
            let Ok(rd) = std::fs::read_dir(d) else {
                return;
            };
            for e in rd.flatten() {
                let p = e.path();
                if p.is_dir() {
                    walk(&p, out);
                } else if p.extension().is_some_and(|x| x == "rs") {
                    out.push(p);
                }
            }
        }
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
        let mut found = BTreeSet::new();
        for c in std::fs::read_dir(root.join("crates")).unwrap().flatten() {
            let name = c.file_name().to_string_lossy().into_owned();
            let mut files = Vec::new();
            walk(&c.path().join("src"), &mut files);
            walk(&c.path().join("tests"), &mut files);
            for f in files {
                let text = std::fs::read_to_string(&f).unwrap_or_default();
                let lines: Vec<&str> = text.lines().collect();
                for (i, l) in lines.iter().enumerate() {
                    let next = lines.get(i + 1).copied().unwrap_or("");
                    let outside = l.contains("include_str!(\"../../")
                        || l.contains("include_bytes!(\"../../")
                        || (l.contains("CARGO_MANIFEST_DIR")
                            && (l.contains("/..") || l.contains("\"..") || next.contains("\"../")));
                    if outside {
                        found.insert(name.clone());
                    }
                }
            }
        }
        assert!(found.len() >= 5, "only {found:?}: the scan is broken");
        for c in &found {
            assert!(
                EXTRA_INPUTS.iter().any(|(n, _)| n == c) || OUTSIDE_IS_A_DEP.contains(&c.as_str()),
                "{c} reads outside its folder but is not on EXTRA_INPUTS"
            );
        }
    }

    #[test]
    fn iso_utc_is_civil_time() {
        assert_eq!(iso_utc(0), "1970-01-01T00:00:00Z");
        assert_eq!(iso_utc(951_782_400), "2000-02-29T00:00:00Z");
        assert_eq!(iso_utc(1_794_711_699), "2026-11-15T03:01:39Z");
    }

    #[test]
    fn args_are_read() {
        let o = parse_args(&v(&[
            "--event",
            "pull_request",
            "--base",
            "b",
            "--head",
            "h",
            "--job",
            "rust",
            "--source",
            "s.sh",
            "--span-seconds",
            "12",
            "r",
        ]))
        .unwrap();
        assert_eq!(
            (o.jobs, o.sources, o.span_seconds),
            (v(&["rust"]), v(&["s.sh"]), Some(12))
        );
        assert_eq!(o.root, "r");
        assert!(parse_args(&v(&["--span-seconds", "x"])).is_err());
        assert!(parse_args(&v(&["--bogus"])).is_err());
    }

    fn placed(name: &str, at: &str, step: &str) -> Entry {
        let mut e = knife_a();
        e.name = name.into();
        e.knife.at = at.into();
        e.knife.step = step.into();
        e
    }

    fn judged(name: &str, affected: bool) -> Judged {
        Judged {
            name: name.into(),
            at: String::new(),
            krate: "a".into(),
            affected,
            why: String::new(),
        }
    }

    #[test]
    fn a_step_or_script_is_skipped_only_when_none_of_its_knives_is_affected() {
        let es = [
            placed("s1", ".github/workflows/aibd.yml:10", "x knives (macOS)"),
            placed("s2", ".github/workflows/aibd.yml:12", "x knives (macOS)"),
            placed("t1", ".github/workflows/aibd.yml:20", "y knives (macOS)"),
            placed("t2", ".github/workflows/aibd.yml:22", "y knives (macOS)"),
            placed("f1", "crates/a/knives/knives-01-a.sh:3", ""),
            placed("g1", "crates/b/knives/knives-01-b.sh:3", ""),
            placed("g2", "crates/b/knives/knives-01-b.sh:9", ""),
        ];
        let refs: Vec<&Entry> = es.iter().collect();
        let js = [
            judged("s1", false),
            judged("s2", false),
            judged("t1", false),
            judged("t2", true),
            judged("f1", false),
            judged("g1", true),
            judged("g2", false),
        ];
        let none = BTreeSet::new();
        let k = skip_units(&refs, &js, None, &none);
        assert_eq!(k.steps, v(&["x knives (macOS)"]));
        assert_eq!(k.files, v(&["crates/a/knives/knives-01-a.sh"]));
        assert_eq!(k.knives, 3);
        // one reason for every knife, or a count that does not line up: nothing is skipped
        assert_eq!(
            skip_units(&refs, &js, Some("event push is not a pull request"), &none),
            Skip::default()
        );
        assert_eq!(skip_units(&refs, &js[..6], None, &none), Skip::default());
        // a step that also calls a knife script stays, its free knives with it
        let called: BTreeSet<String> = ["x knives (macOS)".to_string()].into();
        let k = skip_units(&refs, &js, None, &called);
        assert!(k.steps.is_empty(), "{k:?}");
        assert_eq!(k.knives, 1);
    }

    #[test]
    fn every_knife_affected_has_a_code_for_the_ledger() {
        let m = meta();
        let code = |event: &str, base: &str, changed: &[&str]| {
            let c = v(changed);
            let i = Inputs {
                event,
                base,
                head: "h",
                meta: Ok(&m),
                changed: Ok(&c),
                unresolved: 0,
            };
            why_all_code(&i).map(|(c, _)| c)
        };
        assert_eq!(code("push", "b", &[]), Some("event_not_pr"));
        assert_eq!(code("pull_request", "", &[]), Some("base_unknown"));
        assert_eq!(
            code("pull_request", "b", &[".github/workflows/aibd.yml"]),
            Some("workflow_changed")
        );
        assert_eq!(
            code("pull_request", "b", &["Cargo.lock"]),
            Some("fixed_path")
        );
        assert_eq!(code("pull_request", "b", &["\"q\"x"]), Some("quoted_path"));
        assert_eq!(code("pull_request", "b", &["crates/a/src/x.rs"]), None);
        let c = v(&[]);
        let unread = Inputs {
            event: "pull_request",
            base: "b",
            head: "h",
            meta: Err("x".into()),
            changed: Ok(&c),
            unresolved: 0,
        };
        assert_eq!(
            why_all_code(&unread).map(|(c, _)| c),
            Some("metadata_unread")
        );
        assert_eq!(why_all(&unread), why_all_code(&unread).map(|(_, w)| w));
    }

    #[test]
    fn the_affected_line_and_the_skip_output_have_their_fixed_shape() {
        let o = parse_args(&v(&[
            "--base",
            "b0",
            "--head",
            "h0",
            "--run-id",
            "7",
            "--attempt",
            "2",
            "--skip-out",
            "o",
        ]))
        .expect("args");
        assert_eq!(o.skip_out.as_deref(), Some("o"));
        assert_eq!(
            affected_line(&o, None, 10, 4),
            "KNIVES-AFFECTED ran=6 of=10 skipped=4 all_why=- run=7/2 head=h0 base=b0"
        );
        assert_eq!(
            affected_line(&Opts::default(), Some("event_not_pr"), 3, 0),
            "KNIVES-AFFECTED ran=3 of=3 skipped=0 all_why=event_not_pr run=0/1 head=- base=-"
        );
        let k = Skip {
            steps: v(&["x knives (macOS)", "y"]),
            files: v(&["crates/a/knives/k.sh"]),
            knives: 3,
        };
        assert_eq!(
            skip_output(&k),
            "skip_steps=[\"x knives (macOS)\",\"y\"]\nskip_files=,crates/a/knives/k.sh,\n"
        );
        assert_eq!(
            skip_output(&Skip::default()),
            "skip_steps=[]\nskip_files=,\n"
        );
        assert!(parse_args(&v(&["--attempt", "x"])).is_err());
    }

    #[test]
    fn a_step_that_calls_a_knife_script_is_found() {
        let wf = "\
jobs:
  rust:
    steps:
      - name: a knives (macOS)
        run: |
          kf 'x' 'y' t
          ./crates/aibd/knives/knives-p11.sh
      - name: b knives (macOS)
        run: |
          # crates/aib-x/knives/knives-01.sh is named in a comment only
          kf 'x' 'y' t
      - name: c loop (macOS)
        run: for f in crates/aib-drive/knives/*.sh; do bash \"$f\"; done
      - name: d (macOS)
        run: ./scripts/knives-web.sh
      - uses: actions/cache/save@v4
        with:
          path: crates/x/knives/not-a-step.sh
";
        let got: Vec<String> = steps_calling_scripts(wf).into_iter().collect();
        assert_eq!(got, v(&["a knives (macOS)", "c loop (macOS)", "d (macOS)"]));
    }
}
