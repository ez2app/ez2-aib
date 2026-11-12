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
    style::{Color, Print, ResetColor, SetBackgroundColor, SetForegroundColor},
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
            host.role = "Win 測試".into();
        }
        if let Some(r) = h["role"].as_str() {
            host.role = r.to_string();
        }
        host.identity = h["identity"].as_str().map(str::to_string);
        host.tag = h["tag"].as_str().map(str::to_string);
        host.ci = h["ci"].as_bool().unwrap_or(false);
        host.jump_fallback = h["jump_fallback"].as_str().map(str::to_string);
        rows.push((h["order"].as_i64().unwrap_or(i64::MAX), i, host));
    }
    rows.sort_by_key(|(o, i, _)| (*o, *i));
    let mut out: Vec<Host> = rows.into_iter().map(|(_, _, h)| h).collect();
    if let Some(repo) = v["github_repo"].as_str() {
        let mut g = Host::new(&format!("GitHub={repo}"));
        g.kind = Kind::GitHub;
        g.role = "CI".into();
        g.ci = true;
        out.push(g);
    }
    // Andy 11-12 15:0x「把所有能 ssh 的主機全列出」:servers 只看負載(唯讀)
    for h in v["servers"].as_array().into_iter().flatten() {
        let (Some(name), Some(ssh)) = (h["name"].as_str(), h["ssh"].as_str()) else {
            continue;
        };
        let mut host = Host::new(&format!("{name}={ssh}"));
        host.kind = Kind::Server;
        host.role = h["role"].as_str().unwrap_or("主機").to_string();
        host.poll = SERVER_POLL;
        host.identity = h["identity"].as_str().map(str::to_string);
        host.tag = h["tag"].as_str().map(str::to_string);
        // 043dd #726 L3 + Andy 11-12 21:5x:賣給客戶的主機(tag Ez2)預設不探(對方登入紀錄看得到),
        // 自家主機預設探;hosts.json 的 "poll": true / false 蓋過預設
        host.watch = h["poll"]
            .as_bool()
            .unwrap_or(host.tag.as_deref() != Some("Ez2"));
        host.ci = h["ci"].as_bool().unwrap_or(false);
        out.push(host);
    }
    // 終端機字型有 Nerd Font 才畫得出商標字;沒寫就用字母(別人的終端機不會變方框)
    if v["icons"].as_str() == Some("nerd") {
        for h in &mut out {
            h.nerd = true;
        }
    }
    out
}

const POLL: Duration = Duration::from_secs(20);
const SERVER_POLL: Duration = Duration::from_secs(300);
/// 其他主機(Linux 或 mac):核數 + 平均負載，不碰租約
const SERVER_REMOTE: &str = "echo AIB_HOSTS_SYS $(nproc 2>/dev/null || sysctl -n hw.ncpu) \
                             $(if [ -r /proc/loadavg ]; then echo \"{ $(cut -d' ' -f1-3 /proc/loadavg) }\"; \
                             else sysctl -n vm.loadavg; fi); \
                             [ -r /proc/stat ] && echo AIB_TOPAVG; \
                             LC_ALL=C ps -Ao pid=,pcpu=,etime=,user=,lstart=,args= 2>/dev/null \
                             | sort -k2 -nr | head -5 | cut -c1-400 | sed 's/^/AIB_TOP /'";
const STUCK: Duration = Duration::from_secs(60);
const SPARK: &[char] = &['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█'];
const NAME_W: usize = 15;
/// Windows(OpenSSH 預設 cmd.exe):CPU 使用率 % 與邏輯核數;沒有 aib 租約系統
const WIN_REMOTE: &str = "powershell -NoProfile -Command \"Write-Output ('AIB_WIN ' + \
                          (Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average \
                          + ' ' + (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors)\"";
const REMOTE: &str = "PATH=$HOME/.local/bin:$PATH; aib lease status --json 2>/dev/null; \
                      echo AIB_HOSTS_SYS $(sysctl -n hw.ncpu) $(sysctl -n vm.loadavg) \
                      $(sysctl -n hw.perflevel0.physicalcpu 2>/dev/null); \
                      [ -r /proc/stat ] && echo AIB_TOPAVG; \
                      LC_ALL=C ps -Ao pid=,pcpu=,etime=,user=,lstart=,args= 2>/dev/null \
                      | sort -k2 -nr | head -5 | cut -c1-400 | sed 's/^/AIB_TOP /'";

#[derive(Debug, Clone, Default, PartialEq)]
struct Waiter {
    owner: String,
    what: String,
    since: i64,
    priority: bool,
}

/// 吃 CPU 最多的一支程式(Andy 11-13 01:4x:點進主機要看到為什麼高負載，可以同意關閉)
#[derive(Debug, Clone, Default, PartialEq)]
struct Proc {
    pid: u32,
    cpu: f64,
    /// 跑了多久(ps etime,例 01:02:03)
    etime: String,
    user: String,
    /// 啟動時間(ps lstart,空白壓成一格):關閉前再比一次，pid 換人了就不關
    start: String,
    cmd: String,
}

/// `AIB_TOP <pid> <%cpu> <etime> <user> <lstart 5 段> <args…>`;格式不對 ⇒ None
fn parse_top(line: &str) -> Option<Proc> {
    let rest = line.strip_prefix("AIB_TOP ")?;
    let t: Vec<&str> = rest.split_whitespace().collect();
    if t.len() < 10 {
        return None;
    }
    Some(Proc {
        pid: t[0].parse().ok().filter(|p| *p > 1)?,
        cpu: t[1].parse().ok().filter(|c: &f64| c.is_finite())?,
        etime: t[2].to_string(),
        user: t[3].to_string(),
        start: t[4..9].join(" "),
        cmd: t[9..].join(" "),
    })
}

#[derive(Debug, Clone, Copy, Default, PartialEq)]
enum Kind {
    #[default]
    Mac,
    Windows,
    /// GitHub Actions(CI 執行機):看執行中 / 排隊中的工作
    GitHub,
    /// 其他能 ssh 的主機(正式站、代管雲、跳板…):只讀負載，每 60 秒
    Server,
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
    /// 這台沒有 aib 租約系統(Windows、其他主機)
    no_lease: bool,
    /// 其他主機(不是測試機)
    server: bool,
    /// GitHub:(執行中, 排隊中)
    gh: Option<(u32, u32)>,
    /// GitHub 的工作(owner = 分支、what = 工作流程名、since = 建立時間、priority = 排隊中)
    runs: Vec<Waiter>,
    /// CPU 前 5 名(展開時列出「為什麼忙」,每支一顆「同意關閉」)
    top: Vec<Proc>,
    /// Linux procps 的 %CPU 是整段壽命平均(不是現在):畫面要講清楚
    top_avg: bool,
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
    /// 功能 / 種類(hosts.json 的 role;沒寫就照種類:測試 / Win 測試 / CI / 主機)
    role: String,
    /// 多久問一次(測試機 20 秒、其他主機 5 分鐘)
    poll: Duration,
    /// 要不要探(其他主機預設不探)
    watch: bool,
    identity: Option<String>,
    /// 銘牌(hosts.json 的 tag,例 "Ez2" = 賣給客戶的主機);沒寫就照種類 M / W / L / G
    tag: Option<String>,
    /// 能跑 CI(GitHub 出問題時接手 CI 格的機器;hosts.json 的 "ci": true,GitHub 本身也是)
    ci: bool,
    jump_fallback: Option<String>,
    /// 銘牌畫真的商標字(Nerd Font;hosts.json 頂層 "icons": "nerd",Andy 11-12 23:3x)
    nerd: bool,
    asked: Option<Instant>,
    /// 還沒回來的詢問數(上限 MAX_INFLIGHT)
    inflight: u8,
    stuck: bool,
    open: bool,
    /// 「同意關閉」的進度 / 結果(展開時顯示在最下面)
    note: Option<String>,
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
            role: "測試".into(),
            poll: POLL,
            watch: true,
            identity: None,
            tag: None,
            ci: false,
            jump_fallback: None,
            nerd: false,
            asked: None,
            inflight: 0,
            stuck: false,
            open: false,
            note: None,
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
    // CPU 前 5 名那幾列先拿掉，剩下的才是租約 JSON + AIB_HOSTS_SYS(數字不能混進負載)
    let top: Vec<Proc> = out.lines().filter_map(parse_top).collect();
    let top_avg = out.lines().any(|l| l.trim() == "AIB_TOPAVG");
    let rest: String = out
        .lines()
        .filter(|l| !l.starts_with("AIB_TOP ") && l.trim() != "AIB_TOPAVG")
        .collect::<Vec<_>>()
        .join("\n");
    let out = rest.as_str();
    let mut s = Sample {
        err: err.lines().last().unwrap_or("").trim().to_string(),
        top,
        top_avg,
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
        "-o",
        "ServerAliveInterval=5",
        "-o",
        "ServerAliveCountMax=3",
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

/// a named exception to ADR 0005: `--once` is a one-shot print with no timer loop of its own;
/// it waits at most until `end` for the probes, then draws whatever came back
#[allow(clippy::disallowed_methods)]
fn recv_until(rx: &mpsc::Receiver<(usize, Sample)>, end: Instant) -> Option<(usize, Sample)> {
    rx.recv_timeout(end.saturating_duration_since(Instant::now()))
        .ok()
}

/// 一台最多同時幾支詢問：卡住後再問一次;第二次也卡住就等舊的回來(043dd #726 增量 L1,
/// 遠端指令卡住但連線活著時 keepalive 照答，ssh 不會自己結束)
const MAX_INFLIGHT: u8 = 2;

/// 這一輪要不要問這台(主迴圈與測試共用);要問就記下時間與在途數
fn should_ask(h: &mut Host, stuck_after: Duration) -> bool {
    if let Some(a) = h.asked {
        if a.elapsed() < stuck_after {
            return false;
        }
        h.stuck = true;
        if h.inflight >= MAX_INFLIGHT {
            return false;
        }
        h.asked = None;
    }
    let due = h.at.is_none_or(|a| a.elapsed() >= h.poll);
    if !(due && h.watch && h.inflight < MAX_INFLIGHT) {
        return false;
    }
    h.asked = Some(Instant::now());
    h.inflight += 1;
    true
}

/// 收到一台的回覆(不論是哪一次詢問的)
fn record(h: &mut Host, s: Sample) {
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
    h.inflight = h.inflight.saturating_sub(1);
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
                let cmd = match k {
                    Kind::Windows => WIN_REMOTE,
                    Kind::Server => SERVER_REMOTE,
                    _ => REMOTE,
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
                    Kind::Server => {
                        let mut s = parse_sample(&out, o.status.success(), &err);
                        s.no_lease = true;
                        s.server = true;
                        s
                    }
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

fn load_ratio(s: &Sample) -> Option<f64> {
    let cores = if s.perf > 0.0 { s.perf } else { s.ncpu };
    (s.online && cores > 0.0).then(|| s.load1.max(s.load5 * 0.8) / cores)
}

fn unleased_busy(s: &Sample) -> bool {
    if s.server {
        return false;
    }
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
        None if !h.watch => "不探(hosts.json 寫 \"poll\": true 才探)".to_string(),
        None => "讀取中…".to_string(),
        Some(s) if !s.online => format!("離線 {}", fit(&s.err, 30)),
        Some(Sample {
            gh: Some((r, q)), ..
        }) if *q > 0 => format!("滿了 · 跑 {r} · 排隊 {q}"),
        Some(Sample {
            gh: Some((r, _)), ..
        }) => format!("跑 {r} · 還有空位"),
        Some(s) if s.server => "運作中(只看負載)".to_string(),
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
    out.extend(top_lines(h, s, room));
    out
}

/// 按鈕上的字:「[同意關閉 1234]」(點擊靠這個字找 pid)
const KILL_BTN: &str = "[同意關閉 ";

/// 展開時的「為什麼忙」:負載幾核、CPU 前 5 名各是什麼(哪棵工作樹)、跑多久，每支一顆同意關閉
/// (Andy 11-13 01:4x)。關閉要按 Touch ID(`aib consent ask --strong`):AI 也能送點擊，點擊本身不算同意。
fn top_lines(h: &Host, s: Option<&Sample>, room: usize) -> Vec<Line> {
    let mut out = Vec::new();
    let Some(s) = s.filter(|s| s.online && !s.top.is_empty()) else {
        if let Some(n) = &h.note {
            out.push(vec![pad(), (fit(n, room), Color::Yellow)]);
        }
        return out;
    };
    let sum: f64 = s.top.iter().map(|p| p.cpu).sum();
    let why = if unleased_busy(s) {
        "負載高但沒人拿租約"
    } else if s.ncpu > 0.0 && s.load1 > s.ncpu {
        "負載超過核數"
    } else {
        "目前負載"
    };
    out.push(vec![
        pad(),
        (
            format!(
                "為什麼忙:{why} {:.1}/{:.0} 核 · CPU 前 {} 名合計 {sum:.0}%{}",
                s.load1,
                s.ncpu,
                s.top.len(),
                if s.top_avg {
                    "(Linux:整段平均，不是現在)"
                } else {
                    ""
                }
            ),
            Color::Magenta,
        ),
    ]);
    for p in &s.top {
        let btn = format!("{KILL_BTN}{}]", p.pid);
        let head = format!("{:>3.0}% · 跑了 {} · {} · ", p.cpu, p.etime, p.user);
        let what = match tree_of(&p.cmd) {
            Some(t) => format!("({t}) {}", p.cmd),
            None => p.cmd.clone(),
        };
        let left = room.saturating_sub(w(&head) + w(&btn) + 1);
        out.push(vec![
            pad(),
            (head, Color::White),
            (fit(&what, left), Color::DarkGrey),
            (" ".into(), Color::White),
            (btn, Color::Red),
        ]);
    }
    if let Some(n) = &h.note {
        out.push(vec![pad(), (fit(n, room), Color::Yellow)]);
    }
    out
}

/// 指令裡的工作樹名(`…/wt.noindex/cooldown-34d15/…` ⇒ cooldown-34d15),看得出是哪一席的工作
fn tree_of(cmd: &str) -> Option<&str> {
    let i = cmd.find("wt.noindex/")? + "wt.noindex/".len();
    let t = cmd[i..].split(['/', ' ']).next()?;
    (!t.is_empty()).then_some(t)
}

/// 點到哪一顆「同意關閉」:回傳 pid
fn kill_hit(lines: &[Line], x: usize, y: usize) -> Option<u32> {
    let mut at = 0;
    for (t, _) in lines.get(y)? {
        let tw = w(t);
        if let Some(rest) = t.strip_prefix(KILL_BTN) {
            if x >= at && x < at + tw {
                return rest.trim_end_matches(']').parse().ok();
            }
        }
        at += tw;
    }
    None
}

/// 單引號包起來給 sh(裡面的單引號拆開接)
fn sh_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

/// 檔名 / 腳本裡用的主機代號：只留英數與 . _ -
fn slug(s: &str) -> String {
    let t: String = s
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-') {
                c
            } else {
                '_'
            }
        })
        .collect();
    if t.is_empty() {
        "host".into()
    } else {
        t
    }
}

/// 關閉用的 SIGN 腳本(consent 只收 `bash ~/.ez2-aib/pending/SIGN-*.sh` + Touch ID,而且先過 aib-signlint):
/// 先比啟動時間，pid 已結束或換成別的程式就不關;只送 TERM(不 -9)。別台走 ssh(同一組防護選項),
/// 直連 255 而且 hosts.json 有跳板 ⇒ 帶 -J 再試一次;還是 255 ⇒ 寫「連不上」(不當成已關)。
/// 啟動時間只准英數、冒號、空白(遠端不可信,ps 吐什麼都不能變成指令)⇒ 不符就不產腳本。
fn sign_script(name: &str, p: &Probe, pr: &Proc, receipt: &str) -> Option<String> {
    if pr.start.is_empty()
        || !pr
            .start
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == ':' || c == ' ')
    {
        return None;
    }
    let (pid, start) = (pr.pid, &pr.start);
    let head = format!(
        "#!/bin/bash\n\
         # aib-hosts「同意關閉」:{name} 上 pid {pid}(比對啟動時間，只送 TERM)\n\
         # 可丟環境: {name} 收據: {receipt}\n\
         set -euo pipefail\n\
         mkdir -p \"$(dirname {r})\"\n\
         exec > >(tee -a {r}) 2>&1\n",
        r = sh_quote(receipt)
    );
    if p.target == "local" {
        return Some(format!(
            "{head}if ! now=$(LC_ALL=C ps -o lstart= -p {pid} | tr -s ' ' | sed 's/^ //;s/ $//'); then\n  \
             echo 'pid {pid} 已經結束，沒有關'; exit 3\nfi\n\
             if [ \"$now\" != '{start}' ]; then\n  echo 'pid {pid} 已換成別的程式，沒有關'; exit 3\nfi\n\
             kill {pid}\necho 'killed {pid}'\n"
        ));
    }
    let inner = format!(
        "set -eu; if ! now=$(LC_ALL=C ps -o lstart= -p {pid} | tr -s \" \" | sed \"s/^ //;s/ $//\"); \
         then echo \"pid {pid} 已經結束，沒有關\"; exit 3; fi; \
         if [ \"$now\" != \"{start}\" ]; then echo \"pid {pid} 已換成別的程式，沒有關\"; exit 3; fi; \
         kill {pid}; echo \"killed {pid}\""
    );
    let id = p
        .identity
        .as_deref()
        .map_or(String::new(), |i| format!("-i {} ", sh_quote(i)));
    let ssh = |jump: &str| {
        format!(
            "ssh -o ForwardAgent=no -o ClearAllForwardings=yes -o BatchMode=yes -o ConnectTimeout=8 \
             -o ServerAliveInterval=5 -o ServerAliveCountMax=3 {id}{jump}-- {} {} || rc=$?\n",
            sh_quote(&p.target),
            sh_quote(&inner)
        )
    };
    let mut out = format!("{head}rc=0\n{}", ssh(""));
    if let Some(j) = &p.jump_fallback {
        out.push_str(&format!(
            "if [ \"$rc\" = 255 ]; then\n  rc=0\n  {}fi\n",
            ssh(&format!("-J {} ", sh_quote(j)))
        ));
    }
    out.push_str(&format!(
        "if [ \"$rc\" = 255 ]; then echo '連不上 {}(ssh 255),沒有關'; exit 4; fi\nexit \"$rc\"\n",
        slug(name)
    ));
    Some(out)
}

/// consent 回來的 JSON(最後一行)⇒ 畫面上一句話;只有 result = ok 才算關了
fn kill_note(name: &str, pid: u32, out: &str) -> String {
    let v: Value = out
        .lines()
        .rev()
        .find_map(|l| serde_json::from_str(l.trim()).ok())
        .unwrap_or(Value::Null);
    let r = v["result"].as_str().unwrap_or("");
    let why = v["why"].as_str().unwrap_or("");
    match r {
        "ok" => format!("已關閉 pid {pid}({name})"),
        "denied" => format!("沒關閉 pid {pid}:你按了拒絕"),
        "timeout" | "expired" | "pending" => {
            format!("沒關閉 pid {pid}:沒按 Touch ID 或 3 分鐘逾時")
        }
        "rc_nonzero" => format!("沒關閉 pid {pid}:已結束、換了程式或連不上(看收據)"),
        "" => format!("沒關閉 pid {pid}:aib consent 沒回結果"),
        _ => format!(
            "沒關閉 pid {pid}:{r}{}",
            if why.is_empty() {
                String::new()
            } else {
                format!("({why})")
            }
        ),
    }
}

/// 點了同意關閉：寫一支 SIGN 腳本 → `aib consent ask --strong --wait` 跳 Touch ID,等結果(按了、拒絕、逾時);
/// 結果回傳給畫面
fn ask_kill(idx: usize, name: String, p: Probe, pr: Proc, tx: mpsc::Sender<(usize, String)>) {
    std::thread::spawn(move || {
        let home = std::env::var_os("HOME")
            .map(std::path::PathBuf::from)
            .unwrap_or_default();
        let tag = format!("{}-{}-{}", slug(&name), pr.pid, now());
        let receipt = home
            .join(".ez2-aib/state/aib-hosts/kill")
            .join(format!("{tag}.log"));
        let sign = home
            .join(".ez2-aib/pending")
            .join(format!("SIGN-aibhosts-kill-{tag}.sh"));
        let Some(script) = sign_script(&name, &p, &pr, &receipt.to_string_lossy()) else {
            let _ = tx.send((
                idx,
                format!("沒關閉 pid {}:啟動時間格式不對，不產關閉腳本", pr.pid),
            ));
            return;
        };
        if let Err(e) = std::fs::write(&sign, script) {
            let _ = tx.send((
                idx,
                format!("沒關閉 pid {}:寫不了 {}({e})", pr.pid, sign.display()),
            ));
            return;
        }
        let summary = format!(
            "關閉 {name} 上 pid {}({:.0}% CPU,跑了 {})",
            pr.pid, pr.cpu, pr.etime
        );
        let o = Command::new("aib")
            .args(["consent", "ask", "--strong", "--wait", "200", "--json"])
            .args(["--summary", summary.as_str(), "bash"])
            .arg(&sign)
            .stdin(Stdio::null())
            .output();
        let note = match o {
            Ok(o) => kill_note(&name, pr.pid, &String::from_utf8_lossy(&o.stdout)),
            Err(e) => format!("沒關閉 pid {}:叫不起 aib consent({e})", pr.pid),
        };
        let _ = tx.send((idx, note));
    });
}

/// 整張畫面，和每一行屬於哪一台(點擊用)。
fn frame(hosts: &[Host], width: usize, tick: u64, t: i64) -> (Vec<Line>, Vec<Option<usize>>) {
    list_frame(hosts, width, tick, t)
}

/// 每台 3 行的清單(`--once` 與測試用;互動畫面改用 `screen` 的機箱格)
fn list_frame(hosts: &[Host], width: usize, tick: u64, t: i64) -> (Vec<Line>, Vec<Option<usize>>) {
    let mut lines: Vec<Line> = vec![
        vec![(format!("主機群 {}(台北)", taipei_clock(t)), Color::White)],
        Vec::new(),
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

/// 台北時間(UTC+8),不依賴本機時區
fn taipei_clock(t: i64) -> String {
    let s = (t + 8 * 3600).rem_euclid(86400);
    format!("{:02}:{:02}:{:02}", s / 3600, s % 3600 / 60, s % 60)
}

/// 主機群(Andy 11-12 15:0x):每台一個直立小機箱，預設收合;點一台(或按 1–9)看明細，再點返回。
/// 機箱 6 行 × CELL 格：名字 / 頂 / 負載上半 / 負載下半 / 底(兩顆燈：連線、租約)/ 功能 + 負載
const CELL: usize = 12;
const TOWER_H: usize = 6;
const LEVELS: [char; 9] = [' ', '▁', '▂', '▃', '▄', '▅', '▆', '▇', '█'];

fn kind_color(h: &Host) -> Color {
    match h.kind {
        Kind::Mac => Color::White,
        Kind::Windows => Color::Blue,
        Kind::GitHub => Color::Magenta,
        Kind::Server => Color::Cyan,
    }
}

/// 機箱的負載比例:GitHub 有排隊 = 滿、只有執行中 = 半;其他 = 每核負載
fn tower_ratio(s: &Sample) -> Option<f64> {
    match s.gh {
        Some((_, q)) if q > 0 => Some(1.0),
        Some((r, _)) => Some(if r > 0 { 0.5 } else { 0.0 }),
        None => load_ratio(s),
    }
}

/// 一台的機箱(每行剛好 CELL 格)
fn tower(h: &Host, idx: usize) -> Vec<Line> {
    let s = h.last.as_ref();
    let online = s.is_some_and(|s| s.online);
    let ratio = s.and_then(tower_ratio);
    let fill_c = match ratio {
        Some(r) if r > 0.8 => Color::Red,
        Some(r) if r > 0.5 => Color::Yellow,
        Some(_) => Color::Green,
        None => Color::DarkGrey,
    };
    let level = ratio.map_or(0, |r| (r.clamp(0.0, 1.0) * 16.0).round() as usize);
    let (top, low) = if online && ratio.is_some() {
        (
            LEVELS[level.saturating_sub(8).min(8)].to_string().repeat(6),
            LEVELS[level.min(8)].to_string().repeat(6),
        )
    } else {
        ("░░░░░░".to_string(), "░░░░░░".to_string())
    };
    let led_net = match s {
        None => ('○', Color::DarkGrey),
        Some(s) if s.online => ('●', Color::Green),
        Some(_) => ('●', Color::Red),
    };
    let led_use = match s {
        None => ('○', Color::DarkGrey),
        Some(s) if !s.online => ('○', Color::DarkGrey),
        Some(Sample {
            gh: Some((_, q)), ..
        }) => ('●', if *q > 0 { Color::Red } else { Color::Green }),
        Some(s) if unleased_busy(s) => ('●', Color::Magenta),
        Some(s) if s.server || s.no_lease => ('○', Color::DarkGrey),
        Some(s) if s.held => ('●', Color::Yellow),
        Some(_) => ('●', Color::Green),
    };
    let edge = if online {
        kind_color(h)
    } else {
        Color::DarkGrey
    };
    // Andy 11-12 20:4x:全置中 —— 框(8 格)左右各空 2 格，名字與功能行在格子裡置中
    let side = " ".repeat((CELL - 8) / 2);
    let gap = " ".repeat(CELL - 8 - side.len());
    let (badge, badge_c) = badge(h);
    let pad = |t: String| {
        let t = fit(&t, CELL - 1);
        let room = CELL.saturating_sub(w(&t));
        format!("{}{t}{}", " ".repeat(room / 2), " ".repeat(room - room / 2))
    };
    let stat = match s {
        None if !h.watch => "不探".to_string(),
        None => "…".to_string(),
        Some(s) if !s.online => "離線".to_string(),
        Some(Sample {
            gh: Some((r, q)), ..
        }) => format!("{r}跑{q}排"),
        Some(_) => ratio.map_or("—".into(), |r| format!("{:.0}%", r * 100.0)),
    };
    // Andy 11-12 20:2x:數字加圓框、後面空一格(① lin-macmini);1–9 對應按鍵，第 10 台起不編號
    let num = match char::from_u32(0x2460 + idx as u32) {
        Some(c) if idx < 9 => format!("{c} "),
        _ => "  ".into(),
    };
    vec![
        vec![(pad(format!("{num}{}", h.name)), kind_color(h))],
        // Andy 11-12 20:3x:上緣框線放銘牌，左角右邊一格起(┌M─────┐ / ┌Ez2───┐)
        vec![
            (format!("{side}┌"), edge),
            (badge.clone(), badge_c),
            (format!("{}┐{gap}", "─".repeat(6 - w(&badge))), edge),
        ],
        vec![
            (format!("{side}│"), edge),
            (top, fill_c),
            (format!("│{gap}"), edge),
        ],
        vec![
            (format!("{side}│"), edge),
            (low, fill_c),
            (format!("│{gap}"), edge),
        ],
        vec![
            (format!("{side}└"), edge),
            (led_net.0.to_string(), led_net.1),
            (led_use.0.to_string(), led_use.1),
            (format!("────┘{gap}"), edge),
        ],
        // 負載數字一定看得到;功能字太長就截
        vec![(
            pad(format!(
                "{} {stat}",
                fit(&h.role, (CELL - 2).saturating_sub(w(&stat) + 1))
            )),
            Color::DarkGrey,
        )],
    ]
}

/// Ez2 銘牌的記號色(draw 看到它就畫金底深字)
const EZ2_BADGE: Color = Color::AnsiValue(178);

/// 銘牌字與顏色:hosts.json 的 tag 優先(Ez2 = 賣給客戶的主機，反白);否則照種類
/// M(Mac)/ W(Windows)/ L(Linux 主機)/ G(GitHub);最多 6 格
/// 能跑 CI 的再加「-CI」(Andy 11-12 20:4x,例 M-CI、Ez2-CI)
fn badge(h: &Host) -> (String, Color) {
    let (base, c) = match h.tag.as_deref().map(str::trim).filter(|t| !t.is_empty()) {
        Some(t) => (
            t.to_string(),
            if t == "Ez2" { EZ2_BADGE } else { kind_color(h) },
        ),
        None => {
            let l = if h.nerd {
                // Nerd Font(Material Design 組，比 Font Awesome 組畫得滿、看起來大;Andy 11-12 23:4x)商標字:Apple / Windows / Linux(Tux)/ GitHub;
                // 字型把它畫得比一格寬，後面留一格空白免得壓到下一個字
                match h.kind {
                    Kind::Mac => "\u{f0035} ",
                    Kind::Windows => "\u{f05b3} ",
                    Kind::Server => "\u{f033d} ",
                    Kind::GitHub => "\u{f02a4} ",
                }
            } else {
                match h.kind {
                    Kind::Mac => "M",
                    Kind::Windows => "W",
                    Kind::Server => "L",
                    Kind::GitHub => "G",
                }
            };
            (l.to_string(), kind_color(h))
        }
    };
    let t = match (h.ci, h.nerd && h.tag.is_none()) {
        (true, true) => format!("{base}CI"),
        (true, false) => format!("{base}-CI"),
        (false, _) => base.trim_end().to_string(),
    };
    (fit(&t, 6), c)
}

/// 一排放幾台
fn per_row(width: usize) -> usize {
    (width / CELL).max(1)
}

/// 互動畫面：沒有打開的 ⇒ 機箱格;有打開的 ⇒ 那台的明細(點任一處返回)
fn screen(hosts: &[Host], width: usize, tick: u64, t: i64) -> Vec<Line> {
    let legend = if hosts.iter().any(|h| h.nerd) {
        "  框:\u{f0035} Mac · \u{f05b3} Win · \u{f033d} Linux · \u{f02a4} GitHub · CI 能接 CI · Ez2 客戶主機"
    } else {
        "  框:M Mac · W Win · L Linux · G GitHub · -CI 能接 CI · Ez2 客戶主機"
    };
    let mut lines: Vec<Line> = vec![
        vec![
            (format!("主機群 {}(台北)", taipei_clock(t)), Color::White),
            (legend.into(), Color::DarkGrey),
        ],
        vec![(
            "燈:連線 · 租約(綠空 黃租 紫沒租約在跑)· 點機箱看明細(1–9 也可)".into(),
            Color::DarkGrey,
        )],
    ];
    // Andy 11-12 20:5x:上面兩行說明也置中
    for l in lines.iter_mut() {
        let lw: usize = l.iter().map(|(t, _)| w(t)).sum();
        l.insert(0, (" ".repeat(width.saturating_sub(lw) / 2), Color::White));
    }
    if let Some((i, h)) = hosts.iter().enumerate().find(|(_, h)| h.open) {
        lines.push(vec![(
            format!("◀ 返回(點任一處)  {} · {}", h.name, h.role),
            Color::Cyan,
        )]);
        lines.extend(host_lines(h, i, width, tick, t));
        return lines;
    }
    if hosts.is_empty() {
        lines.push(vec![(
            format!("沒有主機設定:{}", config_path().display()),
            Color::DarkGrey,
        )]);
        return lines;
    }
    let all: Vec<(usize, &Host)> = hosts.iter().enumerate().collect();
    for chunk in all.chunks(per_row(width)) {
        let towers: Vec<Vec<Line>> = chunk.iter().map(|(i, h)| tower(h, *i)).collect();
        let lead = (" ".repeat(grid_off(width)), Color::White);
        for r in 0..TOWER_H {
            lines.push(
                std::iter::once(lead.clone())
                    .chain(towers.iter().flat_map(|tw| tw[r].clone()))
                    .collect(),
            );
        }
    }
    lines
}

/// 整排在窗格裡置中：左邊空幾格
fn grid_off(width: usize) -> usize {
    width.saturating_sub(per_row(width) * CELL) / 2
}

/// 點到哪一台(機箱格模式);明細模式點哪都回 None(呼叫端收起)
fn hit(hosts: &[Host], width: usize, x: usize, y: usize) -> Option<usize> {
    let off = grid_off(width);
    if y < 2 || hosts.iter().any(|h| h.open) || x < off || (x - off) / CELL >= per_row(width) {
        return None;
    }
    let i = (y - 2) / TOWER_H * per_row(width) + (x - off) / CELL;
    (i < hosts.len()).then_some(i)
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
            if *c == EZ2_BADGE {
                // Ez2 銘牌：金底深字反白
                queue!(
                    o,
                    SetBackgroundColor(Color::Yellow),
                    SetForegroundColor(Color::Black),
                    Print(t),
                    ResetColor
                )?;
            } else {
                queue!(o, SetForegroundColor(*c), Print(t))?;
            }
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
    let (ktx, krx) = mpsc::channel::<(usize, String)>();
    if once {
        let mut waiting = 0;
        for (i, h) in hosts.iter().enumerate().filter(|(_, h)| h.watch) {
            ask(i, h.probe(), tx.clone());
            waiting += 1;
        }
        drop(tx);
        // 等到全部回來或 STUCK 到期;卡住的那台照「…」畫，不讓整支卡死(043dd #726 L1)
        let end = Instant::now() + STUCK;
        while waiting > 0 {
            let Some((i, s)) = recv_until(&rx, end) else {
                break;
            };
            waiting -= 1;
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
            if should_ask(h, STUCK) {
                ask(i, h.probe(), tx.clone());
            }
        }
        while let Ok((i, s)) = rx.try_recv() {
            if let Some(h) = hosts.get_mut(i) {
                record(h, s);
            }
        }
        while let Ok((i, n)) = krx.try_recv() {
            if let Some(h) = hosts.get_mut(i) {
                h.note = Some(n);
                // 關掉之後馬上重讀一次，看負載有沒有下來
                h.at = None;
            }
        }
        let (cw, ch) = terminal::size().unwrap_or((100, 20));
        let lines = screen(&hosts, cw as usize, tick, now());
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
                    let was = hosts.get(i).is_some_and(|h| h.open);
                    hosts.iter_mut().for_each(|h| {
                        h.open = false;
                        h.note = None;
                    });
                    if let Some(h) = hosts.get_mut(i) {
                        h.open = !was;
                    }
                }
                _ => {}
            },
            Ok(Event::Mouse(m)) if m.kind == MouseEventKind::Down(MouseButton::Left) => {
                let (cw, _) = terminal::size().unwrap_or((100, 20));
                let (x, y) = (m.column as usize, m.row as usize);
                let lines = screen(&hosts, cw as usize, tick, now());
                let open = hosts.iter().position(|h| h.open);
                if let (Some(i), Some(pid)) = (open, kill_hit(&lines, x, y)) {
                    let h = &mut hosts[i];
                    // 已經在等 Touch ID ⇒ 連點不再跳第二個視窗
                    let waiting = h.note.as_deref().is_some_and(|n| n.starts_with("等你按"));
                    if let Some(pr) = h
                        .last
                        .as_ref()
                        .filter(|_| !waiting)
                        .and_then(|s| s.top.iter().find(|p| p.pid == pid))
                    {
                        h.note = Some(format!(
                            "等你按 Touch ID:關閉 pid {pid}…(3 分鐘沒按 = 不關)"
                        ));
                        ask_kill(i, h.name.clone(), h.probe(), pr.clone(), ktx.clone());
                    }
                } else if hosts.iter().any(|h| h.open) {
                    hosts.iter_mut().for_each(|h| {
                        h.open = false;
                        h.note = None;
                    });
                } else if let Some(i) = hit(&hosts, cw as usize, m.column as usize, m.row as usize)
                {
                    hosts[i].open = true;
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

    /// Andy 11-12 15:0x:主機群 —— 每台一個直立機箱(預設收合),真實負載、功能;點了看明細，再點返回
    #[test]
    fn the_top_edge_carries_a_badge_m_w_l_g_or_the_ez2_tag() {
        let mac = host("mini", None, false);
        let mut win = host("win", None, false);
        win.kind = Kind::Windows;
        let mut lin = host("srv-a", None, false);
        lin.kind = Kind::Server;
        let mut ez2 = host("srv-b", None, false);
        ez2.kind = Kind::Server;
        ez2.tag = Some("Ez2".into());
        let mut gh = host("gh", None, false);
        gh.kind = Kind::GitHub;
        gh.ci = true;
        let mut mac_ci = host("andymini", None, false);
        mac_ci.ci = true;
        let top = |h: &Host| text(&tower(h, 0)[1]);
        // 左角右邊一格起;框線寬不變
        assert_eq!(top(&mac).trim(), "┌M─────┐");
        assert_eq!(top(&win).trim(), "┌W─────┐");
        assert_eq!(top(&lin).trim(), "┌L─────┐");
        assert_eq!(top(&gh).trim(), "┌G-CI──┐");
        assert_eq!(top(&mac_ci).trim(), "┌M-CI──┐");
        // hosts.json "icons": "nerd" ⇒ 商標字 + 一格空白;CI 直接接在後面
        let mut nerd_ci = host("andymini", None, false);
        nerd_ci.ci = true;
        nerd_ci.nerd = true;
        assert_eq!(badge(&nerd_ci).0, "\u{f0035} CI");
        let mut nerd_win = host("w", None, false);
        nerd_win.kind = Kind::Windows;
        nerd_win.nerd = true;
        assert_eq!(badge(&nerd_win).0, "\u{f05b3}");
        ez2.ci = true;
        assert_eq!(top(&ez2).trim(), "┌Ez2-CI┐", "最長剛好 6 格");
        ez2.ci = false;
        assert_eq!(top(&ez2).trim(), "┌Ez2───┐");
        assert_eq!(w(&top(&ez2)), CELL);
        // Ez2 用反白記號色，其他照種類色
        assert_eq!(tower(&ez2, 0)[1][1].1, EZ2_BADGE);
        assert_eq!(tower(&win, 0)[1][1].1, Color::Blue);
        // hosts.json 的 tag 讀得到
        let hs = hosts_from_config(
            r#"{"github_repo":"o/r","hosts":[{"name":"m","ssh":"m","ci":true},{"name":"p","ssh":"p"}],
                "servers":[{"name":"srv1","ssh":"x","role":"代管雲","tag":"Ez2"}]}"#,
        );
        let ci: Vec<(&str, bool)> = hs.iter().map(|h| (h.name.as_str(), h.ci)).collect();
        assert_eq!(
            ci,
            [("m", true), ("p", false), ("GitHub", true), ("srv1", false)]
        );
        assert_eq!(hs[3].tag.as_deref(), Some("Ez2"));
    }

    #[test]
    fn a_stuck_host_is_asked_again_once_then_waits_for_an_answer() {
        let mut h = host("m", None, false);
        assert!(should_ask(&mut h, Duration::ZERO), "第一次問");
        assert!(should_ask(&mut h, Duration::ZERO), "卡住後再問一次");
        assert!(h.stuck);
        assert!(
            !should_ask(&mut h, Duration::ZERO),
            "兩支都在途就不再起新的"
        );
        assert!(!should_ask(&mut h, Duration::ZERO));
        assert_eq!(h.inflight, MAX_INFLIGHT);
        // 舊的那支回來了:不再標卡住，在途少一支
        let s = Sample {
            online: true,
            ..Sample::default()
        };
        record(&mut h, s);
        assert!(!h.stuck);
        assert_eq!(h.inflight, MAX_INFLIGHT - 1);
        // 還沒卡住(門檻很長)就不重問
        h.at = None;
        assert!(should_ask(&mut h, Duration::from_secs(3600)));
        assert!(!should_ask(&mut h, Duration::from_secs(3600)));
    }

    #[test]
    fn customer_servers_are_not_probed_by_default_and_poll_overrides() {
        let hs = hosts_from_config(
            r#"{"hosts":[{"name":"m","ssh":"m"}],
                "servers":[{"name":"own","ssh":"x"},{"name":"cust","ssh":"y","tag":"Ez2"},
                           {"name":"cust2","ssh":"z","tag":"Ez2","poll":true},
                           {"name":"own2","ssh":"w","poll":false}]}"#,
        );
        let watch: Vec<(&str, bool)> = hs.iter().map(|h| (h.name.as_str(), h.watch)).collect();
        assert_eq!(
            watch,
            [
                ("m", true),
                ("own", true),
                ("cust", false),
                ("cust2", true),
                ("own2", false)
            ]
        );
        assert_eq!(hs[2].poll, SERVER_POLL);
        assert!(
            SERVER_POLL >= Duration::from_secs(300),
            "其他主機最快 5 分鐘一次"
        );
        // 不探的那台在機箱與明細都寫「不探」,不是「…」
        let row: String = tower(&hs[2], 2)[5]
            .iter()
            .map(|(t, _)| t.as_str())
            .collect();
        assert!(row.contains("不探"), "{row}");
        let detail: String = host_lines(&hs[2], 2, 100, 0, 0)
            .iter()
            .flatten()
            .map(|(t, _)| t.as_str())
            .collect();
        assert!(detail.contains("不探"), "{detail}");
    }

    #[test]
    fn the_fleet_is_a_grid_of_towers_and_a_click_opens_one() {
        let mut hs: Vec<Host> = (0..12)
            .map(|i| host(&format!("h{i}"), Some(parse_sample(HELD, true, "")), false))
            .collect();
        hs[11].kind = Kind::Server;
        hs[11].role = "正式站".into();
        hs[11].last = Some(Sample {
            online: true,
            server: true,
            no_lease: true,
            ncpu: 8.0,
            load1: 2.0,
            load5: 2.0,
            ..Sample::default()
        });
        let l = screen(&hs, 102, 0, 1500);
        assert!(
            text(&l[0]).trim_start().starts_with("主機群 "),
            "{}",
            text(&l[0])
        );
        // 兩行說明置中：左右空白差不超過 1
        for (y, row) in l.iter().enumerate().take(2) {
            let t = text(row);
            let left = t.len() - t.trim_start().len();
            assert!(
                left > 0 && left.abs_diff(102 - w(&t)) <= 1,
                "第 {y} 行沒置中:{t:?}"
            );
        }
        // 說明兩行都要在 102 格內看得完整
        assert!(text(&l[0]).contains("Ez2 客戶主機") && w(&text(&l[0])) <= 102);
        assert!(text(&l[1]).trim_start().starts_with("燈:連線") && w(&text(&l[1])) <= 102);
        assert_eq!(l.len(), 2 + 2 * TOWER_H, "102 格一排 8 台,12 台兩排");
        for (r, row) in l.iter().enumerate().skip(2) {
            assert!(w(&text(row)) <= 102, "row {r} too wide");
        }
        // Andy 20:4x 全置中:102 格放 8 台 = 96 格，左邊空 3 格;框在格子裡左右各空 2 格
        let off = grid_off(102);
        assert_eq!(off, 3);
        let col = |line: &Line, c: usize| {
            text(line)
                .chars()
                .skip(off + c * CELL)
                .take(CELL)
                .collect::<String>()
        };
        assert_eq!(col(&l[3], 0), "  ┌M─────┐  ", "框置中");
        assert_eq!(col(&l[2], 0).trim(), "① h0");
        assert!(
            col(&l[2], 0).starts_with("   "),
            "名字置中:{:?}",
            col(&l[2], 0)
        );
        // 第 10 台起沒有編號(只有 1–9 能按)
        assert_eq!(col(&l[2 + TOWER_H], 0).trim(), "⑨ h8");
        assert_eq!(col(&l[2 + TOWER_H], 1).trim(), "h9");
        // HELD:10 核負載 5.1 ⇒ 51%,黃(置中)
        assert_eq!(col(&l[7], 0).trim(), "測試 51%");
        // 第二排第四台 = 主機,2/8 = 25%
        assert!(
            text(&l[8 + TOWER_H - 1]).contains("正式站 25%"),
            "{}",
            text(&l[13])
        );
        // 點第二排第 4 台 ⇒ 打開 h11;明細模式點哪都不是機箱
        assert_eq!(hit(&hs, 102, off + 3 * CELL + 2, 2 + TOWER_H + 3), Some(11));
        assert_eq!(hit(&hs, 102, off - 1, 3), None, "左邊空白不是機箱");
        assert_eq!(hit(&hs, 102, off, 3), Some(0));
        assert_eq!(
            hit(&hs, 102, off + 4 * CELL + 2, 2 + TOWER_H + 3),
            None,
            "第二排只有 4 台"
        );
        assert_eq!(hit(&hs, 102, 3, 1), None, "標題列");
        hs[11].open = true;
        let d = screen(&hs, 102, 0, 1500);
        assert!(text(&d[2]).contains("返回") && text(&d[2]).contains("正式站"));
        assert!(d.iter().any(|x| text(x).contains("運作中(只看負載)")));
        assert_eq!(hit(&hs, 102, 12, 9), None);
        // 離線的機箱是灰的空殼
        let off = Host {
            last: Some(parse_sample("", false, "timed out")),
            ..hs[0].clone()
        };
        assert!(text(&tower(&off, 0)[5]).contains("離線"));
        assert!(text(&tower(&off, 0)[2]).contains("░"));
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
    fn opening_a_busy_host_says_why_and_each_process_has_an_agree_to_close_button() {
        let out = format!(
            "{HELD}\nAIB_TOP 4321 97.5 01:02:03 andyimkt Thu Nov 12 13:20:01 2026 /x/cargo test --manifest-path /u/wt.noindex/cooldown-34d15/Cargo.toml\n\
             AIB_TOP   77  2.5 05:00 root Thu Nov  2 09:00:00 2026 /usr/sbin/syslogd\nAIB_TOP bad"
        );
        let s = parse_sample(&out, true, "");
        // 前 5 名那幾列不能混進負載數字
        assert_eq!((s.ncpu, s.load1, s.load5), (10.0, 5.10, 4.28));
        assert_eq!(s.perf, 0.0, "pid 不能被當成效能核數");
        assert_eq!(s.owner, "4542e", "租約 JSON 照讀");
        assert_eq!(s.top.len(), 2, "壞列跳過");
        assert_eq!(
            s.top[1].start, "Thu Nov 2 09:00:00 2026",
            "啟動時間空白壓成一格"
        );
        let mut h = host("a", Some(s.clone()), true);
        let lines = host_lines(&h, 0, 160, 0, 1500);
        let o: Vec<String> = lines.iter().map(text).collect();
        assert!(
            o.iter()
                .any(|l| l.contains("為什麼忙:目前負載 5.1/10 核 · CPU 前 2 名合計 100%")),
            "{o:?}"
        );
        let row = o
            .iter()
            .position(|l| l.contains("[同意關閉 4321]"))
            .expect("每支一顆按鈕");
        assert!(
            o[row].contains("98% · 跑了 01:02:03 · andyimkt · (cooldown-34d15)"),
            "{}",
            o[row]
        );
        assert!(o.iter().any(|l| l.contains("[同意關閉 77]")));
        let x = w(&o[row][..o[row].find("[同意關閉").unwrap()]);
        assert_eq!(kill_hit(&lines, x + 2, row), Some(4321));
        assert_eq!(
            kill_hit(&lines, x.saturating_sub(2), row),
            None,
            "按鈕外面不算"
        );
        assert_eq!(kill_hit(&lines, x + 200, row), None, "按鈕右邊也不算");
        assert_eq!(kill_hit(&lines, x, 0), None);
        h.note = Some("等你按 Touch ID".into());
        let o: Vec<String> = host_lines(&h, 0, 160, 0, 1500).iter().map(text).collect();
        assert!(o.last().unwrap().contains("等你按 Touch ID"));
        // 收起來不列
        h.open = false;
        assert!(!host_lines(&h, 0, 160, 0, 1500)
            .iter()
            .map(text)
            .any(|l| l.contains("同意關閉")));
    }

    #[test]
    fn closing_checks_the_start_time_and_goes_through_hardened_ssh() {
        let mut pr = Proc {
            pid: 4321,
            cpu: 90.0,
            etime: "1:00".into(),
            user: "u".into(),
            start: "Thu Nov 12 13:20:01 2026".into(),
            cmd: "x".into(),
        };
        let mut p = host("a=local", None, false).probe();
        let local = sign_script("Pro 16", &p, &pr, "/r/k.log").unwrap();
        assert!(
            local.contains("# 可丟環境: Pro 16 收據: /r/k.log"),
            "{local}"
        );
        assert!(local.contains("set -euo pipefail"), "{local}");
        assert!(local.contains("ps -o lstart= -p 4321"), "{local}");
        assert!(
            local.contains("if [ \"$now\" != 'Thu Nov 12 13:20:01 2026' ]; then\n  echo 'pid 4321 已換成別的程式，沒有關'; exit 3"),
            "{local}"
        );
        assert!(
            local.ends_with("kill 4321\necho 'killed 4321'\n"),
            "{local}"
        );
        assert!(!local.contains("-9") && !local.contains("ssh "), "{local}");
        p.target = "me@box".into();
        p.identity = Some("/k/id".into());
        p.jump_fallback = Some("j@hop".into());
        let remote = sign_script("andymini", &p, &pr, "/r/k.log").unwrap();
        let ssh: Vec<&str> = remote.lines().filter(|l| l.contains("ssh -o")).collect();
        assert_eq!(ssh.len(), 2, "直連一次 + 跳板一次:{remote}");
        for l in &ssh {
            assert!(
                l.contains("ssh -o ForwardAgent=no -o ClearAllForwardings=yes -o BatchMode=yes"),
                "{l}"
            );
            assert!(
                l.contains("-i '/k/id' ") && l.contains("-- 'me@box' '"),
                "{l}"
            );
            assert!(
                l.contains("[ \"$now\" != \"Thu Nov 12 13:20:01 2026\" ]"),
                "{l}"
            );
        }
        assert!(ssh[1].contains("-J 'j@hop' --"), "{}", ssh[1]);
        assert!(remote.contains(
            "if [ \"$rc\" = 255 ]; then echo '連不上 andymini(ssh 255),沒有關'; exit 4; fi"
        ));
        // 遠端給的啟動時間有怪字 ⇒ 不產腳本
        pr.start = "Thu Nov 12 13:20:01 2026'; rm -rf ~".into();
        assert!(sign_script("a", &p, &pr, "/r").is_none());
        pr.start.clear();
        assert!(sign_script("a", &p, &pr, "/r").is_none());
        assert_eq!(sh_quote("it's"), "'it'\\''s'");
        assert_eq!(slug("Pro 16/x"), "Pro_16_x");
        assert_eq!(tree_of("a /wt.noindex/x-12345/b"), Some("x-12345"));
        assert_eq!(tree_of("plain"), None);
    }

    #[test]
    fn only_an_ok_from_consent_counts_as_closed() {
        let n = |o: &str| kill_note("mini", 7, o);
        assert_eq!(n("{\"id\":\"c1\",\"result\":\"ok\"}"), "已關閉 pid 7(mini)");
        assert_eq!(n("{\"result\":\"denied\"}"), "沒關閉 pid 7:你按了拒絕");
        assert_eq!(
            n("{\"result\":\"timeout\"}"),
            "沒關閉 pid 7:沒按 Touch ID 或 3 分鐘逾時"
        );
        assert_eq!(
            n("{\"result\":\"pending\"}"),
            "沒關閉 pid 7:沒按 Touch ID 或 3 分鐘逾時"
        );
        assert!(n("{\"result\":\"rc_nonzero\"}").contains("看收據"));
        assert_eq!(
            n("{\"result\":\"not_allowed\",\"why\":\"sign_lint\"}"),
            "沒關閉 pid 7:not_allowed(sign_lint)"
        );
        assert_eq!(
            n("c1 ok"),
            "沒關閉 pid 7:aib consent 沒回結果",
            "不是 JSON 不算關了"
        );
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
