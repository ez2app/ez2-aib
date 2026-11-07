//! Port traits implemented outside the core. Phase 0 fixes the shape of the
//! human-presence proof; process trees, data directories, secrets and
//! notifications live in `aibd::platform` and get core traits once their
//! signatures stop moving.

use alloc::vec::Vec;

/// Proof that a real person was present for one specific operation (spec 9.2).
/// Phase 0 only fixes the shape; there is no implementation and no Risk 3
/// operation until one exists on each OS.
pub trait HumanPresence {
    type Error: core::error::Error;
    /// `digest` binds actor, operation, resource, parameters, nonce and expiry.
    fn prove(&self, prompt: &str, digest: &[u8; 32]) -> Result<Vec<u8>, Self::Error>;
}
