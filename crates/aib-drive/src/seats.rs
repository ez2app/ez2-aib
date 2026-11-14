//! S79 sig:席位事實(IFACE-s79-drive-519d0-1115 §1,b433b)。
//!
//! 不拿各席自己寫的狀態字當結論。三個事實 —— `alive`、`hands`、`capacity` ——
//! 每個都是 yes / no / unknown,附 why 與資料時間;`dispatchable` / `size` 由它們算,
//! 不手寫。讀不到 = unknown,**不算閒**。
//!
//! 來源(都在 [`Source`] 後面,測試用假的):
//! - `state/seat-now/<席>.json`(aib now 寫的 state / at)與 `<席>.beat`(狀態列最後一次畫);
//! - `state/dm-queue/<席>/q_*.json`(未讀數、最後一封是送進去了還是還在排隊、在哪個艙哪個窗);
//! - tmux:`tmux -L <艙> list-windows -a` 的視窗最後輸出時間(沒有這個窗 = 視窗不在);
//! - `state/host-lease.json`:這台機器的租約在誰名下(只看得到這台;別台的收據今天不看)。
//!
//! 今天的最小版(519d0 00:10):alive + hands + unread + waiting_on。
//! `usage_pct`、`wip_unpushed`、`open_prs`、`on_deck`、`first` 讀不到 ⇒ null / 空,不猜。

use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::Duration;

/// 待審信未讀到這麼多 ⇒ 失聯(不是閒)
pub const UNREAD_LOST: usize = 10;
/// 最後活動超過這麼多小時 ⇒ 失聯
pub const LOST_HOURS: u64 = 2;
/// 視窗安靜滿這麼多分鐘、沒有排隊中的信 ⇒ 手空
pub const HANDS_FREE_MIN: u64 = 5;
/// 用量到這裡 ⇒ 先交接(讀得到才用)
pub const USAGE_FULL: u8 = 80;
/// 審過未推的片到這麼多 ⇒ 只派審查(讀得到才用)
pub const WIP_MAX: usize = 3;
/// 等收據、預計這麼多分鐘內回來 ⇒ 只派一輪審查這種小事
pub const REVIEW_ETA_MAX: u64 = 40;
/// 統籌席:不算可派的開發人力
pub const COORDINATOR: &str = "519d0";
/// seat-now 檔超過這麼多天沒寫的席不列(早就收掉的舊席)
pub const LIST_DAYS: u64 = 2;
/// 一次 tmux 呼叫最多等這麼久
pub const TMUX_TIMEOUT: Duration = Duration::from_secs(5);
/// JSON 的 schema 名
pub const SCHEMA: &str = "aib-drive.seats.v1";

/// 讀到了 / 讀不到(附原因)
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Known<T> {
    Known(T),
    Unknown(String),
}

/// `seat-now/<席>.json` 裡用得到的
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SeatNow {
    /// 最後一次寫的時間(秒)
    pub at: Option<u64>,
    /// `reply` 等別席 / `decide` 等真人 / 其他或沒有
    pub state: Option<String>,
}

/// 佇列裡的一封信(用得到的欄位)
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct DmItem {
    pub status: String,
    pub sent_at: u64,
    pub read_at: Option<u64>,
    pub done_at: Option<u64>,
    /// 艙(`aihub-<instance>`,也是 tmux socket 名)
    pub hub: Option<String>,
    /// tmux 視窗 id(`@12`)
    pub wid: Option<String>,
}

/// 外部來源
pub trait Source {
    /// 要看的席碼(seat-now 有、`LIST_DAYS` 內寫過的)
    fn seats(&self, now: u64) -> Result<Vec<String>, String>;
    fn seat_now(&self, seat: &str) -> Known<SeatNow>;
    /// 狀態列最後一次畫的時間
    fn beat(&self, seat: &str) -> Option<u64>;
    fn dms(&self, seat: &str) -> Known<Vec<DmItem>>;
    /// 艙裡每個視窗 → 最後輸出時間;讀不到 = Unknown
    fn windows(&self, hub: &str) -> Known<BTreeMap<String, u64>>;
    /// 這台機器的租約在誰名下(沒有 = None)
    fn lease_owner(&self, now: u64) -> Known<Option<String>>;
}

// ---------- 判斷 ----------

/// 一個事實:值 + 為什麼
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Fact {
    pub v: &'static str,
    pub why: String,
}

impl Fact {
    fn new(v: &'static str, why: impl Into<String>) -> Self {
        Fact { v, why: why.into() }
    }
}

/// 一席的判斷結果
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Seat {
    pub seat: String,
    pub cabin: Option<String>,
    pub role: &'static str,
    pub alive: Fact,
    pub hands: Fact,
    pub free_since_min: Option<u64>,
    pub unread: Option<usize>,
    pub waiting_on: &'static str,
    pub eta_min: Option<u64>,
    pub dispatchable: &'static str,
    pub size: &'static str,
    pub reasons: Vec<String>,
    /// 資料時間:各來源裡最新的那個(秒)
    pub data_at: Option<u64>,
}

/// 收到但還沒讀、也還沒結的信
fn unread(dms: &[DmItem]) -> usize {
    dms.iter()
        .filter(|d| matches!(d.status.as_str(), "queued" | "delivered" | "hold_human"))
        .filter(|d| d.read_at.is_none() && d.done_at.is_none())
        .count()
}

/// 最新一封有艙與窗的信 ⇒ 這一席在哪
fn place(dms: &[DmItem]) -> Option<(String, String)> {
    dms.iter()
        .filter(|d| d.hub.is_some() && d.wid.is_some())
        .max_by_key(|d| d.sent_at)
        .and_then(|d| Some((d.hub.clone()?, d.wid.clone()?)))
}

/// 還在排隊、送不進去的信(對方在回合中才會這樣)
fn queued(dms: &[DmItem]) -> bool {
    dms.iter()
        .any(|d| d.status == "queued" && d.done_at.is_none())
}

fn short_cabin(hub: &str) -> String {
    hub.rsplit('-').next().unwrap_or(hub).to_string()
}

/// 一席的事實與結論
pub fn judge(src: &dyn Source, seat: &str, now: u64, lease: &Known<Option<String>>) -> Seat {
    let role = if seat == COORDINATOR {
        "coordinator"
    } else {
        "dev"
    };
    let sn = src.seat_now(seat);
    let beat = src.beat(seat);
    let dms = src.dms(seat);
    let mut reasons = Vec::new();

    let (unread_n, where_) = match &dms {
        Known::Known(d) => (Some(unread(d)), place(d)),
        Known::Unknown(e) => {
            reasons.push(format!("dm queue: {e}"));
            (None, None)
        }
    };
    let activity: Known<Option<u64>> = match &where_ {
        None => Known::Unknown("no dm with a cabin and window yet".into()),
        Some((hub, wid)) => match src.windows(hub) {
            Known::Known(w) => Known::Known(w.get(wid).copied()),
            Known::Unknown(e) => Known::Unknown(e),
        },
    };
    let sn_at = match &sn {
        Known::Known(s) => s.at,
        Known::Unknown(_) => None,
    };
    let last = [beat, sn_at, activity_time(&activity)]
        .into_iter()
        .flatten()
        .max();

    // alive
    let alive = match (&activity, unread_n, last) {
        (Known::Known(None), _, _) => Fact::new("no", "window gone"),
        (_, Some(u), _) if u >= UNREAD_LOST => Fact::new("no", format!("unread {u}")),
        (_, _, Some(t)) if now.saturating_sub(t) > LOST_HOURS * 3600 => Fact::new(
            "no",
            format!("no activity for {} min", now.saturating_sub(t) / 60),
        ),
        (Known::Known(Some(_)), Some(u), Some(_)) => {
            Fact::new("yes", format!("window ok, unread {u}"))
        }
        (Known::Unknown(e), _, _) => Fact::new("unknown", format!("window: {e}")),
        _ => Fact::new("unknown", "dm queue unreadable"),
    };

    // hands
    let quiet_min = activity_time(&activity).map(|t| now.saturating_sub(t) / 60);
    let is_queued = matches!(&dms, Known::Known(d) if queued(d));
    let (hands, free_since_min) = match (&activity, quiet_min) {
        (Known::Known(Some(_)), Some(_)) if is_queued => {
            (Fact::new("in_turn", "a dm is still queued for it"), None)
        }
        (Known::Known(Some(_)), Some(q)) if q >= HANDS_FREE_MIN => (
            Fact::new("free", format!("window quiet {q} min, no dm queued")),
            Some(q),
        ),
        (Known::Known(Some(_)), Some(q)) => (
            Fact::new("in_turn", format!("window output {q} min ago")),
            None,
        ),
        (Known::Known(None), _) => (Fact::new("unknown", "window gone"), None),
        _ => (Fact::new("unknown", alive.why.clone()), None),
    };

    // waiting_on
    let holds_lease = matches!(lease, Known::Known(Some(o)) if o == seat);
    let waiting_on = if holds_lease {
        "receipt"
    } else {
        match &sn {
            Known::Known(s) => match s.state.as_deref() {
                Some("decide") => "human",
                Some("reply") => "seat",
                _ => "none",
            },
            Known::Unknown(e) => {
                reasons.push(format!("seat-now: {e}"));
                "unknown"
            }
        }
    };

    // conclusions
    let dispatchable = if role == "coordinator" {
        reasons.push("coordinator: not development capacity".into());
        "no"
    } else if alive.v == "no" {
        "no"
    } else if alive.v == "unknown" || hands.v == "unknown" {
        "unknown"
    } else if hands.v != "free" {
        "no"
    } else {
        "yes"
    };
    let size = match (dispatchable, waiting_on) {
        ("yes", "receipt") => "review",
        ("yes", "ci" | "human" | "seat") => "slice",
        ("yes", "none") => "mainline",
        _ => "none",
    };
    let data_at = last;
    Seat {
        seat: seat.to_string(),
        cabin: where_.as_ref().map(|(h, _)| short_cabin(h)),
        role,
        alive,
        hands,
        free_since_min,
        unread: unread_n,
        waiting_on,
        eta_min: None,
        dispatchable,
        size,
        reasons,
        data_at,
    }
}

fn activity_time(a: &Known<Option<u64>>) -> Option<u64> {
    match a {
        Known::Known(t) => *t,
        Known::Unknown(_) => None,
    }
}

/// 全部的席
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Report {
    pub as_of: u64,
    pub complete: bool,
    pub errors: Vec<String>,
    pub seats: Vec<Seat>,
}

impl Report {
    pub fn lost(&self) -> Vec<String> {
        self.seats
            .iter()
            .filter(|s| s.alive.v == "no")
            .map(|s| s.seat.clone())
            .collect()
    }
}

pub fn report(src: &dyn Source, now: u64) -> Report {
    let mut errors = Vec::new();
    let codes = match src.seats(now) {
        Ok(c) => c,
        Err(e) => {
            return Report {
                as_of: now,
                complete: false,
                errors: vec![format!("seat list: {e}")],
                seats: Vec::new(),
            }
        }
    };
    let lease = src.lease_owner(now);
    if let Known::Unknown(e) = &lease {
        errors.push(format!("lease: {e}"));
    }
    let seats: Vec<Seat> = codes.iter().map(|c| judge(src, c, now, &lease)).collect();
    let complete = errors.is_empty()
        && seats
            .iter()
            .all(|s| s.alive.v != "unknown" && s.hands.v != "unknown");
    Report {
        as_of: now,
        complete,
        errors,
        seats,
    }
}

/// 秒 → `2026-11-15T00:20:00.000Z`
pub fn iso(t: u64) -> String {
    let days = t / 86_400;
    let s = t % 86_400;
    // civil from days (Howard Hinnant)
    let z = days as i64 + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}.000Z",
        s / 3600,
        s / 60 % 60,
        s % 60
    )
}

pub fn to_json(r: &Report, idle_log: &str) -> Value {
    let seats: Vec<Value> = r
        .seats
        .iter()
        .map(|s| {
            json!({
                "seat": s.seat, "cabin": s.cabin, "role": s.role,
                "alive": {"v": s.alive.v, "why": s.alive.why},
                "hands": {"v": s.hands.v, "free_since_min": s.free_since_min, "why": s.hands.why},
                "capacity": {"usage_pct": Value::Null, "unread": s.unread,
                             "wip_unpushed": Value::Null, "open_prs": Value::Null, "on_deck": Value::Null},
                "waiting_on": {"v": s.waiting_on, "eta_min": s.eta_min},
                "dispatchable": s.dispatchable, "size": s.size, "first": [],
                "reasons": s.reasons,
                "data_at": s.data_at.map(iso),
            })
        })
        .collect();
    json!({
        "schema": SCHEMA, "as_of": iso(r.as_of), "complete": r.complete, "errors": r.errors,
        "seats": seats, "lost": r.lost(), "idle_log": {"file": idle_log},
    })
}

/// 閒置指標:每席一行 `{seat, at, hands, waiting_on, free_since_min}`
pub fn idle_lines(r: &Report) -> String {
    let mut out = String::new();
    for s in &r.seats {
        let v = json!({"seat": s.seat, "at": iso(r.as_of), "hands": s.hands.v,
                       "waiting_on": s.waiting_on, "free_since_min": s.free_since_min});
        out.push_str(&v.to_string());
        out.push('\n');
    }
    out
}

/// 追加到 idle.jsonl(一次寫入;aib-ledger 接上前的暫用追加)
pub fn append_idle(path: &Path, lines: &str) -> std::io::Result<()> {
    use std::io::Write;
    if lines.is_empty() {
        return Ok(());
    }
    if let Some(d) = path.parent() {
        std::fs::create_dir_all(d)?;
    }
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)?;
    f.write_all(lines.as_bytes())
}

/// 一行一席的文字版
pub fn to_text(r: &Report) -> String {
    let mut out = String::new();
    for s in &r.seats {
        out.push_str(&format!(
            "{:<6} {:<12} alive={:<7} hands={:<7} unread={:<4} waiting={:<7} dispatch={:<7} size={}\n",
            s.seat,
            s.role,
            s.alive.v,
            s.hands.v,
            s.unread.map_or("?".into(), |u| u.to_string()),
            s.waiting_on,
            s.dispatchable,
            s.size,
        ));
    }
    let lost = r.lost();
    if !lost.is_empty() {
        out.push_str(&format!("失聯:{}\n", lost.join("、")));
    }
    if !r.complete {
        out.push_str("有來源讀不到、結果不完整");
        if !r.errors.is_empty() {
            out.push_str(&format!(":{}", r.errors.join("; ")));
        }
        out.push('\n');
    }
    out
}

// ---------- 真的來源 ----------

/// 讀 `<state root>` 底下的檔與 tmux
pub struct FsSource {
    pub root: PathBuf,
}

fn num(v: &Value) -> Option<u64> {
    v.as_u64().or_else(|| v.as_f64().map(|f| f as u64))
}

fn read_json(p: &Path) -> Result<Value, String> {
    let t = std::fs::read_to_string(p).map_err(|e| format!("{}: {}", p.display(), e.kind()))?;
    serde_json::from_str(&t).map_err(|e| format!("{}: {e}", p.display()))
}

/// 席碼:5 碼小寫英數
pub fn is_seat_code(s: &str) -> bool {
    s.len() == 5
        && s.chars()
            .all(|c| c.is_ascii_digit() || c.is_ascii_lowercase())
}

impl Source for FsSource {
    fn seats(&self, now: u64) -> Result<Vec<String>, String> {
        let dir = self.root.join("state").join("seat-now");
        let rd = std::fs::read_dir(&dir).map_err(|e| format!("{}: {}", dir.display(), e.kind()))?;
        let mut out = Vec::new();
        for e in rd.filter_map(Result::ok) {
            let name = e.file_name().to_string_lossy().into_owned();
            let Some(code) = name.strip_suffix(".json") else {
                continue;
            };
            if !is_seat_code(code) {
                continue;
            }
            let fresh = read_json(&e.path())
                .ok()
                .and_then(|v| num(&v["at"]))
                .is_some_and(|at| now.saturating_sub(at) <= LIST_DAYS * 86_400);
            if fresh {
                out.push(code.to_string());
            }
        }
        out.sort();
        Ok(out)
    }

    fn seat_now(&self, seat: &str) -> Known<SeatNow> {
        let p = self
            .root
            .join("state/seat-now")
            .join(format!("{seat}.json"));
        match read_json(&p) {
            Ok(v) => Known::Known(SeatNow {
                at: num(&v["at"]),
                state: v["state"].as_str().map(str::to_string),
            }),
            Err(e) => Known::Unknown(e),
        }
    }

    fn beat(&self, seat: &str) -> Option<u64> {
        let p = self
            .root
            .join("state/seat-now")
            .join(format!("{seat}.beat"));
        read_json(&p).ok().and_then(|v| num(&v["at"]))
    }

    fn dms(&self, seat: &str) -> Known<Vec<DmItem>> {
        let dir = self.root.join("state/dm-queue").join(seat);
        let rd = match std::fs::read_dir(&dir) {
            Ok(r) => r,
            // no queue folder: no dm ever came; an empty queue, not unknown
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Known::Known(Vec::new()),
            Err(e) => return Known::Unknown(format!("{}: {}", dir.display(), e.kind())),
        };
        let mut out = Vec::new();
        for e in rd.filter_map(Result::ok) {
            let name = e.file_name().to_string_lossy().into_owned();
            if !(name.starts_with("q_") && name.ends_with(".json")) {
                continue;
            }
            // a file being replaced right now may not parse: skip it, the next run sees it
            let Ok(v) = read_json(&e.path()) else {
                continue;
            };
            let status = v["status"].as_str().unwrap_or("").to_string();
            if status == "archived" {
                continue;
            }
            out.push(DmItem {
                status,
                sent_at: num(&v["sent_at"]).or_else(|| num(&v["ts"])).unwrap_or(0),
                read_at: num(&v["read_at"]),
                done_at: num(&v["done_at"]),
                hub: v["hub"].as_str().map(str::to_string),
                wid: v["wid"].as_str().map(str::to_string),
            });
        }
        Known::Known(out)
    }

    fn windows(&self, hub: &str) -> Known<BTreeMap<String, u64>> {
        let args = [
            "-L",
            hub,
            "list-windows",
            "-a",
            "-F",
            "#{window_id} #{window_activity}",
        ]
        .map(String::from);
        match aib_ghread::run_fixed("tmux", &args, TMUX_TIMEOUT) {
            Ok(b) => Known::Known(parse_windows(&String::from_utf8_lossy(&b))),
            Err(e) => Known::Unknown(format!("tmux {hub}: {e}")),
        }
    }

    fn lease_owner(&self, now: u64) -> Known<Option<String>> {
        let p = self.root.join("state").join("host-lease.json");
        match std::fs::read_to_string(&p) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Known::Known(None),
            Err(e) => Known::Unknown(format!("{}: {}", p.display(), e.kind())),
            Ok(t) => match serde_json::from_str::<Value>(&t) {
                Err(e) => Known::Unknown(format!("{}: {e}", p.display())),
                Ok(v) => Known::Known(lease_from(&v, now)),
            },
        }
    }
}

/// `@12 1794672801` 一行一窗
pub fn parse_windows(text: &str) -> BTreeMap<String, u64> {
    text.lines()
        .filter_map(|l| {
            let (id, t) = l.trim().split_once(' ')?;
            Some((id.to_string(), t.trim().parse().ok()?))
        })
        .collect()
}

/// host-lease.json:`owner` 是 `@席碼`,`expiry_epoch` 過了就不算
pub fn lease_from(v: &Value, now: u64) -> Option<String> {
    let owner = v["owner"].as_str()?.trim_start_matches('@');
    let live = num(&v["expiry_epoch"]).is_some_and(|e| e > now);
    (live && is_seat_code(owner)).then(|| owner.to_string())
}

/// `aib-drive seats [--json] [--state-root DIR]`:整合時接到 main.rs。
/// rc 0 完整 · 2 用法 · 3 有來源讀不到。
pub fn run(args: &[String], now: u64) -> (i32, String) {
    let mut as_json = false;
    let mut root: Option<String> = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--json" => as_json = true,
            "--state-root" => match it.next() {
                Some(v) => root = Some(v.clone()),
                None => return (2, "--state-root needs a folder\n".into()),
            },
            other => return (2, format!("unknown argument {other:?}\n")),
        }
    }
    let Some(root) = aib_expect::state_root(root.as_deref()) else {
        return (3, "no state root (HOME / USERPROFILE unset)\n".into());
    };
    let idle = root.join("state").join("drive").join("idle.jsonl");
    let src = FsSource { root };
    let r = report(&src, now);
    let mut out = if as_json {
        let mut s = to_json(&r, &idle.display().to_string()).to_string();
        s.push('\n');
        s
    } else {
        to_text(&r)
    };
    if let Err(e) = append_idle(&idle, &idle_lines(&r)) {
        out.push_str(&format!("idle log not written: {}\n", e.kind()));
    }
    (if r.complete { 0 } else { 3 }, out)
}
