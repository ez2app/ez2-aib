#!/usr/bin/env bash
# aib-test-core ledger knives (ebb36, b433b review of cb733127 "刀(合併前要有)"): each rule of
# the shadow ledger, broken on purpose, must turn its own test red. RULES §21: knives live in
# crates/<crate>/knives/; aib-xtask knife-anchors reads crates/*/knives/*.sh. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
tk() { python3 -I scripts/knife.py "crates/aib-test-core/src/$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-test-core --locked --lib "$4"; }
# LG1 inside one stream, of two lines of one millisecond the earlier one wins
tk ledger.rs '    (at, inv) >= (than_at, than_inv)' '    (at, inv) > (than_at, than_inv) // knife' \
   evidence_and_local_source_order_lines_of_one_millisecond_the_same_way
# LG2 a pass counts on a run that never ended (keeps `complete` used: RUSTFLAGS has -D warnings)
tk ledger.rs '        How::Ran => complete && u.result == Outcome::Pass,' \
   '        How::Ran => (complete || true) && u.result == Outcome::Pass, // knife' \
   a_pass_counts_only_on_a_run_that_ended_on_its_normal_path
# LG3 a reuse from this machine's own ledger is evidence
tk ledger.rs '                    r.origin == Origin::External' '                    (r.origin == Origin::External || true) // knife' \
   a_reuse_is_evidence_only_from_outside_with_all_four_words
# LG4 a reuse missing one of its four words is evidence
tk ledger.rs '                            .all(|x| !x.trim().is_empty())' \
   '                            .all(|x| !x.is_empty() || x.is_empty()) // knife' \
   a_reuse_is_evidence_only_from_outside_with_all_four_words
# LG5 a reuse is evidence whatever key it was found by (b433b L2)
tk ledger.rs '                        && u.key.as_deref() == Some(r.key.as_str())' \
   '                        && (u.key.is_some() || u.key.is_none()) // knife' \
   a_reuse_is_evidence_only_from_outside_with_all_four_words
# LG6 a line after end is read
tk ledger.rs '        if ended.is_some() {' '        if ended.is_some() && next == 0 { // knife' \
   a_stream_that_is_not_plan_first_or_skips_a_number_is_not_used
# LG7 the seat may change inside a stream
tk ledger.rs '            Some(first) if *first != who => return Err("seat changed inside a stream".into()),' \
   '            Some(first) if *first != who && who.is_empty() => return Err("seat changed inside a stream".into()), // knife' \
   a_stream_that_is_not_plan_first_or_skips_a_number_is_not_used
# LG8 a pass on a run that did not finish is a source
tk ledger.rs '    best.filter(|(st, u, _)| u.how == How::Ran && st.complete() && u.result == Outcome::Pass)' \
   '    best.filter(|(_, u, _)| u.how == How::Ran && u.result == Outcome::Pass) // knife' \
   a_local_source_is_the_newest_complete_pass_with_the_same_key
# LG9 "not this OS" naming the OS the run was on is harmless (b433b M1)
tk ledger.rs '            .is_some_and(|s| s.harmless_on(Use::Evidence, Some(run_os), bite_shown)),' \
   '            .is_some_and(|s| s.harmless_on(Use::Evidence, None.or(Some(run_os)).filter(|_| false), bite_shown)), // knife' \
   skips_follow_the_receipt_rules_and_not_run_is_never_green
# LG10 the writer takes a line the reader refuses (b433b L1)
tk ledger.rs '        check_unit(u)?;' '        let _ = check_unit(u); // knife' \
   the_writer_refuses_what_the_reader_would_refuse
# LG11 an empty stream file is a problem (b433b L4)
tk ledger.rs '            Ok(aib_ledger::Read::Text(t)) if t.is_empty() => continue,' \
   '            Ok(aib_ledger::Read::Text(t)) if t.is_empty() && t.len() > 1 => continue, // knife' \
   an_empty_stream_file_is_a_run_not_started_and_a_failed_plan_leaves_none
# LG12 a newer run that did not run is looked past (b433b L3)
tk ledger.rs '                || !matches!(u.how, How::Ran | How::NotRun)' \
   '                || !matches!(u.how, How::Ran) // knife' \
   a_local_source_is_the_newest_complete_pass_with_the_same_key
# LG13 a source a spot check revoked is still a source
tk ledger.rs '                && u.audit.as_ref().is_some_and(|a| a.revoke)' \
   '                && u.audit.as_ref().is_some_and(|a| a.revoke && a.compare == AuditCompare::Same) // knife' \
   a_spot_check_and_what_it_compares_are_written_and_read_back_and_a_revoke_ends_the_source
