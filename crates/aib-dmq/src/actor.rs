//! `--by` values: `system` / `seat:<code>` / `sender:<code>` / `human:<id>` /
//! `agent:<seat>/<sub-agent id>` (parse_actor in 1.x). `--by` is a literal guard, not an
//! identity check: any shell can type `human:x`. The caller-is-a-seat measurement that
//! refuses `human:` reads from a seat is passed in by the caller.

use crate::util::py_strip;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum ActorClass {
    System,
    Seat,
    Sender,
    Human,
    Agent,
}

impl ActorClass {
    pub fn as_str(self) -> &'static str {
        match self {
            ActorClass::System => "system",
            ActorClass::Seat => "seat",
            ActorClass::Sender => "sender",
            ActorClass::Human => "human",
            ActorClass::Agent => "agent",
        }
    }

    pub fn parse(s: &str) -> Option<ActorClass> {
        [
            Self::System,
            Self::Seat,
            Self::Sender,
            Self::Human,
            Self::Agent,
        ]
        .into_iter()
        .find(|c| c.as_str() == s)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Actor {
    pub class: ActorClass,
    /// None only for `system`.
    pub ident: Option<String>,
}

fn agent_ident_ok(ident: &str) -> bool {
    // [0-9a-f]{5}/[0-9A-Za-z._-]{1,64}, whole string
    let Some((seat, sub)) = ident.split_once('/') else {
        return false;
    };
    seat.len() == 5
        && seat
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        && (1..=64).contains(&sub.len())
        && sub
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'))
}

/// None = no actor or a shape no class takes (both refuse with `actor_missing`).
pub fn parse_actor(actor: Option<&str>) -> Option<Actor> {
    let text = py_strip(actor?);
    if text.is_empty() {
        return None;
    }
    if text == "system" {
        return Some(Actor {
            class: ActorClass::System,
            ident: None,
        });
    }
    let (cls, ident) = text.split_once(':')?;
    let (cls, ident) = (py_strip(cls), py_strip(ident));
    match cls {
        "seat" | "sender" | "human" if !ident.is_empty() => Some(Actor {
            class: ActorClass::parse(cls)?,
            ident: Some(ident.to_string()),
        }),
        "agent" if agent_ident_ok(ident) => Some(Actor {
            class: ActorClass::Agent,
            ident: Some(ident.to_string()),
        }),
        _ => None,
    }
}
