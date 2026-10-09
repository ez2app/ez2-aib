#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# AIB 訊息待審區 —— 已搬成收費外掛 pending-review(10-08 the maintainer:不放在免費程式裡)。這支只負責轉交:
#   有裝外掛 ⇒ 以這支的身分執行外掛那份(source,$0 仍是這支 ⇒ 外掛裡找同層檔案的寫法一個字不用改);
#   沒裝 ⇒ 什麼都不做、結束碼 0(不畫、不佔高、不報錯)。准不准開仍由功能開關(advanced 方案 + pending_panel)判。
#   找法同 addon_path.py(倉外 ~/.ez2-aib/plugins 優先,再倉裡 plugins/);用純 bash 找,滑鼠每點一下都會叫到這支,不起 python。
# 10-09:轉交殼也照駕駛艙入口腳本的規矩先載共用環境(test-contract-seal-population:pane 腳本要 source cockpit-env.sh);
#   拆分前這支本來就第一行載它 ⇒ 失敗情況跟以前一樣。外掛本體也會再載一次,同一份設定重算、值不變。
set -euo pipefail   # 拆分前的原版第 3 行就是這行(test-env-consumer-errexit:載 cockpit-env.sh 的要能在失敗時停)
. "$(dirname "$0")/cockpit-env.sh"
_aib_pr_here="$(cd "$(dirname "$0")" && pwd)"
for _aib_pr_target in "${EZ2_AIB_HOME:-$HOME/.ez2-aib}/plugins/pending-review/pane-pending-review.sh" \
                      "$_aib_pr_here/../../plugins/pending-review/pane-pending-review.sh"; do
  if [ -f "$_aib_pr_target" ]; then
    . "$_aib_pr_target"
    exit $?
  fi
done
exit 0
