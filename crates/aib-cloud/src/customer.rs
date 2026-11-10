// SPDX-License-Identifier: Apache-2.0
//! What a customer may ask the host to do to **their own site** (4.4.1 §1: the customer's
//! AIB seat operates the site; Andy 11-10: "客戶要自己在他的 aib 裡操作主機").
//!
//! The customer's key reaches one fixed command on the host, with the site code fixed by
//! us in that key's entry. What the customer sends arrives as one line of text; this
//! module turns that line into a [`Request`] or refuses it. The line is **data**: it is
//! never handed to a shell, it cannot name a site, and anything outside the small grammar
//! below is refused whole. No I/O.
//!
//! ```text
//! status
//! logs <nginx|php|db> [lines <1..=2000>] [minutes <1..=10080>]
//! php-reload
//! restart
//! backup
//! ```
//!
//! Not offered to customers: start / stop, domains, resources, quarantine, anything about
//! another site or the host.

use crate::svc::{LogSource, LOG_LINES_MAX, LOG_MINUTES_MAX};

/// Longest request line accepted, in bytes.
pub const REQUEST_MAX: usize = 200;

/// One thing a customer asked for.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Request {
    /// The checks of their site.
    Status,
    /// A bounded, masked log of their site.
    Logs {
        /// Which service.
        source: LogSource,
        /// How many lines at most.
        lines: u32,
        /// How far back, in minutes.
        minutes: u32,
    },
    /// Reload PHP in their site.
    PhpReload,
    /// Reboot their site's CT.
    Restart,
    /// How fresh their site's backup is.
    Backup,
}

impl Request {
    /// Whether the request changes something (and so needs the operation journal).
    pub fn writes(self) -> bool {
        matches!(self, Request::PhpReload | Request::Restart)
    }
}

/// Why a request line was refused. The reason is a fixed word: what the customer sent is
/// never echoed back.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Refused {
    /// Nothing was sent (an interactive login, for instance).
    Empty,
    /// Too long, or characters outside `a-z 0-9 -` and blanks.
    Malformed,
    /// A first word that is not one of the requests.
    UnknownRequest,
    /// A request with words it does not take, or a number out of range.
    BadArguments,
}

impl Refused {
    /// The word shown to the customer.
    pub fn as_str(self) -> &'static str {
        match self {
            Refused::Empty => "no_request",
            Refused::Malformed => "malformed_request",
            Refused::UnknownRequest => "unknown_request",
            Refused::BadArguments => "bad_arguments",
        }
    }
}

/// Default number of log lines and look-back when the customer names none.
const LOG_LINES_DEFAULT: u32 = 200;
const LOG_MINUTES_DEFAULT: u32 = 60;

/// Turn the customer's line into a request.
pub fn parse_request(line: &str) -> Result<Request, Refused> {
    if line.trim().is_empty() {
        return Err(Refused::Empty);
    }
    if line.len() > REQUEST_MAX
        || !line
            .bytes()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-' || c == b' ')
    {
        return Err(Refused::Malformed);
    }
    let mut words = line.split(' ').filter(|w| !w.is_empty());
    let first = words.next().ok_or(Refused::Empty)?;
    let rest: alloc::vec::Vec<&str> = words.collect();
    let alone = |r: Request| {
        if rest.is_empty() {
            Ok(r)
        } else {
            Err(Refused::BadArguments)
        }
    };
    match first {
        "status" => alone(Request::Status),
        "php-reload" => alone(Request::PhpReload),
        "restart" => alone(Request::Restart),
        "backup" => alone(Request::Backup),
        "logs" => {
            let (source, mut options) = rest.split_first().ok_or(Refused::BadArguments)?;
            let source = LogSource::parse(source).ok_or(Refused::BadArguments)?;
            let (mut lines, mut minutes) = (None, None);
            while let [key, value, tail @ ..] = options {
                let n: u32 = value.parse().map_err(|_| Refused::BadArguments)?;
                let slot = match *key {
                    "lines" if (1..=LOG_LINES_MAX).contains(&n) => &mut lines,
                    "minutes" if (1..=LOG_MINUTES_MAX).contains(&n) => &mut minutes,
                    _ => return Err(Refused::BadArguments),
                };
                if slot.replace(n).is_some() {
                    return Err(Refused::BadArguments);
                }
                options = tail;
            }
            if !options.is_empty() {
                return Err(Refused::BadArguments);
            }
            Ok(Request::Logs {
                source,
                lines: lines.unwrap_or(LOG_LINES_DEFAULT),
                minutes: minutes.unwrap_or(LOG_MINUTES_DEFAULT),
            })
        }
        _ => Err(Refused::UnknownRequest),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    #[test]
    fn the_whole_grammar() {
        assert_eq!(parse_request("status"), Ok(Request::Status));
        assert_eq!(parse_request("  status  "), Ok(Request::Status));
        assert_eq!(parse_request("php-reload"), Ok(Request::PhpReload));
        assert_eq!(parse_request("restart"), Ok(Request::Restart));
        assert_eq!(parse_request("backup"), Ok(Request::Backup));
        assert_eq!(
            parse_request("logs nginx"),
            Ok(Request::Logs {
                source: LogSource::Nginx,
                lines: 200,
                minutes: 60
            })
        );
        assert_eq!(
            parse_request("logs php minutes 30 lines 50"),
            Ok(Request::Logs {
                source: LogSource::Php,
                lines: 50,
                minutes: 30
            })
        );
        assert_eq!(
            parse_request("logs db lines 2000 minutes 10080"),
            Ok(Request::Logs {
                source: LogSource::Db,
                lines: 2000,
                minutes: 10080
            })
        );
        assert!(Request::Restart.writes() && Request::PhpReload.writes());
        assert!(!Request::Status.writes() && !Request::Backup.writes());
    }

    #[test]
    fn everything_else_is_refused_whole() {
        for (line, why) in [
            ("", Refused::Empty),
            ("   ", Refused::Empty),
            ("stop", Refused::UnknownRequest),
            ("start", Refused::UnknownRequest),
            ("quarantine", Refused::UnknownRequest),
            ("bash", Refused::UnknownRequest),
            ("status site3", Refused::BadArguments),
            ("restart now", Refused::BadArguments),
            ("logs", Refused::BadArguments),
            ("logs wp", Refused::BadArguments),
            ("logs nginx lines", Refused::BadArguments),
            ("logs nginx lines 0", Refused::BadArguments),
            ("logs nginx lines 2001", Refused::BadArguments),
            ("logs nginx minutes 10081", Refused::BadArguments),
            ("logs nginx lines 5 lines 6", Refused::BadArguments),
            ("logs nginx since yesterday", Refused::BadArguments),
            ("logs nginx lines -5", Refused::BadArguments),
            // nothing a shell would care about gets as far as a word
            ("status; reboot", Refused::Malformed),
            ("status && cat /etc/shadow", Refused::Malformed),
            ("logs nginx | nc x 1", Refused::Malformed),
            ("status\nrestart", Refused::Malformed),
            ("STATUS", Refused::Malformed),
            ("logs nginx --since=-1d", Refused::Malformed),
            ("status\t", Refused::Malformed),
            ("logs ../../etc", Refused::Malformed),
        ] {
            assert_eq!(parse_request(line), Err(why), "{line:?}");
        }
        let long = "status ".repeat(40);
        assert_eq!(parse_request(&long), Err(Refused::Malformed));
        assert_eq!(Refused::Malformed.as_str(), "malformed_request");
    }
}
