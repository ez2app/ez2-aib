#!/usr/bin/env bash
# Owner window words, four more (ebab8 11-25 14:01; c2f0b's order of SCAN-owner-window-words 3 1 2 5): aibd's fixed words first, a word an AI of the same account writes (a run id, a template's name, a branch) after them and cut. Each rule broken turns its test red.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# w4-empty (an empty fixed word, a warning that is not there, leaves an empty place)
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs \
  '    let fixed: Vec<&str> = fixed.iter().copied().filter(|f| !f.is_empty()).collect();' \
  '    let fixed: Vec<&str> = fixed.to_vec();' \
  --expect-fail 'owner_ok::tests::fixed_words_come_first_and_a_name_is_cut' \
  -- cargo test -p aibd --locked --lib 'owner_ok::tests::fixed_words_come_first_and_a_name_is_cut'
# w4-rr-order (table 3: the run id first again, before the warning and the machine)
python3 -I scripts/knife.py crates/aibd/src/receipt_runner.rs \
  '    crate::owner_ok::fixed_then_cut(&[red, &to, &counts], run_id, RUN_ID_SHOWN)' \
  '    crate::owner_ok::fixed_then_cut(&[run_id, red, &to], &counts, usize::MAX)' \
  --expect-fail 'receipt_runner::tests::a_run_id_never_stands_before_the_warning_or_the_machine' \
  -- cargo test -p aibd --locked --lib 'receipt_runner::tests::a_run_id_never_stands_before_the_warning_or_the_machine'
# w4-rr-cut (table 3: a run id not cut)
python3 -I scripts/knife.py crates/aibd/src/receipt_runner.rs \
  'pub const RUN_ID_SHOWN: usize = 12;' \
  'pub const RUN_ID_SHOWN: usize = 64;' \
  --expect-fail 'receipt_runner::tests::a_run_id_never_stands_before_the_warning_or_the_machine' \
  -- cargo test -p aibd --locked --lib 'receipt_runner::tests::a_run_id_never_stands_before_the_warning_or_the_machine'
# w4-pa-order (table 1: an apply's window puts the template's words before the base)
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '    let mut s = format!("底 {} · 模板 {}:{}", e.base, e.name, held.join("、"));' \
  '    let mut s = format!("模板 {} · 底 {}:{}", e.name, e.base, held.join("、"));' \
  --expect-fail 'perm_apply::tests::a_template_name_never_stands_before_the_base_when_applied' \
  -- cargo test -p aibd --locked --lib 'perm_apply::tests::a_template_name_never_stands_before_the_base_when_applied'
# w4-pa-cut (table 1: a kept template's name read whole)
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '            name: crate::owner_ok::cut_to(&t.name, crate::owner_ok::NAME_SHOWN),' \
  '            name: t.name.clone(),' \
  --expect-fail 'perm_apply::tests::a_template_name_never_stands_before_the_base_when_applied' \
  -- cargo test -p aibd --locked --lib 'perm_apply::tests::a_template_name_never_stands_before_the_base_when_applied'
# w4-pa-revcut (table 1: a take-back's template name read whole)
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '    crate::owner_ok::cut_to(name, crate::owner_ok::NAME_SHOWN)' \
  '    name.to_owned()' \
  --expect-fail 'perm_apply::tests::a_template_name_never_stands_before_the_base_when_applied' \
  -- cargo test -p aibd --locked --lib 'perm_apply::tests::a_template_name_never_stands_before_the_base_when_applied'
# w4-pt-order (table 2: an add / set window puts the template's name before the base)
python3 -I scripts/knife.py crates/aibd/src/perm_tpl.rs \
  '    let head = crate::owner_ok::fixed_then_name(&[&format!("底 {base}"), "權限模板"], name);' \
  '    let head = format!("權限模板 {name} · 底 {base}");' \
  --expect-fail 'perm_tpl::tests::a_template_name_never_stands_before_the_base' \
  -- cargo test -p aibd --locked --lib 'perm_tpl::tests::a_template_name_never_stands_before_the_base'
# w4-rp-cut (table 5: the branch not cut)
python3 -I scripts/knife.py crates/aibd/src/repo_push.rs \
  '    let room = crate::owner_ok::RESOURCE_MAX.saturating_sub(head.chars().count() + 3);' \
  '    let room = usize::MAX;' \
  --expect-fail 'repo_push::tests::a_branch_never_stands_before_the_head_or_the_host' \
  -- cargo test -p aibd --locked --lib 'repo_push::tests::a_branch_never_stands_before_the_head_or_the_host'
# w4-rp-order (table 5: the branch put before the head, the host and the path)
python3 -I scripts/knife.py crates/aibd/src/repo_push.rs \
  '    format!("{head} · {}", end_shown(&t.branch, room))' \
  '    format!("{} · {head}", end_shown(&t.branch, room))' \
  --expect-fail 'repo_push::tests::a_branch_never_stands_before_the_head_or_the_host' \
  -- cargo test -p aibd --locked --lib 'repo_push::tests::a_branch_never_stands_before_the_head_or_the_host'
# w4-n2-repo (c2f0b REVIEW-win4 N2: the repo name put back before the branch)
python3 -I scripts/knife.py crates/aibd/src/repo_push.rs \
  '    format!("{head} · {}", end_shown(&t.branch, room))' \
  '    format!("{head} · {} {}", t.repo, end_shown(&t.branch, room))' \
  --expect-fail 'repo_push::tests::the_branch_shows_whatever_the_repo_name' \
  -- cargo test -p aibd --locked --lib 'repo_push::tests::the_branch_shows_whatever_the_repo_name'
# w4-n2-tail (N2: the branch cut from its end, its date and number lost)
python3 -I scripts/knife.py crates/aibd/src/repo_push.rs \
  '    format!("{head} · {}", end_shown(&t.branch, room))' \
  '    format!("{head} · {}", crate::owner_ok::cut_to(&t.branch, room.saturating_sub(1)))' \
  --expect-fail 'repo_push::tests::the_branch_shows_whatever_the_repo_name' \
  -- cargo test -p aibd --locked --lib 'repo_push::tests::the_branch_shows_whatever_the_repo_name'
# w4-n1-group (N1: the last subgroup dropped, two subgroups read the same)
python3 -I scripts/knife.py crates/aibd/src/repo_push.rs \
  '        end_shown(last, GROUP_TAIL)' \
  '        end_shown(last, 0)' \
  --expect-fail 'repo_push::tests::two_subgroups_read_apart' \
  -- cargo test -p aibd --locked --lib 'repo_push::tests::two_subgroups_read_apart'
# w4-la-owner (c2f0b REVIEW-win4 L-a: the remote path's owner dropped, two owners of one long name read the same)
python3 -I scripts/knife.py crates/aibd/src/repo_push.rs \
  '    let owner = crate::owner_ok::cut_to(owner, OWNER_SHOWN);' \
  '    let owner = crate::owner_ok::cut_to(owner, 0);' \
  --expect-fail 'repo_push::tests::two_owners_of_one_long_name_read_apart' \
  -- cargo test -p aibd --locked --lib 'repo_push::tests::two_owners_of_one_long_name_read_apart'
# w4-la-tail (L-a: the repo's name not cut to its end)
python3 -I scripts/knife.py crates/aibd/src/repo_push.rs \
  '    let name: String = if c.len() > NAME_TAIL_SHOWN {' \
  '    let name: String = if c.len() > usize::MAX - NAME_TAIL_SHOWN {' \
  --expect-fail 'repo_push::tests::two_owners_of_one_long_name_read_apart' \
  -- cargo test -p aibd --locked --lib 'repo_push::tests::two_owners_of_one_long_name_read_apart'
# w4-la-git (L-a: the .git end kept, eating the name's last characters)
python3 -I scripts/knife.py crates/aibd/src/repo_push.rs \
  '    let p = p.strip_suffix(".git").unwrap_or(p);' \
  '    let p = p.strip_suffix(".gitx").unwrap_or(p);' \
  --expect-fail 'repo_push::tests::two_owners_of_one_long_name_read_apart' \
  -- cargo test -p aibd --locked --lib 'repo_push::tests::two_owners_of_one_long_name_read_apart'
