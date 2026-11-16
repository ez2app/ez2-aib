#!/usr/bin/env bash
# auto_find_work_dm.sh —— aib-drive act(自動找事做)寄信走這支,參數原樣轉給 aib(呼叫端帶 dm)。
#
# 為什麼要一支殼(S79-sender,519d0 11-16 08:46 裁,a9136 設計):aibd 在 launchd 底下起 act,
#   祖先鏈上沒有席位 pane、沒有背景分身、也沒有本倉 src/ 腳本 ⇒ 抬頭「未認證@unknown」,收件席只當資料。
#   act 改呼叫這支本倉 src/ 腳本 ⇒ sender_origin 照現成規則在祖先鏈上認到它(來源名 auto-find-work-dm,
#   白話名 i18n aib.sender.origin.auto_find_work_dm = 自動找事做)。名字是推導來的,不新增認身分的路。
# ★ 不用 exec:exec 會把這個行程的命令列換成 aib 的,祖先鏈上就看不到這支腳本了。
# ★ 不加身分旗標、不讀環境變數自報身分。
# ★ 限制(明寫):同帳號 AI 也能呼叫這支殼 —— 抬頭只說「是這支程式寄的」,是紀律標記,不是防線。
# ★ Windows 沒有 bash:這支只管 macOS / Linux(Windows 另做 Rust 殼,未做)。
set -u
here="$(cd "$(dirname "$0")" && pwd -P)"
aib="$here/../../../bin/aib"           # <倉根>/bin/aib(本檔在 <倉根>/aib/src/cockpit/)
if [ ! -x "$aib" ]; then
  echo "auto_find_work_dm: 找不到 $aib,未送出" >&2
  exit 2                                # 2 = 一個字都沒送到(aib-drive 當 NotSent,可重送)
fi
"$aib" "$@"                           # 只轉發:呼叫端自己帶 dm(aib-drive AibDm::args = [dm, addr, text])
exit $?
