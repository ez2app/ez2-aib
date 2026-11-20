//! SPEC-workspace-multiseat §19d (Andy 11-18 20:3x:「當進到畫面之後,要自動關掉 輸入一行指令的終端機,
//! 不要關錯終端機了」): once the page is paired, `aibd ctl web` closes the terminal tab it was typed
//! in, and only that one.
//!
//! It is matched by this process's own tty and nothing else (never a title, the front window or
//! a program name): macOS Terminal and iTerm2 are asked tab by tab for their tty, and only when
//! exactly one is the same is it closed. Terminal cannot close one tab of several, so there only
//! a window whose single tab is ours is closed. tmux / screen / ssh / an editor's terminal / a
//! terminal not known / other work in the tab / `--keep`: nothing is closed, one line says why.
//! Windows: nothing is closed from here (the console goes when its only program ends).

/// The terminals this can close.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum App {
    Terminal,
    ITerm,
}

/// What to do once the page is paired.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Decision {
    Close {
        app: App,
        tty: String,
    },
    /// Kept, and why (one short line).
    Keep(&'static str),
}

/// A macOS pseudo-terminal name, the only shape that goes into the script.
pub fn tty_ok(t: &str) -> bool {
    t.strip_prefix("/dev/ttys")
        .is_some_and(|n| (1..=4).contains(&n.len()) && n.bytes().all(|b| b.is_ascii_digit()))
}

/// Close or keep. `env` reads this process's environment; `tty` is its own (`tty`'s answer);
/// `others` is how many processes on that tty are not this one or its ancestors (`None`: not
/// known). Every doubt keeps the tab.
pub fn decide(
    env: &dyn Fn(&str) -> Option<String>,
    tty: Option<&str>,
    others: Option<usize>,
    keep: bool,
) -> Decision {
    let set = |k: &str| env(k).is_some_and(|v| !v.is_empty());
    if keep {
        return Decision::Keep("--keep");
    }
    if !cfg!(target_os = "macos") {
        return Decision::Keep("only macOS terminals are closed from here");
    }
    if set("TMUX") || set("STY") {
        return Decision::Keep("inside tmux / screen");
    }
    if set("SSH_CONNECTION") || set("SSH_TTY") || set("SSH_CLIENT") {
        return Decision::Keep("over ssh");
    }
    let app = match env("TERM_PROGRAM").as_deref() {
        Some("Apple_Terminal") => App::Terminal,
        Some("iTerm.app") => App::ITerm,
        _ => {
            return Decision::Keep(
                "not Terminal or iTerm2 (an editor's terminal, or one not known)",
            )
        }
    };
    let Some(tty) = tty.filter(|t| tty_ok(t)) else {
        return Decision::Keep("no terminal of its own");
    };
    match others {
        None => Decision::Keep("could not see what else runs in this tab"),
        Some(0) => Decision::Close {
            app,
            tty: tty.to_owned(),
        },
        Some(_) => Decision::Keep("something else runs in this tab"),
    }
}

/// Processes on the tty that are neither `me` nor one of my ancestors: `rows` are `(pid, ppid)`
/// of every process on that tty (`ps -t`).
pub fn others(rows: &[(u32, u32)], me: u32) -> usize {
    let parent = |p: u32| rows.iter().find(|r| r.0 == p).map(|r| r.1);
    let mut mine = vec![me];
    let mut p = me;
    while let Some(pp) = parent(p) {
        if mine.contains(&pp) {
            break;
        }
        mine.push(pp);
        p = pp;
    }
    rows.iter().filter(|r| !mine.contains(&r.0)).count()
}

/// The AppleScript that closes the one tab whose tty is `tty`, only when exactly one matches.
/// `tty` must pass [`tty_ok`] (nothing else ever reaches the script).
pub fn script(app: App, tty: &str) -> Option<String> {
    if !tty_ok(tty) {
        return None;
    }
    Some(match app {
        // Terminal closes windows, not tabs: only a window whose single tab is ours
        App::Terminal => format!(
            "delay 1\n\
             tell application \"Terminal\"\n\
             set n to 0\n\
             repeat with w in windows\n\
             repeat with t in tabs of w\n\
             if (tty of t) is \"{tty}\" then set n to n + 1\n\
             end repeat\n\
             end repeat\n\
             if n is 1 then\n\
             repeat with w in windows\n\
             if (count of tabs of w) is 1 and (tty of tab 1 of w) is \"{tty}\" then close w\n\
             end repeat\n\
             end if\n\
             end tell"
        ),
        App::ITerm => format!(
            "delay 1\n\
             tell application \"iTerm2\"\n\
             set n to 0\n\
             repeat with w in windows\n\
             repeat with t in tabs of w\n\
             repeat with s in sessions of t\n\
             if (tty of s) is \"{tty}\" then set n to n + 1\n\
             end repeat\n\
             end repeat\n\
             end repeat\n\
             if n is 1 then\n\
             repeat with w in windows\n\
             repeat with t in tabs of w\n\
             repeat with s in sessions of t\n\
             if (tty of s) is \"{tty}\" then close s\n\
             end repeat\n\
             end repeat\n\
             end repeat\n\
             end if\n\
             end tell"
        ),
    })
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    fn env(pairs: &'static [(&'static str, &'static str)]) -> impl Fn(&str) -> Option<String> {
        move |k| pairs.iter().find(|p| p.0 == k).map(|p| p.1.to_owned())
    }

    #[test]
    fn only_a_known_terminal_with_nothing_else_in_the_tab_is_closed() {
        if !cfg!(target_os = "macos") {
            let e = env(&[("TERM_PROGRAM", "Apple_Terminal")]);
            assert!(matches!(
                decide(&e, Some("/dev/ttys003"), Some(0), false),
                Decision::Keep(_)
            ));
            return;
        }
        let term = env(&[("TERM_PROGRAM", "Apple_Terminal")]);
        assert_eq!(
            decide(&term, Some("/dev/ttys003"), Some(0), false),
            Decision::Close {
                app: App::Terminal,
                tty: "/dev/ttys003".into()
            }
        );
        let iterm = env(&[("TERM_PROGRAM", "iTerm.app")]);
        assert!(matches!(
            decide(&iterm, Some("/dev/ttys010"), Some(0), false),
            Decision::Close {
                app: App::ITerm,
                ..
            }
        ));
        for (e, why) in [
            (
                env(&[("TERM_PROGRAM", "Apple_Terminal"), ("TMUX", "/tmp/t,1,0")]),
                "tmux",
            ),
            (
                env(&[("TERM_PROGRAM", "iTerm.app"), ("STY", "1.x")]),
                "screen",
            ),
            (
                env(&[
                    ("TERM_PROGRAM", "Apple_Terminal"),
                    ("SSH_CONNECTION", "a b c d"),
                ]),
                "ssh",
            ),
            (env(&[("TERM_PROGRAM", "vscode")]), "editor"),
            (env(&[]), "unknown"),
        ] {
            assert!(
                matches!(
                    decide(&e, Some("/dev/ttys003"), Some(0), false),
                    Decision::Keep(_)
                ),
                "reason: closed_in_{why}"
            );
        }
        assert!(
            matches!(
                decide(&term, Some("/dev/ttys003"), Some(1), false),
                Decision::Keep(_)
            ),
            "reason: closed_with_other_work"
        );
        assert!(matches!(
            decide(&term, Some("/dev/ttys003"), None, false),
            Decision::Keep(_)
        ));
        assert!(matches!(
            decide(&term, Some("/dev/ttys003"), Some(0), true),
            Decision::Keep("--keep")
        ));
        assert!(matches!(
            decide(&term, Some("not a tty"), Some(0), false),
            Decision::Keep(_)
        ));
        assert!(matches!(
            decide(&term, None, Some(0), false),
            Decision::Keep(_)
        ));
    }

    #[test]
    fn my_own_ancestors_are_not_other_work() {
        // login 100 → zsh 200 → aibd ctl 300; a vim 400 under the same zsh is other work
        let rows = [(100, 1), (200, 100), (300, 200)];
        assert_eq!(others(&rows, 300), 0);
        let rows = [(100, 1), (200, 100), (300, 200), (400, 200)];
        assert_eq!(others(&rows, 300), 1, "reason: other_work_unseen");
    }

    /// Two windows: the script closes only the tab whose tty is exactly ours (ttys003 is not
    /// ttys0031), only when exactly one matches, and never names anything but the tty.
    #[test]
    fn the_script_closes_only_the_exact_tty_and_only_when_it_is_the_one() {
        for app in [App::Terminal, App::ITerm] {
            let s = script(app, "/dev/ttys003").unwrap();
            assert!(s.contains("is \"/dev/ttys003\""));
            assert!(s.contains("if n is 1 then"), "reason: closes_without_count");
            assert!(!s.contains("front") && !s.contains("name of") && !s.contains("System Events"));
        }
        let t = script(App::Terminal, "/dev/ttys003").unwrap();
        assert!(
            t.contains("(count of tabs of w) is 1"),
            "reason: closes_a_window_of_tabs"
        );
        for bad in [
            "/dev/ttys003\" then close every window",
            "/dev/ttys",
            "/dev/tty.x",
            "",
        ] {
            assert!(script(App::ITerm, bad).is_none(), "{bad}");
        }
    }
}
