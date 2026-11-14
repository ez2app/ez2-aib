// SPDX-License-Identifier: Apache-2.0
//! What a seat-open command means, from its words alone (B4-1a, PLAN-b4-cockpit-rust-a9136-1113).
//!
//! Port of the argument rules of the frozen 1.x launcher `aib/src/cockpit/aib`
//! (`aib <provider> <dgrs|safe> <persona> [cabin|new] [<N>sits] [dm] [gameworld] [flags]`,
//! flag loop :1051–1104, trailing words :1110–1124, batch :1139–1150, fourth word
//! :1249–1266, checks :1289–1406, model shape :1473). [`parse_open`] gives an
//! [`OpenSpec`] or the first refusal 1.x gives for the same words, with its i18n key
//! and exit code. It touches no file, process or window, so it runs the same on
//! macOS, Linux and Windows.
//!
//! The checks only the caller can make — the cabin lookup (short code, socket), the kill
//! switch, the edition and bot checks of one AI, and which persona card the third word
//! names — are asked through [`Context`] at the
//! point 1.x makes them, so the first refusal is still 1.x's ([`parse_open`] skips them).
//! Left to the caller after a parse (B4-1b, `aibd ctl seat open`): the in-pane default
//! cabin, the model default, whether the AI's program is installed, and every side
//! effect. `aib seat re-register` (its own argv) is not parsed here.
//!
//! The environment is read through `env` (tests inject it): `AIB_DRY_RUN`,
//! `AIB_PROBE_LIVE`, `EZ2_AIB_INSTANCE`. Pass `|k| std::env::var(k).ok()`: on Windows that
//! lookup already ignores case.
//!
//! The third word is a persona, not a role (Andy 11-14 B4-1a2: 「我不再想綁定 root / pm / dev
//! 這些指令了，我想直接用人格取代」): a card's 6-character uid or its name, looked up among the
//! cards ([`Context::persona`], [`find_persona`]). The seat is opened bound to that card, so
//! there is no role, no captain role (`EZ2_AIB_CAPTAIN_ROLES`) and no custom-role escape
//! (`AIB_ALLOW_CUSTOM_ROLE`) any more; the frozen 1.x launcher keeps its roles until it retires.
//!
//! Known differences from 1.x, all stricter:
//! - a batch count too large for `u32` (`99999999999sits`) is a usage error, reported
//!   after every other check; 1.x passed it on to the batch script.
//! - `--thread` values are matched as ASCII; 1.x's bash glob ranges could follow the
//!   locale.
//! - an AI's name holding a space (`"claude codex"`) is refused; 1.x's glob over the
//!   space-separated list let it through.

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

/// The longest persona word taken, in characters.
pub const PERSONA_MAX_CHARS: usize = 64;

/// A persona card as the caller's card list gives it (2.0: `GET /personas`, today
/// cards.json): its 6-character uid and its display name.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Persona {
    pub uid: String,
    pub name: String,
}

/// Which card the third word names.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PersonaMatch {
    Found(Persona),
    /// No card has that uid or name.
    Missing,
    /// Several cards share that name (and none has it as its uid): the cards, by uid.
    Ambiguous(Vec<Persona>),
}

/// The card the third word names: the card whose uid it is, else the one card with that
/// name, exactly (no case folding, no trimming: the word is shape-checked first). Two
/// cards with the same name are [`PersonaMatch::Ambiguous`], never chosen between
/// quietly; the uid then picks one. The ` · <seat>` suffix a screen adds to tell two
/// seats of one card apart is not part of the name.
pub fn find_persona(word: &str, cards: &[Persona]) -> PersonaMatch {
    if let Some(c) = cards.iter().find(|c| c.uid == word) {
        return PersonaMatch::Found(c.clone());
    }
    let mut same: Vec<Persona> = cards.iter().filter(|c| c.name == word).cloned().collect();
    match same.len() {
        0 => PersonaMatch::Missing,
        1 => PersonaMatch::Found(same.remove(0)),
        _ => {
            same.sort_by(|a, b| a.uid.cmp(&b.uid));
            same.dedup_by(|a, b| a.uid == b.uid);
            PersonaMatch::Ambiguous(same)
        }
    }
}

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
    /// `instance` is `EZ2_AIB_INSTANCE` as it was (`Some("")` when set but empty), or
    /// as [`Context::resolve_cabin`] gave it back.
    FromEnvironment { instance: Option<String> },
}

/// The cabin lookup 1.x makes (short code → full name, the socket check; :1270–1288).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CabinQuery<'a> {
    /// The cabin the words name, unless it is the primary written by hand.
    pub named: Option<&'a str>,
    /// `EZ2_AIB_INSTANCE` when it names a cabin to look up (set, not empty, not `new`,
    /// not the primary) and the words name none and do not ask for a new one.
    pub from_env: Option<&'a str>,
    pub new: bool,
    /// A dry run only warns about a cabin that is not found.
    pub dry_run: bool,
    /// The cabin the words, else the environment, name, unfiltered: the primary and
    /// `new` included, empty `EZ2_AIB_INSTANCE` = none (1.x
    /// `${INSTANCE:-${EZ2_AIB_INSTANCE-}}`). Unless `new`, a socket pointing at another
    /// cabin than this one (after the lookup) is a conflict (aib:692–704, rc 2).
    pub explicit: Option<&'a str>,
}

/// What only the caller can look up, asked at the point 1.x asks it, so the first
/// refusal is the one 1.x gives (Codex 11-13 #1).
pub trait Context {
    /// Look up the cabin, then apply the socket (1.x `aib_apply_socket_instance`, :1286).
    /// `Ok(Some(full))` replaces the name looked up (`named`, else `from_env`); when
    /// neither was asked, it is the cabin the socket points at, and it is taken as the
    /// cabin unless the words ask for a new one or the environment names one. `Ok(None)`
    /// keeps what there is; `Err` refuses (use [`OpenError::Cabin`], rc 2) — a socket
    /// that points at another cabin than `explicit`, unless `new`, is refused here and
    /// never chosen between quietly (043dd r3 M1). Called once,
    /// after the fourth word and before the word count is checked; `named` and
    /// `from_env` have passed the cabin-name check (1.x `aib_cabin_check_name`) already.
    fn resolve_cabin(&self, q: &CabinQuery<'_>) -> Result<Option<String>, OpenError>;
    /// The kill switch: `Some(what the switch file says)` refuses with rc 3. Asked after
    /// the word count and the flag clashes, before the AI's name is checked.
    fn killswitch(&self) -> Option<String>;
    /// The edition and bot checks of one AI (1.x :1317–1395: typesafe_cli.py present,
    /// the bot's thread or agent free). `Err` refuses (use [`OpenError::Preflight`],
    /// rc 2). Asked after the AI's name is checked, before the mode. Words only: none.
    fn provider_preflight(&self, _provider: &str) -> Result<(), OpenError> {
        Ok(())
    }
    /// Which card the third word names (usually [`find_persona`] over the caller's
    /// cards). Asked after the mode and the persona word's shape, before `--reports-to`.
    fn persona(&self, word: &str) -> PersonaMatch;
}

/// The words and a card list: no cabin lookup, no kill switch.
#[derive(Debug, Clone, Copy)]
pub struct WordsOnly<'a>(pub &'a [Persona]);

impl Context for WordsOnly<'_> {
    fn resolve_cabin(&self, _: &CabinQuery<'_>) -> Result<Option<String>, OpenError> {
        Ok(None)
    }
    fn killswitch(&self) -> Option<String> {
        None
    }
    fn persona(&self, word: &str) -> PersonaMatch {
        find_persona(word, self.0)
    }
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
    /// The card the seat is opened bound to.
    pub persona: Persona,
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
    /// The persona word is empty, too long, holds a control character, or starts or ends
    /// with white space (2.0, plain text until the 2.0 dictionary has keys).
    PersonaShape(String),
    /// No card has that uid or name (2.0, plain text).
    PersonaMissing(String),
    /// Several cards share that name: the word and the cards, by uid (2.0, plain text).
    PersonaAmbiguous(String, Vec<Persona>),
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
    /// The cabin lookup refused ([`Context::resolve_cabin`]); the caller's own words.
    Cabin(String),
    /// An edition or bot check refused ([`Context::provider_preflight`]); the caller's
    /// own words.
    Preflight(String),
    /// `aib.err.killswitch` (rc 3) with what the switch file says (`value`); the
    /// renderer adds `path`.
    Killswitch(String),
}

impl OpenError {
    /// The exit code 1.x gives: [`rc::KILLSWITCH`] for the kill switch, else [`rc::USAGE`].
    pub fn rc(&self) -> i32 {
        match self {
            OpenError::Killswitch(_) => rc::KILLSWITCH,
            _ => rc::USAGE,
        }
    }

    /// The 1.x i18n key, and its parameter, for the keyed refusals; `None` for the ones
    /// 1.x prints as plain text (see [`fmt::Display`]). The renderer adds what 1.x adds
    /// beside the words: `path` for `aib.err.killswitch`.
    pub fn key(&self) -> Option<(&'static str, Option<(&'static str, &str)>)> {
        Some(match self {
            OpenError::Usage | OpenError::Arity => ("aib.err.usage", None),
            OpenError::UnknownCmd(v) => ("aib.err.unknown_cmd", Some(("cmd", v))),
            OpenError::Provider(v) => ("aib.err.provider", Some(("provider", v))),
            OpenError::Mode(v) => ("aib.err.mode", Some(("mode", v))),
            OpenError::ReportsTo(v) => ("aib.err.reports_to", Some(("seat", v))),
            OpenError::Instance(v) => ("aib.err.instance", Some(("instance", v))),
            OpenError::Killswitch(v) => ("aib.err.killswitch", Some(("value", v))),
            OpenError::Thread(_)
            | OpenError::ReservedCabin(_)
            | OpenError::CabinEmpty
            | OpenError::CabinChars(_)
            | OpenError::Model
            | OpenError::Cabin(_)
            | OpenError::Preflight(_)
            | OpenError::PersonaShape(_)
            | OpenError::PersonaMissing(_)
            | OpenError::PersonaAmbiguous(..) => return None,
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
                "aib: 「{v}」是開席尾端的保留字,不能當艙名(每個保留字最多寫一次)"
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
            OpenError::Cabin(m) | OpenError::Preflight(m) => write!(f, "{m}"),
            OpenError::PersonaShape(v) => write!(
                f,
                "aib: 第三個字要寫人格名稱或 6 碼 uid(不能空白、不能有控制字元或前後空白，最多 {PERSONA_MAX_CHARS} 字):「{v}」"
            ),
            OpenError::PersonaMissing(v) => write!(
                f,
                "aib: 找不到人格「{v}」(第三個字寫人格名稱或 6 碼 uid)"
            ),
            OpenError::PersonaAmbiguous(v, cards) => {
                let uids: Vec<&str> = cards.iter().map(|c| c.uid.as_str()).collect();
                write!(
                    f,
                    "aib: 有 {} 張人格卡都叫「{v}」,請改寫 uid:{}",
                    cards.len(),
                    uids.join(" ")
                )
            }
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

/// A persona word: not empty, at most [`PERSONA_MAX_CHARS`] characters, no control
/// character, no white space at either end.
fn persona_shape(s: &str) -> bool {
    !s.is_empty()
        && s.chars().count() <= PERSONA_MAX_CHARS
        && !s.chars().any(char::is_control)
        && s.trim() == s
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

/// `<N>sits` with N = `[1-9][0-9]*`: `Some(Some(n))`, `Some(None)` when N does not fit,
/// or `None` when the word is not a batch count.
fn sits(s: &str) -> Option<Option<u32>> {
    let n = s.strip_suffix("sits")?;
    let ok = n.bytes().next().is_some_and(|b| (b'1'..=b'9').contains(&b))
        && n.bytes().all(|b| b.is_ascii_digit());
    ok.then(|| n.parse::<u32>().ok())
}

/// 1.x `aib_cabin_check_name` (cabin_resolve.sh:55): not empty, only `a-z 0-9 -`.
fn check_cabin_name(v: &str) -> Result<(), OpenError> {
    if v.is_empty() {
        return Err(OpenError::CabinEmpty);
    }
    if !lower_word(v) {
        return Err(OpenError::CabinChars(v.to_string()));
    }
    Ok(())
}

/// A flag's value: the next word, which must not be empty.
fn value<'a>(it: &mut impl Iterator<Item = &'a String>) -> Result<&'a str, OpenError> {
    match it.next() {
        Some(v) if !v.is_empty() => Ok(v),
        _ => Err(OpenError::Usage),
    }
}

/// [`parse_open_in`] with the words and `cards` only ([`WordsOnly`]): no cabin lookup, no
/// kill switch.
pub fn parse_open(
    args: &[String],
    env: impl Fn(&str) -> Option<String>,
    cards: &[Persona],
) -> Result<Outcome, OpenError> {
    parse_open_in(args, env, &WordsOnly(cards))
}

/// The words after `aib` (or after `aib seat open`): what they ask for, or the first
/// refusal 1.x gives for them, asking `ctx` what only the caller can look up at the
/// point 1.x looks it up.
pub fn parse_open_in(
    args: &[String],
    env: impl Fn(&str) -> Option<String>,
    ctx: &impl Context,
) -> Result<Outcome, OpenError> {
    let mut dry_run = env("AIB_DRY_RUN").as_deref() == Some("1");
    // 1.x passes --probe-live on when AIB_PROBE_LIVE is set and not empty (:2120)
    let mut probe_live = env("AIB_PROBE_LIVE").is_some_and(|v| !v.is_empty());
    let (mut new, mut again) = (false, false);
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

    // `<N>sits [new]`; a count too large is refused last (043dd L1: the kill switch first)
    let mut batch = None;
    let mut too_many = false;
    if pos.len() == 5 && pos[4] == "new" {
        if let Some(n) = sits(pos[3]) {
            (batch, too_many) = (n, n.is_none());
            new = true;
            pos.truncate(3);
        }
    } else if pos.len() == 4 {
        if let Some(n) = sits(pos[3]) {
            (batch, too_many) = (n, n.is_none());
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
            check_cabin_name(w)?;
            main_explicit |= w == PRIMARY_CABIN;
            instance = Some(w.to_string());
        }
        pos.truncate(3);
    }

    // the cabin lookup: a named cabin (unless the primary by hand), else one the
    // environment names; each name checked first (1.x aib_cabin_pick_named, 043dd M2)
    let mut env_instance = env("EZ2_AIB_INSTANCE");
    let (full, asked_named, asked_env) = {
        let q = CabinQuery {
            named: instance.as_deref().filter(|_| !main_explicit),
            from_env: env_instance.as_deref().filter(|v| {
                instance.is_none() && !new && !v.is_empty() && *v != "new" && *v != PRIMARY_CABIN
            }),
            new,
            dry_run,
            explicit: instance
                .as_deref()
                .or(env_instance.as_deref().filter(|v| !v.is_empty())),
        };
        if let Some(v) = q.named.or(q.from_env) {
            check_cabin_name(v)?;
        }
        (
            ctx.resolve_cabin(&q)?,
            q.named.is_some(),
            q.from_env.is_some(),
        )
    };
    if let Some(full) = full {
        // nothing named, no new cabin asked: the cabin the socket points at (043dd M4)
        let socket = !new && instance.is_none() && env_instance.as_deref().unwrap_or("").is_empty();
        if asked_env {
            env_instance = Some(full);
        } else if asked_named || socket {
            instance = Some(full);
        }
    }
    let env_cabin = env_instance.as_deref().is_some_and(|v| !v.is_empty());
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
    if let Some(v) = ctx.killswitch() {
        return Err(OpenError::Killswitch(v));
    }
    let (provider, mode, word) = (pos[0], pos[1], pos[2]);
    if !PROVIDERS.contains(&provider) {
        return Err(OpenError::Provider(provider.to_string()));
    }
    ctx.provider_preflight(provider)?;
    if !MODES.contains(&mode) {
        return Err(OpenError::Mode(mode.to_string()));
    }
    if !persona_shape(word) {
        return Err(OpenError::PersonaShape(word.to_string()));
    }
    let persona = match ctx.persona(word) {
        PersonaMatch::Found(p) => p,
        PersonaMatch::Missing => return Err(OpenError::PersonaMissing(word.to_string())),
        PersonaMatch::Ambiguous(c) => return Err(OpenError::PersonaAmbiguous(word.to_string(), c)),
    };
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
    if too_many {
        return Err(OpenError::Usage);
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
        Cabin::FromEnvironment {
            instance: env_instance,
        }
    };
    Ok(Outcome::Open(OpenSpec {
        provider: provider.to_string(),
        mode: mode.to_string(),
        persona,
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
