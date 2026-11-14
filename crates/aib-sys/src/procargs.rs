//! The environment part of a macOS `KERN_PROCARGS2` buffer, cut where it
//! really ends (#614).
//!
//! Layout (xnu `sysctl_procargsx`; measured 11-11 on macOS 27 for a
//! process reading itself): `argc` (native int), the exec path, NUL
//! padding, `argc` NUL-terminated arguments, the environment strings, an
//! empty string, more NUL padding, then the "apple" strings (`ptr_munge=`,
//! `main_stack=`, `executable_file=…`). For another process the buffer may
//! stop right after the arguments.
//!
//! sysinfo 0.40.1 skips every run of NULs, so it never sees the empty
//! string and reads the apple strings as variables: a process started with
//! an empty environment (`env -i`) then looks like one with variables and
//! no session marker, and a late-exec candidate was let go as "not ours"
//! (#614 CI, `a_detached_grandchild_of_an_instant_root_is_recorded_and_stopped`).
//! Pure, so it is tested on every platform.

/// Keys the kernel puts in the apple strings (xnu `exec_add_apple_strings`).
const APPLE_KEYS: &[&[u8]] = &[
    b"ptr_munge=",
    b"main_stack=",
    b"executable_file=",
    b"dyld_file=",
    b"executable_cdhash=",
    b"executable_boothash=",
    b"stack_guard=",
    b"malloc_entropy=",
    b"th_port=",
    b"arm64e_abi=",
];

/// The environment strings of `buf`, in order. `None` = the buffer is not
/// a whole `KERN_PROCARGS2` answer (too short for `argc`, or the arguments
/// run past its end), or an apple string stands where a variable should
/// be (no empty string before it). An empty list = no variable shown.
///
/// Limits (as every reader of this format): the padding after the exec path
/// is skipped up to the first non-NUL byte, so an empty `argv[0]`, or no
/// argument and no variable, cannot be told from padding.
pub fn environ(buf: &[u8]) -> Option<Vec<&[u8]>> {
    let argc = i32::from_ne_bytes(buf.get(..4)?.try_into().ok()?);
    let argc = usize::try_from(argc).ok()?;
    let mut rest = buf.get(4..)?;
    let path_end = rest.iter().position(|&b| b == 0)?;
    rest = &rest[path_end..];
    let pad = rest.iter().position(|&b| b != 0).unwrap_or(rest.len());
    rest = &rest[pad..];
    // exactly argc strings, each ended by one NUL (an empty argument is an
    // empty string: never skip a run of NULs here)
    for _ in 0..argc {
        let end = rest.iter().position(|&b| b == 0)?;
        rest = &rest[end + 1..];
    }
    // the variables, up to the first empty string or the end of the buffer
    let mut env = Vec::new();
    while let Some(end) = rest.iter().position(|&b| b == 0) {
        if end == 0 {
            return Some(env);
        }
        // an apple string where a variable should be: the empty string was
        // not there, so where the variables end is not known (043dd #614 L1;
        // read as variables, they hide a cleared environment again)
        if APPLE_KEYS.iter().any(|k| rest[..end].starts_with(k)) {
            return None;
        }
        env.push(&rest[..end]);
        rest = &rest[end + 1..];
    }
    // a last string without its NUL is a cut buffer, not a variable
    rest.is_empty().then_some(env)
}

#[cfg(test)]
mod tests {
    use super::environ;

    fn buf(argc: i32, body: &[u8]) -> Vec<u8> {
        let mut v = argc.to_ne_bytes().to_vec();
        v.extend_from_slice(body);
        v
    }

    #[test]
    fn variables_end_at_the_empty_string_not_at_the_apple_strings() {
        let b = buf(
            2,
            b"/bin/sleep\0\0\0\0/bin/sleep\x0030\0AIBD_SESSION=s-1\0HOME=/h\0\0\0\0\0ptr_munge=\0\0main_stack=\0",
        );
        assert_eq!(
            environ(&b).unwrap(),
            vec![&b"AIBD_SESSION=s-1"[..], &b"HOME=/h"[..]]
        );
    }

    /// The #614 case: `env -i`, then the apple strings. sysinfo read
    /// `ptr_munge=` / `main_stack=` as the environment.
    #[test]
    fn a_cleared_environment_followed_by_apple_strings_is_empty() {
        let b = buf(
            2,
            b"/bin/sleep\0\0\0\0\0\0/bin/sleep\x0030\0\0\0\0\0ptr_munge=\0\0main_stack=\0executable_file=0x1\0",
        );
        assert_eq!(environ(&b).unwrap(), Vec::<&[u8]>::new());
    }

    /// Measured on macOS 27 for another process of the same user: the
    /// buffer stops after the arguments.
    #[test]
    fn a_buffer_that_stops_after_the_arguments_has_no_variables() {
        let b = buf(2, b"/bin/sleep\0\0\0\0\0\0/bin/sleep\x005\0");
        assert_eq!(environ(&b).unwrap(), Vec::<&[u8]>::new());
    }

    #[test]
    fn an_empty_argument_is_counted_not_skipped() {
        // sh -c '' : the empty third argument must not eat the variables
        let b = buf(3, b"/bin/sh\0\0/bin/sh\0-c\0\0A=1\0\0ptr_munge=\0");
        assert_eq!(environ(&b).unwrap(), vec![&b"A=1"[..]]);
    }

    /// 043dd #614 L1: apple strings right after the arguments, with no
    /// empty string first. Read as variables they would be "a variable but
    /// no marker", and the candidate would be let go as not ours.
    #[test]
    fn apple_strings_without_the_empty_string_before_them_are_none() {
        let b = buf(
            2,
            b"/bin/sleep\0\0/bin/sleep\x0030\0ptr_munge=\0main_stack=\0",
        );
        assert_eq!(environ(&b), None);
        let b = buf(1, b"/bin/x\0\0/bin/x\0A=1\0executable_file=0x1\0");
        assert_eq!(environ(&b), None);
    }

    #[test]
    fn a_cut_buffer_is_none() {
        assert_eq!(environ(&[1, 0]), None);
        // two arguments announced, one present
        assert_eq!(environ(&buf(2, b"/bin/x\0\0/bin/x\0")), None);
        // a variable without its NUL
        assert_eq!(environ(&buf(1, b"/bin/x\0\0/bin/x\0A=1")), None);
        assert_eq!(environ(&buf(-1, b"/bin/x\0")), None);
    }
}
