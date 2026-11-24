//! `aib-xtask knife-shards`: which crate-knives cell runs which knife script, by how long
//! each script took before (Andy 11-22: every run's time is to be used, not only printed).
//!
//! crates/aib-knife/run-knives.sh used to give the cells turns in name order. On #875 the four
//! cells then took 63 / 90 / 139 / 88 minutes over 249 scripts, the third one minute short of
//! its limit; the same scripts placed by their seconds come to 95 minutes each.
//!
//! ```text
//! aib-xtask knife-shards plan --shards N --secs FILE --list FILE
//! aib-xtask knife-shards secs LOG...
//! ```
//!
//! - **plan**: `--list` is the scripts that run in this job on this OS, one path a line (what
//!   run-knives.sh prints as `KNIVES-ELIGIBLE` with `AIB_KNIVES_LIST=1`: the runner stays the
//!   only reader of the script headers). `--secs` is the committed table, `<seconds>\t<path>` a
//!   line, `#` lines and blank lines left out. Printed: `<k>\t<path>` for every listed script,
//!   in path byte order, which run-knives.sh reads through `AIB_KNIVES_PLAN`.
//! - Every cell works the plan out for itself from the same two inputs, so the rule has no
//!   choice left in it: scripts are taken heaviest first (same seconds: path byte order) and
//!   each goes to the cell with the fewest seconds so far (same: the lowest cell number). A
//!   script the table does not know weighs the table's median (the lower one of two; an empty
//!   table: 1), so a new script needs no line here to run. A line of the table for a script
//!   that is not listed gets no cell; it still counts in that median (the median is the
//!   table's, whatever is listed).
//! - Each listed script gets exactly one cell. Nothing is skipped by this command and the
//!   table is no evidence of anything: a wrong number only makes the cells less even.
//! - **secs**: the table from run-knives.sh output (`KNIVES-SCRIPT <file>: rc=0 secs=<n>`
//!   lines, whatever stands before them on the line): per script the lower median of its
//!   green runs. Runs that were red or timed out say nothing about a normal run's time.
//!
//! rc: 0 printed; 2 usage, a file not read, a table or list line not understood, a path listed
//! or tabled twice, an empty list. Never a guess: a table that cannot be read is no table.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

/// The committed table: script path ⇒ seconds. A line that is not `<digits>\t<path>` and a
/// path that comes twice are errors naming the line.
pub fn parse_secs(text: &str) -> Result<BTreeMap<String, u64>, String> {
    let mut out = BTreeMap::new();
    for (i, line) in text.lines().enumerate() {
        if line.trim().is_empty() || line.starts_with('#') {
            continue;
        }
        let bad = || format!("line {}: not <seconds>\\t<path>: {line}", i + 1);
        let (secs, path) = line.split_once('\t').ok_or_else(bad)?;
        if secs.is_empty() || !secs.bytes().all(|b| b.is_ascii_digit()) {
            return Err(bad());
        }
        if path.is_empty() || path.contains('\t') || path != path.trim() {
            return Err(bad());
        }
        let secs: u64 = secs.parse().map_err(|_| bad())?;
        if out.insert(path.to_string(), secs).is_some() {
            return Err(format!("line {}: {path} is in the table twice", i + 1));
        }
    }
    Ok(out)
}

/// The scripts to place: one path a line, blank lines left out; a path twice is an error.
pub fn parse_list(text: &str) -> Result<Vec<String>, String> {
    let mut seen = BTreeSet::new();
    let mut out = Vec::new();
    for (i, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        if line != line.trim() || line.contains('\t') {
            return Err(format!("line {}: not a path: {line:?}", i + 1));
        }
        if !seen.insert(line.to_string()) {
            return Err(format!("line {}: {line} is listed twice", i + 1));
        }
        out.push(line.to_string());
    }
    if out.is_empty() {
        return Err("no script is listed".into());
    }
    Ok(out)
}

/// The lower median; nothing to take it of ⇒ 1.
pub fn median(values: &[u64]) -> u64 {
    if values.is_empty() {
        return 1;
    }
    let mut v = values.to_vec();
    v.sort_unstable();
    v[(v.len() - 1) / 2]
}

/// What a script weighs when the table has no line for it: the table's median.
fn unknown_secs(secs: &BTreeMap<String, u64>) -> u64 {
    median(&secs.values().copied().collect::<Vec<_>>())
}

/// Cell (1..=shards) of every listed script, in path byte order. See the module text for the rule.
pub fn plan(
    list: &[String],
    secs: &BTreeMap<String, u64>,
    shards: u32,
) -> Result<Vec<(String, u32)>, String> {
    if shards == 0 {
        return Err("--shards is 0".into());
    }
    let unknown = unknown_secs(secs);
    let mut order: Vec<(u64, &str)> = list
        .iter()
        .map(|p| (secs.get(p).copied().unwrap_or(unknown), p.as_str()))
        .collect();
    // heaviest first; the same seconds in path byte order
    order.sort_by(|a, b| b.0.cmp(&a.0).then(a.1.as_bytes().cmp(b.1.as_bytes())));
    let mut load = vec![0u64; shards as usize];
    let mut out: Vec<(String, u32)> = Vec::with_capacity(order.len());
    for (w, path) in order {
        let mut k = 0;
        for (i, l) in load.iter().enumerate() {
            if *l < load[k] {
                k = i;
            }
        }
        load[k] = load[k].saturating_add(w);
        out.push((path.to_string(), k as u32 + 1));
    }
    out.sort_by(|a, b| a.0.as_bytes().cmp(b.0.as_bytes()));
    Ok(out)
}

/// Seconds every cell gets under `plan` (for the line printed beside the plan).
pub fn loads(plan: &[(String, u32)], secs: &BTreeMap<String, u64>, shards: u32) -> Vec<u64> {
    let unknown = unknown_secs(secs);
    let mut load = vec![0u64; shards as usize];
    for (p, k) in plan {
        let l = &mut load[*k as usize - 1];
        *l = l.saturating_add(secs.get(p).copied().unwrap_or(unknown));
    }
    load
}

/// `KNIVES-SCRIPT <file>: rc=0 secs=<n>` lines of run-knives.sh output ⇒ per script the lower
/// median of its green runs. Other lines, red runs and timed-out runs are not read.
pub fn secs_from_logs(texts: &[String]) -> BTreeMap<String, u64> {
    let mut runs: BTreeMap<String, Vec<u64>> = BTreeMap::new();
    for text in texts {
        for line in text.lines() {
            let Some((_, rest)) = line.split_once("KNIVES-SCRIPT ") else {
                continue;
            };
            let Some((file, tail)) = rest.split_once(": rc=0 secs=") else {
                continue;
            };
            let tail = tail.trim_end();
            if file.is_empty() || tail.is_empty() || !tail.bytes().all(|b| b.is_ascii_digit()) {
                continue;
            }
            if let Ok(n) = tail.parse::<u64>() {
                runs.entry(file.to_string()).or_default().push(n);
            }
        }
    }
    runs.into_iter().map(|(f, v)| (f, median(&v))).collect()
}

/// A script has grown when it took more than this many times its seconds in the table…
pub const GREW_TIMES: u64 = 2;
/// …and at least this many seconds more (a 3 s script that took 8 s has not grown).
pub const GREW_MORE_S: u64 = 60;

/// One script that is not where the table has it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Grew {
    /// In the table at `table` seconds, and far over it now.
    Over { file: String, table: u64, now: u64 },
    /// Not in the table (it is placed as a median script) and heavy now.
    NewHeavy { file: String, now: u64 },
}

/// The scripts of `now` (this run's seconds) the plan was wrong about: the cells were laid
/// out from `table`, so each of these made its cell longer than planned. A script that got
/// faster, and a light new one, are not said.
pub fn grew(table: &BTreeMap<String, u64>, now: &BTreeMap<String, u64>) -> Vec<Grew> {
    now.iter()
        .filter_map(|(file, &n)| match table.get(file) {
            Some(&t) if n > t.saturating_mul(GREW_TIMES) && n >= t.saturating_add(GREW_MORE_S) => {
                Some(Grew::Over {
                    file: file.clone(),
                    table: t,
                    now: n,
                })
            }
            None if n >= GREW_MORE_S => Some(Grew::NewHeavy {
                file: file.clone(),
                now: n,
            }),
            _ => None,
        })
        .collect()
}

const LIMIT_MIN_MINS: u64 = 5;
const LIMIT_MAX_MINS: u64 = 240;

/// Timeout limit in minutes from historical median seconds: 2x + 2m buffer, clamped to [5, 240].
pub fn limit_mins(secs: u64) -> u64 {
    secs.saturating_mul(2)
        .saturating_add(120)
        .div_ceil(60)
        .clamp(LIMIT_MIN_MINS, LIMIT_MAX_MINS)
}

/// `<mins>\t<path>` timeout limits from `table`, in path order. Scripts absent from `table` are omitted
/// so the runner keeps their header/default timeout.
pub fn limits(table: &BTreeMap<String, u64>) -> Vec<(String, u64)> {
    table
        .iter()
        .map(|(file, &secs)| (file.clone(), limit_mins(secs)))
        .collect()
}

/// A single knife script must stay under 15 minutes (900s, leaving 2x headroom for slow runners under 30m) and 140 knives.
pub const MAX_SCRIPT_SECS: u64 = 900;
pub const MAX_SCRIPT_KNIVES: usize = 140;
/// No shard should exceed 140 minutes (8400s) of projected work.
pub const MAX_SHARD_SECS: u64 = 8400;

#[derive(Debug, Default, PartialEq, Eq)]
pub struct SizeSummary {
    pub scripts: usize,
    pub max_secs: u64,
    pub max_knives: usize,
    pub max_shard_secs: u64,
    pub violations: Vec<String>,
}

/// Check per-script knife count, historical seconds, and projected shard load.
/// When `only` is `Some(paths)`, per-script checks apply only to scripts in `paths`
/// (so unrelated PRs are not blocked by pre-existing grandfathered scripts).
pub fn check_size(
    knives: &[crate::knife_anchors::Knife],
    secs: &BTreeMap<String, u64>,
    shards: u32,
    max_secs: u64,
    max_knives: usize,
    max_shard_secs: u64,
    only: Option<&BTreeSet<String>>,
) -> Result<SizeSummary, String> {
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    let mut non_crate_job: BTreeSet<String> = BTreeSet::new();
    for k in knives {
        let file = crate::knife_precheck::written_in(&k.at);
        if file.ends_with(".sh") {
            *counts.entry(file.to_string()).or_default() += 1;
            if k.job == "cloud-knives" || k.job == "cloud-host-knives" {
                non_crate_job.insert(file.to_string());
            }
        }
    }
    let mut summary = SizeSummary {
        scripts: counts.len(),
        ..SizeSummary::default()
    };
    for (file, &count) in &counts {
        if only.is_some_and(|set| !set.contains(file)) {
            continue;
        }
        summary.max_knives = summary.max_knives.max(count);
        if count > max_knives {
            summary.violations.push(format!(
                "{file}: {count} knives (max {max_knives}; split into smaller scripts)"
            ));
        }
        if let Some(&s) = secs.get(file) {
            summary.max_secs = summary.max_secs.max(s);
            if s > max_secs {
                summary.violations.push(format!(
                    "{file}: {s}s in secs table (max {max_secs}s / {}m; split into smaller scripts)",
                    max_secs / 60
                ));
            }
        } else if only.is_some()
            && !secs.is_empty()
            && file.starts_with("crates/")
            && !non_crate_job.contains(file)
        {
            summary.violations.push(format!(
                "{file}: missing from crates/aib-knife/knife-secs.tsv (add an estimated/measured seconds row so shard planning does not fall back to median)"
            ));
        }
    }
    let list: Vec<String> = counts.keys().cloned().collect();
    if !list.is_empty() {
        let p = plan(&list, secs, shards)?;
        let l = loads(&p, secs, shards);
        for (i, &load) in l.iter().enumerate() {
            summary.max_shard_secs = summary.max_shard_secs.max(load);
            if load > max_shard_secs {
                summary.violations.push(format!(
                    "shard {}/{shards}: {load}s projected (max {max_shard_secs}s / {}m)",
                    i + 1,
                    max_shard_secs / 60
                ));
            }
        }
    }
    Ok(summary)
}

const USAGE: &str = "usage: aib-xtask knife-shards plan --shards N --secs FILE --list FILE\n       aib-xtask knife-shards secs LOG...\n       aib-xtask knife-shards grew --secs FILE LOG...\n       aib-xtask knife-shards limits --secs FILE\n       aib-xtask knife-shards check [--secs FILE] [--shards N] [ROOT]\n       aib-xtask knife-shards summary --tier <strict|normal|fast> [--passed N] [--deferred N] [--failed N] [--timed-out N] [--cancelled N]\n       aib-xtask knife-shards queue [--repo OWNER/NAME]";

fn read(path: &str) -> Result<String, String> {
    std::fs::read_to_string(Path::new(path)).map_err(|e| format!("{path}: {e}"))
}

fn plan_cmd(args: &[String]) -> Result<String, String> {
    let (mut shards, mut secs, mut list) = (None, None, None);
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let slot = match a.as_str() {
            "--shards" => &mut shards,
            "--secs" => &mut secs,
            "--list" => &mut list,
            other => return Err(format!("{other}: not an option\n{USAGE}")),
        };
        if slot.is_some() {
            return Err(format!("{a} is given twice\n{USAGE}"));
        }
        *slot = Some(
            it.next()
                .ok_or_else(|| format!("{a} needs a value\n{USAGE}"))?,
        );
    }
    let (Some(shards), Some(secs), Some(list)) = (shards, secs, list) else {
        return Err(USAGE.into());
    };
    let n: u32 = match shards.parse() {
        Ok(n) if n >= 1 && shards.bytes().all(|b| b.is_ascii_digit()) => n,
        _ => return Err(format!("--shards {shards}: not a whole number from 1")),
    };
    let table = parse_secs(&read(secs)?).map_err(|e| format!("{secs}: {e}"))?;
    let scripts = parse_list(&read(list)?).map_err(|e| format!("{list}: {e}"))?;
    let p = plan(&scripts, &table, n)?;
    let known = scripts.iter().filter(|s| table.contains_key(*s)).count();
    let l = loads(&p, &table, n);
    eprintln!(
        "KNIVES-PLAN shards={n} scripts={} known={known} secs={}",
        p.len(),
        l.iter().map(u64::to_string).collect::<Vec<_>>().join(",")
    );
    Ok(p.iter().map(|(f, k)| format!("{k}\t{f}\n")).collect())
}

fn secs_cmd(args: &[String]) -> Result<String, String> {
    if args.is_empty() || args.iter().any(|a| a.starts_with("--")) {
        return Err(USAGE.into());
    }
    let texts = args
        .iter()
        .map(|a| read(a))
        .collect::<Result<Vec<_>, _>>()?;
    let t = secs_from_logs(&texts);
    if t.is_empty() {
        return Err("no `KNIVES-SCRIPT <file>: rc=0 secs=<n>` line in the logs".into());
    }
    Ok(t.iter().map(|(f, s)| format!("{s}\t{f}\n")).collect())
}

/// `grew --secs FILE LOG...`: one line per script the table is wrong about, then the count.
/// Always rc 0 when the files were read: it says, it does not stop anything.
fn grew_cmd(args: &[String]) -> Result<String, String> {
    let [flag, table, logs @ ..] = args else {
        return Err(USAGE.into());
    };
    if flag != "--secs" || logs.is_empty() || logs.iter().any(|a| a.starts_with("--")) {
        return Err(USAGE.into());
    }
    let table = parse_secs(&read(table)?).map_err(|e| format!("{table}: {e}"))?;
    let texts = logs
        .iter()
        .map(|a| read(a))
        .collect::<Result<Vec<_>, _>>()?;
    let now = secs_from_logs(&texts);
    let found = grew(&table, &now);
    let mut out = String::new();
    for g in &found {
        match g {
            Grew::Over { file, table, now } => {
                out.push_str(&format!("KNIVES-GREW {file} table={table} now={now}\n"))
            }
            Grew::NewHeavy { file, now } => {
                out.push_str(&format!("KNIVES-NEW-HEAVY {file} now={now}\n"))
            }
        }
    }
    out.push_str(&format!(
        "KNIVES-GREW-CHECK scripts={} off_the_table={}\n",
        now.len(),
        found.len()
    ));
    Ok(out)
}

fn limits_cmd(args: &[String]) -> Result<String, String> {
    let [flag, table] = args else {
        return Err(USAGE.into());
    };
    if flag != "--secs" || table.starts_with("--") {
        return Err(USAGE.into());
    }
    let table = parse_secs(&read(table)?).map_err(|e| format!("{table}: {e}"))?;
    Ok(limits(&table)
        .into_iter()
        .map(|(f, m)| format!("{m}\t{f}\n"))
        .collect())
}

fn check_cmd(args: &[String]) -> Result<(u8, String), String> {
    let (mut secs, mut shards, mut root) = (None, 4u32, None);
    let (mut base, mut head) = (None, None);
    let (mut max_secs, mut max_knives, mut max_shard_secs) =
        (MAX_SCRIPT_SECS, MAX_SCRIPT_KNIVES, MAX_SHARD_SECS);
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let val = |flag: &str, it: &mut std::slice::Iter<'_, String>| -> Result<String, String> {
            it.next()
                .cloned()
                .ok_or_else(|| format!("{flag} needs a value\n{USAGE}"))
        };
        match a.as_str() {
            "--secs" => secs = Some(val(a, &mut it)?),
            "--base" => base = Some(val(a, &mut it)?),
            "--head" => head = Some(val(a, &mut it)?),
            "--shards" => {
                let v = val(a, &mut it)?;
                shards = v
                    .parse()
                    .ok()
                    .filter(|&n| n >= 1)
                    .ok_or_else(|| format!("--shards {v}: not a whole number from 1"))?;
            }
            "--max-secs" => {
                let v = val(a, &mut it)?;
                max_secs = v
                    .parse()
                    .map_err(|_| format!("--max-secs {v}: not a number"))?;
            }
            "--max-knives" => {
                let v = val(a, &mut it)?;
                max_knives = v
                    .parse()
                    .map_err(|_| format!("--max-knives {v}: not a number"))?;
            }
            "--max-shard-secs" => {
                let v = val(a, &mut it)?;
                max_shard_secs = v
                    .parse()
                    .map_err(|_| format!("--max-shard-secs {v}: not a number"))?;
            }
            other if other.starts_with("--") => {
                return Err(format!("{other}: not an option\n{USAGE}"));
            }
            other if root.is_none() => root = Some(other.to_string()),
            other => return Err(format!("{other}: unexpected argument\n{USAGE}")),
        }
    }
    let root_str = root.unwrap_or_else(|| ".".to_string());
    let root_path = Path::new(&root_str);
    let secs_path = secs.unwrap_or_else(|| {
        root_path
            .join("crates/aib-knife/knife-secs.tsv")
            .to_string_lossy()
            .into_owned()
    });
    let only = match (base.as_deref(), head.as_deref()) {
        (Some(b), Some(h)) => Some(crate::knife_precheck::changed(root_path, b, h)?),
        (None, None) => None,
        _ => return Err(format!("--base and --head go together\n{USAGE}")),
    };
    let table = parse_secs(&read(&secs_path)?).map_err(|e| format!("{secs_path}: {e}"))?;
    let (_, knives, errs) = crate::knife_anchors::collect(root_path);
    if !errs.is_empty() {
        return Err(errs.join("\n"));
    }
    let summary = check_size(
        &knives,
        &table,
        shards,
        max_secs,
        max_knives,
        max_shard_secs,
        only.as_ref(),
    )?;
    let mut out = String::new();
    for v in &summary.violations {
        out.push_str(&format!("KNIVES-SIZE-OVER {v}\n"));
    }
    if summary.violations.is_empty() {
        out.push_str(&format!(
            "KNIVES-SIZE-CHECK scripts={} shards={shards} max_secs={} max_knives={} max_shard_secs={} ok\n",
            summary.scripts, summary.max_secs, summary.max_knives, summary.max_shard_secs
        ));
        Ok((0, out))
    } else {
        out.push_str(&format!(
            "KNIVES-SIZE-CHECK violations={}\n",
            summary.violations.len()
        ));
        Ok((1, out))
    }
}

fn summary_cmd(args: &[String]) -> Result<(u8, String), String> {
    let mut tier = "normal".to_string();
    let (mut passed, mut deferred, mut failed, mut timed_out, mut cancelled) =
        (0u64, 0u64, 0u64, 0u64, 0u64);
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let val = |flag: &str, it: &mut std::slice::Iter<'_, String>| -> Result<String, String> {
            it.next()
                .cloned()
                .ok_or_else(|| format!("{flag} needs a value\n{USAGE}"))
        };
        let num = |flag: &str, it: &mut std::slice::Iter<'_, String>| -> Result<u64, String> {
            let v = val(flag, it)?;
            v.parse()
                .map_err(|_| format!("{flag} {v}: not a whole number"))
        };
        match a.as_str() {
            "--tier" => tier = val(a, &mut it)?,
            "--passed" => passed = num(a, &mut it)?,
            "--deferred" => deferred = num(a, &mut it)?,
            "--failed" => failed = num(a, &mut it)?,
            "--timed-out" => timed_out = num(a, &mut it)?,
            "--cancelled" => cancelled = num(a, &mut it)?,
            other => return Err(format!("{other}: not an option\n{USAGE}")),
        }
    }
    if !matches!(tier.as_str(), "strict" | "normal" | "fast" | "unknown") {
        return Err(format!(
            "--tier {tier}: must be strict, normal, fast, or unknown"
        ));
    }
    let bad = failed.saturating_add(timed_out).saturating_add(cancelled);
    let label = if bad > 0 {
        "失敗"
    } else if deferred > 0 {
        "條件通過"
    } else if tier == "strict" {
        "嚴格通過"
    } else if tier == "unknown" {
        "通過(檔次未讀)"
    } else {
        "一般通過"
    };
    let line = format!(
        "KNIVES-SUMMARY tier={tier} label={label} passed={passed} deferred={deferred} failed={bad}\n"
    );
    Ok((u8::from(bad > 0), line))
}

pub fn estimate_macos_queue_min(running: usize, queued: usize, macos_slots: usize) -> usize {
    let slots = macos_slots.max(1);
    let total = running.saturating_add(queued);
    // Average ~45m per macOS knife/rust cell across slots
    (total.div_ceil(slots)).saturating_mul(45)
}

fn queue_cmd(args: &[String]) -> Result<(u8, String), String> {
    let mut repo = "ez2app/ez2-aib".to_string();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--repo" => {
                repo = it
                    .next()
                    .cloned()
                    .ok_or_else(|| format!("--repo needs a value\n{USAGE}"))?;
            }
            other => return Err(format!("{other}: not an option\n{USAGE}")),
        }
    }
    let query = |status: &str| -> Result<usize, String> {
        let out = std::process::Command::new("gh")
            .args([
                "api",
                &format!("repos/{repo}/actions/runs?status={status}&per_page=1"),
                "--jq",
                ".total_count",
            ])
            .output()
            .map_err(|e| format!("gh api: {e}"))?;
        if !out.status.success() {
            return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
        }
        String::from_utf8_lossy(&out.stdout)
            .trim()
            .parse::<usize>()
            .map_err(|e| format!("bad gh count: {e}"))
    };
    let running = query("in_progress")?;
    let queued = query("queued")?;
    let est = estimate_macos_queue_min(running, queued, 5);
    Ok((
        0,
        format!(
            "CI-QUEUE repo={repo} running_runs={running} queued_runs={queued} est_wait_min={est}\n"
        ),
    ))
}

pub fn run(args: &[String]) -> u8 {
    if let [cmd, rest @ ..] = args {
        if cmd == "check" || cmd == "summary" || cmd == "queue" {
            let res = match cmd.as_str() {
                "check" => check_cmd(rest),
                "summary" => summary_cmd(rest),
                _ => queue_cmd(rest),
            };
            return match res {
                Ok((rc, text)) => {
                    print!("{text}");
                    rc
                }
                Err(e) => {
                    eprintln!("knife-shards: {e}");
                    2
                }
            };
        }
    }
    let r = match args {
        [cmd, rest @ ..] if cmd == "plan" => plan_cmd(rest),
        [cmd, rest @ ..] if cmd == "secs" => secs_cmd(rest),
        [cmd, rest @ ..] if cmd == "grew" => grew_cmd(rest),
        [cmd, rest @ ..] if cmd == "limits" => limits_cmd(rest),
        _ => Err(USAGE.into()),
    };
    match r {
        Ok(text) => {
            print!("{text}");
            0
        }
        Err(e) => {
            eprintln!("knife-shards: {e}");
            2
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_script_far_over_its_seconds_is_said_and_one_a_little_over_is_not() {
        let t = |rows: &[(&str, u64)]| -> BTreeMap<String, u64> {
            rows.iter().map(|(f, s)| (f.to_string(), *s)).collect()
        };
        let table = t(&[
            ("a.sh", 100),
            ("b.sh", 3),
            ("c.sh", 100),
            ("d.sh", 40),
            ("e.sh", 100),
        ]);
        let now = t(&[
            ("a.sh", 201), // over twice, and 60 more
            ("b.sh", 20),  // six times, but 17 s more: a light script
            ("c.sh", 200), // exactly twice: not over
            ("d.sh", 99),  // over twice, 59 s more
            ("e.sh", 10),  // faster
            ("new-heavy.sh", 60),
            ("new-light.sh", 59),
        ]);
        assert_eq!(
            grew(&table, &now),
            vec![
                Grew::Over {
                    file: "a.sh".into(),
                    table: 100,
                    now: 201
                },
                Grew::NewHeavy {
                    file: "new-heavy.sh".into(),
                    now: 60
                },
            ]
        );
        assert!(grew(&table, &BTreeMap::new()).is_empty());
    }

    #[test]
    fn limits_are_computed_from_history_and_clamped() {
        assert_eq!(limit_mins(0), 5);
        assert_eq!(limit_mins(60), 5);
        assert_eq!(limit_mins(120), 6);
        assert_eq!(limit_mins(600), 22);
        assert_eq!(limit_mins(10_000), 240);
        let t = table(&[("a.sh", 30), ("b.sh", 600)]);
        assert_eq!(
            limits(&t),
            vec![("a.sh".to_string(), 5), ("b.sh".to_string(), 22)]
        );
    }

    fn table(rows: &[(&str, u64)]) -> BTreeMap<String, u64> {
        rows.iter().map(|(p, s)| (p.to_string(), *s)).collect()
    }
    fn list(paths: &[&str]) -> Vec<String> {
        paths.iter().map(|p| p.to_string()).collect()
    }
    fn cell(p: &[(String, u32)], path: &str) -> u32 {
        p.iter().find(|(f, _)| f == path).map(|(_, k)| *k).unwrap()
    }

    #[test]
    fn the_heaviest_scripts_go_to_different_cells_and_every_script_gets_one_cell() {
        // name order would give a+c (100+90) to cell 1 and b+d (1+1) to cell 2
        let t = table(&[("a", 100), ("b", 1), ("c", 90), ("d", 1)]);
        let l = list(&["a", "b", "c", "d"]);
        let p = plan(&l, &t, 2).unwrap();
        assert_ne!(cell(&p, "a"), cell(&p, "c"), "{p:?}");
        assert_eq!(
            p.iter().map(|(f, _)| f.as_str()).collect::<Vec<_>>(),
            vec!["a", "b", "c", "d"],
            "every listed script once, in path order"
        );
        assert!(p.iter().all(|(_, k)| (1..=2).contains(k)), "{p:?}");
        // heaviest first: a → 1, c → 2, then the two light ones to the lighter cell (2: 90, 91)
        assert_eq!(
            p,
            vec![
                ("a".into(), 1),
                ("b".into(), 2),
                ("c".into(), 2),
                ("d".into(), 2)
            ]
        );
        assert_eq!(loads(&p, &t, 2), vec![100, 92]);
    }

    #[test]
    fn the_same_inputs_give_the_same_plan_whatever_order_the_list_came_in() {
        let t = table(&[("a", 5), ("b", 5), ("c", 5), ("d", 5), ("e", 5)]);
        let p1 = plan(&list(&["a", "b", "c", "d", "e"]), &t, 3).unwrap();
        let p2 = plan(&list(&["e", "c", "a", "d", "b"]), &t, 3).unwrap();
        assert_eq!(p1, p2);
        // the same seconds: path byte order, the lowest cell number first
        assert_eq!(
            p1.iter().map(|(_, k)| *k).collect::<Vec<_>>(),
            vec![1, 2, 3, 1, 2]
        );
    }

    #[test]
    fn a_script_the_table_does_not_know_weighs_the_tables_median_and_a_stale_line_gets_no_cell() {
        // the median is the whole table's: 10 20 90 500 600 ⇒ 90, with the two stale lines in it
        let t = table(&[
            ("a", 10),
            ("b", 20),
            ("c", 90),
            ("gone1", 500),
            ("gone2", 600),
        ]);
        assert_eq!(median(&[10, 20, 90, 500, 600]), 90);
        let p = plan(&list(&["a", "b", "c", "new"]), &t, 2).unwrap();
        // only the listed scripts get a cell
        assert_eq!(
            p.iter().map(|(f, _)| f.as_str()).collect::<Vec<_>>(),
            vec!["a", "b", "c", "new"]
        );
        // c 90 → 1; new 90 → 2; b 20 → 1 (110); a 10 → 2 (100)
        assert_eq!(cell(&p, "new"), 2);
        assert_eq!(loads(&p, &t, 2), vec![110, 100]);
        // no table at all: every script weighs 1, so the cells take turns in path order
        let none = BTreeMap::new();
        assert_eq!(median(&[]), 1);
        let p = plan(&list(&["a", "b", "c"]), &none, 2).unwrap();
        assert_eq!(p.iter().map(|(_, k)| *k).collect::<Vec<_>>(), vec![1, 2, 1]);
        assert_eq!(median(&[4, 2]), 2, "the lower one of two");
    }

    #[test]
    fn seconds_at_the_top_of_the_range_do_not_overflow() {
        let t = table(&[("a", u64::MAX), ("b", 1)]);
        let p = plan(&list(&["a", "b"]), &t, 1).unwrap();
        assert_eq!(loads(&p, &t, 1), vec![u64::MAX]);
    }

    #[test]
    fn one_cell_takes_everything_and_no_cell_is_an_error() {
        let t = table(&[("a", 3)]);
        let p = plan(&list(&["a", "b"]), &t, 1).unwrap();
        assert!(p.iter().all(|(_, k)| *k == 1));
        assert!(plan(&list(&["a"]), &t, 0).is_err());
    }

    #[test]
    fn a_table_line_that_is_not_seconds_tab_path_is_refused_with_its_line() {
        assert_eq!(
            parse_secs("# made from run 1\n\n12\tcrates/a/knives/k.sh\n").unwrap(),
            table(&[("crates/a/knives/k.sh", 12)])
        );
        for bad in [
            "12 crates/a/knives/k.sh\n",
            "x\tcrates/a/knives/k.sh\n",
            "-1\tcrates/a/knives/k.sh\n",
            "+5\tcrates/a/knives/k.sh\n",
            "1.5\tcrates/a/knives/k.sh\n",
            "\tcrates/a/knives/k.sh\n",
            "12\t\n",
            "12\ta\tb\n",
            "12\t a\n",
        ] {
            let e = parse_secs(&format!("3\tok\n{bad}")).unwrap_err();
            assert!(e.starts_with("line 2: "), "{bad:?}: {e}");
        }
        let e = parse_secs("1\ta\n2\ta\n").unwrap_err();
        assert!(e.contains("twice"), "{e}");
    }

    #[test]
    fn a_list_with_a_path_twice_or_no_path_is_refused() {
        assert_eq!(parse_list("a\n\nb\n").unwrap(), list(&["a", "b"]));
        assert!(parse_list("a\nb\na\n").unwrap_err().contains("twice"));
        assert!(parse_list("\n\n").unwrap_err().contains("no script"));
        assert!(parse_list("a b\n c\n").is_err());
    }

    #[test]
    fn the_table_is_the_median_of_the_green_runs_only() {
        let a = "2026-11-22T15:01:02Z KNIVES-SCRIPT crates/a/knives/k1.sh: rc=0 secs=10\n\
                 KNIVES-SCRIPT crates/a/knives/k2.sh: rc=1 secs=999\n\
                 KNIVES-SCRIPT crates/a/knives/k3.sh: not_run why_not=timeout secs=1800 limit=30m\n\
                 KNIVES-SCRIPT crates/a/knives/k4.sh: SKIPPED why=other_shard shard=1/4\n\
                 KNIVES-SCRIPT crates/a/knives/k5.sh: missed=1 rc=0\n\
                 KNIVES-SCRIPT crates/a/knives/k6.sh: rc=0 secs=7s\n"
            .to_string();
        let b = "KNIVES-SCRIPT crates/a/knives/k1.sh: rc=0 secs=30\r\n\
                 KNIVES-SCRIPT crates/a/knives/k1.sh: rc=0 secs=20\n\
                 KNIVES-SCRIPT crates/a/knives/k2.sh: rc=0 secs=4\n"
            .to_string();
        assert_eq!(
            secs_from_logs(&[a, b]),
            table(&[("crates/a/knives/k1.sh", 20), ("crates/a/knives/k2.sh", 4)])
        );
    }

    #[test]
    fn check_size_flags_heavy_scripts_many_knives_and_overloaded_shards() {
        let make = |file: &str, n: usize| -> Vec<crate::knife_anchors::Knife> {
            (0..n)
                .map(|i| crate::knife_anchors::Knife {
                    at: format!("{file}:{}", i + 1),
                    argv: Ok(vec!["true".into()]),
                    file: "src/lib.rs".into(),
                    old: "a".into(),
                    step: String::new(),
                    job: String::new(),
                })
                .collect()
        };
        let mut knives = make("crates/a/knives/ok.sh", 10);
        knives.extend(make("crates/b/knives/many.sh", 141));
        knives.extend(make("crates/c/knives/slow.sh", 5));
        let secs = table(&[
            ("crates/a/knives/ok.sh", 600),
            ("crates/b/knives/many.sh", 100),
            ("crates/c/knives/slow.sh", 901),
        ]);
        let s = check_size(
            &knives,
            &secs,
            1,
            MAX_SCRIPT_SECS,
            MAX_SCRIPT_KNIVES,
            1600,
            None,
        )
        .unwrap();
        assert_eq!(s.scripts, 3);
        assert_eq!(s.max_knives, 141);
        assert_eq!(s.max_secs, 901);
        assert_eq!(s.max_shard_secs, 1601);
        assert_eq!(s.violations.len(), 3, "{:?}", s.violations);
        assert!(s
            .violations
            .iter()
            .any(|v| v.contains("many.sh: 141 knives")));
        assert!(s.violations.iter().any(|v| v.contains("slow.sh: 901s")));
        assert!(s.violations.iter().any(|v| v.contains("shard 1/1: 1601s")));

        let only: BTreeSet<String> = ["crates/a/knives/ok.sh".to_string()].into();
        let filtered = check_size(
            &knives,
            &secs,
            1,
            MAX_SCRIPT_SECS,
            MAX_SCRIPT_KNIVES,
            MAX_SHARD_SECS,
            Some(&only),
        )
        .unwrap();
        assert!(filtered.violations.is_empty(), "{:?}", filtered.violations);

        let ok = check_size(
            &make("crates/a/knives/ok.sh", 140),
            &table(&[("crates/a/knives/ok.sh", 900)]),
            4,
            MAX_SCRIPT_SECS,
            MAX_SCRIPT_KNIVES,
            MAX_SHARD_SECS,
            None,
        )
        .unwrap();
        assert!(ok.violations.is_empty(), "{:?}", ok.violations);
        let missing_only: BTreeSet<String> = ["crates/d/knives/new.sh".to_string()].into();
        knives.extend(make("crates/d/knives/new.sh", 3));
        let missing_res = check_size(
            &knives,
            &secs,
            1,
            MAX_SCRIPT_SECS,
            MAX_SCRIPT_KNIVES,
            MAX_SHARD_SECS,
            Some(&missing_only),
        )
        .unwrap();
        assert!(
            missing_res
                .violations
                .iter()
                .any(|v| v.contains("missing from crates/aib-knife/knife-secs.tsv")),
            "{:?}",
            missing_res.violations
        );
        assert_eq!(estimate_macos_queue_min(2, 3, 5), 45);
        assert_eq!(estimate_macos_queue_min(5, 5, 5), 90);
    }
}
