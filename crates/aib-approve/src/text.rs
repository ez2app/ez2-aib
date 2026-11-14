//! Text shown to the person in an approval window.
//!
//! 1.x had two behaviours: `touchid_approve.clean` replaced control characters
//! with spaces and dropped invisible ones, while `consent._has_bad_char` refused
//! them (by Unicode category). 2.0 refuses, with the consent rule (PLAN-b2 W2): a character the person cannot see must not
//! change what they approve, and a replaced character changes the meaning just as
//! much as a hidden one. Too long is refused too, never cut, because the part cut
//! off may be the warning or the real host (1.x `approve_gate`, @aeb40 #404 R1).

/// Whole-line limit of the owner-check window (1.x `LA_MAX`, `consent.REASON_MAX`).
pub const LA_MAX: usize = 160;
/// Limit for a hardware-key signing reason (1.x `SE_SIGN_MAX`, helper `maxSignReason`).
pub const SE_SIGN_MAX: usize = 320;
/// Limit for an unseal reason (1.x `UNSEAL_MAX`, helper `maxUnsealReason`).
pub const UNSEAL_MAX: usize = 200;
/// Limit for a field shown in full (1.x `FULL_MAX`).
pub const FULL_MAX: usize = 320;

/// Why a text was refused.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum TextError {
    /// Nothing to show.
    Empty,
    /// A control, invisible or direction-changing character at this code-point index.
    BadChar { index: usize, code: u32 },
    /// More code points than allowed.
    TooLong { len: usize, max: usize },
}

impl TextError {
    /// The 1.x `detail` word for this refusal.
    pub fn detail(&self) -> &'static str {
        match self {
            TextError::Empty => "no_text",
            TextError::BadChar { .. } => "bad_char",
            TextError::TooLong { .. } => "too_long",
        }
    }
}

/// The Unicode version the category table is from. Pinned (exact crate version in
/// Cargo.toml) so a dependency update cannot quietly change what is refused.
pub const UNICODE_VERSION: (u64, u64, u64) = unicode_general_category::UNICODE_VERSION;

/// Characters whose category looks harmless (Mn / Lo / So / Zs) but which show as
/// nothing: 1.x `consent._INVISIBLE_EXTRA` (combining grapheme joiner, Hangul
/// fillers, Khmer inherent vowels, Mongolian selectors, braille blank, variation
/// selectors, halfwidth Hangul filler, variation selectors supplement).
fn invisible_extra(u: u32) -> bool {
    matches!(u,
        0x034f
        | 0x115f | 0x1160
        | 0x17b4 | 0x17b5
        | 0x180b..=0x180f
        | 0x2800
        | 0x3164
        | 0xfe00..=0xfe0f
        | 0xffa0
        | 0xe0100..=0xe01ef
    )
}

/// True for a character the person cannot see, or one that changes how the rest
/// of the line is laid out. Same rule as 1.x `consent._has_bad_char`: the
/// categories control (Cc), format (Cf, which holds the zero-width and direction
/// marks), line and paragraph separator, private use, unassigned and surrogate; any
/// space except U+0020; plus [`invisible_extra`].
pub fn is_bad_char(c: char) -> bool {
    use unicode_general_category::{get_general_category, GeneralCategory as G};
    if invisible_extra(c as u32) {
        return true;
    }
    match get_general_category(c) {
        G::Control
        | G::Format
        | G::LineSeparator
        | G::ParagraphSeparator
        | G::PrivateUse
        | G::Unassigned
        | G::Surrogate => true,
        G::SpaceSeparator => c != ' ',
        _ => false,
    }
}

/// Accept `text` as it is, or refuse it. Never changes the text.
pub fn check(text: &str, max: usize) -> Result<&str, TextError> {
    if text.trim().is_empty() {
        return Err(TextError::Empty);
    }
    let mut len = 0usize;
    for (index, c) in text.chars().enumerate() {
        if is_bad_char(c) {
            return Err(TextError::BadChar {
                index,
                code: c as u32,
            });
        }
        len = index + 1;
    }
    if len > max {
        return Err(TextError::TooLong { len, max });
    }
    Ok(text)
}
