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
//!   through the journal of a fixed set of units. What comes back is passed through
//!   [`mask_line`] before it leaves the host.

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
}

impl LogSource {
    /// The spelling on the command line.
    pub fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "nginx" => LogSource::Nginx,
            "php" => LogSource::Php,
            "db" => LogSource::Db,
            _ => return None,
        })
    }

    fn unit(self) -> &'static str {
        match self {
            LogSource::Nginx => "nginx.service",
            LogSource::Php => "php*-fpm.service",
            LogSource::Db => "mariadb.service",
        }
    }
}

/// Most lines one `site logs` returns.
pub const LOG_LINES_MAX: u32 = 2000;
/// Longest look-back, in minutes (seven days).
pub const LOG_MINUTES_MAX: u32 = 7 * 24 * 60;

/// The command that reads a site's log: the journal of one unit inside the CT, bounded
/// in lines and in time. Read-only, but it runs a program inside a customer CT.
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
        source.unit(),
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

/// `line` with nothing in it that a terminal would act on (ebb36, #641 seg. 2 L1). Part of
/// a log comes from whoever visits the site — a URL, a user agent — and is shown in the
/// customer's own terminal: control characters (ESC and the rest of C0 but the tab, DEL,
/// C1) and the characters that reorder or hide text are written out as `\x1b` /
/// `\u{202e}` instead of being sent.
pub fn plain(line: &str) -> String {
    let mut out = String::with_capacity(line.len());
    for c in line.chars() {
        // direction marks and overrides, line / paragraph separators, zero-width characters
        let reorders = matches!(c, '\u{061c}' | '\u{200b}'..='\u{200f}' | '\u{2028}'..='\u{202e}' | '\u{2066}'..='\u{2069}' | '\u{feff}');
        if c == '\t' || !(c.is_control() || reorders) {
            out.push(c);
        } else if u32::from(c) < 0x100 {
            out.push_str(&alloc::format!("\\x{:02x}", u32::from(c)));
        } else {
            out.push_str(&alloc::format!("\\u{{{:x}}}", u32::from(c)));
        }
    }
    out
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
        assert_eq!(LogSource::parse("wp"), None);
        assert_eq!(LogSource::parse("php"), Some(LogSource::Php));
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
