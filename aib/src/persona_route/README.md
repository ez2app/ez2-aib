# persona route 登記層(E5.4)

大綱原文:經 gateway 寫入 persona route(**先登記 routeKey**)。
本版只做登記層——正式寫入候 E5.5 owner adapter 解鎖。

## 三條紅線(寫死在程式與測試裡,不是寫在註解裡)

### 1. vault 的角色 .md 絕不直寫

角色檔是整檔 hash,動一 byte 全艦隊掉驗證。`refuse_direct_write()` 對任何指向
角色檔的路徑硬拒,而且**先於其他檢查**跑——登記階段就擋掉,不要等到有人真的
去寫才發現。紅線用獨立的例外型別與獨立的退出碼(3),因為它不是「再試一次就好」。

### 2. gateway 回 rc=0 不算成功

判準沿用 ez2-ai 的 `receipt.js`(唯讀考察
`git -C ~/ez2-ai show origin/dev:desktop-shell/src/chat-core/receipt.js`):
`vault-write` 的成功條件是 **`modifiedVault === true` 且 blockers 為空**。

`gateway_write_ok()` **完全不看 exit code**。一個沒改到任何東西卻 exit 0 的呼叫,
是「什麼都沒發生」不是「成功」。測試涵蓋 `{"rc":0}` / `{"ok":true}` / `{"exit0":true}`
都不算成功,以及 `1` / `"true"` / `"yes"` 這類 truthy 冒牌貨不得過關
(`modifiedVault` 必須是 `True` 本身)。

### 3. 天花板未解鎖前一律 staging

本模組**沒有任何真寫入路徑**。`register()` 只把登記記錄寫進 staging,
`plan_write()` 只回一份「將來會送什麼」的計畫(`executes_now: false`)。
有一條 AST 檢查斷言模組內所有寫入呼叫都落在 staging 的暫存檔上——
哪天有人加了一行能改 vault 的程式碼,那條會先紅。

## 為什麼登記的是 routeKey 而不是資料夾

引 ez2-ai 路由中心合約:「UID 是筆記身份,routeKey 是寫入語意,資料夾只是目前
顯示位置。」執行期解析鏈是 `routeKey → route-key-index → folderId →
folder-id-index → currentPath`。硬寫中文資料夾路徑正是那份合約要消滅的東西,
所以 `validate_route_key()` 會拒絕 `Ez2/核心/決策紀錄` 這種路徑形狀。

## 用法

```
route_register.py register --route-key persona.main.signal --purpose 人格訊號回寫
route_register.py list
route_register.py plan --route-key persona.main.signal --payload-sha256 <sha>
route_register.py check-response --response-json '{"modifiedVault":true,"blockers":[]}'
```

`check-response` 的退出碼就是判準:0=真的改到了,1=沒有(含 rc=0 但沒改到)。

## 契約對照(續作補上的部分)

登記前必須通過 `check_against_contract()`,判準來自 ez2-ai 路由中心契約本身:

| 情況 | 結果 | code |
|---|---|---|
| 契約讀不到 | 拒 | `contract_unavailable` |
| 契約裡沒有這個 routeKey | 拒 | `route_not_in_contract` |
| 契約標記不可由 AI 寫入 | 拒 | `route_not_ai_writable` |
| 資料夾違反 write_rules 第 7 條(絕對路徑/`..`/家目錄) | 拒 | `unsafe_folder_path` |
| dataLevel 為敏感或機密 | 拒 | `sensitive_data_level` |
| 可寫且非敏感 | 過 | `contract_ok` |

**契約讀不到就不登記**(fail-closed):看不到規則不等於沒有規則。
敏感/機密一律擋——那是 owner 決策,登記層不該能放行,
與 E2.6「AI 不得核可身份升權」同一條精神。

### 現況:契約裡還沒有 persona 專用的路

我對真契約實查過(40 條 route,29 條 aiWritable,其中 26 條非敏感),
**沒有任何 `persona.*` 開頭的 routeKey**。這代表 E5.4 的登記層現在無路可登記——
要先在 ez2-ai 路由中心新增那條路,不是本 lane 能自己造的
(自行造路正是 `route_not_in_contract` 要擋的事)。
測試裡有一條記錄這個現況:哪天契約新增了 persona 路,那條會紅,提醒同步更新。

## 完成態的裁定(總艦長 a seat,2026-08-09)

動 ez2-ai 路由契約屬**跨 repo lane**,已收錄決策包序 8,與 E5.9 同場候 the maintainer。
因此本 lane **以現狀為完成態**:登記層 fail-closed + 契約哨——契約裡沒有
persona 開頭的 routeKey 時,`route_not_in_contract` 擋下並有測試記錄該現況。
E5.4 以此進審;routeKey 落地那天再接線,屆時只需補「送出 + 依 modifiedVault
判定 + 落 receipt」那一段,判準與測試都已經寫好。

## 現況邊界

- 沒有真的呼叫過 gateway;`plan_write` 是計畫不是預演,它不發任何請求。
- 登記記錄不代表授權:`status` 永遠是 `registered_pending_owner_adapter`,
  記錄裡的 `_ceiling` 欄位明寫這一點。
- E5.5 解鎖後要補的是「送出 + 依 modifiedVault 判定 + 落 receipt」那一段,
  判準本身已經寫好且有測試,屆時不必重想。
