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

use serde_json::{json, Value};

use crate::svc::{LogSource, LOG_LINES_MAX, LOG_MINUTES_MAX};
use crate::verify::{exit_code, CheckResult, Outcome};

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

    /// Whether the request is offered to customers yet. The rest parse, and the host
    /// answers them "not available yet", but the customer's side neither lists nor sends
    /// them (Andy 11-10: 「隱藏未開放動作」).
    pub fn offered(self) -> bool {
        matches!(self, Request::Status | Request::Logs { .. })
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

/// One thing the holder of a **customer** key asked for. A customer key reaches every
/// site whose record names that customer as its owner; a site key reaches one site and
/// speaks [`Request`] directly (Andy 11-10: "要能綁站、也綁客戶、因為客戶底下可能有
/// 單一客戶" — an agency holds the customer key, the client of one of its sites a site
/// key).
///
/// ```text
/// sites
/// usage
/// site <code> <a site request, as in the grammar at the top>
/// ```
///
/// The site named here is **data**: whether it exists and whether it is this customer's
/// is for the host to decide from its own records, with one answer for both "no such
/// site" and "not yours".
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OwnerRequest<'a> {
    /// The customer's sites.
    Sites,
    /// What the customer's sites hold of the plan together.
    Usage,
    /// A request about one site.
    Site {
        /// The site's code as sent; checked for shape only.
        site: &'a str,
        /// What is asked of it.
        request: Request,
    },
}

/// Turn a customer key's line into a request.
pub fn parse_owner_request(line: &str) -> Result<OwnerRequest<'_>, Refused> {
    if line.trim().is_empty() {
        return Err(Refused::Empty);
    }
    // a site code may carry dots; nothing else is added to the site grammar's characters
    if line.len() > REQUEST_MAX
        || !line.bytes().all(|c| {
            c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-' || c == b'.' || c == b' '
        })
    {
        return Err(Refused::Malformed);
    }
    let mut words = line.split(' ').filter(|w| !w.is_empty());
    let first = words.next().ok_or(Refused::Empty)?;
    match first {
        "sites" | "usage" => {
            if words.next().is_some() {
                return Err(Refused::BadArguments);
            }
            Ok(if first == "sites" {
                OwnerRequest::Sites
            } else {
                OwnerRequest::Usage
            })
        }
        "site" => {
            let site = words.next().ok_or(Refused::BadArguments)?;
            if !crate::site::valid_code(site) {
                return Err(Refused::BadArguments);
            }
            let rest = words.collect::<alloc::vec::Vec<_>>().join(" ");
            // the rest is a site request, checked by the site grammar (no dots there)
            let request = parse_request(&rest).map_err(|why| match why {
                Refused::Empty => Refused::BadArguments,
                other => other,
            })?;
            Ok(OwnerRequest::Site { site, request })
        }
        _ => Err(Refused::UnknownRequest),
    }
}

/// What a customer is shown of their site's checks (c477a 11-10,
/// PROPOSAL-customer-status-codes): a **whitelist**.
///
/// * `S01` (is the site up) and `S05` (does it have what the plan gives) are shown as
///   their own rows, under customer-facing codes: `site_down`, `below_plan`, `unknown`.
/// * Everything else — every other check, and any code of `S01` / `S05` not named here —
///   is folded into one row `P` that carries the worst of them as `provider_check`. Red
///   stays red; which check and why does not leave the host: a firewall that is off, or
///   the way a site is isolated, is not something to tell whoever holds a customer's key.
/// * A check added later lands in `P` until it is named here.
/// * `exit` is computed over **all** checks, exactly as `site verify` does.
pub fn status_view(site: Option<&str>, results: &[CheckResult]) -> Value {
    // the order `verify::exit_code` uses: red, then unknown, then warn
    let rank = |o: Outcome| match o {
        Outcome::Green => 0u8,
        Outcome::Warn(_) => 1,
        Outcome::Unknown(_) => 2,
        Outcome::Red(_) => 3,
    };
    let mut shown: alloc::vec::Vec<Value> = alloc::vec::Vec::new();
    let mut provider = 0u8;
    for r in results {
        let own = match (r.id, r.outcome) {
            ("S01" | "S05", Outcome::Green) => Some(("green", None)),
            ("S01", Outcome::Red("ct_down")) => Some(("red", Some("site_down"))),
            ("S05", Outcome::Warn("under_record")) => Some(("warn", Some("below_plan"))),
            ("S01" | "S05", Outcome::Unknown(_)) => Some(("unknown", Some("unknown"))),
            (_, _) => None,
        };
        match own {
            Some((result, code)) => {
                shown.push(json!({ "id": r.id, "result": result, "code": code }));
            }
            None => provider = provider.max(rank(r.outcome)),
        }
    }
    let (result, code) = match provider {
        0 => ("green", None),
        1 => ("warn", Some("provider_check")),
        2 => ("unknown", Some("unknown")),
        _ => ("red", Some("provider_check")),
    };
    shown.push(json!({ "id": "P", "result": result, "code": code }));
    json!({ "site": site, "exit": exit_code(results), "checks": shown })
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
        assert!(Request::Status.offered());
        assert!(parse_request("logs db").unwrap().offered());
        for hidden in ["php-reload", "restart", "backup"] {
            assert!(!parse_request(hidden).unwrap().offered(), "{hidden}");
        }
    }

    /// Andy 11-10: a key may be bound to a customer as well as to one site.
    #[test]
    fn a_customer_key_names_the_site_and_nothing_else() {
        assert_eq!(parse_owner_request("sites"), Ok(OwnerRequest::Sites));
        assert_eq!(parse_owner_request(" usage "), Ok(OwnerRequest::Usage));
        assert_eq!(
            parse_owner_request("site shop.a status"),
            Ok(OwnerRequest::Site {
                site: "shop.a",
                request: Request::Status
            })
        );
        assert_eq!(
            parse_owner_request("site site2  logs php lines 50"),
            Ok(OwnerRequest::Site {
                site: "site2",
                request: parse_request("logs php lines 50").unwrap()
            })
        );
        // a site key's own words are not a customer key's: the site has to be named
        assert_eq!(parse_owner_request("status"), Err(Refused::UnknownRequest));
        for (line, why) in [
            ("", Refused::Empty),
            ("sites all", Refused::BadArguments),
            ("usage site2", Refused::BadArguments),
            ("site", Refused::BadArguments),
            ("site site2", Refused::BadArguments),
            ("site -x status", Refused::BadArguments),
            ("site a status", Refused::BadArguments),
            ("site site2 stop", Refused::UnknownRequest),
            ("site site2 status site3", Refused::BadArguments),
            ("site site2 logs php.ini", Refused::Malformed),
            ("site ../site3 status", Refused::Malformed),
            ("site site2 status; id", Refused::Malformed),
            ("SITES", Refused::Malformed),
        ] {
            assert_eq!(parse_owner_request(line), Err(why), "{line:?}");
        }
        // and the site grammar still takes no site name and no dots
        assert_eq!(
            parse_request("site site2 status"),
            Err(Refused::UnknownRequest)
        );
        assert_eq!(parse_request("status shop.a"), Err(Refused::Malformed));
    }

    fn checks(list: &[(&'static str, Outcome)]) -> alloc::vec::Vec<CheckResult> {
        list.iter()
            .map(|&(id, outcome)| CheckResult { id, outcome })
            .collect()
    }

    fn all_green_but(change: &[(&'static str, Outcome)]) -> alloc::vec::Vec<CheckResult> {
        let ids = ["S00", "S01", "S02", "S03", "S04", "S05", "S06"];
        let list: alloc::vec::Vec<_> = ids
            .iter()
            .map(|id| {
                let o = change.iter().find(|c| c.0 == *id).map(|c| c.1);
                (*id, o.unwrap_or(Outcome::Green))
            })
            .collect();
        checks(&list)
    }

    #[test]
    fn a_customer_is_not_told_what_is_wrong_on_our_side() {
        use std::string::ToString;
        // the firewall is off and the group is wrong: one red row, no word of either
        let view = status_view(
            Some("site2"),
            &all_green_but(&[
                ("S03", Outcome::Red("fw_disabled")),
                ("S04", Outcome::Red("wrong_group")),
            ]),
        );
        assert_eq!(
            view,
            json!({ "site": "site2", "exit": 1, "checks": [
                { "id": "S01", "result": "green", "code": null },
                { "id": "S05", "result": "green", "code": null },
                { "id": "P", "result": "red", "code": "provider_check" },
            ]})
        );
        let text = view.to_string();
        for word in ["fw_", "group", "policy", "S03", "S04"] {
            assert!(!text.contains(word), "{word}");
        }
        // a site we isolated: nothing says so, whatever the checks found
        let view = status_view(
            Some("site2"),
            &all_green_but(&[("S04", Outcome::Red("quarantine_group_missing"))]),
        );
        assert!(!view.to_string().contains("quarantine"));
        assert_eq!(view["exit"], 1);
        // all quiet: three green rows
        let view = status_view(Some("site2"), &all_green_but(&[]));
        assert_eq!(view["exit"], 0);
        assert_eq!(
            view["checks"][2],
            json!({ "id": "P", "result": "green", "code": null })
        );
    }

    #[test]
    fn only_named_checks_and_codes_are_shown_as_they_are() {
        use std::string::ToString;
        // the two things a customer can see for themselves or paid for
        let view = status_view(
            None,
            &all_green_but(&[
                ("S01", Outcome::Red("ct_down")),
                ("S05", Outcome::Warn("under_record")),
            ]),
        );
        assert_eq!(
            view["checks"],
            json!([
                { "id": "S01", "result": "red", "code": "site_down" },
                { "id": "S05", "result": "warn", "code": "below_plan" },
                { "id": "P", "result": "green", "code": null },
            ])
        );
        assert_eq!(view["exit"], 1);
        // unread on either is "unknown", and so is an unknown on our side
        let view = status_view(
            None,
            &all_green_but(&[
                ("S01", Outcome::Unknown("unread")),
                ("S05", Outcome::Unknown("unparsed")),
                ("S02", Outcome::Unknown("unread")),
            ]),
        );
        assert_eq!(
            view["checks"],
            json!([
                { "id": "S01", "result": "unknown", "code": "unknown" },
                { "id": "S05", "result": "unknown", "code": "unknown" },
                { "id": "P", "result": "unknown", "code": "unknown" },
            ])
        );
        assert_eq!(view["exit"], 3);
        // more than the plan is ours to deal with: no S05 row, the provider row is red
        let view = status_view(
            None,
            &all_green_but(&[("S05", Outcome::Red("over_record"))]),
        );
        assert_eq!(
            view["checks"],
            json!([
                { "id": "S01", "result": "green", "code": null },
                { "id": "P", "result": "red", "code": "provider_check" },
            ])
        );
        // a check added later, and a new code on a shown check, are not shown until named
        let mut later = all_green_but(&[("S01", Outcome::Red("brand_new_code"))]);
        later.extend(checks(&[("S99", Outcome::Warn("new_thing"))]));
        let view = status_view(None, &later);
        let text = view.to_string();
        assert!(!text.contains("brand_new_code") && !text.contains("new_thing"));
        assert!(!text.contains("S99"));
        assert_eq!(view["exit"], 1);
        // a warning on our side alone is a warning
        let view = status_view(None, &checks(&[("S99", Outcome::Warn("new_thing"))]));
        assert_eq!(
            view,
            json!({ "site": null, "exit": 2, "checks": [
                { "id": "P", "result": "warn", "code": "provider_check" },
            ]})
        );
    }
}
