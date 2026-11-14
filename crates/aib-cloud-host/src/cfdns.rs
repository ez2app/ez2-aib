// SPDX-License-Identifier: Apache-2.0
//! The DNS step of opening a site (B5, [`aib_cloud::sitecreate::CreateStep::Dns`]): find
//! the customer's zone at Cloudflare, read what is under the site's names, add the
//! proxied records carrying the site's mark, and read them back. Every judgement is
//! [`aib_cloud::sitedns`]'s; this file asks and writes down.
//!
//! * **The key.** [`CLOUDFLARE_ENV`] (root only), the same file and names as the
//!   hand-run DNS tools: `CF_AUTH_EMAIL`, `CF_GLOBAL_KEY`, and `CF_ORIGIN_IPV4`, the
//!   host's own address the records point at. Optional `CF_ENTRY_NAME` (E6 S5): when it is
//!   set together with `CF_ENTRY_ACCOUNT` (the account the entry name is in), a zone in
//!   that same account gets CNAMEs to that one name instead of A records, and an add is
//!   checked against exactly that name ([`aib_cloud::sitedns::allowed_with`]). A zone in
//!   another account would get error 1014 from Cloudflare (PLAN-e6 question 15), a name
//!   with a record that is not the site's would be refused: those get A records, and the
//!   opening record says why (`dns.entry_skipped`). Without both settings: A records.
//!   The key is handed to `curl` on its standard
//!   input (`curl -K -`): it is never in an argument list, never printed, never in the
//!   ledger, the record or an error.
//! * **Only the allowed calls.** Every call is written out and checked by
//!   [`aib_cloud::sitedns::allowed`] before it is made; one that is not on the list is
//!   refused here and nothing leaves the host.
//! * **One ledger line per call** ([`CF_LEDGER`]): when, which site, the method and path,
//!   Cloudflare's status — or `refused`. A ledger that cannot be opened stops the call.
//! * **Journal.** Before the first add the opening record says which names are being
//!   added (`dns.adding`); after the read-back it says done (`dns.done`). A step stopped in
//!   between is carried on safely: the records carry the site's mark, so asking again
//!   finds them and adds nothing twice.
//! * **Whose zone.** The opening record's `tenant_id`; that customer's roster under
//!   [`ROSTERS`] and its `cloudflare_accounts`. No customer, no roster or no accounts is
//!   "cannot tell" ([`aib_cloud::sitedns::OWNER_UNKNOWN`]).

use std::fs;
use std::io::Write;
use std::path::Path;
use std::time::Duration;

use aib_cloud::site::{record_tenant, SiteSpec};
use aib_cloud::sitedns::{self, CfCall, DnsPlan, Zone};
use serde_json::{json, Value};

use crate::sitecreatecmd::{HostPaths, Runner};

/// The Cloudflare key of this host and the address the records point at.
pub const CLOUDFLARE_ENV: &str = "/etc/ez2-cloud/cloudflare.env";
/// The customers' rosters, one `<tenant_id>.json` each.
pub const ROSTERS: &str = "/etc/ez2-cloud/rosters";
/// One line per Cloudflare call.
pub const CF_LEDGER: &str = "/var/log/ez2-cloud/cloudflare.jsonl";
/// Where the calls go.
const API: &str = "https://api.cloudflare.com/client/v4";
/// A records listing longer than this many pages is not read (2000 records).
const MAX_PAGES: u32 = 40;
/// Right after an add the listing's count and its pages can disagree for a moment.
const LISTING_TRIES: u32 = 3;
const LISTING_WAIT: Duration = Duration::from_secs(1);
/// The key is missing from the host or is not a key.
pub const KEY_UNSET: &str = "cloudflare_key_unset";
/// The setting naming the one entry name the records point at as CNAMEs (E6 S5).
pub const ENTRY_KEY: &str = "CF_ENTRY_NAME";
/// The setting naming the Cloudflare account the entry name is in: a zone gets the CNAME
/// only when it is in this same account (c477a S5 L1, error 1014). Not set: no CNAME.
pub const ENTRY_ACCOUNT_KEY: &str = "CF_ENTRY_ACCOUNT";

/// The key. Not `Debug`, not `Display`: it is only ever written into curl's input.
pub struct Creds {
    email: String,
    key: String,
}

/// `KEY=value` from an env file, `export ` and quotes taken off; empty is absent.
fn env_get(text: &str, key: &str) -> Option<String> {
    text.lines()
        .map(str::trim)
        .filter(|l| !l.starts_with('#'))
        .map(|l| l.strip_prefix("export ").unwrap_or(l).trim_start())
        .filter_map(|l| l.strip_prefix(key)?.strip_prefix('='))
        .map(|v| v.trim().trim_matches(|c| c == '"' || c == '\'').to_owned())
        .find(|v| !v.is_empty())
}

/// Safe inside a quoted curl configuration value: nothing that ends the value or the line.
fn plain(v: &str) -> bool {
    !v.is_empty()
        && v.len() <= 254
        && v.bytes()
            .all(|c| c.is_ascii_graphic() && c != b'"' && c != b'\\')
}

/// The key and the origin from [`CLOUDFLARE_ENV`]'s text. What is wrong is said by name,
/// never by value.
pub fn creds_from(text: &str) -> Result<(Creds, Option<String>), String> {
    let email = env_get(text, "CF_AUTH_EMAIL")
        .filter(|e| plain(e) && e.contains('@'))
        .ok_or_else(|| format!("{KEY_UNSET}: CF_AUTH_EMAIL is not set or not an address"))?;
    let key = env_get(text, "CF_GLOBAL_KEY")
        .filter(|k| (32..=64).contains(&k.len()) && k.bytes().all(|c| c.is_ascii_alphanumeric()))
        .ok_or_else(|| format!("{KEY_UNSET}: CF_GLOBAL_KEY is not set or not a key"))?;
    Ok((Creds { email, key }, env_get(text, "CF_ORIGIN_IPV4")))
}

/// curl's configuration for one call, fed on its standard input.
fn curl_config(creds: &Creds, call: &CfCall, body: Option<&str>) -> String {
    let mut c = format!(
        "silent\nproto = \"=https\"\nmax-time = 30\nrequest = \"{}\"\nurl = \"{API}{}\"\nheader = \"X-Auth-Email: {}\"\nheader = \"X-Auth-Key: {}\"\nheader = \"Content-Type: application/json\"\nwrite-out = \"\\n%{{http_code}}\"\n",
        call.method(),
        call.path(),
        creds.email,
        creds.key
    );
    if let Some(b) = body {
        c.push_str(&format!(
            "data = \"{}\"\n",
            b.replace('\\', "\\\\").replace('"', "\\\"")
        ));
    }
    c
}

/// The argument list: nothing but the program and "read the rest from standard input".
pub fn curl_argv() -> Vec<String> {
    ["curl", "-K", "-"]
        .iter()
        .map(|s| (*s).to_owned())
        .collect()
}

/// Cloudflare through a runner, for one site.
pub struct Cf<'a, R: Runner> {
    runner: &'a mut R,
    creds: Creds,
    ledger: &'a Path,
    site: &'a str,
    /// The host's entry name, when set: an add may then be a CNAME to it.
    entry: Option<String>,
}

impl<'a, R: Runner> Cf<'a, R> {
    pub fn new(runner: &'a mut R, creds: Creds, ledger: &'a Path, site: &'a str) -> Self {
        Cf {
            runner,
            creds,
            ledger,
            site,
            entry: None,
        }
    }

    /// The host's entry name (E6 S5): the one name a CNAME add may point at.
    pub fn with_entry(mut self, entry: Option<String>) -> Self {
        self.entry = entry;
        self
    }

    fn ledger_line(
        &self,
        file: &mut fs::File,
        call: &CfCall,
        outcome: Value,
    ) -> Result<(), String> {
        let line = json!({
            "at": crate::probe::unix_now(),
            "site": self.site,
            "method": call.method(),
            "path": call.path(),
            "outcome": outcome,
        });
        writeln!(file, "{line}")
            .and_then(|()| file.sync_data())
            .map_err(|e| format!("{}: {e}", self.ledger.display()))
    }

    /// Make one call: Cloudflare's HTTP status (0 when none came) and its answer
    /// (`null` when it was not JSON). A call not on the list, a ledger that cannot be
    /// written or a runner that cannot run curl is an error and is not made.
    pub fn call(&mut self, call: &CfCall, body: Option<&str>) -> Result<(u16, Value), String> {
        if let Some(dir) = self.ledger.parent() {
            fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
        }
        let mut file = open_ledger(self.ledger)?;
        if !sitedns::allowed_with(call.method(), &call.path(), body, self.entry.as_deref()) {
            self.ledger_line(&mut file, call, json!("refused"))?;
            return Err(format!(
                "{}: {} {} is not one of the allowed calls",
                sitedns::CALL_NOT_ALLOWED,
                call.method(),
                call.path()
            ));
        }
        let config = curl_config(&self.creds, call, body);
        let out = self.runner.run_input(&curl_argv(), config.as_bytes());
        let (http, answer) = match &out {
            Ok(text) => {
                let (payload, code) = text.rsplit_once('\n').unwrap_or(("", text));
                (
                    code.trim().parse::<u16>().unwrap_or(0),
                    serde_json::from_str(payload).unwrap_or(Value::Null),
                )
            }
            Err(_) => (0, Value::Null),
        };
        self.ledger_line(&mut file, call, json!(http))?;
        match out {
            Ok(_) => Ok((http, answer)),
            Err(e) => Err(format!("{}: curl: {e}", sitedns::UNREADABLE)),
        }
    }
}

fn open_ledger(path: &Path) -> Result<fs::File, String> {
    use std::os::unix::fs::OpenOptionsExt;
    fs::OpenOptions::new()
        .append(true)
        .create(true)
        .mode(0o600)
        .open(path)
        .map_err(|e| format!("{}: {e}", path.display()))
}

fn success(answer: &Value) -> bool {
    answer.get("success") == Some(&Value::Bool(true))
}

/// The customer's Cloudflare accounts, from the opening record's customer and their roster.
pub(crate) fn customer_accounts(paths: &HostPaths, record: &Value) -> Result<Vec<String>, String> {
    let unknown = |why: String| format!("{}: {why}", sitedns::OWNER_UNKNOWN);
    let tenant = record_tenant(record)
        .ok_or_else(|| unknown("the opening record names no customer".to_owned()))?;
    let file = paths.rosters.join(format!("{tenant}.json"));
    let text = fs::read_to_string(&file)
        .map_err(|e| unknown(format!("{}: {}", file.display(), e.kind())))?;
    let records = paths
        .records
        .to_str()
        .ok_or_else(|| unknown("the records directory is not text".to_owned()))?;
    let codes = crate::rostercmd::site_codes(records).map_err(unknown)?;
    let codes: Vec<&str> = codes.iter().map(String::as_str).collect();
    let roster = aib_cloud::roster::parse(&text, &codes)
        .map_err(|b| unknown(format!("{}: {}", file.display(), b.as_str())))?;
    if roster.tenant_id != tenant {
        return Err(unknown(format!(
            "{} is not {tenant}'s roster",
            file.display()
        )));
    }
    roster
        .cloudflare_accounts
        .ok_or_else(|| unknown(format!("{} names no Cloudflare account", file.display())))
}

/// Every zone the customer's accounts have under the names a zone of `domain` may have.
pub(crate) fn customer_zones<R: Runner>(
    cf: &mut Cf<'_, R>,
    accounts: &[String],
    domain: &str,
) -> Result<Vec<Zone>, String> {
    let mut zones = Vec::new();
    for account in accounts {
        for name in sitedns::zone_candidates(domain) {
            let call = CfCall::ListZones {
                account: account.clone(),
                name,
            };
            let (http, answer) = cf.call(&call, None)?;
            match sitedns::zones_from(&answer) {
                Some(z) if http == 200 => zones.extend(z),
                _ => {
                    return Err(format!(
                        "{}: the zones of an account named by the roster could not be listed (http {http})",
                        sitedns::failure_word(http, true)
                    ))
                }
            }
        }
    }
    Ok(zones)
}

/// The zone's whole records listing, or `None` when it could not be read whole.
// a named exception to ADR 0005: a short wait between two reads of Cloudflare's listing,
// in a one-shot host command that has no timer loop of its own
#[allow(clippy::disallowed_methods)]
pub(crate) fn listing<R: Runner>(
    cf: &mut Cf<'_, R>,
    zone: &str,
) -> Result<Option<Vec<sitedns::Record>>, String> {
    for attempt in 0..LISTING_TRIES {
        if attempt > 0 {
            std::thread::sleep(LISTING_WAIT);
        }
        let mut pages = Vec::new();
        let mut page = 1;
        loop {
            let call = CfCall::ListRecords {
                zone: zone.to_owned(),
                page,
            };
            let (http, answer) = cf.call(&call, None)?;
            if http == 401 {
                return Err(format!("{}: records listing", sitedns::KEY_REFUSED));
            }
            let Some(p) = sitedns::records_page(&answer).filter(|_| http == 200) else {
                break;
            };
            let last = p.total_pages.max(1);
            pages.push(p);
            if page >= last || page >= MAX_PAGES {
                break;
            }
            page += 1;
        }
        if let Some(all) = sitedns::whole_listing(&pages) {
            return Ok(Some(all));
        }
    }
    Ok(None)
}

/// The DNS step. `record` is the opening record; it is written before the first add and
/// after the read-back.
pub fn dns_step(
    runner: &mut impl Runner,
    paths: &HostPaths,
    spec: &SiteSpec,
    record: &mut Value,
) -> Result<(), String> {
    let text = fs::read_to_string(&paths.cloudflare)
        .map_err(|e| format!("{KEY_UNSET}: {}: {}", paths.cloudflare.display(), e.kind()))?;
    let (creds, origin) = creds_from(&text)?;
    let entry = env_get(&text, ENTRY_KEY);
    if entry
        .as_deref()
        .is_some_and(|e| !aib_cloud::edge::valid_domain(e))
    {
        return Err(format!(
            "{}: {ENTRY_KEY} is not a domain name",
            sitedns::ENTRY_UNSET
        ));
    }
    // the address is needed either way: a zone the entry is passed over for gets A records
    let origin = origin.filter(|o| sitedns::valid_origin(o)).ok_or_else(|| {
        format!(
            "{}: CF_ORIGIN_IPV4 is not set or not a public address",
            sitedns::ORIGIN_UNSET
        )
    })?;
    let entry_account = env_get(&text, ENTRY_ACCOUNT_KEY);
    let domain = spec.hosts.first().cloned().ok_or("the site has no name")?;
    let accounts = customer_accounts(paths, record)?;
    let mut cf = Cf::new(runner, creds, &paths.cf_ledger, &spec.code).with_entry(entry.clone());
    let zones = customer_zones(&mut cf, &accounts, &domain)?;
    let zone = sitedns::pick_zone(&domain, Some(accounts.as_slice()), Some(zones.as_slice()))
        .map_err(|w| format!("{w}: {domain}"))?;
    let (http, ssl) = cf.call(
        &CfCall::SslMode {
            zone: zone.id.clone(),
        },
        None,
    )?;
    if http == 401 {
        return Err(format!("{}: SSL mode", sitedns::KEY_REFUSED));
    }
    sitedns::ssl_mode(&ssl).map_err(|w| format!("{w}: the SSL mode of {}", zone.name))?;
    let names: Vec<&str> = spec.hosts.iter().map(String::as_str).collect();
    let before = listing(&mut cf, &zone.id)?;
    let (target, entry_skipped) = sitedns::target_for(
        &zone,
        &names,
        before.as_deref().unwrap_or(&[]),
        &spec.code,
        &origin,
        entry.as_deref(),
        entry_account.as_deref(),
    );
    let add = match sitedns::plan_to(&zone.name, &names, before.as_deref(), &spec.code, target) {
        DnsPlan::Done => Vec::new(),
        DnsPlan::Stop(w) => return Err(format!("{w}: {} in zone {}", names.join(","), zone.name)),
        DnsPlan::Add(list) => list,
    };
    if !add.is_empty() {
        record["dns"] = json!({
            "zone": zone.name,
            "adding": add.iter().map(|r| r.name.as_str()).collect::<Vec<_>>(),
            "kind": target.kind(),
            "entry_skipped": entry_skipped,
            "at": crate::probe::unix_now(),
        });
        crate::sitecreatecmd::write_record(&paths.creating, &spec.code, record)?;
        for r in &add {
            let (http, answer) = cf.call(
                &CfCall::AddRecord {
                    zone: zone.id.clone(),
                },
                Some(r.body().as_str()),
            )?;
            if http != 200 || !success(&answer) {
                return Err(format!(
                    "{}: the add of {} was not answered as done (http {http}); whether it is there is not known, carrying on looks again",
                    sitedns::NOT_CONFIRMED, r.name
                ));
            }
        }
        let after = listing(&mut cf, &zone.id)?;
        let read_back = sitedns::plan_to(&zone.name, &names, after.as_deref(), &spec.code, target);
        if read_back != DnsPlan::Done {
            return Err(format!(
                "{}: {} did not read back as written in zone {}",
                sitedns::NOT_CONFIRMED,
                names.join(","),
                zone.name
            ));
        }
    }
    record["dns"] = json!({
        "zone": zone.name,
        "names": names,
        "entry_skipped": entry_skipped,
        "done": crate::probe::unix_now(),
    });
    crate::sitecreatecmd::write_record(&paths.creating, &spec.code, record)
}

/// Removing an abandoned opening's DNS: the address records under `names` that carry the
/// site's mark, and nothing else (the rule of removing a site, #669), in the customer's
/// zone of `domain`. Each delete is one [`sitedns::delete_of`] gave for a record of a
/// whole listing taken just before; afterwards the zone is listed again and none may be
/// left. A zone that cannot be found or read is "could not tell" and stops.
pub fn remove_marked(
    runner: &mut impl Runner,
    paths: &HostPaths,
    code: &str,
    domain: &str,
    names: &[&str],
    record: &Value,
) -> Result<(), String> {
    let text = fs::read_to_string(&paths.cloudflare)
        .map_err(|e| format!("{KEY_UNSET}: {}: {}", paths.cloudflare.display(), e.kind()))?;
    let (creds, _) = creds_from(&text)?;
    let accounts = customer_accounts(paths, record)?;
    let mut cf = Cf::new(runner, creds, &paths.cf_ledger, code);
    let zones = customer_zones(&mut cf, &accounts, domain)?;
    let zone = sitedns::pick_zone(domain, Some(accounts.as_slice()), Some(zones.as_slice()))
        .map_err(|w| format!("{w}: {domain}"))?;
    if !names.iter().all(|n| sitedns::in_zone(n, &zone.name)) {
        return Err(format!(
            "{}: {} in zone {}",
            sitedns::NAME_OUTSIDE_ZONE,
            names.join(","),
            zone.name
        ));
    }
    let before = listing(&mut cf, &zone.id)?
        .ok_or_else(|| format!("{}: the records of {}", sitedns::UNREADABLE, zone.name))?;
    for r in sitedns::marked_to_remove(names, &before, code) {
        let d = sitedns::delete_of(&zone.id, r, code)
            .ok_or_else(|| format!("{}: {} {}", sitedns::CALL_NOT_ALLOWED, r.kind, r.name))?;
        let (http, answer) = cf.call(&CfCall::Delete(d), None)?;
        if http != 200 || !success(&answer) {
            return Err(format!(
                "{}: the delete of {} {} was not answered as done (http {http}); carrying on looks again",
                DNS_NOT_REMOVED, r.kind, r.name
            ));
        }
    }
    let after = listing(&mut cf, &zone.id)?
        .ok_or_else(|| format!("{}: the records of {}", sitedns::UNREADABLE, zone.name))?;
    let left = sitedns::marked_to_remove(names, &after, code);
    if left.is_empty() {
        Ok(())
    } else {
        Err(format!(
            "{DNS_NOT_REMOVED}: {} record(s) with the mark of {code} are still in zone {}",
            left.len(),
            zone.name
        ))
    }
}

/// A delete was not answered as done, or a marked record is still listed after it.
pub const DNS_NOT_REMOVED: &str = "dns_not_removed";

/// `site verify` S15 (E6 D1): whether every address record under the site's names goes
/// through Cloudflare's proxy, judged by [`sitedns::proxied`]. Reads only: the zones of the
/// customer's accounts and the zone's records, two of the calls [`sitedns::allowed`] lists,
/// each with its ledger line like every call here. Anything that keeps it from reading the
/// whole zone is an error, which verify reports as unchecked, never as a pass. One zone
/// per site, the zone of its first name: a site with a name outside it (a second domain
/// on another zone) is [`sitedns::MULTI_ZONE`] without its records being read.
/// Only `site verify` calls this; a customer's status never does (c477a, S11 M1).
pub fn proxied_names(
    runner: &mut impl Runner,
    paths: &HostPaths,
    spec: &SiteSpec,
    record: &Value,
) -> Result<sitedns::Proxied, String> {
    let text = fs::read_to_string(&paths.cloudflare)
        .map_err(|e| format!("{KEY_UNSET}: {}: {}", paths.cloudflare.display(), e.kind()))?;
    let (creds, _) = creds_from(&text)?;
    let domain = spec.hosts.first().cloned().ok_or("the site has no name")?;
    let accounts = customer_accounts(paths, record)?;
    let mut cf = Cf::new(runner, creds, &paths.cf_ledger, &spec.code);
    let zones = customer_zones(&mut cf, &accounts, &domain)?;
    let zone = sitedns::pick_zone(&domain, Some(accounts.as_slice()), Some(zones.as_slice()))
        .map_err(|w| format!("{w}: {domain}"))?;
    let names: Vec<&str> = spec.hosts.iter().map(String::as_str).collect();
    if names.iter().any(|n| !sitedns::in_zone(n, &zone.name)) {
        return Ok(sitedns::Proxied::Unchecked(sitedns::MULTI_ZONE));
    }
    let all = listing(&mut cf, &zone.id)?;
    Ok(sitedns::proxied(&names, all.as_deref()))
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    const ACC: &str = "0123456789abcdef0123456789abcdef";
    const OTHER: &str = "fedcba9876543210fedcba9876543210";
    const ZID: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const RID: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    const KEY: &str = "0123456789abcdef0123456789abcdef01234";
    const ORIGIN: &str = "192.0.2.10";
    const CODE: &str = "s-dns5ab";
    const DOMAIN: &str = "shop.example.com";

    /// A Cloudflare that answers from a list of records it keeps, and remembers every
    /// configuration it was fed.
    struct FakeCf {
        fed: Vec<String>,
        argv: Vec<Vec<String>>,
        zones: Value,
        ssl: &'static str,
        records: Vec<Value>,
        /// adds are answered but not kept
        lose_adds: bool,
        /// the zone listing is refused with this status
        zones_http: u16,
        /// deletes are answered but not done
        lose_deletes: bool,
    }

    impl FakeCf {
        fn new() -> Self {
            FakeCf {
                fed: Vec::new(),
                argv: Vec::new(),
                zones: json!([{"id": ZID, "name": "example.com", "status": "active", "account": {"id": ACC}}]),
                ssl: "full",
                records: vec![
                    json!({"id": RID, "type": "MX", "name": "example.com", "content": "mail.example.com", "proxied": false, "comment": null}),
                ],
                lose_adds: false,
                zones_http: 200,
                lose_deletes: false,
            }
        }
        fn deletes(&self) -> usize {
            self.fed
                .iter()
                .filter(|c| c.contains("request = \"DELETE\""))
                .count()
        }
        fn adds(&self) -> usize {
            self.fed
                .iter()
                .filter(|c| c.contains("request = \"POST\""))
                .count()
        }
    }

    fn config_value<'a>(config: &'a str, key: &str) -> Option<&'a str> {
        config.lines().find_map(|l| {
            l.strip_prefix(key)?
                .strip_prefix(" = \"")?
                .strip_suffix('"')
        })
    }

    impl Runner for FakeCf {
        fn run(&mut self, _argv: &[String]) -> Result<String, String> {
            Err("not a curl call".to_owned())
        }
        fn run_input(&mut self, argv: &[String], input: &[u8]) -> Result<String, String> {
            let config = String::from_utf8(input.to_vec()).unwrap();
            self.argv.push(argv.to_vec());
            self.fed.push(config.clone());
            let url = config_value(&config, "url").unwrap().to_owned();
            let path = url.strip_prefix(API).unwrap();
            let method = config_value(&config, "request").unwrap();
            let (code, body) = if path.starts_with("/zones?") {
                if self.zones_http != 200 {
                    (
                        self.zones_http,
                        json!({"success": false, "errors": [{"code": 9109}]}),
                    )
                } else {
                    let name = path.split("name=").nth(1).unwrap();
                    let acc = path
                        .split("account.id=")
                        .nth(1)
                        .unwrap()
                        .split('&')
                        .next()
                        .unwrap();
                    let list: Vec<Value> = self
                        .zones
                        .as_array()
                        .unwrap()
                        .iter()
                        .filter(|z| z["name"] == name && z["account"]["id"] == acc)
                        .cloned()
                        .collect();
                    (200, json!({"success": true, "result": list}))
                }
            } else if path.ends_with("/settings/ssl") {
                (
                    200,
                    json!({"success": true, "result": {"id": "ssl", "value": self.ssl}}),
                )
            } else if method == "DELETE" {
                let id = path.rsplit('/').next().unwrap().to_owned();
                if !self.lose_deletes {
                    self.records.retain(|r| r["id"] != id.as_str());
                }
                (200, json!({"success": true, "result": {"id": id}}))
            } else if method == "POST" {
                let data = config_value(&config, "data").unwrap().replace("\\\"", "\"");
                let mut rec: Value = serde_json::from_str(&data).unwrap();
                rec["id"] = json!(format!("{:032x}", self.records.len() + 7));
                if !self.lose_adds {
                    self.records.push(rec.clone());
                }
                (200, json!({"success": true, "result": rec}))
            } else {
                let n = self.records.len();
                (
                    200,
                    json!({"success": true, "result": self.records,
                    "result_info": {"page": 1, "total_pages": 1, "total_count": n}}),
                )
            };
            Ok(format!("{body}\n{code}"))
        }
    }

    fn scratch(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("aib-cfdns-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    fn paths(root: &Path) -> HostPaths {
        let p = HostPaths {
            creating: root.join("creating"),
            lxc_conf: root.join("lxc"),
            firewall: root.join("firewall"),
            secrets: root.join("secrets"),
            site_admins: root.join("site-admins"),
            setup: root.join("ez2-site-setup"),
            package: root.join("wp-package"),
            package_conf: root.join("wp-package.conf"),
            records: root.join("records"),
            sites_list: root.join("sites.list"),
            keys: root.join("keys.env"),
            opened: root.join("opened"),
            cloudflare: root.join("cloudflare.env"),
            rosters: root.join("rosters"),
            cf_ledger: root.join("log").join("cloudflare.jsonl"),
        };
        for d in [&p.creating, &p.records, &p.rosters] {
            fs::create_dir_all(d).unwrap();
        }
        fs::write(
            &p.cloudflare,
            format!("CF_AUTH_EMAIL=\"ops@example.test\"\nexport CF_GLOBAL_KEY={KEY}\nCF_ORIGIN_IPV4={ORIGIN}\n"),
        )
        .unwrap();
        fs::write(
            p.rosters.join("t-aaa.json"),
            json!({"tenant_id": "t-aaa", "cloudflare_accounts": [ACC], "members": []}).to_string(),
        )
        .unwrap();
        p
    }

    fn spec() -> SiteSpec {
        SiteSpec::new(
            CODE,
            131,
            41,
            &[DOMAIN],
            true,
            "zh_TW",
            aib_cloud::site::Limits::START,
        )
        .unwrap()
    }

    fn opening() -> Value {
        json!({"site": CODE, "ct": 131, "hosts": DOMAIN, "tenant_id": "t-aaa", "step": "licenses"})
    }

    fn ledger(p: &HostPaths) -> Vec<Value> {
        fs::read_to_string(&p.cf_ledger)
            .unwrap_or_default()
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect()
    }

    fn saved(p: &HostPaths) -> Value {
        serde_json::from_str(&fs::read_to_string(p.creating.join(format!("{CODE}.json"))).unwrap())
            .unwrap()
    }

    /// The zone's records for removing: this site's marked address records under both
    /// names, another site's, an unmarked one, the mail record.
    fn marked_zone() -> FakeCf {
        let mut cf = FakeCf::new();
        let mark = format!("ez2:site:{CODE}");
        cf.records.extend([
            json!({"id": "c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1", "type": "A", "name": DOMAIN, "content": ORIGIN, "proxied": true, "comment": mark}),
            json!({"id": "c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2", "type": "AAAA", "name": format!("www.{DOMAIN}"), "content": "2001:db8::1", "proxied": true, "comment": mark}),
            json!({"id": "c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3", "type": "A", "name": DOMAIN, "content": "192.0.2.20", "proxied": true, "comment": "ez2:site:s-other1"}),
            json!({"id": "c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4", "type": "A", "name": format!("www.{DOMAIN}"), "content": "192.0.2.30", "proxied": false, "comment": null}),
        ]);
        cf
    }

    fn www() -> String {
        format!("www.{DOMAIN}")
    }

    #[test]
    fn only_this_sites_marked_address_records_are_removed() {
        let root = scratch("rm");
        let p = paths(&root);
        let mut cf = marked_zone();
        let w = www();
        remove_marked(&mut cf, &p, CODE, DOMAIN, &[DOMAIN, &w], &opening()).unwrap();
        assert_eq!(cf.deletes(), 2);
        let ids: Vec<&str> = cf
            .records
            .iter()
            .map(|r| r["id"].as_str().unwrap())
            .collect();
        assert_eq!(
            ids,
            [
                RID,
                "c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3",
                "c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4"
            ]
        );
        // every call is in the ledger; the key is in none of it
        assert_eq!(ledger(&p).len(), cf.fed.len());
        assert!(!fs::read_to_string(&p.cf_ledger).unwrap().contains(KEY));
        // asked again there is nothing left to delete
        remove_marked(&mut cf, &p, CODE, DOMAIN, &[DOMAIN, &w], &opening()).unwrap();
        assert_eq!(cf.deletes(), 2);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_delete_that_does_not_take_is_not_removed() {
        let root = scratch("rmlost");
        let p = paths(&root);
        let mut cf = marked_zone();
        cf.lose_deletes = true;
        let err = remove_marked(&mut cf, &p, CODE, DOMAIN, &[DOMAIN], &opening()).unwrap_err();
        assert!(err.starts_with(DNS_NOT_REMOVED), "{err}");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_zone_that_cannot_be_read_removes_nothing() {
        let root = scratch("rmzone");
        let p = paths(&root);
        let mut cf = marked_zone();
        cf.zones_http = 403;
        assert!(remove_marked(&mut cf, &p, CODE, DOMAIN, &[DOMAIN], &opening()).is_err());
        assert_eq!(cf.deletes(), 0);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_name_outside_the_zone_removes_nothing() {
        let root = scratch("rmout");
        let p = paths(&root);
        let mut cf = marked_zone();
        let err = remove_marked(
            &mut cf,
            &p,
            CODE,
            DOMAIN,
            &[DOMAIN, "shop.example.net"],
            &opening(),
        )
        .unwrap_err();
        assert!(err.starts_with(sitedns::NAME_OUTSIDE_ZONE), "{err}");
        assert_eq!(cf.deletes(), 0);
        let _ = fs::remove_dir_all(&root);
    }

    /// E6 S5: the host's entry name is a setting; tests use a documentation name.
    const ENTRY: &str = "entry.example.net";

    #[test]
    fn a_sites_marked_cname_is_removed_and_no_other() {
        let root = scratch("rmcname");
        let p = paths(&root);
        let mut cf = marked_zone();
        let mark = format!("ez2:site:{CODE}");
        cf.records.extend([
            json!({"id": "c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5", "type": "CNAME", "name": www(), "content": ENTRY, "proxied": true, "comment": mark}),
            json!({"id": "c6c6c6c6c6c6c6c6c6c6c6c6c6c6c6c6", "type": "CNAME", "name": www(), "content": ENTRY, "proxied": true, "comment": "ez2:site:s-other1"}),
            json!({"id": "c7c7c7c7c7c7c7c7c7c7c7c7c7c7c7c7", "type": "CNAME", "name": DOMAIN, "content": "elsewhere.example.net", "proxied": false, "comment": null}),
        ]);
        let w = www();
        remove_marked(&mut cf, &p, CODE, DOMAIN, &[DOMAIN, &w], &opening()).unwrap();
        // this site's A, AAAA and CNAME go; another site's and the unmarked CNAME stay
        assert_eq!(cf.deletes(), 3);
        let ids: Vec<&str> = cf
            .records
            .iter()
            .map(|r| r["id"].as_str().unwrap())
            .collect();
        assert!(
            !ids.contains(&"c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5"),
            "{ids:?}"
        );
        assert!(ids.contains(&"c6c6c6c6c6c6c6c6c6c6c6c6c6c6c6c6"), "{ids:?}");
        assert!(ids.contains(&"c7c7c7c7c7c7c7c7c7c7c7c7c7c7c7c7"), "{ids:?}");
        let _ = fs::remove_dir_all(&root);
    }

    /// The host's settings with an entry name and, if given, the entry's account.
    fn entry_env(entry: &str, account: Option<&str>) -> String {
        let mut t = format!(
            "CF_AUTH_EMAIL=\"ops@example.test\"\nexport CF_GLOBAL_KEY={KEY}\nCF_ORIGIN_IPV4={ORIGIN}\nCF_ENTRY_NAME={entry}\n"
        );
        if let Some(a) = account {
            t.push_str(&format!("CF_ENTRY_ACCOUNT={a}\n"));
        }
        t
    }

    #[test]
    fn a_zone_the_entry_cannot_serve_gets_a_records_and_says_why() {
        for (account, why) in [
            (Some(OTHER), sitedns::ENTRY_SKIPPED_OTHER_ACCOUNT),
            (None, sitedns::ENTRY_SKIPPED_NO_ACCOUNT),
        ] {
            let root = scratch("entryskip");
            let p = paths(&root);
            fs::write(&p.cloudflare, entry_env(ENTRY, account)).unwrap();
            let mut cf = FakeCf::new();
            let mut rec = opening();
            dns_step(&mut cf, &p, &spec(), &mut rec).unwrap();
            assert_eq!(cf.adds(), 1, "{why}");
            let added = cf.records.last().unwrap();
            assert_eq!(added["type"], "A", "{why}");
            assert_eq!(added["content"], ORIGIN, "{why}");
            // read back as done, and the opening record says why there is no CNAME
            assert!(rec["dns"]["done"].is_u64(), "{why}");
            assert_eq!(rec["dns"]["entry_skipped"], why);
            assert_eq!(saved(&p)["dns"]["entry_skipped"], why);
            let _ = fs::remove_dir_all(&root);
        }
    }

    #[test]
    fn a_host_with_an_entry_name_adds_cnames_to_it() {
        let root = scratch("entry");
        let p = paths(&root);
        // the entry name and its account, the zone in that same account
        fs::write(&p.cloudflare, entry_env(ENTRY, Some(ACC))).unwrap();
        let mut cf = FakeCf::new();
        let mut rec = opening();
        dns_step(&mut cf, &p, &spec(), &mut rec).unwrap();
        assert_eq!(cf.adds(), 1);
        let added = cf.records.last().unwrap();
        assert_eq!(added["type"], "CNAME");
        assert_eq!(added["name"], DOMAIN);
        assert_eq!(added["content"], ENTRY);
        assert_eq!(added["proxied"], true);
        assert_eq!(added["comment"], format!("ez2:site:{CODE}"));
        assert!(rec["dns"]["done"].is_u64());
        assert_eq!(rec["dns"]["entry_skipped"], Value::Null);
        assert!(ledger(&p).iter().all(|l| l["outcome"] == 200));
        // asked again: nothing more
        dns_step(&mut cf, &p, &spec(), &mut rec).unwrap();
        assert_eq!(cf.adds(), 1);
        // an entry setting that is not a name asks nothing
        fs::write(&p.cloudflare, entry_env("not_a_name", Some(ACC))).unwrap();
        let mut fresh = FakeCf::new();
        let e = dns_step(&mut fresh, &p, &spec(), &mut opening()).unwrap_err();
        assert!(e.starts_with(sitedns::ENTRY_UNSET), "{e}");
        assert!(fresh.fed.is_empty());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_free_name_gets_one_proxied_marked_record_read_back() {
        let root = scratch("add");
        let p = paths(&root);
        let mut cf = FakeCf::new();
        let mut rec = opening();
        dns_step(&mut cf, &p, &spec(), &mut rec).unwrap();
        assert_eq!(cf.adds(), 1);
        let added = cf.records.last().unwrap();
        assert_eq!(added["name"], DOMAIN);
        assert_eq!(added["type"], "A");
        assert_eq!(added["content"], ORIGIN);
        assert_eq!(added["proxied"], true);
        assert_eq!(added["comment"], format!("ez2:site:{CODE}"));
        // the mail record is where it was
        assert_eq!(cf.records[0]["type"], "MX");
        assert_eq!(rec["dns"]["zone"], "example.com");
        assert!(rec["dns"]["done"].is_u64());
        assert_eq!(saved(&p)["dns"], rec["dns"]);
        // one ledger line per call: two zone listings (shop.example.com, example.com), the
        // SSL mode, the listing, the add, the listing again
        let lines = ledger(&p);
        assert_eq!(lines.len(), cf.fed.len());
        assert_eq!(lines.len(), 6);
        assert!(lines
            .iter()
            .all(|l| l["site"] == CODE && l["outcome"] == 200));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn the_key_is_never_in_an_argument_the_ledger_or_the_record() {
        let root = scratch("key");
        let p = paths(&root);
        let mut cf = FakeCf::new();
        let mut rec = opening();
        dns_step(&mut cf, &p, &spec(), &mut rec).unwrap();
        assert!(cf.argv.iter().all(|a| *a == curl_argv()));
        assert!(cf
            .fed
            .iter()
            .all(|c| c.contains(&format!("X-Auth-Key: {KEY}"))));
        assert!(!fs::read_to_string(&p.cf_ledger).unwrap().contains(KEY));
        assert!(!rec.to_string().contains(KEY));
        assert!(!fs::read_to_string(p.creating.join(format!("{CODE}.json")))
            .unwrap()
            .contains(KEY));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn asked_again_the_step_adds_nothing() {
        let root = scratch("again");
        let p = paths(&root);
        let mut cf = FakeCf::new();
        dns_step(&mut cf, &p, &spec(), &mut opening()).unwrap();
        dns_step(&mut cf, &p, &spec(), &mut opening()).unwrap();
        assert_eq!(cf.adds(), 1);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_name_that_answers_elsewhere_is_left_alone() {
        let root = scratch("taken");
        let p = paths(&root);
        let mut cf = FakeCf::new();
        cf.records.push(json!({"id": RID, "type": "CNAME", "name": DOMAIN, "content": "shop.vendor.example.net", "proxied": false, "comment": null}));
        let e = dns_step(&mut cf, &p, &spec(), &mut opening()).unwrap_err();
        assert!(e.starts_with(sitedns::NAME_TAKEN), "{e}");
        assert_eq!(cf.adds(), 0);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_zone_of_another_account_is_not_found() {
        let root = scratch("other");
        let p = paths(&root);
        let mut cf = FakeCf::new();
        cf.zones = json!([{"id": ZID, "name": "example.com", "status": "active", "account": {"id": OTHER}}]);
        let e = dns_step(&mut cf, &p, &spec(), &mut opening()).unwrap_err();
        assert!(e.starts_with(sitedns::ZONE_NOT_FOUND), "{e}");
        // and none at all: the same word
        cf.zones = json!([]);
        let e2 = dns_step(&mut cf, &p, &spec(), &mut opening()).unwrap_err();
        assert_eq!(e, e2);
        assert_eq!(cf.adds(), 0);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_customer_without_accounts_cannot_be_told() {
        let root = scratch("owner");
        let p = paths(&root);
        let mut cf = FakeCf::new();
        let mut no_tenant = opening();
        no_tenant.as_object_mut().unwrap().remove("tenant_id");
        let e = dns_step(&mut cf, &p, &spec(), &mut no_tenant).unwrap_err();
        assert!(e.starts_with(sitedns::OWNER_UNKNOWN), "{e}");
        fs::write(
            p.rosters.join("t-aaa.json"),
            json!({"tenant_id": "t-aaa", "members": []}).to_string(),
        )
        .unwrap();
        let e = dns_step(&mut cf, &p, &spec(), &mut opening()).unwrap_err();
        assert!(e.starts_with(sitedns::OWNER_UNKNOWN), "{e}");
        // a roster file under the customer's name that is another customer's
        fs::write(
            p.rosters.join("t-aaa.json"),
            json!({"tenant_id": "t-bbb", "cloudflare_accounts": [ACC], "members": []}).to_string(),
        )
        .unwrap();
        let e = dns_step(&mut cf, &p, &spec(), &mut opening()).unwrap_err();
        assert!(e.starts_with(sitedns::OWNER_UNKNOWN), "{e}");
        // nothing was asked of Cloudflare
        assert!(cf.fed.is_empty());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn access_taken_away_says_so() {
        let root = scratch("revoked");
        let p = paths(&root);
        let mut cf = FakeCf::new();
        cf.zones_http = 403;
        let e = dns_step(&mut cf, &p, &spec(), &mut opening()).unwrap_err();
        assert!(e.starts_with(sitedns::ACCESS_REVOKED), "{e}");
        cf.zones_http = 401;
        let e = dns_step(&mut cf, &p, &spec(), &mut opening()).unwrap_err();
        assert!(e.starts_with(sitedns::KEY_REFUSED), "{e}");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_flexible_ssl_mode_stops_before_any_record() {
        let root = scratch("ssl");
        let p = paths(&root);
        let mut cf = FakeCf::new();
        cf.ssl = "flexible";
        let e = dns_step(&mut cf, &p, &spec(), &mut opening()).unwrap_err();
        assert!(e.starts_with(sitedns::SSL_MODE_NOT_FULL), "{e}");
        assert_eq!(cf.adds(), 0);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn an_add_that_does_not_read_back_is_not_done_and_was_journaled() {
        let root = scratch("lost");
        let p = paths(&root);
        let mut cf = FakeCf::new();
        cf.lose_adds = true;
        let mut rec = opening();
        let e = dns_step(&mut cf, &p, &spec(), &mut rec).unwrap_err();
        assert!(e.starts_with(sitedns::NOT_CONFIRMED), "{e}");
        // the record said what was being added before the add went out
        let s = saved(&p);
        assert_eq!(s["dns"]["adding"], json!([DOMAIN]));
        assert!(s["dns"].get("done").is_none());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn no_key_or_no_origin_asks_nothing() {
        let root = scratch("nokey");
        let p = paths(&root);
        let mut cf = FakeCf::new();
        for (text, word) in [
            ("CF_GLOBAL_KEY=x\nCF_ORIGIN_IPV4=192.0.2.10\n", KEY_UNSET),
            ("CF_AUTH_EMAIL=ops@example.test\nCF_ORIGIN_IPV4=192.0.2.10\n", KEY_UNSET),
            (&*format!("CF_AUTH_EMAIL=\"a\\\"b@example.test\"\nCF_GLOBAL_KEY={KEY}\nCF_ORIGIN_IPV4=192.0.2.10\n"), KEY_UNSET),
            (&*format!("CF_AUTH_EMAIL=ops@example.test\nCF_GLOBAL_KEY={KEY}\n"), sitedns::ORIGIN_UNSET),
            (&*format!("CF_AUTH_EMAIL=ops@example.test\nCF_GLOBAL_KEY={KEY}\nCF_ORIGIN_IPV4=10.0.0.2\n"), sitedns::ORIGIN_UNSET),
        ] {
            fs::write(&p.cloudflare, text).unwrap();
            let e = dns_step(&mut cf, &p, &spec(), &mut opening()).unwrap_err();
            assert!(e.starts_with(word), "{e}");
            assert!(!e.contains(KEY), "{e}");
        }
        fs::remove_file(&p.cloudflare).unwrap();
        let e = dns_step(&mut cf, &p, &spec(), &mut opening()).unwrap_err();
        assert!(e.starts_with(KEY_UNSET), "{e}");
        assert!(cf.fed.is_empty());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_call_off_the_list_is_refused_and_written_down() {
        let root = scratch("refused");
        let p = paths(&root);
        let mut fake = FakeCf::new();
        let (creds, _) = creds_from(&fs::read_to_string(&p.cloudflare).unwrap()).unwrap();
        let mut cf = Cf::new(&mut fake, creds, &p.cf_ledger, CODE);
        let odd = CfCall::ListZones {
            account: ACC.to_owned(),
            name: "example.com&per_page=1".to_owned(),
        };
        let e = cf.call(&odd, None).unwrap_err();
        assert!(e.starts_with(sitedns::CALL_NOT_ALLOWED), "{e}");
        // an add without the site's mark
        let e = cf
            .call(
                &CfCall::AddRecord { zone: ZID.to_owned() },
                Some(json!({"type": "A", "name": DOMAIN, "content": ORIGIN, "proxied": true, "ttl": 1, "comment": "x"}).to_string().as_str()),
            )
            .unwrap_err();
        assert!(e.starts_with(sitedns::CALL_NOT_ALLOWED), "{e}");
        assert!(fake.fed.is_empty());
        let lines = ledger(&p);
        assert_eq!(lines.len(), 2);
        assert!(lines.iter().all(|l| l["outcome"] == "refused"));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_ledger_that_cannot_be_written_stops_the_call() {
        let root = scratch("ledger");
        let mut p = paths(&root);
        // the ledger's place is a directory
        fs::create_dir_all(root.join("log").join("cloudflare.jsonl")).unwrap();
        p.cf_ledger = root.join("log").join("cloudflare.jsonl");
        let mut cf = FakeCf::new();
        assert!(dns_step(&mut cf, &p, &spec(), &mut opening()).is_err());
        assert!(cf.fed.is_empty());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn a_body_with_quotes_reaches_curl_as_written() {
        let (creds, _) = creds_from(&format!(
            "CF_AUTH_EMAIL=ops@example.test\nCF_GLOBAL_KEY={KEY}\n"
        ))
        .unwrap();
        let body = r#"{"comment":"a\"b\\c"}"#;
        let c = curl_config(
            &creds,
            &CfCall::AddRecord {
                zone: ZID.to_owned(),
            },
            Some(body),
        );
        assert!(
            c.contains(r#"data = "{\"comment\":\"a\\\"b\\\\c\"}""#),
            "{c}"
        );
        assert_eq!(c.lines().filter(|l| l.starts_with("data")).count(), 1);
    }

    #[test]
    fn s15_reads_the_zone_and_writes_nothing() {
        let root = scratch("s15");
        let mut p = paths(&root);
        let mut cf = FakeCf::new();
        cf.records.push(json!({"id": "d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1", "type": "A", "name": DOMAIN, "content": ORIGIN, "proxied": true, "comment": null}));
        assert_eq!(
            proxied_names(&mut cf, &p, &spec(), &opening()),
            Ok(sitedns::Proxied::All)
        );
        cf.records[1]["proxied"] = json!(false);
        assert_eq!(
            proxied_names(&mut cf, &p, &spec(), &opening()),
            Ok(sitedns::Proxied::Grey(vec![DOMAIN.to_owned()]))
        );
        // only listings were asked for
        assert_eq!((cf.adds(), cf.deletes()), (0, 0));
        assert!(cf.fed.iter().all(|c| c.contains("request = \"GET\"")));
        // the account's zones cannot be listed: an error, which verify shows as unchecked
        cf.zones_http = 403;
        let err = proxied_names(&mut cf, &p, &spec(), &opening()).unwrap_err();
        assert!(err.starts_with(sitedns::ACCESS_REVOKED), "{err}");
        // no key on the host: nothing is asked
        cf.zones_http = 200;
        let asked = cf.fed.len();
        p.cloudflare = root.join("nosuch.env");
        let err = proxied_names(&mut cf, &p, &spec(), &opening()).unwrap_err();
        assert!(err.starts_with(KEY_UNSET), "{err}");
        assert_eq!(cf.fed.len(), asked);
        // a second domain on another zone: unchecked, and that zone is not listed
        p.cloudflare = root.join("cloudflare.env");
        let two = SiteSpec::new(
            CODE,
            131,
            41,
            &[DOMAIN, "shop.example.org"],
            true,
            "zh_TW",
            aib_cloud::site::Limits::START,
        )
        .unwrap();
        let listed = cf.fed.iter().filter(|c| c.contains("/dns_records")).count();
        assert_eq!(
            proxied_names(&mut cf, &p, &two, &opening()),
            Ok(sitedns::Proxied::Unchecked(sitedns::MULTI_ZONE))
        );
        assert_eq!(
            cf.fed.iter().filter(|c| c.contains("/dns_records")).count(),
            listed
        );
        let _ = fs::remove_dir_all(&root);
    }
}
