//! `aib-plan`:艙進度樹，放在 tmux 中間窗格(左對話 / 中進度樹 / 右 AI 欄)。
//!
//!   aib-plan [--data <plan.json>] [--aib-root <aib 倉>] [--seat <席碼>] [--only-seat <席碼[,席碼]>] [--once [--width N]]
//!
//! - 預設讀 ~/.ez2-aib/pending/aib2-a63ab-1107/cabin-plan/data.json(網頁版、Claude mod 同一份);
//!   檔案一變就重讀。
//! - 有 --aib-root 時每 30 秒唯讀數一次各席 dm 佇列(待審 / 已讀未結);讀不到顯示「?」。
//! - 有 --seat(沒帶就問 tmux 窗選項 @aib_seat)時最上面兩行：本席一開始主軸 / 目前主軸(開始時間)/ 現在時間(台北),見 mainline.rs。
//! - --only-seat(Andy 11-13 非艦長畫面):樹固定只留這幾席的節點(與上層);s(鍵盤或點工具列)不輪替、
//!   r 清除也留著;全艙那一列不畫;建議 / 影子續跑只算這幾席。逗號分隔或重複帶(分身繼承:519d0,a63ab)。
//!   沒帶 --seat 時,最上面兩行主軸用清單第一席。艦長畫面不帶這個旗標。
//! - 席位視角(`aib seatview on`,窗選項 @aib_layout=seat):沒帶 --only-seat 時自動固定在 --seat 那一席。
//! - --once:不進互動，印一次就結束(給 Codex 席、測試、沒有 TTY 的地方)。
//!
//! 按鍵:↑↓ 移動 · → 展開 · ← 收合 / 回上層 · Enter 切換 · e 全部展開 · c 全部收合 ·
//! o 只看沒做完 · x 只看延伸 · n 沒人負責 · s 換席 · r 清除篩選 · m 開合最上面的判斷 · f 開合全艙 ·
//! [ 收起 / 展開左欄(樹) · ] 收起 / 展開右欄(為什麼) · q 離開

use aib_plan::{
    chain, fit, flatten, index, mark, pad, parse, pct, str_width, tally, Filter, Node, Plan, PASS,
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

use aib_plan::autofix::{self, Fixes};
use aib_plan::mainline::{self, Mainline};
use aib_plan::seatview;
use aib_plan::shadow::{self, Savings};

fn epoch_now() -> i64 {
    SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

/// 本艙每一席(進度資料的席位表)的主軸;只讀
fn fleet_of(app: &App, now: i64) -> Vec<(String, Mainline)> {
    if !app.pin.is_empty() {
        return Vec::new(); // 非艦長畫面:全艙那一列不畫
    }
    let root = state_dir();
    let mut seats = app.cabin_seats();
    seats.sort();
    seats
        .into_iter()
        .map(|c| {
            let m = mainline::peek(&root, &c, now);
            (c, m)
        })
        .collect()
}

/// 這個窗格屬於哪一席:tmux show -wv -t $TMUX_PANE @aib_seat;不在 tmux / 讀不到 ⇒ None
fn tmux_seat() -> Option<String> {
    let pane = std::env::var("TMUX_PANE").ok()?;
    let tmux = std::env::var("AIB_TMUX_BIN").unwrap_or_else(|_| "tmux".into());
    let o = std::process::Command::new(tmux)
        .args(["show", "-wv", "-t", &pane, "@aib_seat"])
        .stdin(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .output()
        .ok()?;
    if !o.status.success() {
        return None;
    }
    mainline::seat_from_tmux(&String::from_utf8_lossy(&o.stdout))
}

/// 要固定的席:--only-seat 有帶就照帶的;沒帶而且這一格所在的窗是席位視角(窗選項 @aib_layout=seat,
/// `aib seatview on`)⇒ 固定在 --seat 那一席。窗版面只在啟動時問一次(切換時 aib-seatview 會重開這格)。
fn auto_pin(
    only: &[String],
    seat: Option<&str>,
    layout: impl FnOnce() -> Option<seatview::Layout>,
) -> Vec<String> {
    if !only.is_empty() {
        return only.to_vec();
    }
    match seat {
        Some(s) if layout() == Some(seatview::Layout::Seat) => vec![s.to_string()],
        _ => Vec::new(),
    }
}

/// <狀態根>/state:跟 aib 一樣先看 EZ2_AIB_STATE_ROOT
fn state_dir() -> PathBuf {
    std::env::var_os("EZ2_AIB_STATE_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| home().join(".ez2-aib"))
        .join("state")
}

/// 每 2 分鐘數一次:每席兩支 python,7 席 30 秒一次約佔一核 7%(043dd #726 L2,跟省熱目標相反)
const DM_EVERY: Duration = Duration::from_secs(120);
/// 數一次佇列超過這麼久就當它卡住:顯示「?」,不重起
const DM_STUCK: Duration = Duration::from_secs(90);

struct Args {
    data: PathBuf,
    aib_root: Option<PathBuf>,
    seat: Option<String>,
    only_seat: Vec<String>,
    once: bool,
    width: usize,
}

fn home() -> PathBuf {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_default()
}

/// --only-seat 的值:逗號分隔、去空白;空的或有空項 ⇒ 錯(不靜默變成「全部」)
fn seat_list(v: &str) -> Result<Vec<String>, String> {
    let out: Vec<String> = v.split(',').map(|s| s.trim().to_string()).collect();
    if out.iter().any(|s| s.is_empty()) {
        return Err("bad_value: --only-seat".into());
    }
    Ok(out)
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
        seat: None,
        only_seat: Vec::new(),
        once: false,
        width: 120,
    };
    let mut it = std::env::args().skip(1);
    while let Some(x) = it.next() {
        match x.as_str() {
            "--data" => a.data = it.next().ok_or("missing_value: --data")?.into(),
            "--aib-root" => a.aib_root = Some(it.next().ok_or("missing_value: --aib-root")?.into()),
            "--seat" => a.seat = Some(it.next().ok_or("missing_value: --seat")?),
            "--only-seat" => {
                let v = it.next().ok_or("missing_value: --only-seat")?;
                a.only_seat.extend(seat_list(&v)?);
            }
            "--once" => a.once = true,
            "--width" => {
                a.width = it
                    .next()
                    .and_then(|v| v.parse().ok())
                    .ok_or("bad_value: --width")?
            }
            "-h" | "--help" => return Err(
                "usage: aib-plan [--data FILE] [--aib-root DIR] [--seat CODE] [--only-seat CODE[,CODE]] [--once [--width N]]"
                    .into(),
            ),
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
    /// 最上面兩行(有 --seat 才有);now_secs = 畫的那一刻(台北時間由它算)
    mainline: Option<Mainline>,
    now_secs: i64,
    /// 判斷內容展開了沒(預設收合)
    main_open: bool,
    /// 全艙判斷(本艙每一席的一開始 / 目前主軸 + 判斷);展開了沒(預設收合)
    fleet: Vec<(String, Mainline)>,
    fleet_open: bool,
    /// 點「起點 / 目前」後的一句結果(跳到哪一項 / 樹上沒有),下一次按鍵或點擊前都留著
    jump_note: Option<String>,
    /// 影子續跑省下的時間(右上角 + 樹上「影子續跑」分支;None = 讀不到紀錄檔);檔案沒變就不重讀
    shadow: Option<Savings>,
    shadow_mtime: Option<SystemTime>,
    /// 自動審修紀錄(樹上「自動審修」分支，接在影子續跑下方);檔案沒變就不重讀
    fixes: Option<Fixes>,
    fixes_mtime: Option<SystemTime>,
    /// --only-seat:樹固定只留這幾席(非艦長畫面);空 = 艦長畫面,s 照常輪替
    pin: Vec<String>,
}

/// 畫面上哪一行是工具列、哪幾行是樹上的哪個節點
#[derive(Default, Clone)]
struct Hits {
    toolbar_y: Option<usize>,
    /// 目前主軸那一行(點了開合判斷內容)
    mainline_y: Option<usize>,
    /// 一開始主軸那一行(行尾「全艙判斷」,點了開合全艙)
    fleet_y: Option<usize>,
    /// 右上角「建議 N」在第幾行、從第幾格到第幾格(點了跳到樹上的建議分支)
    sugg: Option<(usize, usize, usize)>,
    /// 「起點」那一行(點了跳到樹上對應的項目)
    start_y: Option<usize>,
    /// 目前那一行「▸ 理由」從第幾格起(點那裡才開合;前面點了跳到樹上)
    toggle_x: Option<usize>,
    /// 右上角「影子續跑 X」在第幾行、從第幾格到第幾格(點了跳到樹上的影子續跑分支)
    shadow: Option<(usize, usize, usize)>,
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
    app.jump_note = None;
    // Andy 11-12 14:5x:點「起點 / 目前」帶到下方樹上對應的項目;點的當下才比對，主軸換了就跳到新的
    if h.start_y == Some(y) {
        let item = app
            .mainline
            .as_ref()
            .and_then(|m| m.first.as_ref())
            .map(|e| e.item.clone());
        return jump_to(app, item.as_deref());
    }
    if h.mainline_y == Some(y) {
        if h.toggle_x.is_some_and(|tx| x >= tx) {
            app.main_open = !app.main_open;
            return true;
        }
        let item = app
            .mainline
            .as_ref()
            .and_then(|m| m.now.as_ref())
            .map(|e| e.item.clone());
        return jump_to(app, item.as_deref());
    }
    if let Some((sy, x0, x1)) = h.sugg {
        if sy == y && x >= x0 && x < x1 {
            return jump_to_suggestions(app);
        }
    }
    if let Some((sy, x0, x1)) = h.shadow {
        if sy == y && x >= x0 && x < x1 {
            return jump_to_shadow(app);
        }
    }
    if h.fleet_y == Some(y) {
        app.fleet_open = !app.fleet_open;
        return true;
    }
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

/// 主軸那句話最像樹上哪一項:相鄰兩字重疊(以較短的一邊為分母)+ 同一個 #PR 號加分;太不像就不跳
fn best_node(plan: &Plan, item: &str) -> Option<(usize, f64)> {
    fn grams(s: &str) -> HashSet<(char, char)> {
        let cs: Vec<char> = s
            .chars()
            .filter(|c| c.is_alphanumeric())
            .flat_map(|c| c.to_lowercase())
            .collect();
        cs.windows(2).map(|w| (w[0], w[1])).collect()
    }
    fn prs(s: &str) -> HashSet<String> {
        s.split(|c: char| !(c == '#' || c.is_ascii_digit()))
            .filter(|w| w.len() > 1 && w.starts_with('#'))
            .map(str::to_string)
            .collect()
    }
    let (g, pr) = (grams(item), prs(item));
    if g.is_empty() {
        return None;
    }
    let mut best: Option<(f64, usize)> = None;
    for (i, n) in plan.nodes.iter().enumerate() {
        let text = format!("{} {}", n.title, n.why.as_deref().unwrap_or(""));
        let h = grams(&n.title);
        if h.is_empty() {
            continue;
        }
        let mut score = g.intersection(&h).count() as f64 / g.len().min(h.len()) as f64;
        if !pr.is_empty() && !pr.is_disjoint(&prs(&text)) {
            score += 0.5;
        }
        if best.is_none_or(|(b, _)| score > b) {
            best = Some((score, i));
        }
    }
    best.filter(|(s, _)| *s >= 0.25)
        .map(|(s, i)| (i, s.min(1.0)))
}

/// 跳到那一項：展開祖先、選它;被篩選藏起來就清掉篩選。找不到對應的就不動
fn jump_to(app: &mut App, item: Option<&str>) -> bool {
    // Andy 11-12 20:4x「點下去也沒有跳到對應的項目」:跳了要說跳到哪、多像;樹上沒有就直說
    let Some((idx, score)) = item.and_then(|t| best_node(&app.plan, t)) else {
        app.jump_note = item.map(|t| {
            format!(
                "樹上沒有對應「{}」的項目(進度樹資料 {})",
                fit(t, 30),
                if app.plan.asof.is_empty() {
                    "沒寫時間"
                } else {
                    app.plan.asof.as_str()
                }
            )
        });
        return true;
    };
    app.jump_note = Some(format!(
        "跳到:{}(相似 {:.0}%{})",
        fit(&app.plan.nodes[idx].title, 40),
        score * 100.0,
        if score < 0.5 {
            ",不太像，樹可能還沒更新"
        } else {
            ""
        }
    ));
    select(app, idx);
    true
}

/// 建議分支:kind = "suggestion" 的節點與它們的上層。回 (上層在 nodes 的位置, 還沒完成的件數)
/// (建議分支在 nodes 的位置, 完成件數, 未完成件數);完成 = 實查通過
/// 畫面上顯示的標題：建議分支的數字一律由 [`suggestions`] 算(資料裡寫的數字不算),
/// 樹、右欄都用這一個,跟右上角「建議 完成/未完成」同一組數字(Andy 11-12 23:1x)
fn shown_title(p: &Plan, counts: Option<(usize, usize, usize)>, idx: usize) -> String {
    let n = &p.nodes[idx];
    match counts {
        Some((at, done, open)) if at == idx => format!(
            "{}(完成 {done} / 未完成 {open})",
            n.title
                .split(['(', '('])
                .next()
                .unwrap_or(&n.title)
                .trim_end()
        ),
        _ => n.title.clone(),
    }
}

/// pin 不空(非艦長畫面)⇒ 只算這幾席負責或協作的建議,數字才對得上畫面上的樹
fn suggestions(plan: &Plan, pin: &[String]) -> Option<(usize, usize, usize)> {
    let mine = |n: &Node| {
        pin.is_empty()
            || pin
                .iter()
                .any(|p| n.owner.as_deref() == Some(p.as_str()) || n.with.iter().any(|w| w == p))
    };
    let items: Vec<&Node> = plan
        .nodes
        .iter()
        .filter(|n| n.kind == "suggestion" && mine(n))
        .collect();
    let parent = items.first()?.parent.as_deref()?;
    let at = plan.nodes.iter().position(|n| n.id == parent)?;
    let open = items
        .iter()
        .filter(|n| n.status.as_deref() != Some("實查通過"))
        .count();
    Some((at, items.len() - open, open))
}

/// 點右上角「建議 N」:跳到建議分支並展開，看得到每一項
fn jump_to_suggestions(app: &mut App) -> bool {
    let Some((idx, _, open)) = suggestions(&app.plan, &app.pin) else {
        return true;
    };
    app.jump_note = Some(format!("跳到:建議(還沒完成 {open} 項)"));
    let id = app.plan.nodes[idx].id.clone();
    app.open.insert(id);
    select(app, idx);
    true
}

/// 樹上「影子續跑」分支的節點 id(每次省成功一項:shadow-1 = 最新)
const SHADOW_ID: &str = "shadow";

/// 影子續跑紀錄檔:跟 `aib test` 同一個位置(EZ2_VERIFY_SHADOW_DIR,沒設 = ~/.ez2-aib/verify-shadow)
fn shadow_path() -> PathBuf {
    std::env::var_os("EZ2_VERIFY_SHADOW_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| home().join(".ez2-aib").join("verify-shadow"))
        .join("test-stats.jsonl")
}

/// 項目名稱快取(沿用池反查結果)
fn shadow_cache() -> PathBuf {
    state_dir().join("aib-plan").join("shadow-names.json")
}

/// 自動審修紀錄檔(`aib-fixlog add` 寫;EZ2_AUTOFIX_LOG 可改位置)
fn autofix_path() -> PathBuf {
    std::env::var_os("EZ2_AUTOFIX_LOG")
        .map(PathBuf::from)
        .unwrap_or_else(|| state_dir().join("aib-plan").join("autofix.jsonl"))
}

/// 樹上「自動審修」分支的節點 id(autofix-1 = 最新;前綴夠長，不會撞到進度資料的 id)
const FIX_ID: &str = "autofix";

/// 右上角的字:「影子續跑」(Andy 11-13 06:1x 只留字，省多少時間看樹上的分支);沒有省成功的紀錄 ⇒ 不顯示
fn shadow_badge(s: Option<&Savings>) -> Option<String> {
    s.filter(|s| !s.saves.is_empty())?;
    Some("   影子續跑".to_string())
}

impl App {
    /// 清除篩選後的樣子:非艦長畫面仍固定在本席
    fn base_filter(&self) -> Filter {
        Filter {
            pin: self.pin.join(","),
            ..Filter::default()
        }
    }

    /// 紀錄檔沒變(修改時間一樣)就不重讀;回傳有沒有換資料
    fn load_shadow(&mut self, path: &Path, cache: &Path) -> bool {
        let m = std::fs::metadata(path).and_then(|m| m.modified()).ok();
        if m.is_some() && m == self.shadow_mtime {
            return false;
        }
        let mut s = shadow::read(path);
        // 項目名稱：從同目錄沿用池反查(很大)⇒ 快取，key = 這份紀錄檔的修改時間 + 大小
        if let (Some(s), Some(dir)) = (s.as_mut(), path.parent()) {
            let meta = std::fs::metadata(path).ok();
            let key = format!(
                "{}:{}",
                meta.as_ref()
                    .and_then(|m| m.modified().ok())
                    .and_then(|t| t.duration_since(SystemTime::UNIX_EPOCH).ok())
                    .map_or(0, |d| d.as_secs()),
                meta.map_or(0, |m| m.len())
            );
            shadow::name_runs_cached(dir, cache, &key, s);
        }
        // 非艦長畫面:影子續跑只算這幾席省的(右上角、樹上分支、跳轉的次數都從這份算);
        // 先篩再比,才是跟上次篩過的比(自審 N1)
        if let Some(s) = s.as_mut() {
            pin_saves(s, &self.pin);
        }
        let changed = s != self.shadow;
        self.shadow = s;
        self.shadow_mtime = m;
        changed
    }

    /// 自動審修紀錄沒變(修改時間一樣)就不重讀;回傳有沒有換資料
    fn load_fixes(&mut self, path: &Path) -> bool {
        let m = std::fs::metadata(path).and_then(|m| m.modified()).ok();
        if m.is_some() && m == self.fixes_mtime {
            return false;
        }
        let f = autofix::read(path);
        let changed = f != self.fixes;
        self.fixes = f;
        self.fixes_mtime = m;
        changed
    }

    /// Andy 11-12 23:5x / 12:0x:影子續跑跟建議一樣，右上角一個數字 + 樹上一個分支，每次省成功一項。
    /// 進度資料重讀或紀錄檔換了都要重接(先拔掉舊的再接;接在最上層底下最後面，沒有狀態 ⇒ 不算進完成度)
    fn graft_shadow(&mut self) {
        let keep = self.plan.nodes.get(self.sel).map(|n| n.id.clone());
        let ours = |id: &str| {
            id == SHADOW_ID
                || id.starts_with("shadow-")
                || id == FIX_ID
                || id.starts_with("autofix-")
        };
        self.plan.nodes.retain(|n| !ours(&n.id));
        let root = self
            .plan
            .nodes
            .iter()
            .find(|n| n.parent.is_none())
            .map(|n| n.id.clone());
        if let (Some(root), Some(s)) = (root.clone(), self.shadow.as_ref()) {
            if !s.saves.is_empty() {
                self.plan.nodes.push(Node {
                    id: SHADOW_ID.into(),
                    parent: Some(root),
                    kind: "group".into(),
                    title: format!(
                        "影子續跑(共省 {},{} 次)",
                        shadow::dur(s.total_s),
                        s.saves.len()
                    ),
                    why: Some(
                        "aib test 沿用沒改到的測試、不重跑;每次有省到時間就記一項(紀錄檔 verify-shadow/test-stats.jsonl)"
                            .into(),
                    ),
                    ..Node::default()
                });
                for (i, e) in s.saves.iter().enumerate() {
                    self.plan.nodes.push(Node {
                        id: format!("shadow-{}", i + 1),
                        parent: Some(SHADOW_ID.into()),
                        kind: "shadow".into(),
                        // Andy 11-13 00:0x:每項要帶出是哪件工作(查不到就照實說)
                        title: format!(
                            "{} 省 {} · {}",
                            mainline::stamp(e.at, self.now_secs),
                            shadow::dur(e.saved_s),
                            e.name.as_deref().unwrap_or("沒記到是哪件")
                        ),
                        why: Some(format!("實跑 {} 支、沿用 {} 支", e.ran, e.reused)),
                        owner: e.seat().map(str::to_string),
                        ..Node::default()
                    });
                }
            }
        }
        // Andy 11-13 01:3x:每修一個 bug 就在影子續跑下方顯示;輕度先跳過的也列出來(註記還有哪些問題)
        if let (Some(root), Some(f)) = (root, self.fixes.as_ref()) {
            // 非艦長畫面:分支標題的件數只算這幾席的(自審 L3)
            let items: Vec<_> = f
                .items
                .iter()
                .filter(|e| self.pin.is_empty() || self.pin.contains(&e.seat))
                .collect();
            if !items.is_empty() {
                let fixed = items.iter().filter(|e| e.fixed).count();
                self.plan.nodes.push(Node {
                    id: FIX_ID.into(),
                    parent: Some(root),
                    kind: "group".into(),
                    title: format!("自動審修(修 {fixed},註記 {})", items.len() - fixed),
                    why: Some(
                        "每做完一個功能審一輪：嚴重 / 中度修完才算完成，輕度可以先記下(紀錄檔 state/aib-plan/autofix.jsonl)"
                            .into(),
                    ),
                    ..Node::default()
                });
                for (i, e) in items.iter().enumerate() {
                    self.plan.nodes.push(Node {
                        id: format!("autofix-{}", i + 1),
                        parent: Some(FIX_ID.into()),
                        kind: "fix".into(),
                        title: format!(
                            "{} {} {} · {} · {}",
                            mainline::stamp(e.at, self.now_secs),
                            if e.fixed { "修" } else { "註記" },
                            e.sev.label(),
                            e.what,
                            e.work
                        ),
                        why: Some(match &e.commit {
                            Some(c) if e.fixed => format!("修正 commit {c}"),
                            _ if e.fixed => "已修".into(),
                            _ => "輕度，先記下沒修".into(),
                        }),
                        owner: Some(e.seat.clone()),
                        ..Node::default()
                    });
                }
            }
        }
        let ix = index(&self.plan);
        if let Some(i) = keep.and_then(|id| ix.by.get(&id).copied()) {
            self.sel = i;
        } else if self.sel >= self.plan.nodes.len() {
            self.sel = 0;
        }
    }
}

/// 非艦長畫面(pin 不空):影子續跑只留這幾席省的,總時間跟著重算;艦長畫面原樣
fn pin_saves(s: &mut Savings, pin: &[String]) {
    if pin.is_empty() {
        return;
    }
    s.saves
        .retain(|e| e.seat().is_some_and(|x| pin.iter().any(|p| p == x)));
    s.total_s = s.saves.iter().map(|e| e.saved_s).sum();
}

/// 點右上角「影子續跑」:跳到樹上的影子續跑分支並展開
fn jump_to_shadow(app: &mut App) -> bool {
    let Some(idx) = app.plan.nodes.iter().position(|n| n.id == SHADOW_ID) else {
        return true;
    };
    let n = app.shadow.as_ref().map_or(0, |s| s.saves.len());
    app.jump_note = Some(format!("跳到:影子續跑(省成功 {n} 次)"));
    app.open.insert(SHADOW_ID.into());
    select(app, idx);
    true
}

/// 選到那一項：展開祖先;被篩選藏起來就清掉篩選
fn select(app: &mut App, idx: usize) {
    let ix = index(&app.plan);
    let mut cur = app.plan.nodes[idx].parent.clone();
    while let Some(pid) = cur {
        app.open.insert(pid.clone());
        cur = ix
            .by
            .get(&pid)
            .and_then(|&i| app.plan.nodes[i].parent.clone());
    }
    app.show_tree = true;
    let prev = app.sel;
    app.sel = idx;
    let shown = |app: &App| {
        flatten(&app.plan, &ix, &app.open, &app.filter)
            .iter()
            .any(|r| r.idx == idx)
    };
    if !shown(app) {
        app.filter = app.base_filter();
        // 非艦長畫面:別席的節點清掉篩選也看不到 ⇒ 不移游標(不停在看不見的列)
        if !shown(app) {
            app.sel = prev;
            app.jump_note = Some("不是本席的項目(非艦長畫面只列本席)".into());
        }
    }
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
    let seat = if !app.pin.is_empty() {
        format!("席:{}(固定)", app.pin.join(","))
    } else if f.seat.is_empty() {
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
        (
            format!("{seat}{search}"),
            Some(!f.seat.is_empty() || !app.pin.is_empty()),
            false,
        ),
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
/// 判斷(Andy 11-12 13:4x:用百分比)= 跟主軸的符合度;≥70 綠、40~69 黃、<40 紅、沒回報灰
fn verdict(e: Option<&mainline::Entry>) -> (String, Color) {
    match e.and_then(|e| e.review.as_ref()) {
        Some(r) => (
            format!("判斷 {}%", r.pct),
            if r.on_track() {
                Color::Green
            } else if r.pct >= 40 {
                Color::Yellow
            } else {
                Color::Red
            },
        ),
        None => ("判斷 —".to_string(), Color::DarkGrey),
    }
}

/// 小卡的指標一格(標籤灰、數字亮)
fn metric(label: &str, n: u32, warn: bool) -> [Seg; 2] {
    [
        Seg(format!("{label} "), Some(Color::DarkGrey), false, false),
        Seg(
            format!("{n}"),
            Some(if warn && n > 0 {
                Color::Yellow
            } else {
                Color::White
            }),
            false,
            true,
        ),
    ]
}

/// 今天只寫 時:分,不是今天才帶 月-日(卡片第一行右邊已有完整日期)
fn short_stamp(t: i64, now: i64) -> String {
    let full = mainline::stamp(t, now);
    if t >= mainline::day_start(now) {
        full.rsplit(' ').next().unwrap_or(&full).to_string()
    } else {
        full
    }
}

/// 全艙(Andy 11-12 14:2x 小卡化):每席一行，欄位對齊 —— @席  判斷  校正 總結 交接  時:分 目前主軸;
/// 自稱主軸內但字面換題 ⇒ ⚠ 換題?
fn fleet_rows(fleet: &[(String, Mainline)], now: i64, w: usize) -> Vec<Vec<Seg>> {
    fleet
        .iter()
        .map(|(code, m)| {
            let (v, c) = verdict(m.now.as_ref());
            let pct = v.trim_start_matches("判斷 ").to_string();
            let flag = mainline::drift_hint(m);
            let head = format!("{MARGIN}@{code}  ");
            let nums = format!(
                "校正{} 總結{} 交接{}  ",
                m.corrections, m.summaries, m.handoffs
            );
            let (t, item) = match m.now.as_ref() {
                Some(e) => (short_stamp(e.started_at, now), e.item.clone()),
                None => ("--".to_string(), "還沒回報".to_string()),
            };
            let t = format!("{t:<5} ");
            let tail = if flag { "  ⚠ 換題?" } else { "" };
            let used = str_width(&head) + 6 + str_width(&nums) + str_width(&t) + str_width(tail);
            let mut row = vec![
                Seg(head, Some(Color::DarkGrey), false, false),
                Seg(
                    format!(
                        "{pct}{}",
                        " ".repeat(6usize.saturating_sub(str_width(&pct)))
                    ),
                    Some(c),
                    false,
                    true,
                ),
                Seg(nums, Some(Color::DarkGrey), false, false),
                Seg(t, Some(Color::DarkGrey), false, false),
                Seg(
                    fit(&item, w.saturating_sub(used).max(8)),
                    Some(Color::White),
                    false,
                    false,
                ),
            ];
            if flag {
                row.push(Seg(tail.into(), Some(Color::Yellow), false, true));
            }
            row
        })
        .collect()
}

/// 照顯示寬度折行(中日韓字算兩格)
fn wrap(s: &str, w: usize) -> Vec<String> {
    let w = w.max(4);
    let mut out = vec![String::new()];
    let mut used = 0;
    for ch in s.chars() {
        let cw = str_width(ch.encode_utf8(&mut [0; 4]));
        if used + cw > w {
            out.push(String::new());
            used = 0;
        }
        if let Some(l) = out.last_mut() {
            l.push(ch);
        }
        used += cw;
    }
    out
}

/// 卡片左邊留白(跟下面樹的縮排一致，不貼左線)
const MARGIN: &str = "  ";
/// 最初目標 / 目前目標 的時間欄寬(「11-12 00:09」最寬 11 格)
const TIME_W: usize = 11;
/// 兩行的標籤(Andy 11-12 23:3x 由「起點 / 目前」改名;兩個一樣寬，字頭才對齊)
const FIRST_LABEL: &str = "最初目標";
const NOW_LABEL: &str = "目前目標";

/// 本席小卡(Andy 11-12 14:2x「重新設計，留下簡易資訊卡」):
///   判斷 95%   校正 0 · 總結 1 · 交接 1                 11-12 14:25   全艙 ▸ ⚠1
///   最初目標  11-12 00:09  席位接續 v2 決定已寫,等 Andy 看
///   目前目標  11-12 14:07  挑機選 1:裝 Rust 挑機指令…                      ▸ 理由
/// 第一行右邊的「現在」與「全艙」由 frame 補上並靠右;理由預設收合
fn mainline_rows(m: &Mainline, now: i64, w: usize, open: bool) -> Vec<Vec<Seg>> {
    let (vtext, vcolor) = verdict(m.now.as_ref());
    let dot = || Seg(" · ".into(), Some(Color::DarkGrey), false, false);
    let mut head = vec![
        seg(MARGIN),
        Seg(vtext, Some(vcolor), false, true),
        seg("   "),
    ];
    head.extend(metric("校正", m.corrections, true));
    head.push(dot());
    head.extend(metric("總結", m.summaries, false));
    head.push(dot());
    head.extend(metric("交接", m.handoffs, false));
    let review = m.now.as_ref().and_then(|e| e.review.as_ref());
    let toggle = review.map(|_| if open { "  ▾ 理由" } else { "  ▸ 理由" });
    let line = |label: &str, e: Option<&mainline::Entry>, strong: bool, tail: Option<&str>| {
        let (t, item) = match e {
            // Andy 11-12 23:2x「起點 / 目前 應該是要帶出日期時間」⇒ 一律 月-日 時:分(不再省略今天的日期)
            Some(e) => (mainline::stamp(e.started_at, now), e.item.clone()),
            None => ("--".to_string(), "還沒回報 aib now".to_string()),
        };
        let lead = format!(
            "{MARGIN}{label}  {t}{}  ",
            " ".repeat(TIME_W.saturating_sub(str_width(&t)))
        );
        let room = w
            .saturating_sub(str_width(&lead) + tail.map_or(0, str_width))
            .max(8);
        let mut row = vec![
            Seg(lead, Some(Color::DarkGrey), false, false),
            Seg(
                fit(&item, room),
                Some(if strong { Color::White } else { Color::Grey }),
                false,
                strong,
            ),
        ];
        if let Some(t) = tail {
            row.push(Seg(t.into(), Some(Color::Cyan), false, true));
        }
        row
    };
    let mut rows = vec![
        head,
        // Andy 11-12 23:3x:字改成「最初目標 / 目前目標」
        line(FIRST_LABEL, m.first.as_ref(), false, None),
        line(NOW_LABEL, m.now.as_ref(), true, toggle),
    ];
    if let (Some(r), true) = (review, open) {
        // 展開：接在主軸字頭下面，標籤固定寬
        let lead = str_width(MARGIN) + str_width(FIRST_LABEL) + 2 + TIME_W + 2;
        let text_w = w.saturating_sub(lead + 8);
        let mut part = |label: &str, text: &str| {
            for (i, l) in wrap(text, text_w).into_iter().enumerate() {
                rows.push(vec![
                    seg(" ".repeat(lead)),
                    Seg(
                        if i == 0 {
                            format!("{label}  ")
                        } else {
                            " ".repeat(8)
                        },
                        Some(Color::Yellow),
                        false,
                        true,
                    ),
                    Seg(l, Some(Color::White), false, false),
                ]);
            }
        };
        part("為什麼", &r.why);
        part("建議  ", &r.idea);
        rows.push(vec![
            seg(" ".repeat(lead)),
            Seg(
                format!("回報 {}", mainline::stamp(r.at, now)),
                Some(Color::DarkGrey),
                false,
                false,
            ),
        ]);
    }
    rows
}

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
    let sugg_counts = suggestions(p, &app.pin);
    let ix = index(p);
    if ix.root.is_none() {
        out.push(vec![seg("進度資料是空的")]);
        return out;
    }
    // Andy 11-12 12:2x:頂部四行(進度 / 期望 / 最落後 / 信)都不要 ⇒ 最上面就是選單
    // Andy 11-12 13:1x:最上面兩行 = 本席一開始主軸 / 目前開發主軸(開始時間)/現在時間
    // Andy 11-12 13:3x:上面再空一行;目前主軸左邊放「判斷」,有延伸可開合、預設收合
    let (mut mainline_y, mut fleet_y) = (None, None);
    let (mut start_y, mut toggle_x) = (None, None);
    let (mut sugg, mut shadow_hit) = (None, None);
    if let Some(m) = &app.mainline {
        out.push(Vec::new());
        let mut rows = mainline_rows(m, app.now_secs, w, app.main_open);
        // 第一行右邊：現在時間 + 全艙(艦長用，點了開合);靠右
        let mut tail = vec![Seg(
            mainline::stamp(app.now_secs, app.now_secs),
            Some(Color::DarkGrey),
            false,
            false,
        )];
        // Andy 11-12 22:3x「把建議也列上右上角，點擊下去會跳到對應下方的建議」
        let mut sugg_at = None;
        // Andy 11-12 23:1x:右上角與分支標題數字要一致,同一個函式算;右上角「建議 ✓完成/!未完成」(23:1x 數字就好;23:3x 加打勾 / 等待符號;11-13 06:1x 沙漏改 !)
        if let Some((_, done, open)) = sugg_counts {
            sugg_at = Some(tail.len());
            tail.push(Seg(
                format!("   建議 ✓{done}/!{open}"),
                Some(Color::Magenta),
                false,
                true,
            ));
        }
        // Andy 11-12 12:0x「影子續跑應列在建議旁邊，一樣要有對應的項目」⇒ 點了跳到樹上的影子續跑分支
        let mut shadow_at = None;
        if let Some(b) = shadow_badge(app.shadow.as_ref()) {
            shadow_at = Some(tail.len());
            tail.push(Seg(b, Some(Color::Green), false, true));
        }
        if !app.fleet.is_empty() {
            // Andy 14:3x:⚠ 直接寫席碼(最多兩席，其餘 +N),不展開就知道看誰
            let hints: Vec<&str> = app
                .fleet
                .iter()
                .filter(|(_, f)| mainline::drift_hint(f))
                .map(|(c, _)| c.as_str())
                .collect();
            tail.push(Seg(
                format!("   全艙 {}", if app.fleet_open { "▾" } else { "▸" }),
                Some(Color::Cyan),
                false,
                true,
            ));
            if !hints.is_empty() {
                let mut t = format!(
                    " ⚠ {}",
                    hints.iter().take(2).copied().collect::<Vec<_>>().join(" ")
                );
                if hints.len() > 2 {
                    t.push_str(&format!(" +{}", hints.len() - 2));
                }
                tail.push(Seg(t, Some(Color::Yellow), false, true));
            }
            fleet_y = Some(out.len());
        }
        let width = |r: &[Seg]| r.iter().map(|g| str_width(&g.0)).sum::<usize>();
        let gap = w.saturating_sub(width(&rows[0]) + width(&tail) + 2).max(3);
        rows[0].push(seg(" ".repeat(gap)));
        if let Some(k) = sugg_at {
            // 「   建議 N」的前三格是空白，點字才算
            let x0 = width(&rows[0]) + width(&tail[..k]) + 3;
            sugg = Some((out.len(), x0, x0 + str_width(&tail[k].0) - 3));
        }
        if let Some(k) = shadow_at {
            let x0 = width(&rows[0]) + width(&tail[..k]) + 3;
            shadow_hit = Some((out.len(), x0, x0 + str_width(&tail[k].0) - 3));
        }
        rows[0].extend(tail);
        // Andy 11-12 23:2x「起點這行應該與上面那行多一個空格」⇒ 判斷那行與起點之間空一行
        mainline_y = Some(out.len() + 3);
        start_y = Some(out.len() + 2);
        toggle_x = rows[2].last().filter(|g| g.0.contains("理由")).map(|_| {
            str_width(&rows[2].iter().map(|g| g.0.as_str()).collect::<String>())
                - str_width("  ▸ 理由")
        });
        rows.insert(1, Vec::new());
        out.extend(rows);
        if app.fleet_open {
            out.push(Vec::new());
            out.extend(fleet_rows(&app.fleet, app.now_secs, w));
        }
    }
    // Andy 11-12 12:4x:選單緊貼最上面太壓迫 ⇒ 上面空一行(點了起點 / 目前 ⇒ 這行寫跳到哪)
    out.push(match &app.jump_note {
        Some(n) => vec![Seg(format!("  {n}"), Some(Color::Yellow), false, false)],
        None => Vec::new(),
    });
    out.push(toolbar(app, w));
    let f = &app.filter;
    let mut hits = Hits {
        toolbar_y: Some(out.len() - 1),
        toolbar: out[out.len() - 1].iter().map(|g| g.0.as_str()).collect(),
        mainline_y,
        fleet_y,
        sugg,
        start_y,
        toggle_x,
        shadow: shadow_hit,
        ..Hits::default()
    };
    out.push(vec![seg("─".repeat(w))]);

    let rows = flatten(p, &ix, &app.open, f);
    let body_h = h.saturating_sub(out.len() + 1).max(4);
    let side_by_side = w >= 100 && app.show_tree && app.show_side;
    let tree_w = if side_by_side { w * 58 / 100 } else { w };
    let side_w = if side_by_side {
        w.saturating_sub(tree_w + 4)
    } else {
        w
    };

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
            let title = shown_title(p, sugg_counts, r.idx);
            let left = format!(
                "{}{} {}{}",
                "  ".repeat(r.depth),
                mark(p, &ix, r.idx, &app.open, f.active()),
                if n.kind == "ext" { "延伸 " } else { "" },
                title
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
    // Andy 11-12 13:5x:標題、內容、理由都擠在一起 ⇒ 段與段之間空一行、標籤後空格、理由折行不截斷
    let mut side: Vec<Vec<Seg>> = Vec::new();
    if app.show_side && !p.nodes.is_empty() {
        let n = &p.nodes[app.sel.min(p.nodes.len() - 1)];
        let label = |k: &str| Seg(format!("{k}  "), Some(Color::DarkGrey), false, true);
        // 一行「標籤  值」;值太長就折行，續行對齊值的起點
        let kv = |side: &mut Vec<Vec<Seg>>, k: &str, v: &str, color: Option<Color>| {
            let lw = str_width(k) + 2;
            for (i, l) in wrap(v, side_w.saturating_sub(lw)).into_iter().enumerate() {
                let head = if i == 0 {
                    label(k)
                } else {
                    seg(" ".repeat(lw))
                };
                side.push(vec![head, Seg(l, color, false, false)]);
            }
        };
        side.push(vec![Seg("為什麼在做這件".into(), None, false, true)]);
        side.push(Vec::new());
        let ch = chain(p, &ix, app.sel.min(p.nodes.len() - 1));
        for (i, &c) in ch.iter().enumerate() {
            let last = i + 1 == ch.len();
            side.push(vec![Seg(
                fit(
                    &format!(
                        "{}{}{}",
                        "  ".repeat(i),
                        if i > 0 { "└ " } else { "" },
                        shown_title(p, sugg_counts, c)
                    ),
                    side_w,
                ),
                if last { None } else { Some(Color::DarkGrey) },
                false,
                last,
            )]);
        }
        if let Some(why) = &n.why {
            side.push(Vec::new());
            kv(&mut side, "理由", why, None);
        }
        side.push(Vec::new());
        if let Some(s) = &n.status {
            kv(&mut side, "狀態", s, status_color(Some(s)));
        }
        let with = if n.with.is_empty() {
            String::new()
        } else {
            format!(" · 一起 {}", n.with.join(" "))
        };
        kv(
            &mut side,
            "誰做",
            &format!("{}{with}", n.owner.as_deref().unwrap_or("沒人負責")),
            None,
        );
        if let Some(hd) = &n.handoff {
            kv(&mut side, "接手", hd, Some(Color::Yellow));
        }
        let tm: Vec<String> = [("開始", &n.start), ("更新", &n.updated), ("預計", &n.eta)]
            .iter()
            .filter_map(|(k, v)| v.as_ref().map(|v| format!("{k} {v}")))
            .collect();
        if !tm.is_empty() {
            kv(&mut side, "時間", &tm.join(" · "), Some(Color::DarkGrey));
        }
        if let Some((wq, rq)) = app.dm_of(n.owner.as_deref()) {
            let f = |v: Option<u32>| v.map(|x| x.to_string()).unwrap_or_else(|| "?".into());
            kv(
                &mut side,
                &format!("DM({})", n.owner.as_deref().unwrap_or("")),
                &format!("待審 {} · 已讀未結 {}", f(wq), f(rq)),
                None,
            );
        }
        if let Some(ev) = &n.evidence {
            kv(&mut side, "證據", ev, Some(Color::DarkGrey));
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
            line.push(Seg(" │  ".into(), Some(Color::DarkGrey), false, false));
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
    app.jump_note = None;
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
        (KeyCode::Char('m'), _) => app.main_open = !app.main_open,
        (KeyCode::Char('f'), _) => app.fleet_open = !app.fleet_open,
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
        // 非艦長畫面固定本席:鍵盤 s 與點工具列「席」(on_click 送 s)都停在這裡;
        // 「沒人負責」跟固定席不可能同時成立(樹會整片空白,自審 L2)⇒ 也不切
        (KeyCode::Char('s') | KeyCode::Char('n'), _) if !app.pin.is_empty() => {}
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
        (KeyCode::Char('r'), _) => app.filter = app.base_filter(),
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
    let mut args = match parse_args() {
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
        mainline: None,
        now_secs: epoch_now(),
        main_open: false,
        fleet: Vec::new(),
        fleet_open: false,
        jump_note: None,
        shadow: None,
        shadow_mtime: None,
        fixes: None,
        fixes_mtime: None,
        pin: Vec::new(),
    };
    // 沒帶 --seat:在 tmux 窗格裡就問這個窗是哪一席(窗選項 @aib_seat,狀態列用的同一個碼)
    // --only-seat 沒帶 --seat:最上面兩行主軸也用同一席(清單第一席)
    if args.seat.is_none() {
        args.seat = args.only_seat.first().cloned().or_else(tmux_seat);
    }
    app.pin = auto_pin(&args.only_seat, args.seat.as_deref(), || {
        std::env::var("TMUX_PANE")
            .ok()
            .map(|p| seatview::pane_layout(&seatview::OwnTmux, &p))
    });
    app.filter = app.base_filter();
    if let Some(seat) = &args.seat {
        app.mainline = Some(mainline::refresh(&state_dir(), seat, app.now_secs));
    }
    app.load(&args.data);
    app.load_shadow(&shadow_path(), &shadow_cache());
    app.load_fixes(&autofix_path());
    app.graft_shadow();
    // 席位表在進度資料裡 ⇒ 先讀資料再量全艙，第一張畫面就有
    app.fleet = fleet_of(&app, app.now_secs);
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
            // 進度資料重讀會蓋掉接上去的影子續跑分支 ⇒ 兩邊任一換了都重接
            let plan_changed = app.mtime != before;
            // 用 | 不用 ||:兩份都要讀，不能前一份換了就跳過後一份
            if app.load_shadow(&shadow_path(), &shadow_cache())
                | app.load_fixes(&autofix_path())
                | plan_changed
            {
                app.graft_shadow();
                dirty = true;
            }
            if let Some(seat) = &args.seat {
                let now = epoch_now();
                let m = Some(mainline::refresh(&state_dir(), seat, now));
                // 現在時間只到分：換分鐘或主軸變了才重畫
                if m != app.mainline || now / 60 != app.now_secs / 60 {
                    dirty = true;
                }
                app.mainline = m;
                app.now_secs = now;
                let f = fleet_of(&app, now);
                if f != app.fleet {
                    dirty = true;
                }
                app.fleet = f;
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
            mainline: None,
            now_secs: 0,
            main_open: false,
            fleet: Vec::new(),
            fleet_open: false,
            jump_note: None,
            shadow: None,
            shadow_mtime: None,
            fixes: None,
            fixes_mtime: None,
            pin: Vec::new(),
        }
    }

    /// Andy 11-12 22:3x:右上角「建議 N」(還沒完成的件數),點字跳到樹上的建議分支並展開;點同一行別處照舊開合全艙
    #[test]
    fn the_suggestions_badge_jumps_to_the_suggestions_branch() {
        let mut app = empty_app();
        app.err = None;
        app.plan = parse(
            r#"{"nodes":[{"id":"V","parent":null,"kind":"vision","title":"總大綱"},
                {"id":"G","parent":"V","kind":"group","title":"別的"},
                {"id":"S","parent":"V","kind":"group","title":"建議"},
                {"id":"S1","parent":"S","kind":"suggestion","title":"一","status":"實查通過"},
                {"id":"S2","parent":"S","kind":"suggestion","title":"二","status":"未開始"},
                {"id":"S3","parent":"S","kind":"suggestion","title":"三"}]}"#,
        )
        .unwrap();
        app.mainline = Some(Mainline::default());
        app.fleet = vec![("aaaaa".into(), Mainline::default())];
        let text =
            |f: &Vec<Vec<Seg>>, i: usize| f[i].iter().map(|g| g.0.as_str()).collect::<String>();
        let f = frame(&app, 120, 30);
        let top = text(&f, 1);
        assert!(top.contains("   建議 ✓1/!2   全艙 ▸"), "{top}");
        let (y, x0, x1) = app.hits.borrow().sugg.expect("記下建議的位置");
        assert_eq!(y, 1);
        assert_eq!(x1 - x0, str_width("建議 ✓1/!2"));
        assert_eq!(
            str_width("⏳"),
            2,
            "沙漏在終端機佔兩格，算錯會讓點擊範圍偏一格"
        );
        assert_eq!(str_width("✓"), 1);
        let before: String = f[1].iter().map(|g| g.0.as_str()).collect();
        assert_eq!(
            str_width(&before[..before.find("建議").unwrap()]),
            x0,
            "點的範圍對準字"
        );
        // 點「建議」:選到建議分支、展開它，看得到子項
        assert!(on_click(&mut app, x0, y));
        assert_eq!(app.plan.nodes[app.sel].id, "S");
        assert!(app.open.contains("S"));
        assert!(!app.fleet_open, "點建議不開全艙");
        assert!(app.jump_note.as_deref().unwrap().contains("還沒完成 2 項"));
        // 分支標題用同一組數字(資料裡寫的「(14 項)」不算數)
        app.plan.nodes[2].title = "建議(14 項)".into();
        let f = frame(&app, 120, 30);
        let all: Vec<String> = (0..f.len()).map(|i| text(&f, i)).collect();
        assert!(
            all.iter().any(|l| l.contains("建議(完成 1 / 未完成 2)")),
            "{all:#?}"
        );
        assert!(!all.iter().any(|l| l.contains("14 項")), "{all:#?}");
        // 點同一行最左邊 ⇒ 照舊開合全艙
        assert!(on_click(&mut app, 0, y));
        assert!(app.fleet_open);
        // 沒有建議節點 ⇒ 不顯示
        app.plan =
            parse(r#"{"nodes":[{"id":"V","parent":null,"kind":"vision","title":"總大綱"}]}"#)
                .unwrap();
        let f = frame(&app, 120, 30);
        assert!(!text(&f, 1).contains("建議"));
        assert!(app.hits.borrow().sugg.is_none());
    }

    /// Andy 11-13 非艦長畫面(519d0 意見 1–5):--only-seat 固定本席(可多席);鍵盤 s 與點工具列「席」都不輪替、
    /// r 不清掉;全艙那一列不畫;建議徽章與影子續跑只算這幾席
    #[test]
    fn only_seat_pins_the_tree_to_those_seats() {
        let mut app = empty_app();
        app.err = None;
        app.plan = parse(
            r#"{"cabin":"dab9","seats":{"a9136":{"cabin":"dab9"},"043dd":{"cabin":"dab9"}},
              "nodes":[{"id":"V","parent":null,"kind":"vision","title":"總大綱"},
              {"id":"A","parent":"V","kind":"task","title":"本席的","owner":"a9136"},
              {"id":"B","parent":"V","kind":"task","title":"別席的","owner":"043dd"},
              {"id":"C","parent":"V","kind":"task","title":"分身繼承的","owner":"a63ab"},
              {"id":"S","parent":"V","kind":"group","title":"建議"},
              {"id":"S1","parent":"S","kind":"suggestion","title":"一","owner":"a9136","status":"實查通過"},
              {"id":"S2","parent":"S","kind":"suggestion","title":"二","owner":"043dd"},
              {"id":"S3","parent":"S","kind":"suggestion","title":"三","owner":"043dd"}]}"#,
        )
        .expect("sample parses");
        app.open.insert("V".into());
        app.pin = seat_list("a9136, a63ab").expect("list");
        app.filter = app.base_filter();
        let ids = |app: &App| -> Vec<String> {
            let ix = index(&app.plan);
            flatten(&app.plan, &ix, &app.open, &app.filter)
                .iter()
                .map(|r| app.plan.nodes[r.idx].id.clone())
                .collect()
        };
        let key = |app: &mut App, k: char| {
            assert!(on_key(
                app,
                KeyEvent::new(KeyCode::Char(k), KeyModifiers::NONE)
            ));
        };
        // 固定席不算篩選:收合照展開集合;建議分支 S 留著(底下有本席的 S1,做完了也列)
        let want = ["V", "A", "C", "S"];
        assert_eq!(ids(&app), want, "only the pinned seats' nodes and parents");
        app.open.insert("S".into());
        assert_eq!(
            ids(&app),
            ["V", "A", "C", "S", "S1"],
            "S1 is ours, S2/S3 are not"
        );
        key(&mut app, 'c');
        assert_eq!(
            ids(&app),
            want,
            "c collapses a pinned tree, S1 hidden again (自審 M1)"
        );
        // 鍵盤 s、n、r
        for k in ['s', 's', 'n', 'r'] {
            key(&mut app, k);
            assert_eq!(app.filter.pin, "a9136,a63ab", "{k} keeps the pinned seats");
            assert!(app.filter.seat.is_empty() && !app.filter.orphan, "{k}");
            assert_eq!(ids(&app), want, "{k} does not show another seat");
        }
        // 點「建議」徽章:跳到看得見的建議分支
        assert!(jump_to_suggestions(&mut app));
        assert_eq!(app.plan.nodes[app.sel].id, "S");
        assert!(
            ids(&app).contains(&"S1".to_string()),
            "the jump lands on a shown row"
        );
        // 跳到別席的節點(主軸「目前」指到 B):不停在看不見的列
        let b = app.plan.nodes.iter().position(|n| n.id == "B").expect("B");
        let before = app.sel;
        select(&mut app, b);
        assert_eq!(app.sel, before, "another seat's node is not selected");
        assert!(ids(&app).iter().all(|i| i != "B"));
        app.open.remove("S");
        // 滑鼠點工具列「席」(on_click 找字 ⇒ 送 s)
        let _ = frame(&app, 140, 30);
        let h = app.hits.borrow().clone();
        let ty = h.toolbar_y.expect("toolbar");
        assert!(h.toolbar.contains("席:a9136,a63ab(固定)"), "{}", h.toolbar);
        let sx = str_width(&h.toolbar[..h.toolbar.find("席:").expect("seat item")]);
        assert!(on_click(&mut app, sx + 1, ty));
        assert_eq!(
            app.filter.pin, "a9136,a63ab",
            "a click on 席 keeps the pinned seats"
        );
        assert!(app.filter.seat.is_empty());
        // 全艙那一列不畫;建議只算本席(1 項,做完 1)
        assert!(fleet_of(&app, 0).is_empty(), "no fleet row on a seat view");
        assert_eq!(
            suggestions(&app.plan, &app.pin).map(|t| (t.1, t.2)),
            Some((1, 0))
        );
        app.mainline = Some(Mainline::default());
        let f = frame(&app, 140, 30);
        let top: String = f[1].iter().map(|g| g.0.as_str()).collect();
        assert!(top.contains("建議 ✓1/!0"), "{top}");
        // 影子續跑只留本席省的
        let save = |name: &str, saved_s: f64| shadow::Save {
            at: 0,
            saved_s,
            ran: 1,
            reused: 1,
            with_s: 1.0,
            repo: String::new(),
            name: Some(name.into()),
        };
        let mut sv = Savings {
            total_s: 30.0,
            saves: vec![save("wt-a9136", 10.0), save("wt-043dd", 20.0)],
        };
        pin_saves(&mut sv, &app.pin);
        assert_eq!((sv.saves.len(), sv.total_s), (1, 10.0));
        // 接上樹:影子續跑與自動審修兩個分支在固定席看得到,件數只算本席(自審 H1 / L3)
        let fix = |seat: &str, fixed: bool| autofix::Fix {
            at: 0,
            seat: seat.into(),
            work: "#1".into(),
            sev: autofix::Sev::Low,
            what: "w".into(),
            fixed,
            commit: None,
        };
        app.shadow = Some(sv);
        app.fixes = Some(Fixes {
            items: vec![fix("a9136", true), fix("043dd", true), fix("043dd", false)],
        });
        app.graft_shadow();
        let title = |id: &str| {
            app.plan
                .nodes
                .iter()
                .find(|n| n.id == id)
                .map(|n| n.title.clone())
                .unwrap_or_default()
        };
        assert!(title("shadow").contains("1 次"), "{}", title("shadow"));
        assert!(title(FIX_ID).contains("修 1,註記 0"), "{}", title(FIX_ID));
        app.open.insert("shadow".into());
        app.open.insert(FIX_ID.into());
        let shown = ids(&app);
        for want in ["shadow", "shadow-1", FIX_ID, "autofix-1"] {
            assert!(shown.contains(&want.to_string()), "{want} in {shown:?}");
        }
        assert!(!shown.contains(&"autofix-2".to_string()), "{shown:?}");
        app.open.remove("shadow");
        app.open.remove(FIX_ID);
        app.shadow = None;
        app.fixes = None;
        app.graft_shadow();
        // 艦長畫面(沒有 pin):s 照常輪到別席、全艙與建議照全部算
        app.pin.clear();
        app.filter = app.base_filter();
        assert!(app.filter.pin.is_empty());
        assert_eq!(ids(&app), ["V", "A", "B", "C", "S"]);
        assert!(on_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('s'), KeyModifiers::NONE)
        ));
        assert_ne!(app.filter.seat, "", "s cycles to a seat when not pinned");
        assert_eq!(
            fleet_of(&app, 0).len(),
            2,
            "the captain view has the fleet row"
        );
        assert_eq!(
            suggestions(&app.plan, &app.pin).map(|t| (t.1, t.2)),
            Some((1, 2))
        );
        // 參數:空項是錯,不靜默變全部
        assert!(seat_list("a9136,").is_err());
        assert!(seat_list("").is_err());
    }

    /// 席位視角:窗是 seat ⇒ 固定在 --seat;--only-seat 優先;完整版面 / 不在 tmux / 沒有席 ⇒ 不固定
    #[test]
    fn seat_layout_pins_to_the_seat() {
        use seatview::Layout::{Full, Seat};
        let only = vec!["519d0".to_string(), "a63ab".to_string()];
        assert_eq!(auto_pin(&[], Some("a9136"), || Some(Seat)), ["a9136"]);
        assert_eq!(auto_pin(&only, Some("a9136"), || Some(Seat)), only);
        assert!(auto_pin(&[], Some("a9136"), || Some(Full)).is_empty());
        assert!(auto_pin(&[], Some("a9136"), || None).is_empty());
        assert!(auto_pin(&[], None, || Some(Seat)).is_empty());
        // --only-seat 有帶就不去問 tmux
        let asked = std::cell::Cell::new(false);
        let _ = auto_pin(&only, Some("a9136"), || {
            asked.set(true);
            Some(Seat)
        });
        assert!(!asked.get());
    }

    /// Andy 11-12 13:1x:有 --seat ⇒ 最上面兩行是一開始 / 目前主軸，下一行空白，再來才是選單;點選單照樣對得到
    #[test]
    fn mainline_rows_sit_above_the_menu() {
        let mut app = empty_app();
        app.err = None;
        app.plan =
            parse(r#"{"nodes":[{"id":"V","parent":null,"kind":"vision","title":"總大綱"}]}"#)
                .unwrap();
        let now = 1_794_460_800; // 台北 11-12
        let ds = mainline::day_start(now);
        app.now_secs = now;
        let entry = |item: &str, review| mainline::Entry {
            item: item.into(),
            started_at: ds + 540,
            seen: ds + 540,
            review,
            track: None,
        };
        app.mainline = Some(Mainline {
            first: Some(entry("席位接續", None)),
            ..Default::default()
        });
        let f = frame(&app, 100, 40);
        let text =
            |f: &Vec<Vec<Seg>>, i: usize| f[i].iter().map(|g| g.0.as_str()).collect::<String>();
        // Andy 13:3x:最上面空一行
        assert!(text(&f, 0).is_empty());
        // Andy 14:2x 小卡：第一行 = 判斷 + 三個次數，右邊靠齊現在時間
        assert!(
            text(&f, 1).starts_with("  判斷 —   校正 0 · 總結 0 · 交接 0"),
            "{}",
            text(&f, 1)
        );
        assert!(text(&f, 1).ends_with("11-12 13:20"), "{}", text(&f, 1));
        assert_eq!(str_width(&text(&f, 1)), 98, "靠右留 2 格");
        // 起點 / 目前：一律帶日期(Andy 11-12 23:2x),字頭上下對齊
        assert!(
            text(&f, 3).starts_with("  最初目標  11-12 00:09"),
            "{}",
            text(&f, 3)
        );
        assert!(text(&f, 4).starts_with("  目前目標  --"), "{}", text(&f, 4));
        let at = |i: usize, k: &str| {
            let t = text(&f, i);
            str_width(&t[..t.find(k).unwrap()])
        };
        assert_eq!(at(3, "席位接續"), at(4, "還沒回報"));
        assert!(!text(&f, 4).contains('▸'), "沒有判斷內容就沒有開合");
        assert!(text(&f, 5).is_empty());
        assert_eq!(app.hits.borrow().toolbar_y, Some(6));

        // 有判斷 ⇒ 百分比 + 顏色;校正 > 0 黃
        let review = |pct: u8| {
            Some(mainline::Review {
                pct,
                why: "因為 Andy 要".into(),
                idea: "建議 X".into(),
                at: ds + 600,
            })
        };
        for (pct, color) in [
            (85, Color::Green),
            (70, Color::Green),
            (69, Color::Yellow),
            (39, Color::Red),
        ] {
            app.mainline = Some(Mainline {
                first: Some(entry("席位接續", None)),
                now: Some(entry("做 B", review(pct))),
                corrections: 2,
                summaries: 3,
                handoffs: 1,
            });
            let f = frame(&app, 100, 40);
            assert_eq!(f[1][1].0, format!("判斷 {pct}%"));
            assert_eq!(f[1][1].1, Some(color), "{pct}");
            assert!(text(&f, 1).contains("校正 2 · 總結 3 · 交接 1"));
            assert_eq!(f[1][4].1, Some(Color::Yellow), "校正 > 0 黃");
        }
        // 很長的主軸 ⇒ 截斷補 …,不折行
        app.mainline = Some(Mainline {
            first: Some(entry(&"長".repeat(80), None)),
            now: Some(entry("做 B", review(90))),
            ..Default::default()
        });
        let f = frame(&app, 100, 40);
        assert!(text(&f, 3).ends_with('…') && str_width(&text(&f, 3)) <= 100);
        assert!(text(&f, 4).ends_with("  ▸ 理由"), "預設收合");
        assert!(text(&f, 5).is_empty());
        assert_eq!(app.hits.borrow().mainline_y, Some(4));
        // 點那一行 ⇒ 展開為什麼 / 建議 / 回報時間;再按 m ⇒ 收合
        // 點「▸ 理由」那幾格才開合(前面點了是跳到樹上)
        let tx = app.hits.borrow().toggle_x.unwrap();
        assert_eq!(tx + str_width("  ▸ 理由"), str_width(&text(&f, 4)));
        assert!(on_click(&mut app, tx + 3, 4));
        let f = frame(&app, 100, 40);
        assert!(text(&f, 4).ends_with("  ▾ 理由"));
        let pad = " ".repeat(at(3, "席位接續"));
        assert_eq!(text(&f, 5), format!("{pad}為什麼  因為 Andy 要"));
        assert_eq!(text(&f, 6), format!("{pad}建議    建議 X"));
        assert_eq!(text(&f, 7), format!("{pad}回報 11-12 00:10"));
        assert!(text(&f, 8).is_empty());
        assert_eq!(app.hits.borrow().toolbar_y, Some(9));
        on_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('m'), KeyModifiers::NONE),
        );
        assert!(!app.main_open);
    }

    /// Andy 11-12 14:5x:點「起點 / 目前」帶到樹上對應的項目;主軸換了，點下去就是新的那一項
    #[test]
    fn clicking_start_or_current_jumps_to_the_matching_item() {
        let mut app = empty_app();
        app.err = None;
        app.plan = parse(
            r#"{"nodes":[
            {"id":"V","parent":null,"kind":"vision","title":"總大綱"},
            {"id":"G","parent":"V","kind":"goal","title":"進度、期望值與席位"},
            {"id":"S","parent":"G","kind":"task","title":"席位接續 v2:重開也能即時接上"},
            {"id":"H","parent":"G","kind":"task","title":"交接檢查 Rust 化"},
            {"id":"P","parent":"V","kind":"task","title":"aib now 歷史","why":"PR #714"}]}"#,
        )
        .unwrap();
        let now = 1_794_460_800;
        app.now_secs = now;
        let e = |item: &str| mainline::Entry {
            item: item.into(),
            started_at: now - 60,
            seen: now - 60,
            review: None,
            track: None,
        };
        app.mainline = Some(Mainline {
            first: Some(e("席位接續 v2 決定已寫,等 Andy 看")),
            now: Some(e("交接檢查整支 Rust 化:排程已寫")),
            ..Default::default()
        });
        let _ = frame(&app, 100, 40);
        let (sy, my) = {
            let h = app.hits.borrow();
            (h.start_y.unwrap(), h.mainline_y.unwrap())
        };
        assert!(on_click(&mut app, 5, sy));
        assert_eq!(app.plan.nodes[app.sel].id, "S");
        assert!(app
            .jump_note
            .as_deref()
            .is_some_and(|n| n.starts_with("跳到:席位接續 v2")));
        let f = frame(&app, 100, 40);
        assert!(
            (0..8).any(|y| f[y]
                .iter()
                .map(|g| g.0.as_str())
                .collect::<String>()
                .contains("跳到:席位接續 v2")),
            "畫面上看得到跳到哪"
        );
        assert!(app.open.contains("V") && app.open.contains("G"), "祖先展開");
        assert!(on_click(&mut app, 5, my));
        assert_eq!(app.plan.nodes[app.sel].id, "H");
        // 主軸即時換了 ⇒ 同一行點下去跳到新的那一項(#PR 號對得上加分)
        app.mainline.as_mut().unwrap().now = Some(e("#714 修 CI 紅燈"));
        assert!(on_click(&mut app, 5, my));
        assert_eq!(app.plan.nodes[app.sel].id, "P");
        // 對不到就不動
        app.mainline.as_mut().unwrap().now = Some(e("吃午餐"));
        assert!(on_click(&mut app, 5, my));
        assert_eq!(app.plan.nodes[app.sel].id, "P");
        assert!(app
            .jump_note
            .as_deref()
            .is_some_and(|n| n.starts_with("樹上沒有對應「吃午餐」")));
    }

    /// Andy 11-12 13:4x:全艙判斷(f 或點)—— 一開始那行尾端 ▸,點開每席一行;自稱主軸內卻換題 ⇒ ⚠
    #[test]
    fn fleet_rows_toggle_and_flag_topic_change() {
        let mut app = empty_app();
        app.err = None;
        app.plan =
            parse(r#"{"nodes":[{"id":"V","parent":null,"kind":"vision","title":"總大綱"}]}"#)
                .unwrap();
        let now = 1_794_460_800;
        app.now_secs = now;
        let en = |item: &str, st: i64, pct: Option<u8>| mainline::Entry {
            item: item.into(),
            started_at: st,
            seen: st,
            review: pct.map(|pct| mainline::Review {
                pct,
                why: "w".into(),
                idea: "i".into(),
                at: st,
            }),
            track: None,
        };
        app.mainline = Some(Mainline {
            first: Some(en("席位接續 v2 決定", 1, None)),
            ..Default::default()
        });
        app.fleet = vec![
            (
                "aaaaa".into(),
                Mainline {
                    first: Some(en("席位接續 v2 決定", 1, None)),
                    now: Some(en("主機發熱三條修法", 2, Some(90))),
                    corrections: 1,
                    ..Default::default()
                },
            ),
            ("bbbbb".into(), Mainline::default()),
        ];
        let text =
            |f: &Vec<Vec<Seg>>, i: usize| f[i].iter().map(|g| g.0.as_str()).collect::<String>();
        let f = frame(&app, 160, 40);
        assert!(text(&f, 1).ends_with("全艙 ▸ ⚠ aaaaa"), "{}", text(&f, 1));
        assert!(text(&f, 5).is_empty(), "預設收合");
        assert_eq!(app.hits.borrow().fleet_y, Some(1));
        assert!(on_click(&mut app, 0, 1));
        let f = frame(&app, 160, 40);
        assert!(text(&f, 1).contains("全艙 ▾"));
        assert!(text(&f, 5).is_empty());
        let a = text(&f, 6);
        assert!(
            a.starts_with("  @aaaaa  90%   校正1 總結0 交接0  01-01 08:00 主機發熱三條修法"),
            "{a}"
        );
        assert!(a.ends_with("⚠ 換題?"), "{a}");
        let b = text(&f, 7);
        assert!(
            b.starts_with("  @bbbbb  —     校正0 總結0 交接0  --    還沒回報"),
            "{b}"
        );
        assert!(!b.contains('⚠'));
        assert_eq!(
            str_width(&a[..a.find("校正").unwrap()]),
            str_width(&b[..b.find("校正").unwrap()]),
            "欄位對齊"
        );
        assert!(text(&f, 8).is_empty());
        // 超過兩席 ⇒ 前兩席 + 其餘數量
        let drift = app.fleet[0].1.clone();
        for c in ["ccccc", "ddddd"] {
            app.fleet.push((c.into(), drift.clone()));
        }
        let f = frame(&app, 160, 40);
        assert!(text(&f, 1).ends_with("⚠ aaaaa ccccc +1"), "{}", text(&f, 1));
        on_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('f'), KeyModifiers::NONE),
        );
        assert!(!app.fleet_open);
    }

    /// Andy 11-12 13:5x:右欄標題 / 內容 / 理由要有空格與空行，理由折行不截斷
    #[test]
    fn side_panel_has_breathing_room() {
        let mut app = empty_app();
        app.err = None;
        let why = "要考慮每個aib艙、每席ai可能重開不同，所以要能即時接上".repeat(3);
        app.plan = parse(&format!(
            r#"{{"nodes":[{{"id":"V","parent":null,"kind":"vision","title":"總大綱","why":"{why}"}}]}}"#
        ))
        .unwrap();
        let f = frame(&app, 120, 40);
        let side: Vec<String> = f
            .iter()
            .map(|l| l.iter().map(|g| g.0.as_str()).collect::<String>())
            .filter_map(|t| t.split_once(" │  ").map(|(_, r)| r.to_string()))
            .collect();
        assert_eq!(side[0], "為什麼在做這件");
        assert_eq!(side[1], "", "標題下空一行");
        assert_eq!(side[2], "總大綱");
        assert_eq!(side[3], "", "理由前空一行");
        assert!(side[4].starts_with("理由  要考慮"), "{}", side[4]);
        assert!(
            side[5].starts_with("      "),
            "理由續行對齊值的起點:{}",
            side[5]
        );
        let joined: String = side[4..]
            .iter()
            .take_while(|l| !l.is_empty())
            .map(|l| l.trim_start_matches("理由").trim())
            .collect();
        assert_eq!(joined, why, "理由不截斷");
        let who = side
            .iter()
            .position(|l| l.starts_with("誰做  沒人負責"))
            .expect("誰做");
        assert_eq!(side[who - 1], "", "細節前空一行");
    }

    #[test]
    fn wrap_breaks_on_display_width() {
        assert_eq!(wrap("一二三四五", 4), vec!["一二", "三四", "五"]);
        assert_eq!(wrap("abc", 10), vec!["abc"]);
    }

    /// Andy 11-12 12:0x:影子續跑跟建議一樣：右上角「影子續跑 共省」,點了跳到樹上的影子續跑分支，
    /// 每次省成功一項(新的在前);進度資料重讀後分支還在
    #[test]
    fn autofix_branch_sits_below_shadow_and_lists_each_finding() {
        let mut app = empty_app();
        app.err = None;
        app.plan =
            parse(r#"{"nodes":[{"id":"V","parent":null,"kind":"vision","title":"總大綱"}]}"#)
                .unwrap();
        app.now_secs = 1_794_460_800;
        app.shadow = Some(shadow::parse(
            r#"{"saved_s": 60, "ran": 1, "reused": 2, "utc": "2026-11-01T00:00:00Z"}"#,
        ));
        let dir = std::env::temp_dir().join(format!("aib-plan-fix-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("autofix.jsonl");
        let rows = [
            r##"{"at":1793491200,"seat":"a63ab","work":"#731","sev":"M","what":"舊名稱沒清","state":"fixed","commit":"cb9014cd"}"##,
            r##"{"at":1793494800,"seat":"043dd","work":"#730","sev":"L","what":"註解錯字","state":"noted"}"##,
            r##"{"at":1793498400,"seat":"a63ab","work":"#731","sev":"H","what":"只記不修","state":"noted"}"##,
        ];
        std::fs::write(&path, rows.join("\n")).unwrap();
        assert!(app.load_fixes(&path), "第一次讀 = 換資料");
        assert!(!app.load_fixes(&path), "檔案沒變不重讀");
        // 真的沒重讀(不是讀了剛好一樣):清掉記憶體裡的，修改時間沒變就不會補回來
        let kept = app.fixes.take();
        assert!(!app.load_fixes(&path), "修改時間沒變 ⇒ 不碰檔案");
        assert!(app.fixes.is_none());
        app.fixes = kept;
        let _ = std::fs::remove_dir_all(&dir);
        app.graft_shadow();
        app.graft_shadow();
        let ids: Vec<&str> = app.plan.nodes.iter().map(|n| n.id.as_str()).collect();
        assert_eq!(
            ids,
            vec![
                "V",
                "shadow",
                "shadow-1",
                "autofix",
                "autofix-1",
                "autofix-2"
            ],
            "接在影子續跑下方;接兩次不重複;嚴重只記不修的列不收"
        );
        let title = |id: &str| app.plan.nodes.iter().find(|n| n.id == id).unwrap();
        assert_eq!(title("autofix").title, "自動審修(修 1,註記 1)");
        assert_eq!(title("autofix").parent.as_deref(), Some("V"));
        assert_eq!(
            title("autofix-1").title,
            "11-01 09:00 註記 輕度 · 註解錯字 · #730"
        );
        assert_eq!(
            title("autofix-2").title,
            "11-01 08:00 修 中度 · 舊名稱沒清 · #731"
        );
        assert_eq!(
            title("autofix-2").why.as_deref(),
            Some("修正 commit cb9014cd")
        );
        assert_eq!(title("autofix-2").owner.as_deref(), Some("a63ab"));
        assert!(
            app.plan
                .nodes
                .iter()
                .filter(|n| n.id.starts_with("autofix"))
                .all(|n| n.status.is_none()),
            "沒有狀態 ⇒ 不算進完成度"
        );
    }

    #[test]
    fn shadow_badge_jumps_to_its_branch_with_one_item_per_save() {
        let mut app = empty_app();
        app.err = None;
        app.plan = parse(
            r#"{"nodes":[{"id":"V","parent":null,"kind":"vision","title":"總大綱"},
                {"id":"G","parent":"V","kind":"group","title":"別的","status":"實查通過"}]}"#,
        )
        .unwrap();
        app.now_secs = 1_794_460_800;
        app.mainline = Some(Mainline::default());
        let mut text = String::new();
        for i in 0..3 {
            text.push_str(&format!(
                "{{\"saved_s\": 600, \"ran\": {i}, \"reused\": 7, \"utc\": \"2026-11-01T0{i}:00:00Z\"}}\n"
            ));
        }
        text.push_str(r#"{"saved_s": 0.0, "ran": 1, "reused": 0, "utc": "2026-11-09T00:00:00Z"}"#);
        let dir = std::env::temp_dir().join(format!("aib-plan-shadow-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("test-stats.jsonl");
        std::fs::write(&path, &text).unwrap();
        let cache = dir.join("names.json");
        assert!(app.load_shadow(&path, &cache), "第一次讀 = 換資料");
        assert!(!app.load_shadow(&path, &cache), "檔案沒變不重讀");
        assert!(cache.exists(), "名稱快取寫在給的位置");
        let _ = std::fs::remove_dir_all(&dir);
        app.graft_shadow();
        app.graft_shadow();
        let titles: Vec<(&str, &str)> = app
            .plan
            .nodes
            .iter()
            .filter(|n| n.id.starts_with("shadow"))
            .map(|n| (n.id.as_str(), n.title.as_str()))
            .collect();
        assert_eq!(
            titles,
            vec![
                ("shadow", "影子續跑(共省 30 分 0 秒,3 次)"),
                ("shadow-1", "11-01 10:00 省 10 分 0 秒 · 沒記到是哪件"),
                ("shadow-2", "11-01 09:00 省 10 分 0 秒 · 沒記到是哪件"),
                ("shadow-3", "11-01 08:00 省 10 分 0 秒 · 沒記到是哪件"),
            ],
            "接兩次不重複;新的在前"
        );
        let br = app.plan.nodes.iter().find(|n| n.id == "shadow").unwrap();
        assert_eq!(br.parent.as_deref(), Some("V"));
        assert!(
            app.plan
                .nodes
                .iter()
                .filter(|n| n.id.starts_with("shadow"))
                .all(|n| n.status.is_none()),
            "沒有狀態 ⇒ 不算進完成度"
        );
        let t = |f: &Vec<Vec<Seg>>, i: usize| f[i].iter().map(|g| g.0.as_str()).collect::<String>();
        let f = frame(&app, 120, 40);
        assert!(t(&f, 1).contains("   影子續跑"), "{}", t(&f, 1));
        assert!(
            !t(&f, 1).contains("30分0秒"),
            "右上角只留字(Andy 11-13 06:1x)"
        );
        let (y, x0, x1) = app.hits.borrow().shadow.expect("右上角可點");
        assert_eq!(y, 1);
        let top = t(&f, 1);
        let at = str_width(&top[..top.find("影子續跑").unwrap()]);
        assert_eq!((x0, x1), (at, at + str_width("影子續跑")));
        assert!(on_click(&mut app, x0, y));
        assert_eq!(app.plan.nodes[app.sel].id, "shadow");
        assert!(app.open.contains("shadow"));
        assert_eq!(app.jump_note.as_deref(), Some("跳到:影子續跑(省成功 3 次)"));
        let f = frame(&app, 120, 40);
        let all: Vec<String> = (0..f.len()).map(|i| t(&f, i)).collect();
        assert!(
            all.iter().any(|l| l.contains("11-01 10:00 省 10 分 0 秒")),
            "展開看得到每一項"
        );
        // 進度資料重讀(換掉整棵樹)後重接，分支還在、選取留在原項
        app.plan =
            parse(r#"{"nodes":[{"id":"V","parent":null,"kind":"vision","title":"總大綱"}]}"#)
                .unwrap();
        app.sel = 0;
        app.graft_shadow();
        assert!(app.plan.nodes.iter().any(|n| n.id == "shadow-3"));
        // 沒有紀錄 ⇒ 右上角不顯示、樹上沒有分支
        app.shadow = None;
        app.graft_shadow();
        assert!(!app.plan.nodes.iter().any(|n| n.id.starts_with("shadow")));
        let f = frame(&app, 120, 40);
        assert!(!t(&f, 1).contains("影子續跑"));
        assert!(app.hits.borrow().shadow.is_none());
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
