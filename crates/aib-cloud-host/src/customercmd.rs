// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host customer --site <code>` — the one command a customer's key reaches
//! (4.4.1 §1; request grammar: `aib_cloud::customer`). It is meant to be the forced
//! command of that key's entry, with the site code written there by us:
//!
//! ```text
//! command="/usr/local/sbin/aib-cloud-host customer --site site2",restrict ssh-ed25519 AAAA… customer
//! ```
//!
//! What the customer typed arrives in `SSH_ORIGINAL_COMMAND`. It is parsed as data, never
//! run; the site is the one on our side of the entry, never one the customer names.
//!
//! Available now: `status` (the site's own checks, no host checks; only the checks and
//! codes `aib_cloud::customer::status_view` names are shown, the rest is one
//! `provider_check` row) and `logs` (bounded, masked). `php-reload`, `restart` and `backup` parse but answer "not available yet":
//! the first two change something and wait for the operation journal and the host lock.
//!
//! Exit status: as `site verify` for `status`; 0 for `logs`; 2 a refused request;
//! 4 not available yet; 3 could not be done.

use std::process::Command;
use std::time::Duration;

use aib_cloud::customer::{parse_request, Request};
use aib_cloud::svc;
use aib_cloud::verify;

use crate::probe::run_capture_bounded;
use crate::quarantinecmd::load_spec;

const RECORDS: &str = "/var/lib/ez2-cloud/sites";

/// Answer one request for one site. `line` is what the customer sent.
pub fn answer(site: &str, records: &str, line: &str) -> u8 {
    let request = match parse_request(line) {
        Ok(r) => r,
        Err(why) => {
            // a fixed word: what was sent is not echoed
            eprintln!("refused: {}", why.as_str());
            eprintln!("requests: status | logs <nginx|php|db> [lines N] [minutes N] | php-reload | restart | backup");
            return 2;
        }
    };
    match request {
        Request::Status => {
            let path = std::path::PathBuf::from(format!("{records}/{site}.json"));
            let (name, _, results) = crate::siteverify::site_checks(&path);
            // the customer's view: a whitelist of checks and codes (aib_cloud::customer)
            println!(
                "{}",
                aib_cloud::customer::status_view(Some(&name), &results)
            );
            verify::exit_code(&results)
        }
        Request::Logs {
            source,
            lines,
            minutes,
        } => {
            let argv = match load_spec(site, records).and_then(|spec| {
                svc::logs_argv(&spec, source, lines, minutes).map_err(|e| e.to_string())
            }) {
                Ok(a) => a,
                Err(_) => {
                    eprintln!("could not read the site record");
                    return 3;
                }
            };
            let mut cmd = Command::new(&argv[0]);
            cmd.args(&argv[1..]).env("LC_ALL", "C");
            // the host counts bytes and lines itself: what prints the log runs in the CT
            let wanted = usize::try_from(lines).unwrap_or(usize::MAX);
            match run_capture_bounded(&mut cmd, Duration::from_secs(30), svc::log_bytes_max(lines))
            {
                Ok((out, more)) => {
                    for l in out.lines().take(wanted) {
                        println!("{}", svc::mask_line(svc::clip_line(l)));
                    }
                    if more || out.lines().count() > wanted {
                        eprintln!("output cut at the limit");
                    }
                    0
                }
                Err(_) => {
                    eprintln!("could not read the log");
                    3
                }
            }
        }
        Request::PhpReload | Request::Restart | Request::Backup => {
            eprintln!("not available yet");
            4
        }
    }
}

pub fn cmd(args: &[String]) -> Result<u8, String> {
    let mut site: Option<&str> = None;
    let mut records = RECORDS.to_owned();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--site" => site = Some(it.next().ok_or("--site needs a site code")?.as_str()),
            "--records" => records = it.next().ok_or("--records needs a dir")?.clone(),
            // anything else on our side of the entry is a mistake in the entry itself
            other => return Err(format!("unknown argument {other}")),
        }
    }
    let site =
        site.ok_or("customer needs --site <code> (set in the key's entry, not by the customer)")?;
    if !aib_cloud::site::valid_code(site) {
        return Err(format!("site code {site:?} is not valid"));
    }
    let line = std::env::var("SSH_ORIGINAL_COMMAND").unwrap_or_default();
    Ok(answer(site, &records, &line))
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    #[test]
    fn refused_and_not_yet() {
        let dir = "/nonexistent/aib-customer";
        // nothing sent (an interactive login), shell syntax, another site, an unknown word
        for line in [
            "",
            "bash",
            "status; reboot",
            "status site3",
            "logs nginx | nc x 1",
            "stop",
        ] {
            assert_eq!(answer("site2", dir, line), 2, "{line:?}");
        }
        for line in ["php-reload", "restart", "backup"] {
            assert_eq!(answer("site2", dir, line), 4, "{line:?}");
        }
        // a request that is fine for a site whose record cannot be read: not done, not green
        assert_eq!(answer("site2", dir, "logs nginx"), 3);
        assert_eq!(answer("site2", dir, "status"), 1);
    }

    /// 33e28, #631 seg. 5 M1: what prints the log runs inside the customer's container.
    #[test]
    fn output_that_does_not_end_is_cut_on_the_host() {
        let sh = |script: &str| {
            let mut cmd = Command::new("sh");
            cmd.args(["-c", script]);
            run_capture_bounded(&mut cmd, Duration::from_secs(20), 10_000)
        };
        // three megabytes where ten thousand bytes were asked for
        let (out, more) = sh("head -c 3000000 /dev/zero | tr '\\0' a").unwrap();
        assert_eq!(out.len(), 10_000);
        assert!(more);
        // a program that never stops is stopped, long before the time is up
        let t0 = std::time::Instant::now();
        let (out, more) = sh("while :; do echo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; done").unwrap();
        assert_eq!(out.len(), 10_000);
        assert!(more);
        assert!(t0.elapsed() < Duration::from_secs(10));
        // output under the cap arrives whole; a program that fails is an error
        assert_eq!(
            sh("printf 'a\\nb\\n'").unwrap(),
            ("a\nb\n".to_owned(), false)
        );
        assert!(sh("echo x; exit 3").is_err());
    }

    #[test]
    fn the_site_comes_from_our_side_only() {
        let args = |a: &[&str]| a.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>();
        assert!(cmd(&args(&[])).is_err());
        assert!(cmd(&args(&["--site", "../etc"])).is_err());
        assert!(cmd(&args(&["--site", "site2", "status"])).is_err());
        assert!(cmd(&args(&["status"])).is_err());
    }
}
