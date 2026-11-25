#!/usr/bin/env bash
# aib-knives: os=unix
# PF1-PF17 L1a + L1c provider_fetch (IFACE-provider-lifecycle-c2f0b-1126 r1 §3, r1-1, r1-5 N2;
# ebab8 11-26 03:16 → 6770f): only the measured https path is fetched, curl's fixed arguments,
# P (hash, size) before S, S before anything is kept, the copy hashed again, the placed file
# checked on every seat open, a cancel stops the hash, a failed job leaves no download folder,
# and start-up never follows a link. One knife per defence; each keeps every name in use.
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
pf() { python3 -I scripts/knife.py crates/aibd/src/provider_fetch.rs "$1" "$2" --expect-fail "provider_fetch::tests::$3" \
        -- cargo test -p aibd --locked --lib "provider_fetch::tests::$3"; }
# PF1 a row whose source is not the provider's own is fetched anyway
pf '    if !source_ok(row) {' '    if !source_ok(row) && false {' only_the_providers_own_https_path_is_fetched
# PF2 any url passes the source check
pf '        && row.url == want' '        && (row.url == want || !want.is_empty())' only_the_providers_own_https_path_is_fetched
# PF3 P: the hash is not compared before the signature check
pf '    if got_sha != row.sha256 || got_size != row.size {' '    if (got_sha != row.sha256 && false) || got_size != row.size {' a_file_that_is_not_the_measured_one_is_never_kept
# PF4 P: the size is not compared before the signature check
pf '    if got_sha != row.sha256 || got_size != row.size {' '    if got_sha != row.sha256 || (got_size != row.size && false) {' a_file_that_is_not_the_measured_one_is_never_kept
# PF5 S: a refused signature is ignored
pf '    signer(&got, &f).map_err(|_| ERR_UNSIGNED)?;' '    let _ = signer(&got, &f).map_err(|_| ERR_UNSIGNED);' an_unsigned_file_is_refused_even_when_its_hash_is_measured
# PF6 the copy is not checked as it is written
pf '    if wrote_sha != row.sha256 || wrote_size != row.size {' '    if (wrote_sha != row.sha256 || wrote_size != row.size) && false {' the_copy_is_checked_again_as_it_is_written
# PF7 a placed file changed later still passes a seat open
pf '    if sha != row.sha256 || size != row.size {' '    if (sha != row.sha256 || size != row.size) && false {' a_placed_file_changed_later_fails_the_check_a_seat_open_makes
# PF8 a failed job leaves its download folder
pf '    let _ = std::fs::remove_dir_all(&dl);' '    let _ = std::fs::remove_dir_all(dl.join("never"));' a_file_that_is_not_the_measured_one_is_never_kept
# PF9 a cancel does not stop the hash
pf '        if cancel.load(Ordering::SeqCst) {' '        if cancel.load(Ordering::SeqCst) && false {' a_cancel_stops_the_hash_midway
# PF10 start-up follows run/dl when it is a link and empties what it points at (N2)
pf '    if is_link(&dm) {' '    if is_link(&dm) && false {' start_up_removes_what_a_crash_left_and_only_the_link_of_a_link
# PF11 curl may follow a redirect
pf '        "--max-redirs",' '        "--max-time",' curl_gets_one_fixed_argument_list_that_stops_at_the_measured_size
# PF12 curl may read more than the measured size
pf '    a.push(row.size.to_string().into());' '    a.push((row.size * 2).to_string().into());' curl_gets_one_fixed_argument_list_that_stops_at_the_measured_size
# PF13 curl may use a protocol other than https
pf '        "--proto",' '        "--proto-default",' curl_gets_one_fixed_argument_list_that_stops_at_the_measured_size
# PF14 another platform's row is fetched (3e53b REVIEW-pfetch N1)
pf '    if platform != Some(row.platform.as_str()) {' '    if platform != Some(row.platform.as_str()) && false {' only_this_machines_row_is_fetched
# PF15 a cancel does not stop curl's wait (L1c)
pf '            return stopped(child, ERR_CANCELLED);' '            let _ = stopped(child, ERR_CANCELLED);' a_download_stops_at_once_on_cancel_or_at_the_time_limit
# PF16 a stopped curl is not killed, only waited for (L1c)
pf '    let _ = child.kill();' '    let _ = child.id();' a_download_stops_at_once_on_cancel_or_at_the_time_limit
# PF17 the time limit does not stop curl (L1c)
pf '        if !tick() {' '        if !tick() && false {' a_download_stops_at_once_on_cancel_or_at_the_time_limit
