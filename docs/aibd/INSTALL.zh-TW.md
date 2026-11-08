<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- 草稿(11-08,C2):對使用者的文字,發版前 Andy 看過才公開。 -->
# 安裝 aibd(預覽版)

aibd 是 AIB 背景服務的 Rust 版本。**這是預覽版**:跟 Python 版 `aib` 並存、不取代它,也不在 PyPI 上。

| 你的電腦 | 檔案 |
|---|---|
| Apple 晶片的 Mac(M1 以後) | `aibd-<版本>-aarch64-apple-darwin` |
| Intel 處理器的 Mac | `aibd-<版本>-x86_64-apple-darwin` |
| Windows 10 / 11(64 位元) | `aibd-<版本>-x86_64-pc-windows-msvc.exe` |

所有檔案和 `SHA256SUMS` 都在發版頁:`https://github.com/ez2app/ez2-aib/releases/tag/aibd-v<版本>`

**檔案還沒有簽章**(Apple / Windows 簽章還沒設定),macOS 和 Windows 可能會跳警告;下面的步驟會先用
`SHA256SUMS` 核對檔案,核對通過才執行。

## macOS

```sh
V=0.2.0-preview.1                                   # 要裝的版本
T=aarch64-apple-darwin                              # Intel Mac 改成 x86_64-apple-darwin
B=https://github.com/ez2app/ez2-aib/releases/download/aibd-v$V
cd ~/Downloads
curl -fLO "$B/aibd-$V-$T" && curl -fLO "$B/SHA256SUMS"
grep " aibd-$V-$T\$" SHA256SUMS | shasum -a 256 -c -    # 一定要印 OK
chmod +x "aibd-$V-$T"
mkdir -p ~/.local/bin && mv "aibd-$V-$T" ~/.local/bin/aibd
~/.local/bin/aibd --version
~/.local/bin/aibd selfcheck
```

用瀏覽器下載(不是 curl)的話,macOS 會在檔案上做記號;核對 OK 之後用
`xattr -d com.apple.quarantine ~/.local/bin/aibd` 拿掉。

## Windows(PowerShell)

```powershell
$V = "0.2.0-preview.1"
$T = "x86_64-pc-windows-msvc"
$B = "https://github.com/ez2app/ez2-aib/releases/download/aibd-v$V"
$D = "$env:LOCALAPPDATA\aibd"; New-Item -ItemType Directory -Force $D | Out-Null
Invoke-WebRequest "$B/aibd-$V-$T.exe" -OutFile "$D\aibd.exe"
Invoke-WebRequest "$B/SHA256SUMS" -OutFile "$D\SHA256SUMS"
$want = ((Get-Content "$D\SHA256SUMS") | Where-Object { $_ -match " aibd-$V-$T\.exe$" }).Split(" ")[0]
$got  = (Get-FileHash "$D\aibd.exe" -Algorithm SHA256).Hash.ToLower()
if ($want -ne $got) { Remove-Item "$D\aibd.exe"; throw "核對不符 —— 檔案已刪除" } else { "核對 OK" }
& "$D\aibd.exe" --version
& "$D\aibd.exe" selfcheck
```

如果 SmartScreen 顯示「Windows 已保護您的電腦」,是因為檔案沒簽章;只有上面核對 OK 才繼續。

## selfcheck 在說什麼

它會列出你的平台、aibd 在這個平台能做什麼,以及每一個**還沒驗證**的部分(例如 Windows 的程序樹處理
只在 GitHub 的 Windows 機器上驗過,還沒在一般桌機上驗)。結束碼 0 代表檢查本身有跑完;要依賴某個功能前,
先看「還沒驗證」那幾行。

## 移除

macOS:`rm ~/.local/bin/aibd` · Windows:`Remove-Item -Recurse "$env:LOCALAPPDATA\aibd"`
