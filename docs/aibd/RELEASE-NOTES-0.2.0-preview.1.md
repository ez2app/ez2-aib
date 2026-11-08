# aibd 0.2.0-preview.1(預覽版)

> 這是 AIB 2.0 Rust 核心的第一個預覽版，給想先試的人。它還不會取代你現在用的 aib,裝了也不影響現有的 aib。

## 有什麼
- **aibd**:在你電腦上跑的背景服務，只接受本機連線。健康狀態不用權杖;事件、量測數字要權杖。
- **`aibd --version`**:看版本。
- **`aibd selfcheck`**:檢查這台電腦支援哪些功能。沒有驗證過的項目會直接寫「未驗證」,不會假裝沒問題。
- 本機 API 三個端點：健康狀態、事件、量測數字。

## 支援平台
| 平台 | 檔案 |
|---|---|
| macOS(Apple 晶片 M1 以上) | `aibd-0.2.0-preview.1-aarch64-apple-darwin` |
| Windows x64(在 GitHub 的 Windows 機器上實測;實體電腦驗收中) | `aibd-0.2.0-preview.1-x86_64-pc-windows-msvc.exe` |

下載的是單一執行檔，不用解壓。macOS Intel 版這次沒有，之後補。

## 安裝

**macOS(終端機)**
```sh
V=0.2.0-preview.1
B=https://github.com/ez2app/ez2-aib/releases/download/aibd-v$V
curl -fLO "$B/aibd-$V-aarch64-apple-darwin"
curl -fLO "$B/SHA256SUMS"
grep " aibd-$V-aarch64-apple-darwin\$" SHA256SUMS | shasum -a 256 -c -
chmod +x "aibd-$V-aarch64-apple-darwin"
mkdir -p ~/.local/bin && mv "aibd-$V-aarch64-apple-darwin" ~/.local/bin/aibd
~/.local/bin/aibd --version
~/.local/bin/aibd selfcheck
```

**Windows(PowerShell)**
```powershell
$V = "0.2.0-preview.1"
$B = "https://github.com/ez2app/ez2-aib/releases/download/aibd-v$V"
$D = "$env:LOCALAPPDATA\aibd"; New-Item -ItemType Directory -Force $D | Out-Null
Invoke-WebRequest "$B/aibd-$V-x86_64-pc-windows-msvc.exe" -OutFile "$D\aibd.exe"
Invoke-WebRequest "$B/SHA256SUMS" -OutFile "$D\SHA256SUMS"
$want = ((Select-String "x86_64-pc-windows-msvc" "$D\SHA256SUMS").Line -split " ")[0]
$got = (Get-FileHash "$D\aibd.exe" -Algorithm SHA256).Hash.ToLower()
if ($want -ne $got) { Remove-Item "$D\aibd.exe"; throw "檢查碼不一致，已刪除下載檔" }
& "$D\aibd.exe" --version
& "$D\aibd.exe" selfcheck
```
檢查碼不一致時會直接停下並刪掉下載的檔案，不會執行。

## 請先知道
- **預覽版**:功能還少，介面之後可能會變。
- **還沒簽章**:從瀏覽器下載的話,macOS 可能跳出安全提示;Windows 可能出現 SmartScreen 或防毒提醒。
- **還沒有的**:用 aibd 開 / 管 AI 席位的程序(程式已寫好、還沒接上 aibd)、網頁工作台、取代現有 aib 的完整功能。

## 檢查碼
SHA256SUMS 附在下載檔旁邊。
