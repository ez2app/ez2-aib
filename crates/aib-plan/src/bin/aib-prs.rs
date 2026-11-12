// SPDX-License-Identifier: Apache-2.0
//! `aib-prs`:開發席座艙「我的 PR 列」(Andy 11-13 06:3x「先做開發席 PR 列」)。
//!
//! 每張 PR 3 行：號碼與標題 / 審查、收據、CI、守望 / 下一步一句(紅 = 要我動手、黃 = 等別人、綠 = 沒擋)。
//! 判斷全在 `aib_plan::prs`;這裡只讀資料與畫面。
//!
//! ```text
//! aib-prs --seat 519d0,a63ab [--all] [--once [--width N]] [--repo ~/ez2-aib] [--pending ~/.ez2-aib/pending]
//! ```
//! - 只讀：跑 `gh pr list`、`git fetch`(每 5 分最多一次)、`git rev-list`、`ps`;不寫任何狀態、不碰租約。
//! - 每 60 秒在背景執行緒拿一次快照(上一次還沒回來就不再開);畫面節奏靠 event::poll(ADR 0005,不 sleep)。
//! - 審查稿只讀首行;收據只讀檔名含頭 8 碼的檔。

use aib_plan::prs::{self, Pr, Receipt, Review, Row, Who};
use aib_plan::{fit, pad, str_width};
use crossterm::{
    cursor,
    event::{self, DisableBracketedPaste, EnableBracketedPaste, Event, KeyCode, KeyModifiers},
    execute, queue,
    style::{Color, Print, ResetColor, SetForegroundColor},
    terminal,
};
use std::collections::{HashMap, HashSet};
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const GH_FIELDS: &str =
    "number,title,headRefName,headRefOid,baseRefName,mergeable,statusCheckRollup,commits";
const REFRESH: Duration = Duration::from_secs(60);
const FETCH_EVERY: i64 = 300;

struct Opt {
    seats: Vec<String>,
    all: bool,
    once: bool,
    width: usize,
    repo: PathBuf,
    pending: PathBuf,
}

#[derive(Default)]
struct Snap {
    at: i64,
    rows: Vec<Row>,
    err: Option<String>,
    /// 不致命但要讓人知道(git fetch 失敗 ⇒ 衝突 / 合併涵蓋用的是舊 origin;1ced7 #734 L1)
    warn: Option<String>,
}

/// 跨快照的快取:git 範圍展開(頭不變、範圍不變就不重算)、上次 fetch 時間
#[derive(Default)]
struct Cache {
    sets: HashMap<String, Option<HashSet<String>>>,
    fetched: i64,
}

fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_secs() as i64)
}

fn home() -> PathBuf {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_default()
}

fn run(cmd: &str, args: &[&str], dir: &Path) -> Result<String, String> {
    let o = Command::new(cmd)
        .args(args)
        .current_dir(dir)
        .stdin(Stdio::null())
        .output()
        .map_err(|e| format!("{cmd}: {e}"))?;
    if !o.status.success() {
        let e = String::from_utf8_lossy(&o.stderr);
        return Err(format!("{cmd} rc={:?}: {}", o.status.code(), e.trim()));
    }
    Ok(String::from_utf8_lossy(&o.stdout).into_owned())
}

/// `git rev-list <b> --not <a>` 的集合;展不開 ⇒ None(判不出來，不當成空)
fn rev_set(repo: &Path, b: &str, not: &str, cache: &mut Cache) -> Option<HashSet<String>> {
    let key = format!("{not}..{b}");
    if let Some(v) = cache.sets.get(&key) {
        return v.clone();
    }
    let v = run("git", &["rev-list", b, "--not", not], repo)
        .ok()
        .map(|o| o.lines().map(str::to_string).collect::<HashSet<_>>());
    // 展不開的不快取(下次 fetch 後可能就有了)
    if v.is_some() {
        cache.sets.insert(key, v.clone());
    }
    v
}

/// 這張 PR 裡「只把 base 接進來」的合併 commit:第二個父在 origin/<base> 上、remerge-diff 0 行
/// (f1fae 規則 merge-only 不重審;c477a 11-13)。結果照合併 commit 快取(內容不會變)。
fn merged_base(repo: &Path, p: &Pr, cache: &mut Cache) -> HashSet<String> {
    let base = format!("origin/{}", p.base);
    let Ok(list) = run(
        "git",
        &["rev-list", "--merges", "--parents", &p.head, "--not", &base],
        repo,
    ) else {
        return HashSet::new();
    };
    let mut out = HashSet::new();
    for l in list.lines() {
        let w: Vec<&str> = l.split_whitespace().collect();
        let (Some(m), Some(p2)) = (w.first(), w.get(2)) else {
            continue;
        };
        let key = format!("merge:{m}");
        let clean = match cache.sets.get(&key) {
            Some(v) => v.is_some(),
            None => {
                let from_base = Command::new("git")
                    .args(["merge-base", "--is-ancestor", p2, &base])
                    .current_dir(repo)
                    .stdin(Stdio::null())
                    .status()
                    .is_ok_and(|s| s.success());
                let quiet = from_base
                    && run("git", &["show", "--remerge-diff", "--format=", m], repo)
                        .is_ok_and(|d| d.trim().is_empty());
                // 只快取乾淨的:不乾淨可能只是本機 origin 還沒追上(1ced7 #734 L2),fetch 後要能重判
                if quiet {
                    cache.sets.insert(key, Some(HashSet::new()));
                }
                quiet
            }
        };
        if clean {
            out.insert(m.to_string());
        }
    }
    out
}

/// 本機試合:`git merge-tree --write-tree`(不加 --quiet:c477a 踩過 --quiet 真衝突也回 0)。
/// rc 0 = 乾淨、1 = 衝突、其他(物件不在)= 判不出來，沿用 gh 的 mergeable(gh 常回 UNKNOWN)
fn local_mergeable(repo: &Path, p: &Pr) -> Option<&'static str> {
    let st = Command::new("git")
        .args([
            "merge-tree",
            "--write-tree",
            &format!("origin/{}", p.base),
            &p.head,
        ])
        .current_dir(repo)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .ok()?;
    match st.code() {
        Some(0) => Some("MERGEABLE"),
        Some(1) => Some("CONFLICTING"),
        _ => None,
    }
}

fn first_line(p: &Path) -> Option<String> {
    let f = std::fs::File::open(p).ok()?;
    std::io::BufReader::new(f).lines().next()?.ok()
}

fn snapshot(o: &Opt, cache: &mut Cache) -> Snap {
    let t = now();
    let gh = match run(
        "gh",
        &[
            "pr", "list", "--state", "open", "--limit", "100", "--json", GH_FIELDS,
        ],
        &o.repo,
    )
    .and_then(|s| prs::parse_gh(&s))
    {
        Ok(v) => v,
        Err(e) => {
            return Snap {
                at: t,
                rows: vec![],
                err: Some(e),
                warn: None,
            }
        }
    };
    let mut warn = None;
    if t - cache.fetched >= FETCH_EVERY {
        match run("git", &["fetch", "-q", "origin"], &o.repo) {
            Ok(_) => cache.fetched = t,
            Err(e) => warn = Some(format!("git fetch 失敗，衝突 / 涵蓋照舊 origin 判:{e}")),
        }
    }
    let by_branch: HashMap<&str, u32> = gh.iter().map(|p| (p.branch.as_str(), p.number)).collect();
    let picked: Vec<&Pr> = gh
        .iter()
        .filter(|p| o.all || prs::mine(&p.branch, &o.seats))
        .collect();
    let wanted: HashSet<&str> = picked
        .iter()
        .flat_map(|p| p.commits.iter().map(String::as_str))
        .collect();
    // 審查稿：首行頭碼是任何一張我的 PR 的 commit 才留
    let mut reviews: Vec<Review> = Vec::new();
    let mut receipt_files: Vec<(String, PathBuf)> = Vec::new();
    if let Ok(rd) = std::fs::read_dir(&o.pending) {
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().into_owned();
            if name.starts_with("REVIEW-") && name.ends_with(".md") {
                if let Some(r) = first_line(&e.path()).and_then(|l| prs::parse_review(&name, &l)) {
                    if wanted.contains(r.head.as_str()) {
                        reviews.push(r);
                    }
                }
            } else if name.starts_with("RECEIPT-") || name.starts_with("TEST-") {
                receipt_files.push((name, e.path()));
            }
        }
    }
    let ps = run("ps", &["-Ao", "args="], &o.repo).unwrap_or_default();
    let watch = prs::watches(&ps);
    let mut rows = Vec::new();
    for p in picked {
        let mut p = p.clone();
        p.mergeable = prs::pick_mergeable(&p.mergeable, local_mergeable(&o.repo, &p));
        let p = &p;
        let mine_reviews: Vec<(Review, Option<HashSet<String>>)> = reviews
            .iter()
            .filter(|r| p.commits.contains(&r.head))
            .map(|r| {
                let set = match &r.range {
                    Some((a, b)) => rev_set(&o.repo, b, a, cache),
                    None => rev_set(&o.repo, &r.head, &format!("origin/{}", p.base), cache),
                };
                (r.clone(), set)
            })
            .collect();
        let merged = merged_base(&o.repo, p, cache);
        let cover = prs::coverage(p, &mine_reviews, &merged);
        let short = &p.head[..p.head.len().min(8)];
        let mut receipts: Vec<(String, Receipt)> = receipt_files
            .iter()
            .filter(|(n, _)| !short.is_empty() && n.contains(short))
            .filter_map(|(n, path)| {
                let text = std::fs::read_to_string(path).ok()?;
                // 檔名有頭 8 碼、內容卻不認得 ⇒ 標格式不明，不當成沒有(c477a 11-13)
                let r = prs::judge_receipt(&text, &p.head)
                    .unwrap_or_else(|| Receipt::Bad("格式不明".into()));
                Some((n.clone(), r))
            })
            .collect();
        receipts.sort_by_key(|(_, r)| *r != Receipt::Ok);
        let stacked = (p.base != "main")
            .then(|| by_branch.get(p.base.as_str()).copied())
            .flatten();
        rows.push(prs::build_row(
            p.clone(),
            cover,
            receipts,
            watch.get(&p.number).cloned(),
            stacked,
            t,
        ));
    }
    prs::sort_rows(&mut rows);
    Snap {
        at: t,
        rows,
        err: None,
        warn,
    }
}

/// 一行 = (文字, 顏色)
fn lines(s: &Snap, o: &Opt, width: usize, t: i64) -> Vec<(String, Color)> {
    let mut out = Vec::new();
    let n = |w: Who| s.rows.iter().filter(|r| r.who == w).count();
    let scope = if o.all {
        "全艙".to_string()
    } else {
        o.seats.join(",")
    };
    let age = if s.at == 0 {
        "讀取中".to_string()
    } else {
        format!("{} 秒前", (t - s.at).max(0))
    };
    out.push((
        fit(
            &format!(
                "PR 列 {scope} · {} 張 · 要我動手 {} · 等 {} · 沒擋 {} · {age}",
                s.rows.len(),
                n(Who::Me),
                n(Who::Wait),
                n(Who::Clear)
            ),
            width,
        ),
        Color::White,
    ));
    if let Some(e) = &s.err {
        out.push((fit(&format!("讀不到:{e}"), width), Color::Red));
    }
    if let Some(w) = &s.warn {
        out.push((fit(w, width), Color::Yellow));
    }
    for r in &s.rows {
        let tail = match r.stacked_on {
            Some(n) => format!(" {} 疊#{n}", &r.pr.head[..8.min(r.pr.head.len())]),
            None => format!(" {}", &r.pr.head[..8.min(r.pr.head.len())]),
        };
        let left = format!("#{} {}", r.pr.number, r.pr.title);
        let lw = width.saturating_sub(str_width(&tail));
        out.push((format!("{}{tail}", pad(&left, lw)), Color::Cyan));
        out.push((fit(&format!("  {}", prs::facts(r)), width), Color::DarkGrey));
        let c = match r.who {
            Who::Me => Color::Red,
            Who::Wait => Color::Yellow,
            Who::Clear => Color::Green,
        };
        out.push((fit(&format!("  ▶ {}", r.next), width), c));
    }
    if s.rows.is_empty() && s.err.is_none() && s.at != 0 {
        out.push(("沒有開著的 PR".into(), Color::DarkGrey));
    }
    out
}

fn restore() {
    let _ = terminal::disable_raw_mode();
    let _ = execute!(
        std::io::stdout(),
        DisableBracketedPaste,
        cursor::Show,
        terminal::LeaveAlternateScreen
    );
}

fn draw(s: &Snap, o: &Opt) -> std::io::Result<()> {
    let (w, h) = terminal::size().unwrap_or((80, 24));
    let mut out = std::io::stdout();
    queue!(out, terminal::Clear(terminal::ClearType::All))?;
    for (y, (t, c)) in lines(s, o, w as usize, now())
        .into_iter()
        .take(h as usize)
        .enumerate()
    {
        queue!(
            out,
            cursor::MoveTo(0, y as u16),
            SetForegroundColor(c),
            Print(t),
            ResetColor
        )?;
    }
    out.flush()
}

fn parse_args() -> Result<Opt, String> {
    let mut o = Opt {
        seats: vec![],
        all: false,
        once: false,
        width: 100,
        repo: home().join("ez2-aib"),
        pending: home().join(".ez2-aib").join("pending"),
    };
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        let mut val = || it.next().ok_or(format!("missing_value: {a}"));
        match a.as_str() {
            "--seat" => {
                o.seats = val()?
                    .split(',')
                    .map(|s| s.trim().to_string())
                    .filter(|s| !s.is_empty())
                    .collect()
            }
            "--all" => o.all = true,
            "--once" => o.once = true,
            "--width" => o.width = val()?.parse().map_err(|_| "bad_width")?,
            "--repo" => o.repo = PathBuf::from(val()?),
            "--pending" => o.pending = PathBuf::from(val()?),
            _ => return Err(format!("unknown_flag: {a}")),
        }
    }
    if o.seats.is_empty() && !o.all {
        return Err("要 --seat <席碼[,席碼]> 或 --all".into());
    }
    Ok(o)
}

fn main() {
    let o = match parse_args() {
        Ok(o) => o,
        Err(e) => {
            eprintln!("aib-prs: {e}\nusage: aib-prs --seat 519d0[,a63ab] [--all] [--once [--width N]] [--repo DIR] [--pending DIR]");
            std::process::exit(2);
        }
    };
    if o.once {
        let s = snapshot(&o, &mut Cache::default());
        for (t, _) in lines(&s, &o, o.width, now()) {
            println!("{t}");
        }
        std::process::exit(if s.err.is_some() { 1 } else { 0 });
    }
    let o = Arc::new(o);
    let cache = Arc::new(Mutex::new(Cache::default()));
    let (tx, rx) = mpsc::channel::<Snap>();
    let mut snap = Snap::default();
    let mut inflight = false;
    let mut last: Option<Instant> = None;
    let _ = terminal::enable_raw_mode();
    let _ = execute!(
        std::io::stdout(),
        terminal::EnterAlternateScreen,
        cursor::Hide,
        // 拖檔進窗格 ⇒ 整段一個 Paste 事件、丟掉，不當按鍵(1ced7 tui-paste 同一課;放最後，舊 Windows 主控台會錯)
        EnableBracketedPaste
    );
    std::panic::set_hook(Box::new(|i| {
        if std::thread::current().name() == Some("main") {
            restore();
        }
        eprintln!("{i}");
    }));
    let mut dirty = true;
    loop {
        if !inflight && last.is_none_or(|l| l.elapsed() >= REFRESH) {
            inflight = true;
            last = Some(Instant::now());
            let (o2, c2, tx2) = (Arc::clone(&o), Arc::clone(&cache), tx.clone());
            std::thread::spawn(move || {
                let s = match c2.lock() {
                    Ok(mut c) => snapshot(&o2, &mut c),
                    Err(_) => Snap {
                        at: now(),
                        rows: vec![],
                        err: Some("cache_poisoned".into()),
                        warn: None,
                    },
                };
                let _ = tx2.send(s);
            });
        }
        if let Ok(s) = rx.try_recv() {
            snap = s;
            inflight = false;
            dirty = true;
        }
        if dirty {
            let _ = draw(&snap, &o);
            dirty = false;
        }
        // 節奏靠 event::poll(ADR 0005):1 秒一拍，讓「n 秒前」走得動
        if event::poll(Duration::from_secs(1)).unwrap_or(false) {
            match event::read() {
                Ok(Event::Key(k)) => match (k.code, k.modifiers) {
                    (KeyCode::Char('q'), _) | (KeyCode::Char('c'), KeyModifiers::CONTROL) => break,
                    (KeyCode::Char('r'), _) => last = None,
                    _ => {}
                },
                Ok(Event::Resize(..)) => dirty = true,
                _ => {} // 貼上(Event::Paste)與其他事件丟掉
            }
        } else {
            dirty = true;
        }
    }
    restore();
}
