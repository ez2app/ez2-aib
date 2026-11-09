# aibd 0.2.0-preview.4(預覽版)

> AIB 2.0 Rust 核心的第四個預覽版。這一版修 preview.3 用起來卡住的地方:**Claude 指令可以帶空字串參數、`aibd ctl watch` 只看這個對話而且不會提早停**,以及 **Mac 上一開就結束的程式停下後名額會放回來**。它還不會取代你現在用的 aib,裝了也不影響現有的 aib。

## 這一版改了什麼
- **空字串參數可以用了**:像 `--setting-sources ""`(不帶這台電腦的個人設定與 hooks)這種寫法,preview.3 會被拒(400),這一版會原樣傳給 Claude。第一個參數(要開的程式)仍然必須是完整路徑、不能是空的。
- **`aibd ctl watch` 只看這個對話、不會提早停**:以前會混進其他對話的事件;這一版只列你指定的那個對話。對話還在輸出時不會被當成已結束而停下。
- **被拒紀錄裡的隱形字元會顯示出來**:程式名稱裡常見的、會讓終端機 / 紀錄反轉或隱藏文字的字元,這一版會轉成看得見的寫法。
- **Mac 上一開就結束的程式會把名額還回來**:preview.3 在 Mac 上，像 `printenv`、`sleep 0` 這種一開就結束的程式，或中途叫過 `ps` 的程式，結束後名額不放，連開幾次就開不了新對話。這一版這類程式在看得到的範圍內沒有殘留，就會把名額放回來。真的 Claude 對話還不一定(見「要知道的」)。
- 沿用 preview.3:開一個 Claude 對話、即時看輸出、收掉、重開 aibd 紀錄還在;只准開名單上的程式,被拒都留紀錄。

## 這一版還沒有
- 網頁操作畫面、在對話裡打字回它(互動輸入)、角色 / 人格、從現在的 aib 匯入資料。
- Windows:可以裝;`aibd ctl policy init` 這一版只在 Mac 能用,Windows 要自己寫名單檔;對話輸出這一版在 Windows 不收,開 Claude 對話只在 Mac 驗過。

## 支援平台
| 平台 | 檔案 |
|---|---|
| macOS(Apple 晶片 M1 以上) | `aibd-0.2.0-preview.4-aarch64-apple-darwin` |
| Windows x64(在 GitHub 的 Windows 機器上實測;實體電腦驗收中) | `aibd-0.2.0-preview.4-x86_64-pc-windows-msvc.exe` |

## 安裝
已裝 preview.1、preview.2 或 preview.3 的,照下面整段重貼一次就會換成新版。**換版後請把正在跑的 aibd 關掉再開**(見「要知道的」第一條)。

**macOS(終端機,整段貼上)**
```sh
V=0.2.0-preview.4
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
$V = "0.2.0-preview.4"
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
aibd ctl policy init                      # 找到本機 claude(要官方簽章的單一執行檔,不收 npm 版),寫允許名單
AIBD_SESSION_POLICY=<policy init 最後印出的路徑> aibd &   # 啟動要帶名單路徑;沒帶 = 什麼都不開
aibd ctl seat create demo                 # 建一個席位,印出席位 id
aibd ctl session start <席位id> -- ~/.local/bin/claude -p "用一句話說今天天氣" --output-format stream-json --verbose --setting-sources ""   # "" = 不帶這台 Mac 的個人設定 / hooks(這一版起可以用空字串)
aibd ctl watch <session-id>               # 一行一行即時看這個對話的輸出
aibd ctl stop <session-id>                # 收掉(連它開的程式一起收)
aibd ctl list                             # 關掉 aibd 再開,席位與對話都還在
```

## 要知道的
- **換版後要重開 aibd**:新的 `aibd ctl watch` 要搭新的 aibd。如果舊版 aibd 還在跑,watch 會直接報錯停下(不會把別的對話印成這一個);把 aibd 關掉再開就好。
- **只准開名單上的程式**:沒有名單就什麼都不開;名單裡寫了 shell、直譯器也一律拒絕(把 shell 複製改名的，這一版擋不住，見「不驗簽章」那條)。每次被拒都會記下(一分鐘內同一種合併成一筆、記次數)。
- **開出來的程式拿到 aibd 的整份環境變數**:你啟動 aibd 時環境裡有的東西(包含金鑰類的變數),它開的程式都看得到。請不要在帶著不想給 Claude 看的變數的終端機裡啟動 aibd。之後會收窄。
- **名單只比對路徑,不驗簽章**:`policy init` 找 claude 時會看官方簽章,但之後開程式只比對名單裡的路徑;那個路徑的檔案被換掉,這一版不會發現。
- **輸出原樣存在你自己的 aibd 資料夾**,可能含程式印出的秘密(這一版還不遮蔽)。資料庫只有你能讀寫(0600)、資料夾 0700。
- **桌機上較長的 Claude 對話，偶爾停下後名額仍卡住**:在桌上型 Mac 上跑比較久的 Claude 對話，偶爾(本機實測 16 次 1 次)停下後名額仍卡住。暫解 = 重啟 aibd,或啟動時帶 `AIBD_MAX_SESSIONS=32` 把上限調高。
- **正常關掉 aibd 再開,已結束的對話可能顯示「被中斷」**:結束時沒能確認收乾淨的,重開時照實標成被中斷。
- **aibd 被強制結束(kill -9)時,macOS 上它開的程式可能留著**:這種情況 aibd 來不及收;請自己看一下有沒有殘留。
