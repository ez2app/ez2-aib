//! `aib-xtask deps [--manifest-path P]`: only `aibd` and `aib-procwatch` may
//! depend on `aib-sys` (API list v0.2, section 9; DECISION-10 B added
//! aib-procwatch for its child listing), and `aib-forkcount` may reach it only
//! through aib-procwatch ([`SYS_VIA`]). The one crate with `unsafe` OS calls must not
//! leak into aib-core, aib-store or any other crate, directly, through a
//! dev / build / target-specific dependency, under a renamed key, or through
//! another workspace crate.
//!
//! [`check_own`] is the other direction: what aib-sys itself may depend on.
//! Every dependency of aib-sys must be on [`SYS_OWN_DEPS`] with the same
//! kind, target, version requirement and feature list (DECISION-11 A: an
//! exact windows-sys version, only the features `job` uses). Adding a crate
//! or a feature means changing this list in the same PR, where review sees it.

use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

pub const SYS_CRATE: &str = "aib-sys";
/// aib-approve: macOS owner keys (presence_macos), DECISION-t135b-objc2-deps.
pub const SYS_ALLOWED: &[&str] = &["aibd", "aib-procwatch", "aib-approve"];

/// `(crate, through)`: `crate` may reach aib-sys, but only through `through`
/// (a path that avoids `through` is still refused). DECISION-10 B: the
/// measurement tool uses aib-procwatch, which keeps aib-sys types private.
/// aib-consent: only through aib-approve (T136 consent uses the owner-key flow; c477a T135-b
/// slice 2 review M1).
pub const SYS_VIA: &[(&str, &str)] = &[
    ("aib-forkcount", "aib-procwatch"),
    ("aib-consent", "aib-approve"),
];

/// One allowed dependency of aib-sys: `(name, target, version req, features)`.
/// Kind is normal ([`SYS_OWN_DEPS`]) or, for test-only Windows features, dev
/// ([`SYS_DEV_DEPS`]); never build. `features` sorted.
pub type OwnDep = (
    &'static str,
    Option<&'static str>,
    &'static str,
    &'static [&'static str],
);

pub const SYS_OWN_DEPS: &[OwnDep] = &[
    ("thiserror", None, "^2.0.21", &[]),
    ("libc", Some("cfg(target_os = \"macos\")"), "^0.2.191", &[]),
    (
        "windows-sys",
        Some("cfg(windows)"),
        "=0.61.2",
        &[
            "Win32_Foundation",
            // winrun::ui_lang: GetUserDefaultUILanguage, the language of the AIB shortcut's
            // console lines (ebab8 11-26 12:39)
            "Win32_Globalization",
            // AP26 W-5: windows-sys gates CreateDesktopW (DEVMODEW) and RegisterClassW
            // (HBRUSH) behind Gdi, so it is a normal feature; BitBlt / GetDIBits /
            // PrintWindow stay out of non-test code by aib-sys `test_only_os_calls`
            "Win32_Graphics_Gdi",
            "Win32_Security",
            // S10-3 / W1b hello_ncrypt (DESIGN-persona-propose-osverify-c2f0b-1123 §3)
            "Win32_Security_Cryptography",
            // L1b wintrust.rs (08de5 b741ec8c8e, 3e53b REVIEW-l1b-wintrust 0/0/0/3): WinVerifyTrust
            // and its WTHelper signer reads (WinTrust); windows-sys gates the WinTrust provider
            // structs behind Catalog and Sip, nothing of either is called on its own
            "Win32_Security_Cryptography_Catalog",
            "Win32_Security_Cryptography_Sip",
            "Win32_Security_WinTrust",
            // PREREAD-shared-folder-noindex-a9136-1121 (5d33a 0/0/2/2): Get/SetFileAttributesW
            "Win32_Storage_FileSystem",
            "Win32_System_Console",
            "Win32_System_Diagnostics_ToolHelp",
            "Win32_System_IO",
            "Win32_System_JobObjects",
            // AP26 W-4 / W-5 (PRE-READ-w4-winsys-features-c2f0b-1125, 3e53b 0/0/2/2):
            // GetModuleHandleW for the passphrase window's class
            "Win32_System_LibraryLoader",
            // winrun: RegSetKeyValueW / RegGetValueW / RegDeleteKeyValueW on the HKCU Run
            // key only (Windows setup, PLAN-win-customer-today-c2f0b-1126 rows 2 / 4)
            "Win32_System_Registry",
            "Win32_System_RemoteDesktop",
            // W-5: aibd's own desktop (CreateDesktopW / SwitchDesktop / SetThreadDesktop)
            "Win32_System_StationsAndDesktops",
            // sysdir: GetSystemDirectoryW for aibd host_ssh (3e53b DEEP2 T1, DESIGN-hostmode §3-4)
            "Win32_System_SystemInformation",
            "Win32_System_Threading",
            // W-5 SetFocus; SendInput comes with it and is kept out of non-test code by
            // aib-sys `test_only_os_calls` (3e53b condition 2)
            "Win32_UI_Input_KeyboardAndMouse",
            // W-5: the passphrase window, SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)
            "Win32_UI_WindowsAndMessaging",
        ],
    ),
    // DECISION-t135b-objc2-deps: macOS presence keys (presence_macos). Appended after
    // windows-sys, which the tests below address as index 2.
    (
        "objc2",
        Some("cfg(target_os = \"macos\")"),
        "=0.6.3",
        &["std"],
    ),
    (
        "objc2-foundation",
        Some("cfg(target_os = \"macos\")"),
        "=0.3.2",
        &["NSData", "NSString", "std"],
    ),
    (
        "objc2-local-authentication",
        Some("cfg(target_os = \"macos\")"),
        "=0.3.2",
        // LABiometryType: S10-2 owner_check_state (DESIGN-persona-propose-osverify-c2f0b-1123 r3 §8)
        // block2: S10-2b-1 evaluatePolicy (DESIGN-s10-2b-nofinger-c2f0b-1125 r1 §7a)
        &["LABiometryType", "LAContext", "block2", "std"],
    ),
    // DECISION-t135b-objc2-deps item 1 (block2 when the signing flow needs it; ebab8 11-25)
    (
        "block2",
        Some("cfg(target_os = \"macos\")"),
        "=0.6.2",
        &["alloc"],
    ),
    // B2 (Andy 11-26 10:1x; DECISION-win-hello-fallback-3e53b-1126): hello_consent, the Windows
    // Hello gesture (UserConsentVerifier and IUserConsentVerifierInterop), no key
    (
        "windows",
        Some("cfg(windows)"),
        "=0.62.2",
        &[
            "Security_Credentials_UI",
            "Win32_Foundation",
            // winshortcut (ebab8 11-26 12:20, PREREAD-win-shortcut-wake-3e53b-1126): the
            // AIB.lnk on the desktop and in the Start menu, IShellLinkW + IPersistFile (Com),
            // SHGetKnownFolderPath (Shell); FileSystem because IShellLinkW::GetPath names
            // WIN32_FIND_DATAW
            "Win32_Storage_FileSystem",
            "Win32_System_Com",
            "Win32_System_WinRT",
            "Win32_UI_Shell",
        ],
    ),
];

/// aib-sys's dev dependencies: Windows features only the W-4 owner-present measurement
/// tests use (the CredUI comparison window and freeing its buffer). As dev
/// features, a normal build has none of them, so product code calling them does not compile
/// (PRE-READ-w4-winsys-features 3e53b condition 1).
pub const SYS_DEV_DEPS: &[OwnDep] = &[(
    "windows-sys",
    Some("cfg(windows)"),
    "=0.61.2",
    &["Win32_Security_Credentials", "Win32_System_Com"],
)];

/// Every dependency of aib-sys that is not exactly on [`SYS_OWN_DEPS`] (or, for dev
/// dependencies, [`SYS_DEV_DEPS`]).
pub fn check_own(meta: &Value) -> Vec<String> {
    let mut errs = Vec::new();
    let Some(sys) = meta["packages"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|p| p["name"].as_str() == Some(SYS_CRATE))
    else {
        return vec![format!("{SYS_CRATE} not found in cargo metadata")];
    };
    // a crate feature can switch on dependency features without touching the
    // dependency entry, so aib-sys has none
    if sys["features"].as_object().is_some_and(|f| !f.is_empty()) {
        errs.push(format!(
            "{SYS_CRATE} has a [features] table ({}); dependency features must be listed on the dependency",
            sys["features"]
        ));
    }
    for d in sys["dependencies"].as_array().into_iter().flatten() {
        let name = d["name"].as_str().unwrap_or("?");
        let target = d["target"].as_str();
        let req = d["req"].as_str().unwrap_or("");
        let kind = d["kind"].as_str();
        let mut feats: Vec<&str> = d["features"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .collect();
        feats.sort_unstable();
        let what = format!("{name} {req} target={target:?} kind={kind:?} features={feats:?}");
        let list = match kind {
            None => SYS_OWN_DEPS,
            Some("dev") => SYS_DEV_DEPS,
            Some(other) => {
                errs.push(format!("{SYS_CRATE} has a {other} dependency: {what}"));
                continue;
            }
        };
        if d["optional"].as_bool() == Some(true) || d["rename"].is_string() {
            errs.push(format!(
                "{SYS_CRATE} dependency is optional or renamed: {what}"
            ));
            continue;
        }
        let ok = list
            .iter()
            .any(|(n, t, r, f)| *n == name && *t == target && *r == req && *f == feats.as_slice());
        if !ok {
            errs.push(format!(
                "{SYS_CRATE} dependency not on the allow-list: {what}"
            ));
        }
    }
    errs
}

/// Workspace package name -> names of the packages it depends on (every
/// kind, every target; `name` is the package name even when renamed).
fn graph(meta: &Value) -> BTreeMap<String, BTreeSet<String>> {
    let mut g = BTreeMap::new();
    for p in meta["packages"].as_array().into_iter().flatten() {
        let Some(name) = p["name"].as_str() else {
            continue;
        };
        let deps = p["dependencies"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|d| d["name"].as_str().map(str::to_owned))
            .collect();
        g.insert(name.to_owned(), deps);
    }
    g
}

/// The chain from `from` to aib-sys that does not pass through any crate in
/// `avoid`, if any (e.g. `["aib-store", "x", "aib-sys"]`).
fn path_to_sys(
    g: &BTreeMap<String, BTreeSet<String>>,
    from: &str,
    avoid: &[&str],
) -> Option<Vec<String>> {
    let mut stack = vec![vec![from.to_owned()]];
    let mut seen = BTreeSet::new();
    while let Some(path) = stack.pop() {
        let last = path.last()?.clone();
        if !seen.insert(last.clone()) {
            continue;
        }
        for d in g.get(&last).into_iter().flatten() {
            if avoid.contains(&d.as_str()) {
                continue;
            }
            let mut next = path.clone();
            next.push(d.clone());
            if d == SYS_CRATE {
                return Some(next);
            }
            stack.push(next);
        }
    }
    None
}

/// Every violation, one line each. Empty = ok.
pub fn check(meta: &Value) -> Vec<String> {
    let g = graph(meta);
    let mut errs = Vec::new();
    for name in g.keys() {
        if name == SYS_CRATE || SYS_ALLOWED.contains(&name.as_str()) {
            continue;
        }
        let via: Vec<&str> = SYS_VIA
            .iter()
            .filter(|(c, _)| *c == name)
            .map(|(_, t)| *t)
            .collect();
        if let Some(path) = path_to_sys(&g, name, &via) {
            errs.push(format!(
                "`{name}` depends on {SYS_CRATE} ({}); only {} may{}",
                path.join(" -> "),
                SYS_ALLOWED.join(", "),
                if via.is_empty() {
                    String::new()
                } else {
                    format!(" (`{name}` only through {})", via.join(", "))
                }
            ));
        }
    }
    errs
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn pkg(name: &str, deps: &[&str]) -> Value {
        json!({"name": name, "dependencies": deps.iter().map(|d| json!({"name": d})).collect::<Vec<_>>()})
    }

    fn meta(pkgs: Vec<Value>) -> Value {
        json!({ "packages": pkgs })
    }

    #[test]
    fn aibd_may_depend_on_sys() {
        let m = meta(vec![
            pkg("aib-core", &[]),
            pkg("aib-sys", &["libc"]),
            pkg("aibd", &["aib-core", "aib-sys"]),
        ]);
        assert!(check(&m).is_empty());
    }

    #[test]
    fn a_direct_dependency_is_refused() {
        let m = meta(vec![pkg("aib-core", &["aib-sys"]), pkg("aib-sys", &[])]);
        let e = check(&m);
        assert_eq!(e.len(), 1);
        assert!(
            e[0].contains("`aib-core` depends on aib-sys (aib-core -> aib-sys)"),
            "{e:?}"
        );
    }

    #[test]
    fn a_dependency_through_another_crate_is_refused() {
        let m = meta(vec![
            pkg("aib-store", &["aib-procwatch"]),
            pkg("aib-procwatch", &["aib-sys"]),
            pkg("aib-sys", &[]),
        ]);
        let e = check(&m);
        assert!(
            e.iter()
                .any(|l| l.contains("aib-store -> aib-procwatch -> aib-sys")),
            "{e:?}"
        );
    }

    #[test]
    fn forkcount_may_reach_sys_only_through_procwatch() {
        let ok = meta(vec![
            pkg("aib-forkcount", &["aib-procwatch"]),
            pkg("aib-procwatch", &["aib-sys"]),
            pkg("aib-sys", &[]),
        ]);
        assert!(check(&ok).is_empty(), "{:?}", check(&ok));
        // directly: refused
        let direct = meta(vec![
            pkg("aib-forkcount", &["aib-procwatch", "aib-sys"]),
            pkg("aib-procwatch", &["aib-sys"]),
            pkg("aib-sys", &[]),
        ]);
        let e = check(&direct);
        assert!(
            e.iter().any(
                |l| l.contains("`aib-forkcount` depends on aib-sys (aib-forkcount -> aib-sys)")
            ),
            "{e:?}"
        );
        // through aibd (not its permitted route): refused
        let other = meta(vec![
            pkg("aib-forkcount", &["aibd"]),
            pkg("aibd", &["aib-sys"]),
            pkg("aib-sys", &[]),
        ]);
        assert!(
            check(&other)
                .iter()
                .any(|l| l.contains("aib-forkcount -> aibd -> aib-sys")),
            "{:?}",
            check(&other)
        );
    }

    #[test]
    fn depending_on_aibd_counts_too() {
        let m = meta(vec![
            pkg("tool", &["aibd"]),
            pkg("aibd", &["aib-sys"]),
            pkg("aib-sys", &[]),
        ]);
        assert!(check(&m).iter().any(|l| l.contains("`tool`")));
    }

    #[test]
    fn a_renamed_dev_or_target_dependency_is_still_seen() {
        // cargo metadata keeps the package name in `name` and puts the key in
        // `rename`; kind and target do not matter
        let m = json!({"packages": [
            {"name": "aib-store", "dependencies": [
                {"name": "aib-sys", "rename": "sys", "kind": "dev", "target": "cfg(windows)"}
            ]},
            {"name": "aib-sys", "dependencies": []}
        ]});
        assert_eq!(check(&m).len(), 1);
    }

    fn sys_with(deps: Value) -> Value {
        json!({"packages": [{"name": "aib-sys", "dependencies": deps}]})
    }

    fn dep(name: &str, target: Option<&str>, req: &str, feats: &[&str]) -> Value {
        json!({"name": name, "target": target, "req": req, "kind": null,
               "features": feats, "optional": false, "rename": null})
    }

    fn allowed() -> Vec<Value> {
        SYS_OWN_DEPS
            .iter()
            .map(|(n, t, r, f)| dep(n, *t, r, f))
            .collect()
    }

    #[test]
    fn own_deps_on_the_list_pass() {
        assert!(check_own(&sys_with(json!(allowed()))).is_empty());
    }

    #[test]
    fn own_dep_list_is_sorted_so_order_cannot_hide_a_change() {
        for (n, _, _, f) in SYS_OWN_DEPS {
            let mut s = f.to_vec();
            s.sort_unstable();
            assert_eq!(&s, f, "{n} features not sorted");
        }
    }

    #[test]
    fn a_new_own_dependency_is_refused() {
        let mut d = allowed();
        d.push(dep("rand", None, "^0.9", &[]));
        let e = check_own(&sys_with(json!(d)));
        assert_eq!(e.len(), 1, "{e:?}");
        assert!(e[0].contains("not on the allow-list: rand"), "{e:?}");
    }

    #[test]
    fn an_extra_windows_feature_is_refused() {
        let mut d = allowed();
        d[2] = dep(
            "windows-sys",
            Some("cfg(windows)"),
            "=0.61.2",
            &["Win32_Foundation", "Win32_Networking_WinSock"],
        );
        assert_eq!(check_own(&sys_with(json!(d))).len(), 1);
    }

    #[test]
    fn a_loose_version_or_other_target_is_refused() {
        let mut d = allowed();
        let (_, _, _, f) = SYS_OWN_DEPS[2];
        d[2] = dep("windows-sys", Some("cfg(windows)"), "^0.61", f);
        assert_eq!(check_own(&sys_with(json!(d))).len(), 1);
        d[2] = dep("windows-sys", None, "=0.61.2", f);
        assert_eq!(check_own(&sys_with(json!(d))).len(), 1);
        d[2] = dep("libc", None, "^0.2.191", &[]);
        assert_eq!(check_own(&sys_with(json!(d))).len(), 1);
    }

    #[test]
    fn a_feature_table_is_refused() {
        let mut m = sys_with(json!(allowed()));
        m["packages"][0]["features"] = json!({"net": ["windows-sys/Win32_Networking_WinSock"]});
        let e = check_own(&m);
        assert_eq!(e.len(), 1, "{e:?}");
        assert!(e[0].contains("[features] table"), "{e:?}");
    }

    #[test]
    fn a_dev_build_optional_or_renamed_own_dependency_is_refused() {
        for (k, v) in [
            ("kind", json!("dev")),
            ("kind", json!("build")),
            ("optional", json!(true)),
            ("rename", json!("w")),
        ] {
            let mut d = allowed();
            d[0][k] = v.clone();
            assert_eq!(check_own(&sys_with(json!(d))).len(), 1, "{k}={v}");
        }
    }

    fn dev_dep(name: &str, target: Option<&str>, req: &str, feats: &[&str]) -> Value {
        let mut d = dep(name, target, req, feats);
        d["kind"] = json!("dev");
        d
    }

    /// AP26 W-4 (3e53b condition 1): the three test-only Windows features as a dev
    /// dependency pass; one more feature, another crate, or the same features as a normal
    /// dependency do not.
    #[test]
    fn only_the_three_test_features_may_be_a_dev_dependency() {
        let (n, t, r, f) = SYS_DEV_DEPS[0];
        let mut d = allowed();
        d.push(dev_dep(n, t, r, f));
        assert!(
            check_own(&sys_with(json!(d))).is_empty(),
            "reason: listed_dev"
        );
        let mut more = f.to_vec();
        more.push("Win32_Networking_WinSock");
        more.sort_unstable();
        let mut d = allowed();
        d.push(dev_dep(n, t, r, &more));
        assert_eq!(
            check_own(&sys_with(json!(d))).len(),
            1,
            "reason: extra_dev_feature"
        );
        let mut d = allowed();
        d.push(dev_dep("tempfile", None, "^3", &[]));
        assert_eq!(
            check_own(&sys_with(json!(d))).len(),
            1,
            "reason: other_dev_crate"
        );
        let mut d = allowed();
        d[2] = dep(n, t, r, f);
        assert_eq!(
            check_own(&sys_with(json!(d))).len(),
            1,
            "reason: test_features_as_normal"
        );
        for (n, _, _, f) in SYS_DEV_DEPS {
            let mut s = f.to_vec();
            s.sort_unstable();
            assert_eq!(&s, f, "{n} dev features not sorted");
        }
    }

    #[test]
    fn an_extra_local_authentication_feature_or_loose_version_is_refused() {
        let i = SYS_OWN_DEPS
            .iter()
            .position(|d| d.0 == "objc2-local-authentication")
            .expect("on the list");
        let (n, t, r, _) = SYS_OWN_DEPS[i];
        let mut d = allowed();
        d[i] = dep(n, t, r, &["LABiometryType", "LAContext", "LAError", "std"]);
        assert_eq!(check_own(&sys_with(json!(d))).len(), 1);
        d[i] = dep(n, t, "^0.3.2", &["LABiometryType", "LAContext", "std"]);
        assert_eq!(check_own(&sys_with(json!(d))).len(), 1);
        d[i] = dep(n, None, r, &["LABiometryType", "LAContext", "std"]);
        assert_eq!(check_own(&sys_with(json!(d))).len(), 1);
    }

    #[test]
    fn aib_approve_may_use_aib_sys_but_its_users_may_not_reach_it() {
        let ok = meta(vec![pkg("aib-approve", &["aib-sys"]), pkg("aib-sys", &[])]);
        assert!(check(&ok).is_empty(), "{:?}", check(&ok));
        let through = meta(vec![
            pkg("tool", &["aib-approve"]),
            pkg("aib-approve", &["aib-sys"]),
            pkg("aib-sys", &[]),
        ]);
        assert_eq!(check(&through).len(), 1, "{:?}", check(&through));
    }

    #[test]
    fn aib_consent_reaches_aib_sys_only_through_aib_approve() {
        let ok = meta(vec![
            pkg("aib-consent", &["aib-approve"]),
            pkg("aib-approve", &["aib-sys"]),
            pkg("aib-sys", &[]),
        ]);
        assert!(check(&ok).is_empty(), "{:?}", check(&ok));
        let direct = meta(vec![
            pkg("aib-consent", &["aib-approve", "aib-sys"]),
            pkg("aib-approve", &["aib-sys"]),
            pkg("aib-sys", &[]),
        ]);
        assert_eq!(check(&direct).len(), 1, "{:?}", check(&direct));
    }
}
