// SPDX-License-Identifier: Apache-2.0
//! Each rule: the shape it is for is found (yes), and the fixed shape is not. The scripts
//! are made up after the night of 11-11 (DEV 4.6 r2 §10); no host names or addresses.

use aib_signlint::{lint, rehearsal, split_acked, State};
use std::collections::BTreeSet;

/// A script with the rehearsal line: body line n is file line n + 2.
fn script(body: &str) -> String {
    format!("#!/bin/bash\n# 可丟環境: ct-900 收據: receipts/r1.json\n{body}")
}

fn found(text: &str, name: &str) -> Vec<(&'static str, usize)> {
    lint(text, name).iter().map(|f| (f.rule, f.line)).collect()
}

fn of(text: &str, rule: &str) -> Vec<usize> {
    lint(text, "SIGN-test.sh")
        .iter()
        .filter(|f| f.rule == rule)
        .map(|f| f.line)
        .collect()
}

#[test]
fn a_careful_script_has_nothing_to_say() {
    let t = script(
        r#"set -uo pipefail
SSHO=(-o ConnectTimeout=20 -o BatchMode=yes)
H() { ssh "${SSHO[@]}" host-a "$@"; }
die() { echo "STOP: $*"; exit "${2:-1}"; }
TS=$(date -u +%Y%m%d-%H%M%S) && [ ${#TS} -eq 15 ] || die "no timestamp" 9
BAK=/root/bak-$TS
H 'true' || die "host unreachable" 10
H "mkdir -m 700 $BAK" || die "cannot make the backup dir" 10
H "BAK=$BAK bash -s" <<'EOS' || die "not changed" 20
set -euo pipefail
A=/usr/local/sbin/tool
[ -s "$BAK/x" ] || { echo "no source"; exit 1; }
cp -a $A $BAK/tool.old
cmp -s $A $BAK/tool.old
echo "a | grep -q b; x || true"
EOS
echo "done # not a comment | head"
"#,
    );
    assert_eq!(found(&t, "SIGN-test.sh"), vec![]);
}

#[test]
fn s1_pipefail_with_an_early_stop() {
    let t = script(
        "set -euo pipefail\n\
         systemctl list-units | grep -q nginx || exit 1\n\
         journalctl -u app | head -5 > /tmp/tail || exit 1\n\
         echo \"$X\" | grep -q on || exit 1\n\
         systemctl list-units | grep -c nginx > /tmp/n || exit 1\n",
    );
    assert_eq!(of(&t, "S1"), vec![4, 5]);
    // without pipefail the same lines are fine
    let t = script("set -e\nsystemctl list-units | grep -q nginx || exit 1\n");
    assert_eq!(of(&t, "S1"), Vec::<usize>::new());
    // pipefail turned off again
    let t = script("set -o pipefail\nset +o pipefail\nps ax | grep -q app || exit 1\n");
    assert_eq!(of(&t, "S1"), Vec::<usize>::new());
}

#[test]
fn s2_swallowed_failures() {
    let t = script(
        "set -euo pipefail\n\
         rm -f /tmp/x || true\n\
         test -e /a && mkdir /b || echo no\n\
         set +e\n\
         cp a b\n",
    );
    assert_eq!(of(&t, "S2"), vec![4, 5, 6, 7]);
    // checked, or set -e on: nothing
    let t = script(
        "set -uo pipefail\n\
         die() { echo \"STOP: $*\"; exit 1; }\n\
         cp a b || die copy\n\
         set -e\n\
         cp c d\n\
         set +e\n\
         mv e f\n\
         rc=$?\n\
         set -e\n",
    );
    assert_eq!(of(&t, "S2"), Vec::<usize>::new());
}

#[test]
fn s3_output_without_the_exit_code() {
    let t = script(
        "set -euo pipefail\n\
         f() {\n\
         \x20 local v=$(cat /etc/x)\n\
         \x20 echo \"$v\"\n\
         }\n\
         [ \"$(cat /etc/y)\" = on ] || exit 1\n\
         for u in $(cat /etc/users); do echo \"$u\"; done\n",
    );
    assert_eq!(of(&t, "S3"), vec![5, 8, 9]);
    // set -e off: a bare x=$(…) goes on with x empty
    let t = script("set -uo pipefail\nv=$(cat /etc/x)\necho \"$v\"\n");
    assert_eq!(of(&t, "S3"), vec![4]);
    // checked
    let t = script(
        "set -uo pipefail\n\
         v=$(cat /etc/x) || exit 3\n\
         w=$(cat /etc/w)\n\
         [ $? -eq 0 ] || exit 3\n\
         f() {\n\
         \x20 local v\n\
         \x20 v=$(cat /etc/x)\n\
         }\n",
    );
    assert_eq!(of(&t, "S3"), Vec::<usize>::new());
}

#[test]
fn s4_an_unreachable_host_is_not_an_absent_file() {
    let t = script(
        "set -uo pipefail\n\
         H() { ssh -o BatchMode=yes host-a \"$@\"; }\n\
         if ! H test -e /etc/x.conf; then\n\
         \x20 echo \"not there, writing it\"\n\
         \x20 H \"touch /etc/x.conf\" || exit 3\n\
         fi\n\
         SSH=\"ssh -o BatchMode=yes host-a\"\n\
         $SSH test -d /srv/a && echo present\n\
         conf=$($SSH cat /etc/app.conf 2>/dev/null) || true\n",
    );
    assert_eq!(of(&t, "S4"), vec![5, 10, 11]);
    // stops, or tells 255 apart
    let t = script(
        "set -uo pipefail\n\
         H() { ssh -o BatchMode=yes host-a \"$@\"; }\n\
         if ! H test -e /etc/x.conf; then\n\
         \x20 exit 4\n\
         fi\n\
         rc=0; H test -e /etc/y || rc=$?\n\
         [ $rc -eq 255 ] && exit 9\n\
         H true || exit 10\n",
    );
    assert_eq!(of(&t, "S4"), Vec::<usize>::new());
}

#[test]
fn s4_on_a_restore_path_a_missing_backup_is_not_skipped() {
    // f1fae 05:1x: the restore path is where tonight's one was
    let body = "set -uo pipefail\n\
                H() { ssh -o BatchMode=yes host-a \"$@\"; }\n\
                H \"bash -s\" <<'EOS' || exit 20\n\
                set -euo pipefail\n\
                B=/root/bak\n\
                [ -s \"$B/app.conf\" ] && cp -a \"$B/app.conf\" /etc/app.conf\n\
                cmp -s \"$B/app.conf\" /etc/app.conf\n\
                EOS\n";
    let t = script(body);
    let f = lint(&t, "SIGN-app-revert.sh");
    let s4: Vec<_> = f.iter().filter(|f| f.rule == "S4").collect();
    assert_eq!(s4.len(), 1, "{f:?}");
    assert_eq!(s4[0].line, 8);
    assert!(s4[0].msg.starts_with("還原路徑裡"), "{}", s4[0].msg);
    // not a restore path: the same test is someone's choice
    assert_eq!(
        found(&t, "SIGN-app.sh")
            .iter()
            .filter(|(r, _)| *r == "S4")
            .count(),
        0
    );
    // a restore function, too; a missing backup that stops is fine
    let t = script(
        "set -euo pipefail\n\
         restore() {\n\
         \x20 if [ -e /root/bak/app.conf ]; then cp /root/bak/app.conf /etc/app.conf; fi\n\
         \x20 cmp -s /root/bak/app.conf /etc/app.conf\n\
         }\n\
         undo() {\n\
         \x20 [ -e /root/bak/b ] || exit 7\n\
         \x20 cp /root/bak/b /etc/b && cmp -s /root/bak/b /etc/b\n\
         }\n",
    );
    assert_eq!(of(&t, "S4"), vec![5]);
}

#[test]
fn s5_a_restore_reads_back_what_it_wrote() {
    let t = script(
        "set -euo pipefail\n\
         restore() {\n\
         \x20 cp -a /root/bak/app.conf /etc/app.conf\n\
         \x20 systemctl restart app\n\
         }\n\
         trap restore ERR\n\
         cp /tmp/new.conf /etc/app.conf\n",
    );
    assert_eq!(of(&t, "S5"), vec![5]);
    let t = script(
        "set -euo pipefail\n\
         restore() {\n\
         \x20 cp -a /root/bak/app.conf /etc/app.conf\n\
         \x20 cmp -s /root/bak/app.conf /etc/app.conf\n\
         \x20 systemctl restart app\n\
         }\n",
    );
    assert_eq!(of(&t, "S5"), Vec::<usize>::new());
}

#[test]
fn u1_what_cannot_be_read_is_unknown() {
    let t = script("set -euo pipefail\neval \"$CMD\"\n");
    let f = lint(&t, "SIGN-x.sh");
    assert!(
        f.iter()
            .any(|f| f.rule == "U1" && f.line == 4 && f.state == State::Unknown),
        "{f:?}"
    );
    let t = script("set -euo pipefail\nH \"bash -s\" <<'EOS'\nset -e\necho never closed\n");
    assert!(lint(&t, "SIGN-x.sh").iter().any(|f| f.rule == "U1"));
}

#[test]
fn r1_the_rehearsal_line_is_needed() {
    let t = "#!/bin/bash\nset -euo pipefail\necho hi\n";
    assert_eq!(found(t, "SIGN-x.sh"), vec![("R1", 1)]);
    let t = "#!/bin/bash\n# 可丟環境: — 收據: —\nset -euo pipefail\n";
    assert!(rehearsal(t).is_none());
    let t = "#!/bin/bash\n# 可丟環境: ct-900 收據: receipts/r1.json\n";
    let r = rehearsal(t).expect("rehearsal");
    assert_eq!(
        (r.env.as_str(), r.receipt.as_str()),
        ("ct-900", "receipts/r1.json")
    );
}

#[test]
fn a_heredoc_for_a_shell_is_its_own_script_and_others_are_data() {
    // the outer script has set -e off; the remote body turns it on: no S2 inside
    let t = script(
        "set -uo pipefail\n\
         H() { ssh -o BatchMode=yes host-a \"$@\"; }\n\
         H \"bash -s\" <<'EOS' || exit 20\n\
         set -euo pipefail\n\
         cp a b\n\
         ps ax | grep -q app\n\
         EOS\n\
         python3 - <<'PY' || exit 21\n\
         import os\n\
         x = os.popen('ps ax | grep -q app') or True\n\
         PY\n",
    );
    // only the body's pipeline, at its own line
    assert_eq!(found(&t, "SIGN-x.sh"), vec![("S1", 8)]);
}

#[test]
fn acks_let_s_rules_through_and_never_u1_or_r1() {
    let t = "#!/bin/bash\nset -euo pipefail\nrm -f /tmp/x || true\neval \"$X\"\n";
    let acks: BTreeSet<String> = ["S2".to_owned(), "U1".to_owned(), "R1".to_owned()].into();
    let (left, acked) = split_acked(lint(t, "SIGN-x.sh"), &acks);
    let left: Vec<_> = left.iter().map(|f| f.rule).collect();
    let acked: Vec<_> = acked.iter().map(|f| f.rule).collect();
    assert_eq!(left, vec!["R1", "U1"]);
    assert_eq!(acked, vec!["S2"]);
}

#[test]
fn a_set_inside_a_function_body_does_not_leak_out() {
    // real SIGN script (11-11): `undo() { set +e; … }` then trap; the lines after the
    // definition still run with set -e on
    let t = script(
        "set -euo pipefail\n\
         undo() {\n\
         \x20 set +e\n\
         \x20 cp -a /root/bak/a /etc/a || ok=0\n\
         \x20 cmp -s /root/bak/a /etc/a || ok=0\n\
         }\n\
         trap undo EXIT\n\
         cp -a /etc/a /etc/a.prev\n\
         install -m 0755 /tmp/n /usr/local/sbin/n\n",
    );
    // the body checks each step; the two lines after the definition run with set -e on
    assert_eq!(of(&t, "S2"), Vec::<usize>::new());
    assert_eq!(of(&t, "S3"), Vec::<usize>::new());
}

#[test]
fn u1_a_command_that_is_built_at_run_time_is_unknown() {
    // ebb36 #653 L1: a variable as the command, a here-string or a pipe into a shell, xargs sh
    let t = script(
        "set -euo pipefail\n\
         c=\"rm -rf $X\"\n\
         $c\n\
         bash <<<\"$PAYLOAD\"\n\
         curl -s https://example.test/x | bash\n\
         echo \"$C\" | xargs -I{} sh -c {}\n",
    );
    assert_eq!(of(&t, "U1"), vec![5, 6, 7, 8]);
    // a command substitution or an array as the command line
    let t = script(
        "set -euo pipefail\nd=$(command -v rm)\n$d -rf /tmp/x\nC=(rm -rf /tmp/y)\n\"${C[@]}\"\n",
    );
    assert_eq!(of(&t, "U1"), vec![5, 7]);
    // what such a variable runs can be read here: a program path (even built from other
    // variables), ssh, a positional parameter, a name the caller passes in
    let t = script(
        "set -euo pipefail\n\
         A=/usr/local/sbin/tool\n\
         N=$BAK/tool.new\n\
         $N --version\n\
         \"$SRC\" --version\n\
         SSH=\"ssh -o BatchMode=yes host-a\"\n\
         run() { \"$@\"; }\n\
         $A --version\n\
         \"${A}\" check\n\
         $SSH true\n\
         run ls\n",
    );
    assert_eq!(of(&t, "U1"), Vec::<usize>::new());
}

#[test]
fn s2_exit_0_and_a_bare_bang_swallow_the_failure() {
    // ebb36 #653 L2 / L3
    let t = script(
        "set -euo pipefail\n\
         systemctl restart x || exit 0\n\
         f() {\n\
         \x20 cp a b || return 0\n\
         }\n\
         ! systemctl restart y\n\
         ! grep -q x /etc/y\n\
         [ $? -eq 0 ] || exit 3\n\
         cp c d || exit 4\n",
    );
    assert_eq!(of(&t, "S2"), vec![4, 6, 8]);
}

#[test]
fn s4_a_remote_call_in_a_process_substitution() {
    // ebb36 #653 L4: `< <(ssh …)` reads nothing when the host is gone, and says nothing
    let t = script(
        "set -euo pipefail\n\
         H() { ssh -o BatchMode=yes host-a \"$@\"; }\n\
         while read -r f; do echo \"$f\"; done < <(H ls /srv)\n\
         while read -r f; do echo \"$f\"; done < <(ls /srv)\n",
    );
    assert_eq!(of(&t, "S4"), vec![5]);
}

#[test]
fn r1_a_rehearsal_line_that_would_mislead_the_dialog_does_not_count() {
    // ebb36 #653 M1: the values are shown in the consent dialog
    let t = "#!/bin/bash\n# 可丟環境: ct-900\u{202E}lmth 收據: r.json\n";
    assert!(rehearsal(t).is_none());
    assert_eq!(found(t, "SIGN-x.sh"), vec![("R1", 1)]);
    let t = format!(
        "#!/bin/bash\n# 可丟環境: ct-900 收據: {}\n",
        "x".repeat(300)
    );
    assert!(rehearsal(&t).is_none());
    let t = format!(
        "#!/bin/bash\n# 可丟環境: ct-900 收據: {}\n",
        "x".repeat(160)
    );
    assert!(rehearsal(&t).is_some());
    let t = "#!/bin/bash\n# 可丟環境: ct\u{200B}900 收據: r.json\n";
    assert!(rehearsal(t).is_none());
}
