// SPDX-License-Identifier: Apache-2.0
//! Each site's own visitor-statistics key (33e28 design DESIGN-fp-site-key-per-site, 11-11).
//! The template carries a loader that holds the template's own key; every site cloned from
//! it, and every site restored from another site's backup, carries someone else's key until
//! the loader is replaced. This module holds the pure rules of that one step; no I/O.
//!
//! The step, run by the host when a site is opened and again after every restore:
//! 1. ask the statistics server for the site's key (`POST /cloud/site {domain, site}`; the
//!    same domain always gets the same key back) and read the answer with [`key_reply`];
//! 2. write the whole loader file with [`loader_file`] (the file is **replaced**, never
//!    added to: run again it cannot leave two loaders, and the template's is gone);
//! 3. read the file back and judge it with [`loader_probe`];
//! 4. the outside check (`fploader`, F2; not in this tree yet) is what counts in the end.
//!
//! A domain the statistics server already holds for another account is a stop, never
//! taken over ([`KeyReply::Conflict`]). Anything that cannot be read is "could not tell".

use alloc::string::String;
use serde_json::Value;

use crate::sitecreate::Probe;

/// Where the loader lives inside the site, relative to the WordPress root. A must-use
/// plugin, so it runs whatever plugins the customer switches off.
pub const LOADER_PATH: &str = "wp-content/mu-plugins/ez2-fp-loader.php";

/// The first line after `<?php` of every loader file we write; the site's code follows.
pub const LOADER_MARK: &str = "// ez2:fp-loader site=";

/// The statistics server's answer for one site.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum KeyReply {
    /// The site's key and the address of its loader.
    Key {
        site_key: String,
        script_url: String,
    },
    /// The domain is held by another account on the statistics server: stop, a person looks.
    Conflict,
    /// Could not tell (no answer, refused, an answer that does not read right).
    Unknown(&'static str),
}

/// A key as the statistics server makes them: letters, digits, `_` and `-`, 8 to 64 long.
pub fn key_ok(key: &str) -> bool {
    (8..=64).contains(&key.len())
        && key
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

/// The loader address of `key`: `https://`, a host, a path that ends in `/<key>.js`, and
/// nothing that could break out of the quoted attribute it is written into (only letters,
/// digits and `:/._-`).
pub fn script_url_ok(url: &str, key: &str) -> bool {
    let Some(rest) = url.strip_prefix("https://") else {
        return false;
    };
    let tail = format!("/{key}.js");
    let host = rest.split('/').next().unwrap_or("");
    url.len() <= 512
        && !host.is_empty()
        && rest.len() > host.len()
        && rest.ends_with(&tail)
        && url
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b":/._-".contains(&b))
}

/// The answer to `POST /cloud/site`: its HTTP status and its body read as JSON (`None` when
/// there was no answer or the body was not JSON).
pub fn key_reply(status: u16, body: Option<&Value>) -> KeyReply {
    match status {
        409 => KeyReply::Conflict,
        200 => {
            let Some(b) = body else {
                return KeyReply::Unknown("reply_not_json");
            };
            if b.get("ok").and_then(Value::as_u64) != Some(1) {
                return KeyReply::Unknown("reply_not_ok");
            }
            let key = b.get("site_key").and_then(Value::as_str).unwrap_or("");
            let url = b.get("script_url").and_then(Value::as_str).unwrap_or("");
            if !key_ok(key) {
                return KeyReply::Unknown("reply_bad_key");
            }
            if !script_url_ok(url, key) {
                return KeyReply::Unknown("reply_bad_script_url");
            }
            KeyReply::Key {
                site_key: key.into(),
                script_url: url.into(),
            }
        }
        _ => KeyReply::Unknown("reply_status"),
    }
}

/// A site code as written into the loader's mark: lowercase letters, digits and `-`.
fn code_ok(code: &str) -> bool {
    (1..=40).contains(&code.len())
        && code
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

/// The whole loader file for site `code` whose loader address is `script_url`, or `None`
/// when either does not read right (nothing is written then). One script tag, printed
/// early in the page head; nothing else.
pub fn loader_file(code: &str, script_url: &str) -> Option<String> {
    let key = script_url.rsplit('/').next()?.strip_suffix(".js")?;
    if !code_ok(code) || !key_ok(key) || !script_url_ok(script_url, key) {
        return None;
    }
    Some(format!(
        "<?php\n{LOADER_MARK}{code}\n// Written by the host; the whole file is replaced each time. Do not edit.\nadd_action('wp_head', function () {{ echo '<script async src=\"{script_url}\"></script>' . \"\\n\"; }}, 1);\n"
    ))
}

/// The loader step from the file read back: `None` could not be read; exactly what
/// [`loader_file`] makes for this site is done; anything else (missing, the template's,
/// another site's, edited) is not done, and running the step again puts it right.
pub fn loader_probe(read_back: Option<Option<&str>>, want: &str) -> Probe {
    match read_back {
        None => Probe::Unknown,
        Some(Some(text)) if text == want => Probe::Done,
        Some(_) => Probe::NotDone,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const KEY: &str = "k_abcDEF123";
    const URL: &str = "https://fp.example.test/e2/k_abcDEF123.js";

    #[test]
    fn a_good_reply_gives_the_key_and_the_loader_address() {
        let b = json!({ "ok": 1, "created": true, "site_key": KEY, "script_url": URL });
        assert_eq!(
            key_reply(200, Some(&b)),
            KeyReply::Key {
                site_key: KEY.into(),
                script_url: URL.into()
            }
        );
        // asked again for the same domain: the same key, not created
        let b = json!({ "ok": 1, "created": false, "site_key": KEY, "script_url": URL });
        assert!(matches!(key_reply(200, Some(&b)), KeyReply::Key { .. }));
    }

    #[test]
    fn a_domain_held_by_another_account_stops() {
        assert_eq!(key_reply(409, None), KeyReply::Conflict);
        assert_eq!(
            key_reply(409, Some(&json!({ "ok": 0, "reason": "conflict" }))),
            KeyReply::Conflict
        );
    }

    #[test]
    fn anything_that_does_not_read_right_is_unknown() {
        let good = json!({ "ok": 1, "site_key": KEY, "script_url": URL });
        for s in [0u16, 400, 401, 403, 404, 429, 500, 502, 204] {
            assert_eq!(
                key_reply(s, Some(&good)),
                KeyReply::Unknown("reply_status"),
                "{s}"
            );
        }
        assert_eq!(key_reply(200, None), KeyReply::Unknown("reply_not_json"));
        assert_eq!(
            key_reply(
                200,
                Some(&json!({ "ok": 0, "site_key": KEY, "script_url": URL }))
            ),
            KeyReply::Unknown("reply_not_ok")
        );
        let long = "k".repeat(65);
        for k in ["", "short", "has space here", "k\"quote12", long.as_str()] {
            let b = json!({ "ok": 1, "site_key": k, "script_url": URL });
            assert_eq!(
                key_reply(200, Some(&b)),
                KeyReply::Unknown("reply_bad_key"),
                "{k}"
            );
        }
        // the loader address must be this key's, on https, with nothing that breaks the tag
        for u in [
            "http://fp.example.test/e2/k_abcDEF123.js",
            "https://fp.example.test/e2/k_other4567.js",
            "https:///k_abcDEF123.js",
            "https://fp.example.test/k_abcDEF123.js\"><script>",
            "https://fp.example.test/e2/k_abcDEF123.js?x=1",
            "https://fp.example.test/e2/xk_abcDEF123.js",
            "https://fp.example.test/e2\"onload=x/k_abcDEF123.js",
            "",
        ] {
            let b = json!({ "ok": 1, "site_key": KEY, "script_url": u });
            assert_eq!(
                key_reply(200, Some(&b)),
                KeyReply::Unknown("reply_bad_script_url"),
                "{u}"
            );
        }
    }

    #[test]
    fn the_loader_file_is_one_tag_for_this_sites_key() {
        let f = loader_file("site9", URL).unwrap();
        assert!(f.starts_with("<?php\n// ez2:fp-loader site=site9\n"), "{f}");
        assert_eq!(f.matches("<script").count(), 1);
        assert!(f.contains(&format!("src=\"{URL}\"")));
        assert!(f.ends_with("}, 1);\n"));
        // the same input always gives the same file (so reading back can compare it)
        assert_eq!(loader_file("site9", URL), Some(f));
        // nothing is made from what does not read right
        assert_eq!(loader_file("Site9", URL), None);
        assert_eq!(loader_file("", URL), None);
        assert_eq!(loader_file("site9;rm", URL), None);
        assert_eq!(
            loader_file("site9", "https://fp.example.test/e2/'x'.js"),
            None
        );
        assert_eq!(
            loader_file("site9", "http://fp.example.test/e2/k_abcDEF123.js"),
            None
        );
        assert_eq!(
            loader_file("site9", "https://fp.example.test/e2/k_abcDEF123"),
            None
        );
    }

    #[test]
    fn only_the_file_we_make_for_this_site_is_done() {
        let want = loader_file("site9", URL).unwrap();
        let want = want.as_str();
        assert_eq!(loader_probe(Some(Some(want)), want), Probe::Done);
        // the template's loader (another key), another site's, an edited one, none at all
        let template = loader_file("tpl", "https://fp.example.test/e2/k_template01.js").unwrap();
        assert_eq!(
            loader_probe(Some(Some(template.as_str())), want),
            Probe::NotDone
        );
        let other = loader_file("site8", URL).unwrap();
        assert_eq!(
            loader_probe(Some(Some(other.as_str())), want),
            Probe::NotDone
        );
        let edited = format!("{want}// added\n");
        assert_eq!(
            loader_probe(Some(Some(edited.as_str())), want),
            Probe::NotDone
        );
        assert_eq!(loader_probe(Some(None), want), Probe::NotDone);
        // could not read it: could not tell
        assert_eq!(loader_probe(None, want), Probe::Unknown);
    }
}
