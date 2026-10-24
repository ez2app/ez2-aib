# 標題接軌 title-beat + 信任狀態紅燈(E2.2)

## 兩件事合成一條標題

```
AIB ∴ ● a seat/Claude [dev1] registry_field_absent
    │ │                            └─ 為什麼是這個顏色(code 直接印出來)
    │ └─ 信任燈
    └─ 節拍(每次取樣前進一格)
```

**節拍**存在的理由:靜止的標題和當掉的更新器長得一模一樣。沒有節拍,
看板凍在三小時前你也看不出來——節拍讓「還活著」變成畫面上看得見的事實。

**紅燈**只看機器觀測得到的欄位,缺欄位一律紅(fail-closed),
與 E2.7 血緣律的 `registry_field_absent` 同一條紀律。標題印的是 code 不只是顏色,
因為「為什麼是紅的」比「它是紅的」有用得多。

## 產品版號與程式標題

產品版號只有頂層 `VERSION` 一份。`cockpit-env.sh` 讀取後匯出
`EZ2_AIB_VERSION`,`cockpit-full.sh` 再產生完整 `Ez2 AIB v<semver>` 標題；CLI
`aib --version` 也讀同一檔。任何 release 都不准在標題程式另抄一份數字。

動態 title beat 依既有畫面裁定只顯示 major.minor.patch,刻意拿掉 prerelease
尾碼；完整 cockpit 標題與 CLI 仍保留 prerelease。兩種呈現都由同一個原始值
推導,不是兩個版本。已經開著的 cockpit 可能仍保留啟動時完整標題；重新載入或
重開後才會取得新的完整值,而 title beat 每拍會重新讀取目前 `VERSION`。

## 判準

| 情況 | 燈 | code |
|---|---|---|
| registry 查無此 session | 紅 | `no_registry_record` |
| 記錄裡沒有心跳 | 紅 | `no_heartbeat` |
| 心跳超過 1 小時沒動 | 紅 | `heartbeat_stale` |
| 綁定申請候人核可 | 紅 | `bind_pending` |
| 記錄沒有驗證欄位 | 紅 | `registry_field_absent` |
| registry 驗證未通過 | 紅 | `not_registry_verified` |
| 人已核可綁定,真綁候 EA.1 | 黃 | `approved_pending_ea1` |
| 已驗證但心跳逾 15 分鐘 | 黃 | `verified_but_aging` |
| 已驗證且心跳新鮮 | 綠 | `verified_fresh` |

## 只認明寫的 `registry_verified`

欄位不存在就是沒驗證(`registry_field_absent`),不從其他欄位推導。
**心跳證明它還活著,不證明它是誰**——把有心跳畫成綠燈是最貴的那種假綠燈。

舊載體 v2 曾用 `binding_uid` / `formal_eligible` / `native_attestation` 推出
`bound_unattested` / `bound_attested` 兩格;那份登記簿 08-11 起就停了,正式路徑
(`title-beat.sh`)也只傳 `None` 進來,10-25 整段拿掉。席位檔另走 `seat_light`,上限黃燈。

## 純度與分工

判定是純函數(`title_beat.py`),不讀時鐘也不讀檔案:`now_ms` 與記錄都由呼叫端注入,
同一份輸入必得同一條標題。取樣與貼字串在 shell(`title-beat.sh`);
規則有測試、取樣沒有,兩者分開才驗得動。

## 顯示在哪(續作補上的部分)

`--install` 同時設兩處,兩處同源都讀 `@aib_title`:

- `set-titles-string`:終端機視窗標題
- `status-right`:艙內狀態列右側 —— 標題在 tmux 裡常被蓋掉或看不到,
  狀態列才是艙內永遠看得見的那一行

取樣**不另起行程**:掛在左欄艦隊面板既有的迴圈裡(它本來就每 3 秒跑一次)。
好處是那個面板死了節拍就跟著凍住,而「更新器死了」正是要看得見的事實——
如果另起一支獨立行程,面板死了標題還在跳,那才是騙人的畫面。

## 狀態表黃金幀

九種信任狀態各畫一行,凍在 `tests/golden/title-states.frame.txt` 逐位元組比對。
案例表 `LIGHT_CASES` 自帶斷言:每個案例宣告的 code 必須真的被算出來,
規則改了而案例沒跟上就會炸。更新:`GOLDEN_UPDATE=1 python3 tests/test-title-beat.py`。

## 用法

```
title-beat.sh --once --print    # 取樣一次只印(不碰 tmux)
title-beat.sh --once           # 取樣並寫進 tmux 的 @aib_title
title-beat.sh --loop           # 常駐(EZ2_TITLE_INTERVAL 秒一拍,預設 5)
title-beat.sh --install        # 設 set-titles,讓視窗標題吃 @aib_title
```
