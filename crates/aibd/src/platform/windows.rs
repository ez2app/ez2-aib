//! Windows: files under %APPDATA% inherit the user-profile ACL (owner, SYSTEM,
//! Administrators). Tightening to owner-only needs an explicit DACL; until that
//! is implemented and tested on a desktop this is UNVERIFIED-desktop.

use std::fs::OpenOptions;
use std::path::Path;

pub fn restrict_dir(_p: &Path) -> std::io::Result<()> {
    Ok(())
}

pub fn private_file_options(_o: &mut OpenOptions) {}
