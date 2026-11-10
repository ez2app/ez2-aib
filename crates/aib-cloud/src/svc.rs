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

/// One log line with secret-looking values replaced by `***`. A value runs from after
/// `key=` / `key:` (any case, also inside a longer name such as `db_password`) to the next
/// space, `&`, quote or end of line. Deliberately broad: a masked harmless value costs a
/// second look, a leaked credential costs the site.
pub fn mask_line(line: &str) -> String {
    let lower = line.to_ascii_lowercase();
    let bytes = line.as_bytes();
    let mut out = String::with_capacity(line.len());
    let mut i = 0;
    while i < bytes.len() {
        let hit = SECRET_KEYS.iter().find_map(|k| {
            let after = i + k.len();
            (lower[i..].starts_with(k) && matches!(bytes.get(after), Some(b'=' | b':')))
                .then_some(after + 1)
        });
        match hit {
            Some(mut value) => {
                out.push_str(&line[i..value]);
                // `Authorization: Bearer xyz`: skip one blank and one scheme word
                while bytes.get(value) == Some(&b' ') {
                    out.push(' ');
                    value += 1;
                }
                let rest = &line[value..];
                let scheme = ["Bearer ", "Basic ", "bearer ", "basic "]
                    .iter()
                    .find(|s| rest.starts_with(**s))
                    .map_or(0, |s| s.len());
                out.push_str(&rest[..scheme]);
                let mut secret = &rest[scheme..];
                let mut skipped = scheme;
                // a quoted value runs to its closing quote
                let quote = secret.chars().next().filter(|c| *c == '"' || *c == '\'');
                let end = match quote {
                    Some(q) => {
                        out.push(q);
                        secret = &secret[1..];
                        skipped += 1;
                        secret.find(q).unwrap_or(secret.len())
                    }
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
                "Set-Cookie: ***; path=/",
            ),
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
}
