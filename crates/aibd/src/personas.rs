//! Persona cards, read-only (B4-1b-1; minimal `PersonaSource`, 7b81e 11-14 OPINION §2).
//!
//! The one place that says what a persona is called: `GET /seats` and `POST /seats/open`
//! both ask it, and the full `GET /personas` (7b81e) is to call it too. The cards come
//! from the 1.x card registry's `identity/cards.json` (`aib-cards/v2`), which the
//! registry writes with a temporary file and a rename (card_registry.py:506–513), so a
//! read never sees half a file. Its `display_name` already follows the library's
//! `labels.json` (the 11-14 renames changed only labels and re-scanned), so the
//! labels-first rule lives in the registry and nothing here reads labels.json.
//!
//! A file that does not parse, cannot be opened, or is not `aib-cards/v2` is "the cards
//! cannot be read" ([`PersonaSourceError`], answered 503), never "no such persona". A file
//! that is not there at all is a machine with no 1.x card registry (a new Windows install):
//! it has the one built-in persona ([`builtin_card`]) so a seat can be opened (ebab8 11-26
//! 12:12, FINDING-win-seatopen-owner-gates: every open was 503 on the customer's laptop).
//! A seat stores only the persona's uid; its name is looked up here when shown, so a
//! rename never leaves a stale name behind (7b81e §2-1).

use std::path::{Path, PathBuf};

/// The `schema_version` read.
pub const CARDS_SCHEMA: &str = "aib-cards/v2";
/// A persona uid: 6 of `a-z 0-9` (the cards' keys).
pub const UID_LEN: usize = 6;

/// One persona card: its uid, its display name and the sha of the card text it was
/// read from (which version of the card a seat was opened with).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PersonaCard {
    pub uid: String,
    pub name: String,
    pub persona_sha: String,
    /// Where the card text is: the registry's `source.vault_root` joined with the card's
    /// `card_path_rel` (S89-live-persona). `None` when the file does not say (an older
    /// registry, or a test fixture): the seat then opens without the card text.
    pub text_path: Option<PathBuf>,
}

impl PersonaCard {
    /// The uid and name, for the seat-open word rules (`aib_seatopen::find_persona`).
    pub fn persona(&self) -> aib_seatopen::Persona {
        aib_seatopen::Persona {
            uid: self.uid.clone(),
            name: self.name.clone(),
        }
    }
}

/// The cards could not be read: the caller answers 503 `persona_source_unavailable`.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum PersonaSourceError {
    #[error("no persona cards file is configured")]
    NotConfigured,
    #[error("the persona cards file cannot be read: {0}")]
    Unreadable(String),
    #[error("the persona cards file is not {CARDS_SCHEMA}: {0}")]
    Malformed(String),
}

/// Where persona cards come from.
pub trait PersonaSource: Send + Sync {
    /// Every card, sorted by uid.
    fn cards(&self) -> Result<Vec<PersonaCard>, PersonaSourceError>;
    /// The `cards.json` the cards come from, for the one write of a card (`persona_write`);
    /// `None`: cards that are not a file are never written.
    fn cards_json(&self) -> Option<&std::path::Path> {
        None
    }
}

/// `true` for a persona uid: 6 of `a-z 0-9`.
pub fn uid_shape(s: &str) -> bool {
    s.len() == UID_LEN
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
}

/// The cards of an `aib-cards/v2` document. Every card must have its uid as its key,
/// a non-empty `display_name` and a 64-hex `persona_sha`; one card that does not is the
/// whole file refused (a half-written or hand-broken file is not "fewer personas").
pub fn parse_cards(text: &str) -> Result<Vec<PersonaCard>, PersonaSourceError> {
    let bad = |m: &str| PersonaSourceError::Malformed(m.to_string());
    let doc: serde_json::Value =
        serde_json::from_str(text).map_err(|e| PersonaSourceError::Malformed(e.to_string()))?;
    if doc.get("schema_version").and_then(|v| v.as_str()) != Some(CARDS_SCHEMA) {
        return Err(bad("schema_version"));
    }
    let cards = doc
        .get("cards")
        .and_then(|v| v.as_object())
        .ok_or_else(|| bad("cards is not an object"))?;
    // the vault root the registry read the cards from: an absolute path, or nothing
    let root = doc
        .get("source")
        .and_then(|s| s.get("vault_root"))
        .and_then(|v| v.as_str())
        .map(std::path::Path::new)
        .filter(|p| p.is_absolute());
    let mut out = Vec::with_capacity(cards.len());
    for (key, c) in cards {
        let field = |k: &str| c.get(k).and_then(|v| v.as_str());
        if !uid_shape(key) || field("uid").is_some_and(|u| u != key) {
            return Err(PersonaSourceError::Malformed(format!("card {key}: uid")));
        }
        let name = field("display_name")
            .filter(|n| !n.trim().is_empty())
            .ok_or_else(|| PersonaSourceError::Malformed(format!("card {key}: display_name")))?;
        let sha = field("persona_sha")
            .filter(|s| s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit()))
            .ok_or_else(|| PersonaSourceError::Malformed(format!("card {key}: persona_sha")))?;
        // only plain parts under the root (no `..`, no root or prefix of its own)
        let text_path = root.zip(field("card_path_rel")).and_then(|(r, rel)| {
            let rel = std::path::Path::new(rel);
            let plain = rel.components().next().is_some()
                && rel
                    .components()
                    .all(|c| matches!(c, std::path::Component::Normal(_)));
            plain.then(|| r.join(rel))
        });
        out.push(PersonaCard {
            uid: key.clone(),
            name: name.to_string(),
            persona_sha: sha.to_string(),
            text_path,
        });
    }
    out.sort_by(|a, b| a.uid.cmp(&b.uid));
    Ok(out)
}

/// The built-in persona's uid: in the uid shape, and only ever listed when there is no cards
/// file, so it never stands beside (or for) a registry card.
pub const BUILTIN_UID: &str = "aib000";
/// Its name, fixed here: no page or API writes it.
/// fc7f9 / ebab8 12:1x: not 助手 (the page has an 助理 line); the page shows it in its own
/// language from `builtin: true`.
pub const BUILTIN_NAME: &str = "AIB 夥伴";
/// What its sha is taken over. It has no card text: a seat opened with it runs as the provider
/// itself (a card with no text path opens without text, S89-live-persona), and as there is no
/// text file, `persona_edit` / `persona_write` find nothing to change.
const BUILTIN_SHA_OF: &str = "aibd built-in persona aib000: no card text";

/// The refusal reason for any change to the built-in persona (3e53b DECISION-builtin-persona
/// -bounds 1-4): it is answered before any owner window.
pub const BUILTIN_READ_ONLY: &str = "builtin_persona_read_only";

/// Is this card the built-in one (its uid and its sha, so a 1.x card that one day has the
/// same uid is not taken for it)?
pub fn is_builtin(c: &PersonaCard) -> bool {
    c == &builtin_card()
}

/// The one persona of a machine with no cards file.
pub fn builtin_card() -> PersonaCard {
    use sha2::Digest;
    PersonaCard {
        uid: BUILTIN_UID.to_owned(),
        name: BUILTIN_NAME.to_owned(),
        persona_sha: sha2::Sha256::digest(BUILTIN_SHA_OF.as_bytes())
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect(),
        text_path: None,
    }
}

/// The registry's `cards.json`, read on every ask (82 cards today; a rename shows at
/// once, nothing is cached).
#[derive(Debug, Clone)]
pub struct CardsFile {
    pub path: Option<PathBuf>,
}

impl PersonaSource for CardsFile {
    fn cards_json(&self) -> Option<&std::path::Path> {
        self.path.as_deref()
    }

    fn cards(&self) -> Result<Vec<PersonaCard>, PersonaSourceError> {
        let path = self
            .path
            .as_ref()
            .ok_or(PersonaSourceError::NotConfigured)?;
        let text = match std::fs::read_to_string(path) {
            Ok(t) => t,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(vec![builtin_card()]),
            Err(e) => return Err(PersonaSourceError::Unreadable(e.kind().to_string())),
        };
        parse_cards(&text)
    }
}

/// The largest card text a seat is given (the 88 cards on the first machine: 12 KB at most).
pub const MAX_PERSONA_TEXT: u64 = 64 * 1024;

/// Why a card's text cannot be given to a seat: the open is refused (a seat that silently
/// runs without its persona is what Andy hit on 11-18).
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum PersonaTextError {
    #[error("the card text cannot be read")]
    Unreadable,
    #[error("the card text is over 64 KiB")]
    TooBig,
    #[error("the card text changed since the card registry last read it: refresh the registry")]
    Changed,
}

impl PersonaTextError {
    /// The `reason` a refused open answers.
    pub fn reason(&self) -> &'static str {
        match self {
            Self::Unreadable => "persona_text_unreadable",
            Self::TooBig => "persona_text_too_big",
            Self::Changed => "persona_changed",
        }
    }
    pub fn message(&self) -> &'static str {
        match self {
            Self::Unreadable => "the persona card text cannot be read",
            Self::TooBig => "the persona card text is over 64 KiB",
            Self::Changed => {
                "the persona card text changed since the card registry read it: refresh the \
                 registry, then open again"
            }
        }
    }
}

/// The card text at `path`, only when it is at most [`MAX_PERSONA_TEXT`] bytes and its
/// sha256 is the card's `persona_sha` (the version the cards list names).
pub fn card_text(path: &std::path::Path, persona_sha: &str) -> Result<Vec<u8>, PersonaTextError> {
    use sha2::Digest;
    use std::io::Read;
    let f = std::fs::File::open(path).map_err(|_| PersonaTextError::Unreadable)?;
    let mut text = Vec::new();
    f.take(MAX_PERSONA_TEXT + 1)
        .read_to_end(&mut text)
        .map_err(|_| PersonaTextError::Unreadable)?;
    if text.len() as u64 > MAX_PERSONA_TEXT {
        return Err(PersonaTextError::TooBig);
    }
    let hex: String = sha2::Sha256::digest(&text)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    if !hex.eq_ignore_ascii_case(persona_sha) {
        return Err(PersonaTextError::Changed);
    }
    Ok(text)
}

/// Keep the checked text in aibd's private folder `dir`, named by its hash: the provider
/// reads this copy, never the vault file (which may change after the check).
pub fn keep_text(
    dir: &std::path::Path,
    persona_sha: &str,
    text: &[u8],
) -> std::io::Result<PathBuf> {
    let p = kept_text_path(dir, persona_sha);
    crate::platform::write_private_file(&p, text)?;
    Ok(p)
}

/// Where [`keep_text`] puts a card's text (a dry run shows it without writing).
pub fn kept_text_path(dir: &std::path::Path, persona_sha: &str) -> PathBuf {
    dir.join(format!("persona-{}.md", persona_sha.to_ascii_lowercase()))
}

/// Fixed cards (tests, and a caller that already holds them).
#[derive(Debug, Clone, Default)]
pub struct FixedCards(pub Vec<PersonaCard>);

impl PersonaSource for FixedCards {
    fn cards(&self) -> Result<Vec<PersonaCard>, PersonaSourceError> {
        let mut v = self.0.clone();
        v.sort_by(|a, b| a.uid.cmp(&b.uid));
        Ok(v)
    }
}

/// The default cards file: `AIBD_PERSONA_CARDS` when set, else
/// `<home>/.ez2-aib/identity/cards.json` with home = `AIBD_SEAT_HOME` (absolute only: the
/// person's real home, which a preview's parent runs with while its own `HOME` is a scratch
/// folder with no cards, 0eed7 PREREAD-cards-0eed7-5e7e91bd2 H1), else `HOME`, else
/// `USERPROFILE` (Windows). `None` when none is set: every open answers 503.
pub fn default_cards_path(lookup: impl Fn(&str) -> Option<String>) -> Option<PathBuf> {
    if let Some(p) = lookup("AIBD_PERSONA_CARDS").filter(|p| !p.is_empty()) {
        return Some(PathBuf::from(p));
    }
    let home = lookup(crate::platform::session::SEAT_HOME_ENV)
        .filter(|h| Path::new(h).is_absolute())
        .or_else(|| lookup("HOME").filter(|h| !h.is_empty()))
        .or_else(|| lookup("USERPROFILE").filter(|h| !h.is_empty()))?;
    Some(
        PathBuf::from(home)
            .join(".ez2-aib")
            .join("identity")
            .join("cards.json"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    const SHA: &str = "77fa1f6bcc5b77fa1f6bcc5b77fa1f6bcc5b77fa1f6bcc5b77fa1f6bcc5b77fa";

    fn doc(cards: &str) -> String {
        format!(r#"{{"schema_version":"aib-cards/v2","cards":{{{cards}}}}}"#)
    }

    fn card(uid: &str, name: &str) -> String {
        format!(r#""{uid}":{{"uid":"{uid}","display_name":"{name}","persona_sha":"{SHA}"}}"#)
    }

    #[test]
    fn cards_are_read_sorted_by_uid() {
        let v = parse_cards(&doc(&format!(
            "{},{}",
            card("un0eba", "AIB2主線核心架構師"),
            card("mnnftg", "AIB 2.0 主軸統籌")
        )))
        .unwrap();
        assert_eq!(
            v.iter().map(|c| c.uid.as_str()).collect::<Vec<_>>(),
            ["mnnftg", "un0eba"]
        );
        assert_eq!(v[0].name, "AIB 2.0 主軸統籌");
        assert_eq!(v[0].persona_sha, SHA);
    }

    #[test]
    fn a_broken_file_is_unreadable_never_fewer_personas() {
        let malformed = |t: &str| matches!(parse_cards(t), Err(PersonaSourceError::Malformed(_)));
        assert!(malformed("{"), "half a file");
        assert!(malformed(r#"{"schema_version":"aib-cards/v1","cards":{}}"#));
        assert!(malformed(r#"{"cards":{}}"#), "no version");
        assert!(malformed(r#"{"schema_version":"aib-cards/v2","cards":[]}"#));
        // one bad card refuses the whole file
        let one_bad = |c: &str| malformed(&doc(&format!("{},{c}", card("un0eba", "a"))));
        assert!(
            one_bad(r#""abc":{"display_name":"x","persona_sha":"00"}"#),
            "short uid"
        );
        assert!(one_bad(&card("ABCDEF", "x")), "upper-case uid");
        assert!(one_bad(&format!(
            r#""aaaaaa":{{"uid":"bbbbbb","display_name":"x","persona_sha":"{SHA}"}}"#
        )));
        assert!(one_bad(&card("aaaaaa", " ")), "blank name");
        assert!(one_bad(
            r#""aaaaaa":{"display_name":"x","persona_sha":"zz"}"#
        ));
        assert!(one_bad(r#""aaaaaa":{"persona_sha":"x"}"#));
        // an empty library is a library, not an error
        assert_eq!(parse_cards(&doc("")).unwrap(), vec![]);
    }

    #[test]
    fn a_missing_file_is_the_built_in_persona_and_no_home_is_unavailable() {
        let none = CardsFile { path: None };
        assert_eq!(none.cards(), Err(PersonaSourceError::NotConfigured));
        let dir =
            std::env::temp_dir().join(format!("aibd-personas-missing-{}", std::process::id()));
        let missing = CardsFile {
            path: Some(dir.join("cards.json")),
        };
        // no cards file at all: the built-in persona only, with no text
        let got = missing.cards().unwrap();
        assert_eq!(got, vec![builtin_card()], "reason: no_file_not_builtin");
        assert_eq!(got[0].uid, BUILTIN_UID);
        assert!(uid_shape(BUILTIN_UID) && got[0].text_path.is_none());
        // the sha is pinned: the built-in card's content changes only with a reviewed edit here
        assert_eq!(
            got[0].persona_sha, "f63539efbf94ab4e4f706a80c034bd4f4654b96301f5b4eb8601938da52e7cee",
            "reason: builtin_sha_changed"
        );
        // a file that is there but cannot be opened is still unreadable, never the built-in
        std::fs::create_dir_all(dir.join("cards.json")).unwrap();
        assert!(
            matches!(missing.cards(), Err(PersonaSourceError::Unreadable(_))),
            "reason: unreadable_read_as_builtin"
        );
        std::fs::remove_dir_all(&dir).unwrap();
    }

    /// A machine that has a cards file never lists the built-in persona.
    #[test]
    fn a_cards_file_never_lists_the_built_in_persona() {
        let d = tempfile::tempdir().unwrap();
        let f = d.path().join("cards.json");
        std::fs::write(&f, doc(&card("un0eba", "a"))).unwrap();
        let got = CardsFile { path: Some(f) }.cards().unwrap();
        assert!(got.iter().all(|c| c.uid != BUILTIN_UID));
        assert_eq!(got.len(), 1);
    }

    /// S89-live-persona: the text path is the registry's vault root + the card's path,
    /// only for plain relative parts; a missing or relative root leaves it unknown.
    #[test]
    fn the_card_text_path_comes_from_the_registry_root() {
        let root = if cfg!(windows) { r"C:\v" } else { "/v" };
        let path = |src: serde_json::Value, rel: Option<&str>| {
            let mut card =
                serde_json::json!({"uid": "abcdef", "display_name": "x", "persona_sha": SHA});
            if let Some(r) = rel {
                card["card_path_rel"] = r.into();
            }
            let mut doc =
                serde_json::json!({"schema_version": "aib-cards/v2", "cards": {"abcdef": card}});
            if !src.is_null() {
                doc["source"] = src;
            }
            parse_cards(&doc.to_string()).unwrap()[0].text_path.clone()
        };
        let src = serde_json::json!({"vault_root": root});
        assert_eq!(
            path(src.clone(), Some("lib/abcdef/角色.md")),
            Some(std::path::Path::new(root).join("lib/abcdef/角色.md"))
        );
        for bad in ["../x.md", "/etc/x.md", "a/../../x.md", "", "./a.md"] {
            assert_eq!(path(src.clone(), Some(bad)), None, "{bad}");
        }
        assert_eq!(path(src, None), None, "no card path");
        assert_eq!(path(serde_json::Value::Null, Some("a.md")), None, "no root");
        assert_eq!(
            path(serde_json::json!({"vault_root": "rel/v"}), Some("a.md")),
            None,
            "a relative root"
        );
    }

    #[test]
    fn the_card_text_must_match_its_hash_and_size() {
        use sha2::Digest;
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("角色.md");
        std::fs::write(&p, "你是 AndyAI 分身").unwrap();
        let sha: String = sha2::Sha256::digest("你是 AndyAI 分身".as_bytes())
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        assert_eq!(card_text(&p, &sha).unwrap(), "你是 AndyAI 分身".as_bytes());
        assert_eq!(card_text(&p, SHA), Err(PersonaTextError::Changed));
        assert_eq!(
            card_text(&dir.path().join("none.md"), &sha),
            Err(PersonaTextError::Unreadable)
        );
        let big = vec![b'x'; usize::try_from(MAX_PERSONA_TEXT).unwrap() + 1];
        std::fs::write(&p, big).unwrap();
        assert_eq!(card_text(&p, &sha), Err(PersonaTextError::TooBig));
        let kept = keep_text(dir.path(), &sha, b"t").unwrap();
        assert_eq!(std::fs::read(&kept).unwrap(), b"t");
        assert!(kept.ends_with(format!("persona-{sha}.md")));
    }

    #[test]
    fn the_default_path_follows_the_environment() {
        let env = |pairs: &'static [(&'static str, &'static str)]| {
            move |k: &str| {
                pairs
                    .iter()
                    .find(|(n, _)| *n == k)
                    .map(|(_, v)| v.to_string())
            }
        };
        assert_eq!(
            default_cards_path(env(&[("AIBD_PERSONA_CARDS", "/x/c.json"), ("HOME", "/h")])),
            Some(PathBuf::from("/x/c.json"))
        );
        assert_eq!(
            default_cards_path(env(&[("HOME", "/h")])),
            Some(
                PathBuf::from("/h")
                    .join(".ez2-aib")
                    .join("identity")
                    .join("cards.json")
            )
        );
        assert_eq!(
            default_cards_path(env(&[("HOME", ""), ("USERPROFILE", "C:\\Users\\u")])),
            Some(
                PathBuf::from("C:\\Users\\u")
                    .join(".ez2-aib")
                    .join("identity")
                    .join("cards.json")
            )
        );
        assert_eq!(default_cards_path(env(&[])), None);
        // a preview's parent: its HOME is a scratch folder, the real home comes as AIBD_SEAT_HOME
        // (an absolute path on every OS: "/Users/real" is not absolute on Windows, 0eed7 M)
        let real = std::env::temp_dir().join("real-home");
        let real_s = real.to_string_lossy().into_owned();
        let seat = |k: &str| match k {
            "HOME" => Some("/m/home".to_string()),
            "AIBD_SEAT_HOME" => Some(real_s.clone()),
            _ => None,
        };
        assert_eq!(
            default_cards_path(seat),
            Some(real.join(".ez2-aib").join("identity").join("cards.json")),
            "reason: seat_home_not_first"
        );
        // only an absolute seat home counts
        assert_eq!(
            default_cards_path(env(&[("HOME", "/h"), ("AIBD_SEAT_HOME", "rel")])),
            Some(
                PathBuf::from("/h")
                    .join(".ez2-aib")
                    .join("identity")
                    .join("cards.json")
            ),
            "reason: relative_seat_home_taken"
        );
        // an explicit cards file still wins
        assert_eq!(
            default_cards_path(env(&[
                ("AIBD_PERSONA_CARDS", "/x/c.json"),
                ("AIBD_SEAT_HOME", "/s")
            ])),
            Some(PathBuf::from("/x/c.json"))
        );
    }

    #[test]
    fn uids() {
        assert!(uid_shape("un0eba"));
        assert!(!uid_shape("un0eb"));
        assert!(!uid_shape("UN0EBA"));
        assert!(!uid_shape("un0eba1"));
        assert!(!uid_shape("架構師"));
    }
}
