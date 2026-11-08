//! Startup sequence: dirs -> single-instance lock -> store + integrity check ->
//! bind loopback -> token + run info -> serve until shutdown -> remove token.

use crate::api::{self, AppState, API_VERSION};
use crate::config::Config;
use crate::guard::GuardConfig;
use crate::platform;
use crate::token::Token;
use aib_store::Store;
use std::future::Future;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;

#[derive(Debug, thiserror::Error)]
pub enum StartError {
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
    #[error("another aibd is already running for this data directory")]
    AlreadyRunning,
    #[error("store: {0}")]
    Store(#[from] aib_store::StoreError),
    #[error("database integrity check failed; refusing to start (restore from backup)")]
    Integrity,
}

/// Information handed back to the caller (and tests) once listening.
#[derive(Debug, Clone)]
pub struct Started {
    pub addr: SocketAddr,
    pub token_path: PathBuf,
    pub run_info_path: PathBuf,
}

pub async fn run(
    cfg: Config,
    shutdown: impl Future<Output = ()> + Send + 'static,
    on_started: impl FnOnce(Started) + Send,
) -> Result<(), StartError> {
    let dirs = platform::OsDirs(cfg.data_dir.clone());
    let run_dir = dirs.run_dir();
    platform::create_private_dir(&dirs.data_dir())?;
    platform::create_private_dir(&run_dir)?;

    let _lock =
        platform::instance_lock(&run_dir.join("aibd.lock"))?.ok_or(StartError::AlreadyRunning)?;
    if let Some(was) = platform::keep_child_zombies() {
        // inherited from whoever started us (SIG_IGN survives exec): without
        // this every group stop would be unverified (ADR 0008 rule 3)
        tracing::warn!(sigchld = was, "SIGCHLD reset to default");
    }

    let db = dirs.data_dir().join("aibd.sqlite");
    let store = tokio::task::spawn_blocking(move || Store::open(&db))
        .await
        .map_err(|e| std::io::Error::other(e.to_string()))??;
    if !store.integrity_ok()? {
        return Err(StartError::Integrity);
    }
    let store = Arc::new(store);

    let listener = tokio::net::TcpListener::bind(cfg.bind).await?;
    let addr = listener.local_addr()?;

    let token = Token::generate()?;
    let token_path = run_dir.join("aibd.token");
    platform::write_private_file(&token_path, token.expose().as_bytes())?;
    let run_info_path = run_dir.join("aibd.json");
    let info = serde_json::json!({
        "port": addr.port(),
        "ip": addr.ip().to_string(),
        "pid": std::process::id(),
        "api_version": API_VERSION,
    });
    platform::write_private_file(&run_info_path, info.to_string().as_bytes())?;

    let guard_cfg = Arc::new(GuardConfig::new(
        addr.port(),
        addr.is_ipv6(),
        cfg.allowed_origins.clone(),
        token,
    ));
    // M1 self-check once, off the async threads (it starts one child).
    let lineage = Arc::new(
        tokio::task::spawn_blocking(crate::lineage::Lineage::detect)
            .await
            .unwrap_or_else(|_| crate::lineage::Lineage::disabled("failed:selfcheck_panicked")),
    );
    tracing::info!(lineage = %lineage.status(), "lineage self-check");
    let app = api::router(AppState { store, lineage }, guard_cfg);
    tracing::info!(%addr, "aibd listening");
    on_started(Started {
        addr,
        token_path: token_path.clone(),
        run_info_path: run_info_path.clone(),
    });

    let served = axum::serve(listener, app)
        .with_graceful_shutdown(shutdown)
        .await;
    let _ = std::fs::remove_file(&token_path);
    let _ = std::fs::remove_file(&run_info_path);
    tracing::info!("aibd stopped");
    served.map_err(StartError::Io)
}
