//! 「加一台測試機」(519d0 11-21 09:27, Andy: what needs his terminal becomes a button;
//! DESIGN-add-test-host-b4297-1121): the parts that need no connection — which machines the
//! owner can pick, the three fixed commands run through the owner's own ssh login (read the
//! machine's host key, write the one authorized line, take it away), what the read says, and the
//! pinned host list with one machine added or taken away.
//!
//! - The machines offered are the `Host` names of the owner's ssh config, nothing else of it
//!   (no address, user or key is read); a name with a pattern (`*`, `?`, `!`) is not a machine.
//! - The commands are constants; the only thing that changes is the authorized line, which goes
//!   on standard input, never on the command line. Each is run by `/bin/sh` on the test machine.
//! - The write is the paste page's line (PAGE-andy-receipt-key r1, 5d33a M1): a file without a
//!   last newline gets one first, the line is added once, the file is 0600, and it answers with
//!   how many copies there are (exactly 1 is success). The removal takes that exact line away
//!   and keeps every other.

use crate::trusted_hosts::{self, TrustedHost};

/// Read on the test machine through the owner's login: its ed25519 host key, the login name and
/// the macOS version (`sw_vers` is not there on anything but a Mac).
pub const READ: &str = "cat /etc/ssh/ssh_host_ed25519_key.pub; id -un; sw_vers -productVersion";

/// Add the line read from standard input to `~/.ssh/authorized_keys` once; prints `ADDED <n>`.
pub const ADD: &str = r#"IFS= read -r L || exit 3
case "$L" in restrict,command=*) ;; *) exit 3;; esac
umask 077
mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh" || exit 4
F="$HOME/.ssh/authorized_keys"
touch "$F" || exit 4
if [ -s "$F" ] && [ -n "$(tail -c1 "$F")" ]; then printf "\n" >> "$F" || exit 4; fi
grep -qxF -- "$L" "$F" || printf "%s\n" "$L" >> "$F" || exit 4
chmod 600 "$F" || exit 4
echo "ADDED $(grep -cxF -- "$L" "$F")""#;

/// Take the line read from standard input out of `~/.ssh/authorized_keys`, every other line
/// kept; prints `REMOVED <copies left>`.
pub const REMOVE: &str = r#"IFS= read -r L || exit 3
case "$L" in restrict,command=*) ;; *) exit 3;; esac
F="$HOME/.ssh/authorized_keys"
[ -f "$F" ] || { echo "REMOVED 0"; exit 0; }
T="$F.aibd.$$"
grep -vxF -- "$L" "$F" > "$T"
r=$?
[ "$r" -le 1 ] || { rm -f "$T"; exit 4; }
chmod 600 "$T" && mv "$T" "$F" || { rm -f "$T"; exit 4; }
echo "REMOVED $(grep -cxF -- "$L" "$F")""#;

/// The one remote command for `script`: `/bin/sh -c '<script>'` (none of the scripts holds a
/// single quote, so nothing is escaped).
pub fn remote_command(script: &str) -> Option<String> {
    (!script.contains('\'')).then(|| format!("/bin/sh -c '{script}'"))
}

/// A machine name the owner can pick: what an ssh `Host` line holds for one machine.
fn pick_ok(s: &str) -> bool {
    (1..=64).contains(&s.len())
        && !s.starts_with('-')
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-._".contains(&b))
}

/// The machine names of an ssh config: the words of its `Host` lines that name one machine,
/// each once, in order.
pub fn host_names(config: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for line in config.lines() {
        let t = line.trim();
        let mut w = t
            .split(|c: char| c.is_whitespace() || c == '=')
            .filter(|x| !x.is_empty());
        if !w.next().is_some_and(|k| k.eq_ignore_ascii_case("host")) {
            continue;
        }
        for name in w {
            if name.starts_with('#') {
                break;
            }
            if pick_ok(name) && !out.iter().any(|o| o == name) {
                out.push(name.to_owned());
            }
        }
    }
    out
}

/// What [`READ`] said about a machine.
#[derive(Debug, PartialEq, Eq)]
pub struct Read {
    /// `ssh-ed25519 <base64>`, its comment dropped (as the host list keeps it).
    pub hostkey: String,
    pub user: String,
    pub macos: String,
}

/// [`READ`]'s three lines; anything else (no ed25519 key, not a Mac) is refused.
pub fn parse_read(out: &str) -> Result<Read, &'static str> {
    let mut l = out.lines().map(str::trim);
    let key = l.next().unwrap_or_default();
    let mut k = key.split(' ');
    let hostkey = match (k.next(), k.next()) {
        (Some("ssh-ed25519"), Some(b)) => format!("ssh-ed25519 {b}"),
        _ => return Err("no_ed25519_host_key"),
    };
    let user = l.next().unwrap_or_default().to_owned();
    let macos = l.next().unwrap_or_default().to_owned();
    if !macos.starts_with(|c: char| c.is_ascii_digit())
        || !macos.bytes().all(|b| b.is_ascii_digit() || b == b'.')
    {
        return Err("not_a_mac");
    }
    if l.next().is_some() {
        return Err("read_said_more");
    }
    Ok(Read {
        hostkey,
        user,
        macos,
    })
}

/// The host list with `h` in it: checked as [`trusted_hosts::check`] checks; the same name
/// is replaced where it stands (a machine added again after its key changed: the list's order
/// is which machine is used first), a new one goes last, at most [`trusted_hosts::MAX_HOSTS`].
pub fn with_host(list: &[TrustedHost], h: TrustedHost) -> Result<Vec<TrustedHost>, &'static str> {
    trusted_hosts::check(&h)?;
    let mut out = list.to_vec();
    if let Some(o) = out.iter_mut().find(|o| o.name == h.name) {
        *o = h;
        return Ok(out);
    }
    if out.len() >= trusted_hosts::MAX_HOSTS {
        return Err("too_many_machines");
    }
    out.push(h);
    Ok(out)
}

/// The host list without the machine `name` (none left is an empty list: the file is then
/// removed, since [`trusted_hosts::parse`] refuses an empty one).
pub fn without_host(list: &[TrustedHost], name: &str) -> Vec<TrustedHost> {
    list.iter().filter(|o| o.name != name).cloned().collect()
}

/// The host list file's text for `list`, as [`trusted_hosts::parse`] reads it back.
pub fn list_text(list: &[TrustedHost]) -> String {
    let v = serde_json::json!({ "hosts": list });
    let mut s = serde_json::to_string_pretty(&v).unwrap_or_default();
    s.push('\n');
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    const KEY: &str =
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl";

    fn mini() -> TrustedHost {
        TrustedHost {
            name: "andymini".into(),
            hostname: "192.0.2.7".into(),
            port: 22,
            user: "owner".into(),
            hostkey: KEY.into(),
            os: "mac".into(),
        }
    }

    #[test]
    fn only_the_names_of_single_machines_are_offered() {
        let cfg = "Host *\n  User x\nHost andymini lin-macmini # two\nhost=pro13\n\
                   Host !bad web-?? -oProxy\nHostName 192.0.2.9\nHost andymini\n  Match all\n";
        assert_eq!(host_names(cfg), ["andymini", "lin-macmini", "pro13"]);
        assert!(
            host_names("HostName andymini\n").is_empty(),
            "reason: hostname_read_as_host"
        );
    }

    #[test]
    fn the_read_gives_the_key_without_its_comment_and_refuses_a_machine_that_is_no_mac() {
        let r = parse_read(&format!("{KEY} root@andymini\nowner\n15.6.1\n")).unwrap();
        assert_eq!(r.hostkey, KEY, "reason: comment_kept");
        assert_eq!((r.user.as_str(), r.macos.as_str()), ("owner", "15.6.1"));
        assert_eq!(
            parse_read("ssh-rsa AAAA x\nowner\n15.6\n"),
            Err("no_ed25519_host_key")
        );
        assert_eq!(
            parse_read(&format!("{KEY}\nowner\nsh: sw_vers: not found\n")),
            Err("not_a_mac")
        );
        assert_eq!(parse_read(&format!("{KEY}\nowner\n\n")), Err("not_a_mac"));
        assert_eq!(
            parse_read(&format!("{KEY}\nowner\n15.6\nmore\n")),
            Err("read_said_more")
        );
    }

    #[test]
    fn the_commands_are_fixed_and_run_by_sh_with_the_line_on_standard_input() {
        for s in [ADD, REMOVE] {
            let c = remote_command(s).unwrap();
            assert!(c.starts_with("/bin/sh -c '") && c.ends_with('\''));
            assert!(
                s.starts_with("IFS= read -r L || exit 3\n"),
                "reason: line_not_from_stdin"
            );
            assert!(
                !s.contains("$1") && !s.contains("$@"),
                "reason: argument_read"
            );
        }
        assert_eq!(remote_command("echo 'x'"), None, "reason: quote_passed");
        assert!(!READ.contains('\''));
    }

    #[test]
    fn a_machine_is_added_once_replaced_by_name_and_taken_away() {
        let a = with_host(&[], mini()).unwrap();
        let text = list_text(&a);
        assert_eq!(
            trusted_hosts::parse(text.as_bytes()).unwrap(),
            a,
            "reason: list_not_read_back"
        );
        let mut again = mini();
        again.hostname = "192.0.2.8".into();
        let two = with_host(
            &a,
            TrustedHost {
                name: "lin".into(),
                ..mini()
            },
        )
        .unwrap();
        let b = with_host(&two, again.clone()).unwrap();
        assert_eq!(b[0], again, "reason: replaced_machine_moved");
        assert_eq!(b.len(), 2, "reason: name_twice");
        let b = without_host(&b, "lin");
        let mut bad = mini();
        bad.hostkey = "ssh-rsa AAAA".into();
        assert_eq!(with_host(&a, bad), Err("hostkey"));
        let full: Vec<TrustedHost> = (0..trusted_hosts::MAX_HOSTS)
            .map(|i| TrustedHost {
                name: format!("m{i}"),
                ..mini()
            })
            .collect();
        assert_eq!(with_host(&full, mini()), Err("too_many_machines"));
        assert!(without_host(&b, "andymini").is_empty());
        assert_eq!(without_host(&b, "other"), b);
    }

    /// The two scripts as `/bin/sh` runs them, on a home of their own.
    #[cfg(unix)]
    #[test]
    fn the_line_is_written_once_after_a_last_newline_and_taken_away_alone() {
        use std::io::Write;
        use std::os::unix::fs::PermissionsExt;
        let line = format!("restrict,command=\"/x/gate\",from=\"192.0.2.7\" {KEY} aibd-delegate");
        let home = tempfile::tempdir().unwrap();
        let ssh = home.path().join(".ssh");
        std::fs::create_dir_all(&ssh).unwrap();
        let file = ssh.join("authorized_keys");
        std::fs::write(&file, "ssh-rsa AAAA other").unwrap(); // no last newline
        let run = |script: &str, input: &str| {
            let mut c = std::process::Command::new("/bin/sh")
                .arg("-c")
                .arg(script)
                .env_clear()
                .env("HOME", home.path())
                .env("PATH", "/usr/bin:/bin")
                .stdin(std::process::Stdio::piped())
                .stdout(std::process::Stdio::piped())
                .spawn()
                .unwrap();
            c.stdin.take().unwrap().write_all(input.as_bytes()).unwrap();
            let o = c.wait_with_output().unwrap();
            (
                o.status.code(),
                String::from_utf8_lossy(&o.stdout).trim().to_owned(),
            )
        };
        let input = format!("{line}\n");
        assert_eq!(run(ADD, &input), (Some(0), "ADDED 1".to_owned()));
        assert_eq!(
            run(ADD, &input),
            (Some(0), "ADDED 1".to_owned()),
            "reason: added_twice"
        );
        assert_eq!(
            std::fs::read_to_string(&file).unwrap(),
            format!("ssh-rsa AAAA other\n{line}\n"),
            "reason: glued_to_the_last_line"
        );
        let mode = std::fs::metadata(&file).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);
        assert_eq!(
            run(ADD, "ssh-rsa AAAA evil\n").0,
            Some(3),
            "reason: any_line_added"
        );
        assert_eq!(run(REMOVE, &input), (Some(0), "REMOVED 0".to_owned()));
        assert_eq!(
            std::fs::read_to_string(&file).unwrap(),
            "ssh-rsa AAAA other\n",
            "reason: other_line_lost"
        );
    }
}
