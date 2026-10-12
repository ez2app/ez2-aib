# 艦長室網頁(Captain's Room)

把一艙的 AI 席畫成一棟辦公大樓裡走動的員工:1F 大廳(接待櫃台的 bot、人資招聘、收發室)、2F 辦公區(每席一張桌)、3F 主管層(艦長室、會議室、休息室)、門外(公車站)。
品牌 logo、正在做的 📌 項目、CTX%、未讀信、交接都畫出來;越累(CTX 越滿)越駝背。
點人可對話(真的 `aib dm`)、看/改人格卡、看第二大腦(唯讀);🛠 裝潢模式可以擺 29 種道具、換地板牆面、搬房間、改大小、上色、匯出/匯入版面。

- 網頁:`tools/captain-room/index.html`(單檔,Canvas 2D,四語 zh-TW / en / ja / ko)
- 伺服器:`src/cockpit/captain_room.py`(只聽 127.0.0.1;每次啟動產生一把鑰匙,寫檔/寄信都要帶)。
  放在 `src/` 底下是因為 dm 的寄件身分只認 `src/` 裡的程式:網頁寄的信抬頭是「艦長室@room」,不會掛成啟動它那一席。
  `tools/captain-room/server.py` 是舊路徑的轉接殼。

## 用法(`aib room`)

| 指令 | 做什麼 |
|---|---|
| `aib room` | 確定伺服器在跑(沒跑就脫離席位啟動),然後開瀏覽器 |
| `aib room status` | 在哪、活不活、有沒有裝開機啟動 |
| `aib room install-boot [--port N] [--ship-seat 席碼]` | macOS 登入就跑、掛了自己重開(launchd);要從正本樹裝 |
| `aib room uninstall-boot` | 拆掉 |
| `aib room serve --cabin auto\|<艙位碼>` | 前景跑(launchd 用這個) |
| `aib room set-pin --pin 1234` / `clear-pin` | 開席/關席/寄信/改人格前頁面要輸入一次;沒設 PIN 頁面會明講 |
| `aib room set-company --name "名字"` | 招牌、公司旗、logo 牆、接待櫃台、海報、網頁標題全套;空字串 = 預設 AIB |

艙位預設 `auto`:跟著「最近有動靜的活艙」走,艙重開、換艙號都不用改設定。選項會記在 `~/.ez2-aib/state/captain-room/config.json`。

頁面每 5 秒拿一次 `/api/state`;伺服器每 10 秒量一次 AIB(`seat-info.py --fleet`),探測慢就自動放慢。所有動作(開席、關席、寄信、改人格、版面、PIN、公司名)留痕在 `actions.log`。

## 網址參數(驗證/直達用)

| 參數 | 作用 |
|---|---|
| `?scene=f1\|f2\|f3\|street` | 直接看那一層 |
| `?sel=席碼[:status\|persona\|brain]` | 預選一席並切到那個分頁 |
| `?hr=1` | 開人資面板(開席) |
| `?decor=1`、`?room=cap\|ship\|lounge\|desks` | 裝潢模式 |
| `?follow=0` | 不跟著事件切層(截圖驗證用) |
| `?tab=status\|persona\|brain` | 右欄分頁 |

無頭 Chrome 截圖:一次只跑一支、網址不要帶 `&`、加 `--virtual-time-budget=8000`(不加會停在「讀取筆記…」)、絕對路徑、`--timeout`。

## 量到的 vs 推的

量到的:CTX、未讀、閒置秒數、交接收據、筆記數、bot 佇列、各席自己回報的 📌。推的(不是事實):❓ 說忙但沒動、會議配對(A 三分鐘內收到 B 的 dm 且兩人都忙)、常用模型清單、人走動與對話的動畫。頁面「說明」段有同一句。

## logo

有真標用真標:`logos/*.svg`(Simple Icons 16.34.0,openai 取 15.22.0,CC0,見 `logos/LICENSE-simple-icons.txt`),對照表在 `captain_room.py` 的 `LOGO_SVG`。沒真標的(Antigravity、Grok 𝕏、kiosapi/teamo/typesafe)退回右欄同一張 Nerd Font 字碼表(`render_lib.ENGINE_ICONS`,伺服器注入 `#brandSrv`,頁面載 `/fonts/nerd.ttf`)。不得自己發明一個「類似」的標;`tests/test-captain-room-web-86ff0.py` 擋住。

## 跟 aib-seat 面板的接點

- 縮圖:頁面每 8 秒 POST `/api/thumb` → `thumb.png`;面板在能貼圖的終端(kitty / Ghostty / WezTerm)顯示。
- 文字摘要:頁面每 8 秒 POST `/api/summary` → `summary.json`(各層人數、等決定、未讀、在線);Terminal.app 貼不了圖就顯示這一行(面板 0.10.3)。
- 一鍵開網頁:`/aib room`;沒在跑就用 `server.json` 記的指令拉起來。

## 檔案(都在 `~/.ez2-aib/state/captain-room/`)

`layout.json` 裝潢(`layout-history/` 留最近 30 份)、`looks.json` 人物外觀、`config.json`(port / ship_seat / cabin / pin / company)、`server.json` 伺服器在哪(面板讀它)、
`thumb.png`、`summary.json`、`actions.log`、`server.log`。

## 注意

- 伺服器**不能**從某一席的終端直接前景起(行程鏈會把寄件人認成那一席);用 `aib room` 或 launchd。
- 從 worktree 起的伺服器用的是那棵樹的 `aib`;`install-boot` 預設拒絕指向開發樹(`AIB_ALLOW_FOREIGN_BINDINGS=1` 可硬裝)。
- 沒設 PIN 時,誰開得了這一頁就能開席(會花錢);推上主線後先 `aib room set-pin`。
- 跨主機(`aib room add-host`)產的 `state/captain-room/fleet_key` 是私鑰:rsync `~/.ez2-aib` 到別台時一定要排除(`--exclude=state/captain-room/fleet_key*`),每台自己產自己的。

## 測試

```bash
bash src/cockpit/aib test tests/test-captain-room-web-86ff0.py
```
