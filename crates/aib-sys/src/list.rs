//! Growing-buffer pid lists (M3 / M4), platform independent so the retry
//! and "incomplete" rules are tested everywhere.

use crate::SysError;

/// How many times a list is retried with a larger buffer.
pub const RETRIES: usize = 3;

/// Read a pid list through `call`, which fills a buffer and returns the
/// number of **bytes** written (`Ok`) or an errno (`Err`).
///
/// - Start from `hint` pids (the OS estimate), plus 25 % and at least 64.
/// - A full buffer may mean truncation: grow (double) and retry, at most
///   [`RETRIES`] more times; still full ⇒ [`SysError::Incomplete`]. A
///   truncated list is never returned.
/// - Zero pids are dropped (the OS pads with them).
pub fn read_pid_list(
    hint: usize,
    mut call: impl FnMut(&mut [i32]) -> Result<usize, i32>,
) -> Result<Vec<u32>, SysError> {
    let mut cap = (hint + hint / 4).max(64);
    for _ in 0..=RETRIES {
        let mut buf = vec![0i32; cap];
        let bytes = call(&mut buf).map_err(SysError::from_errno)?;
        let n = bytes / std::mem::size_of::<i32>();
        if n > cap {
            return Err(SysError::LengthMismatch {
                got: bytes,
                want: cap * std::mem::size_of::<i32>(),
            });
        }
        if n < cap {
            buf.truncate(n);
            return Ok(buf
                .into_iter()
                .filter_map(|p| u32::try_from(p).ok())
                .filter(|p| *p != 0)
                .collect());
        }
        cap = cap.saturating_mul(2);
    }
    Err(SysError::Incomplete)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fill(pids: &[i32]) -> impl FnMut(&mut [i32]) -> Result<usize, i32> + '_ {
        move |buf| {
            let n = pids.len().min(buf.len());
            buf[..n].copy_from_slice(&pids[..n]);
            Ok(n * 4)
        }
    }

    #[test]
    fn fits_first_time() {
        assert_eq!(read_pid_list(3, fill(&[5, 0, 7])), Ok(vec![5, 7]));
    }

    #[test]
    fn a_full_buffer_is_retried_larger() {
        let pids: Vec<i32> = (1..=100).collect();
        let mut sizes = Vec::new();
        let got = read_pid_list(10, |buf| {
            sizes.push(buf.len());
            fill(&pids)(buf)
        })
        .unwrap();
        assert_eq!(got.len(), 100);
        assert_eq!(sizes, vec![64, 128]);
    }

    #[test]
    fn never_fitting_is_incomplete_not_truncated() {
        // the OS keeps filling whatever it gets
        let r = read_pid_list(1, |buf| {
            buf.fill(9);
            Ok(buf.len() * 4)
        });
        assert_eq!(r, Err(SysError::Incomplete));
    }

    #[test]
    fn errors_are_classified() {
        assert_eq!(
            read_pid_list(1, |_| Err(1)),
            Err(SysError::PermissionDenied)
        );
        assert_eq!(read_pid_list(1, |_| Err(0)), Err(SysError::Unknown));
    }

    #[test]
    fn more_bytes_than_the_buffer_is_a_mismatch() {
        let r = read_pid_list(1, |buf| Ok(buf.len() * 4 + 4));
        assert!(matches!(r, Err(SysError::LengthMismatch { .. })), "{r:?}");
    }
}
