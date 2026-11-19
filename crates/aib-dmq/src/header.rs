//! The dm header, translated as it is from `aib/src/cockpit/dm_header.py` (B1dm-header; 519d0
//! 11-18 07:34: the display format the same, the old behavior the acceptance).
//!
//! New form (the one written):
//! `IP:<addr>/<cabin>/<name>@<code> >> IP:<addr>/<cabin>/<name>@<code> MM-DD HH:MM(<zone>)`,
//! on the same line as the text, one space between. Old forms are read only:
//! `[<sender> > <receiver> / 台北 MM-DD HH:MM] text`, with or without `<addr>·<cabin>·` before a
//! token. All three read into the same [`Parsed`] shape.
//!
//! Not here: the global (Nostr) form (`dm_global_header.py`, its own slice) and picking this
//! machine's address (the caller passes it; that is the environment, not the format).

use regex::{Captures, Regex};
use std::sync::OnceLock;

pub const HUMAN_NAME: &str = "the maintainer";
pub const HUMAN_CODE: &str = "hub";
pub const SYSTEM_NAME: &str = "系統";
pub const SYSTEM_CODE: &str = "aib";
pub const UNBOUND_NAME: &str = "未綁";
pub const DEFAULT_TZ: &str = "Asia/Taipei";
pub const ARROW: &str = " >> ";
pub const LOOPBACK: &str = "127.0.0.1";
pub const ROOM_ORIGIN: &str = "captain-room";
pub const ROOM_CODE: &str = "room";
pub const ROOM_NAME: &str = "艦長室";
pub const HUMAN_ORIGINS: &[&str] = &["mouse_ctl"];
pub const SYSTEM_LABELS: &[&str] = &[
    "開席通知",
    "排序暫存",
    "系統",
    "巡查",
    "aib-system",
    "系統狀態通知",
    "自動降溫",
    "強制回報點",
    "艦隊狀態",
    "指令訊息轉送",
    "開啟六席艦隊",
    "AI 啟動器",
    "CodexBot活檢",
    "DotsBot活檢",
];

/// One side: `{addr, cabin, name, code}` (the code field is `code`).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Party {
    pub addr: String,
    pub cabin: String,
    pub name: String,
    pub code: String,
}

/// `party()`: an empty name is 未綁; the code loses a leading `@`.
pub fn party(code: &str, name: &str, addr: &str, cabin: &str) -> Party {
    let name = name.trim();
    Party {
        addr: addr.to_owned(),
        cabin: cabin.to_owned(),
        name: if name.is_empty() { UNBOUND_NAME } else { name }.to_owned(),
        code: code.trim().trim_start_matches('@').to_owned(),
    }
}

/// The zone's label; a zone without one is printed as its name.
pub fn tz_label(tz: &str) -> &str {
    match tz {
        "Asia/Taipei" => "台北時間",
        "Asia/Tokyo" => "東京時間",
        other => other,
    }
}

/// Seconds east of UTC for the zones this format is written in. Neither has had summer time
/// since 1980; Taipei's summers of 1974, 1975 and 1979 (which Python's zone database keeps) are
/// not, since no message carries a time from then.
pub fn tz_offset(tz: &str) -> Option<i64> {
    match tz {
        "Asia/Taipei" => Some(8 * 3600),
        "Asia/Tokyo" => Some(9 * 3600),
        "UTC" => Some(0),
        _ => None,
    }
}

/// `(month, day)` of a day count since 1970-01-01 (proleptic Gregorian).
fn month_day(days: i64) -> (i64, i64) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (m, d)
}

/// `MM-DD HH:MM(<zone label>)` for epoch seconds; `None` for a zone without a known offset.
pub fn format_when(epoch: i64, tz: &str) -> Option<String> {
    let t = epoch + tz_offset(tz)?;
    let (days, secs) = (t.div_euclid(86_400), t.rem_euclid(86_400));
    let (m, d) = month_day(days);
    Some(format!(
        "{m:02}-{d:02} {:02}:{:02}({})",
        secs / 3600,
        secs % 3600 / 60,
        tz_label(tz)
    ))
}

/// `IP:<addr>/<cabin>/<name>@<code>`; an empty code is refused.
pub fn side(p: &Party) -> Result<String, String> {
    let addr = if p.addr.is_empty() { LOOPBACK } else { &p.addr };
    let name = p.name.trim();
    let name = if name.is_empty() { UNBOUND_NAME } else { name };
    let code = p.code.trim().trim_start_matches('@');
    if code.is_empty() {
        return Err(format!("席碼不可空:{p:?}"));
    }
    Ok(format!("IP:{addr}/{}/{name}@{code}", p.cabin))
}

/// The new-form header line (no newline).
pub fn build(sender: &Party, receiver: &Party, epoch: i64, tz: &str) -> Result<String, String> {
    let when = format_when(epoch, tz).ok_or_else(|| format!("unknown zone {tz}"))?;
    Ok(format!(
        "{}{ARROW}{} {when}",
        side(sender)?,
        side(receiver)?
    ))
}

/// What [`parse`] read.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Parsed {
    /// `v2`, `legacy_at` or `legacy_ip`.
    pub form: &'static str,
    pub sender: Party,
    pub receiver: Party,
    /// `MM-DD HH:MM`, or empty.
    pub when: String,
    /// `台北時間`-like, or empty.
    pub tz: String,
    /// `system`, `human`, `seat` or `other`.
    pub sender_kind: &'static str,
    pub body: String,
}

const PARTY: &str = r"IP:(?P<{p}addr>[^/\s]+)/(?P<{p}cabin>[^/\s]*)/(?P<{p}name>[^\s@][^@]*?)@(?P<{p}code>[^\s/@>]+)";

/// A compiled constant pattern; `None` only if the constant itself were broken (the tests
/// would show it: nothing would parse).
fn re(
    cell: &'static OnceLock<Option<Regex>>,
    src: impl FnOnce() -> String,
) -> Option<&'static Regex> {
    cell.get_or_init(|| Regex::new(&src()).ok()).as_ref()
}

fn v2_re() -> Option<&'static Regex> {
    static R: OnceLock<Option<Regex>> = OnceLock::new();
    re(&R, || {
        format!(
            r"^{}{}{} (?P<when>\d\d-\d\d \d\d:\d\d)\((?P<tz>[^()\s]+)\)",
            PARTY.replace("{p}", "s_"),
            regex::escape(ARROW),
            PARTY.replace("{p}", "r_")
        )
    })
}

fn legacy_re() -> Option<&'static Regex> {
    static R: OnceLock<Option<Regex>> = OnceLock::new();
    re(&R, || {
        r"^\s*\[(?P<s>[^\]>]+?) > (?P<r>[^\]/]+?)(?: / (?P<tz>\S+) (?P<when>\d\d-\d\d \d\d:\d\d))?\] ?"
            .to_owned()
    })
}

fn place_re() -> Option<&'static Regex> {
    static R: OnceLock<Option<Regex>> = OnceLock::new();
    re(&R, || r"^(?P<segs>(?:[^\s·@\[\]>]+·){1,2})".to_owned())
}

fn code_token_re() -> Option<&'static Regex> {
    static R: OnceLock<Option<Regex>> = OnceLock::new();
    re(&R, || {
        r"^@(?P<code>[0-9a-z]{4,8})(?:[(](?P<nick>[^)]*)[)])?$".to_owned()
    })
}

fn group(c: &Captures<'_>, name: &str) -> String {
    c.name(name).map_or("", |m| m.as_str()).to_owned()
}

fn from_groups(c: &Captures<'_>, p: &str) -> Party {
    Party {
        addr: group(c, &format!("{p}addr")),
        cabin: group(c, &format!("{p}cabin")),
        name: group(c, &format!("{p}name")),
        code: group(c, &format!("{p}code")),
    }
}

fn all_in(s: &str, ok: impl Fn(char) -> bool) -> bool {
    !s.is_empty() && s.chars().all(ok)
}

/// `_result`'s sender kind: 系統@aib is the system, `hub` a person, a 4–8 character lower-case
/// code a seat (unless grokbot / unknown, or named 未認證 / 艙外), anything else other.
pub fn sender_kind(sender: &Party) -> &'static str {
    let code = sender.code.as_str();
    if code == SYSTEM_CODE && sender.name == SYSTEM_NAME {
        "system"
    } else if code == HUMAN_CODE {
        "human"
    } else if (4..=8).contains(&code.chars().count())
        && all_in(code, |c| c.is_ascii_digit() || c.is_ascii_lowercase())
        && !matches!(code, "grokbot" | "unknown")
        && !matches!(sender.name.as_str(), "未認證" | "艙外")
    {
        "seat"
    } else {
        "other"
    }
}

fn result(
    form: &'static str,
    sender: Party,
    receiver: Party,
    when: String,
    tz: String,
    body: String,
) -> Parsed {
    let sender_kind = sender_kind(&sender);
    Parsed {
        form,
        sender,
        receiver,
        when,
        tz,
        sender_kind,
        body,
    }
}

/// The new form or an old form; `None` when neither (the global form is not read here).
pub fn parse(text: &str) -> Option<Parsed> {
    if text.is_empty() {
        return None;
    }
    if let Some(c) = v2_re().and_then(|r| r.captures(text)) {
        let end = c.get(0).map_or(0, |m| m.end());
        let rest = &text[end..];
        // the Python pattern's (?= |\n|$)
        if rest.is_empty() || rest.starts_with(' ') || rest.starts_with('\n') {
            let rest = rest
                .strip_prefix(' ')
                .or_else(|| rest.strip_prefix('\n'))
                .unwrap_or(rest);
            return Some(result(
                "v2",
                from_groups(&c, "s_"),
                from_groups(&c, "r_"),
                group(&c, "when"),
                group(&c, "tz"),
                rest.to_owned(),
            ));
        }
    }
    parse_legacy(text)
}

/// `<addr>·<cabin>·` before a token: `(token, addr, cabin)`.
fn strip_place(token: &str) -> (&str, String, String) {
    let Some(m) = place_re()
        .and_then(|r| r.captures(token))
        .and_then(|c| c.name("segs"))
    else {
        return (token, String::new(), String::new());
    };
    let all: Vec<&str> = m.as_str().split('·').collect();
    let segs = &all[..all.len() - 1];
    let addr = if segs.len() == 2 { segs[0] } else { "" };
    let cabin = if segs.len() == 2 {
        segs[1]
    } else if segs[0].chars().count() == 4
        && all_in(segs[0], |c| matches!(c, '0'..='9' | 'a'..='f'))
    {
        segs[0]
    } else {
        ""
    };
    let addr = if segs.len() == 1 && cabin.is_empty() {
        segs[0]
    } else {
        addr
    };
    (&token[m.end()..], addr.to_owned(), cabin.to_owned())
}

/// An old-form token (dm-ctl's S_LABEL / T_LABEL) as a side; `origin` is the sender origin's
/// machine name when there is one.
pub fn party_from_label(label: &str, origin: &str) -> Party {
    let (token, addr, cabin) = strip_place(label.trim());
    let p = |code: &str, name: &str| party(code, name, &addr, &cabin);
    if let Some(c) = code_token_re().and_then(|r| r.captures(token)) {
        return p(&group(&c, "code"), &group(&c, "nick"));
    }
    if let Some(hid) = token.strip_prefix("human:") {
        let name = if matches!(hid.to_lowercase().as_str(), "" | "human" | "user") {
            HUMAN_NAME
        } else {
            hid
        };
        return p(HUMAN_CODE, name);
    }
    if HUMAN_ORIGINS.contains(&origin) {
        return p(HUMAN_CODE, HUMAN_NAME);
    }
    if origin == "grokbot" || token.starts_with("GrokBot") {
        return p("grokbot", "GrokBot");
    }
    if origin == ROOM_ORIGIN || token.starts_with(&format!("unattested:{ROOM_ORIGIN}")) {
        return p(ROOM_CODE, ROOM_NAME);
    }
    if let Some(rest) = token.strip_prefix("seat_claimed:") {
        let code = rest.split('(').next().unwrap_or("");
        let code = code.split('（').next().unwrap_or("");
        if code.len() == 5 && all_in(code, |c| matches!(c, '0'..='9' | 'a'..='f')) {
            return p(code, "自稱");
        }
    }
    if let Some(rest) = token.strip_prefix("unattested:") {
        return p(if rest.is_empty() { "unknown" } else { rest }, "未認證");
    }
    if let Some(rest) = token.strip_prefix("agent:") {
        return p(if rest.is_empty() { "agent" } else { rest }, "艙外");
    }
    if !origin.is_empty() || SYSTEM_LABELS.iter().any(|l| token.starts_with(l)) {
        return p(SYSTEM_CODE, SYSTEM_NAME);
    }
    p("", token)
}

/// `[<sender> > <receiver> / 台北 MM-DD HH:MM] text`; without the time part both tokens must
/// be ones it knows (a person's `[a > b]` is not a dm).
pub fn parse_legacy(text: &str) -> Option<Parsed> {
    let c = legacy_re()?.captures(text)?;
    let (s_tok, r_tok) = (group(&c, "s"), group(&c, "r"));
    let (s_tok, r_tok) = (s_tok.trim(), r_tok.trim());
    let sender = party_from_label(s_tok, "");
    let receiver = party_from_label(r_tok, "");
    let when = group(&c, "when");
    if when.is_empty() && (sender.code.is_empty() || receiver.code.is_empty()) {
        return None;
    }
    let placed = place_re().is_some_and(|r| r.is_match(s_tok) || r.is_match(r_tok));
    let tz = group(&c, "tz");
    let tz = if tz.is_empty() || tz.ends_with("時間") {
        tz
    } else {
        format!("{tz}時間")
    };
    let end = c.get(0).map_or(0, |m| m.end());
    Some(result(
        if placed { "legacy_ip" } else { "legacy_at" },
        sender,
        receiver,
        when,
        tz,
        text[end..].to_owned(),
    ))
}

/// A cabin id's tail: after the last `-`, at most four characters (13 -> 13, main -> main).
pub fn cabin_tail(instance: &str) -> String {
    let tail = instance.rsplit('-').next().unwrap_or("");
    let n = tail.chars().count();
    tail.chars().skip(n.saturating_sub(4)).collect()
}

/// `aihub-<instance>` -> the instance's tail; any other socket -> `local_cabin`.
pub fn cabin_of_socket(socket: &str, local_cabin: &str) -> String {
    let s = socket.trim();
    match s.strip_prefix("aihub-") {
        Some(rest) if !rest.is_empty() => cabin_tail(rest),
        _ => local_cabin.to_owned(),
    }
}

/// What [`build_from_labels`] is given besides the two tokens (dm-ctl's arguments; the local
/// address and cabin are the caller's, as `local_addr()` / `local_cabin()` read them).
#[derive(Debug, Clone, Default)]
pub struct FromLabels<'a> {
    pub s_origin: &'a str,
    pub s_socket: &'a str,
    pub t_socket: &'a str,
    /// Across machines: the address sshd saw; then `s_note` is put after the sender's name.
    pub s_addr: &'a str,
    pub s_note: &'a str,
    pub local_addr: &'a str,
    pub local_cabin: &'a str,
}

/// dm-ctl's tokens as a new-form header (Taipei time). Same machine: both sides get the local
/// address, each its socket's cabin. Across machines the sender's cabin is never filled from the
/// receiving machine's: `aihub` is main, an unknown socket leaves it blank.
pub fn build_from_labels(
    s_label: &str,
    t_label: &str,
    o: &FromLabels<'_>,
    epoch: i64,
) -> Result<String, String> {
    let mut s = party_from_label(s_label, o.s_origin);
    let mut r = party_from_label(t_label, "");
    s.addr = if o.s_addr.is_empty() {
        o.local_addr
    } else {
        o.s_addr
    }
    .to_owned();
    s.cabin = cabin_of_socket(o.s_socket, o.local_cabin);
    if !o.s_addr.is_empty() {
        let sock = o.s_socket.trim();
        s.cabin = match sock.strip_prefix("aihub-") {
            _ if sock == "aihub" => "main".to_owned(),
            Some(rest) => cabin_tail(rest),
            None => String::new(),
        };
    }
    r.addr = o.local_addr.to_owned();
    let t_sock = if o.t_socket.is_empty() {
        o.s_socket
    } else {
        o.t_socket
    };
    r.cabin = cabin_of_socket(t_sock, o.local_cabin);
    if !o.s_note.is_empty() {
        s.name = format!("{}({})", s.name, o.s_note);
    }
    build(&s, &r, epoch, DEFAULT_TZ)
}
