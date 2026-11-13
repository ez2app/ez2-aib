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

/// How long a start waits for `aibd.lock` before it says `AlreadyRunning`.
///
/// macOS: a program aibd holds between `fork` and `exec` (`aib_sys::held`, #614) keeps copies of
/// every fd of the process, `aibd.lock` included, until it execs or gives up at
/// `aib_sys::held::HOLD_LIMIT` (5 s). A restart right after a crash, or a second server in the
/// same process (the integration tests), would otherwise see the lock taken by a child that is
/// about to let it go (main 74ec2d39 red: session_queue restart test 3/3 on macOS). So wait out
/// one hold, plus a margin. A second aibd that really runs is still refused, 6 s later.
/// Elsewhere nothing is held before exec: no wait.
#[cfg(target_os = "macos")]
pub const LOCK_WAIT: std::time::Duration =
    std::time::Duration::from_millis(aib_sys::held::HOLD_LIMIT as u64 + 1_000);
#[cfg(not(target_os = "macos"))]
pub const LOCK_WAIT: std::time::Duration = std::time::Duration::ZERO;

/// Take the single-instance lock, retrying for up to [`LOCK_WAIT`] on the registered
/// `startup.lock_wait` timer (ADR 0005: no ad-hoc sleeps), off the async runtime.
async fn instance_lock_waiting(p: &std::path::Path) -> Result<std::fs::File, StartError> {
    if let Some(l) = platform::instance_lock(p)? {
        return Ok(l);
    }
    if LOCK_WAIT.is_zero() {
        return Err(StartError::AlreadyRunning);
    }
    let p = p.to_path_buf();
    tokio::task::spawn_blocking(move || {
        let op =
            crate::timers::STARTUP_LOCK_WAIT.begin(Some(std::time::Instant::now() + LOCK_WAIT));
        while op.tick() {
            if let Some(l) = platform::instance_lock(&p)? {
                return Ok(l);
            }
        }
        Err(StartError::AlreadyRunning)
    })
    .await
    .map_err(|e| StartError::Io(std::io::Error::other(e)))?
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

    let _lock = instance_lock_waiting(&run_dir.join("aibd.lock")).await?;
    if let Some(was) = platform::keep_child_zombies() {
        // inherited from whoever started us (SIG_IGN survives exec): without
        // this every group stop would be unverified (ADR 0008 rule 3)
        tracing::warn!(sigchld = was, "SIGCHLD reset to default");
    }

    let db = dirs.data_dir().join("aibd.sqlite");
    platform::private_db_file(&db)?;
    let db2 = db.clone();
    let store = tokio::task::spawn_blocking(move || Store::open(&db2))
        .await
        .map_err(|e| std::io::Error::other(e.to_string()))??;
    platform::restrict_db_sidecars(&db)?;
    if !store.integrity_ok()? {
        return Err(StartError::Integrity);
    }
    // 043dd T81 N1: a stop cut off by the last exit can be asked again (stopping a
    // stopped session does nothing), so its key is given back; a start's stays (it
    // may have started something)
    match store.idem_release_unfinished("DELETE ") {
        Ok(0) => {}
        Ok(n) => tracing::info!(keys = n, "unfinished stop keys of the last run given back"),
        Err(e) => tracing::warn!(error = %e, "unfinished stop keys not given back"),
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

    // pairing (ADR 0006 §7) is opt-in: the terminal confirmation is not an independent
    // channel against a same-account AI (see crate::pairing)
    let channel = crate::pairing::ConfirmChannel::from_env_value(
        std::env::var("AIBD_PAIRING").ok().as_deref(),
    );
    if channel != crate::pairing::ConfirmChannel::Off {
        tracing::warn!(
            channel = channel.as_str(),
            "pairing is on; its confirmation is UNVERIFIED against an AI on the same account"
        );
    }
    let guard_cfg = Arc::new(
        GuardConfig::new(
            addr.port(),
            addr.is_ipv6(),
            cfg.allowed_origins.clone(),
            token,
        )
        .with_pairing(Arc::new(
            crate::pairing::Pairing::default().with_channel(channel),
        )),
    );
    // M1 self-check once, off the async threads (it starts one child).
    let lineage = Arc::new(
        tokio::task::spawn_blocking(crate::lineage::Lineage::detect)
            .await
            .unwrap_or_else(|_| crate::lineage::Lineage::disabled("failed:selfcheck_panicked")),
    );
    tracing::info!(lineage = %lineage.status(), "lineage self-check");
    let sessions_dir = dirs.data_dir().join("sessions");
    platform::create_private_dir(&sessions_dir)?;
    let sessions = crate::sessions::Sessions::new(
        store.clone(),
        Some(lineage.clone()),
        platform::default_spawn(),
        cfg.max_sessions,
        sessions_dir,
    );
    let session_policy = Arc::new(cfg.session_policy.clone());
    // 043dd M2-1: the queue runs the same gate again when a session leaves it
    sessions.set_policy(session_policy.clone());
    // no instance id, no start (#646): checked before the thermal guard reads anything
    api::process_ready()?;
    // R1 thermal guard, dry run (ADR 0005 §1): before the router takes requests,
    // so it hears the first 0 → 1. Its start-up T1 check reads sensors once.
    let thermal = {
        let (st, ss) = (store.clone(), sessions.clone());
        tokio::task::spawn_blocking(move || {
            crate::thermal::Guard::install(platform::thermal_info(), st, &ss)
        })
        .await
        .unwrap_or_else(|e| {
            // 33e28 #640 N2: a panic in install must not read as "no sensor" (both null)
            tracing::error!(error = %e, "thermal guard: install failed; running without it");
            None
        })
    };
    let app = api::router(
        AppState {
            store,
            lineage,
            sessions: sessions.clone(),
            session_policy,
            thermal,
        },
        guard_cfg,
    );
    tracing::info!(%addr, "aibd listening");
    on_started(Started {
        addr,
        token_path: token_path.clone(),
        run_info_path: run_info_path.clone(),
    });

    // D11: on shutdown, first stop every session (new POSTs get 503 while
    // this runs, GET still answers), then close the server.
    let sweep = sessions.clone();
    let shutdown = async move {
        shutdown.await;
        tracing::info!("aibd stopping: stopping sessions");
        let _ = tokio::task::spawn_blocking(move || sweep.shutdown_all()).await;
    };
    let served = axum::serve(listener, app)
        .with_graceful_shutdown(shutdown)
        .await;
    let _ = std::fs::remove_file(&token_path);
    let _ = std::fs::remove_file(&run_info_path);
    tracing::info!("aibd stopped");
    served.map_err(StartError::Io)
}
