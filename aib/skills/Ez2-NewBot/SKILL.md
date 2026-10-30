---
name: Ez2-NewBot
description: >-
  When the user wants to mount a new *bot bridge seat* in Ez2 AIB — an AI that
  lives in someone else's app (Grok / Claude desktop / Codex app / Meta Muse)
  and talks to AIB through a file mailbox, not an API key. Triggers: 掛一個新 bot／
  新增 bot 席／接一個桌面 AI／xxxbot／bot 橋／mount a bot／add a bridge seat.
  Not for API-key seats (that is Ez2-NewAI) and not for OAuth subscription CLIs
  (claude / codex / grok — those are native providers, already mounted).
  Same folder works for Claude, Codex, Grok, and Antigravity.
---

# Ez2-NewBot — 掛一家新的 *bot 橋接席

**第一句:掛一家 bot 不是「加一列登記簿」,是改 30 幾個散處;舊的對帳閘只守其中 8 處,bot 專用那十幾處要靠新的 `test-bot-provider-sites-987f7.py`。
漏掉的那些不會報錯 —— 席開得起來、也能收信,只是右欄把它畫成「未確認 · 缺 引擎」。**

正本 SOP:`docs/SOP-add-api-key-or-bot-provider-0920.md`(DM 接線的細節以它為準)。
本 skill 補它第 3 節只有 5 條、沒有列出「到底要改哪些檔」的缺口。
依據:2026-10-05 掛 musebot 的實測(交接包 20 檔 + 6 新檔;複核先抓到 7 個缺陷,再用 `git grep claudebot` 逐檔對,又找出 13 處漏改)。

## 0. 先分清楚是哪一種

| 類型 | 它的 AI 在哪 | 怎麼說話 | 用哪個 skill |
|---|---|---|---|
| 原生席 | 本機 CLI(claude/codex/grok/agy/copilot) | 直接跑 | 已掛好,不用做 |
| API-key 席 | 對方的 API | 金鑰 + HTTP | **Ez2-NewAI** |
| ***bot 橋** | **別人的 app 裡(桌面/手機)** | **檔案信箱 + 排程或 hook** | **本 skill** |

bot 橋的特徵:這台只開一個視窗和一個 `~/.ez2-aib/bridge/<席 uuid>/` 信箱,
真正的 AI 在別的 app;它靠 **拉**(排程/cron)或 **hook** 來收信、回信。
現有四家可抄:`sand`(Cursor)、`grokbot`、`claudebot`、`codexbot`。

## 1. 動手前先讀再改

```sh
# 這家要抄誰:最像的那一家
grep -rn '"id": "claudebot"' -A8 src/cockpit/providers.json
ls src/cockpit | grep -E '^(grokbot|claudebot|codexbot|sand)'
# 對帳閘守哪幾處(它是機器事實,不是我的記憶)
sed -n '/^def _registry_consumers/,/^    return out/p' tests/test-provider-registry.py
```

**橋要不要另寫?** 多半不用。`sand_bridge.py` 本來就吃 `--provider-id`,
`grokbot`／`claudebot`／`musebot` 共用它,各自只有幾處小分支(title、bound_env、
bound_agent、鎖、banner、mail-token)。**先數那幾處有幾行,再決定要不要開新檔。**

## 2. 要改的地方(逐項打勾)

**先跑對帳閘,它會指名漏哪裡;下面的清單是給你知道「為什麼有這麼多處」。**

```sh
aib test --fresh --repo . tests/test-provider-registry.py tests/test-bot-provider-sites-987f7.py
```

- `test-provider-registry.py`:守 §B 的 8 處(所有 provider 共用的表)。
- `test-bot-provider-sites-987f7.py`:守 §C 的 bot 散處。**母體從 `providers.json` 的 `menu_group=bot` 來**,
  不寫死數字;每一處解不到 ⇒ 紅(儀器壞了);缺哪一家、在哪一處 ⇒ 紅並指名;已知例外附理由,例外補上了也紅。
  10-05 掛 musebot 時,交接包 20 檔只補到其中 5 處;其餘 **13 處是複核用 `git grep claudebot` 逐檔對出來的**。

### A. 登記簿本體

- `src/cockpit/providers.json` 加一列:`id` / `letter`(三個大寫字母) / `status: supported` /
  `default_requested_model` / `identity` / `identity_note`(寫清楚綁什麼、怎麼收信、**不是**哪一家) /
  `menu_label` / `menu_group: bot`。★ 這一列是兩支對帳閘的母體;**先加它,再跑閘,閘會把其餘每一處點出來。**

### B. 所有 provider 共用的 8 處(`test-provider-registry.py` 守)

1. `src/cockpit/aib` → `PROVIDERS="…"`
2. `src/cockpit/provider-launch.sh` → `case "$PROVIDER" in …)`
3. `src/cockpit/render/render_lib.py` → `ENGINE_LETTERS`
4. 同檔 → `ENGINE_LETTER_COLORS`
5. 同檔 → `ENGINE_ICONS`
6. `src/cockpit/pane-right-ai.sh` → `ENGINE_ORDER`
7. `src/binding/bind_queue.py` → `PROVIDERS`
8. `src/bootstrap/aib_doctor.py` → 預設 providers 清單

### C. bot 專用的散處(`test-bot-provider-sites-987f7.py` 守;**漏了不會報錯,只會壞**)

| 處 | 漏了會怎樣 |
|---|---|
| `AIBOT_PROVIDERS` ×5:`seat_onboard.py`、`seat_status.py`、`render/render_lib.py`、`mouse/list_captains.py`、`pane-right-ai.sh` | 右欄畫成「未確認 · 缺 引擎」、沒有 🤖 段位圖 |
| `activity_probe.py` → `BRIDGE_BOT_PROVIDERS` | 閒置/活著判錯:`[X]` 永不亮或永遠亮 |
| `seat_fork.py` → `BOT_PROVIDERS` | 分席/接回時不當它是橋接席 |
| `mouse/filter_new_ai_providers.py` → `BOT_IDS` | 右鍵「另開 AI」選單排錯組 |
| `bot_status.py` → `BOT_PROVIDERS`(sand 刻意不列) | `aib bot status` 看不到它 |
| `composer_dialect.py` → `_DIALECTS` 加一家(抄 grokbot) | **`aib dm` 一律被擋**:不知道它的輸入框長什麼樣,判不出能不能貼(codexbot 09-24 前例) |
| `provider-run-relogin.sh` → `self_register_seat` 的 case 名單 | **席位檔留舊 pid,dm 判「連不到」** |
| `cockpit_lib.py` → `canonical_provider` 的 aliases | 席位檔的 provider 對不回 hub 選項 |
| `model_label.py` → `-bridge` 去尾的 stem 集合 | 右欄型號欄印出 `xxx-bridge` |
| `nav/captains-ctl.sh` → `🤖` 的 case | 艦長列圖示錯 |
| `bridge_persona.py` → `PROVIDERS`;`identity/create_and_bind_persona.py` → 橋接席名單(sand 刻意不列) | **人格包永遠送不到這一席** |
| `seat_retire.py` → dm.sock 路徑那行 | 收席時 socket 收不乾淨 |
| `routing/table.json` → provider → 廠商 | 路由/額度歸錯家 |
| `bot_threads.py` → `BACKENDS` 或 `UNSUPPORTED` | 開席前選對話那一步沒有它的說法 |
| `entitlement/providers.py` → 備援名單 | 授權判斷漏它 |
| `src/cockpit/bot_launchers.py` → `LAUNCHERS` 加 `aib-<bot>-mail`(公開版沒有 `tools/aib-install-path`;安裝 / 更新 / 開席時自動建,手動 `aib bot install-launchers`) | **對方被教要呼叫的固定入口,`~/.local/bin` 裡根本沒有** |
| `i18n/{zh-TW,en,ja,ko}.json` → `aib.help.providers` | 四語 help 少一家 |
| `render/seat_colors.py` → 席色,跑 `assert_seat_colors_safe()` | 與紅黃綠撞色,狀態燈看不出來 |
| `bot-thread-menu.sh` → 開席前 preflight | 要不要先選對話沒人問 |
| `tests/test-routing-table-second-party.py` → `COMPANIES` 治具母體 | 那支尺的「缺列」紅被 `companies_mismatch` 蓋掉:**紅錯地方**,查的人去修路由表而不是補治具(10-06 實踩) |

### D. bot 專屬的新檔

- `src/cockpit/<bot>_threads.py` —— **綁定 id 的形狀判準與「同一對話只准一隻席」的鎖**
  ★ 那個 id 會變成鎖檔檔名(`<id>.lock`)。**不驗形狀 = 路徑穿越。**
  抄 `grokbot_threads.py` / `claudebot_threads.py`:`valid_*`、`lock_path`、`held_by_names`、`BINDINGS_NAME`
- `src/cockpit/<bot>_mailbox.py` —— 收信/回信 CLI(`pull` / `post` / `dm` / `status`)
  ★ `--session` **一定要驗形狀並確認 resolve 後仍在 `bridge/` 底下**(見 §3 第一條)
- `tools/aib-<bot>`、`tools/aib-<bot>-mail` —— 固定入口(對方只記這個名字,合併搬樹都不用改);**記得 §C 的 install_link**
- `docs/<BOT>-DESKTOP.md` —— 綁定、信箱協議、喚醒方式、已知缺口
- `tests/test-<bot>-mount.py`、`tests/test-<bot>-mailbox.py`(每一條守衛配一把刀,見 §4)

### E. 對帳閘量不到、要人用眼睛看的

- `sand_bridge.py` 的 **dm.sock 目錄**不是清單是 if/else:`claudebot` 落 `claudebot-bindings`,**其他一律落 `grokbot-bindings`**;
  `dm_sock_scan` 也只掃 `grokbot-bindings`。新 bot 若有自己的 `<bot>_threads.BINDINGS_NAME`,這裡要跟著加分支,
  否則它的 socket 跟 `seat_retire.py` 找的位置對不上(musebot 10-05 就是這樣)。
- `sand_bridge.main()` 讀綁定那兩行的名單(`BOUND_PROVIDERS`):不在名單裡 ⇒ `bound_agent`/`bound_env` 的新分支**走不到**,
  而直呼函式的測試仍是綠的。
- `grokbot_mailbox.seat_root()` 不帶 provider 時只排除 claudebot:新 bot 若與 grokbot 共用 `agent_id` 欄位,要補排除。

## 3. 安全四條(每一條都付過代價)

1. **凡是會變成路徑或檔名的外來字串,先驗形狀,再確認組出來的路徑沒跑出該在的目錄。**
   `--session`、對話 id 都是。只驗「非空」等於沒驗。
2. **通行碼(mail-token)擋的是「別的 AI 隨手冒用」。** 只要對方能指定信箱位置,
   它比對的就是對方自己放的那份 ⇒ 門等於不存在。所以第 1 條是這道門的前提。
3. **dm 只走 `aib_dm_tool.run_aib_dm`**(鐵則七)。不要自己 subprocess 一份。
4. **正文會印在人的終端機上** ⇒ 濾控制字元、限長。

## 4. 測試(沒有對照就不算數)

- 每一條新守衛配一把刀:**把修法拿掉,指定那一格必須紅**。
  只有正面斷言的測試,守衛壞掉時不會叫。
- **母體不要寫死數字。** 寫死「三處」的那支測試,正是這次漏掉兩處還全綠的原因。
  要掃就掃全倉,並斷言「掃到的處數 ≥ N」,少了也紅。
- **測試要走真流程,不要只直呼函式。** musebot 的 `bound_agent("musebot")` 測試是綠的,
  而 `main()` 根本不會呼叫它 —— 綠燈證明的是函式會動,不是那條路有人走。
- 跑法:`aib test --fresh --repo . tests/test-<bot>-*.py tests/test-provider-registry.py`
  先拿機器租約:`python3 tools/governance/host_lease.py acquire --owner <艙>/<席> --what … --run-id … --ttl …`
  **看 acquire 的真 rc,拿不到就等**;rc 不要接管線(`| head` 會吃掉它)。
  ★ **租約在手也不要一邊跑一邊改同一棵樹** —— 有些測試會就地改產品檔再還原,
  會把你剛寫的改動一起還原掉(2026-10-05 實際發生,`seat_status.py` 被抹掉一次)。

## 5. 真機驗收(機器驗不到的那一段)

```sh
# 在自己的工作樹開,不要改全艙的 aib;AIB_NO_ATTACH=1 才不會把人的畫面切走
AIB_NO_ATTACH=1 EZ2_AIB_INSTANCE=<艙 id> EZ2_AIHUB_SOCKET=aihub-<艙 id> \
  EZ2_AIB_INSTANCE_HOME=$HOME/.ez2-aib/instances/<艙 id> \
  ./src/cockpit/aib <bot> dgrs root
```

不帶艙變數會**另外開一個全新的艙**;不帶 `AIB_NO_ATTACH=1` 最後一步會 `tmux attach`,
把真人的畫面切到別的艙去。

逐項看:

- 席位檔 `instances/<艙>/seats/wN.json` 的 `provider` 對不對
- `~/.ez2-aib/bridge/<席 uuid>/` 有沒有 `inbox`/`outbox`/`mail-token`
- **右欄那一列有沒有名字**。印成 `-- @xxxxx · 未確認 · 缺 引擎` = 畫面查不到這家的引擎代號
  ⇒ 多半是 §2C 的 `AIBOT_PROVIDERS` 或 render 三表漏了,
  **也可能是全艙的 `aib` 指到別的工作樹**(`cat ~/.local/bin/aib` 看它指哪)
- `aib-<bot>-mail pull/post` 對真席走一輪,通行碼要真的擋得住
- **`aib dm <艙位碼>/<席碼> <訊> --verify` 要得到 `delivered`**(10-05 the maintainer 釘的驗收)。被擋成「provider 來源不一致或不合法(席位檔=缺 tmux=<bot>)」
  = 送信那端跑的程式不認這家:看 §2C 的方言表、self-register 兩張 case、`cockpit_lib` 別名,**以及** `cat ~/.local/bin/aib` 指的是哪棵樹
  (10-05 實測:艙內 aib 指到沒有 musebot 的工作樹,送 a seat 被拒;換含 musebot 的樹同一句就 delivered)
- 對方那側的排程要改盯**這一席的信箱**,不是 POC 的測試信箱

## 6. 交件與合入

- 只在自己的工作樹做;交件附真實輸出,寫清楚**哪些驗過、哪些沒驗**。
- 外部 AI(在別的機器/假倉)交來的包:**patch 用 `git diff` 產生,不要手寫**,
  並附基底 commit sha。手寫 diff 行數對不上會讓 `git apply` 直接拒收;
  基底不同則只能放寬上下文比對,「套得上」就不再等於「套在對的位置」。
- 合進本機 main 前跑一次 `tools/governance/main-merge-witness.sh`;推遠端一律等真人一句。

## 7. 名詞解釋

- **對帳閘**:`tests/test-provider-registry.py`。它比對登記簿與 8 個散處,少一處就紅並指名。
- **刀 / 對照格**:故意把修法拿掉,指定的那一格必須失敗。沒有刀的守衛,壞掉時沒有人會叫。
- **機器租約**:同一台機器同一時間只讓一組測試跑的鎖(`tools/governance/host_lease.py`)。
- **母體**:這一次要檢查的範圍。母體圈錯的守衛,量得再準也是錯的。
