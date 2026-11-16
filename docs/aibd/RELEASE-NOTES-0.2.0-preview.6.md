# aibd 0.2.0-preview.6(預覽版)

> AIB 2.0 Rust 核心的第六個預覽版。這一版重點是**開出來的程式不再拿到 aibd 的整份環境變數**、**輸出裡常見的金鑰會先遮住再存**,以及**同一個請求重送不會開兩次**;Mac 上另外修了幾個名額卡住、開對話卡住的情況。它還不會取代你現在用的 aib,裝了也不影響現有的 aib。

## 這一版改了什麼
- **開出來的程式只拿到必要的環境變數**:以前它看得到你啟動 aibd 時環境裡的全部東西(包含金鑰)。這一版只給一組固定的基本變數(`PATH`、`HOME`、語系、`TERM`、暫存資料夾;Windows 另加系統資料夾、使用者資料夾與 App 資料路徑),再加上名單檔裡 `env` 列出的名字。aibd 自己有的金鑰,名單沒寫就不會給。**升級要注意**:見「要知道的」第二條。<!-- src: CHANGELOG "A session's environment and output are narrowed (T80)" / "the child no longer inherits aibd's whole environment" -->
- **`aibd ctl policy init` 會列出你可能要補的變數名**:它會印出這台機器上有設、而 Claude 可能會用到的變數名字(只印名字，不印值),例如 `ANTHROPIC_API_KEY`、代理伺服器、公司憑證、`CLAUDE_CONFIG_DIR`、`SSH_AUTH_SOCK` 等。<!-- src: CHANGELOG "`aibd ctl policy init` now lists the ones set where it runs" -->
- **啟動時不帶名單路徑也可以**:沒設 `AIBD_SESSION_POLICY` 時,aibd 會讀自己資料夾裡 `policy init` 寫的那份 `session-policy.json`(每個平台都一樣)。有設的話仍以你設的為準;預設那份寫壞了或讀不到,會跟指定的名單壞掉一樣停下不啟動。<!-- src: CHANGELOG "with `AIBD_SESSION_POLICY` unset, aibd reads `session-policy.json`" -->
- **輸出裡常見的金鑰先遮住再存**:程式印出的 `sk-ant-`、`sk-`、GitHub token(`ghp_` 等)、`glpat-`、Slack `xox?-`、AWS `AKIA` / `ASIA`、Google `AIza`、JWT、`Bearer …` 這類字串，存進資料庫前會改成「開頭 + `***`」。只認固定開頭，所以 Claude 自己的編號照樣看得到;盡力而為，不保證抓到全部。<!-- src: CHANGELOG "known key formats a program prints" -->
- **同一個請求重送不會開兩次**:開對話、停對話的請求可以帶 `Idempotency-Key`。網路斷掉重送同一把鑰匙，不會再開一次或再停一次，會拿到第一次的結果(連 aibd 重開後也一樣)。同一把鑰匙拿去送別的請求、或第一次還在處理中，會回 `409 idempotency_conflict`。<!-- src: CHANGELOG "`Idempotency-Key` on the session writes (ADR 0003; T81)" -->
- **Mac:同時開兩個對話，不會一個卡到另一個結束**:以前兩個對話一起開，偶爾有一個要等另一個的程式跑完才回應。這一版同一個 aibd 裡的開對話改成一個接一個(每個只佔幾毫秒)。<!-- src: CHANGELOG "macOS: one held spawn at a time" -->
- **Mac:程式剛好在開跑瞬間結束，不再永久佔住名額**:以前這種情況對話會停在「無法確認已停止」,名額一直不放、新對話被拒。這一版會改用另一種方式確認並放回名額。<!-- src: CHANGELOG "macOS: a session whose child exited between aibd reading" -->
- **Mac:清空環境變數啟動的程式也認得出來**:用 `env -i` 這類方式開的程式，以前會被誤判成「不是這個對話的」而放走;這一版會照實列出來。<!-- src: CHANGELOG "macOS: a late-exec candidate's environment is read by `aib-sys`" -->
- **Mac:當機後馬上重開 aibd 比較不會被誤擋**:重開時最多等 6 秒讓舊的程式放掉鎖，才說「已經在跑」。真的有另一個 aibd 在跑時，仍然會拒絕(6 秒後)。其他平台不等。<!-- src: CHANGELOG "macOS: a start waits up to `HOLD_LIMIT` + 1 s" -->
- **Mac:過熱保護先「只記錄、不動作」**:有對話在跑時,aibd 每 5 秒量一次 CPU 溫度與系統的過熱壓力，結果放在 `GET /api/v1/metrics` 的 `thermal`,並記下「如果開啟保護，這時會停掉哪個對話 / 會暫停開新對話」。這一版**不會**真的停任何對話，也不會拒絕開新對話。沒有感測器或不是 Mac 時,`thermal` 是 `null`。<!-- src: CHANGELOG "Thermal guard, dry run (R1 PR 3a step 2" -->
- 沿用 preview.5:名額滿了排隊、一步開席(`aibd ctl seat open`)、健康檢查分得出重開過、只准開名單上的程式、被拒都留紀錄。

## 這一版還沒有
- 網頁操作畫面：瀏覽器配對的介面已經在程式裡，但預設關閉，發布的執行檔也沒有編進網頁檔案;這一版請不要開。
- 在對話裡打字回它(互動輸入)、角色 / 人格、從現在的 aib 匯入資料。
- 過熱保護：這一版在 Mac 上會量溫度並記錄「本來會怎麼做」,但還不會因為過熱而停對話或暫停開對話。<!-- src: CHANGELOG "It **only records**" -->
- Windows:可以裝;`aibd ctl policy init` 這一版只在 Mac 能用,Windows 要自己寫名單檔(放在 aibd 資料夾的 `session-policy.json` 就不用另外設路徑);對話輸出這一版在 Windows 不收，開 Claude 對話只在 Mac 驗過。<!-- src: CHANGELOG "reads `session-policy.json` from its data folder ... on every platform" -->

## 支援平台
| 平台 | 檔案 |
|---|---|
| macOS(Apple 晶片 M1 以上) | `aibd-0.2.0-preview.6-aarch64-apple-darwin` |
| Windows x64(在 GitHub 的 Windows 機器上實測;實體電腦驗收中) | `aibd-0.2.0-preview.6-x86_64-pc-windows-msvc.exe` |

## 安裝
已裝 preview.1 到 preview.5 的,照下面整段重貼一次就會換成新版。**換版後請把正在跑的 aibd 關掉再開**(見「要知道的」第一條),**並先看第二條:有些人要在名單裡補變數名,Claude 才登得進去**。

**macOS(終端機,整段貼上)**
```sh
V=0.2.0-preview.6
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
set V=0.2.0-preview.6
set B=https://github.com/ez2app/ez2-aib/releases/download/aibd-v%V%
set F=aibd-%V%-x86_64-pc-windows-msvc.exe
set D=%LOCALAPPDATA%\aibd
if not exist "%D%" mkdir "%D%"
curl.exe -fL -o "%D%\aibd.exe" "%B%/%F%" && curl.exe -fL -o "%D%\SHA256SUMS" "%B%/SHA256SUMS"
set GOT=none
for /f "delims=" %h in ('certutil -hashfile "%D%\aibd.exe" SHA256 ^| findstr /v ":"') do @set GOT=%h
findstr /b /i /c:"%GOT%  %F%" "%D%\SHA256SUMS" >nul || (del "%D%\aibd.exe" & echo 檢查碼不一致,已刪除下載檔)
if exist "%D%\aibd.exe" "%D%\aibd.exe" --version && "%D%\aibd.exe" selfcheck
```
檢查碼不一致時會停下並刪掉下載的檔案,不會執行。

## 試一次(macOS)
```sh
aibd ctl policy init                      # 找到本機 claude(要官方簽章的單一執行檔，不收 npm 版),寫允許名單;也會印出可能要補進名單的變數名
aibd &                                    # 這一版起：沒帶名單路徑，會讀 policy init 寫在 aibd 資料夾的那份
aibd ctl seat open demo -- ~/.local/bin/claude -p "用一句話說今天天氣" --output-format stream-json --verbose --setting-sources ""   # 建席位 + 開對話一步完成
aibd ctl watch <session-id>               # 一行一行即時看這個對話的輸出
aibd ctl stop <session-id>                # 收掉(連它開的程式一起收);還在排隊的會直接取消
aibd ctl list                             # 關掉 aibd 再開，席位與對話都還在
```
<!-- src: CHANGELOG "with `AIBD_SESSION_POLICY` unset, aibd reads `session-policy.json`" -->

## 要知道的
- **換版後要重開 aibd**:新的 `aibd ctl` 要搭新的 aibd。舊版 aibd 還在跑時，請關掉再開。
- **升級後 Claude 可能要你在名單補變數名**:開出來的程式現在只拿到基本變數。如果你的 Claude 是用 API 金鑰、Bedrock 或 Vertex 登入，透過代理伺服器或公司憑證上網、把設定資料夾搬到別處，或對話要用 ssh-agent,就要把那些變數名加進名單檔的 `env`。`aibd ctl policy init` 會印出這台機器上有設的那些名字;**已經有的名單檔不會被改寫，要自己手動加**。名單只決定「哪些名字可以傳」,值是 aibd 自己環境裡的值:如果 aibd 不是從終端機啟動(例如開機自動啟動),要確認它自己的環境也有同樣的值。Mac 上大小寫算不同名字,`HTTP_PROXY` 和 `http_proxy` 是兩條，兩條都要寫。<!-- src: CHANGELOG "**Upgrading:** a claude that logs in with an API key" / "An existing policy file is not rewritten" / "On macOS and Linux names have case" -->
- **只准開名單上的程式**:沒有名單就什麼都不開;名單裡寫了 shell、直譯器也一律拒絕(把 shell 複製改名的，這一版擋不住，見「不驗簽章」那條)。每次被拒都會記下(一分鐘內同一種合併成一筆、記次數)。
- **名單只比對路徑，不驗簽章**:`policy init` 找 claude 時會看官方簽章，但之後開程式只比對名單裡的路徑;那個路徑的檔案被換掉，這一版不會發現。
- **輸出存在你自己的 aibd 資料夾**:常見格式的金鑰會先遮住，但只認固定開頭，其他格式的秘密仍可能原樣存下。資料庫只有你能讀寫(0600)、資料夾 0700。<!-- src: CHANGELOG "known key formats a program prints ... Fixed prefixes only ... best effort" -->
- **Mac 上少數情況名額仍會卡住**:如果某個程序在它的建立者結束之後才啟動另一支程式,aibd 查不到它是誰開的，只會標成「無法確認」(`candidates_unreadable`)—— 不會對它送訊號、會保留名額，也不會把這棵樹報成已收乾淨。這時新的對話會排隊，不會被拒;要馬上放名額就重啟 aibd,或啟動時帶 `AIBD_MAX_SESSIONS=32` 把上限調高。
- **正常關掉 aibd 再開，已結束或還在排隊的對話可能顯示「被中斷」**:結束時沒能確認收乾淨的，重開時照實標成被中斷。
- **aibd 被強制結束(kill -9)時,macOS 上它開的程式可能留著**:這種情況 aibd 來不及收;請自己看一下有沒有殘留。
