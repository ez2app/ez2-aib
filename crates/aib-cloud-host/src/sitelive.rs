// SPDX-License-Identifier: Apache-2.0
//! `site create --continue` (B4): the licences, the entry and the health check — the three
//! steps from which a failure **keeps** the site for a person
//! ([`aib_cloud::sitecreate::after_failure`]): an activation at a vendor is used up, and from
//! the entry on the site can be reached. Everything runs through the opening's [`Runner`],
//! so the tests hand in a recorder and nothing here touches a host.
//!
//! **Licences.** Only what the vendor itself answers counts; a "valid" the site stores by
//! itself does not. The host cannot reach the SEOPress vendor: it is asked from inside the
//! site (the plugin's own store address and item number, the site's own home address).
//! SEOPress is activated only when the key in the site is the template's key (the key file
//! on the host, read into memory and compared there — never an argument, never a line of
//! output); a site holding another key is left for a person. FlyingPress and Fluent Forms
//! Pro are not activated here (TODO: both have pitfalls the hand-run script works around —
//! FlyingPress clears its stored key on a failed activation), and the two Freemius plugins
//! activate themselves on the first back-office visit: all three are listed as left for a
//! person, which does not stop the opening. A blank site has no paid plugins.
//!
//! **Entry.** Before anything is changed the container's firewall must be live, not only
//! written: its compiled chain must jump to the site's group (c477a, B2 L1). The site's
//! block (`# site:<code>`, the white-label import) is added to the entry's configuration
//! only when neither its marker nor any of its names is there yet; the new file is written
//! beside the old one, validated, the old one kept as a backup, then swapped in and the
//! entry reloaded — a reload that fails puts the backup back. Every other site must answer
//! after as it did before (the removal's rule: an answer that cannot be had is not "the
//! same").
//!
//! **Health.** From the entry, by the site's own name: the answer must be 200 and carry the
//! site's own mark — the link header the site software sends for its own address
//! (`<https://<name>/wp-json/>`), which only a site whose address the identity step set
//! sends. The mark comes from the site software's REST address: a site whose REST links
//! are turned off (a security plugin) or that still uses plain links (`?rest_route=`) answers
//! 200 without it and is kept for a person; the template has both on. Then the opening
//! becomes a site: the record is written as a full record
//! ([`aib_cloud::site::record`]), the site is added to the export list, and the opening
//! record moves to `sites-opened/<code>.json` (root only): the licences left for a person,
//! what B3 left for later and the administrators are not in the full record.
//!
//! [`Runner`]: crate::sitecreatecmd::Runner

use std::fs;
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;

use aib_cloud::site::{caddy_block_upstream, caddy_marker, SiteSpec};
use aib_cloud::sitedefaults::{
    license_after, license_before, license_by_status, License, LicenseStep,
};
use serde_json::{json, Value};

use crate::sitecreatecmd::{HostPaths, Runner};
use crate::siteidentity::wp;

/// The paid plugins' keys of the template, on the host.
pub const KEYS: &str = "/etc/ez2-cloud/wp-package-keys.env";
/// The export list: one line per site, `<code> <ct> <site folder>`.
pub const SITES_LIST: &str = "/etc/ez2-cloud/sites.list";
/// The entry's container and its configuration: the same as the suspend / restore commands'.
use crate::probe::{CADDYFILE, EDGE_CT};
/// Where the new configuration is written before it is validated and swapped in.
const CADDYFILE_NEW: &str = "/etc/caddy/Caddyfile.ez2-new";
/// The template's SEOPress key, by its name in [`KEYS`].
const SEOPRESS_KEY: &str = "SEOPRESS_PRO_LICENSE_KEY";

fn owned(parts: &[&str]) -> Vec<String> {
    parts.iter().map(|s| (*s).to_owned()).collect()
}

fn first_host(spec: &SiteSpec) -> Result<&str, String> {
    spec.hosts
        .first()
        .map(String::as_str)
        .ok_or_else(|| "the site has no name".to_owned())
}

// ------------------------------------------------------------------ licences

/// The value of `key` in an env file (`KEY=value`, quotes taken off). `None` when the key
/// is not there or is empty.
pub fn env_value(text: &str, key: &str) -> Option<String> {
    text.lines()
        .filter_map(|l| l.trim().strip_prefix(key)?.strip_prefix('='))
        .map(|v| v.trim().trim_matches(|c| c == '"' || c == '\'').to_owned())
        .find(|v| !v.is_empty())
}

/// What the site said when it asked the SEOPress vendor: the first `SP ` line. `SP none` (no
/// key in the site) and anything not understood are `Unknown`, never "not active".
pub fn seopress_answer(out: &str) -> License {
    let Some(line) = out.lines().find(|l| l.starts_with("SP ")) else {
        return License::Unknown;
    };
    let field = |name: &str| {
        line.split_whitespace()
            .find_map(|w| w.strip_prefix(name)?.strip_prefix('='))
    };
    let Some(status) = field("license") else {
        return License::Unknown;
    };
    let left = field("left").and_then(|n| n.parse::<u64>().ok());
    license_by_status(Some(status), left)
}

/// The question the site sends its vendor (the hand-run licence script's, unchanged): the
/// plugin's own store address and item number, the site's own home address.
const SP_PROBE: &str = r#"$k=trim((string)get_option("seopress_pro_license_key")); if($k===""){echo "SP none\n";return;} $r=wp_remote_post(STORE_URL_SEOPRESS,["timeout"=>15,"body"=>["edd_action"=>"check_license","license"=>$k,"item_id"=>ITEM_ID_SEOPRESS,"url"=>get_option("home")]]); if(is_wp_error($r)||200!==wp_remote_retrieve_response_code($r)){echo "SP unknown\n";return;} $d=json_decode(wp_remote_retrieve_body($r),true); if(!is_array($d)||!isset($d["license"])){echo "SP unknown\n";return;} echo "SP license=".preg_replace("/[^a-z_]/","",(string)$d["license"])." left=".preg_replace("/[^0-9a-z]/","",(string)($d["activations_left"]??"na"))."\n";"#;

/// One paid plugin after the licence step.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    /// The vendor has this site as activated.
    Done,
    /// Not done here and not a failure of the opening: a person finishes it (the word says
    /// what).
    Person(&'static str),
}

impl Outcome {
    fn word(&self) -> &'static str {
        match self {
            Outcome::Done => "done",
            Outcome::Person(w) => w,
        }
    }
}

/// Is the key in the site the template's? Both are compared in memory; neither is printed.
fn same_key(site: &str, template: &str) -> bool {
    !site.is_empty() && site.as_bytes() == template.as_bytes()
}

fn seopress(
    runner: &mut impl Runner,
    paths: &HostPaths,
    spec: &SiteSpec,
) -> Result<Outcome, String> {
    let keys = fs::read_to_string(&paths.keys)
        .map_err(|e| format!("license_unknown: {}: {e}", paths.keys.display()))?;
    let template = env_value(&keys, SEOPRESS_KEY).ok_or_else(|| {
        format!(
            "license_unknown: {} has no SEOPress key",
            paths.keys.display()
        )
    })?;
    let site = runner
        .run(&wp(spec, &["option", "get", "seopress_pro_license_key"]))
        .map_err(|e| format!("license_unknown: the site's SEOPress key could not be read: {e}"))?;
    if !same_key(site.trim(), &template) {
        // another key, or none: not ours to activate
        return Ok(Outcome::Person("seopress:key_not_the_template's"));
    }
    // no answer is "unknown", never "not active"
    fn ask(runner: &mut impl Runner, spec: &SiteSpec) -> License {
        seopress_answer(
            &runner
                .run(&wp(spec, &["eval", SP_PROBE]))
                .unwrap_or_default(),
        )
    }
    match license_before(ask(runner, spec)) {
        LicenseStep::Done => Ok(Outcome::Done),
        LicenseStep::Stop(code) => Err(format!("{code}: SEOPress")),
        LicenseStep::Activate => {
            let call_ok = runner
                .run(&wp(spec, &["seopress-pro", "license", "activate"]))
                .is_ok();
            license_after(call_ok, ask(runner, spec))
                .map(|()| Outcome::Done)
                .map_err(|code| format!("{code}: SEOPress"))
        }
    }
}

/// The licence step: SEOPress activated (or a reason it is a person's), the other paid
/// plugins listed as left for a person. Written into the opening record as `licences`.
/// An error is a stop: the site is kept.
pub fn licences_step(
    runner: &mut impl Runner,
    paths: &HostPaths,
    spec: &SiteSpec,
    record: &mut Value,
) -> Result<(), String> {
    if !spec.wp {
        record["licences"] = json!({});
        return Ok(());
    }
    let sp = seopress(runner, paths, spec)?;
    record["licences"] = json!({
        "seopress": sp.word(),
        // TODO(B4+): in-site activation, with the hand-run script's guards
        "flyingpress": Outcome::Person("flyingpress:activate_by_hand").word(),
        "fluentformpro": Outcome::Person("fluentformpro:activate_by_hand").word(),
        // the template's own small program does it on the first back-office visit
        "freemius": Outcome::Person("freemius:first_back_office_visit").word(),
    });
    Ok(())
}

// ------------------------------------------------------------------ entry

/// Is the container's firewall live: does its compiled inbound chain jump to the site's
/// group? `rules` is `iptables -S veth<ct>i0-IN`.
pub fn firewall_compiled(rules: &str, group: &str) -> bool {
    let jump = format!("-j GROUP-{group}-IN");
    rules.lines().any(|l| {
        l.split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
            .ends_with(&jump)
    })
}

/// The entry's configuration with the site's block added. `Ok(None)`: the block is already
/// there exactly as it would be written (a step that ran and was not recorded). Any other
/// trace of the site — its marker with another block, one of its names on another line —
/// is refused: the entry is not changed around something nobody planned.
pub fn edge_with_site(caddyfile: &str, spec: &SiteSpec) -> Result<Option<String>, String> {
    let block = caddy_block_upstream(spec, &spec.ip());
    if caddyfile.contains(&block) {
        return Ok(None);
    }
    let marker = caddy_marker(&spec.code);
    if caddyfile.lines().any(|l| l.trim_end() == marker) {
        return Err(format!(
            "the entry already has a block for {} that is not this one",
            spec.code
        ));
    }
    for host in &spec.hosts {
        let named = caddyfile.lines().any(|l| {
            !l.trim_start().starts_with('#')
                && l.split(|c: char| c.is_whitespace() || c == ',' || c == '{')
                    .any(|w| w == host)
        });
        if named {
            return Err(format!("{host} is already on the entry"));
        }
    }
    let mut out = caddyfile.to_owned();
    if !out.ends_with('\n') {
        out.push('\n');
    }
    out.push('\n');
    out.push_str(&block);
    Ok(Some(out))
}

/// Every other site's first name and its answer through the entry, by name, sorted.
/// `None`: no answer (the removal's rule: that stops the work).
pub fn others(runner: &mut impl Runner, records: &Path, code: &str) -> Vec<(String, Option<u16>)> {
    let Ok(entries) = fs::read_dir(records) else {
        return vec![(format!("(the records in {})", records.display()), None)];
    };
    let mut names = Vec::new();
    for e in entries.flatten() {
        let file = e.file_name().to_string_lossy().into_owned();
        if !file.ends_with(".json") || file == format!("{code}.json") {
            continue;
        }
        let first = fs::read_to_string(e.path())
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .and_then(|v| {
                v.get("hosts").and_then(Value::as_str).and_then(|h| {
                    h.split(|c: char| c == ',' || c.is_whitespace())
                        .find(|x| !x.is_empty())
                        .map(str::to_owned)
                })
            });
        names.push(first.unwrap_or_else(|| format!("(record {file})")));
    }
    names.sort();
    names
        .into_iter()
        .map(|h| {
            let code = if aib_cloud::edge::valid_domain(&h) {
                runner
                    .run(&curl_status(&h))
                    .ok()
                    .and_then(|c| c.trim().parse::<u16>().ok())
                    .filter(|c| *c != 0)
            } else {
                None
            };
            (h, code)
        })
        .collect()
}

fn curl_status(host: &str) -> Vec<String> {
    let resolve = format!("{host}:443:127.0.0.1");
    let url = format!("https://{host}/");
    edge(&[
        "curl",
        "-s",
        "-o",
        "/dev/null",
        "-m",
        "10",
        "-w",
        "%{http_code}",
        "--resolve",
        &resolve,
        &url,
    ])
}

/// Every other site answers as before. An answer that could not be had, then or now, is
/// not "the same".
pub fn same_answers(
    before: &[(String, Option<u16>)],
    now: &[(String, Option<u16>)],
) -> Result<(), String> {
    if let Some((h, _)) = before.iter().chain(now).find(|(_, c)| c.is_none()) {
        return Err(format!("another site could not be asked: {h}"));
    }
    if before != now {
        let show = |v: &[(String, Option<u16>)]| {
            v.iter()
                .map(|(h, c)| format!("{h}={}", c.unwrap_or(0)))
                .collect::<Vec<_>>()
                .join(" ")
        };
        return Err(format!(
            "another site does not answer as before: before {} now {}",
            show(before),
            show(now)
        ));
    }
    Ok(())
}

fn edge(rest: &[&str]) -> Vec<String> {
    let mut v = owned(&["pct", "exec", EDGE_CT, "--"]);
    v.extend(rest.iter().map(|s| (*s).to_owned()));
    v
}

/// Write `text` as the entry's configuration: beside the old one, validated, the old one
/// kept, swapped in, reloaded; a failed reload puts the old one back and reloads that.
/// Put `backup` back as the entry's configuration and reload it. `true` when both went.
fn edge_put_back(runner: &mut impl Runner, backup: &str) -> bool {
    let back = runner.run(&edge(&["cp", "-a", backup, CADDYFILE]));
    let again = runner.run(&edge(&["systemctl", "reload", "caddy"]));
    back.is_ok() && again.is_ok()
}

/// Every `edge_write` keeps the configuration it replaced as `Caddyfile.bak-<time>` in the
/// entry's container, and nothing here removes them (TODO, with the removal's backups:
/// keep the newest few, by a reviewed cleaning rule). Returns that backup's path.
fn edge_write(runner: &mut impl Runner, text: &str) -> Result<String, String> {
    let write_new = format!("umask 022; cat > {CADDYFILE_NEW}");
    runner
        .run_input(&edge(&["sh", "-c", &write_new]), text.as_bytes())
        .map_err(|e| format!("the new entry configuration could not be written: {e}"))?;
    if let Err(e) = runner.run(&edge(&[
        "caddy",
        "validate",
        "--config",
        CADDYFILE_NEW,
        "--adapter",
        "caddyfile",
    ])) {
        let _ = runner.run(&edge(&["rm", "-f", CADDYFILE_NEW]));
        return Err(format!(
            "the new entry configuration is not valid; nothing was changed: {e}"
        ));
    }
    let backup = format!("{CADDYFILE}.bak-{}", crate::probe::unix_now());
    runner
        .run(&edge(&["cp", "-a", CADDYFILE, &backup]))
        .map_err(|e| format!("the entry configuration could not be kept as {backup}: {e}"))?;
    runner
        .run(&edge(&["mv", "-f", CADDYFILE_NEW, CADDYFILE]))
        .map_err(|e| format!("the new entry configuration could not be put in place: {e}"))?;
    if let Err(e) = runner.run(&edge(&["systemctl", "reload", "caddy"])) {
        let back = edge_put_back(runner, &backup);
        return Err(format!(
            "the entry did not reload; the old configuration from {backup} {}: {e}",
            if back {
                "is back"
            } else {
                "could NOT be put back"
            }
        ));
    }
    Ok(backup)
}

/// The entry step: the firewall live, the site's block added, every other site the same.
pub fn edge_step(
    runner: &mut impl Runner,
    paths: &HostPaths,
    spec: &SiteSpec,
) -> Result<(), String> {
    let chain = format!("veth{}i0-IN", spec.ct);
    let rules = runner
        .run(&owned(&["iptables", "-S", &chain]))
        .map_err(|e| format!("firewall_not_live: {chain} could not be read: {e}"))?;
    if !firewall_compiled(&rules, &spec.fw_group) {
        return Err(format!(
            "firewall_not_live: {chain} does not jump to GROUP-{}-IN yet; the entry was not changed",
            spec.fw_group
        ));
    }
    let now = runner
        .run(&edge(&["cat", CADDYFILE]))
        .map_err(|e| format!("the entry configuration could not be read: {e}"))?;
    let Some(text) = edge_with_site(&now, spec)? else {
        return Ok(());
    };
    let before = others(runner, &paths.records, &spec.code);
    same_answers(&before, &before)?;
    let backup = edge_write(runner, &text)?;
    let after = runner
        .run(&edge(&["cat", CADDYFILE]))
        .map_err(|e| format!("the entry configuration could not be read back: {e}"))?;
    if edge_with_site(&after, spec)?.is_some() {
        return Err("the entry configuration read back has no block for the site".to_owned());
    }
    let Err(changed) = same_answers(&before, &others(runner, &paths.records, &spec.code)) else {
        return Ok(());
    };
    // the new block changed another site: the old configuration goes back at once, and the
    // other sites are asked again (33e28, B4 M1)
    if !edge_put_back(runner, &backup) {
        return Err(format!(
            "{changed}; the old configuration from {backup} could NOT be put back"
        ));
    }
    let again = match same_answers(&before, &others(runner, &paths.records, &spec.code)) {
        Ok(()) => "the other sites answer as before again".to_owned(),
        Err(e) => format!("and still: {e}"),
    };
    Err(format!(
        "{changed}; the old configuration from {backup} is back and the site's block is off the entry, {again}"
    ))
}

// ------------------------------------------------------------------ health

/// Does the answer (status line and headers, as `curl -D -` prints them) say 200 with the
/// site's own mark for `host`?
pub fn health_answer(headers: &str, host: &str) -> Result<(), String> {
    let status = headers
        .lines()
        .find(|l| l.starts_with("HTTP/"))
        .and_then(|l| l.split_whitespace().nth(1))
        .and_then(|c| c.parse::<u16>().ok());
    if status != Some(200) {
        return Err(format!(
            "health: {host} answered {}",
            status.map_or("nothing".to_owned(), |c| c.to_string())
        ));
    }
    let mark = format!("<https://{host}/wp-json/>");
    let marked = headers.lines().any(|l| {
        l.split_once(':')
            .is_some_and(|(k, v)| k.trim().eq_ignore_ascii_case("link") && v.contains(&mark))
    });
    if marked {
        Ok(())
    } else {
        Err(format!(
            "health: {host} answered 200 without its own mark {mark}"
        ))
    }
}

/// UTC date and time of `epoch` (`2026-11-11T04:14:39Z`), the records' form.
pub fn iso_utc(epoch: u64) -> String {
    let epoch = i64::try_from(epoch).unwrap_or(i64::MAX);
    let days = epoch.div_euclid(86_400);
    let secs = epoch.rem_euclid(86_400);
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        secs / 3600,
        (secs / 60) % 60,
        secs % 60
    )
}

/// The export list with the site's line added; the same list when it is there already.
/// A line for the code with other values is refused.
pub fn list_with_site(list: &str, spec: &SiteSpec) -> Result<String, String> {
    let line = format!("{} {} /var/www/{}", spec.code, spec.ct, spec.code);
    for l in list.lines() {
        if l.split_whitespace().next() == Some(spec.code.as_str()) {
            return if l.split_whitespace().collect::<Vec<_>>().join(" ") == line {
                Ok(list.to_owned())
            } else {
                Err(format!(
                    "the export list already has another line for {}: {l}",
                    spec.code
                ))
            };
        }
    }
    let mut out = list.to_owned();
    if !out.is_empty() && !out.ends_with('\n') {
        out.push('\n');
    }
    out.push_str(&line);
    out.push('\n');
    Ok(out)
}

fn write_new(path: &Path, text: &str, mode: u32) -> Result<(), String> {
    let dir = path.parent().ok_or("a file with no folder")?;
    let name = path
        .file_name()
        .ok_or("a file with no name")?
        .to_string_lossy()
        .into_owned();
    let tmp = dir.join(format!(".{name}.{}.tmp", std::process::id()));
    let _ = fs::remove_file(&tmp);
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(mode)
        .open(&tmp)
        .map_err(|e| format!("{}: {e}", tmp.display()))?;
    f.write_all(text.as_bytes())
        .and_then(|()| f.sync_all())
        .map_err(|e| format!("{}: {e}", tmp.display()))?;
    fs::rename(&tmp, path).map_err(|e| format!("{}: {e}", path.display()))?;
    let back = fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
    if back == text {
        Ok(())
    } else {
        Err(format!("{}: read back differs", path.display()))
    }
}

/// The health step, and the opening made a site: answer with the mark; the full record
/// written; the export list; the opening record moved to the opened ones. A full record already there for
/// another site is refused.
pub fn health_step(
    runner: &mut impl Runner,
    paths: &HostPaths,
    spec: &SiteSpec,
    opening: &Value,
) -> Result<(), String> {
    let created_at = started_at(opening);
    let host = first_host(spec)?;
    let resolve = format!("{host}:443:127.0.0.1");
    let url = format!("https://{host}/");
    let headers = runner
        .run(&edge(&[
            "curl",
            "-s",
            "-o",
            "/dev/null",
            "-D",
            "-",
            "-m",
            "20",
            "--resolve",
            &resolve,
            &url,
        ]))
        .map_err(|e| format!("health: {host} could not be asked: {e}"))?;
    health_answer(&headers, host)?;
    let path = paths.records.join(format!("{}.json", spec.code));
    let mut record = aib_cloud::site::record(spec, &iso_utc(created_at));
    // the customer the opening was for owns the site: their key sees it from now on
    if let Some(tenant) = aib_cloud::site::record_tenant(opening) {
        record["tenant_id"] = json!(tenant);
    }
    let text = serde_json::to_string_pretty(&record).map_err(|e| e.to_string())? + "\n";
    match fs::read_to_string(&path) {
        Ok(there) if there == text => {}
        Ok(_) => {
            return Err(format!(
                "{}: a record that is not this opening's is there",
                path.display()
            ))
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => write_new(&path, &text, 0o644)?,
        Err(e) => return Err(format!("{}: {e}", path.display())),
    }
    let list = match fs::read_to_string(&paths.sites_list) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(e) => return Err(format!("{}: {e}", paths.sites_list.display())),
    };
    let new_list = list_with_site(&list, spec)?;
    if new_list != list {
        write_new(&paths.sites_list, &new_list, 0o644)?;
    }
    // what the full record does not carry (the licences left for a person, what B3 left for
    // later, the administrators) stays on the host beside it, root only (33e28, B4 M2)
    fs::create_dir_all(&paths.opened).map_err(|e| format!("{}: {e}", paths.opened.display()))?;
    let kept = paths.opened.join(format!("{}.json", spec.code));
    let opened = serde_json::to_string_pretty(opening).map_err(|e| e.to_string())? + "\n";
    write_new(&kept, &opened, 0o600)?;
    let opening = paths.creating.join(format!("{}.json", spec.code));
    match fs::remove_file(&opening) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(format!("{}: {e}", opening.display())),
    }
}

/// What a person still has to do for an opened site, from its opening record: what B3 left
/// for later and every licence not done.
pub fn pending_of(record: &Value) -> Vec<String> {
    let mut out: Vec<String> = record
        .get("later")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect();
    if let Some(licences) = record.get("licences").and_then(Value::as_object) {
        out.extend(
            licences
                .iter()
                .filter_map(|(name, w)| Some((name, w.as_str()?)))
                .filter(|(_, w)| *w != "done")
                .map(|(name, w)| format!("licence {name}: {w}")),
        );
    }
    out
}

/// The time an opening started, from its record (`started_at`); now when it has none.
pub fn started_at(record: &Value) -> u64 {
    record
        .get("started_at")
        .and_then(Value::as_u64)
        .unwrap_or_else(crate::probe::unix_now)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use std::path::PathBuf;

    fn spec() -> SiteSpec {
        SiteSpec::new(
            "s-b4test",
            131,
            41,
            &["b4.example.test"],
            true,
            "zh_TW",
            aib_cloud::site::Limits::START,
        )
        .unwrap()
    }

    fn scratch(name: &str) -> HostPaths {
        let d = std::env::temp_dir().join(format!("aib-b4-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        for sub in ["creating", "records", "secrets", "lxc", "firewall"] {
            fs::create_dir_all(d.join(sub)).unwrap();
        }
        fs::write(d.join("keys.env"), "SEOPRESS_PRO_LICENSE_KEY=\"tmplkey\"\n").unwrap();
        HostPaths {
            creating: d.join("creating"),
            lxc_conf: d.join("lxc"),
            firewall: d.join("firewall"),
            secrets: d.join("secrets"),
            site_admins: d.join("site-admins"),
            setup: PathBuf::from("/usr/local/sbin/ez2-site-setup"),
            records: d.join("records"),
            sites_list: d.join("sites.list"),
            keys: d.join("keys.env"),
            opened: d.join("opened"),
            cloudflare: d.join("cloudflare.env"),
            rosters: d.join("rosters"),
            cf_ledger: d.join("cloudflare.jsonl"),
        }
    }

    /// A host answering from a script; every command remembered.
    struct Host<F: FnMut(&[String]) -> Result<String, String>> {
        calls: Vec<String>,
        fed: Vec<String>,
        answer: F,
    }

    impl<F: FnMut(&[String]) -> Result<String, String>> Runner for Host<F> {
        fn run(&mut self, argv: &[String]) -> Result<String, String> {
            self.calls.push(argv.join(" "));
            (self.answer)(argv)
        }
        fn run_input(&mut self, argv: &[String], input: &[u8]) -> Result<String, String> {
            self.calls.push(argv.join(" "));
            self.fed.push(String::from_utf8_lossy(input).into_owned());
            (self.answer)(argv)
        }
    }

    fn host<F: FnMut(&[String]) -> Result<String, String>>(answer: F) -> Host<F> {
        Host {
            calls: Vec::new(),
            fed: Vec::new(),
            answer,
        }
    }

    // ---- licences

    #[test]
    fn a_key_file_value_is_read_without_its_quotes() {
        let t = "# keys\nOTHER=x\nSEOPRESS_PRO_LICENSE_KEY='abc'\n";
        assert_eq!(
            env_value(t, "SEOPRESS_PRO_LICENSE_KEY").as_deref(),
            Some("abc")
        );
        assert_eq!(
            env_value("SEOPRESS_PRO_LICENSE_KEY=\n", "SEOPRESS_PRO_LICENSE_KEY"),
            None
        );
        assert_eq!(
            env_value(
                "SEOPRESS_PRO_LICENSE_KEY_OLD=x\n",
                "SEOPRESS_PRO_LICENSE_KEY"
            ),
            None
        );
    }

    #[test]
    fn only_the_vendor_s_own_words_count_for_seopress() {
        assert_eq!(
            seopress_answer("SP license=valid left=3\n"),
            License::Active
        );
        assert_eq!(
            seopress_answer("noise\nSP license=site_inactive left=2\n"),
            License::Inactive
        );
        assert_eq!(
            seopress_answer("SP license=site_inactive left=0\n"),
            License::Quota
        );
        assert_eq!(
            seopress_answer("SP license=expired left=na\n"),
            License::NotValid
        );
        assert_eq!(seopress_answer("SP none\n"), License::Unknown);
        assert_eq!(seopress_answer("SP unknown\n"), License::Unknown);
        assert_eq!(seopress_answer(""), License::Unknown);
    }

    /// A site whose key is `key` and whose vendor says `before`, then `after` once the
    /// activation ran.
    fn sp_site(
        key: &'static str,
        before: &'static str,
        after: &'static str,
    ) -> impl FnMut(&[String]) -> Result<String, String> {
        let mut activated = false;
        move |argv: &[String]| {
            let a = argv.join(" ");
            if a.contains("option get seopress_pro_license_key") {
                Ok(format!("{key}\n"))
            } else if a.contains("license activate") {
                activated = true;
                Ok(String::new())
            } else if a.contains(" eval ") {
                Ok(if activated { after } else { before }.to_owned())
            } else {
                Err(format!("unexpected: {a}"))
            }
        }
    }

    #[test]
    fn seopress_is_activated_when_the_site_holds_the_template_s_key() {
        let paths = scratch("sp-ok");
        let mut h = host(sp_site(
            "tmplkey",
            "SP license=site_inactive left=5\n",
            "SP license=valid left=4\n",
        ));
        let mut rec = json!({});
        licences_step(&mut h, &paths, &spec(), &mut rec).unwrap();
        assert_eq!(rec["licences"]["seopress"], "done");
        assert_eq!(
            rec["licences"]["flyingpress"],
            "flyingpress:activate_by_hand"
        );
        assert!(h
            .calls
            .iter()
            .any(|c| c.ends_with("seopress-pro license activate")));
        // the template's key is never an argument
        assert!(h.calls.iter().all(|c| !c.contains("tmplkey")));
    }

    #[test]
    fn another_key_in_the_site_is_left_for_a_person_and_nothing_is_activated() {
        let paths = scratch("sp-other");
        let mut h = host(sp_site(
            "someone-elses",
            "SP license=site_inactive left=5\n",
            "",
        ));
        let mut rec = json!({});
        licences_step(&mut h, &paths, &spec(), &mut rec).unwrap();
        assert_eq!(
            rec["licences"]["seopress"],
            "seopress:key_not_the_template's"
        );
        assert!(h.calls.iter().all(|c| !c.contains("license activate")));
    }

    #[test]
    fn no_room_left_or_no_answer_stops_with_the_vendor_s_word() {
        let paths = scratch("sp-quota");
        let mut h = host(sp_site("tmplkey", "SP license=site_inactive left=0\n", ""));
        let e = licences_step(&mut h, &paths, &spec(), &mut json!({})).unwrap_err();
        assert!(e.starts_with("license_quota"), "{e}");
        let mut h = host(sp_site("tmplkey", "SP unknown\n", ""));
        let e = licences_step(&mut h, &paths, &spec(), &mut json!({})).unwrap_err();
        assert!(e.starts_with("license_unknown"), "{e}");
        assert!(h.calls.iter().all(|c| !c.contains("license activate")));
    }

    #[test]
    fn an_activation_the_vendor_does_not_confirm_is_a_failure() {
        let paths = scratch("sp-after");
        let mut h = host(sp_site(
            "tmplkey",
            "SP license=site_inactive left=5\n",
            "SP license=site_inactive left=5\n",
        ));
        let e = licences_step(&mut h, &paths, &spec(), &mut json!({})).unwrap_err();
        assert!(e.starts_with("license_inactive"), "{e}");
    }

    #[test]
    fn a_blank_site_has_no_licences() {
        let paths = scratch("sp-blank");
        let blank = SiteSpec::new(
            "s-b4blnk",
            131,
            41,
            &["b4.example.test"],
            false,
            "zh_TW",
            aib_cloud::site::Limits::START,
        )
        .unwrap();
        let mut h = host(|a: &[String]| Err(format!("unexpected: {}", a.join(" "))));
        let mut rec = json!({});
        licences_step(&mut h, &paths, &blank, &mut rec).unwrap();
        assert_eq!(rec["licences"], json!({}));
        assert!(h.calls.is_empty());
    }

    // ---- entry

    #[test]
    fn a_firewall_is_live_only_when_its_chain_jumps_to_the_group() {
        let live = "-N veth131i0-IN\n-A veth131i0-IN -j GROUP-ez2-site-v1-IN\n";
        assert!(firewall_compiled(live, "ez2-site-v1"));
        assert!(!firewall_compiled("-N veth131i0-IN\n", "ez2-site-v1"));
        assert!(!firewall_compiled(live, "ez2-site-v2"));
    }

    const OTHER: &str = "(ez2_whitelabel) {\n}\n\n# site:site1\neu02-site1.example.test {\n\timport ez2_whitelabel\n}\n";

    #[test]
    fn the_site_s_block_is_added_once_and_nothing_else_moves() {
        let s = spec();
        let text = edge_with_site(OTHER, &s).unwrap().unwrap();
        assert!(text.starts_with(OTHER));
        assert!(text.contains("# site:s-b4test\nb4.example.test {\n\timport ez2_whitelabel\n"));
        assert!(text.contains("reverse_proxy 10.77.0.41:80"));
        // already there as it would be written: nothing to do
        assert_eq!(edge_with_site(&text, &s).unwrap(), None);
    }

    #[test]
    fn a_marker_or_a_name_already_on_the_entry_is_refused() {
        let s = spec();
        let marked = format!("{OTHER}\n# site:s-b4test\nold.example.test {{\n}}\n");
        assert!(edge_with_site(&marked, &s).is_err());
        let named = format!("{OTHER}\n# site:x\nb4.example.test, www.x.test {{\n}}\n");
        assert!(edge_with_site(&named, &s).is_err());
        // a comment naming it does not count
        let comment = format!("{OTHER}\n# was b4.example.test once\n");
        assert!(edge_with_site(&comment, &s).unwrap().is_some());
    }

    #[test]
    fn other_sites_must_answer_as_before_and_no_answer_is_never_the_same() {
        let a = |c| vec![("x.test".to_owned(), c)];
        assert!(same_answers(&a(Some(200)), &a(Some(200))).is_ok());
        assert!(same_answers(&a(Some(200)), &a(Some(502))).is_err());
        assert!(same_answers(&a(None), &a(None)).is_err());
        assert!(same_answers(&[], &[]).is_ok());
    }

    /// An entry that keeps its configuration in memory; `reload_ok` decides the reload,
    /// `answers` what the other site says through it.
    fn entry(
        firewall: &'static str,
        reload_ok: bool,
        mut answers: Vec<&'static str>,
    ) -> impl FnMut(&[String]) -> Result<String, String> {
        let mut file = OTHER.to_owned();
        let mut new: Option<String> = None;
        let mut backup: Option<String> = None;
        let mut reloads = 0;
        move |argv: &[String]| {
            let a = argv.join(" ");
            if a.starts_with("iptables -S veth131i0-IN") {
                return Ok(firewall.to_owned());
            }
            if a.contains("curl") && a.contains("%{http_code}") {
                return Ok(if answers.len() > 1 {
                    answers.remove(0)
                } else {
                    answers[0]
                }
                .to_owned());
            }
            if a.ends_with("cat /etc/caddy/Caddyfile") {
                return Ok(file.clone());
            }
            if a.contains("caddy validate") {
                return Ok(String::new());
            }
            if a.contains("cp -a /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak-") {
                backup = Some(file.clone());
                return Ok(String::new());
            }
            if a.contains("mv -f") {
                file = new.take().ok_or("no new file")?;
                return Ok(String::new());
            }
            if a.contains("Caddyfile.bak-") && a.ends_with("/etc/caddy/Caddyfile") {
                file = backup.clone().ok_or("no backup")?;
                return Ok(String::new());
            }
            if a.contains("systemctl reload caddy") {
                reloads += 1;
                return if reload_ok || reloads > 1 {
                    Ok(String::new())
                } else {
                    Err("reload failed".to_owned())
                };
            }
            if a.contains("cat > /etc/caddy/Caddyfile.ez2-new") {
                new = Some(String::new());
                return Ok(String::new());
            }
            if a.contains("rm -f") {
                return Ok(String::new());
            }
            Err(format!("unexpected: {a}"))
        }
    }

    fn with_other_record(paths: &HostPaths) {
        fs::write(
            paths.records.join("site1.json"),
            r#"{"site":"site1","hosts":"eu02-site1.example.test,ez2.example.test"}"#,
        )
        .unwrap();
    }

    #[test]
    fn the_entry_is_changed_only_when_the_firewall_is_live() {
        let paths = scratch("edge-fw");
        with_other_record(&paths);
        let mut h = host(entry("-N veth131i0-IN\n", true, vec!["200"]));
        let e = edge_step(&mut h, &paths, &spec()).unwrap_err();
        assert!(e.starts_with("firewall_not_live"), "{e}");
        assert!(h.calls.iter().all(|c| !c.contains("Caddyfile")));
    }

    #[test]
    fn the_entry_step_adds_the_block_and_the_other_site_still_answers() {
        let paths = scratch("edge-ok");
        with_other_record(&paths);
        let mut h = host(entry(
            "-A veth131i0-IN -j GROUP-ez2-site-v1-IN\n",
            true,
            vec!["200"],
        ));
        // the fake keeps the fed text as the new file
        edge_step_fed(&mut h, &paths).unwrap();
        assert_eq!(h.fed.len(), 1);
        assert!(h.fed[0].contains("# site:s-b4test"));
        let order: Vec<&str> = h
            .calls
            .iter()
            .filter_map(|c| {
                [
                    "caddy validate",
                    "cp -a /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak",
                    "mv -f",
                    "systemctl reload",
                ]
                .into_iter()
                .find(|w| c.contains(w))
            })
            .collect();
        assert_eq!(
            order,
            [
                "caddy validate",
                "cp -a /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak",
                "mv -f",
                "systemctl reload"
            ]
        );
    }

    #[test]
    fn a_failed_reload_puts_the_old_configuration_back() {
        let paths = scratch("edge-reload");
        with_other_record(&paths);
        let mut h = host(entry(
            "-A veth131i0-IN -j GROUP-ez2-site-v1-IN\n",
            false,
            vec!["200"],
        ));
        let e = edge_step_fed(&mut h, &paths).unwrap_err();
        assert!(e.contains("did not reload") && e.contains("is back"), "{e}");
        assert_eq!(
            h.calls
                .iter()
                .filter(|c| c.contains("systemctl reload"))
                .count(),
            2
        );
        assert!(h
            .calls
            .iter()
            .any(|c| c.contains("cp -a /etc/caddy/Caddyfile.bak-")));
    }

    #[test]
    fn another_site_that_answers_differently_after_is_a_failure() {
        let paths = scratch("edge-other");
        with_other_record(&paths);
        let mut h = host(entry(
            "-A veth131i0-IN -j GROUP-ez2-site-v1-IN\n",
            true,
            vec!["200", "502"],
        ));
        let e = edge_step_fed(&mut h, &paths).unwrap_err();
        assert!(e.contains("does not answer as before"), "{e}");
    }

    #[test]
    fn a_block_that_changes_another_site_is_taken_off_the_entry_again() {
        let paths = scratch("edge-back");
        with_other_record(&paths);
        // before 200, with the block 502, with the old configuration back 200
        let mut h = host(entry(
            "-A veth131i0-IN -j GROUP-ez2-site-v1-IN\n",
            true,
            vec!["200", "502", "200"],
        ));
        let e = edge_step_fed(&mut h, &paths).unwrap_err();
        assert!(e.contains("does not answer as before"), "{e}");
        assert!(
            e.contains("is back") && e.contains("answer as before again"),
            "{e}"
        );
        assert!(h
            .calls
            .iter()
            .any(|c| c.contains("cp -a /etc/caddy/Caddyfile.bak-")));
        assert_eq!(
            h.calls
                .iter()
                .filter(|c| c.contains("systemctl reload"))
                .count(),
            2
        );
        assert_eq!(
            h.calls
                .iter()
                .filter(|c| c.contains("%{http_code}"))
                .count(),
            3
        );
    }

    #[test]
    fn what_a_person_still_has_to_do_comes_from_the_record() {
        let rec = json!({
            "later": ["fp_site_key: F1 is not open", "setup_log: x stays in the container"],
            "licences": {"seopress": "done", "freemius": "freemius:first_back_office_visit"},
        });
        assert_eq!(
            pending_of(&rec),
            [
                "fp_site_key: F1 is not open",
                "setup_log: x stays in the container",
                "licence freemius: freemius:first_back_office_visit",
            ]
        );
        assert!(pending_of(&json!({})).is_empty());
    }

    /// The fake entry needs what was fed to `cat >`: hand it over before `mv`.
    fn edge_step_fed<F: FnMut(&[String]) -> Result<String, String>>(
        h: &mut Host<F>,
        paths: &HostPaths,
    ) -> Result<(), String> {
        struct Bridge<'a, F: FnMut(&[String]) -> Result<String, String>> {
            inner: &'a mut Host<F>,
            fed: Option<String>,
            file: Option<String>,
        }
        impl<F: FnMut(&[String]) -> Result<String, String>> Runner for Bridge<'_, F> {
            fn run(&mut self, argv: &[String]) -> Result<String, String> {
                let a = argv.join(" ");
                if a.contains("mv -f") {
                    self.inner.calls.push(a);
                    self.file = self.fed.take();
                    return if self.file.is_some() {
                        Ok(String::new())
                    } else {
                        Err("no new file".to_owned())
                    };
                }
                if a.ends_with("cat /etc/caddy/Caddyfile") {
                    if let Some(f) = &self.file {
                        self.inner.calls.push(a);
                        return Ok(f.clone());
                    }
                }
                if a.contains("Caddyfile.bak-")
                    && a.ends_with("/etc/caddy/Caddyfile")
                    && !a.contains("cp -a /etc/caddy/Caddyfile /")
                {
                    self.file = None;
                }
                self.inner.run(argv)
            }
            fn run_input(&mut self, argv: &[String], input: &[u8]) -> Result<String, String> {
                self.fed = Some(String::from_utf8_lossy(input).into_owned());
                self.inner.run_input(argv, input)
            }
        }
        let mut b = Bridge {
            inner: h,
            fed: None,
            file: None,
        };
        edge_step(&mut b, paths, &spec())
    }

    // ---- health

    #[test]
    fn health_needs_200_and_the_site_s_own_mark() {
        let ok = "HTTP/2 200 \r\ncontent-type: text/html\r\nlink: <https://b4.example.test/wp-json/>; rel=\"https://api.w.org/\"\r\n";
        assert!(health_answer(ok, "b4.example.test").is_ok());
        let other = ok.replace("b4.example.test/wp-json", "template.example.test/wp-json");
        assert!(health_answer(&other, "b4.example.test").is_err());
        let code = ok.replace("HTTP/2 200", "HTTP/2 503");
        assert!(health_answer(&code, "b4.example.test").is_err());
        assert!(health_answer("", "b4.example.test").is_err());
    }

    #[test]
    fn records_carry_a_utc_date() {
        assert_eq!(iso_utc(0), "1970-01-01T00:00:00Z");
        assert_eq!(iso_utc(1_794_379_200), "2026-11-11T06:40:00Z");
    }

    #[test]
    fn the_export_list_gets_one_line_and_keeps_its_comments() {
        let s = spec();
        let list = "# comment\nsite1 110 /var/www/site1\n";
        let out = list_with_site(list, &s).unwrap();
        assert_eq!(
            out,
            "# comment\nsite1 110 /var/www/site1\ns-b4test 131 /var/www/s-b4test\n"
        );
        assert_eq!(list_with_site(&out, &s).unwrap(), out);
        assert!(list_with_site("s-b4test 999 /var/www/x\n", &s).is_err());
    }

    /// A customer's opening: the full record names the customer, so their key sees the site;
    /// an opening that names none gives the record none (never the site's own code).
    #[test]
    fn the_customer_of_the_opening_owns_the_site() {
        let paths = scratch("health-tenant");
        let s = spec();
        let mut h = host(|_: &[String]| {
            Ok("HTTP/2 200\r\nlink: <https://b4.example.test/wp-json/>; rel=\"https://api.w.org/\"\r\n".to_owned())
        });
        let opening =
            json!({ "started_at": 1_794_379_200_u64, "step": "health", "tenant_id": "t-acme1" });
        health_step(&mut h, &paths, &s, &opening).unwrap();
        let rec: Value =
            serde_json::from_str(&fs::read_to_string(paths.records.join("s-b4test.json")).unwrap())
                .unwrap();
        assert_eq!(aib_cloud::site::record_tenant(&rec), Some("t-acme1"));
        assert!(aib_cloud::site::record_gaps(&rec).is_empty());
    }

    #[test]
    fn a_healthy_site_becomes_a_full_record_on_the_list_and_its_opening_goes() {
        let paths = scratch("health-ok");
        let s = spec();
        fs::write(paths.creating.join("s-b4test.json"), "{}").unwrap();
        fs::write(&paths.sites_list, "site1 110 /var/www/site1\n").unwrap();
        let mut h = host(|_: &[String]| {
            Ok("HTTP/2 200\r\nlink: <https://b4.example.test/wp-json/>; rel=\"https://api.w.org/\"\r\n".to_owned())
        });
        let opening = json!({
            "started_at": 1_794_379_200_u64,
            "step": "health",
            "admins": ["owner"],
            "later": ["fp_site_key: F1 is not open"],
            "licences": {"seopress": "done", "flyingpress": "flyingpress:activate_by_hand"},
        });
        health_step(&mut h, &paths, &s, &opening).unwrap();
        let rec: Value =
            serde_json::from_str(&fs::read_to_string(paths.records.join("s-b4test.json")).unwrap())
                .unwrap();
        assert!(
            aib_cloud::site::record_gaps(&rec).is_empty(),
            "{:?}",
            aib_cloud::site::record_gaps(&rec)
        );
        assert_eq!(rec["created"], "2026-11-11T06:40:00Z");
        assert!(fs::read_to_string(&paths.sites_list)
            .unwrap()
            .ends_with("s-b4test 131 /var/www/s-b4test\n"));
        assert!(!paths.creating.join("s-b4test.json").exists());
        // what the full record does not carry stays beside it, root only (33e28, B4 M2)
        let kept = paths.opened.join("s-b4test.json");
        let back: Value = serde_json::from_str(&fs::read_to_string(&kept).unwrap()).unwrap();
        assert_eq!(back, opening);
        assert_eq!(
            fs::metadata(&kept).unwrap().permissions().mode() & 0o777,
            0o600
        );
        // again: the same record is there, nothing changes
        fs::write(paths.creating.join("s-b4test.json"), "{}").unwrap();
        health_step(&mut h, &paths, &s, &opening).unwrap();
    }

    #[test]
    fn an_unhealthy_site_keeps_its_opening_and_gets_no_record() {
        let paths = scratch("health-bad");
        let s = spec();
        fs::write(paths.creating.join("s-b4test.json"), "{}").unwrap();
        let mut h = host(|_: &[String]| Ok("HTTP/2 502\r\n".to_owned()));
        assert!(health_step(&mut h, &paths, &s, &json!({"started_at": 1})).is_err());
        assert!(paths.creating.join("s-b4test.json").exists());
        assert!(!paths.records.join("s-b4test.json").exists());
        assert!(!paths.sites_list.exists());
    }

    #[test]
    fn a_record_for_the_code_that_is_not_this_one_is_refused() {
        let paths = scratch("health-taken");
        let s = spec();
        fs::write(
            paths.records.join("s-b4test.json"),
            "{\"site\":\"s-b4test\",\"ct\":1}\n",
        )
        .unwrap();
        let mut h = host(|_: &[String]| {
            Ok("HTTP/2 200\r\nlink: <https://b4.example.test/wp-json/>; rel=\"x\"\r\n".to_owned())
        });
        assert!(health_step(&mut h, &paths, &s, &json!({"started_at": 1})).is_err());
    }
}
