//! One hard deadline for "did each of these exit?" (043dd 11-09, bb7dc #535).
//!
//! On Windows a Job's active-process count drops when a process *starts*
//! terminating; its own handle is signaled only after the kernel has torn it
//! down. So "Job empty" followed by a zero-second look at each handle can see
//! a process that is already on its way out as "still running" (CI job
//! 128857333329: emptied=true, survivors=[3596, 3628]). Each member is
//! therefore given what is left of the **same** deadline the Job wait used:
//! nobody waits longer than the caller's limit in total, and whoever has not
//! signaled by then is reported as a survivor, never guessed gone.
//!
//! Platform-free so the timing rules are tested everywhere with a fake wait;
//! `session::SessionTree::kill` passes `Process::wait`.

use std::time::{Duration, Instant};

/// Indices of `items` that `wait` did not report exited by `deadline`.
/// `wait(item, left)` = `Ok(true)` exited within `left`, `Ok(false)` still
/// running; `left` is what remains of the deadline (zero once it has passed,
/// which still lets an already-exited item answer). The first error stops the
/// walk and is returned: an unanswered question is not an answer.
pub fn still_running<T, E>(
    items: &[T],
    deadline: Instant,
    mut wait: impl FnMut(&T, Duration) -> Result<bool, E>,
) -> Result<Vec<usize>, E> {
    let mut left_running = Vec::new();
    for (i, item) in items.iter().enumerate() {
        let left = deadline.saturating_duration_since(Instant::now());
        if !wait(item, left)? {
            left_running.push(i);
        }
    }
    Ok(left_running)
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used, clippy::disallowed_methods)]
    use super::*;

    /// A fake member that exits `after` from `t0` (None = never).
    fn fake(t0: Instant, after: Option<Duration>) -> impl Fn(Duration) -> bool {
        move |left| match after {
            Some(a) => {
                let due = t0 + a;
                let now = Instant::now();
                if due <= now {
                    return true;
                }
                if due - now <= left {
                    std::thread::sleep(due - now);
                    return true;
                }
                std::thread::sleep(left);
                false
            }
            None => {
                std::thread::sleep(left);
                false
            }
        }
    }

    #[test]
    fn a_member_that_signals_late_but_in_time_is_not_a_survivor() {
        // the CI shape: the Job is already empty, the handle lags by 150 ms
        let t0 = Instant::now();
        let m = [fake(t0, Some(Duration::from_millis(150)))];
        let left =
            still_running(&m, t0 + Duration::from_secs(2), |f, l| Ok::<_, ()>(f(l))).unwrap();
        assert!(left.is_empty());
    }

    #[test]
    fn a_zero_second_look_would_have_called_it_a_survivor() {
        // knife for the old code: same late member, no time given
        let t0 = Instant::now();
        let m = [fake(t0, Some(Duration::from_millis(150)))];
        let left = still_running(&m, t0, |f, l| Ok::<_, ()>(f(l))).unwrap();
        assert_eq!(left, vec![0]);
    }

    #[test]
    fn members_that_never_exit_are_survivors_and_the_total_wait_is_one_deadline() {
        let t0 = Instant::now();
        let m: Vec<_> = (0..4).map(|_| fake(t0, None)).collect();
        let left = still_running(&m, t0 + Duration::from_millis(200), |f, l| {
            Ok::<_, ()>(f(l))
        })
        .unwrap();
        assert_eq!(left, vec![0, 1, 2, 3]);
        let took = t0.elapsed();
        assert!(
            took < Duration::from_millis(600),
            "per-member waits, not one deadline: {took:?}"
        );
    }

    #[test]
    fn exited_before_the_deadline_passed_still_answers_after_it() {
        let t0 = Instant::now();
        let m = [fake(t0, None), fake(t0, Some(Duration::ZERO))];
        let left =
            still_running(&m, t0 + Duration::from_millis(50), |f, l| Ok::<_, ()>(f(l))).unwrap();
        assert_eq!(left, vec![0]);
    }

    #[test]
    fn an_error_is_returned_not_counted_as_exited() {
        let r = still_running(&[1, 2], Instant::now(), |&i, _| {
            if i == 2 {
                Err("access")
            } else {
                Ok(true)
            }
        });
        assert_eq!(r, Err("access"));
    }
}
