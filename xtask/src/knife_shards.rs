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

const USAGE: &str = "usage: aib-xtask knife-shards plan --shards N --secs FILE --list FILE\n       aib-xtask knife-shards secs LOG...";

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

pub fn run(args: &[String]) -> u8 {
    let r = match args {
        [cmd, rest @ ..] if cmd == "plan" => plan_cmd(rest),
        [cmd, rest @ ..] if cmd == "secs" => secs_cmd(rest),
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
}
