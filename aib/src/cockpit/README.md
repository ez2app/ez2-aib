# 指揮艙(E2.1 遷入正本)

demo 正本備份=`~/ez2-cockpit-demo/`(活體仍在跑,遷的是副本)。
外部路徑一律走 `cockpit-env.sh` 的環境變數;共用純函數(CJK 對齊/大綱解析/
面板狀態機)在 `cockpit_lib.py`,由 `tests/test-cockpit-smoke.py` 看守。

## 遷移對照(demo -> 正本)

| demo(~/ez2-cockpit-demo/) | 正本(src/cockpit/) | 遷移時修正 |
|---|---|---|
| acb-131-demo.sh | cockpit-layout.sh | 相容入口;委派眼驗版組裝器 |
| acb-131-v2-upgrade.sh | cockpit-upgrade.sh | 相容入口;已移除破壞性重建 |
| acb-left-fleet.sh | pane-left-fleet.sh | ~/.acb→$EZ2_ACB_HOME |
| acb-left-persona.sh | pane-left-persona.sh | trunc→cockpit_lib;runtime 路徑 env |
| acb-left-brain.sh | pane-left-brain.sh | registry/vault/深度檔 env |
| acb-right-ai.sh | pane-right-ai.sh | title-override/ctx/tasks glob env |
| acb-bottom-fleet.sh | pane-bottom-fleet.sh | board 路徑→$EZ2_FLEET_BOARD |
| acb-bottom-tabs.sh | pane-bottom-tabs.sh | 舊大綱硬路徑→$EZ2_OUTLINE;解析→lib |
| bottom-tab-toggle.sh | ctl-bottom-tab.sh | 狀態機→lib;狀態檔進 state dir |
| brain-depth-toggle.sh | ctl-brain-depth.sh | 同上 |
| outline-progress.sh | outline-progress.sh | 解析→lib;路徑 env |
| acb-sandbox/outline-board.sh | warroom-board.sh | CJK/解析→lib;路徑 env |
| acb-sandbox/depth-ctl.sh | ctl-view-depth.sh | 狀態機→lib;狀態檔進 state dir |
| acb-sandbox/outline-to-tasks.py | outline-to-tasks.py | 解析→lib;路徑 env |

盤點註記:demo 目錄可執行檔共 16 件;`ez2-conv-distill.py` 是 Stop hook
正本(~/.claude/)的備份,不屬指揮艙,不遷。實遷 15 支 + 新增
`cockpit-env.sh` / `cockpit_lib.py` 兩支基礎件。

## aib CLI(E2.9)

```sh
aib <provider> <dgrs|safe> <role> [new] [--dry-run]
```

provider=claude|codex|agy|copilot；dgrs 僅關閉本次 provider 的 approval/sandbox，
不增加 ACB、RDL、merge、deploy 或 production 權限；safe 保留 provider 一般保護。
role 是本 session 職務名(小寫英數與連字號)。第四參數 `new` 每次原子建立
獨立 outer session、inner hub socket 與 runtime state；`--new` 是相容別名。
`--help` 雙語(字典正本 `i18n/`,
本檔不硬寫任何人類語句;載入器 `i18n_lib.py` 照 loader-spec §3)。

### 兩個 socket:座艙四格與 AI 席位分住兩邊

| socket | session | 住誰 | 怎麼列 |
|---|---|---|---|
| 預設 `-L default` | `aib-<instance>` 窗 0 `cockpit` | 座艙四格 left/hub/right/bottom | 指令一 |
| `-L aihub-<instance>` | `aihub` | AI 席位,一席一窗 | 指令二 |

```sh
tmux -L default list-panes -t aib-<instance>:0 -F '#{pane_id} #{@aib_role}'
tmux -L aihub-<instance> list-windows
```

`:0` 不可省:`-t <session>` 跟著**當前 window** 走,人在戰情室窗時同一條只回一格。
失效形狀:在席位窗裡跑裸 `tmux list-panes` 看不到四格——裸 `tmux` 讀殼裡 `TMUX`
指的席位 socket,那不是壞掉。

行為:`cockpit-full.sh --ensure` 是唯一組裝入口。主畫面是 the maintainer 眼驗的四個
physical pane：左側內容 tabs、中間真 ai-hub、右側內容 tabs、跨全寬底部 tabs；
另有一個戰情室 window。組裝器不執行 `kill-server` 或 `kill-session`，重跑只會
重套樣式、hooks 與比例，不會殺掉既有 provider 對話。
右上 AI 清單沿用已拍板的引擎品牌正本：Claude=`CL`/colour 173，
Codex=`CD`/colour 252，避免與 Copilot=`CP` 混淆。

字型或視窗縮放時，`cockpit-resize.sh` 重算 pane 格數；各側欄 renderer 再經
`pane-fit.py` 依實際高寬截斷內容，固定保留 pane 內容最上方的眼驗 tab，避免
長大腦樹或多 AI 清單觸發 terminal scroll 把 tab 捲走。

`identity-env.sh` 是新艙身份隔離清單正本。`new` 在建立 tmux 前先清除舊
persona/session/lane；每個組裝 pane 再以 `env -u` 清一次；outer 與 hub
session 另寫 tmux removal marker，防止後開 window 從共用 server global
environment 拿回舊身份。舊 instance/session/socket/state/task 變數也用同一機制
先移除，再在 session 寫入本艙的正確值，所以後開 shell 不會指回舊艙。
`provider-launch.sh` 保留第四道 carrier scrub。

Codex/Claude/AGY/Copilot 視窗委派本機正式 ACB native-session carrier。以 Codex
為例，`aib codex dgrs pm new` 最終由 ACB 執行 `codex`、注入可信 onboarding，
並在新對話內完成真人 BIND 與 registry authority；AIB 不自行偽造 session UUID、
不複製權限旗標，也不把 placeholder 當已接 adapter。

`gemini` 不在 native selector 集合：目前正式 ACB 沒有 Gemini session driver，
因此 launcher 會在建艙前 fail-closed，而不是開一個必定立即退出的假綠視窗。

`--dry-run`(或 `AIB_DRY_RUN=1`)只印指令序列,**全程不碰 tmux** ——
本機有活著的指揮艙,演練不得打擾它。`AIB_FAKE_NEW_INSTANCE` 只供測試固定
演練名稱；dry-run 不建立 instance 目錄。

眼驗版逐項契約與反向判準見 `docs/reports/E2.17-eye-verified-ui-contract.md`。

## 四區資料化主題(AIB-THEME-20260809)

預設主題正本是 `themes/aib-default.json`，包含左 Rose Gold 95、中 Space Gray
236、右 Pacific Blue 24、底 Gold 130 的常態色，以及同色系 focus 亮階與十個
色彩家族。人類可見名稱走 `i18n/` 四語；更換主題只改資料檔或設定
`EZ2_AIB_THEME_FILE`，不得在 pane 腳本另藏 colour 對照。

`theme.py` 在任何 tmux mutation 前檢查 JSON schema、owner、regular
non-symlink 與 group/world-write；`theme-ctl.sh` 把 normal/focused 分別寫成每個
pane 自己的 `window-style`/`window-active-style`，並安裝成對
`pane-focus-in/out` 和 `after-select-pane` 對帳。重跑 `cockpit-full.sh --ensure`
會重套主題，但不重建 pane 或 provider session。

Gold 底欄另由 `cockpit_lib.panel_contrast_sgr` 按 xterm RGB 亮度自動選黑粗體；
其他三個暗底選亮灰。ANSI reset 後會重新套用基準色，避免局部 DIM/品牌色令後續
文字回到難讀灰色。字型家族與大小不屬本工作包；它們只能觸發既有 resize，不能
改變主題黃金值。

## 渲染與黃金幀(E2.11)

`render/render_lib.py` 是版面寬度的唯一實作:跳脫碼零寬、東亞寬字兩格、
組合附加符號零格、截斷後補回 SGR 重置碼(不補的話顏色會溢出染到後面整行,
這是 demo 三支 pane 各自抄的 trunc 都沒處理的洞)。三個面板的渲染是純函數
(`render_persona` / `render_brain` / `render_ai_list`),資料由呼叫端傳入。

黃金幀在 `tests/golden/`:每個面板一份 `*.fixture.json`(固定假資料)與一份
`*.frame.txt`(80x24 逐字凍結)。版面退化不會讓任何斷言失敗、只會讓畫面變醜,
而醜是沒人會寫測試的那種 bug——所以整張畫面逐位元組比對。

改版面時更新黃金幀:`python3 tests/test-golden-frames.py --update`(刻意要人
手動下指令並說明理由;自動覆蓋等於沒有黃金幀)。渲染器不得讀時鐘/環境/檔案,
測試有機械檢查看守這條——否則黃金幀無法重現。

## 說明浮層(E2.12)

`help/help-overlay.sh [--print|--install] [--dry-run]`。快鍵總表的文字全部來自
`i18n/` 的 `aib.overlay.*` 與 `aib.keys.*`(每個快鍵拆 `.combo` 與 `.desc` 兩條,
對齊由 `render_lib` 算,不把版面塞進翻譯字串)。版面沿用 E2.11 的寬度實作,
不另造一套。

兩個決定,理由寫在腳本裡免得被改回去:

1. 綁 `<修飾鍵>+?` 與 prefix `?`,**不綁 root 表的裸 `?`**。root 表是全域的,
   中欄跑著真的 AI 對話——在那裡打一個問號是每天都會發生的事,綁下去等於
   使用者從此打不出問號。測試有一條守衛專擋這個回頭路。
2. 修飾鍵的**顯示名**隨平台變(macOS=Option、其他=Alt,`uname -s` 判定,
   `EZ2_MODIFIER_LABEL` 可覆寫),但 tmux 的**綁定名**永遠是 `M-`。顯示與綁定
   分開,才不會有人把 `Option-?` 寫進 bind-key 然後整組快鍵靜默失效。

## 四區焦點導航(AIB-UI-NAV-v1)

`nav/region-nav-ctl.sh --install` 安裝兩個焦點感知鍵：`Tab` 切目前區域內的
既有 tab，`Shift+Tab` 按左 -> 中 -> 右 -> 下 -> 左切換 outer 區域。中央的 tab
是 inner ai-hub 既有 window，不會新建或 respawn provider。

tmux root key table 是 server-global，所以 binding 同時要求 cockpit window 的
`@aib_cockpit=1` 與 allowlist 內的 `@aib_role`；不符合時原樣送出 Tab/BTab。
區域定址逐次從 role 找唯一 pane ID，不使用 pane index。焦點底色由 pane 級主題
兩態控制，邊框只設在 cockpit window；普通 Tab 不會改 outer pane 幾何。

## 滑鼠互動(E2.13)

眼驗版組裝器把隔離的 inner hub 與 outer cockpit mouse 都設為 `on`，讓中央
status tab 能收到真實 click。nested provider 會要求 mouse reporting，因此 inner
只把 `MouseDrag1Pane` 固定為 `copy-mode -M`：拖曳永遠選字，普通 click 與滾輪仍按
tmux 預設路由。outer mouse 保留左、右、下 copy-mode 與下方邊界 resize。這些設定
只套在 AIB 自有的 inner server 與 cockpit session。

`mouse/mouse-ctl.sh --install` 綁 tmux 滑鼠鍵;`--click <role> <x> <y>` 處理一次
左鍵點擊,`--context <surface> ...` 處理右鍵情境選單。左鍵可點的兩件事:
底部分頁列(點標籤切分頁)、AI 列表(點某個 AI 或它的子 agent 跳中欄)。

**AIB 左鍵 hit-test 只在標記面板的 `MouseUp1Pane` 接管,其餘一律
`send-keys -M` 原樣放行。** 面板由 pane 腳本自己標記 `@aib_pane`(tabs / ai-list)。
例外(與 `mouse/mouse-ctl.sh` 現況一致,10-07 更正):
- `MouseDown1Pane` 點右欄(ai-list)只記捲軸並把焦點拉回中欄,不 select 右欄
  (焦點留在右欄的話,接著打字會印在捲軸上;09-24 起)。
- `MouseDrag1Pane`:左/右/下格由**外層**進 `copy-mode -M`。**中欄(10-08 the maintainer 裁)轉給內層 tmux**
  (`send-keys -M -t =`),由上面 inner 的 `MouseDrag1Pane copy-mode -M` 接:先用滾輪把中欄捲到對話歷史,
  再拖就選得到;判準看格子(`@aib_role=hub`)不看 `mouse_any_flag`(內層 client 的旗標恆開)。
  外層這格已在 copy-mode 時照舊外層選。程式若畫在全螢幕畫面(alternate screen),它自己的捲動歷史不在 tmux 裡,選不到。
下方邊界 resize 仍由 tmux 原狀態機處理。

右鍵在 Pane、Border、Status 三種表面共用一個入口。tmux 內建選單本來綁在
`MouseDown3`;對 AIB 指揮艙,Down 只抑制內建選單,`MouseUp3` 才顯示 AIB
情境選單。安裝器會先保存各事件原本的 server-global binding;同一 tmux
server 上非 AIB session 仍執行原 binding(包含 tmux 內建 `display-menu`),重裝也不會
把 AIB wrapper 重複包裝。保存與重綁由 tmux server lock 串行化,同時冷啟動兩艙也
不會互相把對方的 wrapper 當成原 binding。

在 AI 列表的精確列上按右鍵時,詳情與畫面共用當次渲染寫出的 rowmap,
選單提供 Details、Open conversation 與 Focus pane。Details popup 關閉時會在有界時間
內排空同一次滑鼠的 Down/Up 完整序列,避免關閉的 Up 漏到底下面板。若
rowmap 已過期、跨 instance,或 inner hub window 已消失,click 會靜默降級為
passthrough(rc=0),不把 tmux 錯誤刷進 AI pane。

標記面板內若沒點到互動目標,結果是 passthrough=不動作:判不出來就不動,
滑鼠是使用者的手,寧可沒反應也不要跳去他沒要去的地方。

**畫面與落點同源**:分頁列由 `render_tab_bar` 同時產出文字與可點區段,
AI 列表由 `render_ai_list_with_map` 同時產出畫面與行對映;兩者在渲染當下把落點寫進
`$EZ2_COCKPIT_STATE_DIR/{tab-spans,ai-rowmap}.json`,點擊判定只讀那份檔案。
分開算的話,標籤一改寬度滑鼠就點到隔壁那格,而且不會有任何測試失敗。

## 執行期狀態

`bottom-tab` / `brain-depth` / `view-depth` 三個狀態檔寫在
`$EZ2_COCKPIT_STATE_DIR`(預設 `~/.ez2-aib/cockpit`),不再寫腳本目錄,
repo 保持乾淨。

## 已知過渡依賴(EM 絞殺者遷移時逐一拔除)

- `$EZ2_ACB_HOME`(sessions/inbox/logs/role-policy):ACB 現行狀態,唯讀。
- `$EZ2_FLEET_BOARD`:share/claude-ui 的艦隊看板。
- `$EZ2_CODEX_SESSIONS_ROOT` 預設為 `~/.codex/sessions`；子 AI 只接受父、子
  rollout 雙向驗證的 Codex child thread UUID。Claude temporary tasks 混有背景
  shell 命令，不是身份來源。
- 底部 tab 2 的「最近事件」讀 fleet-sync.jsonl;E1.2 收集器上線後改讀
  AIB 帳本投影(src/projections)。


## aib idle(艙級閒置偵測與收艙,0.34.0-alpha.1)

(已實作、已測)

```
python3 src/cockpit/idle_reaper.py [scan] [--json]        盤點
python3 src/cockpit/idle_reaper.py close <target> [--yes] [--force] [--dry-run]
python3 src/cockpit/idle_reaper.py reap [--dry-run]       依安全名單自動收
python3 src/cockpit/idle_reaper.py watch [--interval 300] [--dry-run] [--max-rounds N]
python3 src/cockpit/idle_reaper.py policy [show|init [--force]]
rc:0 正常 / 2 用法或名單缺 / 3 目標拒收(有人接著)
```

目標五類 × 判決:

| target | 判決 | 意思 |
|---|---|---|
| `inst:<N>`(main=acb131/aihub;N=aib-N/aihub-N) | `active` | 有人接著(外殼有 client、或內艙 client 的 tty 不屬外殼任何 pane=有人直接 attach)或有席在工作 |
| | `unknown` | detach 但至少一席量不到活動訊號 → **fail-closed 不收** |
| | `detached_idle` | 無人 + 每一席 idle ≥ `idle_threshold_s` + 艙齡 ≥ `min_age_s` |
| | `dead_inner` | 內艙不存在或內艙所有 pane 已死,只剩外殼四支腳本在燒 |
| `srv:<tmux-dir>`(非標準 socket 目錄,同 TMUX_TMPDIR 一組) | `active` / `young` / `test_residue` | 演練/測試 server |
| `orphan:<pid>` | `orphan_server` | tmux server 在、socket 沒了 |
| `script:<pid>` | `orphan_script` | 輪詢腳本 ppid=1 且不在任何 pane 子樹 |
| `cli:<pid>` | `stray_active` / `stray_idle` | 艙外 claude/codex/agy CLI;idle 看 tty atime;**永不自動** |

判準與席位級 [X] **共用 `activity_probe.seat_for_window` / `activity_age`**(不建第二真相源)。

安全名單 `~/.ez2-aib/idle-policy.json`(`EZ2_AIB_IDLE_POLICY` 可覆寫;`policy init` 寫預設):

```json
{"schema":"aib-idle-policy/v1",
 "auto_close":{"test_residue":true,"orphan_server":true,"orphan_script":true,
               "dead_inner":true,"detached_idle":false},
 "idle_threshold_s":3600,"min_age_s":1800,
 "protect":{"instances":["main"],"sessions":[],"roles":[]}}
```

名單**只會縮小不會放大**,四條結構紅線名單壓不破:
1. `unknown` 永不自動(量不到 ≠ 閒置)
2. 有人接著永不自動(人工 `close` 要 `--force`)
3. `cli:*` 永不自動(人的終端,可能有未落地上下文)
4. 艙齡 <60s 永不自動(`AUTO_MIN_AGE_FLOOR_S`)——真機 e2e 時我用 `min_age_s:0` 測自己的治具,
   同一輪把**別的 session 正在跑的 `test-eye-ui-launch` 治具 server 也收了**,補的硬地板

收艙順序:內艙 `kill-server` → 外殼 `kill-session` → 殘留 PID 逐個 `SIGTERM` → 重驗存活。
只碰自己盤點到的 PID、一律正數、`os.kill(-…)`/`killpg` 在源碼裡是零(測試釘死,含 code_only 掃描)。
留痕 `~/.ez2-aib/logs/idle-reaper.jsonl`:收與略過都寫一行,略過附 `reason`
(`class_disabled:<v>` / `human_attached` / `verdict:unknown` / `protected_*` / `cli_never_auto` / `too_young_for_auto`)。

(整合紀錄與邊界:docs/PLAN-idle-reaper-0817.md)

### 常駐(the maintainer 08-18 裁:lobby 窗常駐 `aib idle watch`)

`cockpit-full --ensure` 在 hub 裡保證恰有一個 `@aib_role=sys.watch` 的 `watch` 窗跑
`idle_reaper.py watch`(interval 由 `EZ2_AIB_IDLE_WATCH_INTERVAL` 帶入,預設 300s、下限 30s;
pane 死了下次 ensure respawn 同一個 window id;不標 `@aib_lobby`,故首席開席時的 lobby 清除迴圈
不碰它;冪等不疊窗、不 kill 任何窗)。`sys.*` 是系統窗保留前綴(aib ROLE_RE 禁 `.`,人開的席
不可能撞名):右欄不列為席(否則落 ❓+[X]=一鍵關監看器)、艦長列不算候選、idle_reaper 靠
pane 子樹 argv 自辨不算席。沒有安全名單時 watch 只掃不收並把原因印在畫面(常駐不因名單缺而死;
名單每輪重讀,`policy init` 後下一輪生效);多艙多 watch 以 `~/.ez2-aib/idle-reaper.lock`
全機單飛,同一輪只有一個真的收,其餘只掃。測試:`tests/test-idle-watch-lobby.py`(真 tmux)。

## 引擎徽章 icon 三態(R6,08-30)

`render_lib.render_engine_badge(provider, fallback, truecolor, mode)`:`letters`(3 格,**函式預設**;落檔與任何被引用的輸出鎖此態)/ `icon`(1 格)/ `both`(4 格)。mode 由採集端 `render/engine_badge_mode.py:resolve_badge_mode` 判後注入(`EZ2_AIB_ENGINE_BADGE` 預設 both;非即時 TTY ⇒ letters;即時 TTY 由 `pane-right-ai.sh` 的 `[ -t 1 ]` 注入 `EZ2_AIB_BADGE_TTY`,採集端 python 的 stdout 被重導到檔案、自己 `isatty()` 恆 False)。渲染器不讀環境。
字碼:claude U+EC82 / codex U+EC81 / copilot U+EC1E 是官方標;agy U+EBAE(cod-magnet)與 grok U+F0B1F(md-alpha_x_box)是**語意選字**,不是官方標。
★ **已知未做(R6-legend)**:字型缺字/缺檔退回 letters 的理由**目前只到 stderr(`$_ai_out.err`)**,不是人在看的面;右欄圖例未加。再開條件=圖例 i18n 四語與寬度先定。在那之前「退回並說出來」那一格只滿足了一半。

## aib restart(艙的清單與恢復,含停電/重開機;10-09)

`aib restart` 列兩區:**在線的艙**、**上次開機時還在、現在斷掉的艙**;每艙上方 `[編號] ▶ 恢復:…`,艙間分隔線。
在 AIB 艙裡會跳 tmux 選單(點一艙就在彈出視窗跑恢復),不在艙裡(剛開機)就問編號。實作 `cabin_restart.py`。

| 指令 | 做什麼 |
|---|---|
| `aib restart` / `--no-menu` / `--json` | 列表(只查不動) |
| `aib restart --restore <艙碼>` | 在線 ⇒ 開新 Terminal 視窗 `tmux attach -d`(有人在看要 `--force` 並再確認);斷掉 ⇒ 先列計畫、按 y,原艙號開回、每席 `AIB_REVIVE_SESSION_UUID` 同碼復活 |
| `aib restart --restore-all` | 停電/重開機後:上次開機時的艙全部恢復,一艙對一艙,問一次 |
| `aib restart snapshot` | 手動補一份快照 |

- **快照**:watch 每輪寫 `<狀態根>/state/cabin-snapshots/boot-<開機時間>.json`(按開機分檔,重開機後新艙不會蓋掉停電前那份;留 10 次;`EZ2_AIB_RESTART_SNAPSHOT=0` 關)。超過 15 分鐘沒更新,列表最上面警告。
- **每席走哪條**:對話檔與工作資料夾都在 ⇒ 同碼復活;缺一樣 ⇒ 開新席並用 `bind_one.py` 移植人格與大腦;bot 席帶回 `agent_id` 綁定;codex 復活後直接 `seat-detect` 補席碼。
- **中途斷掉可重跑**:已經回來的席標 ✅ 跳過;只回來一部分的艙列成 🧩,只補缺的席。
- **重開機後 pid 被重用**:`seat_liveness` 把「開席早於這次開機」的席當成已死,否則同碼復活會被擋。
- 窗名(root-claude / root2-claude)可能對調;席碼、對話、人格不變。

## aib cli(五家 AI CLI 版本、開席倒數更新;10-09)

開席動畫每家 logo 名字下三行「本機 x / 最新 y / 更新日 月-日」(更新日 = 這台電腦最後一次安裝/更新那一家的日子)(↑ 有新版、✓ 已最新;找不到最新版就寫「找不到」);動畫播完、接入指揮艙前,有新版就倒數 30 秒,
時間到自動更新(`s` 跳過、Enter 馬上更新)。只在真人終端開席時出現(AI 代開 `AIB_QUIET` 不查不擋);`AIB_CLI_UPDATE=0` 整段關。實作 `cli_versions.py`。

| 指令 | 做什麼 |
|---|---|
| `aib cli` | 看 claude/codex/grok/copilot/agy/gemini 本機、最新、最後更新(這台電腦最後一次安裝/更新的時間)、上一版 |
| `aib cli update [家…]` | 現在更新(不倒數);agy 跑自己的 `agy update`(已是最新就不動) |
| `aib cli rollback <家> [--to 版本]` | 退回上一次更新前的版本;退掉的那一版開席不再催 |

- 怎麼查/怎麼更新逐家實查:claude 官方安裝程式(`claude update` / `claude install <版>`)、codex/copilot/gemini npm 全域、grok `grok update --check`、agy 沒有只查不裝。
- 最新版快取 1 小時(`state/cli-versions.json`),沒網路 10 分鐘不重試;更新鎖 `state/cli-update.lock`(同時只准一個視窗更新)。
- 更新後連 `--version` 都跑不起來 ⇒ 自動裝回原版。gemini 只在 `aib cli`,不在開席五家。
