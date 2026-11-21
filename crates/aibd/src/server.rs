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
    /// A11: `revive-policy.json` is there but cannot be used (a9136 11-13:
    /// stop with the reason, never fall back to defaults quietly).
    #[error("revive policy: {0}")]
    RevivePolicy(String),
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
    // N-web-dmbridge: <data>/bridge.json is where legacy_send reads its switch
    crate::bridge_cfg::set_dir(&cfg.data_dir);
    // DESIGN-pr-api-7b81e-1121: what a pull request is opened with, where the platform has it
    if let Some(api) = crate::pr_api::platform_api() {
        crate::pr_open::set_api(api);
    }
    crate::gh_view::log_override();
    let run_dir = dirs.run_dir();
    platform::create_private_dir(&dirs.data_dir())?;
    platform::create_private_dir(&run_dir)?;

    let _lock = instance_lock_waiting(&run_dir.join("aibd.lock")).await?;
    let revive_policy =
        crate::alive::RevivePolicy::load(&dirs.data_dir()).map_err(StartError::RevivePolicy)?;
    tracing::info!(
        alive_alert = revive_policy.alive_alert,
        auto_revive = revive_policy.auto_revive,
        alive_after_s = revive_policy.alive_after_s,
        "revive policy"
    );
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
    // N-ws-dismiss-persist: 「暫不處理」 stays off after a restart
    let st0 = store.clone();
    match tokio::task::spawn_blocking(move || crate::slots::reload_taken(&st0)).await {
        Ok(Ok(n)) => tracing::info!(dismissed = n, "slot dismissals replayed"),
        Ok(Err(e)) => tracing::warn!(error = %e, "slot dismissals not replayed"),
        Err(e) => tracing::warn!(error = %e, "slot dismissals not replayed"),
    }
    // S89-talk-2a: the message library is its own file (never a migration of aibd.sqlite,
    // so an older aibd still starts); rows a stop left without their event get one now
    let msg_path = dirs.data_dir().join(aib_store::MESSAGES_FILE);
    platform::private_db_file(&msg_path)?;
    let (mp, st2) = (msg_path.clone(), store.clone());
    // 519d0 11-18: the person's stable key; old per-credential keys are merged into it
    let keys = crate::person::load_or_make(&dirs.data_dir())?;
    let (k2, data2) = (keys.clone(), dirs.data_dir());
    let messages = tokio::task::spawn_blocking(move || {
        let db = aib_store::MessageDb::open(&mp)?;
        let book = crate::contacts::Book::new(&data2);
        match crate::person::merge_old_keys(&k2, &db, &book) {
            (Ok(0), Ok(0)) => {}
            (rows, entries) => tracing::info!(
                rows = ?rows.ok(), contacts = ?entries.ok(),
                "old page keys merged into the person's key"
            ),
        }
        match crate::messages::reconcile(&db, &st2) {
            Ok(0) => {}
            Ok(n) => tracing::info!(rows = n, "messages without their event recovered"),
            Err(e) => tracing::warn!(error = %e, "messages not reconciled"),
        }
        Ok::<_, aib_store::StoreError>(db)
    })
    .await
    .map_err(|e| std::io::Error::other(e.to_string()))??;
    platform::restrict_db_sidecars(&msg_path)?;
    let messages = Arc::new(messages);
    crate::person::set(keys);
    // N-workspace second wave: the assignments are their own file too; one that will not
    // open leaves aibd running and those routes answering 503 (logged here)
    let ws_path = dirs.data_dir().join(aib_store::WORKSPACE_FILE);
    let opened = match platform::private_db_file(&ws_path) {
        Ok(()) => {
            let p = ws_path.clone();
            tokio::task::spawn_blocking(move || aib_store::WorkspaceDb::open(&p).ok())
                .await
                .ok()
                .flatten()
        }
        Err(_) => None,
    };
    match opened {
        Some(db) => {
            if let Err(e) = platform::restrict_db_sidecars(&ws_path) {
                tracing::warn!(error = %e, "workspace file sidecars not restricted");
            }
            let db = Arc::new(db);
            let (d2, s2, dd) = (db.clone(), store.clone(), dirs.data_dir());
            let _ = tokio::task::spawn_blocking(move || {
                crate::workspace::import_at_start(&d2, &s2, &dd);
            })
            .await;
            crate::assign::set_db(db);
        }
        None => tracing::warn!("workspace file not opened; assignments are unavailable"),
    }

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
    if channel == crate::pairing::ConfirmChannel::TouchId {
        tracing::info!("pairing is on; the owner's fingerprint confirms it (crate::touch)");
    } else if channel != crate::pairing::ConfirmChannel::Off {
        tracing::warn!(
            channel = channel.as_str(),
            method = channel.method().unwrap_or(""),
            "WEAK pairing is on; its confirmation is UNVERIFIED against an AI on the same account"
        );
    }
    // S89-dgrs (Andy 11-18 02:5x 「全開」, QUEUE-focus r4 §1): this machine's own switch lets a
    // dgrs seat take a confirmed page's text too; off unless set, and still journaled as
    // verified_person false
    let dgrs_input = crate::seat_input::dgrs_from_env_value(
        std::env::var("AIBD_SEAT_INPUT_DGRS").ok().as_deref(),
    );
    crate::seat_input::set_dgrs_input(dgrs_input);
    if dgrs_input {
        tracing::warn!(
            "a dgrs seat takes typed input (AIBD_SEAT_INPUT_DGRS=1): a page confirmed by a terminal y, \
             which a same-account AI can also type, can drive a seat that asks no tool permission"
        );
    }
    let pairing = Arc::new(crate::pairing::Pairing::default().with_channel(channel));
    // a preview takes the passes its parent hands down stdin (Andy 11-19 08:5x)
    if crate::preview::is_preview() {
        crate::preview::read_passes(pairing.clone());
    }
    if channel == crate::pairing::ConfirmChannel::TouchId {
        // every start checks the key (stored public key, asks for the owner, own files); a key
        // that fails leaves pairing refused, never falls back to the y or the box
        let d = dirs.data_dir();
        match tokio::task::spawn_blocking(move || crate::touch::presence_here(&d)).await {
            Ok(Ok(p)) => {
                // a63 (Andy 11-20): the page's owner asks use the same key, only when switched on
                if crate::owner_ok::switch_on(std::env::var(crate::owner_ok::SWITCH).ok()) {
                    crate::owner_ok::set_presence(p.clone(), pairing.clone());
                    tracing::warn!("the page may ask the owner's fingerprint (AIBD_OWNER_TOUCH=1)");
                }
                pairing.set_presence(p);
            }
            Ok(Err(why)) => tracing::warn!(why, "fingerprint pairing is refused at this start"),
            Err(_) => tracing::warn!("fingerprint pairing is refused: the key check did not end"),
        }
    }
    // SPEC §19d: each pairing step's outcome in the event journal (the tab may close)
    {
        let st = store.clone();
        pairing.set_journal(Arc::new(move |kind, payload| {
            let Ok(id) = crate::sessions::new_id("ev_") else {
                return;
            };
            let _ = st.append(vec![aib_store::NewEvent {
                event_id: id.clone(),
                aggregate_type: "pairing".into(),
                aggregate_id: id,
                sequence: 1,
                event_type: kind.into(),
                correlation_id: None,
                occurred_at: crate::sessions::now_rfc3339(),
                payload,
            }]);
        }));
    }
    // S89-pair-carry: this build's own digest, for telling an update from a restart at stop
    let exe = tokio::task::spawn_blocking(crate::pair_carry::exe)
        .await
        .ok()
        .flatten();
    let carried = match (&exe, channel != crate::pairing::ConfirmChannel::Off) {
        (Some((_, digest)), true) => {
            let (dir, p, d) = (dirs.data_dir(), pairing.clone(), digest.clone());
            let st = store.clone();
            tokio::task::spawn_blocking(move || {
                // 0eed7 M1: one event per credential, sequence 1 of its own aggregate; the store
                // refuses a second sequence 1, so a carry file put back is not taken again
                let claim = |digest: &[u8; 32]| {
                    let key = crate::pair_carry::claim_key(digest);
                    let Ok(id) = crate::sessions::new_id("ev_") else {
                        return false;
                    };
                    st.append(vec![aib_store::NewEvent {
                        event_id: id,
                        aggregate_type: "pairing".into(),
                        aggregate_id: key,
                        sequence: 1,
                        event_type: "pair.carry_taken".into(),
                        correlation_id: None,
                        occurred_at: crate::sessions::now_rfc3339(),
                        payload: serde_json::json!({}),
                    }])
                    .is_ok()
                };
                crate::pair_carry::take_at_start(&dir, &p, &d, crate::pair_carry::now_unix(), claim)
            })
            .await
            .unwrap_or(Err(crate::pair_carry::Skip::Unreadable))
        }
        // pairing off, or no executable to read: a file left behind is still removed
        _ => {
            let _ = std::fs::remove_file(crate::pair_carry::path(&dirs.data_dir()));
            Err(crate::pair_carry::Skip::NoExe)
        }
    };
    let guard_cfg = Arc::new(
        GuardConfig::new(
            addr.port(),
            addr.is_ipv6(),
            cfg.allowed_origins.clone(),
            token,
        )
        .with_pairing(pairing.clone()),
    );
    // M1 self-check once, off the async threads (it starts one child).
    let lineage = Arc::new(
        tokio::task::spawn_blocking(crate::lineage::Lineage::detect)
            .await
            .unwrap_or_else(|_| crate::lineage::Lineage::disabled("failed:selfcheck_panicked")),
    );
    tracing::info!(lineage = %lineage.status(), "lineage self-check");
    // S107-userhtml: pages in <data dir>/web are served before the built-in ones
    crate::static_files::set_user_dir(dirs.data_dir().join("web"));
    crate::workspace::set_data_dir(dirs.data_dir());
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
    // a preview this aibd starts runs under the same policy (519d0 11-19 21:26)
    crate::preview::set_parent_policy(session_policy.clone());
    crate::preview::set_parent_cards(cfg.persona_cards.clone());
    // 043dd M2-1: the queue runs the same gate again when a session leaves it
    sessions.set_policy(session_policy.clone());
    // no instance id, no start (#646): checked before the thermal guard reads anything
    api::process_ready()?;
    // A11 T46: who is alive (no resident timer; see crate::alive)
    let weak = Arc::downgrade(&sessions);
    let rejoin = revive_policy.rejoin_after_restart;
    let alive = crate::alive::Alive::new(store.clone(), revive_policy, move || {
        weak.upgrade().map(|s| s.live_seats()).unwrap_or_default()
    });
    sessions.set_alive(alive.clone());
    let _ = tokio::task::spawn_blocking(move || alive.start()).await;
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
    // S89-events-push: the store wakes open event streams after each write
    crate::events_push::install(&store);
    if let Ok(n) = carried {
        if n > 0 {
            tracing::warn!(
                pairs = n,
                "沿用換版前的配對(來源 terminal),aibd ctl pair --revoke 可撤 \
                 (page pairing carried over the update; safe seats only until paired again)"
            );
            if let Ok(id) = crate::sessions::new_id("ev_") {
                let _ = store.append(vec![aib_store::NewEvent {
                    event_id: id.clone(),
                    aggregate_type: "pairing".into(),
                    aggregate_id: id,
                    sequence: 1,
                    event_type: "pair.carried".into(),
                    correlation_id: None,
                    occurred_at: crate::sessions::now_rfc3339(),
                    payload: serde_json::json!({"pairs": n, "source": "terminal"}),
                }]);
            }
        }
    }
    let state = AppState {
        store,
        lineage,
        sessions: sessions.clone(),
        session_policy,
        thermal,
        personas: Arc::new(crate::personas::CardsFile {
            path: cfg.persona_cards.clone(),
        }),
        contacts: Arc::new(crate::contacts::Book::new(&dirs.data_dir())),
        messages,
    };
    // B1dm-S5: a seat that starts gets what other seats sent it while it was not running
    let (started_tx, mut started_rx) = tokio::sync::mpsc::unbounded_channel::<String>();
    // DESIGN-web-inbox-aibd-7ebca-1121 r1 A: a web seat a bridged letter was put in is told
    // the same way (redeliver, then mail_nudge)
    crate::dm_import::set_wake(started_tx.clone());
    sessions.set_started(started_tx);
    // N-workspace: a seat's next turn starts with what it has not seen of its conversations
    sessions.set_catch_up(Arc::new(crate::catch_up::Library(
        state.messages.clone(),
        state.store.clone(),
    )));
    // G13: a seat's turn answer back into the conversation that called it
    crate::reply_back::start(state.clone());
    // S79-rust 3b: the drive wake's work orders for web seats are sent from here
    crate::drive_send::start(state.clone());
    crate::work_audit::start(state.clone());
    // ORDER-webonly-today 7: the timed sentences the page set, if any
    crate::seat_tick::ensure_started(state.clone());
    // N-webonly 第 4 件: receipts that were under way when aibd stopped are fetched again
    // ... and the runner behind them, only with AIBD_RECEIPT_RUN=1 (b4297's half; without a
    // link it still refuses every request)
    if crate::receipt_runner::set_at_start(
        &state,
        std::env::var(crate::receipt_runner::SWITCH).ok(),
    ) {
        tracing::warn!("a page may ask for receipts on a pinned test machine (AIBD_RECEIPT_RUN=1)");
    }
    crate::receipt_run::ensure_polling(state.clone());
    let redeliver_state = state.clone();
    tokio::spawn(async move {
        while let Some(seat) = started_rx.recv().await {
            crate::seat_send::redeliver(redeliver_state.clone(), seat.clone()).await;
            // N-webonly-c32: then its open messages that are due are named once more
            crate::mail_nudge::nudge(redeliver_state.clone(), seat).await;
        }
    });
    // 5d33a REVIEW-d3wake L1: every seat looked at once, a look-again lost with the stop
    let start_store = state.store.clone();
    tokio::spawn(async move {
        if let Ok(Ok(metas)) = tokio::task::spawn_blocking(move || start_store.seat_metas()).await {
            let ids: Vec<String> = metas.into_iter().map(|m| m.seat_id).collect();
            crate::mail_nudge::at_start(&ids);
        }
    });
    // N-revive: seats running when aibd last stopped on purpose start again, conversation kept
    if rejoin {
        let st = state.clone();
        tokio::spawn(async move {
            let n = crate::revive::after_start(st).await;
            tracing::info!(seats = n, "rejoin after start");
        });
    }
    let app = api::router(state, guard_cfg);
    tracing::info!(%addr, "aibd listening");
    on_started(Started {
        addr,
        token_path: token_path.clone(),
        run_info_path: run_info_path.clone(),
    });

    // D11: on shutdown, first stop every session (new POSTs get 503 while
    // this runs, GET still answers), then close the server.
    let sweep = sessions.clone();
    let carry_dir = dirs.data_dir();
    let shutdown = async move {
        shutdown.await;
        // S89-pair-carry: a clean stop with a new build in place writes the carry file
        if let Some((path, digest)) = exe {
            let written = tokio::task::spawn_blocking(move || {
                crate::pair_carry::write_at_stop(
                    &carry_dir,
                    &pairing,
                    &path,
                    &digest,
                    crate::pair_carry::now_unix(),
                )
            })
            .await;
            match written {
                Ok(Ok(n)) => tracing::info!(pairs = n, "pairing carried to the next build"),
                Ok(Err(why)) => tracing::info!(why = why.as_str(), "pairing not carried"),
                Err(_) => tracing::warn!("pairing carry: writer panicked"),
            }
        }
        tracing::info!("aibd stopping: stopping sessions");
        // open event streams end first, or the graceful stop would wait on them
        crate::events_push::close();
        let _ = tokio::task::spawn_blocking(move || sweep.shutdown_all()).await;
        // §20: the preview build goes with this aibd
        let _ = tokio::task::spawn_blocking(crate::preview::shutdown).await;
    };
    let served = axum::serve(listener, app)
        .with_graceful_shutdown(shutdown)
        .await;
    let _ = std::fs::remove_file(&token_path);
    let _ = std::fs::remove_file(&run_info_path);
    tracing::info!("aibd stopped");
    served.map_err(StartError::Io)
}
