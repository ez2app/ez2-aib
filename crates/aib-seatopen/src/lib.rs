// SPDX-License-Identifier: Apache-2.0
//! What a seat-open command means, from its words alone (B4-1a, PLAN-b4-cockpit-rust-a9136-1113).
//!
//! Port of the argument rules of the frozen 1.x launcher `aib/src/cockpit/aib`
//! (`aib <provider> <dgrs|safe> <role> [cabin|new] [<N>sits] [dm] [gameworld] [flags]`,
//! flag loop :1051–1104, trailing words :1110–1124, batch :1139–1150, fourth word
//! :1249–1266, checks :1289–1406, model shape :1473). [`parse_open`] gives an
//! [`OpenSpec`] or the first refusal 1.x gives for the same words, with its i18n key
//! and exit code. It touches no file, process or window, so it runs the same on
//! macOS, Linux and Windows.
//!
//! Left to the caller (B4-1b, `aibd ctl seat open`), in this order after a parse:
//! the cabin lookup (short code, socket, in-pane default), the kill switch, edition and
//! bot checks, the model default, whether the AI's program is installed, and every
//! side effect. `aib seat re-register` (its own argv) is not parsed here.
//!
//! The environment is read through `env` (tests inject it): `AIB_DRY_RUN`,
//! `EZ2_AIB_INSTANCE`, `EZ2_AIB_CAPTAIN_ROLES`, `AIB_ALLOW_CUSTOM_ROLE`. Pass
//! `|k| std::env::var(k).ok()`: on Windows that lookup already ignores case.
//!
//! Known differences from 1.x, both stricter:
//! - a batch count too large for `u32` (`99999999999sits`) is a usage error; 1.x passed
//!   it on to the batch script.
//! - `--thread` values are matched as ASCII; 1.x's bash glob ranges could follow the
//!   locale.

use std::fmt;

/// The AIs a seat can open with (1.x `PROVIDERS`, :147).
pub const PROVIDERS: &[&str] = &[
    "claude",
    "codex",
    "agy",
    "copilot",
    "grok",
    "deepseek",
    "nvidia",
    "openrouter",
    "typesafe",
    "openrouterjev",
    "grokbot",
    "codexbot",
    "claudebot",
    "musebot",
];

/// 1.x `MODES` (:148): `dgrs` = the AI changes files and runs commands without asking.
pub const MODES: &[&str] = &["dgrs", "safe"];

/// Roles a seat may be opened as: the 1.x `aib.role.<role>` keys (roles.py). Pinned to
/// the i18n file in this tree by a test, so a role added there turns that test red.
pub const ROLES: &[&str] = &[
    "advisor", "boards", "bot", "design", "dev", "monitor", "pm", "qa", "root",
];

/// The primary cabin's name (1.x `AIB_CABIN_PRIMARY`).
pub const PRIMARY_CABIN: &str = "main";

/// Exit codes of the seat-open path (1.x `die`), so callers do not each write their own.
pub mod rc {
    /// Every refusal of [`super::parse_open`]; also the cabin lookup's refusals.
    pub const USAGE: i32 = 2;
    /// The kill switch is on (`aib.err.killswitch`).
    pub const KILLSWITCH: i32 = 3;
    /// The AI's program is not installed (`aib.err.cli_missing`).
    pub const CLI_MISSING: i32 = 6;
    /// tmux is missing for a new cabin (`aib.err.tmux_missing`), 1.x only.
    pub const TMUX_MISSING: i32 = 127;
    /// A new cabin could not be reserved or given its token.
    pub const RESERVE_FAILED: i32 = 1;
}

/// What the words ask for.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    /// `-h` / `--help`: print the usage, exit 0.
    Help,
    /// `-V` / `--version`: print the version, exit 0.
    Version,
    /// Open a seat (or a batch of seats).
    Open(OpenSpec),
}

/// Which cabin the seat goes into, before any lookup.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Cabin {
    /// `new`, `--new`, `--instance new`, or a batch with `new`: a cabin of its own.
    New,
    /// A cabin named in the words (fourth word or `--instance`); a short code is still
    /// a name here. `main_explicit`: the name is the primary cabin, written by hand.
    Named { name: String, main_explicit: bool },
    /// Three words and nothing else: the cabin of the pane it is typed in, or a new one.
    Default,
    /// Three words with `--again` or `EZ2_AIB_INSTANCE` set: the caller's cabin.
    FromEnvironment,
}

/// The review panel (1.x `OPEN_DM`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Dm {
    /// Neither `dm` nor `--dm` / `--no-dm`: as remembered.
    Auto,
    On,
    Off,
}

/// A seat-open request, checked.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OpenSpec {
    pub provider: String,
    pub mode: String,
    pub role: String,
    pub cabin: Cabin,
    /// `--again`: one more seat in the same hub.
    pub again: bool,
    pub dm: Dm,
    pub reports_to: Option<String>,
    /// `--model`, shape-checked; `None` = the caller picks the default.
    pub model: Option<String>,
    /// `--thread <uuid>`.
    pub thread: Option<String>,
    pub dry_run: bool,
    pub probe_live: bool,
    /// The trailing word `gameworld`.
    pub gameworld: bool,
    /// `<N>sits`: open N seats.
    pub batch: Option<u32>,
}

/// The first refusal, as 1.x gives it for the same words.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum OpenError {
    /// `aib.err.usage`: a bad or empty flag value, an unknown flag, or flags that
    /// contradict each other.
    Usage,
    /// `aib.err.usage` after printing the usage: not exactly three words.
    Arity,
    /// `aib.err.unknown_cmd`: the first word is not a command or an AI.
    UnknownCmd(String),
    /// `aib.err.provider`
    Provider(String),
    /// `aib.err.mode`
    Mode(String),
    /// `aib.err.role`: not lower-case letters, digits and `-`.
    Role(String),
    /// `aib.err.role_undefined`: a well-formed role that is not defined.
    RoleUndefined(String),
    /// `aib.err.reports_to`
    ReportsTo(String),
    /// `aib.err.instance`
    Instance(String),
    /// `--thread` is not a UUID (1.x plain text).
    Thread(String),
    /// `dm` / `gameworld` left over in the cabin place (1.x plain text).
    ReservedCabin(String),
    /// The cabin name is empty (1.x `aib_cabin_check_name`, plain text).
    CabinEmpty,
    /// The cabin name holds a character other than `a-z 0-9 -` (plain text).
    CabinChars(String),
    /// The `--model` value has the wrong shape (plain text).
    Model,
}

impl OpenError {
    /// The exit code 1.x gives: every refusal here is [`rc::USAGE`].
    pub fn rc(&self) -> i32 {
        rc::USAGE
    }

    /// The 1.x i18n key, and its parameter, for the keyed refusals; `None` for the ones
    /// 1.x prints as plain text (see [`fmt::Display`]).
    pub fn key(&self) -> Option<(&'static str, Option<(&'static str, &str)>)> {
        Some(match self {
            OpenError::Usage | OpenError::Arity => ("aib.err.usage", None),
            OpenError::UnknownCmd(v) => ("aib.err.unknown_cmd", Some(("cmd", v))),
            OpenError::Provider(v) => ("aib.err.provider", Some(("provider", v))),
            OpenError::Mode(v) => ("aib.err.mode", Some(("mode", v))),
            OpenError::Role(v) => ("aib.err.role", Some(("role", v))),
            OpenError::RoleUndefined(v) => ("aib.err.role_undefined", Some(("role", v))),
            OpenError::ReportsTo(v) => ("aib.err.reports_to", Some(("seat", v))),
            OpenError::Instance(v) => ("aib.err.instance", Some(("instance", v))),
            OpenError::Thread(_)
            | OpenError::ReservedCabin(_)
            | OpenError::CabinEmpty
            | OpenError::CabinChars(_)
            | OpenError::Model => return None,
        })
    }

    /// 1.x prints the usage before this refusal.
    pub fn shows_usage(&self) -> bool {
        matches!(self, OpenError::Arity)
    }
}

impl fmt::Display for OpenError {
    /// The plain-text refusals word for word as 1.x prints them; a keyed one as
    /// `key` or `key name=value` (the caller renders it in the person's language).
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            OpenError::Thread(v) => write!(f, "aib: --thread 要 UUID(查法:aib bot list),不是:{v}"),
            OpenError::ReservedCabin(v) => write!(
                f,
                "aib: 「{v}」是開席尾端的保留字，不能當艙名(每個保留字最多寫一次)"
            ),
            OpenError::CabinEmpty => write!(
                f,
                "aib: 艙解析輸入為空:instance(空字串不是「沒給」——沒給請 unset。留著空字串會安靜地退回主艙)"
            ),
            OpenError::CabinChars(v) => write!(
                f,
                "aib: 艙解析輸入含非法字元:instance={v}(只准小寫 a-z、數字 0-9 與 -)"
            ),
            OpenError::Model => write!(f, "aib: requested model is missing or invalid"),
            _ => match self.key() {
                Some((k, Some((n, v)))) => write!(f, "{k} {n}={v}"),
                Some((k, None)) => write!(f, "{k}"),
                None => Ok(()),
            },
        }
    }
}

impl std::error::Error for OpenError {}

fn lower_word(s: &str) -> bool {
    s.bytes()
        .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

/// 1.x `ROLE_RE` `^[a-z0-9-]+$`.
fn role_shape(s: &str) -> bool {
    !s.is_empty() && lower_word(s)
}

/// 1.x `INSTANCE_RE` `^[a-z0-9][a-z0-9-]*$`.
fn instance_shape(s: &str) -> bool {
    s.bytes().next().is_some_and(|b| b != b'-') && lower_word(s)
}

/// 1.x `REPORTS_TO_RE` `^[0-9a-z]{4,8}$`.
fn reports_to_shape(s: &str) -> bool {
    (4..=8).contains(&s.len())
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
}

/// 1.x model shape `^[~]?[0-9A-Za-z][0-9A-Za-z._:/-]*$` (:1473).
fn model_shape(s: &str) -> bool {
    let s = s.strip_prefix('~').unwrap_or(s);
    let mut b = s.bytes();
    b.next().is_some_and(|c| c.is_ascii_alphanumeric())
        && b.all(|c| c.is_ascii_alphanumeric() || b"._:/-".contains(&c))
}

/// 8-4-4-4-12 hex digits.
fn uuid_shape(s: &str) -> bool {
    let parts: Vec<&str> = s.split('-').collect();
    parts.len() == 5
        && parts
            .iter()
            .zip([8, 4, 4, 4, 12])
            .all(|(p, n)| p.len() == n && p.bytes().all(|c| c.is_ascii_hexdigit()))
}

/// `<N>sits` with N = `[1-9][0-9]*`: `Some(Ok(n))`, `Some(Err)` when N does not fit, or
/// `None` when the word is not a batch count.
fn sits(s: &str) -> Option<Result<u32, OpenError>> {
    let n = s.strip_suffix("sits")?;
    let ok = n.bytes().next().is_some_and(|b| (b'1'..=b'9').contains(&b))
        && n.bytes().all(|b| b.is_ascii_digit());
    ok.then(|| n.parse::<u32>().map_err(|_| OpenError::Usage))
}

/// roles.py `role_family`: trailing ASCII digits off (`dev2` → `dev`); all digits stays.
pub fn role_family(role: &str) -> &str {
    let f = role.trim_end_matches(|c: char| c.is_ascii_digit());
    if f.is_empty() {
        role
    } else {
        f
    }
}

/// roles.py `is_defined`: a listed role, a captain role from `EZ2_AIB_CAPTAIN_ROLES`, or
/// one of their families; `AIB_ALLOW_CUSTOM_ROLE=1` lets any role through.
pub fn role_defined(role: &str, env: &impl Fn(&str) -> Option<String>) -> bool {
    if env("AIB_ALLOW_CUSTOM_ROLE").as_deref() == Some("1") {
        return true;
    }
    if role.is_empty() {
        return false;
    }
    let captains = env("EZ2_AIB_CAPTAIN_ROLES").unwrap_or_default();
    let known = |r: &str| ROLES.contains(&r) || captains.split_whitespace().any(|c| c == r);
    known(role) || known(role_family(role))
}

/// A flag's value: the next word, which must not be empty.
fn value<'a>(it: &mut impl Iterator<Item = &'a String>) -> Result<&'a str, OpenError> {
    match it.next() {
        Some(v) if !v.is_empty() => Ok(v),
        _ => Err(OpenError::Usage),
    }
}

/// The words after `aib` (or after `aib seat open`): what they ask for, or the first
/// refusal 1.x gives for them.
pub fn parse_open(
    args: &[String],
    env: impl Fn(&str) -> Option<String>,
) -> Result<Outcome, OpenError> {
    let mut dry_run = env("AIB_DRY_RUN").as_deref() == Some("1");
    let (mut new, mut again, mut probe_live) = (false, false, false);
    let mut dm = Dm::Auto;
    let mut reports_to: Option<String> = None;
    let mut model: Option<String> = None;
    let mut thread: Option<String> = None;
    let mut instance: Option<String> = None;
    let mut main_explicit = false;
    let mut pos: Vec<&str> = Vec::new();

    // 1.x aib_take_instance_flag
    let mut take_instance =
        |v: &str, new: &mut bool, instance: &mut Option<String>| -> Result<(), OpenError> {
            if v.is_empty() {
                return Err(OpenError::Usage);
            }
            if v == "new" {
                *new = true;
                *instance = None;
            } else {
                *instance = Some(v.to_string());
                main_explicit |= v == PRIMARY_CABIN;
            }
            Ok(())
        };

    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "-h" | "--help" => return Ok(Outcome::Help),
            "-V" | "--version" => return Ok(Outcome::Version),
            "--dry-run" => dry_run = true,
            "--new" => new = true,
            "--again" => again = true,
            "--dm" => dm = Dm::On,
            "--no-dm" => dm = Dm::Off,
            "--probe-live" => probe_live = true,
            "--reports-to" => reports_to = Some(value(&mut it)?.to_string()),
            "--model" => model = Some(value(&mut it)?.to_string()),
            "--thread" => {
                let v = value(&mut it)?;
                if !uuid_shape(v) {
                    return Err(OpenError::Thread(v.to_string()));
                }
                thread = Some(v.to_string());
            }
            "--instance" => take_instance(value(&mut it)?, &mut new, &mut instance)?,
            s => {
                if let Some(v) = s.strip_prefix("--reports-to=") {
                    if v.is_empty() {
                        return Err(OpenError::Usage);
                    }
                    reports_to = Some(v.to_string());
                } else if let Some(v) = s.strip_prefix("--model=") {
                    if v.is_empty() {
                        return Err(OpenError::Usage);
                    }
                    model = Some(v.to_string());
                } else if let Some(v) = s.strip_prefix("--thread=") {
                    // 1.x: an empty `--thread=` is a thread refusal, not a usage one
                    if !uuid_shape(v) {
                        return Err(OpenError::Thread(v.to_string()));
                    }
                    thread = Some(v.to_string());
                } else if let Some(v) = s.strip_prefix("--instance=") {
                    take_instance(v, &mut new, &mut instance)?;
                } else if s.starts_with('-') {
                    return Err(OpenError::Usage);
                } else {
                    pos.push(s);
                }
            }
        }
    }

    // trailing `dm` / `gameworld`, each at most once, only after an AI's name
    let mut gameworld = false;
    if pos.first().is_some_and(|p| PROVIDERS.contains(p)) {
        let mut seen_dm = false;
        while pos.len() >= 4 {
            match pos[pos.len() - 1] {
                "dm" if !seen_dm => {
                    seen_dm = true;
                    dm = Dm::On;
                }
                "gameworld" if !gameworld => gameworld = true,
                _ => break,
            }
            pos.pop();
        }
    }

    // `<N>sits [new]`
    let mut batch = None;
    if pos.len() == 5 && pos[4] == "new" {
        if let Some(n) = sits(pos[3]) {
            batch = Some(n?);
            new = true;
            pos.truncate(3);
        }
    } else if pos.len() == 4 {
        if let Some(n) = sits(pos[3]) {
            batch = Some(n?);
            pos.truncate(3);
        }
    }

    // the fourth word: `new` or a cabin
    if pos.len() == 4 {
        let w = pos[3];
        if w == "new" {
            new = true;
        } else {
            if instance.is_some() {
                return Err(OpenError::Usage);
            }
            if w == "dm" || w == "gameworld" {
                return Err(OpenError::ReservedCabin(w.to_string()));
            }
            if w.is_empty() {
                return Err(OpenError::CabinEmpty);
            }
            if !lower_word(w) {
                return Err(OpenError::CabinChars(w.to_string()));
            }
            main_explicit |= w == PRIMARY_CABIN;
            instance = Some(w.to_string());
        }
        pos.truncate(3);
    }

    let env_cabin = env("EZ2_AIB_INSTANCE").is_some_and(|v| !v.is_empty());
    let default_cabin = pos.len() == 3 && !new && !again && instance.is_none() && !env_cabin;

    if !pos.is_empty() && pos.len() != 3 && !PROVIDERS.contains(&pos[0]) {
        return Err(OpenError::UnknownCmd(pos[0].to_string()));
    }
    if pos.len() != 3 {
        return Err(OpenError::Arity);
    }
    if new && (instance.is_some() || again) {
        return Err(OpenError::Usage);
    }
    let (provider, mode, role) = (pos[0], pos[1], pos[2]);
    if !PROVIDERS.contains(&provider) {
        return Err(OpenError::Provider(provider.to_string()));
    }
    if !MODES.contains(&mode) {
        return Err(OpenError::Mode(mode.to_string()));
    }
    if !role_shape(role) {
        return Err(OpenError::Role(role.to_string()));
    }
    if !role_defined(role, &env) {
        return Err(OpenError::RoleUndefined(role.to_string()));
    }
    if let Some(r) = &reports_to {
        if !reports_to_shape(r) {
            return Err(OpenError::ReportsTo(r.clone()));
        }
    }
    if let Some(i) = &instance {
        if !instance_shape(i) {
            return Err(OpenError::Instance(i.clone()));
        }
    }
    if model.as_deref().is_some_and(|m| !model_shape(m)) {
        return Err(OpenError::Model);
    }

    let cabin = if new {
        Cabin::New
    } else if let Some(name) = instance {
        Cabin::Named {
            name,
            main_explicit,
        }
    } else if default_cabin {
        Cabin::Default
    } else {
        Cabin::FromEnvironment
    };
    Ok(Outcome::Open(OpenSpec {
        provider: provider.to_string(),
        mode: mode.to_string(),
        role: role.to_string(),
        cabin,
        again,
        dm,
        reports_to,
        model,
        thread,
        dry_run,
        probe_live,
        gameworld,
        batch,
    }))
}

#[cfg(test)]
mod tests;
