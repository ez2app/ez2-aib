// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud <site <request…> | sites | usage> --host H --user U --known-hosts F [--port N] [--key F] [--raw] [--print]`
//! — a customer asks the host for something about **their own sites** (4.4.1 §1, 4.5 §4).
//!
//! ```text
//! a key for one site:    aib-cloud site status
//!                        aib-cloud site logs <nginx|php|db> [lines N] [minutes N]
//!                        aib-cloud site login-link
//! a key for a customer:  aib-cloud sites
//!                        aib-cloud usage
//!                        aib-cloud site <code> status
//!                        aib-cloud site <code> logs <nginx|php|db> [lines N] [minutes N]
//!                        aib-cloud site <code> login-link
//!                        aib-cloud site create <domain> [blank]
//! ```
//!
//! `site create` answers the new site's code and `opening` at once; `site <code> status`
//! then answers how far the opening got (`state`, `step_done`, `stopped_at`) until the
//! site is live. Both are the host's fixed words, shown as they are. For a while after the
//! site opened, `status` answers `open` with the site's domain and administrators: that is
//! shown as the opened screen (`aib_cloud::wording::opened`), which names the next command.
//!
//! `login-link` answers a one-time link that logs the site's first administrator straight
//! in (Andy 11-12 04:3x). It is printed alone, one line on standard output, and kept
//! nowhere by this program: no file, no log (ebb36 L1). A refusal or a failure is the
//! host's one word on standard error (`role_forbidden`, `tenant_mismatch`,
//! `provider_check`, `unavailable`), and the host not reached is exit 3 as for every request.
//!
//! With a key for one site, which site is not said here: the host knows it from the key.
//! With a key for a customer the site is named, and the host decides from its own records
//! whether it is that customer's. Where to connect comes
//! from the flags, or from `EZ2_CLOUD_HOST`, `EZ2_CLOUD_USER`, `EZ2_CLOUD_KNOWN_HOSTS`,
//! `EZ2_CLOUD_PORT` and `EZ2_CLOUD_KEY` when a flag is not given.
//!
//! The request is checked here with the host's own parser, then sent with the system's
//! `ssh` and the arguments `aib_cloud::client` builds (no ssh configuration file is read,
//! the host key must match the file named, nothing is forwarded). `--print` shows those
//! arguments and sends nothing.
//!
//! The host answers in codes. What is shown is the sentence `aib_cloud::wording` has for
//! each code (zh-TW, final wording); a code it does not know is not shown. `sites`,
//! `usage` and a customer key's refusals have no final wording yet: the host's document,
//! or the refusal's code alone, is shown as it is. `--raw` shows the host's answer as it
//! came, for scripts.
//!
//! Exit status: the host's own for an answered request (see `aib-cloud-host customer`);
//! 2 the request or a flag was refused here and nothing was sent; 3 the host could not
//! be reached or `ssh` could not be run.

use std::ffi::OsString;
use std::path::PathBuf;
use std::process::{Command, ExitCode, Stdio};

use aib_cloud::client::{parse_line, site_line, ssh_args, Line, NotSent, Target};
use aib_cloud::customer::{OwnerRequest, Request};
use aib_cloud::wording;

const USAGE: &str = "aib-cloud <site [<code>] <status | logs <nginx|php|db> [lines N] [minutes N] | login-link> | site create <domain> [blank] | sites | usage> --host H --user U --known-hosts F [--port N] [--key F] [--raw] [--print]";

/// What the command line and the environment say, before any of it is checked.
#[derive(Debug, Default, PartialEq, Eq)]
struct Asked {
    request: String,
    host: Option<String>,
    user: Option<String>,
    known_hosts: Option<String>,
    port: Option<String>,
    key: Option<String>,
    raw: bool,
    print: bool,
}

/// Read the words after the command. Flags may come anywhere; every other word is part of the
/// request. A flag given twice, a flag without its value, or an unknown flag is an error.
fn read_args(args: &[String]) -> Result<Asked, String> {
    let mut asked = Asked::default();
    let mut words: Vec<&str> = Vec::new();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let slot = match a.as_str() {
            "--host" => &mut asked.host,
            "--user" => &mut asked.user,
            "--known-hosts" => &mut asked.known_hosts,
            "--port" => &mut asked.port,
            "--key" => &mut asked.key,
            "--print" => {
                asked.print = true;
                continue;
            }
            "--raw" => {
                asked.raw = true;
                continue;
            }
            flag if flag.starts_with("--") => return Err(format!("unknown flag {flag}")),
            word => {
                words.push(word);
                continue;
            }
        };
        let value = it.next().ok_or_else(|| format!("{a} needs a value"))?;
        if slot.replace(value.clone()).is_some() {
            return Err(format!("{a} given twice"));
        }
    }
    asked.request = words.join(" ");
    Ok(asked)
}

/// Fill what the flags left out from the environment (`get` reads one variable).
fn with_env(mut asked: Asked, get: impl Fn(&str) -> Option<String>) -> Asked {
    let fill = |slot: &mut Option<String>, name: &str| {
        if slot.is_none() {
            *slot = get(name).filter(|v| !v.is_empty());
        }
    };
    fill(&mut asked.host, "EZ2_CLOUD_HOST");
    fill(&mut asked.user, "EZ2_CLOUD_USER");
    fill(&mut asked.known_hosts, "EZ2_CLOUD_KNOWN_HOSTS");
    fill(&mut asked.port, "EZ2_CLOUD_PORT");
    fill(&mut asked.key, "EZ2_CLOUD_KEY");
    asked
}

/// Why nothing is sent.
#[derive(Debug, PartialEq, Eq)]
enum Stop {
    /// A flag is missing or is not what it should be: said in plain words.
    Flag(String),
    /// A value or the request was refused by `aib_cloud::client`: a fixed code.
    NotSent(NotSent),
}

impl Stop {
    /// The line for `--raw` and for flags: a code, never what was typed.
    fn raw(&self) -> String {
        match self {
            Stop::Flag(why) => why.clone(),
            Stop::NotSent(why) => format!("not sent: {}", why.as_str()),
        }
    }
}

/// The `ssh` arguments for what was asked, or why nothing is sent.
fn plan(asked: &Asked) -> Result<Vec<String>, Stop> {
    let need = |v: &Option<String>, what: &str| {
        v.clone()
            .ok_or_else(|| Stop::Flag(format!("{what} is not set (flag or environment)")))
    };
    let host = need(&asked.host, "--host")?;
    let user = need(&asked.user, "--user")?;
    let known_hosts = need(&asked.known_hosts, "--known-hosts")?;
    let port = match &asked.port {
        None => 22,
        Some(p) => p
            .parse::<u16>()
            .map_err(|_| Stop::Flag("--port is not a port number".to_owned()))?,
    };
    let target = Target {
        host: &host,
        port,
        user: &user,
        key: asked.key.as_deref(),
        known_hosts: &known_hosts,
    };
    ssh_args(&target, &asked.request).map_err(Stop::NotSent)
}

fn run(args: &[String]) -> Result<u8, String> {
    let asked = with_env(command(args)?, |name| std::env::var(name).ok());
    let ssh = match plan(&asked) {
        Ok(a) => a,
        Err(stop) => {
            match (&stop, asked.raw) {
                (Stop::NotSent(why), false) => eprintln!("{}", wording::not_sent(*why)),
                _ => eprintln!("aib-cloud: {}", stop.raw()),
            }
            return Ok(2);
        }
    };
    if asked.print {
        for a in &ssh {
            println!("{a}");
        }
        return Ok(0);
    }
    let done = match Command::new(ssh_program())
        .args(&ssh)
        .stdin(Stdio::null())
        .output()
    {
        Ok(d) => d,
        Err(e) => {
            if asked.raw {
                eprintln!("aib-cloud: ssh could not be run: {e}");
            } else {
                eprintln!("{}", wording::NO_SSH);
            }
            return Ok(3);
        }
    };
    let out = String::from_utf8_lossy(&done.stdout);
    let err = String::from_utf8_lossy(&done.stderr);
    let code = match done.status.code() {
        // ssh's own failure (no route, key refused, host key mismatch) is 255
        Some(255) | None => {
            if asked.raw {
                eprint!("{err}");
                eprintln!("aib-cloud: the host was not reached");
            } else {
                eprintln!("{}", wording::NOT_REACHED);
            }
            return Ok(3);
        }
        Some(code) => u8::try_from(code).unwrap_or(3),
    };
    if asked.raw {
        print!("{out}");
        eprint!("{err}");
        return Ok(code);
    }
    // plan() checked the request; one that does not parse was never sent
    match parse_line(&asked.request) {
        Ok(Line::Site(request)) => shout(request, code, &wording::reply(request, code, &out, &err)),
        Ok(Line::Owner(owner @ OwnerRequest::Site { request, .. })) if !answered(request, code) => {
            eprint!("{}", owner_answer(owner, code, &out, &err));
        }
        Ok(Line::Owner(owner)) => print!("{}", owner_answer(owner, code, &out, &err)),
        Err(_) => {}
    }
    Ok(code)
}

/// Whether what is shown for `request` goes to standard output: everything, but a
/// `login-link` that did not give a link (its one word goes to standard error, so standard
/// output only ever holds the link).
fn answered(request: Request, code: u8) -> bool {
    request != Request::LoginLink || code == 0
}

/// A site key's answer, on standard output or, for a `login-link` that gave no link, on
/// standard error ([`answered`]).
fn shout(request: Request, code: u8, text: &str) {
    if answered(request, code) {
        println!("{text}");
    } else {
        eprintln!("{text}");
    }
}

/// The system's own `ssh`: on Windows the one Windows ships
/// (`%SystemRoot%\System32\OpenSSH\ssh.exe`), never another `ssh` found first on the
/// path; elsewhere `ssh` from the path.
fn ssh_program() -> PathBuf {
    ssh_program_for(cfg!(windows), std::env::var_os("SystemRoot"))
}

fn ssh_program_for(windows: bool, system_root: Option<OsString>) -> PathBuf {
    if windows {
        system_root
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("C:\\Windows"))
            .join("System32")
            .join("OpenSSH")
            .join("ssh.exe")
    } else {
        PathBuf::from("ssh")
    }
}

/// What is shown for a customer key's answer.
fn owner_answer(owner: OwnerRequest<'_>, code: u8, out: &str, err: &str) -> String {
    match (refusal(err), owner) {
        // no final wording for these yet: the code alone, after the document when there
        // is one (`already_opening` gives the customer's own opening back)
        (Some(word), _) if code == 2 => format!("{}{word}\n", shown(out)),
        // a site still opening: how far it got, the host's fixed words as they are
        (
            _,
            OwnerRequest::Site {
                request: Request::Status,
                ..
            },
        ) if aib_cloud::customer::is_opening(out) => match aib_cloud::customer::opened_of(out) {
            // just opened: the opened screen, for the first administrator and one other
            Some(o) => format!(
                "{}\n",
                wording::opened(
                    &o.site,
                    &o.domain,
                    &o.admins[0],
                    o.admins.get(1).map(String::as_str)
                )
            ),
            None => shown(out),
        },
        (_, OwnerRequest::Site { request, .. }) => {
            format!("{}\n", wording::reply(request, code, out, err))
        }
        (_, OwnerRequest::Sites | OwnerRequest::Usage) => shown(out),
        // the host's document until the sentences are final; without one, the host's
        // own word (`unavailable`)
        (_, OwnerRequest::Create { .. } | OwnerRequest::Domain { .. }) => {
            if out.trim().is_empty() {
                plain_word(err)
                    .map(|w| format!("{w}\n"))
                    .unwrap_or_default()
            } else {
                shown(out)
            }
        }
    }
}

/// The first line of `err` that is one plain code word.
fn plain_word(err: &str) -> Option<&str> {
    err.lines()
        .map(str::trim)
        .find(|w| !w.is_empty() && w.bytes().all(|c| c.is_ascii_lowercase() || c == b'_'))
}

/// The command and its words as one request line: `site …` as [`site_line`] reads it,
/// `sites` and `usage` as they are (a word after them is sent along to be refused).
fn command(args: &[String]) -> Result<Asked, String> {
    let (first, rest) = args.split_first().ok_or_else(|| USAGE.to_owned())?;
    let mut asked = read_args(rest)?;
    asked.request = match first.as_str() {
        "site" => site_line(&asked.request),
        "sites" | "usage" => format!("{first} {}", asked.request).trim_end().to_owned(),
        _ => return Err(USAGE.to_owned()),
    };
    Ok(asked)
}

/// The host's document as it is printed: line by line through [`aib_cloud::svc::plain`],
/// so nothing in it acts on the terminal or hides from the person reading (ebb36, #648
/// L2). The host's JSON writer already escapes the C0 controls; it passes DEL, C1,
/// direction overrides and invisible characters as they are.
fn shown(out: &str) -> String {
    out.lines()
        .map(|l| aib_cloud::svc::plain(l) + "\n")
        .collect()
}

/// The code of the host's `refused: <code>` line, when it is a plain code word.
fn refusal(err: &str) -> Option<&str> {
    err.lines()
        .filter_map(|l| l.trim().strip_prefix("refused: "))
        .find(|w| !w.is_empty() && w.bytes().all(|c| c.is_ascii_lowercase() || c == b'_'))
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match run(&args) {
        Ok(code) => ExitCode::from(code),
        Err(e) => {
            eprintln!("aib-cloud: {e}");
            ExitCode::from(2)
        }
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    fn words(a: &[&str]) -> Vec<String> {
        a.iter().map(|s| (*s).to_owned()).collect()
    }

    fn no_env(_: &str) -> Option<String> {
        None
    }

    #[test]
    fn flags_anywhere_and_the_rest_is_the_request() {
        let asked = read_args(&words(&[
            "logs",
            "--host",
            "host1.example.net",
            "nginx",
            "lines",
            "50",
            "--user",
            "site2",
            "--known-hosts",
            "/k",
            "--print",
        ]))
        .unwrap();
        assert_eq!(asked.request, "logs nginx lines 50");
        assert_eq!(asked.host.as_deref(), Some("host1.example.net"));
        assert!(asked.print);
        let ssh = plan(&asked).unwrap();
        assert_eq!(
            ssh[ssh.len() - 2..],
            ["site2@host1.example.net", "logs nginx lines 50"]
        );
        assert!(ssh.windows(2).any(|w| w[0] == "-p" && w[1] == "22"));
    }

    #[test]
    fn a_flag_is_not_guessed() {
        assert!(read_args(&words(&["status", "--host"])).is_err());
        assert!(read_args(&words(&["status", "--host", "a", "--host", "b"])).is_err());
        assert!(read_args(&words(&["status", "--site", "site3"])).is_err());
        assert!(read_args(&words(&["status", "--apply"])).is_err());
    }

    #[test]
    fn the_environment_fills_only_what_the_flags_left_out() {
        let env = |name: &str| match name {
            "EZ2_CLOUD_HOST" => Some("env.example.net".to_owned()),
            "EZ2_CLOUD_USER" => Some("envuser".to_owned()),
            "EZ2_CLOUD_KNOWN_HOSTS" => Some("/env/known_hosts".to_owned()),
            "EZ2_CLOUD_PORT" => Some("2222".to_owned()),
            "EZ2_CLOUD_KEY" => Some(String::new()),
            _ => None,
        };
        let asked = with_env(
            read_args(&words(&["status", "--host", "flag.example.net"])).unwrap(),
            env,
        );
        assert_eq!(asked.host.as_deref(), Some("flag.example.net"));
        assert_eq!(asked.user.as_deref(), Some("envuser"));
        // an empty variable is no value
        assert_eq!(asked.key, None);
        let ssh = plan(&asked).unwrap();
        assert!(ssh.windows(2).any(|w| w[0] == "-p" && w[1] == "2222"));
        assert_eq!(ssh[ssh.len() - 2], "envuser@flag.example.net");
    }

    #[test]
    fn nothing_is_sent_without_a_target_or_with_a_bad_request() {
        let bare = with_env(read_args(&words(&["status"])).unwrap(), no_env);
        assert!(plan(&bare).unwrap_err().raw().contains("--host"));
        let full = |request: &[&str]| {
            let mut a = words(request);
            a.extend(words(&[
                "--host",
                "host1.example.net",
                "--user",
                "site2",
                "--known-hosts",
                "/k",
            ]));
            plan(&with_env(read_args(&a).unwrap(), no_env))
        };
        assert!(full(&["status"]).is_ok());
        assert_eq!(full(&[]).unwrap_err().raw(), "not sent: no_request");
        assert_eq!(
            full(&["stop"]).unwrap_err().raw(),
            "not sent: unknown_request"
        );
        // not offered yet: the same answer as any unknown word
        assert_eq!(
            full(&["restart"]).unwrap_err().raw(),
            "not sent: unknown_request"
        );
        assert_eq!(
            full(&["status", "site3"]).unwrap_err().raw(),
            "not sent: bad_arguments"
        );
        // what was typed is not echoed
        assert_eq!(
            full(&["status;id"]).unwrap_err().raw(),
            "not sent: malformed_request"
        );
        let mut a = words(&["status", "--port", "70000"]);
        a.extend(words(&[
            "--host",
            "h",
            "--user",
            "u",
            "--known-hosts",
            "/k",
        ]));
        assert!(plan(&read_args(&a).unwrap())
            .unwrap_err()
            .raw()
            .contains("--port"));
        // and the whole command needs `site`, `sites` or `usage` first
        assert!(run(&words(&["status"])).is_err());
        assert!(run(&[]).is_err());
    }

    #[test]
    fn a_customer_keys_commands_become_its_lines() {
        let line = |a: &[&str]| command(&words(a)).unwrap().request;
        assert_eq!(line(&["site", "status"]), "status");
        assert_eq!(
            line(&["site", "site3", "status", "--raw"]),
            "site site3 status"
        );
        assert_eq!(
            line(&["site", "site3", "logs", "php"]),
            "site site3 logs php"
        );
        assert_eq!(line(&["sites"]), "sites");
        assert_eq!(line(&["usage", "--print"]), "usage");
        // a word too many is sent on to be refused, not dropped
        assert_eq!(line(&["sites", "all"]), "sites all");
        assert!(command(&words(&["list"])).is_err());
        let full = |a: &[&str]| {
            let mut a = words(a);
            a.extend(words(&[
                "--host",
                "host1.example.net",
                "--user",
                "t1",
                "--known-hosts",
                "/k",
            ]));
            plan(&with_env(command(&a).unwrap(), no_env))
        };
        assert_eq!(full(&["sites"]).unwrap().last().unwrap(), "sites");
        assert_eq!(
            full(&["site", "site3", "status"]).unwrap().last().unwrap(),
            "site site3 status"
        );
        assert_eq!(
            full(&["sites", "all"]).unwrap_err().raw(),
            "not sent: bad_arguments"
        );
        assert_eq!(
            full(&["site", "site3", "restart"]).unwrap_err().raw(),
            "not sent: unknown_request"
        );
    }

    #[test]
    fn the_hosts_document_is_printed_as_plain_text() {
        assert_eq!(shown("{\"sites\":[]}\n"), "{\"sites\":[]}\n");
        assert_eq!(
            shown("{\"hosts\":\"a\u{9b}31m\u{202e}b\u{e0041}\"}\n"),
            "{\"hosts\":\"a\\x9b31m\\u{202e}b\\u{e0041}\"}\n"
        );
        assert_eq!(shown(""), "");
    }

    /// Windows uses the ssh it ships, under its own system folder, wherever that is.
    #[test]
    fn the_system_s_own_ssh_is_run() {
        let tail = std::path::Path::new("System32")
            .join("OpenSSH")
            .join("ssh.exe");
        let p = ssh_program_for(true, Some(OsString::from("D:\\Win Dows")));
        assert!(p.starts_with("D:\\Win Dows") && p.ends_with(&tail), "{p:?}");
        let p = ssh_program_for(true, None);
        assert!(p.starts_with("C:\\Windows") && p.ends_with(&tail), "{p:?}");
        assert_eq!(ssh_program_for(false, None), PathBuf::from("ssh"));
    }

    /// E1 N1 (Andy 11-12 04:3x): `login-link` is sent under either kind of key, and its
    /// standard output is the link alone; any other answer is one word on standard error.
    #[test]
    fn a_login_link_is_the_only_thing_on_standard_output() {
        let full = |typed: &[&str]| {
            let mut a = words(typed);
            a.extend(words(&[
                "--host",
                "host1.example.net",
                "--user",
                "site2",
                "--known-hosts",
                "/k",
            ]));
            plan(&with_env(command(&a).unwrap(), no_env)).unwrap()
        };
        assert_eq!(full(&["site", "login-link"]).last().unwrap(), "login-link");
        assert_eq!(
            full(&["site", "s-abc123", "login-link"]).last().unwrap(),
            "site s-abc123 login-link"
        );
        let url = "https://shop.example.test/?ez2_otl=AbC123\n";
        let ll = OwnerRequest::Site {
            site: "s-abc123",
            request: Request::LoginLink,
        };
        assert!(answered(Request::LoginLink, 0));
        assert_eq!(owner_answer(ll, 0, url, ""), url);
        // a refusal or a failure is never on standard output
        assert!(!answered(Request::LoginLink, 2) && !answered(Request::LoginLink, 3));
        assert!(answered(Request::Status, 1));
        assert_eq!(
            owner_answer(ll, 2, "", "refused: role_forbidden\n"),
            "role_forbidden\n"
        );
        assert_eq!(owner_answer(ll, 3, "", "unavailable\n"), "unavailable\n");
        assert_eq!(
            owner_answer(ll, 3, "", "provider_check\n"),
            "provider_check\n"
        );
    }

    /// E1: opening a site and following it are shown in the host's fixed words.
    #[test]
    fn an_opening_is_shown_in_the_host_s_words() {
        let create = OwnerRequest::Create {
            name: "www.example.com",
            blank: false,
        };
        let opening = "{\"site\":\"s-abc123\",\"state\":\"opening\"}\n";
        assert_eq!(owner_answer(create, 0, opening, ""), opening);
        assert_eq!(
            owner_answer(create, 3, "", "unavailable\n"),
            "unavailable\n"
        );
        assert_eq!(
            owner_answer(create, 2, opening, "refused: already_opening\n"),
            format!("{opening}already_opening\n")
        );
        assert_eq!(
            owner_answer(create, 2, "", "refused: domain_taken\n"),
            "domain_taken\n"
        );
        let status = OwnerRequest::Site {
            site: "s-abc123",
            request: Request::Status,
        };
        let progress =
            "{\"site\":\"s-abc123\",\"state\":\"stopped\",\"step_done\":\"start\",\"stopped_at\":\"install\"}\n";
        assert_eq!(owner_answer(status, 0, progress, ""), progress);
        // just opened: the opened screen, which names the next command and no link
        let opened = "{\"site\":\"s-abc123\",\"state\":\"open\",\"step_done\":\"health\",\"stopped_at\":null,\"domain\":\"shop.example.test\",\"admins\":[\"ops\",\"ops2\"]}\n";
        let screen = owner_answer(status, 0, opened, "");
        assert_eq!(
            screen,
            format!(
                "{}\n",
                wording::opened("s-abc123", "shop.example.test", "ops", Some("ops2"))
            )
        );
        assert!(
            screen.contains("請輸入:aib-cloud site s-abc123 login-link\n"),
            "{screen}"
        );
        assert!(!screen.contains("ez2_otl"));
        // one administrator only
        let one = opened.replace("[\"ops\",\"ops2\"]", "[\"ops\"]");
        assert!(owner_answer(status, 0, &one, "").contains("   管理員帳號:ops\n"));
        // a live site's checks still get their sentences
        assert_ne!(
            owner_answer(status, 0, "{\"exit\":0,\"checks\":[]}", ""),
            "{\"exit\":0,\"checks\":[]}"
        );
    }

    #[test]
    fn a_refusal_is_shown_as_its_code_only() {
        assert_eq!(refusal("refused: role_forbidden\n"), Some("role_forbidden"));
        assert_eq!(
            refusal("noise\nrefused: tenant_mismatch\n"),
            Some("tenant_mismatch")
        );
        // anything that is not a plain code word is not shown
        assert_eq!(refusal("refused: see /var/lib/x\n"), None);
        assert_eq!(refusal("refused: \n"), None);
        assert_eq!(refusal("could not read the site records\n"), None);
    }
}
