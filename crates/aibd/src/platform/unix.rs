use std::fs::OpenOptions;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::Path;

pub fn restrict_dir(p: &Path) -> std::io::Result<()> {
    std::fs::set_permissions(p, std::fs::Permissions::from_mode(0o700))
}

pub fn private_file_options(o: &mut OpenOptions) {
    o.mode(0o600);
}
