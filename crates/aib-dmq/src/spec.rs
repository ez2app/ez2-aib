//! Statuses, the transition table, refusal reasons and exit codes, copied from the frozen
//! 1.x `dm_queue.py` (STATUSES, ACTIVE_STATUSES, TRANSITIONS, TRANSITION_REASON,
//! REV_REASON, _TRANSITION_RC). The fixture test compares these tables with the fixture
//! header, so a drift on either side turns red.

use crate::actor::ActorClass;

pub const ITEM_SCHEMA: &str = "aib-dm-queue-item/v2";
pub const LEGACY_ITEM_SCHEMAS: &[&str] = &["aib-dm-queue-item/v1"];
pub const ORDER_SCHEMA: &str = "aib-dm-queue-order/v1";

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Status {
    Queued,
    Claimed,
    Delivered,
    Read,
    Done,
    Archived,
    Deleted,
    HoldHuman,
}

impl Status {
    /// In the 1.x order (STATUSES).
    pub const ALL: [Status; 8] = [
        Status::Queued,
        Status::Claimed,
        Status::Delivered,
        Status::Read,
        Status::Done,
        Status::Archived,
        Status::Deleted,
        Status::HoldHuman,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            Status::Queued => "queued",
            Status::Claimed => "claimed",
            Status::Delivered => "delivered",
            Status::Read => "read",
            Status::Done => "done",
            Status::Archived => "archived",
            Status::Deleted => "deleted",
            Status::HoldHuman => "hold_human",
        }
    }

    pub fn parse(s: &str) -> Option<Status> {
        Status::ALL.into_iter().find(|x| x.as_str() == s)
    }

    /// Still in order.json: sorted, claimed, drawn in the waiting box (ACTIVE_STATUSES).
    pub fn is_active(self) -> bool {
        matches!(self, Status::Queued | Status::Claimed | Status::HoldHuman)
    }

    /// The waiting tab's rows (WAITING_STATUSES).
    pub fn is_waiting(self) -> bool {
        self.is_active() || self == Status::Delivered
    }
}

pub const ACTIVE: [Status; 3] = [Status::Queued, Status::Claimed, Status::HoldHuman];

use ActorClass::{Agent, Human, Seat, System};

/// Who may move an item from one status to another. `None` = that move does not exist.
/// A seat actor must also be the receiving seat; an agent actor's seat part must be too
/// (checked in [`crate::transition::decide`]).
pub fn allowed(from: Status, to: Status) -> Option<&'static [ActorClass]> {
    use Status::*;
    Some(match (from, to) {
        (Queued, Claimed) => &[System],
        (Claimed, Queued) => &[System],
        (Claimed, Delivered) => &[System],
        (Claimed, HoldHuman) => &[System],
        (Queued, HoldHuman) => &[System],
        (HoldHuman, Queued) => &[Human],
        (Queued, Archived) => &[System],
        (HoldHuman, Archived) => &[System],
        (Delivered, Read) => &[Seat, System, Agent],
        (Queued, Read) => &[Seat, System, Agent],
        (HoldHuman, Read) => &[Seat, Human],
        (Delivered, Done) => &[Seat, System, Agent],
        (Read, Done) => &[Seat, System, Agent],
        (Delivered, Archived) => &[System],
        (Read, Archived) => &[System],
        (Done, Archived) => &[System],
        (Done, Read) => &[Seat, Human],
        (Archived, Deleted) => &[Human, System],
        (f, Deleted) if f != Deleted => &[Human],
        _ => return None,
    })
}

/// Why a system actor may archive (ARCHIVE_WHYS).
pub const ARCHIVE_WHYS: &[&str] = &[
    "days",
    "superseded",
    AUTO_ARCHIVE_WHY,
    "duplicate",
    "test_ping",
    "notice_read",
];
pub const AUTO_ARCHIVE_WHY: &str = "done_12h";
pub const AUTO_DELETE_WHY: &str = "archived_30d";
pub const AUTO_READ_WHY: &str = "auto_notice";
pub const ONBOARD_ACK_WHY: &str = "onboard_acked";
pub const SYSTEM_READ_WHYS: &[&str] = &[AUTO_READ_WHY, ONBOARD_ACK_WHY];
pub const SUPERSEDE_DONE_PREFIX: &str = "superseded_by:";
pub const REOPEN_WHY_PREFIX: &str = "reopen:";
/// The start of EXPIRED_HOLD_WHY up to its first "(": a hold with this why sends `expired`.
pub const EXPIRED_HOLD_WHY_HEAD: &str = "已到期";

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Reason {
    Ok,
    Transition,
    LockTimeout,
    Actor,
    ActorMissing,
    Evidence,
    NotFound,
    ImRoomOnly,
    NoRev,
    RevMismatch,
}

impl Reason {
    /// TRANSITION_REASON keys (sorted the way the fixture header lists them).
    pub const TRANSITION: [Reason; 8] = [
        Reason::Actor,
        Reason::ActorMissing,
        Reason::Evidence,
        Reason::ImRoomOnly,
        Reason::LockTimeout,
        Reason::NotFound,
        Reason::Ok,
        Reason::Transition,
    ];
    /// REV_REASON keys, sorted.
    pub const REV: [Reason; 4] = [
        Reason::NoRev,
        Reason::NotFound,
        Reason::Ok,
        Reason::RevMismatch,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            Reason::Ok => "ok",
            Reason::Transition => "transition",
            Reason::LockTimeout => "lock_timeout",
            Reason::Actor => "actor",
            Reason::ActorMissing => "actor_missing",
            Reason::Evidence => "evidence",
            Reason::NotFound => "not_found",
            Reason::ImRoomOnly => "im_room_only",
            Reason::NoRev => "no_rev",
            Reason::RevMismatch => "rev_mismatch",
        }
    }

    /// The message 1.x prints with the refusal (TRANSITION_REASON / REV_REASON).
    pub fn message(self) -> &'static str {
        match self {
            Reason::Ok => "",
            Reason::Transition => "這個狀態不能這樣改",
            Reason::LockTimeout => "這一席的信正被別的程式改著(等了 10 秒還沒放),這次沒改;稍後再試一次",
            Reason::Actor => "這個動作不是你能做的",
            Reason::ActorMissing => "要帶 --by(收件席讀完請用 read,不要 delete)",
            Reason::Evidence => "完成要帶證據(存在的檔 / 你寄出的回信 id / 查得到的 commit / 完整 PR 網址 https://github.com/ez2app/…/pull/N;夾在說明文字裡也可以,只寫 #N 不算),或用 --why 寫結案理由",
            Reason::NotFound => "這封信已經不在了",
            Reason::ImRoomOnly => "Telegram 來信只在艦長室看,還不能放進 AI 框(第 39 節第二步做好前)",
            Reason::NoRev => "這一幀沒有修訂號,重畫後再按",
            Reason::RevMismatch => "這封信在你確認前被改過(或已被處理)",
        }
    }

    /// The exit code a refused read / done / delete / requeue / reopen returns
    /// (_TRANSITION_RC; `ok` is 0). 1.x falls back to 4 for a reason not in its table;
    /// every reason here is in it.
    pub fn exit_code(self) -> i32 {
        match self {
            Reason::Ok => 0,
            Reason::NotFound => 4,
            Reason::RevMismatch => 5,
            Reason::NoRev => 6,
            Reason::Actor | Reason::ActorMissing => 7,
            Reason::Transition | Reason::ImRoomOnly => 8,
            Reason::Evidence => 9,
            Reason::LockTimeout => 10,
        }
    }
}

/// `--by` written but in a shape no class takes (ACTOR_FORMAT_HINT); the reason stays
/// `actor_missing`, only the message changes.
pub fn actor_format_hint(given: &str, seat: &str) -> String {
    format!("--by 格式不對(收到 {given}):收件席寫 seat:{seat},真人寫 human:<名字>,系統寫 system")
}
