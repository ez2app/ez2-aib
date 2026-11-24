# aibd 0.2.0-preview.8(預覽版)

> AIB 2.0 Rust 核心的第八個預覽版。這一版第一次附上**內建網頁**(多席工作台與聯絡中心),`aibd ctl web` 打開就能用;也第一次有 macOS 上的安裝 / 啟動 / 停止。它還不會取代你現在用的 aib,裝了也不影響現有的 aib。

## 這一版改了什麼
- **內建網頁預設開啟**:多席工作台是首頁,聯絡中心在 `/contacts.html`。`aibd ctl web` 印出網址和一組代碼,在網頁上輸入代碼就配對好。
- **終端機配對是「弱配對」**:用終端機按 y 配對的網頁,只能讀(席位、對話、輸出、狀態),不能寫;`aibd selfcheck` 會多一列 `pairing` 說明。等 Touch ID / Windows Hello 配對上線後,弱配對會全部失效。
- **macOS 上的安裝與啟動**:`aibd install`、`aibd status`、`aibd uninstall`、`aibd start`、`aibd stop`,會登記成開機自動啟動(launchd)。
- **`aibd update --file`(macOS)**:先核准再換檔、換之前不執行新檔、失敗自動換回舊檔與資料庫。**這一版發布的檔案沒有簽章,所以還不能用它升級**(它只接受簽章相同的新檔);這一版請照下面「安裝」整段重貼。
- **席位**:開席位時會直接啟動對應的 AI;席位之間可以傳訊息、開對話串、接手別席的工作;可以開 Codex 席。
- **Windows**:對話輸出、selfcheck、policy init、`ctl web` 的修正。
- **網頁版開發線**:網頁收件匣、席位之間的信件轉送(G5)、席位規劃與席位搬家、重新登入。
- 自動推進(`aib-drive`、`aib-slice`)跟著 main 更新;出廠開關仍全關。

## 這一版還沒有
- 檔案簽章(Apple / Windows):沒有簽章,`aibd update` 還不能用,也還不能用 Touch ID / Windows Hello 配對。
- 從現在的 aib 匯入全部資料、取代現在的 aib。

## 支援平台
| 平台 | 檔案 |
|---|---|
| macOS(Apple 晶片 M1 以上) | `aibd-0.2.0-preview.8-aarch64-apple-darwin`、`aib-drive-0.2.0-preview.8-aarch64-apple-darwin`、`aib-slice-0.2.0-preview.8-aarch64-apple-darwin` |
| Windows x64(在 GitHub 的 Windows 機器上實測;實體電腦驗收中) | `aibd-0.2.0-preview.8-x86_64-pc-windows-msvc.exe`、`aib-drive-0.2.0-preview.8-x86_64-pc-windows-msvc.exe`、`aib-slice-0.2.0-preview.8-x86_64-pc-windows-msvc.exe` |

## 安裝
已裝 preview.1 到 preview.7 的,照下面整段重貼一次就會換成新版。**換版後請把正在跑的 aibd 關掉再開**(見「要知道的」第一條),**並先看第二條:有些人要在名單裡補變數名,Claude 才登得進去**。

**macOS(終端機,整段貼上)**
```sh
V=0.2.0-preview.8
B=https://github.com/ez2app/ez2-aib/releases/download/aibd-v$V
F=aibd-$V-aarch64-apple-darwin
curl -fLO "$B/$F" && curl -fLO "$B/SHA256SUMS" \
  && grep " $F\$" SHA256SUMS | shasum -a 256 -c - \
  && chmod +x "$F" && mkdir -p ~/.local/bin && mv "$F" ~/.local/bin/aibd \
  && ~/.local/bin/aibd --version && ~/.local/bin/aibd selfcheck
```
檢查碼不一致時會停下,不會安裝。想直接打 `aibd` 執行,要把 `~/.local/bin` 加進 PATH;不加也可以用完整路徑 `~/.local/bin/aibd`。

**Windows(命令提示字元 cmd,整段貼上;Windows 10 1803 以上內建 curl.exe 與 certutil)**
```bat
set V=0.2.0-preview.8
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
檢查碼不一致時會停下並刪掉下載的檔案,不會執行。

**自動推進兩支程式(macOS,終端機,整段貼上;先照上面裝好 aibd)**
```sh
V=0.2.0-preview.8
B=https://github.com/ez2app/ez2-aib/releases/download/aibd-v$V
curl -fLO "$B/SHA256SUMS"
for T in aib-drive aib-slice; do
  F=$T-$V-aarch64-apple-darwin
  curl -fLO "$B/$F" && grep " $F\$" SHA256SUMS | shasum -a 256 -c - \
    && chmod +x "$F" && mkdir -p ~/.local/bin && mv "$F" ~/.local/bin/$T || break
done
```
檢查碼不一致時會停下,不會安裝。Windows 版的檔名見「支援平台」,這一版請照 aibd 那段的方式自己下載並比對檢查碼。

## 試一次(macOS)
```sh
aibd ctl policy init                      # 找到本機 claude(要官方簽章的單一執行檔，不收 npm 版),寫允許名單;也會印出可能要補進名單的變數名
aibd &                                    # 這一版起：沒帶名單路徑，會讀 policy init 寫在 aibd 資料夾的那份
aibd ctl seat open demo -- ~/.local/bin/claude -p "用一句話說今天天氣" --output-format stream-json --verbose --setting-sources ""   # 建席位 + 開對話一步完成
aibd ctl watch <session-id>               # 一行一行即時看這個對話的輸出
aibd ctl stop <session-id>                # 收掉(連它開的程式一起收);還在排隊的會直接取消
aibd ctl list                             # 關掉 aibd 再開，席位與對話都還在
```

## 試一次自動推進(macOS,先照安裝說明裝好)
```sh
aib-drive refresh --repo <repo 資料夾>     # 更新分支 / PR 的快取
aib-drive next                            # 現在能做的事，最該做的排前面(只讀)
aib-drive act --auto                      # 跑一輪：開關全關時不寄信，只記下這一輪本來會寄哪些
aib-drive switch nudge on --by <你的席碼> --why "<為什麼>"   # 打開「提醒」這種信
```

## 要知道的
- **暫停新工作**:`aib-drive switch paused on --by <席碼> --why "<為什麼>"`,開關保持原樣，開審查、修正通知先不寄，提醒照寄;`paused off` 恢復。要全部不寄，就把各個開關關掉。
- **Windows 請照本頁的 cmd 區塊裝**:安裝說明(INSTALL)裡 Windows 那段還是舊寫法(PowerShell),這一版先照本頁「安裝」的 cmd 區塊裝。
- **自動推進寄出的信，寄件人這一版顯示「未認證@unknown」**:提醒、通知修、開審查這三種信，收信的 AI 席可能只把它當資料參考、不照著做。下一版會改成寄件人「系統@aib」,並在信尾寫明是自動推進代發。
- **不會轟炸在忙的席**:提醒、開審查在對方回合中也會寄，但同一席 30 分鐘最多一封、未讀 10 封以上就不寄。
- **換版後要重開 aibd**:新的 `aibd ctl` 要搭新的 aibd。舊版 aibd 還在跑時，請關掉再開。
- **升級後 Claude 可能要你在名單補變數名**:開出來的程式現在只拿到基本變數。如果你的 Claude 是用 API 金鑰、Bedrock 或 Vertex 登入，透過代理伺服器或公司憑證上網、把設定資料夾搬到別處，或對話要用 ssh-agent,就要把那些變數名加進名單檔的 `env`。`aibd ctl policy init` 會印出這台機器上有設的那些名字;**已經有的名單檔不會被改寫，要自己手動加**。名單只決定「哪些名字可以傳」,值是 aibd 自己環境裡的值:如果 aibd 不是從終端機啟動(例如開機自動啟動),要確認它自己的環境也有同樣的值。Mac 上大小寫算不同名字,`HTTP_PROXY` 和 `http_proxy` 是兩條，兩條都要寫。
- **只准開名單上的程式**:沒有名單就什麼都不開;名單裡寫了 shell、直譯器也一律拒絕(把 shell 複製改名的，這一版擋不住，見「不驗簽章」那條)。每次被拒都會記下(一分鐘內同一種合併成一筆、記次數)。
- **名單只比對路徑，不驗簽章**:`policy init` 找 claude 時會看官方簽章，但之後開程式只比對名單裡的路徑;那個路徑的檔案被換掉，這一版不會發現。
- **輸出存在你自己的 aibd 資料夾**:常見格式的金鑰會先遮住，但只認固定開頭，其他格式的秘密仍可能原樣存下。資料庫只有你能讀寫(0600)、資料夾 0700。
- **Mac 上少數情況名額仍會卡住**:如果某個程序在它的建立者結束之後才啟動另一支程式,aibd 查不到它是誰開的，只會標成「無法確認」(`candidates_unreadable`)—— 不會對它送訊號、會保留名額，也不會把這棵樹報成已收乾淨。這時新的對話會排隊，不會被拒;要馬上放名額就重啟 aibd,或啟動時帶 `AIBD_MAX_SESSIONS=32` 把上限調高。
- **正常關掉 aibd 再開，已結束或還在排隊的對話可能顯示「被中斷」**:結束時沒能確認收乾淨的，重開時照實標成被中斷。
- **aibd 被強制結束(kill -9)時,macOS 上它開的程式可能留著**:這種情況 aibd 來不及收;請自己看一下有沒有殘留。
