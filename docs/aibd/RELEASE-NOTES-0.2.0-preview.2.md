# aibd 0.2.0-preview.2(預覽版)

> AIB 2.0 Rust 核心的第二個預覽版。這一版開始能**用 aibd 開一個程式、看它的狀態、把它收掉**。它還不會取代你現在用的 aib,裝了也不影響現有的 aib。

## 有什麼
- **新的：開程式、看狀態、收掉**:透過本機 API 叫 aibd 開一個程式;隨時可以看它還在跑、已結束(含結束碼)、還是被收掉。收掉時會連它再開出來的程式一起收，收完會照實回報結果：有沒收掉的會列出來，不會說成已收乾淨。
- **aibd 正常關掉時(含按 Ctrl-C)會去收它開的程式**,結果同樣照實記下。
- 程式自己先結束、留下它開的背景程式時,aibd 也會去收一次。
- **`aibd selfcheck`**:「程序管理」這一項現在會實際開一個小程式再收掉來量。macOS 上收乾淨時顯示「未證實」(外部看已經收乾淨,但系統沒辦法證明一個不漏);有收不掉的會顯示失敗並寫出還剩幾個。Windows 的 selfcheck 這一項仍顯示「還沒接上」;開程式 / 收程式本身已可用。
- 沿用 preview.1:`aibd --version`、健康狀態(不用權杖)、事件與量測數字(要權杖);只接受本機連線。開程式、收程式也要權杖。

## 支援平台
| 平台 | 檔案 |
|---|---|
| macOS(Apple 晶片 M1 以上) | `aibd-0.2.0-preview.2-aarch64-apple-darwin` |
| Windows x64(在 GitHub 的 Windows 機器上實測;實體電腦驗收中) | `aibd-0.2.0-preview.2-x86_64-pc-windows-msvc.exe` |

下載的是單一執行檔，不用解壓。macOS Intel 版這次也還沒有。

## 安裝
已裝 preview.1 的，照下面整段重貼一次就會換成新版。

**macOS(終端機，整段貼上)**
```sh
V=0.2.0-preview.2
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
$V = "0.2.0-preview.2"
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

## 怎麼試開程式
aibd 開著時,第一行會印出它聽的位址和權杖檔的位置(`{"listening":"<位址>","token_file":"<權杖檔>"}`),權杖就是權杖檔的內容。下面用 macOS 終端機示範(把 `<位址>`、`<權杖>` 換成你的):

開一個程式(這裡用 `/bin/sleep 60` 當例子),會回 `{"id":"s_…","pid":…,"state":"running",…}`:
```sh
curl -s -X POST http://<位址>/api/v1/sessions -H "Authorization: Bearer <權杖>" -H "Content-Type: application/json" -d '{"argv":["/bin/sleep","60"]}'
```
看狀態:
```sh
curl -s http://<位址>/api/v1/sessions/<id> -H "Authorization: Bearer <權杖>"
```
收掉(連它開出來的程式一起收;回應裡的 `stop` 會寫收的結果，macOS 上狀態會是 `stop_unconfirmed`):
```sh
curl -s -X DELETE http://<位址>/api/v1/sessions/<id> -H "Authorization: Bearer <權杖>"
```
權杖只給自己用，不要貼到別處。

## 從瀏覽器下載被擋住時
- **macOS**:出現「無法打開」時，在終端機執行 `xattr -d com.apple.quarantine <檔案路徑>`,或在 Finder 對檔案按右鍵 → 打開。
- **Windows**:對檔案按右鍵 → 內容 → 勾「解除封鎖」;SmartScreen 出現時按「其他資訊」→「仍要執行」。請先照上面的步驟比對檢查碼再這樣做。

## 請先知道
- **預覽版**:功能還少，介面之後可能會變。
- **還沒簽章**:從瀏覽器下載的話,macOS 可能跳出安全提示;Windows 可能出現 SmartScreen 或防毒提醒。
- **開出來的程式**:沒有終端機畫面(看不到它印的字,輸出直接丟掉);結束的紀錄只留最近 100 筆,aibd 重開就沒了;aibd 重開後不會接回之前開的程式;同時最多 8 個，可用 `AIBD_MAX_SESSIONS` 調成 1–64。程式要用完整路徑指定(例 `/bin/sleep`,不能只寫 `sleep`)。
- **收程式的限制**:
  - macOS 上收完的狀態會是 `stop_unconfirmed`、結果標「未證實」(看得到的都收了，但系統沒辦法證明一個不漏),不會顯示 `killed`;`stop.survivors` 是空的就代表沒看到殘留。有收不掉的會標出來並列出。只有 Windows 證明收乾淨時才顯示 `killed`。
  - 收完沒辦法完整檢查(例如讀不到某些程序)時,那一筆先繼續佔一個名額,不會假裝已經結束;aibd 會在背景每 2 秒重新檢查一次、最多 30 秒,看乾淨了就還名額。30 秒後仍讀不到就繼續佔著,並在事件裡寫明原因。
  - 刻意脫離的程式(自己另開一組、或清掉 aibd 給的標記)可能收不到。
  - aibd 被強制結束(例如「強制結束」、kill -9、當機)時，它開的程式在 macOS 上會留著，要自己關。
  - Windows 由系統代收;這部分在實體電腦還沒驗完。
  - Windows 上 aibd 只處理 Ctrl-C:直接關掉視窗、登出或關機時，不會跑正常的關閉流程;不過它開的程式仍會跟著 aibd 結束，一起被系統收掉。
- **還沒有的**:用 aibd 開 AI 席位(人格、對話畫面)、網頁工作台、取代現有 aib 的完整功能。

## 檢查碼
SHA256SUMS 附在下載檔旁邊。它只能確認你下載的檔案完整、沒有損壞，不是數位簽章。
