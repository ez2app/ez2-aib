//! What may be asked for: `aib fp install <slug> --domain <domain> [--kind content|shop]
//! [--like <slug>] [--brand <name>] [--sub <label>]`. Everything is checked here before
//! any step is read, so a bad value never reaches the server.

/// What the shop sells through the site (SPEC r1): a shop also gets the purchase checks.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Content,
    Shop,
}

impl Kind {
    pub fn parse(s: &str) -> Option<Kind> {
        match s {
            "content" => Some(Kind::Content),
            "shop" => Some(Kind::Shop),
            _ => None,
        }
    }

    pub fn word(self) -> &'static str {
        match self {
            Kind::Content => "content",
            Kind::Shop => "shop",
        }
    }
}

/// One install, as asked.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Request {
    /// The footprint tenant's slug (the same code the shop's Ez2.PAGE has).
    pub slug: String,
    /// The shop's own domain, without `www.` (both are registered).
    pub domain: String,
    pub kind: Kind,
    /// The shop whose current settings are copied (plugin folder, plugin settings, the
    /// answer page's settings, the AI key). None: nothing is copied.
    pub like: Option<String>,
    /// The answer page's brand name: each shop gives its own, never the reference
    /// shop's (PREREAD L3).
    pub brand: Option<String>,
    /// The answer page's subdomain label (`aigc`): each shop gives its own (L3).
    pub sub: Option<String>,
}

/// A slug: 2–32 of `a-z 0-9`, starting with a letter.
pub fn slug_ok(s: &str) -> bool {
    let b = s.as_bytes();
    (2..=32).contains(&b.len())
        && b[0].is_ascii_lowercase()
        && b.iter()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
}

/// A domain: lower-case labels of `a-z 0-9 -`, at least two, none starting or ending
/// with `-`, at most 253 characters; not starting with `www.` (the apex is asked for,
/// `www.` is added) and not an address.
pub fn domain_ok(s: &str) -> bool {
    if s.is_empty() || s.len() > 253 || s.starts_with("www.") {
        return false;
    }
    let labels: Vec<&str> = s.split('.').collect();
    if labels.len() < 2 {
        return false;
    }
    let tld_alpha = labels
        .last()
        .is_some_and(|t| t.bytes().all(|c| c.is_ascii_lowercase()));
    tld_alpha
        && labels.iter().all(|l| {
            !l.is_empty()
                && l.len() <= 63
                && !l.starts_with('-')
                && !l.ends_with('-')
                && l.bytes()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
        })
}

/// A subdomain label: one label of `a-z 0-9 -`, 1–63, not `www`.
pub fn sub_ok(s: &str) -> bool {
    s != "www" && !s.contains('.') && domain_ok(&format!("{s}.example.com"))
}

/// A brand name: 1–60 characters, no control characters (it is shown on the answer
/// page and written into the engine's settings as it is).
pub fn brand_ok(s: &str) -> bool {
    let n = s.chars().count();
    (1..=60).contains(&n) && !s.chars().any(char::is_control) && s.trim() == s
}

impl Request {
    /// Checks every value; the first wrong one is named.
    pub fn check(&self) -> Result<(), &'static str> {
        if !slug_ok(&self.slug) {
            return Err("bad_slug");
        }
        if !domain_ok(&self.domain) {
            return Err("bad_domain");
        }
        if let Some(l) = &self.like {
            if !slug_ok(l) {
                return Err("bad_like");
            }
            if *l == self.slug {
                return Err("like_is_self");
            }
        }
        if self.brand.as_deref().is_some_and(|b| !brand_ok(b)) {
            return Err("bad_brand");
        }
        if self.sub.as_deref().is_some_and(|s| !sub_ok(s)) {
            return Err("bad_sub");
        }
        Ok(())
    }

    /// The two names the shop is registered under (SOP: `www.` is another site).
    pub fn hosts(&self) -> [String; 2] {
        [self.domain.clone(), format!("www.{}", self.domain)]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn req() -> Request {
        Request {
            slug: "wuxuan".into(),
            domain: "wuxuanbags.com".into(),
            kind: Kind::Content,
            like: Some("tahokkaido".into()),
            brand: Some("吳軒手工皮包".into()),
            sub: Some("aigc".into()),
        }
    }

    #[test]
    fn a_request_is_checked_before_any_step() {
        assert_eq!(req().check(), Ok(()));
        let mut r = req();
        r.slug = "Wuxuan".into();
        assert_eq!(r.check(), Err("bad_slug"));
        let mut r = req();
        r.slug = "1shop".into();
        assert_eq!(r.check(), Err("bad_slug"));
        let mut r = req();
        r.domain = "www.wuxuanbags.com".into();
        assert_eq!(
            r.check(),
            Err("bad_domain"),
            "reason: www_is_added_not_asked"
        );
        let mut r = req();
        r.domain = "wuxuanbags".into();
        assert_eq!(r.check(), Err("bad_domain"));
        let mut r = req();
        r.domain = "192.0.2.1".into(); // ip-literal-ok: documentation address, must be refused
        assert_eq!(
            r.check(),
            Err("bad_domain"),
            "reason: address_taken_as_domain"
        );
        let mut r = req();
        r.domain = "shop.-bad.com".into();
        assert_eq!(r.check(), Err("bad_domain"));
        let mut r = req();
        r.like = Some("wuxuan".into());
        assert_eq!(r.check(), Err("like_is_self"));
        let mut r = req();
        r.brand = Some("a\nb".into());
        assert_eq!(r.check(), Err("bad_brand"));
        let mut r = req();
        r.sub = Some("www".into());
        assert_eq!(r.check(), Err("bad_sub"));
        let mut r = req();
        r.sub = Some("ai.gc".into());
        assert_eq!(r.check(), Err("bad_sub"));
    }

    #[test]
    fn both_names_are_registered() {
        assert_eq!(
            req().hosts(),
            [
                "wuxuanbags.com".to_string(),
                "www.wuxuanbags.com".to_string()
            ]
        );
        assert_eq!(Kind::parse("shop"), Some(Kind::Shop));
        assert_eq!(Kind::parse("Shop"), None);
        assert_eq!(Kind::Shop.word(), "shop");
    }
}
