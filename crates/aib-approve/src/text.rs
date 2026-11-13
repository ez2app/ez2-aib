//! Text shown to the person in an approval window.
//!
//! 1.x had two behaviours: `touchid_approve.clean` replaced control characters
//! with spaces and dropped invisible ones, while `consent._has_bad_char` refused
//! them. 2.0 refuses (PLAN-b2 W2): a character the person cannot see must not
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

/// True for a character the person cannot see, or one that changes how the rest
/// of the line is laid out. Covers the 1.x `_CTRL` and `_INVISIBLE` sets plus the
/// extra ranges aib-expect `clean()` refuses (180E, FE00–FE0F, E0000–E007F).
pub fn is_bad_char(c: char) -> bool {
    let u = c as u32;
    matches!(u,
        0x00..=0x1f | 0x7f..=0x9f          // C0, DEL, C1
        | 0x2028 | 0x2029                   // line / paragraph separator
        | 0xad | 0x61c | 0x180e             // soft hyphen, Arabic letter mark, Mongolian vowel separator
        | 0x200b..=0x200f                   // zero width, LRM / RLM
        | 0x202a..=0x202e                   // embeddings / overrides
        | 0x2060..=0x2069                   // word joiner, invisible operators, isolates
        | 0xfeff                            // BOM / zero-width no-break space
        | 0xfe00..=0xfe0f                   // variation selectors
        | 0xe0000..=0xe007f                 // tag characters
    )
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
