// SPDX-License-Identifier: Apache-2.0
//! `aib-hosts`:測試機一台一顆電池，放在 tmux 下欄(Andy 11-12 05:0x:「每台主機顯示成一個動態電池，
//! 真實的顯示與動態變化」;05:2x:四台都要、用看得懂的名字、標題下空一行、點電池看排隊與誰在跑)。
//!
//! - 每台每 20 秒讀一次 `aib lease status --json` 與 `sysctl -n hw.ncpu vm.loadavg`:別台用 ssh,
//!   本機(目標寫 `local`)直接跑。一次性執行緒(ADR 0005 不用 `thread::sleep`);卡住 60 秒顯示
//!   「?」、不重起。
//! - 電池 = 這台還能給別人用的量:空機滿格綠色;有人租著 = 租約剩餘時間比例，隨時間往下掉(五段:
//!   黃綠 / 黃 / 橘 / 深橘 / 紅),有一格亮點來回跑;連不上 = 空殼紅色「離線」。負載用最近 30 筆畫小折線。
//! - 收起時每台固定 3 行(名字電池 / 負載 / 在做什麼),離線與讀取中也一樣高。
//! - 滑鼠點一台(或按 1–9)展開 / 收起：正在跑的是誰、做什麼、跑多久;排隊的每一位是誰、做什麼、
//!   等多久、有沒有優先。
//! - 主機參數 `名字=目標`(例 `"Pro 13=user@host"`);沒給就讀 ~/.ez2-aib/hosts.json,只寫目標就用目標當名字。
//! - 只讀：不拿租約、不寫任何狀態。`--once [--width N] [--open]` 印一次就結束(測試用)。
//! - ssh 一律 `ForwardAgent=no` / `ClearAllForwardings=yes` / `BatchMode=yes`。

use crossterm::{
    cursor, event,
    event::{DisableMouseCapture, EnableMouseCapture, Event, KeyCode, MouseButton, MouseEventKind},
    execute, queue,
    style::{Color, Print, ResetColor, SetForegroundColor},
    terminal,
};
use serde_json::Value;
use std::io::Write;
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// 測試機清單讀私有設定 `~/.ez2-aib/hosts.json`(`AIB_HOSTS_FILE`),跟 `aib-lease hosts` 同一份;
/// 公開倉不寫主機位址與帳號(Andy 11-12 14:5x 加 GitHub 與 Windows 時一起改)。
/// 欄位:name / ssh(`local` = 本機)/ os(`windows`)/ order / identity / jump_fallback;
/// 頂層 `github_repo` ⇒ 多一列 GitHub(CI 執行機)。
fn config_path() -> std::path::PathBuf {
    if let Some(p) = std::env::var_os("AIB_HOSTS_FILE") {
        return p.into();
    }
    let home = std::env::var_os("HOME").unwrap_or_default();
    std::path::Path::new(&home)
        .join(".ez2-aib")
        .join("hosts.json")
}

fn hosts_from_config(text: &str) -> Vec<Host> {
    let Ok(v) = serde_json::from_str::<Value>(text) else {
        return Vec::new();
    };
    let mut rows: Vec<(i64, usize, Host)> = Vec::new();
    for (i, h) in v["hosts"].as_array().into_iter().flatten().enumerate() {
        let (Some(name), Some(ssh)) = (h["name"].as_str(), h["ssh"].as_str()) else {
            continue;
        };
        let mut host = Host::new(&format!("{name}={ssh}"));
        if h["os"].as_str() == Some("windows") {
            host.kind = Kind::Windows;
        }
        host.identity = h["identity"].as_str().map(str::to_string);
        host.jump_fallback = h["jump_fallback"].as_str().map(str::to_string);
        rows.push((h["order"].as_i64().unwrap_or(i64::MAX), i, host));
    }
    rows.sort_by_key(|(o, i, _)| (*o, *i));
    let mut out: Vec<Host> = rows.into_iter().map(|(_, _, h)| h).collect();
    if let Some(repo) = v["github_repo"].as_str() {
        let mut g = Host::new(&format!("GitHub={repo}"));
        g.kind = Kind::GitHub;
        out.push(g);
    }
    out
}

const POLL: Duration = Duration::from_secs(20);
const STUCK: Duration = Duration::from_secs(60);
const SPARK: &[char] = &['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█'];
const NAME_W: usize = 15;
/// Windows(OpenSSH 預設 cmd.exe):CPU 使用率 % 與邏輯核數;沒有 aib 租約系統
const WIN_REMOTE: &str = "powershell -NoProfile -Command \"Write-Output ('AIB_WIN ' + \
                          (Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average \
                          + ' ' + (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors)\"";
const REMOTE: &str = "PATH=$HOME/.local/bin:$PATH; aib lease status --json 2>/dev/null; \
                      echo AIB_HOSTS_SYS $(sysctl -n hw.ncpu) $(sysctl -n vm.loadavg) \
                      $(sysctl -n hw.perflevel0.physicalcpu 2>/dev/null)";

#[derive(Debug, Clone, Default, PartialEq)]
struct Waiter {
    owner: String,
    what: String,
    since: i64,
    priority: bool,
}

#[derive(Debug, Clone, Copy, Default, PartialEq)]
enum Kind {
    #[default]
    Mac,
    Windows,
    /// GitHub Actions(CI 執行機):看執行中 / 排隊中的工作
    GitHub,
}

#[derive(Debug, Clone, Default, PartialEq)]
struct Sample {
    online: bool,
    held: bool,
    owner: String,
    what: String,
    acquired: i64,
    expiry: i64,
    queue: Vec<Waiter>,
    ncpu: f64,
    load1: f64,
    load5: f64,
    /// 效能核數(Apple 晶片);讀不到 = 0,改用 ncpu
    perf: f64,
    /// 這台沒有 aib 租約系統(Windows)
    no_lease: bool,
    /// GitHub:(執行中, 排隊中)
    gh: Option<(u32, u32)>,
    /// GitHub 的工作(owner = 分支、what = 工作流程名、since = 建立時間、priority = 排隊中)
    runs: Vec<Waiter>,
    err: String,
}

#[derive(Clone)]
struct Host {
    name: String,
    target: String,
    last: Option<Sample>,
    at: Option<Instant>,
    loads: Vec<f64>,
    kind: Kind,
    identity: Option<String>,
    jump_fallback: Option<String>,
    asked: Option<Instant>,
    stuck: bool,
    open: bool,
}

impl Host {
    fn new(arg: &str) -> Host {
        let (name, target) = match arg.split_once('=') {
            Some((n, t)) if !n.trim().is_empty() && !t.trim().is_empty() => {
                (n.trim().to_string(), t.trim().to_string())
            }
            _ => (arg.to_string(), arg.to_string()),
        };
        Host {
            name,
            target,
            last: None,
            at: None,
            loads: Vec::new(),
            kind: Kind::Mac,
            identity: None,
            jump_fallback: None,
            asked: None,
            stuck: false,
            open: false,
        }
    }
}

fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn owner_of(v: &Value) -> String {
    v["owner"]
        .as_str()
        .unwrap_or("")
        .trim_start_matches('@')
        .to_string()
}

/// 輸出 = lease JSON(多行)+ `AIB_HOSTS_SYS <ncpu> { l1 l5 l15 }`。
fn parse_sample(out: &str, ok: bool, err: &str) -> Sample {
    let mut s = Sample {
        err: err.lines().last().unwrap_or("").trim().to_string(),
        ..Sample::default()
    };
    let (json, sys) = match out.find("AIB_HOSTS_SYS ") {
        Some(i) => (&out[..i], &out[i + "AIB_HOSTS_SYS ".len()..]),
        None => (out, ""),
    };
    let nums: Vec<f64> = sys
        .split_whitespace()
        .filter_map(|w| w.trim_matches(|c| c == '{' || c == '}').parse().ok())
        .collect();
    if let Some(n) = nums.first() {
        s.ncpu = *n;
    }
    if let Some(l) = nums.get(1) {
        s.load1 = *l;
    }
    if let Some(l) = nums.get(2) {
        s.load5 = *l;
    }
    if let Some(p) = nums.get(4) {
        s.perf = *p;
    }
    if let Ok(v) = serde_json::from_str::<Value>(json.trim()) {
        s.online = true;
        s.held = v["state"] == "held";
        let l = &v["lease"];
        s.owner = owner_of(l);
        s.what = l["what"].as_str().unwrap_or("").to_string();
        s.acquired = l["acquired_epoch"].as_i64().unwrap_or(0);
        s.expiry = l["expiry_epoch"].as_i64().unwrap_or(0);
        s.queue = v["queue"]
            .as_array()
            .map(|a| {
                a.iter()
                    .map(|q| Waiter {
                        owner: owner_of(q),
                        what: q["what"].as_str().unwrap_or("").to_string(),
                        since: q["since_epoch"].as_i64().unwrap_or(0),
                        priority: q["priority"].as_bool().unwrap_or(false),
                    })
                    .collect()
            })
            .unwrap_or_default();
    } else {
        s.online = ok && !nums.is_empty();
    }
    s
}

fn parse_win(out: &str, ok: bool, err: &str) -> Sample {
    let mut s = Sample {
        err: err.lines().last().unwrap_or("").trim().to_string(),
        no_lease: true,
        ..Sample::default()
    };
    let nums: Vec<f64> = out
        .lines()
        .find_map(|l| l.trim().strip_prefix("AIB_WIN "))
        .map(|r| {
            r.split_whitespace()
                .filter_map(|w| w.parse().ok())
                .collect()
        })
        .unwrap_or_default();
    if let (true, [pct, n, ..]) = (ok, nums.as_slice()) {
        s.online = *n > 0.0;
        s.ncpu = *n;
        s.perf = *n;
        s.load1 = pct / 100.0 * n;
        s.load5 = s.load1;
    }
    s
}

/// `2026-11-12T06:40:00Z` ⇒ epoch 秒;讀不到 0
fn iso_epoch(t: &str) -> i64 {
    let n = |r: std::ops::Range<usize>| t.get(r).and_then(|x| x.parse::<i64>().ok());
    let (Some(y), Some(m), Some(d), Some(hh), Some(mm), Some(ss)) =
        (n(0..4), n(5..7), n(8..10), n(11..13), n(14..16), n(17..19))
    else {
        return 0;
    };
    let (y, m) = if m <= 2 { (y - 1, m + 9) } else { (y, m - 3) };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let doy = (153 * m + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    (era * 146_097 + doe - 719_468) * 86400 + hh * 3600 + mm * 60 + ss
}

fn parse_gh(out: &str, ok: bool, err: &str) -> Sample {
    let mut s = Sample {
        err: err.lines().last().unwrap_or("").trim().to_string(),
        ..Sample::default()
    };
    let Ok(Value::Array(rows)) = serde_json::from_str::<Value>(out.trim()) else {
        return s;
    };
    s.online = ok;
    let (mut run, mut wait) = (0, 0);
    for r in rows {
        let queued = match r["status"].as_str() {
            Some("in_progress") => false,
            Some("queued" | "waiting" | "pending" | "requested") => true,
            _ => continue,
        };
        if queued {
            wait += 1;
        } else {
            run += 1;
        }
        s.runs.push(Waiter {
            owner: r["headBranch"].as_str().unwrap_or("").to_string(),
            what: r["name"].as_str().unwrap_or("").to_string(),
            since: iso_epoch(r["createdAt"].as_str().unwrap_or("")),
            priority: queued,
        });
    }
    s.gh = Some((run, wait));
    s
}

/// 一台要怎麼問:種類、目標、ssh 金鑰、跳板(直連網路失敗 rc 255 才用)
#[derive(Debug, Clone)]
struct Probe {
    kind: Kind,
    target: String,
    identity: Option<String>,
    jump_fallback: Option<String>,
}

impl Host {
    fn probe(&self) -> Probe {
        Probe {
            kind: self.kind,
            target: self.target.clone(),
            identity: self.identity.clone(),
            jump_fallback: self.jump_fallback.clone(),
        }
    }
}

fn ssh_run(p: &Probe, cmd: &str, jump: Option<&str>) -> std::io::Result<std::process::Output> {
    let mut c = Command::new("ssh");
    c.args([
        "-o",
        "ForwardAgent=no",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
    ]);
    if let Some(i) = &p.identity {
        c.args(["-i", i.as_str()]);
    }
    if let Some(j) = jump {
        c.args(["-J", j]);
    }
    c.args(["--", p.target.as_str(), cmd])
        .stdin(Stdio::null())
        .output()
}

fn ask(idx: usize, p: Probe, tx: mpsc::Sender<(usize, Sample)>) {
    std::thread::spawn(move || {
        let o = match p.kind {
            Kind::GitHub => Command::new("gh")
                .args([
                    "run",
                    "list",
                    "-R",
                    p.target.as_str(),
                    "--limit",
                    "50",
                    "--json",
                    "status,name,headBranch,createdAt",
                ])
                .stdin(Stdio::null())
                .output(),
            _ if p.target == "local" => Command::new("/bin/sh")
                .args(["-c", REMOTE])
                .stdin(Stdio::null())
                .output(),
            k => {
                let cmd = if k == Kind::Windows {
                    WIN_REMOTE
                } else {
                    REMOTE
                };
                match ssh_run(&p, cmd, None) {
                    Ok(o) if o.status.code() == Some(255) && p.jump_fallback.is_some() => {
                        ssh_run(&p, cmd, p.jump_fallback.as_deref())
                    }
                    o => o,
                }
            }
        };
        let s = match o {
            Ok(o) => {
                let (out, err) = (
                    String::from_utf8_lossy(&o.stdout),
                    String::from_utf8_lossy(&o.stderr),
                );
                match p.kind {
                    Kind::GitHub => parse_gh(&out, o.status.success(), &err),
                    Kind::Windows => parse_win(&out, o.status.success(), &err),
                    Kind::Mac => parse_sample(&out, o.status.success(), &err),
                }
            }
            Err(e) => Sample {
                err: e.to_string(),
                ..Sample::default()
            },
        };
        let _ = tx.send((idx, s));
    });
}

/// 字寬:CJK / 全形算 2。
fn w(s: &str) -> usize {
    s.chars()
        .map(|c| match c as u32 {
            0x1100..=0x115F
            | 0x2E80..=0xA4CF
            | 0xAC00..=0xD7A3
            | 0xF900..=0xFAFF
            | 0xFE30..=0xFE4F
            | 0xFF00..=0xFF60
            | 0xFFE0..=0xFFE6 => 2,
            _ => 1,
        })
        .sum()
}

fn fit(s: &str, max: usize) -> String {
    let mut o = String::new();
    let mut n = 0;
    for c in s.chars() {
        let cw = w(&c.to_string());
        if n + cw > max {
            break;
        }
        n += cw;
        o.push(c);
    }
    o
}

/// 沒人拿租約、負載卻超過每效能核 0.8 ⇒ 有人沒拿租約在跑(Andy 11-12 14:3x;a63ab 在 Pro 16 犯過)。
/// 算法同 aib-lease hosts:max(load1, load5 × 0.8) / 效能核
const UNLEASED_BUSY_RATIO: f64 = 0.8;

fn unleased_busy(s: &Sample) -> bool {
    let cores = if s.perf > 0.0 { s.perf } else { s.ncpu };
    s.online && !s.held && cores > 0.0 && s.load1.max(s.load5 * 0.8) / cores > UNLEASED_BUSY_RATIO
}

/// GitHub 那一列：第 2 行執行中 / 排隊數，第 3 行第一個在跑的;展開列出全部
fn gh_lines(mut out: Vec<Line>, s: Option<&Sample>, open: bool, room: usize, t: i64) -> Vec<Line> {
    let Some(s) = s.filter(|s| s.online) else {
        out.push(vec![pad(), ("工作 —".into(), Color::DarkGrey)]);
        let why = s.map_or("第一次讀取中".to_string(), |s| {
            format!("讀不到 GitHub:{}", s.err)
        });
        out.push(vec![pad(), (fit(&why, room), Color::DarkGrey)]);
        return out;
    };
    let (r, q) = s.gh.unwrap_or((0, 0));
    out.push(vec![
        pad(),
        (format!("工作 執行中 {r} · 排隊 {q}"), Color::DarkGrey),
    ]);
    let line = |w: &Waiter| {
        let tag = if w.priority { "排隊" } else { "▶" };
        let age = if w.since > 0 {
            format!(" · {}", mins(t - w.since))
        } else {
            String::new()
        };
        format!("{tag} {} · {}{age}", w.what, w.owner)
    };
    if !open {
        let first = s.runs.iter().find(|w| !w.priority).or(s.runs.first());
        let third = first.map_or("沒有工作在跑".to_string(), line);
        out.push(vec![pad(), (fit(&third, room), Color::DarkGrey)]);
        return out;
    }
    if s.runs.is_empty() {
        out.push(vec![pad(), ("▶ 沒有工作在跑".into(), Color::Green)]);
    }
    for w in &s.runs {
        let c = if w.priority {
            Color::Cyan
        } else {
            Color::Yellow
        };
        out.push(vec![pad(), (fit(&line(w), room), c)]);
    }
    out
}

/// 電池剩多少(0..=100)與顏色。
fn charge(s: Option<&Sample>, t: i64) -> (u32, Color) {
    match s {
        None => (0, Color::DarkGrey),
        Some(s) if !s.online => (0, Color::Red),
        // GitHub:有排隊 = 執行機都在用(空);只有執行中 = 還有空位(半格黃);都沒有 = 滿格綠
        Some(Sample {
            gh: Some((_, q)), ..
        }) if *q > 0 => (0, Color::Red),
        Some(Sample {
            gh: Some((r, _)), ..
        }) if *r > 0 => (50, Color::Yellow),
        Some(Sample { gh: Some(_), .. }) => (100, Color::Green),
        Some(s) if unleased_busy(s) => (100, Color::Magenta),
        Some(s) if !s.held => (100, Color::Green),
        Some(s) => {
            let span = (s.expiry - s.acquired).max(1);
            let left = (s.expiry - t).clamp(0, span);
            let p = u32::try_from(left * 100 / span).unwrap_or(0);
            (p, held_color(p))
        }
    }
}

/// 有人租著時，剩多少分五段顏色(Andy 11-12 07:2x「百分比顏色分別要更多一點」):
/// 80 以上黃綠、60–79 黃、40–59 橘、20–39 深橘、20 以下紅。空機滿格用正綠，跟黃綠分得開。
fn held_color(p: u32) -> Color {
    match p {
        80.. => Color::AnsiValue(154),
        60..=79 => Color::AnsiValue(226),
        40..=59 => Color::AnsiValue(214),
        20..=39 => Color::AnsiValue(202),
        _ => Color::Red,
    }
}

type Line = Vec<(String, Color)>;

fn mins(secs: i64) -> String {
    if secs >= 3600 {
        format!("{}時{}分", secs / 3600, secs % 3600 / 60)
    } else {
        format!("{}分", (secs.max(0) + 59) / 60)
    }
}

fn pad() -> (String, Color) {
    (" ".repeat(NAME_W), Color::White)
}

/// 一台：名字 + 電池 + 狀態;負載折線;在做什麼;展開時加正在跑與排隊明細。
fn host_lines(h: &Host, idx: usize, width: usize, tick: u64, t: i64) -> Vec<Line> {
    let s = h.last.as_ref();
    let (pct, col) = charge(s, t);
    let cells = width.saturating_sub(48).clamp(10, 40);
    let filled = (pct as usize * cells).div_ceil(100).min(cells);
    let busy = s.is_some_and(|s| s.online && s.held);
    // 佔用中：亮點在已充的格子裡來回跑
    let spot = if busy && filled > 1 {
        let k = (tick as usize) % (2 * filled - 2);
        Some(if k < filled { k } else { 2 * filled - 2 - k })
    } else {
        None
    };
    let bar: String = (0..cells)
        .map(|i| match (i < filled, spot == Some(i)) {
            (true, true) => '▓',
            (true, false) => '█',
            _ => '░',
        })
        .collect();
    let mark = if h.open { '▾' } else { '▸' };
    let name = fit(&format!("{mark}{} {}", idx + 1, h.name), NAME_W - 1);
    let name_pad = " ".repeat(NAME_W.saturating_sub(w(&name)));
    let status = match s {
        None => "讀取中…".to_string(),
        Some(s) if !s.online => format!("離線 {}", fit(&s.err, 30)),
        Some(Sample {
            gh: Some((r, q)), ..
        }) if *q > 0 => format!("滿了 · 跑 {r} · 排隊 {q}"),
        Some(Sample {
            gh: Some((r, _)), ..
        }) => format!("跑 {r} · 還有空位"),
        Some(s) if unleased_busy(s) => "沒租約在跑 不可用".to_string(),
        Some(s) if s.no_lease => "空機 可用(沒有租約系統)".to_string(),
        Some(s) if !s.held => "空機 可用".to_string(),
        Some(s) => format!("{} 用 · 剩 {}", s.owner, mins(s.expiry - t)),
    };
    let stale = if h.stuck { " ?" } else { "" };
    let mut l1: Line = vec![
        (format!("{name}{name_pad}"), Color::White),
        (format!("▕{bar}▏"), col),
        ("▌".into(), col),
        (format!(" {pct:>3}% "), col),
        (format!("{status}{stale}"), Color::White),
    ];
    if let Some(s) = s.filter(|s| !s.queue.is_empty()) {
        l1.push((format!(" · 排隊 {}", s.queue.len()), Color::Cyan));
    }
    let cap = s.map_or(8.0, |s| if s.ncpu > 0.0 { s.ncpu } else { 8.0 });
    let spark: String = h
        .loads
        .iter()
        .map(|l| SPARK[((l / cap) * 7.0).round().clamp(0.0, 7.0) as usize])
        .collect();
    let online = s.is_some_and(|s| s.online);
    let load = s.filter(|s| s.online).map_or(String::new(), |s| {
        format!("負載 {:.1}/{:.0} 核 ", s.load1, s.ncpu)
    });
    let lcol = if s.is_some_and(|s| s.ncpu > 0.0 && s.load1 > s.ncpu) {
        Color::Red
    } else {
        Color::Blue
    };
    let mut out = vec![l1];
    let room = width.saturating_sub(NAME_W + 2);
    if h.kind == Kind::GitHub {
        return gh_lines(out, s, h.open, room, t);
    }
    // 每台收起時固定 3 行(Andy 11-12 07:2x「離線的主機跟其他主機的高度間距要一致」):
    // 第 2 行負載(離線 / 讀取中寫「負載 —」),第 3 行在做什麼 / 空機 / 離線原因。
    if online {
        out.push(vec![pad(), (load, Color::DarkGrey), (spark, lcol)]);
    } else {
        out.push(vec![pad(), ("負載 —".into(), Color::DarkGrey)]);
    }
    if !h.open {
        let third = match s {
            Some(s) if s.held => fit(&s.what, room),
            Some(s) if unleased_busy(s) => "負載高但沒人拿租約:有人沒拿租約就在跑".to_string(),
            Some(s) if s.online => "沒有人在跑".to_string(),
            Some(_) => "連不上:ssh 逾時或主機關機,20 秒後再試".to_string(),
            None => "第一次讀取中".to_string(),
        };
        out.push(vec![pad(), (fit(&third, room), Color::DarkGrey)]);
        return out;
    }
    // 展開：誰在跑、排隊明細
    match s {
        Some(s) if s.held => {
            out.push(vec![
                pad(),
                (
                    format!("▶ 正在跑 {} · 已 {}", s.owner, mins(t - s.acquired)),
                    Color::Yellow,
                ),
            ]);
            out.push(vec![
                pad(),
                (format!("  {}", fit(&s.what, room)), Color::White),
            ]);
        }
        Some(s) if s.online => out.push(vec![pad(), ("▶ 沒有人在跑".into(), Color::Green)]),
        _ => {}
    }
    match s {
        Some(s) if !s.queue.is_empty() => {
            for (i, q) in s.queue.iter().enumerate() {
                let pri = if q.priority { " · 優先" } else { "" };
                out.push(vec![
                    pad(),
                    (
                        format!("{}. {} · 等了 {}{pri}", i + 1, q.owner, mins(t - q.since)),
                        Color::Cyan,
                    ),
                ]);
                out.push(vec![
                    pad(),
                    (format!("   {}", fit(&q.what, room)), Color::DarkGrey),
                ]);
            }
        }
        Some(s) if s.online => out.push(vec![pad(), ("沒有人排隊".into(), Color::DarkGrey)]),
        _ => {}
    }
    out
}

/// 整張畫面，和每一行屬於哪一台(點擊用)。
fn frame(hosts: &[Host], width: usize, tick: u64, t: i64) -> (Vec<Line>, Vec<Option<usize>>) {
    let clock = {
        // 台北時間(UTC+8),不依賴本機時區
        let s = (t + 8 * 3600).rem_euclid(86400);
        format!("{:02}:{:02}:{:02}", s / 3600, s % 3600 / 60, s % 60)
    };
    let mut lines: Vec<Line> = vec![
        vec![
            (format!("測試機 {clock}(台北)"), Color::White),
            (
                "  綠 空機 · 紫 沒租約在跑 · GitHub 紅 = 排隊中 · 租約剩餘 黃綠→黃→橘→深橘→紅 · 空殼 離線 · 點電池(或按 1–9)看誰在跑、誰在排隊 · q 離開"
                    .into(),
                Color::DarkGrey,
            ),
        ],
        Vec::new(), // Andy:標題和第一顆電池之間空一行
    ];
    let mut owner: Vec<Option<usize>> = vec![None, None];
    for (i, h) in hosts.iter().enumerate() {
        for l in host_lines(h, i, width, tick, t) {
            lines.push(l);
            owner.push(Some(i));
        }
    }
    (lines, owner)
}

fn draw(o: &mut impl Write, lines: &[Line], width: usize, height: usize) -> std::io::Result<()> {
    for (y, l) in lines.iter().take(height).enumerate() {
        queue!(o, cursor::MoveTo(0, y as u16))?;
        let mut used = 0;
        for (txt, c) in l {
            let room = width.saturating_sub(used);
            if room == 0 {
                break;
            }
            let t = fit(txt, room);
            used += w(&t);
            queue!(o, SetForegroundColor(*c), Print(t))?;
        }
        queue!(
            o,
            ResetColor,
            terminal::Clear(terminal::ClearType::UntilNewLine)
        )?;
    }
    queue!(o, terminal::Clear(terminal::ClearType::FromCursorDown))?;
    o.flush()
}

fn restore() {
    let _ = terminal::disable_raw_mode();
    let _ = execute!(
        std::io::stdout(),
        DisableMouseCapture,
        cursor::Show,
        terminal::LeaveAlternateScreen
    );
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut once = false;
    let mut open_all = false;
    let mut width = 100usize;
    let mut names: Vec<String> = Vec::new();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--once" => once = true,
            "--open" => open_all = true,
            "--width" => width = it.next().and_then(|v| v.parse().ok()).unwrap_or(100),
            "-h" | "--help" => {
                println!(
                    "usage: aib-hosts [--once [--width N] [--open]] [名字=目標]...  \
                     (沒給就讀 ~/.ez2-aib/hosts.json,含 Windows 與 GitHub;目標 local = 本機)"
                );
                return;
            }
            h => names.push(h.to_string()),
        }
    }
    let mut hosts: Vec<Host> = if names.is_empty() {
        hosts_from_config(&std::fs::read_to_string(config_path()).unwrap_or_default())
    } else {
        names.iter().map(|n| Host::new(n)).collect()
    };
    if hosts.is_empty() {
        eprintln!(
            "沒有測試機設定:{}(或在參數寫 名字=目標)",
            config_path().display()
        );
    }
    for h in hosts.iter_mut() {
        h.open = open_all;
    }
    let (tx, rx) = mpsc::channel::<(usize, Sample)>();
    if once {
        for (i, h) in hosts.iter().enumerate() {
            ask(i, h.probe(), tx.clone());
        }
        drop(tx);
        for (i, s) in rx.iter() {
            if let Some(h) = hosts.get_mut(i) {
                h.loads.push(s.load1);
                h.last = Some(s);
            }
        }
        let mut out = std::io::stdout();
        for l in frame(&hosts, width, 0, now()).0 {
            let s: String = l.iter().map(|(t, _)| t.as_str()).collect();
            if writeln!(out, "{}", fit(&s, width)).is_err() {
                return;
            }
        }
        return;
    }
    let mut out = std::io::stdout();
    let _ = terminal::enable_raw_mode();
    let _ = execute!(
        out,
        terminal::EnterAlternateScreen,
        EnableMouseCapture,
        cursor::Hide
    );
    std::panic::set_hook(Box::new(|i| {
        restore();
        eprintln!("{i}");
    }));
    let mut tick: u64 = 0;
    loop {
        let t0 = Instant::now();
        for (i, h) in hosts.iter_mut().enumerate() {
            let due = h.at.is_none_or(|a| a.elapsed() >= POLL);
            match h.asked {
                Some(a) if a.elapsed() >= STUCK => h.stuck = true,
                Some(_) => {}
                None if due => {
                    h.asked = Some(Instant::now());
                    ask(i, h.probe(), tx.clone());
                }
                None => {}
            }
        }
        while let Ok((i, s)) = rx.try_recv() {
            if let Some(h) = hosts.get_mut(i) {
                if s.online {
                    h.loads.push(s.load1);
                    if h.loads.len() > 30 {
                        h.loads.remove(0);
                    }
                }
                h.last = Some(s);
                h.at = Some(Instant::now());
                h.asked = None;
                h.stuck = false;
            }
        }
        let (cw, ch) = terminal::size().unwrap_or((100, 20));
        let (lines, owner) = frame(&hosts, cw as usize, tick, now());
        if draw(&mut out, &lines, cw as usize, ch as usize).is_err() {
            break;
        }
        tick = tick.wrapping_add(1);
        // 動畫節奏靠 event::poll(ADR 0005)
        let wait = Duration::from_millis(250).saturating_sub(t0.elapsed());
        if !event::poll(wait).unwrap_or(false) {
            continue;
        }
        match event::read() {
            Ok(Event::Key(k)) => match k.code {
                // 只認 q:Esc 會被拆開的控制序列誤觸(11-12 05:42 右下電池自己關掉)
                KeyCode::Char('q') => break,
                KeyCode::Char(c @ '1'..='9') => {
                    let i = c as usize - '1' as usize;
                    if let Some(h) = hosts.get_mut(i) {
                        h.open = !h.open;
                    }
                }
                _ => {}
            },
            Ok(Event::Mouse(m)) if m.kind == MouseEventKind::Down(MouseButton::Left) => {
                if let Some(Some(i)) = owner.get(m.row as usize) {
                    if let Some(h) = hosts.get_mut(*i) {
                        h.open = !h.open;
                    }
                }
            }
            _ => {}
        }
    }
    restore();
}

#[cfg(test)]
mod tests {
    use super::*;

    const HELD: &str = r#"{"state":"held","lease":{"owner":"@4542e","what":"scan","acquired_epoch":1000,"expiry_epoch":2000},"queue":[{"owner":"7b81e","what":"chrome-guard 刀","since_epoch":1200,"priority":false},{"owner":"@33e28","what":"告警","since_epoch":1400,"priority":true}]}
AIB_HOSTS_SYS 10 { 5.10 4.28 4.04 }"#;

    fn host(arg: &str, sample: Option<Sample>, open: bool) -> Host {
        let mut h = Host::new(arg);
        h.last = sample;
        h.open = open;
        h.loads = vec![1.0, 5.0, 12.0];
        h
    }

    fn text(l: &Line) -> String {
        l.iter().map(|(t, _)| t.as_str()).collect()
    }

    #[test]
    fn a_held_lease_drains_with_time_and_keeps_the_queue() {
        let s = parse_sample(HELD, true, "");
        assert!(s.online && s.held);
        assert_eq!(s.owner, "4542e");
        assert_eq!(s.queue.len(), 2);
        assert_eq!(s.queue[1].owner, "33e28");
        assert!(s.queue[1].priority);
        assert_eq!((s.ncpu, s.load1), (10.0, 5.1));
        assert_eq!(charge(Some(&s), 1000).0, 100);
        assert_eq!(charge(Some(&s), 1500).0, 50);
        assert_eq!(charge(Some(&s), 1900), (10, Color::Red));
        assert_eq!(charge(Some(&s), 9999).0, 0);
    }

    #[test]
    fn held_colors_have_five_steps_and_none_is_the_free_green() {
        let s = parse_sample(HELD, true, "");
        let cols: Vec<Color> = [1100, 1300, 1500, 1700, 1900]
            .iter()
            .map(|t| charge(Some(&s), *t).1)
            .collect();
        for (i, a) in cols.iter().enumerate() {
            assert_ne!(*a, Color::Green, "held never looks free");
            for b in &cols[i + 1..] {
                assert_ne!(a, b, "{cols:?}");
            }
        }
    }

    #[test]
    fn every_host_is_three_lines_when_closed() {
        let free = parse_sample(
            "{\"state\":\"free\",\"queue\":[]}\nAIB_HOSTS_SYS 8 { 1 1 1 }",
            true,
            "",
        );
        let off = parse_sample("", false, "ssh: connect to host x port 22: timed out\n");
        for (i, s) in [
            Some(parse_sample(HELD, true, "")),
            Some(free),
            Some(off),
            None,
        ]
        .into_iter()
        .enumerate()
        {
            let l = host_lines(&host("x", s, false), i, 120, 0, 1500);
            assert_eq!(l.len(), 3, "{:?}", l.iter().map(text).collect::<Vec<_>>());
        }
    }

    #[test]
    fn free_is_full_green_and_unreachable_is_red_empty() {
        let free = parse_sample(
            "{\"state\":\"free\",\"queue\":[]}\nAIB_HOSTS_SYS 8 { 1.0 1 1 }",
            true,
            "",
        );
        assert_eq!(charge(Some(&free), 0), (100, Color::Green));
        let off = parse_sample(
            "",
            false,
            "ssh: connect to host x port 22: Operation timed out\n",
        );
        assert!(!off.online);
        assert_eq!(charge(Some(&off), 0), (0, Color::Red));
        assert!(off.err.contains("timed out"));
        assert_eq!(charge(None, 0).1, Color::DarkGrey);
    }

    /// Andy 11-12 14:3x:沒人拿租約、負載卻高 ⇒ 不是空機(a63ab 在 Pro 16 沒拿租約跑測，面板寫空機)
    #[test]
    fn a_loaded_machine_without_a_lease_is_not_free() {
        let free = "{\"state\":\"free\",\"queue\":[]}\n";
        // 16 核、8 效能核、load 7.1 ⇒ 0.89/效能核
        let run = parse_sample(
            &format!("{free}AIB_HOSTS_SYS 16 {{ 7.1 5.9 5.5 }} 8"),
            true,
            "",
        );
        assert_eq!((run.perf, run.load5), (8.0, 5.9));
        assert!(unleased_busy(&run));
        assert_eq!(charge(Some(&run), 0), (100, Color::Magenta));
        let h = host("x", Some(run.clone()), false);
        let l = host_lines(&h, 0, 120, 0, 0);
        assert!(text(&l[0]).contains("沒租約在跑 不可用"), "{}", text(&l[0]));
        assert!(text(&l[2]).contains("沒拿租約"));
        // 同一台安靜 ⇒ 空機;讀不到效能核就用全部核數
        let quiet = parse_sample(
            &format!("{free}AIB_HOSTS_SYS 16 {{ 5.0 5.0 5.0 }} 8"),
            true,
            "",
        );
        assert!(!unleased_busy(&quiet));
        assert_eq!(charge(Some(&quiet), 0), (100, Color::Green));
        let old = parse_sample(
            &format!("{free}AIB_HOSTS_SYS 16 {{ 7.1 5.9 5.5 }}"),
            true,
            "",
        );
        assert!(!unleased_busy(&old), "7.1 / 16 核 = 0.44");
        // 有人拿著租約 ⇒ 照租約顯示，不是「沒租約在跑」
        let mut held = run.clone();
        held.held = true;
        assert!(!unleased_busy(&held));
    }

    #[test]
    fn names_are_labels_and_the_target_stays_behind() {
        let h = Host::new("Pro 13=user@host-b");
        assert_eq!(
            (h.name.as_str(), h.target.as_str()),
            ("Pro 13", "user@host-b")
        );
        let h = Host::new("andymini");
        assert_eq!(
            (h.name.as_str(), h.target.as_str()),
            ("andymini", "andymini")
        );
        let (lines, _) = frame(&[host("Pro 13=user@host-b", None, false)], 100, 0, 0);
        let all: String = lines.iter().map(text).collect();
        assert!(all.contains("Pro 13") && !all.contains("host-b"), "{all}");
    }

    /// Andy 11-12 14:5x:測試機也要有 GitHub 與 Windows;清單從私有設定讀(公開倉不寫位址)
    #[test]
    fn config_adds_windows_and_github() {
        let cfg = r#"{"github_repo":"o/r","hosts":[
            {"name":"win","ssh":"u@w","os":"windows","order":5,"identity":"/k"},
            {"name":"m","ssh":"m","order":1,"jump_fallback":"j"},
            {"name":"bad"}]}"#;
        let hs = hosts_from_config(cfg);
        let names: Vec<&str> = hs.iter().map(|h| h.name.as_str()).collect();
        assert_eq!(names, ["m", "win", "GitHub"]);
        assert_eq!(hs[0].jump_fallback.as_deref(), Some("j"));
        assert_eq!(
            (hs[1].kind, hs[1].identity.as_deref()),
            (Kind::Windows, Some("/k"))
        );
        assert_eq!((hs[2].kind, hs[2].target.as_str()), (Kind::GitHub, "o/r"));
        assert!(hosts_from_config("").is_empty() && hosts_from_config("{}").is_empty());
        // Windows:CPU 50%、8 核 ⇒ 負載 4/8,沒有租約系統
        let w = parse_win("AIB_WIN 50 8\r\n", true, "");
        assert!(w.online && w.no_lease && (w.load1 - 4.0).abs() < 1e-9);
        assert_eq!(charge(Some(&w), 0), (100, Color::Green));
        let busy = parse_win("AIB_WIN 95 8", true, "");
        assert!(unleased_busy(&busy));
        assert!(!parse_win("", false, "timed out").online);
        let l = host_lines(
            &Host {
                last: Some(w),
                ..hs[1].clone()
            },
            1,
            120,
            0,
            0,
        );
        assert!(
            text(&l[0]).contains("空機 可用(沒有租約系統)"),
            "{}",
            text(&l[0])
        );
    }

    #[test]
    fn github_row_counts_running_and_queued() {
        let now = iso_epoch("2026-11-12T06:40:00Z");
        assert_eq!(now, 1_794_465_600);
        let j = r#"[{"status":"in_progress","name":"ci","headBranch":"b1","createdAt":"2026-11-12T06:30:00Z"},
                    {"status":"queued","name":"aibd","headBranch":"b2","createdAt":"2026-11-12T06:38:00Z"},
                    {"status":"completed","name":"x","headBranch":"b3","createdAt":"2026-11-12T06:00:00Z"}]"#;
        let s = parse_gh(j, true, "");
        assert_eq!(s.gh, Some((1, 1)));
        assert_eq!(
            charge(Some(&s), now),
            (0, Color::Red),
            "有排隊 = 執行機都在用"
        );
        let mut g = Host::new("GitHub=o/r");
        g.kind = Kind::GitHub;
        g.last = Some(s.clone());
        let l = host_lines(&g, 0, 120, 0, now);
        assert_eq!(l.len(), 3, "收起也是 3 行");
        assert!(
            text(&l[0]).contains("滿了 · 跑 1 · 排隊 1"),
            "{}",
            text(&l[0])
        );
        assert!(text(&l[1]).contains("執行中 1 · 排隊 1"));
        assert!(text(&l[2]).contains("▶ ci · b1"), "{}", text(&l[2]));
        g.open = true;
        let l = host_lines(&g, 0, 120, 0, now);
        assert!(l.iter().any(|x| text(x).contains("排隊 aibd · b2")));
        let idle = parse_gh("[]", true, "");
        assert_eq!(charge(Some(&idle), now), (100, Color::Green));
        assert!(!parse_gh("not json", false, "HTTP 502").online);
    }

    #[test]
    fn a_blank_line_follows_the_title_and_rows_map_to_hosts() {
        let hs = [
            host("a", Some(parse_sample(HELD, true, "")), false),
            host("b", None, false),
        ];
        let (lines, owner) = frame(&hs, 100, 0, 1500);
        assert!(lines[1].is_empty(), "空一行");
        assert_eq!(owner[0], None);
        assert_eq!(owner[1], None);
        assert_eq!(owner[2], Some(0));
        assert_eq!(lines.len(), owner.len());
        assert_eq!(*owner.last().unwrap(), Some(1));
    }

    #[test]
    fn opening_a_battery_lists_who_runs_and_who_waits() {
        let s = parse_sample(HELD, true, "");
        let closed = host_lines(&host("a", Some(s.clone()), false), 0, 120, 0, 1500);
        let open = host_lines(&host("a", Some(s), true), 0, 120, 0, 1500);
        let o: Vec<String> = open.iter().map(text).collect();
        assert!(open.len() > closed.len());
        assert!(o.iter().any(|l| l.contains("正在跑 4542e")), "{o:?}");
        assert!(o.iter().any(|l| l.contains("1. 7b81e · 等了 5分")), "{o:?}");
        assert!(
            o.iter()
                .any(|l| l.contains("2. 33e28") && l.contains("優先")),
            "{o:?}"
        );
        assert!(o.iter().any(|l| l.contains("chrome-guard 刀")), "{o:?}");
        let free = parse_sample(
            "{\"state\":\"free\",\"queue\":[]}\nAIB_HOSTS_SYS 8 { 1 1 1 }",
            true,
            "",
        );
        let f: Vec<String> = host_lines(&host("b", Some(free), true), 1, 120, 0, 0)
            .iter()
            .map(text)
            .collect();
        assert!(f.iter().any(|l| l.contains("沒有人在跑")), "{f:?}");
        assert!(f.iter().any(|l| l.contains("沒有人排隊")), "{f:?}");
    }

    #[test]
    fn every_width_draws_without_panicking_and_the_spot_moves() {
        let mut h = host("andymini", Some(parse_sample(HELD, true, "")), true);
        h.stuck = true;
        for width in [0, 1, 20, 60, 128, 300] {
            for tick in 0..5 {
                let (l, o) = frame(std::slice::from_ref(&h), width, tick, 1500);
                assert_eq!(l.len(), o.len());
            }
        }
        let a = frame(std::slice::from_ref(&h), 100, 0, 1500).0;
        let b = frame(std::slice::from_ref(&h), 100, 3, 1500).0;
        assert_ne!(a[2][1].0, b[2][1].0, "the bright cell moves while busy");
        assert!(a[2][4].0.ends_with(" ?"), "stuck shows ?");
    }
}
