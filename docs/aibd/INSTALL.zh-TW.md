<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- 草稿(11-08,C2):對使用者的文字,發版前 Andy 看過才公開。 -->
# 安裝 aibd(預覽版)

aibd 是 AIB 背景服務的 Rust 版本。**這是預覽版**:跟 Python 版 `aib` 並存、不取代它,也不在 PyPI 上。

| 你的電腦 | 檔案 |
|---|---|
| Apple 晶片的 Mac(M1 以後) | `aibd-<版本>-aarch64-apple-darwin` |
| Intel 處理器的 Mac | 這個預覽版還沒有(沒有 Intel 機器可以驗) |
| Windows x64(GitHub Windows 機器實測;實體電腦驗收中) | `aibd-<版本>-x86_64-pc-windows-msvc.exe` |

所有檔案和 `SHA256SUMS` 都在發版頁:`https://github.com/ez2app/ez2-aib/releases/tag/aibd-v<版本>`

**檔案還沒有簽章**(Apple / Windows 簽章還沒設定),macOS 和 Windows 可能會跳警告;下面的步驟會先用
`SHA256SUMS` 核對檔案,核對通過才執行。

## macOS

```sh
V=0.2.0-preview.9
F=aibd-$V-aarch64-apple-darwin
B=https://github.com/ez2app/ez2-aib/releases/download/aibd-v$V
cd ~/Downloads \
  && curl -fLO "$B/$F" && curl -fLO "$B/SHA256SUMS" \
  && grep " $F\$" SHA256SUMS | shasum -a 256 -c - \
  && chmod +x "$F" && mkdir -p ~/.local/bin && mv "$F" ~/.local/bin/aibd \
  && ~/.local/bin/aibd --version && ~/.local/bin/aibd selfcheck
```
整段一起貼上。下載失敗或檢查碼不一致時會停在那一步:不會安裝、也不會執行。想直接打 `aibd`,要把 `~/.local/bin`
加進 PATH;不加也可以用完整路徑 `~/.local/bin/aibd`。

用瀏覽器下載(不是 curl)的話,macOS 會在檔案上做記號;核對 OK 之後用
`xattr -d com.apple.quarantine ~/.local/bin/aibd` 拿掉。

## Windows(PowerShell)

```powershell
$ErrorActionPreference = "Stop"
$V = "0.2.0-preview.9"
$F = "aibd-$V-x86_64-pc-windows-msvc.exe"
$B = "https://github.com/ez2app/ez2-aib/releases/download/aibd-v$V"
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
整段一起貼上。檢查碼不一致時會停下並刪掉下載的檔案,不會執行。

如果 SmartScreen 顯示「Windows 已保護您的電腦」,是因為檔案沒簽章;上面那段只有檢查碼一致才會執行。

## selfcheck 在說什麼

它會列出你的平台、aibd 在這個平台能做什麼,以及每一個**還沒驗證**的部分(例如
在 macOS 上沒辦法證明一整棵程序樹都乾淨停掉,所以那一列寫 `unverified`)。結束碼 0 代表沒有任何一列失敗;
要依賴某個功能前,先看「還沒驗證」那幾行。

## 自動推進(macOS)

自動推進是同一個發版裡的另外兩支程式:`aib-drive` 算出每個 AIB 席位接下來能做什麼,`aib-slice` 讀它需要的
開著的 PR。aibd 每 10 分鐘叫醒 `aib-drive` 一次。要先裝好 Python 版 `aib` 並開好席位;紀錄寫在 `~/.ez2-aib/drive/`。

**裝好之後什麼信都不會發。** 四個開關 `nudge`、`open_review`、`notify_fix`、`dispatch` 出廠都是關的;每 10 分鐘那一輪
只把「本來會發的信」寫進 `~/.ez2-aib/drive/act-dryrun.jsonl`。`dispatch` 這個預覽版還沒開放,請保持關閉。

### 安裝

先跑上面 aibd 那一段,再跑:

```sh
(
  set -e
  V=0.2.0-preview.9
  B=https://github.com/ez2app/ez2-aib/releases/download/aibd-v$V
  cd ~/Downloads && curl -fLO "$B/SHA256SUMS"
  for p in aib-drive aib-slice; do
    F=$p-$V-aarch64-apple-darwin
    curl -fLO "$B/$F"
    grep " $F\$" SHA256SUMS | shasum -a 256 -c -
    chmod +x "$F" && mv "$F" ~/.local/bin/$p
  done
  ~/.local/bin/aib-drive switch 2>&1 | head -1
)
```
整段一次貼上。下載失敗或雜湊對不上就停在那裡,那支程式不會被搬進去。

`aib-slice` 用 GitHub CLI 從你本機的 clone 讀 PR(席位在做的那個 repo)。先登入一次,再告訴自動推進 clone 在哪
(一行、完整路徑):

```sh
gh auth login
mkdir -p ~/.ez2-aib/drive && echo "$HOME/<你的 clone 路徑>" > ~/.ez2-aib/drive/refresh-repo
```
沒有這個檔,掃描會被拒,清單裡 PR 那幾層一直是 unknown,`open_review` / `notify_fix` 開了也沒有信可發。

接著讓 aibd 開機自動啟動、並打開自動推進。這段會寫出 `~/Library/LaunchAgents/app.ez2.aibd.plist`:

```sh
mkdir -p ~/Library/LaunchAgents ~/Library/Logs
P=~/Library/LaunchAgents/app.ez2.aibd.plist
cat > "$P" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>app.ez2.aibd</string>
  <key>ProgramArguments</key><array><string>$HOME/.local/bin/aibd</string></array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>AIBD_DRIVE_WAKE</key><string>1</string>
    <key>AIB_DRIVE</key><string>$HOME/.local/bin/aib-drive</string>
    <key>AIBD_LOG</key><string>info</string>
    <key>PATH</key><string>$HOME/.local/bin:$HOME/.ez2-aib/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ProcessType</key><string>Standard</string>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/ez2-aibd.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/ez2-aibd.log</string>
</dict>
</plist>
EOF
launchctl bootout gui/$(id -u)/app.ez2.aibd 2>/dev/null || true
plutil -lint "$P" && launchctl bootstrap gui/$(id -u) "$P" && launchctl list | grep app.ez2.aibd
```

再跑一次(升級)時會先停掉正在跑的 aibd,載入的才是新檔。

裡面兩行最重要:
- `ProcessType` 是 `Standard`。寫成 `Background` 的話,macOS 會把 aibd 和它叫起來的程式降到很慢,一輪跑到逾時。
- `PATH` 最前面是 `~/.local/bin`(`aib` 在這裡)。少了它,aibd 那一輪找不到 `aib`,開關打開也一封信都送不出去。

確認有在跑:10 分鐘內 `~/.ez2-aib/drive/act-dryrun.jsonl` 會多一行,而且
`~/.local/bin/aib-drive next --json` 的來源沒有 `unknown`(`partial` 可以:新裝的機器上有幾層資料還不全，是正常的)。

### 打開開關

```sh
~/.local/bin/aib-drive switch nudge on --by <你的席碼 5 碼> --why "<為什麼>"
```
`nudge`:信等了 5 分鐘還沒被讀,提醒那一席;`open_review`:PR 好了,請審查席來審;`notify_fix`:告訴作者要修什麼。
每次切換都會寫進 `~/.ez2-aib/drive/switches.jsonl`。

### 停下來

- 只停一種信:`aib-drive switch <名稱> off --by <席碼> --why "<為什麼>"`。
- 暫停新工作、開關保持原樣:`aib-drive switch paused on --by <席碼> --why "<為什麼>"`(恢復用 `paused off`)。開審查、修正通知先不寄，提醒照寄;要全部不寄就把各開關關掉。
- 停掉 aibd 本身(到下次登入前):`launchctl bootout gui/$(id -u)/app.ez2.aibd`。

### 移除

```sh
launchctl bootout gui/$(id -u)/app.ez2.aibd
rm ~/Library/LaunchAgents/app.ez2.aibd.plist ~/.local/bin/aib-drive ~/.local/bin/aib-slice
```
`~/.ez2-aib/drive/` 裡的紀錄會留著;不要了再自己刪掉那個資料夾。

Windows:這個預覽版的自動推進只會列清單(`aib-drive next`),不發信,也不會開機自動啟動。

## 移除

macOS:有裝自動推進的話先照上面停掉並移除,再 `rm ~/.local/bin/aibd` · Windows:`Remove-Item -Recurse "$env:LOCALAPPDATA\aibd"`
