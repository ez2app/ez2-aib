// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud site <request…> --host H --user U --known-hosts F [--port N] [--key F] [--raw] [--print]`
//! — a customer asks the host for something about **their own site** (4.4.1 §1).
//!
//! ```text
//! aib-cloud site status
//! aib-cloud site logs <nginx|php|db> [lines N] [minutes N]
//! ```
//!
//! Which site is not said here: the host knows it from the key. Where to connect comes
//! from the flags, or from `EZ2_CLOUD_HOST`, `EZ2_CLOUD_USER`, `EZ2_CLOUD_KNOWN_HOSTS`,
//! `EZ2_CLOUD_PORT` and `EZ2_CLOUD_KEY` when a flag is not given.
//!
//! The request is checked here with the host's own parser, then sent with the system's
//! `ssh` and the arguments `aib_cloud::client` builds (no ssh configuration file is read,
//! the host key must match the file named, nothing is forwarded). `--print` shows those
//! arguments and sends nothing.
//!
//! The host answers in codes. What is shown is the sentence `aib_cloud::wording` has for
//! each code (zh-TW, final wording); a code it does not know is not shown. `--raw` shows
//! the host's answer as it came, for scripts.
//!
//! Exit status: the host's own for an answered request (see `aib-cloud-host customer`);
//! 2 the request or a flag was refused here and nothing was sent; 3 the host could not
//! be reached or `ssh` could not be run.

use std::process::{Command, ExitCode, Stdio};

use aib_cloud::client::{ssh_args, NotSent, Target};
use aib_cloud::customer::parse_request;
use aib_cloud::wording;

const USAGE: &str = "aib-cloud site <status | logs <nginx|php|db> [lines N] [minutes N]> --host H --user U --known-hosts F [--port N] [--key F] [--raw] [--print]";

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

/// Read the words after `site`. Flags may come anywhere; every other word is part of the
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
    let rest = match args.split_first() {
        Some((first, rest)) if first == "site" => rest,
        _ => return Err(USAGE.to_owned()),
    };
    let asked = with_env(read_args(rest)?, |name| std::env::var(name).ok());
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
    let done = match Command::new("ssh").args(&ssh).stdin(Stdio::null()).output() {
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
    if let Ok(request) = parse_request(&asked.request) {
        println!("{}", wording::reply(request, code, &out, &err));
    }
    Ok(code)
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
        // and the whole command needs `site` first
        assert!(run(&words(&["status"])).is_err());
        assert!(run(&[]).is_err());
    }
}
