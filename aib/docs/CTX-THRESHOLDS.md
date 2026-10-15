# CTX 門檻總表 / CTX threshold table

單一資料源 / Single source: `aib/policy/ctx-thresholds.json`(`triggers` 七格 + `texts` 字串鍵),
讀者一律經 `aib/src/rotation/ctx_policy.py`。碼裡不寫門檻數字。
All readers go through `ctx_policy.py`; no threshold number is written in code.

## 繁中

| 用量 | 會發生什麼 | 哪支程式 |
|---|---|---|
| 30% | 靜默精煉:這則回完跑 `aib memory tidy`(距上次精煉沒有新的真人訊息就不叫,格不算用掉) | `auto_distill_hook.py prompt`(Claude 注入)/ `stop`(其他家 aib dm 給自己)/ `agy-pre` |
| 50% | 同上 | 同上 |
| 70% | 同上 | 同上 |
| 80% | 一行交接提醒;艦隊派工閘到這格起不再派新工給這一席 | 同上;派工閘 `fleet_identity/ctx_scheduler.py` |
| 85% | 精煉 + 要求交接:注入「先做完收尾三步:`aib memory tidy` → `aib summary` → `aib handoff`(三步 + verify),再做別的」;之後每一則讀交接收據,verify 沒過就再提醒。**Claude 席 Stop 擋回合**直到 verify 前 8 項全過(同一則最多擋 3 次,擋滿放行並記 `gate_gave_up`);真人訊息含「先做」那一則不擋 | `auto_distill_hook.py prompt` + `review-gate`(Stop);非 Claude 席 `stop` 每 10 分鐘最多一封、一個對話最多 3 封再提醒;巡檢唯讀列出 `fleet_patrol.py scan`(「⚠ 85% 交接未完成」);交接憑證 `seat_continuity.py`、交接地板 / 建議 `lifecycle.py` / `handover_prep.py` |
| 90% | 靜默精煉(若 85 的交接還沒過,改成再提醒交接,不另叫精煉) | `auto_distill_hook.py` |
| 95% | 紅線:停止所有新工作,立刻交接 | `auto_distill_hook.py`(三條路) |

規則:
- 每一格同一個對話只觸發一次(已觸發紀錄 `state/auto-distill/<席>.json`,換對話重算)。
- 同一則跨過好幾格:交接類只講最重的那一句;要求交接 / 紅線那句已含整理記憶,不另叫精煉。
- 提醒字裡的 % 是讀 gauge(`$EZ2_CTX_DIR/<uuid>.pct`)來的,不讓 AI 自報;讀不到 / 過期 ⇒ 不猜、不輸出、不擋。
- 任何錯 ⇒ rc 0、無輸出(不能擋人講話)。
- 「先做」例外要靠 `src/persona-collect/human_filter.py` 認真人訊息;公開版目前沒有那一包(已列進 CLOUD-MISSING-FILES,
  等雲端補)。補進來之前:「先做」例外**不生效**(85 照擋),精煉格照觸發(不判「有沒有新的真人訊息」)。
- 安裝(10-15 起):**安裝 / 升級時就裝**——`auto_distill_hook.py ensure-all` 由 install.sh(一行安裝最後、建狀態目錄之前)、
  `aib init`、`aib update`(升級完成後,用升級後這份)呼叫:只裝這台有裝的 CLI(沒裝的略過並說明)、各家設定改之前先備份、
  重跑不重複裝、`AIB_AUTO_DISTILL=0` 全關;Codex 裝好印一行「請在 Codex 打 /hooks 信任一次」。開席時 `provider-launch.sh`
  那一段照留(已裝就不動;只在 `[ -t 0 ]`)。路徑經 `canonical_tree` 算正本樹(公開倉 `<倉>/aib/src/...`、pipx 本檔自己)。
- /clear 之後:Claude 換新對話編號、席位檔不變 ⇒ 由 `$EZ2_CTX_DIR/by-seat/<席碼>.json`(狀態列寫)反查席碼
  (session_id 相同、observed_at 12 小時內、那個艙有沒退役的席位檔且窗號對得上);席碼一律用登記的,不用新編號尾碼。
  兩者都對不上 ⇒ 當非 AIB 席。不把新編號寫回席位檔(席碼 = 登記 uuid 尾碼,dm 位址靠它)。

### 各家觸發路 / Per-brand trigger paths

| 品牌(provider) | 實際跑的程式 | 觸發路 | 85 能不能擋 | 裝在哪 | 用量量得到嗎 |
|---|---|---|---|---|---|
| claude | Claude Code CLI `claude` | UserPromptSubmit `prompt` 注入 + Stop `review-gate` | **能**(Stop 擋回合,同一則最多 3 次) | `~/.claude/settings.json`(或 `$CLAUDE_CONFIG_DIR`) | 能(狀態列寫 gauge;/clear 後走 by-seat) |
| codex | `codex`(本機 alias `ez2-codex`) | Stop hook `stop` → aib dm 給自己 | 不能(dm 再提醒:每 10 分最多一封、最多 3 封) | `~/.codex/hooks.json`(或 `$CODEX_HOME`);要在 /hooks 信任一次 | 能(`codex_ctx_probe` 一輪結束先刷) |
| grok | `grok` | Stop hook `stop` → aib dm | 不能(同上) | `~/.grok/hooks/aib-auto-distill.json` | 能(`grok_ctx_probe`) |
| copilot | `copilot` | agentStop hook `stop` → aib dm | 不能(同上) | `~/.copilot/hooks/aib-auto-distill.json` | 能(`copilot_ctx_probe`) |
| agy(gemini 是它的別名) | `agy` | PreInvocation `agy-pre`:叫模型前注入(ephemeral) | 不能(同格注入一次) | `~/.gemini/config/hooks.json` 的 `aib-auto-distill` | 能(agy 狀態列) |
| deepseek / nvidia / openrouter | aib 自己的 Python REPL(`deepseek_cli.py` 等;**不是** Claude Code) | 外層代觸發:REPL 每一輪回完 `api_tidy.turn_end` → `outer_turn` → 同一條 `stop` 路 | 不能 | 不用裝(在 REPL 程式裡) | **量不到**(gauge 的 pct 一律 None)⇒ **每 20 輪精煉**(`outer_turn` 數輪,REPL 代跑 `api_tidy.run`);不觸發交接類 |
| typesafe / openrouterjev | `typesafe_cli.py` | 同上(檔補進來時要加同一行,測試 O4 會查) | 不能 | — | 公開版**沒有** `typesafe_cli.py`,開不起來 |
| claudebot | 本機 Claude 對話 + `claudebot_hook.py` 的 Stop | 橋接代觸發:`bot_turn(gauge_sid=綁定的 Claude 對話)`,rc 2 續一輪 | 部分(續一輪要求交接;verify 沒過再提醒,封頂 3 次) | `claudebot_hook install` 裝的 Claude 設定 | 能(綁定對話的 gauge);量不到 ⇒ 每 20 輪精煉 |
| codexbot | `codexbot_bridge.py`(Codex Desktop App Server) | 橋接代觸發:每一輪完成後 `bot_turn(gauge_sid=桌面 sessionId)`,指示附在下一則送出的訊息 | 不能 | 不用裝 | 試著量(`codex_ctx_probe` 讀桌面對話的 rollout,**沒實測**);量不到 ⇒ **每 20 輪精煉** |
| grokbot / musebot / sand | `sand_bridge.py` | 橋接代觸發:每一則回真人後 `bot_turn`,指示放進 inbox 系統信 | 不能 | 不用裝 | **量不到** ⇒ **每 20 輪精煉**,不觸發 80 / 85 / 95 |

全部呼叫 `auto_distill_hook.py` 同一套判斷(`crossings` / `handoff_owed` / `handoff_done` / 已觸發紀錄),沒有第二套。

量不到用量(gauge 沒有 pct)的席一律**每 20 輪精煉一次**(維護者 10-15 22:4x):N 是 policy 表的 `unmeasured_tidy_every_turns`(經 `ctx_policy.unmeasured_every()` 讀,單一來源);計數與已觸發紀錄同一席同一對話一份(`state/auto-distill/unmeasured-<席>.json`,換對話從 0 算、同一輪不重複);API 席由 `outer_turn` 數、bot 由 `bot_turn` 數。量得到用量的席照上面的表,不受影響。

已刪掉的舊表(B,艦隊巡檢 `stages` / `actions`)與讀者現況:

| 舊鍵(舊值) | 現在 |
|---|---|
| `co_rule`(75)、`should_converge`(85)派工閘 | 讀表上 80「交接提醒」:80 起不派新工 |
| `must_handoff`(90)交接憑證 | 讀表上 85「要求交接」 |
| `should_converge`(85)交棒前置建議、`final_clear_advice_floor`(85) | 讀表上 85 |
| `prepare_compact`(80)增員閘 | 刪;`seat-gate` 不再自動判,回「由人決定」 |
| `scout_successor`(50)找接班、`co_rule`(75)完整總結偵測 | 刪;`handover_probe` 不再判 |
| `should_checkpoint`(60) | 刪(不再是一階) |
| `must_handoff`(90)輪替 check / handoff 預設門檻與地板 | 讀表上 85(維護者 20:4x);`--threshold` 只准比地板嚴 |
| `advisor_stop`(96)顧問期自動解除、`claude_final`(97) | 刪;顧問期只剩 manual 與 24h 天花板 |
| `captain_compact`(85) | 本來就沒讀者,刪 |

## English

| Context use | What happens | Program |
|---|---|---|
| 30% / 50% / 70% | Silent distill: after answering, run `aib memory tidy` (skipped while there is no new human message since the last tidy) | `auto_distill_hook.py prompt` (Claude inject) / `stop` (other CLIs: aib dm to itself) / `agy-pre` |
| 80% | One-line handoff reminder; the fleet dispatch gate stops sending new work to the seat | same; dispatch gate in `ctx_scheduler.py` |
| 85% | Distill + handoff required: inject "finish the three wrap-up steps (`aib memory tidy` → `aib summary` → `aib handoff` + verify) before anything else"; re-remind on every message while the handoff receipt shows verify not passed. **The Claude Stop hook blocks the turn** until verify items 1-8 pass (max 3 blocks per message, then released and marked `gate_gave_up`); a human message containing 「先做」 is not blocked | `prompt` + `review-gate` (Stop); non-Claude `stop` re-reminds at most every 10 min, 3 times per conversation; `fleet_patrol.py scan` lists unfinished handoffs (read-only) |
| 90% | Silent distill (or the handoff re-reminder if 85 is still not passed) | `auto_distill_hook.py` |
| 95% | Red line: stop all new work and hand off now | `auto_distill_hook.py` |

Until `src/persona-collect/` (human_filter) is added to the public repo, the 「先做」 exception does not take effect and tidy cells fire without the "new human message" check. Each cell fires once per conversation. The % in every message is read from the gauge, never self-reported; no gauge ⇒ no output and no block. Any error ⇒ exit 0 with no output.

Per-brand: every provider now has a trigger path (table above). Hooks are installed at install / `aib init` / `aib update`
time by `auto_distill_hook.py ensure-all` (only CLIs present on this computer; others skipped with a line; configs backed
up; `AIB_AUTO_DISTILL=0` turns it all off). API-key REPL seats are triggered by the REPL itself after each turn
(`outer_turn`); bridge bots by the bridge (`bot_turn`, full table when the bot's gauge is measurable). Not measurable today:
API-key seats, GrokBot / MuseBot / Sand (and CodexBot / ClaudeBot when their gauge is missing): distill every 20 turns
(`unmeasured_tidy_every_turns` in the policy file; one counter per seat and conversation), no handoff cells. After `/clear` the seat is found via
`$EZ2_CTX_DIR/by-seat/<code>.json`.
