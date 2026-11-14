//! Approval categories and the operation matrix.
//!
//! The 1.x table is `approve_gate.CATEGORIES` (aib/src/cockpit/approve_gate.py):
//! 17 high-risk categories that never get a time window, and three that may.
//! [`Category::legacy_max_window_s`] keeps the 1.x numbers so the Python shim can be
//! compared against this crate. [`Category::max_window_s`] is the 2.0 policy:
//! ADR 0003 puts "run an arbitrary system command" in Risk 3, so `command` loses
//! the 3 h window it had in 1.x (Codex review of PLAN v0, point 2).
//!
//! None of this is a defence against an AI running as the same user: it can edit
//! the binary that holds this table. It keeps the core from asking for the wrong
//! kind of proof by mistake. The defence is the proof itself.

use crate::proof::ProofLevel;

const H: u64 = 3600;

/// One approval category. Unknown names map to [`Category::Unregistered`], which
/// is high risk.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Category {
    ReleaseSign,
    LicenseSign,
    Payment,
    DeleteData,
    TrustedDevice,
    WpLogin,
    Quote,
    Secret,
    CfToken,
    PageBind,
    PhonePair,
    ProjectRuling,
    AutoUpdate,
    WindowGrant,
    PixelRealGen,
    HostRescue,
    Unregistered,
    SeatInput,
    Outbound,
    Command,
}

/// Every category, in the 1.x table order.
pub const ALL: [Category; 20] = [
    Category::ReleaseSign,
    Category::LicenseSign,
    Category::Payment,
    Category::DeleteData,
    Category::TrustedDevice,
    Category::WpLogin,
    Category::Quote,
    Category::Secret,
    Category::CfToken,
    Category::PageBind,
    Category::PhonePair,
    Category::ProjectRuling,
    Category::AutoUpdate,
    Category::WindowGrant,
    Category::PixelRealGen,
    Category::HostRescue,
    Category::Unregistered,
    Category::SeatInput,
    Category::Outbound,
    Category::Command,
];

impl Category {
    /// The id used in 1.x files, i18n keys and grants.
    pub fn id(self) -> &'static str {
        match self {
            Category::ReleaseSign => "release_sign",
            Category::LicenseSign => "license_sign",
            Category::Payment => "payment",
            Category::DeleteData => "delete_data",
            Category::TrustedDevice => "trusted_device",
            Category::WpLogin => "wp_login",
            Category::Quote => "quote",
            Category::Secret => "secret",
            Category::CfToken => "cf_token",
            Category::PageBind => "page_bind",
            Category::PhonePair => "phone_pair",
            Category::ProjectRuling => "project_ruling",
            Category::AutoUpdate => "auto_update",
            Category::WindowGrant => "window_grant",
            Category::PixelRealGen => "pixel_real_gen",
            Category::HostRescue => "host_rescue",
            Category::Unregistered => "unregistered",
            Category::SeatInput => "seat_input",
            Category::Outbound => "outbound",
            Category::Command => "command",
        }
    }

    /// Parse an id. Anything not in the table is [`Category::Unregistered`]
    /// (high risk), never an error that a caller could treat as "no rule".
    pub fn parse(id: &str) -> Category {
        ALL.iter()
            .copied()
            .find(|c| c.id() == id)
            .unwrap_or(Category::Unregistered)
    }

    /// 1.x `high_risk`: no time window, one press per action.
    pub fn legacy_high_risk(self) -> bool {
        !matches!(
            self,
            Category::SeatInput | Category::Outbound | Category::Command
        )
    }

    /// 1.x `max_window` in seconds (0 for high risk).
    pub fn legacy_max_window_s(self) -> u64 {
        match self {
            Category::SeatInput => 24 * H,
            Category::Outbound | Category::Command => 3 * H,
            _ => 0,
        }
    }

    /// 2.0 policy: the longest time window a grant may cover. `command` is Risk 3
    /// in ADR 0003, so it gets none.
    pub fn max_window_s(self) -> u64 {
        match self {
            Category::SeatInput => 24 * H,
            Category::Outbound => 3 * H,
            _ => 0,
        }
    }

    /// The weakest proof this category accepts for a single action (matrix v0,
    /// PLAN-b2 §3; to be reviewed with the first PR).
    ///
    /// Seat input, outbound messages and approved commands (consent) take a fresh
    /// owner check (fingerprint or login password) inside a trusted flow, as 1.x
    /// does today. Everything that signs, pays, deletes, hands out a secret, trusts
    /// a device or binds an account, and anything unregistered, needs a signature by
    /// a hardware-held key over the full binding.
    pub fn required_proof(self) -> ProofLevel {
        match self {
            Category::SeatInput | Category::Outbound | Category::Command => {
                ProofLevel::PresenceOnly
            }
            _ => ProofLevel::HardwareSigned,
        }
    }
}

/// The 2.0 window cap over all categories (1.x `WINDOW_CAP_S`, 7 h), applied on
/// top of [`Category::max_window_s`].
pub const WINDOW_CAP_S: u64 = 7 * H;

/// The window a grant for `cat` may actually cover, after both caps.
pub fn window_allowed_s(cat: Category) -> u64 {
    cat.max_window_s().min(WINDOW_CAP_S)
}
