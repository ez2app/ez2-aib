//! `aib-plan`:艙進度樹，放在 tmux 中間窗格(左對話 / 中進度樹 / 右 AI 欄)。
//!
//!   aib-plan [--data <plan.json>] [--aib-root <aib 倉>] [--once [--width N]]
//!
//! - 預設讀 ~/.ez2-aib/pending/aib2-a63ab-1107/cabin-plan/data.json(網頁版、Claude mod 同一份);
//!   檔案一變就重讀。
//! - 有 --aib-root 時每 30 秒唯讀數一次各席 dm 佇列(待審 / 已讀未結);讀不到顯示「?」。
//! - --once:不進互動，印一次就結束(給 Codex 席、測試、沒有 TTY 的地方)。
//!
//! 按鍵:↑↓ 移動 · → 展開 · ← 收合 / 回上層 · Enter 切換 · e 全部展開 · c 全部收合 ·
//! o 只看沒做完 · x 只看延伸 · n 沒人負責 · s 換席 · / 搜尋 · r 清除篩選 ·
//! [ 收起 / 展開左欄(樹) · ] 收起 / 展開右欄(為什麼) · q 離開

use aib_plan::{
    bar, chain, fit, flatten, index, mark, pad, parse, pct, str_width, tally, Filter, Plan, PASS,
};
use crossterm::{
    cursor,
    event::{self, Event, KeyCode, KeyEvent, KeyModifiers},
    execute, queue,
    style::{Attribute, Color, Print, ResetColor, SetAttribute, SetForegroundColor},
    terminal::{self, ClearType},
};
use std::collections::{BTreeMap, HashSet};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::time::{Duration, Instant, SystemTime};

const DM_EVERY: Duration = Duration::from_secs(30);

struct Args {
    data: PathBuf,
    aib_root: Option<PathBuf>,
    once: bool,
    width: usize,
}

fn home() -> PathBuf {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_default()
}

fn parse_args() -> Result<Args, String> {
    let mut a = Args {
        data: home()
            .join(".ez2-aib")
            .join("pending")
            .join("aib2-a63ab-1107")
            .join("cabin-plan")
            .join("data.json"),
        aib_root: None,
        once: false,
        width: 120,
    };
    let mut it = std::env::args().skip(1);
    while let Some(x) = it.next() {
        match x.as_str() {
            "--data" => a.data = it.next().ok_or("missing_value: --data")?.into(),
            "--aib-root" => a.aib_root = Some(it.next().ok_or("missing_value: --aib-root")?.into()),
            "--once" => a.once = true,
            "--width" => {
                a.width = it
                    .next()
                    .and_then(|v| v.parse().ok())
                    .ok_or("bad_value: --width")?
            }
            "-h" | "--help" => {
                return Err(
                    "usage: aib-plan [--data FILE] [--aib-root DIR] [--once [--width N]]".into(),
                )
            }
            other => return Err(format!("unknown_arg: {other}")),
        }
    }
    Ok(a)
}

type DmMap = BTreeMap<String, (Option<u32>, Option<u32>)>;

/// 唯讀數佇列：沿用現有 dm_queue.py list(P1 帳本做好後改讀 aib-drive 快照)。
fn count_dm(root: &Path, seats: &[String]) -> DmMap {
    let mut out = DmMap::new();
    let script = root.join("src").join("cockpit").join("dm_queue.py");
    for s in seats {
        let get = |tab: &str| -> Option<u32> {
            let o = std::process::Command::new("python3")
                .arg(&script)
                .args(["list", "--seat", s, "--tab", tab])
                .stdin(std::process::Stdio::null())
                .output()
                .ok()?;
            if !o.status.success() {
                return None;
            }
            let v: serde_json::Value = serde_json::from_slice(&o.stdout).ok()?;
            v.as_array().map(|a| a.len() as u32)
        };
        let w = get("waiting");
        let r = get("read");
        out.insert(s.clone(), (w, r));
    }
    out
}

struct App {
    plan: Plan,
    err: Option<String>,
    mtime: Option<SystemTime>,
    open: HashSet<String>,
    sel: usize,
    filter: Filter,
    show_tree: bool,
    show_side: bool,
    typing: Option<String>,
    dm: DmMap,
    dm_at: Option<Instant>,
}

impl App {
    fn load(&mut self, path: &Path) {
        let m = std::fs::metadata(path).and_then(|m| m.modified()).ok();
        if m.is_some() && m == self.mtime && self.err.is_none() {
            return;
        }
        match std::fs::read_to_string(path)
            .map_err(|e| format!("read_failed: {e}"))
            .and_then(|t| parse(&t))
        {
            Ok(p) => {
                let keep = self.plan.nodes.get(self.sel).map(|n| n.id.clone());
                self.plan = p;
                self.err = None;
                self.mtime = m;
                let ix = index(&self.plan);
                self.sel = keep
                    .and_then(|k| ix.by.get(&k).copied())
                    .or(ix.root)
                    .unwrap_or(0);
            }
            Err(e) => self.err = Some(e),
        }
    }

    fn cabin_seats(&self) -> Vec<String> {
        self.plan
            .seats
            .iter()
            .filter(|(_, s)| s.cabin == self.plan.cabin)
            .map(|(k, _)| k.clone())
            .collect()
    }

    fn dm_of(&self, who: Option<&str>) -> Option<(Option<u32>, Option<u32>)> {
        let w = who?;
        if let Some(v) = self.dm.get(w) {
            return Some(*v);
        }
        self.plan
            .seats
            .get(w)
            .and_then(|s| s.dm.as_ref())
            .map(|d| (d.waiting, d.read))
    }

    fn dm_txt(&self, who: Option<&str>) -> String {
        match self.dm_of(who) {
            None => String::new(),
            Some((None, _)) => " ✉?".into(),
            Some((Some(w), r)) => {
                let mut s = String::new();
                if w > 0 {
                    s += &format!(" ✉{w}");
                }
                if let Some(r) = r.filter(|r| *r > 0) {
                    s += &format!(" ◌{r}");
                }
                s
            }
        }
    }
}

/// 一段要畫的字：文字 + 顏色 + 是否反白 / 粗體。
#[derive(Clone)]
struct Seg(String, Option<Color>, bool, bool);

fn seg(s: impl Into<String>) -> Seg {
    Seg(s.into(), None, false, false)
}

fn status_color(s: Option<&str>) -> Option<Color> {
    match s {
        Some(PASS) => Some(Color::Green),
        Some("實查不通過") => Some(Color::Red),
        Some("未驗") => Some(Color::Yellow),
        Some(_) => Some(Color::DarkGrey),
        None => None,
    }
}

/// 把整個畫面算成「每行一串 Seg」;純函式，--once 與互動共用。
fn frame(app: &App, w: usize, h: usize) -> Vec<Vec<Seg>> {
    let mut out: Vec<Vec<Seg>> = Vec::new();
    if let Some(e) = &app.err {
        out.push(vec![Seg(
            format!("讀不到進度資料:{e}"),
            Some(Color::Red),
            false,
            false,
        )]);
        return out;
    }
    let p = &app.plan;
    let ix = index(p);
    let Some(root) = ix.root else {
        out.push(vec![seg("進度資料是空的")]);
        return out;
    };
    let all = tally(p, &ix, root);
    out.push(vec![
        Seg(
            format!(
                "艙 {} · 整體 {}% {} ({}/{})",
                p.cabin,
                pct(all),
                bar(pct(all), 16),
                all.0,
                all.1
            ),
            None,
            false,
            true,
        ),
        Seg(
            format!("  · {}", p.asof),
            Some(Color::DarkGrey),
            false,
            false,
        ),
    ]);
    let mut g = Vec::new();
    for &k in &ix.kids[root] {
        let t = tally(p, &ix, k);
        g.push(seg(format!(
            "{} {} {}%   ",
            p.nodes[k].id,
            bar(pct(t), 6),
            pct(t)
        )));
    }
    out.push(g);
    let mut seats = vec![seg("席位:")];
    for s in app.cabin_seats() {
        let d = app.dm_txt(Some(&s));
        let col = if d.contains('✉') {
            Some(Color::Red)
        } else {
            None
        };
        seats.push(Seg(
            format!("{s}{}   ", if d.is_empty() { " ✓".into() } else { d }),
            col,
            false,
            false,
        ));
    }
    let age = app
        .dm_at
        .map(|t| format!("佇列 {} 秒前", t.elapsed().as_secs()))
        .unwrap_or_else(|| "佇列：用資料檔".into());
    seats.push(Seg(
        format!("(✉ 待審 · ◌ 已讀未結 · {age})"),
        Some(Color::DarkGrey),
        false,
        false,
    ));
    out.push(seats);
    let f = &app.filter;
    let on = |b: bool, s: &str| format!("{}{s}", if b { "●" } else { "○" });
    out.push(vec![Seg(
        format!(
            "[ {}  ] {}  │ {} {} {} 席:{} │ /搜尋{} │ e 全開 c 全收 r 清除 q 離開",
            if app.show_tree { "◀樹" } else { "▶樹" },
            if app.show_side {
                "為什麼▶"
            } else {
                "◀為什麼"
            },
            on(f.only_open, "o 沒做完"),
            on(f.only_ext, "x 延伸"),
            on(f.orphan, "n 沒人負責"),
            if f.seat.is_empty() {
                "全部(s)"
            } else {
                &f.seat
            },
            match &app.typing {
                Some(t) => format!(":{t}▏"),
                None if !f.q.is_empty() => format!(":{}", f.q),
                None => String::new(),
            }
        ),
        Some(Color::Cyan),
        false,
        false,
    )]);
    out.push(vec![seg("─".repeat(w))]);

    let rows = flatten(p, &ix, &app.open, f);
    let body_h = h.saturating_sub(out.len() + 1).max(4);
    let side_by_side = w >= 100 && app.show_tree && app.show_side;
    let tree_w = if side_by_side { w * 58 / 100 } else { w };
    let side_w = if side_by_side { w - tree_w - 3 } else { w };

    // 左：樹
    let mut tree_lines: Vec<Vec<Seg>> = Vec::new();
    if app.show_tree {
        let room = if !side_by_side && app.show_side {
            body_h.saturating_sub(9).max(3)
        } else {
            body_h
        };
        let si = rows.iter().position(|r| r.idx == app.sel).unwrap_or(0);
        let start = si
            .saturating_sub(room / 2)
            .min(rows.len().saturating_sub(room));
        if rows.is_empty() {
            tree_lines.push(vec![seg("沒有符合篩選的項目。")]);
        }
        for r in rows.iter().skip(start).take(room) {
            let n = &p.nodes[r.idx];
            let t = tally(p, &ix, r.idx);
            let leaf = ix.kids[r.idx].is_empty();
            let right = format!(
                " {:>4} {}{}",
                if t.1 > 0 {
                    format!("{}%", pct(t))
                } else {
                    String::new()
                },
                n.owner.as_deref().unwrap_or(
                    if n.kind == "task" && n.status.as_deref() != Some(PASS) {
                        "沒人負責"
                    } else {
                        ""
                    }
                ),
                app.dm_txt(n.owner.as_deref())
            );
            let left = format!(
                "{}{} {}{}",
                "  ".repeat(r.depth),
                mark(p, &ix, r.idx, &app.open, f.active()),
                if n.kind == "ext" { "延伸 " } else { "" },
                n.title
            );
            let lw = tree_w.saturating_sub(str_width(&right)).max(8);
            let col = if leaf {
                status_color(n.status.as_deref())
            } else {
                None
            };
            let orphan = n.kind == "task" && n.owner.is_none() && n.status.as_deref() != Some(PASS);
            tree_lines.push(vec![
                Seg(pad(&left, lw), col, r.idx == app.sel, n.kind == "group"),
                Seg(
                    fit(&right, tree_w - lw),
                    if orphan {
                        Some(Color::Red)
                    } else {
                        Some(Color::DarkGrey)
                    },
                    false,
                    false,
                ),
            ]);
        }
    } else {
        tree_lines.push(vec![Seg(
            "(左欄已收起，按 [ 展開)".into(),
            Some(Color::DarkGrey),
            false,
            false,
        )]);
    }

    // 右：為什麼 + 細節
    let mut side: Vec<Vec<Seg>> = Vec::new();
    if app.show_side {
        let n = &p.nodes[app.sel.min(p.nodes.len() - 1)];
        side.push(vec![Seg("為什麼在做這件".into(), None, false, true)]);
        let ch = chain(p, &ix, app.sel.min(p.nodes.len() - 1));
        for (i, &c) in ch.iter().enumerate() {
            let last = i + 1 == ch.len();
            side.push(vec![Seg(
                fit(
                    &format!(
                        "{}{}{}",
                        "  ".repeat(i),
                        if i > 0 { "└ " } else { "" },
                        p.nodes[c].title
                    ),
                    side_w,
                ),
                if last { None } else { Some(Color::DarkGrey) },
                false,
                last,
            )]);
        }
        if let Some(why) = &n.why {
            side.push(vec![seg(fit(&format!("理由:{why}"), side_w))]);
        }
        if let Some(s) = &n.status {
            side.push(vec![Seg(
                format!("狀態:{s}"),
                status_color(Some(s)),
                false,
                false,
            )]);
        }
        let with = if n.with.is_empty() {
            String::new()
        } else {
            format!(" · 一起:{}", n.with.join(" "))
        };
        side.push(vec![seg(fit(
            &format!("誰做:{}{with}", n.owner.as_deref().unwrap_or("沒人負責")),
            side_w,
        ))]);
        if let Some(hd) = &n.handoff {
            side.push(vec![Seg(
                fit(&format!("接手:{hd}"), side_w),
                Some(Color::Yellow),
                false,
                false,
            )]);
        }
        let tm: Vec<String> = [("開始", &n.start), ("更新", &n.updated), ("預計", &n.eta)]
            .iter()
            .filter_map(|(k, v)| v.as_ref().map(|v| format!("{k} {v}")))
            .collect();
        if !tm.is_empty() {
            side.push(vec![Seg(
                fit(&format!("時間:{}", tm.join(" · ")), side_w),
                Some(Color::DarkGrey),
                false,
                false,
            )]);
        }
        if let Some((wq, rq)) = app.dm_of(n.owner.as_deref()) {
            let f = |v: Option<u32>| v.map(|x| x.to_string()).unwrap_or_else(|| "?".into());
            side.push(vec![seg(format!(
                "DM({}):待審 {} · 已讀未結 {}",
                n.owner.as_deref().unwrap_or(""),
                f(wq),
                f(rq)
            ))]);
        }
        if let Some(ev) = &n.evidence {
            side.push(vec![Seg(
                fit(&format!("證據:{ev}"), side_w),
                Some(Color::DarkGrey),
                false,
                false,
            )]);
        }
    }

    if side_by_side {
        for i in 0..body_h {
            let mut line = tree_lines.get(i).cloned().unwrap_or_default();
            let used: usize = line.iter().map(|s| str_width(&s.0)).sum();
            line.push(seg(" ".repeat(tree_w.saturating_sub(used))));
            line.push(Seg(" │ ".into(), Some(Color::DarkGrey), false, false));
            line.extend(side.get(i).cloned().unwrap_or_default());
            out.push(line);
        }
    } else {
        out.extend(tree_lines);
        if app.show_side {
            out.push(vec![seg("─".repeat(w))]);
            out.extend(side);
        }
    }
    out.truncate(h.saturating_sub(1).max(1));
    out
}

fn draw(app: &App) -> io::Result<()> {
    let (cw, ch) = terminal::size()?;
    let lines = frame(app, cw as usize, ch as usize);
    let mut o = io::stdout();
    queue!(o, cursor::MoveTo(0, 0), terminal::Clear(ClearType::All))?;
    for (y, line) in lines.iter().enumerate() {
        queue!(o, cursor::MoveTo(0, y as u16))?;
        let mut used = 0usize;
        for Seg(t, c, inv, bold) in line {
            let t = fit(t, (cw as usize).saturating_sub(used));
            used += str_width(&t);
            if let Some(c) = c {
                queue!(o, SetForegroundColor(*c))?;
            }
            if *inv {
                queue!(o, SetAttribute(Attribute::Reverse))?;
            }
            if *bold {
                queue!(o, SetAttribute(Attribute::Bold))?;
            }
            queue!(o, Print(t), SetAttribute(Attribute::Reset), ResetColor)?;
        }
    }
    o.flush()
}

fn on_key(app: &mut App, k: KeyEvent) -> bool {
    if let Some(buf) = app.typing.as_mut() {
        match k.code {
            KeyCode::Enter => {
                app.filter.q = buf.clone();
                app.typing = None;
            }
            KeyCode::Esc => app.typing = None,
            KeyCode::Backspace => {
                buf.pop();
            }
            KeyCode::Char(c) => buf.push(c),
            _ => {}
        }
        return true;
    }
    let ix = index(&app.plan);
    let rows = flatten(&app.plan, &ix, &app.open, &app.filter);
    let pos = rows.iter().position(|r| r.idx == app.sel).unwrap_or(0);
    let id = app
        .plan
        .nodes
        .get(app.sel)
        .map(|n| n.id.clone())
        .unwrap_or_default();
    match (k.code, k.modifiers) {
        (KeyCode::Char('q'), _) | (KeyCode::Char('c'), KeyModifiers::CONTROL) => return false,
        (KeyCode::Up | KeyCode::Char('k'), _) => {
            if let Some(r) = rows.get(pos.saturating_sub(1)) {
                app.sel = r.idx;
            }
        }
        (KeyCode::Down | KeyCode::Char('j'), _) => {
            if let Some(r) = rows.get((pos + 1).min(rows.len().saturating_sub(1))) {
                app.sel = r.idx;
            }
        }
        (KeyCode::Right, _) => {
            app.open.insert(id);
        }
        (KeyCode::Left, _) => {
            if app.open.contains(&id) {
                app.open.remove(&id);
            } else if let Some(&pi) = app.plan.nodes[app.sel]
                .parent
                .as_ref()
                .and_then(|x| ix.by.get(x))
            {
                app.sel = pi;
            }
        }
        (KeyCode::Enter | KeyCode::Char(' '), _) => {
            if !app.open.remove(&id) {
                app.open.insert(id);
            }
        }
        (KeyCode::Char('e'), _) => {
            app.open = app
                .plan
                .nodes
                .iter()
                .enumerate()
                .filter(|(i, _)| !ix.kids[*i].is_empty())
                .map(|(_, n)| n.id.clone())
                .collect()
        }
        (KeyCode::Char('c'), _) => {
            app.open = ix
                .root
                .map(|r| app.plan.nodes[r].id.clone())
                .into_iter()
                .collect()
        }
        (KeyCode::Char('o'), _) => app.filter.only_open = !app.filter.only_open,
        (KeyCode::Char('x'), _) => app.filter.only_ext = !app.filter.only_ext,
        (KeyCode::Char('n'), _) => app.filter.orphan = !app.filter.orphan,
        (KeyCode::Char('s'), _) => {
            let mut owners: Vec<String> = app
                .plan
                .nodes
                .iter()
                .filter_map(|n| n.owner.clone())
                .collect();
            owners.sort();
            owners.dedup();
            let mut cyc = vec![String::new()];
            cyc.extend(owners);
            let i = cyc.iter().position(|s| *s == app.filter.seat).unwrap_or(0);
            app.filter.seat = cyc[(i + 1) % cyc.len()].clone();
        }
        (KeyCode::Char('/'), _) => app.typing = Some(app.filter.q.clone()),
        (KeyCode::Char('r'), _) => app.filter = Filter::default(),
        (KeyCode::Char('['), _) => {
            app.show_tree = !app.show_tree;
            if !app.show_tree {
                app.show_side = true;
            }
        }
        (KeyCode::Char(']'), _) => {
            app.show_side = !app.show_side;
            if !app.show_side {
                app.show_tree = true;
            }
        }
        _ => {}
    }
    true
}

fn main() {
    let args = match parse_args() {
        Ok(a) => a,
        Err(e) => {
            eprintln!("{e}");
            std::process::exit(2);
        }
    };
    let mut app = App {
        plan: Plan::default(),
        err: None,
        mtime: None,
        open: ["V", "G5", "SC"].iter().map(|s| s.to_string()).collect(),
        sel: 0,
        filter: Filter::default(),
        show_tree: true,
        show_side: true,
        typing: None,
        dm: DmMap::new(),
        dm_at: None,
    };
    app.load(&args.data);
    if let Some(i) = index(&app.plan).by.get("SC") {
        app.sel = *i;
    }
    if args.once {
        if let Some(root) = &args.aib_root {
            app.dm = count_dm(root, &app.cabin_seats());
            app.dm_at = Some(Instant::now());
        }
        // 接 | head 時管線先關：寫失敗就安靜結束，不 panic(rc 101)
        let mut out = io::stdout().lock();
        for line in frame(&app, args.width, 400) {
            let text: String = line.iter().map(|s| s.0.as_str()).collect();
            if writeln!(out, "{text}").is_err() {
                break;
            }
        }
        let _ = out.flush();
        std::process::exit(if app.err.is_some() { 1 } else { 0 });
    }

    // dm 計數：主迴圈每 DM_EVERY 起一支一次性執行緒去數，不擋畫面。
    // 不用 thread::sleep(ADR 0005);節奏靠主迴圈本來就有的 event::poll。
    let (tx, rx) = mpsc::channel::<DmMap>();
    let mut dm_started: Option<Instant> = None;
    let mut dm_in_flight = false;

    let mut o = io::stdout();
    if terminal::enable_raw_mode().is_err() {
        eprintln!("no_tty: 用 --once 印一次");
        std::process::exit(2);
    }
    let _ = execute!(o, terminal::EnterAlternateScreen, cursor::Hide);
    let mut last_check = Instant::now();
    let mut dirty = true;
    loop {
        if dirty {
            let _ = draw(&app);
            dirty = false;
        }
        if event::poll(Duration::from_millis(500)).unwrap_or(false) {
            match event::read() {
                Ok(Event::Key(k)) if k.kind == event::KeyEventKind::Press => {
                    if !on_key(&mut app, k) {
                        break;
                    }
                    dirty = true;
                }
                Ok(Event::Resize(_, _)) => dirty = true,
                _ => {}
            }
        }
        if let Some(root) = args.aib_root.as_deref() {
            if !dm_in_flight && dm_started.is_none_or(|t| t.elapsed() >= DM_EVERY) {
                let (root, seats, tx) = (root.to_path_buf(), app.cabin_seats(), tx.clone());
                std::thread::spawn(move || {
                    let _ = tx.send(count_dm(&root, &seats));
                });
                dm_started = Some(Instant::now());
                dm_in_flight = true;
            }
        }
        if let Ok(m) = rx.try_recv() {
            app.dm = m;
            app.dm_at = Some(Instant::now());
            dm_in_flight = false;
            dirty = true;
        }
        if last_check.elapsed() >= Duration::from_secs(2) {
            let before = app.mtime;
            app.load(&args.data);
            if app.mtime != before {
                dirty = true;
            }
            last_check = Instant::now();
            dirty = dirty || app.dm_at.is_some();
        }
    }
    let _ = execute!(o, cursor::Show, terminal::LeaveAlternateScreen);
    let _ = terminal::disable_raw_mode();
}
