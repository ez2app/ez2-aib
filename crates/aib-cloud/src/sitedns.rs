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
//! * **Done.** A record with this site's mark, an A record to the host, proxied, is the
//!   step done: asked again, the step adds nothing (it can be carried on after any
//!   stop). A record with the mark that is anything else is not ours as written and
//!   stops ([`MARKED_DIFFERS`]).
//! * **Only five calls.** [`allowed`] is the whole list of Cloudflare calls this code may
//!   make: list an account's zones by name, read a zone's SSL mode, list a zone's records,
//!   add one A record to a public address, proxied, carrying a site's mark, and clear the
//!   cache of named hosts (a customer's `cache flush`: never the whole zone, c477a L7).
//!   Anything else
//!   — a delete above all, which the list could not tell from any other delete — is
//!   refused before it leaves the host (ebb36 B5 L1). The key is the account's global
//!   key, so this list is the only thing between this code and the customer's zones.
//! * **Inside the zone.** Every name of the site must be the zone or a name under it
//!   ([`NAME_OUTSIDE_ZONE`]): Cloudflare would take any other name as a label under the
//!   zone and make a record nobody asked for (ebb36 B5 L2).
//! * **What is not looked at.** The SSL mode is the zone's; a rule that changes it for one
//!   host name is not seen here. A name delegated away by an NS record is not seen either:
//!   the health step finds it.

use alloc::string::{String, ToString};
use alloc::vec::Vec;

use serde_json::{json, Value};

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
    /// Clear the cache of some hosts of a zone (the body names them; never everything).
    PurgeHosts { zone: String },
}

impl CfCall {
    /// The HTTP method.
    pub fn method(&self) -> &'static str {
        match self {
            CfCall::ListZones { .. } | CfCall::SslMode { .. } | CfCall::ListRecords { .. } => "GET",
            CfCall::AddRecord { .. } | CfCall::PurgeHosts { .. } => "POST",
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
            CfCall::PurgeHosts { zone } => format!("/zones/{zone}/purge_cache"),
        }
    }
}

/// Whether a written-out call is one of the five, exactly: the method, every part of the
/// path, the query in its one order, and for an add a body that is one A record to a
/// public address, proxied, carrying a site's mark. Everything else is refused.
pub fn allowed(method: &str, path: &str, body: Option<&str>) -> bool {
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
        ("POST", ["", "zones", z, "dns_records"], None, Some(b)) => valid_id(z) && add_body_ok(b),
        ("POST", ["", "zones", z, "purge_cache"], None, Some(b)) => valid_id(z) && purge_body_ok(b),
        _ => false,
    }
}

/// The body of an add: exactly `type`, `name`, `content`, `proxied`, `ttl`, `comment`; an
/// A record to a public address ([`valid_origin`]); proxied; the comment one site's mark.
fn add_body_ok(body: &str) -> bool {
    let Ok(Value::Object(m)) = serde_json::from_str::<Value>(body) else {
        return false;
    };
    let keys = ["type", "name", "content", "proxied", "ttl", "comment"];
    m.len() == keys.len()
        && keys.iter().all(|k| m.contains_key(*k))
        && m["type"] == "A"
        && m["name"].as_str().is_some_and(crate::edge::valid_domain)
        && m["content"].as_str().is_some_and(valid_origin)
        && m["proxied"] == Value::Bool(true)
        && m["ttl"] == 1
        && m["comment"]
            .as_str()
            .and_then(|c| c.strip_prefix(DNS_MARKER_PREFIX))
            .is_some_and(crate::site::valid_code)
}

/// Most hosts one cache clear may name (Cloudflare's own limit per call is 30).
pub const PURGE_HOSTS_MAX: usize = 30;

/// The body of a cache clear: exactly `hosts`, one to [`PURGE_HOSTS_MAX`] names, each a
/// domain, none twice. `purge_everything`, `files`, `tags`, `prefixes` or anything beside
/// `hosts` is refused: a clear reaches the named hosts only (c477a L7).
fn purge_body_ok(body: &str) -> bool {
    let Ok(Value::Object(m)) = serde_json::from_str::<Value>(body) else {
        return false;
    };
    let Some(Value::Array(hosts)) = m.get("hosts") else {
        return false;
    };
    let names: Vec<&str> = hosts.iter().filter_map(Value::as_str).collect();
    let mut seen = names.clone();
    seen.sort_unstable();
    seen.dedup();
    m.len() == 1
        && !names.is_empty()
        && names.len() == hosts.len()
        && names.len() <= PURGE_HOSTS_MAX
        && seen.len() == names.len()
        && names.iter().all(|n| crate::edge::valid_domain(n))
}

/// The body that clears the cache of the site's names that are in `zone`: `None` when
/// none of them is, or when there are more than one call may name. Only names of the
/// zone go in, so a clear cannot reach a host of another zone (or of another site: the
/// names are the site's own, from its record).
pub fn purge_body(zone: &str, names: &[&str]) -> Option<String> {
    let mine: Vec<&str> = names.iter().copied().filter(|n| in_zone(n, zone)).collect();
    if mine.is_empty() || mine.len() > PURGE_HOSTS_MAX {
        return None;
    }
    let body = json!({ "hosts": mine }).to_string();
    purge_body_ok(&body).then_some(body)
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
    pub name: String,
    pub content: String,
    pub comment: String,
}

impl NewRecord {
    /// The body of the add call: an A record to the host, proxied (`ttl` 1 is
    /// Cloudflare's "automatic", the only one a proxied record has), the site's mark as
    /// its comment.
    pub fn body(&self) -> String {
        serde_json::json!({
            "type": "A",
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
    if !valid_origin(origin) {
        return DnsPlan::Stop(ORIGIN_UNSET);
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
            [ours] if ours.kind == "A" && ours.content == origin && ours.proxied => continue,
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
            name: String::from(*name),
            content: String::from(origin),
            comment: mark.clone(),
        });
    }
    if add.is_empty() {
        DnsPlan::Done
    } else {
        DnsPlan::Add(add)
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
            "01.2.3.4",
            "1.2.3.0",
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
    fn only_the_four_calls_are_allowed() {
        let zone = String::from(ZID);
        let body = NewRecord {
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
                "GET",
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
            // no delete at all in this slice (ebb36 B5 L1)
            ("DELETE", format!("/zones/{ZID}/dns_records/{RID}"), None),
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

    #[test]
    fn a_cache_clear_names_hosts_and_never_the_whole_zone() {
        let p = format!("/zones/{ZID}/purge_cache");
        let ok = json!({"hosts": ["shop.example.com", "www.shop.example.com"]}).to_string();
        assert!(allowed("POST", &p, Some(ok.as_str())));
        assert_eq!(
            CfCall::PurgeHosts {
                zone: ZID.to_string()
            }
            .path(),
            p
        );
        let refused = [
            json!({"purge_everything": true}),
            json!({"hosts": ["shop.example.com"], "purge_everything": true}),
            json!({"files": ["https://shop.example.com/"]}),
            json!({"prefixes": ["shop.example.com/"]}),
            json!({"hosts": []}),
            json!({"hosts": ["shop.example.com", "shop.example.com"]}),
            json!({"hosts": ["shop.example.com/x"]}),
            json!({"hosts": [1]}),
            json!({"hosts": "shop.example.com"}),
        ];
        for body in &refused {
            assert!(
                !allowed("POST", &p, Some(body.to_string().as_str())),
                "{body}"
            );
        }
        let many: Vec<String> = (0..=PURGE_HOSTS_MAX)
            .map(|i| format!("h{i}.example.com"))
            .collect();
        assert!(!allowed(
            "POST",
            &p,
            Some(json!({ "hosts": many }).to_string().as_str())
        ));
        assert!(!allowed("POST", &p, None));
        assert!(!allowed("GET", &p, Some(ok.as_str())));
        assert!(!allowed(
            "POST",
            &format!("/zones/{ZID}/purge_cache?x=1"),
            Some(ok.as_str())
        ));
    }

    #[test]
    fn a_cache_clear_body_keeps_to_the_zone() {
        let body = purge_body(
            "example.com",
            &["shop.example.com", "other.test", "example.com"],
        )
        .unwrap();
        assert_eq!(
            body,
            json!({"hosts": ["shop.example.com", "example.com"]}).to_string()
        );
        assert_eq!(purge_body("example.com", &["other.test"]), None);
        assert_eq!(purge_body("example.com", &[]), None);
        assert_eq!(purge_body("example.com", &["notexample.com"]), None);
    }
}
