//! With the cargo feature `web`: embed the built web workbench (`apps/web/dist`, or the
//! folder in `AIBD_WEB_DIST`) into aibd as `OUT_DIR/web_assets.rs` (see
//! `src/static_files.rs`). Without the feature this does nothing, so building and testing
//! aibd never needs node.

use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

fn content_type(p: &Path) -> &'static str {
    match p.extension().and_then(|e| e.to_str()).unwrap_or("") {
        "html" => "text/html; charset=utf-8",
        "js" | "mjs" => "text/javascript; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "json" | "map" => "application/json",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "avif" => "image/avif",
        "ico" => "image/x-icon",
        "webp" => "image/webp",
        "woff2" => "font/woff2",
        "woff" => "font/woff",
        "ttf" => "font/ttf",
        "otf" => "font/otf",
        "wasm" => "application/wasm",
        "txt" => "text/plain; charset=utf-8",
        "webmanifest" => "application/manifest+json",
        _ => "application/octet-stream",
    }
}

fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
    let mut entries: Vec<_> = std::fs::read_dir(dir)
        .unwrap_or_else(|e| panic!("web: cannot read {}: {e}", dir.display()))
        .filter_map(Result::ok)
        .collect();
    entries.sort_by_key(|e| e.path());
    for e in entries {
        // hidden files (.DS_Store, .vite/) are not part of the page (1ced7 A3 N1)
        if e.file_name().to_string_lossy().starts_with('.') {
            continue;
        }
        let ft = e
            .file_type()
            .unwrap_or_else(|err| panic!("web: {}: {err}", e.path().display()));
        // symlinks are refused: the bundle is exactly the files the build produced
        if ft.is_symlink() {
            panic!("web: symlink in dist: {}", e.path().display());
        } else if ft.is_dir() {
            walk(&e.path(), out);
        } else if ft.is_file() {
            out.push(e.path());
        }
    }
}

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-env-changed=AIBD_WEB_DIST");
    if std::env::var_os("CARGO_FEATURE_WEB").is_none() {
        // `X-Aibd-Build` = version + this (static_files::BUILD)
        println!("cargo:rustc-env=AIBD_BUILD_DIST=nodist");
        return;
    }
    let manifest = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap_or_default());
    let dist = std::env::var_os("AIBD_WEB_DIST")
        .map(PathBuf::from)
        .unwrap_or_else(|| manifest.join("../../apps/web/dist"));
    let dist = dist.canonicalize().unwrap_or_else(|e| {
        panic!(
            "web: no built front end at {} ({e}); run the apps/web build first, or build \
             aibd without --features web",
            dist.display()
        )
    });
    println!("cargo:rerun-if-changed={}", dist.display());
    let mut files = Vec::new();
    walk(&dist, &mut files);
    let mut code = String::from("pub static ASSETS: &[Asset] = &[\n");
    let mut has_index = false;
    let mut whole = Sha256::new();
    for f in &files {
        let name = f.to_string_lossy();
        if name.ends_with(".gz") || name.ends_with(".br") {
            continue; // attached to their original below
        }
        println!("cargo:rerun-if-changed={}", f.display());
        let rel = f
            .strip_prefix(&dist)
            .unwrap_or_else(|_| panic!("web: {} outside dist", f.display()))
            .components()
            .map(|c| c.as_os_str().to_string_lossy().into_owned())
            .collect::<Vec<_>>()
            .join("/");
        has_index |= rel == "index.html";
        let bytes = std::fs::read(f).unwrap_or_else(|e| panic!("web: {}: {e}", f.display()));
        let etag: String = Sha256::digest(&bytes)[..16]
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        whole.update(rel.as_bytes());
        whole.update([0u8]);
        whole.update(etag.as_bytes());
        whole.update([0u8]);
        let variant = |ext: &str| {
            let p = PathBuf::from(format!("{name}.{ext}"));
            if p.is_file() {
                format!("Some(include_bytes!({:?}))", p.display().to_string())
            } else {
                "None".to_string()
            }
        };
        code += &format!(
            "    Asset {{ path: {rel:?}, content_type: {:?}, body: include_bytes!({:?}), gz: {}, br: {}, etag: {etag:?} }},\n",
            content_type(f),
            f.display().to_string(),
            variant("gz"),
            variant("br"),
        );
    }
    code += "];\n";
    if !has_index {
        panic!("web: {} has no index.html", dist.display());
    }
    let dist_hash: String = whole.finalize()[..8]
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    println!("cargo:rustc-env=AIBD_BUILD_DIST={dist_hash}");
    let out = PathBuf::from(std::env::var("OUT_DIR").unwrap_or_default()).join("web_assets.rs");
    std::fs::write(&out, code).unwrap_or_else(|e| panic!("web: write {}: {e}", out.display()));
}
