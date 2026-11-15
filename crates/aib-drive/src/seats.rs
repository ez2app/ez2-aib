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
//! - 座艙進度樹 `cabin-plan/data.json`:節點的 owner / status(審過未推、還沒開始的);
//! - `gh pr list`:開著的 PR 的分支名(分支名裡有席碼那一段就算那席的);
//! - `state/seat-info-cache/<席>.json`:座艙 dev 欄算好的工作樹(ahead / dirty)。
//!
//! sig(519d0 00:10):alive + hands + unread + waiting_on。
//! sig-b(驗收表 B3 / B7):`wip_unpushed`、`open_prs`、`planned` 與 `first`(IFACE 四個代碼)。
//! `on_deck`(合併隊下一張的作者)今天沒有來源 ⇒ null。
//! `usage_pct` 今天沒有來源 ⇒ 一律 null,不猜。讀不到的容量 ⇒ null + errors,不擋派工。

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
/// 一次 gh 呼叫最多等這麼久
pub const GH_TIMEOUT: Duration = Duration::from_secs(20);
/// 進度樹裡「審過未推」的狀態字
pub const WIP_STATUS: &str = "審過未推";
/// 進度樹裡「排在這席、還沒開始」的狀態字
pub const ON_DECK_STATUS: [&str; 3] = ["未開始", "已同意未做", "已定未做"];
/// `first_detail` 最多列幾件
pub const FIRST_MAX: usize = 6;
/// `first` 只能用的代碼(IFACE §1),也是列的順序
pub const FIRST_CODES: [&str; 4] = ["plan_node", "memory_tidy", "handoff", "clean_target"];
/// plan_node:這席名下、做完還沒實查的節點狀態(實查完改「實查通過 / 實查不通過」是收尾)
pub const PLAN_UNVERIFIED: &str = "未驗";
/// memory_tidy:席位記憶 `memory/<席>/context.md` 超過這麼多行(`aib memory tidy` apply 的上限,超過就擋)
pub const MEMORY_LINES_MAX: usize = 90;
/// 沒有比 main 多的 commit、乾淨、最後 commit 超過這麼多小時的樹 ⇒ 可收(剛開的新樹不算)
pub const CLEAN_AFTER_H: u64 = 2;
/// 進度樹預設位置(state root 底下)
pub const PLAN_FILE: &str = "pending/aib2-a63ab-1107/cabin-plan/data.json";

/// 進度樹的一個節點(用得到的欄位)
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PlanNode {
    pub id: String,
    pub owner: Option<String>,
    pub status: Option<String>,
}

/// 一棵工作樹(座艙 dev 欄的快取)
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Tree {
    pub path: String,
    pub branch: String,
    /// 比 main 多幾個 commit
    pub ahead: u64,
    /// 沒 commit 的檔數
    pub dirty: u64,
    /// 最後一個 commit 的時間(秒)
    pub last_ct: Option<u64>,
}

/// 可收的樹:沒有比 main 多的 commit、乾淨、而且安靜超過 CLEAN_AFTER_H(剛開的新樹不算)
pub fn stale_clean(t: &Tree, now: u64) -> bool {
    t.ahead == 0
        && t.dirty == 0
        && t.last_ct
            .is_some_and(|c| now.saturating_sub(c) >= CLEAN_AFTER_H * 3600)
}

/// 讀到了 / 讀不到(附原因)
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Known<T> {
    Known(T),
    Unknown(String),
}

impl<T> Known<T> {
    /// 借用裡面的值,原因照抄
    pub fn as_ref_known(&self) -> Known<&T> {
        match self {
            Known::Known(v) => Known::Known(v),
            Known::Unknown(e) => Known::Unknown(e.clone()),
        }
    }
    /// 讀到了就換成 `f(值)`
    pub fn map<U>(self, f: impl FnOnce(T) -> U) -> Known<U> {
        match self {
            Known::Known(v) => Known::Known(f(v)),
            Known::Unknown(e) => Known::Unknown(e),
        }
    }
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
    /// 進度樹的節點(一輪讀一次)
    fn plan_nodes(&self) -> Known<Vec<PlanNode>> {
        Known::Unknown("no plan source".into())
    }
    /// 開著的 PR 的分支名(一輪讀一次)
    fn open_pr_heads(&self) -> Known<Vec<String>> {
        Known::Unknown("no gh source".into())
    }
    /// 席位檔:席碼 → (tmux socket, 視窗 id)(一輪讀一次)
    fn seat_windows(&self) -> Known<BTreeMap<String, (String, String)>> {
        Known::Unknown("no seat-file source".into())
    }
    /// 這一席的工作樹
    fn trees(&self, _seat: &str) -> Known<Vec<Tree>> {
        Known::Unknown("no worktree source".into())
    }
    /// The seat-now file is not there at all (never written, or removed when the seat
    /// closed). Unreadable for another reason is not gone (87167 M1 on 1cbf03ba).
    fn seat_now_gone(&self, _seat: &str) -> bool {
        false
    }
    /// 席位記憶 `memory/<席>/context.md` 的行數
    fn memory_lines(&self, _seat: &str) -> Known<usize> {
        Known::Unknown("no memory source".into())
    }
    /// 交接開了頭沒寫完:最後一次 collect 比最後一張交接收據新 ⇒ Some(collect 時間);沒有 ⇒ None
    fn handoff_open(&self, _seat: &str) -> Known<Option<u64>> {
        Known::Unknown("no handoff source".into())
    }
}

/// 一輪只讀一次、每席共用的來源
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Shared {
    pub lease: Known<Option<String>>,
    pub nodes: Known<Vec<PlanNode>>,
    pub prs: Known<Vec<String>>,
    pub wins: Known<BTreeMap<String, (String, String)>>,
}

impl Shared {
    pub fn read(src: &dyn Source, now: u64) -> Self {
        Shared {
            lease: src.lease_owner(now),
            nodes: src.plan_nodes(),
            prs: src.open_pr_heads(),
            wins: src.seat_windows(),
        }
    }
}

/// 分支名用 `-` `/` 切開,有一段正好是席碼 ⇒ 那席的
/// (`feat/drive-sig-b433b-1115`、`integ-src/dmqrecv2-1ced7`)
pub fn branch_of_seat(branch: &str, seat: &str) -> bool {
    branch.split(['-', '/']).any(|t| t == seat)
}

/// 這席名下、狀態符合的節點 id(照進度樹順序)
fn owned(nodes: &[PlanNode], seat: &str, want: impl Fn(&str) -> bool) -> Vec<String> {
    nodes
        .iter()
        .filter(|n| n.owner.as_deref() == Some(seat))
        .filter(|n| n.status.as_deref().is_some_and(&want))
        .map(|n| n.id.clone())
        .collect()
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
    /// 今天沒有來源,一律 None
    pub usage_pct: Option<u8>,
    /// 進度樹裡這席「審過未推」的節點(讀不到 = None)
    pub wip_unpushed: Option<Vec<String>>,
    /// 開著的 PR 數(讀不到 = None)
    pub open_prs: Option<usize>,
    /// 進度樹裡排在這席、還沒開始的節點(讀不到 = None)
    pub planned: Option<Vec<String>>,
    /// 合併隊下一張的作者(IFACE §1);今天沒有來源 ⇒ None
    pub on_deck: Option<bool>,
    /// 閒下來先做的收尾,只用 [`FIRST_CODES`](讀得到才列)
    pub first: Vec<&'static str>,
    /// 給人看的明細:`dm` / `commit` / `push` / `tidy`(不含新工作)
    pub first_detail: Vec<String>,
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
pub fn judge(src: &dyn Source, seat: &str, now: u64, shared: &Shared) -> Seat {
    let lease = &shared.lease;
    let role = if seat == COORDINATOR {
        "coordinator"
    } else {
        "dev"
    };
    let sn = src.seat_now(seat);
    let beat = src.beat(seat);
    let dms = src.dms(seat);
    let mut reasons = Vec::new();

    let (unread_n, by_dm) = match &dms {
        Known::Known(d) => (Some(unread(d)), place(d)),
        Known::Unknown(e) => {
            reasons.push(format!("dm queue: {e}"));
            (None, None)
        }
    };
    // 7b81e L1: the seat file is the record; the latest dm is only a fallback
    let where_ = match shared.wins.as_ref_known().map(|m| m.get(seat).cloned()) {
        Known::Known(Some(w)) => {
            if by_dm.as_ref().is_some_and(|d| d != &w) {
                reasons.push(format!(
                    "seat file says {} {}, latest dm says otherwise",
                    w.0, w.1
                ));
            }
            Some(w)
        }
        Known::Known(None) => by_dm,
        Known::Unknown(e) => {
            if by_dm.is_none() {
                reasons.push(format!("seat file: {e}"));
            }
            by_dm
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

    // 87167 M1 on 1cbf03ba: a seat listed only from its dm queue (B1) has no seat-now file,
    // or one not written for LIST_DAYS, and no beat that new either. Its cabin may be gone
    // and its queue empty, so window and unread cannot say; not writing seat-now for two
    // days is itself the known fact that it is not working.
    let stale = |t: u64| now.saturating_sub(t) > LIST_DAYS * 86_400;
    let no_seat_now = match &sn {
        Known::Known(s) => s.at.is_none_or(stale),
        Known::Unknown(_) => src.seat_now_gone(seat),
    } && beat.is_none_or(stale);

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
        _ if no_seat_now => Fact::new("no", format!("no seat-now for {LIST_DAYS} days")),
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
                // 7b81e L2: a lease we cannot read may be this seat's receipt
                _ if matches!(lease, Known::Unknown(_)) => "unknown",
                _ => "none",
            },
            Known::Unknown(e) => {
                reasons.push(format!("seat-now: {e}"));
                "unknown"
            }
        }
    };

    // capacity (B3): unreadable ⇒ None, never a guess
    let (wip, deck) = match &shared.nodes {
        Known::Known(n) => (
            Some(owned(n, seat, |s| s == WIP_STATUS)),
            Some(owned(n, seat, |s| ON_DECK_STATUS.contains(&s))),
        ),
        Known::Unknown(_) => (None, None),
    };
    let open_prs = match &shared.prs {
        Known::Known(p) => Some(p.iter().filter(|b| branch_of_seat(b, seat)).count()),
        Known::Unknown(_) => None,
    };
    let trees = src.trees(seat);
    if let Known::Unknown(e) = &trees {
        reasons.push(format!("worktrees: {e}"));
    }
    // first (B7): the four IFACE codes in that order, each only when its source is read
    let unverified = match &shared.nodes {
        Known::Known(n) => owned(n, seat, |s| s == PLAN_UNVERIFIED),
        Known::Unknown(_) => Vec::new(),
    };
    let memory = match src.memory_lines(seat) {
        Known::Known(n) => Some(n),
        Known::Unknown(e) => {
            reasons.push(format!("memory: {e}"));
            None
        }
    };
    let handoff = match src.handoff_open(seat) {
        Known::Known(h) => h,
        Known::Unknown(e) => {
            reasons.push(format!("handoff: {e}"));
            None
        }
    };
    let mut first = Vec::new();
    if !unverified.is_empty() {
        first.push("plan_node");
    }
    if memory.is_some_and(|n| n > MEMORY_LINES_MAX) {
        first.push("memory_tidy");
    }
    if handoff.is_some() {
        first.push("handoff");
    }
    if matches!(&trees, Known::Known(t) if t.iter().any(|t| stale_clean(t, now))) {
        first.push("clean_target");
    }
    let mut first_detail = first_things(unread_n, &trees, wip.as_deref().unwrap_or(&[]), now);
    first_detail.extend(closing_things(&unverified, memory, handoff));
    first_detail.truncate(FIRST_MAX);

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
    let wip_full = wip.as_ref().is_some_and(|w| w.len() >= WIP_MAX);
    if wip_full && dispatchable == "yes" {
        reasons.push(format!(
            "{WIP_MAX}+ reviewed slices not pushed: review only"
        ));
    }
    let size = match (dispatchable, waiting_on) {
        ("yes", _) if wip_full => "review",
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
        usage_pct: None,
        wip_unpushed: wip,
        open_prs,
        planned: deck,
        on_deck: None,
        first,
        first_detail,
        waiting_on,
        eta_min: None,
        dispatchable,
        size,
        reasons,
        data_at,
    }
}

/// 收尾明細(給人看),照這個順序:讀信 → commit → 推審過的 → 清已合的樹。
/// 新工作(下一個節點)不算收尾、不列(7b81e sig-b M2)。讀不到的來源就不列那一類。
pub fn first_things(
    unread: Option<usize>,
    trees: &Known<Vec<Tree>>,
    wip: &[String],
    now: u64,
) -> Vec<String> {
    let mut out = Vec::new();
    if let Some(u) = unread.filter(|u| *u > 0) {
        out.push(format!("dm: {u} unread"));
    }
    let trees: &[Tree] = match trees {
        Known::Known(t) => t,
        Known::Unknown(_) => &[],
    };
    for t in trees.iter().filter(|t| t.dirty > 0) {
        out.push(format!(
            "commit {}: {} files not committed",
            t.path, t.dirty
        ));
    }
    for id in wip {
        out.push(format!("push {id}: reviewed, not pushed"));
    }
    for t in trees.iter().filter(|t| stale_clean(t, now)) {
        out.push(format!("tidy {}: nothing beyond main", t.path));
    }
    out.truncate(FIRST_MAX);
    out
}

/// plan_node / memory_tidy / handoff 的明細(給人看),接在 [`first_things`] 後面
pub fn closing_things(
    unverified: &[String],
    memory: Option<usize>,
    handoff: Option<u64>,
) -> Vec<String> {
    let mut out = Vec::new();
    for id in unverified {
        out.push(format!(
            "plan {id}: {PLAN_UNVERIFIED}, verify and set its status"
        ));
    }
    if let Some(n) = memory.filter(|n| *n > MEMORY_LINES_MAX) {
        out.push(format!(
            "memory: context.md {n} lines (over {MEMORY_LINES_MAX}), tidy it"
        ));
    }
    if let Some(t) = handoff {
        out.push(format!(
            "handoff: collected at {}, no receipt after it",
            iso(t)
        ));
    }
    out
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
    /// 讀不到、只讓某些欄變 null 的來源(不影響 complete;JSON 裡寫進 errors,`partial: ` 開頭)
    pub partial: Vec<String>,
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
    let codes = match src.seats(now) {
        Ok(c) => c,
        Err(e) => {
            return Report {
                as_of: now,
                complete: false,
                errors: vec![format!("seat list: {e}")],
                partial: Vec::new(),
                seats: Vec::new(),
            }
        }
    };
    let shared = Shared::read(src, now);
    // IFACE r7 3: `complete` is the roster, alive and hands only. Every other source that
    // cannot be read leaves its column null (or waiting_on unknown) and is reported in
    // `errors` as `partial: …`; the round stays complete (7b81e sig-b L2).
    let mut partial = Vec::new();
    if let Known::Unknown(e) = &shared.lease {
        partial.push(format!("lease: {e}"));
    }
    if let Known::Unknown(e) = &shared.nodes {
        partial.push(format!("plan: {e}"));
    }
    if let Known::Unknown(e) = &shared.prs {
        partial.push(format!("open prs: {e}"));
    }
    if let Known::Unknown(e) = &shared.wins {
        partial.push(format!("seat files: {e}"));
    }
    let seats: Vec<Seat> = codes.iter().map(|c| judge(src, c, now, &shared)).collect();
    // a lost seat is a known fact; its hands cannot be read and do not matter
    // (b433b review of next f106a7f8 M1: one lost seat made every round incomplete)
    let facts_read = |s: &Seat| s.alive.v != "unknown" && s.hands.v != "unknown";
    let complete = seats.iter().filter(|s| s.alive.v != "no").all(facts_read);
    Report {
        as_of: now,
        complete,
        // hard errors are only the seat list (returned above); the rest is partial
        errors: Vec::new(),
        partial,
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

pub fn to_json(r: &Report, idle_log: &str, newly_free: &[String]) -> Value {
    let errors: Vec<String> = r
        .errors
        .iter()
        .cloned()
        .chain(r.partial.iter().map(|p| format!("partial: {p}")))
        .collect();
    let seats: Vec<Value> = r
        .seats
        .iter()
        .map(|s| {
            json!({
                "seat": s.seat, "cabin": s.cabin, "role": s.role,
                "alive": {"v": s.alive.v, "why": s.alive.why},
                "hands": {"v": s.hands.v, "free_since_min": s.free_since_min, "why": s.hands.why},
                "capacity": {"usage_pct": s.usage_pct, "unread": s.unread,
                             "wip_unpushed": s.wip_unpushed.as_ref().map(Vec::len),
                             "wip_nodes": s.wip_unpushed,
                             "open_prs": s.open_prs,
                             "on_deck": s.on_deck,
                             "planned": s.planned.as_ref().map(Vec::len),
                             "planned_nodes": s.planned},
                "waiting_on": {"v": s.waiting_on, "eta_min": s.eta_min},
                "dispatchable": s.dispatchable, "size": s.size, "first": s.first,
                "first_detail": s.first_detail,
                "reasons": s.reasons,
                "data_at": s.data_at.map(iso),
            })
        })
        .collect();
    json!({
        "schema": SCHEMA, "as_of": iso(r.as_of), "complete": r.complete, "errors": errors,
        "seats": seats, "lost": r.lost(), "newly_free": newly_free,
        "idle_log": {"file": idle_log},
    })
}

/// idle.jsonl 每席最後一筆的 hands(A4 去重的狀態就是這個檔)
pub fn last_hands(idle_text: &str) -> BTreeMap<String, String> {
    let mut out = BTreeMap::new();
    for l in idle_text.lines() {
        let Ok(v) = serde_json::from_str::<Value>(l) else {
            continue;
        };
        if let (Some(s), Some(h)) = (v["seat"].as_str(), v["hands"].as_str()) {
            out.insert(s.to_string(), h.to_string());
        }
    }
    out
}

/// A4:這一輪「手空滿 5 分」、上一輪還不是 free 的席(每段空檔只出現一次)。
/// 統籌席不列。上一輪沒有紀錄 = 第一次看到,也算剛變空。
pub fn newly_free(prev: &BTreeMap<String, String>, r: &Report) -> Vec<String> {
    r.seats
        .iter()
        .filter(|s| s.role != "coordinator" && s.hands.v == "free")
        .filter(|s| prev.get(&s.seat).map(String::as_str) != Some("free"))
        .map(|s| s.seat.clone())
        .collect()
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
            "{:<6} {:<12} alive={:<7} hands={:<7} unread={:<4} wip={:<3} prs={:<3} planned={:<3} waiting={:<7} dispatch={:<7} size={}\n",
            s.seat,
            s.role,
            s.alive.v,
            s.hands.v,
            s.unread.map_or("?".into(), |u| u.to_string()),
            s.wip_unpushed.as_ref().map_or("?".into(), |w| w.len().to_string()),
            s.open_prs.map_or("?".into(), |p| p.to_string()),
            s.planned.as_ref().map_or("?".into(), |d| d.len().to_string()),
            s.waiting_on,
            s.dispatchable,
            s.size,
        ));
        if s.dispatchable == "yes" {
            for f in &s.first_detail {
                out.push_str(&format!("       先做:{f}\n"));
            }
        }
    }
    if !r.partial.is_empty() {
        out.push_str(&format!("部分欄讀不到:{}\n", r.partial.join("; ")));
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

/// 讀 `<state root>` 底下的檔、tmux 與 gh
pub struct FsSource {
    pub root: PathBuf,
    /// 進度樹檔(預設 `<root>/PLAN_FILE`)
    pub plan: PathBuf,
    /// `gh -R` 的 OWNER/NAME,或本機 clone 的資料夾(從 origin 解出 OWNER/NAME);None = gh 照目前資料夾找倉
    pub repo: Option<String>,
}

impl FsSource {
    pub fn new(root: PathBuf) -> Self {
        FsSource {
            plan: root.join(PLAN_FILE),
            root,
            repo: None,
        }
    }
}

/// 席位檔 `instances/<艙>/seats/w*.json`:席碼 = session_uuid 末 5 碼。
/// 是這席的 ⇒ (最後使用時間, (socket, window_id))
pub fn seat_file_window(v: &Value, seat: &str) -> Option<(u64, (String, String))> {
    let uuid = v["session_uuid"].as_str()?;
    if !uuid.ends_with(seat) || !is_seat_code(seat) {
        return None;
    }
    let t = num(&v["last_used_epoch"])
        .or_else(|| num(&v["started_epoch"]))
        .unwrap_or(0);
    Some((
        t,
        (
            v["socket"].as_str()?.to_string(),
            v["window_id"].as_str()?.to_string(),
        ),
    ))
}

/// 進度樹 `{"nodes": [{"id", "owner", "status"}, …]}`
pub fn parse_plan(v: &Value) -> Result<Vec<PlanNode>, String> {
    let nodes = v["nodes"].as_array().ok_or("no nodes array")?;
    Ok(nodes
        .iter()
        .filter_map(|n| {
            Some(PlanNode {
                id: n["id"].as_str()?.to_string(),
                owner: n["owner"].as_str().map(str::to_string),
                status: n["status"].as_str().map(str::to_string),
            })
        })
        .collect())
}

/// 座艙 dev 快取 `sections.dev.data.trees[]`
pub fn parse_trees(v: &Value) -> Result<Vec<Tree>, String> {
    let trees = v["sections"]["dev"]["data"]["trees"]
        .as_array()
        .ok_or("no sections.dev.data.trees")?;
    Ok(trees
        .iter()
        .filter_map(|t| {
            Some(Tree {
                path: t["path"].as_str()?.to_string(),
                branch: t["branch"].as_str().unwrap_or("").to_string(),
                ahead: num(&t["ahead"])?,
                dirty: num(&t["dirty"]).unwrap_or(0),
                last_ct: num(&t["last_ct"]),
            })
        })
        .collect())
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
        // B1: a seat whose seat-now file is gone (or was never written) but still gets dm
        // is listed too, so it can be judged lost (c477a / 2240e: 53 and 38 unread, never in
        // the list). The folder's time moves whenever a file in it is made, renamed or
        // removed (a dm landing, a close, a liveness write), so a closed seat that still gets
        // dm stays listed: that is the seat to report lost (87167 N2 on 1cbf03ba).
        let queues = self.root.join("state").join("dm-queue");
        if let Ok(rd) = std::fs::read_dir(&queues) {
            for e in rd.filter_map(Result::ok) {
                let code = e.file_name().to_string_lossy().into_owned();
                if !is_seat_code(&code) || out.contains(&code) {
                    continue;
                }
                let recent = e
                    .metadata()
                    .ok()
                    .filter(std::fs::Metadata::is_dir)
                    .and_then(|m| m.modified().ok())
                    .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                    .is_some_and(|t| now.saturating_sub(t.as_secs()) <= LIST_DAYS * 86_400);
                if recent {
                    out.push(code);
                }
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

    fn seat_now_gone(&self, seat: &str) -> bool {
        let p = self
            .root
            .join("state/seat-now")
            .join(format!("{seat}.json"));
        matches!(std::fs::symlink_metadata(&p), Err(e) if e.kind() == std::io::ErrorKind::NotFound)
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

    fn plan_nodes(&self) -> Known<Vec<PlanNode>> {
        match read_json(&self.plan).and_then(|v| parse_plan(&v)) {
            Ok(n) => Known::Known(n),
            Err(e) => Known::Unknown(e),
        }
    }

    fn open_pr_heads(&self) -> Known<Vec<String>> {
        let mut args: Vec<String> = [
            "pr",
            "list",
            "--state",
            "open",
            "--limit",
            "200",
            "--json",
            "headRefName",
            "--jq",
            ".[].headRefName",
        ]
        .map(String::from)
        .to_vec();
        if let Some(r) = &self.repo {
            match gh_repo(r) {
                Ok(name) => args.extend(["-R".to_string(), name]),
                Err(e) => return Known::Unknown(format!("--repo {r}: {e}")),
            }
        }
        match aib_ghread::run_fixed("gh", &args, GH_TIMEOUT) {
            Ok(b) => Known::Known(
                String::from_utf8_lossy(&b)
                    .lines()
                    .map(str::trim)
                    .filter(|l| !l.is_empty())
                    .map(str::to_string)
                    .collect(),
            ),
            Err(e) => Known::Unknown(format!("gh pr list: {e}")),
        }
    }

    fn seat_windows(&self) -> Known<BTreeMap<String, (String, String)>> {
        let dir = self.root.join("instances");
        let rd = match std::fs::read_dir(&dir) {
            Ok(r) => r,
            Err(e) => return Known::Unknown(format!("{}: {}", dir.display(), e.kind())),
        };
        let mut best: BTreeMap<String, (u64, (String, String))> = BTreeMap::new();
        for cabin in rd.filter_map(Result::ok) {
            let Ok(files) = std::fs::read_dir(cabin.path().join("seats")) else {
                continue;
            };
            for f in files.filter_map(Result::ok) {
                let name = f.file_name().to_string_lossy().into_owned();
                if !(name.starts_with('w') && name.ends_with(".json")) {
                    continue;
                }
                let Ok(v) = read_json(&f.path()) else {
                    continue;
                };
                let Some(seat) = v["session_uuid"]
                    .as_str()
                    .and_then(|u| u.get(u.len().saturating_sub(5)..))
                else {
                    continue;
                };
                if let Some((t, w)) = seat_file_window(&v, seat) {
                    if best.get(seat).is_none_or(|(bt, _)| t > *bt) {
                        best.insert(seat.to_string(), (t, w));
                    }
                }
            }
        }
        Known::Known(best.into_iter().map(|(k, (_, w))| (k, w)).collect())
    }

    fn trees(&self, seat: &str) -> Known<Vec<Tree>> {
        let p = self
            .root
            .join("state/seat-info-cache")
            .join(format!("{seat}.json"));
        match read_json(&p).and_then(|v| parse_trees(&v)) {
            Ok(t) => Known::Known(t),
            Err(e) => Known::Unknown(e),
        }
    }

    fn memory_lines(&self, seat: &str) -> Known<usize> {
        let p = self.root.join("memory").join(seat).join("context.md");
        match std::fs::read_to_string(&p) {
            Ok(t) => Known::Known(t.lines().count()),
            // a new seat has no memory yet: nothing to tidy (87167 L1 on 1cbf03ba)
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Known::Known(0),
            Err(e) => Known::Unknown(format!("{}: {}", p.display(), e.kind())),
        }
    }

    fn handoff_open(&self, seat: &str) -> Known<Option<u64>> {
        let dir = self.root.join("state/handoff");
        let collect = dir.join(format!("{seat}.collect.json"));
        let collected = match std::fs::read_to_string(&collect) {
            // never collected: no handoff was started
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Known::Known(None),
            Err(e) => return Known::Unknown(format!("{}: {}", collect.display(), e.kind())),
            Ok(t) => match serde_json::from_str::<Value>(&t)
                .ok()
                .and_then(|v| num_or_float(&v["ts"]))
            {
                Some(ts) => ts,
                None => return Known::Unknown(format!("{}: no ts", collect.display())),
            },
        };
        let receipts = dir.join(format!("{seat}.receipts.jsonl"));
        let last_receipt = match std::fs::read_to_string(&receipts) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
            Err(e) => return Known::Unknown(format!("{}: {}", receipts.display(), e.kind())),
            Ok(t) => last_receipt_ts(&t),
        };
        Known::Known(handoff_left_open(collected, last_receipt))
    }
}

/// collect 之後沒有收據 ⇒ 交接開了頭沒寫完(收據時間 ≥ collect 時間才算寫完)
pub fn handoff_left_open(collected: u64, last_receipt: Option<u64>) -> Option<u64> {
    match last_receipt {
        Some(r) if r >= collected => None,
        _ => Some(collected),
    }
}

/// `receipts.jsonl` 最後一筆讀得懂的 ts(秒,小數捨去);壞行跳過
pub fn last_receipt_ts(text: &str) -> Option<u64> {
    text.lines()
        .rev()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
        .find_map(|v| num_or_float(&v["ts"]))
}

/// 整數或小數秒(交接檔的 ts 是 `1794580402.166284`)
fn num_or_float(v: &Value) -> Option<u64> {
    num(v).or_else(|| {
        v.as_f64()
            .filter(|f| f.is_finite() && *f >= 0.0)
            .map(|f| f as u64)
    })
}

/// `--repo`:已經是 OWNER/NAME 就照用;是資料夾 ⇒ 讀它的 origin 網址解出 OWNER/NAME
/// (519d0 07:41 B3:seats 把本機路徑直接丟給 `gh -R`,gh 要 OWNER/REPO,open_prs 一直讀不到)
pub fn gh_repo(repo: &str) -> Result<String, String> {
    let p = Path::new(repo);
    if !p.is_dir() {
        return if is_owner_name(repo) {
            Ok(repo.to_string())
        } else {
            Err("not OWNER/NAME and not a folder".into())
        };
    }
    let args: Vec<String> = ["-C", repo, "remote", "get-url", "origin"]
        .map(String::from)
        .to_vec();
    let out = aib_ghread::run_fixed("git", &args, GH_TIMEOUT)
        .map_err(|e| format!("git remote get-url origin: {e}"))?;
    let url = String::from_utf8_lossy(&out).trim().to_string();
    owner_name_from_url(&url).ok_or_else(|| format!("origin {url:?} is not a GitHub OWNER/NAME"))
}

/// `OWNER/NAME`:兩段、各段只有英數與 `-` `_` `.`,不是 `.` / `..`
pub fn is_owner_name(s: &str) -> bool {
    let parts: Vec<&str> = s.split('/').collect();
    parts.len() == 2
        && parts.iter().all(|p| {
            !p.is_empty()
                && *p != "."
                && *p != ".."
                && p.chars()
                    .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
        })
}

/// GitHub 遠端網址 → OWNER/NAME:`git@github.com:o/n(.git)`、`https://github.com/o/n(.git)`、
/// `ssh://git@github.com/o/n(.git)`;別的主機或形狀 ⇒ None
pub fn owner_name_from_url(url: &str) -> Option<String> {
    let rest = url
        .strip_prefix("git@github.com:")
        .or_else(|| url.strip_prefix("https://github.com/"))
        .or_else(|| url.strip_prefix("ssh://git@github.com/"))?;
    let rest = rest.trim_end_matches('/');
    let rest = rest.strip_suffix(".git").unwrap_or(rest);
    is_owner_name(rest).then(|| rest.to_string())
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
    let mut plan: Option<String> = None;
    let mut repo: Option<String> = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let slot = match a.as_str() {
            "--json" => {
                as_json = true;
                continue;
            }
            "--state-root" => &mut root,
            "--plan" => &mut plan,
            "--repo" => &mut repo,
            other => return (2, format!("unknown argument {other:?}\n")),
        };
        match it.next() {
            Some(v) => *slot = Some(v.clone()),
            None => return (2, format!("{a} needs a value\n")),
        }
    }
    let Some(root) = aib_expect::state_root(root.as_deref()) else {
        return (3, "no state root (HOME / USERPROFILE unset)\n".into());
    };
    let idle = idle_path(&root);
    let mut src = FsSource::new(root);
    if let Some(p) = plan {
        src.plan = PathBuf::from(p);
    }
    src.repo = repo;
    run_with(&src, &idle, as_json, now)
}

/// `<state root>/drive/idle.jsonl`(IFACE r4 #3:狀態檔都在 observe 的 `<root>/drive/`)
pub fn idle_path(root: &Path) -> PathBuf {
    root.join("drive").join("idle.jsonl")
}

/// 一輪:判斷、輸出;只有 `--json`(observe / wake 那一輪)才追加 idle.jsonl,
/// 人手動看文字版只讀不寫,不會先吃掉「剛變空」(7b81e sig-b L1)。
pub fn run_with(src: &dyn Source, idle: &Path, as_json: bool, now: u64) -> (i32, String) {
    let mut r = report(src, now);
    // A4: the previous round is whatever idle.jsonl last says per seat
    let prev = last_hands(&std::fs::read_to_string(idle).unwrap_or_default());
    let fresh = newly_free(&prev, &r);
    if as_json {
        // a log that cannot be written goes into the JSON, never after it
        if let Err(e) = append_idle(idle, &idle_lines(&r)) {
            r.partial
                .push(format!("idle log not written: {}", e.kind()));
        }
        let mut s = to_json(&r, &idle.display().to_string(), &fresh).to_string();
        s.push('\n');
        return (if r.complete { 0 } else { 3 }, s);
    }
    let mut t = to_text(&r);
    if !fresh.is_empty() {
        t.push_str(&format!("剛變空:{}\n", fresh.join("、")));
    }
    (if r.complete { 0 } else { 3 }, t)
}
