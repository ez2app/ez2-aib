// SPDX-License-Identifier: Apache-2.0
//! `aib-hosts`:測試機一台一顆電池，放在 tmux 下欄(Andy 11-12 05:0x:「每台主機顯示成一個動態電池，
//! 真實的顯示與動態變化」)。
//!
//! - 每台每 20 秒用 ssh 讀一次 `aib lease status --json` 與 `sysctl -n hw.ncpu vm.loadavg`
//!   (一次性執行緒，ADR 0005 不用 `thread::sleep`;卡住 60 秒顯示「?」、不重起)。
//! - 電池 = 這台還能給別人用的量:空機滿格綠色;有人租著 = 租約剩餘時間比例，隨時間往下掉(黃);
//!   連不上 = 空殼紅色「離線」。佔用中的電池有一格亮點來回跑(動態),負載用最近 30 筆畫小折線。
//! - 只讀：不拿租約、不寫任何狀態。`--once [--width N]` 印一次就結束(測試用)。
//! - ssh 一律 `ForwardAgent=no` / `ClearAllForwardings=yes` / `BatchMode=yes`。

use crossterm::{
    cursor, event,
    event::{Event, KeyCode},
    execute, queue,
    style::{Color, Print, ResetColor, SetForegroundColor},
    terminal,
};
use serde_json::Value;
use std::io::Write;
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const DEFAULT_HOSTS: &[&str] = &["andymini", "lin-macmini", "andyimkt@100.116.110.31"];
const POLL: Duration = Duration::from_secs(20);
const STUCK: Duration = Duration::from_secs(60);
const SPARK: &[char] = &['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█'];

#[derive(Debug, Clone, Default, PartialEq)]
struct Sample {
    online: bool,
    held: bool,
    owner: String,
    what: String,
    acquired: i64,
    expiry: i64,
    queue: usize,
    ncpu: f64,
    load1: f64,
    err: String,
}

struct Host {
    name: String,
    last: Option<Sample>,
    at: Option<Instant>,
    loads: Vec<f64>,
    asked: Option<Instant>,
    stuck: bool,
}

fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

/// ssh 輸出 = lease JSON(多行)+ `AIB_HOSTS_SYS <ncpu> { l1 l5 l15 }`。
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
    if let Ok(v) = serde_json::from_str::<Value>(json.trim()) {
        s.online = true;
        s.held = v["state"] == "held";
        let l = &v["lease"];
        s.owner = l["owner"]
            .as_str()
            .unwrap_or("")
            .trim_start_matches('@')
            .to_string();
        s.what = l["what"].as_str().unwrap_or("").to_string();
        s.acquired = l["acquired_epoch"].as_i64().unwrap_or(0);
        s.expiry = l["expiry_epoch"].as_i64().unwrap_or(0);
        s.queue = v["queue"].as_array().map_or(0, |a| a.len());
    } else {
        s.online = ok && !nums.is_empty();
    }
    s
}

fn ask(host: String, tx: mpsc::Sender<(String, Sample)>) {
    std::thread::spawn(move || {
        let o = Command::new("ssh")
            .args([
                "-o",
                "ForwardAgent=no",
                "-o",
                "ClearAllForwardings=yes",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=8",
                "--",
                host.as_str(),
                "PATH=$HOME/.local/bin:$PATH; aib lease status --json 2>/dev/null; \
                 echo AIB_HOSTS_SYS $(sysctl -n hw.ncpu) $(sysctl -n vm.loadavg)",
            ])
            .stdin(Stdio::null())
            .output();
        let s = match o {
            Ok(o) => parse_sample(
                &String::from_utf8_lossy(&o.stdout),
                o.status.success(),
                &String::from_utf8_lossy(&o.stderr),
            ),
            Err(e) => Sample {
                err: e.to_string(),
                ..Sample::default()
            },
        };
        let _ = tx.send((host, s));
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

/// 電池剩多少(0..=100)與顏色。
fn charge(s: Option<&Sample>, t: i64) -> (u32, Color) {
    match s {
        None => (0, Color::DarkGrey),
        Some(s) if !s.online => (0, Color::Red),
        Some(s) if !s.held => (100, Color::Green),
        Some(s) => {
            let span = (s.expiry - s.acquired).max(1);
            let left = (s.expiry - t).clamp(0, span);
            let p = u32::try_from(left * 100 / span).unwrap_or(0);
            (p, if p < 20 { Color::Red } else { Color::Yellow })
        }
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

/// 一台三行：名字 + 電池 + 狀態;負載折線;在做什麼。
fn host_lines(h: &Host, width: usize, tick: u64, t: i64) -> Vec<Line> {
    let s = h.last.as_ref();
    let (pct, col) = charge(s, t);
    let cells = width.saturating_sub(40).clamp(10, 40);
    let filled = (pct as usize * cells).div_ceil(100).min(cells);
    let mut bar = String::new();
    let busy = s.is_some_and(|s| s.online && s.held);
    // 佔用中：亮點在已充的格子裡來回跑
    let spot = if busy && filled > 1 {
        let k = (tick as usize) % (2 * filled - 2);
        Some(if k < filled { k } else { 2 * filled - 2 - k })
    } else {
        None
    };
    for i in 0..cells {
        bar.push(if i < filled {
            if spot == Some(i) {
                '▓'
            } else {
                '█'
            }
        } else {
            '░'
        });
    }
    let name = fit(h.name.rsplit('@').next().unwrap_or(&h.name), 14);
    let pad = " ".repeat(15usize.saturating_sub(w(&name)));
    let status = match s {
        None => "讀取中…".to_string(),
        Some(s) if !s.online => format!("離線 {}", fit(&s.err, 30)),
        Some(s) if !s.held => "空機 可用".to_string(),
        Some(s) => format!("{} 用 · 剩 {}", s.owner, mins(s.expiry - t)),
    };
    let stale = if h.stuck { " ?" } else { "" };
    let mut l1: Line = vec![
        (format!("{name}{pad}"), Color::White),
        (format!("▕{bar}▏"), col),
        ("▌".into(), col),
        (format!(" {pct:>3}% "), col),
        (format!("{status}{stale}"), Color::White),
    ];
    if let Some(s) = s {
        if s.queue > 0 {
            l1.push((format!(" · 排隊 {}", s.queue), Color::Cyan));
        }
    }
    let spark: String = h
        .loads
        .iter()
        .map(|l| {
            let cap = s.map_or(8.0, |s| if s.ncpu > 0.0 { s.ncpu } else { 8.0 });
            let i = ((l / cap) * 7.0).round().clamp(0.0, 7.0) as usize;
            SPARK[i]
        })
        .collect();
    let load = s.map_or(String::new(), |s| {
        if s.online {
            format!("負載 {:.1}/{:.0} 核 ", s.load1, s.ncpu)
        } else {
            String::new()
        }
    });
    let lcol = s.map_or(Color::DarkGrey, |s| {
        if s.ncpu > 0.0 && s.load1 > s.ncpu {
            Color::Red
        } else {
            Color::Blue
        }
    });
    let l2: Line = vec![
        (" ".repeat(15), Color::White),
        (load, Color::DarkGrey),
        (spark, lcol),
    ];
    let what = s
        .filter(|s| s.held)
        .map_or(String::new(), |s| fit(&s.what, width.saturating_sub(16)));
    let l3: Line = vec![(" ".repeat(15), Color::White), (what, Color::DarkGrey)];
    vec![l1, l2, l3]
}

fn frame(hosts: &[Host], width: usize, tick: u64, t: i64) -> Vec<Line> {
    let clock = {
        // 台北時間(UTC+8),不依賴本機時區
        let s = (t + 8 * 3600).rem_euclid(86400);
        format!("{:02}:{:02}:{:02}", s / 3600, s % 3600 / 60, s % 60)
    };
    let mut out: Vec<Line> = vec![vec![
        (format!("測試機 {clock}(台北)"), Color::White),
        (
            "  電池 = 還能給別人用的量 · 綠 空機 · 黃 租約剩餘 · 紅 離線 · q 離開".into(),
            Color::DarkGrey,
        ),
    ]];
    for h in hosts {
        out.extend(host_lines(h, width, tick, t));
    }
    out
}

fn draw(o: &mut impl Write, lines: &[Line], width: usize, height: usize) -> std::io::Result<()> {
    queue!(o, cursor::MoveTo(0, 0))?;
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

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let once = args.iter().any(|a| a == "--once");
    let mut width = 100usize;
    let mut names: Vec<String> = Vec::new();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--once" => {}
            "--width" => width = it.next().and_then(|v| v.parse().ok()).unwrap_or(100),
            "-h" | "--help" => {
                println!("usage: aib-hosts [--once [--width N]] [HOST]...  (預設 andymini lin-macmini Pro 13)");
                return;
            }
            h => names.push(h.to_string()),
        }
    }
    if names.is_empty() {
        names = DEFAULT_HOSTS.iter().map(|s| s.to_string()).collect();
    }
    let mut hosts: Vec<Host> = names
        .into_iter()
        .map(|name| Host {
            name,
            last: None,
            at: None,
            loads: Vec::new(),
            asked: None,
            stuck: false,
        })
        .collect();
    let (tx, rx) = mpsc::channel::<(String, Sample)>();
    if once {
        for h in &hosts {
            ask(h.name.clone(), tx.clone());
        }
        drop(tx);
        for (name, s) in rx.iter() {
            if let Some(h) = hosts.iter_mut().find(|h| h.name == name) {
                h.loads.push(s.load1);
                h.last = Some(s);
            }
        }
        let mut out = std::io::stdout();
        for l in frame(&hosts, width, 0, now()) {
            let s: String = l.iter().map(|(t, _)| t.as_str()).collect();
            if writeln!(out, "{}", fit(&s, width)).is_err() {
                return;
            }
        }
        return;
    }
    let mut out = std::io::stdout();
    let _ = terminal::enable_raw_mode();
    let _ = execute!(out, terminal::EnterAlternateScreen, cursor::Hide);
    std::panic::set_hook(Box::new(|i| {
        let _ = terminal::disable_raw_mode();
        let _ = execute!(
            std::io::stdout(),
            cursor::Show,
            terminal::LeaveAlternateScreen
        );
        eprintln!("{i}");
    }));
    let mut tick: u64 = 0;
    loop {
        let t0 = Instant::now();
        for h in hosts.iter_mut() {
            let due = h.at.is_none_or(|a| a.elapsed() >= POLL);
            match h.asked {
                Some(a) if a.elapsed() >= STUCK => h.stuck = true,
                Some(_) => {}
                None if due => {
                    h.asked = Some(Instant::now());
                    ask(h.name.clone(), tx.clone());
                }
                None => {}
            }
        }
        while let Ok((name, s)) = rx.try_recv() {
            if let Some(h) = hosts.iter_mut().find(|h| h.name == name) {
                h.loads.push(s.load1);
                if h.loads.len() > 30 {
                    h.loads.remove(0);
                }
                h.last = Some(s);
                h.at = Some(Instant::now());
                h.asked = None;
                h.stuck = false;
            }
        }
        let (cw, ch) = terminal::size().unwrap_or((100, 20));
        let lines = frame(&hosts, cw as usize, tick, now());
        if draw(&mut out, &lines, cw as usize, ch as usize).is_err() {
            break;
        }
        tick = tick.wrapping_add(1);
        // 動畫節奏靠 event::poll(ADR 0005)
        let wait = Duration::from_millis(250).saturating_sub(t0.elapsed());
        if event::poll(wait).unwrap_or(false) {
            if let Ok(Event::Key(k)) = event::read() {
                if matches!(k.code, KeyCode::Char('q') | KeyCode::Esc) {
                    break;
                }
            }
        }
    }
    let _ = terminal::disable_raw_mode();
    let _ = execute!(out, cursor::Show, terminal::LeaveAlternateScreen);
}

#[cfg(test)]
mod tests {
    use super::*;

    const HELD: &str = r#"{"state":"held","lease":{"owner":"@4542e","what":"scan","acquired_epoch":1000,"expiry_epoch":2000},"queue":[{},{}]}
AIB_HOSTS_SYS 10 { 5.10 4.28 4.04 }"#;

    #[test]
    fn a_held_lease_drains_with_time_and_shows_queue_and_load() {
        let s = parse_sample(HELD, true, "");
        assert!(s.online && s.held);
        assert_eq!((s.owner.as_str(), s.queue), ("4542e", 2));
        assert_eq!((s.ncpu, s.load1), (10.0, 5.1));
        assert_eq!(charge(Some(&s), 1000).0, 100);
        assert_eq!(charge(Some(&s), 1500).0, 50);
        assert_eq!(charge(Some(&s), 1900), (10, Color::Red));
        assert_eq!(charge(Some(&s), 9999).0, 0);
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

    #[test]
    fn every_width_draws_without_panicking_and_the_spot_moves() {
        let h = Host {
            name: "andyimkt@100.116.110.31".into(),
            last: Some(parse_sample(HELD, true, "")),
            at: None,
            loads: vec![1.0, 5.0, 12.0],
            asked: None,
            stuck: true,
        };
        for width in [0, 1, 20, 60, 128, 300] {
            for tick in 0..5 {
                let f = frame(std::slice::from_ref(&h), width, tick, 1500);
                assert_eq!(f.len(), 4);
            }
        }
        let a = frame(std::slice::from_ref(&h), 100, 0, 1500);
        let b = frame(std::slice::from_ref(&h), 100, 3, 1500);
        assert_ne!(a[1][1].0, b[1][1].0, "the bright cell moves while busy");
        assert!(a[1][4].0.ends_with(" ?"), "stuck shows ?");
        assert_eq!(a[1][0].0.trim(), "100.116.110.31");
    }
}
