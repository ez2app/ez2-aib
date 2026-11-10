#!/usr/bin/env bash
# Static-file knives (ADR 0006 §6): each breaks one rule and must turn its own test red
# (scripts/knife.py checks the failed test's name). Run from the repo root.
set -eu
S=crates/aibd/src/static_files.rs
G=crates/aibd/src/guard.rs
k() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --test static_files "$4"; }

# T1 an aibd without the web build serves something instead of saying so
k "$S" '    if assets.is_empty() {' '    if assets.is_empty() && false { // knife' \
  without_the_web_feature_pages_are_404_web_not_built_and_never_hold_the_token
# T2 If-None-Match is ignored (no 304)
k "$S" '    if !hashed && etag_matches(h, a.etag) {' '    if !hashed && etag_matches(h, a.etag) && false { // knife' \
  a_matching_if_none_match_gives_304_with_no_body
# T3 hashed assets lose their one-year immutable cache
k "$S" '        out.header(header::CACHE_CONTROL, IMMUTABLE)' '        out.header(header::CACHE_CONTROL, "no-cache") // knife' \
  hashed_assets_are_immutable_for_a_year
# T4 br is sent to a client that did not ask for it
k "$S" '        (Some(b), _) if accepts(h, "br") => (b, Some("br")),' '        (Some(b), _) if accepts(h, "br") || true => (b, Some("br")), // knife' \
  precompressed_files_follow_accept_encoding
# T5 q=0 is read as "accepted"
k "$S" '            name.eq_ignore_ascii_case(coding) && !zero' '            name.eq_ignore_ascii_case(coding) && (!zero || true) // knife' \
  precompressed_files_follow_accept_encoding
# T6 the index loses its content security policy
k "$S" '            .header(header::CONTENT_SECURITY_POLICY, INDEX_CSP)' '            // knife: no CSP' \
  index_has_a_strong_etag_no_cache_and_a_strict_policy
# T7 HEAD sends the body
k "$S" '    let body = if method == Method::HEAD {' '    let body = if method == Method::HEAD && false { // knife' \
  unknown_files_and_other_methods_are_refused
# T8 API answers without no-store
k "$G" '    crate::static_files::api_no_store(&raw_path, &mut resp);' '    let _ = (&raw_path, &mut resp); // knife: no no-store' \
  api_paths_keep_the_bearer_rule_and_answer_no_store
# T9 every path made public, the API included
k "$G" '    if PUBLIC_PATHS.contains(&path) || !is_api(path) {' '    if PUBLIC_PATHS.contains(&path) || !is_api(path) || true { // knife' \
  api_paths_keep_the_bearer_rule_and_answer_no_store
echo "static knives: 9/9 bite"
