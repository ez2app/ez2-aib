# tmux 全域 hook 下標保留表

tmux 的 hook 是陣列(`set-hook -g '<名>[<下標>]'`)。全域(`-g`)那一份是**同一個 tmux server 上所有 AIB 艙共用**的,
下標撞了就會互蓋。下面是 AIB 自己用的全域下標;新功能要用全域 hook 先在這裡登記,別處不准用表上的號碼
(`aib/tests/test-pane-visible-043dd-1107.py` 的 V9 靜態掃描擋 4770)。

| 下標 | hook | 誰用 | 內容 | 規則 |
|---|---|---|---|---|
| 47 | `session-window-changed` | `src/cockpit/pane-right-tabs.sh` | 右欄頁籤 | (既有) |
| 4770 | `session-window-changed`、`client-attached`、`client-detached`、`client-session-changed` | `src/cockpit/pane-tick.sh`(`pane_vis_hook`) | 一個全域廣播:對 `<狀態根>/state/pane-vis/reg/*.reg` 每個登記 append 1 個位元組;內含標記 `aib-pane-vis-4770`,同一使用者各艙內容相同 | 只在四種那一格都「空或含標記」時寫,檢查與寫入在同一條 tmux 指令串(不會被插隊);有別的內容就不寫、該艙退回輪詢(`hook_foreign`) |

session / window 層(`-t`、`-w`)的 hook 不在這張表:它們只屬於自己那個 session / 視窗,不會跨艙互蓋。

## 已知限制(4770)

- 別的程式在我們寫入**之後**用同一格,會蓋掉我們的廣播。後果:各艙收不到喚醒,退回退避輪詢,最慢 10 秒才重畫。不會畫錯。下一次「看得見→看不見」時讀到不是我們的內容,就記 `hook_foreign`,不搶回來。
- tmux 讀不了陣列格式(探針 `#{command-alias[0]}` 是空的)⇒ 一律不寫,退回輪詢(`no_hook_fmt`)。
- 計數在各艙 `cockpit/.panevis.stat.<原因>`:`hook_foreign`、`no_hook_fmt`、`no_hook_reg`、`no_hook_path`、`no_hook_tmux`,以及每次退回輪詢的 `poll`。
