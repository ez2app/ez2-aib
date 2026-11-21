#!/usr/bin/env bash
# the seat folder held open (5d33a PREREAD-g5-bridge-7ebca-5d33a-1121 L1 / L2; G5 and G3 share it)
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
sd() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-dmq-store --locked --test seat_dir "$4"; }
# SD1 the seat folder is opened through a link
sd crates/aib-dmq-store/src/fsx.rs \
   '        OFlag::O_RDONLY | OFlag::O_DIRECTORY | OFlag::O_NOFOLLOW | OFlag::O_CLOEXEC' \
   '        OFlag::O_RDONLY | OFlag::O_DIRECTORY | OFlag::O_CLOEXEC // knife' \
   a_seat_folder_that_is_a_link_takes_no_write
# SD2 a file in the folder is reached by path again, so a link swapped in takes the write
sd crates/aib-dmq-store/src/fsx.rs \
   '        let fd = openat(&self.fd, name, at::file_flags(f), at::private())?;' \
   '        let fd = openat(nix::fcntl::AT_FDCWD, &self.path.join(name), at::file_flags(f), at::private())?; // knife' \
   a_folder_changed_into_a_link_after_opening_keeps_the_write_where_it_was_opened
# SD3 the folder lock is a file of its own, so the Python writer is not kept out
sd crates/aib-dmq-store/src/locks.rs \
   '        Level::Directory => dir.lock_handle(DIR_LOCK_FILE),' \
   '        Level::Directory => dir.open_private(DIR_LOCK_FILE), // knife' \
   a_held_folder_lock_holds_the_python_writer
# SD4 the queue folder above the seat folders is opened through a link (5d33a REVIEW-g5dir L1)
sd crates/aib-dmq-store/src/fsx.rs \
   '            let p = open(parent, at::dir_flags(), Mode::empty())?;' \
   '            let p = open(parent, nix::fcntl::OFlag::O_RDONLY | nix::fcntl::OFlag::O_DIRECTORY, Mode::empty())?; // knife' \
   a_queue_folder_that_is_a_link_takes_no_write
