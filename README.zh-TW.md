[English](README.md) | [繁體中文](README.zh-TW.md)

# aib

**只跑這次改動會影響到的測試;沒有新的紅燈才推。**

aib 會記下每一支測試實際讀過哪些檔(Python、bash、Node 都追得到)。
下次跑的時候,如果某支測試讀過的東西都沒變,就沿用它上次的**綠燈**,不再重跑。
紅燈永遠不沿用;aib 量不到的,一律照跑。

另外,`aib ship` 給你一道推前檢查:先跑受影響的測試,有紅燈的話,再到主線上重跑一次,
分辨這是「主線本來就紅」還是「這次改壞的」。只有沒有新紅燈的那一包才會推上去。
發版(改版號)只能由人在終端機上做。

## 快速開始

```sh
(curl -fsSL https://aib.ez2.app/install.sh || curl -fsSL https://raw.githubusercontent.com/ez2app/ez2-aib/main/install.sh) | bash
# macOS:裝到 ~/ez2-aib;之後隨時再跑同一行就是更新(網站連不上時自動改用 GitHub 上的同一份)
aib update                                          # 裝好之後也可以這樣更新(aib update --tools 會再問要加裝哪些 AI 工具)
# 其他裝法:pipx install git+https://github.com/ez2app/ez2-aib
#          git clone https://github.com/ez2app/ez2-aib ~/ez2-aib && export PATH="$HOME/ez2-aib/bin:$PATH"

cd your-project
aib test tests/test_something.py     # 第一次:實跑並記下來
aib test tests/test_something.py     # 什麼都沒改:沿用
aib test --all                       # 整套測試,同樣的規則
aib ship package --branch my-branch  # 推前檢查,過了就推上主線
```

Python 3.9 以上,Linux 或 macOS。不支援 Windows(WSL2 沒有測過)。

## 什麼時候值得用 aib?

aib 讓每支測試檔各自在一個有追蹤的行程裡跑,每支多一點成本;**全套很慢、而且多數改動只碰到一部分**時才划算。
實測數字(Linux、4 核心、牆鐘秒數):

| 專案 | 測試檔 | pytest(一個行程) | pytest `-n auto` | aib 第一次 | aib 什麼都沒改 | aib 改了核心檔 |
|---|---|---|---|---|---|---|
| 一個大型私有專案 | 983 | 約 3.3 小時(一支一支跑) | — | — | — | 一般推送 54 分鐘(`aib ship`) |
| [attrs](https://github.com/python-attrs/attrs) | 26 | 18.8 秒 | 8.2 秒 | 15.1 秒 | **3.0 秒** | 11.7 秒 |
| [click](https://github.com/pallets/click) | 43 | 7.0 秒 | 6.2 秒 | 17.2 秒 | **3.8 秒** | 15.2 秒 |

- **值得**:全套要跑好幾分鐘以上;改動通常只碰到部分模組;CI 每次推送都整套重跑。
- **不值得**:全套本來幾秒就跑完 —— 改到每支測試都會 import 的檔就全部重跑,這時 aib 比直接跑 pytest 還慢。
- 附帶好處:aib 讓每支測試檔單獨跑,會揭露「要別的測試先跑過才會過」的測試
  (click 就有一支:`tests/test_utils/test_echo_via_pager.py` 單獨跑會失敗,直接用 pytest 也一樣)。

## 支援的語言

Python(unittest、pytest)完整追蹤。Node(node:test、jest、mocha)、Go、PHP 可以經 aib 跑,也有推前檢查;
能沿用多少要看追蹤器看得到多少 —— 目前的對照表見 `docs/AIB-TEST-QUICKSTART.zh-TW.md`。
其他語言可以在 `.aib/ship.toml` 宣告測試指令;它們每次都會照跑,但打包和發版照常可用。

## 駕駛艙:在 tmux 裡開 AI 席位

`aib` 也是一個**駕駛艙**:把幾個 AI 寫程式助手(「席位」)並排開在同一個 tmux 視窗,每席有自己的角色(`dev`、`qa`、`pm`…),
左中右三欄、滑鼠選單、席位互傳訊息、對話快滿時的交接筆記,以及艦隊工具(席位身分、派工、巡查)。

```sh
aib claude safe dev        # 開一席 Claude Code,角色 dev,每個動作先問你
aib codex dgrs qa          # 開一席 Codex,角色 qa,全部放行(見下)
aib --help-cockpit         # 所有選項
```

- **需要:**macOS 和 tmux。每家 AI 自己的命令列工具要先裝好、登入好(`claude`、`codex`、`grok`…),aib 不幫你裝也不幫你登入。
  駕駛艙還沒在 Linux 上試過。
- **macOS 上的 bash 測試:** aib 追蹤 bash 腳本要 bash 4.1 以上。macOS 內建的是 3.2,跑 bash 腳本的測試會判「量不到」、每次重跑
  (安全,只是比較慢)。`brew install bash` 之後就能沿用。
- **權限模式,每席自己選:**`safe` 保留 AI 工具原本的保護(改檔、跑指令前會問你);`dgrs` **只對那一席**關掉這些保護,
  AI 不問就做,開席時 aib 會印警告和改用 `safe` 重開的指令。開每一席時自己決定。
- **Claude** 是 Anthropic 的商標;Codex 是 OpenAI 的;Grok 是 xAI 的。aib 與它們沒有關係。

## 沿用有多安全?

- 只沿用綠燈,而且只有在測試讀過的每一個輸入都一個位元組都沒變的時候。
- aib 看不到測試讀了什麼(追不到的子行程、網路、已知會自己讀檔的 C 擴充)⇒ 那支標成「量不到」,每次都照跑。
- **已知的缺口:**不在 aib 名單上、又在 C 層自己讀檔的 C 擴充,Python 的稽核鉤子看不到。Linux 上由下面的原生安全網補;
  **沒有安全網時(macOS,或沒有 C 編譯器),這種檔改了,測試可能不會重跑。**你的測試若依賴這樣讀的檔,
  發版前跑一次 `aib test --fresh`。
- `aib ship audit` 會隨機抽幾支「沿用」的測試真的重跑、比對結果。

## 隱私

aib 不會把你的程式碼、測試結果或任何資料傳給我們。紀錄只存在你自己的電腦上(`~/.ez2-aib/`,外加 `~/.cache/ez2-aib/`
一小份快取)。aib 自己的網路流量,只有 `aib ship` 用 git 對**你自己的**遠端做 fetch 與 push。

駕駛艙有一點不同:你開的 AI 席位就是各家 AI 自己的工具,它們照常連自家的服務。駕駛艙自己存在你電腦上的,全在 `~/.ez2-aib/` 底下:
席位紀錄與交接筆記、席位互傳的訊息、專案卡與角色卡,以及你為 API 金鑰席位加的金鑰(在 `~/.ez2-aib/secrets/`,資料夾 0700、
檔案 0600,用放在旁邊的金鑰加密 —— 防的是隨手看到和不小心提交,擋不住已經能讀你家目錄的人)。刪掉 `~/.ez2-aib/` 就全部清掉。

## 原生安全網(只在 Linux)

有些測試是由 C 程式(C 擴充、C 函式庫)讀檔的,Python 的稽核鉤子看不到。
在 Linux 上,如果 PATH 上有 C 編譯器(`cc`、`gcc` 或 `clang`),aib 會把它自帶的一小段 C 原始碼
(`aib/tools/verify/ldp-shim/`)編譯到 `~/.cache/ez2-aib/`,並用 `LD_PRELOAD` **只掛進測試行程**,讓這類讀檔也記得到。
沒有編譯器、或程式庫掛不上 ⇒ 安全網不開,aib 會說明原因。**這時上面那個缺口是開著的:**C 層讀的檔記不到,
那種檔改了,測試可能不會重跑。macOS 上一律沒有這層。
要關掉:`AIB_NATIVE_GUARD=0`。

## 暫存區保持乾淨

測試常會留下暫存檔,有時還會留下背景程式:一個忘了關的 tmux 伺服器可以跑上好幾個小時、吃掉好幾 GB 記憶體。aib 會替它跑的測試收尾:

- 每支測試有自己的暫存資料夾(`TMPDIR`,在 `<tmp>/aibt-…` 底下),測試結束就刪。
- 測試開了沒關的背景程式,測試結束時一併結束。
- 總表會點名哪幾支測試留了東西,方便你從源頭修。
- `aib tmp` 列出 aib 和它的測試留在暫存區的東西,以及已經結束的測試留下、還在跑的背景程式;`aib tmp --apply` 才真的清。
  不是 aib 的東西、捷徑、還有程式在用的,一律不碰。`aib ship` 推前會自動做一次。

## 付費外掛與授權

核心(`aib test`、`aib ship`、駕駛艙)免費、開源。另有一些外掛另外販售,需要 Ez2 簽發的授權檔。授權在本機離線驗證,
用 aib 內附的公鑰(Ed25519);改過的授權檔就會失效。

```sh
aib license status       # 這台的授權狀態
aib license device-id    # 這台在這份授權下的機器碼(授權綁機器時,把它給我們)
aib license activate     # 線上啟用的授權:登記這台
aib license deactivate   # 換電腦前先在舊電腦執行,釋出名額
```

機器碼是把你電腦的硬體編號和授權碼合在一起雜湊算出來的;硬體編號本身不會被顯示、送出或儲存,同一台電腦換一份授權,
算出來的機器碼就不同。線上啟用只送兩樣東西:簽過的授權檔和這個機器碼。

## CI

`aib ship ci` 會寫一份 GitHub Actions workflow,把 aib 的紀錄存在 Actions 快取裡,所以 CI 也只跑有變的。
從 fork 來的 PR 一律全部照跑(它們不准碰到紀錄)。

## 移除

```sh
rm -rf ~/ez2-aib ~/.local/bin/aib        # 用一行安裝裝的話
pipx uninstall ez2-aib                   # 用 pipx 裝的話
rm -rf ~/.ez2-aib ~/.cache/ez2-aib       # aib 的紀錄與快取(專案以外只寫這兩處,外加系統暫存資料夾裡的暫存檔)
rm -rf your-project/.aib                 # 只有 `aib ship` 建過、而且你不要了才刪
```

## 參與與資安

見 `CONTRIBUTING.zh-TW.md` 與 `SECURITY.zh-TW.md`。

## 沿革

AIB 的前身是我們 2024 年開始開發的 **ACB**。
ACB 的架構已經不符合現在的使用方式,所以沒有再繼續修補,而是整個重構成 AIB。
`aib test` 和 `aib ship` 是這次重構中開源的核心。

## 授權

Apache License 2.0 —— 見 `LICENSE`。
