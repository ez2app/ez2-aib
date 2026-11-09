# aibd 0.2.0-preview.3(預覽版)

> AIB 2.0 Rust 核心的第三個預覽版。這一版開始能**在 Mac 上用 aibd 開一個真的 Claude 對話、即時看它的輸出、把它收掉,而且 aibd 重開後紀錄都還在**。它還不會取代你現在用的 aib,裝了也不影響現有的 aib。

## 有什麼
- **新的:開一個 Claude 對話、即時看輸出**:`aibd ctl session start` 用你這台電腦已登入的 Claude 開一個對話(不需要另外給金鑰);`aibd ctl watch` 一行一行即時看它輸出(Claude 的 stream-json 每行一筆)。
- **收掉**:`aibd ctl stop` 收掉這個對話,連它再開出來的程式一起收;收不乾淨會照實列出來。
- **重開 aibd 紀錄還在**:席位、對話、已經出來的輸出都存在本機;aibd 關掉再開,`aibd ctl list` 看得到之前的席位與對話,輸出也讀得回來;上次還在跑的對話會標成「被中斷」,不會自動重開。
- **只准開名單上的程式,被拒都留紀錄**:`aibd ctl policy init` 會找到本機的 Claude 寫進名單;名單外的程式、shell、直譯器一律拒絕,每次被拒都記一筆(同一種一分鐘合併成一筆加次數)。
- 沿用 preview.2:開程式 / 看狀態 / 收掉、`aibd selfcheck`、健康狀態;只接受本機連線,開程式要權杖。

## 這一版還沒有
- 網頁操作畫面、在對話裡打字回它(互動輸入)、角色 / 人格、從現在的 aib 匯入資料。
- Windows:可以裝;`aibd ctl policy init` 這一版只在 Mac 能用,Windows 要自己寫名單檔;對話輸出這一版在 Windows 不收,開 Claude 對話只在 Mac 驗過。

## 支援平台
| 平台 | 檔案 |
|---|---|
| macOS(Apple 晶片 M1 以上) | `aibd-0.2.0-preview.3-aarch64-apple-darwin` |
| Windows x64(在 GitHub 的 Windows 機器上實測;實體電腦驗收中) | `aibd-0.2.0-preview.3-x86_64-pc-windows-msvc.exe` |

## 安裝
已裝 preview.1 或 preview.2 的，照下面整段重貼一次就會換成新版。

**macOS(終端機，整段貼上)**
```sh
V=0.2.0-preview.3
B=https://github.com/ez2app/ez2-aib/releases/download/aibd-v$V
F=aibd-$V-aarch64-apple-darwin
curl -fLO "$B/$F" && curl -fLO "$B/SHA256SUMS" \
  && grep " $F\$" SHA256SUMS | shasum -a 256 -c - \
  && chmod +x "$F" && mkdir -p ~/.local/bin && mv "$F" ~/.local/bin/aibd \
  && ~/.local/bin/aibd --version && ~/.local/bin/aibd selfcheck
```
檢查碼不一致時會停下，不會安裝。想直接打 `aibd` 執行，要把 `~/.local/bin` 加進 PATH;不加也可以用完整路徑 `~/.local/bin/aibd`。

**Windows(PowerShell,整段貼上)**
```powershell
$ErrorActionPreference = "Stop"
$V = "0.2.0-preview.3"
$B = "https://github.com/ez2app/ez2-aib/releases/download/aibd-v$V"
$F = "aibd-$V-x86_64-pc-windows-msvc.exe"
$D = "$env:LOCALAPPDATA\aibd"; New-Item -ItemType Directory -Force $D | Out-Null
Invoke-WebRequest "$B/$F" -OutFile "$D\aibd.exe"
Invoke-WebRequest "$B/SHA256SUMS" -OutFile "$D\SHA256SUMS"
$line = Select-String -SimpleMatch " $F" "$D\SHA256SUMS" | Select-Object -First 1
if (-not $line) { Remove-Item "$D\aibd.exe"; throw "檢查碼檔裡找不到 $F,已刪除下載檔" }
$want = ([regex]::Match($line.Line, '^[0-9a-fA-F]{64}')).Value.ToLower()
$got = (Get-FileHash "$D\aibd.exe" -Algorithm SHA256).Hash.ToLower()
if (-not $want -or $want -ne $got) { Remove-Item "$D\aibd.exe"; throw "檢查碼不一致，已刪除下載檔" }
Unblock-File "$D\aibd.exe"
& "$D\aibd.exe" --version
& "$D\aibd.exe" selfcheck
```
檢查碼不一致時會停下並刪掉下載的檔案，不會執行。

## 試一次(macOS)
```sh
aibd ctl policy init                      # 找到本機 claude(要官方簽章的單一執行檔,不收 npm 版),寫允許名單
AIBD_SESSION_POLICY=<policy init 最後印出的路徑> aibd &   # 啟動要帶名單路徑(policy init 最後一行可直接複製);沒帶 = 什麼都不開
aibd ctl seat create demo                 # 建一個席位,印出席位 id
aibd ctl session start <席位id> -- ~/.local/bin/claude -p "用一句話說今天天氣" --output-format stream-json --verbose --setting-sources project   # 對話在 aibd 資料夾的 sessions/ 裡跑(空夾),project = 不帶這台 Mac 的個人設定 / hooks
aibd ctl watch <session-id>               # 一行一行即時看輸出
aibd ctl stop <session-id>                # 收掉(連它開的程式一起收)
aibd ctl list                             # 關掉 aibd 再開,席位與對話都還在
```

## 要知道的
- **只准開名單上的程式**:沒有名單就什麼都不開;名單裡寫了 shell、直譯器也一律拒絕。每次被拒都記一筆。
- **輸出原樣存在你自己的 aibd 資料夾**,可能含程式印出的秘密(這一版還不遮蔽)。資料庫只有你能讀寫(0600)、資料夾 0700。
- **舊版 preview.2 的「直接開程式」也要名單了**:升上來之後沒寫名單,開程式會被拒(403)。
- **macOS 上 Claude 對話結束後,名額可能不放**:aibd 看不到它開出來的子程序,就照實記成「未確認」、名額先留著。連開約 8 次會開不了新對話 ⇒ 重啟 aibd,或啟動時帶 `AIBD_MAX_SESSIONS=32`。下一版修。
- **正常關掉 aibd 再開,已結束的對話可能顯示「被中斷」**:同上,結束時沒能確認收乾淨的,重開時照實標成被中斷。
- **aibd 被強制結束(kill -9)時,macOS 上它開的程式可能留著**:這種情況 aibd 來不及收;請自己看一下有沒有殘留。
