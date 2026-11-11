// SPDX-License-Identifier: Apache-2.0
//! The DNS step of opening a site (B5, [`aib_cloud::sitecreate::CreateStep::Dns`]): find
//! the customer's zone at Cloudflare, read what is under the site's names, add the
//! proxied records carrying the site's mark, and read them back. Every judgement is
//! [`aib_cloud::sitedns`]'s; this file asks and writes down.
//!
//! * **The key.** [`CLOUDFLARE_ENV`] (root only), the same file and names as the
//!   hand-run DNS tools: `CF_AUTH_EMAIL`, `CF_GLOBAL_KEY`, and `CF_ORIGIN_IPV4`, the
//!   host's own address the records point at. The key is handed to `curl` on its standard
//!   input (`curl -K -`): it is never in an argument list, never printed, never in the
//!   ledger, the record or an error.
//! * **Only five calls.** Every call is written out and checked by
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
}

impl<'a, R: Runner> Cf<'a, R> {
    pub fn new(runner: &'a mut R, creds: Creds, ledger: &'a Path, site: &'a str) -> Self {
        Cf {
            runner,
            creds,
            ledger,
            site,
        }
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
        if !sitedns::allowed(call.method(), &call.path(), body) {
            self.ledger_line(&mut file, call, json!("refused"))?;
            return Err(format!(
                "{}: {} {} is not one of the five calls",
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
fn customer_accounts(paths: &HostPaths, record: &Value) -> Result<Vec<String>, String> {
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
fn customer_zones<R: Runner>(
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
fn listing<R: Runner>(
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
    let origin = origin.filter(|o| sitedns::valid_origin(o)).ok_or_else(|| {
        format!(
            "{}: CF_ORIGIN_IPV4 is not set or not a public address",
            sitedns::ORIGIN_UNSET
        )
    })?;
    let domain = spec.hosts.first().cloned().ok_or("the site has no name")?;
    let accounts = customer_accounts(paths, record)?;
    let mut cf = Cf::new(runner, creds, &paths.cf_ledger, &spec.code);
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
    let add = match sitedns::plan(&names, before.as_deref(), &spec.code, &origin) {
        DnsPlan::Done => Vec::new(),
        DnsPlan::Stop(w) => return Err(format!("{w}: {} in zone {}", names.join(","), zone.name)),
        DnsPlan::Add(list) => list,
    };
    if !add.is_empty() {
        record["dns"] = json!({
            "zone": zone.name,
            "adding": add.iter().map(|r| r.name.as_str()).collect::<Vec<_>>(),
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
        if sitedns::plan(&names, after.as_deref(), &spec.code, &origin) != DnsPlan::Done {
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
        "done": crate::probe::unix_now(),
    });
    crate::sitecreatecmd::write_record(&paths.creating, &spec.code, record)
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
            }
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
}
