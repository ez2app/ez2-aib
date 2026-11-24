//! The plugin area on the built-in page (PLAN-skipstop-master H1b, the page's half):
//! `POST /api/v1/act` with
//!
//! - `{"type":"plugin_list"}`: every folder under `<AIB home>/plugins`, with its name, version,
//!   one line, whether it is switched on, and whether its command can be run now (`runs`: `ok`
//!   or the fixed word of [`aib_plugin::No`]). A folder whose file is not understood is listed
//!   with `problem`, never left out. Read only.
//! - `{"type":"plugin_set","name":…,"on":true|false}`: the user's switch for one installed
//!   plugin ([`aib_plugin::set_switch`]: under a lock, written beside and renamed). From the
//!   built-in page in this pairing only. The answer is the list after the change.
//!
//! A plugin is on by default when its file says so; the switch is the user's and wins. Nothing
//! here installs, removes or runs a plugin.

use std::path::{Path, PathBuf};

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};

pub const LIST: &str = "plugin_list";
pub const SET: &str = "plugin_set";
pub const MARKETPLACE_LIST: &str = "marketplace_list";
pub const MARKETPLACE_INSTALL: &str = "marketplace_install";

/// The list the page is shown, from the plugin area under `home`.
pub fn list(home: &Path, windows: bool) -> Value {
    let dir = aib_plugin::plugins_dir(home);
    let found = match aib_plugin::find(&dir) {
        Ok(f) => f,
        Err(_) => return json!({ "state": "unreadable" }),
    };
    let switches = aib_plugin::read_switches(&aib_plugin::switches_file(home));
    let rows: Vec<Value> = found
        .iter()
        .map(|f| match &f.manifest {
            Err(_) => json!({ "folder": f.folder, "problem": "not_understood" }),
            Ok(m) => {
                let folder = dir.join(&f.folder);
                // can its first command be run now, and if not, the word why
                let runs = match m.commands.first() {
                    None => "no_command",
                    Some(c) => {
                        match aib_plugin::runnable(m, &folder, &switches, &c.name, windows) {
                            Ok(_) => "ok",
                            Err(no) => no.word(),
                        }
                    }
                };
                json!({
                    "name": m.name, "version": m.version, "summary": m.summary,
                    "paid": m.paid, "default_on": m.default_on,
                    // null: the switches file cannot be read, so nothing is taken as on
                    "on": switches.as_ref().ok().map(|s| aib_plugin::switched_on(m, s)),
                    "runs": runs,
                })
            }
        })
        .collect();
    json!({
        "state": "ok",
        "plugins": rows,
        "switches_readable": switches.is_ok(),
        // names in the user's switches that are no installed plugin (a typo turns nothing off)
        "stray": switches.as_ref().map(|s| aib_plugin::stray_switches(s, &found)).unwrap_or_default(),
    })
}

/// What `plugin_set` asks: the plugin's name and the switch. Nothing else may be in the body.
pub fn asked(body: &Value) -> Option<(String, bool)> {
    let o = body.as_object()?;
    if o.keys()
        .any(|k| !matches!(k.as_str(), "type" | "name" | "on"))
    {
        return None;
    }
    let name = o.get("name")?.as_str()?;
    let on = o.get("on")?.as_bool()?;
    aib_plugin::name_ok(name).then(|| (name.to_string(), on))
}

/// Moves the switch of an installed plugin; the list after it, or the status and code.
pub fn set(home: &Path, body: &Value, windows: bool) -> Result<Value, (StatusCode, &'static str)> {
    let (name, on) = asked(body).ok_or((StatusCode::BAD_REQUEST, "bad_plugin_set"))?;
    let found = aib_plugin::find(&aib_plugin::plugins_dir(home))
        .map_err(|_| (StatusCode::CONFLICT, "plugins_unreadable"))?;
    // only a plugin that is installed and understood: a switch for a name that is nothing
    // would look as if it had turned something off
    if !found
        .iter()
        .any(|f| f.manifest.as_ref().is_ok_and(|m| m.name == name))
    {
        return Err((StatusCode::NOT_FOUND, "no_such_plugin"));
    }
    aib_plugin::set_switch(&aib_plugin::switches_file(home), &name, on)
        .map_err(|_| (StatusCode::CONFLICT, "switch_not_written"))?;
    Ok(list(home, windows))
}

fn home_now() -> Option<PathBuf> {
    aib_plugin::home(
        std::env::var("EZ2_AIB_HOME").ok().as_deref(),
        std::env::var_os("HOME")
            .or_else(|| std::env::var_os("USERPROFILE"))
            .map(PathBuf::from)
            .as_deref(),
    )
}

fn refuse(status: StatusCode, code: &str) -> Response {
    (
        status,
        Json(json!({ "code": code, "message": code, "request_id": crate::guard::request_id() })),
    )
        .into_response()
}

/// One look at the plugin area at a time (each one reads and sums every plugin's executable):
/// a page that asks again and again waits its turn, it does not start as many reads.
static ONE: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn turn() -> std::sync::MutexGuard<'static, ()> {
    // a poisoned lock only means an earlier look panicked; the area is read anew each time
    ONE.lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

pub async fn list_act() -> Response {
    let got = tokio::task::spawn_blocking(|| {
        let _turn = turn();
        home_now().map(|h| list(&h, cfg!(windows)))
    })
    .await;
    match got {
        Ok(Some(v)) => Json(v).into_response(),
        _ => refuse(StatusCode::INTERNAL_SERVER_ERROR, "no_home"),
    }
}

pub async fn set_act(body: Value) -> Response {
    let got = tokio::task::spawn_blocking(move || {
        let _turn = turn();
        home_now().map(|h| set(&h, &body, cfg!(windows)))
    })
    .await;
    match got {
        Ok(Some(Ok(v))) => Json(v).into_response(),
        Ok(Some(Err((status, code)))) => refuse(status, code),
        _ => refuse(StatusCode::INTERNAL_SERVER_ERROR, "no_home"),
    }
}

/// Format marketplace entries for the current OS/arch target from catalog JSON.
pub fn marketplace_list_from(
    catalog_text: &str,
    os: &str,
    arch: &str,
) -> Result<Value, (StatusCode, &'static str)> {
    let entries = aib_plugin::marketplace::parse_catalog(catalog_text)
        .map_err(|_| (StatusCode::BAD_GATEWAY, "marketplace_bad_catalog"))?;
    let target = aib_plugin::marketplace::target_triple(os, arch);
    let rows: Vec<Value> = entries
        .into_iter()
        .map(|e| {
            let has_target = target.is_some_and(|t| e.targets.contains_key(t));
            json!({
                "name": e.name,
                "version": e.version,
                "summary": e.summary,
                "paid": e.paid,
                "default_on": e.default_on,
                "supported": has_target,
            })
        })
        .collect();
    Ok(json!({ "state": "ok", "target": target, "plugins": rows }))
}

/// What `marketplace_install` asks: only `type` and `name`.
pub fn asked_install(body: &Value) -> Option<String> {
    let o = body.as_object()?;
    if o.keys().any(|k| !matches!(k.as_str(), "type" | "name")) {
        return None;
    }
    let name = o.get("name")?.as_str()?;
    aib_plugin::name_ok(name).then(|| name.to_string())
}

pub fn marketplace_install_with<F>(
    home: &Path,
    body: &Value,
    os: &str,
    arch: &str,
    windows: bool,
    mut fetch: F,
) -> Result<Value, (StatusCode, &'static str)>
where
    F: FnMut(&str) -> Result<Vec<u8>, String>,
{
    let name = asked_install(body).ok_or((StatusCode::BAD_REQUEST, "bad_marketplace_install"))?;
    let target = aib_plugin::marketplace::target_triple(os, arch)
        .ok_or((StatusCode::BAD_REQUEST, "unsupported_target"))?;
    let cat_bytes = fetch(aib_plugin::marketplace::MARKETPLACE_URL)
        .map_err(|_| (StatusCode::BAD_GATEWAY, "marketplace_unreachable"))?;
    let cat_text = std::str::from_utf8(&cat_bytes)
        .map_err(|_| (StatusCode::BAD_GATEWAY, "marketplace_bad_catalog"))?;
    let entries = aib_plugin::marketplace::parse_catalog(cat_text)
        .map_err(|_| (StatusCode::BAD_GATEWAY, "marketplace_bad_catalog"))?;
    let entry = entries
        .into_iter()
        .find(|e| e.name == name)
        .ok_or((StatusCode::NOT_FOUND, "no_such_plugin"))?;
    let asset = entry
        .targets
        .get(target)
        .ok_or((StatusCode::BAD_REQUEST, "unsupported_target"))?;
    let manifest_bytes =
        fetch(&entry.manifest.url).map_err(|_| (StatusCode::BAD_GATEWAY, "download_failed"))?;
    let bin_bytes = fetch(&asset.url).map_err(|_| (StatusCode::BAD_GATEWAY, "download_failed"))?;
    aib_plugin::marketplace::install_from_bytes(
        home,
        &entry,
        target,
        windows,
        &manifest_bytes,
        &bin_bytes,
    )
    .map_err(|_| (StatusCode::CONFLICT, "install_failed"))?;
    Ok(list(home, windows))
}

pub async fn marketplace_list_act() -> Response {
    let got = tokio::task::spawn_blocking(|| {
        let bytes = aib_plugin::marketplace::fetch_https(aib_plugin::marketplace::MARKETPLACE_URL)
            .map_err(|_| (StatusCode::BAD_GATEWAY, "marketplace_unreachable"))?;
        let text = std::str::from_utf8(&bytes)
            .map_err(|_| (StatusCode::BAD_GATEWAY, "marketplace_bad_catalog"))?;
        marketplace_list_from(text, std::env::consts::OS, std::env::consts::ARCH)
    })
    .await;
    match got {
        Ok(Ok(v)) => Json(v).into_response(),
        Ok(Err((status, code))) => refuse(status, code),
        _ => refuse(StatusCode::INTERNAL_SERVER_ERROR, "marketplace_failed"),
    }
}

pub async fn marketplace_install_act(body: Value) -> Response {
    let got = tokio::task::spawn_blocking(move || {
        let _turn = turn();
        home_now().map(|h| {
            marketplace_install_with(
                &h,
                &body,
                std::env::consts::OS,
                std::env::consts::ARCH,
                cfg!(windows),
                aib_plugin::marketplace::fetch_https,
            )
        })
    })
    .await;
    match got {
        Ok(Some(Ok(v))) => Json(v).into_response(),
        Ok(Some(Err((status, code)))) => refuse(status, code),
        _ => refuse(StatusCode::INTERNAL_SERVER_ERROR, "no_home"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Home(PathBuf);
    impl Drop for Home {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn home(tag: &str) -> Home {
        let h = std::env::temp_dir().join(format!("aibd-plugins-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&h);
        std::fs::create_dir_all(h.join("plugins")).expect("home");
        Home(h)
    }

    /// Installs a plugin whose executable is `bytes`; `default` is its file's own switch.
    fn install(h: &Home, name: &str, default: &str, bytes: &[u8]) {
        let folder = h.0.join("plugins").join(name);
        std::fs::create_dir_all(folder.join("bin")).expect("folders");
        let exe = aib_plugin::exe_path(&folder, &format!("bin/{name}"), cfg!(windows));
        std::fs::write(&exe, bytes).expect("exe");
        let file = json!({
            "schema": aib_plugin::SCHEMA, "name": name, "version": "1.2.3",
            "summary": "what it does", "paid": false, "default": default,
            "commands": [{"name": name, "bin": format!("bin/{name}")}],
            "sha256": {format!("bin/{name}"): aib_plugin::sha256_hex(bytes)},
        });
        std::fs::write(folder.join("aib-plugin.json"), file.to_string()).expect("file");
    }

    /// Writes the user's switches file as it stands (its folder may not be there yet).
    fn write_switches(h: &Home, text: &str) {
        let f = aib_plugin::switches_file(&h.0);
        std::fs::create_dir_all(f.parent().expect("a folder")).expect("folder");
        std::fs::write(f, text).expect("switches");
    }

    fn row<'a>(v: &'a Value, name: &str) -> &'a Value {
        v["plugins"]
            .as_array()
            .expect("plugins")
            .iter()
            .find(|p| p["name"] == name || p["folder"] == name)
            .unwrap_or_else(|| panic!("no row {name} in {v}"))
    }

    #[test]
    fn the_list_names_every_folder_with_its_switch_and_whether_it_runs() {
        let h = home("list");
        // none installed: an empty list, not an error
        let v = list(&h.0, cfg!(windows));
        assert_eq!(
            (v["state"].as_str(), v["plugins"].as_array().map(Vec::len)),
            (Some("ok"), Some(0))
        );

        install(&h, "alpha", "on", b"a");
        install(&h, "beta", "off", b"b");
        std::fs::create_dir_all(h.0.join("plugins").join("broken")).expect("folder");
        std::fs::write(
            h.0.join("plugins").join("broken").join("aib-plugin.json"),
            "{",
        )
        .expect("file");
        let v = list(&h.0, cfg!(windows));
        let a = row(&v, "alpha");
        assert_eq!(
            (a["on"].as_bool(), a["runs"].as_str(), a["version"].as_str()),
            (Some(true), Some("ok"), Some("1.2.3"))
        );
        // off by its own file: listed, off, and not run
        let b = row(&v, "beta");
        assert_eq!(
            (
                b["on"].as_bool(),
                b["runs"].as_str(),
                b["default_on"].as_bool()
            ),
            (Some(false), Some("off"), Some(false))
        );
        // a folder whose file is not understood is in the list, with the problem
        assert_eq!(row(&v, "broken")["problem"], "not_understood");
        assert_eq!(v["plugins"].as_array().map(Vec::len), Some(3));

        // the executable is changed after it was installed: on, and said not to run
        let exe = aib_plugin::exe_path(
            &h.0.join("plugins").join("alpha"),
            "bin/alpha",
            cfg!(windows),
        );
        std::fs::write(exe, b"changed").expect("exe");
        let a = list(&h.0, cfg!(windows));
        assert_eq!(
            (
                row(&a, "alpha")["on"].as_bool(),
                row(&a, "alpha")["runs"].as_str()
            ),
            (Some(true), Some("sum_differs"))
        );

        // a switches file that cannot be read: nothing is taken as on, and it is said
        write_switches(&h, "{");
        let v = list(&h.0, cfg!(windows));
        assert_eq!(v["switches_readable"], false);
        assert_eq!(
            (
                row(&v, "beta")["on"].clone(),
                row(&v, "beta")["runs"].as_str()
            ),
            (Value::Null, Some("switches_unread"))
        );
    }

    #[test]
    fn the_switch_is_moved_for_an_installed_plugin_only_and_the_user_s_word_wins() {
        let h = home("set");
        install(&h, "alpha", "on", b"a");
        install(&h, "beta", "off", b"b");
        let w = cfg!(windows);
        // off: the plugin on by default is off; on: the plugin off by default is on
        let v = set(&h.0, &json!({"type": SET, "name": "alpha", "on": false}), w).expect("set");
        assert_eq!(
            (
                row(&v, "alpha")["on"].as_bool(),
                row(&v, "alpha")["runs"].as_str()
            ),
            (Some(false), Some("off"))
        );
        let v = set(&h.0, &json!({"type": SET, "name": "beta", "on": true}), w).expect("set");
        assert_eq!(
            (
                row(&v, "beta")["on"].as_bool(),
                row(&v, "beta")["runs"].as_str()
            ),
            (Some(true), Some("ok"))
        );
        assert_eq!(row(&v, "alpha")["on"], false, "the other switch stays");
        // and back
        let v = set(&h.0, &json!({"type": SET, "name": "alpha", "on": true}), w).expect("set");
        assert_eq!(row(&v, "alpha")["on"], true);

        // a name that is no installed plugin; a body that is not a name and a yes or no
        let code = |body: Value| {
            set(&h.0, &body, w)
                .map(|_| ())
                .map_err(|(s, c)| (s.as_u16(), c))
        };
        assert_eq!(
            code(json!({"type": SET, "name": "gamma", "on": false})),
            Err((404, "no_such_plugin"))
        );
        for bad in [
            json!({"type": SET, "name": "alpha"}),
            json!({"type": SET, "name": "alpha", "on": "off"}),
            json!({"type": SET, "name": "alpha", "on": 0}),
            json!({"type": SET, "on": false}),
            json!({"type": SET, "name": "../alpha", "on": false}),
            json!({"type": SET, "name": "alpha", "on": false, "file": "/etc/x"}),
            json!([]),
        ] {
            assert_eq!(code(bad.clone()), Err((400, "bad_plugin_set")), "{bad}");
        }
        // none of those wrote anything
        let v = list(&h.0, w);
        assert_eq!(
            (
                row(&v, "alpha")["on"].as_bool(),
                row(&v, "beta")["on"].as_bool()
            ),
            (Some(true), Some(true))
        );
        assert!(v["stray"].as_array().is_some_and(Vec::is_empty), "{v}");

        // a switches file that cannot be read is not written over
        write_switches(&h, "{");
        assert_eq!(
            code(json!({"type": SET, "name": "alpha", "on": false})),
            Err((409, "switch_not_written"))
        );
        assert_eq!(
            std::fs::read_to_string(aib_plugin::switches_file(&h.0)).expect("read"),
            "{"
        );
    }

    #[test]
    fn marketplace_list_and_install_verify_sha_and_reject_bad_requests() {
        let h = home("mkt");
        let man = json!({
            "schema": aib_plugin::SCHEMA,
            "name": "gamma",
            "version": "0.1.0",
            "summary": "g",
            "paid": false,
            "default": "on",
            "commands": [{"name": "gamma", "bin": "bin/gamma"}]
        })
        .to_string()
        .into_bytes();
        let bin = b"#!/bin/sh\necho ok\n";
        let m_sha = aib_plugin::sha256_hex(&man);
        let b_sha = aib_plugin::sha256_hex(bin);
        let cat = json!({
            "schema": aib_plugin::marketplace::MARKETPLACE_SCHEMA,
            "plugins": [{
                "name": "gamma",
                "version": "0.1.0",
                "summary": "g",
                "paid": false,
                "default": "on",
                "manifest": {
                    "url": "https://github.com/ez2app/gamma/releases/download/v0.1.0/aib-plugin.json",
                    "sha256": m_sha
                },
                "targets": {
                    "aarch64-apple-darwin": {
                        "url": "https://github.com/ez2app/gamma/releases/download/v0.1.0/gamma",
                        "sha256": b_sha
                    }
                }
            }]
        })
        .to_string();

        let listed = marketplace_list_from(&cat, "macos", "aarch64").expect("list");
        assert_eq!(listed["plugins"][0]["supported"], true);
        let win_list = marketplace_list_from(&cat, "windows", "x86_64").expect("win list");
        assert_eq!(win_list["plugins"][0]["supported"], false);

        // bad body / unknown plugin / sha mismatch
        let mut fetcher = |url: &str| -> Result<Vec<u8>, String> {
            if url == aib_plugin::marketplace::MARKETPLACE_URL {
                Ok(cat.as_bytes().to_vec())
            } else if url.ends_with("aib-plugin.json") {
                Ok(man.clone())
            } else {
                Ok(bin.to_vec())
            }
        };
        assert_eq!(
            marketplace_install_with(
                &h.0,
                &json!({"type": MARKETPLACE_INSTALL, "name": "../gamma"}),
                "macos",
                "aarch64",
                false,
                &mut fetcher,
            )
            .map_err(|(s, c)| (s.as_u16(), c)),
            Err((400, "bad_marketplace_install"))
        );
        assert_eq!(
            marketplace_install_with(
                &h.0,
                &json!({"type": MARKETPLACE_INSTALL, "name": "missing"}),
                "macos",
                "aarch64",
                false,
                &mut fetcher,
            )
            .map_err(|(s, c)| (s.as_u16(), c)),
            Err((404, "no_such_plugin"))
        );

        let installed = marketplace_install_with(
            &h.0,
            &json!({"type": MARKETPLACE_INSTALL, "name": "gamma"}),
            "macos",
            "aarch64",
            false,
            &mut fetcher,
        )
        .expect("install");
        assert_eq!(installed["plugins"][0]["name"], "gamma");
        assert_eq!(installed["plugins"][0]["runs"], "ok");
    }
}
