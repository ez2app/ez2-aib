// SPDX-License-Identifier: Apache-2.0
//! The import of a restore (E3, B3 slice 4): what must hold before a downloaded and
//! checked `.wpress` backup is put into the target site, and what says the import was
//! done. The steps are the EU01 restore line's (SOP v3.5, phase B): the import plugins
//! are brought up to date (an old one fails the import outright), the backup is put where
//! the plugin finds it and the plugin must list it (one it cannot see is a permission
//! fault), then the plugin's command line restores it and only its own "Restore
//! complete" counts.
//!
//! Room is counted twice, in two places:
//! * on the host, by the download gate ([`crate::restoregate`]): the downloaded file;
//! * in the site's own disk, here ([`site_room`]): the backup moved into the site, what
//!   it unpacks to, and the restore point when it is kept in the site; a disk whose room
//!   cannot be read stops the restore.

/// Room left in the site's disk beyond what the import and the restore point take.
pub const SITE_SPARE_BYTES: u64 = 1024 * 1024 * 1024;

/// The plugins the import needs active, brought up to date before every import: the
/// migration plugin and the extension that gives it a command line and no size limit.
pub const IMPORT_PLUGINS: [&str; 2] = [
    "all-in-one-wp-migration",
    "all-in-one-wp-migration-unlimited-extension",
];

/// The plugin's own line for a finished restore.
pub const RESTORE_COMPLETE: &str = "Success: Restore complete.";

/// The site's disk as `df` reads it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SiteRoom {
    pub free: u64,
    pub used: u64,
}

/// Whether the site's disk holds the import of a backup of `size`: the backup and what
/// it unpacks to, the site as it is now when the restore point is kept in the site, and
/// [`SITE_SPARE_BYTES`].
pub fn site_room(
    size: u64,
    room: Option<SiteRoom>,
    point_in_site: bool,
) -> Result<(), &'static str> {
    let room = room.ok_or("site_disk_unknown")?;
    let point = if point_in_site { room.used } else { 0 };
    let need = size
        .checked_mul(2)
        .and_then(|n| n.checked_add(point))
        .and_then(|n| n.checked_add(SITE_SPARE_BYTES));
    match need {
        Some(need) if room.free >= need => Ok(()),
        _ => Err("site_disk_too_small"),
    }
}

/// Whether every plugin in [`IMPORT_PLUGINS`] is in `active`, one name a line (what
/// `plugin list --status=active --field=name` prints).
pub fn plugins_ready(active: &str) -> Result<(), &'static str> {
    let on = |p: &str| active.lines().any(|l| l.trim() == p);
    if IMPORT_PLUGINS.iter().all(|p| on(p)) {
        Ok(())
    } else {
        Err("import_plugin_missing")
    }
}

/// Whether the plugin lists the backup: its name as a whole field of a line of
/// `ai1wm list-backups`.
pub fn backup_listed(listing: &str, name: &str) -> Result<(), &'static str> {
    let seen = listing
        .lines()
        .any(|l| l.split_whitespace().any(|field| field == name));
    if seen {
        Ok(())
    } else {
        Err("backup_not_visible")
    }
}

/// Whether the plugin said the restore was complete, on a line of its own.
pub fn import_finished(output: &str) -> Result<(), &'static str> {
    if output.lines().any(|l| l.trim() == RESTORE_COMPLETE) {
        Ok(())
    } else {
        Err("import_not_confirmed")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const GIB: u64 = 1024 * 1024 * 1024;

    #[test]
    fn the_site_needs_room_for_the_backup_twice_and_a_spare_gib() {
        let room = |free| {
            Some(SiteRoom {
                free,
                used: 3 * GIB,
            })
        };
        assert_eq!(site_room(2 * GIB, room(5 * GIB), false), Ok(()));
        assert_eq!(
            site_room(2 * GIB, room(5 * GIB - 1), false),
            Err("site_disk_too_small")
        );
        assert_eq!(
            site_room(u64::MAX / 2 + 1, room(u64::MAX), false),
            Err("site_disk_too_small")
        );
    }

    #[test]
    fn a_restore_point_kept_in_the_site_is_counted_too() {
        let room = |free| {
            Some(SiteRoom {
                free,
                used: 3 * GIB,
            })
        };
        assert_eq!(site_room(2 * GIB, room(8 * GIB), true), Ok(()));
        assert_eq!(
            site_room(2 * GIB, room(8 * GIB - 1), true),
            Err("site_disk_too_small")
        );
        assert_eq!(
            site_room(2 * GIB, room(5 * GIB), true),
            Err("site_disk_too_small")
        );
    }

    #[test]
    fn a_site_disk_that_cannot_be_read_stops_the_import() {
        assert_eq!(site_room(1, None, false), Err("site_disk_unknown"));
    }

    #[test]
    fn the_import_plugins_must_be_active() {
        let both =
            "akismet\nall-in-one-wp-migration\nall-in-one-wp-migration-unlimited-extension\n";
        assert_eq!(plugins_ready(both), Ok(()));
        assert_eq!(
            plugins_ready(
                "  all-in-one-wp-migration \n all-in-one-wp-migration-unlimited-extension"
            ),
            Ok(())
        );
        assert_eq!(
            plugins_ready("all-in-one-wp-migration\n"),
            Err("import_plugin_missing")
        );
        assert_eq!(
            plugins_ready("all-in-one-wp-migration-unlimited-extension\n"),
            Err("import_plugin_missing")
        );
        assert_eq!(
            plugins_ready(
                "all-in-one-wp-migration-unlimited-extension-old\nall-in-one-wp-migration\n"
            ),
            Err("import_plugin_missing")
        );
        assert_eq!(plugins_ready(""), Err("import_plugin_missing"));
    }

    #[test]
    fn the_plugin_must_list_the_backup() {
        let name = "example-com-20261101-101010-ab12cd.wpress";
        let listing = "+---------------------------------------------+------+\n| Backup name | Date |\n| example-com-20261101-101010-ab12cd.wpress | Nov 1 |\n";
        assert_eq!(backup_listed(listing, name), Ok(()));
        assert_eq!(
            backup_listed(
                "| example-com-20261101-101010-ab12cd.wpress.part | x |",
                name
            ),
            Err("backup_not_visible")
        );
        assert_eq!(
            backup_listed("No backups found.", name),
            Err("backup_not_visible")
        );
        assert_eq!(backup_listed(listing, ""), Err("backup_not_visible"));
    }

    #[test]
    fn only_the_plugins_own_complete_line_counts() {
        assert_eq!(
            import_finished("Restoring...\nSuccess: Restore complete.\n"),
            Ok(())
        );
        assert_eq!(import_finished("  Success: Restore complete.  "), Ok(()));
        assert_eq!(
            import_finished("Restoring...\n"),
            Err("import_not_confirmed")
        );
        assert_eq!(
            import_finished("Error: Restore complete. was not reached\n"),
            Err("import_not_confirmed")
        );
        assert_eq!(import_finished(""), Err("import_not_confirmed"));
    }
}
