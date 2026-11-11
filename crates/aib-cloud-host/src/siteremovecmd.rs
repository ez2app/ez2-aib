// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host site remove <code> --address <main address> --plan` — the read-only
//! half of removing a site. It reads the site's record, checks that the code and the main
//! address typed are the record's, probes each of the nine steps of
//! [`aib_cloud::siteremove`] on this host and prints what would be done next, sealed like
//! every other plan. **Nothing is changed**: without `--plan` the command refuses.
//!
//! What it does not look at yet is said in the plan, not guessed: whether the site's paid
//! plugins are still activated at their vendors is answered inside the site, and that is
//! not wired here. The licence step is therefore "could not tell" unless the person
//! passes `--licences-left-at-vendor`, which is written into the plan.
//!
//! The archive of a site is looked for under [`ARCHIVES`]. That directory is outside the
//! one the daily backup rotates, and the archive tool must be able to enter it as the
//! container's own root, which is an unprivileged id on the host: a first removal by hand
//! stopped at exactly that, with the archive placed where only the host's root could go.

use std::fs;
use std::path::Path;
use std::process::Command;
use std::time::Duration;

use aib_cloud::site::{customer_ssh_port, SiteSpec};
use aib_cloud::sitecreate::Probe;
use aib_cloud::siteremove::{self, Next, RemoveStep, STEPS};
use serde_json::{json, Value};

use crate::opstore;
use crate::probe::run_capture;

const RECORDS: &str = "/var/lib/ez2-cloud/sites";
const REMOVED_RECORDS: &str = "/var/lib/ez2-cloud/sites-removed";
/// Where the archives of removed sites are kept.
pub const ARCHIVES: &str = "/var/lib/vz/ez2-removed";
const EXPORT_LIST: &str = "/etc/ez2-cloud/sites.list";
const NETWORK_FILE: &str = "/etc/network/interfaces";

/// What was read from the host for one site. `None` is "could not be read".
#[derive(Default)]
pub struct Seen {
    pub entry_config: Option<String>,
    pub container_list: Option<String>,
    /// `Some(true)` running, `Some(false)` stopped; only asked when the container is listed.
    pub running: Option<bool>,
    /// Whether a disk of the container is still in the storage.
    pub disk: Option<bool>,
    pub archive: Option<Probe>,
    pub live_rules: Option<String>,
    pub network_file: Option<String>,
    pub firewall_file: Option<bool>,
    pub export_list: Option<String>,
    /// Whether the record is still in the records directory.
    pub record_in_place: Option<bool>,
}

fn known(answer: Option<bool>, done_when: bool) -> Probe {
    match answer {
        None => Probe::Unknown,
        Some(v) if v == done_when => Probe::Done,
        Some(_) => Probe::NotDone,
    }
}

/// One probe per step, in [`STEPS`] order. Separate from [`cmd`] so it can be tested
/// without a host.
pub fn probes(spec: &SiteSpec, seen: &Seen, licences_wired: Option<Probe>) -> [Probe; 9] {
    let listed = siteremove::container_listed(seen.container_list.as_deref(), spec.ct);
    let access = match customer_ssh_port(spec.ct) {
        Some(port) => siteremove::access_probe(
            seen.live_rules.as_deref(),
            seen.network_file.as_deref(),
            port,
            &spec.ip(),
        ),
        None => Probe::Unknown,
    };
    let stopped = match listed {
        None => Probe::Unknown,
        Some(false) => Probe::Done,
        Some(true) => known(seen.running, false),
    };
    let destroyed = match (listed, seen.disk) {
        (Some(false), Some(false)) => Probe::Done,
        (Some(true), Some(_)) | (Some(false), Some(true)) => Probe::NotDone,
        _ => Probe::Unknown,
    };
    [
        siteremove::unserve_probe(seen.entry_config.as_deref(), &spec.code, spec.primary()),
        seen.archive.unwrap_or(Probe::Unknown),
        licences_wired.unwrap_or(Probe::Unknown),
        access,
        stopped,
        destroyed,
        known(seen.firewall_file, false),
        siteremove::lists_probe(seen.export_list.as_deref(), &spec.code),
        known(seen.record_in_place, false),
    ]
}

fn probe_word(p: Probe) -> &'static str {
    match p {
        Probe::Done => "done",
        Probe::NotDone => "not_done",
        Probe::Unknown => "unknown",
    }
}

/// The plan document. Separate from [`cmd`] so it can be tested without a host.
pub fn plan_doc(spec: &SiteSpec, probes: &[Probe; 9], licences_left_at_vendor: bool) -> Value {
    let next = siteremove::next(probes, licences_left_at_vendor);
    let (next_word, next_step, stop) = match next {
        Next::Run(s) => ("run", Some(s), None),
        Next::Finished => ("finished", None, None),
        Next::Stop(why, s) => ("stop", Some(s), Some(why)),
    };
    json!({
        "plan": "site-remove",
        "site": spec.code,
        "ct": spec.ct,
        "address": spec.primary(),
        "level": "L2",
        "licences_left_at_vendor": licences_left_at_vendor,
        "steps": STEPS.iter().zip(probes).map(|(s, p)| json!({
            "step": s.as_str(),
            "now": probe_word(*p),
            "needs_archive": siteremove::needs_archive(*s),
        })).collect::<Vec<_>>(),
        "next": next_word,
        "next_step": next_step.map(RemoveStep::as_str),
        "stop": stop,
        "may_call_off": siteremove::may_call_off_now(next),
        "archives": ARCHIVES,
        "on_failure": "stop where it failed; every step is safe to do again; after the licence step the only way is forward",
        "not_looked_at": ["licences at the vendors (answered inside the site; not wired to this command yet)", "DNS records of the site's addresses", "the id ranges given to the container"],
    })
}

fn capture(program: &str, args: &[&str], seconds: u64) -> Option<String> {
    let mut cmd = Command::new(program);
    cmd.args(args);
    run_capture(&mut cmd, Duration::from_secs(seconds)).ok()
}

fn read_text(path: &str) -> Option<String> {
    fs::read_to_string(path).ok()
}

/// `Some(false)` only when the path is known not to be there.
fn exists(path: &str) -> Option<bool> {
    match fs::symlink_metadata(path) {
        Ok(_) => Some(true),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Some(false),
        Err(_) => None,
    }
}

/// The newest archive of container `ct` under `dir`, checked: the compressed file is
/// whole and holds the container's own configuration. No archive is "not done"; an
/// archive that does not check out, or a directory that cannot be read, is "could not
/// tell" and is left for a person.
fn archive_probe(dir: &Path, ct: u32) -> Probe {
    let entries = match fs::read_dir(dir) {
        Ok(e) => e,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Probe::NotDone,
        Err(_) => return Probe::Unknown,
    };
    let prefix = format!("vzdump-lxc-{ct}-");
    let mut names: Vec<String> = Vec::new();
    for entry in entries {
        let Ok(entry) = entry else {
            return Probe::Unknown;
        };
        let name = entry.file_name().to_string_lossy().into_owned();
        if name.starts_with(&prefix) && name.ends_with(".tar.zst") {
            names.push(name);
        }
    }
    names.sort();
    let Some(newest) = names.last() else {
        return Probe::NotDone;
    };
    let path = dir.join(newest);
    let Some(path) = path.to_str() else {
        return Probe::Unknown;
    };
    let whole = capture("zstd", &["-tq", path], 600).is_some();
    let own = capture(
        "tar",
        &["--zstd", "-tf", path, "./etc/vzdump/pct.conf"],
        600,
    )
    .is_some();
    if whole && own {
        Probe::Done
    } else {
        Probe::Unknown
    }
}

fn see(spec: &SiteSpec, records: &str) -> Seen {
    let ct = format!("{}", spec.ct);
    let container_list = capture("pct", &["list"], 20);
    let listed = siteremove::container_listed(container_list.as_deref(), spec.ct);
    let running = if listed == Some(true) {
        match capture("pct", &["status", &ct], 20)
            .as_deref()
            .map(str::trim)
        {
            Some("status: running") => Some(true),
            Some("status: stopped") => Some(false),
            _ => None,
        }
    } else {
        None
    };
    let disk = capture("pvesm", &["list", "local-lvm", "--vmid", &ct], 30).and_then(|t| {
        t.lines()
            .next()
            .is_some_and(|h| h.starts_with("Volid"))
            .then(|| t.contains(&format!("vm-{ct}-disk")))
    });
    Seen {
        entry_config: crate::probe::edge_caddyfile().ok(),
        container_list,
        running,
        disk,
        archive: Some(archive_probe(
            &Path::new(ARCHIVES).join(&spec.code),
            spec.ct,
        )),
        live_rules: capture("iptables", &["-w", "-t", "nat", "-S", "PREROUTING"], 20),
        network_file: read_text(NETWORK_FILE),
        firewall_file: exists(&format!("/etc/pve/firewall/{ct}.fw")),
        export_list: read_text(EXPORT_LIST),
        record_in_place: exists(&format!("{records}/{}.json", spec.code)),
    }
}

pub fn cmd(args: &[String]) -> Result<u8, String> {
    let code = args
        .first()
        .filter(|a| !a.starts_with("--"))
        .ok_or("needs <site code>")?
        .clone();
    let mut records = RECORDS.to_owned();
    let mut address: Option<String> = None;
    let mut plan_only = false;
    let mut left = false;
    let mut it = args[1..].iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--plan" => plan_only = true,
            "--licences-left-at-vendor" => left = true,
            "--records" => records = it.next().ok_or("--records needs a dir")?.clone(),
            "--address" => {
                address = Some(
                    it.next()
                        .ok_or("--address needs the site's main address")?
                        .clone(),
                );
            }
            other => return Err(format!("unknown argument {other}")),
        }
    }
    if !plan_only {
        return Err(
            "only --plan is available: removing needs the operation journal and an approval, which are not wired to this command yet"
                .to_owned(),
        );
    }
    let address = address.ok_or("removing a site needs --address <its main address>")?;
    if exists(&format!("{records}/{code}.json")) == Some(false)
        && exists(&format!("{REMOVED_RECORDS}/{code}.json")) == Some(true)
    {
        println!(
            "{}",
            // the record was moved aside: said as found, and said that nothing was probed
            json!({ "plan": "site-remove", "site": code, "already_removed": true, "probed": false })
        );
        return Ok(0);
    }
    let spec = crate::quarantinecmd::load_spec(&code, &records)?;
    if !siteremove::confirmed(&spec.code, spec.primary(), &code, &address) {
        // the same words whether the code or the address is the one that is off
        return Err(
            "the site code and the main address given are not those of one site".to_owned(),
        );
    }
    let seen = see(&spec, &records);
    let p = probes(&spec, &seen, None);
    let mut doc = plan_doc(&spec, &p, left);
    let op = opstore::op_id(crate::probe::unix_now(), opstore::random4()?);
    let fingerprint = opstore::host_fingerprint(Path::new(opstore::MACHINE_ID))?;
    crate::quarantinecmd::seal_with_op(&mut doc, &op, &fingerprint, env!("CARGO_PKG_VERSION"))?;
    println!(
        "{}",
        serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?
    );
    Ok(0)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use aib_cloud::site::spec_from_record;

    fn spec() -> SiteSpec {
        spec_from_record(&json!({
            "site": "site9", "ct": 119, "ip": "10.77.0.19", "hosts": "www.example.com",
            "wp": 1, "locale": "en_US", "created": "2026-01-01T00:00:00Z", "template": 900,
            "limits": { "cores": 2, "cpulimit_x100": 200, "memory_mb": 1024, "swap_mb": 512, "pids_max": 512 }
        }))
        .unwrap()
    }

    const LIST_WITH: &str =
        "VMID       Status     Lock         Name\n119        running                 site9\n";
    const LIST_WITHOUT: &str =
        "VMID       Status     Lock         Name\n101        running                 edge\n";
    const RULES_WITH: &str = "-P PREROUTING ACCEPT\n-A PREROUTING -i eth9 -p tcp -m tcp --dport 2119 -j DNAT --to-destination 10.77.0.19:22\n";
    const RULES_WITHOUT: &str = "-P PREROUTING ACCEPT\n";

    fn untouched() -> Seen {
        Seen {
            entry_config: Some("# site:site9\nwww.example.com {\n}\n".to_owned()),
            container_list: Some(LIST_WITH.to_owned()),
            running: Some(true),
            disk: Some(true),
            archive: Some(Probe::NotDone),
            live_rules: Some(RULES_WITH.to_owned()),
            network_file: Some("iface x\n".to_owned()),
            firewall_file: Some(true),
            export_list: Some("site9 119 /var/www/site9\n".to_owned()),
            record_in_place: Some(true),
        }
    }

    fn gone() -> Seen {
        Seen {
            entry_config: Some("# site:other\nother.example.com {\n}\n".to_owned()),
            container_list: Some(LIST_WITHOUT.to_owned()),
            running: None,
            disk: Some(false),
            archive: Some(Probe::Done),
            live_rules: Some(RULES_WITHOUT.to_owned()),
            network_file: Some("iface x\n".to_owned()),
            firewall_file: Some(false),
            export_list: Some("other 101 /var/www/other\n".to_owned()),
            record_in_place: Some(false),
        }
    }

    #[test]
    fn an_untouched_site_has_nothing_done_and_starts_at_the_entry() {
        let p = probes(&spec(), &untouched(), Some(Probe::NotDone));
        assert!(p.iter().all(|x| *x == Probe::NotDone), "{p:?}");
        let doc = plan_doc(&spec(), &p, false);
        assert_eq!(doc["next"], "run");
        assert_eq!(doc["next_step"], "unserve");
        assert_eq!(doc["may_call_off"], true);
        assert_eq!(doc["address"], "www.example.com");
    }

    #[test]
    fn a_plan_that_stops_never_says_it_can_be_called_off() {
        // the entry could not be read while the container is already gone
        let mut seen = gone();
        seen.entry_config = None;
        let p = probes(&spec(), &seen, Some(Probe::Done));
        let doc = plan_doc(&spec(), &p, false);
        assert_eq!(doc["next"], "stop");
        assert_eq!(doc["next_step"], "unserve");
        assert_eq!(doc["may_call_off"], false);
        // steps out of order: nothing is archived and the way in is already closed
        let mut seen = untouched();
        seen.live_rules = gone().live_rules;
        let p = probes(&spec(), &seen, Some(Probe::NotDone));
        let doc = plan_doc(&spec(), &p, false);
        assert_eq!(doc["next"], "stop");
        assert_eq!(doc["stop"], "steps_out_of_order");
        assert_eq!(doc["may_call_off"], false);
    }

    #[test]
    fn a_removed_site_has_everything_done() {
        let p = probes(&spec(), &gone(), Some(Probe::Done));
        assert!(p.iter().all(|x| *x == Probe::Done), "{p:?}");
        assert_eq!(plan_doc(&spec(), &p, false)["next"], "finished");
    }

    #[test]
    fn the_licences_are_not_guessed() {
        // not wired: unknown, and the plan stops there once the archive is done
        let mut seen = untouched();
        seen.entry_config = gone().entry_config;
        seen.archive = Some(Probe::Done);
        let p = probes(&spec(), &seen, None);
        let doc = plan_doc(&spec(), &p, false);
        assert_eq!(doc["next"], "stop");
        assert_eq!(doc["next_step"], "licenses");
        assert_eq!(doc["stop"], "probe_unknown");
        // the person's explicit word passes it over, and the plan says so
        let doc = plan_doc(&spec(), &p, true);
        assert_eq!(doc["next"], "run");
        assert_eq!(doc["next_step"], "access");
        assert_eq!(doc["licences_left_at_vendor"], true);
        assert_eq!(doc["may_call_off"], false);
    }

    #[test]
    fn what_could_not_be_read_is_unknown_not_absent() {
        let at = |s: RemoveStep| STEPS.iter().position(|x| *x == s).unwrap();
        let mut seen = gone();
        seen.container_list = None;
        let p = probes(&spec(), &seen, Some(Probe::Done));
        assert_eq!(p[at(RemoveStep::Stop)], Probe::Unknown);
        assert_eq!(p[at(RemoveStep::Destroy)], Probe::Unknown);
        let mut seen = gone();
        seen.disk = None;
        assert_eq!(
            probes(&spec(), &seen, Some(Probe::Done))[at(RemoveStep::Destroy)],
            Probe::Unknown
        );
        let mut seen = gone();
        seen.firewall_file = None;
        assert_eq!(
            probes(&spec(), &seen, Some(Probe::Done))[at(RemoveStep::Firewall)],
            Probe::Unknown
        );
        let mut seen = gone();
        seen.record_in_place = None;
        assert_eq!(
            probes(&spec(), &seen, Some(Probe::Done))[at(RemoveStep::Record)],
            Probe::Unknown
        );
        let mut seen = gone();
        seen.archive = None;
        assert_eq!(
            probes(&spec(), &seen, Some(Probe::Done))[at(RemoveStep::Archive)],
            Probe::Unknown
        );
        // a listed container whose state was not answered
        let mut seen = untouched();
        seen.running = None;
        assert_eq!(
            probes(&spec(), &seen, Some(Probe::Done))[at(RemoveStep::Stop)],
            Probe::Unknown
        );
        // the container is gone and its disk is still there: not destroyed
        let mut seen = gone();
        seen.disk = Some(true);
        assert_eq!(
            probes(&spec(), &seen, Some(Probe::Done))[at(RemoveStep::Destroy)],
            Probe::NotDone
        );
    }

    #[test]
    fn an_archive_is_looked_for_by_the_containers_number() {
        let dir = std::env::temp_dir().join(format!(
            "aib-rm-{}-{}",
            std::process::id(),
            crate::probe::unix_now()
        ));
        // no directory, then an empty one, then another container's archive: not done
        assert_eq!(archive_probe(&dir, 119), Probe::NotDone);
        fs::create_dir_all(&dir).unwrap();
        assert_eq!(archive_probe(&dir, 119), Probe::NotDone);
        fs::write(
            dir.join("vzdump-lxc-1190-2026_01_01-00_00_00.tar.zst"),
            b"x",
        )
        .unwrap();
        assert_eq!(archive_probe(&dir, 119), Probe::NotDone);
        // a file of this container that is not a whole archive: could not tell, never done
        fs::write(
            dir.join("vzdump-lxc-119-2026_01_01-00_00_00.tar.zst"),
            b"not an archive",
        )
        .unwrap();
        assert_eq!(archive_probe(&dir, 119), Probe::Unknown);
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn nothing_but_a_plan() {
        let args: Vec<String> = ["site9", "--address", "www.example.com"]
            .iter()
            .map(|s| (*s).to_owned())
            .collect();
        assert!(cmd(&args).unwrap_err().contains("only --plan"));
        let args: Vec<String> = ["site9", "--plan"]
            .iter()
            .map(|s| (*s).to_owned())
            .collect();
        assert!(cmd(&args).unwrap_err().contains("--address"));
    }
}
