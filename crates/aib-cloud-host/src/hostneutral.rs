// SPDX-License-Identifier: Apache-2.0
//! The must-load plugin that keeps the site software's own health screen (Tools → Site
//! Health → Info, and `wp site-health` / the REST answer built from the same data) from
//! naming the machine under the site (E6 S6, PLAN-e6 B1). Put in place at the identity step
//! of an opening, next to the one-time login's plugin, and read back.
//!
//! It filters `debug_information`: the server section's architecture (`php_uname`: the
//! kernel's release names the platform) and web server software (its name and version)
//! are taken out, and so is any other field whose text (its `value` or its `debug`, the
//! copy-to-clipboard form, a list read to the end) holds the kernel's release. Inside the
//! container PHP's own name for the machine is the site's code (the mask sets it), so the
//! paths and names that hold the code stay; only where that name is not the site's code
//! (a site whose mask did not take) is it the machine's, and then fields holding it are
//! taken out too. The site's code is the last part of `ABSPATH`, so every site gets the
//! same file byte for byte. It prints nothing and changes no setting. What an
//! administrator can still read another way (`phpinfo()`, a plugin of their own, a shell)
//! is not covered: `aib_cloud::mask::CANNOT_HIDE` says so.

use aib_cloud::site::SiteSpec;

use crate::loginlink::{as_site, LOOK, WRITE};
use crate::sitecreatecmd::Runner;

/// The plugin, byte for byte what the site gets.
pub const PLUGIN: &str = r#"<?php
/* Ez2 managed hosting (E6 S6): the site health screen does not name the machine under the site. */
add_filter('debug_information', function ($info) {
    if (!is_array($info)) { return $info; }
    if (isset($info['wp-server']['fields']) && is_array($info['wp-server']['fields'])) {
        unset($info['wp-server']['fields']['server_architecture'], $info['wp-server']['fields']['httpd_software']);
    }
    $words = array(php_uname('r'));
    $site = defined('ABSPATH') ? basename(rtrim(ABSPATH, '/')) : '';
    $node = php_uname('n');
    if ($node !== '' && strcasecmp($node, $site) !== 0) { $words[] = $node; }
    $words = array_filter($words, 'strlen');
    foreach ($info as $s => $section) {
        if (!is_array($section) || !isset($section['fields']) || !is_array($section['fields'])) { continue; }
        foreach ($section['fields'] as $k => $field) {
            if (!is_array($field)) { continue; }
            $text = '';
            foreach (array('value', 'debug') as $part) {
                if (!isset($field[$part])) { continue; }
                $v = $field[$part];
                if (is_array($v)) {
                    array_walk_recursive($v, function ($x) use (&$text) { if (is_scalar($x)) { $text .= ' ' . $x; } });
                } elseif (is_scalar($v)) {
                    $text .= ' ' . $v;
                }
            }
            foreach ($words as $w) {
                if (stripos($text, $w) !== false) { unset($info[$s]['fields'][$k]); break; }
            }
        }
    }
    return $info;
}, PHP_INT_MAX);
"#;

/// The plugin's file name in the site's must-load folder.
pub const FILE: &str = "ez2-host-neutral.php";

/// The plugin file in the site.
pub fn path(code: &str) -> String {
    format!("{}/{FILE}", crate::loginlink::mu_plugin_dir(code))
}

/// The plugin in place: written when none is there and read back; the same one is left as
/// it is; another file of that name stops (`host_plugin_differs`). Answers `written` or
/// `same`.
pub fn ensure(runner: &mut impl Runner, spec: &SiteSpec) -> Result<&'static str, String> {
    let sha = crate::opstore::sha256_hex(PLUGIN.as_bytes())?;
    let file = path(&spec.code);
    let said = runner
        .run(&as_site(spec, &["sh", "-c", LOOK, "sh", &file]))
        .map_err(|e| format!("host_plugin_unchecked: {e}"))?;
    match said.split_whitespace().next() {
        Some("none") => {}
        Some(s) if s == sha => return Ok("same"),
        Some(s) if s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit()) => {
            return Err(format!(
                "host_plugin_differs: another {file} is in the site; it was left alone"
            ))
        }
        _ => {
            return Err("host_plugin_unchecked: the site's answer is not a fingerprint".to_owned())
        }
    }
    let dir = crate::loginlink::mu_plugin_dir(&spec.code);
    runner
        .run_input(
            &as_site(spec, &["sh", "-c", WRITE, "sh", &dir, &file]),
            PLUGIN.as_bytes(),
        )
        .map_err(|e| format!("host_plugin_not_written: {e}"))?;
    let back = runner
        .run(&as_site(spec, &["sha256sum", &file]))
        .map_err(|e| format!("host_plugin_not_written: read back: {e}"))?;
    if back.split_whitespace().next() != Some(sha.as_str()) {
        return Err("host_plugin_not_written: it reads back otherwise".to_owned());
    }
    Ok("written")
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;
    use std::process::Command;

    #[test]
    fn the_health_filter_names_no_platform() {
        // the filter is there, on the health screen's data, last of all
        assert!(PLUGIN.starts_with("<?php\n"));
        assert!(PLUGIN.contains("add_filter('debug_information',"));
        assert!(PLUGIN.contains("}, PHP_INT_MAX);"));
        // it takes out the architecture (kernel release) and the web server software
        assert!(PLUGIN.contains("['server_architecture']"));
        assert!(PLUGIN.contains("['httpd_software']"));
        assert!(PLUGIN.contains("$words = array(php_uname('r'));"));
        // both forms of a field are read, a list to the end
        assert!(PLUGIN.contains("array('value', 'debug')"));
        assert!(PLUGIN.contains("array_walk_recursive("));
        // it says nothing itself: no output, no setting, no name of a platform or vendor
        for word in [
            "echo",
            "print",
            "update_option",
            "header(",
            "pve",
            "PVE",
            "vendor-x",
            "ts.net",
        ] {
            assert!(!PLUGIN.contains(word), "{word}");
        }
        assert!(path("s-abc123").ends_with("/wp-content/mu-plugins/ez2-host-neutral.php"));
        // the PHP parses, where a PHP is installed (the CI image may have none)
        let php = Command::new("php").arg("-v").output();
        if php.is_ok_and(|o| o.status.success()) {
            let f =
                std::env::temp_dir().join(format!("ez2-host-neutral-{}.php", std::process::id()));
            std::fs::write(&f, PLUGIN).unwrap();
            let out = Command::new("php").arg("-l").arg(&f).output().unwrap();
            let _ = std::fs::remove_file(&f);
            assert!(
                out.status.success(),
                "{}",
                String::from_utf8_lossy(&out.stdout)
            );
        } else {
            eprintln!("php not installed: php -l skipped");
        }
    }

    /// Runs the plugin's filter in PHP on a health screen whose site lives in
    /// `/var/www/<site>/` (`=`: PHP's own name for the machine), and answers the fields
    /// left in each section; `None` where no PHP is installed.
    fn filtered(site: &str) -> Option<String> {
        let php = Command::new("php").arg("-v").output();
        if !php.is_ok_and(|o| o.status.success()) {
            return None;
        }
        let dir = std::env::temp_dir().join(format!("ez2-host-neutral-run-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let plugin = dir.join("plugin.php");
        let run = dir.join("run.php");
        std::fs::write(&plugin, PLUGIN).unwrap();
        std::fs::write(&run, RUN).unwrap();
        let out = Command::new("php")
            .arg(&run)
            .arg(&plugin)
            .arg(site)
            .output()
            .unwrap();
        let _ = std::fs::remove_dir_all(&dir);
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
        Some(String::from_utf8_lossy(&out.stdout).into_owned())
    }

    /// A health screen as the site software builds it, with the site in `/var/www/<argv 2>/`
    /// (`=`: PHP's name for the machine, as in a masked container), its paths named the way
    /// PHP names the machine.
    const RUN: &str = r#"<?php
define('ABSPATH', '/var/www/' . ($argv[2] === '=' ? php_uname('n') : $argv[2]) . '/');
function add_filter($hook, $f, $order) { $GLOBALS['f'] = $f; }
require $argv[1];
$node = php_uname('n');
$info = array(
    'wp-paths-sizes' => array('fields' => array(
        'wordpress_path' => array('label' => 'path', 'value' => '/var/www/' . $node),
        'uploads_path' => array('label' => 'uploads', 'value' => '/var/www/' . $node . '/wp-content/uploads', 'debug' => '/var/www/' . $node . '/wp-content/uploads'),
    )),
    'wp-server' => array('fields' => array(
        'server_architecture' => array('value' => 'x'),
        'httpd_software' => array('value' => 'y'),
        'kernel_in_debug' => array('value' => 'Linux', 'debug' => php_uname('r')),
        'kernel_in_list' => array('value' => array('a' => array('b' => php_uname('r')))),
        'php_version' => array('value' => '8.3'),
    )),
);
$out = call_user_func($GLOBALS['f'], $info);
foreach ($out as $s => $section) { fwrite(STDOUT, $s . ':' . implode(',', array_keys($section['fields'])) . "\n"); }
"#;

    #[test]
    fn a_row_holding_the_site_code_stays() {
        // the machine's name is only a word to take out where it is not the site's code
        assert!(PLUGIN
            .contains("if ($node !== '' && strcasecmp($node, $site) !== 0) { $words[] = $node; }"));
        assert!(PLUGIN.contains("basename(rtrim(ABSPATH, '/'))"));
        assert!(!PLUGIN.contains("gethostname()"));
        let Some(same) = filtered("=") else {
            eprintln!("php not installed: the filter was not run, only read");
            return;
        };
        // the site's code is PHP's name for the machine: its paths stay; the kernel's
        // release goes, in `debug` and deep in a list as well as in `value`
        assert!(
            same.contains("wp-paths-sizes:wordpress_path,uploads_path\n"),
            "{same}"
        );
        assert!(same.contains("wp-server:php_version\n"), "{same}");
        // a site whose mask did not take: PHP's name is the machine's, and it goes
        let other = filtered("s-xyz000").unwrap();
        assert!(other.contains("wp-paths-sizes:\n"), "{other}");
        assert!(other.contains("wp-server:php_version\n"), "{other}");
    }

    /// A site that answers the plugin's look, write and read-back.
    struct Site {
        file: Option<Vec<u8>>,
        written_as: Option<Vec<u8>>,
        calls: Vec<String>,
    }

    impl Runner for Site {
        fn run(&mut self, argv: &[String]) -> Result<String, String> {
            self.calls.push(argv.join(" "));
            let sha = self
                .file
                .as_ref()
                .map(|b| format!("{}  x\n", crate::opstore::sha256_hex(b).unwrap()));
            Ok(sha.unwrap_or_else(|| "none\n".to_owned()))
        }
        fn run_input(&mut self, argv: &[String], input: &[u8]) -> Result<String, String> {
            self.calls.push(argv.join(" "));
            self.file = Some(self.written_as.clone().unwrap_or_else(|| input.to_vec()));
            Ok(String::new())
        }
    }

    fn spec() -> SiteSpec {
        SiteSpec::new(
            "s-abc123",
            131,
            41,
            &["new.example.test"],
            true,
            "zh_TW",
            aib_cloud::site::Limits::START,
        )
        .unwrap()
    }

    #[test]
    fn the_plugin_is_put_in_place_once_and_read_back() {
        let mut site = Site {
            file: None,
            written_as: None,
            calls: Vec::new(),
        };
        assert_eq!(ensure(&mut site, &spec()).unwrap(), "written");
        assert_eq!(site.file.as_deref(), Some(PLUGIN.as_bytes()));
        assert!(site.calls[1].starts_with("pct exec 131 -- sudo -u s-abc123 sh -c "));
        assert_eq!(ensure(&mut site, &spec()).unwrap(), "same");
        // another file of that name is left alone
        site.file = Some(b"<?php // the customer's own\n".to_vec());
        let e = ensure(&mut site, &spec()).unwrap_err();
        assert!(e.starts_with("host_plugin_differs"), "{e}");
        // one that reads back otherwise is not in place
        let mut bad = Site {
            file: None,
            written_as: Some(b"cut".to_vec()),
            calls: Vec::new(),
        };
        let e = ensure(&mut bad, &spec()).unwrap_err();
        assert!(e.starts_with("host_plugin_not_written"), "{e}");
    }
}
