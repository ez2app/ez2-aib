//! N-web-oneline 1 (519d0 11-19 01:2x, SURVEY-webonly-519d0-1119 §6; Andy 11-19 01:1x:「我最終不要
//! 讓用戶開終端機對話,終端機只有那一行叫起網頁服務用」): when `aibd ctl web` finds aibd not
//! answering, it asks launchd to start the aibd that `aibd install --no-start` registered for this
//! account, or says in one line how to install it. It never starts aibd in the background itself
//! (a forked aibd would outlive this terminal with nobody to stop or update it).
//!
//! This is only what to do; `ctl` runs the steps and waits for aibd to answer. The job file is the
//! one `install` writes ([`crate::install::DEFAULT_LABEL`] under `~/Library/LaunchAgents`); no other
//! label or folder is looked at.

use std::path::{Path, PathBuf};

/// What `aibd ctl web` does when aibd does not answer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Wake {
    /// launchd has aibd's job file: load it into this user's GUI domain (already loaded is
    /// fine), then start it.
    Launchd {
        plist: PathBuf,
        domain: String,
        target: String,
    },
    /// Nothing here may start it; one line says what to do.
    Tell(String),
}

/// What to do on this machine. `macos`: running on macOS; `home`: this account's home folder;
/// `uid`: its user id; `exists`: whether a file is there.
pub fn plan(
    macos: bool,
    home: Option<&Path>,
    uid: Option<u32>,
    exists: &dyn Fn(&Path) -> bool,
) -> Wake {
    if !macos {
        return Wake::Tell(
            "aibd is not running. Start aibd in its own window, then run aibd ctl web again \
             (starting it from this line is macOS only for now)."
                .into(),
        );
    }
    let (Some(home), Some(uid)) = (home, uid) else {
        return Wake::Tell(
            "aibd is not running, and this account's home folder or user id is not known: \
             start aibd, then run aibd ctl web again."
                .into(),
        );
    };
    let label = crate::install::DEFAULT_LABEL;
    let plist = home
        .join("Library")
        .join("LaunchAgents")
        .join(format!("{label}.plist"));
    if !exists(&plist) {
        return Wake::Tell(
            "aibd is not running and is not installed for this account. Install it once with \
             `aibd install --no-start`, then run aibd ctl web again."
                .into(),
        );
    }
    Wake::Launchd {
        plist,
        domain: format!("gui/{uid}"),
        target: format!("gui/{uid}/{label}"),
    }
}

/// Whether this line names the aibd launchd runs: no `AIBD_BIND` and no `AIBD_DATA_DIR`, or
/// one that is the default folder (`data` vs `default`, compared as given). Any other folder or
/// address is some other aibd (a test, a preview), and launchd's would be the wrong one to
/// start (0eed7 11-20 01:2x: a script's aibd with its own folder had launchctl kick the resident).
pub fn names_resident(data: Option<&Path>, default: Option<&Path>, bind_set: bool) -> bool {
    !bind_set
        && match data {
            None => true,
            Some(d) => default.is_some_and(|f| f == d),
        }
}

/// What a line naming another aibd says when that aibd does not answer.
pub fn tell_other() -> Wake {
    Wake::Tell(
        "aibd is not running at the folder or address this line names (AIBD_DATA_DIR / AIBD_BIND); that is not the installed aibd, so launchd is not asked: start that aibd, then run aibd ctl web again."
            .into(),
    )
}

/// `launchctl` arguments for a [`Wake::Launchd`], in order. Only the last one's result counts:
/// `bootstrap` fails when the job is loaded already, which is fine.
pub fn steps(w: &Wake) -> Vec<Vec<String>> {
    match w {
        Wake::Launchd {
            plist,
            domain,
            target,
        } => vec![
            vec![
                "bootstrap".into(),
                domain.clone(),
                plist.to_string_lossy().into_owned(),
            ],
            vec!["kickstart".into(), target.clone()],
        ],
        Wake::Tell(_) => Vec::new(),
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    fn home() -> PathBuf {
        PathBuf::from("/Users/someone")
    }

    #[test]
    fn an_installed_aibd_is_loaded_then_started_by_launchd() {
        let w = plan(true, Some(&home()), Some(501), &|p| {
            p.ends_with("Library/LaunchAgents/app.ez2.aibd.plist")
        });
        assert_eq!(
            steps(&w),
            vec![
                vec![
                    "bootstrap".to_string(),
                    "gui/501".into(),
                    home()
                        .join("Library")
                        .join("LaunchAgents")
                        .join("app.ez2.aibd.plist")
                        .to_string_lossy()
                        .into_owned()
                ],
                vec!["kickstart".to_string(), "gui/501/app.ez2.aibd".into()],
            ],
            "reason: steps"
        );
    }

    #[test]
    fn a_line_naming_another_folder_or_address_never_wakes_the_resident() {
        let def = PathBuf::from("/Users/someone/Library/Application Support/aibd");
        assert!(
            names_resident(None, Some(&def), false),
            "reason: plain_line"
        );
        assert!(
            names_resident(Some(&def), Some(&def), false),
            "reason: default_named"
        );
        let tmp = PathBuf::from("/tmp/x");
        assert!(
            !names_resident(Some(&tmp), Some(&def), false),
            "reason: other_folder_wakes_resident"
        );
        assert!(
            !names_resident(None, Some(&def), true),
            "reason: other_address_wakes_resident"
        );
        assert!(!names_resident(Some(&tmp), None, false));
        assert!(steps(&tell_other()).is_empty(), "reason: told_but_started");
        let Wake::Tell(t) = tell_other() else {
            panic!("reason: not_told")
        };
        assert!(!t.contains("  "), "reason: gap_in_the_line {t}");
    }

    #[test]
    fn nothing_is_started_without_the_job_file_or_off_macos() {
        let none = plan(true, Some(&home()), Some(501), &|_| false);
        let Wake::Tell(t) = &none else {
            panic!("reason: started_without_job_file {none:?}")
        };
        assert!(t.contains("aibd install --no-start"), "{t}");
        assert!(steps(&none).is_empty());
        // Windows / Linux: told, never launchd, even with a file of that name
        let off = plan(false, Some(&home()), Some(501), &|_| true);
        let Wake::Tell(t) = &off else {
            panic!("reason: launchd_off_macos {off:?}")
        };
        assert!(t.contains("its own window"), "{t}");
        assert!(matches!(
            plan(true, None, Some(501), &|_| true),
            Wake::Tell(_)
        ));
        assert!(matches!(
            plan(true, Some(&home()), None, &|_| true),
            Wake::Tell(_)
        ));
    }
}
