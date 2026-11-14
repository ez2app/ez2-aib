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
//! A file that is missing, does not parse, or is not `aib-cards/v2` is "the cards cannot
//! be read" ([`PersonaSourceError`], answered 503), never "no such persona".
//! A seat stores only the persona's uid; its name is looked up here when shown, so a
//! rename never leaves a stale name behind (7b81e §2-1).

use std::path::PathBuf;

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
        out.push(PersonaCard {
            uid: key.clone(),
            name: name.to_string(),
            persona_sha: sha.to_string(),
        });
    }
    out.sort_by(|a, b| a.uid.cmp(&b.uid));
    Ok(out)
}

/// The registry's `cards.json`, read on every ask (82 cards today; a rename shows at
/// once, nothing is cached).
#[derive(Debug, Clone)]
pub struct CardsFile {
    pub path: Option<PathBuf>,
}

impl PersonaSource for CardsFile {
    fn cards(&self) -> Result<Vec<PersonaCard>, PersonaSourceError> {
        let path = self
            .path
            .as_ref()
            .ok_or(PersonaSourceError::NotConfigured)?;
        let text = std::fs::read_to_string(path)
            .map_err(|e| PersonaSourceError::Unreadable(e.kind().to_string()))?;
        parse_cards(&text)
    }
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
/// `<home>/.ez2-aib/identity/cards.json` with home = `HOME`, else `USERPROFILE`
/// (Windows). `None` when neither is set: every open answers 503.
pub fn default_cards_path(lookup: impl Fn(&str) -> Option<String>) -> Option<PathBuf> {
    if let Some(p) = lookup("AIBD_PERSONA_CARDS").filter(|p| !p.is_empty()) {
        return Some(PathBuf::from(p));
    }
    let home = lookup("HOME")
        .filter(|h| !h.is_empty())
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
    fn a_missing_file_or_no_home_is_unavailable() {
        let none = CardsFile { path: None };
        assert_eq!(none.cards(), Err(PersonaSourceError::NotConfigured));
        let dir =
            std::env::temp_dir().join(format!("aibd-personas-missing-{}", std::process::id()));
        let missing = CardsFile {
            path: Some(dir.join("cards.json")),
        };
        assert!(matches!(
            missing.cards(),
            Err(PersonaSourceError::Unreadable(_))
        ));
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
