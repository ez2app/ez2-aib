// SPDX-License-Identifier: Apache-2.0
//! `edge domain preflight | add | rm` as decisions (4.4.1 §6.2, §12). A customer domain is
//! put on a site only after its DNS has been looked at, and taken off in separate, named
//! pieces: the route, the record, the certificate. No I/O here: the caller resolves DNS
//! and reads the edge configuration.
//!
//! What this module holds:
//! * **Preflight** ([`preflight`]): every A record is ours, there is **no AAAA** (site CTs
//!   have no public IPv6; a wrong AAAA fails certificate issuance even when A is right),
//!   and CAA — if there is any — lets our certificate authority issue. The first visitor
//!   must not be the one who finds out.
//! * **One domain, one site** ([`add_plan`]): a name that is already on any block of the
//!   edge configuration is refused, on this site ("already there") as on another.
//! * **Only this site's block changes**, and only its line of names: whatever else is in
//!   the block (hand-added routes) stays byte for byte.
//! * **A quarantined site gets no domain** ([`crate::quarantine::guard`]).
//! * **Removing a name** ([`rm_plan`]) never removes the site's first name (the site would
//!   lose its identity; that is `site delete`), and says separately that the certificate
//!   is left in storage until it is forgotten on purpose.

use alloc::borrow::ToOwned;
use alloc::string::String;
use alloc::vec::Vec;

use crate::quarantine::{self, Reopens};
use crate::site::{caddy_marker, caddy_swap_block, Error, SiteSpec};

/// A lower-case DNS name of at least two labels: letters, digits and inner hyphens, no
/// wildcard, no trailing dot, at most 253 characters. What is typed is what is matched.
pub fn valid_domain(name: &str) -> bool {
    let labels: Vec<&str> = name.split('.').collect();
    name.len() <= 253
        && labels.len() >= 2
        && labels.iter().all(|l| {
            let b = l.as_bytes();
            (1..=63).contains(&b.len())
                && b[0] != b'-'
                && b[b.len() - 1] != b'-'
                && b.iter()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'-')
        })
        // a top-level label that is all digits is an address, not a name
        && labels
            .last()
            .is_some_and(|tld| !tld.bytes().all(|c| c.is_ascii_digit()))
}

/// What the resolver returned for one name. `None` means the lookup itself failed (not
/// "no such record"): nothing is concluded from it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Dns {
    /// A records, as dotted quads.
    pub a: Option<Vec<String>>,
    /// AAAA records.
    pub aaaa: Option<Vec<String>>,
    /// CAA records as `<tag> <value>` (`issue letsencrypt.org`), for the name or the
    /// closest parent that has any.
    pub caa: Option<Vec<String>>,
}

/// One reason a domain is not ready.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NotReady {
    /// The name itself is not acceptable.
    Name,
    /// A lookup failed; try again rather than guess.
    LookupFailed,
    /// No A record.
    NoA,
    /// An A record that is not our address (also: several, one of them not ours).
    AElsewhere,
    /// There is an AAAA record.
    HasAaaa,
    /// CAA records exist and none lets our authority issue.
    CaaForbids,
}

/// Check a domain before it is put on a site. Empty means ready. `our_ipv4` is the edge's
/// public address; `ca` is the authority's CAA name (`letsencrypt.org`).
pub fn preflight(name: &str, dns: &Dns, our_ipv4: &str, ca: &str) -> Vec<NotReady> {
    if !valid_domain(name) {
        return alloc::vec![NotReady::Name];
    }
    let mut out = Vec::new();
    let (Some(a), Some(aaaa), Some(caa)) = (&dns.a, &dns.aaaa, &dns.caa) else {
        return alloc::vec![NotReady::LookupFailed];
    };
    if a.is_empty() {
        out.push(NotReady::NoA);
    } else if a.iter().any(|ip| ip != our_ipv4) {
        out.push(NotReady::AElsewhere);
    }
    if !aaaa.is_empty() {
        out.push(NotReady::HasAaaa);
    }
    // only `issue` decides for ordinary certificates; `issuewild` and `iodef` do not
    let issue: Vec<&str> = caa
        .iter()
        .filter_map(|r| r.trim().strip_prefix("issue "))
        .map(|v| v.trim().trim_matches('"'))
        .collect();
    let allows = |v: &&str| v.split(';').next().map(str::trim) == Some(ca);
    if !issue.is_empty() && !issue.iter().any(allows) {
        out.push(NotReady::CaaForbids);
    }
    out
}

/// Every name on the site line of every block in the edge configuration, with the site
/// code of its block: the line right after a `# site:<code>` marker, up to its `{`.
fn names_by_site(caddyfile: &str) -> Vec<(&str, &str)> {
    let mut out = Vec::new();
    let mut lines = caddyfile.lines();
    while let Some(line) = lines.next() {
        let Some(code) = line.trim_end().strip_prefix("# site:") else {
            continue;
        };
        if let Some(names) = lines.next().and_then(|l| l.trim_end().strip_suffix('{')) {
            out.extend(names.split_whitespace().map(|n| (code, n)));
        }
    }
    out
}

/// This site's block with its line of names replaced, everything else untouched.
fn block_with_names(caddyfile: &str, code: &str, names: &[String]) -> Result<String, Error> {
    let block = quarantine::caddy_block_text(caddyfile, code)?;
    let mut lines: Vec<&str> = block.lines().collect();
    let names_line = alloc::format!("{} {{", names.join(" "));
    match lines.get(1) {
        Some(l) if l.trim_end().ends_with('{') && !l.starts_with(char::is_whitespace) => {}
        _ => {
            return Err(Error::Observed(alloc::format!(
                "block for {code}: the line after the marker is not a line of names"
            )))
        }
    }
    lines[1] = &names_line;
    let mut out = String::new();
    for l in lines {
        out.push_str(l);
        out.push('\n');
    }
    debug_assert!(out.starts_with(&caddy_marker(code)));
    Ok(out)
}

/// What `add` / `rm` amount to. The caller applies them in order.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Action {
    /// Replace the edge configuration with this text, validate, reload.
    WriteCaddyfile {
        /// The whole file; it differs from the one read only in this site's line of names.
        text: String,
    },
    /// Write the site record with these names (first one stays first).
    WriteRecordHosts(Vec<String>),
    /// From outside: the name answers over HTTPS with a certificate for it.
    VerifyServes(String),
    /// From outside: the name no longer reaches this site.
    VerifyGone(String),
    /// Not done by `rm`: the certificate and its key stay in the edge's storage until
    /// someone removes them on purpose. Listed so that the plan says so.
    CertificateStays(String),
}

fn refuse<T>(what: &str, name: &str) -> Result<T, Error> {
    Err(Error::Observed(alloc::format!("domain {name}: {what}")))
}

/// Plan putting `name` on the site. `ct_fw` is the CT's firewall file and `record` the
/// site's quarantine record, for the quarantine guard; `ready` is [`preflight`]'s answer —
/// a name that is not ready is refused here too, so the check cannot be skipped.
pub fn add_plan(
    spec: &SiteSpec,
    name: &str,
    ready: &[NotReady],
    caddyfile: &str,
    ct_fw: &str,
    record: Option<&quarantine::Record>,
) -> Result<Vec<Action>, Error> {
    if !valid_domain(name) {
        return refuse("not a domain name", name);
    }
    if !ready.is_empty() {
        return refuse("preflight did not pass", name);
    }
    quarantine::guard(&spec.code, ct_fw, record, Reopens::DomainAdd)?;
    if let Some((code, _)) = names_by_site(caddyfile).iter().find(|(_, n)| *n == name) {
        return if *code == spec.code {
            refuse("already on this site", name)
        } else {
            refuse("already on another site", name)
        };
    }
    let mut names = spec.hosts.clone();
    names.push(name.to_owned());
    let block = block_with_names(caddyfile, &spec.code, &names)?;
    let text = caddy_swap_block(caddyfile, &spec.code, &block)?;
    Ok(alloc::vec![
        Action::WriteCaddyfile { text },
        Action::WriteRecordHosts(names),
        Action::VerifyServes(name.to_owned()),
    ])
}

/// Plan taking `name` off the site. The site's first name cannot be removed.
pub fn rm_plan(spec: &SiteSpec, name: &str, caddyfile: &str) -> Result<Vec<Action>, Error> {
    if spec.primary() == name {
        return refuse("is the site's first name and cannot be removed", name);
    }
    if !spec.hosts.iter().any(|h| h == name) {
        return refuse("is not on this site", name);
    }
    let names: Vec<String> = spec.hosts.iter().filter(|h| *h != name).cloned().collect();
    let block = block_with_names(caddyfile, &spec.code, &names)?;
    let text = caddy_swap_block(caddyfile, &spec.code, &block)?;
    Ok(alloc::vec![
        Action::WriteCaddyfile { text },
        Action::WriteRecordHosts(names),
        Action::VerifyGone(name.to_owned()),
        Action::CertificateStays(name.to_owned()),
    ])
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use crate::site::{caddy_block_upstream, desired_firewall, Limits};

    const OURS: &str = "203.0.113.10";
    const CA: &str = "letsencrypt.org";

    fn spec() -> SiteSpec {
        SiteSpec::new(
            "site2",
            112,
            12,
            &["site2.example.test", "www.site2.example.test"],
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

    /// This site's block with a hand-added route, and another site.
    fn caddyfile() -> String {
        let mine = caddy_block_upstream(&spec(), "10.77.0.12").replace(
            "\tencode zstd gzip\n",
            "\tencode zstd gzip\n\thandle /e2/* {\n\t\trespond 204\n\t}\n",
        );
        alloc::format!(
            "{{\n\temail ops@example.test\n}}\n\n{mine}\n# site:other\nother.example.test shop.example.test {{\n\treverse_proxy 10.77.0.20:80\n}}\n"
        )
    }

    fn dns(a: &[&str], aaaa: &[&str], caa: &[&str]) -> Dns {
        let v = |x: &[&str]| Some(x.iter().map(|s| (*s).to_owned()).collect());
        Dns {
            a: v(a),
            aaaa: v(aaaa),
            caa: v(caa),
        }
    }

    #[test]
    fn domain_names() {
        for ok in [
            "example.test",
            "a.b.example.test",
            "xn--fsq.example.test",
            "a-b.c0.test",
        ] {
            assert!(valid_domain(ok), "{ok}");
        }
        let long = alloc::format!("{}.test", "a".repeat(64));
        for bad in [
            "",
            "localhost",
            "*.example.test",
            "Example.test",
            "example.test.",
            "-a.example.test",
            "a-.example.test",
            "a..example.test",
            "a b.example.test",
            "example.test/x",
            "203.0.113.10",
            long.as_str(),
        ] {
            assert!(!valid_domain(bad), "{bad:?}");
        }
    }

    #[test]
    fn preflight_reads_a_aaaa_and_caa() {
        let pf = |d: &Dns| preflight("shop.example.test", d, OURS, CA);
        assert_eq!(pf(&dns(&[OURS], &[], &[])), []);
        assert_eq!(pf(&dns(&[], &[], &[])), [NotReady::NoA]);
        assert_eq!(
            pf(&dns(&["198.51.100.7"], &[], &[])),
            [NotReady::AElsewhere]
        );
        // ours and somebody else's: half the visitors go elsewhere
        assert_eq!(
            pf(&dns(&[OURS, "198.51.100.7"], &[], &[])),
            [NotReady::AElsewhere]
        );
        assert_eq!(
            pf(&dns(&[OURS], &["2001:db8::1"], &[])),
            [NotReady::HasAaaa]
        );
        assert_eq!(pf(&dns(&[OURS], &[], &["issue \"letsencrypt.org\""])), []);
        assert_eq!(
            pf(&dns(
                &[OURS],
                &[],
                &["issue letsencrypt.org; validationmethods=http-01"]
            )),
            []
        );
        assert_eq!(
            pf(&dns(&[OURS], &[], &["issue \"digicert.com\""])),
            [NotReady::CaaForbids]
        );
        assert_eq!(
            pf(&dns(
                &[OURS],
                &[],
                &["issue \"digicert.com\"", "issue \"letsencrypt.org\""]
            )),
            []
        );
        // issuewild and iodef say nothing about an ordinary certificate
        assert_eq!(
            pf(&dns(
                &[OURS],
                &[],
                &["issuewild \";\"", "iodef \"mailto:x@example.test\""]
            )),
            []
        );
        // a name that is not allowed to be issued for at all
        assert_eq!(
            pf(&dns(&[OURS], &[], &["issue \";\""])),
            [NotReady::CaaForbids]
        );
        assert_eq!(
            pf(&dns(
                &["198.51.100.7"],
                &["2001:db8::1"],
                &["issue \"x.test\""]
            )),
            [
                NotReady::AElsewhere,
                NotReady::HasAaaa,
                NotReady::CaaForbids
            ]
        );
        // a failed lookup is not "no record"
        let mut failed = dns(&[OURS], &[], &[]);
        failed.aaaa = None;
        assert_eq!(pf(&failed), [NotReady::LookupFailed]);
        assert_eq!(
            preflight("*.example.test", &dns(&[OURS], &[], &[]), OURS, CA),
            [NotReady::Name]
        );
    }

    #[test]
    fn add_changes_one_line_of_one_block() {
        let file = caddyfile();
        let plan = add_plan(&spec(), "shop2.example.test", &[], &file, &fw(), None).unwrap();
        let Action::WriteCaddyfile { text } = &plan[0] else {
            panic!("route first");
        };
        // exactly one line differs, and it is the names line of this site
        let changed: Vec<(&str, &str)> = file
            .lines()
            .zip(text.lines())
            .filter(|(a, b)| a != b)
            .collect();
        assert_eq!(
            changed,
            [(
                "site2.example.test www.site2.example.test {",
                "site2.example.test www.site2.example.test shop2.example.test {"
            )]
        );
        assert_eq!(file.lines().count(), text.lines().count());
        assert!(text.contains("handle /e2/* {"));
        assert_eq!(
            plan[1],
            Action::WriteRecordHosts(alloc::vec![
                "site2.example.test".to_owned(),
                "www.site2.example.test".to_owned(),
                "shop2.example.test".to_owned()
            ])
        );
        assert_eq!(
            plan[2],
            Action::VerifyServes("shop2.example.test".to_owned())
        );
    }

    #[test]
    fn add_refusals() {
        let file = caddyfile();
        let add = |name: &str, ready: &[NotReady], ct_fw: &str| {
            add_plan(&spec(), name, ready, &file, ct_fw, None)
        };
        assert!(add("new.example.test", &[], &fw()).is_ok());
        // already on this site, already on another site
        assert!(add("www.site2.example.test", &[], &fw()).is_err());
        assert!(add("shop.example.test", &[], &fw()).is_err());
        // not ready, not a name
        assert!(add("new.example.test", &[NotReady::HasAaaa], &fw()).is_err());
        assert!(add("*.example.test", &[], &fw()).is_err());
        assert!(add("new.example.test {\n\trespond 200\n}", &[], &fw()).is_err());
        // a quarantined site gets no domain
        assert!(add("new.example.test", &[], &quarantine::firewall()).is_err());
    }

    #[test]
    fn rm_takes_one_name_off_and_says_the_certificate_stays() {
        let file = caddyfile();
        let plan = rm_plan(&spec(), "www.site2.example.test", &file).unwrap();
        let Action::WriteCaddyfile { text } = &plan[0] else {
            panic!("route first");
        };
        assert!(text.contains("\nsite2.example.test {\n"));
        assert!(text.contains("other.example.test shop.example.test {"));
        assert!(text.contains("handle /e2/* {"));
        assert_eq!(
            plan[1],
            Action::WriteRecordHosts(alloc::vec!["site2.example.test".to_owned()])
        );
        assert_eq!(
            plan[2],
            Action::VerifyGone("www.site2.example.test".to_owned())
        );
        assert_eq!(
            plan[3],
            Action::CertificateStays("www.site2.example.test".to_owned())
        );
        // the first name stays; a name of another site is not ours to remove
        assert!(rm_plan(&spec(), "site2.example.test", &file).is_err());
        assert!(rm_plan(&spec(), "shop.example.test", &file).is_err());
    }

    #[test]
    fn a_block_whose_second_line_is_not_names_is_refused() {
        let file = caddyfile().replace(
            "# site:site2\nsite2.example.test www.site2.example.test {",
            "# site:site2\n\timport snippet\nsite2.example.test www.site2.example.test {",
        );
        assert!(add_plan(&spec(), "new.example.test", &[], &file, &fw(), None).is_err());
    }
}
