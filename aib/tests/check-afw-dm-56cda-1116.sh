#!/usr/bin/env bash
# auto_find_work_dm.sh 只轉發參數(S79-sender,a9136 f8053b39 設計審 H1):
#   aib-drive 的 AibDm::args 已經是 [dm, addr, text];殼若再加一個 dm,act 實際跑 `aib dm dm …`,每封都壞。
#   (失敗時印 cargo 形狀的 `test … FAILED` 一行,knife.py 才認得出是哪一項紅)
#   這支用真殼 + 假 aib(把收到的參數逐行記下)跑一次,比對參數一字不差;找不到 aib 要 exit 2。
set -eu
here="$(cd "$(dirname "$0")" && pwd -P)"
shell="$here/../src/cockpit/auto_find_work_dm.sh"
t="$(mktemp -d)"
trap 'rm -rf "$t"' EXIT
mkdir -p "$t/bin" "$t/aib/src/cockpit"
cp "$shell" "$t/aib/src/cockpit/auto_find_work_dm.sh"
printf '#!/bin/sh\nfor a in "$@"; do printf "%%s\\n" "$a"; done > "%s/argv"\necho "RESULT stage=delivered id=q_test relay=- thread=-"\nexit 0\n' "$t" > "$t/bin/aib"
chmod +x "$t/bin/aib" "$t/aib/src/cockpit/auto_find_work_dm.sh"
out="$(bash "$t/aib/src/cockpit/auto_find_work_dm.sh" dm dab9/56cda "派工: 一 → 56cda
第二行 有空白")"
want="$(printf 'dm\ndab9/56cda\n派工: 一 → 56cda\n第二行 有空白\n')"
got="$(cat "$t/argv")"
[ "$got" = "$want" ] || { echo "FAIL argv:"; printf "%s\n" "$got"; echo "test check_afw_dm_forwards ... FAILED"; exit 1; }
case "$out" in *"RESULT stage=delivered"*) ;; *) echo "FAIL: RESULT line not passed through"; exit 1 ;; esac
rm "$t/bin/aib"
set +e
bash "$t/aib/src/cockpit/auto_find_work_dm.sh" dm dab9/56cda x 2>/dev/null
rc=$?
set -e
[ "$rc" = "2" ] || { echo "FAIL: no aib should be exit 2, got $rc"; echo "test check_afw_dm_no_aib ... FAILED"; exit 1; }
echo "ok auto_find_work_dm forwards its arguments as they are"
