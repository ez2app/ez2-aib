# SPDX-License-Identifier: Apache-2.0
"""Release-pinned SHA256 for packaged AIB data files (dataguard 對釘正本).

為什麼是 .py 不是 .json:pin 若放資料檔,它自己也要過權限閘,雞生蛋;
Python 模組走 import 不走資料閘,且測試 test-dataguard 會驗 pin 與
git 工作樹位元組逐一相符,release 漂移在 CI 就紅。

更新方式:改動任何被 pin 的資料檔時,必須同 commit 更新對應 pin
(測試強制;等同「資料檔改版=發版行為」的低成本執法)。
"""

# key = repo 相對路徑(呼叫端明示傳入,不做路徑推斷)
PINS = {
    "themes/aib-default.json":
        "dfc699f2369db55a5c832183a740bccd097b0aa7afa215c5bb771a54d6b99757",
    "src/messaging/notify-rules.json":
        "446166db94b5a1bc0cee86399851dfdabbbfa5cecba6bf04f195025b37128804",
}

# v0.7.0-alpha.1 發版時逐檔重算 sha256,兩份皆與 PINS 相符後才更新本標記——
# 標記是「這些指紋在哪一版被確認過」,不是為了讓測試變綠而改的數字。
# v0.9.0-alpha.2(a seat):同樣逐檔重算後才動本行。兩份實測與 PINS 逐位元組相符,
# 且本次整合(root/e8-governance + qa/e43-rereview)未改動這兩個資料檔——
# 亦即這是「重新確認」不是「重新對釘」。
# v0.9.0-alpha.3(a seat):同樣先逐檔重算才動本行。實測兩份與 PINS 逐位元組
# 相符(themes/aib-default.json = dfc699f2…、src/messaging/notify-rules.json = 446166db…),
# 且本版未改動這兩個資料檔——「重新確認」不是「重新對釘」。
# v0.10.0-alpha.1(EM.1 claimtruth 遷入):同樣先逐檔重算才動本行。實測兩份與 PINS
# 逐位元組相符(themes/aib-default.json = dfc699f2…、src/messaging/notify-rules.json
# = 446166db…),且 `git diff --name-only HEAD` 對這兩檔為空——本版未改動它們,
# 故為「重新確認」不是「重新對釘」。★本行是被版號 bump 連動的:test-dataguard 的
# test_pin_version_matches_product_version 會把它釘死在 VERSION 上,所以每次發版
# 都必須先重算再改。這正是「改一個地方會波及別人」的實例,而它被全套測試抓到。
# v0.11.0-alpha.1:同樣先逐檔重算(dfc699f2…/446166db…逐位元組相符,本版未動兩檔)
# ——「重新確認」非「重新對釘」。
# v0.12.0-alpha.1(a seat 收官發版):同樣先逐檔重算(dfc699f2…/446166db…
# 逐位元組相符,git diff 對兩檔為空)——「重新確認」非「重新對釘」。
# v0.15.0-alpha.1(a seat,the maintainer 直令發版):同樣先逐檔重算才動本行。實測
# 兩份與 PINS 逐位元組相符(themes/aib-default.json = dfc699f2…、
# src/messaging/notify-rules.json = 446166db…),`git diff --name-only HEAD` 對
# 這兩檔為空——「重新確認」非「重新對釘」。本版的主題工作只動 theme.py /
# theme-ctl.sh(覆寫層),**沒有動 palette 資料檔本身**,故指紋不變。
# v0.16.0-alpha.1(a seat):同樣先逐檔重算才動本行。實測兩份與 PINS
# 逐位元組相符(themes/aib-default.json = dfc699f2…、
# src/messaging/notify-rules.json = 446166db…),`git diff --name-only HEAD`
# 對這兩檔為空——「重新確認」非「重新對釘」。本版動的是右鍵選單與大綱,
# 未觸及任何被 pin 的資料檔。
# v0.17.0-alpha.1(a seat):同樣先逐檔重算才動本行。實測兩份與 PINS
# 逐位元組相符(dfc699f2…/446166db…),`git diff --name-only HEAD` 對這兩檔
# 為空——「重新確認」非「重新對釘」。★本版**動了主題相關的碼**
# (theme.py/theme-ctl.sh 的無底色層),但**沒有動 palette 資料檔本身**,
# 故指紋不變;這正是「pin 釘的是資料不是行為」的例子。
# v0.18.0-alpha.1(a seat):同樣先逐檔重算才動本行。兩份與 PINS 逐位
# 元組相符(dfc699f2…/446166db…),`git diff --name-only HEAD` 對這兩檔為空
# ——「重新確認」非「重新對釘」。本版只動文件與用字,零資料檔改動。
# v0.19.0-alpha.1(a seat):同樣先逐檔重算才動本行,兩份逐位元組相符,
# `git diff --name-only HEAD` 對這兩檔為空——「重新確認」非「重新對釘」。
# v0.20.0-alpha.1(a seat,the maintainer 直令發版):同樣先逐檔重算才動本行。實測兩份
# 與 PINS 逐位元組相符(dfc699f2…/446166db…),`git diff v0.19.0-alpha.1..HEAD`
# 對這兩檔為空——「重新確認」非「重新對釘」。本版動的是 CI 觸發、發版關卡、
# 判準分派與交接單,零資料檔改動。
# v0.21.0-alpha.1(a seat,the maintainer 直令發版):同樣先逐檔重算才動本行。實測兩份
# 與 PINS 逐位元組相符(dfc699f2…/446166db…),`git diff v0.20.0-alpha.1..HEAD`
# 對這兩檔為空——「重新確認」非「重新對釘」。本版動的是發版閘、CI 觸發與
# 自架 runner,零資料檔改動。
# v0.22.0-alpha.1(a seat,the maintainer 直令發版):同樣先逐檔重算才動本行。實測兩份
# 與 PINS 逐位元組相符,`git diff v0.21.0-alpha.1..HEAD` 對這兩檔為空——
# 「重新確認」非「重新對釘」。本版動的是 AI 綁定人格與第二大腦、右欄三行制,
# 零資料檔改動。
# ★ 本行在 v0.22.0-alpha.1 打 tag 時漏更新,是 test-dataguard 抓到的
#   (它比對 pins 版號與 VERSION 檔)——**發版當下 main 就帶著這支紅**,
#   而我發版前跑的 87/1 把它算進「既有紅」。教訓:發版前的既有紅要逐支確認
#   它為什麼紅,不能只看數字有沒有變多。
# v0.24.0-alpha.1(seat 0c153):同樣先逐檔重算才動本行。實測兩份與 PINS 逐位元組
# 相符(themes/aib-default.json = dfc699f2…、src/messaging/notify-rules.json
# = 446166db…),且 `git diff --name-only origin/main...HEAD` 對這兩檔為空——
# 本版未改動它們,故為「重新確認」不是「重新對釘」。
# v0.25.0-alpha.1(第一艦長 4cdc5,the maintainer 直令發版):同樣先逐檔重算才動本行。
# 實測兩份與 PINS 逐位元組相符(themes/aib-default.json = dfc699f2…、
# src/messaging/notify-rules.json = 446166db…),且 `git diff --name-only
# origin/main...HEAD` 對這兩檔為空——「重新確認」不是「重新對釘」。
# 本版動的是右欄脊柱、席位守衛接線、開席回執與繼承閘,零資料檔改動。
# v0.26.0-alpha.1(第一艦長 4cdc5,the maintainer 直令發版):同樣先逐檔重算才動本行。
# 實測兩份與 PINS 逐位元組相符(dfc699f2…/446166db…),且 `git diff --name-only
# 70962eb..HEAD` 對這兩檔為空——「重新確認」不是「重新對釘」。
# 本版動的是 hub socket 變數收斂與三份落檔更正,零資料檔改動。
# v0.27.0-alpha.1(第一艦長 4cdc5,the maintainer 直令發版):同樣先逐檔重算才動本行。
# 實測兩份與 PINS 逐位元組相符(dfc699f2…/446166db…),且
# `git diff --name-only v0.26.0-alpha.1..HEAD` 對這兩檔為空——「重新確認」
# 不是「重新對釘」。本版動的是艦長列、測試隔離與一道政策閘,零資料檔改動。
# v0.28.0-alpha.1(第一艦長 4cdc5,the maintainer 直令發版):同樣先逐檔重算才動本行。
# 實測兩份與 PINS 逐位元組相符(dfc699f2…/446166db…),且
# `git diff --name-only v0.27.0-alpha.1..HEAD` 對這兩檔為空——「重新確認」
# 不是「重新對釘」。本版是量測與盤點版,零產品碼改動、零資料檔改動。
# v0.29.0-alpha.1(第一艦長 e5300,the maintainer 直令發版):同樣先逐檔重算才動本行。
# 實測兩份與 PINS 逐位元組相符(dfc699f2…/446166db…),且
# `git diff --name-only v0.28.0-alpha.1..HEAD` 對這兩檔為空——「重新確認」
# 不是「重新對釘」。本版動的是三道守衛(絕對路徑、演習旗標、hub 擁有權)
# 與一份審查債正本,零資料檔改動。
# v0.30.0-alpha.1(第一艦長 e5300,the maintainer 直令發版):同樣先逐檔重算才動本行。
# 實測兩份與 PINS 逐位元組相符(dfc699f2…/446166db…),且
# `git diff --name-only v0.29.0-alpha.1..HEAD` 對這兩檔為空——「重新確認」
# 不是「重新對釘」。本版動的是標題定址與發版腳本,零資料檔改動。
# v0.31.0-alpha.1(claude 身份綁定 --session-id):同樣先逐檔 shasum 重算,
# 兩份與 PINS 逐位元組相符(dfc699f2…/446166db…),且 `git diff --name-only
# main` 的變更集只有 cockpit/tests/VERSION——零資料檔改動,重新確認非重新對釘。
# v0.32.0-alpha.1(收官:生產驗證+分支對清):同樣先逐檔 shasum 重算,兩份與
# PINS 逐位元組相符(dfc699f2…/446166db…);本版只動版號載體與 CHANGELOG,
# 零資料檔改動,重新確認非重新對釘。
# v0.33.0-alpha.1(艦長生命週期第一刀):同樣先逐檔 shasum 重算,兩份與 PINS
# 逐位元組相符(dfc699f2…/446166db…);本版動 rotation/docs/tests,零資料檔改動。
# v0.34.0-alpha.1(aib idle 整合):同樣先逐檔 shasum 重算,兩份與 PINS
# 逐位元組相符(dfc699f2…/446166db…);本版動 cockpit/aib 掛鉤與新模組,零資料檔改動。
# v0.35.0-alpha.1(D 綁定四片+G 收尾+C v3.2):逐檔 shasum 重算,兩份與 PINS
# 逐位元組相符(dfc699f2…/446166db…);零資料檔改動。
# v0.36.0-alpha.1(收官:分支帳全清+watch 常駐):逐檔 shasum 重算,兩份與 PINS
# 逐位元組相符(dfc699f2…/446166db…);零資料檔改動。
# v0.37.0-alpha.1(B lane 全交+F 守門鏈+巡查程式化+指揮移交):逐檔 shasum
# 重算,兩份與 PINS 逐位元組相符(dfc699f2…/446166db…);零資料檔改動。
# v0.38.0-alpha.1(制度層收官:四道機器閘+W1/W2):逐檔 shasum 重算,兩份與
# PINS 逐位元組相符(dfc699f2…/446166db…);零資料檔改動。
# v0.39.0-alpha.1(身分→人格→大腦→真寫全通):逐檔 shasum 重算,兩份與 PINS
# 逐位元組相符(dfc699f2…/446166db…);`git diff v0.30.0-alpha.1..HEAD` 對這兩檔
# 為空——「重新確認」不是「重新對釘」。
# ★★ 補記(a seat 08-18 深夜):v0.39.0-alpha.1 那次發版**漏改本行**,於是
#   test_pin_version_matches_product_version 自發版起一直是紅的,而發版當下
#   宣稱「全綠」。**這是第二次**——上面 v0.22.0-alpha.1 那段已經記過同一件事。
#   同型第二次發生 = 不是人的疏忽,是閘門的形狀不對:發版腳本只驗「被釘檔沒變」,
#   從不驗也不更新本行,**它查的是另一件事,漏的這件從來沒有人查**。
#   故同 commit 為 tools/aib-release.sh 補上版號一致性檢查(修產生器,不只修已產生)。
# v0.43.0-alpha.1(2026-08-21)重新對釘:**自己重算 sha256**,不抄上面的註解
# ——註解是上一次的結論,重算才是這一次的證據。兩份逐位元組相符
# (themes/aib-default.json = dfc699f2…、src/messaging/notify-rules.json
# = 446166db…),且 `git diff --name-only HEAD` 對這兩檔為空:本版未改動它們,
# 只是版號前進。★ 這一行是被 tools/aib-release.sh 擋下來才補的——閘門做對了
# 它該做的事,而我在跑閘門之前並不知道要改它。
# v0.59.2-alpha.1(2026-09-11)重新確認:本版未改 pinned 兩檔,sha256 現算
# themes/aib-default.json = dfc699f2…、src/messaging/notify-rules.json = 446166db…
# v0.62.1-alpha.1(2026-09-15)重新確認:本版未改 pinned 兩檔(git diff 93f6b7fe..244d85f3 對兩檔為空),sha256 現算
# themes/aib-default.json = dfc699f2…、src/messaging/notify-rules.json = 446166db…
# v0.62.2(2026-09-18)重新確認:本版未改 pinned 兩檔(themes/aib-default.json、src/messaging/notify-rules.json),sha256 現算同前。
# v0.62.3(2026-09-19)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.62.4(2026-09-20)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.62.5(2026-09-20)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.62.6(2026-09-20)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.62.7(2026-09-20)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.62.8(2026-09-21)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.63.3(2026-09-22)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.63.4(2026-09-22)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.63.5(2026-09-23)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.63.6(2026-09-23)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.63.7(2026-09-23)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.63.8(2026-09-23)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.63.9(2026-09-23)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.63.11(2026-09-24)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.63.12(2026-09-24)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.63.13(2026-09-24)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.64.0(2026-09-24)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.64.1(2026-09-24)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.64.2(2026-09-25)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.64.3(2026-09-25)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.64.4(2026-09-25)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.64.6(2026-09-25)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.65.0(2026-09-25)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.65.1(2026-09-25)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.66.0(2026-09-26)重新確認:本版未改 pinned 兩檔,sha256 現算同前。
# v0.67.0(2026-09-26 a seat)重新確認:本版未改 pinned 兩檔(0.66.0..main 對兩檔 0 顆),sha256 現算 dfc699f2…/446166db… 同前。
# v0.68.0(2026-09-26 @28b40ad7)重新確認:本版未改 pinned 兩檔(v0.67.0..main 對兩檔 0 顆),sha256 現算 dfc699f2…/446166db… 同前。
# v0.69.0(2026-09-27 integrate/v069-4b2a7)重新確認:本版未改 pinned 兩檔(v0.68.0..整合 tip 對兩檔 0 顆),sha256 現算同前。
# v0.69.1(2026-09-27 integrate/v0691-1857a)重新確認:本版未改 pinned 兩檔(c977344f..整合 tip 對兩檔 0 顆),sha256 現算同前。
# v0.69.2(2026-09-28 release/v0692-codex)重新確認:本版未改 pinned 兩檔(v0.69.1..候選對兩檔 0 顆),sha256 現算 dfc699f2…/446166db… 同前。
# v0.69.3(2026-09-28 aaa-claudebot-cloud-v1)重新確認:本版未改 pinned 兩檔(v0.69.2..候選對兩檔 0 顆),sha256 現算 dfc699f2…/446166db… 同前。
# v0.69.4(2026-09-29 fix/dm-address-teaching-7fb31)重新確認:本版未改 pinned 兩檔(4ea15f0b5..候選對兩檔 0 顆),sha256 現算 dfc699f2…/446166db… 同前。
# v0.69.5(2026-09-29 release/v0695-pr6)重新確認:本版未改 pinned 兩檔(v0.69.4..候選對兩檔 0 顆),sha256 現算 dfc699f2…/446166db… 同前。
# v0.70.0(2026-09-30 release/v0700-ce24a)重新確認:本版未改 pinned 兩檔(v0.69.5..候選對兩檔 0 顆),sha256 同前。
# v0.70.1(2026-10-05 aaa-claudebot-cloud-v1)重新確認:本版未改 pinned 兩檔(v0.70.0..候選對兩檔 0 顆),sha256 同前。
# v0.70.2(2026-10-05 aaa-claudebot-cloud-v1)重新確認:本版未改 pinned 兩檔(v0.70.1..候選對兩檔 0 顆),sha256 同前。
# v0.70.3(2026-10-05 aaa-claudebot-cloud-v1)重新確認:本版未改 pinned 兩檔(v0.70.2..候選對兩檔 0 顆),sha256 同前。
# v0.70.4(2026-10-05 aaa-claudebot-cloud-v1)重新確認:本版未改 pinned 兩檔(v0.70.3..候選對兩檔 0 顆),sha256 同前。
# v0.70.5(2026-10-05 aaa-claudebot-cloud-v1)重新確認:本版未改 pinned 兩檔(v0.70.4..候選對兩檔 0 顆),sha256 同前。
# v0.70.6(2026-10-05 aaa-claudebot-cloud-v1)重新確認:本版未改 pinned 兩檔(v0.70.5..候選對兩檔 0 顆),sha256 同前。
# v0.70.7(2026-10-05 aaa-claudebot-cloud-v1)重新確認:本版未改 pinned 兩檔(v0.70.6..候選對兩檔 0 顆),sha256 同前。
# v0.70.8(2026-10-05 aaa-claudebot-cloud-v1)重新確認:本版未改 pinned 兩檔(v0.70.7..候選對兩檔 0 顆),sha256 同前。
# v0.70.9(2026-10-05 aaa-claudebot-cloud-v1)重新確認:本版未改 pinned 兩檔(v0.70.8..候選對兩檔 0 顆),sha256 同前。
# v0.70.10(2026-10-05 aaa-claudebot-cloud-v1)重新確認:本版未改 pinned 兩檔(v0.70.9..候選對兩檔 0 顆),sha256 同前。
# v0.70.11(2026-10-05 aaa-claudebot-cloud-v1)重新確認:本版未改 pinned 兩檔(v0.70.10..候選對兩檔 0 顆),sha256 同前。
PINNED_AT_VERSION = "0.70.11"
