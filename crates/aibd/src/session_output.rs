//! A session's stdout / stderr as events (preview.3 C): one `session.output`
//! event per line, on its own aggregate `<session id>/output`, so it is on
//! the global cursor, stored, and read back after a restart
//! (`GET /api/v1/sessions/{id}/output`).
//!
//! - A line is what the program wrote up to `\n`. With Claude Code
//!   `--output-format stream-json` that is one whole JSON event per line
//!   (message, tool use, result), not a token delta, so storing it does not
//!   break ADR 0005's "streaming deltas are not persisted" (partial-message
//!   output is not turned on by aibd).
//! - Bounds: a line over [`MAX_LINE`] bytes is cut (`cut: true`); after
//!   [`MAX_TOTAL`] bytes per session one `session.output_truncated` event is
//!   written and the rest is read and dropped (the program never blocks on a
//!   full pipe).
//! - Each stream ends with `session.output_closed {stream, why}` (`eof` or
//!   `read_error`). A detached child that keeps the pipe open keeps its
//!   reader waiting; the session's own end does not wait for the output.
//! - The output is the program's own text and may hold anything it printed.
//!   Known key formats are masked before it is stored ([`crate::secret_mask`],
//!   T80; best effort). It is never written to aibd's log; it lives in the
//!   owner-only data directory like the rest of the store.
//! - Windows: not captured yet (sessions start with null stdio; UNVERIFIED).

use crate::sessions::now_rfc3339;
use aib_store::{NewEvent, Store};
use std::io::{BufRead, BufReader, Read};
use std::sync::{Arc, Mutex};

/// Longest stored line, in bytes (a Claude tool result can be large).
pub const MAX_LINE: usize = 256 * 1024;
/// Most output stored per session, in bytes.
pub const MAX_TOTAL: u64 = 32 * 1024 * 1024;
/// The aggregate type of output events.
pub const AGGREGATE: &str = "session_output";

/// The output aggregate of a session.
pub fn aggregate_id(session_id: &str) -> String {
    format!("{session_id}/output")
}

struct State {
    seq: i64,
    bytes: u64,
    truncated: bool,
    write_failed: bool,
}

struct Sink {
    store: Arc<Store>,
    session: String,
    agg: String,
    st: Mutex<State>,
}

impl Sink {
    /// Write one event under the lock, so both streams share one gap-free
    /// sequence. A failed write does not use up the sequence number.
    fn write(&self, st: &mut State, kind: &str, payload: serde_json::Value) {
        let seq = st.seq + 1;
        let ev = NewEvent {
            event_id: crate::sessions::new_id("ev_")
                .unwrap_or_else(|_| format!("ev_{}_{seq}", self.agg)),
            aggregate_type: AGGREGATE.into(),
            aggregate_id: self.agg.clone(),
            sequence: seq,
            event_type: kind.into(),
            correlation_id: None,
            occurred_at: now_rfc3339(),
            payload,
        };
        match self.store.append(vec![ev]) {
            Ok(_) => st.seq = seq,
            Err(e) => {
                if !st.write_failed {
                    // the text itself is never logged
                    tracing::error!(session = %self.session, error = %e, "session output write failed");
                }
                st.write_failed = true;
            }
        }
    }

    fn line(&self, stream: &'static str, raw: &[u8], consumed: usize, cut: bool) {
        let mut st = self.st.lock().unwrap_or_else(|e| e.into_inner());
        if st.truncated {
            return;
        }
        let n = u64::try_from(consumed).unwrap_or(u64::MAX);
        if st.bytes.saturating_add(n) > MAX_TOTAL {
            st.truncated = true;
            let at = st.bytes;
            self.write(
                &mut st,
                "session.output_truncated",
                serde_json::json!({"id": self.session, "stream": stream, "kept_bytes": at,
                    "max_bytes": MAX_TOTAL}),
            );
            return;
        }
        st.bytes += n;
        let mut b = raw;
        if let Some(x) = b.strip_suffix(b"\n") {
            b = x;
        }
        if let Some(x) = b.strip_suffix(b"\r") {
            b = x;
        }
        // T80: known key formats never reach the store
        let text = crate::secret_mask::mask(&String::from_utf8_lossy(b)).into_owned();
        self.write(
            &mut st,
            "session.output",
            serde_json::json!({"stream": stream, "line": text, "cut": cut}),
        );
    }

    fn closed(&self, stream: &'static str, why: &str) {
        let mut st = self.st.lock().unwrap_or_else(|e| e.into_inner());
        self.write(
            &mut st,
            "session.output_closed",
            serde_json::json!({"id": self.session, "stream": stream, "why": why}),
        );
    }
}

/// Read up to and including the next `\n`, keeping at most `cap` bytes in
/// `out`; the rest of an over-long line is read and dropped. Returns the
/// bytes consumed (0 = end of stream) and whether the line was cut.
pub fn read_line_capped<R: BufRead>(
    r: &mut R,
    out: &mut Vec<u8>,
    cap: usize,
) -> std::io::Result<(usize, bool)> {
    let mut total = 0usize;
    let mut cut = false;
    loop {
        let (used, done) = {
            let avail = match r.fill_buf() {
                Ok(b) => b,
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(e) => return Err(e),
            };
            if avail.is_empty() {
                return Ok((total, cut));
            }
            let (chunk, done) = match avail.iter().position(|b| *b == b'\n') {
                Some(i) => (&avail[..=i], true),
                None => (avail, false),
            };
            let room = cap.saturating_sub(out.len());
            if chunk.len() > room {
                out.extend_from_slice(&chunk[..room]);
                cut = true;
            } else {
                out.extend_from_slice(chunk);
            }
            (chunk.len(), done)
        };
        r.consume(used);
        total += used;
        if done {
            return Ok((total, cut));
        }
    }
}

fn pump(sink: &Sink, stream: &'static str, r: Box<dyn Read + Send>) {
    let mut r = BufReader::with_capacity(64 * 1024, r);
    let mut buf = Vec::new();
    loop {
        buf.clear();
        match read_line_capped(&mut r, &mut buf, MAX_LINE) {
            Ok((0, _)) => return sink.closed(stream, "eof"),
            Ok((n, cut)) => sink.line(stream, &buf, n, cut),
            Err(_) => return sink.closed(stream, "read_error"),
        }
    }
}

/// Start one reader thread per stream. If a thread cannot be started its
/// stream is closed at once with `why: reader_not_started` (the program then
/// gets EPIPE on that stream rather than blocking).
pub fn start(store: Arc<Store>, session_id: &str, out: crate::platform::session::Output) {
    let sink = Arc::new(Sink {
        store,
        session: session_id.to_owned(),
        agg: aggregate_id(session_id),
        st: Mutex::new(State {
            seq: 0,
            bytes: 0,
            truncated: false,
            write_failed: false,
        }),
    });
    for (stream, r) in [("stdout", out.stdout), ("stderr", out.stderr)] {
        let s = sink.clone();
        let spawned = std::thread::Builder::new()
            .name(format!("aibd-session-{stream}"))
            .spawn(move || pump(&s, stream, r));
        if spawned.is_err() {
            sink.closed(stream, "reader_not_started");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lines(input: &[u8], cap: usize) -> Vec<(Vec<u8>, usize, bool)> {
        let mut r = BufReader::with_capacity(4, input);
        let mut out = Vec::new();
        loop {
            let mut b = Vec::new();
            let (n, cut) = read_line_capped(&mut r, &mut b, cap).unwrap_or((0, false));
            if n == 0 {
                return out;
            }
            out.push((b, n, cut));
        }
    }

    #[test]
    fn lines_are_split_on_newline_across_small_buffers() {
        let got = lines(b"one\ntwo\r\nlast", 100);
        assert_eq!(
            got,
            vec![
                (b"one\n".to_vec(), 4, false),
                (b"two\r\n".to_vec(), 5, false),
                (b"last".to_vec(), 4, false),
            ]
        );
    }

    #[test]
    fn an_over_long_line_is_cut_and_the_rest_dropped_not_split() {
        let got = lines(b"abcdefghij\nnext\n", 4);
        assert_eq!(
            got,
            vec![(b"abcd".to_vec(), 11, true), (b"next".to_vec(), 5, true)]
        );
        // "next\n" is 5 bytes: cut at 4, the newline is dropped too
    }

    #[test]
    fn empty_input_is_end_of_stream() {
        assert!(lines(b"", 10).is_empty());
    }

    #[test]
    fn the_output_aggregate_is_per_session() {
        assert_eq!(aggregate_id("s_1"), "s_1/output");
    }
}
