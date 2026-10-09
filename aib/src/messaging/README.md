# 訊息咽喉與血緣律(E2.7)

大綱原文:`send/dm 驗 sender=registry_verified 主AI;子AI 無訊息動詞只回報 spawner`。

## 兩個檔

- `lineage.py` — 判定核心(純函數 `decide(request, sender)`)+ CLI。
  回 `{decision, code, detail}`,code 是穩定字串,給人看也給機器讀。
  判定事件由 `collector_common.make_event` 組(E8.3 反重造閘:不造第二套
  HLC/actor/事件組裝),actor 是守門者自己(kind=system),**不冒充送訊者**。
- `aib-send.sh` — 咽喉包裝(原理 5:閘門設在咽喉不設在善意)。
  預設 dry-run;deny 時即使帶 `--execute` 也一封都不送 —— 閘門在前,旗標在後。

## 判定優先序(全部 fail-closed)

| 情況 | 結果 | code |
|---|---|---|
| 輸入殘缺 / 未知動詞 | deny | `malformed` / `unknown_verb` |
| 子AI 血緣不明 | deny | `lineage_missing` |
| 子AI 回報自己的 spawner | allow | `report_to_spawner` |
| 子AI 回報別人 | deny | `report_off_lineage` |
| 子AI 用任何訊息動詞 | deny | `sub_agent_no_messaging` |
| 主AI 身份紀錄沒有任何綁定欄位 | deny | `registry_field_absent` |
| 主AI 已綁定但 ACB 標記未經證實 | **allow**(裁決後) | `bound_unattested` |
| 主AI 未通過 registry 驗證 | deny | `not_registry_verified` |
| 主AI 已驗證卻仍標 T0 候綁 | deny | `identity_inconsistent` |
| 主AI 已驗證 + 訊息動詞 | allow | `verified_main` |
| 人類 | allow | `human_principal` |
| 系統/收集器 | deny | `system_no_messaging` |

## 為什麼「欄位不存在」等於未驗證

`registry_verified` 只認身份紀錄裡**明寫**的那一欄。不從「有心跳」「檔案存在」
這類旁證推論——心跳新鮮度證明它還活著,不證明它是誰。ACB 那四個後門的共同形狀
就是拿旁證當身份證明,這裡把那條路直接封死,並用 `registry_field_absent` 這個
獨立的 code 讓維運看得出是「沒驗」而不是「驗失敗」。

子AI 自報 `registry_verified: true` 也沒用:`kind` 先判,血緣律不看宣稱。

## 判定留痕(續作)

設 `EZ2_AIB_LEDGER=<帳本路徑>`(或 `lineage.py --ledger PATH`),每次判定都寫一筆
`system.note` 進帳本:**放行與擋下都記**——只記放行的話,越權嘗試就查無此事。
守門者交的是**草稿**,event_id/HLC/integrity 由帳本發(自帶 event_id 會被帳本拒收,
這點有測試釘著)。事件的 subject 是被判定的身份,actor 是守門者自己 kind=system,
不冒充送訊者。

寫入失敗**不改變判定結果**(原理 10:閘門可擋,回饋器永不擋),但會在 stderr 出聲;
CLI 的輸出會帶 `event_id: null`,讓呼叫端看得出「這筆沒記到」。

## 拒絕理由雙語

判定回傳除了穩定的 `code`,還帶 `reason`(走 `i18n/` 的 `aib.lineage.<code>`,
15 個碼兩語齊全,有測試逐一驗)。i18n 整個壞掉時退回 code 本身——
守門者不因翻譯壞掉而失聲。

## R-30 出站 lint 與 Alt+m 送信(E2.4)

`r30_lint.py` 擋三類字元:圈號 ①②、全形英數 ＡＢ１２、框線字元 ─│┌
(外加我自行分出的第四類:區塊填充字 █▓▒░ ,獨立 code 方便單獨放寬)。
擋它們的理由是機器可讀性與版面:圈號與框線在不同終端機寬度不一會撞歪版面,
全形英數讓 grep/比對/去重全部失準。**刻意不擋全形標點**(,。「」)——
中文正文本來就用它們,擋了等於不准講中文。

**字碼範圍是依派工令的三個名稱推導的,不是從 ACB 抄來的。** 我在 `~/.acb`
只找得到「R-30 禁字元」這個名稱,找不到列舉的字元集。範圍攤在 `RANGES` 表裡
供審查者修正;日後若取得 ACB 正本清單,應以那份取代本表,不要兩邊各留一份。

`--fix` 只換有安全替代的字(全形→半形、①→`1.`),**不自動刪除框線字元** ——
自動刪字會改動作者原意,那不是 lint 該做的事,留給人決定。

送信流程 `ctl-send-message.sh`:

1. `--install` 綁 `M-m`(不綁裸 `m`,理由同 E2.12 的裸 `?`:root 表是全域的,
   中欄跑著真 AI 對話,搶走一個字母鍵等於使用者從此打不出那個字)。
2. `--compose` 開兩段對話框收「收件者/內容」,直接進預演。
3. `--preview` 跑 lint 與血緣律,印出**將執行的完整指令**,絕不送出。
4. `--confirm` 是**獨立的一步**才會真送。訊息送出去收不回來,而 Alt+m 很容易誤觸;
   沒有「記住我的選擇」這種選項。

lint 跑在身份判定**之前**:內容不合規就沒有「誰可以送」的問題。
違規一律不送,**沒有繞過旗標** —— 一開後門,R-30 就名存實亡。

## 現況邊界

- **欄位名修正(2026-08-08)**:原本讀 `registry_verified`,那個名字是猜的。
  EA.1 綁定完成後實查 498 筆 registry 記錄,`registry_verified` 一筆都沒有;
  真正存在的是 `binding_uid` / `session_status` / `formal_eligible` /
  `native_attestation` / `authority_level`。現在改讀這些真欄位。
- **已綁但未證實(主管裁決 2026-08-08:放行)**:77 筆有 `binding_uid`,
  其中 68 筆 `session_status=active`,而那 68 筆自己標著 `formal_eligible=false`、
  `native_attestation=false`、`authority_level=same_user_registry_unattested`。
  裁決理由:**T1 的語意是「我知道你是誰」,不是「我確信你是誰」**;後者是 T2。
  registry 認得即 T1,送訊息是 T1 能力。把 attested 塞進 T1 會讓全艦隊落回 T0 而停擺,
  那不是安全是癱瘓(原理 10)。三個附帶條件各有一條測試釘著:
  1. 判定事件的 summary 必須帶 `authority_level` 原值——放行可以,不留痕不行。
  2. `bound_unattested` 保留獨立碼,不併回 `verified_main`——否則 attested 上線時
     分不出「驗過」與「認得但沒驗過」。
  3. 高風險動作(部署/合併/改執法設定)**不在本閘授權範圍**:它們不在 `KNOWN_VERBS`
     裡,對任何人都是 `unknown_verb` 擋掉,授權歸 E2.8 政策 lane。
     訊息可讀可查可事後討論,部署不是。
- 只有訊息通道這一個咽喉;tmux `send-keys` 這種側路不在本 lane 範圍(候 E2.10 哨兵)。
- **閘門目前是自願的**:任何人仍可直接跑 `acb send` 繞過本腳本。真正的強制需要
  在 PATH 層放 shim 或掛 hook,而那會影響活著的艦隊,不是我能單方面裝的。
  本 lane 交的是「可被強制執行的判定」;要不要把它變成唯一入口,是艦長的決定。
  在那之前,繞道走的訊息不會留下判定痕跡——這是已知缺口,不是已解問題。

## 四色通知契約(E1.3)

`notify_policy.py` 是唯一判級器，資料規則在 `notify-rules.json`。紅、黃、綠、藍
固定依最嚴等級取值；AI 對 AI 例行訊息另有 blue lock，即使內文出現 `BLOCKED`
也不靠文字自行升級。只有結構化 `requested_level=red|yellow` 且
`escalation_reason` 非空時可升級；每次有效升級或違規嘗試都追加 owner-only
metadata 稽核，不保存訊息或理由原文。`audit-report` 依完整 `actor_ai_id` 彙總，
不以顯示名或 UUID 尾碼合併身份。

三個消費面都調同一模組：E1.3 ticker、E2.10 哨兵，以及底部對話 tab 的四色未讀
數。`notify_adapter.py` 才能跨出副作用邊界：macOS 走 argv-only `osascript`，
Windows/WSL 走靜態 PowerShell 程式加環境資料；紅/黃另發系統通知，紅/黃/綠會將
四區 pane 暫時套成資料檔指定色。閃爍以 generation token 防競態，舊通知的延遲
restore 不得覆寫新通知，最後從每個 pane 的 theme contract 還原兩態樣式。

總開關是 `M-n`；狀態可機器驗回：

```bash
src/cockpit/ctl-notify.sh show
src/cockpit/ctl-notify.sh toggle
src/cockpit/ctl-notify.sh toggle-level yellow
python3 src/messaging/notify_policy.py audit-report /path/to/notify-audit.jsonl
```

哨兵只有明示 `--notify-state` 才啟用副作用 runtime；測試一律注入 fake adapter，
不接觸活體 tmux 或桌面通知。若同時提供 `--notify-session`，目標必須是具四個唯一
role 與完整 theme pane contract 的 AIB cockpit，否則只回結構化失敗，不猜 pane。

目前 ACB `send` 傳輸沒有可驗證的 notification envelope，且本工作包禁止改事件
schema。因此 AIB 出站 AI 訊息仍以「不標即 blue」運作；red/yellow 的結構化理由
可在 AIB 內部事件與哨兵路徑驗證，但尚不能宣稱能跨 ACB 帳本重放。正式補齊要由
E1.4/E2.18 的 schema/DM 設計審查提供欄位與送達收據，不能把理由塞進摘要或
`data_ref` 猜值。

## 抬頭上的來源名(`unattested:<名>`,2026-09-05)

`unattested` 這一格是 EG.5 留的**最低權限身分格**:推導不出席位時,系統誠實寫
「證明不了」,不再兜底猜 `human:$USER`。但它一直只寫得出 `unattested:unknown` ——
連 `cooldown.py` 這種**本倉自己的自動化**送出來的通知,收件端也看不出是誰送的。

現在名字由 `src/cockpit/sender_origin.py` 從**行程祖先鏈**推導:取最近一支
本倉 `src/` 底下的 `.py`/`.sh`,用它的檔名(`cooldown.py` → `unattested:cooldown`)。

三件寫清楚,免得下一個人以為它比實際更強:

1. **權限一格沒動。** `kind` 仍是 `unattested`,血緣閘仍走 `unattested_sender`。
   這一刀只讓抬頭說得出來源。
2. **不是自報。** offhub 那一段明文裁過「讓送方自選顯示名 = 又開一個冒充面」。
   本刀不繞過那條裁定,而是讓它的**前提消失**:名字不是送方寫的,是機器讀的;
   要冒充得先偽造自己的 ppid 鏈,不是設一個環境變數。
3. **推不出來一律回 `unknown`。** 真人從純終端送、`tests/` 底下的臂送,都推不出名字
   —— 那是對的,`tests/test-aib-dm-cli.py` 那三支臂驗的正是這一格。
   失效方向刻意落在低權格:不猜、不升權。

★ 一格容易做錯的:「是來源但名字表達不出來」與「這不是來源」**不得回同一個值**。
  同形的話,一支名字含怪字元的腳本會讓抬頭掛上它**祖父**的名字,而那是一個
  看起來完全正常的錯誤歸屬。模組用 `UNNAMEABLE` 把兩者分開,臂
  `tests/test-sender-origin.py::test_unnameable_source_stops_instead_of_naming_its_grandparent` 釘著。
