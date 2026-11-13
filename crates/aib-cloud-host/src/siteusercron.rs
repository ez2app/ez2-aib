//! S18 (E10 6.1) on the host: run [`aib_cloud::usercron::S18_SCRIPT`] inside a site's
//! container and judge what it printed with [`aib_cloud::usercron::s18_user_cron`].
//!
//! Read only. The script answers whether cron runs, whether `cron.allow` / `cron.deny`
//! let the site's user use it, and two counts about that user's crontab; the crontab and
//! the two files themselves never leave the container. `site verify` asks it for a site
//! that is not quarantined, as it does S17 (see `siteverify::site_checks_for`): a
//! customer's status does not show it yet.

use aib_cloud::site::SiteSpec;
use aib_cloud::usercron;
use aib_cloud::verify::CheckResult;

use crate::sitecreatecmd::Runner;
use crate::siteidentity::in_ct;

/// S18 for one site: the script runs as root inside its container with the site's code
/// (its user) as the one argument. Whatever could not be run is passed on as `None`.
pub(crate) fn s18_check(runner: &mut impl Runner, spec: &SiteSpec) -> CheckResult {
    let listing = runner
        .run(&in_ct(
            spec.ct,
            &["sh", "-c", usercron::S18_SCRIPT, "sh", &spec.code],
        ))
        .ok();
    usercron::s18_user_cron(listing.as_deref())
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use aib_cloud::verify::Outcome;

    struct Ct(Result<&'static str, ()>, Vec<Vec<String>>);

    impl Runner for Ct {
        fn run(&mut self, argv: &[String]) -> Result<String, String> {
            self.1.push(argv.to_vec());
            self.0.map(str::to_owned).map_err(|()| "failed".to_owned())
        }
    }

    fn spec() -> SiteSpec {
        SiteSpec::new(
            "s-abc123",
            131,
            31,
            &["new.example.test"],
            true,
            "zh_TW",
            aib_cloud::site::Limits::START,
        )
        .unwrap()
    }

    const GREEN: &str = "LoadState=loaded\nUnitFileState=enabled\nActiveState=active\n\
        ez2-s18-allow none\nez2-s18-deny none\nez2-s18-tab 1 0\nez2-s18-end\n";

    #[test]
    fn s18_runs_one_script_in_the_sites_container_as_its_user() {
        let mut ct = Ct(Ok(GREEN), Vec::new());
        assert_eq!(s18_check(&mut ct, &spec()).outcome, Outcome::Green);
        assert_eq!(ct.1.len(), 1);
        assert_eq!(
            ct.1[0],
            [
                "pct",
                "exec",
                "131",
                "--",
                "sh",
                "-c",
                usercron::S18_SCRIPT,
                "sh",
                "s-abc123"
            ]
        );
    }

    #[test]
    fn s18_that_could_not_run_is_unknown() {
        let mut ct = Ct(Err(()), Vec::new());
        assert_eq!(
            s18_check(&mut ct, &spec()).outcome,
            Outcome::Unknown("user_cron_unread")
        );
    }
}
