//! N-revive (a9136 11-18, ORDER-workspace-519d0-1118 第三波 1; Andy: every swap stopped his
//! seats and broke the talk): when aibd starts, a seat whose session was running when aibd
//! stopped on purpose (`ended_by = shutdown`, the version swap) is started again with its own
//! conversation picked up (`--resume <the id it was opened with>`), in its own folder, mode,
//! model and persona. What other seats sent it meanwhile is delivered by the usual start hook
//! (`seat_send::redeliver`).
//!
//! Not picked up again:
//! - a seat stopped by the person or that ended by itself (`api`, `timeout`, `root_exit`…):
//!   that was the seat's end;
//! - a session cut by a crash (`aibd_restart`): its processes may still run on macOS (the
//!   store says so), and two processes on one conversation would write over each other; the
//!   alive check reports it as before;
//! - a dgrs seat on a machine where a dgrs seat takes no input (5d33a wave-3 G1);
//! - an archived seat, a provider that cannot be resumed, a persona card no longer there.
//!
//! Which conversation: [`conversation`] (resume only one claude kept a file for; a seat that
//! never had a turn starts its id fresh; a seat opened before this slice takes its folder's
//! newest conversation and keeps that id from now on).
//!
//! Each one picked up writes `seat.rejoined {seat_id, session_id}`; each one passed over that
//! was running writes `seat.rejoin_skipped {seat_id, reason}`; one picked up that ends by
//! itself within [`crate::sessions::REJOIN_FAIL_WINDOW`], not with 0, writes
//! `seat.rejoin_failed {seat_id, session_id, exit_code}` (L7). Off with
//! `"rejoin_after_restart": false` in `revive-policy.json`.

use crate::api::AppState;
use aib_store::{NewEvent, Seat, SeatMeta, SessionRow};

/// Why a seat that was running is not picked up again.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Skip {
    NoConversationId,
    Provider,
    NoMode,
    PersonaGone,
    PersonaText,
    Start,
    DgrsOff,
    StopUnconfirmed,
    Folder,
}

impl Skip {
    pub fn as_str(self) -> &'static str {
        match self {
            Skip::NoConversationId => "no_conversation_id",
            Skip::Provider => "provider_cannot_resume",
            Skip::NoMode => "no_mode",
            Skip::PersonaGone => "persona_missing",
            Skip::PersonaText => "persona_text_unreadable",
            Skip::Start => "start_failed",
            Skip::DgrsOff => "dgrs_input_off",
            Skip::StopUnconfirmed => "stop_unconfirmed",
            Skip::Folder => "folder_refused",
        }
    }
}

/// What to do with a seat at start: `None` leaves it alone (archived, never ran, stopped on
/// purpose, cut by a crash); `Some(Ok)` picks it up again: its last session ended because aibd
/// stopped on purpose (`shutdown`) and that end is confirmed (`killed`: aibd stopped it and saw
/// the process tree gone (sessions::finish); `interrupted`: it was still waiting and never had
/// a process). `Some(Err)`:
/// it ended with the stop but not confirmed gone (`stop_unconfirmed`), so its process may still
/// run and a second one on the same conversation would clash (5d33a a77fee24 M1).
///
/// Only the last stop counts (f1fae 13:10 swap: two seats a stop before had ended and been
/// skipped; the next start took them up though nobody ran them since): the session must have
/// ended within [`STOP_WINDOW`] seconds of `last_stop`, the newest shutdown end of any seat.
pub fn at_stop(
    seat: &Seat,
    sessions: &[SessionRow],
    last_stop: Option<i64>,
) -> Option<Result<(), Skip>> {
    if seat.state == aib_store::ARCHIVED {
        return None;
    }
    let last = sessions.last()?;
    if last.ended_by.as_deref() != Some("shutdown") {
        return None;
    }
    let ended = last.ended_at.as_deref().and_then(unix_of)?;
    if ended < last_stop? - STOP_WINDOW {
        return None;
    }
    // a stop that kept its slot (not confirmed gone) is turned `interrupted` by the next
    // start, keeping who ended it (0eed7 51ac92d9 M1): its recorded stop still decides
    let state = match last.state.as_str() {
        "interrupted" if last.stop_status.is_some() => "stop_unconfirmed",
        s => s,
    };
    Some(match state {
        "killed" | "interrupted" => Ok(()),
        // macOS under launchd (measured 11-18 on the live aibd, every shutdown stop):
        // unverified only because other processes share the coalition; the seat's own tree was
        // seen clean (no survivor) and its root's end was recorded. The provider that writes the
        // conversation is that root, so a second one does not clash with it.
        "stop_unconfirmed"
            if last.stop_reason.as_deref() == Some("observed_clean")
                && (last.exit_code.is_some() || last.signal.is_some()) =>
        {
            Ok(())
        }
        // 5d33a 11-18 (N-ws-revive-held): what kept it unverified were only processes that
        // started before the seat's provider (pid and start time read together, against the
        // provider's start recorded at the start), and the provider's own end was recorded:
        // none of them can be the provider or its child. Said as seat.rejoin_ignored_older.
        "stop_unconfirmed" if older_and_ended(last) => Ok(()),
        _ => Err(Skip::StopUnconfirmed),
    })
}

/// claude's folder for a seat working in `cwd`, named from the folder's real path (0eed7
/// eb0bf164 M2: claude names it from the realpath — `/var/…` is `/private/var/…` on macOS, a
/// linked project folder is its target, `//` is one — so the written cwd missed the kept
/// conversation and the seat was started fresh on an id claude already had, and refused).
/// The cwd as written when it cannot be resolved. Blocking.
pub fn claude_dir_for(config: &std::path::Path, cwd: &str) -> std::path::PathBuf {
    let real = std::fs::canonicalize(cwd)
        .ok()
        .and_then(|p| p.to_str().map(str::to_owned))
        .unwrap_or_else(|| cwd.to_owned());
    aib_seatopen::claude_project_dir(config, &real)
}

/// The stop reason of a stop whose only unreadable candidates started before the session's
/// root (`platform::tree`, 5d33a 11-18).
pub const OLDER: &str = "candidates_unreadable_older";

/// Only processes older than the provider kept the stop unverified, and the provider's own
/// end was recorded (5d33a 11-18).
fn older_and_ended(s: &SessionRow) -> bool {
    s.stop_reason.as_deref() == Some(OLDER) && (s.exit_code.is_some() || s.signal.is_some())
}

/// The session a rejoin passes over older unreadable processes for (its last session, when
/// that stop's reason is [`OLDER`]): said as `seat.rejoin_ignored_older`.
pub fn ignored_older(sessions: &[SessionRow]) -> Option<&SessionRow> {
    sessions
        .last()
        .filter(|s| s.stop_reason.as_deref() == Some(OLDER))
}

/// How many of a session's events are read to find its stop (a seat's session has a few).
const OLDER_EVENTS_READ: u32 = 1000;

/// The processes a rejoin lets go (519d0 11-18 23:04): of the session's last
/// `session.stop_unconfirmed`, each unreadable candidate with the start the stop read, as
/// `{session_id, pid, comm, started}` (no arguments). Its stop said nothing ⇒ none.
pub fn older_processes(
    session_id: &str,
    events: &[aib_store::StoredEvent],
) -> Vec<serde_json::Value> {
    let Some(stop) = events
        .iter()
        .rev()
        .find(|e| e.event_type == "session.stop_unconfirmed")
    else {
        return Vec::new();
    };
    stop.payload["candidates"]["unreadable"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter(|c| c["started"].is_u64())
                .map(|c| {
                    serde_json::json!({"session_id": session_id, "pid": c["pid"],
                        "comm": c["comm"], "started": c["started"]})
                })
                .collect()
        })
        .unwrap_or_default()
}

/// How long one stop takes to end every seat (its sweep runs in seconds; this is wide).
pub const STOP_WINDOW: i64 = 300;

/// The newest end by a stop of aibd among `sessions` (Unix seconds).
pub fn last_stop<'a>(sessions: impl IntoIterator<Item = &'a SessionRow>) -> Option<i64> {
    sessions
        .into_iter()
        .filter(|s| s.ended_by.as_deref() == Some("shutdown"))
        .filter_map(|s| s.ended_at.as_deref().and_then(unix_of))
        .max()
}

/// Unix seconds of a UTC RFC 3339 time as aibd writes it (`2026-11-18T04:25:58.371Z`).
pub fn unix_of(t: &str) -> Option<i64> {
    let b = t.as_bytes();
    if b.len() < 19
        || b[4] != b'-'
        || b[7] != b'-'
        || b[10] != b'T'
        || b[13] != b':'
        || b[16] != b':'
    {
        return None;
    }
    let n = |a: usize, z: usize| t.get(a..z)?.parse::<i64>().ok();
    let (y, mo, d) = (n(0, 4)?, n(5, 7)?, n(8, 10)?);
    let (h, mi, se) = (n(11, 13)?, n(14, 16)?, n(17, 19)?);
    // days from 1970-01-01 (civil calendar)
    let (y2, m2) = if mo <= 2 {
        (y - 1, mo + 9)
    } else {
        (y, mo - 3)
    };
    let era = y2.div_euclid(400);
    let yoe = y2 - era * 400;
    let doy = (153 * m2 + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    Some(days * 86_400 + h * 3600 + mi * 60 + se)
}

/// 5d33a wave-3 G1: a dgrs seat comes back only on a machine that lets a dgrs seat take
/// input (`AIBD_SEAT_INPUT_DGRS=1`, `dgrs_on`); with it off the seat stays stopped rather
/// than run with no one able to reach it.
pub fn plan(meta: &SeatMeta, dgrs_on: bool) -> Result<String, Skip> {
    if !aib_seatopen::PIPE_PROVIDERS.contains(&meta.provider.as_str()) {
        return Err(Skip::Provider);
    }
    let Some(mode) = meta.mode.clone() else {
        return Err(Skip::NoMode);
    };
    if mode == "dgrs" && !dgrs_on {
        return Err(Skip::DgrsOff);
    }
    Ok(mode)
}

/// Which conversation the seat goes on with, and whether there is one to resume:
/// - its kept id with claude's file for it ⇒ resume it;
/// - its kept id with no file (the seat never had a turn: claude keeps nothing and refuses
///   `--resume`) ⇒ start that id fresh;
/// - no kept id (a seat opened before this slice) ⇒ when `own_folder` (the seat works in its
///   own `seat-work/<seat>`, nobody else's conversations are there) the newest conversation
///   claude has for it, resumed; otherwise, or none there, a new id, fresh. A folder given at
///   open may be shared by two seats or hold the person's own Claude talks: taking its newest
///   would give one conversation two writers or hand a seat the person's talk (5d33a
///   63e75bff M1).
///
/// `dir` is claude's folder for the seat's cwd ([`aib_seatopen::claude_project_dir`]).
/// Blocking (reads the folder).
pub fn conversation(
    kept: Option<&str>,
    dir: Option<&std::path::Path>,
    own_folder: bool,
    new_id: impl FnOnce() -> Option<String>,
) -> Option<(String, bool)> {
    let has = |id: &str| dir.is_some_and(|d| d.join(format!("{id}.jsonl")).is_file());
    if let Some(id) = kept.filter(|s| !s.is_empty()) {
        return Some((id.to_owned(), has(id)));
    }
    let newest = dir
        .filter(|_| own_folder)
        .and_then(|d| std::fs::read_dir(d).ok())
        .into_iter()
        .flatten()
        .filter_map(Result::ok)
        .filter_map(|e| {
            let name = e.file_name().into_string().ok()?;
            let id = name.strip_suffix(".jsonl")?.to_owned();
            let at = e.metadata().ok()?.modified().ok()?;
            uuid_shaped(&id).then_some((at, id))
        })
        .max();
    match newest {
        Some((_, id)) => Some((id, true)),
        None => new_id().map(|id| (id, false)),
    }
}

fn uuid_shaped(s: &str) -> bool {
    s.len() == 36
        && s.bytes().enumerate().all(|(i, b)| match i {
            8 | 13 | 18 | 23 => b == b'-',
            _ => b.is_ascii_hexdigit(),
        })
}

async fn note(st: &AppState, kind: &str, payload: serde_json::Value) {
    let store = st.store.clone();
    let kind = kind.to_owned();
    // its own aggregate (as assignment events do): no race with the seat's own sequence
    let _ = tokio::task::spawn_blocking(move || {
        let id = crate::sessions::new_id("ev_").ok()?;
        store
            .append(vec![NewEvent {
                event_id: id.clone(),
                aggregate_type: "rejoin".into(),
                aggregate_id: id,
                sequence: 1,
                event_type: kind,
                correlation_id: None,
                occurred_at: crate::sessions::now_rfc3339(),
                payload,
            }])
            .ok()
    })
    .await;
}

/// Pick up again every seat that was running when aibd stopped on purpose. Runs once at
/// start, after the router is built; each seat on its own (one failing does not stop the
/// rest). Returns how many were started.
pub async fn after_start(st: AppState) -> usize {
    let store = st.store.clone();
    let listed = tokio::task::spawn_blocking(move || -> aib_store::Result<_> {
        let seats = store.seats()?;
        let metas = store.seat_metas()?;
        let mut all = Vec::new();
        for s in seats {
            let ss = store.sessions_of_seat(&s.seat_id)?;
            all.push((s, ss));
        }
        let stop = last_stop(all.iter().flat_map(|(_, ss)| ss.iter()));
        let mut out = Vec::new();
        for (s, ss) in all {
            if let Some(what) = at_stop(&s, &ss, stop) {
                let m = metas.iter().find(|m| m.seat_id == s.seat_id).cloned();
                let older = match ignored_older(&ss).filter(|_| what.is_ok()) {
                    Some(r) => {
                        let evs = store.events_of(&r.session_id, 0, OLDER_EVENTS_READ)?;
                        older_processes(&r.session_id, &evs)
                    }
                    None => Vec::new(),
                };
                out.push((s, m, what, older));
            }
        }
        Ok(out)
    })
    .await;
    let Ok(Ok(todo)) = listed else {
        tracing::warn!("rejoin: seats not read; none picked up again");
        return 0;
    };
    let mut started = 0;
    for (seat, meta, what, older) in todo {
        for mut p in older {
            tracing::info!(seat = %seat.seat_id, process = %p,
                "rejoin: a process older than the seat's provider let go");
            p["seat_id"] = serde_json::json!(seat.seat_id);
            note(&st, "seat.rejoin_ignored_older", p).await;
        }
        let done = match what {
            Ok(()) => rejoin(&st, &seat, meta).await,
            Err(why) => Err(why),
        };
        match done {
            Ok(session_id) => {
                started += 1;
                if !st.sessions.mark_rejoined(&session_id) {
                    // it ended before it could be watched (a 0.1 s exit)
                    let rec = st.sessions.get(&session_id);
                    // not ended yet (only not watched) ⇒ nothing said (5d33a f2f57e36 L1)
                    let failed = rec.as_ref().is_some_and(|r| {
                        r.ended_by.is_some_and(|by| {
                            crate::sessions::rejoin_failed(
                                by,
                                r.exit_code,
                                r.signal.as_deref(),
                                std::time::Duration::ZERO,
                            )
                        })
                    });
                    if failed {
                        note(
                            &st,
                            "seat.rejoin_failed",
                            serde_json::json!({"seat_id": seat.seat_id, "session_id": session_id,
                                "exit_code": rec.as_ref().and_then(|r| r.exit_code),
                                "signal": rec.as_ref().and_then(|r| r.signal.clone())}),
                        )
                        .await;
                    }
                }
                note(
                    &st,
                    "seat.rejoined",
                    serde_json::json!({"seat_id": seat.seat_id, "session_id": session_id}),
                )
                .await;
            }
            Err(why) => {
                tracing::warn!(seat = %seat.seat_id, reason = why.as_str(), "rejoin skipped");
                note(
                    &st,
                    "seat.rejoin_skipped",
                    serde_json::json!({"seat_id": seat.seat_id, "reason": why.as_str()}),
                )
                .await;
            }
        }
    }
    started
}

/// Why a page's restart did not start the seat.
#[derive(Debug, PartialEq, Eq)]
pub enum NotRestarted {
    /// no such seat
    Unknown,
    /// the seat is archived
    Archived,
    /// it runs now (or another restart of it is under way)
    Running,
    /// what a rejoin would skip it for
    Skip(Skip),
    /// the library could not be read
    Unread,
}

/// The seats being restarted now: a second press while the first starts is `Running`.
fn restarting() -> &'static std::sync::Mutex<std::collections::HashSet<String>> {
    static R: std::sync::OnceLock<std::sync::Mutex<std::collections::HashSet<String>>> =
        std::sync::OnceLock::new();
    R.get_or_init(Default::default)
}

/// 043dd 4b09556d1 / 0eed7 L7: the page's 重開 on a seat whose rejoin failed (or any stopped
/// seat): the same start a rejoin makes — its own provider, mode and persona, the conversation
/// it kept — when it does not run now. The caller ([`crate::seat_input`]) refused a user folder
/// page and a carried pairing; a dgrs seat without this machine's switch is refused here
/// (`Skip::DgrsOff`, as a rejoin). Writes `seat.restarted {seat_id, session_id, by}`.
pub async fn restart(
    st: &AppState,
    seat_id: &str,
    by: serde_json::Value,
) -> Result<String, NotRestarted> {
    // held for this restart and let go however it ends, a dropped request too (5d33a
    // PREREAD 532b54da L1: a page whose connection broke left the seat 409 for good)
    struct Held(String);
    impl Drop for Held {
        fn drop(&mut self) {
            restarting()
                .lock()
                .unwrap_or_else(|x| x.into_inner())
                .remove(&self.0);
        }
    }
    let _held = {
        let mut r = restarting().lock().unwrap_or_else(|x| x.into_inner());
        if !r.insert(seat_id.to_owned()) {
            return Err(NotRestarted::Running);
        }
        Held(seat_id.to_owned())
    };
    let session_id = restart_once(st, seat_id).await?;
    note(
        st,
        "seat.restarted",
        serde_json::json!({"seat_id": seat_id, "session_id": session_id, "by": by}),
    )
    .await;
    Ok(session_id)
}

async fn restart_once(st: &AppState, seat_id: &str) -> Result<String, NotRestarted> {
    let runs =
        st.sessions.list().iter().any(|s| {
            s.seat_id.as_deref() == Some(seat_id) && matches!(s.state, "running" | "pending")
        });
    if runs {
        return Err(NotRestarted::Running);
    }
    let (store, id) = (st.store.clone(), seat_id.to_owned());
    let got = tokio::task::spawn_blocking(move || -> aib_store::Result<_> {
        Ok((store.seat(&id)?, store.seat_meta(&id)?))
    })
    .await;
    let Ok(Ok((seat, meta))) = got else {
        return Err(NotRestarted::Unread);
    };
    let seat = seat.ok_or(NotRestarted::Unknown)?;
    if aib_store::ARCHIVED == seat.state {
        return Err(NotRestarted::Archived);
    }
    rejoin(st, &seat, meta).await.map_err(NotRestarted::Skip)
}

async fn rejoin(st: &AppState, seat: &Seat, meta: Option<SeatMeta>) -> Result<String, Skip> {
    let meta = meta.ok_or(Skip::NoConversationId)?;
    let mode = plan(&meta, crate::seat_input::dgrs_input_on())?;
    let personas = st.personas.clone();
    let cards = tokio::task::spawn_blocking(move || personas.cards())
        .await
        .ok()
        .and_then(Result::ok)
        .ok_or(Skip::PersonaGone)?;
    let uid = meta.persona_uid.clone().ok_or(Skip::PersonaGone)?;
    let card = cards
        .into_iter()
        .find(|c| c.uid == uid)
        .ok_or(Skip::PersonaGone)?;
    // the same folder checks as an open (5d33a a77fee24 L1): still a folder, and no project
    // settings appeared there meanwhile (they would be loaded: --setting-sources project,local)
    let dir = std::path::PathBuf::from(&meta.cwd);
    let home = crate::seats::home_dir(|k| std::env::var(k).ok());
    let folder_ok = tokio::task::spawn_blocking(move || {
        dir.is_dir() && crate::seats::project_settings_in(&dir, home.as_deref()).is_none()
    })
    .await
    .unwrap_or(false);
    if !folder_ok {
        return Err(Skip::Folder);
    }
    let home = crate::seats::home_dir(|k| std::env::var(k).ok());
    let config = std::env::var_os("CLAUDE_CONFIG_DIR")
        .map(std::path::PathBuf::from)
        .or_else(|| home.map(|h| h.join(".claude")));
    let (kept, cwd) = (meta.resume_id.clone(), meta.cwd.clone());
    let own = crate::seats::seat_home(st.sessions.private_dir(), &seat.seat_id);
    let picked = tokio::task::spawn_blocking(move || {
        let canon =
            |p: &std::path::Path| std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf());
        let own_folder = canon(std::path::Path::new(&cwd)) == canon(&own);
        let dir = config.map(|c| claude_dir_for(&c, &cwd));
        conversation(kept.as_deref(), dir.as_deref(), own_folder, || {
            crate::seats::new_uuid().ok()
        })
    })
    .await
    .ok()
    .flatten();
    let (conv, resume) = picked.ok_or(Skip::NoConversationId)?;
    let argv = if resume {
        aib_seatopen::resume_argv(&meta.provider, &mode, meta.model.as_deref(), &conv)
    } else {
        aib_seatopen::pipe_argv(&meta.provider, &mode, meta.model.as_deref(), &conv)
    };
    let mut argv = argv.map_err(|_| Skip::Provider)?;
    // a seat opened before this slice: keep the id now, so the next restart finds it
    if meta.resume_id.is_none() {
        let (store, seat_id, id) = (st.store.clone(), seat.seat_id.clone(), conv.clone());
        let _ = tokio::task::spawn_blocking(move || {
            let ev = crate::sessions::new_id("ev_").ok()?;
            store
                .fill_resume_id(&seat_id, &id, &ev, &crate::sessions::now_rfc3339())
                .ok()
        })
        .await;
    }
    // the same two talk tools and the same persona as at open (seats::open)
    let exe = std::env::current_exe().map_err(|_| Skip::Start)?;
    let exe = exe.to_str().ok_or(Skip::Start)?.to_owned();
    let private = st.sessions.private_dir().to_path_buf();
    let data = private
        .parent()
        .and_then(|d| d.to_str())
        .map(str::to_owned)
        .ok_or(Skip::Start)?;
    let (p2, c2) = (private.clone(), conv.clone());
    let cfg =
        tokio::task::spawn_blocking(move || crate::seats::keep_talk_config(&p2, &c2, &exe, &data))
            .await
            .ok()
            .and_then(Result::ok)
            .and_then(|p| p.to_str().map(str::to_owned))
            .ok_or(Skip::Start)?;
    argv.extend(crate::seats::talk_args(&cfg, &mode));
    if let Some(path) = card.text_path.clone() {
        let sha = card.persona_sha.clone();
        let file = tokio::task::spawn_blocking(move || {
            let text = crate::personas::card_text(&path, &sha).ok()?;
            crate::personas::keep_text(&private, &sha, &text).ok()
        })
        .await
        .ok()
        .flatten()
        .and_then(|p| p.to_str().map(str::to_owned))
        .ok_or(Skip::PersonaText)?;
        if !aib_seatopen::add_persona_file(&meta.provider, &mut argv, &file) {
            return Err(Skip::Provider);
        }
    }
    let session = crate::seats::start_session(st, &seat.seat_id, &card, Ok(argv), &meta.cwd, &mode)
        .await
        .map_err(|_| Skip::Start)?;
    Ok(session["id"].as_str().unwrap_or_default().to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn seat(state: &str) -> Seat {
        Seat {
            seat_id: "seat_a".into(),
            name: "a".into(),
            state: state.into(),
            revision: 1,
            created_at: String::new(),
            updated_at: String::new(),
        }
    }

    const AT: &str = "2026-11-18T05:10:00.000Z";

    /// at_stop with the last stop taken from these sessions (one seat's view)
    fn at(s: &Seat, ss: &[SessionRow]) -> Option<Result<(), Skip>> {
        at_stop(s, ss, last_stop(ss.iter()))
    }

    fn ended(by: Option<&str>) -> SessionRow {
        ended_as(by, "killed")
    }

    fn ended_as(by: Option<&str>, state: &str) -> SessionRow {
        SessionRow {
            session_id: "s".into(),
            seat_id: Some("seat_a".into()),
            pid: 1,
            argv0: None,
            argc: 1,
            state: state.into(),
            holds_slot: false,
            started_at: String::new(),
            ended_at: Some(AT.into()),
            ended_by: by.map(str::to_owned),
            exit_code: None,
            signal: None,
            stop_status: None,
            stop_reason: None,
            updated_at: String::new(),
        }
    }

    fn meta(mode: &str, id: Option<&str>) -> SeatMeta {
        SeatMeta {
            seat_id: "seat_a".into(),
            provider: "claude".into(),
            role: None,
            cwd: "/w".into(),
            model: None,
            model_pinned: false,
            resume_id: id.map(str::to_owned),
            project_uid: None,
            persona_uid: Some("p".into()),
            brain_route: None,
            updated_at: String::new(),
            mode: Some(mode.into()),
            axis_id: None,
            cabin: None,
        }
    }

    /// G1: same mode back; a dgrs seat only where dgrs takes input; no id ⇒ not picked up.
    #[test]
    fn a_dgrs_seat_comes_back_only_where_dgrs_takes_input() {
        let id = Some("0b9c8a2e-1f3d-4c5b-9a7e-6d5c4b3a2f10");
        assert_eq!(plan(&meta("safe", id), false).unwrap(), "safe");
        assert_eq!(plan(&meta("dgrs", id), false), Err(Skip::DgrsOff));
        assert_eq!(plan(&meta("dgrs", id), true).unwrap(), "dgrs");
    }

    /// Only a seat whose last session ended because aibd stopped on purpose, confirmed gone,
    /// is picked up: not one stopped by the person, not one cut by a crash, not an archived
    /// one; one whose stop was not confirmed is passed over with its reason (M1).
    #[test]
    fn only_a_seat_running_when_aibd_stopped_is_picked_up() {
        let s = seat("RUNNING");
        assert_eq!(
            at(&s, &[ended(Some("api")), ended(Some("shutdown"))]),
            Some(Ok(()))
        );
        assert_eq!(
            at(&s, &[ended_as(Some("shutdown"), "interrupted")]),
            Some(Ok(()))
        );
        assert_eq!(
            at(&s, &[ended_as(Some("shutdown"), "stop_unconfirmed")]),
            Some(Err(Skip::StopUnconfirmed)),
            "reason: unconfirmed_stop_picked_up"
        );
        // macOS: unverified for the coalition, the seat's own tree clean and its root ended
        let mut clean = ended_as(Some("shutdown"), "stop_unconfirmed");
        clean.stop_reason = Some("observed_clean".into());
        clean.exit_code = Some(143);
        assert_eq!(at(&s, &[clean.clone()]), Some(Ok(())));
        clean.exit_code = None;
        assert_eq!(
            at(&s, &[clean]),
            Some(Err(Skip::StopUnconfirmed)),
            "reason: root_end_unknown_picked_up"
        );
        // only processes older than the provider kept it unverified, its end recorded
        // (5d33a 11-18); without the end, or an interrupted one freed by the next start, the
        // same rule
        let mut older = ended_as(Some("shutdown"), "stop_unconfirmed");
        older.stop_status = Some("unverified".into());
        older.stop_reason = Some(OLDER.into());
        older.signal = Some("SIGTERM".into());
        assert_eq!(at(&s, &[older.clone()]), Some(Ok(())));
        assert_eq!(
            ignored_older(std::slice::from_ref(&older)).map(|r| r.session_id.as_str()),
            Some("s")
        );
        older.state = "interrupted".into();
        assert_eq!(at(&s, &[older.clone()]), Some(Ok(())));
        older.signal = None;
        assert_eq!(
            at(&s, &[older]),
            Some(Err(Skip::StopUnconfirmed)),
            "reason: older_without_root_end_picked_up"
        );
        // the same held stop after the next start freed its slot (`interrupted`, ended_by
        // kept): still decided by its stop, never taken up as a plain interrupted one
        // (0eed7 51ac92d9 M1)
        let mut freed = ended_as(Some("shutdown"), "interrupted");
        freed.stop_status = Some("unverified".into());
        freed.stop_reason = Some("candidates_unreadable".into());
        freed.exit_code = Some(143);
        assert_eq!(
            at(&s, &[freed]),
            Some(Err(Skip::StopUnconfirmed)),
            "reason: freed_held_stop_picked_up"
        );
        assert_eq!(at(&s, &[ended(Some("shutdown")), ended(Some("api"))]), None);
        assert_eq!(at(&s, &[ended(Some(aib_store::ENDED_BY_RESTART))]), None);
        assert_eq!(at(&s, &[]), None);
        assert_eq!(
            at(&seat(aib_store::ARCHIVED), &[ended(Some("shutdown"))]),
            None
        );
    }

    /// What a rejoin lets go is read from the session's last stop: each unreadable candidate
    /// with the start the stop read (519d0 11-18 23:04), `{session_id, pid, comm, started}`;
    /// one without a start, or a session whose stop said nothing, gives none.
    #[test]
    fn the_processes_let_go_come_from_the_last_stop_with_their_start() {
        let ev = |seq: i64, kind: &str, payload: serde_json::Value| aib_store::StoredEvent {
            global_seq: seq,
            event_id: format!("ev_{seq}"),
            aggregate_type: "session".into(),
            aggregate_id: "s_1".into(),
            sequence: seq,
            event_type: kind.into(),
            schema_version: 1,
            correlation_id: None,
            occurred_at: String::new(),
            payload,
        };
        let evs = [
            ev(1, "session.started", serde_json::json!({})),
            ev(
                2,
                "session.stop_unconfirmed",
                serde_json::json!({"candidates": {"unreadable": [
                    {"pid": 1, "comm": "old", "started": 100}]}}),
            ),
            ev(
                3,
                "session.stop_unconfirmed",
                serde_json::json!({"candidates": {"unreadable": [
                    {"pid": 12492, "comm": "PlugInLibraryService", "started": 1_794_990_000},
                    {"pid": 7, "comm": "unread"}]}}),
            ),
            ev(4, "session.interrupted", serde_json::json!({})),
        ];
        assert_eq!(
            older_processes("s_1", &evs),
            vec![serde_json::json!({"session_id": "s_1", "pid": 12492,
                "comm": "PlugInLibraryService", "started": 1_794_990_000})]
        );
        assert!(older_processes("s_1", &evs[..1]).is_empty());
    }

    /// Resume only a conversation claude kept a file for; a seat that never had a turn starts
    /// its id fresh; a seat with no kept id takes the newest conversation of its folder.
    #[test]
    fn a_seat_resumes_only_a_conversation_claude_kept() {
        let d = tempfile::tempdir().unwrap();
        let (a, b) = (
            "0b9c8a2e-1f3d-4c5b-9a7e-6d5c4b3a2f10",
            "1b9c8a2e-1f3d-4c5b-9a7e-6d5c4b3a2f11",
        );
        let fresh = || Some("2b9c8a2e-1f3d-4c5b-9a7e-6d5c4b3a2f12".to_owned());
        assert_eq!(
            conversation(Some(a), Some(d.path()), true, fresh),
            Some((a.to_owned(), false)),
            "reason: resume_without_a_file"
        );
        std::fs::write(d.path().join(format!("{a}.jsonl")), "{}").unwrap();
        assert_eq!(
            conversation(Some(a), Some(d.path()), true, fresh),
            Some((a.to_owned(), true))
        );
        assert_eq!(
            conversation(None, Some(d.path()), true, fresh),
            Some((a.to_owned(), true))
        );
        std::fs::write(d.path().join("notes.jsonl"), "{}").unwrap();
        let later = std::time::SystemTime::now() + std::time::Duration::from_secs(60);
        std::fs::write(d.path().join(format!("{b}.jsonl")), "{}").unwrap();
        std::fs::File::options()
            .append(true)
            .open(d.path().join(format!("{b}.jsonl")))
            .unwrap()
            .set_modified(later)
            .unwrap();
        assert_eq!(
            conversation(None, Some(d.path()), true, fresh),
            Some((b.to_owned(), true))
        );
        // a folder given at open (maybe shared, maybe the person's): never someone's talk
        assert_eq!(
            conversation(None, Some(d.path()), false, fresh),
            Some(("2b9c8a2e-1f3d-4c5b-9a7e-6d5c4b3a2f12".to_owned(), false)),
            "reason: took_a_shared_folders_talk"
        );
        let empty = tempfile::tempdir().unwrap();
        assert_eq!(
            conversation(None, Some(empty.path()), true, fresh),
            Some(("2b9c8a2e-1f3d-4c5b-9a7e-6d5c4b3a2f12".to_owned(), false))
        );
    }

    /// f1fae 13:10: only the last stop counts — a seat whose session ended with an earlier
    /// stop (and was skipped then) is not picked up at a later start.
    #[test]
    fn only_the_last_stop_brings_a_seat_back() {
        let s = seat("RUNNING");
        let mut old = ended(Some("shutdown"));
        old.ended_at = Some("2026-11-18T04:25:58.371Z".into());
        let newest = unix_of(AT);
        assert_eq!(
            at_stop(&s, &[old.clone()], newest),
            None,
            "reason: an_older_stop_brings_it_back"
        );
        old.ended_at = Some("2026-11-18T05:09:58.000Z".into());
        assert_eq!(at_stop(&s, &[old], newest), Some(Ok(())));
        assert_eq!(unix_of("1970-01-02T00:00:01Z"), Some(86_401));
        assert_eq!(unix_of("2026-11-18T04:25:58.371Z"), Some(1_794_975_958));
        assert_eq!(unix_of("x"), None);
    }

    /// 0eed7 eb0bf164 M2: a cwd through a link (or with `//`) finds claude's folder by its
    /// real path, as claude names it.
    #[cfg(unix)]
    #[test]
    fn claudes_folder_is_found_through_a_link() {
        let d = tempfile::tempdir().unwrap();
        let real = d.path().join("real");
        std::fs::create_dir(&real).unwrap();
        let link = d.path().join("link");
        std::os::unix::fs::symlink(&real, &link).unwrap();
        let c = std::path::Path::new("/h/.claude");
        let want = aib_seatopen::claude_project_dir(
            c,
            std::fs::canonicalize(&real).unwrap().to_str().unwrap(),
        );
        assert_eq!(
            claude_dir_for(c, link.to_str().unwrap()),
            want,
            "reason: link_not_resolved"
        );
        let doubled = format!("{}//real", d.path().display());
        assert_eq!(claude_dir_for(c, &doubled), want);
        // a folder that is not there is named as written
        assert_eq!(
            claude_dir_for(c, "/no/such/place"),
            aib_seatopen::claude_project_dir(c, "/no/such/place")
        );
    }

    /// N-revive L7 (0eed7): a brought-back seat that ends by itself soon, not with 0, failed.
    #[test]
    fn a_seat_that_ends_soon_after_coming_back_failed_to() {
        use crate::sessions::rejoin_failed;
        use std::time::Duration;
        let soon = Duration::from_millis(100);
        assert!(
            rejoin_failed("root_exit", Some(1), None, soon),
            "reason: quick_exit_1_not_failed"
        );
        assert!(rejoin_failed("root_exit", None, Some("SIGABRT"), soon));
        assert!(!rejoin_failed("root_exit", Some(0), None, soon));
        assert!(
            !rejoin_failed("api", Some(1), None, soon),
            "reason: a_stop_counted_failed"
        );
        assert!(!rejoin_failed(
            "root_exit",
            Some(1),
            None,
            Duration::from_secs(60)
        ));
    }
}
