//! aibd §9 配置助理, slice 9c: one callable proposal (ebab8 11-23 08:00). From a person's
//! purpose sentence and the existing personas and brains it builds the suggestion card the
//! assistant hands to the page (an `assistant_suggest` body, so it goes through
//! [`crate::assistant::parse_suggest`] like one the assistant wrote), and turns a confirmed
//! card into owner requests through a [`Requests`] seam: owner_request is not in main yet,
//! so the wiring gives the real one later (DESIGN-assistant-6770f-1123 r1 §2, §3).
//!
//! - An existing persona is used when its name is close to the purpose ([`MATCH_AT`]), or
//!   contained in it; otherwise a new one is drafted. Ties go to the shorter name, then the
//!   smaller uid, so the same lists give the same card.
//! - The brain: the one the chosen persona is bound to, else the closest existing one, else
//!   a draft.
//! - The seat is always a draft in `safe` or `work` (never `dgrs` / `host`), naming the
//!   persona only when it already exists (a new one has no uid until the person makes it).
//! - Names read from the lists are data: they only go into the card's note and fields, and
//!   the card is read back with the same strict reader as the assistant's own.

use crate::assistant::{parse_suggest, to_request, RequestRefused, SuggestItem};
use serde_json::{json, Value};

/// A name this close to the purpose (bigram Dice, 0..=1) is「已有」.
pub const MATCH_AT: f64 = 0.5;
/// The most characters of a purpose sentence.
pub const PURPOSE_MAX: usize = 200;
/// The most characters of a drafted name.
pub const NAME_MAX: usize = 20;

/// A persona as the catalog lists it, with the brain it is bound to.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PersonaRow {
    pub uid: String,
    pub name: String,
    pub brain: Option<String>,
}

/// A brain as the catalog lists it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BrainRow {
    pub uid: String,
    pub name: String,
}

/// Letters and digits only, lower case: what is compared.
fn norm(s: &str) -> Vec<char> {
    s.chars()
        .filter(|c| c.is_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect()
}

fn bigrams(v: &[char]) -> Vec<(char, char)> {
    v.windows(2).map(|w| (w[0], w[1])).collect()
}

/// How close a name is to the purpose: 0.9 when the whole name (2+ chars) is in it, else
/// bigram Dice.
pub fn score(purpose: &str, name: &str) -> f64 {
    let (p, n) = (norm(purpose), norm(name));
    if n.len() < 2 || p.len() < 2 {
        return 0.0;
    }
    if p.windows(n.len()).any(|w| w == n.as_slice()) {
        return 0.9;
    }
    let (bp, bn) = (bigrams(&p), bigrams(&n));
    let mut left = bn.clone();
    let mut hit = 0usize;
    for g in &bp {
        if let Some(i) = left.iter().position(|x| x == g) {
            left.swap_remove(i);
            hit += 1;
        }
    }
    (2 * hit) as f64 / (bp.len() + bn.len()) as f64
}

/// The closest row at or over [`MATCH_AT`]: (uid, score).
fn closest<'a>(
    purpose: &str,
    rows: impl Iterator<Item = (&'a str, &'a str)>,
) -> Option<(String, f64)> {
    let mut best: Option<(f64, usize, String)> = None;
    for (uid, name) in rows {
        let s = score(purpose, name);
        if s < MATCH_AT {
            continue;
        }
        let len = name.chars().count();
        let better = match &best {
            None => true,
            Some((bs, bl, bu)) => {
                s > *bs || (s == *bs && (len < *bl || (len == *bl && uid < bu.as_str())))
            }
        };
        if better {
            best = Some((s, len, uid.to_string()));
        }
    }
    best.map(|(s, _, u)| (u, s))
}

/// A drafted name: the purpose's first [`NAME_MAX`] characters, punctuation trimmed;
/// if the leading window is all punctuation, advance to the first alphanumeric char.
fn draft_name(purpose: &str) -> String {
    let take_trimmed = |s: &str| -> String {
        let n: String = s.chars().take(NAME_MAX).collect();
        n.trim_matches(|c: char| !c.is_alphanumeric()).to_string()
    };
    let first = take_trimmed(purpose);
    if !first.is_empty() {
        return first;
    }
    take_trimmed(purpose.trim_start_matches(|c: char| !c.is_alphanumeric()))
}

/// The suggestion card for a purpose. Errors: `purpose_shape` (empty / too long after
/// control and A13 format characters are taken out), `request_mode` (a seat mode the
/// assistant may not ask for).
pub fn propose(
    purpose: &str,
    personas: &[PersonaRow],
    brains: &[BrainRow],
    mode: &str,
) -> Result<Value, &'static str> {
    let purpose = crate::assistant::shown(purpose);
    let purpose = purpose.trim();
    if norm(purpose).len() < 2 || purpose.chars().count() > PURPOSE_MAX {
        return Err("purpose_shape");
    }
    if !matches!(mode, "safe" | "work") {
        return Err(RequestRefused::Mode.code());
    }
    let name = draft_name(purpose);
    if name.is_empty() {
        return Err("purpose_shape");
    }
    let mut items = Vec::new();
    let persona = closest(
        purpose,
        personas.iter().map(|p| (p.uid.as_str(), p.name.as_str())),
    );
    let chosen = persona
        .as_ref()
        .and_then(|(u, _)| personas.iter().find(|p| &p.uid == u));
    match (&persona, chosen) {
        (Some((_, s)), Some(p)) => items.push(json!({"what":"persona","uid":p.uid,
            "note": format!("「{}」跟用途最像({}%)", p.name, (s * 100.0).round() as i64)})),
        _ => items.push(json!({"what":"persona","draft":{"name":name,
            "text": format!("你是「{name}」。用途:{purpose}")},
            "note":"沒有相近的人格,新建一張"})),
    }
    let bound = chosen
        .and_then(|p| p.brain.as_deref())
        .filter(|b| brains.iter().any(|r| r.uid == *b));
    let mut chosen_brain: Option<String> = None;
    if let Some(b) = bound {
        chosen_brain = Some(b.to_string());
        items.push(json!({"what":"brain","uid":b,"note":"這個人格原本綁的第二大腦"}));
    } else if let Some((u, _)) = closest(
        purpose,
        brains.iter().map(|b| (b.uid.as_str(), b.name.as_str())),
    ) {
        chosen_brain = Some(u.clone());
        items.push(json!({"what":"brain","uid":u,"note":"跟用途最像的第二大腦"}));
    } else {
        items.push(
            json!({"what":"brain","draft":{"name":name},"note":"沒有相近的第二大腦,新建一個"}),
        );
    }
    let mut seat = json!({"mode": mode});
    if let Some(p) = chosen {
        seat["persona_uid"] = json!(p.uid);
    }
    if let Some(b) = &chosen_brain {
        seat["brain"] = json!(b);
    }
    items.push(json!({"what":"seat","draft":seat,
        "note": if chosen.is_some() { "用上面的人格開一位" } else { "先建人格,建好再按一次開席" }}));
    let card = json!({ "items": items });
    // the card must read back like one the assistant wrote
    parse_suggest(&card)?;
    Ok(card)
}

/// Where a confirmed card's requests go (owner_request once it is in main; a list in tests).
pub trait Requests {
    /// Hand a validated batch of requests on together (one owner_batch, one check); their ids.
    fn submit_batch(&mut self, reqs: &[(&'static str, Value)]) -> Result<Vec<String>, String>;
}

/// Turn every item of a confirmed card into its owner request; an existing one used as it is
/// makes none. Every item is checked before any is handed on, so a card with one refused
/// item hands on nothing. When a new persona is being drafted, `seat_open` waits for it
/// (never opens a seat with no persona).
pub fn submit_all(items: &[SuggestItem], to: &mut impl Requests) -> Result<Vec<String>, String> {
    let has_new_persona = items.iter().any(|i| i.what == "persona" && i.uid.is_none());
    let mut reqs = Vec::new();
    for it in items {
        match to_request(it) {
            Ok(Some((act, params))) => {
                if act == "seat_open" && has_new_persona {
                    continue;
                }
                reqs.push((act, params));
            }
            Ok(None) => {}
            Err(e) => return Err(e.code().to_string()),
        }
    }
    to.submit_batch(&reqs)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::assistant::REQUEST_ACTS;

    fn p(uid: &str, name: &str, brain: Option<&str>) -> PersonaRow {
        PersonaRow {
            uid: uid.into(),
            name: name.into(),
            brain: brain.map(str::to_string),
        }
    }
    fn b(uid: &str, name: &str) -> BrainRow {
        BrainRow {
            uid: uid.into(),
            name: name.into(),
        }
    }

    #[derive(Default)]
    struct Got(Vec<(String, Value)>);
    impl Requests for Got {
        fn submit_batch(&mut self, reqs: &[(&'static str, Value)]) -> Result<Vec<String>, String> {
            let mut ids = Vec::with_capacity(reqs.len());
            for (act, params) in reqs {
                self.0.push(((*act).to_string(), params.clone()));
                ids.push(format!("r{}", self.0.len()));
            }
            Ok(ids)
        }
    }

    #[test]
    fn scores() {
        // se, eo, 寫手 of 10 + 4 bigrams
        assert!((score("幫我寫 SEO 文章的寫手", "SEO寫手") - 6.0 / 14.0).abs() < 1e-9);
        assert_eq!(score("我要一位 SEO 專家", "SEO專家"), 0.9);
        assert_eq!(score("x", "SEO專家"), 0.0);
        assert_eq!(score("SEO專家", "a"), 0.0);
        assert!((score("abcd", "abce") - 2.0 / 3.0).abs() < 1e-9);
        assert_eq!(score("aaaa", "aa"), 0.9);
        assert!(score("完全不同的東西", "SEO專家") < MATCH_AT);
    }

    #[test]
    fn an_existing_persona_and_its_brain_are_used_and_the_seat_names_it() {
        let ps = [
            p("aaaaa1", "SEO專家", Some("brn_a")),
            p("aaaaa2", "清展寫手", None),
        ];
        let bs = [b("brn_a", "SEO 筆記"), b("brn_b", "其他")];
        let card = propose("我要一位 SEO 專家幫我看排名", &ps, &bs, "work").unwrap();
        let items = parse_suggest(&card).unwrap();
        assert_eq!(items[0].uid.as_deref(), Some("aaaaa1"));
        assert!(items[0].note.contains("90%"));
        assert_eq!(items[1].uid.as_deref(), Some("brn_a"));
        assert_eq!(
            items[2].draft,
            Some(json!({"mode":"work","persona_uid":"aaaaa1","brain":"brn_a"}))
        );
        let mut got = Got::default();
        let ids = submit_all(&items, &mut got).unwrap();
        assert_eq!(ids, vec!["r1"]);
        assert_eq!(got.0[0].0, "seat_open");
        assert_eq!(got.0[0].1["brain"], "brn_a");
    }

    #[test]
    fn nothing_close_drafts_new_ones_and_the_seat_waits_for_the_persona() {
        let ps = [p("aaaaa1", "SEO專家", None)];
        let bs = [b("brn_a", "SEO 筆記")];
        let card = propose("  幫我整理每天的家庭帳本\u{7}\u{202E}  ", &ps, &bs, "safe").unwrap();
        let items = parse_suggest(&card).unwrap();
        assert!(items.iter().all(|i| i.uid.is_none()));
        assert_eq!(
            items[0].draft.as_ref().unwrap()["name"],
            "幫我整理每天的家庭帳本"
        );
        assert_eq!(items[2].draft, Some(json!({"mode":"safe"})));
        let mut got = Got::default();
        submit_all(&items, &mut got).unwrap();
        let acts: Vec<&str> = got.0.iter().map(|(a, _)| a.as_str()).collect();
        assert_eq!(acts, vec!["persona_add", "brain_add"]);
        assert!(acts.iter().all(|a| REQUEST_ACTS.contains(a)));
    }

    #[test]
    fn a_bound_brain_that_is_gone_falls_back_to_the_closest() {
        let ps = [p("aaaaa1", "SEO專家", Some("brn_gone"))];
        let bs = [b("brn_b", "SEO專家筆記")];
        let items = parse_suggest(&propose("SEO專家", &ps, &bs, "safe").unwrap()).unwrap();
        assert_eq!(items[1].uid.as_deref(), Some("brn_b"));
    }

    #[test]
    fn ties_go_to_the_shorter_name_then_the_smaller_uid() {
        // all three are in the purpose (0.9 each): the shorter name, then the smaller uid
        let ps = [
            p("aaaaa3", "SEO專家二號", None),
            p("aaaaa2", "SEO專家", None),
            p("aaaaa1", "SEO專家", None),
        ];
        let items = parse_suggest(&propose("SEO專家二號機", &ps, &[], "safe").unwrap()).unwrap();
        assert_eq!(items[0].uid.as_deref(), Some("aaaaa1"));
        // a higher score wins over a shorter name
        // (SEO們: se, eo of 4 + 3 bigrams = 0.57, shorter; SEO專家 is in it = 0.9)
        let ps = [p("aaaaa1", "SEO們", None), p("aaaaa2", "SEO專家", None)];
        assert!((score("SEO專家", "SEO們") - 4.0 / 7.0).abs() < 1e-9);
        let items = parse_suggest(&propose("SEO專家", &ps, &[], "safe").unwrap()).unwrap();
        assert_eq!(items[0].uid.as_deref(), Some("aaaaa2"));
    }

    #[test]
    fn a_bad_purpose_or_mode_is_refused() {
        assert_eq!(propose("", &[], &[], "safe"), Err("purpose_shape"));
        assert_eq!(propose(" 。 ", &[], &[], "safe"), Err("purpose_shape"));
        assert_eq!(
            propose(&"字".repeat(PURPOSE_MAX + 1), &[], &[], "safe"),
            Err("purpose_shape")
        );
        assert!(propose(&"字".repeat(PURPOSE_MAX), &[], &[], "safe").is_ok());
        for m in ["dgrs", "host", ""] {
            assert_eq!(propose("SEO專家", &[], &[], m), Err("request_mode"), "{m}");
        }
    }

    #[test]
    fn a_card_with_one_refused_item_hands_on_nothing() {
        let items = parse_suggest(&json!({"items":[
            {"what":"role","draft":{"name":"審查"}},
            {"what":"seat","draft":{"mode":"dgrs"}}]}))
        .unwrap();
        let mut got = Got::default();
        assert_eq!(
            submit_all(&items, &mut got),
            Err("request_mode".to_string())
        );
        assert!(got.0.is_empty());
    }
}
