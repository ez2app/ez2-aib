//! A page asks the owner's fingerprint (BOUNDARY-webonly-5d33a-1119 §4,
//! IFACE-webconsent-7ebca-1119 r1, CHECK-webconsent-5d33a-1119, a63 Andy 11-20): aibd opens a
//! ticket in its own memory, raises the fingerprint window with the key pairing already uses
//! (touch.rs, the Secure Enclave), the page polls the ticket's state, and right before acting
//! aibd checks the ticket once more against what it is about to do.
//!
//! Default off: only with [`SWITCH`]`=1`, `AIBD_PAIRING=touchid` and a key that checked out at
//! start; otherwise [`ask`] and [`state`] answer [`Asked::Unavailable`] /
//! [`State::Unavailable`] and [`approved_for`] is false. A caller that gets Unavailable refuses
//! with `owner_check_unavailable` (5d33a §0); nothing falls back to a page button. Only the
//! [`WIRED`] operations raise a window; the 1.x aib-consent queue is not used.
//!
//! - The category comes from the caller's fixed table, never from the page; SeatInput and
//!   Outbound are not page asks (5d33a 01:31).
//! - The window text is built from the ticket itself (no words from the page): the resource
//!   with [`aib_approve::text::is_bad_char`] characters removed, a path keeping its last 80.
//! - The parameters are kept only as their SHA-256; the check compares that, the operation and
//!   the resource, and a ticket is used once.

use aib_approve::category::Category;
use sha2::{Digest, Sha256};

/// The longest resource shown (characters; a path keeps its end, 5d33a L1).
pub const RESOURCE_MAX: usize = 80;

/// The refusal code for a caller when no fingerprint can be asked.
pub const UNAVAILABLE: &str = "owner_check_unavailable";

/// What the page wants done and the owner approves.
#[derive(Debug, Clone)]
pub struct Ask {
    /// From the caller's fixed table ([`page_category`]); never the page's word.
    pub category: Category,
    /// e.g. `worktree.open`, `seat.open_dgrs`, `decision.answer`.
    pub operation: &'static str,
    /// e.g. a repo path, a seat name, a card id (cleaned and cut for the window).
    pub resource: String,
    /// The exact parameters of the act; only their SHA-256 is kept.
    pub params: Vec<u8>,
    /// `page:<pair id>`: a record, not an identity.
    pub by: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Asked {
    Ticket(String),
    Unavailable(&'static str),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum State {
    Pending,
    Approved { at: u64, digest: String },
    Refused(String),
    Expired,
    Unavailable(&'static str),
}

/// A ticket as aib-consent keeps it: what [`approved_for`] compares.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Record {
    pub ticket: String,
    pub category: Category,
    pub operation: String,
    pub resource: String,
    pub params_sha256: String,
    pub approved: bool,
    pub expires_at: u64,
    pub used: bool,
}

/// The categories a page may ask (5d33a CHECK-webconsent): Command (open a dgrs seat, register a
/// repo, go on preview), ProjectRuling (a decision's answer, a version fixed), PageBind (pairing
/// confirmed again), WindowGrant (the dgrs switch), DeleteData, AutoUpdate. A waiting-box ticket
/// keeps its own.
pub fn page_category(c: Category) -> bool {
    matches!(
        c,
        Category::Command
            | Category::ProjectRuling
            | Category::PageBind
            | Category::WindowGrant
            | Category::DeleteData
            | Category::AutoUpdate
    )
}

/// The parameters' SHA-256, lower-case hex.
pub fn params_sha256(params: &[u8]) -> String {
    Sha256::digest(params)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// The resource as the window shows it: no invisible or layout characters, its last
/// [`RESOURCE_MAX`] characters.
pub fn clean_resource(s: &str) -> String {
    let kept: Vec<char> = s
        .chars()
        .filter(|c| !aib_approve::text::is_bad_char(*c))
        .collect();
    let from = kept.len().saturating_sub(RESOURCE_MAX);
    let tail: String = kept[from..].iter().collect();
    if from > 0 {
        format!("…{tail}")
    } else {
        tail
    }
}

/// What each operation is called in the window, in the owner's words (Andy 11-21 01:23: the
/// window said `repo.register` and no more). Fixed here; never a word of the request's.
pub const ACTIONS: [(&str, &str); 14] = [
    (crate::repos::OP_REGISTER, "登記倉"),
    (crate::repos::OP_OPEN, "開能改程式的席(帶倉)"),
    (crate::send_back::OP_SEND_BACK, "送回倉"),
    (crate::repo_push::OP, "推分支"),
    (crate::pr_open::OP, "開草稿 PR"),
    (crate::seat_trust::OP, "把席設為受信任"),
    (crate::seat_move::OP, "把舊席搬進網頁席"),
    (crate::seat_rank::OP, "改誰能標急的權級"),
    (crate::receipt_runner::OP, "到測試機跑一份收據"),
    (crate::test_host_add::OP_ADD, "加一台測試機"),
    (crate::test_host_add::OP_REMOVE, "收回一台測試機"),
    (crate::gh_token::OP, "存 GitHub 權杖"),
    (crate::provider_login::OP, "重新登入 AI"),
    (crate::drive_switch::OP, "打開自動推進派工"),
];

/// An operation's name in the window; one the table does not have is shown as it is.
pub fn action_words(operation: &str) -> &str {
    ACTIONS
        .iter()
        .find(|(op, _)| *op == operation)
        .map_or(operation, |(_, words)| *words)
}

/// Marks the window's own layout uses: none of them is shown from an object.
const OBJECT_MARKS: [char; 8] = ['|', '「', '」', '『', '』', '｜', '│', '¦'];
/// Of an object too long to show whole, this much of its start is kept (the name comes first).
const OBJECT_HEAD: usize = 24;

/// The object as the window shows it, between 「」 (5d33a PREREAD-touchwords L1): cleaned as
/// [`clean_resource`] cleans; none of the window's own marks; nothing that reads as a clock
/// or as the window's own "請在 … 前按" (a `:` between two digits and 「請在」 are broken with
/// `·`); at most [`RESOURCE_MAX`] characters and one `…`, its start and its end kept.
pub fn object_shown(s: &str) -> String {
    let mut kept: Vec<char> = s
        .chars()
        .filter(|c| !aib_approve::text::is_bad_char(*c))
        .map(|c| if OBJECT_MARKS.contains(&c) { ' ' } else { c })
        .collect();
    for i in 1..kept.len().saturating_sub(1) {
        if matches!(kept[i], ':' | '：') && kept[i - 1].is_numeric() && kept[i + 1].is_numeric() {
            kept[i] = '·';
        }
    }
    let text = kept.iter().collect::<String>().replace("請在", "請·在");
    let c: Vec<char> = text.chars().collect();
    if c.len() <= RESOURCE_MAX {
        return text;
    }
    let head: String = c[..OBJECT_HEAD].iter().collect();
    let tail: String = c[c.len() - (RESOURCE_MAX - OBJECT_HEAD)..].iter().collect();
    format!("{head}…{tail}")
}

/// One part of a plain sentence: a name, a branch or `owner/name` as aibd already checked it
/// when the ask was made. Read again here so that the sentence can only ever hold this shape.
fn piece(v: &serde_json::Value, key: &str) -> Option<String> {
    let t = v[key].as_str()?;
    let ok = (1..=100).contains(&t.len())
        && t.bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-' | b'/'));
    ok.then(|| t.to_owned())
}

/// A seat as people call it: the five characters after `seat_`.
fn seat_code(v: &serde_json::Value) -> Option<String> {
    let id = piece(v, "seat_id")?;
    let code: String = id.strip_prefix("seat_")?.chars().take(5).collect();
    (code.len() == 5).then_some(code)
}

/// The window's first line (Andy 11-21 01:44:「最上方 要用真人容易了解的簡易描述」): one
/// fixed sentence per operation, filled only from the ticket's own parameters, each piece a
/// checked shape or a number. None for an operation with no sentence, or when a piece is not
/// its shape: the line below still says everything.
pub fn plain_words(operation: &str, params: &[u8]) -> Option<String> {
    let v: serde_json::Value = serde_json::from_slice(params).ok()?;
    let n = |key: &str| v[key].as_u64();
    Some(match operation {
        crate::repos::OP_REGISTER => format!("登記這個資料夾當倉:{}", piece(&v, "name")?),
        // A11: a dgrs open with no repo, and a page's restart of a dgrs seat
        crate::repos::OP_OPEN => match v["kind"].as_str() {
            Some("open") => format!(
                "開一個完全權限的席({}),人格 {}",
                piece(&v, "provider")?,
                piece(&v, "persona_uid")?
            ),
            Some("restart") => format!(
                "重開完全權限的席 @{}({})",
                seat_code(&v)?,
                piece(&v, "provider")?
            ),
            _ => format!(
                "開一個能改程式的席,帶倉 {}({})",
                piece(&v, "repo")?,
                piece(&v, "provider")?
            ),
        },
        crate::persona_edit::OP_SET => crate::persona_edit::plain(&v)?,
        crate::send_back::OP_SEND_BACK => format!(
            "把席 {} 的 {} 個提交、{} 個檔送回 {}",
            seat_code(&v)?,
            n("commits")?,
            n("files")?,
            piece(&v, "repo")?
        ),
        crate::repo_push::OP => format!(
            "把 {} 的分支 {} 推上遠端",
            piece(&v, "repo")?,
            piece(&v, "branch")?
        ),
        crate::pr_open::OP => format!(
            "在 {} 開一張草稿 PR(分支 {} → {})",
            piece(&v, "slug")?,
            piece(&v, "branch")?,
            piece(&v, "base")?
        ),
        crate::test_host_add::OP_ADD | crate::test_host_add::OP_REMOVE => {
            let fp = v["fingerprint"].as_str()?;
            let fp_ok = fp.len() <= 60
                && fp.strip_prefix("SHA256:").is_some_and(|b| {
                    b.bytes()
                        .all(|c| c.is_ascii_alphanumeric() || c == b'+' || c == b'/')
                });
            let at = format!(
                "{}@{} 埠 {}(主機鑰匙 {})",
                piece(&v, "user")?,
                piece(&v, "hostname")?,
                n("port")?,
                fp_ok.then_some(fp)?
            );
            if operation == crate::test_host_add::OP_ADD {
                // 5d33a N1 (REVIEW-addtesthost ee27b561): the line's from= is said too; a line
                // without one, or with one the line writer would refuse, shows no window
                let from = v["line"]
                    .as_str()?
                    .split(",from=\"")
                    .nth(1)?
                    .split('"')
                    .next()
                    .filter(|f| crate::receipt_link::from_ok(f))?;
                format!("讓 aibd 的鑰匙在測試機 {at}只能跑收據,只收從 {from} 連進來的")
            } else {
                format!("從測試機 {at}收回 aibd 的收據鑰匙")
            }
        }
        crate::seat_trust::OP => {
            format!("讓席 {} 受信任 {} 小時", seat_code(&v)?, n("hours")?)
        }
        crate::seat_move::OP => format!(
            "把舊席 {} 搬進網頁席 {},寄它的信改送網頁席",
            piece(&v, "old_code").filter(|c| aib_store::seat_moves::code_ok(c))?,
            seat_code(&v)?
        ),
        crate::seat_rank::OP => {
            let rank = || v["rank"].as_str().and_then(crate::seat_rank::rank_words);
            match v["what"].as_str()? {
                "role" => format!("把角色 {} 的權級設為{}", piece(&v, "role_uid")?, rank()?),
                "grant" => format!(
                    "給席 {} 角色 {} 的權級",
                    seat_code(&v)?,
                    piece(&v, "role_uid")?
                ),
                "revoke" => format!(
                    "收回席 {} 角色 {} 的權級",
                    seat_code(&v)?,
                    piece(&v, "role_uid")?
                ),
                "perm" if v["act"].as_str() == Some(aib_store::ranks::URGENT) => {
                    format!("標急最低要{}", rank()?)
                }
                _ => return None,
            }
        }
        crate::gh_token::OP => format!(
            "把 GitHub 權杖存進這台,給 {} 推分支與開 PR 用",
            piece(&v, "slug")?
        ),
        crate::provider_login::OP => {
            format!("重新登入 {}(接著在瀏覽器授權)", piece(&v, "provider")?)
        }
        // D7a: the reason the owner typed is in the window's resource line (free text)
        crate::drive_switch::OP if v["to"] == "on" => {
            "打開自動推進派工:aibd 照自動推進的計畫寄派工給網頁席".to_owned()
        }
        _ => return None,
    })
}

/// The fingerprint window's words (DESIGN-touch-words-7b81e-1121 r1; Andy 11-21 01:23, 01:44):
/// on top one plain sentence ([`plain_words`], or the operation's name) and until when; below
/// it what the window always said (the operation, the object, the ticket, the parameters'
/// hash). `until` is aibd's own clock ([`until_words`]); only the object carries free words of
/// the request, and [`object_shown`] keeps it from saying anything of the window's own.
pub fn window_text(ask: &Ask, ticket: &str, until: &str) -> String {
    let p = params_sha256(&ask.params);
    format!(
        "{}({until},過了這張就作廢)對照碼 {}\nAIB 網頁要求:{}|對象:{}|單號 {}|參數 {}",
        top_words(ask),
        pair_code(ticket),
        ask.operation,
        object_shown(&ask.resource),
        ticket,
        &p[..12]
    )
}

/// The window's first words: the plain sentence, or the operation's name when it has none.
pub fn top_words(ask: &Ask) -> String {
    plain_words(ask.operation, &ask.params)
        .unwrap_or_else(|| action_words(ask.operation).to_owned())
}

/// Four characters of the ticket's hash, shown by the system's window and by the countdown
/// beside it (5d33a PREREAD-touchcountdown L1): the person presses only when the two agree.
/// The ticket is aibd's own random word; nothing of the request reaches it.
pub fn pair_code(ticket: &str) -> String {
    params_sha256(ticket.as_bytes())[..4].to_ascii_uppercase()
}

/// 「請在 01:23:44 前按」 for a ticket that expires at the unix second `expires_at`; the
/// seconds it lives when the machine's clock cannot be read as a time of day.
pub fn until_words(expires_at: u64) -> String {
    match crate::touch::clock_text(expires_at) {
        Some(t) => format!("請在 {t} 前按"),
        None => format!("請在 {TICKET_SECS} 秒內按"),
    }
}

/// The operations whose caller resends with the ticket and checks it with [`approved_for`]
/// (a63, Andy 11-20): registering a repo, opening a dgrs seat on one, trusting a seat, pushing
/// a branch, opening a pull request, sending a seat's work back to its repo. Every other page
/// ask stays [`UNAVAILABLE`] until its caller uses the ticket, so no window is raised for
/// nothing.
///
/// Also one receipt going to one test machine (`receipt.run`, asked by aibd's own runner).
///
/// One line an operation, each slice adding its own (0eed7 a82 on 936759b065, 519d0 11-21:
/// `repo.send_back` was merged without its line and every send back answered
/// `owner_check_unavailable`). `every_operation_constant_is_wired` reads the source: an
/// `operation: SOME_CONSTANT` anywhere in aibd that is not listed here fails it.
pub const WIRED: &[&str] = &[
    crate::repos::OP_REGISTER,
    crate::repos::OP_OPEN,
    crate::seat_trust::OP, // seat.trust
    crate::seat_move::OP,  // seat.move (G5 信橋, 5d33a PREREAD-g5-bridge M1)
    crate::seat_rank::OP,  // rank.set (誰能標急, 5d33a PREREAD-inboxaibd F r2)
    crate::repo_push::OP,
    crate::pr_open::OP,
    crate::send_back::OP_SEND_BACK, // repo.send_back (0eed7 a82)
    crate::gh_token::OP,            // repo.gh_token_set (DESIGN-gh-token-card)
    crate::seat_tick::OP_SET,
    crate::seat_tick::OP_RESUME,
    crate::provider_login::OP, // provider.login (重新登入)
    crate::drive_switch::OP,
    // one receipt going to one test machine: asked by aibd's own runner, which checks the
    // ticket itself before anything is sent (receipt_runner::TouchOwner)
    crate::receipt_runner::OP,
    // aibd's line written to / taken from a test machine through the owner's login, with the
    // real address and host key in the window (DESIGN-add-test-host-b4297-1121, 5d33a B1 L1)
    crate::test_host_add::OP_ADD,
    crate::test_host_add::OP_REMOVE,
    // A1: a persona card's sections changed from the built-in page (DESIGN-a1-edit-archive)
    crate::persona_edit::OP_SET,
    // aibd §7: a permission template change that turns an item on or raises the base
    crate::perm_tpl::OP,
];

/// How long a ticket lives: the owner answers and the page resends within it.
pub const TICKET_SECS: u64 = 120;

/// The switch: the owner's fingerprint for page asks, only with `AIBD_PAIRING=touchid` and a
/// checked key (default off: every ask stays [`UNAVAILABLE`]).
pub const SWITCH: &str = "AIBD_OWNER_TOUCH";

/// [`SWITCH`] is on only when it is exactly `1`.
pub fn switch_on(v: Option<String>) -> bool {
    v.as_deref() == Some("1")
}

/// What a preview's window starts with (5d33a PREREAD-pvtouch-rrmerge L1): with the switch
/// handed to the preview its windows look like the real one's, yet pushing a branch or opening
/// a pull request there acts on GitHub all the same; the owner is told which aibd asks.
pub const PREVIEW_MARK: &str = "預覽:";

/// [`PREVIEW_MARK`] for a preview aibd, nothing for the real one.
pub fn mark_for(preview: bool) -> &'static str {
    if preview {
        PREVIEW_MARK
    } else {
        ""
    }
}

static OWNER: std::sync::OnceLock<std::sync::Arc<Owner>> = std::sync::OnceLock::new();

/// Whether the owner check can be asked here (the switch on and the key checked at start).
pub fn available() -> bool {
    OWNER.get().is_some()
}

/// Called once at start when [`SWITCH`] is `1` and the fingerprint key checked out (the first
/// call wins); the windows share pairing's gate.
pub fn set_presence(p: crate::touch::SharedPresence, gate: std::sync::Arc<dyn WindowGate>) {
    let o = Owner::new(p, gate, unix_now).marked(mark_for(crate::preview::is_preview()));
    let _ = OWNER.set(std::sync::Arc::new(o));
    // the countdown beside the system's window (DESIGN-touch-countdown-7b81e-1121)
    crate::touch_hud::turn_on();
}

/// The one gate every fingerprint window of this aibd passes ([`crate::pairing::Pairing`]).
pub trait WindowGate: Send + Sync {
    fn open(&self) -> Result<(), &'static str>;
    fn closed(&self, yes: bool);
    /// How long until [`Self::open`] stops answering `pair_touch_limited`; none when it would not.
    fn wait(&self) -> Option<std::time::Duration> {
        None
    }
}

impl WindowGate for crate::pairing::Pairing {
    fn open(&self) -> Result<(), &'static str> {
        self.window_open()
    }
    fn wait(&self) -> Option<std::time::Duration> {
        self.touch_wait()
    }
    fn closed(&self, yes: bool) {
        self.window_closed(yes)
    }
}

/// Closes the window in the gate even when the window's thread panics (5d33a N1).
struct Closing {
    gate: std::sync::Arc<dyn WindowGate>,
    yes: bool,
}

impl Drop for Closing {
    fn drop(&mut self) {
        self.gate.closed(self.yes);
    }
}

pub(crate) fn unix_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Opens a ticket and raises the owner's fingerprint window for it (off the caller's thread);
/// the page polls [`state`] and resends with the ticket.
pub fn ask(ask: &Ask) -> Asked {
    if !page_category(ask.category) {
        return Asked::Unavailable("category_not_for_page");
    }
    if !WIRED.contains(&ask.operation) {
        return Asked::Unavailable(UNAVAILABLE);
    }
    match OWNER.get() {
        Some(o) => o.ask(ask, |f| {
            std::thread::spawn(f);
        }),
        None => Asked::Unavailable(UNAVAILABLE),
    }
}

/// The unix second from which a window fits the 3 in 10 minutes / 6 an hour again; none when
/// one fits now or nothing is wired.
pub fn retry_at() -> Option<u64> {
    OWNER.get().and_then(|o| o.retry_at())
}

/// What the page gets (with its `request_id`) when [`ask`] answers [`Asked::Unavailable`]: the
/// real reason, and for `pair_touch_limited` the time to try again (0eed7 pv7 L: it said "cannot
/// be asked on this machine yet").
pub fn refusal(why: &'static str) -> serde_json::Value {
    refusal_at(why, retry_at())
}

pub(crate) fn refusal_at(why: &'static str, at: Option<u64>) -> serde_json::Value {
    let message = match why {
        "pair_touch_busy" => "a fingerprint window is open already; answer that one first",
        "pair_touch_cooling" => "a fingerprint window was just declined; wait a little",
        "pair_touch_limited" => {
            "at most 3 fingerprint windows in 10 minutes and 6 in an hour; try again later"
        }
        _ => "this needs the owner's fingerprint, and it cannot be asked on this machine yet",
    };
    let mut v = serde_json::json!({"code": "forbidden", "reason": why, "message": message});
    if why == "pair_touch_limited" {
        if let Some(at) = at {
            v["retry_at"] = serde_json::json!(at);
            if let Some(hms) = crate::touch::clock_text(at) {
                v["retry_hms"] = serde_json::json!(hms);
            }
        }
    }
    v
}

/// A ticket's state for the page `by` (`page:<pair>`); another page's ticket reads as expired.
pub fn state(ticket: &str, by: &str) -> State {
    match OWNER.get() {
        Some(o) => o.state(ticket, by),
        None => State::Unavailable(UNAVAILABLE),
    }
}

/// The bytes the key signs for one ticket.
pub fn binding(ticket: &str, ask: &Ask) -> Vec<u8> {
    format!(
        "aibd/owner-ok/v1\n{ticket}\n{}\n{}\n{}\n{}\n",
        ask.operation,
        ask.resource,
        params_sha256(&ask.params),
        ask.by
    )
    .into_bytes()
}

struct Slot {
    rec: Record,
    by: String,
    state: State,
    /// The key's signature of this ticket's [`binding`], once approved (none from a presence
    /// with no key).
    sig: Vec<u8>,
}

/// The tickets of this aibd, in memory only: a restart drops them all.
pub struct Owner {
    presence: crate::touch::SharedPresence,
    /// The window's first words: [`PREVIEW_MARK`] in a preview, else nothing.
    mark: &'static str,
    slots: std::sync::Mutex<std::collections::HashMap<String, Slot>>,
    gate: std::sync::Arc<dyn WindowGate>,
    clock: fn() -> u64,
}

impl Owner {
    pub fn new(
        presence: crate::touch::SharedPresence,
        gate: std::sync::Arc<dyn WindowGate>,
        clock: fn() -> u64,
    ) -> Owner {
        Owner {
            presence,
            mark: "",
            slots: std::sync::Mutex::default(),
            gate,
            clock,
        }
    }

    /// The words every window of this owner starts with ([`mark_for`]).
    pub fn marked(mut self, mark: &'static str) -> Owner {
        self.mark = mark;
        self
    }

    fn slots(&self) -> std::sync::MutexGuard<'_, std::collections::HashMap<String, Slot>> {
        self.slots.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// [`retry_at`] for this owner: its clock plus the gate's wait, rounded up to the second.
    pub fn retry_at(&self) -> Option<u64> {
        let w = self.gate.wait()?;
        Some((self.clock)() + w.as_secs() + u64::from(w.subsec_nanos() > 0))
    }

    /// `spawn` runs the window (a thread in aibd, inline in tests).
    pub fn ask(
        self: &std::sync::Arc<Self>,
        ask: &Ask,
        spawn: impl FnOnce(Box<dyn FnOnce() + Send>),
    ) -> Asked {
        let now = (self.clock)();
        let Ok(ticket) = crate::sessions::new_id("ot_") else {
            return Asked::Unavailable(UNAVAILABLE);
        };
        if let Err(why) = self.gate.open() {
            return Asked::Unavailable(why);
        }
        {
            let mut s = self.slots();
            s.retain(|_, v| now < v.rec.expires_at + 600);
            s.insert(
                ticket.clone(),
                Slot {
                    rec: Record {
                        ticket: ticket.clone(),
                        category: ask.category,
                        operation: ask.operation.to_owned(),
                        resource: ask.resource.clone(),
                        params_sha256: params_sha256(&ask.params),
                        approved: false,
                        expires_at: now + TICKET_SECS,
                        used: false,
                    },
                    by: ask.by.clone(),
                    state: State::Pending,
                    sig: Vec::new(),
                },
            );
        }
        let me = self.clone();
        // made before the window's thread, so a spawn that fails still closes the gate
        // (5d33a PREREAD-ownertouch r2 N1)
        let mut closing = Closing {
            gate: self.gate.clone(),
            yes: false,
        };
        let words = window_text(ask, &ticket, &until_words(now + TICKET_SECS));
        let why = format!("{}{words}", self.mark);
        let msg = binding(&ticket, ask);
        let t = ticket.clone();
        let (hud_words, hud_until, hud_code) = (
            format!("{}{}", self.mark, top_words(ask)),
            now + TICKET_SECS,
            pair_code(&ticket),
        );
        spawn(Box::new(move || {
            // lives as long as the window below; only shows (crate::touch_hud)
            let _hud = crate::touch_hud::Hud::show(&hud_words, hud_until, &hud_code);
            // the window lives as long as the ticket: past that it closes by itself, and a
            // window nobody answered is not a no (0eed7 VERIFY-ownertouch-andy M1)
            let mut sig = Vec::new();
            let got = me.presence.ask_until_keep(
                &why,
                &msg,
                std::time::Duration::from_secs(TICKET_SECS),
                &mut sig,
            );
            closing.yes = crate::touch::ends_quietly(&got);
            drop(closing);
            me.answered(&t, got, sig);
        }));
        Asked::Ticket(ticket)
    }

    fn answered(&self, ticket: &str, got: Result<(), &'static str>, sig: Vec<u8>) {
        let now = (self.clock)();
        let mut s = self.slots();
        let Some(slot) = s.get_mut(ticket) else {
            return;
        };
        if slot.state != State::Pending {
            return;
        }
        slot.state = match got {
            Ok(()) if now < slot.rec.expires_at => {
                slot.rec.approved = true;
                slot.sig = sig;
                State::Approved {
                    at: now,
                    digest: slot.rec.params_sha256.clone(),
                }
            }
            Ok(()) => State::Expired,
            Err(crate::touch::TIMEOUT) => State::Expired,
            Err(e) => State::Refused(e.to_owned()),
        };
    }

    pub fn state(&self, ticket: &str, by: &str) -> State {
        let now = (self.clock)();
        match self.slots().get(ticket) {
            Some(v) if v.by == by && !v.rec.used && now < v.rec.expires_at => v.state.clone(),
            _ => State::Expired,
        }
    }

    /// Approved, not used, not expired, the same page and the same act: used up here.
    pub fn approved_for(&self, ticket: &str, ask: &Ask) -> bool {
        self.take(ticket, ask).is_some()
    }

    /// [`Owner::approved_for`] that hands back the key's signature of the ticket's
    /// [`binding`]; an approval with no signature kept is not one here (and is used up).
    pub fn approved_signed_for(&self, ticket: &str, ask: &Ask) -> Option<Vec<u8>> {
        self.take(ticket, ask).filter(|sig| !sig.is_empty())
    }

    fn take(&self, ticket: &str, ask: &Ask) -> Option<Vec<u8>> {
        let now = (self.clock)();
        let mut s = self.slots();
        match s.get_mut(ticket) {
            Some(v) if v.by == ask.by && record_matches(&v.rec, ticket, ask, now) => {
                v.rec.used = true;
                Some(v.sig.clone())
            }
            _ => None,
        }
    }

    /// Whether `sig` is the owner's key's signature of `msg` (a stored approval checked again).
    pub fn verify(&self, msg: &[u8], sig: &[u8]) -> bool {
        self.presence.verify(msg, sig)
    }
}

/// A ticket word as aibd hands it out: 1-64 of `A-Z a-z 0-9 - _` (anything else is never a
/// ticket, and is not echoed).
pub fn ticket_ok(t: &str) -> bool {
    (1..=64).contains(&t.len())
        && t.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

/// The word the page reads for a [`State`] (GAP-repo-register G2, 043dd eeaf7378e4): an
/// approval's time and digest stay inside aibd.
pub fn state_word(s: &State) -> serde_json::Value {
    match s {
        State::Pending => serde_json::json!({"state": "pending"}),
        State::Approved { .. } => serde_json::json!({"state": "approved"}),
        State::Refused(_) => serde_json::json!({"state": "denied"}),
        State::Expired => serde_json::json!({"state": "expired"}),
        State::Unavailable(why) => serde_json::json!({"state": "unavailable", "reason": why}),
    }
}

/// GET /api/v1/owner/tickets/{id}: the built-in page's own question only (a seat or a script
/// gets 403 `page_only`); a malformed ticket is 400 `ticket`. Reading changes nothing.
pub async fn ticket_state(
    axum::Extension(c): axum::Extension<crate::guard::Caller>,
    axum::extract::Path(id): axum::extract::Path<String>,
) -> axum::response::Response {
    use axum::http::StatusCode;
    use axum::response::IntoResponse;
    if !matches!(c, crate::guard::Caller::Page(_)) {
        return (
            StatusCode::FORBIDDEN,
            axum::Json(serde_json::json!({"code": "page_only",
                "message": "a ticket's state is the page's to read"})),
        )
            .into_response();
    }
    if !ticket_ok(&id) {
        return (
            StatusCode::BAD_REQUEST,
            axum::Json(serde_json::json!({"code": "ticket",
                "message": "not a ticket aibd hands out"})),
        )
            .into_response();
    }
    // the refusal above is the only one here (knife OT1 cuts it): past it a caller with no pair
    // reads as a page no ticket was ever handed to
    let by: String = if let crate::guard::Caller::Page(page) = &c {
        page.digest[..8]
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect()
    } else {
        String::new()
    };
    axum::Json(state_word(&state(&id, &format!("page:{by}")))).into_response()
}

/// Right before acting: the ticket is approved, not expired, not used, from the same page and
/// for this very act; a yes is used up. Without the switch nothing is approved.
pub fn approved_for(ticket: &str, ask: &Ask) -> bool {
    OWNER.get().is_some_and(|o| o.approved_for(ticket, ask))
}

/// [`approved_for`] for a caller that keeps the approval: the key's signature of the ticket's
/// [`binding`], to store and check again with [`verify`]. Without the switch: none.
pub fn approved_signed_for(ticket: &str, ask: &Ask) -> Option<Vec<u8>> {
    OWNER.get().and_then(|o| o.approved_signed_for(ticket, ask))
}

/// Whether `sig` is the owner's key's signature of `msg`: the key this aibd checked at start.
/// Without the switch or a key: never.
pub fn verify(msg: &[u8], sig: &[u8]) -> bool {
    OWNER.get().is_some_and(|o| o.verify(msg, sig))
}

/// [`approved_for`]'s comparison on a record already read.
pub fn record_matches(rec: &Record, ticket: &str, ask: &Ask, now: u64) -> bool {
    rec.approved
        && !rec.used
        && now < rec.expires_at
        && rec.ticket == ticket
        && rec.category == ask.category
        && page_category(rec.category)
        && rec.operation == ask.operation
        && rec.resource == ask.resource
        && rec.params_sha256 == params_sha256(&ask.params)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn a() -> Ask {
        Ask {
            category: Category::Command,
            operation: "worktree.open",
            resource: "/Users/x/repo".into(),
            params: b"{\"repo\":\"/Users/x/repo\"}".to_vec(),
            by: "page:0123abcd".into(),
        }
    }

    fn rec(ask: &Ask) -> Record {
        Record {
            ticket: "t1".into(),
            category: ask.category,
            operation: ask.operation.into(),
            resource: ask.resource.clone(),
            params_sha256: params_sha256(&ask.params),
            approved: true,
            expires_at: 200,
            used: false,
        }
    }

    #[test]
    fn a_page_asks_only_its_six_categories_and_nothing_is_wired_yet() {
        for c in aib_approve::category::ALL {
            let mut x = a();
            x.category = c;
            let want = if matches!(c, Category::SeatInput | Category::Outbound) || !page_category(c)
            {
                "category_not_for_page"
            } else {
                UNAVAILABLE
            };
            assert_eq!(ask(&x), Asked::Unavailable(want), "reason: category {c:?}");
        }
        assert!(
            !page_category(Category::SeatInput),
            "reason: seat_input_asked"
        );
        assert!(!page_category(Category::Outbound), "reason: outbound_asked");
        assert_eq!(
            state("t1", "page:0123abcd"),
            State::Unavailable(UNAVAILABLE)
        );
        // G2: what the page reads; an approval's digest is not shown
        for (st, want) in [
            (State::Pending, "pending"),
            (
                State::Approved {
                    at: 1,
                    digest: "d".into(),
                },
                "approved",
            ),
            (State::Refused("no".into()), "denied"),
            (State::Expired, "expired"),
            (State::Unavailable(UNAVAILABLE), "unavailable"),
        ] {
            let v = state_word(&st);
            assert_eq!(v["state"], want, "reason: word {st:?}");
            assert!(
                v.get("digest").is_none() && v.get("at").is_none(),
                "reason: leaked {v}"
            );
        }
        assert!(ticket_ok("t-1_A"));
        for bad in ["", "a/b", "..", "t 1", &"x".repeat(65)] {
            assert!(!ticket_ok(bad), "reason: ticket_shape {bad}");
        }
        assert!(!approved_for("t1", &a()), "reason: stub_approves");
    }

    /// Every `operation: CONSTANT` of an owner ask in aibd's own code (tests left out), as
    /// `(file, constant, its string)`. An operation given as a variable is not one of these:
    /// its caller does not use a ticket, and the ask stays unavailable.
    fn operation_constants() -> Vec<(String, String, String)> {
        let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
        operation_constants_in(&dir).unwrap_or_else(|e| panic!("reason: {e}"))
    }

    fn operation_constants_in(
        dir: &std::path::Path,
    ) -> Result<Vec<(String, String, String)>, String> {
        // 5d33a N1 (REVIEW-wired aba125e2): every folder under src, not only the top one
        let mut files = Vec::new();
        let mut dirs = vec![dir.to_path_buf()];
        while let Some(d) = dirs.pop() {
            for e in std::fs::read_dir(&d).map_err(|e| e.to_string())?.flatten() {
                let path = e.path();
                if path.is_dir() {
                    dirs.push(path);
                } else if path.extension().is_some_and(|x| x == "rs") {
                    files.push(path);
                }
            }
        }
        let mut out = Vec::new();
        for path in files {
            let file = path
                .strip_prefix(dir)
                .unwrap_or(&path)
                .to_string_lossy()
                .replace('\\', "/");
            let src = std::fs::read_to_string(&path).map_err(|e| e.to_string())?;
            let code = src.split("#[cfg(test)]").next().unwrap_or_default();
            for line in code.lines() {
                let Some(name) = line
                    .trim()
                    .strip_prefix("operation: ")
                    .map(|r| r.trim_end_matches(','))
                else {
                    continue;
                };
                // 5d33a N2: a written-out name or a path to another file's constant would not
                // be read here and could be missing from WIRED unseen; it must be a constant
                // of the file that asks
                if name.starts_with('"') || name.contains("::") {
                    return Err(format!(
                        "operation_not_a_constant_of_its_file {file} {name}"
                    ));
                }
                let constant = !name.is_empty()
                    && name
                        .bytes()
                        .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_');
                if !constant {
                    continue;
                }
                let decl = format!("const {name}: &str = \"");
                let value = code
                    .split_once(&decl)
                    .and_then(|(_, rest)| rest.split_once('"'))
                    .map(|(v, _)| v.to_owned())
                    .ok_or_else(|| format!("constant_not_in_its_file {file} {name}"))?;
                out.push((file.clone(), name.to_owned(), value));
            }
        }
        out.sort();
        Ok(out)
    }

    /// 5d33a N1 N2 (REVIEW-wired aba125e2): an ask in a sub-folder is read; an operation
    /// written out or named by a path is refused, not passed over
    #[test]
    fn the_scan_reads_sub_folders_and_refuses_what_it_cannot_read() {
        let d = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(d.path().join("platform/mac")).unwrap();
        let ask =
            "pub const OP_X: &str = \"x.y\";\nfn f() { A {\n        operation: OP_X,\n    }; }\n";
        std::fs::write(d.path().join("platform/mac/x.rs"), ask).unwrap();
        std::fs::write(d.path().join("top.rs"), "fn g() {}\n").unwrap();
        let got = operation_constants_in(d.path()).unwrap();
        assert_eq!(
            got,
            vec![(
                "platform/mac/x.rs".to_owned(),
                "OP_X".to_owned(),
                "x.y".to_owned()
            )],
            "reason: sub_folder_not_read"
        );
        for bad in ["\"x.y\"", "crate::x::OP_X"] {
            std::fs::write(
                d.path().join("top.rs"),
                format!("fn g() {{ A {{\n    operation: {bad},\n}}; }}\n"),
            )
            .unwrap();
            let e = operation_constants_in(d.path()).unwrap_err();
            assert!(
                e.starts_with("operation_not_a_constant_of_its_file top.rs"),
                "reason: {bad} passed over: {e}"
            );
        }
    }

    /// 519d0 11-21 (0eed7 a82: `seat_send_back` answered `owner_check_unavailable` on the
    /// integration head): each slice was green alone and nobody added the line.
    #[test]
    fn every_operation_constant_is_wired() {
        let ops = operation_constants();
        assert!(ops.len() >= 4, "reason: operations_not_read {ops:?}");
        for (file, name, value) in &ops {
            assert!(
                WIRED.contains(&value.as_str()),
                "reason: operation_not_wired {file} {name} {value}"
            );
        }
        assert!(
            WIRED.contains(&crate::send_back::OP_SEND_BACK),
            "reason: send_back_not_wired"
        );
        let mut seen = std::collections::BTreeSet::new();
        for w in WIRED {
            assert!(seen.insert(*w), "reason: wired_twice {w}");
        }
    }

    #[test]
    fn the_window_shows_the_cleaned_end_of_the_resource_and_the_params_hash() {
        let mut x = a();
        x.resource = format!("{}\u{202e}/tail-end", "d/".repeat(60));
        let r = clean_resource(&x.resource);
        assert!(!r.contains('\u{202e}'), "reason: bad_char_shown");
        assert!(r.ends_with("/tail-end"), "reason: path_head_kept {r}");
        assert_eq!(r.chars().count(), RESOURCE_MAX + 1, "reason: not_cut {r}");
        assert_eq!(clean_resource("短"), "短");
        // an operation with no sentence: its name on top; start and end of a long object below
        let w = window_text(&x, "t9", "請在 01:23:44 前按");
        assert!(
            w.starts_with("worktree.open(請在 01:23:44 前按,過了這張就作廢)對照碼 ")
                && w.contains("\nAIB 網頁要求:worktree.open|對象:d/d/d/d/d/d/d/d/d/d/d/d/…"),
            "{w}"
        );
        assert!(w.contains("/tail-end|單號 t9|參數 "), "{w}");
        assert!(w.ends_with(&params_sha256(&x.params)[..12]), "{w}");
        assert_eq!(object_shown(&x.resource).chars().count(), RESOURCE_MAX + 1);
    }

    /// Andy 11-21 01:23, 01:44; 5d33a PREREAD-touchwords L1.
    #[test]
    fn the_window_says_what_is_done_until_when_and_no_object_can_say_it_instead() {
        let mut x = a();
        x.operation = crate::repos::OP_REGISTER;
        x.resource = "demo-c45c · /Users/x/demo-c45c · remote github.com".into();
        x.params = br#"{"name":"demo-c45c","path":"/Users/x/demo-c45c","remote":"git@github.com:o/r.git"}"#.to_vec();
        let w = window_text(&x, "ot_1", "請在 01:23:44 前按");
        assert!(
            w.starts_with(&format!(
                "登記這個資料夾當倉:demo-c45c(請在 01:23:44 前按,過了這張就作廢)對照碼 {}\nAIB 網頁要求:repo.register|對象:demo-c45c · /Users/x/demo-c45c · remote github.com|單號 ot_1|參數 ",
                pair_code("ot_1")
            )),
            "reason: window_words {w}"
        );
        // the code the countdown beside the window shows too: four of the ticket's own hash
        let code = pair_code("ot_1");
        assert!(
            code.len() == 4
                && code
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'A'..=b'F').contains(&b)),
            "reason: pair_code {code}"
        );
        assert_ne!(code, pair_code("ot_2"), "reason: one_code_for_every_ticket");
        assert_eq!(top_words(&x), "登記這個資料夾當倉:demo-c45c");
        // one fixed sentence per operation, from the ticket's parameters
        for (op, params, want) in [
            (
                crate::repos::OP_OPEN,
                r#"{"repo":"demo","provider":"claude","persona_uid":"p1"}"#,
                "開一個能改程式的席,帶倉 demo(claude)",
            ),
            (
                crate::send_back::OP_SEND_BACK,
                r#"{"repo":"demo","seat_id":"seat_33ff9a0b1c2d3e4f","commits":3,"files":5}"#,
                "把席 33ff9 的 3 個提交、5 個檔送回 demo",
            ),
            (
                crate::repo_push::OP,
                r#"{"repo":"demo","branch":"web/ab12-1120-1"}"#,
                "把 demo 的分支 web/ab12-1120-1 推上遠端",
            ),
            (
                crate::pr_open::OP,
                r#"{"slug":"ez2app/demo","branch":"web/ab12-1120-1","base":"main"}"#,
                "在 ez2app/demo 開一張草稿 PR(分支 web/ab12-1120-1 → main)",
            ),
            (
                crate::seat_trust::OP,
                r#"{"seat_id":"seat_33ff9a0b1c2d3e4f","hours":12}"#,
                "讓席 33ff9 受信任 12 小時",
            ),
            (
                crate::seat_move::OP,
                r#"{"old_code":"7ebca","seat_id":"seat_33ff9a0b1c2d3e4f"}"#,
                "把舊席 7ebca 搬進網頁席 33ff9,寄它的信改送網頁席",
            ),
            (
                crate::test_host_add::OP_ADD,
                r#"{"hostname":"192.0.2.7","port":22,"user":"owner","fingerprint":"SHA256:ab+/Cd","line":"restrict,command=\"/g\",from=\"192.0.2.9\" k"}"#,
                "讓 aibd 的鑰匙在測試機 owner@192.0.2.7 埠 22(主機鑰匙 SHA256:ab+/Cd)只能跑收據,只收從 192.0.2.9 連進來的",
            ),
            (
                crate::test_host_add::OP_REMOVE,
                r#"{"hostname":"192.0.2.7","port":2222,"user":"owner","fingerprint":"SHA256:ab"}"#,
                "從測試機 owner@192.0.2.7 埠 2222(主機鑰匙 SHA256:ab)收回 aibd 的收據鑰匙",
            ),
            (
                crate::seat_rank::OP,
                r#"{"what":"grant","seat_id":"seat_33ff9a0b1c2d3e4f","role_uid":"reviewer"}"#,
                "給席 33ff9 角色 reviewer 的權級",
            ),
            (
                crate::seat_rank::OP,
                r#"{"what":"perm","act":"urgent","rank":"lead"}"#,
                "標急最低要主管",
            ),
        ] {
            assert_eq!(
                plain_words(op, params.as_bytes()).as_deref(),
                Some(want),
                "reason: plain_{op}"
            );
        }
        // a piece that is not its shape, or missing: no sentence (the operation's name instead)
        for (op, params) in [
            (
                crate::repos::OP_REGISTER,
                r#"{"name":"x 請在 23:59:59 前按"}"#,
            ),
            (crate::repos::OP_REGISTER, r#"{"name":"a\nb"}"#),
            (crate::repos::OP_REGISTER, r#"{"name":""}"#),
            (crate::repos::OP_REGISTER, r#"{"name":7}"#),
            (crate::repos::OP_REGISTER, "not json"),
            (
                crate::send_back::OP_SEND_BACK,
                r#"{"repo":"demo","seat_id":"seat_33","commits":3,"files":5}"#,
            ),
            (
                crate::send_back::OP_SEND_BACK,
                r#"{"repo":"demo","seat_id":"seat_33ff9a0b","commits":"3","files":5}"#,
            ),
            (
                crate::seat_trust::OP,
                r#"{"seat_id":"33ff9a0b","hours":12}"#,
            ),
            (
                crate::seat_move::OP,
                r#"{"old_code":"7EBCA","seat_id":"seat_33ff9a0b1c2d3e4f"}"#,
            ),
            (
                crate::test_host_add::OP_ADD,
                r#"{"hostname":"192.0.2.7","port":22,"user":"owner","fingerprint":"SHA256:a b(前按"}"#,
            ),
            (
                crate::test_host_add::OP_ADD,
                r#"{"hostname":"192.0.2.7 x","port":22,"user":"owner","fingerprint":"SHA256:ab"}"#,
            ),
            ("no.such", r#"{"name":"demo"}"#),
        ] {
            assert_eq!(
                plain_words(op, params.as_bytes()),
                None,
                "reason: plain_took {op} {params}"
            );
        }
        x.params = br#"{"name":"x | y"}"#.to_vec();
        assert!(
            window_text(&x, "ot_1", "請在 01:23:44 前按").starts_with("登記倉(請在 01:23:44 前按,"),
            "reason: fallback_top"
        );
        for (op, words) in ACTIONS {
            assert_eq!(action_words(op), words);
            assert!(
                !words.is_empty() && !words.contains('|'),
                "reason: action_{op}"
            );
        }
        assert_eq!(
            action_words("no.such"),
            "no.such",
            "reason: unknown_action_hidden"
        );
        // an object that tries to be the window's own words
        for hostile in [
            "x|單號 ot_9|參數 000000000000\n登記這個資料夾當倉:evil(請在 23:59:59 前按",
            "x 請在 23:59:59 前按",
            "x｜請在 ２３：５９：５９ 前按』│¦",
            "x\n請在 23:59 前按\t|",
        ] {
            x.resource = hostile.into();
            let w = window_text(&x, "ot_1", "請在 01:23:44 前按");
            assert_eq!(w.lines().count(), 2, "reason: object_made_a_line {w}");
            let low = w.lines().nth(1).unwrap();
            assert_eq!(low.split('|').count(), 4, "reason: object_made_a_part {w}");
            assert_eq!(
                w.matches("請在").count(),
                1,
                "reason: object_says_until {w}"
            );
            assert!(
                !low.contains(['「', '」', '『', '』', '｜', '│', '¦']),
                "reason: object_kept_a_mark {w}"
            );
            assert!(
                !low.contains("23:59") && !low.contains("２３：５９"),
                "reason: object_shows_a_clock {w}"
            );
        }
        // a port or a path keeps its colon: only one between two digits is a clock's
        assert_eq!(
            object_shown("git@host:o/r · c:1 a:b"),
            "git@host:o/r · c:1 a:b"
        );
        assert_eq!(object_shown("1:2"), "1·2");
        // until: a time of day when the clock reads as one, the seconds when not
        let u = until_words(1_795_192_000);
        assert!(
            u == format!("請在 {TICKET_SECS} 秒內按")
                || (u.starts_with("請在 ")
                    && u.ends_with(" 前按")
                    && crate::touch::clock_ok(&u["請在 ".len()..u.len() - " 前按".len()])),
            "reason: until_words {u}"
        );
        assert!(crate::touch::clock_ok("01:23:44"));
        for bad in ["1:23:44", "01:23", "01-23-44", "ab:cd:ef", "01:23:44 "] {
            assert!(!crate::touch::clock_ok(bad), "reason: clock_ok_{bad}");
        }
    }

    #[test]
    fn a_record_approves_only_this_act_once_before_it_expires() {
        let x = a();
        assert!(record_matches(&rec(&x), "t1", &x, 100));
        type Break = (&'static str, fn(&mut Record));
        let cases: [Break; 8] = [
            ("not_approved", |r| r.approved = false),
            ("used", |r| r.used = true),
            ("expired", |r| r.expires_at = 100),
            ("other_ticket", |r| r.ticket = "t2".into()),
            ("other_category", |r| r.category = Category::DeleteData),
            ("other_operation", |r| r.operation = "seat.open_dgrs".into()),
            ("other_resource", |r| r.resource = "/Users/x/other".into()),
            ("other_params", |r| r.params_sha256 = params_sha256(b"x")),
        ];
        for (why, f) in cases {
            let mut r = rec(&x);
            f(&mut r);
            assert!(!record_matches(&r, "t1", &x, 100), "reason: {why}");
        }
        let mut s = x.clone();
        s.category = Category::SeatInput;
        assert!(
            !record_matches(&rec(&s), "t1", &s, 100),
            "reason: seat_input_record"
        );
    }

    struct Says(Result<(), &'static str>);
    impl crate::touch::Presence for Says {
        fn ask(&self, _reason: &str, msg: &[u8]) -> Result<(), &'static str> {
            assert!(
                msg.starts_with(b"aibd/owner-ok/v1\n"),
                "precondition: binding"
            );
            self.0
        }
    }

    static NOW: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1000);
    fn now() -> u64 {
        NOW.load(std::sync::atomic::Ordering::SeqCst)
    }
    fn at(t: u64) {
        NOW.store(t, std::sync::atomic::Ordering::SeqCst);
    }
    fn reg() -> Ask {
        let mut x = a();
        x.operation = crate::repos::OP_REGISTER;
        x
    }
    fn gate() -> std::sync::Arc<dyn WindowGate> {
        std::sync::Arc::new(crate::pairing::Pairing::default())
    }
    fn owner(says: Result<(), &'static str>) -> std::sync::Arc<Owner> {
        std::sync::Arc::new(Owner::new(std::sync::Arc::new(Says(says)), gate(), now))
    }
    fn inline(f: Box<dyn FnOnce() + Send>) {
        f()
    }
    fn ticket(a: Asked) -> String {
        match a {
            Asked::Ticket(t) => t,
            other => panic!("reason: no_ticket {other:?}"),
        }
    }

    #[test]
    fn the_owners_yes_approves_one_act_for_one_page_once() {
        at(1000);
        assert!(switch_on(Some("1".into())));
        for off in [
            None,
            Some("0".into()),
            Some("true".into()),
            Some(" 1".into()),
        ] {
            assert!(!switch_on(off.clone()), "reason: switch_on {off:?}");
        }
        assert!(
            WIRED.contains(&crate::repos::OP_REGISTER) && WIRED.contains(&crate::repos::OP_OPEN)
        );
        // the window is pending until the owner answers
        let o = owner(Ok(()));
        let held = std::sync::Mutex::new(None::<Box<dyn FnOnce() + Send>>);
        let t = ticket(o.ask(&reg(), |f| *held.lock().unwrap() = Some(f)));
        assert!(ticket_ok(&t), "reason: ticket_shape {t}");
        assert_eq!(o.state(&t, "page:0123abcd"), State::Pending);
        assert!(!o.approved_for(&t, &reg()), "reason: pending_approved");
        assert_eq!(
            o.ask(&reg(), inline),
            Asked::Unavailable("pair_touch_busy"),
            "reason: two_windows"
        );
        let run = held.lock().unwrap().take().unwrap();
        run();
        assert!(matches!(
            o.state(&t, "page:0123abcd"),
            State::Approved { .. }
        ));
        // another page reads nothing and cannot use it
        assert_eq!(
            o.state(&t, "page:ffffffff"),
            State::Expired,
            "reason: other_page_reads"
        );
        let mut other = reg();
        other.by = "page:ffffffff".into();
        assert!(!o.approved_for(&t, &other), "reason: other_page_uses");
        // other params or another act: no
        let mut p = reg();
        p.params = b"{}".to_vec();
        assert!(!o.approved_for(&t, &p), "reason: other_params");
        let mut op = reg();
        op.operation = crate::repos::OP_OPEN;
        assert!(!o.approved_for(&t, &op), "reason: other_operation");
        // this act, once
        assert!(o.approved_for(&t, &reg()), "reason: yes_refused");
        assert!(!o.approved_for(&t, &reg()), "reason: used_twice");
        assert_eq!(
            o.state(&t, "page:0123abcd"),
            State::Expired,
            "reason: used_still_shown"
        );
        assert!(!o.approved_for("ot_nope", &reg()), "reason: unknown_ticket");

        // a yes that comes after the ticket ran out approves nothing
        at(5000);
        let o = owner(Ok(()));
        let held = std::sync::Mutex::new(None::<Box<dyn FnOnce() + Send>>);
        let t = ticket(o.ask(&reg(), |f| *held.lock().unwrap() = Some(f)));
        at(5000 + TICKET_SECS);
        held.lock().unwrap().take().unwrap()();
        assert_eq!(
            o.state(&t, "page:0123abcd"),
            State::Expired,
            "reason: late_yes"
        );
        assert!(!o.approved_for(&t, &reg()), "reason: late_yes_used");
        // an approved ticket runs out too
        at(6000);
        let t = ticket(o.ask(&reg(), inline));
        at(6000 + TICKET_SECS);
        assert!(!o.approved_for(&t, &reg()), "reason: expired_used");
    }

    #[test]
    fn a_no_cools_the_window_and_an_hour_holds_six() {
        // the owner windows pass pairing's own gate (5d33a L1): a no cools both kinds
        let pairing = std::sync::Arc::new(crate::pairing::Pairing::default());
        let o = std::sync::Arc::new(Owner::new(
            std::sync::Arc::new(Says(Err("presence_cancelled"))),
            pairing.clone(),
            || 10_000,
        ));
        let t = ticket(o.ask(&reg(), inline));
        assert_eq!(
            o.state(&t, "page:0123abcd"),
            State::Refused("presence_cancelled".into())
        );
        assert!(!o.approved_for(&t, &reg()), "reason: no_approves");
        assert_eq!(
            o.ask(&reg(), inline),
            Asked::Unavailable("pair_touch_cooling"),
            "reason: no_cool"
        );
        assert_eq!(
            pairing.window_open(),
            Err("pair_touch_cooling"),
            "reason: pairing_not_cooled"
        );
        // the count is over the whole aibd: pairing's own windows and the owner's together
        let pairing = std::sync::Arc::new(crate::pairing::Pairing::default());
        let o = std::sync::Arc::new(Owner::new(
            std::sync::Arc::new(Says(Ok(()))),
            pairing.clone(),
            || 20_000,
        ));
        assert_eq!(pairing.window_open(), Ok(()));
        pairing.window_closed(true);
        for i in 1..crate::pairing::RECONFIRM_SHORT_MAX {
            assert!(
                matches!(o.ask(&reg(), inline), Asked::Ticket(_)),
                "reason: window {i}"
            );
        }
        assert_eq!(
            o.ask(&reg(), inline),
            Asked::Unavailable("pair_touch_limited"),
            "reason: limit_apart"
        );
        // and when it fits again: ten minutes after the first of the three (0eed7 pv7 L)
        let at = o.retry_at().expect("reason: limit_no_retry");
        assert!(
            (20_000 + 590..=20_000 + 600).contains(&at),
            "reason: limit_retry_at {at}"
        );
        let v = refusal_at("pair_touch_limited", Some(at));
        assert_eq!(v["retry_at"], at, "reason: limit_retry_shown");
        assert!(
            v["message"]
                .as_str()
                .unwrap()
                .contains("3 fingerprint windows in 10 minutes"),
            "reason: limit_words {v}"
        );
        if let Some(hms) = v["retry_hms"].as_str() {
            assert!(crate::touch::clock_ok(hms), "reason: limit_hms {hms}");
        }
        assert!(
            refusal_at("pair_touch_busy", Some(at))
                .get("retry_at")
                .is_none(),
            "reason: retry_only_for_limit"
        );
        assert!(
            refusal_at(UNAVAILABLE, None)["message"]
                .as_str()
                .unwrap()
                .contains("cannot be asked on this machine yet"),
            "reason: unwired_words"
        );
        // a window whose thread never starts still closes in the gate (5d33a r2 N1)
        let pairing = std::sync::Arc::new(crate::pairing::Pairing::default());
        let o = std::sync::Arc::new(Owner::new(
            std::sync::Arc::new(Says(Ok(()))),
            pairing.clone(),
            || 40_000,
        ));
        let _ = ticket(o.ask(&reg(), drop));
        assert_eq!(
            pairing.window_open(),
            Err("pair_touch_cooling"),
            "reason: unspawned_left_busy"
        );
        // a window whose thread panics still closes in the gate (5d33a N1)
        struct Boom;
        impl crate::touch::Presence for Boom {
            fn ask(&self, _r: &str, _m: &[u8]) -> Result<(), &'static str> {
                panic!("precondition: the window panics")
            }
        }
        let pairing = std::sync::Arc::new(crate::pairing::Pairing::default());
        let o = std::sync::Arc::new(Owner::new(
            std::sync::Arc::new(Boom),
            pairing.clone(),
            || 30_000,
        ));
        let t = ticket(o.ask(&reg(), |f| {
            let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(f));
        }));
        assert_eq!(o.state(&t, "page:0123abcd"), State::Pending);
        assert_eq!(
            pairing.window_open(),
            Err("pair_touch_cooling"),
            "reason: panic_left_busy"
        );
        // the window's words and the signed bytes carry this ticket and these params
        let b = String::from_utf8(binding("ot_1", &reg())).unwrap();
        assert!(b.contains("\not_1\nrepo.register\n"), "{b}");
        assert!(b.contains(&params_sha256(&reg().params)), "{b}");
        assert!(b.ends_with("\npage:0123abcd\n"), "{b}");
    }

    /// 5d33a PREREAD-pvtouch-rrmerge L1: a preview's window says it is the preview's; the real
    /// aibd's window is as it was.
    #[test]
    fn a_previews_window_says_preview_first_and_the_real_ones_does_not() {
        assert_eq!(mark_for(true), PREVIEW_MARK, "reason: preview_unmarked");
        assert_eq!(mark_for(false), "", "reason: real_marked");
        struct Said(std::sync::Mutex<Vec<String>>);
        impl crate::touch::Presence for Said {
            fn ask(&self, reason: &str, _msg: &[u8]) -> Result<(), &'static str> {
                self.0.lock().unwrap().push(reason.to_owned());
                Ok(())
            }
            fn ask_until(
                &self,
                reason: &str,
                _msg: &[u8],
                _limit: std::time::Duration,
            ) -> Result<(), &'static str> {
                self.0.lock().unwrap().push(reason.to_owned());
                Ok(())
            }
        }
        for (mark, preview) in [(PREVIEW_MARK, true), ("", false)] {
            let p = std::sync::Arc::new(Said(std::sync::Mutex::new(Vec::new())));
            let o = std::sync::Arc::new(Owner::new(p.clone(), gate(), now).marked(mark));
            let _ = ticket(o.ask(&reg(), inline));
            let said = p.0.lock().unwrap().clone();
            assert_eq!(said.len(), 1, "reason: no_window");
            assert_eq!(
                said[0].starts_with("預覽:"),
                preview,
                "reason: window_mark_wrong {}",
                said[0]
            );
            assert!(
                said[0].contains("AIB 網頁要求:repo.register|"),
                "reason: window_words_lost {}",
                said[0]
            );
        }
    }

    /// 0eed7 VERIFY-ownertouch-andy M1 (N-webonly-c45): the window lives as long as the ticket
    /// and no longer; one nobody answered is expired, not refused, starts no cool-down and
    /// leaves the gate free for the next ask at once.
    #[test]
    fn a_window_nobody_answers_closes_with_the_ticket_and_is_not_a_no() {
        struct Limit(std::sync::Mutex<Option<std::time::Duration>>);
        impl crate::touch::Presence for Limit {
            fn ask(&self, _reason: &str, _msg: &[u8]) -> Result<(), &'static str> {
                Ok(())
            }
            fn ask_until(
                &self,
                _reason: &str,
                _msg: &[u8],
                limit: std::time::Duration,
            ) -> Result<(), &'static str> {
                *self.0.lock().unwrap() = Some(limit);
                Err(crate::touch::TIMEOUT)
            }
        }
        let p = std::sync::Arc::new(Limit(std::sync::Mutex::new(None)));
        let o = std::sync::Arc::new(Owner::new(p.clone(), gate(), || 30_000));
        let a = reg();
        let t = ticket(o.ask(&a, inline));
        assert_eq!(
            *p.0.lock().unwrap(),
            Some(std::time::Duration::from_secs(TICKET_SECS)),
            "reason: window_without_the_tickets_limit"
        );
        assert_eq!(
            o.state(&t, &a.by),
            State::Expired,
            "reason: timeout_read_as_a_no"
        );
        assert!(!o.approved_for(&t, &a), "reason: timeout_approved");
        // not busy, not cooling: the next window opens at once
        assert!(
            matches!(o.ask(&a, inline), Asked::Ticket(_)),
            "reason: gate_held_or_cooled_after_a_timeout"
        );
        // a no still cools (the difference a timeout must not blur)
        let o = std::sync::Arc::new(Owner::new(
            std::sync::Arc::new(Says(Err("cancel"))),
            gate(),
            || 30_000,
        ));
        let _ = ticket(o.ask(&a, inline));
        assert_eq!(
            o.ask(&a, inline),
            Asked::Unavailable("pair_touch_cooling"),
            "reason: a_no_did_not_cool"
        );
    }

    /// 5d33a PREREAD-touchclose L1: the ticket's end is the one clock; a yes that comes back
    /// after it approves nothing.
    #[test]
    fn a_yes_after_the_tickets_end_approves_nothing() {
        static LATE: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(50_000);
        struct Slow;
        impl crate::touch::Presence for Slow {
            fn ask(&self, _reason: &str, _msg: &[u8]) -> Result<(), &'static str> {
                // the owner's finger lands one second after the ticket ended
                LATE.fetch_add(TICKET_SECS + 1, std::sync::atomic::Ordering::SeqCst);
                Ok(())
            }
        }
        let o = std::sync::Arc::new(Owner::new(std::sync::Arc::new(Slow), gate(), || {
            LATE.load(std::sync::atomic::Ordering::SeqCst)
        }));
        let a = reg();
        let t = ticket(o.ask(&a, inline));
        assert!(!o.approved_for(&t, &a), "reason: late_yes_approved");
        assert_eq!(
            o.state(&t, &a.by),
            State::Expired,
            "reason: late_yes_not_expired"
        );
    }
}
