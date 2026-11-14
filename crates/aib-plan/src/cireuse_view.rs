// SPDX-License-Identifier: Apache-2.0
//! 影子續跑分支底下「CI 可沿用(觀察中)」那一行(S68,Andy 11-14 同意先觀察一週)。
//!
//! 資料:共用沿用帳本 `ez2-reuse-ledger.v1`(S66 / S68 同一份格式)。aib-cireuse 在 GitHub 每次
//! main push 跑完後判一次、把帳上傳成 artifact(.github/workflows/cireuse-shadow.yml);座艙這邊
//! 每 30 分鐘叫同資料夾的 `aib-cireuse pull` 下載新的到本機帳本資料夾，再讀最近 7 天的 push 列。
//!
//! 只是觀察：這一行是預測(`reuse_prediction`),不跳任何格、不當任何沿用來源。字樣照
//! DEV-shadow r1 §5:「CI 可沿用 k/n 格(觀察中，未生效;main 這輪 N 分)」,不寫省下多少時間
//! (§0:影子期的數字一律標未生效)。k/n 與分鐘是最新一次判完的 main push。

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
    /// None = 沒有帳(資料夾不在 / 一筆判定都沒有)
    pub week: Option<Week>,
    /// 座艙旁邊找不到 aib-cireuse(S68c,Andy 11-14:要顯示「未安裝」、不要靜默消失)
    pub missing: bool,
}

impl Loader {
    /// 回傳畫面要不要換
    pub fn refresh(&mut self, dir: &Path, now_secs: i64) -> bool {
        self.refresh_with(dir, now_secs, bin().is_none())
    }

    /// `missing` 由呼叫的人量(測試不用真的放一支程式)
    pub fn refresh_with(&mut self, dir: &Path, now_secs: i64, missing: bool) -> bool {
        let flipped = missing != self.missing;
        self.missing = missing;
        let key = dir_key(dir);
        let fresh = self.at.is_some_and(|t| t.elapsed() < RECOMPUTE_EVERY);
        if key == self.key && fresh {
            return flipped;
        }
        let w = key
            .map(|_| week_summary(&ledger_lines(dir), now_secs, DAYS))
            .filter(|w| w.judged > 0);
        let changed = w != self.week;
        self.key = key;
        self.at = Some(Instant::now());
        self.week = w;
        changed || flipped
    }

    /// 樹上要不要畫這一行:有帳、或沒裝(沒裝要講出來)
    pub fn shown(&self) -> bool {
        self.week.is_some() || self.missing
    }

    /// 樹上那一行的字
    pub fn title(&self) -> String {
        match (&self.week, self.missing) {
            (_, true) => "CI 可沿用:未安裝 aib-cireuse(觀察中，未生效)".into(),
            (Some(w), false) => title(w),
            (None, false) => String::new(),
        }
    }

    /// 點開看到的說明
    pub fn why(&self, dir: &Path) -> String {
        let mut s = String::new();
        if self.missing {
            s.push_str(&format!(
                "座艙同一個資料夾找不到 {BIN_NAME},下載不到 GitHub 上的新帳;裝法：aib-release binsync(必裝清單)。"
            ));
        }
        if let Some(w) = &self.week {
            if self.missing {
                s.push_str("本機已有的帳:");
            }
            s.push_str(&why(w, dir));
        }
        s
    }
}

/// 程式檔名(Windows 帶 .exe)
pub const BIN_NAME: &str = if cfg!(windows) {
    "aib-cireuse.exe"
} else {
    "aib-cireuse"
};

/// 跟座艙同一個資料夾的 aib-cireuse;沒有 = None
pub fn bin() -> Option<PathBuf> {
    std::env::current_exe()
        .ok()
        .map(|p| p.with_file_name(BIN_NAME))
        .filter(|p| p.is_file())
}

/// 樹上那一行的字(DEV-shadow r1 §5 S68 定的字樣;不寫「可省」)
pub fn title(w: &Week) -> String {
    match &w.latest {
        Some(l) => format!(
            "CI 可沿用 {}/{} 格(觀察中，未生效;main 這輪{})",
            l.cells_reusable,
            l.cells_total,
            l.main_wall_minutes
                .map_or("時間讀不到".to_string(), |m| format!(" {m:.0} 分"))
        ),
        None => "CI 可沿用(觀察中，未生效)".into(),
    }
}

/// 點開看到的說明:追得到是哪一次 main push、帳在哪
pub fn why(w: &Week, dir: &Path) -> String {
    let mut s = String::new();
    if let Some(l) = &w.latest {
        s.push_str(&format!(
            "最新一次 main push {}({}):aib-cireuse 判每一格能不能沿用 PR 同一棵樹已跑過的結果，只記錄、不跳任何格。",
            l.head_sha.get(..8).unwrap_or(&l.head_sha),
            l.state
        ));
    }
    s.push_str(&format!(
        "最近 {DAYS} 天判了 {} 次，其中 {} 次每一格都可沿用。帳本 {}(ez2-reuse-ledger.v1,kind reuse_prediction)",
        w.judged,
        w.reusable_pushes,
        dir.display()
    ));
    s
}

static PULLING: AtomicBool = AtomicBool::new(false);

/// 在背景叫同資料夾的 `aib-cireuse pull`;沒有這支程式、或上一次還沒跑完就不叫。
/// 回傳有沒有叫(不等它跑完;跑完帳本資料夾的修改時間會變，下一次 refresh 就會讀到)。
pub fn spawn_pull(dir: &Path, repo: &str) -> bool {
    let Some(bin) = bin() else {
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

    fn row(sha: &str, verdict: &str, at: i64, k: u64) -> String {
        serde_json::json!({
            "schema": aib_cireuse::LEDGER_SCHEMA, "kind": aib_cireuse::KIND_PREDICTION,
            "unit": "push", "head_sha": sha, "verdict": verdict, "state": verdict,
            "main_created_at": at, "at": aib_cireuse::iso_utc(at + 60),
            "cells_reusable": k, "cells_total": 6, "observed_main_wall_minutes": 101.4,
        })
        .to_string()
    }

    #[test]
    fn loader_reads_the_ledger_and_shows_the_latest_push_without_a_saving() {
        let d = std::env::temp_dir().join(format!("aib-plan-cireuse-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        let now = 1_778_300_000;
        let mut l = Loader::default();
        assert!(
            !l.refresh_with(&d, now, false),
            "no folder: nothing to draw"
        );
        assert!(l.week.is_none() && !l.shown());
        std::fs::create_dir_all(&d).unwrap();
        std::fs::write(
            d.join("2026-05-09.jsonl"),
            format!(
                "{}\n{}\n",
                row("a0c1d2e3f4", "could_reuse", now - 200, 6),
                row("b9b8b7b6b5", "cannot_reuse", now - 100, 4)
            ),
        )
        .unwrap();
        std::fs::write(d.join("junk.txt"), "x").unwrap();
        assert!(l.refresh_with(&d, now, false));
        let w = l.week.clone().unwrap();
        assert_eq!(l.title(), title(&w));
        assert_eq!((w.judged, w.reusable_pushes), (2, 1));
        assert_eq!(
            title(&w),
            "CI 可沿用 4/6 格(觀察中，未生效;main 這輪 101 分)"
        );
        let why = why(&w, &d);
        assert!(why.contains("b9b8b7b6"), "traceable to the push: {why}");
        assert!(why.contains(&d.display().to_string()), "{why}");
        assert!(!title(&w).contains("可省") && !why.contains("可省"));
        assert!(
            !l.refresh_with(&d, now, false),
            "same files, just computed: no change"
        );
        // 程式被拿掉:同一份帳也要重畫，標題講未安裝、說明仍帶本機帳
        assert!(l.refresh_with(&d, now, true));
        assert!(l.shown());
        assert!(l.title().contains("未安裝 aib-cireuse"), "{}", l.title());
        let why = l.why(&d);
        assert!(why.contains(BIN_NAME) && why.contains("b9b8b7b6"), "{why}");
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn not_installed_is_shown_even_with_no_ledger() {
        let d = std::env::temp_dir().join(format!("aib-plan-cireuse-none-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        let mut l = Loader::default();
        assert!(l.refresh_with(&d, 1_778_300_000, true));
        assert!(l.week.is_none() && l.shown());
        assert_eq!(l.title(), "CI 可沿用:未安裝 aib-cireuse(觀察中，未生效)");
        assert!(!l.why(&d).contains("可省"));
        assert!(l.refresh_with(&d, 1_778_300_000, false));
        assert!(!l.shown());
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
