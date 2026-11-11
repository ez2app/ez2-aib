// SPDX-License-Identifier: Apache-2.0
//! What a newly opened site starts with: its title, the login names of its first
//! administrators, and whether each paid plugin of the template is really activated at
//! its vendor. No I/O: the host shell asks the site and the vendors and hands the answers
//! here.
//!
//! The rules were set by the operator on the day the first site was opened by hand:
//! - the title is the first label of the site's domain (`www` does not count);
//! - an administrator's login name is the part of the mail address before the `@`, and a
//!   number is added when that name is taken;
//! - a paid plugin counts as activated only on the vendor's own answer. What is stored in
//!   the site is not evidence: an earlier tool wrote "valid" there itself.
//!
//! The mail addresses of the administrators are not in this crate; they come from the
//! host's configuration.

use alloc::string::{String, ToString};

/// The title of a new site: the first label of `host` that is not `www`.
/// `host` is a name that already passed the request's own check.
pub fn site_title(host: &str) -> &str {
    let rest = host.strip_prefix("www.").unwrap_or(host);
    let label = rest.split('.').next().unwrap_or(rest);
    if label.is_empty() {
        host
    } else {
        label
    }
}

/// The longest login name the site software stores.
pub const LOGIN_MAX: usize = 60;
/// The last number tried after a taken name before giving up.
pub const LOGIN_TRIES: u32 = 99;

/// The login name for the administrator with mail address `email`: the part before the
/// `@`, in lower case, of letters, digits, `.`, `_` and `-` only, starting and ending
/// with a letter or digit. `taken` are the login names and the author-address names
/// already in the site; a taken name gets `2`, `3`, … added. `None` when nothing usable
/// is left of the address or every number is taken.
pub fn admin_login(email: &str, taken: &[&str]) -> Option<String> {
    let (local, domain) = email.split_once('@')?;
    if domain.is_empty() || domain.contains('@') {
        return None;
    }
    let cleaned: String = local
        .chars()
        .filter_map(|c| {
            let c = c.to_ascii_lowercase();
            (c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, '.' | '_' | '-'))
                .then_some(c)
        })
        .collect();
    let base = cleaned.trim_matches(|c: char| !c.is_ascii_alphanumeric());
    if base.is_empty() {
        return None;
    }
    // room is left for the number
    let base = &base[..base.len().min(LOGIN_MAX - 2)];
    let base = base.trim_end_matches(|c: char| !c.is_ascii_alphanumeric());
    let free = |name: &str| !taken.iter().any(|t| t.eq_ignore_ascii_case(name));
    if free(base) {
        return Some(base.to_string());
    }
    (2..=LOGIN_TRIES)
        .map(|n| format!("{base}{n}"))
        .find(|name| free(name))
}

/// Where a paid plugin stands at its vendor, from the vendor's own answer.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum License {
    /// The vendor has this site as activated.
    Active,
    /// The key is good and this site is not activated yet; there is room for it.
    Inactive,
    /// The key is good and no activation is left for one more site.
    Quota,
    /// The vendor says the key itself is not good (expired, revoked, not its key).
    NotValid,
    /// The vendor did not answer, or answered something this code does not know. Never
    /// read as "active" and never as "not there".
    Unknown,
}

impl License {
    /// The fixed word for the journal and for the answer to the customer's key.
    pub fn code(self) -> &'static str {
        match self {
            License::Active => "license_active",
            License::Inactive => "license_inactive",
            License::Quota => "license_quota",
            License::NotValid => "license_not_valid",
            License::Unknown => "license_unknown",
        }
    }
}

/// A vendor that answers with a status word for this site and the number of activations
/// left (`None` when it does not say or the count is not a number; `unlimited` plans say
/// a word instead of a number and are passed as `None`).
/// `status` is the vendor's word, `left` what it says is left.
pub fn license_by_status(status: Option<&str>, left: Option<u64>) -> License {
    match status {
        Some("valid") => License::Active,
        Some("site_inactive" | "inactive") => match left {
            Some(0) => License::Quota,
            _ => License::Inactive,
        },
        Some(
            "expired"
            | "disabled"
            | "revoked"
            | "invalid"
            | "missing"
            | "item_name_mismatch"
            | "invalid_item_id"
            | "key_mismatch"
            | "license_not_activable",
        ) => License::NotValid,
        Some("no_activations_left") => License::Quota,
        _ => License::Unknown,
    }
}

/// A vendor that keeps one activation record per site. `license_status` is its word for
/// the key (`active` is the good one). `site_holds_number` is whether the site holds the
/// number of an activation record; `activation_found` is the vendor's answer about that
/// record (`None`: the vendor was not asked, or did not answer). `count` and `limit` are
/// its activations used and allowed (`limit` `None`: no limit).
///
/// A site that holds a number the vendor was not asked about is "unknown": activating
/// again on that would open a second record and use up one more activation. Only a site
/// with no number, or one whose number the vendor says it does not have, is "inactive".
pub fn license_by_activation(
    license_status: Option<&str>,
    site_holds_number: bool,
    activation_found: Option<bool>,
    count: Option<u64>,
    limit: Option<u64>,
) -> License {
    match license_status {
        Some("active") => {}
        Some("revoked" | "inactive" | "expired") => return License::NotValid,
        _ => return License::Unknown,
    }
    if site_holds_number {
        match activation_found {
            Some(true) => return License::Active,
            Some(false) => {}
            None => return License::Unknown,
        }
    }
    match (count, limit) {
        (Some(c), Some(l)) if c >= l => License::Quota,
        // a limit with no count cannot be judged
        (None, Some(_)) => License::Unknown,
        _ => License::Inactive,
    }
}

/// What to do about one plugin while a site is being opened.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LicenseStep {
    /// Nothing: the vendor already has the site.
    Done,
    /// Ask the vendor to activate, then ask again.
    Activate,
    /// Stop with this word; the site is kept and a person looks.
    Stop(&'static str),
}

/// Before activating: only a known "not activated, room left" leads to an activation.
pub fn license_before(now: License) -> LicenseStep {
    match now {
        License::Active => LicenseStep::Done,
        License::Inactive => LicenseStep::Activate,
        other => LicenseStep::Stop(other.code()),
    }
}

/// After activating: `call_ok` is whether the plugin's own activation reported no error,
/// `now` the vendor's answer to a fresh question. Both are needed; a plugin that stores
/// "valid" by itself does not count, and neither does a vendor that still says no.
pub fn license_after(call_ok: bool, now: License) -> Result<(), &'static str> {
    match (call_ok, now) {
        (true, License::Active) => Ok(()),
        (false, _) => Err("license_activation_failed"),
        (true, other) => Err(other.code()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_title_is_the_first_label_and_www_does_not_count() {
        assert_eq!(site_title("example.com"), "example");
        assert_eq!(site_title("www.example.com"), "example");
        assert_eq!(site_title("shop.example.com"), "shop");
        assert_eq!(site_title("www.shop.example.co.uk"), "shop");
        // a name that only starts with the letters www is not the www label
        assert_eq!(site_title("wwwexample.com"), "wwwexample");
    }

    #[test]
    fn the_login_name_is_the_mail_prefix() {
        assert_eq!(
            admin_login("first@example.com", &[]).as_deref(),
            Some("first")
        );
        assert_eq!(
            admin_login("First.Last@example.com", &[]).as_deref(),
            Some("first.last")
        );
        assert_eq!(
            admin_login("a+tag@example.com", &[]).as_deref(),
            Some("atag")
        );
        assert_eq!(
            admin_login("-._x_.-@example.com", &[]).as_deref(),
            Some("x")
        );
    }

    #[test]
    fn a_taken_login_name_gets_a_number() {
        assert_eq!(
            admin_login("first@example.com", &["first"]).as_deref(),
            Some("first2")
        );
        assert_eq!(
            admin_login("first@example.com", &["First", "first2"]).as_deref(),
            Some("first3")
        );
        let all: alloc::vec::Vec<String> = core::iter::once("first".to_string())
            .chain((2..=LOGIN_TRIES).map(|n| format!("first{n}")))
            .collect();
        let refs: alloc::vec::Vec<&str> = all.iter().map(String::as_str).collect();
        assert_eq!(admin_login("first@example.com", &refs), None);
    }

    #[test]
    fn an_address_with_nothing_usable_gives_no_login_name() {
        for bad in [
            "",
            "first",
            "@example.com",
            "first@",
            "a@b@c",
            "+++@example.com",
            "...@example.com",
        ] {
            assert_eq!(admin_login(bad, &[]), None, "{bad}");
        }
    }

    #[test]
    fn a_long_prefix_is_cut_and_still_takes_a_number() {
        let long = format!("{}@example.com", "a".repeat(80));
        let name = admin_login(&long, &[]).unwrap();
        assert_eq!(name.len(), LOGIN_MAX - 2);
        let second = admin_login(&long, &[name.as_str()]).unwrap();
        assert!(second.len() <= LOGIN_MAX && second.ends_with('2'));
    }

    #[test]
    fn a_status_vendor_is_read_by_its_own_words() {
        assert_eq!(license_by_status(Some("valid"), Some(5)), License::Active);
        assert_eq!(
            license_by_status(Some("site_inactive"), Some(9917)),
            License::Inactive
        );
        assert_eq!(
            license_by_status(Some("site_inactive"), None),
            License::Inactive
        );
        assert_eq!(
            license_by_status(Some("site_inactive"), Some(0)),
            License::Quota
        );
        assert_eq!(
            license_by_status(Some("no_activations_left"), None),
            License::Quota
        );
        assert_eq!(
            license_by_status(Some("expired"), Some(3)),
            License::NotValid
        );
    }

    #[test]
    fn no_answer_or_an_unknown_word_is_unknown_never_active() {
        assert_eq!(license_by_status(None, Some(5)), License::Unknown);
        assert_eq!(license_by_status(Some(""), None), License::Unknown);
        assert_eq!(license_by_status(Some("VALID"), None), License::Unknown);
        assert_eq!(
            license_by_status(Some("something_new"), None),
            License::Unknown
        );
        assert_eq!(
            license_by_activation(None, true, Some(true), Some(1), None),
            License::Unknown
        );
        assert_eq!(
            license_by_activation(Some("something_new"), true, Some(true), None, None),
            License::Unknown
        );
    }

    #[test]
    fn an_activation_vendor_needs_its_own_record_of_this_site() {
        let a = license_by_activation;
        assert_eq!(
            a(Some("active"), true, Some(true), Some(28), None),
            License::Active
        );
        // the site holds a number the vendor does not have: not activated
        assert_eq!(
            a(Some("active"), true, Some(false), Some(27), None),
            License::Inactive
        );
        // the site holds no number at all: not activated, whatever was or was not asked
        assert_eq!(
            a(Some("active"), false, None, Some(27), None),
            License::Inactive
        );
        assert_eq!(
            a(Some("active"), false, Some(true), Some(27), None),
            License::Inactive
        );
        assert_eq!(
            a(Some("active"), false, None, Some(3), Some(3)),
            License::Quota
        );
        assert_eq!(
            a(Some("active"), true, Some(false), Some(3), Some(3)),
            License::Quota
        );
        assert_eq!(
            a(Some("active"), false, None, Some(2), Some(3)),
            License::Inactive
        );
        assert_eq!(
            a(Some("active"), false, None, None, Some(3)),
            License::Unknown
        );
        assert_eq!(
            a(Some("revoked"), true, Some(true), Some(1), None),
            License::NotValid
        );
    }

    #[test]
    fn a_number_the_vendor_was_not_asked_about_is_unknown_not_inactive() {
        // the site holds an activation number and the vendor did not answer about it:
        // activating again would open a second record and use up one more activation
        let got = license_by_activation(Some("active"), true, None, Some(28), None);
        assert_eq!(got, License::Unknown);
        assert_eq!(license_before(got), LicenseStep::Stop("license_unknown"));
        // with room left, with none left, with or without a count: still unknown
        for (count, limit) in [(Some(2), Some(3)), (Some(3), Some(3)), (None, None)] {
            assert_eq!(
                license_by_activation(Some("active"), true, None, count, limit),
                License::Unknown
            );
        }
    }

    #[test]
    fn only_a_known_inactive_license_is_activated() {
        assert_eq!(license_before(License::Active), LicenseStep::Done);
        assert_eq!(license_before(License::Inactive), LicenseStep::Activate);
        assert_eq!(
            license_before(License::Quota),
            LicenseStep::Stop("license_quota")
        );
        assert_eq!(
            license_before(License::NotValid),
            LicenseStep::Stop("license_not_valid")
        );
        assert_eq!(
            license_before(License::Unknown),
            LicenseStep::Stop("license_unknown")
        );
    }

    #[test]
    fn activated_means_the_call_and_the_vendor_both_say_so() {
        assert_eq!(license_after(true, License::Active), Ok(()));
        assert_eq!(
            license_after(false, License::Active),
            Err("license_activation_failed")
        );
        assert_eq!(
            license_after(true, License::Inactive),
            Err("license_inactive")
        );
        assert_eq!(
            license_after(true, License::Unknown),
            Err("license_unknown")
        );
        assert_eq!(license_after(true, License::Quota), Err("license_quota"));
    }
}
