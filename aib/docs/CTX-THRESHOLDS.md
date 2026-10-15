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
- 安裝:開 Claude 席時 `provider-launch.sh` 跑 `auto_distill_hook.py ensure`,把 UserPromptSubmit `prompt` 與 Stop `review-gate`
  寫進 `~/.claude/settings.json`;路徑經 `canonical_tree` 算正本樹(公開倉 `<倉>/aib/src/...`、pipx 本檔自己)。

已刪掉的舊表(B,艦隊巡檢 `stages` / `actions`)與讀者現況:

| 舊鍵(舊值) | 現在 |
|---|---|
| `co_rule`(75)、`should_converge`(85)派工閘 | 讀表上 80「交接提醒」:80 起不派新工 |
| `must_handoff`(90)交接憑證 | 讀表上 85「要求交接」 |
| `should_converge`(85)交棒前置建議、`final_clear_advice_floor`(85) | 讀表上 85 |
| `prepare_compact`(80)增員閘 | 刪;`seat-gate` 不再自動判,回「由人決定」 |
| `scout_successor`(50)找接班、`co_rule`(75)完整總結偵測 | 刪;`handover_probe` 不再判 |
| `should_checkpoint`(60) | 刪(不再是一階) |
| `must_handoff`(90)輪替 check / handoff 預設門檻與地板 | 刪;`rotation.py check/handoff` 要明給 `--threshold` |
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

Each cell fires once per conversation. The % in every message is read from the gauge, never self-reported; no gauge ⇒ no output and no block. Any error ⇒ exit 0 with no output.
