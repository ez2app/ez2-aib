// SPDX-License-Identifier: Apache-2.0
//! `site login-link <domain> [--login <user>]`: a one-time link that **logs straight in**
//! to the site's dashboard (Andy 11-11 23:1x: 「一次性登入連結 應該是直接登入，而不是 跳轉到改密碼」).
//! The identity step of `site create` puts the must-load plugin in place
//! ([`ensure_mu_plugin`]); an opened site's answer names this command for the caller to
//! run next and never carries a link itself (ebb36 L1, f1fae 11-11).
//!
//! The rules, from f1fae's reviewed bash version (`SIGN-eu02-e1a-loginlink-f1fae-1111.sh`):
//!
//! - The site carries one must-load plugin, [`MU_PLUGIN`] (this crate's
//!   `ez2-onetime-login.php`, the same bytes as that script's, [`MU_PLUGIN_SHA256`]). It is
//!   written only when no file of that name is there, and read back; a different file of
//!   that name is left alone and nothing is issued (`mu_plugin_differs`).
//! - The link is `https://<domain>/?ez2_otl=<token>`: 43 letters and digits from this
//!   host's random source ([`token_from`]). The site is given **only `sha256(token)`**, on
//!   standard input (never an argument), and keeps it as a transient for [`LINK_SECONDS`].
//!   The plugin deletes the key before it logs anyone in and goes on only when its own
//!   delete removed it (two requests at once: one wins, c477a N1 / ebb36 L2), so a link
//!   works once; it logs in
//!   only a user who can `manage_options`, and goes to the dashboard.
//! - Only an administrator gets a link: anyone else is refused before anything is written,
//!   with the fixed code `login_not_admin` (`login_unknown` for a login the site does not have).
//! - The whole link goes to standard output only, for the caller to keep (on the Mac:
//!   `~/.ez2-aib/private/`, 0600). It is in no argument, no error, no journal: the
//!   operation journal says that a link was issued, on which site and when; standard error
//!   says for whom. Standard output is one line, the link: the caller writes it to its
//!   private file and never tees it into a log or a receipt.

use std::fs;
use std::path::Path;

use aib_cloud::site::SiteSpec;
use serde_json::{json, Value};

use crate::opstore;
use crate::sitecreatecmd::{HostPaths, Runner};
use crate::siteidentity::{in_ct, wp};

/// The must-load plugin, byte for byte what the site gets.
pub const MU_PLUGIN: &str = include_str!("ez2-onetime-login.php");
/// Its SHA-256 (the same as the reviewed bash version's fingerprint).
pub const MU_PLUGIN_SHA256: &str =
    "a1c5b4dcf2e99dfb2a05188739e2e6d113bac2064447ffa209ead8dda7f85a0f";
/// How long an unused link works, in seconds (the plugin trusts the transient's expiry).
pub const LINK_SECONDS: u32 = 600;
/// The command in the operation journal.
pub const COMMAND: &str = "site.login-link";
/// Letters of a token, and how many (the plugin takes exactly `[A-Za-z0-9]{43}`).
const TOKEN_ALPHABET: &[u8; 62] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
pub const TOKEN_LEN: usize = 43;

/// Looks for the plugin as the site's user: `none`, or `sha256sum`'s line.
pub(crate) const LOOK: &str = "if [ -e \"$1\" ]; then sha256sum \"$1\"; else echo none; fi";
/// Writes the plugin from standard input as the site's user, whole or not at all.
pub(crate) const WRITE: &str = "mkdir -p \"$1\" && cat > \"$2.tmp\" && mv \"$2.tmp\" \"$2\"";

/// The plugin's place in the site.
pub fn mu_plugin_dir(code: &str) -> String {
    format!("/var/www/{code}/wp-content/mu-plugins")
}

/// The plugin file in the site.
pub fn mu_plugin_path(code: &str) -> String {
    format!("{}/ez2-onetime-login.php", mu_plugin_dir(code))
}

/// A login that may stand inside a program text: what the login rule makes (lower-case
/// letters, digits, `.`, `_`, `-`). Anything else is refused before a program is made.
pub fn usable_login(login: &str) -> bool {
    (1..=60).contains(&login.len())
        && login
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b"._-".contains(&b))
}

/// A token from random bytes: only bytes that fall evenly on the alphabet are used.
/// `None` when the bytes run out first.
pub fn token_from(bytes: &[u8]) -> Option<String> {
    let even = 256 - 256 % TOKEN_ALPHABET.len();
    let s: String = bytes
        .iter()
        .filter(|b| usize::from(**b) < even)
        .map(|b| char::from(TOKEN_ALPHABET[usize::from(*b) % TOKEN_ALPHABET.len()]))
        .take(TOKEN_LEN)
        .collect();
    (s.len() == TOKEN_LEN).then_some(s)
}

/// What the site is given for a token: its SHA-256, lower hex.
pub fn token_hash(token: &str) -> Result<String, String> {
    opstore::sha256_hex(token.as_bytes())
}

/// Read-only: is `login` an administrator of the site? Answers `admin`, `not_admin` or
/// `no_such_login`. `None` for a login that is not [`usable_login`].
pub fn admin_program(login: &str) -> Option<String> {
    usable_login(login).then(|| {
        format!(
            "$u = get_user_by('login', '{login}'); if (!$u) {{ echo \"no_such_login\\n\"; return; }} echo user_can($u, 'manage_options') ? \"admin\\n\" : \"not_admin\\n\";"
        )
    })
}

/// The program that keeps a hash read from standard input as the key of a transient that
/// names `login`'s id, reads it back, and answers `issued <home>/`. It asks again whether
/// `login` is an administrator. `None` for a login that is not [`usable_login`].
pub fn mint_program(login: &str) -> Option<String> {
    usable_login(login).then(|| {
        format!(
            "$h = trim(stream_get_contents(STDIN)); if (!preg_match('/^[0-9a-f]{{64}}$/', $h)) {{ echo \"bad_input\\n\"; return; }} $u = get_user_by('login', '{login}'); if (!$u || !user_can($u, 'manage_options')) {{ echo \"not_admin\\n\"; return; }} $k = 'ez2_otl_' . $h; set_transient($k, $u->ID, {LINK_SECONDS}); if ((int) get_transient($k) !== (int) $u->ID) {{ echo \"not_stored\\n\"; return; }} echo 'issued ' . home_url('/') . \"\\n\";"
        )
    })
}

/// The link for `domain` and `token`.
pub fn link(domain: &str, token: &str) -> String {
    format!("https://{domain}/?ez2_otl={token}")
}

/// Where the plugin stands in a site.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MuState {
    /// No file of that name.
    None,
    /// This crate's plugin, byte for byte.
    Same,
    /// Another file of that name: left alone.
    Differs,
}

pub(crate) fn as_site(spec: &SiteSpec, rest: &[&str]) -> Vec<String> {
    let mut v = vec!["sudo", "-u", spec.code.as_str()];
    v.extend_from_slice(rest);
    in_ct(spec.ct, &v)
}

/// Read-only: the plugin in the site, as the site's user.
pub fn mu_state(runner: &mut impl Runner, spec: &SiteSpec) -> Result<MuState, String> {
    let path = mu_plugin_path(&spec.code);
    let said = runner
        .run(&as_site(spec, &["sh", "-c", LOOK, "sh", &path]))
        .map_err(|e| format!("mu_plugin_unchecked: {e}"))?;
    let said = said.trim();
    if said == "none" {
        return Ok(MuState::None);
    }
    match said.split_whitespace().next() {
        Some(sha) if sha == MU_PLUGIN_SHA256 => Ok(MuState::Same),
        Some(sha) if sha.len() == 64 && sha.bytes().all(|b| b.is_ascii_hexdigit()) => {
            Ok(MuState::Differs)
        }
        _ => Err("mu_plugin_unchecked: the site's answer is not a fingerprint".to_owned()),
    }
}

/// The plugin in place: written when none is there and read back; the same one is left
/// as it is; another one stops (`mu_plugin_differs`). Answers `written` or `same`.
pub fn ensure_mu_plugin(runner: &mut impl Runner, spec: &SiteSpec) -> Result<&'static str, String> {
    match mu_state(runner, spec)? {
        MuState::Same => return Ok("same"),
        MuState::Differs => {
            return Err(format!(
                "mu_plugin_differs: another {} is in the site; it was left alone",
                mu_plugin_path(&spec.code)
            ))
        }
        MuState::None => {}
    }
    let dir = mu_plugin_dir(&spec.code);
    let path = mu_plugin_path(&spec.code);
    runner
        .run_input(
            &as_site(spec, &["sh", "-c", WRITE, "sh", &dir, &path]),
            MU_PLUGIN.as_bytes(),
        )
        .map_err(|e| format!("mu_plugin_not_written: {e}"))?;
    let back = runner
        .run(&as_site(spec, &["sha256sum", &path]))
        .map_err(|e| format!("mu_plugin_not_written: read back: {e}"))?;
    if back.split_whitespace().next() != Some(MU_PLUGIN_SHA256) {
        return Err("mu_plugin_not_written: it reads back otherwise".to_owned());
    }
    Ok("written")
}

/// Read-only checks before anything is written: the site answers for `domain`, `login`
/// is one of its administrators, and the plugin is not another one.
pub fn check(
    runner: &mut impl Runner,
    spec: &SiteSpec,
    domain: &str,
    login: &str,
) -> Result<(), String> {
    let program = admin_program(login).ok_or("login_unusable: not a login this host makes")?;
    let home = runner
        .run(&wp(spec, &["option", "get", "home"]))
        .map_err(|e| format!("login_link_unchecked: home: {e}"))?;
    let want = format!("https://{domain}");
    if home.trim().trim_end_matches('/') != want {
        return Err(format!(
            "site_address_differs: {} does not answer for {domain}",
            spec.code
        ));
    }
    let said = runner
        .run(&wp(spec, &["eval", &program]))
        .map_err(|e| format!("login_link_unchecked: {login}: {e}"))?;
    match said.trim() {
        "admin" => {}
        "not_admin" => {
            return Err(format!(
                "login_not_admin: {login} is not an administrator of {domain}"
            ))
        }
        "no_such_login" => return Err(format!("login_unknown: {domain} has no {login}")),
        _ => return Err(format!("login_link_unchecked: {login}: an unknown answer")),
    }
    if mu_state(runner, spec)? == MuState::Differs {
        return Err(format!(
            "mu_plugin_differs: another {} is in the site; it was left alone",
            mu_plugin_path(&spec.code)
        ));
    }
    Ok(())
}

/// The link, made: the plugin in place, a token from this host's random source, its hash
/// kept by the site. Answers the whole link; no error carries the token (the site never
/// sees it).
pub fn issue(
    runner: &mut impl Runner,
    spec: &SiteSpec,
    domain: &str,
    login: &str,
) -> Result<String, String> {
    let program = mint_program(login).ok_or("login_unusable: not a login this host makes")?;
    ensure_mu_plugin(runner, spec)?;
    let token = token_from(&runner.random(4 * TOKEN_LEN)?)
        .ok_or("login_link_failed: not enough random bytes for a token")?;
    let hash = token_hash(&token).map_err(|e| format!("login_link_failed: {e}"))?;
    let said = runner
        .run_input(&wp(spec, &["eval", &program]), hash.as_bytes())
        .map_err(|e| format!("login_link_failed: {login}: {e}"))?;
    let said = said.trim();
    if said == "not_admin" {
        return Err(format!(
            "login_not_admin: {login} is not an administrator of {domain}"
        ));
    }
    let Some(home) = said.strip_prefix("issued ") else {
        return Err(format!("login_link_failed: {login}: the key was not kept"));
    };
    if home.trim_end_matches('/') != format!("https://{domain}") {
        return Err(format!(
            "site_address_differs: {} does not answer for {domain}",
            spec.code
        ));
    }
    Ok(link(domain, &token))
}

/// The site whose first name is `domain`, from the site records under `records`.
pub fn site_of(records: &Path, domain: &str) -> Result<SiteSpec, String> {
    let list = crate::sitecreatecmd::read_records(records)
        .ok_or_else(|| format!("site_unknown: {} could not be read", records.display()))?;
    let rec = list
        .iter()
        .find(|r| {
            r.get("hosts")
                .and_then(Value::as_str)
                .and_then(|h| h.split(',').next())
                .map(str::trim)
                == Some(domain)
        })
        .ok_or_else(|| format!("site_unknown: no site record names {domain} first"))?;
    aib_cloud::site::spec_from_record(rec).map_err(|e| format!("site_unknown: {e:?}"))
}

/// The first of the host's first administrators: the default login.
pub fn first_admin(site_admins: &Path) -> Result<String, String> {
    let text = fs::read_to_string(site_admins).ok();
    crate::siteidentity::admins(text.as_deref())
        .ok()
        .and_then(|a| a.into_iter().next())
        .map(|(_, login)| login)
        .ok_or_else(|| {
            format!(
                "login_unknown: no first administrator in {}",
                site_admins.display()
            )
        })
}

/// One link as an operation on the domain's target (an unfinished opening holds it back):
/// the checks under the lock change nothing, so a refusal leaves no journal; the plan has
/// the domain, the site and the login, never the link. `approval` is who stands behind it
/// ([`opstore::BY_LOCAL_ROOT`] on the host, [`opstore::BY_CUSTOMER_KEY`] for the
/// customer's own request).
pub fn issue_op<R: Runner>(
    place: &opstore::OpPlace,
    domain: &str,
    login: &str,
    paths: &HostPaths,
    runner: &mut R,
    approval: &str,
) -> Result<String, String> {
    let on = crate::sitecreatecmd::target(domain);
    opstore::run_op_prepared(
        place,
        COMMAND,
        &on,
        approval,
        crate::probe::unix_now(),
        // the runner goes through the prepared input: one closure holds it at a time
        move || {
            let spec = site_of(&paths.records, domain)?;
            check(runner, &spec, domain, login)?;
            let plan = json!({
                "command": COMMAND,
                "domain": domain,
                "site": spec.code,
                "login": login,
                "seconds": LINK_SECONDS,
            });
            Ok(Some((plan, (spec, runner))))
        },
        |_, (spec, runner): (SiteSpec, &mut R)| issue(runner, &spec, domain, login),
    )?
    .ok_or_else(|| "login_link_failed: nothing was issued".to_owned())
}

/// The login a customer's link is for: the first administrator the site's opening made
/// (its opening record, kept under [`HostPaths::opened`]), or for a site not opened that
/// way the host's first one ([`first_admin`]).
pub fn customer_login(paths: &HostPaths, code: &str) -> Result<String, String> {
    let kept = paths.opened.join(format!("{code}.json"));
    match fs::read_to_string(&kept) {
        Ok(text) => {
            let rec: Value = serde_json::from_str(&text)
                .map_err(|e| format!("login_unknown: {}: {e}", kept.display()))?;
            crate::sitecreatecmd::link_login(&rec)
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => first_admin(&paths.site_admins),
        Err(e) => Err(format!("login_unknown: {}: {e}", kept.display())),
    }
}

/// The customer's `login-link` for their site `code` (Andy 11-12 04:3x): the link for the
/// site's first name and [`customer_login`], as an operation the customer key approves.
/// The site is the one the key's entry or the customer's record names, never a domain the
/// customer typed. Answers the whole link; the caller prints it alone.
pub fn customer_link<R: Runner>(
    place: &opstore::OpPlace,
    paths: &HostPaths,
    code: &str,
    runner: &mut R,
) -> Result<String, String> {
    if !aib_cloud::site::valid_code(code) {
        return Err(format!("site_unknown: {code:?} is not a site code"));
    }
    let path = paths.records.join(format!("{code}.json"));
    let rec: Value = fs::read_to_string(&path)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .ok_or_else(|| format!("site_unknown: {} could not be read", path.display()))?;
    let spec =
        aib_cloud::site::spec_from_record(&rec).map_err(|e| format!("site_unknown: {e:?}"))?;
    if spec.code != code {
        return Err(format!("site_unknown: {} is not {code}'s", path.display()));
    }
    let domain = spec
        .hosts
        .first()
        .ok_or_else(|| format!("site_unknown: {code} has no name"))?;
    let login = customer_login(paths, code)?;
    if !usable_login(&login) {
        return Err("login_unusable: not a login this host makes".to_owned());
    }
    issue_op(
        place,
        domain,
        &login,
        paths,
        runner,
        opstore::BY_CUSTOMER_KEY,
    )
}

/// `site login-link <domain> [--login <user>]` on this host. Writing (a transient, and the
/// plugin when it is missing): run only as a reviewed, approved operation (see
/// [`opstore::BY_LOCAL_ROOT`]). Standard output is the link and nothing else.
pub fn cmd(args: &[String]) -> Result<u8, String> {
    let domain = args
        .first()
        .filter(|a| !a.starts_with("--"))
        .ok_or("needs <domain>")?;
    let domain = crate::sitecreatecmd::domain_arg(domain)?;
    let mut login = None;
    let mut it = args[1..].iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--login" => login = Some(it.next().ok_or("--login needs a user")?.clone()),
            other => return Err(format!("unknown argument {other}")),
        }
    }
    let paths = HostPaths::host();
    let login = match login {
        Some(l) => l,
        None => first_admin(&paths.site_admins)?,
    };
    if !usable_login(&login) {
        return Err("login_unusable: not a login this host makes".to_owned());
    }
    let place = opstore::OpPlace::host()?;
    let url = issue_op(
        &place,
        &domain,
        &login,
        &paths,
        &mut crate::sitecreatecmd::HostRunner,
        opstore::BY_LOCAL_ROOT,
    )?;
    eprintln!("login link issued once for {login} on {domain} (one use, {LINK_SECONDS} s); the link is on standard output only");
    println!("{url}");
    Ok(0)
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    const DOMAIN: &str = "new.example.test";

    fn spec() -> SiteSpec {
        SiteSpec::new(
            "s-lltest",
            131,
            41,
            &[DOMAIN],
            true,
            "zh_TW",
            aib_cloud::site::Limits::START,
        )
        .unwrap()
    }

    /// A site: its home, its users (login, administrator?), the plugin file, the transients.
    struct Wp {
        calls: Vec<String>,
        fed: Vec<(String, Vec<u8>)>,
        home: String,
        users: Vec<(String, bool)>,
        mu: Option<Vec<u8>>,
        /// what a write leaves (a disk that changes it): `None` keeps what was given
        mu_written_as: Option<Vec<u8>>,
        transients: BTreeMap<String, String>,
    }

    impl Wp {
        fn new() -> Self {
            Wp {
                calls: Vec::new(),
                fed: Vec::new(),
                home: format!("https://{DOMAIN}"),
                users: vec![("ops".to_owned(), true), ("editor".to_owned(), false)],
                mu: None,
                mu_written_as: None,
                transients: BTreeMap::new(),
            }
        }

        fn sha_line(&self, path: &str) -> Option<String> {
            self.mu
                .as_ref()
                .map(|b| format!("{}  {path}\n", opstore::sha256_hex(b).unwrap()))
        }

        fn user(&self, program: &str) -> Option<&(String, bool)> {
            self.users
                .iter()
                .find(|(l, _)| program.contains(&format!("get_user_by('login', '{l}')")))
        }
    }

    impl Runner for Wp {
        fn run(&mut self, argv: &[String]) -> Result<String, String> {
            let line = argv.join(" ");
            self.calls.push(line.clone());
            let last = argv.last().cloned().unwrap_or_default();
            if argv.iter().any(|a| a == LOOK) {
                return Ok(self.sha_line(&last).unwrap_or_else(|| "none\n".to_owned()));
            }
            if argv
                .get(argv.len().saturating_sub(2))
                .is_some_and(|a| a == "sha256sum")
            {
                return self.sha_line(&last).ok_or("no file".to_owned());
            }
            if line.ends_with("option get home") {
                return Ok(format!("{}\n", self.home));
            }
            if argv.iter().any(|a| a == "eval") {
                return Ok(match self.user(&last) {
                    None => "no_such_login\n",
                    Some((_, true)) => "admin\n",
                    Some((_, false)) => "not_admin\n",
                }
                .to_owned());
            }
            Ok(String::new())
        }

        fn run_input(&mut self, argv: &[String], input: &[u8]) -> Result<String, String> {
            let line = argv.join(" ");
            self.calls.push(line.clone());
            self.fed.push((line, input.to_vec()));
            if argv.iter().any(|a| a == WRITE) {
                self.mu = Some(self.mu_written_as.clone().unwrap_or_else(|| input.to_vec()));
                return Ok(String::new());
            }
            let program = argv.last().cloned().unwrap_or_default();
            let h = String::from_utf8_lossy(input).trim().to_owned();
            if h.len() != 64 || !h.bytes().all(|b| b.is_ascii_hexdigit()) {
                return Ok("bad_input\n".to_owned());
            }
            match self.user(&program) {
                Some((l, true)) => {
                    let l = l.clone();
                    self.transients.insert(format!("ez2_otl_{h}"), l);
                    Ok(format!("issued {}/\n", self.home))
                }
                _ => Ok("not_admin\n".to_owned()),
            }
        }

        fn random(&mut self, n: usize) -> Result<Vec<u8>, String> {
            Ok((0..n)
                .map(|i| u8::try_from((i * 37 + 11) % 251).unwrap())
                .collect())
        }
    }

    fn token_of(url: &str) -> &str {
        url.strip_prefix(&format!("https://{DOMAIN}/?ez2_otl="))
            .unwrap()
    }

    /// The plugin in the repository is the reviewed one, byte for byte.
    #[test]
    fn the_mu_plugin_is_the_reviewed_one() {
        assert_eq!(
            opstore::sha256_hex(MU_PLUGIN.as_bytes()).unwrap(),
            MU_PLUGIN_SHA256
        );
        assert!(MU_PLUGIN.starts_with("<?php\n"));
        assert!(MU_PLUGIN.ends_with("}, 1);\n"));
    }

    /// The plugin logs in only an administrator, deletes the key before it logs in, and
    /// goes to the dashboard: a direct login, not a password reset.
    #[test]
    fn the_mu_plugin_logs_in_only_an_administrator_once() {
        let at = |s: &str| MU_PLUGIN.find(s).unwrap_or_else(|| panic!("{s}"));
        let shape = at("preg_match('/^[A-Za-z0-9]{43}$/', $t)");
        let hashed = at("$key = 'ez2_otl_' . hash('sha256', $t);");
        let deleted = at("if (!delete_transient($key)) { return; }");
        let admin = at("if (!$u || !user_can($u, 'manage_options')) { return; }");
        let cookie = at("wp_set_auth_cookie($u->ID, false, is_ssl());");
        let to = at("wp_safe_redirect(admin_url());");
        assert!(
            shape < hashed && hashed < deleted && deleted < admin && admin < cookie && cookie < to
        );
        for not in [
            "retrievepassword",
            "rp_key",
            "get_password_reset_key",
            "wp-login.php",
        ] {
            assert!(!MU_PLUGIN.contains(not), "{not}");
        }
    }

    #[test]
    fn tokens_are_even_and_43_long() {
        assert_eq!(token_from(&[0u8; 42]), None);
        let t = token_from(&(0u8..=255).collect::<Vec<_>>()).unwrap();
        assert_eq!(t.len(), TOKEN_LEN);
        assert!(t.bytes().all(|b| b.is_ascii_alphanumeric()));
        // bytes past the even cut are skipped, not folded onto the first letters
        assert_eq!(token_from(&[250u8; 200]), None);
    }

    /// The site gets the token's hash on standard input and nothing else: the token is in
    /// no argument, and the key it keeps is the hash of the token in the link.
    #[test]
    fn the_token_is_never_in_an_argument_and_the_site_gets_only_its_hash() {
        let mut site = Wp::new();
        let url = issue(&mut site, &spec(), DOMAIN, "ops").unwrap();
        let token = token_of(&url);
        assert_eq!(token.len(), TOKEN_LEN);
        assert!(token.bytes().all(|b| b.is_ascii_alphanumeric()));
        assert!(
            !site.calls.iter().any(|c| c.contains(token)),
            "the token in an argument"
        );
        assert!(!site
            .fed
            .iter()
            .any(|(_, i)| String::from_utf8_lossy(i).contains(token)));
        let key = format!("ez2_otl_{}", opstore::sha256_hex(token.as_bytes()).unwrap());
        assert_eq!(site.transients.get(&key).map(String::as_str), Some("ops"));
        assert_eq!(site.transients.len(), 1);
        // and the plugin was put in and read back
        assert_eq!(site.mu.as_deref(), Some(MU_PLUGIN.as_bytes()));
    }

    /// The program asks again for an administrator, keeps the key ten minutes and reads it
    /// back before it says `issued`.
    #[test]
    fn the_mint_program_checks_the_administrator_and_keeps_the_key_ten_minutes() {
        let p = mint_program("ops").unwrap();
        assert!(p.contains("stream_get_contents(STDIN)"));
        assert!(p.contains("preg_match('/^[0-9a-f]{64}$/', $h)"));
        assert!(p.contains("if (!$u || !user_can($u, 'manage_options')) {"));
        assert!(p.contains("set_transient($k, $u->ID, 600);"));
        let kept = p.find("set_transient(").unwrap();
        let back = p.find("(int) get_transient($k) !== (int) $u->ID").unwrap();
        let said = p.find("echo 'issued '").unwrap();
        assert!(kept < back && back < said);
        assert!(admin_program("ops")
            .unwrap()
            .contains("user_can($u, 'manage_options')"));
        for bad in ["", "O'x", "a b", "a');", "Ops"] {
            assert!(mint_program(bad).is_none(), "{bad}");
            assert!(admin_program(bad).is_none(), "{bad}");
        }
    }

    /// Not an administrator: refused with a fixed code, before anything is written.
    #[test]
    fn a_login_that_is_not_an_administrator_is_refused_with_a_fixed_code() {
        let mut site = Wp::new();
        let err = check(&mut site, &spec(), DOMAIN, "editor").unwrap_err();
        assert!(err.starts_with("login_not_admin: "), "{err}");
        assert!(site.fed.is_empty() && site.transients.is_empty() && site.mu.is_none());
        let err = check(&mut site, &spec(), DOMAIN, "nobody").unwrap_err();
        assert!(err.starts_with("login_unknown: "), "{err}");
        // the mint itself refuses as well, with the same code
        let err = issue(&mut site, &spec(), DOMAIN, "editor").unwrap_err();
        assert!(err.starts_with("login_not_admin: "), "{err}");
        assert!(site.transients.is_empty());
        assert!(check(&mut Wp::new(), &spec(), DOMAIN, "ops").is_ok());
    }

    #[test]
    fn a_site_answering_for_another_address_gets_no_link() {
        let mut site = Wp::new();
        site.home = "https://other.example.test".to_owned();
        let err = check(&mut site, &spec(), DOMAIN, "ops").unwrap_err();
        assert!(err.starts_with("site_address_differs: "), "{err}");
        assert!(site.fed.is_empty());
        let err = issue(&mut site, &spec(), DOMAIN, "ops").unwrap_err();
        assert!(err.starts_with("site_address_differs: "), "{err}");
    }

    /// Another file of the plugin's name is left alone and nothing is issued.
    #[test]
    fn a_different_mu_plugin_is_left_alone() {
        let mut site = Wp::new();
        site.mu = Some(b"<?php // the customer's own\n".to_vec());
        let err = check(&mut site, &spec(), DOMAIN, "ops").unwrap_err();
        assert!(err.starts_with("mu_plugin_differs: "), "{err}");
        let err = issue(&mut site, &spec(), DOMAIN, "ops").unwrap_err();
        assert!(err.starts_with("mu_plugin_differs: "), "{err}");
        assert_eq!(
            site.mu.as_deref(),
            Some(&b"<?php // the customer's own\n"[..])
        );
        assert!(site.fed.is_empty() && site.transients.is_empty());
        // the same plugin is left as it is
        let mut site = Wp::new();
        site.mu = Some(MU_PLUGIN.as_bytes().to_vec());
        assert_eq!(ensure_mu_plugin(&mut site, &spec()).unwrap(), "same");
        assert!(site.fed.is_empty());
    }

    #[test]
    fn a_mu_plugin_that_reads_back_otherwise_stops() {
        let mut site = Wp::new();
        site.mu_written_as = Some(b"<?php\n".to_vec());
        let err = issue(&mut site, &spec(), DOMAIN, "ops").unwrap_err();
        assert!(err.starts_with("mu_plugin_not_written: "), "{err}");
        assert!(site.transients.is_empty());
        let mut site = Wp::new();
        assert_eq!(ensure_mu_plugin(&mut site, &spec()).unwrap(), "written");
        let (cmd, input) = &site.fed[0];
        assert!(
            cmd.starts_with("pct exec 131 -- sudo -u s-lltest sh -c "),
            "{cmd}"
        );
        assert!(cmd.ends_with(" sh /var/www/s-lltest/wp-content/mu-plugins /var/www/s-lltest/wp-content/mu-plugins/ez2-onetime-login.php"));
        assert_eq!(input, MU_PLUGIN.as_bytes());
    }

    /// An opened site's link is for the first administrator the identity made.
    #[test]
    fn the_opened_sites_link_is_for_the_first_administrator() {
        let rec = json!({ "admins": ["ops", "ops2"] });
        assert_eq!(crate::sitecreatecmd::link_login(&rec).unwrap(), "ops");
        // what the opening answers is the command, never a link (ebb36 L1)
        let next = crate::sitecreatecmd::login_link_next(DOMAIN, "ops");
        assert_eq!(
            next,
            "aib-cloud-host site login-link new.example.test --login ops"
        );
        assert!(!next.contains("ez2_otl") && !next.contains("https://"));
        for none in [json!({}), json!({ "admins": [] }), json!({ "admins": [7] })] {
            let err = crate::sitecreatecmd::link_login(&none).unwrap_err();
            assert!(err.starts_with("login_unknown: "), "{err}");
        }
    }

    fn scratch(name: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("aib-ll-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

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

    fn paths(d: &Path) -> HostPaths {
        fs::create_dir_all(d.join("records")).unwrap();
        let rec = aib_cloud::site::record(&spec(), "2026-11-11T00:00:00Z");
        fs::write(d.join("records").join("s-lltest.json"), rec.to_string()).unwrap();
        fs::write(d.join("site-admins"), "# first\nops@example.test\n").unwrap();
        HostPaths {
            creating: d.join("creating"),
            lxc_conf: d.join("lxc"),
            firewall: d.join("firewall"),
            secrets: d.join("secrets"),
            site_admins: d.join("site-admins"),
            setup: d.join("setup"),
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

    /// As an operation: the journal says a link was issued and never carries it; a refusal
    /// leaves no journal at all, so nothing is held back.
    #[test]
    fn the_journal_says_a_link_was_issued_and_never_carries_it() {
        let d = scratch("op");
        let place = place(&d);
        let paths = paths(&d);
        assert_eq!(first_admin(&paths.site_admins).unwrap(), "ops");
        let mut site = Wp::new();
        let err = issue_op(
            &place,
            DOMAIN,
            "editor",
            &paths,
            &mut site,
            opstore::BY_LOCAL_ROOT,
        )
        .unwrap_err();
        assert!(err.starts_with("login_not_admin: "), "{err}");
        assert_eq!(fs::read_dir(&place.ops_dir).unwrap().count(), 0);
        let err = issue_op(
            &place,
            "other.example.test",
            "ops",
            &paths,
            &mut site,
            opstore::BY_LOCAL_ROOT,
        )
        .unwrap_err();
        assert!(err.starts_with("site_unknown: "), "{err}");
        let url = issue_op(
            &place,
            DOMAIN,
            "ops",
            &paths,
            &mut site,
            opstore::BY_LOCAL_ROOT,
        )
        .unwrap();
        let token = token_of(&url).to_owned();
        let mut journals = String::new();
        for e in fs::read_dir(&place.ops_dir).unwrap() {
            journals.push_str(&fs::read_to_string(e.unwrap().path()).unwrap());
        }
        assert!(
            journals.contains(COMMAND) && journals.contains("\"verified\""),
            "{journals}"
        );
        assert!(!journals.contains(&token) && !journals.contains("ez2_otl"));
    }

    /// E1 N1 (Andy 11-12 04:3x): the customer's own request gets the link of the site its
    /// key reaches, for the administrator the opening made, approved by the customer key.
    #[test]
    fn a_customer_s_link_is_for_their_site_s_first_administrator() {
        let d = scratch("cust");
        let place = place(&d);
        let paths = paths(&d);
        // a site not opened by the customer: the host's first administrator
        assert_eq!(customer_login(&paths, "s-lltest").unwrap(), "ops");
        // an opened one: the first administrator its opening made, not the host's
        fs::create_dir_all(&paths.opened).unwrap();
        fs::write(
            paths.opened.join("s-lltest.json"),
            json!({ "admins": ["editor", "ops"] }).to_string(),
        )
        .unwrap();
        assert_eq!(customer_login(&paths, "s-lltest").unwrap(), "editor");
        let mut site = Wp::new();
        // `editor` is no administrator of the site: refused before anything is written
        let err = customer_link(&place, &paths, "s-lltest", &mut site).unwrap_err();
        assert!(err.starts_with("login_not_admin: "), "{err}");
        fs::write(
            paths.opened.join("s-lltest.json"),
            json!({ "admins": ["ops", "editor"] }).to_string(),
        )
        .unwrap();
        let url = customer_link(&place, &paths, "s-lltest", &mut site).unwrap();
        assert!(
            url.starts_with(&format!("https://{DOMAIN}/?ez2_otl=")),
            "{url}"
        );
        let mut journals = String::new();
        for e in fs::read_dir(&place.ops_dir).unwrap() {
            journals.push_str(&fs::read_to_string(e.unwrap().path()).unwrap());
        }
        assert!(
            journals.contains(opstore::BY_CUSTOMER_KEY) && journals.contains("\"verified\""),
            "{journals}"
        );
        assert!(!journals.contains(token_of(&url)));
        // a site with no record is no site
        let err = customer_link(&place, &paths, "s-none", &mut site).unwrap_err();
        assert!(err.starts_with("site_unknown: "), "{err}");
    }
}
