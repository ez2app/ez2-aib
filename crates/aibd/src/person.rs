//! The person's key on this aibd (519d0 11-18; 0eed7 VERIFY-workspace-0eed7-2436023c M): one
//! key that does not change when a page pairs again.
//!
//! Before, a page was `human:<first 8 bytes of its credential's hash>`: an aibd restart made
//! the credential 401, pairing again made a new key, and the person's threads and contact
//! settings stayed under the old one (`thread_not_found`).
//!
//! - `<data>/person.id`: 16 random bytes in hex, made once (owner only, like the rest of the
//!   data folder). The built-in page's key is `human:<the first 8 bytes>`; every credential
//!   paired for the built-in page is that key.
//! - A credential paired for a user folder page is a second key, `human:<first 8 bytes of
//!   SHA-256("user|" + id)>`: also stable, and its abilities stay the smaller ones (what it may
//!   do is decided on the credential, not on the key).
//! - At start, every other `human:` key in the message library and the contacts file is merged
//!   into the built-in key ([`merge_old_keys`]): they were all this person's pages.
//! - Not a defence (a same-account process reads the file); it names the person, nothing more.

use std::path::Path;
use std::sync::OnceLock;

pub const PERSON_FILE: &str = "person.id";

/// The two keys of the person on this aibd.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Keys {
    pub built_in: String,
    pub user_page: String,
}

static KEYS: OnceLock<Keys> = OnceLock::new();

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// The keys of the id `id` (32 lowercase hex).
pub fn keys_of(id: &str) -> Keys {
    use sha2::Digest;
    let user = sha2::Sha256::digest(format!("user|{id}").as_bytes());
    Keys {
        built_in: format!("human:{}", &id[..16]),
        user_page: format!("human:{}", hex(&user[..8])),
    }
}

fn id_ok(s: &str) -> bool {
    s.len() == 32
        && s.bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}

/// Read `<data>/person.id`, making it first when it is not there.
pub fn load_or_make(data: &Path) -> std::io::Result<Keys> {
    let p = data.join(PERSON_FILE);
    match std::fs::read_to_string(&p) {
        Ok(s) if id_ok(s.trim()) => return Ok(keys_of(s.trim())),
        Ok(_) => {
            return Err(std::io::Error::other(format!(
                "{PERSON_FILE} is not 32 lowercase hex; left as it is"
            )))
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(e),
    }
    let mut b = [0u8; 16];
    getrandom::fill(&mut b).map_err(|e| std::io::Error::other(e.to_string()))?;
    let id = hex(&b);
    crate::platform::write_private_file(&p, id.as_bytes())?;
    Ok(keys_of(&id))
}

/// Set once at start; tests that build an `AppState` by hand leave it unset (the old
/// per-credential key is used then).
pub fn set(k: Keys) {
    let _ = KEYS.set(k);
}

/// The person's key for a credential paired for the built-in page or a user folder page.
pub fn key_for(user_page: bool) -> Option<String> {
    KEYS.get().map(|k| {
        if user_page {
            k.user_page.clone()
        } else {
            k.built_in.clone()
        }
    })
}

/// Every other `human:` key of the library and the contacts file becomes the built-in key.
/// `(rows moved, contact entries moved)`.
pub fn merge_old_keys(
    k: &Keys,
    db: &aib_store::MessageDb,
    book: &crate::contacts::Book,
) -> (
    aib_store::Result<usize>,
    Result<usize, crate::contacts::BookError>,
) {
    let keep = vec![k.built_in.clone(), k.user_page.clone()];
    (
        db.merge_humans(keep.clone(), k.built_in.clone()),
        book.merge_humans(&keep, &k.built_in),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_keys_come_from_the_file_and_stay_the_same() {
        let dir = tempfile::tempdir().unwrap();
        let a = load_or_make(dir.path()).unwrap();
        let b = load_or_make(dir.path()).unwrap();
        assert_eq!(a, b);
        assert!(a.built_in.starts_with("human:") && a.built_in.len() == 22);
        assert!(a.user_page.starts_with("human:") && a.user_page.len() == 22);
        assert_ne!(a.built_in, a.user_page);
        std::fs::write(dir.path().join(PERSON_FILE), "not hex").unwrap();
        assert!(load_or_make(dir.path()).is_err());
    }
}
