// SPDX-License-Identifier: Apache-2.0
//! A site's caches cleared at its customer's request (E10 7.6, `cache flush`).
//!
//! * **Their site only.** Each site has its own CT, so WordPress's object cache
//!   (`wp cache flush`) and the page cache plugin (FlyingPress) are cleared inside that
//!   CT, as the site's own user, for the site's own path. Nothing else on the host is
//!   touched.
//! * **Cloudflare by name, never the zone.** When the host has its Cloudflare key, the
//!   cache of the site's own names is cleared in the customer's zone: one
//!   [`sitedns::CfCall::PurgeHosts`] whose body names those hosts and nothing else
//!   ([`sitedns::purge_body`]). A clear of a whole zone, of files or of prefixes is not on
//!   the list of calls and cannot be made (c477a L7: a cache shared by sites must not be
//!   cleared for one of them). No key on the host is [`CF_SKIPPED`], not a failure.
//! * **A failure blocks nothing.** Each part answers its own word; the operation itself is
//!   always done, so a Cloudflare that does not answer does not leave the site blocked for
//!   further operations the way a failed operation does. The exit status says whether
//!   every part that was there worked.

use std::fs;

use aib_cloud::site::SiteSpec;
use aib_cloud::sitedns::{self, CfCall};
use serde_json::{json, Value};

use crate::opstore;
use crate::sitecreatecmd::{HostPaths, Runner};
use crate::siteidentity::wp;

/// The operation's name in the journal.
pub const COMMAND: &str = "site.cache-flush";
/// The page cache plugin of our WordPress package.
pub const PAGE_CACHE_PLUGIN: &str = "flying-press";
/// The host has no Cloudflare key: that part was not asked for.
pub const CF_SKIPPED: &str = "cf_skipped";
/// The shortest gap between two clears of one site, in seconds (33e28 L1). To be tuned
/// with Cloudflare's rate answers once it is used for real (33e28 N2).
pub const MIN_GAP_S: u64 = 60;
/// A clear asked again before [`MIN_GAP_S`] has passed since the last one of that site.
pub const TOO_SOON: &str = "cache_flush_too_soon";

/// Seconds still to wait before `target` may be cleared again, from the last entry of
/// every journal on the host; `None` when it may be cleared now. Only clears of that same
/// site count.
pub fn wait_left(lasts: &[aib_cloud::op::Entry], target: &str, now: u64) -> Option<u64> {
    let last = lasts
        .iter()
        .filter(|e| e.command == COMMAND && e.target == target)
        .map(|e| e.at)
        .max()?;
    let next = last.saturating_add(MIN_GAP_S);
    next.checked_sub(now).filter(|left| *left > 0)
}

/// What each part of a clear did, as the words the host answers with.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Cleared {
    /// `flushed` or `failed`.
    pub wordpress: &'static str,
    /// `purged`, `absent` (the plugin is not active) or `failed`.
    pub page_cache: &'static str,
    /// `purged`, [`CF_SKIPPED`] or `failed`.
    pub cloudflare: &'static str,
}

impl Cleared {
    /// 0 when every part that was there worked, 3 otherwise.
    pub fn exit(&self) -> u8 {
        let failed = [self.wordpress, self.page_cache, self.cloudflare].contains(&"failed");
        if failed {
            3
        } else {
            0
        }
    }

    /// The host's answer, one JSON line.
    pub fn to_json(&self) -> Value {
        json!({
            "wordpress": self.wordpress,
            "page_cache": self.page_cache,
            "cloudflare": self.cloudflare,
        })
    }
}

/// WordPress's object cache, in the site's CT.
fn wordpress(runner: &mut impl Runner, spec: &SiteSpec) -> &'static str {
    match runner.run(&wp(spec, &["cache", "flush"])) {
        Ok(_) => "flushed",
        Err(_) => "failed",
    }
}

/// The page cache plugin, in the site's CT: only when it is active. Whether it is comes
/// from the list of active plugins: a list WordPress does not give is `failed`, never
/// `absent` (33e28 L2: `plugin is-active` answers a broken WordPress and an inactive
/// plugin with the same exit 1).
fn page_cache(runner: &mut impl Runner, spec: &SiteSpec) -> &'static str {
    let active = match runner.run(&wp(
        spec,
        &["plugin", "list", "--status=active", "--field=name"],
    )) {
        Ok(names) => names.lines().any(|n| n.trim() == PAGE_CACHE_PLUGIN),
        Err(_) => return "failed",
    };
    if !active {
        return "absent";
    }
    match runner.run(&wp(spec, &["flying-press", "purge-everything"])) {
        Ok(_) => "purged",
        Err(_) => "failed",
    }
}

/// The site's own names at Cloudflare, when the host has its key.
fn cloudflare(
    runner: &mut impl Runner,
    paths: &HostPaths,
    spec: &SiteSpec,
    record: &Value,
) -> Result<&'static str, String> {
    let text = match fs::read_to_string(&paths.cloudflare) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(CF_SKIPPED),
        Err(e) => return Err(format!("{}: {}", paths.cloudflare.display(), e.kind())),
    };
    let (creds, _) = crate::cfdns::creds_from(&text)?;
    let domain = spec.hosts.first().cloned().ok_or("the site has no name")?;
    let accounts = crate::cfdns::customer_accounts(paths, record)?;
    let mut cf = crate::cfdns::Cf::new(runner, creds, &paths.cf_ledger, &spec.code);
    let zones = crate::cfdns::customer_zones(&mut cf, &accounts, &domain)?;
    let zone = sitedns::pick_zone(&domain, Some(accounts.as_slice()), Some(zones.as_slice()))
        .map_err(|w| format!("{w}: {domain}"))?;
    let names: Vec<&str> = spec.hosts.iter().map(String::as_str).collect();
    let body = sitedns::purge_body(&zone.name, &names)
        .ok_or_else(|| format!("none of the site's names is in zone {}", zone.name))?;
    let (http, answer) = cf.call(
        &CfCall::PurgeHosts {
            zone: zone.id.clone(),
        },
        Some(body.as_str()),
    )?;
    if http == 200 && answer.get("success") == Some(&Value::Bool(true)) {
        Ok("purged")
    } else {
        Err(format!("the clear was not answered as done (http {http})"))
    }
}

/// Clear the site's caches. `record` is the site's record (its customer names the
/// Cloudflare accounts). Why a part failed stays on the host's standard error.
pub fn clear(
    runner: &mut impl Runner,
    paths: &HostPaths,
    spec: &SiteSpec,
    record: &Value,
) -> Cleared {
    let wordpress = wordpress(runner, spec);
    let page_cache = page_cache(runner, spec);
    let cloudflare = match cloudflare(runner, paths, spec, record) {
        Ok(word) => word,
        Err(why) => {
            eprintln!("cloudflare: {why}");
            "failed"
        }
    };
    Cleared {
        wordpress,
        page_cache,
        cloudflare,
    }
}

/// The customer's `cache flush` for their site `code`, as an operation the customer key
/// approves. The site is the one the key's entry or the customer's record names.
pub fn customer_clear<R: Runner>(
    place: &opstore::OpPlace,
    paths: &HostPaths,
    code: &str,
    runner: &mut R,
) -> Result<Cleared, String> {
    customer_clear_at(place, paths, code, runner, crate::probe::unix_now())
}

/// [`customer_clear`] at the time `now`: a clear of the same site less than
/// [`MIN_GAP_S`] after the last one is refused ([`TOO_SOON`]) before anything runs. The
/// gap is judged under the host lock, from the journals as they are then.
fn customer_clear_at<R: Runner>(
    place: &opstore::OpPlace,
    paths: &HostPaths,
    code: &str,
    runner: &mut R,
    now: u64,
) -> Result<Cleared, String> {
    if !aib_cloud::site::valid_code(code) {
        return Err(format!("site_unknown: {code:?} is not a site code"));
    }
    let path = paths.records.join(format!("{code}.json"));
    let record: Value = fs::read_to_string(&path)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .ok_or_else(|| format!("site_unknown: {} could not be read", path.display()))?;
    let spec =
        aib_cloud::site::spec_from_record(&record).map_err(|e| format!("site_unknown: {e:?}"))?;
    if spec.code != code {
        return Err(format!("site_unknown: {} is not {code}'s", path.display()));
    }
    let domain = spec
        .hosts
        .first()
        .cloned()
        .ok_or_else(|| format!("site_unknown: {code} has no name"))?;
    let on = crate::sitecreatecmd::target(&domain);
    let ops_dir = place.ops_dir.clone();
    let gate_on = on.clone();
    opstore::run_op_prepared(
        place,
        COMMAND,
        &on,
        opstore::BY_CUSTOMER_KEY,
        now,
        move || {
            // under the host lock, so a clear that went through between this request's
            // arrival and the lock is in the journals read here (c477a L1): two requests
            // for one site cannot both pass the gap by looking before either wrote
            let lasts = opstore::last_entries(&ops_dir)?;
            if let Some(left) = wait_left(&lasts, &gate_on, now) {
                return Err(format!(
                    "{TOO_SOON}: {code} was cleared less than {MIN_GAP_S}s ago; again in {left}s"
                ));
            }
            let plan = json!({ "command": COMMAND, "domain": domain, "site": spec.code });
            Ok(Some((plan, (spec, record, runner))))
        },
        |_, (spec, record, runner): (SiteSpec, Value, &mut R)| {
            Ok(clear(runner, paths, &spec, &record))
        },
    )?
    .ok_or_else(|| "cache_flush_failed: nothing was done".to_owned())
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use std::path::Path;

    const ACC: &str = "0123456789abcdef0123456789abcdef";
    const ZID: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const KEY: &str = "0123456789abcdef0123456789abcdef01234";
    const CODE: &str = "s-cache1";

    /// A host that answers wp-cli and curl from what it is told, and remembers what it ran
    /// and what curl was fed.
    struct Fake {
        ran: Vec<Vec<String>>,
        fed: Vec<String>,
        wp_fails: bool,
        list_fails: bool,
        plugin_active: bool,
        purge_fails: bool,
        cf_http: u16,
        /// Set in the test that holds the lock: `run` tells it has reached the WordPress
        /// flush (under the lock) and waits there until told to go on.
        hold: Option<(std::sync::mpsc::Sender<()>, std::sync::mpsc::Receiver<()>)>,
    }

    impl Fake {
        fn new() -> Self {
            Fake {
                ran: Vec::new(),
                fed: Vec::new(),
                wp_fails: false,
                list_fails: false,
                plugin_active: true,
                purge_fails: false,
                cf_http: 200,
                hold: None,
            }
        }
        fn wp_calls(&self) -> Vec<String> {
            self.ran
                .iter()
                .filter(|a| a.iter().any(|w| w.starts_with("--path=")))
                .map(|a| a.join(" "))
                .collect()
        }
    }

    impl Runner for Fake {
        fn run(&mut self, argv: &[String]) -> Result<String, String> {
            self.ran.push(argv.to_vec());
            let line = argv.join(" ");
            if line.ends_with(" cache flush") {
                if let Some((entered, go)) = &self.hold {
                    entered.send(()).unwrap();
                    go.recv().unwrap();
                }
                return if self.wp_fails {
                    Err("wp failed".to_owned())
                } else {
                    Ok("Success: The cache was flushed.".to_owned())
                };
            }
            if line.contains(" plugin list --status=active --field=name") {
                return if self.list_fails {
                    Err("Error: WordPress is not installed".to_owned())
                } else if self.plugin_active {
                    Ok(format!("akismet\n{PAGE_CACHE_PLUGIN}\n"))
                } else {
                    Ok("akismet\nflying-pages\n".to_owned())
                };
            }
            if line.ends_with(" flying-press purge-everything") {
                return if self.purge_fails {
                    Err("exit 1".to_owned())
                } else {
                    Ok(String::new())
                };
            }
            Err(format!("unexpected: {line}"))
        }
        fn run_input(&mut self, argv: &[String], input: &[u8]) -> Result<String, String> {
            self.ran.push(argv.to_vec());
            let config = String::from_utf8_lossy(input).into_owned();
            self.fed.push(config.clone());
            if config.contains("/zones?account.id=") {
                let zones = if config.contains("&name=example.com\"") {
                    json!({"success": true, "result": [
                        {"id": ZID, "name": "example.com", "status": "active", "account": {"id": ACC}}]})
                } else {
                    json!({"success": true, "result": []})
                };
                return Ok(format!("{zones}\n200"));
            }
            if config.contains("/purge_cache\"") {
                return Ok(format!(
                    "{}\n{}",
                    json!({"success": self.cf_http == 200}),
                    self.cf_http
                ));
            }
            Err(format!("unexpected call: {config}"))
        }
    }

    fn spec(hosts: &[&str]) -> SiteSpec {
        SiteSpec::new(
            CODE,
            131,
            31,
            hosts,
            true,
            "zh_TW",
            aib_cloud::site::Limits::START,
        )
        .unwrap()
    }

    /// The site's record as the host keeps it, with its customer.
    fn record(hosts: &[&str]) -> Value {
        let mut r = aib_cloud::site::record(&spec(hosts), "2026-11-12T00:00:00Z");
        r["tenant_id"] = json!("t-cache");
        r
    }

    fn scratch(name: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("aib-cache-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    /// A host in a scratch directory: the site's record, its customer's roster, and the
    /// Cloudflare key when `key` is set.
    fn paths(root: &Path, key: bool) -> HostPaths {
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
        for d in [&p.records, &p.rosters] {
            fs::create_dir_all(d).unwrap();
        }
        fs::write(
            p.records.join(format!("{CODE}.json")),
            record(&["shop.example.com"]).to_string(),
        )
        .unwrap();
        fs::write(
            p.rosters.join("t-cache.json"),
            json!({"tenant_id": "t-cache", "cloudflare_accounts": [ACC], "members": []})
                .to_string(),
        )
        .unwrap();
        if key {
            fs::write(
                &p.cloudflare,
                format!("CF_AUTH_EMAIL=\"ops@example.test\"\nCF_GLOBAL_KEY={KEY}\n"),
            )
            .unwrap();
        }
        p
    }

    /// An operation place in a scratch directory, with this process in the fake `/proc`.
    fn place(d: &Path) -> opstore::OpPlace {
        fs::write(d.join("boot_id"), "0f0e0d0c-0b0a-4908-8706-050403020100\n").unwrap();
        let me = d.join("proc").join(std::process::id().to_string());
        fs::create_dir_all(&me).unwrap();
        let filler = (3..22).map(|i| i.to_string()).collect::<Vec<_>>().join(" ");
        fs::write(
            me.join("stat"),
            format!(
                "{} (aib-cloud-host) {filler} 4242 0 0\n",
                std::process::id()
            ),
        )
        .unwrap();
        fs::create_dir_all(d.join("ops")).unwrap();
        opstore::OpPlace {
            ops_dir: d.join("ops"),
            lock: opstore::LockPaths {
                lock: d.join("host.lock"),
                proc_root: d.join("proc"),
                boot_id: d.join("boot_id"),
            },
            fingerprint: "f".repeat(64),
        }
    }

    /// The bodies of the clears Cloudflare was asked for.
    fn purge_bodies(fake: &Fake) -> Vec<Value> {
        fake.fed
            .iter()
            .filter(|c| c.contains("/purge_cache\""))
            .filter_map(|c| {
                let line = c.lines().find_map(|l| l.strip_prefix("data = \""))?;
                let text = line.strip_suffix('"')?.replace("\\\"", "\"");
                serde_json::from_str(&text).ok()
            })
            .collect()
    }

    #[test]
    fn everything_is_cleared_in_the_sites_own_ct_and_names() {
        let p = paths(&scratch("all"), true);
        let mut fake = Fake::new();
        let hosts = ["shop.example.com", "www.shop.example.com", "other.test"];
        let done = clear(&mut fake, &p, &spec(&hosts), &record(&hosts));
        assert_eq!(
            done,
            Cleared {
                wordpress: "flushed",
                page_cache: "purged",
                cloudflare: "purged",
            }
        );
        assert_eq!(done.exit(), 0);
        // every wp call is the site's own: its CT, its user, its path
        let wp = fake.wp_calls();
        assert_eq!(wp.len(), 3, "{wp:?}");
        for call in &wp {
            assert!(call.starts_with("pct exec 131 -- "), "{call}");
            assert!(call.contains(&format!(" sudo -u {CODE} -H ")), "{call}");
            assert!(
                call.contains(&format!(" --path=/var/www/{CODE} ")),
                "{call}"
            );
        }
        // Cloudflare: the site's names in the zone, never the zone itself, and never a
        // name of another zone
        let bodies = purge_bodies(&fake);
        assert_eq!(bodies.len(), 1, "{:?}", fake.fed);
        assert_eq!(
            bodies[0],
            json!({"hosts": ["shop.example.com", "www.shop.example.com"]})
        );
        assert!(fake.fed.iter().all(|c| !c.contains("purge_everything")));
        assert!(fake
            .fed
            .iter()
            .any(|c| c.contains(&format!("/zones/{ZID}/purge_cache\""))));
        let ledger = fs::read_to_string(&p.cf_ledger).unwrap();
        assert!(ledger.contains("/purge_cache"));
        assert!(!ledger.contains(KEY));
    }

    #[test]
    fn no_key_on_the_host_skips_cloudflare_and_is_not_a_failure() {
        let p = paths(&scratch("nokey"), false);
        let mut fake = Fake::new();
        let hosts = ["shop.example.com"];
        let done = clear(&mut fake, &p, &spec(&hosts), &record(&hosts));
        assert_eq!(done.cloudflare, CF_SKIPPED);
        assert_eq!(done.exit(), 0);
        assert!(fake.fed.is_empty());
    }

    #[test]
    fn a_page_cache_plugin_that_is_not_active_is_left_alone() {
        let p = paths(&scratch("noplugin"), false);
        let mut fake = Fake::new();
        fake.plugin_active = false;
        let hosts = ["shop.example.com"];
        let done = clear(&mut fake, &p, &spec(&hosts), &record(&hosts));
        assert_eq!(done.page_cache, "absent");
        assert_eq!(done.exit(), 0);
        assert!(fake
            .wp_calls()
            .iter()
            .all(|c| !c.contains("purge-everything")));
    }

    #[test]
    fn a_part_that_fails_is_said_and_the_rest_still_runs() {
        let p = paths(&scratch("fails"), true);
        let mut fake = Fake::new();
        fake.wp_fails = true;
        fake.purge_fails = true;
        fake.cf_http = 500;
        let hosts = ["shop.example.com"];
        let done = clear(&mut fake, &p, &spec(&hosts), &record(&hosts));
        assert_eq!(
            done,
            Cleared {
                wordpress: "failed",
                page_cache: "failed",
                cloudflare: "failed",
            }
        );
        assert_eq!(done.exit(), 3);
        assert_eq!(
            done.to_json(),
            json!({"wordpress": "failed", "page_cache": "failed", "cloudflare": "failed"})
        );
    }

    #[test]
    fn a_site_whose_names_are_in_no_zone_of_the_customer_clears_nothing_there() {
        let p = paths(&scratch("nozone"), true);
        let mut fake = Fake::new();
        let hosts = ["shop.elsewhere.test"];
        let done = clear(&mut fake, &p, &spec(&hosts), &record(&hosts));
        assert_eq!(done.cloudflare, "failed");
        assert!(purge_bodies(&fake).is_empty());
    }

    #[test]
    fn the_customer_clear_is_journaled_as_the_customer_keys_and_never_blocks() {
        let d = scratch("journal");
        let p = paths(&d, false);
        let place = place(&d);
        let mut fake = Fake::new();
        fake.wp_fails = true;
        // the journal stamps its entries with the host's clock, so the test starts there
        let t = crate::probe::unix_now();
        let done = customer_clear_at(&place, &p, CODE, &mut fake, t).unwrap();
        assert_eq!(done.wordpress, "failed");
        // a failed part does not fail the operation: the next one is not blocked
        let again =
            customer_clear_at(&place, &p, CODE, &mut Fake::new(), t + MIN_GAP_S + 5).unwrap();
        assert_eq!(again.exit(), 0);
        assert!(opstore::CUSTOMER_COMMANDS.contains(&COMMAND));
        // a code that is not a site's, or a site without a record, is refused before
        // anything runs
        let mut idle = Fake::new();
        assert!(customer_clear(&place, &p, "../x", &mut idle).is_err());
        assert!(customer_clear(&place, &p, "s-other1", &mut idle).is_err());
        assert!(idle.ran.is_empty());
    }

    #[test]
    fn a_plugin_list_that_cannot_be_read_is_failed_not_absent() {
        let p = paths(&scratch("nolist"), false);
        let mut fake = Fake::new();
        fake.list_fails = true;
        let hosts = ["shop.example.com"];
        let done = clear(&mut fake, &p, &spec(&hosts), &record(&hosts));
        assert_eq!(done.page_cache, "failed");
        assert_eq!(done.exit(), 3);
        assert!(fake
            .wp_calls()
            .iter()
            .all(|c| !c.contains("purge-everything")));
        // a name that only looks like it is not the plugin
        let mut near = Fake::new();
        near.plugin_active = false;
        let done = clear(&mut near, &p, &spec(&hosts), &record(&hosts));
        assert_eq!(done.page_cache, "absent");
    }

    #[test]
    fn a_second_clear_of_a_site_inside_the_gap_is_refused_before_anything_runs() {
        let d = scratch("gap");
        let p = paths(&d, false);
        let place = place(&d);
        // the journal stamps its entries with the host's clock, so the test starts there
        let t = crate::probe::unix_now();
        customer_clear_at(&place, &p, CODE, &mut Fake::new(), t).unwrap();
        let mut idle = Fake::new();
        let err = customer_clear_at(&place, &p, CODE, &mut idle, t + MIN_GAP_S - 1).unwrap_err();
        assert!(err.starts_with(TOO_SOON), "{err}");
        assert!(idle.ran.is_empty());
        // once the gap has passed it runs again
        customer_clear_at(&place, &p, CODE, &mut Fake::new(), t + MIN_GAP_S + 5).unwrap();
        // the gap is the site's own: another site's clear does not count
        let other = aib_cloud::op::Entry {
            op: String::from("x"),
            seq: 3,
            at: t,
            state: aib_cloud::op::State::Verified,
            command: String::from(COMMAND),
            target: String::from("domain:other.example.org"),
            plan_sha256: "0".repeat(64),
            code: String::from("ok"),
            approval: String::new(),
        };
        assert_eq!(
            wait_left(
                core::slice::from_ref(&other),
                "domain:shop.example.com",
                t + 1
            ),
            None
        );
        assert_eq!(
            wait_left(&[other], "domain:other.example.org", t + 1),
            Some(MIN_GAP_S - 1)
        );
    }

    #[test]
    fn a_clear_arriving_while_another_holds_the_lock_runs_nothing_and_is_too_soon_after() {
        let d = scratch("race");
        let p = paths(&d, false);
        let here = place(&d);
        let t = crate::probe::unix_now();
        let (entered_tx, entered_rx) = std::sync::mpsc::channel();
        let (go_tx, go_rx) = std::sync::mpsc::channel();
        let dir = d.clone();
        let first = std::thread::spawn(move || {
            let (place, p2) = (place(&dir), paths(&dir, false));
            let mut held = Fake::new();
            held.hold = Some((entered_tx, go_rx));
            customer_clear_at(&place, &p2, CODE, &mut held, t)
        });
        // the first clear is under the lock, at its WordPress step
        entered_rx.recv().unwrap();
        // a second request for the same site, at the same moment: it runs nothing
        let mut idle = Fake::new();
        assert!(customer_clear_at(&here, &p, CODE, &mut idle, t).is_err());
        assert!(idle.ran.is_empty());
        go_tx.send(()).unwrap();
        assert_eq!(first.join().unwrap().unwrap().exit(), 0);
        // and once the first is done it is too soon, judged from the journal the first wrote
        let err = customer_clear_at(&here, &p, CODE, &mut idle, t).unwrap_err();
        assert!(err.starts_with(TOO_SOON), "{err}");
        assert!(idle.ran.is_empty());
    }
}
