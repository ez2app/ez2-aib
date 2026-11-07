//! Port traits implemented outside the core. Phase 0 defines the platform
//! surface `aibd` needs to start; later phases add process trees, secrets,
//! human presence and notifications with the same pattern.

use std::path::PathBuf;

/// Where aibd keeps its own data. Never the AIB 1.x state root.
pub trait DataDirs {
    /// Root of the aibd data directory (database, run files).
    fn data_dir(&self) -> PathBuf;
    /// Directory for runtime files that only live while aibd runs (token, lock).
    fn run_dir(&self) -> PathBuf {
        self.data_dir().join("run")
    }
}

/// Proof that a real person was present for one specific operation (spec 9.2).
/// Phase 0 only fixes the shape; there is no implementation and no Risk 3
/// operation until one exists on each OS.
pub trait HumanPresence {
    type Error: std::error::Error;
    /// `digest` binds actor, operation, resource, parameters, nonce and expiry.
    fn prove(&self, prompt: &str, digest: &[u8; 32]) -> Result<Vec<u8>, Self::Error>;
}
