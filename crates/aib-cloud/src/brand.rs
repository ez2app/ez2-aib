// SPDX-License-Identifier: Apache-2.0
//! Whether a site shows the Ez2 brand, and what changes when it does not (E9; plan
//! `PLAN-brand-toggle-e9-ebb36-1111` v1). No I/O: the host shell reads the site and hands
//! the facts here.
//!
//! The operator's rule (11-11): the brand is **shown by default** and can be hidden as a
//! feature of a plan. Hiding is a fact of one site, kept in its record as `brand`; a plan
//! only decides whether "hidden" may be chosen. The record says what is wanted. Whether a
//! hidden site really shows nothing is what the brand scan's receipt says, never this
//! field alone.
//!
//! What hiding changes, as far as this crate decides it:
//! - our own must-use plugins of the template are installed under neutral file names with
//!   neutral headers (the same code, built from one source; nothing is renamed on a site);
//! - a file under the name the *other* brand uses is removed only when it is ours (known
//!   name and our mark inside); anything else under that name stops the step;
//! - the first administrator's mail is the customer's, else the host's neutral mail, and
//!   never the operations mail; its public names carry the site's title, not a person.

use alloc::string::String;
use serde_json::Value;

/// The record key.
pub const KEY: &str = "brand";

/// Whether a site shows the Ez2 brand.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Brand {
    Shown,
    Hidden,
}

impl Brand {
    /// The value written in the record.
    pub fn as_str(self) -> &'static str {
        match self {
            Brand::Shown => "shown",
            Brand::Hidden => "hidden",
        }
    }
}

/// The brand a site record asks for. Only a record **without** the key is `Shown` (records
/// written before E9); `null`, another type or an unknown word is refused, never guessed.
pub fn record_brand(rec: &Value) -> Result<Brand, &'static str> {
    match rec.get(KEY) {
        None => Ok(Brand::Shown),
        Some(Value::String(s)) if s == "shown" => Ok(Brand::Shown),
        Some(Value::String(s)) if s == "hidden" => Ok(Brand::Hidden),
        Some(_) => Err("brand_value"),
    }
}

/// Words that give the brand away. Matched without regard to case. Only what *we* put in a
/// site is scanned for them: a customer's own text that names Ez2 is theirs.
pub const BRAND_WORDS: &[&str] = &["ez2"];

/// Does `s` contain a brand word?
pub fn has_brand_word(s: &str) -> bool {
    let low = s.to_ascii_lowercase();
    BRAND_WORDS.iter().any(|w| low.contains(w))
}

/// Our own must-use plugins in the template: (file name with the brand, neutral file name).
/// The code inside is the same; the neutral build also carries a neutral header.
pub const OWN_MU_PLUGINS: &[(&str, &str)] = &[
    ("ez2-cache-clear.php", "site-cache-clear.php"),
    ("ez2-footprint-loader.php", "site-analytics-loader.php"),
    ("ez2-fs-autolicense.php", "site-license-sync.php"),
];

/// The file name one of our must-use plugins has on a site of `brand`. `name` may be
/// either of its two names. `None`: not one of ours.
pub fn mu_plugin_name(brand: Brand, name: &str) -> Option<&'static str> {
    let (shown, hidden) = OWN_MU_PLUGINS
        .iter()
        .find(|(s, h)| *s == name || *h == name)?;
    Some(match brand {
        Brand::Shown => shown,
        Brand::Hidden => hidden,
    })
}

/// The name the same plugin has under the other brand: it must not be on the site.
pub fn mu_plugin_other_name(brand: Brand, name: &str) -> Option<&'static str> {
    let other = match brand {
        Brand::Shown => Brand::Hidden,
        Brand::Hidden => Brand::Shown,
    };
    mu_plugin_name(other, name)
}

/// The mark the footprint loader carries on its first lines (fpkey finds its own file by
/// it). The site's key follows the mark.
pub fn loader_mark(brand: Brand) -> &'static str {
    match brand {
        Brand::Shown => "// ez2:fp-loader site=",
        Brand::Hidden => "// loader site=",
    }
}

/// What was found under the name the other brand uses.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Found {
    Absent,
    /// A known name of ours with our mark inside.
    Ours,
    /// The name is there, but the file is not ours (no mark, a customer's file).
    NotOurs,
    /// It could not be read.
    Unreadable,
}

/// What to do about the other brand's file before the step can be Done.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OtherName {
    /// Nothing there: this part is done.
    Clear,
    /// Ours: remove it, then look again.
    Remove,
    /// Stop and let a person look; nothing is removed.
    Stop(&'static str),
}

/// Two loaders must never both run, and a file that is not ours is never removed.
pub fn other_name_action(found: Found) -> OtherName {
    match found {
        Found::Absent => OtherName::Clear,
        Found::Ours => OtherName::Remove,
        Found::NotOurs => OtherName::Stop("brand_other_name_not_ours"),
        Found::Unreadable => OtherName::Stop("brand_other_name_unreadable"),
    }
}

/// The first administrator's mail. `customer`: the address the order gives, if any;
/// `neutral`: the host's neutral mail, if configured; `ops`: the operations mail.
///
/// Shown: the operations mail (the rule before E9). Hidden: the customer's, else the
/// neutral one, else stop: never the operations mail, and a "neutral" mail that carries a
/// brand word is refused.
pub fn admin_mail<'a>(
    brand: Brand,
    customer: Option<&'a str>,
    neutral: Option<&'a str>,
    ops: &'a str,
) -> Result<&'a str, &'static str> {
    match brand {
        Brand::Shown => Ok(ops),
        Brand::Hidden => {
            let pick = customer
                .filter(|m| !m.is_empty())
                .or(neutral.filter(|m| !m.is_empty()))
                .ok_or("brand_no_neutral_mail")?;
            if pick == ops || has_brand_word(pick) {
                return Err("brand_mail_not_neutral");
            }
            Ok(pick)
        }
    }
}

/// The administrator's public names (display name, nickname; the author page uses the
/// nice name, which WordPress derives from the login). Hidden: the site's title, so no
/// person of ours is named on the site. Shown: the login, as before.
pub fn admin_display_name(brand: Brand, title: &str, login: &str) -> String {
    match brand {
        Brand::Shown => String::from(login),
        Brand::Hidden => String::from(title),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn only_a_missing_key_means_shown() {
        assert_eq!(record_brand(&json!({"site": "s-1"})), Ok(Brand::Shown));
        assert_eq!(record_brand(&json!({"brand": "shown"})), Ok(Brand::Shown));
        assert_eq!(record_brand(&json!({"brand": "hidden"})), Ok(Brand::Hidden));
        for bad in [
            json!({"brand": null}),
            json!({"brand": true}),
            json!({"brand": 1}),
            json!({"brand": "Hidden"}),
            json!({"brand": "hide"}),
            json!({"brand": ""}),
            json!({"brand": ["hidden"]}),
        ] {
            assert_eq!(record_brand(&bad), Err("brand_value"), "{bad}");
        }
        for b in [Brand::Shown, Brand::Hidden] {
            assert_eq!(record_brand(&json!({ KEY: b.as_str() })), Ok(b));
        }
    }

    #[test]
    fn neutral_names_carry_no_brand_word() {
        for (shown, hidden) in OWN_MU_PLUGINS {
            assert!(has_brand_word(shown), "{shown}");
            assert!(!has_brand_word(hidden), "{hidden}");
            assert!(hidden.ends_with(".php") && shown.ends_with(".php"));
        }
        assert!(!has_brand_word(loader_mark(Brand::Hidden)));
        assert!(has_brand_word("Powered by EZ2"));
        assert!(has_brand_word("x-Ez2-y"));
        assert!(!has_brand_word("e z 2"));
    }

    #[test]
    fn each_plugin_has_one_name_per_brand() {
        let n = "ez2-footprint-loader.php";
        assert_eq!(
            mu_plugin_name(Brand::Hidden, n),
            Some("site-analytics-loader.php")
        );
        assert_eq!(mu_plugin_name(Brand::Shown, n), Some(n));
        assert_eq!(
            mu_plugin_name(Brand::Shown, "site-analytics-loader.php"),
            Some(n)
        );
        assert_eq!(mu_plugin_other_name(Brand::Hidden, n), Some(n));
        assert_eq!(
            mu_plugin_other_name(Brand::Shown, n),
            Some("site-analytics-loader.php")
        );
        assert_eq!(mu_plugin_name(Brand::Hidden, "customer-plugin.php"), None);
        // no name is used twice, across both columns
        let mut all: alloc::vec::Vec<&str> =
            OWN_MU_PLUGINS.iter().flat_map(|(s, h)| [*s, *h]).collect();
        all.sort_unstable();
        let len = all.len();
        all.dedup();
        assert_eq!(all.len(), len);
    }

    // knife: a file that is not ours is never removed; two loaders never both stay
    #[test]
    fn the_other_brands_file_is_removed_only_when_ours() {
        assert_eq!(other_name_action(Found::Absent), OtherName::Clear);
        assert_eq!(other_name_action(Found::Ours), OtherName::Remove);
        assert_eq!(
            other_name_action(Found::NotOurs),
            OtherName::Stop("brand_other_name_not_ours")
        );
        assert_eq!(
            other_name_action(Found::Unreadable),
            OtherName::Stop("brand_other_name_unreadable")
        );
    }

    // knife: a hidden site never gets the operations mail
    #[test]
    fn a_hidden_site_never_gets_the_operations_mail() {
        let ops = "ops@ez2.shop";
        assert_eq!(
            admin_mail(Brand::Shown, Some("c@shop.test"), None, ops),
            Ok(ops)
        );
        assert_eq!(
            admin_mail(Brand::Hidden, Some("c@shop.test"), Some("n@mail.test"), ops),
            Ok("c@shop.test")
        );
        assert_eq!(
            admin_mail(Brand::Hidden, None, Some("n@mail.test"), ops),
            Ok("n@mail.test")
        );
        assert_eq!(
            admin_mail(Brand::Hidden, Some(""), Some("n@mail.test"), ops),
            Ok("n@mail.test")
        );
        assert_eq!(
            admin_mail(Brand::Hidden, None, None, ops),
            Err("brand_no_neutral_mail")
        );
        assert_eq!(
            admin_mail(Brand::Hidden, None, Some(""), ops),
            Err("brand_no_neutral_mail")
        );
        assert_eq!(
            admin_mail(Brand::Hidden, None, Some(ops), ops),
            Err("brand_mail_not_neutral")
        );
        assert_eq!(
            admin_mail(Brand::Hidden, None, Some("site@EZ2.example"), ops),
            Err("brand_mail_not_neutral")
        );
    }

    #[test]
    fn a_hidden_site_names_no_person_of_ours() {
        assert_eq!(admin_display_name(Brand::Hidden, "shop", "andy"), "shop");
        assert_eq!(admin_display_name(Brand::Shown, "shop", "andy"), "andy");
    }
}
