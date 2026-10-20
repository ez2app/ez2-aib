# 對話面板 fixtures(Claude Code 對話紀錄 jsonl 樣本,去識別化)— @ee0bb 2026-10-20

來源:本席 2026-10-15 → 10-20 的真實 Claude Code 對話紀錄(`~/.claude/projects/<專案>/<session>.jsonl`),用腳本抽出並去識別化。
**給 @d07f9 做 C2(transcript 事件正規化)/ C3(pending 詢問)/ C7(通知)的測試資料;給 @abb89 統整。**

去識別化規則(全部機械替換,結構與欄位名不動):使用者名/路徑 → `<user>`;信箱 → `<email>`;IP → `<host-ip>`/`<ip>`;客戶網域 → `client-x.example`;席碼 → `<seat-xxxx>`;32/40–64 位 hex → `<hex32>`/`<hex-long>`;uuid → 另一組穩定假 uuid;字串超過 3000 字截斷並加註(真實紀錄**不截**)。

| 檔 | 記錄 | 看什麼 |
|---|---|---|
| 01-ask-user-question | assistant tool_use `AskUserQuestion` + 對應 user tool_result(**沒有 preview 的樣本**:本席 9 次提問都沒用 preview,更正 19:1x) | C3:詢問不只 y/n,是多題、多選、`multiSelect`;preview 欄位的形狀請看工具 schema(options[].preview 字串,單選限定);回答在 tool_result 的 `answers`(以**問題文字**為 key)與 `annotations`。C4 回答要帶 tool_use_id 冪等 |
| 02-pasted-content | user 訊息含 `<pasted_content id=…>…</pasted_content id=…>` | P2:貼上的長文是一個區塊,開合標籤帶同一個 id;面板別拆成多行 Enter |
| 03-hook-stop-feedback | user 訊息,文字以「Stop hook feedback:」開頭 | C2 建議 kind=hook;這是回合被 hook 擋下的原因(例 aib-now-gate),不顯示的話使用者只看到 AI「沒動」 |
| 04-hook-userpromptsubmit | attachment / assistant 內含「UserPromptSubmit hook success」 | 同上,hook 回饋夾在 system-reminder 裡 |
| 05-hook-sessionstart | user / attachment 含 SessionStart hook 輸出 | 新對話或 compact 後第一筆會有一大段 system-reminder(含 compact 摘要) |
| 07-bash-input | user 訊息 `<bash-input>…</bash-input>` + `<bash-stdout>` `<bash-stderr>` | **本人用 `!` 前綴直接跑的指令**;C1 要支援,C2 建議 kind=human_shell 並標「本人執行」(10-20 追「誰合了 PR」就靠這個) |
| 08-usage-limit-reset | user 訊息含「Your claude.ai usage limit has reset.」 | C7:額度恢復通知的原文 |
| 09-login-successful | user 訊息含 `<local-command-stdout>Login successful</local-command-stdout>` + `<command-name>/login</command-name>` | C7:登入成功;`<local-command-caveat>` 說明它是本機指令、不是給 AI 的請求 |
| 10-system-records | type=system 三筆 | 系統事件長相(含 compact 邊界等) |
| 11-attachment-records | type=attachment 三筆 | 附件型記錄(hook 輸出、工具說明等)多走這型 |
| 12-queue-operation | type=queue-operation | 使用者在 AI 工作中送出訊息的排隊事件(對應 P6「排隊中 n 則」) |
| 13-assistant-tool-call-and-result | assistant tool_use(Bash)+ user tool_result | C2 kind=tool_call / tool_result 的基本對;tool_result 常含主機路徑與設定檔內容 → **預設摺疊、不進 room 快照 / 手機** |

缺的:
- AskUserQuestion 帶 `preview` 的真實樣本:本對話沒有(我提問時沒用過 preview)。
- `<task-notification>`:本席對話沒有,**@abb89 10-20 19:12 從他的紀錄補了 `06-task-notification.jsonl`(2 筆)**。它是背景工作(子代理 / 背景指令 / Workflow)完成時系統塞進對話的 user 訊息。
- 其他家 CLI(Codex / Gemini)的紀錄格式:本席只有 Claude Code。

真實紀錄的其他特徵(做 C2 時要留意):
- 一行一個 JSON;`type` 有 user / assistant / system / attachment / queue-operation / mode / permission-mode / last-prompt / file-history-snapshot / api-request / api-request-blob 等,**對話面板只需要 user / assistant / system / attachment / queue-operation**,其他是內部狀態,量很大(本對話 4271 行裡 user+assistant 只 1297 行)。
- `message.content` 可能是字串,也可能是區塊陣列(text / tool_use / tool_result / thinking);`parentUuid` 串起順序;`isSidechain=true` 是子代理的對話,不是主線。
- 使用者在 AI 工作中送的訊息,會以 system-reminder 形式夾在下一個 tool_result 裡(「The user sent a new message while you were working」),不是獨立 user 記錄。

產生腳本:本席 scratchpad(非倉內);要重抽或改規則找 @ee0bb。

- 06-task-notification.jsonl:@abb89 10-20 19:1x 從本席補的 2 筆 <task-notification>(背景指令完成通知;type=user、isMeta 無、內容在 content 字串);路徑 / uuid 已去識別化。

| 14-ask-preview(@d07f9 10-20 補) | assistant tool_use `AskUserQuestion`(options[].preview 字串,單選)+ 對應 user tool_result | C3:帶 preview 的選項(頁面放在右側等寬框);answers 以問題文字為 key。從 @d07f9 席 jsonl 抽,去識別化同上 |
