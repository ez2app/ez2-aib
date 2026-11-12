// SPDX-License-Identifier: Apache-2.0
//! `aib-fixlog`:寫一個自動審修發現到 `autofix.jsonl`,aib-plan 的「自動審修」分支(影子續跑下方)會列出來
//! (Andy 11-13 01:3x:做完一個功能自動審一輪，嚴重 / 中度修完，輕度可以先記下;每修一個都要顯示)。
//!
//! ```text
//! aib-fixlog add --seat a63ab --work "#731 影子續跑名稱" --sev H|M|L --what "問題一句話" \
//!                --state fixed|noted [--commit <sha>]
//! ```
//! - `noted`(先記下沒修)只准輕度 L;嚴重 / 中度一定要 `fixed` 並帶 `--commit`。
//! - 位置：EZ2_AUTOFIX_LOG,沒設 = `<EZ2_AIB_STATE_ROOT 或 ~/.ez2-aib>/state/aib-plan/autofix.jsonl`。
//! - 一列一次 append 寫完(多席同時寫不會交錯成半列)。

use aib_plan::autofix::{self, Fix, Sev};
use std::io::Write;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

const USAGE: &str = "usage: aib-fixlog add --seat CODE --work TEXT --sev H|M|L --what TEXT --state fixed|noted [--commit SHA]";

fn log_path() -> PathBuf {
    if let Some(p) = std::env::var_os("EZ2_AUTOFIX_LOG") {
        return PathBuf::from(p);
    }
    std::env::var_os("EZ2_AIB_STATE_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            std::env::var_os("HOME")
                .or_else(|| std::env::var_os("USERPROFILE"))
                .map(PathBuf::from)
                .unwrap_or_default()
                .join(".ez2-aib")
        })
        .join("state")
        .join("aib-plan")
        .join("autofix.jsonl")
}

/// 參數 ⇒ 一個發現;檢查跟讀的那邊一樣嚴(寫得進去的一定讀得出來)
fn build(args: &[String], now: i64) -> Result<Fix, String> {
    let mut it = args.iter();
    if it.next().map(String::as_str) != Some("add") {
        return Err(USAGE.into());
    }
    let (mut seat, mut work, mut sev, mut what, mut state, mut commit) =
        (None, None, None, None, None, None);
    while let Some(k) = it.next() {
        let v = it
            .next()
            .map(|v| v.trim().to_string())
            // 值不能是空的，也不能是下一個旗標(少打一個值時不會把旗標吃成內容)
            .filter(|v| !v.is_empty() && !v.starts_with("--"))
            .ok_or_else(|| format!("missing_value: {k}"))?;
        let slot = match k.as_str() {
            "--seat" => &mut seat,
            "--work" => &mut work,
            "--sev" => &mut sev,
            "--what" => &mut what,
            "--state" => &mut state,
            "--commit" => &mut commit,
            _ => return Err(format!("unknown_flag: {k}\n{USAGE}")),
        };
        *slot = Some(v);
    }
    let need = |v: Option<String>, k: &str| v.ok_or_else(|| format!("missing: {k}\n{USAGE}"));
    let sev_s = need(sev, "--sev")?;
    let sev = Sev::parse(&sev_s).ok_or_else(|| format!("bad_sev: {sev_s}(H / M / L)"))?;
    let fixed = match need(state, "--state")?.as_str() {
        "fixed" => true,
        "noted" => false,
        s => return Err(format!("bad_state: {s}(fixed / noted)")),
    };
    if !fixed && sev != Sev::Low {
        return Err(format!(
            "refused: {} 不能只記不修(只有輕度 L 可以 noted)",
            sev.label()
        ));
    }
    if fixed && sev != Sev::Low && commit.is_none() {
        return Err(format!(
            "missing: --commit({}修完要帶修正的 commit)",
            sev.label()
        ));
    }
    let f = Fix {
        at: now,
        seat: need(seat, "--seat")?,
        work: need(work, "--work")?,
        sev,
        what: need(what, "--what")?,
        fixed,
        commit,
    };
    // 寫進去的一定讀得出來
    match autofix::parse_line(&autofix::to_line(&f)) {
        Some(back) if back == f => Ok(f),
        _ => Err("internal: line does not read back".into()),
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_secs() as i64);
    let f = match build(&args, now) {
        Ok(f) => f,
        Err(e) => {
            eprintln!("aib-fixlog: {e}");
            std::process::exit(2);
        }
    };
    let path = log_path();
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let line = format!("{}\n", autofix::to_line(&f));
    let res = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .and_then(|mut fh| fh.write_all(line.as_bytes()));
    match res {
        Ok(()) => println!(
            "ok {} {} {} → {}",
            if f.fixed { "修" } else { "註記" },
            f.sev.label(),
            f.what,
            path.display()
        ),
        Err(e) => {
            eprintln!("aib-fixlog: write {}: {e}", path.display());
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn a(s: &str) -> Vec<String> {
        s.split('|').map(str::to_string).collect()
    }

    #[test]
    fn a_fixed_bug_needs_its_commit_and_only_low_may_be_noted() {
        let ok = build(
            &a("add|--seat|a63ab|--work|#731|--sev|M|--what|名稱沒清|--state|fixed|--commit|cb9014cd"),
            7,
        )
        .unwrap();
        assert_eq!((ok.at, ok.sev, ok.fixed), (7, Sev::Mid, true));
        assert!(build(
            &a("add|--seat|a|--work|w|--sev|M|--what|q|--state|fixed"),
            7
        )
        .unwrap_err()
        .contains("--commit"));
        assert!(build(
            &a("add|--seat|a|--work|w|--sev|H|--what|q|--state|noted"),
            7
        )
        .unwrap_err()
        .starts_with("refused"));
        assert!(build(
            &a("add|--seat|a|--work|w|--sev|L|--what|q|--state|noted"),
            7
        )
        .is_ok());
        assert!(build(
            &a("add|--seat|a|--work|w|--sev|Z|--what|q|--state|noted"),
            7
        )
        .is_err());
        assert!(build(
            &a("add|--seat|a|--work|w|--sev|L|--what| |--state|noted"),
            7
        )
        .is_err());
        assert!(build(&a("list"), 7).is_err());
        assert!(
            build(&a("add|--seat|--work|w|--sev|L|--what|q|--state|noted"), 7)
                .unwrap_err()
                .starts_with("missing_value: --seat"),
            "少一個值不能把下一個旗標吃成內容"
        );
    }
}
