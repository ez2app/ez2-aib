// SPDX-License-Identifier: Apache-2.0
//! `site svc <start|stop|restart|php-reload>` and `site logs` as decisions (4.4.1 §12).
//! Both are the first things reached for when a site misbehaves, so both are kept narrow:
//! a fixed set of actions and of log sources, each an argument list built here from a
//! validated site spec, never from operator text. No I/O.
//!
//! * **Nothing here lifts a quarantine.** `start`, `restart` and `php-reload` are refused
//!   for a quarantined site ([`crate::quarantine::guard`]); `stop` is allowed but the plan
//!   says what it costs: the processes and memory an investigation would want.
//! * **Logs are bounded**: a number of lines and a look-back window, both capped, read
//!   through the journal of a fixed set of units — or, for WordPress's own error log, the
//!   last lines of one fixed file, read as the site's user. What comes back is passed
//!   through [`mask_line`] and then [`HostHide::hide`] before it leaves the host.

use alloc::borrow::ToOwned;
use alloc::string::{String, ToString};
use alloc::vec::Vec;

use crate::quarantine::{self, Reopens};
use crate::site::{Error, SiteSpec};

/// A service action.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Svc {
    /// Start the CT.
    Start,
    /// Shut the CT down.
    Stop,
    /// Reboot the CT.
    Restart,
    /// Reload PHP-FPM inside the CT (new code and settings, no dropped requests).
    PhpReload,
}

impl Svc {
    /// The spelling on the command line.
    pub fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "start" => Svc::Start,
            "stop" => Svc::Stop,
            "restart" => Svc::Restart,
            "php-reload" => Svc::PhpReload,
            _ => return None,
        })
    }
}

/// What a service action amounts to.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SvcPlan {
    /// The command, as an argument list.
    pub argv: Vec<String>,
    /// `pct status` must say this afterwards (`running` / `stopped`).
    pub expect_status: &'static str,
    /// What is lost by doing it, when something is.
    pub costs: Option<&'static str>,
    /// How to undo it, as an argument list; `None` when the effect cannot be taken back.
    pub undo: Option<Vec<String>>,
}

fn argv(parts: &[&str]) -> Vec<String> {
    parts.iter().map(|p| (*p).to_owned()).collect()
}

/// Plan a service action. `ct_fw` and `record` are the CT's firewall file and the site's
/// quarantine record, for the quarantine guard.
pub fn svc_plan(
    spec: &SiteSpec,
    action: Svc,
    ct_fw: &str,
    record: Option<&quarantine::Record>,
) -> Result<SvcPlan, Error> {
    let ct = spec.ct.to_string();
    let quarantined = record.is_some() || quarantine::is_quarantined(ct_fw);
    if action != Svc::Stop {
        quarantine::guard(&spec.code, ct_fw, record, Reopens::Start)?;
    }
    Ok(match action {
        Svc::Start => SvcPlan {
            argv: argv(&["pct", "start", &ct]),
            expect_status: "running",
            costs: None,
            undo: Some(argv(&["pct", "shutdown", &ct])),
        },
        Svc::Stop => SvcPlan {
            argv: argv(&["pct", "shutdown", &ct, "--timeout", "60"]),
            expect_status: "stopped",
            costs: Some(if quarantined {
                "the site goes offline; the running processes and memory of a quarantined CT are lost to whoever investigates"
            } else {
                "the site goes offline"
            }),
            undo: (!quarantined).then(|| argv(&["pct", "start", &ct])),
        },
        Svc::Restart => SvcPlan {
            argv: argv(&["pct", "reboot", &ct, "--timeout", "60"]),
            expect_status: "running",
            costs: Some("the site is unavailable while the CT reboots"),
            undo: None,
        },
        Svc::PhpReload => SvcPlan {
            // the unit name carries the PHP version: reload whichever FPM unit is loaded
            argv: argv(&[
                "pct",
                "exec",
                &ct,
                "--",
                "systemctl",
                "reload",
                "php*-fpm.service",
            ]),
            expect_status: "running",
            costs: None,
            undo: None,
        },
    })
}

/// A log source inside a site CT.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LogSource {
    /// The web server.
    Nginx,
    /// PHP-FPM.
    Php,
    /// The database.
    Db,
    /// WordPress's own error log, `wp-content/debug.log` (written when the site turns
    /// `WP_DEBUG_LOG` on; PHP notices and fatals of plugins land there, not in the journal).
    Wp,
}

impl LogSource {
    /// The spelling on the command line.
    pub fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "nginx" => LogSource::Nginx,
            "php" => LogSource::Php,
            "db" => LogSource::Db,
            "wp" => LogSource::Wp,
            _ => return None,
        })
    }

    /// The journal unit, for the sources the journal holds.
    fn unit(self) -> Option<&'static str> {
        match self {
            LogSource::Nginx => Some("nginx.service"),
            LogSource::Php => Some("php*-fpm.service"),
            LogSource::Db => Some("mariadb.service"),
            LogSource::Wp => None,
        }
    }
}

/// Most lines one `site logs` returns.
pub const LOG_LINES_MAX: u32 = 2000;
/// Longest look-back, in minutes (seven days).
pub const LOG_MINUTES_MAX: u32 = 7 * 24 * 60;

/// What reads WordPress's error log inside the CT, as the site's user: `$1` is the file,
/// `$2` the number of lines. A site that never turned the log on has no file, and that is
/// an empty log, not an error. `tail` and `test` are the CT's own; what they print is
/// bounded by the host ([`log_bytes_max`]), not by them.
pub const WP_LOG_SH: &str = "f=\"$1\"; [ -f \"$f\" ] || exit 0; exec tail -n \"$2\" -- \"$f\"";

/// Where a site's WordPress error log is: the default of `WP_DEBUG_LOG = true`, below
/// the document root every site gets (`/var/www/<code>`). A site that points the log
/// elsewhere is not followed: the path is ours, never the site's.
pub fn wp_log_path(spec: &SiteSpec) -> String {
    alloc::format!("/var/www/{}/wp-content/debug.log", spec.code)
}

/// The command that reads a site's log: the journal of one unit inside the CT, bounded
/// in lines and in time; for [`LogSource::Wp`], the last lines of the site's
/// [`wp_log_path`], read **as the site's user** (`sudo -u <code>`), so a link the site
/// plants there reaches nothing its own user could not read anyway. `minutes` does not
/// narrow that file (it has no index by time) but is held to the same bounds. Read-only,
/// but it runs a program inside a customer CT.
pub fn logs_argv(
    spec: &SiteSpec,
    source: LogSource,
    lines: u32,
    minutes: u32,
) -> Result<Vec<String>, Error> {
    if !(1..=LOG_LINES_MAX).contains(&lines) {
        return Err(Error::Limit("log_lines", lines));
    }
    if !(1..=LOG_MINUTES_MAX).contains(&minutes) {
        return Err(Error::Limit("log_minutes", minutes));
    }
    let ct = spec.ct.to_string();
    let since = alloc::format!("-{minutes}min");
    let lines = lines.to_string();
    let Some(unit) = source.unit() else {
        let path = wp_log_path(spec);
        return Ok(argv(&[
            "pct", "exec", &ct, "--", "sudo", "-u", &spec.code, "--", "sh", "-c", WP_LOG_SH, "sh",
            &path, &lines,
        ]));
    };
    Ok(argv(&[
        "pct",
        "exec",
        &ct,
        "--",
        "journalctl",
        "--no-pager",
        "-o",
        "short-iso",
        "-u",
        unit,
        "--since",
        &since,
        "-n",
        &lines,
    ]))
}

/// Keys whose value is not shown, wherever `key=value` or `key: value` appears in a line.
const SECRET_KEYS: &[&str] = &[
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "apikey",
    "api_key",
    "authorization",
    "cookie",
    "key",
    "signature",
];

/// Longest log line passed on, in bytes; what a longer line has beyond that is dropped.
pub const LOG_LINE_MAX: usize = 4096;

/// Most bytes of output the host reads for a request of `lines` lines: room for every
/// line at its longest, and never more than 8 MiB. The count is kept **on the host**:
/// the program that prints the log runs inside the customer's container and is not
/// trusted to stop by itself (33e28, #631 seg. 5 M1).
pub fn log_bytes_max(lines: u32) -> usize {
    usize::try_from(lines)
        .unwrap_or(usize::MAX)
        .saturating_mul(LOG_LINE_MAX)
        .min(8 * 1024 * 1024)
}

/// `line` cut to [`LOG_LINE_MAX`] bytes, on a character boundary.
pub fn clip_line(line: &str) -> &str {
    if line.len() <= LOG_LINE_MAX {
        return line;
    }
    let mut end = LOG_LINE_MAX;
    while !line.is_char_boundary(end) {
        end -= 1;
    }
    &line[..end]
}

/// One log line with secret-looking values replaced by `***`.
///
/// A key is any of `SECRET_KEYS`, in any case, also inside a longer name such as
/// `db_password`. It may be closed by a quote or a bracket and followed by blanks before
/// its `=`, `:` or `=>` — `"password":"x"`, `'password' => 'x'`, `[password] => x`,
/// `password = x` (33e28, #631 seg. 5 M2). The value runs to its closing quote, or to the
/// next space, `&`, quote, `;` or `,`. Two keys are secret **to the end of the line**: a
/// cookie header (every cookie in it), and an `Authorization` whose scheme is not Bearer
/// or Basic (Digest and the like spread the credential over several fields).
///
/// Deliberately broad: a masked harmless value costs a second look, a leaked credential
/// costs the site.
pub fn mask_line(line: &str) -> String {
    plain(&mask_secrets(line))
}

/// What stands in for a piece of host information in a log line.
pub const HIDDEN: &str = "[hidden]";

/// Path prefixes that only exist on the host, never inside a site's CT: a line that
/// names one has the whole path (up to the next blank or quote) replaced by [`HIDDEN`].
pub const HOST_PATHS: &[&str] = &["/var/lib/lxc/", "/etc/pve/", "/var/lib/vz/", "/dev/pve/"];

/// Shortest word [`HostHide`] takes: anything shorter would cut ordinary text apart.
pub const HIDE_WORD_MIN: usize = 4;

/// The names the host goes by, to hide: its short name, the full name `hostname -f`
/// answers when it does, and for each name with a dot its first label and the domain
/// after it (33e28 L3: on this kind of host `/etc/hostname` is usually the short name and
/// the domain is where the provider shows). A name that is empty is left out; one shorter
/// than [`HIDE_WORD_MIN`] is left out by [`HostHide::new`].
pub fn host_names(short: &str, full: Option<&str>) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for name in core::iter::once(short).chain(full).map(str::trim) {
        let parts = name.split_once('.');
        for n in [Some(name), parts.map(|p| p.0), parts.map(|p| p.1)]
            .into_iter()
            .flatten()
        {
            if !n.is_empty() && !out.iter().any(|o| o == n) {
                out.push(n.to_owned());
            }
        }
    }
    out
}

/// One line of a site's log as it leaves the host: secrets masked ([`mask_line`]), the
/// host hidden ([`HostHide::hide`]), **then** cut to [`LOG_LINE_MAX`] — cut first, a host
/// word across the cut would be left as its first half, which no whole-word match finds
/// (33e28 L1). What is read is bounded already ([`log_bytes_max`]).
pub fn send_line(line: &str, hide: &HostHide) -> String {
    let (hidden, _) = hide.hide(&mask_line(line));
    clip_line(&hidden).to_owned()
}

/// What of the host a site's log must not carry out to its customer (4.4.1 §23, Andy
/// 11-10: "要讓 ai 也盡量找不出主機與主機商的任何資訊"): the host's name, the words the
/// host keeps in its own list (its addresses, the provider's name — **never in this
/// repository**, which is public), the CT's number where it is named as one (`ct112`,
/// `CT 112`, `CT-112`, `lxc/112`, `lxc-112`, the volumes `vm-112-disk-N` and
/// `subvol-112-disk-N`), and the host-only paths of [`HOST_PATHS`].
///
/// Words match without regard to ASCII case and only as whole words: `192.0.2.1` is not
/// found inside `192.0.2.10`. Built on the host, used per line.
///
/// The host's own list is where what no program here can know goes: its public
/// addresses, the provider's name, **the gateway address of the CTs' network** (33e28
/// N2: it tells how the host's network is laid out).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HostHide {
    words: Vec<String>,
}

impl HostHide {
    /// From the host's names ([`host_names`]), the text of the host's own list (one word
    /// per line; blank lines and `#` lines skipped) and the CT's number. Words shorter
    /// than [`HIDE_WORD_MIN`] are left out.
    pub fn new(names: &[String], list: &str, ct: u32) -> Self {
        let mut words: Vec<String> = Vec::new();
        let ct_words = [
            alloc::format!("ct{ct}"),
            alloc::format!("ct {ct}"),
            alloc::format!("ct-{ct}"),
            alloc::format!("lxc/{ct}"),
            alloc::format!("lxc-{ct}"),
            alloc::format!("vm-{ct}-disk"),
            alloc::format!("subvol-{ct}-disk"),
        ];
        let from_list = list.lines().map(str::trim).filter(|w| !w.starts_with('#'));
        for w in names
            .iter()
            .map(|n| n.trim())
            .chain(from_list)
            .chain(ct_words.iter().map(String::as_str))
        {
            let w = w.to_ascii_lowercase();
            if w.len() >= HIDE_WORD_MIN && !words.contains(&w) {
                words.push(w);
            }
        }
        // longest first: `host1.example.net` goes whole before `host1` could cut it
        words.sort_by_key(|w| core::cmp::Reverse(w.len()));
        HostHide { words }
    }

    /// `line` with every host word and host path replaced by [`HIDDEN`], and how many
    /// were.
    pub fn hide(&self, line: &str) -> (String, usize) {
        let lower = line.to_ascii_lowercase();
        let bytes = line.as_bytes();
        let word_char = |at: usize| bytes.get(at).is_some_and(|b| b.is_ascii_alphanumeric());
        let mut out = String::with_capacity(line.len());
        let mut count = 0;
        let mut i = 0;
        while i < bytes.len() {
            if let Some(p) = HOST_PATHS.iter().find(|p| lower[i..].starts_with(**p)) {
                let rest = &line[i + p.len()..];
                let end = rest.find([' ', '\t', '"', '\'']).unwrap_or(rest.len());
                out.push_str(HIDDEN);
                count += 1;
                i += p.len() + end;
                continue;
            }
            let at_start = i == 0 || !word_char(i - 1);
            let hit = self.words.iter().find(|w| {
                at_start && lower[i..].starts_with(w.as_str()) && !word_char(i + w.len())
            });
            if let Some(w) = hit {
                out.push_str(HIDDEN);
                count += 1;
                i += w.len();
                continue;
            }
            let next = (i + 1..=bytes.len())
                .find(|j| line.is_char_boundary(*j))
                .unwrap_or(bytes.len());
            out.push_str(&line[i..next]);
            i = next;
        }
        (out, count)
    }
}

/// `line` with nothing in it that a terminal would act on (ebb36, #641 seg. 2 L1). Part of
/// a log comes from whoever visits the site — a URL, a user agent — and is shown in the
/// customer's own terminal: control characters (ESC and the rest of C0 but the tab, DEL,
/// C1) and the characters that reorder or hide text are written out as `\x1b` /
/// `\u{202e}` instead of being sent.
pub fn plain(line: &str) -> String {
    let mut out = String::with_capacity(line.len());
    for c in line.chars() {
        if c == '\t' || !(c.is_control() || unseen(c)) {
            out.push(c);
        } else if u32::from(c) < 0x100 {
            out.push_str(&alloc::format!("\\x{:02x}", u32::from(c)));
        } else {
            out.push_str(&alloc::format!("\\u{{{:x}}}", u32::from(c)));
        }
    }
    out
}

/// Characters a person does not see and a program — or the customer's AI reading the same
/// output — does (ebb36, #648 L1): Unicode format characters (direction marks and
/// overrides, zero-width and invisible operators, interlinear annotation marks, the tag
/// block, which can carry a whole hidden sentence), line and paragraph separators,
/// variation selectors, private-use characters and non-characters. Ranges, because this
/// crate has no Unicode tables; a code point not assigned yet is not covered.
fn unseen(c: char) -> bool {
    matches!(
        c,
        '\u{00ad}'
            | '\u{0600}'..='\u{0605}'
            | '\u{061c}'
            | '\u{06dd}'
            | '\u{070f}'
            | '\u{0890}'..='\u{0891}'
            | '\u{08e2}'
            | '\u{180e}'
            | '\u{200b}'..='\u{200f}'
            | '\u{2028}'..='\u{202e}'
            | '\u{2060}'..='\u{206f}'
            | '\u{fe00}'..='\u{fe0f}'
            | '\u{feff}'
            | '\u{fff9}'..='\u{fffb}'
            | '\u{110bd}'
            | '\u{110cd}'
            | '\u{13430}'..='\u{1343f}'
            | '\u{1bca0}'..='\u{1bca3}'
            | '\u{1d173}'..='\u{1d17a}'
            | '\u{e0000}'..='\u{e0fff}'
            | '\u{e000}'..='\u{f8ff}'
            | '\u{f0000}'..='\u{ffffd}'
            | '\u{100000}'..='\u{10fffd}'
            | '\u{fdd0}'..='\u{fdef}'
    ) || (u32::from(c) & 0xfffe) == 0xfffe
}

fn mask_secrets(line: &str) -> String {
    let lower = line.to_ascii_lowercase();
    let bytes = line.as_bytes();
    let mut out = String::with_capacity(line.len());
    let mut i = 0;
    while i < bytes.len() {
        let hit = SECRET_KEYS.iter().find_map(|k| {
            if !lower[i..].starts_with(k) {
                return None;
            }
            let mut at = i + k.len();
            while matches!(bytes.get(at), Some(b'"' | b'\'' | b']')) {
                at += 1;
            }
            while matches!(bytes.get(at), Some(b' ' | b'\t')) {
                at += 1;
            }
            let separator = match (bytes.get(at), bytes.get(at + 1)) {
                (Some(b'='), Some(b'>')) => 2,
                (Some(b'=' | b':'), _) => 1,
                _ => return None,
            };
            Some((*k, at + separator))
        });
        match hit {
            Some((key, mut value)) => {
                out.push_str(&line[i..value]);
                while bytes.get(value) == Some(&b' ') {
                    out.push(' ');
                    value += 1;
                }
                let rest = &line[value..];
                // `Authorization: Bearer xyz`: the scheme word stays, the credential goes
                let scheme = if key == "authorization" {
                    ["bearer ", "basic "]
                        .iter()
                        .find(|s| lower[value..].starts_with(**s))
                        .map_or(0, |s| s.len())
                } else {
                    0
                };
                out.push_str(&rest[..scheme]);
                let mut secret = &rest[scheme..];
                let mut skipped = scheme;
                let whole = key == "cookie" || (key == "authorization" && scheme == 0);
                // a quoted value runs to its closing quote
                let quote = secret
                    .chars()
                    .next()
                    .filter(|c| !whole && (*c == '"' || *c == '\''));
                let end = match quote {
                    Some(q) => {
                        out.push(q);
                        secret = &secret[1..];
                        skipped += 1;
                        secret.find(q).unwrap_or(secret.len())
                    }
                    None if whole => secret.len(),
                    None => secret
                        .find([' ', '&', '"', '\'', ';', ','])
                        .unwrap_or(secret.len()),
                };
                if end > 0 {
                    out.push_str("***");
                }
                i = value + skipped + end;
            }
            None => {
                // copy one whole character
                let next = (i + 1..=bytes.len())
                    .find(|j| line.is_char_boundary(*j))
                    .unwrap_or(bytes.len());
                out.push_str(&line[i..next]);
                i = next;
            }
        }
    }
    out
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use crate::site::{desired_firewall, Limits};

    fn spec() -> SiteSpec {
        SiteSpec::new(
            "site2",
            112,
            12,
            &["site2.example.test"],
            true,
            "zh_TW",
            Limits::TEST_TIER,
        )
        .unwrap()
        .with_fw_group("ez2-site-v2")
        .unwrap()
    }

    fn fw() -> String {
        desired_firewall("ez2-site-v2", None)
    }

    #[test]
    fn svc_plans_are_fixed_argument_lists() {
        let p = svc_plan(&spec(), Svc::Start, &fw(), None).unwrap();
        assert_eq!(p.argv, ["pct", "start", "112"]);
        assert_eq!((p.expect_status, p.costs), ("running", None));
        assert_eq!(p.undo.unwrap(), ["pct", "shutdown", "112"]);
        let p = svc_plan(&spec(), Svc::Stop, &fw(), None).unwrap();
        assert_eq!(p.argv, ["pct", "shutdown", "112", "--timeout", "60"]);
        assert_eq!(p.expect_status, "stopped");
        assert_eq!(p.undo.unwrap(), ["pct", "start", "112"]);
        let p = svc_plan(&spec(), Svc::Restart, &fw(), None).unwrap();
        assert_eq!(p.argv[..3], ["pct", "reboot", "112"]);
        assert!(p.undo.is_none() && p.costs.is_some());
        let p = svc_plan(&spec(), Svc::PhpReload, &fw(), None).unwrap();
        assert_eq!(
            p.argv,
            [
                "pct",
                "exec",
                "112",
                "--",
                "systemctl",
                "reload",
                "php*-fpm.service"
            ]
        );
        for (text, want) in [
            ("start", Some(Svc::Start)),
            ("php-reload", Some(Svc::PhpReload)),
            ("kill", None),
            ("", None),
        ] {
            assert_eq!(Svc::parse(text), want);
        }
    }

    #[test]
    fn a_quarantined_site_is_not_started_and_stopping_it_says_what_is_lost() {
        let q = quarantine::firewall();
        for action in [Svc::Start, Svc::Restart, Svc::PhpReload] {
            assert!(svc_plan(&spec(), action, &q, None).is_err(), "{action:?}");
        }
        let stop = svc_plan(&spec(), Svc::Stop, &q, None).unwrap();
        assert!(stop.costs.unwrap().contains("investigates"));
        // and there is no "undo" that would start it again
        assert!(stop.undo.is_none());
    }

    #[test]
    fn logs_are_bounded() {
        let a = logs_argv(&spec(), LogSource::Nginx, 200, 60).unwrap();
        assert_eq!(
            a,
            [
                "pct",
                "exec",
                "112",
                "--",
                "journalctl",
                "--no-pager",
                "-o",
                "short-iso",
                "-u",
                "nginx.service",
                "--since",
                "-60min",
                "-n",
                "200"
            ]
        );
        assert!(logs_argv(&spec(), LogSource::Php, 1, 1)
            .unwrap()
            .contains(&"php*-fpm.service".to_owned()));
        assert!(logs_argv(&spec(), LogSource::Db, LOG_LINES_MAX, LOG_MINUTES_MAX).is_ok());
        assert!(logs_argv(&spec(), LogSource::Db, LOG_LINES_MAX + 1, 60).is_err());
        assert!(logs_argv(&spec(), LogSource::Db, 0, 60).is_err());
        assert!(logs_argv(&spec(), LogSource::Db, 10, LOG_MINUTES_MAX + 1).is_err());
        assert!(logs_argv(&spec(), LogSource::Db, 10, 0).is_err());
        assert_eq!(LogSource::parse("wp"), Some(LogSource::Wp));
        assert_eq!(LogSource::parse("journal"), None);
        assert_eq!(LogSource::parse("php"), Some(LogSource::Php));
    }

    #[test]
    fn the_wp_log_is_read_as_the_sites_user_from_a_fixed_path() {
        let a = logs_argv(&spec(), LogSource::Wp, 50, 60).unwrap();
        assert_eq!(
            a,
            [
                "pct",
                "exec",
                "112",
                "--",
                "sudo",
                "-u",
                "site2",
                "--",
                "sh",
                "-c",
                WP_LOG_SH,
                "sh",
                "/var/www/site2/wp-content/debug.log",
                "50"
            ]
        );
        assert_eq!(wp_log_path(&spec()), "/var/www/site2/wp-content/debug.log");
        // the same bounds as every other source
        assert!(logs_argv(&spec(), LogSource::Wp, LOG_LINES_MAX + 1, 60).is_err());
        assert!(logs_argv(&spec(), LogSource::Wp, 10, 0).is_err());
        // no file is an empty log, and the count is the second argument
        assert!(WP_LOG_SH.contains("[ -f \"$f\" ] || exit 0"));
        assert!(WP_LOG_SH.contains("tail -n \"$2\" -- \"$f\""));
    }

    fn hide() -> HostHide {
        HostHide::new(
            &host_names("host7.example.net\n", None),
            "# the host's own list\n192.0.2.1\n\nExampleCloud\nab\n",
            112,
        )
    }

    #[test]
    fn host_words_are_hidden_whole_and_counted() {
        let h = hide();
        assert_eq!(
            h.hide("connect to 192.0.2.1:443 via HOST7.example.net"),
            ("connect to [hidden]:443 via [hidden]".to_owned(), 2)
        );
        // a longer address is not cut apart, nor a word inside a longer one
        assert_eq!(
            h.hide("peer 192.0.2.10 and 192.0.2.1x"),
            ("peer 192.0.2.10 and 192.0.2.1x".to_owned(), 0)
        );
        assert_eq!(
            h.hide("billed by examplecloud, not myexamplecloud"),
            ("billed by [hidden], not myexamplecloud".to_owned(), 1)
        );
        // the CT's number where it is named as one, and only then
        assert_eq!(
            h.hide("ct112 CT 112 lxc/112 lxc-112 ct1120 port 112"),
            (
                "[hidden] [hidden] [hidden] [hidden] ct1120 port 112".to_owned(),
                4
            )
        );
        // a word shorter than the minimum and a comment are not taken
        assert_eq!(
            h.hide("ab # the host's own list"),
            ("ab # the host's own list".to_owned(), 0)
        );
    }

    #[test]
    fn the_hosts_names_are_its_short_and_full_names_and_their_domain() {
        assert_eq!(
            host_names("node7\n", Some("node7.dc.example.net\n")),
            ["node7", "node7.dc.example.net", "dc.example.net"]
        );
        // a dotted /etc/hostname gives its first label as well
        assert_eq!(
            host_names("node7.example.net", None),
            ["node7.example.net", "node7", "example.net"]
        );
        assert!(host_names("", None).is_empty());
        let h = HostHide::new(&host_names("node7", Some("node7.example.net")), "", 112);
        assert_eq!(
            h.hide("NODE7 node7.example.net mail.example.net"),
            ("[hidden] [hidden] mail.[hidden]".to_owned(), 3)
        );
        // a domain shorter than the minimum is not taken
        let h = HostHide::new(&host_names("node7", Some("node7.io")), "", 112);
        assert_eq!(h.hide("see a.io"), ("see a.io".to_owned(), 0));
        // more ways of naming the CT
        assert_eq!(
            h.hide("CT-112 vm-112-disk-0 subvol-112-disk-1"),
            ("[hidden] [hidden]-0 [hidden]-1".to_owned(), 3)
        );
    }

    #[test]
    fn a_host_word_across_the_cut_is_hidden_before_the_cut() {
        let h = HostHide::new(&host_names("host7", Some("host7.example.net")), "", 112);
        // the cut falls inside the name: cut first, " hos" would be sent
        let mut line = "x".repeat(LOG_LINE_MAX - 4);
        line.push_str(" host7.example.net and more");
        let sent = send_line(&line, &h);
        assert_eq!(sent.len(), LOG_LINE_MAX);
        assert!(sent.ends_with("x [hi"), "{}", &sent[LOG_LINE_MAX - 20..]);
        // and secrets are masked on the way
        assert_eq!(
            send_line("password=hunter2 on host7", &h),
            "password=*** on [hidden]"
        );
    }

    #[test]
    fn host_paths_are_hidden_to_the_next_blank() {
        let h = HostHide::new(&[], "", 112);
        assert_eq!(
            h.hide("open /var/lib/lxc/112/rootfs/x failed; see /etc/pve/lxc/112.conf"),
            ("open [hidden] failed; see [hidden]".to_owned(), 2)
        );
        assert_eq!(
            h.hide("\"/var/lib/vz/dump/a.tar\" and /dev/pve/vm-112-disk-0"),
            ("\"[hidden]\" and [hidden]".to_owned(), 2)
        );
        // a site's own paths stay
        assert_eq!(
            h.hide("PHP Fatal in /var/www/site2/wp-content/x.php"),
            ("PHP Fatal in /var/www/site2/wp-content/x.php".to_owned(), 0)
        );
    }

    #[test]
    fn secrets_do_not_leave_with_the_logs() {
        for (line, want) in [
            (
                "GET /wp-login.php?log=a&pwd=hunter2&x=1 200",
                "GET /wp-login.php?log=a&pwd=***&x=1 200",
            ),
            ("db_password=s3cret host=db", "db_password=*** host=db"),
            (
                "Authorization: Bearer abc.def.ghi",
                "Authorization: Bearer ***",
            ),
            (
                "authorization: Basic dXNlcjpwYXNz next",
                "authorization: Basic *** next",
            ),
            (
                "Set-Cookie: wordpress_logged_in=abc123; path=/",
                "Set-Cookie: ***",
            ),
            // 33e28, #631 seg. 5 M2: one sample per shape that used to pass unmasked
            (
                "{\"user\":\"a\",\"password\":\"hunter2\"}",
                "{\"user\":\"a\",\"password\":\"***\"}",
            ),
            (
                "\"api_key\": \"k1\", \"n\": 1",
                "\"api_key\": \"***\", \"n\": 1",
            ),
            ("password = hunter2 next", "password = *** next"),
            ("'password' => 'hunter2',", "'password' => '***',"),
            ("[password] => hunter2", "[password] => ***"),
            ("    [db_password] =>hunter2", "    [db_password] =>***"),
            (
                "Cookie: a=1; wordpress_logged_in_abc=SECRET; b=2",
                "Cookie: ***",
            ),
            (
                "Authorization: Digest username=\"u\", response=\"6629fae4\"",
                "Authorization: ***",
            ),
            (
                "authorization: BEARER abc.def next",
                "authorization: BEARER *** next",
            ),
            // a key that is only a word in a sentence is left alone
            ("password reset requested", "password reset requested"),
            ("token=abc123", "token=***"),
            ("api_key=\"k1\" other", "api_key=\"***\" other"),
            ("X-Signature: deadbeef", "X-Signature: ***"),
            (
                "nothing to hide here: 200 OK",
                "nothing to hide here: 200 OK",
            ),
            ("中文 密碼 password=祕密 完", "中文 密碼 password=*** 完"),
            ("password=", "password="),
        ] {
            assert_eq!(mask_line(line), want, "{line}");
        }
    }

    #[test]
    fn the_host_keeps_its_own_count_of_log_output() {
        assert_eq!(log_bytes_max(1), 4096);
        assert_eq!(log_bytes_max(200), 819_200);
        // the largest request a customer can make stays under the fixed ceiling
        assert_eq!(log_bytes_max(LOG_LINES_MAX), 8_192_000);
        assert!(log_bytes_max(LOG_LINES_MAX) < 8 * 1024 * 1024);
        assert_eq!(log_bytes_max(u32::MAX), 8 * 1024 * 1024);
        assert_eq!(clip_line("short"), "short");
        let long = "a".repeat(LOG_LINE_MAX + 100);
        assert_eq!(clip_line(&long).len(), LOG_LINE_MAX);
        // a line of three-byte characters is cut between characters, not inside one
        let wide = "密".repeat(2000);
        let cut = clip_line(&wide);
        assert_eq!(cut.len(), 4095);
        assert!(wide.starts_with(cut));
    }
    /// ebb36, #641 seg. 2 L1: what a visitor put into a log does not drive the customer's terminal.
    #[test]
    fn nothing_in_a_line_reaches_the_terminal_as_a_control() {
        assert_eq!(plain("GET /a\tb 200"), "GET /a\tb 200");
        assert_eq!(plain("ua=\u{1b}]52;c;aGk=\u{7}"), "ua=\\x1b]52;c;aGk=\\x07");
        assert_eq!(plain("a\u{1b}[2Jb\rc\u{7f}"), "a\\x1b[2Jb\\x0dc\\x7f");
        assert_eq!(plain("x\u{9b}31m"), "x\\x9b31m");
        assert_eq!(plain("evil\u{202e}gpj.exe"), "evil\\u{202e}gpj.exe");
        assert_eq!(plain("中文 記錄 ok"), "中文 記錄 ok");
        // what nobody sees and a reader's AI does (ebb36, #648 L1): the tag block, a soft
        // hyphen, an invisible operator, private use, a non-character, a variation selector
        assert_eq!(
            plain("ok\u{e0049}\u{e0067}\u{e007f}"),
            "ok\\u{e0049}\\u{e0067}\\u{e007f}"
        );
        assert_eq!(plain("a\u{ad}b\u{2062}c"), "a\\xadb\\u{2062}c");
        assert_eq!(
            plain("\u{e000}\u{fffe}\u{fe0f}"),
            "\\u{e000}\\u{fffe}\\u{fe0f}"
        );
        // a control character inside a secret's value does not let the value out (N2)
        let masked = mask_line("password=\u{1b}hunter2 next");
        assert!(
            !masked.contains("hunter2") && !masked.contains('\u{1b}'),
            "{masked}"
        );
        assert_eq!(
            plain("a\u{200f}b\u{200b}c\u{feff}d\u{061c}"),
            "a\\u{200f}b\\u{200b}c\\u{feff}d\\u{61c}"
        );
        // and it is what the customer's log lines go through, after the secrets
        let line = mask_line("password=hunter2 \u{1b}[31mred");
        assert!(
            !line.contains('\u{1b}') && !line.contains("hunter2"),
            "{line}"
        );
        assert!(line.contains("\\x1b[31mred"));
    }
}
