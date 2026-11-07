use std::fs::OpenOptions;
use std::io;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::Path;

fn verify_mode(p: &Path, want: u32) -> io::Result<()> {
    let mode = std::fs::metadata(p)?.permissions().mode() & 0o777;
    if mode == want {
        Ok(())
    } else {
        Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!("{} has mode {mode:o}, expected {want:o}", p.display()),
        ))
    }
}

pub fn restrict_dir(p: &Path) -> io::Result<()> {
    std::fs::set_permissions(p, std::fs::Permissions::from_mode(0o700))?;
    verify_mode(p, 0o700)
}

pub fn private_file_options(o: &mut OpenOptions) {
    o.mode(0o600);
}

/// Called on the empty temp file before any secret is written into it.
pub fn restrict_file(p: &Path) -> io::Result<()> {
    // create_new + mode(0o600) already applied; umask can only remove bits.
    std::fs::set_permissions(p, std::fs::Permissions::from_mode(0o600))?;
    verify_mode(p, 0o600)
}
