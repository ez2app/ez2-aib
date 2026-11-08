//! POSIX [`super::session::SpawnFn`]: one process group + the recorded tree
//! (`tree.rs`). macOS keeps the root unreaped until the last signal (H1);
//! never `clean` (DECISION-23).

use super::session::{Exit, ExitWait, Running, SpawnFail, Spec, Stop, StopStatus};
use super::tree::{spawn_tree, RootEnd, RootWatch, SpawnSpec, TreeHandle};
use crate::lineage::Lineage;
use std::ffi::OsString;
use std::sync::Arc;
use std::time::Duration;

struct UnixRunning {
    h: TreeHandle,
}

struct Watch(RootWatch);

fn exit_of(e: RootEnd) -> Exit {
    match (e.code, e.signal) {
        (Some(c), _) => Exit::Code(c),
        (None, Some(n)) => Exit::Signal(signal_name(n)),
        // M5 could not read it (root_identity_lost): no status to report
        (None, None) => Exit::Code(-1),
    }
}

fn signal_name(n: i32) -> String {
    nix::sys::signal::Signal::try_from(n)
        .map(|s| s.as_str().to_owned())
        .unwrap_or_else(|_| format!("SIG{n}"))
}

impl ExitWait for Watch {
    fn wait(&self, timeout: Duration) -> Option<Exit> {
        self.0.wait(timeout).map(exit_of)
    }
}

impl Running for UnixRunning {
    fn pid(&self) -> u32 {
        self.h.root_pid()
    }

    fn try_exit(&mut self) -> std::io::Result<Option<Exit>> {
        Ok(self.h.root_end().map(exit_of))
    }

    fn exit_wait(&self) -> Arc<dyn ExitWait> {
        Arc::new(Watch(self.h.root_watch()))
    }

    /// TERM (group, then each checked pid) → `grace` → KILL → 2 s → verify;
    /// the same whether the root still runs (DELETE / shutdown) or exited by
    /// itself (the one stop after a root exit).
    fn stop(&mut self, grace: Duration) -> Stop {
        match self.h.cleanup(grace) {
            Ok(r) => Stop::from_report(&r),
            Err(_) => Stop {
                status: StopStatus::Unverified("stop_failed".into()),
                survivors: Vec::new(),
                tree_size: 0,
                signals: Vec::new(),
                blind_spots: super::tree::BLIND_SPOTS_UNCONTAINED.to_vec(),
            },
        }
    }
}

/// The child's environment is `session::child_env` (aibd's own minus every
/// `AIBD_*`, the request's `env`, `AIBD_SESSION` last), from an empty one.
pub fn spawn(spec: &Spec, lineage: Option<Arc<Lineage>>) -> Result<Box<dyn Running>, SpawnFail> {
    let (program, args) = match spec.argv.split_first() {
        Some((p, a)) => (p.into(), a.iter().map(OsString::from).collect()),
        None => {
            return Err(SpawnFail {
                tag: "empty_argv".into(),
                left_running: None,
            })
        }
    };
    let s = SpawnSpec {
        program,
        args,
        cwd: Some(spec.cwd.clone()),
        env: super::session::child_env(spec, std::env::vars_os()),
        env_clear: true,
        session_id: spec.id.clone(),
        watch_forks: true,
        discard_output: true,
        lineage: lineage.filter(|l| l.enabled()),
    };
    match spawn_tree(&s) {
        Ok(h) => Ok(Box::new(UnixRunning { h })),
        Err(e) => Err(SpawnFail::from_io(&e)),
    }
}
