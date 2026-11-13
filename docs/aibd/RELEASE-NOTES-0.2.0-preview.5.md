# aibd 0.2.0-preview.5(預覽版)

> AIB 2.0 Rust 核心的第五個預覽版。這一版重點是**開對話不再被拒、改成排隊**,以及**Mac 桌機停下對話後名額更常放回來**;另外多了「一步開席」指令。它還不會取代你現在用的 aib,裝了也不影響現有的 aib。

## 這一版改了什麼
- **名額滿了改成排隊，不再直接拒絕**:以前同時開的對話到上限,新的會被拒(429)。這一版會先排隊(狀態 `pending`),有名額空出來就照先來後到自動開跑;開跑前會再檢查一次名單。排隊中的可以用 `aibd ctl stop` 取消。隊伍最多 64 個，滿了才會拒絕(`session_queue_full`)。aibd 關掉或重開時，還在排隊的會標成「被中斷」。
- **Mac 桌機停下對話後，名額更常放回來**:preview.4 在桌上型 Mac 偶爾停下後名額卡住，原因是系統另外拉起的背景服務(例如 Dock 的輔助程式)被算進這個對話。這一版會分出不屬於這個對話的程式，不再為它卡名額;停止紀錄也會寫出讀不到的是哪幾支程式，方便查。
- **一步開席**:`aibd ctl seat open <名字> -- <程式完整路徑> [參數…]` 一次做完「建席位 + 開對話」。程式路徑不對時，什麼都不會建;席位建好但對話沒開成，會印出席位 id,可以直接重開對話。
- **健康檢查分得出 aibd 重開過**:`/api/v1/health` 多回 `version`、`started_at`、`instance_id`(每次啟動都不同)。仍然不回任何檔案路徑。
- **錯誤碼清單固定**:對外的錯誤碼清單順序與筆數在編譯時就檢查，不會因為合併而默默多一筆或換位。
- 沿用 preview.4:空字串參數可用、`aibd ctl watch` 只看指定對話、只准開名單上的程式、被拒都留紀錄。

## 這一版還沒有
- 網頁操作畫面：瀏覽器配對的介面已經在程式裡，但預設關閉，發布的執行檔也沒有編進網頁檔案;這一版請不要開。
- 在對話裡打字回它(互動輸入)、角色 / 人格、從現在的 aib 匯入資料。
- 過熱保護：這一版只放進量溫度的程式，還不會因為過熱而暫停開對話。
- Windows:可以裝;`aibd ctl policy init` 這一版只在 Mac 能用,Windows 要自己寫名單檔;對話輸出這一版在 Windows 不收，開 Claude 對話只在 Mac 驗過。

## 支援平台
| 平台 | 檔案 |
|---|---|
| macOS(Apple 晶片 M1 以上) | `aibd-0.2.0-preview.5-aarch64-apple-darwin` |
| Windows x64(在 GitHub 的 Windows 機器上實測;實體電腦驗收中) | `aibd-0.2.0-preview.5-x86_64-pc-windows-msvc.exe` |

## 安裝
已裝 preview.1 到 preview.4 的,照下面整段重貼一次就會換成新版。**換版後請把正在跑的 aibd 關掉再開**(見「要知道的」第一條)。

**macOS(終端機,整段貼上)**
```sh
V=0.2.0-preview.5
B=https://github.com/ez2app/ez2-aib/releases/download/aibd-v$V
F=aibd-$V-aarch64-apple-darwin
curl -fLO "$B/$F" && curl -fLO "$B/SHA256SUMS" \
  && grep " $F\$" SHA256SUMS | shasum -a 256 -c - \
  && chmod +x "$F" && mkdir -p ~/.local/bin && mv "$F" ~/.local/bin/aibd \
  && ~/.local/bin/aibd --version && ~/.local/bin/aibd selfcheck
```
檢查碼不一致時會停下,不會安裝。想直接打 `aibd` 執行,要把 `~/.local/bin` 加進 PATH;不加也可以用完整路徑 `~/.local/bin/aibd`。

**Windows(PowerShell,整段貼上)**
```powershell
$ErrorActionPreference = "Stop"
$V = "0.2.0-preview.5"
$B = "https://github.com/ez2app/ez2-aib/releases/download/aibd-v$V"
$F = "aibd-$V-x86_64-pc-windows-msvc.exe"
$D = "$env:LOCALAPPDATA\aibd"; New-Item -ItemType Directory -Force $D | Out-Null
Invoke-WebRequest "$B/$F" -OutFile "$D\aibd.exe"
Invoke-WebRequest "$B/SHA256SUMS" -OutFile "$D\SHA256SUMS"
$line = Select-String -SimpleMatch " $F" "$D\SHA256SUMS" | Select-Object -First 1
if (-not $line) { Remove-Item "$D\aibd.exe"; throw "檢查碼檔裡找不到 $F,已刪除下載檔" }
$want = ([regex]::Match($line.Line, '^[0-9a-fA-F]{64}')).Value.ToLower()
$got = (Get-FileHash "$D\aibd.exe" -Algorithm SHA256).Hash.ToLower()
if (-not $want -or $want -ne $got) { Remove-Item "$D\aibd.exe"; throw "檢查碼不一致,已刪除下載檔" }
Unblock-File "$D\aibd.exe"
& "$D\aibd.exe" --version
& "$D\aibd.exe" selfcheck
```
檢查碼不一致時會停下並刪掉下載的檔案,不會執行。

## 試一次(macOS)
```sh
aibd ctl policy init                      # 找到本機 claude(要官方簽章的單一執行檔，不收 npm 版),寫允許名單
AIBD_SESSION_POLICY=<policy init 最後印出的路徑> aibd &   # 啟動要帶名單路徑;沒帶 = 什麼都不開
aibd ctl seat open demo -- ~/.local/bin/claude -p "用一句話說今天天氣" --output-format stream-json --verbose --setting-sources ""   # 這一版起：建席位 + 開對話一步完成
aibd ctl watch <session-id>               # 一行一行即時看這個對話的輸出
aibd ctl stop <session-id>                # 收掉(連它開的程式一起收);還在排隊的會直接取消
aibd ctl list                             # 關掉 aibd 再開，席位與對話都還在
```

## 要知道的
- **換版後要重開 aibd**:新的 `aibd ctl` 要搭新的 aibd。舊版 aibd 還在跑時，請關掉再開。
- **只准開名單上的程式**:沒有名單就什麼都不開;名單裡寫了 shell、直譯器也一律拒絕(把 shell 複製改名的，這一版擋不住，見「不驗簽章」那條)。每次被拒都會記下(一分鐘內同一種合併成一筆、記次數)。
- **開出來的程式拿到 aibd 的整份環境變數**:你啟動 aibd 時環境裡有的東西(包含金鑰類的變數),它開的程式都看得到。請不要在帶著不想給 Claude 看的變數的終端機裡啟動 aibd。之後會收窄。
- **名單只比對路徑，不驗簽章**:`policy init` 找 claude 時會看官方簽章，但之後開程式只比對名單裡的路徑;那個路徑的檔案被換掉，這一版不會發現。
- **輸出原樣存在你自己的 aibd 資料夾**,可能含程式印出的秘密(這一版還不遮蔽)。資料庫只有你能讀寫(0600)、資料夾 0700。
- **Mac 上少數情況名額仍會卡住**:如果某個程序在它的建立者結束之後才啟動另一支程式,aibd 查不到它是誰開的，只會標成「無法確認」(`candidates_unreadable`)—— 不會對它送訊號、會保留名額，也不會把這棵樹報成已收乾淨。這時新的對話會排隊，不會被拒;要馬上放名額就重啟 aibd,或啟動時帶 `AIBD_MAX_SESSIONS=32` 把上限調高。
- **正常關掉 aibd 再開，已結束或還在排隊的對話可能顯示「被中斷」**:結束時沒能確認收乾淨的，重開時照實標成被中斷。
- **aibd 被強制結束(kill -9)時,macOS 上它開的程式可能留著**:這種情況 aibd 來不及收;請自己看一下有沒有殘留。
