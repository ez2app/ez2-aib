//! Read-only guards for DM closure. A verified snapshot is not authority to close:
//! a backend must revalidate it atomically with the state transition.
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::io::Read;
use std::path::{Path, PathBuf};

const MAX_MESSAGE_BYTES: u64 = 8 * 1024 * 1024;

#[derive(Debug, PartialEq, Eq)]
pub struct Snapshot {
    pub id: String,
    pub revision: u64,
    pub digest: String,
}

/// Read a complete ordinary file, refusing non-files and oversized inputs.
fn read_bounded(path: &Path) -> Result<Vec<u8>, &'static str> {
    let mut options = std::fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW);
    }
    let file = options.open(path).map_err(|_| "file unreadable")?;
    let meta = file.metadata().map_err(|_| "metadata unreadable")?;
    if !meta.is_file() || meta.len() > MAX_MESSAGE_BYTES {
        return Err("not a bounded regular file");
    }
    let mut bytes = Vec::new();
    file.take(MAX_MESSAGE_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "file unreadable")?;
    if bytes.len() as u64 > MAX_MESSAGE_BYTES {
        return Err("file too large");
    }
    Ok(bytes)
}

/// Fully read the referenced relay from a permitted directory. This returns
/// a read-only snapshot; it cannot close a message without a shared lock.
pub fn prepare_from_paths(
    item_path: &Path,
    allowed_relay_dirs: &[PathBuf],
    expected_id: &str,
    expected_revision: u64,
    needle: &str,
) -> Result<Snapshot, &'static str> {
    let item_bytes = read_bounded(item_path)?;
    let item: Value = serde_json::from_slice(&item_bytes).map_err(|_| "invalid item")?;
    let message = item
        .get("msg")
        .and_then(Value::as_str)
        .ok_or("missing message")?
        .trim();
    let relay_bytes = if message.starts_with("長訊已落檔") {
        let path = message
            .split_once("請讀:")
            .map(|(_, p)| p.trim())
            .ok_or("invalid relay pointer")?;
        let path = Path::new(path);
        if !path.is_absolute() {
            return Err("invalid relay pointer");
        }
        let name = path
            .file_name()
            .and_then(|n| n.to_str())
            .ok_or("invalid relay name")?;
        let core = name
            .strip_prefix("RELAY-dm-")
            .and_then(|s| s.strip_suffix(".txt"))
            .ok_or("invalid relay name")?;
        if core.is_empty()
            || !core
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        {
            return Err("invalid relay name");
        }
        let resolved = std::fs::canonicalize(path).map_err(|_| "relay unread")?;
        let parent = resolved
            .parent()
            .ok_or("relay outside permitted directory")?;
        let allowed = allowed_relay_dirs
            .iter()
            .filter_map(|p| std::fs::canonicalize(p).ok())
            .any(|p| p == parent);
        if !allowed {
            return Err("relay outside permitted directory");
        }
        Some(read_bounded(&resolved)?)
    } else {
        None
    };
    prepare(
        &item_bytes,
        relay_bytes.as_deref(),
        expected_id,
        expected_revision,
        needle,
    )
}

/// Validate the exact item and, when indicated by its message, its already-read
/// relay bytes. `relay_path` is resolved by the caller, not trusted as authority.
pub fn prepare(
    item_bytes: &[u8],
    relay_bytes: Option<&[u8]>,
    expected_id: &str,
    expected_revision: u64,
    needle: &str,
) -> Result<Snapshot, &'static str> {
    if needle.is_empty() {
        return Err("empty expect");
    }
    let item: Value = serde_json::from_slice(item_bytes).map_err(|_| "invalid item")?;
    let id = item.get("id").and_then(Value::as_str).ok_or("missing id")?;
    if id != expected_id {
        return Err("id mismatch");
    }
    let revision = item
        .get("rev")
        .and_then(Value::as_u64)
        .ok_or("missing revision")?;
    if revision != expected_revision {
        return Err("revision mismatch");
    }
    let message = item
        .get("msg")
        .and_then(Value::as_str)
        .ok_or("missing message")?;
    // A relay notification without its actual bytes must never be closed based
    // only on its placeholder. Detection is deliberately fail-closed.
    let needs_relay = message.contains("RELAY-dm-") && message.contains(".txt");
    if needs_relay && relay_bytes.is_none() {
        return Err("relay unread");
    }
    let relay = match relay_bytes {
        Some(b) => std::str::from_utf8(b).map_err(|_| "invalid relay")?,
        None => "",
    };
    if !message.contains(needle) && !relay.contains(needle) {
        return Err("expect mismatch");
    }
    let mut h = Sha256::new();
    h.update((item_bytes.len() as u64).to_be_bytes());
    h.update(item_bytes);
    match relay_bytes {
        Some(b) => {
            h.update([1]);
            h.update((b.len() as u64).to_be_bytes());
            h.update(b);
        }
        None => h.update([0]),
    }
    Ok(Snapshot {
        id: id.to_owned(),
        revision,
        digest: format!("{:x}", h.finalize()),
    })
}

/// Compare inside the backend's shared mutation lock, immediately before commit.
/// Calling this outside that lock does not eliminate races.
pub fn verify_snapshot(
    snapshot: &Snapshot,
    current_item: &[u8],
    current_relay: Option<&[u8]>,
    needle: &str,
) -> Result<(), &'static str> {
    let now = prepare(
        current_item,
        current_relay,
        &snapshot.id,
        snapshot.revision,
        needle,
    )?;
    if now.digest != snapshot.digest {
        return Err("fingerprint mismatch");
    }
    Ok(())
}

/// Re-read both paths while the shared backend lock is held, then compare.
/// The caller must keep that same lock through the actual transition.
pub fn verify_snapshot_from_paths(
    snapshot: &Snapshot,
    item_path: &Path,
    allowed_relay_dirs: &[PathBuf],
    needle: &str,
) -> Result<(), &'static str> {
    let now = prepare_from_paths(
        item_path,
        allowed_relay_dirs,
        &snapshot.id,
        snapshot.revision,
        needle,
    )?;
    if now.digest != snapshot.digest {
        return Err("fingerprint mismatch");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    const MSG: &[u8] = br#"{"id":"q_1234567890abcdef","rev":2,"msg":"Read /tmp/RELAY-dm-x.txt"}"#;
    #[test]
    fn relay_is_required_and_content_checked() {
        assert_eq!(
            prepare(MSG, None, "q_1234567890abcdef", 2, "specific").unwrap_err(),
            "relay unread"
        );
        assert_eq!(
            prepare(MSG, Some(b"wrong"), "q_1234567890abcdef", 2, "specific").unwrap_err(),
            "expect mismatch"
        );
        assert!(prepare(MSG, Some(b"specific"), "q_1234567890abcdef", 2, "specific").is_ok());
    }
    #[test]
    fn id_revision_or_relay_change_refuses() {
        let snap = prepare(MSG, Some(b"specific"), "q_1234567890abcdef", 2, "specific").unwrap();
        assert_eq!(
            verify_snapshot(&snap, MSG, Some(b"specific changed"), "specific").unwrap_err(),
            "fingerprint mismatch"
        );
        assert!(prepare(MSG, Some(b"specific"), "q_wrong", 2, "specific").is_err());
        assert!(prepare(MSG, Some(b"specific"), "q_1234567890abcdef", 3, "specific").is_err());
        assert!(verify_snapshot(&snap, MSG, None, "specific").is_err());
    }
    #[test]
    fn real_relay_is_read_and_must_be_in_allowed_directory() {
        let dir = tempfile::tempdir().unwrap();
        let elsewhere = tempfile::tempdir().unwrap();
        let relay = dir.path().join("RELAY-dm-test.txt");
        std::fs::write(&relay, "unique marker").unwrap();
        let item = dir.path().join("item.json");
        let msg = format!("長訊已落檔(13 位元組),請讀:{}", relay.display());
        std::fs::write(
            &item,
            serde_json::json!({"id":"q_1234567890abcdef","rev":2,"msg":msg}).to_string(),
        )
        .unwrap();
        assert!(prepare_from_paths(
            &item,
            &[dir.path().to_path_buf()],
            "q_1234567890abcdef",
            2,
            "unique marker"
        )
        .is_ok());
        let snapshot = prepare_from_paths(
            &item,
            &[dir.path().to_path_buf()],
            "q_1234567890abcdef",
            2,
            "unique marker",
        )
        .unwrap();
        std::fs::write(&relay, "unique marker changed").unwrap();
        assert_eq!(
            verify_snapshot_from_paths(
                &snapshot,
                &item,
                &[dir.path().to_path_buf()],
                "unique marker"
            )
            .unwrap_err(),
            "fingerprint mismatch"
        );
        assert_eq!(
            prepare_from_paths(
                &item,
                &[elsewhere.path().to_path_buf()],
                "q_1234567890abcdef",
                2,
                "unique marker"
            )
            .unwrap_err(),
            "relay outside permitted directory"
        );
        std::fs::remove_file(relay).unwrap();
        assert!(prepare_from_paths(
            &item,
            &[dir.path().to_path_buf()],
            "q_1234567890abcdef",
            2,
            "unique marker"
        )
        .is_err());
    }
}
