# aibd 0.2.0-preview.9(預覽版)

> AIB 2.0 Rust 核心的第九個預覽版。這一版補齊 **Windows 客戶版安裝與上手流程**（安裝後執行 `setup` 即寫入開機自啟並打開工作台、頁面重登、新手教學）、**省時雙系統（`aib-skipstop` 外掛目錄與三檔次切換）**、以及 **帳本併發建夾保護**。它仍與 Python `aib` 並存、不取代現有環境。

## 這一版改了什麼
- **Windows 客戶版流程（#900）**：
  - 安裝精靈／setup 流程（本頁附件為 `aibd-0.2.0-preview.9-x86_64-pc-windows-msvc.exe`，請照下方 cmd 區塊下載並核對 SHA256；Windows 雙擊安裝包另由客戶試用包提供）：檢查 Windows Hello、寫入開機自動啟動並直接打開工作台。
  - 拿掉啟動前手動設定、支援從網頁重新登入 Claude、共用卡與無 PIN 引導。
  - 新增新手教學引導（左欄專案逐步打勾與管理網站／主機引導）。
- **省時雙系統與外掛目錄（#879 / #883 / #884 / #886 / #898）**：
  - **外掛區與目錄**：支援 `~/.ez2-aib/plugins/<名>/` 外掛探索、SHA256 校驗、外掛目錄列出與一鍵安裝（`aib-skipstop` 預設開啟、隨時可關）。
  - **省時三檔次切換**：工作台支援「嚴格／一般／快速」三檔次切換，設定超出上限時會顯示提示。
  - **誠實省時顯示**：主機面板五格（本日／本週／本月／本年／至今）與獨立翻牌看板 `saved.html` 僅顯示真實牆鐘省時，「已確認」與「預估」分開計算。
  - **雙平台穩定度**：帳本目錄建立改為預設併發安全（`ensure_dir`，修復 Windows `os error 183`），並精簡主倉重複測試套件。
- 自動推進（`aib-drive`、`aib-slice`）跟著 main 更新；出廠開關仍全關。

## 這一版還沒有
- 檔案簽章（Apple / Windows）：還沒有簽章，`aibd update` 還不能直接自動驗證升級；請照下面「安裝」整段重貼。
- 從現在的 `aib` 匯入全部資料並取代。

## 支援平台
| 平台 | 檔案 |
|---|---|
| macOS(Apple 晶片 M1 以上) | `aibd-0.2.0-preview.9-aarch64-apple-darwin`、`aib-drive-0.2.0-preview.9-aarch64-apple-darwin`、`aib-slice-0.2.0-preview.9-aarch64-apple-darwin` |
| Windows x64(在 GitHub 的 Windows 機器上實測；實體電腦驗收中) | `aibd-0.2.0-preview.9-x86_64-pc-windows-msvc.exe`、`aib-drive-0.2.0-preview.9-x86_64-pc-windows-msvc.exe`、`aib-slice-0.2.0-preview.9-x86_64-pc-windows-msvc.exe` |

## 安裝
已裝 preview.1 到 preview.8 的，照下面整段重貼一次就會換成新版。**換版後請把正在跑的 aibd 關掉再開**。

**macOS(終端機,整段貼上)**
```sh
V=0.2.0-preview.9
B=https://github.com/ez2app/ez2-aib/releases/download/aibd-v$V
F=aibd-$V-aarch64-apple-darwin
curl -fLO "$B/$F" && curl -fLO "$B/SHA256SUMS" \
  && grep " $F\$" SHA256SUMS | shasum -a 256 -c - \
  && chmod +x "$F" && mkdir -p ~/.local/bin && mv "$F" ~/.local/bin/aibd \
  && ~/.local/bin/aibd --version && ~/.local/bin/aibd selfcheck
```
檢查碼不一致時會停下，不會安裝。想直接打 `aibd` 執行，要把 `~/.local/bin` 加進 PATH；不加也可以用完整路徑 `~/.local/bin/aibd`。

**Windows(命令提示字元 cmd,整段貼上;Windows 10 1803 以上內建 curl.exe 與 certutil)**
```bat
set V=0.2.0-preview.9
set B=https://github.com/ez2app/ez2-aib/releases/download/aibd-v%V%
set F=aibd-%V%-x86_64-pc-windows-msvc.exe
set D=%LOCALAPPDATA%\aibd
if not exist "%D%" mkdir "%D%"
curl.exe -fL -o "%D%\aibd.exe" "%B%/%F%" && curl.exe -fL -o "%D%\SHA256SUMS" "%B%/SHA256SUMS"
set GOT=none
for /f "delims=" %h in ('certutil -hashfile "%D%\aibd.exe" SHA256 ^| findstr /v ":"') do @set GOT=%h
findstr /b /i /c:"%GOT%  %F%" "%D%\SHA256SUMS" >nul || (del "%D%\aibd.exe" & echo checksum mismatch, download deleted)
if exist "%D%\aibd.exe" "%D%\aibd.exe" --version && "%D%\aibd.exe" selfcheck
```
檢查碼不一致時會停下並刪掉下載的檔案，不會執行。

**自動推進兩支程式(macOS,終端機,整段貼上;先照上面裝好 aibd)**
```sh
V=0.2.0-preview.9
B=https://github.com/ez2app/ez2-aib/releases/download/aibd-v$V
curl -fLO "$B/SHA256SUMS"
for T in aib-drive aib-slice; do
  F=$T-$V-aarch64-apple-darwin
  curl -fLO "$B/$F" && grep " $F\$" SHA256SUMS | shasum -a 256 -c - \
    && chmod +x "$F" && mkdir -p ~/.local/bin && mv "$F" ~/.local/bin/$T || break
done
```
檢查碼不一致時會停下，不會安裝。Windows 版的檔名見「支援平台」，這一版請照 aibd 那段的方式自己下載並比對檢查碼。

## 要知道的
- **Windows 請照本頁的 cmd 區塊下載執行**：安裝說明（INSTALL）裡 Windows 舊段為 PowerShell，本預覽版頁提供 `aibd-0.2.0-preview.9-x86_64-pc-windows-msvc.exe`；實體筆電驗收請配合檢查 Windows Hello 與開機自動啟動。
- **換版後要重開 aibd**：新的 `aibd ctl` 要搭新的 aibd。舊版 aibd 還在跑時，請關掉再開。
