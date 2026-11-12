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
//! logs <nginx|php|db|wp> [lines <1..=2000>] [minutes <1..=10080>]
//! login-link
//! cache flush
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

/// Longest line of a customer key, in bytes: room for `site <code> domain check <name>`
/// with the longest code (41) and the longest name (253). What follows a site's code is
/// still a site request and held to [`REQUEST_MAX`] by its own parser.
pub const OWNER_REQUEST_MAX: usize = 320;

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
    /// A one-time link that logs the site's first administrator straight into the
    /// dashboard (Andy 11-12 03:58: the customer takes it themselves after an opening).
    /// The host's answer is the link alone, one line on standard output.
    LoginLink,
    /// Clear their site's caches: WordPress's object cache and the page cache plugin in
    /// their site's CT, and at Cloudflare the cache of their site's own names only (c477a
    /// L7: never a whole zone, never another site's).
    CacheFlush,
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
        matches!(
            self,
            Request::PhpReload | Request::Restart | Request::LoginLink | Request::CacheFlush
        )
    }

    /// Whether the request is offered to customers yet. The rest parse, and the host
    /// answers them "not available yet", but the customer's side neither lists nor sends
    /// them (Andy 11-10: 「隱藏未開放動作」).
    pub fn offered(self) -> bool {
        matches!(
            self,
            Request::Status | Request::Logs { .. } | Request::LoginLink
        )
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
        "login-link" => alone(Request::LoginLink),
        "cache" => match rest.as_slice() {
            ["flush"] => Ok(Request::CacheFlush),
            _ => Err(Refused::BadArguments),
        },
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
/// site create <name> [blank]
/// site <code> domain check <name>
/// site <code> domain add <name>
/// ```
///
/// `site create <name>` opens a new site for the customer at a domain of their own (Andy
/// 11-11: 「客戶可以透過 aib 的 skill 完成新網站含網址的建立」, and no address of ours is given
/// to it: 「不用預設主機網址」). The host chooses the site's code; the name is the only
/// thing about the new site said on the line. `create` is therefore not a site's code
/// here. The new site gets our WordPress package unless the customer asks for a `blank`
/// one (Andy 11-11: 「除非客戶說空白wp，不然都用我們的wp模版包」). A domain is a name the
/// customer points at us; `check` looks, `add` puts one more on a site.
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
    /// Open a new site for this customer at a domain of their own; the host chooses the
    /// site's code.
    Create {
        /// The name as sent; checked for shape only ([`crate::edge::valid_domain`]).
        name: &'a str,
        /// A WordPress with nothing of ours added, instead of our package.
        blank: bool,
    },
    /// A domain of the customer's own, for one of their sites.
    Domain {
        /// The site's code as sent; checked for shape only.
        site: &'a str,
        /// Look only, or put the name on the site.
        action: DomainAction,
        /// The name as sent; checked for shape only ([`crate::edge::valid_domain`]).
        name: &'a str,
    },
}

/// What is asked about a domain.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DomainAction {
    /// Whether the name is ready to be put on a site (its DNS). Changes nothing.
    Check,
    /// Put the name on the site.
    Add,
}

impl OwnerRequest<'_> {
    /// Whether the request changes something (and so needs the operation journal).
    pub fn writes(self) -> bool {
        match self {
            OwnerRequest::Sites | OwnerRequest::Usage => false,
            OwnerRequest::Site { request, .. } => request.writes(),
            OwnerRequest::Create { .. } => true,
            OwnerRequest::Domain { action, .. } => action == DomainAction::Add,
        }
    }

    /// Whether the request is offered to customers yet (as [`Request::offered`]): what is
    /// not parses, and the host answers "not available yet", but the customer's side
    /// neither lists nor sends it.
    pub fn offered(self) -> bool {
        match self {
            OwnerRequest::Sites | OwnerRequest::Usage => true,
            OwnerRequest::Site { request, .. } => request.offered(),
            // the host opens a site in the background (E1); binding a domain is PR C
            OwnerRequest::Create { .. } => true, // E1
            OwnerRequest::Domain { .. } => false,
        }
    }
}

/// Turn a customer key's line into a request.
pub fn parse_owner_request(line: &str) -> Result<OwnerRequest<'_>, Refused> {
    if line.trim().is_empty() {
        return Err(Refused::Empty);
    }
    // a site code may carry dots; nothing else is added to the site grammar's characters
    if line.len() > OWNER_REQUEST_MAX
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
            // `create` is the request, never a site's code (`site::RESERVED_CODES`): one
            // name follows it, the new site's domain, then `blank` or nothing
            if site == "create" {
                let (name, option, more) = (words.next(), words.next(), words.next());
                let blank = match (option, more) {
                    (None, None) => false,
                    (Some("blank"), None) => true,
                    _ => return Err(Refused::BadArguments),
                };
                let name = name.filter(|n| crate::edge::valid_domain(n));
                return name
                    .map(|name| OwnerRequest::Create { name, blank })
                    .ok_or(Refused::BadArguments);
            }
            if !crate::site::valid_code(site) {
                return Err(Refused::BadArguments);
            }
            let rest: alloc::vec::Vec<&str> = words.collect();
            if let ["domain", more @ ..] = rest.as_slice() {
                let &[action, name] = more else {
                    return Err(Refused::BadArguments);
                };
                let action = match action {
                    "check" => DomainAction::Check,
                    "add" => DomainAction::Add,
                    _ => return Err(Refused::BadArguments),
                };
                if !crate::edge::valid_domain(name) {
                    return Err(Refused::BadArguments);
                }
                return Ok(OwnerRequest::Domain { site, action, name });
            }
            let rest = rest.join(" ");
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

/// What a request is, in Ez2.PAGE's own words for operations (Andy 11-11: "名稱與
/// ez2.page 對齊,ez2.page 之後會串接提供主機服務"). Hosting uses the names and the
/// policy of `ez2-page-tenant-authorization` as they are, so one account, one set of
/// roles and one table serve both products.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Operation {
    /// `read_private`: see what is the customer's own — a site's status, the list of sites.
    ReadPrivate,
    /// `edit` / `manage_blocks`: work on a site — reload PHP.
    Edit,
    /// `publish` / `bind_route`: change what is live — restart a site (and, later, bind
    /// a domain).
    Publish,
    /// `view_analytics`: read figures and logs — logs, backup freshness, usage.
    ViewAnalytics,
    /// `archive`: take a site out of service. No customer request does this yet.
    Archive,
    /// `create`: open a new site on the customer's plan. The customer's own decision
    /// (it uses up the plan), so the owner's and the platform's only.
    Create,
    /// `administer`: sign in to the site as its administrator (a one-time login link),
    /// which can add plugins and users — more than an editor may do (Andy 11-12: the
    /// owner's and the platform's only).
    Administer,
}

impl Operation {
    /// Ez2.PAGE's spelling.
    pub fn as_str(self) -> &'static str {
        match self {
            Operation::ReadPrivate => "read_private",
            Operation::Edit => "edit",
            Operation::Publish => "publish",
            Operation::ViewAnalytics => "view_analytics",
            Operation::Archive => "archive",
            Operation::Create => "create",
            Operation::Administer => "administer",
        }
    }
}

impl Request {
    /// The operation a site request is.
    pub fn operation(self) -> Operation {
        match self {
            Request::Status => Operation::ReadPrivate,
            Request::Logs { .. } | Request::Backup => Operation::ViewAnalytics,
            // clearing a cache changes no content and no account: whoever works on the
            // site may (owner, admin, editor)
            Request::PhpReload | Request::CacheFlush => Operation::Edit,
            // the link signs in as the site's administrator (Andy 11-12: owner and admin)
            Request::LoginLink => Operation::Administer,
            Request::Restart => Operation::Publish,
        }
    }
}

impl OwnerRequest<'_> {
    /// The operation a customer key's request is.
    pub fn operation(self) -> Operation {
        match self {
            OwnerRequest::Sites => Operation::ReadPrivate,
            OwnerRequest::Usage => Operation::ViewAnalytics,
            OwnerRequest::Site { request, .. } => request.operation(),
            OwnerRequest::Create { .. } => Operation::Create,
            // looking at a name's DNS shows nothing of ours, but it is the first half of
            // binding it: the same right as the binding
            OwnerRequest::Domain { .. } => Operation::Publish,
        }
    }
}

/// Who a key belongs to: Ez2.PAGE's six roles, with Ez2.PAGE's policy. A key may carry
/// several.
///
/// | operation | owner | admin | editor | publisher | analyst | viewer |
/// |---|---|---|---|---|---|---|
/// | `read_private` | yes | yes | yes | yes | yes | yes |
/// | `edit`, `manage_blocks` | yes | yes | yes | — | — | — |
/// | `publish`, `bind_route` | yes | yes | — | yes | — | — |
/// | `view_analytics` | yes | yes | — | — | yes | — |
/// | `archive` | yes | yes | — | — | — | — |
/// | `create` | yes | yes | — | — | — | — |
/// | `administer` (login link) | yes | yes | — | — | — | — |
///
/// So, as there: an editor works on a site but changing what is live is the
/// publisher's, and figures and logs are the analyst's. `admin` is the platform's.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Role {
    /// The customer.
    Owner,
    /// The platform's own service identity.
    Admin,
    /// Works on a site's content.
    Editor,
    /// May change what is live.
    Publisher,
    /// Reads figures and logs.
    Analyst,
    /// Sees what is there.
    Viewer,
}

impl Role {
    /// The spelling in a key's entry and in the roster.
    pub fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "owner" => Role::Owner,
            "admin" => Role::Admin,
            "editor" => Role::Editor,
            "publisher" => Role::Publisher,
            "analyst" => Role::Analyst,
            "viewer" => Role::Viewer,
            _ => return None,
        })
    }

    /// Whether this role alone may do an operation: Ez2.PAGE's policy table.
    pub fn may(self, operation: Operation) -> bool {
        use Role::{Admin, Analyst, Editor, Owner, Publisher};
        match operation {
            Operation::ReadPrivate => true,
            Operation::Edit => matches!(self, Owner | Admin | Editor),
            Operation::Publish => matches!(self, Owner | Admin | Publisher),
            Operation::ViewAnalytics => matches!(self, Owner | Admin | Analyst),
            Operation::Archive | Operation::Create => matches!(self, Owner | Admin),
            // the login link signs in as the site's administrator (Andy 11-12)
            Operation::Administer => matches!(self, Owner | Admin),
        }
    }
}

/// The roles of a key, out of the comma-separated word in its entry (`editor,publisher`).
/// `None` when the word is empty, names a role twice or names something that is not a
/// role: an entry we wrote wrongly gives no rights at all rather than some.
pub fn parse_roles(word: &str) -> Option<alloc::vec::Vec<Role>> {
    let mut roles = alloc::vec::Vec::new();
    for part in word.split(',') {
        let role = Role::parse(part)?;
        if roles.contains(&role) {
            return None;
        }
        roles.push(role);
    }
    Some(roles)
}

/// Whether the host's answer to `site <code> status` is how far the opening of a site got
/// (the site has no record yet), not the site's checks: `state` and `step_done` are there
/// (`aib-cloud-host`, `sitecreaterun::progress`). Its words are fixed: shown as they are.
pub fn is_opening(reply: &str) -> bool {
    serde_json::from_str::<Value>(reply.trim())
        .ok()
        .is_some_and(|v| {
            v.get("step_done").is_some() && v.get("state").is_some_and(Value::is_string)
        })
}

/// A site just opened, as the host's answer to `site <code> status` tells it for a while
/// after the opening (`aib-cloud-host`, `sitecreaterun::opened_view`): what the customer's
/// side needs for the opened screen ([`crate::wording::opened`]) and nothing more.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Opened {
    /// The site's code.
    pub site: alloc::string::String,
    /// The site's domain.
    pub domain: alloc::string::String,
    /// Its administrators' logins, the first one first (at least one).
    pub admins: alloc::vec::Vec<alloc::string::String>,
}

/// The opened site in the host's answer, when it is one: `state` is `open` and the site's
/// code, its domain and at least one administrator are there and of the right shape.
pub fn opened_of(reply: &str) -> Option<Opened> {
    let v: Value = serde_json::from_str(reply.trim()).ok()?;
    if v.get("state").and_then(Value::as_str) != Some("open") || !is_opening(reply) {
        return None;
    }
    let site = v.get("site").and_then(Value::as_str)?;
    let domain = v.get("domain").and_then(Value::as_str)?;
    if !crate::site::valid_code(site) || !crate::edge::valid_domain(domain) {
        return None;
    }
    let mut admins = alloc::vec::Vec::new();
    for a in v.get("admins").and_then(Value::as_array)? {
        let a = a.as_str().filter(|a| !a.is_empty())?;
        admins.push(alloc::string::String::from(a));
    }
    if admins.is_empty() {
        return None;
    }
    Some(Opened {
        site: site.into(),
        domain: domain.into(),
        admins,
    })
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
        assert_eq!(parse_request("login-link"), Ok(Request::LoginLink));
        assert_eq!(parse_request("login-link ops"), Err(Refused::BadArguments));
        assert_eq!(parse_request("cache flush"), Ok(Request::CacheFlush));
        assert_eq!(parse_request("  cache   flush "), Ok(Request::CacheFlush));
        for bad in [
            "cache",
            "cache flush all",
            "cache purge",
            "cache flush site2",
        ] {
            assert_eq!(parse_request(bad), Err(Refused::BadArguments), "{bad:?}");
        }
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
        // WordPress's own error log; not listed to customers until its wording is set
        assert_eq!(
            parse_request("logs wp lines 20"),
            Ok(Request::Logs {
                source: LogSource::Wp,
                lines: 20,
                minutes: 60
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
            ("logs syslog", Refused::BadArguments),
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
        // E1 N1 (Andy 11-12 04:3x): the customer takes their login link themselves
        assert!(Request::LoginLink.offered() && Request::LoginLink.writes());
        assert!(parse_request("logs db").unwrap().offered());
        // E10 7.6: the host clears a site's caches, but the customer is not offered it
        // until its sentences are final (HUMAN-PASTE-4542e-1112.md, waiting on Andy)
        assert!(!Request::CacheFlush.offered() && Request::CacheFlush.writes());
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

    /// Andy 11-11: the customer opens a new site at a domain of their own, from their own
    /// aib. The name is all the line says of the new site; it is checked for shape only.
    #[test]
    fn a_customer_key_opens_a_site_and_names_a_domain() {
        let create = OwnerRequest::Create {
            name: "www.example.com",
            blank: false,
        };
        // the domain itself (no `www`) is a name like any other; `blank` asks for a
        // WordPress without our package
        assert_eq!(
            parse_owner_request("site create example.com blank"),
            Ok(OwnerRequest::Create {
                name: "example.com",
                blank: true
            })
        );
        assert_eq!(
            parse_owner_request("site create www.example.com"),
            Ok(create)
        );
        assert_eq!(
            parse_owner_request(" site  create  www.example.com "),
            Ok(create)
        );
        assert_eq!(
            parse_owner_request("site s-ab12cd domain check shop.example.com"),
            Ok(OwnerRequest::Domain {
                site: "s-ab12cd",
                action: DomainAction::Check,
                name: "shop.example.com"
            })
        );
        assert_eq!(
            parse_owner_request("site site2 domain add example.org"),
            Ok(OwnerRequest::Domain {
                site: "site2",
                action: DomainAction::Add,
                name: "example.org"
            })
        );
        for (line, why) in [
            // `create` takes one name: no code, no size chosen by the customer, and
            // a site is not opened without an address
            ("site create", Refused::BadArguments),
            ("site create site9", Refused::BadArguments),
            ("site create status", Refused::BadArguments),
            ("site create example.org site9", Refused::BadArguments),
            ("site create example.org blank blank", Refused::BadArguments),
            ("site create example.org blank site9", Refused::BadArguments),
            ("site create blank", Refused::BadArguments),
            ("site create blank example.org", Refused::BadArguments),
            ("site create domain add example.org", Refused::BadArguments),
            ("site create 192.0.2.7", Refused::BadArguments),
            ("site create *.example.org", Refused::Malformed),
            ("create", Refused::UnknownRequest),
            ("site site2 domain", Refused::BadArguments),
            ("site site2 domain add", Refused::BadArguments),
            ("site site2 domain rm example.org", Refused::BadArguments),
            (
                "site site2 domain add example.org example.net",
                Refused::BadArguments,
            ),
            ("site site2 domain add localhost", Refused::BadArguments),
            ("site site2 domain add 192.0.2.7", Refused::BadArguments),
            (
                "site site2 domain add -x.example.org",
                Refused::BadArguments,
            ),
            ("site site2 domain add example..org", Refused::BadArguments),
            ("site site2 domain add *.example.org", Refused::Malformed),
            ("site site2 domain add example.org/x", Refused::Malformed),
            ("site site2 domain add EXAMPLE.org", Refused::Malformed),
            // `domain` is a word of the line, not a site's code
            ("site domain add example.org", Refused::BadArguments),
        ] {
            assert_eq!(parse_owner_request(line), Err(why), "{line:?}");
        }
        // the words of the line are nobody's site code, here or in a record
        for word in crate::site::RESERVED_CODES {
            assert!(!crate::site::valid_code(word), "{word}");
            if *word != "create" {
                assert_eq!(
                    parse_owner_request(&alloc::format!("site {word} status")),
                    Err(Refused::BadArguments),
                    "{word}"
                );
            }
        }
        // the longest name there is fits on the line with the longest code
        let long = alloc::format!(
            "site {} domain check {}.{}.{}.{}",
            "s".repeat(41),
            "a".repeat(63),
            "b".repeat(63),
            "c".repeat(63),
            "d".repeat(61)
        );
        assert!(long.len() > REQUEST_MAX && long.len() <= OWNER_REQUEST_MAX);
        assert!(matches!(
            parse_owner_request(&long),
            Ok(OwnerRequest::Domain { .. })
        ));
        let too_long = alloc::format!("site create example.org{}", " ".repeat(OWNER_REQUEST_MAX));
        assert_eq!(parse_owner_request(&too_long), Err(Refused::Malformed));
        // what changes something, and what is offered yet
        assert!(create.writes());
        let check = parse_owner_request("site site2 domain check example.org").unwrap();
        let add = parse_owner_request("site site2 domain add example.org").unwrap();
        assert!(!check.writes() && add.writes());
        assert!(!OwnerRequest::Sites.writes() && OwnerRequest::Sites.offered());
        // E1: opening a site is offered; a domain is not yet
        assert!(create.offered());
        for hidden in [check, add] {
            assert!(!hidden.offered(), "{hidden:?}");
        }
        // opening a site is the owner's alone; a domain is the publisher's too
        assert_eq!(create.operation(), Operation::Create);
        assert_eq!(check.operation(), Operation::Publish);
        assert_eq!(add.operation(), Operation::Publish);
        assert!(Role::Owner.may(Operation::Create) && Role::Admin.may(Operation::Create));
        for role in [Role::Editor, Role::Publisher, Role::Analyst, Role::Viewer] {
            assert!(!role.may(Operation::Create), "{role:?}");
        }
    }

    /// E1: the progress of an opening is told from the site's checks.
    #[test]
    fn an_opening_is_told_from_the_site_s_checks() {
        assert!(is_opening(
            "{\"site\":\"s-abc123\",\"state\":\"opening\",\"step_done\":null,\"stopped_at\":null}\n"
        ));
        assert!(!is_opening("{\"exit\":0,\"checks\":[]}"));
        assert!(!is_opening("{\"state\":\"opening\"}"));
        assert!(!is_opening("not json"));
    }

    /// E1 N1: a site just opened names its domain and its administrators, and only an
    /// answer of that shape is read as one.
    #[test]
    fn an_opened_site_is_read_only_from_its_own_shape() {
        let good = r#"{"site":"s-abc123","state":"open","step_done":"health","stopped_at":null,"domain":"shop.example.test","admins":["ops","ops2"]}"#;
        assert_eq!(
            opened_of(good),
            Some(Opened {
                site: "s-abc123".into(),
                domain: "shop.example.test".into(),
                admins: alloc::vec!["ops".into(), "ops2".into()],
            })
        );
        for bad in [
            // still opening
            r#"{"site":"s-abc123","state":"opening","step_done":"edge","stopped_at":null,"domain":"shop.example.test","admins":["ops"]}"#,
            // no administrator, or one that is not a login
            r#"{"site":"s-abc123","state":"open","step_done":"health","stopped_at":null,"domain":"shop.example.test","admins":[]}"#,
            r#"{"site":"s-abc123","state":"open","step_done":"health","stopped_at":null,"domain":"shop.example.test","admins":[""]}"#,
            r#"{"site":"s-abc123","state":"open","step_done":"health","stopped_at":null,"domain":"shop.example.test","admins":[7]}"#,
            // a domain or a code that is not one
            r#"{"site":"s-abc123","state":"open","step_done":"health","stopped_at":null,"domain":"shop example","admins":["ops"]}"#,
            r#"{"site":"S/1","state":"open","step_done":"health","stopped_at":null,"domain":"shop.example.test","admins":["ops"]}"#,
            // not the progress answer at all
            r#"{"site":"s-abc123","state":"open","domain":"shop.example.test","admins":["ops"]}"#,
            r#"{"exit":0,"checks":[]}"#,
        ] {
            assert_eq!(opened_of(bad), None, "{bad}");
        }
    }

    /// Andy 11-11: names and policy are Ez2.PAGE's.
    #[test]
    fn a_role_gives_what_the_table_says_and_no_more() {
        let all = [
            Role::Owner,
            Role::Admin,
            Role::Editor,
            Role::Publisher,
            Role::Analyst,
            Role::Viewer,
        ];
        let who = |operation: Operation| -> alloc::vec::Vec<Role> {
            all.iter().copied().filter(|r| r.may(operation)).collect()
        };
        // the table, row by row, as ez2-page-tenant-authorization has it
        assert_eq!(who(Operation::ReadPrivate), all);
        assert_eq!(
            who(Operation::Edit),
            [Role::Owner, Role::Admin, Role::Editor]
        );
        assert_eq!(
            who(Operation::Publish),
            [Role::Owner, Role::Admin, Role::Publisher]
        );
        assert_eq!(
            who(Operation::ViewAnalytics),
            [Role::Owner, Role::Admin, Role::Analyst]
        );
        assert_eq!(who(Operation::Archive), [Role::Owner, Role::Admin]);
        assert_eq!(who(Operation::Create), [Role::Owner, Role::Admin]);
        assert_eq!(Operation::ViewAnalytics.as_str(), "view_analytics");
        // which operation each request is
        let logs = parse_request("logs nginx").unwrap();
        assert_eq!(Request::Status.operation(), Operation::ReadPrivate);
        assert_eq!(logs.operation(), Operation::ViewAnalytics);
        assert_eq!(Request::Backup.operation(), Operation::ViewAnalytics);
        assert_eq!(Request::PhpReload.operation(), Operation::Edit);
        // a cache clear changes no content and no account: whoever works on the site
        assert_eq!(Request::CacheFlush.operation(), Operation::Edit);
        assert_eq!(
            all.iter()
                .copied()
                .filter(|r| r.may(Request::CacheFlush.operation()))
                .collect::<alloc::vec::Vec<_>>(),
            [Role::Owner, Role::Admin, Role::Editor]
        );
        // a link into the dashboard signs in as the site's administrator: the owner's and
        // the platform's only, not an editor's (Andy 11-12)
        assert_eq!(Request::LoginLink.operation(), Operation::Administer);
        assert_eq!(who(Operation::Administer), [Role::Owner, Role::Admin]);
        assert!(!Role::Editor.may(Request::LoginLink.operation()));
        assert!(!Role::Viewer.may(Request::LoginLink.operation()));
        // what is live is the publisher's to change, not the editor's
        assert_eq!(Request::Restart.operation(), Operation::Publish);
        assert!(!Role::Editor.may(Request::Restart.operation()));
        assert_eq!(OwnerRequest::Sites.operation(), Operation::ReadPrivate);
        assert_eq!(OwnerRequest::Usage.operation(), Operation::ViewAnalytics);
        let restart = OwnerRequest::Site {
            site: "site2",
            request: Request::Restart,
        };
        assert_eq!(restart.operation(), Operation::Publish);
        // a key may carry several roles; a wrong entry gives none
        assert_eq!(
            parse_roles("editor,publisher"),
            Some(alloc::vec![Role::Editor, Role::Publisher])
        );
        for bad in [
            "",
            "editor,",
            "editor,editor",
            "Editor",
            "root",
            "editor publisher",
        ] {
            assert_eq!(parse_roles(bad), None, "{bad:?}");
        }
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
