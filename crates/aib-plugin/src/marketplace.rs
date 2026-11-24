//! AIB 2.0 plugin marketplace catalog (`aib-marketplace/v1`, PLAN-skipstop-master H2):
//! reads the official catalog (`ez2app/aib-marketplace`), enforces HTTPS allowlisted GitHub
//! URLs and 64-hex SHA-256 digests, and installs verified plugin binaries into
//! `<AIB home>/plugins/<name>/`.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::process::Command;

use serde_json::{Map, Value};

pub const MARKETPLACE_SCHEMA: &str = "aib-marketplace/v1";
pub const MARKETPLACE_URL: &str =
    "https://raw.githubusercontent.com/ez2app/aib-marketplace/main/index.json";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Asset {
    pub url: String,
    pub sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CatalogEntry {
    pub name: String,
    pub version: String,
    pub summary: String,
    pub paid: bool,
    pub default_on: bool,
    pub manifest: Asset,
    pub targets: BTreeMap<String, Asset>,
}

/// Map `(std::env::consts::OS, std::env::consts::ARCH)` to the marketplace target key.
pub fn target_triple(os: &str, arch: &str) -> Option<&'static str> {
    match (os, arch) {
        ("macos", "aarch64") => Some("aarch64-apple-darwin"),
        ("windows", "x86_64") => Some("x86_64-pc-windows-msvc"),
        ("linux", "x86_64") => Some("x86_64-unknown-linux-gnu"),
        _ => None,
    }
}

fn hex64_ok(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

fn url_ok(u: &str) -> bool {
    u.starts_with("https://github.com/") || u.starts_with("https://raw.githubusercontent.com/")
}

fn asset_from(o: &Map<String, Value>, label: &str) -> Result<Asset, String> {
    let url = o
        .get("url")
        .and_then(Value::as_str)
        .filter(|u| url_ok(u))
        .ok_or_else(|| {
            format!("{label}: url must be https://github.com/ or raw.githubusercontent.com/")
        })?;
    let sha256 = o
        .get("sha256")
        .and_then(Value::as_str)
        .filter(|h| hex64_ok(h))
        .ok_or_else(|| format!("{label}: sha256 must be 64 lowercase hex chars"))?;
    Ok(Asset {
        url: url.to_string(),
        sha256: sha256.to_string(),
    })
}

/// Parse and validate an `aib-marketplace/v1` `index.json` document.
pub fn parse_catalog(text: &str) -> Result<Vec<CatalogEntry>, String> {
    let v = crate::strict_json(text)?;
    let o = v.as_object().ok_or("catalog must be a JSON object")?;
    if o.get("schema").and_then(Value::as_str) != Some(MARKETPLACE_SCHEMA) {
        return Err("catalog schema is not aib-marketplace/v1".into());
    }
    let arr = o
        .get("plugins")
        .and_then(Value::as_array)
        .ok_or("catalog plugins must be an array")?;
    let mut out = Vec::with_capacity(arr.len());
    let mut seen = BTreeSet::new();
    for item in arr {
        let po = item
            .as_object()
            .ok_or("catalog plugin entry must be an object")?;
        let name = po
            .get("name")
            .and_then(Value::as_str)
            .filter(|n| crate::name_ok(n))
            .ok_or("bad plugin name")?;
        if !seen.insert(name.to_string()) {
            return Err(format!("duplicate plugin in catalog: {name}"));
        }
        let version = po
            .get("version")
            .and_then(Value::as_str)
            .filter(|s| !s.trim().is_empty())
            .ok_or_else(|| format!("{name}: missing version"))?;
        let summary = po
            .get("summary")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let paid = po
            .get("paid")
            .and_then(Value::as_bool)
            .ok_or_else(|| format!("{name}: missing bool paid"))?;
        let default_on = match po.get("default").and_then(Value::as_str) {
            Some("on") => true,
            Some("off") => false,
            _ => return Err(format!("{name}: default must be \"on\" or \"off\"")),
        };
        let manifest_obj = po
            .get("manifest")
            .and_then(Value::as_object)
            .ok_or_else(|| format!("{name}: missing manifest"))?;
        let manifest = asset_from(manifest_obj, &format!("{name}.manifest"))?;
        let targets_obj = po
            .get("targets")
            .and_then(Value::as_object)
            .ok_or_else(|| format!("{name}: missing targets"))?;
        if targets_obj.is_empty() {
            return Err(format!("{name}: targets must not be empty"));
        }
        let mut targets = BTreeMap::new();
        for (k, tv) in targets_obj {
            let to = tv
                .as_object()
                .ok_or_else(|| format!("{name}.{k}: target must be an object"))?;
            targets.insert(k.clone(), asset_from(to, &format!("{name}.{k}"))?);
        }
        out.push(CatalogEntry {
            name: name.to_string(),
            version: version.to_string(),
            summary,
            paid,
            default_on,
            manifest,
            targets,
        });
    }
    Ok(out)
}

/// Verify downloaded bytes against catalog SHA-256 digests and install into `<home>/plugins/<name>/`.
pub fn install_from_bytes(
    home: &Path,
    entry: &CatalogEntry,
    target: &str,
    windows: bool,
    manifest_bytes: &[u8],
    bin_bytes: &[u8],
) -> Result<PathBuf, String> {
    let want_manifest = &entry.manifest.sha256;
    let got_manifest = crate::sha256_hex(manifest_bytes);
    if &got_manifest != want_manifest {
        return Err(format!(
            "manifest sha256 mismatch: want {want_manifest}, got {got_manifest}"
        ));
    }
    let asset = entry
        .targets
        .get(target)
        .ok_or_else(|| format!("{}: no binary for target {target}", entry.name))?;
    let got_bin = crate::sha256_hex(bin_bytes);
    if got_bin != asset.sha256 {
        return Err(format!(
            "binary sha256 mismatch: want {}, got {got_bin}",
            asset.sha256
        ));
    }
    let manifest_text = std::str::from_utf8(manifest_bytes)
        .map_err(|_| "manifest is not valid UTF-8".to_string())?;
    let mut manifest_val = crate::strict_json(manifest_text)?;
    let parsed = crate::parse(manifest_text, &entry.name)?;
    if parsed.version != entry.version {
        return Err(format!(
            "manifest version {} != catalog version {}",
            parsed.version, entry.version
        ));
    }
    let cmd = parsed
        .commands
        .first()
        .ok_or_else(|| format!("{}: manifest has no commands", entry.name))?;
    if let Some(obj) = manifest_val.as_object_mut() {
        let mut sums = Map::new();
        sums.insert(cmd.bin.clone(), Value::String(got_bin.clone()));
        obj.insert("sha256".into(), Value::Object(sums));
    }
    let final_manifest = serde_json::to_string_pretty(&manifest_val)
        .map_err(|e| format!("serialize manifest: {e}"))?;

    let plugins_dir = crate::plugins_dir(home);
    std::fs::create_dir_all(&plugins_dir).map_err(|e| format!("create plugins dir: {e}"))?;
    let stage = plugins_dir.join(format!(".staging-{}-{}", entry.name, std::process::id()));
    let _ = std::fs::remove_dir_all(&stage);
    let exe_dst = crate::exe_path(&stage, &cmd.bin, windows);
    if let Some(parent) = exe_dst.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("create bin dir: {e}"))?;
    }
    std::fs::write(&exe_dst, bin_bytes).map_err(|e| format!("write binary: {e}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&exe_dst, std::fs::Permissions::from_mode(0o755))
            .map_err(|e| format!("chmod binary: {e}"))?;
    }
    std::fs::write(stage.join("aib-plugin.json"), final_manifest)
        .map_err(|e| format!("write manifest: {e}"))?;

    let dest = plugins_dir.join(&entry.name);
    if dest.exists() {
        let backup = plugins_dir.join(format!(".old-{}-{}", entry.name, std::process::id()));
        let _ = std::fs::remove_dir_all(&backup);
        std::fs::rename(&dest, &backup).map_err(|e| format!("move old plugin: {e}"))?;
        if let Err(e) = std::fs::rename(&stage, &dest) {
            let _ = std::fs::rename(&backup, &dest);
            let _ = std::fs::remove_dir_all(&stage);
            return Err(format!("install plugin: {e}"));
        }
        let _ = std::fs::remove_dir_all(&backup);
    } else if let Err(e) = std::fs::rename(&stage, &dest) {
        let _ = std::fs::remove_dir_all(&stage);
        return Err(format!("install plugin: {e}"));
    }
    Ok(dest)
}

/// Fetch bytes over HTTPS using `curl` (present on macOS, Linux, and Windows 10/11).
pub fn fetch_https(url: &str) -> Result<Vec<u8>, String> {
    if !url_ok(url) {
        return Err("refused non-allowlisted URL".into());
    }
    let out = Command::new("curl")
        .args([
            "--proto",
            "=https",
            "--tlsv1.2",
            "-fsSL",
            "--max-time",
            "30",
            url,
        ])
        .output()
        .map_err(|e| format!("curl failed to start: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "download failed (rc {}): {}",
            out.status.code().unwrap_or(-1),
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    Ok(out.stdout)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_manifest(version: &str) -> Vec<u8> {
        serde_json::json!({
            "schema": crate::SCHEMA,
            "name": "demo-plg",
            "version": version,
            "summary": "demo",
            "paid": false,
            "default": "on",
            "commands": [{"name": "demo-plg", "bin": "bin/demo-plg"}]
        })
        .to_string()
        .into_bytes()
    }

    fn sample_catalog(manifest_sha: &str, bin_sha: &str, url: &str) -> String {
        serde_json::json!({
            "schema": MARKETPLACE_SCHEMA,
            "plugins": [{
                "name": "demo-plg",
                "version": "0.1.0",
                "summary": "demo",
                "paid": false,
                "default": "on",
                "manifest": {
                    "url": "https://github.com/ez2app/demo/releases/download/v0.1.0/aib-plugin.json",
                    "sha256": manifest_sha
                },
                "targets": {
                    "aarch64-apple-darwin": { "url": url, "sha256": bin_sha },
                    "x86_64-pc-windows-msvc": { "url": url, "sha256": bin_sha }
                }
            }]
        })
        .to_string()
    }

    #[test]
    fn catalog_rejects_bad_schema_url_and_sha() {
        let h = "a".repeat(64);
        let ok = sample_catalog(
            &h,
            &h,
            "https://github.com/ez2app/demo/releases/download/v0.1.0/bin",
        );
        assert_eq!(parse_catalog(&ok).expect("valid").len(), 1);

        assert!(parse_catalog(&ok.replace("aib-marketplace/v1", "v0")).is_err());
        assert!(parse_catalog(&ok.replace("https://github.com/", "http://github.com/")).is_err());
        assert!(
            parse_catalog(&ok.replace("https://github.com/", "https://evil.example/")).is_err()
        );
        assert!(parse_catalog(&ok.replace(&h, "deadbeef")).is_err());
    }

    #[test]
    fn install_verifies_sha256_and_writes_runnable_plugin() {
        let dir = tempfile::tempdir().expect("tmp");
        let man = sample_manifest("0.1.0");
        let bin = b"#!/bin/sh\necho ok\n";
        let m_sha = crate::sha256_hex(&man);
        let b_sha = crate::sha256_hex(bin);
        let cat = sample_catalog(
            &m_sha,
            &b_sha,
            "https://github.com/ez2app/demo/releases/download/v0.1.0/demo-plg",
        );
        let entries = parse_catalog(&cat).expect("catalog");
        let entry = &entries[0];

        // wrong manifest sha or wrong binary sha is refused before writing
        assert!(
            install_from_bytes(dir.path(), entry, "aarch64-apple-darwin", false, b"{}", bin)
                .is_err()
        );
        assert!(install_from_bytes(
            dir.path(),
            entry,
            "aarch64-apple-darwin",
            false,
            &man,
            b"bad"
        )
        .is_err());
        assert!(!crate::plugins_dir(dir.path()).join("demo-plg").exists());

        // valid bytes install cleanly on both unix and windows layouts
        let dest = install_from_bytes(dir.path(), entry, "aarch64-apple-darwin", false, &man, bin)
            .expect("install");
        assert!(dest.join("aib-plugin.json").is_file());
        assert!(dest.join("bin/demo-plg").is_file());

        let win_dest =
            install_from_bytes(dir.path(), entry, "x86_64-pc-windows-msvc", true, &man, bin)
                .expect("win");
        assert!(win_dest.join("bin/demo-plg.exe").is_file());
    }
}
