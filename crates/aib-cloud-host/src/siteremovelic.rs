// SPDX-License-Identifier: Apache-2.0
//! The licence step of `site remove` on the host: each paid plugin of
//! [`aib_cloud::siteremove::PAID_PLUGINS`] is asked about from inside the site, and the
//! activations that can be given back by a program are given back there and asked about
//! again. Which plugin is which is the fixed table in `aib-cloud`; this file only asks and
//! reads the answers. The questions are those of the reviewed hand-run program
//! (`site-remove.sh` step 3 of the ez2-cloud-site-manual skill); SEOPress is asked the way
//! `site create` asks it ([`crate::sitelive`]), with the plugin's own store address and
//! item number.
//!
//! Keys stay inside the site or in memory: the template's SEOPress key is read from the
//! host's key file and compared with the site's key in memory; neither is an argument or
//! a line of output.
//!
//! A site that cannot be read (its container is gone or not running, or the site software
//! does not answer) is "unreadable" ([`ask_all`] answers `None`): every paid plugin is
//! then recorded as left at its vendor with the word `site_unreadable`, and the removal
//! goes on (Andy 11-11: what cannot be done automatically is left for now, not chased).

use aib_cloud::site::SiteSpec;
use aib_cloud::sitedefaults::License;
use aib_cloud::siteremove::VendorAnswer;

use crate::sitecreatecmd::Runner;
use crate::siteidentity::wp;

/// Fluent Forms Pro asked about this site, from the site (the hand-run program's question,
/// unchanged).
const FF_ASK: &str = r#"if(!class_exists("\\FluentFormPro\\FluentUpdater\\FluentLicensing")){echo "FFV absent\n";return;} $l=\FluentFormPro\FluentUpdater\FluentLicensing::getInstance(); $s=(array)get_option("__fluentformpro_license",[]); $k=(string)($s["license_key"]??""); if($k===""){echo "FFV nokey\n";return;} $u=add_query_arg(["fluent-cart"=>"check_license"],$l->getConfig("api_url")); $r=wp_remote_post($u,["timeout"=>15,"body"=>["item_id"=>$l->getConfig("item_id"),"current_version"=>$l->getConfig("version"),"site_url"=>$l->siteUrl(),"license_key"=>$k,"activation_hash"=>(string)($s["activation_hash"]??""),"platform_version"=>get_bloginfo("version"),"server_version"=>PHP_VERSION]]); if(is_wp_error($r)){echo "FFV unknown\n";return;} $d=json_decode(wp_remote_retrieve_body($r),true); if(!is_array($d)){echo "FFV unknown\n";return;} echo "FFV status=".preg_replace("/[^a-z_]/","",(string)($d["status"]??""))."\n";"#;

/// Fluent Forms Pro given back by its own licensing client, and the vendor asked again in
/// the same call: deactivating clears the key the site keeps, so a later question would
/// have nothing to ask with (the hand-run program's call, unchanged).
const FF_OFF: &str = r#"$l=\FluentFormPro\FluentUpdater\FluentLicensing::getInstance(); $s=(array)get_option("__fluentformpro_license",[]); $k=(string)($s["license_key"]??""); if($k===""){echo "FFOFF ERR no-key\n";return;} $body=["item_id"=>$l->getConfig("item_id"),"current_version"=>$l->getConfig("version"),"site_url"=>$l->siteUrl(),"license_key"=>$k,"activation_hash"=>(string)($s["activation_hash"]??""),"platform_version"=>get_bloginfo("version"),"server_version"=>PHP_VERSION]; $u=add_query_arg(["fluent-cart"=>"check_license"],$l->getConfig("api_url")); $r=$l->deactivate(); if(is_wp_error($r)){echo "FFOFF ERR\n";return;} $c=wp_remote_post($u,["timeout"=>15,"body"=>$body]); $d=is_wp_error($c)?null:json_decode(wp_remote_retrieve_body($c),true); echo "FFOFF DONE after=".(is_array($d)?preg_replace("/[^a-z_]/","",(string)($d["status"]??"")):"unknown")."\n";"#;

/// SEOPress given back: the vendor's own deactivate call, sent from the site with the
/// site's key, the plugin's store address and item number and the site's home address
/// (the same as the question [`crate::sitelive::SP_PROBE`] sends).
const SP_OFF: &str = r#"$k=trim((string)get_option("seopress_pro_license_key")); if($k===""){echo "SPOFF none\n";return;} $r=wp_remote_post(STORE_URL_SEOPRESS,["timeout"=>15,"body"=>["edd_action"=>"deactivate_license","license"=>$k,"item_id"=>ITEM_ID_SEOPRESS,"url"=>get_option("home")]]); if(is_wp_error($r)||200!==wp_remote_retrieve_response_code($r)){echo "SPOFF unknown\n";return;} $d=json_decode(wp_remote_retrieve_body($r),true); if(!is_array($d)||!isset($d["license"])){echo "SPOFF unknown\n";return;} echo "SPOFF license=".preg_replace("/[^a-z_]/","",(string)$d["license"])."\n";"#;

/// Whether FlyingPress keeps an activation number (the hand-run program's question).
const FP_ASK: &str = r#"$o=(array)get_option("flyingpress_license_options",[]); echo "FP activation=".((string)($o["sc_activation_id"]??"")===""?0:1)."\n";"#;

/// Whether one Freemius plugin holds a licence in the site; `FS_FN` is its Freemius
/// function (the hand-run program's question, one plugin at a time).
const FS_ASK: &str = r#"if(!function_exists("FS_FN")){echo "FS absent\n";return;} $fs=call_user_func("FS_FN"); echo "FS licensed=".((is_object($fs)&&is_object($fs->_get_license()))?1:0)."\n";"#;

/// The Freemius function of each Freemius plugin of the table.
pub const FREEMIUS_FN: [(&str, &str); 2] = [("stackable", "sugb_fs"), ("blocksy", "blc_fs")];

/// The first line of `out` that starts with `prefix`, without the prefix.
fn line_after<'a>(out: &'a str, prefix: &str) -> Option<&'a str> {
    out.lines().find_map(|l| l.strip_prefix(prefix))
}

/// The SEOPress vendor's answer for this site as the removal reads it.
pub fn seopress_word(l: License) -> VendorAnswer {
    match l {
        License::Active => VendorAnswer::Active,
        License::Inactive | License::Quota => VendorAnswer::NotActive,
        License::NotValid => VendorAnswer::NotValid,
        License::Unknown => VendorAnswer::Unknown,
    }
}

/// Whether the vendor answered SEOPress's deactivate call at all (its word is then read
/// by asking again).
pub fn seopress_off(out: &str) -> bool {
    line_after(out, "SPOFF license=").is_some_and(|w| !w.is_empty())
}

/// Fluent Forms Pro's answer: `valid` is held; any other word the vendor says is not;
/// no plugin or no key in the site is "nothing in the site"; no answer is unknown.
pub fn fluent_answer(out: &str) -> VendorAnswer {
    match line_after(out, "FFV ") {
        Some("absent" | "nokey") => VendorAnswer::NoData,
        Some(rest) => match rest.strip_prefix("status=") {
            Some("valid") => VendorAnswer::Active,
            Some(w) if !w.is_empty() => VendorAnswer::NotActive,
            _ => VendorAnswer::Unknown,
        },
        None => VendorAnswer::Unknown,
    }
}

/// Fluent Forms Pro's deactivation: whether the call went through, and the vendor's word
/// right after it.
pub fn fluent_off(out: &str) -> (bool, VendorAnswer) {
    match line_after(out, "FFOFF DONE after=") {
        Some("valid") => (true, VendorAnswer::Active),
        Some("" | "unknown") => (true, VendorAnswer::Unknown),
        Some(_) => (true, VendorAnswer::NotActive),
        None => (false, VendorAnswer::Unknown),
    }
}

/// FlyingPress: an activation number kept is an activation.
pub fn flying_answer(out: &str) -> VendorAnswer {
    match line_after(out, "FP activation=") {
        Some("1") => VendorAnswer::Active,
        Some("0") => VendorAnswer::NotActive,
        _ => VendorAnswer::Unknown,
    }
}

/// A Freemius plugin: a licence held in the site is an activation.
pub fn freemius_answer(out: &str) -> VendorAnswer {
    if out.lines().any(|l| l == "FS absent") {
        return VendorAnswer::NoData;
    }
    match line_after(out, "FS licensed=") {
        Some("1") => VendorAnswer::Active,
        Some("0") => VendorAnswer::NotActive,
        _ => VendorAnswer::Unknown,
    }
}

fn eval(runner: &mut impl Runner, spec: &SiteSpec, code: &str) -> String {
    runner
        .run(&wp(spec, &["eval", code]))
        .unwrap_or_default()
}

/// SEOPress, asked only when the key in the site is the template's (`template` is that
/// key, `None` when it could not be read).
fn seopress(runner: &mut impl Runner, spec: &SiteSpec, template: Option<&str>) -> VendorAnswer {
    let Ok(site) = runner.run(&wp(spec, &["option", "get", "seopress_pro_license_key"])) else {
        return VendorAnswer::Unknown;
    };
    let site = site.trim();
    if site.is_empty() {
        return VendorAnswer::NoData;
    }
    match template {
        Some(t) if crate::sitelive::same_key(site, t) => {}
        _ => return VendorAnswer::KeyNotTemplate,
    }
    seopress_word(crate::sitelive::seopress_answer(&eval(
        runner,
        spec,
        crate::sitelive::SP_PROBE,
    )))
}

/// One plugin of the table asked about; `None` for a name the table does not have.
pub fn ask_one(
    runner: &mut impl Runner,
    spec: &SiteSpec,
    plugin: &str,
    template: Option<&str>,
) -> Option<VendorAnswer> {
    Some(match plugin {
        "seopress" => seopress(runner, spec, template),
        "fluentformpro" => fluent_answer(&eval(runner, spec, FF_ASK)),
        "flyingpress" => flying_answer(&eval(runner, spec, FP_ASK)),
        other => {
            let (_, f) = FREEMIUS_FN.iter().find(|(n, _)| *n == other)?;
            freemius_answer(&eval(runner, spec, &FS_ASK.replace("FS_FN", f)))
        }
    })
}

/// Every paid plugin asked about. `None`: the site could not be read (the site software
/// did not answer for its own address).
pub fn ask_all(
    runner: &mut impl Runner,
    spec: &SiteSpec,
    template: Option<&str>,
) -> Option<Vec<(&'static str, VendorAnswer)>> {
    runner.run(&wp(spec, &["option", "get", "siteurl"])).ok()?;
    Some(
        aib_cloud::siteremove::PAID_PLUGINS
            .iter()
            .map(|p| {
                let a = ask_one(runner, spec, p.name, template).unwrap_or(VendorAnswer::Unknown);
                (p.name, a)
            })
            .collect(),
    )
}

/// One activation given back from the site: whether the call went through, and the
/// vendor's word asked right after. Only the plugins the table gives back in the site are
/// given back; any other name is a call that did not go through.
pub fn give_back(
    runner: &mut impl Runner,
    spec: &SiteSpec,
    plugin: &str,
    template: Option<&str>,
) -> (bool, VendorAnswer) {
    match plugin {
        "seopress" => {
            let ok = seopress_off(&eval(runner, spec, SP_OFF));
            (ok, seopress(runner, spec, template))
        }
        "fluentformpro" => fluent_off(&eval(runner, spec, FF_OFF)),
        _ => (false, VendorAnswer::Unknown),
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use aib_cloud::siteremove::{give_back_of, GiveBack, PAID_PLUGINS};

    #[test]
    fn each_answer_is_read_by_its_own_words_and_nothing_else_is_guessed() {
        assert_eq!(fluent_answer("FFV status=valid\n"), VendorAnswer::Active);
        assert_eq!(fluent_answer("FFV status=expired\n"), VendorAnswer::NotActive);
        assert_eq!(fluent_answer("FFV status=\n"), VendorAnswer::Unknown);
        assert_eq!(fluent_answer("FFV absent\n"), VendorAnswer::NoData);
        assert_eq!(fluent_answer("FFV nokey\n"), VendorAnswer::NoData);
        assert_eq!(fluent_answer("FFV unknown\n"), VendorAnswer::Unknown);
        assert_eq!(fluent_answer(""), VendorAnswer::Unknown);
        assert_eq!(flying_answer("FP activation=1\n"), VendorAnswer::Active);
        assert_eq!(flying_answer("FP activation=0\n"), VendorAnswer::NotActive);
        assert_eq!(flying_answer("Warning\n"), VendorAnswer::Unknown);
        assert_eq!(freemius_answer("FS licensed=1\n"), VendorAnswer::Active);
        assert_eq!(freemius_answer("FS licensed=0\n"), VendorAnswer::NotActive);
        assert_eq!(freemius_answer("FS absent\n"), VendorAnswer::NoData);
        assert_eq!(freemius_answer(""), VendorAnswer::Unknown);
        assert_eq!(seopress_word(License::Active), VendorAnswer::Active);
        assert_eq!(seopress_word(License::Inactive), VendorAnswer::NotActive);
        assert_eq!(seopress_word(License::Quota), VendorAnswer::NotActive);
        assert_eq!(seopress_word(License::NotValid), VendorAnswer::NotValid);
        assert_eq!(seopress_word(License::Unknown), VendorAnswer::Unknown);
        assert!(seopress_off("SPOFF license=deactivated\n"));
        assert!(!seopress_off("SPOFF unknown\n"));
        assert!(!seopress_off("SPOFF license=\n"));
    }

    #[test]
    fn a_deactivation_counts_only_with_the_vendors_word_after_it() {
        assert_eq!(
            fluent_off("FFOFF DONE after=inactive\n"),
            (true, VendorAnswer::NotActive)
        );
        assert_eq!(
            fluent_off("FFOFF DONE after=valid\n"),
            (true, VendorAnswer::Active)
        );
        assert_eq!(
            fluent_off("FFOFF DONE after=unknown\n"),
            (true, VendorAnswer::Unknown)
        );
        assert_eq!(fluent_off("FFOFF ERR\n"), (false, VendorAnswer::Unknown));
        assert_eq!(fluent_off(""), (false, VendorAnswer::Unknown));
    }

    /// Answers each `wp eval` from a table; the site key is the template's.
    struct Site {
        up: bool,
        calls: Vec<String>,
    }

    impl Runner for Site {
        fn run(&mut self, argv: &[String]) -> Result<String, String> {
            let line = argv.join(" ");
            self.calls.push(line.clone());
            if !self.up {
                return Err("container not running".to_owned());
            }
            Ok(if line.contains("option get siteurl") {
                "https://www.example.com\n".to_owned()
            } else if line.contains("option get seopress_pro_license_key") {
                "TEMPLATEKEY\n".to_owned()
            } else if line.contains("SPOFF") {
                "SPOFF license=deactivated\n".to_owned()
            } else if line.contains("check_license\",\"license\"") {
                "SP license=valid left=3\n".to_owned()
            } else if line.contains("FFOFF") {
                "FFOFF DONE after=inactive\n".to_owned()
            } else if line.contains("FFV") {
                "FFV status=valid\n".to_owned()
            } else if line.contains("FP activation") {
                "FP activation=1\n".to_owned()
            } else if line.contains("sugb_fs") {
                "FS licensed=1\n".to_owned()
            } else if line.contains("blc_fs") {
                "FS absent\n".to_owned()
            } else {
                String::new()
            })
        }
    }

    fn spec() -> SiteSpec {
        aib_cloud::site::spec_from_record(&serde_json::json!({
            "site": "site9", "ct": 119, "ip": "10.77.0.19", "hosts": "www.example.com",
            "wp": 1, "locale": "en_US", "created": "2026-01-01T00:00:00Z", "template": 900,
            "limits": { "cores": 2, "cpulimit_x100": 200, "memory_mb": 1024, "swap_mb": 512, "pids_max": 512 }
        }))
        .unwrap()
    }

    #[test]
    fn every_paid_plugin_is_asked_and_a_site_that_does_not_answer_is_unreadable() {
        let mut site = Site {
            up: true,
            calls: Vec::new(),
        };
        let got = ask_all(&mut site, &spec(), Some("TEMPLATEKEY")).unwrap();
        assert_eq!(
            got,
            [
                ("seopress", VendorAnswer::Active),
                ("fluentformpro", VendorAnswer::Active),
                ("flyingpress", VendorAnswer::Active),
                ("stackable", VendorAnswer::Active),
                ("blocksy", VendorAnswer::NoData),
            ]
        );
        // the key is compared in memory: it is never in a command
        assert!(!site.calls.iter().any(|c| c.contains("TEMPLATEKEY")));
        // another template key: not ours to give back, and the vendor is not asked
        let mut site = Site {
            up: true,
            calls: Vec::new(),
        };
        let got = ask_all(&mut site, &spec(), Some("OTHER")).unwrap();
        assert_eq!(got[0], ("seopress", VendorAnswer::KeyNotTemplate));
        assert!(!site.calls.iter().any(|c| c.contains("check_license\",\"license\"")));
        let got = ask_all(&mut site, &spec(), None).unwrap();
        assert_eq!(got[0], ("seopress", VendorAnswer::KeyNotTemplate));
        // the site does not answer at all: unreadable, nothing else asked
        let mut down = Site {
            up: false,
            calls: Vec::new(),
        };
        assert_eq!(ask_all(&mut down, &spec(), Some("TEMPLATEKEY")), None);
        assert_eq!(down.calls.len(), 1);
        // the table and this file name the same plugins
        for p in PAID_PLUGINS {
            assert!(ask_one(&mut site, &spec(), p.name, None).is_some(), "{}", p.name);
        }
        assert_eq!(ask_one(&mut site, &spec(), "someother", None), None);
    }

    #[test]
    fn only_the_plugins_given_back_in_the_site_are_given_back() {
        let mut site = Site {
            up: true,
            calls: Vec::new(),
        };
        for p in PAID_PLUGINS {
            let (ok, _) = give_back(&mut site, &spec(), p.name, Some("TEMPLATEKEY"));
            assert_eq!(ok, give_back_of(p.name) == Some(GiveBack::InSite), "{}", p.name);
        }
        let (ok, now) = give_back(&mut site, &spec(), "fluentformpro", None);
        assert_eq!((ok, now), (true, VendorAnswer::NotActive));
    }
}
