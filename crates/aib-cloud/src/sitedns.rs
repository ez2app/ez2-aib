// SPDX-License-Identifier: Apache-2.0
//! The DNS step of opening a site ([`crate::sitecreate::CreateStep::Dns`]): which
//! Cloudflare zone is the customer's, what is already under the site's names, and what
//! to add. No I/O: the host shell asks Cloudflare (through [`allowed`] only) and hands
//! the answers here.
//!
//! * **Whose zone.** The customer's roster names the Cloudflare accounts that are theirs
//!   (`cloudflare_accounts`, [`crate::roster::Roster`]). A zone is used only when it is
//!   active, its name is the site's name or a parent of it, and its own account id is one
//!   of those: every zone in the answer is checked, not only the first. No zone, a zone
//!   that is not active and a zone of any other account — ours, another customer's —
//!   all get the same word, [`ZONE_NOT_FOUND`], so the answer tells nobody which domains
//!   the key can see. A roster without the field cannot tell ([`OWNER_UNKNOWN`]).
//! * **What is there.** Under each of the site's names only address records count (A,
//!   AAAA, CNAME, [`crate::siteremove::DNS_ADDRESS_TYPES`]); mail and text records are the
//!   owner's and are never looked at nor touched. An address record without this site's
//!   mark ([`crate::siteremove::dns_marker`]) means the name already answers somewhere
//!   else: nothing is added over it ([`NAME_TAKEN`]). So does a wildcard address record
//!   above the name ([`WILDCARD_ANSWERS`]): it is what visitors reach today.
//! * **Done.** A record with this site's mark, pointing where [`Target`] says, proxied, is
//!   the step done: asked again, the step adds nothing (it can be carried on after any
//!   stop). A record with the mark that is anything else is not ours as written and
//!   stops ([`MARKED_DIFFERS`]).
//! * **Where to (E6 S5).** By default a site's records are A records to the host's own
//!   address ([`Target::Origin`]). The host may name one fixed entry name in its settings
//!   instead; then each record is a CNAME to that name ([`Target::Entry`]), so a lookup of
//!   the customer's name shows the entry name and no address of ours. The name is the
//!   host's setting, never written in this code. Whether a zone gets the CNAME is decided
//!   per zone ([`target_for`]): only when the host also names the entry's own account and
//!   the zone is in it, and no name of the site has a record that is not this site's;
//!   otherwise A records, with the word for why. A site's record already written as an A
//!   record reads back as done either way.
//!   **Not solved here (PLAN-e6 question 15):** a CNAME to a proxied name in another
//!   Cloudflare account is refused by Cloudflare (error 1014, cross-account). Such a zone
//!   gets A records ([`ENTRY_SKIPPED_OTHER_ACCOUNT`]); a CNAME for it would need
//!   Cloudflare for SaaS, which this code does not do.
//! * **Only seven calls.** [`allowed`] is the whole list of Cloudflare calls this code may
//!   make: list an account's zones by name, read a zone's SSL mode and its Always Use
//!   HTTPS, list a zone's records, add one record, proxied, carrying a site's mark (an A
//!   record to a public address, or, only through [`allowed_with`] given the host's entry
//!   name, a CNAME to exactly that name), make one record proxied with a site's mark
//!   ([`adopt`], E6 S12), and delete one record. Anything else is refused before it leaves the host. The list cannot tell one
//!   delete from another (ebb36 B5 L1), so a delete can only be written out as a
//!   [`MarkedDelete`], and the only way to get one is [`delete_of`]: a record just listed,
//!   an address record, carrying this site's mark. The key is the account's global key,
//!   so this list is the only thing between this code and the customer's zones.
//! * **Inside the zone.** Every name of the site must be the zone or a name under it
//!   ([`NAME_OUTSIDE_ZONE`]): Cloudflare would take any other name as a label under the
//!   zone and make a record nobody asked for (ebb36 B5 L2).
//! * **What is not looked at.** The SSL mode is the zone's; a rule that changes it for one
//!   host name is not seen here. A name delegated away by an NS record is not seen either:
//!   the health step finds it.

use alloc::string::{String, ToString};
use alloc::vec::Vec;

use serde_json::Value;

use crate::siteremove::{dns_marker, DNS_ADDRESS_TYPES, DNS_MARKER_PREFIX};

/// No zone for the name, a zone that is not active, or a zone of an account the
/// customer's roster does not name: one word for all of them.
pub const ZONE_NOT_FOUND: &str = "dns_zone_not_found";
/// The site has no customer, the customer has no roster, or the roster names no
/// Cloudflare account: whose a zone is cannot be told.
pub const OWNER_UNKNOWN: &str = "dns_owner_unknown";
/// Two active zones of the customer's accounts have the same name.
pub const ZONE_AMBIGUOUS: &str = "dns_zone_ambiguous";
/// Cloudflare refused to show an account the roster names: the customer took our
/// access away.
pub const ACCESS_REVOKED: &str = "dns_access_revoked";
/// A name of the site already has an address record that is not this site's.
pub const NAME_TAKEN: &str = "dns_name_taken";
/// A wildcard address record above a name of the site answers for it today.
pub const WILDCARD_ANSWERS: &str = "dns_wildcard_answers";
/// A name of the site is neither the zone nor a name under it.
pub const NAME_OUTSIDE_ZONE: &str = "dns_name_outside_zone";
/// A record with this site's mark is there and is not what this code writes.
pub const MARKED_DIFFERS: &str = "dns_marked_record_differs";
/// The zone's SSL mode would not reach the host over HTTPS (`off`, `flexible`).
pub const SSL_MODE_NOT_FULL: &str = "dns_ssl_mode_not_full";
/// Cloudflare's answer could not be read whole: could not tell.
pub const UNREADABLE: &str = "cloudflare_unreadable";
/// Cloudflare refused our own key.
pub const KEY_REFUSED: &str = "cloudflare_key_refused";
/// A call outside [`allowed`] was asked for; it was not made.
pub const CALL_NOT_ALLOWED: &str = "dns_call_not_allowed";
/// After the records were added they did not read back as written.
pub const NOT_CONFIRMED: &str = "dns_not_confirmed";
/// The host has no address of its own configured for the records to point at.
pub const ORIGIN_UNSET: &str = "dns_origin_unset";
/// The host's entry name is set but is not a domain name, or is one of the site's own
/// names (a CNAME to itself).
pub const ENTRY_UNSET: &str = "dns_entry_unset";

/// Where a site's records point (E6 S5).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Target<'a> {
    /// An A record to the host's own public address: the default.
    Origin(&'a str),
    /// A CNAME to the one entry name the host's settings give. `origin` is the host's
    /// address all the same: a record of the site already written as an A record to it
    /// reads back as done (c477a S5 L1), it is not rewritten.
    Entry { name: &'a str, origin: &'a str },
}

/// The host has an entry name but no account id for it (`CF_ENTRY_ACCOUNT`), or one that
/// is not an id: whether a zone is in the entry's account cannot be told, so A records.
pub const ENTRY_SKIPPED_NO_ACCOUNT: &str = "entry_skipped_no_account";
/// The zone is in another Cloudflare account than the entry name: a CNAME to it would be
/// refused by Cloudflare (error 1014), so A records.
pub const ENTRY_SKIPPED_OTHER_ACCOUNT: &str = "entry_skipped_other_account";
/// A name of the site already has a record that is not this site's (a TXT, an MX, any):
/// Cloudflare refuses a CNAME beside it, so A records (c477a S5 N1).
pub const ENTRY_SKIPPED_NAME_IN_USE: &str = "entry_skipped_name_in_use";

impl Target<'_> {
    /// The record type written.
    pub fn kind(&self) -> &'static str {
        match self {
            Target::Origin(_) => "A",
            Target::Entry { .. } => "CNAME",
        }
    }

    /// What the record holds.
    pub fn content(&self) -> &str {
        match self {
            Target::Origin(o) => o,
            Target::Entry { name, .. } => name,
        }
    }

    /// Whether a listed record points here, judged by the record's own type: an A record
    /// to the host's address, or (with an entry name) a CNAME to that name, no case and no
    /// final dot.
    pub fn is(&self, r: &Record) -> bool {
        match self {
            Target::Origin(o) => r.kind == "A" && r.content == *o,
            Target::Entry { name, origin } => {
                (r.kind == "CNAME" && same_name(&r.content, name))
                    || (r.kind == "A" && r.content == *origin)
            }
        }
    }
}

/// Where zone `zone`'s records for site `code` point, and why the entry name was passed
/// over when it was (E6 S5, c477a L1 / N1). Without an entry name: the host's address,
/// nothing to say. With one, a CNAME only when the host names the entry's own account
/// (`entry_account`), the zone is in that same account, and no name of the site has any
/// record that is not this site's (`records`, the zone's whole listing). Anything else is
/// the host's address and the word for why: never a stop, never a CNAME Cloudflare would
/// refuse or answer with 1014.
pub fn target_for<'a>(
    zone: &Zone,
    names: &[&str],
    records: &[Record],
    code: &str,
    origin: &'a str,
    entry: Option<&'a str>,
    entry_account: Option<&str>,
) -> (Target<'a>, Option<&'static str>) {
    let address = Target::Origin(origin);
    let Some(name) = entry else {
        return (address, None);
    };
    if !entry_account.is_some_and(valid_id) {
        return (address, Some(ENTRY_SKIPPED_NO_ACCOUNT));
    }
    if entry_account != Some(zone.account.as_str()) {
        return (address, Some(ENTRY_SKIPPED_OTHER_ACCOUNT));
    }
    let mark = dns_marker(code);
    let in_use = |r: &Record| r.comment != mark && names.iter().any(|n| same_name(&r.name, n));
    if records.iter().any(in_use) {
        return (address, Some(ENTRY_SKIPPED_NAME_IN_USE));
    }
    (Target::Entry { name, origin }, None)
}

/// The records listing is asked this many at a time.
pub const PER_PAGE: u32 = 50;

/// A Cloudflare id (account, zone, record): 32 lowercase hexadecimal characters.
pub fn valid_id(id: &str) -> bool {
    id.len() == 32
        && id
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

/// A dotted quad that is a public host address: not private, loopback, link local,
/// shared, multicast, reserved or a network's first address. The documentation ranges are
/// let through so tests need no real address.
pub fn valid_origin(ip: &str) -> bool {
    let parts: Vec<&str> = ip.split('.').collect();
    if parts.len() != 4 {
        return false;
    }
    let mut o = [0u8; 4];
    for (i, p) in parts.iter().enumerate() {
        if p.is_empty() || p.len() > 3 || !p.bytes().all(|c| c.is_ascii_digit()) {
            return false;
        }
        if p.len() > 1 && p.starts_with('0') {
            return false;
        }
        match p.parse::<u8>() {
            Ok(v) => o[i] = v,
            Err(_) => return false,
        }
    }
    let private = o[0] == 10
        || (o[0] == 172 && (16..=31).contains(&o[1]))
        || (o[0] == 192 && o[1] == 168)
        || (o[0] == 100 && (64..=127).contains(&o[1]))
        || o[0] == 127
        || (o[0] == 169 && o[1] == 254)
        || o[0] == 0
        || o[0] >= 224;
    !private && o[3] != 0
}

/// One call to Cloudflare. Built from checked values only; [`allowed`] checks the
/// written-out call again before it is made.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CfCall {
    /// The zones of one account that have this exact name.
    ListZones { account: String, name: String },
    /// A zone's SSL mode.
    SslMode { zone: String },
    /// One page of a zone's records.
    ListRecords { zone: String, page: u32 },
    /// Add one record (the body says which; it must carry a site's mark).
    AddRecord { zone: String },
    /// Delete one record: only ever one [`delete_of`] gave.
    Delete(MarkedDelete),
    /// A zone's Always Use HTTPS setting (read only, E6 S12).
    AlwaysHttps { zone: String },
    /// Make one listed record proxied and mark it as a site's: only ever one [`adopt`]
    /// gave (E6 S12).
    Edit(MarkedEdit),
}

/// A delete of one record that was listed with this site's mark. Its fields are private:
/// it is made by [`delete_of`] only.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MarkedDelete {
    zone: String,
    record: String,
}

/// The delete of `r`, listed in zone `zone`, for site `code`: only an address record
/// whose comment is exactly the site's mark (the rule of removing a site, #669), with ids
/// that are ids. Anything else gives nothing.
pub fn delete_of(zone: &str, r: &Record, code: &str) -> Option<MarkedDelete> {
    (valid_id(zone)
        && valid_id(&r.id)
        && crate::site::valid_code(code)
        && DNS_ADDRESS_TYPES.contains(&r.kind.as_str())
        && r.comment == dns_marker(code))
    .then(|| MarkedDelete {
        zone: String::from(zone),
        record: r.id.clone(),
    })
}

/// The records of site `code` under its `names` in a whole listing: address records
/// carrying the site's mark. Mail and text records and unmarked records are not in it.
pub fn marked_to_remove<'a>(names: &[&str], records: &'a [Record], code: &str) -> Vec<&'a Record> {
    let mark = dns_marker(code);
    records
        .iter()
        .filter(|r| DNS_ADDRESS_TYPES.contains(&r.kind.as_str()))
        .filter(|r| r.comment == mark)
        .filter(|r| names.iter().any(|n| same_name(&r.name, n)))
        .collect()
}

impl CfCall {
    /// The HTTP method.
    pub fn method(&self) -> &'static str {
        match self {
            CfCall::ListZones { .. } | CfCall::SslMode { .. } | CfCall::ListRecords { .. } => "GET",
            CfCall::AlwaysHttps { .. } => "GET",
            CfCall::AddRecord { .. } => "POST",
            CfCall::Delete(_) => "DELETE",
            CfCall::Edit(_) => "PATCH",
        }
    }

    /// The path under `/client/v4`, with its query.
    pub fn path(&self) -> String {
        match self {
            CfCall::ListZones { account, name } => {
                format!("/zones?account.id={account}&name={name}")
            }
            CfCall::SslMode { zone } => format!("/zones/{zone}/settings/ssl"),
            CfCall::ListRecords { zone, page } => {
                format!("/zones/{zone}/dns_records?per_page={PER_PAGE}&page={page}")
            }
            CfCall::AddRecord { zone } => format!("/zones/{zone}/dns_records"),
            CfCall::Delete(d) => format!("/zones/{}/dns_records/{}", d.zone, d.record),
            CfCall::AlwaysHttps { zone } => format!("/zones/{zone}/settings/always_use_https"),
            CfCall::Edit(e) => format!("/zones/{}/dns_records/{}", e.zone, e.record),
        }
    }
}

/// Whether a written-out call is one of the seven, exactly: the method, every part of the
/// path, the query in its one order, and for an add a body that is one A record to a
/// public address, proxied, carrying a site's mark. Everything else is refused. (Whose a
/// deleted record is cannot be seen here: that is [`delete_of`]'s.)
pub fn allowed(method: &str, path: &str, body: Option<&str>) -> bool {
    allowed_with(method, path, body, None)
}

/// [`allowed`], and for an add also a CNAME to exactly `entry`, the host's entry name,
/// when it gives one (E6 S5). A CNAME to any other name is refused all the same.
pub fn allowed_with(method: &str, path: &str, body: Option<&str>, entry: Option<&str>) -> bool {
    let (route, query) = match path.split_once('?') {
        Some((r, q)) => (r, Some(q)),
        None => (path, None),
    };
    let parts: Vec<&str> = route.split('/').collect();
    match (method, parts.as_slice(), query, body) {
        ("GET", ["", "zones"], Some(q), None) => {
            let Some((a, n)) = q.split_once('&') else {
                return false;
            };
            a.strip_prefix("account.id=").is_some_and(valid_id)
                && n.strip_prefix("name=")
                    .is_some_and(crate::edge::valid_domain)
        }
        ("GET", ["", "zones", z, "settings", "ssl"], None, None) => valid_id(z),
        ("GET", ["", "zones", z, "dns_records"], Some(q), None) => {
            let Some(page) = q
                .strip_prefix("per_page=")
                .and_then(|r| r.strip_prefix(format!("{PER_PAGE}").as_str()))
                .and_then(|r| r.strip_prefix("&page="))
            else {
                return false;
            };
            valid_id(z)
                && !page.is_empty()
                && page.len() <= 4
                && page.bytes().all(|c| c.is_ascii_digit())
                && !page.starts_with('0')
        }
        ("POST", ["", "zones", z, "dns_records"], None, Some(b)) => {
            valid_id(z) && add_body_ok(b, entry)
        }
        ("DELETE", ["", "zones", z, "dns_records", r], None, None) => valid_id(z) && valid_id(r),
        ("GET", ["", "zones", z, "settings", "always_use_https"], None, None) => valid_id(z),
        ("PATCH", ["", "zones", z, "dns_records", r], None, Some(b)) => {
            valid_id(z) && valid_id(r) && edit_body_ok(b)
        }
        _ => false,
    }
}

/// The body of an add: exactly `type`, `name`, `content`, `proxied`, `ttl`, `comment`; an
/// A record to a public address ([`valid_origin`]), or a CNAME to `entry` when the host
/// gives one; proxied; the comment one site's mark.
fn add_body_ok(body: &str, entry: Option<&str>) -> bool {
    let Ok(Value::Object(m)) = serde_json::from_str::<Value>(body) else {
        return false;
    };
    let keys = ["type", "name", "content", "proxied", "ttl", "comment"];
    let to_entry = |c: &str| entry.is_some_and(|e| crate::edge::valid_domain(e) && same_name(c, e));
    m.len() == keys.len()
        && keys.iter().all(|k| m.contains_key(*k))
        && m["name"].as_str().is_some_and(crate::edge::valid_domain)
        && match m["type"].as_str() {
            Some("A") => m["content"].as_str().is_some_and(valid_origin),
            Some("CNAME") => m["content"].as_str().is_some_and(to_entry),
            Some(_) | None => false,
        }
        && m["proxied"] == Value::Bool(true)
        && m["ttl"] == 1
        && m["comment"]
            .as_str()
            .and_then(|c| c.strip_prefix(DNS_MARKER_PREFIX))
            .is_some_and(crate::site::valid_code)
}

/// The longest record comment Cloudflare keeps on every plan.
pub const COMMENT_MAX: usize = 100;

/// The body of an adopting edit (E6 S12): exactly `proxied` and `comment`; proxied; the
/// comment starts with one site's mark, alone or followed by a space and the record's old
/// comment; no longer than [`COMMENT_MAX`], no control characters. The type, name and
/// content are never in it: an edit cannot point a record anywhere else.
fn edit_body_ok(body: &str) -> bool {
    let Ok(Value::Object(m)) = serde_json::from_str::<Value>(body) else {
        return false;
    };
    let comment_ok = |c: &str| {
        marked_by(c).is_some()
            && c.chars().count() <= COMMENT_MAX
            && !c.chars().any(char::is_control)
    };
    m.len() == 2
        && m.get("proxied") == Some(&Value::Bool(true))
        && m.get("comment")
            .and_then(Value::as_str)
            .is_some_and(comment_ok)
}

/// The site whose mark a comment starts with: `ez2:site:<code>`, alone or followed by a
/// space and anything else (an adopted record keeps its old comment after the mark).
pub fn marked_by(comment: &str) -> Option<&str> {
    let rest = comment.strip_prefix(DNS_MARKER_PREFIX)?;
    let code = rest.split(' ').next().unwrap_or(rest);
    crate::site::valid_code(code).then_some(code)
}

/// What follows the mark on an adopted record whose old comment was empty, so an adopted
/// record's comment is never the bare mark and `site remove` ([`delete_of`]) leaves it.
pub const ADOPTED_TAIL: &str = "adopted";

/// The comment an adopted record gets: the site's mark, then its old comment (control
/// characters made spaces) or [`ADOPTED_TAIL`] when there was none, cut to
/// [`COMMENT_MAX`] characters. Never the bare mark, so adopted records outlive the site.
/// A comment already carrying this site's mark is kept as it is (not marked twice).
pub fn adopted_comment(code: &str, old: &str) -> String {
    let old = old.trim();
    if marked_by(old) == Some(code) {
        return String::from(old);
    }
    let mark = dns_marker(code);
    let old: String = old
        .chars()
        .map(|c| if c.is_control() { ' ' } else { c })
        .collect();
    let tail = if old.is_empty() {
        ADOPTED_TAIL
    } else {
        old.as_str()
    };
    format!("{mark} {tail}").chars().take(COMMENT_MAX).collect()
}

/// An adopting edit of one listed record. Its fields are private: it is made by [`adopt`]
/// only.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MarkedEdit {
    zone: String,
    record: String,
    comment: String,
}

impl MarkedEdit {
    /// The record's id.
    pub fn record(&self) -> &str {
        &self.record
    }
    /// The comment the record gets.
    pub fn comment(&self) -> &str {
        &self.comment
    }
    /// The PATCH body: proxied, and the comment.
    pub fn body(&self) -> String {
        serde_json::json!({ "proxied": true, "comment": self.comment }).to_string()
    }
}

/// What adopting a site's DNS (E6 S12) does with one address record under its names.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Adopt {
    /// An A record to this host that is grey or unmarked: made proxied and marked.
    Edit(MarkedEdit),
    /// Already proxied with this site's mark: nothing to do (無變更).
    Unchanged,
    /// Points somewhere else — another address, an AAAA, a CNAME: not this host's, never
    /// touched.
    Elsewhere,
    /// Carries another site's mark: never touched.
    OtherSite,
    /// The zone's or the record's id is not an id: not touched.
    Refused,
}

/// Adopting site `code` (a site opened by hand, its records made before marks): every
/// address record listed exactly under one of its `names`, and what is done with it. Only
/// an A record whose content is `origin`, this host's own address, is ever edited; mail
/// and text records are not in the list at all, nor are wildcards.
pub fn adopt(
    zone: &str,
    names: &[&str],
    records: &[Record],
    origin: &str,
    code: &str,
) -> Vec<(Record, Adopt)> {
    records
        .iter()
        .filter(|r| DNS_ADDRESS_TYPES.contains(&r.kind.as_str()))
        .filter(|r| names.iter().any(|n| same_name(n, &r.name)))
        .map(|r| {
            let action = match marked_by(&r.comment) {
                Some(other) if other != code => Adopt::OtherSite,
                _ if r.kind != "A" || r.content != origin => Adopt::Elsewhere,
                Some(_) if r.proxied => Adopt::Unchanged,
                _ if !valid_id(zone) || !valid_id(&r.id) => Adopt::Refused,
                _ => Adopt::Edit(MarkedEdit {
                    zone: String::from(zone),
                    record: r.id.clone(),
                    comment: adopted_comment(code, &r.comment),
                }),
            };
            (r.clone(), action)
        })
        .collect()
}

/// The Edge proves its certificates only over TLS-ALPN for a name: behind the proxy
/// Cloudflare answers TLS itself, so the certificate could not be renewed.
pub const ACME_ALPN_ONLY: &str = "dns_acme_tls_alpn_only";
/// The Edge's configuration could not be read, so which challenge it uses is not known.
pub const ACME_UNKNOWN: &str = "dns_acme_unknown";

/// Caddy's challenges when nothing says otherwise.
const CADDY_DEFAULT_CHALLENGES: [&str; 2] = ["http-01", "tls-alpn-01"];

/// The ACME challenges the Edge's Caddy may use for `name`, from its merged (adapted,
/// JSON) configuration: the first automation policy whose subjects take the name, or that
/// has none; its issuers' challenges. No policy, or one without issuers, is Caddy's
/// default (HTTP-01 and TLS-ALPN-01). A DNS provider is DNS-01 only; the internal issuer
/// is no challenge. `None` when the configuration is not one or an issuer is not known.
pub fn acme_challenges(adapted: &Value, name: &str) -> Option<Vec<&'static str>> {
    adapted.get("apps")?.as_object()?;
    let Some(policies) = adapted.pointer("/apps/tls/automation/policies") else {
        return Some(CADDY_DEFAULT_CHALLENGES.to_vec());
    };
    let takes = |p: &&Value| match p.get("subjects") {
        None => true,
        Some(s) => s.as_array().is_some_and(|s| {
            s.iter()
                .filter_map(Value::as_str)
                .any(|s| same_name(s, name) || wildcard_covers(s, name))
        }),
    };
    let Some(policy) = policies.as_array()?.iter().find(takes) else {
        return Some(CADDY_DEFAULT_CHALLENGES.to_vec());
    };
    let Some(issuers) = policy.get("issuers") else {
        return Some(CADDY_DEFAULT_CHALLENGES.to_vec());
    };
    let mut found = Vec::new();
    for issuer in issuers.as_array()? {
        match issuer.get("module").and_then(Value::as_str)? {
            "acme" | "zerossl" => {
                let ch = issuer.get("challenges");
                let off = |k: &str| {
                    ch.and_then(|c| c.get(k))
                        .and_then(|c| c.get("disabled"))
                        .and_then(Value::as_bool)
                        .unwrap_or(false)
                };
                if ch.and_then(|c| c.get("dns")).is_some() {
                    found.push("dns-01");
                    continue;
                }
                if !off("http") {
                    found.push("http-01");
                }
                if !off("tls-alpn") {
                    found.push("tls-alpn-01");
                }
            }
            "internal" => {}
            _ => return None,
        }
    }
    found.sort_unstable();
    found.dedup();
    Some(found)
}

/// Whether a site's DNS may be adopted (put behind the proxy): the zone's SSL mode is
/// `full` or `strict` ([`ssl_mode`]), and for every name the Edge's challenges are known
/// and not TLS-ALPN alone. The first reason it may not, otherwise.
pub fn adopt_gate(
    ssl: Result<(), &'static str>,
    acme: &[Option<Vec<&'static str>>],
) -> Result<(), &'static str> {
    ssl?;
    for a in acme {
        match a.as_deref() {
            None => return Err(ACME_UNKNOWN),
            Some(["tls-alpn-01"]) => return Err(ACME_ALPN_ONLY),
            Some(_) => {}
        }
    }
    Ok(())
}

/// The names a zone of `domain` may have: the domain itself and each parent of it that
/// still has two labels, longest first.
pub fn zone_candidates(domain: &str) -> Vec<String> {
    let labels: Vec<&str> = domain.split('.').collect();
    (0..labels.len().saturating_sub(1))
        .map(|i| labels[i..].join("."))
        .collect()
}

/// One zone as the listing shows it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Zone {
    pub id: String,
    pub name: String,
    pub status: String,
    pub account: String,
}

/// The zones of one listing answer, or `None` when the answer is not a successful
/// listing whose every entry has an id, a name, a status and an account id.
pub fn zones_from(answer: &Value) -> Option<Vec<Zone>> {
    if answer.get("success") != Some(&Value::Bool(true)) {
        return None;
    }
    let mut out = Vec::new();
    for z in answer.get("result")?.as_array()? {
        let text = |k: &str| z.get(k).and_then(Value::as_str).map(String::from);
        out.push(Zone {
            id: text("id").filter(|i| valid_id(i))?,
            name: text("name")?,
            status: text("status")?,
            account: z
                .get("account")?
                .get("id")?
                .as_str()
                .filter(|i| valid_id(i))
                .map(String::from)?,
        });
    }
    Some(out)
}

/// The zone of `domain` among every zone the customer's accounts showed. `accounts` are
/// the roster's; `None` is "the roster does not say". `zones` is `None` when a listing
/// could not be read.
pub fn pick_zone(
    domain: &str,
    accounts: Option<&[String]>,
    zones: Option<&[Zone]>,
) -> Result<Zone, &'static str> {
    let accounts = accounts.filter(|a| !a.is_empty()).ok_or(OWNER_UNKNOWN)?;
    let zones = zones.ok_or(UNREADABLE)?;
    // the longest name first: a zone of the subdomain itself wins over the parent's
    for name in zone_candidates(domain) {
        let found: Vec<&Zone> = zones
            .iter()
            .filter(|z| {
                same_name(&z.name, &name) && z.status == "active" && accounts.contains(&z.account)
            })
            .collect();
        match found.as_slice() {
            [] => continue,
            [one] => return Ok((*one).clone()),
            _ => return Err(ZONE_AMBIGUOUS),
        }
    }
    Err(ZONE_NOT_FOUND)
}

/// What the zone's SSL mode means for a proxied site: `full` and `strict` reach the host
/// over HTTPS; `off` and `flexible` do not (the entry sends plain HTTP to HTTPS, so
/// `flexible` loops). The mode is the customer's and is not changed here.
pub fn ssl_mode(answer: &Value) -> Result<(), &'static str> {
    if answer.get("success") != Some(&Value::Bool(true)) {
        return Err(UNREADABLE);
    }
    match answer
        .get("result")
        .and_then(|r| r.get("value"))
        .and_then(Value::as_str)
    {
        Some("full" | "strict") => Ok(()),
        Some("off" | "flexible") => Err(SSL_MODE_NOT_FULL),
        _ => Err(UNREADABLE),
    }
}

/// One DNS record as the listing shows it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Record {
    pub id: String,
    pub kind: String,
    pub name: String,
    pub content: String,
    pub proxied: bool,
    pub comment: String,
}

/// One page of a records listing: its records, and the listing's page count and total.
pub struct Page {
    pub records: Vec<Record>,
    pub total_pages: u32,
    pub total_count: u32,
}

/// One page of a records listing, or `None` when it is not a successful answer whose
/// every record has an id, a type, a name and content, and that says how many pages and
/// records there are.
pub fn records_page(answer: &Value) -> Option<Page> {
    if answer.get("success") != Some(&Value::Bool(true)) {
        return None;
    }
    let info = answer.get("result_info")?;
    let num = |k: &str| {
        info.get(k)
            .and_then(Value::as_u64)
            .and_then(|n| u32::try_from(n).ok())
    };
    let mut records = Vec::new();
    for r in answer.get("result")?.as_array()? {
        let text = |k: &str| r.get(k).and_then(Value::as_str).map(String::from);
        records.push(Record {
            id: text("id").filter(|i| valid_id(i))?,
            kind: text("type")?,
            name: text("name")?,
            content: text("content")?,
            proxied: r.get("proxied").and_then(Value::as_bool).unwrap_or(false),
            // a record without a comment has none; one of another type is not read
            comment: match r.get("comment") {
                None | Some(Value::Null) => String::new(),
                Some(Value::String(c)) => c.clone(),
                Some(_) => return None,
            },
        });
    }
    Some(Page {
        records,
        total_pages: num("total_pages")?,
        total_count: num("total_count")?,
    })
}

/// The pages of one listing put together: `None` when any page is missing, the pages do
/// not agree on their count, or the records are not as many as the listing says.
pub fn whole_listing(pages: &[Page]) -> Option<Vec<Record>> {
    let first = pages.first()?;
    let pages_n = usize::try_from(first.total_pages.max(1)).ok()?;
    if pages.len() != pages_n
        || pages
            .iter()
            .any(|p| p.total_pages != first.total_pages || p.total_count != first.total_count)
    {
        return None;
    }
    let all: Vec<Record> = pages.iter().flat_map(|p| p.records.clone()).collect();
    (all.len() == usize::try_from(first.total_count).ok()?).then_some(all)
}

/// One record to add.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NewRecord {
    /// `A` or `CNAME` ([`Target::kind`]).
    pub kind: &'static str,
    pub name: String,
    pub content: String,
    pub comment: String,
}

impl NewRecord {
    /// The body of the add call: an A record to the host or a CNAME to the entry name,
    /// proxied (`ttl` 1 is Cloudflare's "automatic", the only one a proxied record has),
    /// the site's mark as its comment.
    pub fn body(&self) -> String {
        serde_json::json!({
            "type": self.kind,
            "name": self.name,
            "content": self.content,
            "proxied": true,
            "ttl": 1,
            "comment": self.comment,
        })
        .to_string()
    }
}

/// What the DNS step does.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DnsPlan {
    /// Every name has this site's record as written: nothing to do.
    Done,
    /// Add these, then read the zone again.
    Add(Vec<NewRecord>),
    /// Do nothing and say this word.
    Stop(&'static str),
}

fn same_name(a: &str, b: &str) -> bool {
    let a = a.strip_suffix('.').unwrap_or(a);
    let b = b.strip_suffix('.').unwrap_or(b);
    !a.is_empty() && a.eq_ignore_ascii_case(b)
}

/// Whether `wild` (`*.<parent>`) answers for `name`: the parent is a proper suffix of it.
fn wildcard_covers(wild: &str, name: &str) -> bool {
    let Some(parent) = wild.strip_prefix("*.") else {
        return false;
    };
    let name = name.strip_suffix('.').unwrap_or(name).to_ascii_lowercase();
    let parent = parent
        .strip_suffix('.')
        .unwrap_or(parent)
        .to_ascii_lowercase();
    name.len() > parent.len() + 1
        && name.ends_with(&parent)
        && name.as_bytes()[name.len() - parent.len() - 1] == b'.'
}

/// Whether `name` is `zone` or a name under it (no case, no final dot).
pub fn in_zone(name: &str, zone: &str) -> bool {
    let name = name.strip_suffix('.').unwrap_or(name).to_ascii_lowercase();
    let zone = zone.strip_suffix('.').unwrap_or(zone).to_ascii_lowercase();
    !zone.is_empty()
        && (name == zone
            || (name.len() > zone.len() + 1
                && name.ends_with(&zone)
                && name.as_bytes()[name.len() - zone.len() - 1] == b'.'))
}

/// The DNS step for site `code` whose names are `names`, in zone `zone`, pointing at
/// `origin`. `records` is the zone's whole listing ([`whole_listing`]), `None` when it
/// was not complete.
pub fn plan(
    zone: &str,
    names: &[&str],
    records: Option<&[Record]>,
    code: &str,
    origin: &str,
) -> DnsPlan {
    plan_to(zone, names, records, code, Target::Origin(origin))
}

/// [`plan`], pointing where `target` says (E6 S5): the host's address, or the entry name
/// of its settings.
pub fn plan_to(
    zone: &str,
    names: &[&str],
    records: Option<&[Record]>,
    code: &str,
    target: Target<'_>,
) -> DnsPlan {
    let origin = match target {
        Target::Origin(o) | Target::Entry { origin: o, .. } => o,
    };
    if !valid_origin(origin) {
        return DnsPlan::Stop(ORIGIN_UNSET);
    }
    if let Target::Entry { name, .. } = target {
        if !crate::edge::valid_domain(name) || names.iter().any(|n| same_name(n, name)) {
            return DnsPlan::Stop(ENTRY_UNSET);
        }
    }
    let Some(records) = records else {
        return DnsPlan::Stop(UNREADABLE);
    };
    if names.is_empty() || !crate::site::valid_code(code) {
        return DnsPlan::Stop(UNREADABLE);
    }
    if !names.iter().all(|n| in_zone(n, zone)) {
        return DnsPlan::Stop(NAME_OUTSIDE_ZONE);
    }
    let mark = dns_marker(code);
    let address = |r: &&Record| DNS_ADDRESS_TYPES.contains(&r.kind.as_str());
    let mut add = Vec::new();
    for name in names {
        let here: Vec<&Record> = records
            .iter()
            .filter(address)
            .filter(|r| same_name(&r.name, name))
            .collect();
        if here.iter().any(|r| r.comment != mark) {
            return DnsPlan::Stop(NAME_TAKEN);
        }
        match here.as_slice() {
            [] => {}
            [ours] if target.is(ours) && ours.proxied => continue,
            _ => return DnsPlan::Stop(MARKED_DIFFERS),
        }
        if records
            .iter()
            .filter(address)
            .any(|r| wildcard_covers(&r.name, name))
        {
            return DnsPlan::Stop(WILDCARD_ANSWERS);
        }
        add.push(NewRecord {
            kind: target.kind(),
            name: String::from(*name),
            content: String::from(target.content()),
            comment: mark.clone(),
        });
    }
    if add.is_empty() {
        DnsPlan::Done
    } else {
        DnsPlan::Add(add)
    }
}

/// A name of the site with no address record of its own and no wildcard that answers for
/// it: nothing to judge, so not a pass.
pub const NO_ADDRESS: &str = "dns_no_address_record";
/// The site's names are not all in the zone of its first name (a second domain on another
/// zone): S15 reads one zone per site, so such a site is never judged, only unchecked.
pub const MULTI_ZONE: &str = "multi_zone";

/// What `site verify` S15 (E6 D1) finds of a site's names at Cloudflare.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Proxied {
    /// Every name answers, and every address record that answers for it goes through the
    /// proxy.
    All,
    /// These names answer with at least one record that is "DNS only": anyone who looks
    /// the name up gets the host's own address. Names only, never what the record holds.
    Grey(Vec<String>),
    /// Could not tell. Never a pass.
    Unchecked(&'static str),
}

/// S15 for the site's `names` against the zone's whole listing ([`whole_listing`], `None`
/// when it was not complete). Every address record (A, AAAA, CNAME) under a name must be
/// proxied; a name without one of its own is answered by a wildcard over it, if any, and
/// that wildcard is judged instead. That is wider than DNS (a name with only mail or text
/// records gets no answer from the wildcard), so it can only report more grey, never less.
/// A grey record anywhere is a fail, whatever else is unknown.
pub fn proxied(names: &[&str], records: Option<&[Record]>) -> Proxied {
    let Some(records) = records else {
        return Proxied::Unchecked(UNREADABLE);
    };
    let is_address = |r: &&Record| DNS_ADDRESS_TYPES.contains(&r.kind.as_str());
    let mut grey = Vec::new();
    let mut unanswered = names.is_empty();
    for name in names {
        let mut here: Vec<&Record> = records
            .iter()
            .filter(is_address)
            .filter(|r| same_name(&r.name, name))
            .collect();
        if here.is_empty() {
            here = records
                .iter()
                .filter(is_address)
                .filter(|r| wildcard_covers(&r.name, name))
                .collect();
        }
        if here.is_empty() {
            unanswered = true;
        } else if here.iter().any(|r| !r.proxied) {
            grey.push(String::from(*name));
        }
    }
    if !grey.is_empty() {
        Proxied::Grey(grey)
    } else if unanswered {
        Proxied::Unchecked(NO_ADDRESS)
    } else {
        Proxied::All
    }
}

/// The word for a Cloudflare answer that was not a success: our key refused (401),
/// access to the customer's account taken away (403 on an account's listing), or could
/// not tell.
pub fn failure_word(http: u16, listing_an_account: bool) -> &'static str {
    match http {
        401 => KEY_REFUSED,
        403 if listing_an_account => ACCESS_REVOKED,
        _ => UNREADABLE,
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use serde_json::json;

    const ACC: &str = "0123456789abcdef0123456789abcdef";
    const OTHER: &str = "fedcba9876543210fedcba9876543210";
    const ZID: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const RID: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    const ORIGIN: &str = "192.0.2.10";

    fn zone(name: &str, status: &str, account: &str) -> Zone {
        Zone {
            id: String::from(ZID),
            name: String::from(name),
            status: String::from(status),
            account: String::from(account),
        }
    }

    fn rec(kind: &str, name: &str, content: &str, proxied: bool, comment: &str) -> Record {
        Record {
            id: String::from(RID),
            kind: String::from(kind),
            name: String::from(name),
            content: String::from(content),
            proxied,
            comment: String::from(comment),
        }
    }

    fn accounts() -> Vec<String> {
        vec![String::from(ACC)]
    }

    #[test]
    fn only_an_active_zone_of_the_customers_account_is_used() {
        let a = accounts();
        let z = [zone("example.com", "active", ACC)];
        assert_eq!(
            pick_zone("example.com", Some(&a[..]), Some(&z[..])).map(|z| z.name),
            Ok(String::from("example.com"))
        );
        // a subdomain's site uses the parent zone
        assert_eq!(
            pick_zone("shop.example.com", Some(&a[..]), Some(&z[..])).map(|z| z.name),
            Ok(String::from("example.com"))
        );
        // another account's zone, an inactive zone, no zone: the same word
        for zs in [
            vec![zone("example.com", "active", OTHER)],
            vec![zone("example.com", "pending", ACC)],
            vec![zone("example.com", "moved", ACC)],
            vec![zone("example.org", "active", ACC)],
            vec![zone("ample.com", "active", ACC)],
            vec![],
        ] {
            assert_eq!(
                pick_zone("example.com", Some(&a[..]), Some(&zs[..])),
                Err(ZONE_NOT_FOUND),
                "{zs:?}"
            );
        }
    }

    #[test]
    fn every_zone_in_the_answer_has_its_account_checked() {
        // the other account's zone comes first: it is not taken, the customer's is
        let a = accounts();
        let mut theirs = zone("example.com", "active", ACC);
        theirs.id = String::from(OTHER);
        let z = [zone("example.com", "active", OTHER), theirs];
        assert_eq!(
            pick_zone("example.com", Some(&a[..]), Some(&z[..])).map(|z| z.id),
            Ok(String::from(OTHER))
        );
    }

    #[test]
    fn of_two_zones_with_one_name_only_the_active_one_counts() {
        let a = accounts();
        let z = [
            zone("example.com", "pending", ACC),
            zone("example.com", "active", ACC),
        ];
        assert_eq!(
            pick_zone("example.com", Some(&a[..]), Some(&z[..])).map(|z| z.status),
            Ok(String::from("active"))
        );
        let two = [
            zone("example.com", "active", ACC),
            zone("example.com", "active", ACC),
        ];
        assert_eq!(
            pick_zone("example.com", Some(&a[..]), Some(&two[..])),
            Err(ZONE_AMBIGUOUS)
        );
    }

    #[test]
    fn a_roster_without_accounts_cannot_tell() {
        let z = [zone("example.com", "active", ACC)];
        assert_eq!(
            pick_zone("example.com", None, Some(&z[..])),
            Err(OWNER_UNKNOWN)
        );
        assert_eq!(
            pick_zone("example.com", Some(&[][..]), Some(&z[..])),
            Err(OWNER_UNKNOWN)
        );
        assert_eq!(
            pick_zone("example.com", Some(&accounts()[..]), None),
            Err(UNREADABLE)
        );
    }

    #[test]
    fn the_subdomains_own_zone_wins() {
        let a = accounts();
        let mut sub = zone("shop.example.com", "active", ACC);
        sub.id = String::from(OTHER);
        let z = [zone("example.com", "active", ACC), sub];
        assert_eq!(
            pick_zone("shop.example.com", Some(&a[..]), Some(&z[..])).map(|z| z.id),
            Ok(String::from(OTHER))
        );
        assert_eq!(
            zone_candidates("a.shop.example.com"),
            ["a.shop.example.com", "shop.example.com", "example.com"]
        );
    }

    #[test]
    fn a_zone_listing_is_read_whole_or_not_at_all() {
        let ok = json!({"success": true, "result": [
            {"id": ZID, "name": "example.com", "status": "active", "account": {"id": ACC}}]});
        assert_eq!(
            zones_from(&ok),
            Some(vec![zone("example.com", "active", ACC)])
        );
        let no_account = json!({"success": true, "result": [
            {"id": ZID, "name": "example.com", "status": "active"}]});
        assert_eq!(zones_from(&no_account), None);
        let bad_id = json!({"success": true, "result": [
            {"id": "x", "name": "example.com", "status": "active", "account": {"id": ACC}}]});
        assert_eq!(zones_from(&bad_id), None);
        assert_eq!(zones_from(&json!({"success": false, "result": []})), None);
        assert_eq!(zones_from(&json!({"success": true})), None);
    }

    #[test]
    fn a_name_answered_elsewhere_is_left_alone() {
        for kind in ["A", "AAAA", "CNAME"] {
            let r = [rec(kind, "example.com", "198.51.100.7", false, "")];
            assert_eq!(
                plan(
                    "example.com",
                    &["example.com"],
                    Some(&r[..]),
                    "s-abc123",
                    ORIGIN
                ),
                DnsPlan::Stop(NAME_TAKEN),
                "{kind}"
            );
        }
        // another site's mark is not this site's
        let r = [rec("A", "example.com", ORIGIN, true, "ez2:site:s-other1")];
        assert_eq!(
            plan(
                "example.com",
                &["example.com"],
                Some(&r[..]),
                "s-abc123",
                ORIGIN
            ),
            DnsPlan::Stop(NAME_TAKEN)
        );
        // ours beside one that is not: still taken
        let r = [
            rec("A", "example.com", ORIGIN, true, "ez2:site:s-abc123"),
            rec("AAAA", "example.com", "2001:db8::1", false, ""),
        ];
        assert_eq!(
            plan(
                "example.com",
                &["example.com"],
                Some(&r[..]),
                "s-abc123",
                ORIGIN
            ),
            DnsPlan::Stop(NAME_TAKEN)
        );
    }

    #[test]
    fn mail_and_text_records_are_not_looked_at() {
        let r = [
            rec("MX", "example.com", "mail.example.com", false, ""),
            rec("TXT", "example.com", "v=spf1 -all", false, ""),
            rec("A", "mail.example.com", "198.51.100.7", false, ""),
        ];
        assert_eq!(
            plan(
                "example.com",
                &["example.com"],
                Some(&r[..]),
                "s-abc123",
                ORIGIN
            ),
            DnsPlan::Add(vec![NewRecord {
                kind: "A",
                name: String::from("example.com"),
                content: String::from(ORIGIN),
                comment: String::from("ez2:site:s-abc123"),
            }])
        );
    }

    #[test]
    fn this_sites_record_is_the_step_done() {
        let r = [rec("A", "Example.com.", ORIGIN, true, "ez2:site:s-abc123")];
        assert_eq!(
            plan(
                "example.com",
                &["example.com"],
                Some(&r[..]),
                "s-abc123",
                ORIGIN
            ),
            DnsPlan::Done
        );
        // the mark on anything else is not ours as written
        for other in [
            rec("A", "example.com", ORIGIN, false, "ez2:site:s-abc123"),
            rec(
                "A",
                "example.com",
                "198.51.100.7",
                true,
                "ez2:site:s-abc123",
            ),
            rec(
                "CNAME",
                "example.com",
                "x.example.net",
                true,
                "ez2:site:s-abc123",
            ),
        ] {
            assert_eq!(
                plan(
                    "example.com",
                    &["example.com"],
                    Some(&[other.clone()][..]),
                    "s-abc123",
                    ORIGIN
                ),
                DnsPlan::Stop(MARKED_DIFFERS),
                "{other:?}"
            );
        }
        let two = [
            rec("A", "example.com", ORIGIN, true, "ez2:site:s-abc123"),
            rec("A", "example.com", ORIGIN, true, "ez2:site:s-abc123"),
        ];
        assert_eq!(
            plan(
                "example.com",
                &["example.com"],
                Some(&two[..]),
                "s-abc123",
                ORIGIN
            ),
            DnsPlan::Stop(MARKED_DIFFERS)
        );
    }

    #[test]
    fn only_a_listed_record_with_this_sites_mark_can_be_deleted() {
        let ours = rec("A", "example.com", ORIGIN, true, "ez2:site:s-abc123");
        let d = delete_of(ZID, &ours, "s-abc123").unwrap();
        let call = CfCall::Delete(d);
        assert_eq!(call.method(), "DELETE");
        assert_eq!(call.path(), format!("/zones/{ZID}/dns_records/{RID}"));
        assert!(allowed(call.method(), &call.path(), None));
        for (r, code) in [
            (rec("A", "example.com", ORIGIN, true, ""), "s-abc123"),
            (
                rec("A", "example.com", ORIGIN, true, "ez2:site:s-other1"),
                "s-abc123",
            ),
            (
                rec("A", "example.com", ORIGIN, true, "ez2:site:s-abc123 "),
                "s-abc123",
            ),
            (
                rec(
                    "MX",
                    "example.com",
                    "mail.example.com",
                    false,
                    "ez2:site:s-abc123",
                ),
                "s-abc123",
            ),
            (
                rec("TXT", "example.com", "x", false, "ez2:site:s-abc123"),
                "s-abc123",
            ),
        ] {
            assert_eq!(delete_of(ZID, &r, code), None, "{r:?}");
        }
        let mut bad_id = ours.clone();
        bad_id.id = String::from("../x");
        assert_eq!(delete_of(ZID, &bad_id, "s-abc123"), None);
        assert_eq!(delete_of("zone", &ours, "s-abc123"), None);
    }

    #[test]
    fn only_marked_address_records_under_the_names_are_removed() {
        let records = [
            rec("A", "example.com", ORIGIN, true, "ez2:site:s-abc123"),
            rec(
                "AAAA",
                "www.example.com",
                "2001:db8::1",
                true,
                "ez2:site:s-abc123",
            ),
            rec("A", "other.example.com", ORIGIN, true, "ez2:site:s-abc123"),
            rec("A", "example.com", "198.51.100.7", false, ""),
            rec(
                "MX",
                "example.com",
                "mail.example.com",
                false,
                "ez2:site:s-abc123",
            ),
            rec("A", "example.com", ORIGIN, true, "ez2:site:s-other1"),
        ];
        let got = marked_to_remove(&["example.com", "WWW.example.com."], &records, "s-abc123");
        assert_eq!(got.len(), 2);
        assert_eq!(got[0].name, "example.com");
        assert_eq!(got[1].name, "www.example.com");
    }

    #[test]
    fn a_name_outside_the_zone_adds_nothing() {
        // Cloudflare would make other.example.net.example.com of it (ebb36 B5 L2)
        for names in [
            &["other.example.net"][..],
            &["example.com", "other.example.net"][..],
            &["badexample.com"][..],
            &["com"][..],
        ] {
            assert_eq!(
                plan("example.com", names, Some(&[][..]), "s-abc123", ORIGIN),
                DnsPlan::Stop(NAME_OUTSIDE_ZONE),
                "{names:?}"
            );
        }
        assert!(matches!(
            plan(
                "example.com",
                &["Shop.Example.com.", "example.com"],
                Some(&[][..]),
                "s-abc123",
                ORIGIN
            ),
            DnsPlan::Add(_)
        ));
        assert!(in_zone("a.b.example.com", "example.com."));
        assert!(!in_zone("example.com", ""));
    }

    #[test]
    fn a_wildcard_that_answers_today_stops() {
        let r = [rec("CNAME", "*.example.com", "x.example.net", true, "")];
        assert_eq!(
            plan(
                "example.com",
                &["shop.example.com"],
                Some(&r[..]),
                "s-abc123",
                ORIGIN
            ),
            DnsPlan::Stop(WILDCARD_ANSWERS)
        );
        // a wildcard does not answer for the zone's own name
        assert!(matches!(
            plan(
                "example.com",
                &["example.com"],
                Some(&r[..]),
                "s-abc123",
                ORIGIN
            ),
            DnsPlan::Add(_)
        ));
        assert!(!wildcard_covers("*.example.com", "badexample.com"));
        assert!(wildcard_covers("*.example.com", "a.b.example.com"));
    }

    #[test]
    fn an_unreadable_listing_or_no_origin_adds_nothing() {
        assert_eq!(
            plan("example.com", &["example.com"], None, "s-abc123", ORIGIN),
            DnsPlan::Stop(UNREADABLE)
        );
        for o in [
            "",
            "10.0.0.1",
            "192.168.1.2",
            "127.0.0.1",
            "100.64.0.1",
            "1.2.3",
            "192.0.2.010",
            "192.0.2.0",
        ] {
            assert_eq!(
                plan(
                    "example.com",
                    &["example.com"],
                    Some(&[][..]),
                    "s-abc123",
                    o
                ),
                DnsPlan::Stop(ORIGIN_UNSET),
                "{o}"
            );
        }
        assert!(valid_origin("198.51.100.7"));
    }

    #[test]
    fn a_listing_is_whole_only_when_its_pages_and_count_agree() {
        let page = |n: usize, pages: u32, total: u32| Page {
            records: (0..n)
                .map(|_| rec("A", "x.example.com", ORIGIN, false, ""))
                .collect(),
            total_pages: pages,
            total_count: total,
        };
        assert_eq!(whole_listing(&[page(2, 1, 2)]).map(|r| r.len()), Some(2));
        assert_eq!(whole_listing(&[page(0, 0, 0)]).map(|r| r.len()), Some(0));
        assert_eq!(
            whole_listing(&[page(50, 2, 51), page(1, 2, 51)]).map(|r| r.len()),
            Some(51)
        );
        // a page missing, a count that does not add up, pages that disagree
        assert_eq!(whole_listing(&[page(50, 2, 51)]), None);
        assert_eq!(whole_listing(&[page(2, 1, 3)]), None);
        assert_eq!(whole_listing(&[page(50, 2, 51), page(1, 3, 51)]), None);
        assert_eq!(whole_listing(&[]), None);
        let ans = json!({"success": true, "result": [
            {"id": RID, "type": "A", "name": "example.com", "content": ORIGIN, "proxied": true, "comment": null}],
            "result_info": {"total_pages": 1, "total_count": 1}});
        let p = records_page(&ans).unwrap();
        assert_eq!(p.records[0].comment, "");
        assert!(p.records[0].proxied);
        let no_info = json!({"success": true, "result": []});
        assert!(records_page(&no_info).is_none());
    }

    #[test]
    fn only_the_five_calls_are_allowed() {
        let zone = String::from(ZID);
        let body = NewRecord {
            kind: "A",
            name: String::from("example.com"),
            content: String::from(ORIGIN),
            comment: dns_marker("s-abc123"),
        }
        .body();
        let calls = [
            CfCall::ListZones {
                account: String::from(ACC),
                name: String::from("example.com"),
            },
            CfCall::SslMode { zone: zone.clone() },
            CfCall::ListRecords {
                zone: zone.clone(),
                page: 2,
            },
        ];
        for c in &calls {
            assert!(allowed(c.method(), &c.path(), None), "{c:?}");
        }
        let add = CfCall::AddRecord { zone: zone.clone() };
        assert!(allowed(add.method(), &add.path(), Some(body.as_str())));
        let refused = [
            ("GET", "/zones".to_string(), None),
            (
                "GET",
                format!("/zones?account.id={ACC}&name=example.com&per_page=1"),
                None,
            ),
            (
                "GET",
                format!("/zones?name=example.com&account.id={ACC}"),
                None,
            ),
            (
                "GET",
                format!("/zones?account.id={ACC}&name=example.com%26x"),
                None,
            ),
            (
                "PATCH",
                format!("/zones/{ZID}/settings/always_use_https"),
                None,
            ),
            ("PATCH", format!("/zones/{ZID}/settings/ssl"), None),
            (
                "GET",
                format!("/zones/{ZID}/dns_records?per_page=50&page=0"),
                None,
            ),
            (
                "GET",
                format!("/zones/{ZID}/dns_records?per_page=5000&page=1"),
                None,
            ),
            ("GET", format!("/zones/{ZID}/dns_records"), None),
            ("PUT", format!("/zones/{ZID}/dns_records/{RID}"), None),
            ("GET", format!("/zones/{ZID}/../../user/tokens"), None),
            ("GET", "/user/tokens".to_string(), None),
            ("GET", format!("/accounts/{ACC}/members"), None),
            ("DELETE", format!("/zones/{ZID}"), None),
            ("DELETE", format!("/zones/{ZID}/dns_records/{RID}/x"), None),
            ("DELETE", format!("/zones/{ZID}/dns_records/x"), None),
            (
                "DELETE",
                format!("/zones/{ZID}/dns_records/{RID}"),
                Some(body.as_str()),
            ),
            (
                "GET",
                format!("/zones/{ZID}/settings/ssl"),
                Some(body.as_str()),
            ),
        ];
        for (m, p, b) in &refused {
            assert!(!allowed(m, p, *b), "{m} {p}");
        }
    }

    #[test]
    fn an_add_must_carry_a_sites_mark_and_be_proxied() {
        let p = format!("/zones/{ZID}/dns_records");
        let ok = json!({"type": "A", "name": "example.com", "content": ORIGIN, "proxied": true, "ttl": 1, "comment": "ez2:site:s-abc123"});
        assert!(allowed("POST", &p, Some(ok.to_string().as_str())));
        let mut no_mark = ok.clone();
        no_mark["comment"] = json!("hello");
        let mut not_proxied = ok.clone();
        not_proxied["proxied"] = json!(false);
        let mut mx = ok.clone();
        mx["type"] = json!("MX");
        let mut extra = ok.clone();
        extra["priority"] = json!(10);
        let mut bad_code = ok.clone();
        bad_code["comment"] = json!("ez2:site:");
        let mut ttl = ok.clone();
        ttl["ttl"] = json!(300);
        // only an A record to a public address (ebb36 B5 L1)
        let mut cname = ok.clone();
        cname["type"] = json!("CNAME");
        cname["content"] = json!("elsewhere.example.net");
        let mut aaaa = ok.clone();
        aaaa["type"] = json!("AAAA");
        let mut private = ok.clone();
        private["content"] = json!("10.0.0.2");
        let mut word = ok.clone();
        word["content"] = json!("elsewhere.example.net");
        for b in [
            no_mark,
            not_proxied,
            mx,
            extra,
            bad_code,
            ttl,
            cname,
            aaaa,
            private,
            word,
        ] {
            assert!(!allowed("POST", &p, Some(b.to_string().as_str())), "{b}");
        }
        assert!(!allowed("POST", &p, None));
        assert!(!allowed("POST", &p, Some("not json")));
    }

    /// E6 S5 samples: the host's entry name is a setting; tests use a documentation name.
    const ENTRY: &str = "entry.example.net";

    #[test]
    fn the_add_is_a_cname_to_the_entry() {
        let names = ["example.com", "www.example.com"];
        let got = plan_to(
            "example.com",
            &names,
            Some(&[][..]),
            "s-abc123",
            Target::Entry {
                name: ENTRY,
                origin: ORIGIN,
            },
        );
        let DnsPlan::Add(add) = got.clone() else {
            panic!("{got:?}");
        };
        assert_eq!(add.len(), 2);
        let p = format!("/zones/{ZID}/dns_records");
        for r in &add {
            assert_eq!(r.kind, "CNAME");
            assert_eq!(r.content, ENTRY);
            assert_eq!(r.comment, "ez2:site:s-abc123");
            let body: Value = serde_json::from_str(&r.body()).unwrap();
            assert_eq!(body["type"], "CNAME");
            assert_eq!(body["content"], ENTRY);
            assert_eq!(body["proxied"], true);
            // the add is on the list only given the host's entry name
            assert!(allowed_with(
                "POST",
                &p,
                Some(r.body().as_str()),
                Some(ENTRY)
            ));
            assert!(!allowed("POST", &p, Some(r.body().as_str())));
        }
        // the A path is as it was: with an entry name given, an A add to the host is still one
        let a = json!({"type": "A", "name": "example.com", "content": ORIGIN, "proxied": true, "ttl": 1, "comment": "ez2:site:s-abc123"});
        assert!(allowed_with(
            "POST",
            &p,
            Some(a.to_string().as_str()),
            Some(ENTRY)
        ));
        // asked again with the CNAMEs there (case and a final dot as Cloudflare may list them)
        let there = [
            rec(
                "CNAME",
                "example.com",
                "Entry.Example.net.",
                true,
                "ez2:site:s-abc123",
            ),
            rec("CNAME", "www.example.com", ENTRY, true, "ez2:site:s-abc123"),
        ];
        assert_eq!(
            plan_to(
                "example.com",
                &names,
                Some(&there[..]),
                "s-abc123",
                Target::Entry {
                    name: ENTRY,
                    origin: ORIGIN
                }
            ),
            DnsPlan::Done
        );
        // S15 judges the CNAMEs like any address record: proxied passes, grey fails
        assert_eq!(proxied(&names, Some(&there[..])), Proxied::All);
        let mut grey = there.clone();
        grey[1].proxied = false;
        assert_eq!(
            proxied(&names, Some(&grey[..])),
            Proxied::Grey(vec![String::from("www.example.com")])
        );
    }

    #[test]
    fn a_cname_elsewhere_is_refused() {
        let p = format!("/zones/{ZID}/dns_records");
        let ok = json!({"type": "CNAME", "name": "example.com", "content": ENTRY, "proxied": true, "ttl": 1, "comment": "ez2:site:s-abc123"});
        assert!(allowed_with(
            "POST",
            &p,
            Some(ok.to_string().as_str()),
            Some(ENTRY)
        ));
        let mut elsewhere = ok.clone();
        elsewhere["content"] = json!("elsewhere.example.net");
        let mut under = ok.clone();
        under["content"] = json!("x.entry.example.net");
        let mut address = ok.clone();
        address["content"] = json!(ORIGIN);
        let mut grey = ok.clone();
        grey["proxied"] = json!(false);
        for b in [elsewhere, under, address, grey] {
            assert!(
                !allowed_with("POST", &p, Some(b.to_string().as_str()), Some(ENTRY)),
                "{b}"
            );
        }
        // no entry name on the host, or one that is not a name: no CNAME at all
        assert!(!allowed_with(
            "POST",
            &p,
            Some(ok.to_string().as_str()),
            None
        ));
        assert!(!allowed_with(
            "POST",
            &p,
            Some(ok.to_string().as_str()),
            Some("not a name")
        ));
        // this site's mark on a CNAME pointing elsewhere is not ours as written
        let r = [rec(
            "CNAME",
            "example.com",
            "elsewhere.example.net",
            true,
            "ez2:site:s-abc123",
        )];
        let to_entry = |r: &[Record]| {
            plan_to(
                "example.com",
                &["example.com"],
                Some(r),
                "s-abc123",
                Target::Entry {
                    name: ENTRY,
                    origin: ORIGIN,
                },
            )
        };
        assert_eq!(to_entry(&r[..]), DnsPlan::Stop(MARKED_DIFFERS));
        // judged by the record's own type: this site's A record to the host is done as it
        // is, one to another address is not ours as written (c477a S5 L1)
        let a = [rec("A", "example.com", ORIGIN, true, "ez2:site:s-abc123")];
        assert_eq!(to_entry(&a[..]), DnsPlan::Done);
        let moved = [rec(
            "A",
            "example.com",
            "198.51.100.7",
            true,
            "ez2:site:s-abc123",
        )];
        assert_eq!(to_entry(&moved[..]), DnsPlan::Stop(MARKED_DIFFERS));
        // an entry name that is not a name, or is one of the site's own names
        for e in ["not a name", "example.com", "Example.com."] {
            assert_eq!(
                plan_to(
                    "example.com",
                    &["example.com"],
                    Some(&[][..]),
                    "s-abc123",
                    Target::Entry {
                        name: e,
                        origin: ORIGIN
                    }
                ),
                DnsPlan::Stop(ENTRY_UNSET),
                "{e}"
            );
        }
    }

    #[test]
    fn a_zone_in_another_account_falls_back_to_a() {
        let names = ["example.com", "www.example.com"];
        let theirs = zone("example.com", "active", OTHER);
        let (t, why) = target_for(
            &theirs,
            &names,
            &[],
            "s-abc123",
            ORIGIN,
            Some(ENTRY),
            Some(ACC),
        );
        assert_eq!(
            (t, why),
            (Target::Origin(ORIGIN), Some(ENTRY_SKIPPED_OTHER_ACCOUNT))
        );
        // the plan for it is the A records, not a stop
        let DnsPlan::Add(add) = plan_to("example.com", &names, Some(&[][..]), "s-abc123", t) else {
            panic!("not an add");
        };
        assert!(add.iter().all(|r| r.kind == "A" && r.content == ORIGIN));
        // the same account: the CNAME
        let ours = zone("example.com", "active", ACC);
        assert_eq!(
            target_for(
                &ours,
                &names,
                &[],
                "s-abc123",
                ORIGIN,
                Some(ENTRY),
                Some(ACC)
            ),
            (
                Target::Entry {
                    name: ENTRY,
                    origin: ORIGIN
                },
                None
            )
        );
        // no entry name: the address, nothing to say
        assert_eq!(
            target_for(&ours, &names, &[], "s-abc123", ORIGIN, None, Some(ACC)),
            (Target::Origin(ORIGIN), None)
        );
    }

    #[test]
    fn no_entry_account_means_no_cname() {
        let names = ["example.com"];
        let ours = zone("example.com", "active", ACC);
        for account in [None, Some(""), Some("not-an-id")] {
            assert_eq!(
                target_for(&ours, &names, &[], "s-abc123", ORIGIN, Some(ENTRY), account),
                (Target::Origin(ORIGIN), Some(ENTRY_SKIPPED_NO_ACCOUNT)),
                "{account:?}"
            );
        }
    }

    #[test]
    fn a_name_in_use_falls_back_to_a() {
        let names = ["example.com", "www.example.com"];
        let ours = zone("example.com", "active", ACC);
        let pick =
            |r: &[Record]| target_for(&ours, &names, r, "s-abc123", ORIGIN, Some(ENTRY), Some(ACC));
        for other in [
            rec("TXT", "www.example.com", "v=spf1 -all", false, ""),
            rec("MX", "WWW.example.com.", "mail.example.net", false, ""),
            rec("TXT", "example.com", "hello", false, "ez2:site:s-other1"),
        ] {
            assert_eq!(
                pick(core::slice::from_ref(&other)),
                (Target::Origin(ORIGIN), Some(ENTRY_SKIPPED_NAME_IN_USE)),
                "{other:?}"
            );
        }
        // this site's own records and records under other names do not count
        let fine = [
            rec("A", "example.com", ORIGIN, true, "ez2:site:s-abc123"),
            rec("TXT", "mail.example.com", "v=spf1 -all", false, ""),
        ];
        assert_eq!(
            pick(&fine[..]).0,
            Target::Entry {
                name: ENTRY,
                origin: ORIGIN
            }
        );
    }

    #[test]
    fn the_ssl_mode_must_reach_the_host_over_https() {
        let m = |v: &str| json!({"success": true, "result": {"id": "ssl", "value": v}});
        assert_eq!(ssl_mode(&m("full")), Ok(()));
        assert_eq!(ssl_mode(&m("strict")), Ok(()));
        assert_eq!(ssl_mode(&m("flexible")), Err(SSL_MODE_NOT_FULL));
        assert_eq!(ssl_mode(&m("off")), Err(SSL_MODE_NOT_FULL));
        assert_eq!(ssl_mode(&m("weird")), Err(UNREADABLE));
        assert_eq!(ssl_mode(&json!({"success": false})), Err(UNREADABLE));
    }

    #[test]
    fn a_refusal_is_told_apart_only_where_it_means_something() {
        assert_eq!(failure_word(403, true), ACCESS_REVOKED);
        assert_eq!(failure_word(403, false), UNREADABLE);
        assert_eq!(failure_word(401, true), KEY_REFUSED);
        assert_eq!(failure_word(500, true), UNREADABLE);
        assert_eq!(failure_word(0, true), UNREADABLE);
    }

    /// S15 samples: an IPv6 address from the documentation range.
    const V6: &str = "2001:db8::10"; // ip-literal-ok: documentation range sample (S15 tests)
    const SHOP: &str = "shop.example.com";
    const WWW: &str = "www.shop.example.com";

    #[test]
    fn all_proxied_is_a_pass() {
        let records = vec![
            rec("A", SHOP, ORIGIN, true, ""),
            rec("AAAA", SHOP, V6, true, ""),
            rec("CNAME", WWW, SHOP, true, ""),
            // mail and text records are not served through the proxy and say nothing here
            rec("MX", SHOP, "mail.example.net", false, ""),
            rec("TXT", SHOP, "v=spf1 -all", false, ""),
        ];
        assert_eq!(proxied(&[SHOP, WWW], Some(&records)), Proxied::All);
        // a name without a record of its own is judged by the wildcard that answers for it
        let wild = vec![
            rec("A", SHOP, ORIGIN, true, ""),
            rec("A", "*.shop.example.com", ORIGIN, true, ""),
        ];
        assert_eq!(proxied(&[SHOP, WWW], Some(&wild)), Proxied::All);
    }

    #[test]
    fn a_grey_record_under_the_site_is_a_fail() {
        // one of two records under the name is DNS only: the name is grey
        let records = vec![
            rec("A", SHOP, ORIGIN, true, ""),
            rec("AAAA", SHOP, V6, false, ""),
            rec("CNAME", WWW, SHOP, true, ""),
        ];
        let got = proxied(&[SHOP, WWW], Some(&records));
        assert_eq!(got, Proxied::Grey(vec![String::from(SHOP)]));
        // the finding names the name, never what the record holds
        if let Proxied::Grey(names) = &got {
            assert!(names.iter().all(|n| !n.contains(ORIGIN) && !n.contains(V6)));
        }
        // a grey wildcard answering for a name is grey too, and a grey record wins over a
        // name that cannot be judged
        let wild = vec![
            rec("A", SHOP, ORIGIN, true, ""),
            rec("A", "*.shop.example.com", ORIGIN, false, ""),
        ];
        assert_eq!(
            proxied(&[SHOP, WWW, "other.example.org"], Some(&wild)),
            Proxied::Grey(vec![String::from(WWW)])
        );
        // case and a final dot do not hide a record
        let upper = vec![rec("A", "SHOP.example.com.", ORIGIN, false, "")];
        assert_eq!(
            proxied(&[SHOP], Some(&upper)),
            Proxied::Grey(vec![String::from(SHOP)])
        );
    }

    #[test]
    fn an_unreadable_zone_is_unchecked_not_pass() {
        // a listing that was not read whole
        assert_eq!(proxied(&[SHOP], None), Proxied::Unchecked(UNREADABLE));
        // a name nothing answers for, or no names at all, cannot be judged
        let records = vec![rec("A", SHOP, ORIGIN, true, "")];
        assert_eq!(
            proxied(&[SHOP, WWW], Some(&records)),
            Proxied::Unchecked(NO_ADDRESS)
        );
        assert_eq!(proxied(&[], Some(&records)), Proxied::Unchecked(NO_ADDRESS));
        let mail_only = vec![rec("MX", SHOP, "mail.example.net", false, "")];
        assert_eq!(
            proxied(&[SHOP], Some(&mail_only)),
            Proxied::Unchecked(NO_ADDRESS)
        );
    }

    // E6 S12: adopting a hand-opened site's DNS

    #[test]
    fn adopt_needs_full_ssl_and_a_challenge_the_proxy_passes() {
        let both = Some(vec!["http-01", "tls-alpn-01"]);
        assert_eq!(adopt_gate(Ok(()), core::slice::from_ref(&both)), Ok(()));
        assert_eq!(adopt_gate(Ok(()), &[Some(vec!["http-01"])]), Ok(()));
        assert_eq!(
            adopt_gate(Err(SSL_MODE_NOT_FULL), core::slice::from_ref(&both)),
            Err(SSL_MODE_NOT_FULL)
        );
        assert_eq!(
            adopt_gate(Err(UNREADABLE), core::slice::from_ref(&both)),
            Err(UNREADABLE)
        );
        assert_eq!(
            adopt_gate(Ok(()), &[both.clone(), Some(vec!["tls-alpn-01"])]),
            Err(ACME_ALPN_ONLY)
        );
        assert_eq!(adopt_gate(Ok(()), &[both, None]), Err(ACME_UNKNOWN));
        // the SSL answer is what decides flexible and off
        for mode in ["flexible", "off"] {
            let answer = json!({"success": true, "result": {"id": "ssl", "value": mode}});
            assert_eq!(
                adopt_gate(ssl_mode(&answer), &[Some(vec!["http-01"])]),
                Err(SSL_MODE_NOT_FULL),
                "{mode}"
            );
        }
    }

    #[test]
    fn the_edges_challenges_are_read_from_its_merged_config() {
        let name = "shop.example.com";
        let none = json!({"apps": {"http": {}}});
        assert_eq!(
            acme_challenges(&none, name),
            Some(vec!["http-01", "tls-alpn-01"])
        );
        let alpn_only = json!({"apps": {"tls": {"automation": {"policies": [
            {"subjects": ["other.example.com"], "issuers": [{"module": "internal"}]},
            {"subjects": ["*.example.com"], "issuers": [{"module": "acme",
                "challenges": {"http": {"disabled": true}}}]}
        ]}}}});
        assert_eq!(acme_challenges(&alpn_only, name), Some(vec!["tls-alpn-01"]));
        assert_eq!(
            acme_challenges(&alpn_only, "other.example.com"),
            Some(vec![])
        );
        let dns = json!({"apps": {"tls": {"automation": {"policies": [
            {"issuers": [{"module": "acme", "challenges": {"dns": {"provider": {"name": "x"}}}}]}
        ]}}}});
        assert_eq!(acme_challenges(&dns, name), Some(vec!["dns-01"]));
        let unknown_issuer = json!({"apps": {"tls": {"automation": {"policies": [
            {"issuers": [{"module": "someone_else"}]}
        ]}}}});
        assert_eq!(acme_challenges(&unknown_issuer, name), None);
        assert_eq!(acme_challenges(&json!("not json config"), name), None);
    }

    #[test]
    fn an_adopting_edit_is_allowed_only_proxied_and_marked() {
        let path = format!("/zones/{ZID}/dns_records/{RID}");
        let ok = |b: &str| allowed("PATCH", &path, Some(b));
        let mark = dns_marker("s-abc123");
        assert!(ok(&json!({"proxied": true, "comment": mark}).to_string()));
        assert!(ok(
            &json!({"proxied": true, "comment": format!("{mark} old note")}).to_string()
        ));
        assert!(!ok(&json!({"proxied": false, "comment": mark}).to_string()));
        assert!(!ok(
            &json!({"proxied": true, "comment": "old note"}).to_string()
        ));
        assert!(!ok(
            &json!({"proxied": true, "comment": format!("{mark}X")}).to_string()
        ));
        assert!(!ok(&json!({"proxied": true}).to_string()));
        assert!(!ok(
            &json!({"proxied": true, "comment": mark, "content": "198.51.100.7"}).to_string()
        ));
        assert!(!ok(
            &json!({"proxied": true, "comment": format!("{mark} {}", "x".repeat(100))}).to_string()
        ));
        assert!(!ok(
            &json!({"proxied": true, "comment": format!("{mark}\nx")}).to_string()
        ));
        assert!(!allowed("PATCH", &path, None));
        assert!(!allowed(
            "PATCH",
            &format!("/zones/{ZID}/dns_records/x"),
            Some(
                json!({"proxied": true, "comment": mark})
                    .to_string()
                    .as_str()
            )
        ));
        let https = CfCall::AlwaysHttps {
            zone: String::from(ZID),
        };
        assert!(allowed(https.method(), &https.path(), None));
    }

    #[test]
    fn adopting_edits_only_a_records_to_this_host() {
        let code = "s-abc123";
        let mark = dns_marker(code);
        let records = [
            rec("A", "example.com", ORIGIN, false, "set by hand"),
            rec("A", "www.example.com", ORIGIN, true, &mark),
            rec("A", "shop.example.com", "198.51.100.7", false, ""),
            rec("CNAME", "example.com", "elsewhere.example.net", false, ""),
            rec("A", "example.com", ORIGIN, false, &dns_marker("s-zzz999")),
            rec("MX", "example.com", ORIGIN, false, ""),
            rec("A", "*.example.com", ORIGIN, false, ""),
        ];
        let names = ["example.com", "www.example.com", "shop.example.com"];
        let rows = adopt(ZID, &names, &records, ORIGIN, code);
        let actions: Vec<&Adopt> = rows.iter().map(|(_, a)| a).collect();
        assert_eq!(actions.len(), 5, "{rows:?}");
        let Adopt::Edit(e) = actions[0] else {
            panic!("{rows:?}")
        };
        assert_eq!(e.comment(), format!("{mark} set by hand"));
        assert!(allowed(
            "PATCH",
            &CfCall::Edit(e.clone()).path(),
            Some(e.body().as_str())
        ));
        assert_eq!(
            &actions[1..],
            &[
                &Adopt::Unchanged,
                &Adopt::Elsewhere,
                &Adopt::Elsewhere,
                &Adopt::OtherSite
            ]
        );
        assert_eq!(
            adopted_comment(code, &"y".repeat(200)).chars().count(),
            COMMENT_MAX
        );
    }

    #[test]
    fn an_adopted_comment_is_never_the_bare_mark_so_site_remove_keeps_it() {
        let code = "s-adopt1";
        let mark = dns_marker(code);
        for old in ["", "  ", "made by hand"] {
            let c = adopted_comment(code, old);
            assert_ne!(c, mark, "{old:?}");
            assert_eq!(marked_by(&c), Some(code));
            let r = Record {
                id: String::from(RID),
                kind: String::from("A"),
                name: String::from("shop.example.com"),
                content: String::from("192.0.2.10"),
                proxied: true,
                comment: c,
            };
            assert!(delete_of(ZID, &r, code).is_none(), "{old:?}");
            // the control: the same record with the bare mark is one site remove deletes
            let bare = Record {
                comment: mark.clone(),
                ..r
            };
            assert!(delete_of(ZID, &bare, code).is_some(), "{old:?}");
        }
        assert_eq!(adopted_comment(code, ""), format!("{mark} {ADOPTED_TAIL}"));
    }

    #[test]
    fn adopting_a_grey_record_already_marked_for_this_site_keeps_its_comment() {
        let code = "s-adopt1";
        let mark = dns_marker(code);
        assert_eq!(adopted_comment(code, &mark), mark);
        let kept = format!("{mark} made by hand");
        assert_eq!(adopted_comment(code, &kept), kept);
        assert_eq!(
            adopted_comment(code, "ez2:site:s-other x"),
            format!("{mark} ez2:site:s-other x")
        );
    }
}
