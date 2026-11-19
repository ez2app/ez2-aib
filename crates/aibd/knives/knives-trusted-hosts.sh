#!/usr/bin/env bash
# The pinned test machine list aibd's delegated receipts reach (ORDER-webonly-today 5 G1 slice 1, a9136 11-20)
set -u
th() { python3 -I scripts/knife.py crates/aibd/src/trusted_hosts.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "trusted_hosts::tests::$3"; }
# TH1 ~/.ssh/config is read
th '    let mut a: Vec<String> = vec!["-F".into(), "none".into()];' '    let mut a: Vec<String> = vec![]; // knife' \
   every_ssh_reads_only_the_list_and_trusts_only_its_keys
# TH2 an unknown host key is taken on first sight
th '        "StrictHostKeyChecking=yes".into(),' '        "StrictHostKeyChecking=accept-new".into(),' \
   every_ssh_reads_only_the_list_and_trusts_only_its_keys
# TH3 a proxy from elsewhere is used
th '        "ProxyCommand=none".into(),' '        "Compression=no".into(), // knife' \
   every_ssh_reads_only_the_list_and_trusts_only_its_keys
# TH4 no key given offers the account's default agent
th '        format!("IdentityAgent={}", shown(key.map(|k| k.agent))),' '        format!("IdentityAgent={}", key.map_or_else(|| "SSH_AUTH_SOCK".to_owned(), |k| k.agent.display().to_string())),' \
   every_ssh_reads_only_the_list_and_trusts_only_its_keys
# TH11 ~/.ssh/id_* are tried beside aibd's key (5d33a PREREAD-trustedhosts M1)
th '        format!("IdentityFile={}", shown(key.map(|k| k.public))),' '        "AddKeysToAgent=no".into(), // knife' \
   every_ssh_reads_only_the_list_and_trusts_only_its_keys
# TH12 a path with a blank or % is passed to ssh (5d33a PREREAD-trustedhosts L1)
th '    if !paths.iter().all(|p| path_ok(p)) {' '    if paths.is_empty() { // knife' \
   every_ssh_reads_only_the_list_and_trusts_only_its_keys
# TH5 a host name that is an ssh option is taken
th "        && s.as_bytes()[0] != b'-'" '        && true // knife' \
   each_field_has_one_shape
# TH6 a bad entry is skipped and the rest taken
th '        check(h).map_err(|f| format!("machine {i}: bad {f}"))?;' '        let _ = check(h); // knife' \
   a_list_is_taken_whole_or_not_at_all
# TH7 an unknown field (a proxy, a jump) is ignored
th '#[serde(deny_unknown_fields)] // a proxy or jump field is refused, not ignored' '// knife' \
   a_list_is_taken_whole_or_not_at_all
# TH8 a fingerprint or a commented key is taken as the host key
th '        (Some("ssh-ed25519"), Some(k), None) => {' '        (Some("ssh-ed25519"), Some(k), _) => {' \
   each_field_has_one_shape
# TH9 a link is followed to the list
th '    if !m.file_type().is_file() || m.len() > MAX_FILE {' '    if m.len() > MAX_FILE { // knife' \
   the_list_is_a_plain_small_file_and_its_digest_changes_with_it
# TH10 the destination is not last (an option could follow it)
th '    a.push("--".into());' '    // knife' \
   every_ssh_reads_only_the_list_and_trusts_only_its_keys
