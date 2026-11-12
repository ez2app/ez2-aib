// SPDX-License-Identifier: Apache-2.0
//! `site create --continue` (B3): the install and the identity, the last two steps that
//! are still undone on failure ([`aib_cloud::sitecreate::after_failure`]). Both run through
//! the opening's [`Runner`], so the tests hand in a recorder and nothing here touches a host.
//!
//! **Install.** The template already carries the site software; the reviewed installer
//! that sets one site up inside a container (its own system user and PHP pool, the web
//! server's site, the database, the site software and the template package) is pushed in
//! and run, then removed, and the site software must say it is installed. A blank site
//! keeps the site software only: the template package list is removed first. The install
//! also closes a gap f1fae's ten-category map found: the site software's own scheduler
//! runs only when a page is visited, so it is switched off (`DISABLE_WP_CRON`) and a timer
//! in the container runs the due events every five minutes, as the site's own user; the
//! timer must be enabled and active afterwards. The constant and both unit files are read
//! back as written; a site where all of it is already in place is left alone.
//!
//! **Identity.** Everything that came from the template and would be the same on every
//! site is made the site's own: the keys and salts are made anew, the address and the
//! title are set and read back, the first administrators ([`SITE_ADMINS`] on the host:
//! login = the address before `@`) are made with a random password each, set through the
//! program's standard input — never an argument, never a log line — and kept on the host
//! only, root-readable ([`SECRETS`]`/<code>.admin.<login>`, 0600). The installer's own
//! administrator is removed. The site's notice address is the customer's when the opening
//! record carries one, else the first administrator's (the template's is ours). The
//! must-load plugin of the one-time login is put in place and read back
//! ([`crate::loginlink::ensure_mu_plugin`]); the link is issued by `site login-link`.
//!
//! The installer's own output goes to a log in the container and only its exit code and
//! `wp core is-installed` decide ([`SETUP_WRAP`]). Two things only the host can show
//! (33e28 B3 N1): the site's own user must be able to write `wp-config.php` (the installer
//! leaves it 660, owner the site user) for `config set` and `shuffle-salts`; and `pct exec`
//! must pass standard input through to `sudo … wp eval` for the passwords. A title is read
//! back as the site software stores it ([`stored_as`], N2): today's titles are the domain
//! and need nothing, a title of the customer's own with `&` or quotes would.
//!
//! Not done here, interfaces only (see [`fp_site_key`], [`mask_files`]): the footprint key
//! of the site (33e28's design asks F1 for it, and F1 is not open) and the files of the
//! host-information mask inside the container (branch ct-mask2, not in main yet).
//!
//! [`Runner`]: crate::sitecreatecmd::Runner
//! [`SITE_ADMINS`]: crate::sitecreatecmd::SITE_ADMINS

use std::fs;
use std::io::Write;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::Path;

use aib_cloud::site::SiteSpec;
use aib_cloud::sitedefaults::site_title;
use serde_json::{json, Value};

use crate::sitecreatecmd::{admin_logins, HostPaths, Runner};

/// Where each site's own secrets live on the host.
pub const SECRETS: &str = "/etc/ez2-cloud/sites";
/// The reviewed installer on the host.
pub const SETUP_ON_HOST: &str = "/usr/local/sbin/ez2-site-setup";
/// Where the installer is put inside the container for the install, and removed after.
pub const SETUP_IN_CT: &str = "/root/ez2-site-setup";
/// What the installer is given as its fifth argument. On the host's `ez2-site-setup` that
/// argument is `wp:1|0`, and `0` means no site software at all (a static page, the
/// installer stops before the database). A blank opening still has the site software and
/// only goes without the template package (removed before the installer runs), so the
/// install always passes `1`; following `spec.wp` here would make a blank site no site.
const SETUP_WITH_WP: &str = "1";
/// How the installer is run: its own output goes to a log file in the container (made
/// root only before the installer starts; the installer's own umask is left alone), the command answers with the log's last line, cut short, and the installer's
/// exit code. A whole install prints far more than [`crate::sitecreatecmd::CT_OUT_MAX`],
/// and only the exit code and `wp core is-installed` decide (33e28 B3 L1).
/// `$1` is the log, the rest is the installer and its arguments.
pub const SETUP_WRAP: &str =
    "log=$1; shift; (umask 077; : >\"$log\") && chmod 600 \"$log\" || exit 1; \"$@\" >\"$log\" 2>&1; rc=$?; tail -n 1 \"$log\" | cut -c1-200; exit $rc";
/// The administrator the installer makes; the identity step replaces it with the site's own.
pub const SETUP_ADMIN: &str = "ez2admin";
/// The template package list inside the container: a blank site has an empty one, so the
/// installer neither installs the package nor fetches its own small set from the network.
const PACKAGE_LIST: &str = "/root/wp-package.conf";
/// The template package on the host (the old ez2-site-create step 5): its folders go in
/// as one archive, with the list and the paid plugins' keys ([`crate::sitelive::KEYS`]).
pub const PACKAGE_DIR: &str = "/var/lib/ez2-cloud/wp-package";
/// The template package's list on the host.
pub const PACKAGE_CONF: &str = "/etc/ez2-cloud/wp-package.conf";
/// The package archive inside the container for the install, and removed after.
const PACKAGE_IN_CT: &str = "/root/wp-package.tar.gz";
/// The paid plugins' keys inside the container for the install, shredded after.
const KEYS_IN_CT: &str = "/root/wp-package-keys.env";
/// What leaves the container after the install, whatever the installer said: the package,
/// its list and the keys (shredded; removed if shred is missing).
const PACKAGE_CLEANUP: &str = "rm -f /root/wp-package.tar.gz /root/wp-package.conf; shred -u /root/wp-package-keys.env 2>/dev/null || rm -f /root/wp-package-keys.env";
/// The site software's command line inside the container.
const WP_CLI: &str = "/usr/local/bin/wp";
/// Characters of a password, and how many.
const PASSWORD_ALPHABET: &[u8; 62] =
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
const PASSWORD_LEN: usize = 24;

fn owned(parts: &[&str]) -> Vec<String> {
    parts.iter().map(|s| (*s).to_owned()).collect()
}

/// A command inside container `ct`.
pub(crate) fn in_ct(ct: u32, rest: &[&str]) -> Vec<String> {
    let n = ct.to_string();
    let mut v = owned(&["pct", "exec", &n, "--"]);
    v.extend(rest.iter().map(|s| (*s).to_owned()));
    v
}

/// The site software's command line, as the site's own user, on its own folder.
pub(crate) fn wp(spec: &SiteSpec, rest: &[&str]) -> Vec<String> {
    let path = format!("--path=/var/www/{}", spec.code);
    let mut v = owned(&["sudo", "-u", &spec.code, "-H", WP_CLI, &path]);
    v.extend(rest.iter().map(|s| (*s).to_owned()));
    let parts: Vec<&str> = v.iter().map(String::as_str).collect();
    in_ct(spec.ct, &parts)
}

/// The installer's log inside the container.
pub fn setup_log(code: &str) -> String {
    format!("/root/{code}-setup.log")
}

/// The site's first name: its address and its title come from it.
fn first_host(spec: &SiteSpec) -> Result<&str, String> {
    spec.hosts
        .first()
        .map(String::as_str)
        .ok_or_else(|| "the site has no name".to_owned())
}

// ------------------------------------------------------------------ the scheduler

/// The name of the scheduler's timer and service in the container. Neutral: no brand word.
pub fn cron_unit(code: &str) -> String {
    format!("wpcron-{code}")
}

/// The service the timer starts: the due events, once, as the site's own user.
pub fn cron_service(code: &str) -> String {
    format!(
        "[Unit]\nDescription=Scheduled events of site {code}\n\n[Service]\nType=oneshot\nUser={code}\nNice=10\nExecStart={WP_CLI} --path=/var/www/{code} cron event run --due-now --quiet\n"
    )
}

/// The timer: two minutes after the start, then every five minutes.
pub fn cron_timer(code: &str) -> String {
    format!(
        "[Unit]\nDescription=Scheduled events of site {code}, every five minutes\n\n[Timer]\nOnBootSec=2min\nOnUnitActiveSec=5min\nUnit={}.service\n\n[Install]\nWantedBy=timers.target\n",
        cron_unit(code)
    )
}

/// The two unit files of the scheduler in the container, with what each must hold.
fn cron_files(code: &str) -> [(String, String); 2] {
    let unit = cron_unit(code);
    [
        (
            format!("/etc/systemd/system/{unit}.service"),
            cron_service(code),
        ),
        (
            format!("/etc/systemd/system/{unit}.timer"),
            cron_timer(code),
        ),
    ]
}

/// Whether the scheduler is in place as [`scheduler`] leaves it, read back: the site
/// software's own scheduler off, both unit files exactly as written, the timer enabled and
/// active. The fixed word says what is not: `cron_unchecked` could not be read,
/// `cron_not_set` the constant, `cron_unit_differs` a file, `cron_not_running` the timer.
fn scheduler_in_place(runner: &mut impl Runner, spec: &SiteSpec) -> Result<(), String> {
    let got = runner
        .run(&wp(spec, &["config", "get", "DISABLE_WP_CRON"]))
        .map_err(|e| format!("cron_unchecked: config get: {e}"))?;
    if !matches!(got.trim(), "1" | "true") {
        return Err(format!(
            "cron_not_set: DISABLE_WP_CRON reads {:?}",
            got.trim()
        ));
    }
    for (file, text) in cron_files(&spec.code) {
        let there = runner
            .run(&in_ct(spec.ct, &["cat", &file]))
            .map_err(|e| format!("cron_unchecked: {file}: {e}"))?;
        if there != text {
            return Err(format!("cron_unit_differs: {file}"));
        }
    }
    let timer = format!("{}.timer", cron_unit(&spec.code));
    for (ask, want) in [("is-enabled", "enabled"), ("is-active", "active")] {
        let said = runner
            .run(&in_ct(spec.ct, &["systemctl", ask, &timer]))
            .map_err(|e| format!("cron_unchecked: {ask} {timer}: {e}"))?;
        if said.trim() != want {
            return Err(format!(
                "cron_not_running: {timer} {ask} says {:?}",
                said.trim()
            ));
        }
    }
    Ok(())
}

/// The site software's scheduler off and the timer in its place, read back
/// ([`scheduler_in_place`]). A site where it is already in place is left alone and the
/// answer is `false`; otherwise everything is written again and `true` once it reads
/// back. What cannot be read back after writing stops with its fixed word. The install
/// runs it, and so does a restore before its tidying (`restorestepscmd`): a backup may
/// bring back a `wp-config.php` without the scheduler switched off.
pub(crate) fn scheduler(runner: &mut impl Runner, spec: &SiteSpec) -> Result<bool, String> {
    // before writing, "could not read" only means "write it": the read after decides
    if scheduler_in_place(runner, spec).is_ok() {
        return Ok(false);
    }
    runner
        .run(&wp(
            spec,
            &["config", "set", "DISABLE_WP_CRON", "true", "--raw"],
        ))
        .map_err(|e| format!("cron_failed: config set: {e}"))?;
    for (file, text) in cron_files(&spec.code) {
        runner
            .run_input(&in_ct(spec.ct, &["tee", &file]), text.as_bytes())
            .map_err(|e| format!("cron_failed: {file}: {e}"))?;
    }
    let timer = format!("{}.timer", cron_unit(&spec.code));
    runner
        .run(&in_ct(spec.ct, &["systemctl", "daemon-reload"]))
        .map_err(|e| format!("cron_failed: daemon-reload: {e}"))?;
    runner
        .run(&in_ct(spec.ct, &["systemctl", "enable", "--now", &timer]))
        .map_err(|e| format!("cron_failed: enable {timer}: {e}"))?;
    scheduler_in_place(runner, spec)?;
    Ok(true)
}

// ------------------------------------------------------------------ install

/// An address that may stand as an argument and a notice address: one `@`, a dotted
/// domain, nothing that a shell or an option parser would read otherwise.
pub fn valid_email(s: &str) -> bool {
    let Some((local, domain)) = s.split_once('@') else {
        return false;
    };
    (3..=254).contains(&s.len())
        && !local.is_empty()
        && !s.starts_with('-')
        && domain.contains('.')
        && !domain.starts_with('.')
        && !domain.ends_with('.')
        && !domain.contains('@')
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._%+-@".contains(&b))
}

/// The first administrators: `(address, login)` in the order of the host's list.
pub fn admins(text: Option<&str>) -> Result<Vec<(String, String)>, String> {
    let logins = admin_logins(text).map_err(str::to_owned)?;
    let emails: Vec<String> = text
        .unwrap_or_default()
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .map(str::to_owned)
        .collect();
    if emails.len() != logins.len() || !emails.iter().all(|e| valid_email(e)) {
        return Err("admins_unusable".to_owned());
    }
    Ok(emails.into_iter().zip(logins).collect())
}

/// The site's notice address and where it came from: the customer's when the opening
/// record carries one (it must be a usable address), else the first administrator's.
pub fn notice_address(
    record: &Value,
    admins: &[(String, String)],
) -> Result<(String, &'static str), String> {
    match record.get("customer_email") {
        None | Some(Value::Null) => admins
            .first()
            .map(|(e, _)| (e.clone(), "site_admins"))
            .ok_or_else(|| "admins_unknown".to_owned()),
        Some(Value::String(e)) if valid_email(e) => Ok((e.clone(), "customer")),
        Some(_) => Err("customer_email_unusable".to_owned()),
    }
}

fn read_admins(paths: &HostPaths) -> Result<Vec<(String, String)>, String> {
    let text = fs::read_to_string(&paths.site_admins).ok();
    admins(text.as_deref()).map_err(|e| format!("{e}: {}", paths.site_admins.display()))
}

/// One host file into the container, root only.
fn push(runner: &mut impl Runner, ct: &str, from: &str, to: &str) -> Result<(), String> {
    runner
        .run(&owned(&["pct", "push", ct, from, to, "--perms", "0600"]))
        .map(|_| ())
        .map_err(|e| format!("install_failed: push {to}: {e}"))
}

/// The template package goes in before the installer (the old ez2-site-create step 5;
/// the template container has none, so without it no paid plugin is installed and the
/// licences step cannot pass). A blank opening gets an empty list instead. Keys go by
/// file, never as an argument.
fn push_package(
    runner: &mut impl Runner,
    paths: &HostPaths,
    spec: &SiteSpec,
) -> Result<(), String> {
    if !spec.wp {
        return runner
            .run(&in_ct(spec.ct, &["truncate", "-s", "0", PACKAGE_LIST]))
            .map(|_| ())
            .map_err(|e| format!("install_failed: {PACKAGE_LIST}: {e}"));
    }
    if !paths.package.join("plugins").is_dir() || !paths.package_conf.is_file() {
        return Err(format!(
            "install_failed: the template package is not on the host ({} with plugins/, {})",
            paths.package.display(),
            paths.package_conf.display()
        ));
    }
    let n = spec.ct.to_string();
    let archive = format!("/tmp/{}-wp-package.tar.gz", spec.code);
    let dir = paths.package.to_string_lossy().into_owned();
    let made = runner
        .run(&owned(&["tar", "-czf", &archive, "-C", &dir, "."]))
        .map_err(|e| format!("install_failed: pack the template package: {e}"));
    let pushed = made.and_then(|_| push(runner, &n, &archive, PACKAGE_IN_CT));
    // the archive leaves the host whatever happened
    let dropped = runner.run(&owned(&["rm", "-f", &archive]));
    pushed?;
    dropped.map_err(|e| format!("install_failed: {archive} stays on the host: {e}"))?;
    push(
        runner,
        &n,
        &paths.package_conf.to_string_lossy(),
        PACKAGE_LIST,
    )?;
    if paths.keys.is_file() {
        push(runner, &n, &paths.keys.to_string_lossy(), KEYS_IN_CT)?;
    }
    Ok(())
}

/// The install: the reviewed installer pushed in, run, removed; the site software must
/// say it is installed; then the scheduler.
pub fn install_step(
    runner: &mut impl Runner,
    paths: &HostPaths,
    spec: &SiteSpec,
    record: &Value,
) -> Result<(), String> {
    let host = first_host(spec)?.to_owned();
    let admins = read_admins(paths)?;
    let (notice, _) = notice_address(record, &admins)?;
    let n = spec.ct.to_string();
    push_package(runner, paths, spec)?;
    let setup = paths.setup.to_string_lossy().into_owned();
    runner
        .run(&owned(&[
            "pct",
            "push",
            &n,
            &setup,
            SETUP_IN_CT,
            "--perms",
            "0700",
        ]))
        .map_err(|e| format!("install_failed: push the installer: {e}"))?;
    let hosts = spec.hosts.join(",");
    let log = setup_log(&spec.code);
    let ran = runner.run(&in_ct(
        spec.ct,
        &[
            "sh",
            "-c",
            SETUP_WRAP,
            "sh",
            &log,
            SETUP_IN_CT,
            &spec.code,
            &host,
            &hosts,
            &spec.locale,
            SETUP_WITH_WP,
            site_title(&host),
            &notice,
        ],
    ));
    // the installer and the package leave the container whatever it said
    let removed = runner.run(&in_ct(spec.ct, &["rm", "-f", SETUP_IN_CT]));
    let cleaned = runner.run(&in_ct(spec.ct, &["sh", "-c", PACKAGE_CLEANUP]));
    ran.map_err(|e| format!("install_failed: the installer: {e}"))?;
    removed.map_err(|e| format!("install_failed: {SETUP_IN_CT} stays in the container: {e}"))?;
    cleaned.map_err(|e| format!("install_failed: the package stays in the container: {e}"))?;
    runner
        .run(&wp(spec, &["core", "is-installed"]))
        .map_err(|e| format!("install_not_done: the site software is not installed: {e}"))?;
    scheduler(runner, spec).map(|_| ())
}

// ------------------------------------------------------------------ identity

/// A password from random bytes: only bytes that fall evenly on the alphabet are used,
/// so every character is as likely as every other. `None` when the bytes run out first.
pub fn password_from(bytes: &[u8]) -> Option<String> {
    let even = 256 - 256 % PASSWORD_ALPHABET.len();
    let s: String = bytes
        .iter()
        .filter(|b| usize::from(**b) < even)
        .map(|b| char::from(PASSWORD_ALPHABET[usize::from(*b) % PASSWORD_ALPHABET.len()]))
        .take(PASSWORD_LEN)
        .collect();
    (s.len() == PASSWORD_LEN).then_some(s)
}

/// The program that sets a password read from its standard input and reads it back:
/// the user is fetched again and the password checked against what is stored, so "set"
/// is said only for a password that now logs in (33e28 B3 L2). The login is put in
/// the program text, so it must be one the login rule makes (letters, digits, `.`, `_`,
/// `-`): anything else is refused before a program is made.
pub fn set_password_program(login: &str) -> Option<String> {
    let plain = !login.is_empty()
        && login
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b"._-".contains(&b));
    plain.then(|| {
        format!(
            "$u = get_user_by('login', '{login}'); if (!$u) {{ exit(3); }} $p = rtrim(stream_get_contents(STDIN), \"\\n\"); if (strlen($p) < 20) {{ exit(4); }} wp_set_password($p, $u->ID); $v = get_user_by('id', $u->ID); if (!$v || !wp_check_password($p, $v->user_pass, $v->ID)) {{ exit(5); }} echo \"set\\n\";"
        )
    })
}

/// `secrets/<code>.admin.<login>`, root only, written whole or not at all.
pub fn write_secret(dir: &Path, code: &str, login: &str, password: &str) -> Result<(), String> {
    let path = dir.join(format!("{code}.admin.{login}"));
    let tmp = dir.join(format!(".{code}.admin.{login}.tmp"));
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(&tmp)
        .map_err(|e| format!("{}: {e}", tmp.display()))?;
    f.set_permissions(fs::Permissions::from_mode(0o600))
        .and_then(|()| f.write_all(password.as_bytes()))
        .and_then(|()| f.write_all(b"\n"))
        .and_then(|()| f.sync_all())
        .map_err(|e| format!("{}: {e}", tmp.display()))?;
    fs::rename(&tmp, &path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mode = fs::metadata(&path)
        .map_err(|e| format!("{}: {e}", path.display()))?
        .permissions()
        .mode();
    if mode & 0o777 != 0o600 {
        return Err(format!("{} is {:o}, not 600", path.display(), mode & 0o777));
    }
    Ok(())
}

/// Every password file of `code` under `dir` removed (a site that is undone keeps none).
/// No directory is nothing to remove.
pub fn drop_admin_secrets(dir: &Path, code: &str) -> Result<(), String> {
    let list = match fs::read_dir(dir) {
        Ok(l) => l,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(e) => return Err(format!("{}: {e}", dir.display())),
    };
    let mine = format!("{code}.admin.");
    let tmp = format!(".{code}.admin.");
    for entry in list {
        let entry = entry.map_err(|e| format!("{}: {e}", dir.display()))?;
        let name = entry.file_name().to_string_lossy().into_owned();
        if name.starts_with(&mine) || name.starts_with(&tmp) {
            fs::remove_file(entry.path())
                .map_err(|e| format!("{}: {e}", entry.path().display()))?;
        }
    }
    Ok(())
}

/// Where a step that is not done here would go: it says so, and the record lists it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Pending {
    /// Waits for something outside this host; the word says what.
    Later(&'static str),
}

/// TODO(33e28 B3 L4): files the installer leaves in the container, root only, that are not
/// cleared yet: its credentials file (the removed installer administrator's password and
/// the database password that wp-config already holds) and its log. The container is the
/// customer's, so they are listed for a person until a later step clears them.
pub fn left_in_container(code: &str) -> [String; 2] {
    [
        format!("setup_credentials: /root/{code}-credentials stays in the container"),
        format!("setup_log: {} stays in the container", setup_log(code)),
    ]
}

/// TODO(F1): the site's own footprint key (33e28, DESIGN-fp-site-key-per-site-33e28-1111):
/// ask F1 for the site's key, write the loader whole, read it back. F1 is not open, so the
/// template's key stays until then.
pub fn fp_site_key(_spec: &SiteSpec) -> Pending {
    Pending::Later("fp_site_key: F1 is not open")
}

/// TODO(ct-mask2): the files of the host-information mask inside the container
/// (aib_cloud::mask), written here once that branch is in main.
pub fn mask_files(_spec: &SiteSpec) -> Pending {
    Pending::Later("mask_files: ct-mask2 is not in main")
}

/// What an option reads back as once the site software has stored it. The title is
/// escaped as HTML on the way in (`&` reads back as `&amp;`, 33e28 B3 N2); the
/// addresses are kept as given.
pub fn stored_as(key: &str, value: &str) -> String {
    if key != "blogname" {
        return value.to_owned();
    }
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#039;")
}

fn user_id(runner: &mut impl Runner, spec: &SiteSpec, login: &str) -> Option<String> {
    runner
        .run(&wp(spec, &["user", "get", login, "--field=ID"]))
        .ok()
        .map(|s| s.trim().to_owned())
        .filter(|s| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()))
}

/// The identity: keys and salts anew, address and title, the first administrators with
/// their own passwords, the installer's administrator gone, the notice address.
pub fn identity_step(
    runner: &mut impl Runner,
    paths: &HostPaths,
    spec: &SiteSpec,
    record: &mut Value,
) -> Result<(), String> {
    let host = first_host(spec)?.to_owned();
    let url = format!("https://{host}");
    let title = site_title(&host).to_owned();
    runner
        .run(&wp(spec, &["config", "shuffle-salts"]))
        .map_err(|e| format!("identity_failed: shuffle-salts: {e}"))?;
    for (key, value) in [("siteurl", &url), ("home", &url), ("blogname", &title)] {
        runner
            .run(&wp(spec, &["option", "update", key, value]))
            .map_err(|e| format!("identity_failed: {key}: {e}"))?;
        let back = runner
            .run(&wp(spec, &["option", "get", key]))
            .map_err(|e| format!("identity_unchecked: {key}: {e}"))?;
        if back.trim() != stored_as(key, value) {
            return Err(format!("identity_not_set: {key} reads {:?}", back.trim()));
        }
    }
    let admins = read_admins(paths)?;
    // The installer gave its administrator the site's notice address, which is the first
    // site administrator's when the customer gave none; the site software refuses a second
    // user with that address, so the installer's administrator lets go of it first (e1a
    // 11-12: user create failed). It is removed after the loop.
    if !admins.iter().any(|(_, l)| l == SETUP_ADMIN) && user_id(runner, spec, SETUP_ADMIN).is_some()
    {
        let parked = format!("{SETUP_ADMIN}@{}.invalid", spec.code);
        let flag = format!("--user_email={parked}");
        runner
            // --skip-email: the site software would otherwise mail "Email Changed" to the
            // address let go of, the first administrator's (c477a M1; no mail on opening)
            .run(&wp(
                spec,
                &["user", "update", SETUP_ADMIN, &flag, "--skip-email"],
            ))
            .map_err(|e| format!("identity_failed: {SETUP_ADMIN} address: {e}"))?;
        let back = runner
            .run(&wp(
                spec,
                &["user", "get", SETUP_ADMIN, "--field=user_email"],
            ))
            .map_err(|e| format!("identity_unchecked: {SETUP_ADMIN} address: {e}"))?;
        if back.trim() != parked {
            return Err(format!(
                "identity_not_set: {SETUP_ADMIN} address reads {:?}",
                back.trim()
            ));
        }
    }
    let mut first_id: Option<String> = None;
    let mut logins = Vec::new();
    for (email, login) in &admins {
        let program = set_password_program(login)
            .ok_or_else(|| format!("identity_failed: {login:?} is not a usable login"))?;
        let id = match user_id(runner, spec, login) {
            Some(id) => id,
            None => runner
                .run(&wp(
                    spec,
                    &[
                        "user",
                        "create",
                        login,
                        email,
                        "--role=administrator",
                        "--porcelain",
                    ],
                ))
                .map_err(|e| format!("identity_failed: user create {login}: {e}"))?
                .trim()
                .to_owned(),
        };
        if id.is_empty() || !id.bytes().all(|b| b.is_ascii_digit()) {
            return Err(format!("identity_unchecked: {login} has no id"));
        }
        runner
            .run(&wp(spec, &["user", "set-role", login, "administrator"]))
            .map_err(|e| format!("identity_failed: set-role {login}: {e}"))?;
        let password = password_from(&runner.random(4 * PASSWORD_LEN)?)
            .ok_or("identity_failed: not enough random bytes for a password")?;
        // the password goes in on standard input; an error names the login only
        let said = runner
            .run_input(&wp(spec, &["eval", &program]), password.as_bytes())
            .map_err(|_| format!("identity_failed: the password of {login} was not set"))?;
        if said.trim() != "set" {
            return Err(format!(
                "identity_failed: the password of {login} was not set"
            ));
        }
        write_secret(&paths.secrets, &spec.code, login, &password)
            .map_err(|e| format!("identity_failed: {e}"))?;
        first_id.get_or_insert(id);
        logins.push(login.clone());
    }
    let first_id = first_id.ok_or("admins_unknown")?;
    if !logins.iter().any(|l| l == SETUP_ADMIN) && user_id(runner, spec, SETUP_ADMIN).is_some() {
        let reassign = format!("--reassign={first_id}");
        runner
            .run(&wp(
                spec,
                &["user", "delete", SETUP_ADMIN, &reassign, "--yes"],
            ))
            .map_err(|e| format!("identity_failed: remove {SETUP_ADMIN}: {e}"))?;
        if user_id(runner, spec, SETUP_ADMIN).is_some() {
            return Err(format!("identity_not_set: {SETUP_ADMIN} is still there"));
        }
    }
    let (notice, from) = notice_address(record, &admins)?;
    for key in ["admin_email", "new_admin_email"] {
        runner
            .run(&wp(spec, &["option", "update", key, &notice]))
            .map_err(|e| format!("identity_failed: {key}: {e}"))?;
    }
    let back = runner
        .run(&wp(spec, &["option", "get", "admin_email"]))
        .map_err(|e| format!("identity_unchecked: admin_email: {e}"))?;
    if back.trim() != notice {
        return Err("identity_not_set: admin_email reads otherwise".to_owned());
    }
    // the one-time login (Andy 11-11 23:1x): the plugin that makes a link a direct login,
    // in place and read back; the link itself comes from `site login-link`
    let plugin = crate::loginlink::ensure_mu_plugin(runner, spec)
        .map_err(|e| format!("identity_failed: {e}"))?;
    record["login_plugin"] = json!(plugin);
    let mut pending: Vec<String> = [fp_site_key(spec), mask_files(spec)]
        .into_iter()
        .map(|Pending::Later(w)| w.to_owned())
        .collect();
    pending.extend(left_in_container(&spec.code));
    record["admins"] = json!(logins);
    record["admin_email_from"] = json!(from);
    record["later"] = json!(pending);
    Ok(())
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    const ADMINS: &str = "# first administrators\nops@example.test\nOps@other.example.test\n";

    fn spec(wp: bool) -> SiteSpec {
        SiteSpec::new(
            "s-b3test",
            131,
            41,
            &["new.example.test"],
            wp,
            "zh_TW",
            aib_cloud::site::Limits::START,
        )
        .unwrap()
    }

    fn scratch(name: &str) -> HostPaths {
        let d = std::env::temp_dir().join(format!("aib-b3-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(d.join("secrets")).unwrap();
        fs::write(d.join("site-admins"), ADMINS).unwrap();
        fs::create_dir_all(d.join("wp-package/plugins")).unwrap();
        fs::write(
            d.join("wp-package.conf"),
            "plugin|wp-seopress|pkg|activate|-\n",
        )
        .unwrap();
        fs::write(d.join("keys.env"), "SEOPRESS_PRO_LICENSE_KEY=\"tmplkey\"\n").unwrap();
        HostPaths {
            creating: d.join("creating"),
            lxc_conf: d.join("lxc"),
            firewall: d.join("firewall"),
            secrets: d.join("secrets"),
            site_admins: d.join("site-admins"),
            setup: PathBuf::from("/usr/local/sbin/ez2-site-setup"),
            package: d.join("wp-package"),
            package_conf: d.join("wp-package.conf"),
            records: d.join("records"),
            sites_list: d.join("sites.list"),
            keys: d.join("keys.env"),
            opened: d.join("opened"),
            cloudflare: d.join("cloudflare.env"),
            rosters: d.join("rosters"),
            cf_ledger: d.join("cloudflare.jsonl"),
        }
    }

    /// A container where every command works, the site software keeps its options and
    /// users, and what is fed on standard input is kept.
    struct Site {
        calls: Vec<String>,
        fed: Vec<(String, Vec<u8>)>,
        options: std::collections::BTreeMap<String, String>,
        users: Vec<String>,
        /// each user's address; the installer's administrator has the install's notice
        /// address, as ez2-site-setup gives it
        emails: std::collections::BTreeMap<String, String>,
        /// the options after the address of the last `user update`
        updated: Vec<String>,
        /// a `user update` that says it worked but keeps the old address
        sticky_email: bool,
        cron: Option<String>,
        enabled: bool,
        /// the one-time login plugin in the site
        mu: Option<Vec<u8>>,
        /// files written with `tee`, read with `cat`
        files: std::collections::BTreeMap<String, Vec<u8>>,
        /// `tee` keeps something other than what it was fed
        garble: bool,
        /// a command (joined argv contains it) that fails
        broken: Option<&'static str>,
    }

    impl Site {
        fn new() -> Self {
            Site {
                calls: Vec::new(),
                fed: Vec::new(),
                options: Default::default(),
                users: vec![SETUP_ADMIN.to_owned()],
                emails: [(SETUP_ADMIN.to_owned(), "ops@example.test".to_owned())].into(),
                updated: Vec::new(),
                sticky_email: false,
                cron: None,
                enabled: false,
                mu: None,
                files: Default::default(),
                garble: false,
                broken: None,
            }
        }
        /// the arguments after the site software's `--path=`
        fn wp_args(argv: &[String]) -> Option<Vec<String>> {
            let i = argv.iter().position(|a| a.starts_with("--path="))?;
            Some(argv[i + 1..].to_vec())
        }
    }

    impl Runner for Site {
        fn run(&mut self, argv: &[String]) -> Result<String, String> {
            let line = argv.join(" ");
            self.calls.push(line.clone());
            if self.broken.is_some_and(|b| line.contains(b)) {
                return Err("broken".to_owned());
            }
            if line.contains("ez2-onetime-login.php") {
                let sha = self
                    .mu
                    .as_ref()
                    .map(|b| format!("{}  x\n", crate::opstore::sha256_hex(b).unwrap()));
                let reads_back = argv.len() >= 2 && argv[argv.len() - 2] == "sha256sum";
                return match sha {
                    Some(sha) => Ok(sha),
                    None if reads_back => Err("no file".to_owned()),
                    None => Ok("none\n".to_owned()),
                };
            }
            let Some(a) = Self::wp_args(argv) else {
                if let [.., cat, file] = argv {
                    if cat == "cat" {
                        return self
                            .files
                            .get(file)
                            .map(|b| String::from_utf8_lossy(b).into_owned())
                            .ok_or("no such file".to_owned());
                    }
                }
                let asks = line.contains("is-enabled") || line.contains("is-active");
                if asks && !self.enabled {
                    return Err("disabled".to_owned());
                }
                if line.contains("is-enabled") {
                    return Ok("enabled\n".to_owned());
                }
                if line.contains("is-active") {
                    return Ok("active\n".to_owned());
                }
                if line.contains("enable --now") {
                    self.enabled = true;
                }
                return Ok(String::new());
            };
            let a: Vec<&str> = a.iter().map(String::as_str).collect();
            match a.as_slice() {
                ["config", "set", "DISABLE_WP_CRON", v, "--raw"] => {
                    self.cron = Some(if *v == "true" { "1" } else { "" }.to_owned());
                    Ok(String::new())
                }
                ["config", "get", "DISABLE_WP_CRON"] => self.cron.clone().ok_or("no".to_owned()),
                ["option", "update", k, v] => {
                    self.options.insert((*k).to_owned(), (*v).to_owned());
                    Ok(String::new())
                }
                ["option", "get", k] => self
                    .options
                    .get(*k)
                    .map(|v| format!("{v}\n"))
                    .ok_or("no option".to_owned()),
                ["user", "get", login, "--field=ID"] => self
                    .users
                    .iter()
                    .position(|u| u == login)
                    .map(|i| format!("{}\n", i + 1))
                    .ok_or("no user".to_owned()),
                ["user", "create", login, email, ..] => {
                    // the site software takes no second user with an address in use
                    if self.emails.values().any(|e| e == email) {
                        return Err("Error: Sorry, that email address is already used!".to_owned());
                    }
                    self.users.push((*login).to_owned());
                    self.emails.insert((*login).to_owned(), (*email).to_owned());
                    Ok(format!("{}\n", self.users.len()))
                }
                ["user", "update", login, flag, rest @ ..] => {
                    let email = flag.strip_prefix("--user_email=").ok_or("flag")?;
                    self.updated = rest.iter().map(|r| (*r).to_owned()).collect();
                    if !self.sticky_email {
                        self.emails.insert((*login).to_owned(), email.to_owned());
                    }
                    Ok(String::new())
                }
                ["user", "get", login, "--field=user_email"] => self
                    .emails
                    .get(*login)
                    .map(|e| format!("{e}\n"))
                    .ok_or("no user".to_owned()),
                ["user", "delete", login, ..] => {
                    self.users.retain(|u| u != login);
                    self.emails.remove(*login);
                    Ok(String::new())
                }
                _ => Ok(String::new()),
            }
        }

        fn run_input(&mut self, argv: &[String], input: &[u8]) -> Result<String, String> {
            let line = argv.join(" ");
            self.calls.push(line.clone());
            self.fed.push((line.clone(), input.to_vec()));
            if self.broken.is_some_and(|b| line.contains(b)) {
                return Err("broken".to_owned());
            }
            if line.contains("ez2-onetime-login.php") {
                self.mu = Some(input.to_vec());
                return Ok(String::new());
            }
            if let [.., tee, file] = argv {
                if tee == "tee" {
                    let kept = if self.garble {
                        b"other".to_vec()
                    } else {
                        input.to_vec()
                    };
                    self.files.insert(file.clone(), kept);
                }
            }
            Ok(if line.contains(" eval ") {
                "set\n".to_owned()
            } else {
                String::from_utf8_lossy(input).into_owned()
            })
        }

        fn random(&mut self, n: usize) -> Result<Vec<u8>, String> {
            Ok((0..n).map(|i| u8::try_from(i * 7 % 251).unwrap()).collect())
        }
    }

    #[test]
    fn the_install_runs_the_installer_and_puts_the_scheduler_on_a_timer() {
        let paths = scratch("install");
        let mut site = Site::new();
        let record = json!({ "blank": false });
        install_step(&mut site, &paths, &spec(true), &record).unwrap();
        let setup = format!(
            "pct exec 131 -- sh -c {SETUP_WRAP} sh /root/s-b3test-setup.log {SETUP_IN_CT} s-b3test new.example.test new.example.test zh_TW 1 new ops@example.test"
        );
        assert!(site.calls.contains(&setup), "{:#?}", site.calls);
        assert!(site
            .calls
            .contains(&format!("pct exec 131 -- rm -f {SETUP_IN_CT}")));
        assert!(
            !site
                .calls
                .iter()
                .any(|c| c.contains(&format!("truncate -s 0 {PACKAGE_LIST}"))),
            "a template site keeps its package"
        );
        assert_eq!(site.cron.as_deref(), Some("1"));
        let unit = cron_unit("s-b3test");
        assert_eq!(site.fed.len(), 2);
        assert!(site.fed[0]
            .0
            .ends_with(&format!("tee /etc/systemd/system/{unit}.service")));
        assert_eq!(site.fed[0].1, cron_service("s-b3test").as_bytes());
        assert_eq!(site.fed[1].1, cron_timer("s-b3test").as_bytes());
        assert!(site.enabled);
        assert!(cron_service("s-b3test").contains("User=s-b3test\n"));
        assert!(cron_service("s-b3test").contains("cron event run --due-now"));
        assert!(cron_timer("s-b3test").contains("OnUnitActiveSec=5min\n"));
        assert!(
            !cron_unit("s-b3test").contains("ez2"),
            "no brand word in the container's units"
        );
    }

    /// A template opening gets the package, its list and the keys before the installer
    /// (the old ez2-site-create step 5); all three leave the container after, the keys
    /// shredded, and no key is ever an argument (audit 11-12 #1, #9).
    #[test]
    fn the_template_package_goes_in_before_the_installer_and_out_after() {
        let paths = scratch("package");
        let mut site = Site::new();
        install_step(&mut site, &paths, &spec(true), &json!({})).unwrap();
        let at = |part: &str| {
            site.calls
                .iter()
                .position(|c| c.contains(part))
                .unwrap_or_else(|| panic!("{part}: {:#?}", site.calls))
        };
        let dir = paths.package.display().to_string();
        let tar = at(&format!(
            "tar -czf /tmp/s-b3test-wp-package.tar.gz -C {dir} ."
        ));
        let archive =
            at("pct push 131 /tmp/s-b3test-wp-package.tar.gz /root/wp-package.tar.gz --perms 0600");
        let dropped = at("rm -f /tmp/s-b3test-wp-package.tar.gz");
        let list = at(&format!(
            "pct push 131 {} /root/wp-package.conf --perms 0600",
            paths.package_conf.display()
        ));
        let keys = at(&format!(
            "pct push 131 {} /root/wp-package-keys.env --perms 0600",
            paths.keys.display()
        ));
        let setup = at("/root/ez2-site-setup s-b3test");
        let cleaned = at(&format!("pct exec 131 -- sh -c {PACKAGE_CLEANUP}"));
        assert!(
            tar < archive && archive < dropped && dropped < list && list < keys && keys < setup
        );
        assert!(setup < cleaned);
        assert!(PACKAGE_CLEANUP.contains("shred -u /root/wp-package-keys.env"));
        assert!(
            !site.calls.iter().any(|c| c.contains("tmplkey")),
            "a key is never an argument"
        );
        // the cleanup runs even when the installer fails
        let mut site = Site::new();
        site.broken = Some("/root/ez2-site-setup s-b3test");
        assert!(install_step(&mut site, &paths, &spec(true), &json!({})).is_err());
        assert!(site.calls.iter().any(|c| c.contains(PACKAGE_CLEANUP)));
        // no package on the host: stopped before the installer, nothing fetched instead
        fs::remove_file(&paths.package_conf).unwrap();
        let mut site = Site::new();
        let err = install_step(&mut site, &paths, &spec(true), &json!({})).unwrap_err();
        assert!(
            err.contains("the template package is not on the host"),
            "{err}"
        );
        assert!(!site.calls.iter().any(|c| c.contains("ez2-site-setup")));
    }

    #[test]
    fn a_blank_install_drops_the_template_package_first() {
        let paths = scratch("blank");
        let mut site = Site::new();
        install_step(&mut site, &paths, &spec(false), &json!({ "blank": true })).unwrap();
        let drop = format!("pct exec 131 -- truncate -s 0 {PACKAGE_LIST}");
        let at = site.calls.iter().position(|c| *c == drop).expect("dropped");
        let setup = site
            .calls
            .iter()
            .position(|c| c.contains("/root/ez2-site-setup s-b3test"))
            .unwrap();
        assert!(at < setup);
        assert!(
            !site
                .calls
                .iter()
                .any(|c| c.contains("wp-package.tar.gz -C")),
            "a blank opening gets no package"
        );
    }

    #[test]
    fn a_scheduler_that_is_not_running_stops_the_install() {
        let paths = scratch("cronoff");
        let mut site = Site::new();
        site.broken = Some("enable --now");
        let err = install_step(&mut site, &paths, &spec(true), &json!({})).unwrap_err();
        assert!(err.starts_with("cron_failed"), "{err}");
        // the timer was written but never started: is-enabled must not be believed blindly
        let mut site = Site::new();
        site.broken = Some("is-active");
        site.enabled = true;
        let err = install_step(&mut site, &paths, &spec(true), &json!({})).unwrap_err();
        assert!(err.starts_with("cron_unchecked"), "{err}");
    }

    #[test]
    fn a_scheduler_already_in_place_is_left_alone() {
        let mut site = Site::new();
        assert_eq!(scheduler(&mut site, &spec(true)), Ok(true));
        let written = site.fed.len();
        let calls = site.calls.len();
        assert_eq!(scheduler(&mut site, &spec(true)), Ok(false));
        assert_eq!(site.fed.len(), written, "nothing written again");
        let again = &site.calls[calls..];
        assert!(
            again
                .iter()
                .all(|c| !c.contains(" set ") && !c.contains("--now")),
            "{again:#?}"
        );
        // one file changed since: everything is written again and read back
        let timer = cron_files("s-b3test")[1].0.clone();
        site.files.insert(timer.clone(), b"[Timer]\n".to_vec());
        assert_eq!(scheduler(&mut site, &spec(true)), Ok(true));
        assert_eq!(site.files[&timer], cron_timer("s-b3test").as_bytes());
        // and the constant switched back on since: the same
        site.cron = Some(String::new());
        assert_eq!(scheduler(&mut site, &spec(true)), Ok(true));
        assert_eq!(site.cron.as_deref(), Some("1"));
    }

    #[test]
    fn a_scheduler_is_read_back_as_written() {
        // a unit file that does not hold what was written
        let mut site = Site::new();
        site.garble = true;
        let err = scheduler(&mut site, &spec(true)).unwrap_err();
        assert!(err.starts_with("cron_unit_differs"), "{err}");
        // a unit file that cannot be read back after writing
        let mut site = Site::new();
        site.broken = Some(" cat ");
        let err = scheduler(&mut site, &spec(true)).unwrap_err();
        assert!(err.starts_with("cron_unchecked"), "{err}");
        // the constant that does not read as on
        let mut site = Site::new();
        site.broken = Some("set DISABLE_WP_CRON");
        let err = scheduler(&mut site, &spec(true)).unwrap_err();
        assert!(err.starts_with("cron_failed"), "{err}");
        let mut site = Site::new();
        site.broken = Some("get DISABLE_WP_CRON");
        let err = scheduler(&mut site, &spec(true)).unwrap_err();
        assert!(err.starts_with("cron_unchecked"), "{err}");
    }

    #[test]
    fn an_installer_that_fails_is_still_taken_out_of_the_container() {
        let paths = scratch("setupfail");
        let mut site = Site::new();
        site.broken = Some("/root/ez2-site-setup s-b3test");
        let err = install_step(&mut site, &paths, &spec(true), &json!({})).unwrap_err();
        assert!(err.starts_with("install_failed: the installer"), "{err}");
        // the installer goes, then the template package and its keys (always last)
        let n = site.calls.len();
        assert_eq!(
            site.calls[n - 2..],
            [
                format!("pct exec 131 -- rm -f {SETUP_IN_CT}"),
                format!("pct exec 131 -- sh -c {PACKAGE_CLEANUP}"),
            ]
        );
    }

    /// The installer's administrator has the first administrator's address (the notice
    /// address when the customer gave none): it lets go of it before that administrator is
    /// created, or the site software refuses the user (e1a 11-12).
    #[test]
    fn the_installer_s_address_is_let_go_before_the_first_administrator() {
        let paths = scratch("setupmail");
        let mut site = Site::new();
        assert_eq!(site.emails[SETUP_ADMIN], "ops@example.test");
        let mut record = json!({});
        identity_step(&mut site, &paths, &spec(true), &mut record).unwrap();
        let at = |part: &str| site.calls.iter().position(|c| c.contains(part)).unwrap();
        let parked = at("user update ez2admin --user_email=ez2admin@s-b3test.invalid");
        assert!(
            parked < at("user create ops ops@example.test"),
            "{:#?}",
            site.calls
        );
        assert_eq!(site.users, ["ops", "ops2"]);
        assert_eq!(site.emails["ops"], "ops@example.test");
        assert_eq!(
            site.updated,
            ["--skip-email"],
            "no Email Changed mail (c477a M1)"
        );
        // an address that is not taken stops the step before any user is created (c477a N1)
        let mut site = Site::new();
        site.sticky_email = true;
        let err = identity_step(&mut site, &paths, &spec(true), &mut json!({})).unwrap_err();
        assert!(
            err.starts_with("identity_not_set: ez2admin address"),
            "{err}"
        );
        assert!(!site.calls.iter().any(|c| c.contains("user create")));
        // an address that does not read back stops the step before any user is created
        let mut site = Site::new();
        site.broken = Some("--field=user_email");
        let err = identity_step(&mut site, &paths, &spec(true), &mut json!({})).unwrap_err();
        assert!(
            err.starts_with("identity_unchecked: ez2admin address"),
            "{err}"
        );
        assert!(!site.calls.iter().any(|c| c.contains("user create")));
    }

    #[test]
    fn the_identity_makes_the_site_its_own() {
        let paths = scratch("identity");
        let mut site = Site::new();
        let mut record = json!({ "customer_email": "owner@customer.example.test" });
        identity_step(&mut site, &paths, &spec(true), &mut record).unwrap();
        assert!(site
            .calls
            .iter()
            .any(|c| c.ends_with("config shuffle-salts")));
        assert_eq!(site.options["siteurl"], "https://new.example.test");
        assert_eq!(site.options["home"], "https://new.example.test");
        assert_eq!(site.options["blogname"], "new");
        assert_eq!(site.options["admin_email"], "owner@customer.example.test");
        assert_eq!(
            site.options["new_admin_email"],
            "owner@customer.example.test"
        );
        assert_eq!(record["admin_email_from"], "customer");
        assert_eq!(record["admins"], json!(["ops", "ops2"]));
        // the one-time login plugin is in the site (Andy 11-11: a link logs straight in)
        assert_eq!(
            site.mu.as_deref(),
            Some(crate::loginlink::MU_PLUGIN.as_bytes())
        );
        assert_eq!(record["login_plugin"], "written");
        assert_eq!(
            site.users,
            ["ops", "ops2"],
            "the installer's administrator is gone"
        );
        let later = record["later"].as_array().unwrap();
        assert_eq!(later.len(), 4, "{later:?}");
        assert!(later.contains(&json!(
            "setup_credentials: /root/s-b3test-credentials stays in the container"
        )));
        // a password per administrator, on the host only, root only, never in an argument
        for login in ["ops", "ops2"] {
            let f = paths.secrets.join(format!("s-b3test.admin.{login}"));
            let pw = fs::read_to_string(&f).unwrap();
            let pw = pw.trim_end();
            assert_eq!(pw.len(), PASSWORD_LEN);
            assert_eq!(
                fs::metadata(&f).unwrap().permissions().mode() & 0o777,
                0o600
            );
            assert!(
                !site.calls.iter().any(|c| c.contains(pw)),
                "{login}'s password in an argument"
            );
            assert!(site
                .fed
                .iter()
                .any(|(c, input)| c.contains(&format!("'{login}'")) && input == pw.as_bytes()));
        }
        // without a customer address the first administrator's stands
        let paths = scratch("identity2");
        let mut site = Site::new();
        let mut record = json!({});
        identity_step(&mut site, &paths, &spec(true), &mut record).unwrap();
        assert_eq!(site.options["admin_email"], "ops@example.test");
        assert_eq!(record["admin_email_from"], "site_admins");
    }

    #[test]
    fn a_password_that_was_not_set_is_not_kept_and_names_no_secret() {
        let paths = scratch("pwfail");
        let mut site = Site::new();
        site.broken = Some(" eval ");
        let err = identity_step(&mut site, &paths, &spec(true), &mut json!({})).unwrap_err();
        assert_eq!(err, "identity_failed: the password of ops was not set");
        assert!(fs::read_dir(&paths.secrets).unwrap().next().is_none());
    }

    #[test]
    fn an_address_that_does_not_read_back_stops_the_identity() {
        let paths = scratch("readback");
        let mut site = Site::new();
        site.broken = Some("option get siteurl");
        let err = identity_step(&mut site, &paths, &spec(true), &mut json!({})).unwrap_err();
        assert!(err.starts_with("identity_unchecked: siteurl"), "{err}");
    }

    #[test]
    fn unusable_addresses_and_logins_are_refused() {
        assert!(valid_email("a@b.test"));
        for bad in [
            "",
            "a",
            "a@b",
            "-a@b.test",
            "a@.b",
            "a@b.",
            "a b@c.test",
            "a@b@c.test",
            "a;rm@b.test",
        ] {
            assert!(!valid_email(bad), "{bad}");
        }
        assert!(notice_address(&json!({ "customer_email": "x; y@z.test" }), &[]).is_err());
        assert!(notice_address(&json!({ "customer_email": 7 }), &[]).is_err());
        assert!(notice_address(&json!({}), &[]).is_err());
        assert!(set_password_program("ops.2_x-y").is_some());
        for bad in ["", "O'x", "a b", "a');"] {
            assert!(set_password_program(bad).is_none(), "{bad}");
        }
        assert!(admins(None).is_err());
        assert!(admins(Some("# none\n")).is_err());
    }

    #[test]
    fn passwords_are_even_and_long_enough() {
        assert_eq!(password_from(&[0u8; 10]), None);
        let p = password_from(&(0u8..=255).collect::<Vec<_>>()).unwrap();
        assert_eq!(p.len(), PASSWORD_LEN);
        assert!(p.bytes().all(|b| b.is_ascii_alphanumeric()));
        // bytes past the even cut are skipped, not folded onto the first letters
        assert_eq!(password_from(&[255u8; 200]), None);
    }

    #[test]
    fn an_undone_site_keeps_no_password() {
        let paths = scratch("drop");
        write_secret(&paths.secrets, "s-b3test", "ops", "x".repeat(24).as_str()).unwrap();
        fs::write(paths.secrets.join("s-other.admin.ops"), "y").unwrap();
        fs::write(paths.secrets.join(".s-b3test.admin.ops.tmp"), "z").unwrap();
        drop_admin_secrets(&paths.secrets, "s-b3test").unwrap();
        let left: Vec<String> = fs::read_dir(&paths.secrets)
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
            .collect();
        assert_eq!(left, ["s-other.admin.ops"]);
        assert!(drop_admin_secrets(&paths.secrets.join("none"), "s-b3test").is_ok());
    }

    /// The installer's own output stays in its log: the command answers with the last line
    /// only, whatever the installer printed, and with the installer's exit code (33e28 B3
    /// L1). Run with this machine's `sh`, not on a host.
    #[test]
    fn the_installer_output_stays_in_its_log() {
        let paths = scratch("setuplog");
        let log = paths.secrets.join("setup.log");
        let log_s = log.to_string_lossy().into_owned();
        let loud = "i=0; while [ $i -lt 3000 ]; do echo line-$i-of-a-long-install-xxxxxxxxxxxxxxxx; i=$((i+1)); done; echo last line; exit $0";
        let wrapped = |rc: &str| {
            let mut cmd = std::process::Command::new("sh");
            cmd.args(["-c", SETUP_WRAP, "sh", &log_s, "sh", "-c", loud, rc]);
            crate::probe::run_bounded_input(
                &mut cmd,
                std::time::Duration::from_secs(30),
                crate::sitecreatecmd::CT_OUT_MAX,
                b"",
            )
        };
        assert_eq!(wrapped("0").unwrap(), "last line\n");
        let kept = fs::metadata(&log).unwrap();
        assert!(
            kept.len() > u64::try_from(crate::sitecreatecmd::CT_OUT_MAX).unwrap(),
            "{}",
            kept.len()
        );
        assert_eq!(
            kept.permissions().mode() & 0o077,
            0,
            "the log is the owner's only"
        );
        let failed = wrapped("7").unwrap_err();
        assert!(failed.contains("exited"), "{failed}");
    }

    /// "set" is said only after the stored password is checked against the one given.
    #[test]
    fn the_password_is_read_back_before_it_is_called_set() {
        let program = set_password_program("ops").unwrap();
        let set = program.find("wp_set_password($p").unwrap();
        let check = program
            .find("wp_check_password($p, $v->user_pass, $v->ID)")
            .unwrap();
        let said = program.find("echo \"set").unwrap();
        assert!(set < check && check < said, "{program}");
        assert!(program.contains("get_user_by('id', $u->ID)"));
    }

    /// A title reads back escaped as the site software stores it; addresses as given.
    #[test]
    fn a_title_is_compared_as_the_site_software_stores_it() {
        assert_eq!(stored_as("blogname", "new"), "new");
        assert_eq!(
            stored_as("blogname", "A & B <\"x\"> 'y'"),
            "A &amp; B &lt;&quot;x&quot;&gt; &#039;y&#039;"
        );
        assert_eq!(
            stored_as("home", "https://a.test/?x&y"),
            "https://a.test/?x&y"
        );
    }
}
