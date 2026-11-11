// SPDX-License-Identifier: Apache-2.0
//! Whose a shared backup is (DEV 4.6 §15.4 step 3, plan §14.7 #11): a customer restores a
//! site from a backup they shared with our drive account. The host can fetch any file
//! shared with that account, so "the host can download it" says nothing about whose it
//! is — without a gate, one customer could restore another customer's backup (members,
//! orders) onto their own domain by naming its file.
//!
//! The hard condition: the file's owner, and the person who shared it when the drive
//! names one, are addresses this tenant registered in its roster (`emails`). Anything
//! short of that stops the restore:
//!
//! - no owner named (a shared-drive file, or the drive did not say) ⇒ [`Gate::Unknown`];
//! - the roster registers no address ⇒ [`Gate::Unknown`] (nothing to compare with);
//! - more than one owner, an address that is not an address, or one that is not
//!   registered ⇒ [`Gate::Refused`];
//! - an owner or sharer address that **another** tenant's roster also registers ⇒
//!   [`Gate::Refused`] (c477a M1): with only this tenant's roster read, tenant B could
//!   restore tenant A's backup by registering A's address too. So the caller reads every
//!   roster on the host and passes the other tenants' addresses; when it cannot read them
//!   all, the answer is [`Gate::Unknown`] ([`Why::OtherRostersUnreadable`]).
//!
//! Only [`Gate::Allowed`] lets the restore go on; `Unknown` is not a softer `Allowed`.
//!
//! When the drive names the sharer, the caller **must** pass it (c477a N2): left out, only
//! the owner is judged, and a file shared on by someone else would pass.
//!
//! The gate is as strong as the roster's `emails`. **Pending Andy** (c477a M1): an address
//! goes into a roster only after its holder confirmed it by mail; who writes it and how
//! that confirmation is made are not decided yet.
//!
//! Addresses are compared after [`normalize`]: outer spaces dropped and the whole address
//! lower-cased, then equal as a whole. Case is treated as the same on both sides of the
//! `@` (c477a N1): the part before it may be case-sensitive by RFC 5321, but every large
//! mail service treats it as not, and a customer writes their address either way. That is
//! the one provider-style rule taken. No dropping dots or `+tag`: those differ between
//! providers, the drive treats such addresses as other accounts, and taking them would let
//! through an owner the roster never named.
//!
//! No I/O: reading the owner from the drive and the rosters from their files are the caller's.

use alloc::string::String;

/// Longest address taken (RFC 5321 path limit). Longer is not an address.
pub const ADDRESS_MAX: usize = 254;

fn blank_or_control(b: u8) -> bool {
    b.is_ascii_whitespace() || b.is_ascii_control()
}

/// An address as compared: outer spaces dropped, lower-cased. `None` when it is not one
/// address: not ASCII, a space or control character inside, not exactly one `@`, an empty
/// side, a domain without a dot or with an empty label, or longer than [`ADDRESS_MAX`].
pub fn normalize(raw: &str) -> Option<String> {
    let s = raw.trim();
    if s.is_empty() || s.len() > ADDRESS_MAX || !s.is_ascii() {
        return None;
    }
    if s.bytes().any(blank_or_control) {
        return None;
    }
    let mut parts = s.split('@');
    let local = parts.next()?;
    let domain = parts.next()?;
    if parts.next().is_some() || local.is_empty() {
        return None;
    }
    if !domain.contains('.') || domain.split('.').any(str::is_empty) {
        return None;
    }
    Some(s.to_ascii_lowercase())
}

/// Why a restore may not go on.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Why {
    /// The drive named no owner.
    NoOwner,
    /// The roster registers no address for this tenant.
    NoneRegistered,
    /// The drive named more than one owner.
    ManyOwners,
    /// The owner or the sharer is not an address.
    NotAnAddress,
    /// The owner is not one of the tenant's addresses.
    OwnerNotRegistered,
    /// The sharer is not one of the tenant's addresses.
    SharerNotRegistered,
    /// The owner or the sharer is also registered by another tenant.
    AddressOfAnotherTenant,
    /// Not every other tenant's roster could be read (set by the caller).
    OtherRostersUnreadable,
}

impl Why {
    /// The fixed word shown. Nothing from the drive or the roster is echoed.
    pub fn as_str(self) -> &'static str {
        match self {
            Why::NoOwner => "share_owner_unknown",
            Why::NoneRegistered => "share_no_registered_address",
            Why::ManyOwners => "share_many_owners",
            Why::NotAnAddress => "share_not_an_address",
            Why::OwnerNotRegistered => "share_owner_not_this_tenant",
            Why::SharerNotRegistered => "share_sharer_not_this_tenant",
            Why::AddressOfAnotherTenant => "share_address_also_another_tenant",
            Why::OtherRostersUnreadable => "share_other_rosters_unreadable",
        }
    }
}

/// The gate's answer.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Gate {
    /// The backup is this tenant's: the restore may go on.
    Allowed,
    /// Cannot tell whose it is: stop, and say so.
    Unknown(Why),
    /// It is not this tenant's, or not cleanly: stop.
    Refused(Why),
}

impl Gate {
    /// Only [`Gate::Allowed`] passes.
    pub fn passes(self) -> bool {
        matches!(self, Gate::Allowed)
    }
}

fn registered(address: &str, tenant: &[String]) -> Option<bool> {
    let a = normalize(address)?;
    Some(tenant.contains(&a))
}

/// Judge one shared file. `owners` are the owners the drive names for it (all of them, as
/// given); `sharer` the person who shared it, when the drive says (then it must be given);
/// `tenant` the tenant's registered addresses and `others` every address the **other**
/// tenants' rosters register, both already normalized (as
/// [`crate::roster::Roster::emails`] holds them).
pub fn judge(owners: &[&str], sharer: Option<&str>, tenant: &[String], others: &[String]) -> Gate {
    if tenant.is_empty() {
        return Gate::Unknown(Why::NoneRegistered);
    }
    let owner = match owners {
        [] => return Gate::Unknown(Why::NoOwner),
        [one] => *one,
        _ => return Gate::Refused(Why::ManyOwners),
    };
    match registered(owner, tenant) {
        Some(true) => {}
        Some(false) => return Gate::Refused(Why::OwnerNotRegistered),
        None => return Gate::Refused(Why::NotAnAddress),
    }
    if let Some(s) = sharer {
        let Some(ok) = registered(s, tenant) else {
            return Gate::Refused(Why::NotAnAddress);
        };
        if !ok {
            return Gate::Refused(Why::SharerNotRegistered);
        }
    }
    let theirs = |a: &str| normalize(a).is_some_and(|a| others.contains(&a));
    if theirs(owner) || sharer.is_some_and(theirs) {
        return Gate::Refused(Why::AddressOfAnotherTenant);
    }
    Gate::Allowed
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloc::vec::Vec;

    fn tenant() -> Vec<String> {
        alloc::vec![
            String::from("owner@example.com"),
            String::from("staff@example.com"),
        ]
    }

    #[test]
    fn an_address_is_trimmed_and_lower_cased_and_nothing_else() {
        assert_eq!(
            normalize("  Owner@Example.COM \n").as_deref(),
            Some("owner@example.com")
        );
        // dots and +tags stay: the drive treats these as other accounts
        assert_eq!(
            normalize("o.w.ner+x@example.com").as_deref(),
            Some("o.w.ner+x@example.com")
        );
    }

    #[test]
    fn what_is_not_one_address_is_none() {
        for bad in [
            "",
            "   ",
            "owner",
            "owner@",
            "@example.com",
            "a@b@example.com",
            "a@b.example@example.com",
            "owner@example",
            "owner@example..com",
            "owner@.example.com",
            "owner@example.com.",
            "own er@example.com",
            "owner@exa\tmple.com",
            "owner@exämple.com",
            "owner\u{7f}@example.com",
        ] {
            assert_eq!(normalize(bad), None, "{bad:?}");
        }
        let long = format!("{}@example.com", "a".repeat(ADDRESS_MAX));
        assert_eq!(normalize(&long), None);
        let edge = format!("{}@example.com", "a".repeat(ADDRESS_MAX - 12));
        assert_eq!(edge.len(), ADDRESS_MAX);
        assert!(normalize(&edge).is_some());
    }

    #[test]
    fn the_tenants_own_owner_passes() {
        let g = judge(&["Owner@Example.com"], None, &tenant(), &[]);
        assert_eq!(g, Gate::Allowed);
        assert!(g.passes());
        assert_eq!(
            judge(
                &["owner@example.com"],
                Some(" STAFF@example.com"),
                &tenant(),
                &[]
            ),
            Gate::Allowed
        );
    }

    #[test]
    fn another_owner_is_refused() {
        let g = judge(&["someone@example.org"], None, &tenant(), &[]);
        assert_eq!(g, Gate::Refused(Why::OwnerNotRegistered));
        assert!(!g.passes());
        // a lookalike with a +tag or extra dots is another account
        assert_eq!(
            judge(&["owner+x@example.com"], None, &tenant(), &[]),
            Gate::Refused(Why::OwnerNotRegistered)
        );
        assert_eq!(
            judge(&["o.wner@example.com"], None, &tenant(), &[]),
            Gate::Refused(Why::OwnerNotRegistered)
        );
    }

    #[test]
    fn a_sharer_who_is_not_the_tenants_is_refused() {
        assert_eq!(
            judge(
                &["owner@example.com"],
                Some("someone@example.org"),
                &tenant(),
                &[]
            ),
            Gate::Refused(Why::SharerNotRegistered)
        );
        assert_eq!(
            judge(
                &["owner@example.com"],
                Some("not an address"),
                &tenant(),
                &[]
            ),
            Gate::Refused(Why::NotAnAddress)
        );
    }

    #[test]
    fn no_owner_or_nothing_registered_is_unknown_not_allowed() {
        let g = judge(&[], None, &tenant(), &[]);
        assert_eq!(g, Gate::Unknown(Why::NoOwner));
        assert!(!g.passes());
        let g = judge(&["owner@example.com"], None, &[], &[]);
        assert_eq!(g, Gate::Unknown(Why::NoneRegistered));
        assert!(!g.passes());
    }

    #[test]
    fn more_than_one_owner_or_a_bad_owner_is_refused() {
        assert_eq!(
            judge(
                &["owner@example.com", "staff@example.com"],
                None,
                &tenant(),
                &[]
            ),
            Gate::Refused(Why::ManyOwners)
        );
        assert_eq!(
            judge(&["owner"], None, &tenant(), &[]),
            Gate::Refused(Why::NotAnAddress)
        );
    }

    #[test]
    fn an_address_another_tenant_also_registers_is_refused() {
        let others = alloc::vec![String::from("staff@example.com")];
        // the owner is this tenant's, and also another's
        assert_eq!(
            judge(&["Staff@example.com"], None, &tenant(), &others),
            Gate::Refused(Why::AddressOfAnotherTenant)
        );
        // the sharer is also another's
        assert_eq!(
            judge(
                &["owner@example.com"],
                Some("staff@example.com"),
                &tenant(),
                &others
            ),
            Gate::Refused(Why::AddressOfAnotherTenant)
        );
        // other tenants registering other addresses change nothing
        assert_eq!(
            judge(&["owner@example.com"], None, &tenant(), &others),
            Gate::Allowed
        );
    }

    #[test]
    fn every_word_is_fixed_and_distinct() {
        let all = [
            Why::NoOwner,
            Why::NoneRegistered,
            Why::ManyOwners,
            Why::NotAnAddress,
            Why::OwnerNotRegistered,
            Why::SharerNotRegistered,
            Why::AddressOfAnotherTenant,
            Why::OtherRostersUnreadable,
        ];
        let words: Vec<&str> = all.iter().map(|w| w.as_str()).collect();
        for (i, w) in words.iter().enumerate() {
            assert!(w.starts_with("share_"));
            assert!(!words[i + 1..].contains(w));
        }
    }
}
