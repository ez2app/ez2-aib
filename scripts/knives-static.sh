#!/usr/bin/env bash
# Static-file knives (ADR 0006 §6): each breaks one rule and must turn its own test red
# (scripts/knife.py checks the failed test's name). Run from the repo root.
set -eu
# FILE is written out on every line (not a variable): aib-xtask knife-anchors reads the
# anchors before the push and only understands plain text and $1..$9 (#590).
k() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --test static_files "$4"; }

# T1 an aibd without the web build serves something instead of saying so
k crates/aibd/src/static_files.rs '    if assets.is_empty() {' '    if assets.is_empty() && false { // knife' \
  without_the_web_feature_pages_are_404_web_not_built_and_never_hold_the_token
# T2 If-None-Match is ignored (no 304)
k crates/aibd/src/static_files.rs '    if !hashed && etag_matches(h, a.etag) {' '    if !hashed && etag_matches(h, a.etag) && false { // knife' \
  a_matching_if_none_match_gives_304_with_no_body
# T3 hashed assets lose their one-year immutable cache
k crates/aibd/src/static_files.rs '        out.header(header::CACHE_CONTROL, IMMUTABLE)' '        out.header(header::CACHE_CONTROL, "no-cache") // knife' \
  hashed_assets_are_immutable_for_a_year
# T4 br is sent to a client that did not ask for it
k crates/aibd/src/static_files.rs '        (Some(b), _) if accepts(h, "br") => (b, Some("br")),' '        (Some(b), _) if accepts(h, "br") || true => (b, Some("br")), // knife' \
  precompressed_files_follow_accept_encoding
# T5 q=0 is read as "accepted"
k crates/aibd/src/static_files.rs '            name.eq_ignore_ascii_case(coding) && !zero' '            name.eq_ignore_ascii_case(coding) && (!zero || true) // knife' \
  precompressed_files_follow_accept_encoding
# T6 the index loses its content security policy
k crates/aibd/src/static_files.rs '            .header(header::CONTENT_SECURITY_POLICY, PAGE_CSP)' '            // knife: no CSP' \
  index_has_a_strong_etag_no_cache_and_a_strict_policy
# T7 HEAD sends the body
k crates/aibd/src/static_files.rs '    let body = if method == Method::HEAD {' '    let body = if method == Method::HEAD && false { // knife' \
  unknown_files_and_other_methods_are_refused
# T8 API answers without no-store
k crates/aibd/src/guard.rs '    crate::static_files::api_no_store(&raw_path, &mut resp);' '    let _ = (&raw_path, &mut resp); // knife: no no-store' \
  api_paths_keep_the_bearer_rule_and_answer_no_store
# T9 every path made public, the API included
k crates/aibd/src/guard.rs '    if PUBLIC_PATHS.contains(&path) || !is_api(path) {' '    if PUBLIC_PATHS.contains(&path) || !is_api(path) || true { // knife' \
  api_paths_keep_the_bearer_rule_and_answer_no_store
# T10 a route mounted outside /api/ answers without the token (a9136 A3 L1)
k crates/aibd/src/guard.rs 'pub const METRICS: &str = "/api/v1/metrics";' 'pub const METRICS: &str = "/metrics"; // knife' \
  every_route_but_the_public_ones_wants_the_token
# T11 a public route moved outside /api/ (the token test skips it; the list test does not)
k crates/aibd/src/guard.rs 'pub const HEALTH: &str = "/api/v1/health";' 'pub const HEALTH: &str = "/healthz"; // knife' \
  every_route_sits_under_api_and_has_handlers
# T12 only index.html gets the page policy (1ced7 A3 L3)
k crates/aibd/src/static_files.rs '    let is_html = a.content_type.starts_with("text/html");' '    let is_html = a.path == "index.html"; // knife' \
  every_html_file_gets_the_page_policy
# T13 one ETag for every encoding (1ced7 A3 L2)
k crates/aibd/src/static_files.rs '            .header(header::ETAG, etag_of(a.etag, coding))' '            .header(header::ETAG, etag_of(a.etag, None)) // knife' \
  each_encoding_has_its_own_etag_and_any_of_them_gives_304
# T14 If-None-Match recognises only the plain ETag
k crates/aibd/src/static_files.rs '            t == "*" || known.iter().any(|k| k == t)' '            t == "*" || known[..1].iter().any(|k| k == t) // knife' \
  each_encoding_has_its_own_etag_and_any_of_them_gives_304
# T15 API answers lose X-Aibd-Build (1ced7 A3 M2)
k crates/aibd/src/static_files.rs '    h.insert(BUILD_HEADER, HeaderValue::from_static(BUILD));' '    let _ = (BUILD_HEADER, BUILD); // knife' \
  api_answers_carry_the_build
# T16 the page policy drops data: fonts (1ced7 A3 L1)
k crates/aibd/src/static_files.rs "font-src 'self' data:; " '/*knife*/' \
  every_html_file_gets_the_page_policy
echo "static knives: 16/16 bite"
