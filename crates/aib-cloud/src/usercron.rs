//! S18 (E10 6.1): whether a site's own user can keep a schedule of its own with the
//! container's cron, judged from what [`S18_SCRIPT`] printed inside the site's container.
//!
//! What is read, and what is not:
//! * cron's unit: loaded, enabled, active (`systemctl show`, three keys);
//! * `/etc/cron.allow` and `/etc/cron.deny`: whether each is there and whether it names
//!   the site's user. Only that answer leaves the container, never the files;
//! * the site user's crontab: whether there is one, how many lines are not comments, and
//!   how many of those call `wp-cron.php` with curl or wget. Only those two numbers leave
//!   the container: a crontab can hold passwords or keys.
//!
//! cron's own rule decides who may use it: when `cron.allow` is there only the users it
//! names may; when it is not, `cron.deny` names who may not; with neither, everyone may.
//!
//! A line that calls the site's `wp-cron.php` over the network is a warning, not a
//! failure: the site's scheduler already runs from the container's timer (S14), and a
//! request the container sends to its own public address times out (E10 6.1 audit,
//! section 2). [`SELF_URL_HINT`] says what to do about it.
//!
//! Anything not read, or not read as expected, is unknown and never green.

use crate::verify::{CheckResult, Outcome};
use alloc::vec::Vec;

/// Run as `sh -c S18_SCRIPT sh <site code>` inside the site's container, as root. Prints
/// cron's three unit keys, then one marker line each for the allow file, the deny file
/// and the crontab, then [`S18_END`]. Exits non-zero when a part could not be read.
pub const S18_SCRIPT: &str = concat!(
    "u=$1; [ -n \"$u\" ] || exit 1; ",
    "systemctl show cron.service -p LoadState -p UnitFileState -p ActiveState || exit 1; ",
    "for k in allow deny; do f=/etc/cron.$k; ",
    "if [ ! -e \"$f\" ] && [ ! -L \"$f\" ]; then echo \"ez2-s18-$k none\"; ",
    "else awk -v u=\"$u\" '{ gsub(/^[ \\t]+|[ \\t]+$/, \"\") } $0 == u { f = 1 } END { exit f ? 0 : 1 }' \"$f\"; r=$?; ",
    "case $r in 0) echo \"ez2-s18-$k listed\" ;; 1) echo \"ez2-s18-$k unlisted\" ;; *) exit 1 ;; esac; fi; done; ",
    "t=/var/spool/cron/crontabs/$u; ",
    "if [ ! -e \"$t\" ] && [ ! -L \"$t\" ]; then echo 'ez2-s18-tab none'; ",
    "else [ -r \"$t\" ] || exit 1; ",
    "n=$(grep -cvE '^[[:space:]]*(#|$)' \"$t\") || [ $? -eq 1 ] || exit 1; ",
    "s=$(grep -vE '^[[:space:]]*#' \"$t\" | grep -ciE '(curl|wget)[^#]*wp-cron[.]php') || [ $? -eq 1 ] || exit 1; ",
    "echo \"ez2-s18-tab $n $s\"; fi; ",
    "echo ez2-s18-end"
);

/// The last line of what [`S18_SCRIPT`] prints: every part ran to its end.
pub const S18_END: &str = "ez2-s18-end";

/// What to tell whoever reads a `user_cron_self_url` warning (4542e, 11-12).
pub const SELF_URL_HINT: &str = "排程已由主機 timer 處理，這行可移除";

/// What one of `cron.allow` / `cron.deny` says about the site's user.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Listed {
    /// The file is not there.
    None,
    /// The file names the user.
    Listed,
    /// The file is there and does not name the user.
    Unlisted,
}

/// What [`S18_SCRIPT`] printed, read strictly: each part once, in its order.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Seen<'a> {
    load: &'a str,
    file: &'a str,
    active: &'a str,
    allow: Listed,
    deny: Listed,
    /// `None` when the user has no crontab; else lines that are not comments, and of those
    /// the ones that call `wp-cron.php` with curl or wget.
    tab: Option<(u32, u32)>,
}

fn listed(line: &str, marker: &str) -> Option<Listed> {
    match line.strip_prefix(marker)?.strip_prefix(' ')? {
        "none" => Some(Listed::None),
        "listed" => Some(Listed::Listed),
        "unlisted" => Some(Listed::Unlisted),
        _ => None,
    }
}

fn parse(text: &str) -> Option<Seen<'_>> {
    let lines: Vec<&str> = text
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .collect();
    let [a, b, c, allow, deny, tab, end] = lines.as_slice() else {
        return None;
    };
    if *end != S18_END {
        return None;
    }
    let mut unit = [None; 3];
    for line in [a, b, c] {
        let (key, value) = line.split_once('=')?;
        let slot = match key {
            "LoadState" => 0,
            "UnitFileState" => 1,
            "ActiveState" => 2,
            _ => return None,
        };
        if unit[slot].replace(value).is_some() {
            return None;
        }
    }
    let [Some(load), Some(file), Some(active)] = unit else {
        return None;
    };
    let tab = match tab.strip_prefix("ez2-s18-tab ")? {
        "none" => None,
        counts => {
            let (n, s) = counts.split_once(' ')?;
            let (n, s) = (n.parse::<u32>().ok()?, s.parse::<u32>().ok()?);
            if s > n {
                return None;
            }
            Some((n, s))
        }
    };
    Some(Seen {
        load,
        file,
        active,
        allow: listed(allow, "ez2-s18-allow")?,
        deny: listed(deny, "ez2-s18-deny")?,
        tab,
    })
}

/// S18: the site's user can keep a schedule with the container's cron. `listing` is what
/// [`S18_SCRIPT`] printed; `None` when it could not be run or did not end well.
///
/// * red `user_cron_off`: cron is not there, not enabled or not running;
/// * red `user_cron_denied`: `cron.allow` is there and does not name the user, or there is
///   no `cron.allow` and `cron.deny` names the user;
/// * warn `user_cron_self_url`: a line of the user's crontab calls `wp-cron.php` with curl
///   or wget (see [`SELF_URL_HINT`]);
/// * unknown `user_cron_unread` / `user_cron_unparsed`: not read, or not as expected;
/// * green otherwise. No crontab, or an empty one, is green: nothing is scheduled yet.
pub fn s18_user_cron(listing: Option<&str>) -> CheckResult {
    let outcome = match listing.map(parse) {
        None => Outcome::Unknown("user_cron_unread"),
        Some(None) => Outcome::Unknown("user_cron_unparsed"),
        Some(Some(seen)) => judge(&seen),
    };
    CheckResult { id: "S18", outcome }
}

fn judge(seen: &Seen<'_>) -> Outcome {
    match seen.load {
        "loaded" => {}
        "not-found" => return Outcome::Red("user_cron_off"),
        _ => return Outcome::Unknown("user_cron_unparsed"),
    }
    match seen.file {
        "enabled" | "enabled-runtime" => {}
        "disabled" | "masked" | "masked-runtime" => return Outcome::Red("user_cron_off"),
        _ => return Outcome::Unknown("user_cron_unparsed"),
    }
    match seen.active {
        "active" => {}
        "inactive" | "failed" | "deactivating" => return Outcome::Red("user_cron_off"),
        _ => return Outcome::Unknown("user_cron_unparsed"),
    }
    let denied = match seen.allow {
        Listed::Listed => false,
        Listed::Unlisted => true,
        Listed::None => seen.deny == Listed::Listed,
    };
    if denied {
        return Outcome::Red("user_cron_denied");
    }
    match seen.tab {
        Some((_, self_url)) if self_url > 0 => Outcome::Warn("user_cron_self_url"),
        _ => Outcome::Green,
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use alloc::format;
    use alloc::string::String;

    const ON: &str = "LoadState=loaded\nUnitFileState=enabled\nActiveState=active\n";

    fn out(unit: &str, allow: &str, deny: &str, tab: &str) -> String {
        format!("{unit}ez2-s18-allow {allow}\nez2-s18-deny {deny}\nez2-s18-tab {tab}\n{S18_END}\n")
    }

    fn s18(text: &str) -> Outcome {
        s18_user_cron(Some(text)).outcome
    }

    #[test]
    fn cron_running_and_open_to_everyone_is_green() {
        assert_eq!(s18(&out(ON, "none", "none", "none")), Outcome::Green);
        assert_eq!(s18(&out(ON, "none", "none", "0 0")), Outcome::Green);
        assert_eq!(s18(&out(ON, "none", "none", "3 0")), Outcome::Green);
        assert_eq!(
            s18_user_cron(Some(&out(ON, "none", "none", "none"))).id,
            "S18"
        );
    }

    #[test]
    fn an_allow_file_decides_alone_when_it_is_there() {
        assert_eq!(s18(&out(ON, "listed", "none", "none")), Outcome::Green);
        // cron.allow wins over cron.deny
        assert_eq!(s18(&out(ON, "listed", "listed", "none")), Outcome::Green);
        assert_eq!(
            s18(&out(ON, "unlisted", "none", "none")),
            Outcome::Red("user_cron_denied")
        );
        assert_eq!(
            s18(&out(ON, "unlisted", "unlisted", "none")),
            Outcome::Red("user_cron_denied")
        );
    }

    #[test]
    fn without_an_allow_file_the_deny_file_decides() {
        assert_eq!(s18(&out(ON, "none", "unlisted", "none")), Outcome::Green);
        assert_eq!(
            s18(&out(ON, "none", "listed", "none")),
            Outcome::Red("user_cron_denied")
        );
    }

    #[test]
    fn cron_not_there_off_or_stopped_is_red() {
        for unit in [
            "LoadState=not-found\nUnitFileState=\nActiveState=inactive\n",
            "LoadState=loaded\nUnitFileState=disabled\nActiveState=active\n",
            "LoadState=loaded\nUnitFileState=masked\nActiveState=inactive\n",
            "LoadState=loaded\nUnitFileState=enabled\nActiveState=inactive\n",
            "LoadState=loaded\nUnitFileState=enabled\nActiveState=failed\n",
        ] {
            assert_eq!(
                s18(&out(unit, "none", "none", "none")),
                Outcome::Red("user_cron_off"),
                "{unit}"
            );
        }
    }

    #[test]
    fn a_line_calling_wp_cron_over_the_network_is_a_warning() {
        assert_eq!(
            s18(&out(ON, "none", "none", "2 1")),
            Outcome::Warn("user_cron_self_url")
        );
        // a denied user is red before any warning
        assert_eq!(
            s18(&out(ON, "unlisted", "none", "2 1")),
            Outcome::Red("user_cron_denied")
        );
    }

    #[test]
    fn what_was_not_read_is_never_green() {
        assert_eq!(
            s18_user_cron(None).outcome,
            Outcome::Unknown("user_cron_unread")
        );
        let full = out(ON, "none", "none", "none");
        for bad in [
            String::new(),
            // no end line: a part did not finish
            full.replace(S18_END, ""),
            // a part twice, or one missing
            out(ON, "none", "none", "none").replace("ez2-s18-deny none\n", ""),
            format!("{ON}{full}"),
            // values it does not know
            out(ON, "maybe", "none", "none"),
            out(ON, "none", "none", "x 0"),
            out(ON, "none", "none", "1 2"),
            out(ON, "none", "none", "1"),
            out(
                "LoadState=loaded\nLoadState=loaded\nActiveState=active\n",
                "none",
                "none",
                "none",
            ),
            out(
                "LoadState=loaded\nUnitFileState=enabled\nSubState=running\n",
                "none",
                "none",
                "none",
            ),
            out(
                "LoadState=bad-setting\nUnitFileState=enabled\nActiveState=active\n",
                "none",
                "none",
                "none",
            ),
            out(
                "LoadState=loaded\nUnitFileState=static\nActiveState=active\n",
                "none",
                "none",
                "none",
            ),
            out(
                "LoadState=loaded\nUnitFileState=enabled\nActiveState=activating\n",
                "none",
                "none",
                "none",
            ),
        ] {
            assert!(
                matches!(s18(&bad), Outcome::Unknown(_)),
                "{bad:?} gave {:?}",
                s18(&bad)
            );
        }
    }

    #[test]
    fn the_script_leaves_only_answers_and_counts() {
        // never the crontab or the allow / deny files themselves
        assert!(!S18_SCRIPT.contains("cat "));
        assert!(!S18_SCRIPT.contains("crontab -l"));
        assert!(S18_SCRIPT.contains("grep -cvE"));
        assert!(S18_SCRIPT.ends_with(S18_END));
    }
}
