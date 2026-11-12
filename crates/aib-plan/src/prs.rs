// SPDX-License-Identifier: Apache-2.0
//! 開發席座艙「我的 PR 列」的純邏輯(Andy 11-13 06:3x「先做開發席 PR 列」;計畫
//! pending/PLAN-dev-pr-list-519d0-1113.md r2,Codex 意見已收)。
//!
//! 開發席最常問「我的 PR 卡在哪」,答案散在 GitHub 格、審查稿首行、收據檔、mergewatch 程序四處。
//! 這裡只做判斷：輸入是讀好的文字(gh JSON、審查稿首行、收據全文、ps 輸出、git rev-list 結果),
//! 輸出是每張 PR 的事實與「下一步」一句。讀檔、跑指令、畫面在 `bin/aib-prs.rs`。
//!
//! 判不出來就說判不出來(Codex r1):範圍展不開、收據格式不認得 ⇒ `Unknown`,不當成「沒問題」也不當成「零」。

use std::collections::{BTreeMap, HashMap, HashSet};

/// 一個 CI 格的狀態
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Cell {
    Pass,
    Fail,
    Running,
    Skip,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Check {
    pub name: String,
    pub cell: Cell,
    /// 開始時間(epoch 秒);跑中的格用來算「macOS 跑了幾分」
    pub started: Option<i64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Pr {
    pub number: u32,
    pub title: String,
    pub branch: String,
    pub base: String,
    pub head: String,
    /// MERGEABLE / CONFLICTING / UNKNOWN
    pub mergeable: String,
    pub checks: Vec<Check>,
    /// 這張 PR 自己的 commit(不含 base 上的)
    pub commits: Vec<String>,
}

/// `gh pr list --json number,title,headRefName,headRefOid,baseRefName,mergeable,statusCheckRollup,commits`
pub fn parse_gh(text: &str) -> Result<Vec<Pr>, String> {
    let v: serde_json::Value = serde_json::from_str(text).map_err(|e| format!("gh_json: {e}"))?;
    let arr = v.as_array().ok_or("gh_json: not an array")?;
    let s = |x: &serde_json::Value, k: &str| {
        x.get(k).and_then(|y| y.as_str()).unwrap_or("").to_string()
    };
    let mut out = Vec::new();
    for p in arr {
        let Some(number) = p.get("number").and_then(|n| n.as_u64()) else {
            continue;
        };
        let checks = p
            .get("statusCheckRollup")
            .and_then(|c| c.as_array())
            .map(|a| a.iter().map(parse_check).collect())
            .unwrap_or_default();
        let commits = p
            .get("commits")
            .and_then(|c| c.as_array())
            .map(|a| {
                a.iter()
                    .map(|c| s(c, "oid"))
                    .filter(|o| is_sha(o, 40))
                    .collect()
            })
            .unwrap_or_default();
        out.push(Pr {
            number: number as u32,
            title: s(p, "title"),
            branch: s(p, "headRefName"),
            base: s(p, "baseRefName"),
            head: s(p, "headRefOid"),
            mergeable: s(p, "mergeable"),
            checks,
            commits,
        });
    }
    Ok(out)
}

fn parse_check(c: &serde_json::Value) -> Check {
    let s = |k: &str| c.get(k).and_then(|y| y.as_str()).unwrap_or("");
    // CheckRun:status + conclusion;StatusContext:state + context
    let name = if s("name").is_empty() {
        s("context")
    } else {
        s("name")
    }
    .to_string();
    let cell = match (s("status"), s("conclusion"), s("state")) {
        (_, _, "SUCCESS") => Cell::Pass,
        (_, _, "FAILURE" | "ERROR") => Cell::Fail,
        (_, _, "PENDING" | "EXPECTED") => Cell::Running,
        ("COMPLETED", "SUCCESS" | "NEUTRAL", _) => Cell::Pass,
        ("COMPLETED", "SKIPPED", _) => Cell::Skip,
        ("COMPLETED", _, _) => Cell::Fail, // FAILURE / CANCELLED / TIMED_OUT / ACTION_REQUIRED
        _ => Cell::Running,
    };
    Check {
        name,
        cell,
        started: parse_iso(s("startedAt")),
    }
}

/// `2026-11-12T22:21:11Z` ⇒ epoch 秒(只認 GitHub 的 UTC 寫法)
pub fn parse_iso(t: &str) -> Option<i64> {
    let b = t.as_bytes();
    if b.len() != 20 || b[19] != b'Z' {
        return None;
    }
    let n = |r: std::ops::Range<usize>| t.get(r)?.parse::<i64>().ok();
    let (y, mo, d, h, mi, s) = (
        n(0..4)?,
        n(5..7)?,
        n(8..10)?,
        n(11..13)?,
        n(14..16)?,
        n(17..19)?,
    );
    // 日數:Howard Hinnant days_from_civil
    let y2 = if mo <= 2 { y - 1 } else { y };
    let era = y2.div_euclid(400);
    let yoe = y2 - era * 400;
    let mp = (mo + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    Some(days * 86_400 + h * 3600 + mi * 60 + s)
}

pub fn is_sha(s: &str, len: usize) -> bool {
    s.len() == len
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// 分支是不是這幾席的:`<type>/<名>-<席碼>-<MMDD>` 裡有 `-<席碼>-` 或以 `-<席碼>` 結尾
pub fn mine(branch: &str, seats: &[String]) -> bool {
    seats.iter().any(|s| {
        !s.is_empty() && (branch.contains(&format!("-{s}-")) || branch.ends_with(&format!("-{s}")))
    })
}

// ───────────────────────── 審查稿 ─────────────────────────

/// 審查稿首行(RULES 固定格式):`結論: 通過 頭: <40> H/M/L/N: a/b/c/d 範圍: …`
#[derive(Debug, Clone, PartialEq)]
pub struct Review {
    pub file: String,
    pub pass: bool,
    pub head: String,
    pub hmln: [u32; 4],
    /// `a..b`(兩端都是 7–40 碼);沒寫範圍、只寫「全文」⇒ None = 這張 PR 從 base 到 head
    pub range: Option<(String, String)>,
}

pub fn parse_review(file: &str, first_line: &str) -> Option<Review> {
    let l = first_line.trim();
    let rest = l.strip_prefix("結論:")?.trim_start();
    let pass = if rest.starts_with("通過") {
        true
    } else if rest.starts_with("不通過") {
        false
    } else {
        return None;
    };
    let head = after(l, "頭:")?.split_whitespace().next()?.to_string();
    if !is_sha(&head, 40) {
        return None;
    }
    let nums: Vec<u32> = after(l, "H/M/L/N:")?
        .split_whitespace()
        .next()?
        .split('/')
        .map(|x| x.parse().ok())
        .collect::<Option<_>>()?;
    let hmln: [u32; 4] = nums.try_into().ok()?;
    let range = after(l, "範圍:").and_then(find_range);
    Some(Review {
        file: file.to_string(),
        pass,
        head,
        hmln,
        range,
    })
}

fn after<'a>(l: &'a str, key: &str) -> Option<&'a str> {
    l.find(key).map(|i| l[i + key.len()..].trim_start())
}

/// 文字裡第一個 `<hex7-40>..<hex7-40>`
fn find_range(t: &str) -> Option<(String, String)> {
    for (i, _) in t.match_indices("..") {
        let left: String = t[..i]
            .chars()
            .rev()
            .take_while(|c| c.is_ascii_hexdigit())
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect();
        let right: String = t[i + 2..]
            .chars()
            .take_while(|c| c.is_ascii_hexdigit())
            .collect();
        let ok =
            |s: &str| (7..=40).contains(&s.len()) && s.bytes().all(|b| !b.is_ascii_uppercase());
        if ok(&left) && ok(&right) {
            return Some((left, right));
        }
    }
    None
}

/// 一份審查稿涵蓋哪些 commit:git 算好的集合(`rev-list b --not a`,或「全文」時 `rev-list head --not origin/<base>`)
/// 與 PR 自己的 commit 取交集。`expanded` = None ⇒ git 展不開(物件不在 / 範圍寫錯)⇒ 判不出來。
#[derive(Debug, Clone, PartialEq)]
pub enum Cover {
    /// 每個 commit 都有通過的稿
    All,
    /// 這些 commit 沒有任何通過的稿(依 PR 順序)
    Missing(Vec<String>),
    /// 有稿判不出來,而且沒判出來的部分可能就是缺的
    Unknown(String),
    /// 有稿寫「不通過」且是最新的頭
    Rejected(String),
}

pub fn coverage(pr: &Pr, reviews: &[(Review, Option<HashSet<String>>)]) -> Cover {
    if pr.commits.is_empty() {
        return Cover::Unknown("gh 沒給 commit 清單".into());
    }
    // 現在這個頭被判不通過 ⇒ 最優先(有更新的稿就不算)
    if let Some((r, _)) = reviews.iter().find(|(r, _)| r.head == pr.head && !r.pass) {
        if !reviews.iter().any(|(o, _)| o.head == pr.head && o.pass) {
            return Cover::Rejected(r.file.clone());
        }
    }
    let mut covered: HashSet<&str> = HashSet::new();
    let mut unknown = None;
    for (r, set) in reviews {
        if !r.pass {
            continue;
        }
        match set {
            Some(s) => covered.extend(
                pr.commits
                    .iter()
                    .filter(|c| s.contains(*c))
                    .map(String::as_str),
            ),
            None => unknown = Some(r.file.clone()),
        }
    }
    let missing: Vec<String> = pr
        .commits
        .iter()
        .filter(|c| !covered.contains(c.as_str()))
        .cloned()
        .collect();
    match (missing.is_empty(), unknown) {
        (true, _) => Cover::All,
        (false, Some(f)) => Cover::Unknown(format!("範圍展不開:{f}")),
        (false, None) => Cover::Missing(missing),
    }
}

// ───────────────────────── 收據 ─────────────────────────

/// 收據判定。認三種寫法:
/// - remote-crate-run:`HEAD <40>` … `FMT-OK` `CLIPPY-OK` `TEST-OK` … `HEAD-END <40>`
/// - FALLBACK:`HEAD <40>` + `RC stepNN=0 fmt|clippy|test…`
/// - 1ced7 式:`head <40> want <40>` + `fmt_rc=0` `clippy_rc=0` `test1_rc=0`
///
/// 頭不對、任何一步非 0、缺 fmt / clippy / test 任何一項 ⇒ 不算。
#[derive(Debug, Clone, PartialEq)]
pub enum Receipt {
    Ok,
    /// 頭對但不完整 / 有失敗,附原因
    Bad(String),
}

pub fn judge_receipt(text: &str, head: &str) -> Option<Receipt> {
    let mut seen_head = false;
    let (mut fmt, mut clippy, mut test) = (false, false, false);
    for l in text.lines() {
        let l = l.trim();
        let w: Vec<&str> = l.split_whitespace().collect();
        match w.as_slice() {
            ["HEAD", h] => {
                if *h != head {
                    return None;
                }
                seen_head = true;
            }
            ["head", h, "want", want] => {
                if *h != head || *want != head {
                    return None;
                }
                seen_head = true;
            }
            ["HEAD-END", h] if *h != head => return Some(Receipt::Bad("HEAD-END 換頭".into())),
            _ => {}
        }
        if let Some(rest) = l.strip_prefix("RC step") {
            // RC step09=0 clippy
            let (code, name) = rest.split_once(' ').unwrap_or((rest, ""));
            let rc = code.split_once('=').map(|(_, r)| r).unwrap_or("");
            if rc != "0" {
                return Some(Receipt::Bad(format!("step 非 0:{l}")));
            }
            let n = name.to_ascii_lowercase();
            fmt |= n.starts_with("fmt");
            clippy |= n.starts_with("clippy");
            test |= n.starts_with("test");
        }
        for tok in &w {
            if let Some((k, v)) = tok.split_once("_rc=") {
                if v != "0" {
                    return Some(Receipt::Bad(format!("{k} rc={v}")));
                }
                fmt |= k == "fmt";
                clippy |= k == "clippy";
                test |= k.starts_with("test");
            }
        }
        match l {
            "FMT-OK" => fmt = true,
            "CLIPPY-OK" => clippy = true,
            "TEST-OK" => test = true,
            _ => {}
        }
        if l.ends_with("-FAIL") || l.starts_with("FAIL ") {
            return Some(Receipt::Bad(l.to_string()));
        }
    }
    if !seen_head {
        return None;
    }
    let lack: Vec<&str> = [("fmt", fmt), ("clippy", clippy), ("test", test)]
        .iter()
        .filter(|(_, ok)| !ok)
        .map(|(n, _)| *n)
        .collect();
    Some(if lack.is_empty() {
        Receipt::Ok
    } else {
        Receipt::Bad(format!("缺 {}", lack.join(" / ")))
    })
}

// ───────────────────────── 守望 ─────────────────────────

/// `ps -Ao args=` ⇒ PR 號 → mergewatch 掛的頭(本機看得到的那幾支)
pub fn watches(ps: &str) -> HashMap<u32, String> {
    let mut m = HashMap::new();
    for l in ps.lines() {
        let w: Vec<&str> = l.split_whitespace().collect();
        if let Some(i) = w.iter().position(|a| a.ends_with("mergewatch.sh")) {
            if let (Some(n), Some(h)) = (w.get(i + 1), w.get(i + 2)) {
                if let (Ok(n), true) = (n.parse::<u32>(), is_sha(h, 40)) {
                    m.insert(n, h.to_string());
                }
            }
        }
    }
    m
}

// ───────────────────────── 下一步 ─────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Who {
    /// 要我動手(紅)
    Me,
    /// 等別人 / 等機器(黃)
    Wait,
    /// 守望在、沒有擋的(綠)
    Clear,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Row {
    pub pr: Pr,
    pub cover: Cover,
    /// 本機有的同頭收據:(檔名, 判定)
    pub receipts: Vec<(String, Receipt)>,
    /// mergewatch 掛的頭(本機 ps 看得到才有)
    pub watch: Option<String>,
    /// base 不是 main 時，base 那張 PR 的號碼(在清單裡找得到才有)
    pub stacked_on: Option<u32>,
    pub who: Who,
    pub next: String,
}

/// 第一條成立就停(計畫 §4 r2)
pub fn next_step(
    pr: &Pr,
    cover: &Cover,
    receipts: &[(String, Receipt)],
    watch: Option<&str>,
    stacked_on: Option<u32>,
    now: i64,
) -> (Who, String) {
    if pr.mergeable == "CONFLICTING" {
        return (Who::Me, "跟 base 衝突：接 main 手解".into());
    }
    match cover {
        Cover::Rejected(f) => return (Who::Me, format!("審查不通過:{f}")),
        Cover::Missing(m) => {
            let first: Vec<&str> = m.iter().take(3).map(|c| &c[..8]).collect();
            return (Who::Me, format!("缺審 {} 顆:{}", m.len(), first.join(" ")));
        }
        _ => {}
    }
    let red: Vec<&str> = pr
        .checks
        .iter()
        .filter(|c| c.cell == Cell::Fail)
        .map(|c| c.name.as_str())
        .collect();
    if !red.is_empty() {
        return (Who::Me, format!("CI 紅:{}", red.join(", ")));
    }
    if !receipts.iter().any(|(_, r)| *r == Receipt::Ok) {
        return match receipts.first() {
            Some((f, Receipt::Bad(why))) => (Who::Me, format!("收據不算({why}):{f}")),
            _ => (Who::Me, "跑同頭收據(本機沒有這個頭的收據)".into()),
        };
    }
    if let Cover::Unknown(why) = cover {
        return (Who::Wait, format!("審查判不出來:{why}"));
    }
    if pr.base != "main" {
        return (
            Who::Wait,
            match stacked_on {
                Some(n) => format!("等 #{n} 合(疊在 {})", pr.base),
                None => format!("疊在 {},等它合", pr.base),
            },
        );
    }
    match watch {
        None => return (Who::Me, "報頭碼給 f1fae 掛守望".into()),
        Some(h) if h != pr.head => return (Who::Me, format!("守望掛舊頭 {},報新頭", &h[..8])),
        _ => {}
    }
    if let Some(m) = running_minutes(pr, now) {
        return (Who::Wait, format!("等 CI({m})"));
    }
    (Who::Clear, "守望在、格跑完，等它合".into())
}

/// 跑中的格:有 macOS 就寫「macOS 跑 N 分」,不然「N 格跑中」
fn running_minutes(pr: &Pr, now: i64) -> Option<String> {
    let run: Vec<&Check> = pr
        .checks
        .iter()
        .filter(|c| c.cell == Cell::Running)
        .collect();
    if run.is_empty() {
        return None;
    }
    let mac = run
        .iter()
        .filter(|c| c.name.contains("macos"))
        .filter_map(|c| c.started)
        .min();
    Some(match mac {
        Some(t) => format!("macOS 跑 {} 分", (now - t).max(0) / 60),
        None => format!("{} 格跑中", run.len()),
    })
}

pub fn build_row(
    pr: Pr,
    cover: Cover,
    receipts: Vec<(String, Receipt)>,
    watch: Option<String>,
    stacked_on: Option<u32>,
    now: i64,
) -> Row {
    let (who, next) = next_step(&pr, &cover, &receipts, watch.as_deref(), stacked_on, now);
    Row {
        pr,
        cover,
        receipts,
        watch,
        stacked_on,
        who,
        next,
    }
}

/// 要我動手的在上 ⇒ 等別人 ⇒ 沒擋的;同一級號碼大的(新的)在上
pub fn sort_rows(rows: &mut [Row]) {
    rows.sort_by(|a, b| a.who.cmp(&b.who).then(b.pr.number.cmp(&a.pr.number)));
}

/// 第 2 行的事實:審 / 收據 / CI / 守望
pub fn facts(r: &Row) -> String {
    let rev = match &r.cover {
        Cover::All => "審 ✓".to_string(),
        Cover::Missing(m) => format!("審 缺{}", m.len()),
        Cover::Unknown(_) => "審 ?".to_string(),
        Cover::Rejected(_) => "審 ✗".to_string(),
    };
    let rc = if r.receipts.iter().any(|(_, x)| *x == Receipt::Ok) {
        "收據 ✓"
    } else if r.receipts.is_empty() {
        "收據 —"
    } else {
        "收據 ✗"
    };
    let mut n: BTreeMap<u8, usize> = BTreeMap::new();
    for c in &r.pr.checks {
        *n.entry(match c.cell {
            Cell::Pass => 0,
            Cell::Fail => 1,
            Cell::Running => 2,
            Cell::Skip => 3,
        })
        .or_default() += 1;
    }
    let g = |k| n.get(&k).copied().unwrap_or(0);
    let ci = format!("CI {}✓ {}✗ {}…", g(0), g(1), g(2));
    let w = match &r.watch {
        Some(h) if *h == r.pr.head => "守望 ✓",
        Some(_) => "守望 舊頭",
        None => "守望 —",
    };
    format!("{rev}  {rc}  {ci}  {w}")
}

#[cfg(test)]
mod tests {
    use super::*;

    const H: &str = "e21362b781bcea7c93516273633c62251b64a335";
    const C1: &str = "2114b286ff751f6ab30e76ba9490a55259b913d4";

    fn pr() -> Pr {
        Pr {
            number: 733,
            title: "t".into(),
            branch: "feat/aib-plan-autofix-a63ab-1113".into(),
            base: "main".into(),
            head: H.into(),
            mergeable: "MERGEABLE".into(),
            checks: vec![Check {
                name: "rust (macos-latest)".into(),
                cell: Cell::Pass,
                started: None,
            }],
            commits: vec![C1.into(), H.into()],
        }
    }

    fn all(cs: &[&str]) -> Option<HashSet<String>> {
        Some(cs.iter().map(|c| c.to_string()).collect())
    }

    fn rv(head: &str, pass: bool) -> Review {
        Review {
            file: format!("REVIEW-{}.md", &head[..8]),
            pass,
            head: head.into(),
            hmln: [0; 4],
            range: None,
        }
    }

    #[test]
    fn gh_json_reads_checks_commits_and_status_contexts() {
        let j = r#"[{"number":733,"title":"x","headRefName":"feat/a-519d0-1113","headRefOid":"e21362b781bcea7c93516273633c62251b64a335",
          "baseRefName":"main","mergeable":"MERGEABLE",
          "statusCheckRollup":[
            {"__typename":"CheckRun","name":"rust (macos-latest)","status":"IN_PROGRESS","conclusion":"","startedAt":"2026-11-12T22:21:11Z"},
            {"__typename":"CheckRun","name":"tip","status":"COMPLETED","conclusion":"SUCCESS"},
            {"__typename":"CheckRun","name":"win","status":"COMPLETED","conclusion":"CANCELLED"},
            {"__typename":"CheckRun","name":"py39","status":"COMPLETED","conclusion":"SKIPPED"},
            {"__typename":"StatusContext","context":"legacy","state":"PENDING"}],
          "commits":[{"oid":"2114b286ff751f6ab30e76ba9490a55259b913d4"},{"oid":"bad"}]}]"#;
        let p = &parse_gh(j).unwrap()[0];
        let cells: Vec<Cell> = p.checks.iter().map(|c| c.cell).collect();
        assert_eq!(
            cells,
            [
                Cell::Running,
                Cell::Pass,
                Cell::Fail,
                Cell::Skip,
                Cell::Running
            ],
            "取消算紅、略過不算"
        );
        assert_eq!(p.checks[4].name, "legacy");
        assert_eq!(p.checks[0].started, Some(1_794_522_071));
        assert_eq!(p.commits, [C1], "不是 40 碼的 oid 丟掉");
        assert!(parse_gh("{}").is_err());
    }

    #[test]
    fn iso_is_utc_epoch() {
        assert_eq!(parse_iso("1970-01-01T00:00:00Z"), Some(0));
        assert_eq!(parse_iso("2000-03-01T00:00:00Z"), Some(951_868_800));
        assert_eq!(parse_iso("2026-11-12T22:21:11+08:00"), None);
    }

    #[test]
    fn a_branch_belongs_to_its_seat_suffix() {
        let s = vec!["519d0".to_string(), "a63ab".to_string()];
        assert!(mine("feat/aib-plan-autofix-a63ab-1113", &s));
        assert!(mine("feat/aib-prs-519d0", &s));
        assert!(!mine("fix/tui-paste-1ced7-1113", &s));
        assert!(!mine("feat/xa63abx-1113", &s), "要整段席碼");
    }

    #[test]
    fn review_first_line_is_parsed_strictly() {
        let l =
            format!("結論: 通過 頭: {H} H/M/L/N: 0/0/1/3 範圍: 445dfbf7..f78f1c7b(重點 9d1ad69d)");
        let r = parse_review("R.md", &l).unwrap();
        assert!(r.pass);
        assert_eq!(r.hmln, [0, 0, 1, 3]);
        assert_eq!(r.range, Some(("445dfbf7".into(), "f78f1c7b".into())));
        let full = parse_review(
            "R.md",
            &format!("結論: 不通過 頭: {H} H/M/L/N: 1/0/0/0 範圍: 全文"),
        )
        .unwrap();
        assert!(!full.pass);
        assert_eq!(full.range, None);
        // 40 碼範圍，範圍裡有說明字
        let l = format!("結論: 通過 頭: {H} H/M/L/N: 0/0/0/2 範圍: {C1}..{H}(單一 commit)");
        assert_eq!(
            parse_review("R.md", &l).unwrap().range,
            Some((C1.into(), H.into()))
        );
        // 頭不是 40 碼、少一格、亂寫 ⇒ 不認
        assert!(parse_review(
            "R.md",
            "結論: 通過 頭: e21362b7 H/M/L/N: 0/0/0/0 範圍: 全文"
        )
        .is_none());
        assert!(parse_review(
            "R.md",
            &format!("結論: 通過 頭: {H} H/M/L/N: 0/0/0 範圍: 全文")
        )
        .is_none());
        assert!(
            parse_review("R.md", &format!("結論: 大致通過 頭: {H} H/M/L/N: 0/0/0/0")).is_none()
        );
        assert!(parse_review("R.md", "# 審查 xxx").is_none());
    }

    #[test]
    fn every_commit_needs_a_passing_review() {
        let p = pr();
        // 只審到 C1 ⇒ 缺 H
        let got = coverage(&p, &[(rv(C1, true), all(&[C1]))]);
        assert_eq!(got, Cover::Missing(vec![H.into()]));
        // 兩份湊滿 ⇒ 全涵蓋
        assert_eq!(
            coverage(&p, &[(rv(C1, true), all(&[C1])), (rv(H, true), all(&[H]))]),
            Cover::All
        );
        // 範圍展不開 + 有缺 ⇒ 判不出來，不當成缺也不當成齊
        assert!(matches!(
            coverage(&p, &[(rv(C1, true), all(&[C1])), (rv(H, true), None)]),
            Cover::Unknown(_)
        ));
        // 現在的頭被判不通過 ⇒ 不通過(就算舊的稿涵蓋了)
        assert!(
            matches!(
                coverage(
                    &p,
                    &[(rv(H, true), all(&[C1, H])), (rv(H, false), all(&[C1, H]))]
                ),
                Cover::All
            ),
            "同頭有通過的稿就不算被擋"
        );
        assert!(matches!(
            coverage(&p, &[(rv(H, false), all(&[C1, H]))]),
            Cover::Rejected(_)
        ));
        // 不通過的稿不算涵蓋
        assert_eq!(
            coverage(&p, &[(rv(C1, false), all(&[C1])), (rv(H, true), all(&[H]))]),
            Cover::Missing(vec![C1.into()])
        );
        let mut empty = pr();
        empty.commits.clear();
        assert!(matches!(coverage(&empty, &[]), Cover::Unknown(_)));
    }

    #[test]
    fn a_receipt_counts_only_for_this_head_and_all_steps() {
        let rcr = format!("HEAD {H}\nFMT-OK\nCLIPPY-OK\nWINCLIPPY-OK\ntest result: FAILED. 0 passed; 1 failed\nTEST-OK\nKNIVES-OK\nHEAD-END {H}\nEND x");
        assert_eq!(
            judge_receipt(&rcr, H),
            Some(Receipt::Ok),
            "刀咬到的 FAILED 不是失敗"
        );
        assert_eq!(judge_receipt(&rcr, C1), None, "別的頭的收據不算這張");
        let moved = format!("HEAD {H}\nFMT-OK\nCLIPPY-OK\nTEST-OK\nHEAD-END {C1}");
        assert!(
            matches!(judge_receipt(&moved, H), Some(Receipt::Bad(_))),
            "跑到一半換頭"
        );
        let fb = format!(
            "HEAD {H}\nRC step08=0 fmt\nRC step09=0 clippy\nRC step13=0 test (macOS)\nKNIVES-OK"
        );
        assert_eq!(judge_receipt(&fb, H), Some(Receipt::Ok));
        let fb_bad = format!("HEAD {H}\nRC step08=0 fmt\nRC step09=1 clippy\nRC step13=0 test");
        assert!(matches!(judge_receipt(&fb_bad, H), Some(Receipt::Bad(_))));
        let t = format!(
            "rc=0\nhead {H} want {H}\nfmt_rc=0\nclippy_rc=0\ntest1_rc=0\ntest2_rc=0\nrelease_rc=0"
        );
        assert_eq!(judge_receipt(&t, H), Some(Receipt::Ok));
        let t_bad = format!("head {H} want {H}\nfmt_rc=0\nclippy_rc=0\ntest1_rc=101");
        assert!(matches!(judge_receipt(&t_bad, H), Some(Receipt::Bad(_))));
        let lack = format!("HEAD {H}\nFMT-OK\nCLIPPY-OK");
        assert_eq!(
            judge_receipt(&lack, H),
            Some(Receipt::Bad("缺 test".into())),
            "中途停掉的不算全過"
        );
        let failed = format!("HEAD {H}\nFMT-OK\nCLIPPY-FAIL\nTEST-OK");
        assert!(matches!(judge_receipt(&failed, H), Some(Receipt::Bad(_))));
    }

    #[test]
    fn mergewatch_args_give_pr_and_head() {
        let ps = format!("bash ./mergewatch.sh 731 {H}\n/bin/bash /x/tools-f1fae/mergewatch.sh 670 {C1}\nbash ./mergewatch.sh 9 short\nvim mergewatch.sh.bak 1 {H}");
        let w = watches(&ps);
        assert_eq!(w.get(&731).map(String::as_str), Some(H));
        assert_eq!(w.get(&670).map(String::as_str), Some(C1));
        assert_eq!(w.len(), 2);
    }

    #[test]
    fn next_step_takes_the_first_blocker_in_order() {
        let ok = vec![("R".to_string(), Receipt::Ok)];
        let now = 1_794_523_000;
        let step = |p: &Pr, c: &Cover, r: &[(String, Receipt)], w: Option<&str>, s| {
            next_step(p, c, r, w, s, now)
        };
        // 1 衝突蓋過一切
        let mut p = pr();
        p.mergeable = "CONFLICTING".into();
        assert_eq!(
            step(&p, &Cover::Missing(vec![H.into()]), &[], None, None).1,
            "跟 base 衝突：接 main 手解"
        );
        // 2 缺審
        let p = pr();
        let (who, t) = step(
            &p,
            &Cover::Missing(vec![C1.into(), H.into()]),
            &ok,
            Some(H),
            None,
        );
        assert_eq!((who, t.as_str()), (Who::Me, "缺審 2 顆:2114b286 e21362b7"));
        // 3 CI 紅
        let mut red = pr();
        red.checks.push(Check {
            name: "win".into(),
            cell: Cell::Fail,
            started: None,
        });
        assert_eq!(step(&red, &Cover::All, &ok, Some(H), None).1, "CI 紅:win");
        // 4 沒收據 / 收據不算
        assert!(step(&p, &Cover::All, &[], Some(H), None)
            .1
            .starts_with("跑同頭收據"));
        let bad = vec![("T.txt".to_string(), Receipt::Bad("缺 test".into()))];
        assert_eq!(
            step(&p, &Cover::All, &bad, Some(H), None).1,
            "收據不算(缺 test):T.txt"
        );
        // 5 審查判不出來 ⇒ 等(不當成可以合)
        assert_eq!(
            step(&p, &Cover::Unknown("x".into()), &ok, Some(H), None).0,
            Who::Wait
        );
        // 6 疊在別張
        let mut st = pr();
        st.base = "feat/aib-plan-suggest-a63ab-1112".into();
        assert_eq!(
            step(&st, &Cover::All, &ok, None, Some(731)).1,
            "等 #731 合(疊在 feat/aib-plan-suggest-a63ab-1112)"
        );
        // 7 守望沒掛 / 掛舊頭
        assert_eq!(
            step(&p, &Cover::All, &ok, None, None).1,
            "報頭碼給 f1fae 掛守望"
        );
        assert_eq!(
            step(&p, &Cover::All, &ok, Some(C1), None).1,
            "守望掛舊頭 2114b286,報新頭"
        );
        // 8 跑中
        let mut run = pr();
        run.checks[0] = Check {
            name: "rust (macos-latest)".into(),
            cell: Cell::Running,
            started: Some(now - 23 * 60 - 5),
        };
        assert_eq!(
            step(&run, &Cover::All, &ok, Some(H), None),
            (Who::Wait, "等 CI(macOS 跑 23 分)".into())
        );
        // 9 沒擋
        assert_eq!(step(&p, &Cover::All, &ok, Some(H), None).0, Who::Clear);
    }

    #[test]
    fn rows_sort_me_first_then_newest() {
        let mk = |n: u32, who| Row {
            pr: Pr { number: n, ..pr() },
            cover: Cover::All,
            receipts: vec![],
            watch: None,
            stacked_on: None,
            who,
            next: String::new(),
        };
        let mut rows = vec![
            mk(1, Who::Clear),
            mk(2, Who::Me),
            mk(3, Who::Wait),
            mk(4, Who::Me),
        ];
        sort_rows(&mut rows);
        let order: Vec<u32> = rows.iter().map(|r| r.pr.number).collect();
        assert_eq!(order, [4, 2, 3, 1]);
        let f = facts(&rows[0]);
        assert_eq!(f, "審 ✓  收據 —  CI 1✓ 0✗ 0…  守望 —");
    }
}
