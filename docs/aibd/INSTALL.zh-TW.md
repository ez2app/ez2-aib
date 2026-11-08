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
V=0.2.0-preview.1
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
$V = "0.2.0-preview.1"
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
用 aibd 開 / 管 AI 席位的程序還沒接上(程式已寫好))。結束碼 0 代表檢查本身有跑完;要依賴某個功能前,
先看「還沒驗證」那幾行。

## 移除

macOS:`rm ~/.local/bin/aibd` · Windows:`Remove-Item -Recurse "$env:LOCALAPPDATA\aibd"`
