//! Read-only guards for DM closure. A verified snapshot is not authority to close:
//! a backend must revalidate it atomically with the state transition.
use serde_json::Value;
use sha2::{Digest, Sha256};

#[derive(Debug, PartialEq, Eq)]
pub struct Snapshot {
    pub id: String,
    pub revision: u64,
    pub digest: String,
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
}
