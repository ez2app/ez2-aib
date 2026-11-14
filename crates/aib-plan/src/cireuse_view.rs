// SPDX-License-Identifier: Apache-2.0
//! 影子續跑分支底下「CI 可沿用(觀察中)」那一行(S68,Andy 11-14 同意先觀察一週)。
//!
//! 資料:共用沿用帳本 `ez2-reuse-ledger.v1`(S66 / S68 同一份格式)。aib-cireuse 在 GitHub 每次
//! main push 跑完後判一次、把帳上傳成 artifact(.github/workflows/cireuse-shadow.yml);座艙這邊
//! 每 30 分鐘叫同資料夾的 `aib-cireuse pull` 下載新的到本機帳本資料夾，再讀最近 7 天的 push 列。
//!
//! 只是觀察：這一行是預測(`reuse_prediction`),不跳任何格、不當任何沿用來源。「可省」是預測
//! 可沿用的格在 main 實跑的機時上限，不是牆鐘、不是量到的省下時間，字面上照實寫。

use aib_cireuse::{ledger_lines, week_summary, Week};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant, SystemTime};

/// 座艙看幾天
pub const DAYS: i64 = 7;
/// 多久叫一次 pull
pub const PULL_EVERY: Duration = Duration::from_secs(30 * 60);
/// 帳沒變也每 10 分鐘重算一次(「最近 7 天」會隨時間滑動)
const RECOMPUTE_EVERY: Duration = Duration::from_secs(10 * 60);

/// 本機帳本資料夾:EZ2_REUSE_LEDGER_DIR;沒設 ⇒ Windows `%LOCALAPPDATA%\ez2-aib\reuse-ledger`,
/// 其他 `~/.ez2-aib/reuse-ledger`(跟 S66 約定的同一處)
pub fn ledger_dir() -> PathBuf {
    if let Some(d) = std::env::var_os("EZ2_REUSE_LEDGER_DIR") {
        return PathBuf::from(d);
    }
    if cfg!(windows) {
        if let Some(l) = std::env::var_os("LOCALAPPDATA") {
            return PathBuf::from(l).join("ez2-aib").join("reuse-ledger");
        }
    }
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_default()
        .join(".ez2-aib")
        .join("reuse-ledger")
}

/// 帳本資料夾的「版本」:日帳檔數 + 最新修改時間(秒);讀不到 = None
fn dir_key(dir: &Path) -> Option<(usize, u64)> {
    let rd = std::fs::read_dir(dir).ok()?;
    let mut n = 0;
    let mut newest = 0;
    for e in rd.filter_map(Result::ok) {
        let name = e.file_name();
        if !name.to_str().is_some_and(aib_cireuse::is_ledger_file_name) {
            continue;
        }
        n += 1;
        let t = e
            .metadata()
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| t.duration_since(SystemTime::UNIX_EPOCH).ok())
            .map_or(0, |d| d.as_secs());
        newest = newest.max(t);
    }
    Some((n, newest))
}

/// 讀帳、算一週;帳沒變而且算過不到 10 分鐘就不重算
#[derive(Debug, Default)]
pub struct Loader {
    key: Option<(usize, u64)>,
    at: Option<Instant>,
    /// None = 沒有帳(資料夾不在 / 一筆判定都沒有)⇒ 樹上不畫這一行
    pub week: Option<Week>,
}

impl Loader {
    /// 回傳畫面要不要換
    pub fn refresh(&mut self, dir: &Path, now_secs: i64) -> bool {
        let key = dir_key(dir);
        let fresh = self.at.is_some_and(|t| t.elapsed() < RECOMPUTE_EVERY);
        if key == self.key && fresh {
            return false;
        }
        let w = key
            .map(|_| week_summary(&ledger_lines(dir), now_secs, DAYS))
            .filter(|w| w.judged > 0);
        let changed = w != self.week;
        self.key = key;
        self.at = Some(Instant::now());
        self.week = w;
        changed
    }
}

/// 樹上那一行的字
pub fn title(w: &Week) -> String {
    format!(
        "CI 可沿用(觀察中):本週 {} 次、可省上限 {} 分",
        w.reusable_pushes,
        w.could_save_s / 60
    )
}

/// 點開看到的說明
pub fn why(w: &Week) -> String {
    let mut s = format!(
        "aib-cireuse 判 main push 能不能沿用 PR 同一棵樹已跑過的 CI 結果，只記錄、不跳任何格。最近 {DAYS} 天判了 {} 次，其中 {} 次每一格都可沿用。可省上限 = 預測可沿用的格在 main 實跑的機時合計，不是牆鐘、不是量到的省下時間",
        w.judged, w.reusable_pushes
    );
    if w.unknown_save > 0 {
        s.push_str(&format!(";{} 次秒數讀不到、沒算進去", w.unknown_save));
    }
    s
}

static PULLING: AtomicBool = AtomicBool::new(false);

/// 在背景叫同資料夾的 `aib-cireuse pull`;沒有這支程式、或上一次還沒跑完就不叫。
/// 回傳有沒有叫(不等它跑完;跑完帳本資料夾的修改時間會變，下一次 refresh 就會讀到)。
pub fn spawn_pull(dir: &Path, repo: &str) -> bool {
    let Some(bin) = std::env::current_exe()
        .ok()
        .map(|p| {
            p.with_file_name(if cfg!(windows) {
                "aib-cireuse.exe"
            } else {
                "aib-cireuse"
            })
        })
        .filter(|p| p.is_file())
    else {
        return false;
    };
    if PULLING.swap(true, Ordering::SeqCst) {
        return false;
    }
    let (dir, repo) = (dir.to_path_buf(), repo.to_string());
    std::thread::spawn(move || {
        let _ = std::process::Command::new(bin)
            .arg("pull")
            .arg("--repo")
            .arg(&repo)
            .arg("--reuse-ledger")
            .arg(&dir)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
        PULLING.store(false, Ordering::SeqCst);
    });
    true
}

/// 要下載哪個倉的帳:EZ2_CIREUSE_REPO,沒設 = 本專案倉
pub fn repo() -> String {
    std::env::var("EZ2_CIREUSE_REPO").unwrap_or_else(|_| "ez2app/ez2-aib".into())
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    fn row(sha: &str, verdict: &str, at: i64, save: i64) -> String {
        serde_json::json!({
            "schema": aib_cireuse::LEDGER_SCHEMA, "kind": aib_cireuse::KIND_PREDICTION,
            "unit": "push", "head_sha": sha, "verdict": verdict,
            "main_created_at": at, "could_save_s": save,
        })
        .to_string()
    }

    #[test]
    fn loader_reads_the_ledger_and_says_upper_bound() {
        let d = std::env::temp_dir().join(format!("aib-plan-cireuse-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        let now = 1_778_300_000;
        let mut l = Loader::default();
        assert!(!l.refresh(&d, now), "no folder: nothing to draw");
        assert!(l.week.is_none());
        std::fs::create_dir_all(&d).unwrap();
        std::fs::write(
            d.join("2026-05-09.jsonl"),
            format!(
                "{}\n{}\n",
                row("a", "could_reuse", now - 100, 1500),
                row("b", "cannot_reuse", now - 200, 300)
            ),
        )
        .unwrap();
        std::fs::write(d.join("junk.txt"), "x").unwrap();
        assert!(l.refresh(&d, now));
        let w = l.week.clone().unwrap();
        assert_eq!((w.judged, w.reusable_pushes, w.could_save_s), (2, 1, 1800));
        assert_eq!(title(&w), "CI 可沿用(觀察中):本週 1 次、可省上限 30 分");
        assert!(why(&w).contains("不是牆鐘"));
        assert!(!l.refresh(&d, now), "same files, just computed: no change");
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn no_judged_push_draws_nothing() {
        let d = std::env::temp_dir().join(format!("aib-plan-cireuse-empty-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        std::fs::write(
            d.join("2026-05-09.jsonl"),
            row("a", "unknown", 1_778_299_000, 0),
        )
        .unwrap();
        let mut l = Loader::default();
        l.refresh(&d, 1_778_300_000);
        assert!(l.week.is_none());
        let _ = std::fs::remove_dir_all(&d);
    }
}
