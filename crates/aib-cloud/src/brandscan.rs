// SPDX-License-Identifier: Apache-2.0
//! The brand scan of a hidden site (E9 step 4; plan `PLAN-brand-toggle-e9-ebb36-1111`
//! §2 / §2.1 / §4 P1). No I/O: the host reads each place of the site and hands the text
//! here; this module says whether a brand word shows, and whether the scan saw enough to
//! say so.
//!
//! * **Brand words.** `ez2` anywhere, no case. `aib` is ours too, but only as a whole word
//!   and only where a name is ours to give (a must-use plugin's file name, a plugin header):
//!   as a part of a longer word it is ordinary text.
//! * **Places.** Every place [`Place::required`] must have been read for the answer to be
//!   [`Scan::Clean`]; a place that could not be read, or was not handed in at all, makes the
//!   answer [`Scan::Incomplete`] — never clean (a scan without the rights to read a place
//!   has not shown it is clean).
//! * **Listed only.** Internal names (option names, scheduler hooks, log file names) are
//!   not renamed on a live site — that breaks serialized data — so a brand word there is
//!   listed and does not make the site dirty.
//! * **Not looked at.** The customer's own posts: a customer may write about us. The host
//!   does not hand them in, and nothing here asks for them.
//! * A site whose brand is shown is not scanned ([`Scan::NotHidden`]).

use alloc::vec::Vec;

use crate::brand::{Brand, BRAND_WORDS};

/// A word that is ours only as a whole word, and only in names we give.
pub const WHOLE_WORDS: &[&str] = &["aib"];

/// Where a piece of the site's text was read.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Place {
    /// B1: `admin_email` and `new_admin_email`.
    AdminMail,
    /// B2: the administrators' login, nicename, display name, url, nickname, description.
    AdminUser,
    /// B3 / B4: a must-use plugin's file name.
    MuPluginFile,
    /// B3: a plugin header (Plugin Name, Author, Author URI, Plugin URI, Description).
    PluginHeader,
    /// B4: the footprint loader's mark and its loading address.
    Loader,
    /// B6: the health file path the entry serves.
    HealthPath,
    /// B7: a mail's From name, From address, Reply-To, Return-Path, Message-ID domain.
    MailHeader,
    /// B8: response headers and error pages.
    Response,
    /// B9: the page's own output: generator, feed generator, HTML comments, JSON-LD
    /// publisher, robots, sitemap.
    PageOutput,
    /// B10: option names, scheduler hooks, log file names (listed only).
    InternalName,
}

impl Place {
    /// Every place, in order.
    pub const ALL: [Place; 10] = [
        Place::AdminMail,
        Place::AdminUser,
        Place::MuPluginFile,
        Place::PluginHeader,
        Place::Loader,
        Place::HealthPath,
        Place::MailHeader,
        Place::Response,
        Place::PageOutput,
        Place::InternalName,
    ];

    /// The fixed word.
    pub fn as_str(self) -> &'static str {
        match self {
            Place::AdminMail => "admin_mail",
            Place::AdminUser => "admin_user",
            Place::MuPluginFile => "mu_plugin_file",
            Place::PluginHeader => "plugin_header",
            Place::Loader => "loader",
            Place::HealthPath => "health_path",
            Place::MailHeader => "mail_header",
            Place::Response => "response",
            Place::PageOutput => "page_output",
            Place::InternalName => "internal_name",
        }
    }

    /// Whether a brand word here makes the site dirty (else it is listed only).
    pub fn judged(self) -> bool {
        !matches!(self, Place::InternalName)
    }

    /// Whether the scan must have read this place to call the site clean. Internal names
    /// are listed only, but must still have been read: "nothing listed" has to mean "looked".
    pub fn required(self) -> bool {
        true
    }

    /// Whether `aib` as a whole word counts here.
    fn names_ours(self) -> bool {
        matches!(self, Place::MuPluginFile | Place::PluginHeader)
    }
}

/// One piece of the site as the host read it: `None` when it could not be read.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Seen<'a> {
    /// Where.
    pub place: Place,
    /// What was read.
    pub text: Option<&'a str>,
}

/// One brand word found.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Hit {
    /// Where.
    pub place: Place,
    /// Which word (one of [`BRAND_WORDS`] or [`WHOLE_WORDS`]).
    pub word: &'static str,
    /// Whether it makes the site dirty.
    pub judged: bool,
}

/// The scan's answer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Scan {
    /// The brand is shown: nothing to scan.
    NotHidden,
    /// Every place was read and no judged place shows a brand word. `listed` are the hits
    /// in internal names.
    Clean {
        /// Hits in places that are listed only.
        listed: Vec<Hit>,
    },
    /// A judged place shows a brand word. A judged hit is enough: the site is dirty even
    /// when another place was not read (2240e L1: a hit must never read as "come back
    /// later").
    Dirty {
        /// Every hit, judged or not.
        hits: Vec<Hit>,
        /// The places not read, if any (the hit stands on its own; these are listed).
        missing: Vec<Place>,
    },
    /// A place was not read (absent or unreadable) and no judged place that was read shows
    /// a brand word: the site is not shown clean. Hits in internal names are still given.
    Incomplete {
        /// The places not read.
        missing: Vec<Place>,
        /// Every hit in what was read.
        hits: Vec<Hit>,
    },
}

impl Scan {
    /// The fixed word.
    pub fn as_str(&self) -> &'static str {
        match self {
            Scan::NotHidden => "not_hidden",
            Scan::Clean { .. } => "clean",
            Scan::Dirty { .. } => "dirty",
            Scan::Incomplete { .. } => "incomplete",
        }
    }
}

fn word_char(c: u8) -> bool {
    c.is_ascii_alphanumeric()
}

/// Whether `word` (lowercase) is in `low` as a whole word: no letter or digit on either side.
fn whole_word(low: &str, word: &str) -> bool {
    let b = low.as_bytes();
    let mut from = 0;
    while let Some(i) = low.get(from..).and_then(|rest| rest.find(word)) {
        let at = from + i;
        let end = at + word.len();
        let before = at == 0 || !word_char(b[at - 1]);
        let after = end == b.len() || !word_char(b[end]);
        if before && after {
            return true;
        }
        from = at + 1;
    }
    false
}

/// The brand words in `text` read at `place`, in the order of the word lists.
pub fn words_in(place: Place, text: &str) -> Vec<&'static str> {
    let low = text.to_ascii_lowercase();
    let mut out: Vec<&'static str> = BRAND_WORDS
        .iter()
        .copied()
        .filter(|w| low.contains(w))
        .collect();
    if place.names_ours() {
        out.extend(WHOLE_WORDS.iter().copied().filter(|w| whole_word(&low, w)));
    }
    out
}

/// Scan a site whose brand is `brand`, from what the host read.
pub fn scan(brand: Brand, seen: &[Seen<'_>]) -> Scan {
    if brand == Brand::Shown {
        return Scan::NotHidden;
    }
    let mut hits = Vec::new();
    let mut unreadable = Vec::new();
    for s in seen {
        match s.text {
            None => unreadable.push(s.place),
            Some(t) => hits.extend(words_in(s.place, t).into_iter().map(|word| Hit {
                place: s.place,
                word,
                judged: s.place.judged(),
            })),
        }
    }
    let mut missing: Vec<Place> = Place::ALL
        .iter()
        .copied()
        .filter(|p| p.required())
        .filter(|p| unreadable.contains(p) || !seen.iter().any(|s| s.place == *p))
        .collect();
    missing.dedup();
    if hits.iter().any(|h| h.judged) {
        Scan::Dirty { hits, missing }
    } else if !missing.is_empty() {
        Scan::Incomplete { missing, hits }
    } else {
        Scan::Clean { listed: hits }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn all_clean() -> Vec<Seen<'static>> {
        let text = |place: Place| -> &'static str {
            match place {
                Place::AdminMail => "owner@shop.example.test",
                Place::AdminUser => "owner Shop Example",
                Place::MuPluginFile => "site-cache-clear.php\nsite-analytics-loader.php",
                Place::PluginHeader => "Plugin Name: Site Cache Clear\nAuthor: Site",
                Place::Loader => "// loader site=s-abc123 https://shop.example.test/fp.js",
                Place::HealthPath => "/.well-known/site-health-s-abc123.json",
                Place::MailHeader => "From: Shop Example <wordpress@shop.example.test>",
                Place::Response => "server: Caddy",
                Place::PageOutput => "<meta name=\"generator\" content=\"WordPress 6.8\">",
                Place::InternalName => "cron: wp_version_check",
            }
        };
        Place::ALL
            .iter()
            .map(|p| Seen {
                place: *p,
                text: Some(text(*p)),
            })
            .collect()
    }

    fn with(place: Place, text: Option<&'static str>) -> Vec<Seen<'static>> {
        let mut v = all_clean();
        for s in v.iter_mut().filter(|s| s.place == place) {
            s.text = text;
        }
        v
    }

    #[test]
    fn a_shown_site_is_not_scanned() {
        assert_eq!(scan(Brand::Shown, &[]), Scan::NotHidden);
        assert_eq!(
            scan(Brand::Shown, &with(Place::AdminMail, Some("ops@ez2.shop"))),
            Scan::NotHidden
        );
    }

    #[test]
    fn every_place_read_and_nothing_found_is_clean() {
        let got = scan(Brand::Hidden, &all_clean());
        assert_eq!(got, Scan::Clean { listed: Vec::new() });
        assert_eq!(got.as_str(), "clean");
    }

    #[test]
    fn ez2_anywhere_in_a_judged_place_is_dirty() {
        // one planted in each judged place (plan §4 P1): every one is found
        for (place, text) in [
            (Place::AdminMail, "ops@EZ2.shop"),
            (Place::AdminUser, "ez2ops"),
            (Place::MuPluginFile, "ez2-cache-clear.php"),
            (Place::PluginHeader, "Author: Ez2"),
            (Place::Loader, "// ez2:fp-loader site=s-abc123"),
            (Place::HealthPath, "/.well-known/ez2-health-s-abc123.json"),
            (Place::MailHeader, "Message-ID: <x@mail.ez2.app>"),
            (Place::Response, "x-powered-by: ez2"),
            (Place::PageOutput, "<!-- built by ez2 -->"),
        ] {
            let got = scan(Brand::Hidden, &with(place, Some(text)));
            assert_eq!(
                got,
                Scan::Dirty {
                    hits: vec![Hit {
                        place,
                        word: "ez2",
                        judged: true
                    }],
                    missing: Vec::new(),
                },
                "{place:?}"
            );
        }
    }

    #[test]
    fn an_internal_name_is_listed_not_judged() {
        let got = scan(
            Brand::Hidden,
            &with(Place::InternalName, Some("option: ez2_fp_site")),
        );
        assert_eq!(
            got,
            Scan::Clean {
                listed: vec![Hit {
                    place: Place::InternalName,
                    word: "ez2",
                    judged: false
                }]
            }
        );
    }

    #[test]
    fn aib_counts_only_as_a_whole_word_in_names_we_give() {
        for name in ["aib-consent.php", "Plugin Name: AIB Helper", "x.aib", "aib"] {
            assert_eq!(words_in(Place::MuPluginFile, name), vec!["aib"], "{name}");
            assert_eq!(words_in(Place::PluginHeader, name), vec!["aib"], "{name}");
        }
        // inside a longer word it is ordinary text
        for name in [
            "aibd.php",
            "taib-x.php",
            "Plugin Name: Maibox",
            "aib2",
            "kaib",
        ] {
            assert!(words_in(Place::MuPluginFile, name).is_empty(), "{name}");
        }
        // and outside names we give it is not looked for at all
        assert!(words_in(Place::PageOutput, "<p>aib</p>").is_empty());
        assert!(words_in(Place::AdminUser, "aib").is_empty());
        // a whole word found after a part-word earlier in the same text
        assert_eq!(words_in(Place::PluginHeader, "aibd and aib"), vec!["aib"]);
        // both words
        assert_eq!(
            words_in(Place::MuPluginFile, "ez2-aib.php"),
            vec!["ez2", "aib"]
        );
    }

    #[test]
    fn a_place_not_read_is_incomplete_never_clean() {
        // unreadable (no rights, an answer that could not be read)
        let got = scan(Brand::Hidden, &with(Place::Response, None));
        assert_eq!(
            got,
            Scan::Incomplete {
                missing: vec![Place::Response],
                hits: Vec::new()
            }
        );
        assert_eq!(got.as_str(), "incomplete");
        // not handed in at all
        let mut v = all_clean();
        v.retain(|s| s.place != Place::InternalName);
        assert_eq!(
            scan(Brand::Hidden, &v),
            Scan::Incomplete {
                missing: vec![Place::InternalName],
                hits: Vec::new()
            }
        );
        // nothing handed in
        assert_eq!(
            scan(Brand::Hidden, &[]),
            Scan::Incomplete {
                missing: Place::ALL.to_vec(),
                hits: Vec::new()
            }
        );
        // a judged hit in what was read makes the site dirty even with a place not read;
        // the place not read is still named (2240e L1)
        let mut v = with(Place::AdminMail, Some("ops@ez2.shop"));
        v.retain(|s| s.place != Place::PageOutput);
        assert_eq!(
            scan(Brand::Hidden, &v),
            Scan::Dirty {
                hits: vec![Hit {
                    place: Place::AdminMail,
                    word: "ez2",
                    judged: true
                }],
                missing: vec![Place::PageOutput],
            }
        );
        // a hit only in an internal name, with a place not read, stays incomplete
        let mut v = with(Place::InternalName, Some("ez2_cron"));
        v.retain(|s| s.place != Place::PageOutput);
        assert_eq!(
            scan(Brand::Hidden, &v),
            Scan::Incomplete {
                missing: vec![Place::PageOutput],
                hits: vec![Hit {
                    place: Place::InternalName,
                    word: "ez2",
                    judged: false
                }]
            }
        );
    }

    #[test]
    fn one_place_read_twice_counts_once_and_one_unreadable_read_spoils_it() {
        let mut v = all_clean();
        v.push(Seen {
            place: Place::PageOutput,
            text: None,
        });
        assert_eq!(
            scan(Brand::Hidden, &v),
            Scan::Incomplete {
                missing: vec![Place::PageOutput],
                hits: Vec::new()
            }
        );
        let mut v = all_clean();
        v.push(Seen {
            place: Place::PageOutput,
            text: Some("<!-- ok -->"),
        });
        assert_eq!(scan(Brand::Hidden, &v), Scan::Clean { listed: Vec::new() });
    }

    #[test]
    fn the_words_are_fixed() {
        let words: Vec<&str> = Place::ALL.iter().map(|p| p.as_str()).collect();
        assert_eq!(
            words,
            [
                "admin_mail",
                "admin_user",
                "mu_plugin_file",
                "plugin_header",
                "loader",
                "health_path",
                "mail_header",
                "response",
                "page_output",
                "internal_name"
            ]
        );
        assert!(Place::ALL.iter().all(|p| p.required()));
        assert_eq!(Place::ALL.iter().filter(|p| !p.judged()).count(), 1);
    }
}
