#![allow(clippy::unwrap_used)] // test code: a panic is the failure report
//! L2 (f163c REVIEW-l2-c2f0b-f163c-b029c9cc L1): a session that had to wait for a slot is
//! checked again when it leaves the queue: a program in aibd's providers folder that is not
//! the measured file does not start, even when it was put there while the session waited.
//! The sessions use a fake start that runs no process and whose stop is clean (with no
//! lineage a real stop keeps its slot, thermal_hooks.rs), so this runs the same on Windows
//! and macOS; the fake records whether it was ever asked to start the placed program.
// test pacing / deadlines only, not daemon timers (ADR 0005 named exception)
#![allow(clippy::disallowed_methods)]

use aib_store::Store;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

static STARTED_PLACED: AtomicBool = AtomicBool::new(false);

/// Never exits by itself; its stop is clean. Touches no process.
struct Quiet;

struct NeverExits;

impl aibd::platform::session::ExitWait for NeverExits {
    fn wait(&self, t: Duration) -> Option<aibd::platform::session::Exit> {
        std::thread::sleep(t.min(Duration::from_millis(50)));
        None
    }
}

impl aibd::platform::session::Running for Quiet {
    fn pid(&self) -> u32 {
        999_995
    }
    fn try_exit(&mut self) -> std::io::Result<Option<aibd::platform::session::Exit>> {
        Ok(None)
    }
    fn exit_wait(&self) -> Arc<dyn aibd::platform::session::ExitWait> {
        Arc::new(NeverExits)
    }
    fn stop(&mut self, _: Duration) -> aibd::platform::session::Stop {
        use aibd::platform::session::{CandidateDiag, Stop, StopStatus};
        Stop {
            status: StopStatus::Clean,
            survivors: vec![],
            tree_size: 1,
            signals: vec![],
            blind_spots: vec![],
            candidates: CandidateDiag::default(),
        }
    }
}

fn quiet_spawn(
    spec: &aibd::platform::session::Spec,
    _: Option<Arc<aibd::lineage::Lineage>>,
) -> Result<Box<dyn aibd::platform::session::Running>, aibd::platform::session::SpawnFail> {
    if spec.argv.first().is_some_and(|a| a.contains("providers")) {
        STARTED_PLACED.store(true, Ordering::SeqCst);
    }
    Ok(Box::new(Quiet))
}

fn req(argv: &[&str]) -> aibd::sessions::Request {
    aibd::sessions::Request {
        argv: argv.iter().map(|s| (*s).to_owned()).collect(),
        cwd: None,
        env: vec![],
        timeout: None,
        withhold: vec![],
        input: false,
    }
}

#[test]
fn a_placed_program_changed_while_its_session_waited_does_not_start() {
    let tmp = tempfile::tempdir().unwrap();
    let data = tmp.path();
    let sessions_dir = data.join("sessions");
    std::fs::create_dir_all(&sessions_dir).unwrap();
    let store = Arc::new(Store::open(&data.join("db.sqlite")).unwrap());
    let s = aibd::sessions::Sessions::new(store.clone(), None, quiet_spawn, 1, sessions_dir);

    // the one slot is taken (an absolute path of this platform; the fake runs nothing)
    let holder = data.join("holder").display().to_string();
    let a = s.start_for_seat(None, req(&[&holder])).unwrap();
    assert_eq!(a.state, "running");

    // a program in the providers folder, by the measured version's own path; while its
    // session waits, the file there is not the measured one (put there, or changed)
    // the version the measured table lists for this platform (any one, on a platform with none)
    let version = aibd::provider_fetch::this_platform()
        .and_then(|p| aib_seatopen::measured_for("claude", p))
        .map_or("0.0.0", |m| m.version);
    let dir = data.join("providers").join("claude").join(version);
    std::fs::create_dir_all(&dir).unwrap();
    let placed = dir.join(aibd::provider_fetch::program_name("claude"));
    std::fs::write(&placed, b"not the measured program\n").unwrap();
    let b = s
        .start_for_seat(None, req(&[placed.to_str().unwrap(), "hi"]))
        .unwrap();
    assert_eq!(b.state, "pending", "reason: not_queued");

    // the slot frees: the waiting session is checked before it starts, and refused
    s.stop(&a.id, Duration::from_secs(2)).unwrap();
    let end = Instant::now() + Duration::from_secs(10);
    let got = loop {
        let g = s.get(&b.id).unwrap();
        if g.state != "pending" || Instant::now() > end {
            break g;
        }
        std::thread::sleep(Duration::from_millis(20));
    };
    assert_eq!(
        got.state, "failed",
        "reason: changed_program_started {got:?}"
    );
    assert_eq!(got.pid, 0, "reason: changed_program_started");
    assert!(
        !STARTED_PLACED.load(Ordering::SeqCst),
        "reason: changed_program_started"
    );
    // refused by the placed check itself, with its own word (3e53b r13), not some other failure
    let ev = store
        .events_of_types(&["session.spawn_failed"], 50)
        .unwrap()
        .into_iter()
        .find(|e| e.aggregate_id == b.id)
        .expect("reason: no_spawn_failed_event");
    // a platform the measured table lists: the file is not the measured one; any other
    // (Linux CI) has no row at all
    let want = if aibd::provider_fetch::this_platform().is_some() {
        "lifecycle_verify_failed"
    } else {
        "provider_unmeasured"
    };
    assert_eq!(
        ev.payload["code"].as_str(),
        Some(want),
        "reason: refused_for_another_reason {}",
        ev.payload
    );
    s.shutdown_all();
}
