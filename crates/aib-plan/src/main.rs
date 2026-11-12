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
//! o 只看沒做完 · x 只看延伸 · n 沒人負責 · s 換席 · r 清除篩選 ·
//! [ 收起 / 展開左欄(樹) · ] 收起 / 展開右欄(為什麼) · q 離開

use aib_plan::{
    chain, fit, flatten, index, mark, pad, parse, pct, str_width, tally, Filter, Plan, PASS,
};
use crossterm::{
    cursor,
    event::{
        self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyEvent, KeyModifiers,
        MouseButton, MouseEventKind,
    },
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
/// 數一次佇列超過這麼久就當它卡住:顯示「?」,不重起
const DM_STUCK: Duration = Duration::from_secs(90);

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
            let py = std::env::var("EZ2_PY3").unwrap_or_else(|_| "python3".into());
            let o = std::process::Command::new(py)
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
    /// 上一次畫面的點擊對照(滑鼠用);frame() 寫入
    hits: std::cell::RefCell<Hits>,
}

/// 畫面上哪一行是工具列、哪幾行是樹上的哪個節點
#[derive(Default, Clone)]
struct Hits {
    toolbar_y: Option<usize>,
    toolbar: String,
    tree_top: usize,
    tree_rows: Vec<usize>,
    tree_x_end: usize,
}

/// 工具列上的字 → 對應的鍵
const TOOLBAR_KEYS: [(&str, char); 10] = [
    ("樹", '['),
    ("為什麼", ']'),
    ("沒做完", 'o'),
    ("延伸", 'x'),
    ("沒人負責", 'n'),
    ("席:", 's'),
    ("e 全開", 'e'),
    ("c 全收", 'c'),
    ("r 清除", 'r'),
    ("q 離開", 'q'),
];

/// 滑鼠點擊：工具列的字 = 按那個鍵;樹上的列 = 選取並展開 / 收合。回傳 false = 要離開。
fn on_click(app: &mut App, x: usize, y: usize) -> bool {
    let h = app.hits.borrow().clone();
    if h.toolbar_y == Some(y) {
        // 043dd incr3 L2:搜尋輸入中，點工具列不能把字打進搜尋框
        if app.typing.is_some() {
            return true;
        }
        for (tok, key) in TOOLBAR_KEYS {
            let tight = tok.replacen(' ', "", 1);
            let found = h
                .toolbar
                .find(tok)
                .map(|p| (p, str_width(tok)))
                .or_else(|| h.toolbar.find(&tight).map(|p| (p, str_width(&tight))));
            if let Some((pos, tw)) = found {
                let start = str_width(&h.toolbar[..pos]);
                let end = start + tw;
                // ●/○ 開關圖示也算在那一顆裡
                if x + 1 >= start && x < end {
                    return on_key(app, KeyEvent::new(KeyCode::Char(key), KeyModifiers::NONE));
                }
            }
        }
        return true;
    }
    if y >= h.tree_top && x < h.tree_x_end {
        if let Some(&idx) = h.tree_rows.get(y - h.tree_top) {
            app.sel = idx;
            let ix = index(&app.plan);
            if ix.kids.get(idx).is_some_and(|k| !k.is_empty()) {
                if let Some(id) = app.plan.nodes.get(idx).map(|n| n.id.clone()) {
                    if !app.open.remove(&id) {
                        app.open.insert(id);
                    }
                }
            }
        }
    }
    true
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

/// 工具列：開著的篩選是彩色實心 ●、關著的是灰色空心 ○;寬度不夠就把間距縮小，不讓後面的鍵被切掉。
fn toolbar(app: &App, w: usize) -> Vec<Seg> {
    let f = &app.filter;
    let grey = Some(Color::DarkGrey);
    let seat = if f.seat.is_empty() {
        "席:全部(s)".to_string()
    } else {
        format!("席:{}", f.seat)
    };
    let search = match &app.typing {
        Some(t) => format!(":{t}▏"),
        None if !f.q.is_empty() => format!(":{}", f.q),
        None => String::new(),
    };
    // (字, 開著嗎, 前面要不要大間距)
    let items: Vec<(String, Option<bool>, bool)> = vec![
        (
            (if app.show_tree { "◀樹" } else { "▶樹" }).to_string(),
            None,
            false,
        ),
        (
            (if app.show_side {
                "為什麼▶"
            } else {
                "◀為什麼"
            })
            .to_string(),
            None,
            false,
        ),
        ("沒做完".to_string(), Some(f.only_open), true),
        ("延伸".to_string(), Some(f.only_ext), false),
        ("沒人負責".to_string(), Some(f.orphan), false),
        (format!("{seat}{search}"), Some(!f.seat.is_empty()), false),
        ("e 全開".to_string(), None, true),
        ("c 全收".to_string(), None, false),
        ("r 清除".to_string(), None, false),
        ("q 離開".to_string(), None, false),
    ];
    let body: usize = items
        .iter()
        .map(|(t, o, _)| str_width(t) + usize::from(o.is_some() && !t.starts_with("席:")))
        .sum();
    let bigs = items.iter().filter(|i| i.2).count();
    let n = items.len() - 1;
    let (small, big) = if body + n * 3 + bigs * 3 <= w {
        (3, 6)
    } else if body + n * 2 + bigs * 2 <= w {
        (2, 4)
    } else {
        (1, 2)
    };
    // 最窄還放不下:鍵字母和字之間不留空格(「o沒做完」),滑鼠點擊兩種寫法都認
    let tight = body + n + bigs > w;
    let items: Vec<(String, Option<bool>, bool)> = items
        .into_iter()
        .map(|(t, o, wide)| {
            let t = match t.char_indices().nth(1) {
                Some((i, ' ')) if tight && t.is_char_boundary(i) => {
                    format!("{}{}", &t[..i], &t[i + 1..])
                }
                _ => t,
            };
            (t, o, wide)
        })
        .collect();
    let mut out = Vec::new();
    for (i, (t, o, wide)) in items.into_iter().enumerate() {
        if i > 0 {
            out.push(Seg(
                " ".repeat(if wide { big } else { small }),
                None,
                false,
                false,
            ));
        }
        match o {
            Some(true) if t.starts_with("席:") => out.push(Seg(t, Some(Color::Cyan), false, true)),
            Some(true) => {
                out.push(Seg("●".into(), Some(Color::Green), false, true));
                out.push(Seg(t, Some(Color::Green), false, true));
            }
            Some(false) if !t.starts_with("席:") => {
                out.push(Seg("○".into(), grey, false, false));
                out.push(Seg(t, grey, false, false));
            }
            _ => out.push(Seg(t, grey, false, false)),
        }
    }
    out
}

/// 把整個畫面算成「每行一串 Seg」;純函式，--once 與互動共用。
fn frame(app: &App, w: usize, h: usize) -> Vec<Vec<Seg>> {
    // 043dd incr3 L1:錯誤畫面 / 空資料提早 return 時不能沿用上一張畫面的點擊對照
    *app.hits.borrow_mut() = Hits::default();
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
    if ix.root.is_none() {
        out.push(vec![seg("進度資料是空的")]);
        return out;
    }
    // Andy 11-12 12:2x:頂部四行(進度 / 期望 / 最落後 / 信)都不要 ⇒ 最上面就是選單
    // Andy 11-12 12:4x:選單緊貼最上面太壓迫 ⇒ 上面空一行
    out.push(Vec::new());
    out.push(toolbar(app, w));
    let f = &app.filter;
    let mut hits = Hits {
        toolbar_y: Some(out.len() - 1),
        toolbar: out[out.len() - 1].iter().map(|g| g.0.as_str()).collect(),
        ..Hits::default()
    };
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
            hits.tree_rows.push(r.idx);
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
            let lw = tree_w.saturating_sub(str_width(&right)).max(8).min(tree_w);
            let col = if leaf {
                status_color(n.status.as_deref())
            } else {
                None
            };
            let orphan = n.kind == "task" && n.owner.is_none() && n.status.as_deref() != Some(PASS);
            tree_lines.push(vec![
                Seg(pad(&left, lw), col, r.idx == app.sel, n.kind == "group"),
                Seg(
                    fit(&right, tree_w.saturating_sub(lw)),
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
    if app.show_side && !p.nodes.is_empty() {
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

    hits.tree_top = out.len();
    hits.tree_x_end = if side_by_side { tree_w } else { w };
    if rows.is_empty() {
        hits.tree_rows.clear();
    }
    if !app.show_tree {
        hits.tree_rows.clear();
    }
    *app.hits.borrow_mut() = hits;
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
            } else if let Some(&pi) = app
                .plan
                .nodes
                .get(app.sel)
                .and_then(|n| n.parent.as_ref())
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
        hits: Default::default(),
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
    let mut dm_stuck = false;

    // 出錯也要把終端機還原(raw mode / 替代畫面),不然使用者的窗格會壞掉
    let prev = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        // 只有主執行緒 panic 才會結束畫面;計數執行緒 panic 不動終端機
        if std::thread::current().name() == Some("main") {
            let _ = execute!(
                io::stdout(),
                DisableMouseCapture,
                cursor::Show,
                terminal::LeaveAlternateScreen
            );
            let _ = terminal::disable_raw_mode();
        }
        prev(info);
    }));
    let mut o = io::stdout();
    if terminal::enable_raw_mode().is_err() {
        eprintln!("no_tty: 用 --once 印一次");
        std::process::exit(2);
    }
    let _ = execute!(
        o,
        terminal::EnterAlternateScreen,
        cursor::Hide,
        EnableMouseCapture
    );
    let mut last_check = Instant::now();
    let mut last_age_draw = Instant::now();
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
                Ok(Event::Mouse(m)) => {
                    let (x, y) = (m.column as usize, m.row as usize);
                    let go = match m.kind {
                        MouseEventKind::Down(MouseButton::Left) => on_click(&mut app, x, y),
                        MouseEventKind::ScrollUp => {
                            on_key(&mut app, KeyEvent::new(KeyCode::Up, KeyModifiers::NONE))
                        }
                        MouseEventKind::ScrollDown => {
                            on_key(&mut app, KeyEvent::new(KeyCode::Down, KeyModifiers::NONE))
                        }
                        _ => true,
                    };
                    if !go {
                        break;
                    }
                    dirty = true;
                }
                Ok(Event::Resize(_, _)) => dirty = true,
                _ => {}
            }
        }
        if let Some(root) = args.aib_root.as_deref() {
            // 數佇列卡住(python 掛住)超過 DM_STUCK:不重起(免得每 90 秒多一支 python),
            // 改顯示「?」;那支回來了才再數。畫面照舊可用。
            if dm_in_flight && !dm_stuck && dm_started.is_some_and(|t| t.elapsed() >= DM_STUCK) {
                dm_stuck = true;
                for v in app.dm.values_mut() {
                    *v = (None, None);
                }
                dirty = true;
            }
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
            dm_stuck = false;
            dirty = true;
        }
        if last_check.elapsed() >= Duration::from_secs(2) {
            let before = app.mtime;
            app.load(&args.data);
            if app.mtime != before {
                dirty = true;
            }
            last_check = Instant::now();
        }
        // 「幾秒前數的」只要每 30 秒重畫一次，不用每 2 秒整頁重畫(省熱)
        if app.dm_at.is_some() && last_age_draw.elapsed() >= DM_EVERY {
            dirty = true;
        }
        if dirty {
            last_age_draw = Instant::now();
        }
    }
    let _ = execute!(
        o,
        DisableMouseCapture,
        cursor::Show,
        terminal::LeaveAlternateScreen
    );
    let _ = terminal::disable_raw_mode();
}

#[cfg(test)]
mod tests {
    use super::*;

    fn empty_app() -> App {
        App {
            plan: Plan::default(),
            err: Some("read_failed: test".into()),
            mtime: None,
            open: HashSet::new(),
            sel: 0,
            filter: Filter::default(),
            show_tree: true,
            show_side: true,
            typing: None,
            dm: DmMap::new(),
            dm_at: None,
            hits: Default::default(),
        }
    }

    /// 043dd M1:讀不到資料檔(nodes 空)時，每個鍵、每種寬度都不能 panic
    #[test]
    fn empty_plan_survives_every_key_and_width() {
        let keys = [
            KeyCode::Up,
            KeyCode::Down,
            KeyCode::Left,
            KeyCode::Right,
            KeyCode::Enter,
            KeyCode::Char(' '),
            KeyCode::Char('j'),
            KeyCode::Char('k'),
            KeyCode::Char('e'),
            KeyCode::Char('c'),
            KeyCode::Char('o'),
            KeyCode::Char('x'),
            KeyCode::Char('n'),
            KeyCode::Char('s'),
            KeyCode::Char('r'),
            KeyCode::Char('['),
            KeyCode::Char(']'),
        ];
        let mut app = empty_app();
        for k in keys {
            assert!(on_key(&mut app, KeyEvent::new(k, KeyModifiers::NONE)));
            for w in [0, 1, 5, 7, 8, 24, 80, 160] {
                let _ = frame(&app, w, 30);
            }
        }
    }

    /// 043dd L2:極窄寬度(--once --width 5)不下溢
    #[test]
    fn narrow_width_does_not_underflow() {
        let p = parse(
            r#"{"nodes":[{"id":"V","parent":null,"kind":"vision","title":"總大綱很長很長很長"},
            {"id":"T1","parent":"V","kind":"task","title":"任務","status":"未驗","owner":"a63ab"}]}"#,
        )
        .expect("sample parses");
        let mut app = empty_app();
        app.plan = p;
        app.err = None;
        app.open.insert("V".into());
        for w in 0..12 {
            let _ = frame(&app, w, 20);
        }
    }

    /// 點樹上的列 = 選取 + 展開 / 收合;點工具列「e 全開」= 全部展開;點空白不出錯
    #[test]
    fn mouse_click_toggles_rows_and_toolbar() {
        let p = parse(
            r#"{"nodes":[{"id":"V","parent":null,"kind":"vision","title":"總大綱"},
            {"id":"G","parent":"V","kind":"group","title":"群組"},
            {"id":"T1","parent":"G","kind":"task","title":"任務","status":"未驗","owner":"a63ab"}]}"#,
        )
        .expect("sample parses");
        let mut app = empty_app();
        app.plan = p;
        app.err = None;
        app.open.insert("V".into());
        let _ = frame(&app, 100, 30);
        let h = app.hits.borrow().clone();
        let gy = h.tree_top
            + h.tree_rows
                .iter()
                .position(|&i| app.plan.nodes[i].id == "G")
                .expect("G shown");
        assert!(on_click(&mut app, 2, gy));
        assert!(app.open.contains("G"), "click opens G");
        assert_eq!(app.plan.nodes[app.sel].id, "G");
        let _ = frame(&app, 100, 30);
        let h = app.hits.borrow().clone();
        let gy = h.tree_top
            + h.tree_rows
                .iter()
                .position(|&i| app.plan.nodes[i].id == "G")
                .expect("G shown");
        assert!(on_click(&mut app, 2, gy));
        assert!(!app.open.contains("G"), "second click closes G");
        let ty = h.toolbar_y.expect("toolbar");
        let x = str_width(
            &h.toolbar[..h
                .toolbar
                .find("e 全開")
                .unwrap_or_else(|| panic!("e in {:?}", h.toolbar))],
        );
        assert!(on_click(&mut app, x + 1, ty));
        assert!(app.open.contains("G"), "toolbar e opens all");
        let qx = str_width(&h.toolbar[..h.toolbar.find("q 離開").expect("q")]);
        assert!(!on_click(&mut app, qx, ty), "toolbar q quits");
        assert!(on_click(&mut app, 79, 29));
        assert!(on_click(&mut app, 0, 0));
        // 043dd incr3 L2:搜尋輸入中點工具列不打字、不離開
        app.typing = Some(String::new());
        assert!(on_click(&mut app, qx, ty), "q while typing does not quit");
        assert_eq!(app.typing.as_deref(), Some(""), "nothing typed into search");
        app.typing = None;
        // 043dd incr3 L1:錯誤畫面不沿用舊的點擊對照
        app.err = Some("x".into());
        let _ = frame(&app, 100, 30);
        assert!(app.hits.borrow().toolbar_y.is_none());
        assert!(
            on_click(&mut app, qx, ty),
            "old q spot does not quit on the error screen"
        );
    }
    #[test]
    fn toolbar_colours_an_active_filter_and_fits_a_narrow_pane() {
        let mut app = empty_app();
        app.filter.only_open = true;
        let bar = toolbar(&app, 120);
        let dot = bar
            .iter()
            .position(|g| g.0 == "●")
            .expect("active filter has a filled dot");
        assert_eq!(bar[dot].1, Some(Color::Green));
        assert_eq!(bar[dot + 1].0, "沒做完");
        assert!(
            bar.iter().any(|g| g.0 == "○"),
            "inactive filters keep the hollow dot"
        );
        for w in [72, 76, 80, 90, 101] {
            let text: String = toolbar(&app, w).iter().map(|g| g.0.as_str()).collect();
            assert!(
                str_width(&text) <= w,
                "toolbar fits {w}: {}",
                str_width(&text)
            );
            assert!(text.ends_with("離開"), "q stays on screen at {w}");
        }
        // 緊湊寫法也點得到;點圓點後的字 = 按 o
        app.plan =
            parse(r#"{"nodes":[{"id":"V","parent":null,"kind":"vision","title":"總大綱"}]}"#)
                .expect("sample parses");
        app.err = None;
        let _ = frame(&app, 72, 30);
        let h = app.hits.borrow().clone();
        let ty = h.toolbar_y.expect("toolbar");
        let ox = str_width(&h.toolbar[..h.toolbar.find("沒做完").expect("o")]);
        assert!(on_click(&mut app, ox, ty));
        assert!(!app.filter.only_open, "click on tight o toggles it off");
        let qx = str_width(&h.toolbar[..h.toolbar.find("q離開").expect("tight q")]);
        assert!(!on_click(&mut app, qx, ty), "tight q quits");
    }
}
