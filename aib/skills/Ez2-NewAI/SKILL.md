---
name: Ez2-NewAI
description: >-
  When the user wants to add a new API-key AI seat to Ez2 AIB (OpenAI-compat
  or Anthropic native, e.g. OpenRouter / Teamo / self-hosted gateway). Do not
  use for OAuth subscription CLIs (claude / codex / grok).
---

# Ez2-NewAI — 新建 API-key AI 席

正本 checklist：`docs/SOP-add-api-key-or-bot-provider-0920.md`。
人怎麼填 key／開席：`docs/SOP-api-key-seat-how-to-0920.md`。
本 skill 是執行配方；細節以 SOP 為準。

## 先問清楚（缺一不可）

向使用者確認（能從對話推就不要再問）：

1. **引擎 id**：全小寫英數（`[a-z0-9]+`），例 `openrouter`
2. **顯示名**／**三字 letter**（例 OpenRouter／ORR）
3. **協議**：OpenAI-compat（`/v1/chat/completions`）或 Anthropic native（`/v1/messages`）
4. **預設 base URL** 與 **預設 model id**（OpenRouter 部分 id 以 `~` 開頭；開席鏈勿讓 shell 展開 `~`）
5. **環境變數名**：例 `OPENROUTER_API_KEY`（可選 BASE／Referer／Title）
6. **範本席**：通常抄 `teamo_cli.py`（OpenAI）或 `teamoclaude_cli.py`（Anthropic）

**禁止**在聊天裡貼／回顯真 API key。只指引寫入 `~/.ez2-aib/secrets/api-key-<id>.env`。

## 做法（worktree → 本機 main）

正本樹預設只讀；改碼走 worktree，測綠後 ff-merge 本機 main。

1. **金鑰櫃**：`src/bootstrap/aib_secrets.py` 登記 `api-key-<id>.env` 與變數列；必要時建空檔骨架（0600）。
2. **CLI**：`src/cockpit/<id>_cli.py`
   - 模組載入即 `repl_readline.enable_repl_readline()`（守門測試必過）
   - import `aib_dm_tool`：system 帶 `DM_SYSTEM_SNIPPET`、啟動印 `dm_boot_banner()`、人類 `/dm`、輸出過 `enforce_dm_truth`
   - OpenAI-compat：Bearer + chat completions；Anthropic：`x-api-key` + messages
   - 設合理 `max_tokens` 預設（避免供應商用超大 max 導致 402 且席內像沒回應）；錯誤打到**席窗 stdout**
3. **掛載清單**（漏一項席選單就看不到）：
   - `src/cockpit/providers.json`
   - `src/cockpit/provider-launch.sh`（argv 對齊既有 teamo）
   - `src/cockpit/aib` 的 `PROVIDERS` 與 `AIB_SESSION_ID_PROVIDERS`
   - `composer_dialect`／`render_lib` letter+色／`list_captains`／`pane-right-ai`／`captains-ctl`／`bind_queue`／`routing/table.json`／`title_beat`／`aib_doctor`
4. **測試**：`tests/test-<id>-cli.py`；把 `<id>_cli.py` 加進 `tests/test-repl-readline-required.py`；更新 launch-direct／routing 白名單若有。
5. **SOP**：一頁 `docs/SOP-<id>-api-key-seat-….md`；更新 how-to 與 add-provider 表列。
6. **驗收**：缺 key 具名失敗；`aib <id> dgrs root` 開席；席內 `/models`、對話有回；`/dm` 可用。
7. **合入**：ff-merge 本機 main。發版／推 origin 另遵現行 release 流程。

## 常見坑

- macOS `/bin/bash` 3.2、BSD sed
- 模型 id 以 `~` 開頭被 zsh／開席正則踩到
- 未設 `max_tokens` → 402，錯誤只在 stderr 會像沒回應

## 不要做

- 不要做 OAuth 訂閱席（claude／codex／grok…）
- 不要在聊天收集或複述 API key
- 不要略過 `aib_dm`／readline 守門
- 不要直接在正本樹髒改；走 worktree

## 第三協議：TypeSafe System One

`typesafe` **不是** OpenAI-compat，也 **不是** Anthropic Messages。
用 `POST /v1/an internal service`（`noul` / `choice` / `score`）。開席 SOP：`docs/SOP-typesafe-api-key-seat-0920.md`。
本 skill 的 OpenAI／Anthropic checklist **不夠** 覆蓋它；照該 SOP 掛線。
