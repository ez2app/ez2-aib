// SPDX-License-Identifier: Apache-2.0
//! `aib-cloud-host site brand-check <code> [--records dir] [--json]` — the host's half of the
//! brand scan (E9 step 4; plan `PLAN-brand-toggle-e9-ebb36-1111` §2 / §4 P1). Reads the site
//! and hands every piece to [`aib_cloud::brandscan::scan`], which alone decides.
//!
//! * **Read only.** One `wp eval` inside the site's container, as the site's own user, with a
//!   fixed program (no argument from outside reaches it). It reads the notice addresses, the
//!   administrators' names and addresses, the must-use plugins' file names and headers, and
//!   the names of options and scheduled hooks that hold a brand word. Nothing is written,
//!   nothing secret is read (no option value other than the two addresses).
//! * **This slice reads B1, B2, B3 and B10.** The footprint loader's mark (B4), the health
//!   path (B6), the mail headers (B7), the responses (B8) and the page output (B9) are not
//!   read yet, so they are not handed in and the answer is "incomplete" with those places
//!   named — never "clean" (a place not looked at is not shown clean).
//! * **The site is the customer's**, and so is what it answers: the output is capped
//!   ([`crate::sitecreatecmd::CT_OUT_MAX`]), the one line after [`MARK`] is the answer (a
//!   second such line makes the whole read unreadable), and
//!   nothing read is printed — the report names places and words only.
//! * Option and hook names can be many: the program lists only those holding one of
//!   [`BRAND_WORDS`] (the same test `brandscan` applies to internal names, where the
//!   whole-word list does not count).
//!
//! Exit status: 0 not hidden or clean; 1 dirty; 3 incomplete (a place not read); 2 the
//! check could not be made (arguments, the record).

use std::fs;

use aib_cloud::brand::{record_brand, BRAND_WORDS};
use aib_cloud::brandscan::{scan, Hit, Place, Scan, Seen};
use aib_cloud::site::{spec_from_record_strict, SiteSpec};
use serde_json::{json, Value};

use crate::sitecreatecmd::{HostRunner, Runner};
use crate::siteidentity::wp;

const RECORDS: &str = "/var/lib/ez2-cloud/sites";

/// What precedes the one line of the answer: whatever else the site prints is not read.
pub const MARK: &str = "@@brand-scan@@";

/// The places this slice reads (the rest are not handed in, see the module text).
pub const READ: [Place; 5] = [
    Place::AdminMail,
    Place::AdminUser,
    Place::MuPluginFile,
    Place::PluginHeader,
    Place::InternalName,
];

/// The words as a PHP list. Each is checked to be letters and digits only, so nothing in it
/// can leave the string it is put in.
fn php_words() -> Result<String, String> {
    let mut parts = Vec::new();
    for w in BRAND_WORDS {
        if w.is_empty()
            || !w
                .bytes()
                .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
        {
            return Err(format!("brand word {w:?} is not letters and digits"));
        }
        parts.push(format!("'{w}'"));
    }
    Ok(format!("array({})", parts.join(",")))
}

/// The program `wp eval` runs. Fixed apart from the brand words.
pub fn program() -> Result<String, String> {
    let words = php_words()?;
    Ok(format!(
        r#"require_once ABSPATH . 'wp-admin/includes/plugin.php';
global $wpdb;
$w = {words};
$o = array('mail' => array(), 'users' => array(), 'mu' => array(), 'internal' => array());
foreach (array('admin_email', 'new_admin_email') as $k) {{ $o['mail'][] = (string) get_option($k, ''); }}
foreach (get_users(array('role' => 'administrator')) as $u) {{
  $o['mail'][] = (string) $u->user_email;
  foreach (array($u->user_login, $u->user_nicename, $u->display_name, $u->user_url,
    get_user_meta($u->ID, 'nickname', true), get_user_meta($u->ID, 'description', true)) as $v) {{
    $o['users'][] = (string) $v;
  }}
}}
foreach (get_mu_plugins() as $f => $h) {{
  $o['mu'][] = array('file' => (string) $f, 'header' => array((string) $h['Name'],
    (string) $h['PluginURI'], (string) $h['Description'], (string) $h['Author'], (string) $h['AuthorURI']));
}}
foreach ($wpdb->get_col("SELECT option_name FROM {{$wpdb->options}}") as $n) {{
  foreach ($w as $x) {{ if (stripos($n, $x) !== false) {{ $o['internal'][] = 'option ' . $n; break; }} }}
}}
foreach ((array) _get_cron_array() as $t => $hs) {{
  foreach ((array) $hs as $n => $v) {{
    foreach ($w as $x) {{ if (stripos($n, $x) !== false) {{ $o['internal'][] = 'hook ' . $n; break; }} }}
  }}
}}
echo "\n{MARK}" . wp_json_encode($o) . "\n";"#
    ))
}

/// The pieces read, by place, from the program's output. `Err` when the answer is not there
/// or not in the expected shape (then every place it would have given is unreadable).
pub fn parse(out: &str) -> Result<Vec<(Place, String)>, String> {
    let mut lines = out.lines().filter_map(|l| l.strip_prefix(MARK));
    let line = lines.next().ok_or("no answer line")?;
    if lines.next().is_some() {
        return Err("more than one answer line".to_owned());
    }
    let v: Value = serde_json::from_str(line).map_err(|_| "the answer is not JSON".to_owned())?;
    let strings = |key: &str| -> Result<Vec<String>, String> {
        v.get(key)
            .and_then(Value::as_array)
            .ok_or_else(|| format!("{key}: not a list"))?
            .iter()
            .map(|s| {
                s.as_str()
                    .map(str::to_owned)
                    .ok_or_else(|| format!("{key}: not text"))
            })
            .collect()
    };
    let mut got = Vec::new();
    for s in strings("mail")? {
        got.push((Place::AdminMail, s));
    }
    for s in strings("users")? {
        got.push((Place::AdminUser, s));
    }
    for m in v
        .get("mu")
        .and_then(Value::as_array)
        .ok_or("mu: not a list")?
    {
        let file = m.get("file").and_then(Value::as_str).ok_or("mu: no file")?;
        got.push((Place::MuPluginFile, file.to_owned()));
        for h in m
            .get("header")
            .and_then(Value::as_array)
            .ok_or("mu: no header")?
        {
            got.push((
                Place::PluginHeader,
                h.as_str().ok_or("mu: header not text")?.to_owned(),
            ));
        }
    }
    for s in strings("internal")? {
        got.push((Place::InternalName, s));
    }
    Ok(got)
}

/// Read the site and scan it.
pub fn check(
    runner: &mut dyn Runner,
    rec: &Value,
    code: &str,
) -> Result<(Scan, Option<String>), String> {
    let brand = record_brand(rec).map_err(|e| format!("record brand: {e}"))?;
    let spec: SiteSpec = spec_from_record_strict(rec).map_err(|e| format!("record: {e:?}"))?;
    if spec.code != code {
        return Err(format!("the record is not for {code}"));
    }
    if !spec.wp {
        return Err("the site has no site software to read".to_owned());
    }
    let read = runner
        .run(&wp(&spec, &["eval", &program()?]))
        .and_then(|out| parse(&out));
    let (pieces, why) = match read {
        Ok(p) => (p, None),
        Err(e) => (Vec::new(), Some(e)),
    };
    let mut seen: Vec<Seen<'_>> = Vec::new();
    // every place this slice reads is handed in once even when empty: "read, nothing there"
    let empty = if why.is_some() { None } else { Some("") };
    for place in READ {
        seen.push(Seen { place, text: empty });
    }
    for (place, text) in &pieces {
        seen.push(Seen {
            place: *place,
            text: Some(text.as_str()),
        });
    }
    Ok((scan(brand, &seen), why))
}

fn hits_json(hits: &[Hit]) -> Value {
    Value::Array(
        hits.iter()
            .map(|h| json!({"place": h.place.as_str(), "word": h.word, "judged": h.judged}))
            .collect(),
    )
}

/// The exit status and what is printed. Places and words only, never what was read.
pub fn report(code: &str, result: &Scan, why: Option<&str>, as_json: bool) -> (u8, String) {
    let (rc, missing, hits): (u8, Vec<Place>, Vec<Hit>) = match result {
        Scan::NotHidden => (0, Vec::new(), Vec::new()),
        Scan::Clean { listed } => (0, Vec::new(), listed.clone()),
        Scan::Dirty { hits } => (1, Vec::new(), hits.clone()),
        Scan::Incomplete { missing, hits } => (3, missing.clone(), hits.clone()),
    };
    let missing_words: Vec<&str> = missing.iter().map(|p| p.as_str()).collect();
    if as_json {
        let v = json!({
            "code": code,
            "result": result.as_str(),
            "missing": missing_words,
            "hits": hits_json(&hits),
            "unread_because": why,
        });
        return (rc, v.to_string());
    }
    let mut out = format!("{code} brand-check {}", result.as_str());
    if !missing_words.is_empty() {
        out.push_str(&format!("\n  not read: {}", missing_words.join(" ")));
    }
    if let Some(w) = why {
        out.push_str(&format!("\n  unread because: {w}"));
    }
    for h in &hits {
        let how = if h.judged { "shows" } else { "listed" };
        out.push_str(&format!("\n  {how} {} in {}", h.word, h.place.as_str()));
    }
    (rc, out)
}

fn run(args: &[String], runner: &mut dyn Runner) -> Result<u8, String> {
    let mut records = RECORDS.to_owned();
    let mut as_json = false;
    let mut code: Option<String> = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--records" => records = it.next().ok_or("--records needs a dir")?.clone(),
            "--json" => as_json = true,
            c if !c.starts_with('-') && code.is_none() => {
                if !aib_cloud::site::valid_code(c) {
                    return Err(format!("site code {c:?} is not valid"));
                }
                code = Some(c.to_owned());
            }
            other => return Err(format!("unknown argument {other}")),
        }
    }
    let code = code.ok_or("site brand-check needs <code>")?;
    let path = format!("{records}/{code}.json");
    let text = fs::read_to_string(&path).map_err(|e| format!("{path}: {e}"))?;
    let rec: Value = serde_json::from_str(&text).map_err(|_| format!("{path}: not JSON"))?;
    let (result, why) = check(runner, &rec, &code)?;
    let (rc, out) = report(&code, &result, why.as_deref(), as_json);
    println!("{out}");
    Ok(rc)
}

/// `site brand-check`. A check that could not be made is exit 2 (the caller prints why).
pub fn cmd(args: &[String]) -> Result<u8, String> {
    run(args, &mut HostRunner)
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Fake {
        out: Result<String, String>,
        calls: Vec<Vec<String>>,
    }

    impl Runner for Fake {
        fn run(&mut self, argv: &[String]) -> Result<String, String> {
            self.calls.push(argv.to_vec());
            self.out.clone()
        }
    }

    fn fake(answer: Value) -> Fake {
        Fake {
            out: Ok(format!("Notice: something\n{MARK}{answer}\n")),
            calls: Vec::new(),
        }
    }

    fn neutral() -> Value {
        json!({
            "mail": ["owner@customer.example.test", "", "owner@customer.example.test"],
            "users": ["owner", "owner", "Shop", "", "owner", ""],
            "mu": [{"file": "cache-clear.php", "header": ["Cache clear", "", "Clears the cache", "", ""]}],
            "internal": [],
        })
    }

    fn record(brand: &str) -> Value {
        let spec = aib_cloud::site::SiteSpec::new(
            "site2",
            112,
            12,
            &["site2.example.net"],
            true,
            "zh_TW",
            aib_cloud::site::Limits::TEST_TIER,
        )
        .unwrap();
        let mut rec = aib_cloud::site::record(&spec, "2026-11-11T00:00:00Z");
        rec["brand"] = json!(brand);
        rec
    }

    fn places(result: &Scan) -> Vec<Place> {
        match result {
            Scan::Incomplete { missing, .. } => missing.clone(),
            _ => Vec::new(),
        }
    }

    const NOT_READ: [Place; 5] = [
        Place::Loader,
        Place::HealthPath,
        Place::MailHeader,
        Place::Response,
        Place::PageOutput,
    ];

    #[test]
    fn a_neutral_site_is_incomplete_only_for_the_places_not_read_yet() {
        let mut f = fake(neutral());
        let (r, why) = check(&mut f, &record("hidden"), "site2").unwrap();
        assert_eq!(why, None);
        assert_eq!(places(&r), NOT_READ.to_vec());
        assert!(matches!(&r, Scan::Incomplete { hits, .. } if hits.is_empty()));
        // one call, inside the container, as the site's own user
        assert_eq!(f.calls.len(), 1);
        let line = f.calls[0].join(" ");
        assert!(line.starts_with("pct exec "), "{line}");
        assert!(line.contains(" eval "), "{line}");
    }

    #[test]
    fn each_place_read_is_scanned() {
        for (key, place) in [
            ("mail", Place::AdminMail),
            ("users", Place::AdminUser),
            ("internal", Place::InternalName),
        ] {
            let mut a = neutral();
            a[key]
                .as_array_mut()
                .unwrap()
                .push(json!("ops@EZ2.example.test"));
            let (r, _) = check(&mut fake(a), &record("hidden"), "site2").unwrap();
            let Scan::Incomplete { hits, .. } = r else {
                panic!("{key}")
            };
            assert_eq!(hits.len(), 1, "{key}");
            assert_eq!(hits[0].place, place, "{key}");
            assert_eq!(hits[0].judged, place != Place::InternalName, "{key}");
        }
        let mut a = neutral();
        a["mu"] = json!([{"file": "aib-loader.php", "header": ["Loader", "", "", "Ez2 team", ""]}]);
        let (r, _) = check(&mut fake(a), &record("hidden"), "site2").unwrap();
        let Scan::Incomplete { hits, .. } = r else {
            panic!()
        };
        let got: Vec<(Place, &str)> = hits.iter().map(|h| (h.place, h.word)).collect();
        assert_eq!(
            got,
            vec![(Place::MuPluginFile, "aib"), (Place::PluginHeader, "ez2")]
        );
    }

    #[test]
    fn a_failed_read_makes_every_place_unread_never_clean() {
        let mut f = Fake {
            out: Err("timed out".to_owned()),
            calls: Vec::new(),
        };
        let (r, why) = check(&mut f, &record("hidden"), "site2").unwrap();
        assert_eq!(why.as_deref(), Some("timed out"));
        assert_eq!(places(&r), Place::ALL.to_vec());
        for bad in [
            "no mark here\n",
            "@@brand-scan@@{not json\n",
            "@@brand-scan@@{\"mail\":[1]}\n",
        ] {
            let mut f = Fake {
                out: Ok(bad.to_owned()),
                calls: Vec::new(),
            };
            let (r, why) = check(&mut f, &record("hidden"), "site2").unwrap();
            assert!(why.is_some(), "{bad}");
            assert_eq!(places(&r), Place::ALL.to_vec(), "{bad}");
        }
    }

    #[test]
    fn a_second_answer_line_makes_the_read_unreadable() {
        // the site prints a line of its own that looks like a clean answer
        let mut a = neutral();
        a["mail"] = json!(["ops@ez2.example.test"]);
        let out = format!("{MARK}{}\n{MARK}{a}\n", neutral());
        let mut f = Fake {
            out: Ok(out),
            calls: Vec::new(),
        };
        let (r, why) = check(&mut f, &record("hidden"), "site2").unwrap();
        assert_eq!(why.as_deref(), Some("more than one answer line"));
        assert_eq!(places(&r), Place::ALL.to_vec());
    }

    #[test]
    fn a_record_for_another_site_is_not_checked() {
        let mut f = fake(neutral());
        assert!(check(&mut f, &record("hidden"), "site3").is_err());
        assert!(f.calls.is_empty());
    }

    #[test]
    fn a_shown_site_is_read_but_not_judged() {
        let mut a = neutral();
        a["mail"] = json!(["ops@ez2.example.test"]);
        let (r, _) = check(&mut fake(a), &record("shown"), "site2").unwrap();
        assert_eq!(r, Scan::NotHidden);
        let (rc, out) = report("s-abc123", &r, None, false);
        assert_eq!((rc, out.as_str()), (0, "s-abc123 brand-check not_hidden"));
    }

    #[test]
    fn the_report_names_places_and_words_never_what_was_read() {
        let mut a = neutral();
        a["mail"] = json!(["ops@ez2.example.test"]);
        let (r, why) = check(&mut fake(a), &record("hidden"), "site2").unwrap();
        for as_json in [false, true] {
            let (rc, out) = report("s-abc123", &r, why.as_deref(), as_json);
            assert_eq!(rc, 3);
            assert!(!out.contains("ops@"), "{out}");
            assert!(out.contains("admin_mail"), "{out}");
            assert!(out.contains("health_path"), "{out}");
        }
        let dirty = Scan::Dirty {
            hits: vec![Hit {
                place: Place::AdminUser,
                word: "ez2",
                judged: true,
            }],
        };
        assert_eq!(report("s-abc123", &dirty, None, false).0, 1);
        assert_eq!(
            report("s-abc123", &Scan::Clean { listed: Vec::new() }, None, false).0,
            0
        );
    }

    #[test]
    fn the_program_takes_nothing_from_outside_and_lists_by_the_brand_words() {
        let p = program().unwrap();
        assert!(p.contains("$w = array('ez2');"), "{p}");
        assert!(p.contains(MARK));
        // no value of an option other than the two addresses is read
        assert_eq!(p.matches("get_option(").count(), 1, "{p}");
        assert!(p.contains("array('admin_email', 'new_admin_email')"));
    }

    #[test]
    fn a_bad_record_or_a_site_without_software_is_not_checked() {
        let mut rec = record("hidden");
        rec["brand"] = json!("Hidden");
        assert!(check(&mut fake(neutral()), &rec, "site2").is_err());
        let mut rec = record("hidden");
        rec["wp"] = json!(0);
        let mut f = fake(neutral());
        assert!(check(&mut f, &rec, "site2").is_err());
        assert!(f.calls.is_empty());
    }

    #[test]
    fn arguments() {
        let mut f = fake(neutral());
        let a = |v: &[&str]| v.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>();
        assert!(run(&a(&[]), &mut f).is_err());
        assert!(run(&a(&["../x"]), &mut f).is_err());
        assert!(run(&a(&["s-abc123", "--bogus"]), &mut f).is_err());
        assert!(run(&a(&["site2", "--records", "/nonexistent/aib-bc"]), &mut f).is_err());
        assert!(f.calls.is_empty());
    }
}
