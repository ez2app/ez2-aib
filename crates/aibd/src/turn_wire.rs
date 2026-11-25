//! The turn-by-turn shell shared by the brands aibd speaks to one program per turn (grok
//! `streaming-json`, copilot `json`; 08de5 11-25, ebab8 q_cd554b24, TAKEOVER-brands §總表):
//! the seat writes Claude-style `stream-json` user lines; each one becomes a turn, whose
//! program is started through aibd's own spawn (the platform's tree and M1 lineage — a stop
//! finds what it started) with the brand's words; the program's output is translated back into
//! Claude-style `stream-json` (`assistant`, `result`) for `ctl`, `usage`, `session_output` and
//! the page. The persona card and the user's words never go in argv (`ps` shows argv to the
//! same account; Windows' command line is 32 K): a brand takes them on the turn's stdin or in
//! a prompt file — aibd's own owner-only folder, a random new name (O_EXCL, never through a
//! link), 0600, removed after the turn.
//!
//! What each brand differs in is [`Brand`]: its turn words, how the prompt goes in, and how
//! its output lines read.

use crate::lineage::Lineage;
use crate::platform::session::{
    CandidateDiag, Exit, ExitWait, Input, Output, Running, SpawnFail, SpawnFn, Spec, Stop,
    StopStatus,
};
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Condvar, Mutex};
use std::time::Duration;

/// How a turn's prompt reaches the program.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PromptBy {
    /// Written to the turn's stdin, which is then closed.
    Stdin,
    /// Written to a private file whose path goes in the turn's words.
    File,
}

/// What one brand of turn-by-turn program differs in.
pub trait Brand: Send + 'static {
    /// The brand's name, for the error lines the shell writes (`grok spawn failed: …`).
    fn name(&self) -> &'static str;
    /// How the prompt goes in.
    fn prompt_by(&self) -> PromptBy;
    /// The words of turn `turn` after the program (the seat's own words `base[1..]` and the
    /// brand's per-turn ones); `prompt_file` is `Some` for [`PromptBy::File`].
    fn turn_args(
        &mut self,
        base: &[String],
        turn: usize,
        prompt_file: Option<&Path>,
    ) -> Vec<String>;
    /// The Claude-style lines one output line of the program becomes (none, one or more).
    fn lines(&mut self, raw: &str) -> Vec<String>;
    /// What is left to say once the turn's output closed without a `result` line.
    fn finish(&mut self) -> Vec<String>;
}

/// How a turn's program is started: through aibd's own spawn, never a bare `Command`
/// (condition 2), and where a prompt file goes (condition 3).
#[derive(Clone)]
pub struct Turns {
    pub spawn: SpawnFn,
    pub lineage: Option<Arc<Lineage>>,
    /// aibd's own folder for prompt files, owner only ([`crate::platform::create_private_dir`]),
    /// never the shared temp folder.
    pub prompt_dir: PathBuf,
    /// The seat's gate run again on each turn's words before it starts: the program starts
    /// anew and reads its set-up again every turn, so what was added after the seat opened (an
    /// MCP server, a hook, a plugin, another version) is found before that turn (3e53b M1).
    pub check: fn(&Spec) -> Result<(), String>,
    /// A folder aibd made for this seat alone (copilot's own home, DESIGN-copilot-seat-home):
    /// removed with everything in it when the seat ends.
    pub own_dir: Option<PathBuf>,
}

/// Extract the plain user text from one Claude-style `stream-json` user line.
pub fn user_text(line: &[u8]) -> Option<String> {
    let v: Value = serde_json::from_slice(line).ok()?;
    let msg = v.get("message")?;
    let content = msg.get("content")?;
    if let Some(s) = content.as_str() {
        return Some(s.to_owned());
    }
    let arr = content.as_array()?;
    let mut out = String::new();
    for b in arr {
        if b.get("type").and_then(Value::as_str) == Some("text") {
            if let Some(t) = b.get("text").and_then(Value::as_str) {
                if !out.is_empty() {
                    out.push('\n');
                }
                out.push_str(t);
            }
        }
    }
    (!out.is_empty()).then_some(out)
}

/// The first line of every turn's prompt. grok 1.0.70 runs a prompt file whose first line is a
/// slash command as that command, on this machine, without the model (`/hooks-list` listed
/// the user's 22 hooks; measured signed in, EVIDENCE-brand-grok-08de5-1125 §M2), and its
/// commands include `hooks-add` and `always-approve`; on a later line it is only text. copilot
/// 1.0.115 reads a slash on stdin as text (EVIDENCE-brand-copilot §斜線) and gets the same
/// line. The user's words, the persona card or a handoff note an AI wrote never come first.
pub const PROMPT_OPENER: &str = "Message from the aibd seat:";

/// What a turn's program is given: [`PROMPT_OPENER`], then the prompt.
pub fn prompt_text(prompt: &str) -> String {
    format!("{PROMPT_OPENER}\n{prompt}")
}

/// The turn's prompt: the persona card before the user's words on the first turn only.
pub fn turn_prompt(persona: Option<&str>, prompt: &str, is_first: bool) -> String {
    match persona.filter(|p| is_first && !p.is_empty()) {
        Some(p) => format!("{p}\n\n{prompt}"),
        None => prompt.to_owned(),
    }
}

/// How long a turn's stop may wait for the tree after its output closed.
const TURN_GRACE: Duration = Duration::from_secs(2);
/// How long the end of a turn waits for its root's exit after its output closed.
const TURN_EXIT_WAIT: Duration = Duration::from_secs(5);

struct State {
    exit: Mutex<Option<Exit>>,
    cv: Condvar,
    stopping: AtomicBool,
    /// The turn running now, as aibd's spawn returned it.
    active: Mutex<Option<Box<dyn Running>>>,
    /// A finished turn's stop that was not clean: the session's stop reports it.
    left: Mutex<Option<Stop>>,
}

fn locked<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

struct TurnExitWait(Arc<State>);

impl ExitWait for TurnExitWait {
    // ExitWait::wait is called off the async runtime by sessions.rs with the caller's timeout.
    #[allow(clippy::disallowed_methods)]
    fn wait(&self, timeout: Duration) -> Option<Exit> {
        let g = locked(&self.0.exit);
        if let Some(e) = g.clone() {
            return Some(e);
        }
        let (g, _) = self
            .0
            .cv
            .wait_timeout_while(g, timeout, |e| e.is_none())
            .unwrap_or_else(|e| e.into_inner());
        g.clone()
    }
}

/// The seat's stdin: one turn per line.
struct TurnInput {
    tx: mpsc::Sender<Vec<u8>>,
    buf: Vec<u8>,
}

impl Write for TurnInput {
    fn write(&mut self, data: &[u8]) -> std::io::Result<usize> {
        self.buf.extend_from_slice(data);
        while let Some(pos) = self.buf.iter().position(|&b| b == b'\n') {
            let line: Vec<u8> = self.buf.drain(..=pos).collect();
            self.tx
                .send(line)
                .map_err(|_| std::io::Error::new(std::io::ErrorKind::BrokenPipe, "turns closed"))?;
        }
        Ok(data.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

struct TurnRunning {
    state: Arc<State>,
    output: Option<Output>,
    input: Option<Input>,
}

/// The stop of a session with no turn running and none left unclean: nothing of it is alive.
pub(crate) fn idle_stop() -> Stop {
    Stop {
        status: StopStatus::Clean,
        survivors: Vec::new(),
        tree_size: 0,
        signals: Vec::new(),
        blind_spots: Vec::new(),
        candidates: CandidateDiag {
            count: 0,
            orphan_extra: 0,
            unreadable: Vec::new(),
            unreadable_more: 0,
            other_coalition: Vec::new(),
            other_coalition_more: 0,
        },
    }
}

impl Running for TurnRunning {
    fn pid(&self) -> u32 {
        locked(&self.state.active).as_ref().map_or(0, |r| r.pid())
    }

    fn take_output(&mut self) -> Option<Output> {
        self.output.take()
    }

    fn take_input(&mut self) -> Option<Input> {
        self.input.take()
    }

    fn try_exit(&mut self) -> std::io::Result<Option<Exit>> {
        Ok(locked(&self.state.exit).clone())
    }

    fn exit_wait(&self) -> Arc<dyn ExitWait> {
        Arc::new(TurnExitWait(self.state.clone()))
    }

    /// The turn running now is stopped as the platform stops a tree, and its own report is
    /// the session's (a tree not clean stays not clean); with none running, a finished turn
    /// whose stop was not clean is reported; else nothing is alive.
    fn stop(&mut self, grace: Duration) -> Stop {
        self.state.stopping.store(true, Ordering::SeqCst);
        let turn = locked(&self.state.active).take().map(|mut r| r.stop(grace));
        let left = locked(&self.state.left).take();
        {
            let mut ex = locked(&self.state.exit);
            if ex.is_none() {
                *ex = Some(Exit::Code(0));
            }
        }
        self.state.cv.notify_all();
        turn.or(left).unwrap_or_else(idle_stop)
    }
}

fn write_line(w: &mut std::io::PipeWriter, line: &str) {
    let _ = writeln!(w, "{line}");
    let _ = w.flush();
}

fn error_line(why: &str) -> String {
    json!({"type":"result","subtype":"error","is_error":true,"result":why}).to_string()
}

/// A turn's prompt file, removed when the turn is over.
struct PromptGuard(PathBuf);

impl Drop for PromptGuard {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

/// Write `content` to a new file of a random name in `dir` (aibd's own folder, owner only):
/// created only if no file is there (O_EXCL), never through a link (O_NOFOLLOW on Unix; on
/// Windows create_new refuses an existing name, links included — UNVERIFIED on a machine),
/// owner only (0600; on Windows the folder's owner-only DACL). An AI of the same account that
/// guessed the name cannot make aibd write through it (TAKEOVER-brands-08de5-1125 §1.3).
fn write_prompt_file(dir: &Path, content: &str) -> std::io::Result<PathBuf> {
    crate::platform::create_private_dir(dir)?;
    let name = crate::sessions::new_id("p_").map_err(std::io::Error::other)?;
    let path = dir.join(format!("{name}.prompt"));
    let mut f = open_new_private(&path)?;
    f.write_all(content.as_bytes())?;
    f.flush()?;
    Ok(path)
}

/// A new file at `path`, owner only: refused when anything is at that name, a link included.
pub(crate) fn open_new_private(path: &Path) -> std::io::Result<std::fs::File> {
    let mut opts = std::fs::OpenOptions::new();
    opts.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600).custom_flags(nix::libc::O_NOFOLLOW);
    }
    opts.open(path)
}

/// One turn: started through `turns.spawn` with the brand's words, the prompt on its stdin
/// when the brand takes it there; its stdout translated onto `out_w`, its stderr copied onto
/// `err_w`; the turn's tree stopped (and reaped) once its output closed. Returns whether the
/// root ended with code 0.
/// A turn's words and, for a brand that takes it there, the prompt for its stdin.
struct TurnWords<'a> {
    args: Vec<String>,
    stdin: Option<&'a str>,
}

fn run_turn(
    st: &State,
    turns: &Turns,
    spec: &Spec,
    brand: &mut dyn Brand,
    words: TurnWords<'_>,
    out_w: &mut std::io::PipeWriter,
    err_w: &std::io::PipeWriter,
) -> Result<bool, String> {
    let TurnWords {
        args,
        stdin: stdin_prompt,
    } = words;
    let turn_spec = Spec {
        argv: std::iter::once(spec.argv[0].clone()).chain(args).collect(),
        input: stdin_prompt.is_some(),
        ..spec.clone()
    };
    if let Err(why) = (turns.check)(&turn_spec) {
        return Err(format!("{} turn not started: {why}", brand.name()));
    }
    let mut run = (turns.spawn)(&turn_spec, turns.lineage.clone())
        .map_err(|e| format!("{} spawn failed: {}", brand.name(), e.tag))?;
    let output = run.take_output();
    let input = run.take_input();
    *locked(&st.active) = Some(run);
    // a stop that came before the turn was kept finds it here (it set `stopping` first)
    if st.stopping.load(Ordering::SeqCst) {
        if let Some(mut r) = locked(&st.active).take() {
            let s = r.stop(TURN_GRACE);
            *locked(&st.left) = Some(s);
        }
        return Ok(false);
    }
    if let Some(prompt) = stdin_prompt {
        // the prompt, then stdin closed: the program reads it to its end
        let Some(mut w) = input else {
            return Err(format!("{} stdin not given", brand.name()));
        };
        let _ = w.write_all(prompt.as_bytes());
        let _ = w.flush();
        drop(w);
    }
    let Some(Output { stdout, stderr }) = output else {
        return Err(format!("{} output not captured", brand.name()));
    };
    let mut ew = err_w.try_clone().ok();
    let err_copy = std::thread::spawn(move || {
        let mut br = BufReader::new(stderr);
        let mut line = String::new();
        while br.read_line(&mut line).unwrap_or(0) > 0 {
            if let Some(w) = ew.as_mut() {
                let _ = w.write_all(line.as_bytes());
                let _ = w.flush();
            }
            line.clear();
        }
    });
    let mut emitted_result = false;
    let mut br = BufReader::new(stdout);
    let mut line = String::new();
    while br.read_line(&mut line).unwrap_or(0) > 0 {
        for out_line in brand.lines(&line) {
            emitted_result |= serde_json::from_str::<Value>(&out_line)
                .ok()
                .and_then(|v| v.get("type").and_then(Value::as_str).map(|t| t == "result"))
                .unwrap_or(false);
            write_line(out_w, &out_line);
        }
        line.clear();
    }
    let _ = err_copy.join();
    // the output closed: the root's exit, then the turn's tree stopped and reaped; a stop
    // that took the turn first has its report already
    let exit_ok = match locked(&st.active).take() {
        Some(mut r) => {
            let ex = r.exit_wait().wait(TURN_EXIT_WAIT);
            let s = r.stop(TURN_GRACE);
            if !matches!(s.status, StopStatus::Clean) {
                *locked(&st.left) = Some(s);
            }
            ex == Some(Exit::Code(0))
        }
        None => false,
    };
    if !emitted_result {
        for l in brand.finish() {
            write_line(out_w, &l);
        }
        let msg = if exit_ok {
            String::new()
        } else {
            format!("{} exited with error", brand.name())
        };
        write_line(
            out_w,
            &json!({"type":"result","subtype": if exit_ok { "success" } else { "error" },"is_error": !exit_ok,"result": msg}).to_string(),
        );
    }
    Ok(exit_ok)
}

/// Start a turn-by-turn session backed by pipes into `session_output`. Each turn's program
/// is started through `turns` (aibd's own spawn) with `brand`'s words.
pub fn spawn(
    spec: &Spec,
    persona: Option<String>,
    turns: Turns,
    mut brand: Box<dyn Brand>,
) -> Result<Box<dyn Running>, SpawnFail> {
    let pipe_err = |e: std::io::Error| SpawnFail::from_io(&e);
    let (out_r, mut out_w) = std::io::pipe().map_err(pipe_err)?;
    let (err_r, err_w) = std::io::pipe().map_err(pipe_err)?;
    let (tx, rx) = mpsc::channel::<Vec<u8>>();
    let state = Arc::new(State {
        exit: Mutex::new(None),
        cv: Condvar::new(),
        stopping: AtomicBool::new(false),
        active: Mutex::new(None),
        left: Mutex::new(None),
    });
    let st_bg = state.clone();
    let spec_bg = spec.clone();
    let spawned = std::thread::Builder::new()
        .name(format!("aibd-{}-turns", brand.name()))
        .spawn(move || {
            let mut turn = 0usize;
            while let Ok(raw_in) = rx.recv() {
                if st_bg.stopping.load(Ordering::SeqCst) {
                    break;
                }
                let Some(text) = user_text(&raw_in) else {
                    continue;
                };
                let prompt = prompt_text(&turn_prompt(persona.as_deref(), &text, turn == 0));
                let (args, file, stdin_prompt) = match brand.prompt_by() {
                    PromptBy::File => match write_prompt_file(&turns.prompt_dir, &prompt) {
                        Ok(path) => {
                            let args = brand.turn_args(&spec_bg.argv, turn, Some(&path));
                            (args, Some(PromptGuard(path)), None)
                        }
                        Err(_) => {
                            let why = format!("{} prompt write failed", brand.name());
                            write_line(&mut out_w, &error_line(&why));
                            continue;
                        }
                    },
                    PromptBy::Stdin => (
                        brand.turn_args(&spec_bg.argv, turn, None),
                        None,
                        Some(prompt.as_str()),
                    ),
                };
                let ran = run_turn(
                    &st_bg,
                    &turns,
                    &spec_bg,
                    brand.as_mut(),
                    TurnWords {
                        args,
                        stdin: stdin_prompt,
                    },
                    &mut out_w,
                    &err_w,
                );
                drop(file);
                if let Err(why) = ran {
                    write_line(&mut out_w, &error_line(&why));
                    continue;
                }
                turn += 1;
            }
            if let Some(d) = turns.own_dir.as_deref() {
                let _ = std::fs::remove_dir_all(d);
            }
            let mut ex = locked(&st_bg.exit);
            if ex.is_none() {
                *ex = Some(Exit::Code(0));
            }
            st_bg.cv.notify_all();
        });
    if let Err(e) = spawned {
        return Err(SpawnFail::from_io(&e));
    }
    let input: Option<Input> = spec.input.then(|| {
        Box::new(TurnInput {
            tx,
            buf: Vec::new(),
        }) as Input
    });
    Ok(Box::new(TurnRunning {
        state,
        output: Some(Output {
            stdout: Box::new(out_r),
            stderr: Box::new(err_r),
        }),
        input,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_user_text_and_prepends_persona_only_on_first_turn() {
        let line = br#"{"type":"user","message":{"role":"user","content":[{"type":"text","text":"hello"}]}}"#;
        let t = user_text(line).unwrap();
        assert_eq!(t, "hello");
        assert_eq!(
            turn_prompt(Some("You are A."), &t, true),
            "You are A.\n\nhello"
        );
        assert_eq!(turn_prompt(Some("You are A."), &t, false), "hello");
    }

    /// The specs every turn was started with, what each turn's stdin got, and its prompt
    /// file as it was when the turn started.
    #[allow(clippy::type_complexity)]
    static STARTED: std::sync::Mutex<Vec<(Spec, String, String)>> =
        std::sync::Mutex::new(Vec::new());

    /// The seat's gate as a test has it: refused once a file `refuse` is in the seat's folder.
    fn folder_check(spec: &Spec) -> Result<(), String> {
        if spec.cwd.join("refuse").exists() {
            Err("would load the user's own hooks 1 (grok_user_setup)".into())
        } else {
            Ok(())
        }
    }

    /// The turns started for the seat in `cwd` (tests run side by side).
    fn mine(cwd: &Path) -> Vec<(Spec, String, String)> {
        STARTED
            .lock()
            .unwrap()
            .iter()
            .filter(|(s, _, _)| s.cwd == cwd)
            .cloned()
            .collect()
    }

    struct DoneWait;
    impl ExitWait for DoneWait {
        fn wait(&self, _timeout: Duration) -> Option<Exit> {
            Some(Exit::Code(0))
        }
    }

    /// What a fake turn's stdin received, kept when it is dropped (closed).
    struct Sink(usize, Vec<u8>);
    impl Write for Sink {
        fn write(&mut self, b: &[u8]) -> std::io::Result<usize> {
            self.1.extend_from_slice(b);
            Ok(b.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    impl Drop for Sink {
        fn drop(&mut self) {
            if let Some(e) = STARTED.lock().unwrap().get_mut(self.0) {
                e.1 = String::from_utf8_lossy(&self.1).into_owned();
            }
        }
    }

    /// A turn that printed one answer and left a process behind.
    struct FakeTurn(Option<Output>, Option<Input>);
    impl Running for FakeTurn {
        fn pid(&self) -> u32 {
            4242
        }
        fn take_output(&mut self) -> Option<Output> {
            self.0.take()
        }
        fn take_input(&mut self) -> Option<Input> {
            self.1.take()
        }
        fn try_exit(&mut self) -> std::io::Result<Option<Exit>> {
            Ok(Some(Exit::Code(0)))
        }
        fn exit_wait(&self) -> Arc<dyn ExitWait> {
            Arc::new(DoneWait)
        }
        fn stop(&mut self, _grace: Duration) -> Stop {
            Stop {
                status: StopStatus::TreeNotClean,
                survivors: vec![4243],
                ..idle_stop()
            }
        }
    }

    fn fake_spawn(spec: &Spec, _l: Option<Arc<Lineage>>) -> Result<Box<dyn Running>, SpawnFail> {
        let mut g = STARTED.lock().unwrap();
        let file = spec
            .argv
            .iter()
            .position(|w| w == "--prompt-file")
            .and_then(|i| spec.argv.get(i + 1))
            .and_then(|p| std::fs::read_to_string(p).ok())
            .unwrap_or_default();
        g.push((spec.clone(), String::new(), file));
        let at = g.len() - 1;
        drop(g);
        let out = "ANSWER OK\n";
        let input = spec.input.then(|| Box::new(Sink(at, Vec::new())) as Input);
        Ok(Box::new(FakeTurn(
            Some(Output {
                stdout: Box::new(std::io::Cursor::new(out.as_bytes().to_vec())),
                stderr: Box::new(std::io::Cursor::new(Vec::new())),
            }),
            input,
        )))
    }

    /// A brand that reads `ANSWER x` as a result, taking its prompt as the test says.
    struct TestBrand(PromptBy);
    impl Brand for TestBrand {
        fn name(&self) -> &'static str {
            "test"
        }
        fn prompt_by(&self) -> PromptBy {
            self.0
        }
        fn turn_args(&mut self, base: &[String], _turn: usize, file: Option<&Path>) -> Vec<String> {
            let mut a: Vec<String> = base.iter().skip(1).cloned().collect();
            if let Some(f) = file {
                a.push("--prompt-file".into());
                a.push(f.to_string_lossy().into_owned());
            }
            a
        }
        fn lines(&mut self, raw: &str) -> Vec<String> {
            raw.strip_prefix("ANSWER ")
                .map(|t| vec![json!({"type":"result","result":t.trim()}).to_string()])
                .unwrap_or_default()
        }
        fn finish(&mut self) -> Vec<String> {
            Vec::new()
        }
    }

    fn one_turn(by: PromptBy, dir: &Path) -> (Box<dyn Running>, (Spec, String, String)) {
        let spec = Spec {
            id: "s_0123456789abcdef".into(),
            argv: vec!["/bin/brand".into(), "--safe".into()],
            cwd: dir.into(),
            env: Vec::new(),
            pass: Vec::new(),
            input: true,
        };
        let turns = Turns {
            spawn: fake_spawn,
            lineage: None,
            prompt_dir: dir.join("turn-prompts"),
            check: folder_check,
            own_dir: None,
        };
        let mut s = spawn(&spec, Some("CARD".into()), turns, Box::new(TestBrand(by))).unwrap();
        let mut input = s.take_input().unwrap();
        let mut out = BufReader::new(s.take_output().unwrap().stdout);
        writeln!(
            input,
            "{}",
            json!({"type":"user","message":{"role":"user","content":"/hooks-add rm"}})
        )
        .unwrap();
        let mut seen = String::new();
        while !seen.contains("\"result\"") {
            let mut l = String::new();
            assert!(
                out.read_line(&mut l).unwrap() > 0,
                "reason: no_result {seen}"
            );
            seen.push_str(&l);
        }
        assert!(seen.contains("OK"), "{seen}");
        let mine = mine(dir);
        assert_eq!(mine.len(), 1, "reason: turn_not_through_spawn");
        (s, mine[0].clone())
    }

    /// 08de5 11-25 conditions 2 and 3: a turn is started through the spawn aibd hands over
    /// (the seat's words plus the turn's prompt file, no stdin), the prompt file sits in the
    /// private folder and is gone after the turn, and a turn whose tree did not stop clean is
    /// what the session's stop reports.
    #[test]
    fn every_turn_goes_through_aibds_spawn_and_its_stop_is_reported() {
        let d = tempfile::tempdir().unwrap();
        let (mut s, (started, stdin, file)) = one_turn(PromptBy::File, d.path());
        // 3e53b M2: the first line is aibd's, never the user's (a `/` there runs as a command)
        assert_eq!(
            file,
            format!("{PROMPT_OPENER}\nCARD\n\n/hooks-add rm"),
            "reason: prompt_file_starts_with_user_words"
        );
        let a = &started.argv;
        assert_eq!(
            &a[..2],
            ["/bin/brand", "--safe"],
            "reason: seat_words_changed {a:?}"
        );
        assert_eq!(a[2], "--prompt-file");
        let dir = d.path().join("turn-prompts").to_string_lossy().into_owned();
        assert!(a[3].starts_with(&dir), "reason: prompt_not_private {a:?}");
        assert!(
            !started.input && stdin.is_empty(),
            "reason: turn_given_stdin"
        );
        // the file goes when the turn is over, just after its result line was written
        let gone = (0..100).any(|_| {
            let g = !Path::new(&a[3]).exists();
            if !g {
                #[allow(clippy::disallowed_methods)] // a test's short poll, no timer to register
                std::thread::sleep(Duration::from_millis(20));
            }
            g
        });
        assert!(gone, "reason: prompt_left");
        let stop = s.stop(Duration::from_millis(10));
        assert_eq!(
            stop.status,
            StopStatus::TreeNotClean,
            "reason: unclean_turn_said_clean"
        );
        assert_eq!(stop.survivors, vec![4243]);
    }

    /// 3e53b M1: the gate runs again before every turn; a turn after something was added to
    /// the user's set-up is refused with the reason and never started.
    #[test]
    fn a_turn_after_the_set_up_changed_is_refused_and_not_started() {
        let d = tempfile::tempdir().unwrap();
        let spec = Spec {
            id: "s_0123456789abcdee".into(),
            argv: vec!["/bin/brand".into(), "--safe".into()],
            cwd: d.path().into(),
            env: Vec::new(),
            pass: Vec::new(),
            input: true,
        };
        let turns = Turns {
            spawn: fake_spawn,
            lineage: None,
            prompt_dir: d.path().join("turn-prompts"),
            check: folder_check,
            own_dir: Some(d.path().join("own")),
        };
        std::fs::create_dir(d.path().join("own")).unwrap();
        std::fs::write(d.path().join("own").join("config.json"), "{}").unwrap();
        let brand = Box::new(TestBrand(PromptBy::Stdin));
        let mut s = spawn(&spec, None, turns, brand).unwrap();
        let mut input = s.take_input().unwrap();
        let mut out = BufReader::new(s.take_output().unwrap().stdout);
        let mut turn = |text: &str| {
            writeln!(
                input,
                "{}",
                json!({"type":"user","message":{"role":"user","content":text}})
            )
            .unwrap();
            loop {
                let mut l = String::new();
                assert!(out.read_line(&mut l).unwrap() > 0, "reason: no_result");
                if l.contains("\"result\"") {
                    return l;
                }
            }
        };
        assert!(
            !turn("one").contains("is_error\":true"),
            "reason: first_turn_refused"
        );
        std::fs::write(d.path().join("refuse"), "").unwrap();
        let r = turn("two");
        assert!(
            r.contains("test turn not started") && r.contains("grok_user_setup"),
            "reason: changed_set_up_started {r}"
        );
        assert_eq!(mine(d.path()).len(), 1, "reason: refused_turn_started");
        // the seat's own folder goes with the seat (its input closed: the seat ends)
        drop(turn);
        drop(input);
        let gone = (0..100).any(|_| {
            let g = !d.path().join("own").exists();
            if !g {
                #[allow(clippy::disallowed_methods)] // a test's short poll, no timer to register
                std::thread::sleep(Duration::from_millis(20));
            }
            g
        });
        assert!(gone, "reason: own_dir_left");
    }

    /// Condition 4: a brand that takes its prompt on stdin gets the card and the words there,
    /// and none of it is in the turn's argv.
    #[test]
    fn a_stdin_prompt_never_goes_in_argv() {
        let d = tempfile::tempdir().unwrap();
        let (_s, (started, stdin, _)) = one_turn(PromptBy::Stdin, d.path());
        assert!(started.input, "reason: stdin_not_asked");
        assert_eq!(
            stdin,
            format!("{PROMPT_OPENER}\nCARD\n\n/hooks-add rm"),
            "reason: prompt_not_on_stdin"
        );
        for w in &started.argv {
            assert!(
                !w.contains("CARD") && !w.contains("hooks-add"),
                "reason: prompt_in_argv {w}"
            );
        }
    }

    /// Condition 3: the prompt file gets a new random name each time, owner only, and a name
    /// already there (a link planted by an AI of the same account) is never written through.
    #[cfg(unix)]
    #[test]
    fn a_prompt_file_is_new_private_and_never_written_through_a_link() {
        use std::os::unix::fs::PermissionsExt;
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().join("p");
        let a = write_prompt_file(&dir, "one").unwrap();
        let b = write_prompt_file(&dir, "two").unwrap();
        assert_ne!(a, b, "reason: guessable_name");
        assert_eq!(
            std::fs::metadata(&a).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert_eq!(
            std::fs::metadata(&dir).unwrap().permissions().mode() & 0o777,
            0o700
        );
        let target = d.path().join("victim");
        std::fs::write(&target, "keep").unwrap();
        let planted = dir.join("planted.prompt");
        std::os::unix::fs::symlink(&target, &planted).unwrap();
        assert!(
            open_new_private(&planted).is_err(),
            "reason: written_through_link"
        );
        assert_eq!(std::fs::read_to_string(&target).unwrap(), "keep");
        let there = dir.join("there.prompt");
        std::fs::write(&there, "old").unwrap();
        assert!(
            open_new_private(&there).is_err(),
            "reason: existing_file_reused"
        );
        assert_eq!(std::fs::read_to_string(&there).unwrap(), "old");
    }
}
