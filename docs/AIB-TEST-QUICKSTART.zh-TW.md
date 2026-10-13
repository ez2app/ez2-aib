[English](https://github.com/ez2app/ez2-aib/blob/main/docs/AIB-TEST-QUICKSTART.md) | [繁體中文](https://github.com/ez2app/ez2-aib/blob/main/docs/AIB-TEST-QUICKSTART.zh-TW.md) | [日本語](https://github.com/ez2app/ez2-aib/blob/main/docs/AIB-TEST-QUICKSTART.ja.md) | [한국어](https://github.com/ez2app/ez2-aib/blob/main/docs/AIB-TEST-QUICKSTART.ko.md)

# `aib test` 入門:只重跑有變的測試

> 給第一次用的人。一句話:**測試讀過的檔、執行環境都沒變,就沿用上次的綠燈;紅燈永遠重跑;量不到的一律重跑。**
> 所以用錯最多只是「省不到」,不會把壞掉的程式說成好的。

## 1. 怎麼跑

```bash
aib test tests/test_foo.py tests/test_bar.py      # 跑這幾支(能沿用就沿用)
aib test --fresh tests/test_foo.py                # 這次全部實跑(照樣留紀錄)
aib test --why tests/test_foo.py                  # 不跑,只說會不會沿用、卡在哪個檔
aib test --repo ~/my-project tests/test_x.py      # 在別的倉裡跑
aib test --estimate                              # 不跑,用這個倉最近 50 個提交估每個提交能省多少(要先跑過一次)
```

畫面每支一行:`✓ 沿用`、`▶ 實跑`、`└ 綠/✗ 紅`;最後一行合計。紅的會把輸出印在總表下面。

### 用在自己的專案(有自己的虛擬環境)

```bash
cd ~/my-project
source .venv/bin/activate                          # 或:倉裡有 .venv/ 或 venv/ 就自動用,不必啟用
pip install pytest -e .                            # 專案與測試要用的套件都裝在這個環境裡
aib test --repo . tests/test_*.py
```

用哪一支 python:`AIB_TEST_PYTHON=<路徑>` > 倉裡的 `.venv/`、`venv/` > 已啟用的 `VIRTUAL_ENV` > aib 自己的。
不是 aib 自己那支時,開頭會印一行 `ℹ 測試用的 python:…(版本;pytest 版本;來源)`。
那支 python 的版本、有沒有 pytest 都算「執行環境」:換了就全部重跑。套件升版也會(它讀的套件檔內容變了)。
實測(10-04,itsdangerous 5 支):第一輪 3.2 秒全實跑;第二輪 0.4 秒全沿用;改 `signer.py` 一行 ⇒ 5 支全部重跑。

外部專案實測(10 個,紅綠全部跟直接跑 pytest 一致):itsdangerous、markupsafe、more-itertools、click、dateutil、
Flask(19.5 → 5.3 秒)、requests、django-taggit(46 → 12 秒)、pytest-xdist(99 → 74 秒)、humanize(12 → 2.4 秒)。
都是函式庫、只在 Linux 量過;細節與實測抓到的問題見 docs/DESIGN-aib-ship-for-all-users-1006.md §26。

## 2. 支援哪些測試

| 寫法 | 怎麼跑 | 狀態 |
|---|---|---|
| Python,檔尾有 `if __name__ == "__main__":` | 直接執行 | 穩定 |
| Python,pytest 寫法(沒有上面那段;含 `class TestXxx:`、`async def test_`) | 那支 python 有 pytest 就用 pytest;沒有就用內建的小執行器跑 `test_*` 函式 | 可用;用到 pytest fixture 又沒裝 pytest ⇒ 判紅並說要裝 |
| Node(`.js` `.mjs` `.cjs`,用 `node:test`) | `node <檔>` | 第一版(共用驗收 12 格全過) |
| JS/TS,用 jest / mocha(看倉根 package.json,`node_modules/.bin/` 要裝好) | jest:`--runInBand --no-watchman`,一次一支;mocha:`mocha <檔>` | 可用(10-07:luxon 23 支、cliui 紅綠與直接跑框架一致,第二輪綠的全沿用) |
| JS/TS,用 vitest | `vitest run --pool=forks <檔>` | 跑得對但**量不到、每次重跑**:vitest 4 / vite 8 用原生程式找檔,追蹤器看不到 |
| bash 腳本(被 Python/Node 測試叫起來的) | 跟著父行程 | 穩定 |
| Go(倉根有 `go.mod`;`*_test.go`) | `go test ./<那個檔的資料夾>`,借 Go 自己的快取(用這台真正的 GOCACHE / GOMODCACHE) | 可用(10-07,google/uuid:每支約 0.25 秒、改壞 ⇒ 全紅)。以套件為單位:同資料夾的檔一起紅綠;模組要先下載好(`go mod download`) |
| PHP(composer.json 用 phpunit;`tests/` 底下的 `*Test.php`) | `php vendor/bin/phpunit <檔>`(先 `composer install`) | 可用(10-07,sebastian/diff 17 支,紅綠與直接跑 phpunit 一致);追蹤器看不懂 ⇒ 每次實跑 |
| 其他語言(Ruby、Rust、Java…) | 在 `.aib/ship.toml` 宣告:`[test]` `files = ["spec/**/*_spec.rb"]`、`command = "bundle exec rspec {file}"`(`{file}` 換成那支的相對路徑)| 追蹤器看不懂 ⇒ 每次實跑,但推包、發版、原因排行都能用;Ruby(minitest/rspec)、Go、JS 的總結行認得「一格都沒跑」⇒ 列成沒驗(10-07 minitest 實測)|

## 3. 什麼時候會沿用、什麼時候一定重跑

一定重跑:
- 上次是紅的、逾時、或結果時好時壞(同樣條件紅過)。
- 測試讀過的任何檔內容變了、列過的目錄多了或少了檔、問過不存在的檔現在有了。
- 執行環境變了(語言版本、環境變數、套件鎖檔)。
- 碰到網路、或起了量不到的程式(沒有接頭的語言、被砍掉的子行程)。
  資料庫:C 寫的驅動(psycopg2、mysqlclient、grpc、pyzmq、pycurl…)自己開連線、不經過 Python 的 socket,
  所以一載入就算碰網路(10-07 起;以前漏了 ⇒ 資料庫內容變了照樣沿用)。純 Python 的客戶端走 socket,本來就抓得到。
- 紀錄過期:第一次 3 天;到期重跑還是綠,期限加倍(6、12 天),最多 14 天。
- 測試讀的檔裡寫著日期,而從上次到現在跨過了那一天。

**翻譯檔以字串為單位(10-06,不用設定)**:專案用 gettext 翻譯(Django、Flask-Babel、babel 都是)時,
只透過 gettext 查字的測試,只有它**用到的那幾句**譯文變了才重跑;改別句、加新句 ⇒ 沿用。
測試自己直接讀 .mo、列舉整份目錄表、或翻譯檔壞掉 ⇒ 照舊整份算(重跑)。.mo 是從 .po 編出來的:改了 .po 要先重編 .mo。

字典放 JSON、自己寫查字函式的專案(10-12 起**自動**,不用設定):`locales/`、`i18n/`、`lang/`、`messages/`、`translations/`
底下(含一層子資料夾,例如 `locales/en/common.json`)、內容是「鍵 → 字串」字典的 `*.json`,自動以鍵為單位。
測試用 `json.load(檔案)` 讀進來、只取用到的鍵 ⇒ 只有那些鍵(連同底下整棵值)變了才重跑;改別的鍵、加新鍵 ⇒ 沿用。
自動模式**不認**「先 `read()` 出原文再 `json.loads`」:原文在測試手上,可能拿去比快照、交給別的程式,那是整份都用到 ⇒ 照整份算。
拿整份去比較、列舉、複製、轉字串,或內容在解析前被改過(例如 Windows 換行被轉)⇒ 照舊整份算。
數字、布林、陣列、空物件這類設定檔樣子的 JSON 不會被當成字典。推前檢查改到這些檔時會印「以字串為單位 —— 少跑 N 支」。

字典放在別的地方,或要關掉:在 `.aib/ship.toml` 宣告(相對倉根的樣式;寫了就**完全照寫的**,不再自動找;
宣告的檔 `json.loads(原文)` 也認 —— 你確定測試不會另外拿原文去用才這樣宣告):

```toml
[test]
keyed_files = ["data/strings/*.json"]   # 改成這些
# keyed_files = []                      # 關掉(全部照整檔算)
```

改到一份看起來像翻譯字典、但不在上面位置的 JSON 時,推前檢查會印一行教你怎麼宣告。
目前只認 Python 讀的(`json.load` / `json.loads`);**JS 專案(例如 i18next 用 node 讀 JSON)照整檔算**。

## 4. 三個開關

- 這一輪全部實跑:`AIB_TEST_NO_REUSE=1 aib test …`
- 某支永遠實跑:測試檔前 40 行寫 `# aib: always-run`
- 這台做不到的就別跑(標「⊘ 沒驗」,不算綠也不算紅):測試檔前 40 行寫
  `# aib: needs darwin`、`needs linux`、`needs ipv6`、`needs tool:<程式名>`、`needs repo:<旁邊的倉名>`
  ★ 條件只能寫「做這件事的前提」,不能寫「要驗的那件事本身」,否則缺的時候不跑就等於藏起來。

## 5. 看得到的「沒驗」

- `⊘ 沒驗`:這台缺條件,沒跑。總表另列一行,整批結束碼 4。
- `⊘ 其中 N 格跳過、沒驗`:綠燈裡有被跳過的格子(`skip`)。總表另列一行。
- 結束碼:0 全綠;1 有紅;3 紀錄沒留住;4 有沒驗的。

## 6. 推主線與發版:`aib ship`(10-06,任何專案都能用,不用設定)

給用戶看的白話說明(每次會看到什麼、怎麼讀、怎麼省最多):`docs/AIB-SHIP-FOR-USERS.zh-TW.md`。

```bash
cd ~/my-project
aib ship init                     # 新用戶第一步:列專案名稱與專案樹、找版號與測試、檢查測的是不是這棵樹、全部跑一次建紀錄、
                                  #   估之後每次能省多少、列牽動最多測試的檔;不推、不提交(--no-run 只偵測)
aib ship package --preview        # 不跑不推:預計沿用幾支、要多久、主因是哪個檔
aib ship hotfiles                 # 很多測試都會讀、又常被改的 10 個檔(讀的支數 × 最近改的次數):它們的改動攢起來一起推最省
aib ship package                  # 推一包到 origin 的 main:版號規則 → 修正要配測試 → 推前檢查 → 推
aib ship watch                    # 開發時掛著:存檔停 10 秒就在鏡像樹先跑(不碰你的工作目錄),推的時候直接沿用
aib ship stats                    # 從第一次起的總計帳(總支數/總格數、每一次與每週實際省多少)+ 什麼情況下推會省最多、要做什麼
# 推前檢查一開始先比:沒有 aib(一支一支跑)要多久、有 aib 這台預計多久(幾點完成)、加開機器推算;跑完再對帳(同時跑省下 / 影子續跑再省)
aib ship stats --all              # 這台機器每個專案一行(總支數/總格數、推幾次、省多少)+ 全部合計
aib ship audit                    # 抽幾支「沿用」的測試真的重跑、比對結果(沿用對不對);結果記進帳,每週底下列出
aib ship stale [--run]            # 超過 7 天沒實跑過的測試(推前檢查只挑受影響的,沒被碰到的會很久沒人跑,紅藏在這裡);
                                  #   --run 一次全跑。建議每週跑一次(10-07:補跑才發現 5 支一直紅、14 支從沒在雲端跑過)
aib ship why                      # 「每次都得重跑」的測試依原因排行(支數、每輪花掉幾秒),附白話與怎麼辦(10-07)
aib ship crosscheck               # 整套對答案(10-07):aib 一次只跑一支;先一支一支跑(能沿用就沿用),再用專案平常的方式整套跑一次,
                                  #   逐檔比紅綠。不一致 = 測試會互相影響(共用狀態、執行順序),aib 的綠不代表 CI 整套跑會綠。
                                  #   支援 pytest、jest、vitest、mocha;結束碼 0 一致 / 1 不一致 / 4 比不了。建議每週跑一次
aib ship check                    # 查主線歷史有沒有違規的提交(給 CI 用)
aib ship release --version 1.4.0  # 只有真人在終端機:先預覽,打出版號才發
aib ship package --emergency      # 只有真人在終端機:打理由,跳過推前檢查一次;下一包自動補驗,補驗前不能發版
```

- **哪些檔算測試**:Python 是 `test_*.py`、`*_test.py`、`tests/` 下的 `test-*.py`;**pytest 設了 `testpaths` 就只算那些路徑**
  (10-07 Flask:`examples/` 底下是另外安裝的範例,pytest 本來就不跑)。JS/TS(倉根有 `package.json`)是 `*.test.*`、`*.spec.*`、
  `__tests__/` 底下的檔;mocha 專案另算 `test/` 這一層的 `.js/.mjs/.cjs`。
- **版號在哪自動找**:`VERSION` → `pyproject.toml` → `package.json` → `setup.cfg` → 唯一一個 `__version__`。
  第一次推會把找到的寫成 `.aib/ship.toml`(一顆提交加進那一包),之後照它走,人可以改。找不到 ⇒ 不能發版,推包照常。
- **一包不准改版號**;改版號只有 `release`(真人)。所以平常推包只跑受影響的測試,版號一批才動一次。
- **改了程式沒改測試 ⇒ 不推**,除非某顆提交訊息寫一行 `No-Test-Reason: 原因`(純重構、只改文字)。
- **推前檢查**:會紅的先跑、一紅就停;**任何紅都不推**(aib 自己的倉例外:主線上也紅的舊紅會放行)。
  新的紅會在同一份程式上再跑一次,綠了標「時紅時綠」(還是不推)。結尾印最慢 10 支、估省多少。
- **重開機後同一行再下**,同一份程式上已跑綠的不再跑。
- **測到的不是要推的程式 ⇒ 不推**:測試用的 python 從這棵樹外面載入你的套件(共用 venv、`pip install -e` 指到另一個 clone)⇒
  推前檢查直接說「沒驗」。在專案裡建自己的 `.venv` 並 `pip install -e .`,或設 `AIB_TEST_PYTHON`。
  `pip install -e .` 的專案可以推包,但 `aib ship watch` 不能用(鏡像樹測不到鏡像裡的程式,它會照實說)。
- **發了壞版**:把壞的那幾顆 `git revert` 當一包推(修正要配測試的規則照查),再由真人發下一個版號。不要把版號改回去。
- aib 自己的倉照舊:`python3 tools/release/ship.py …` 與 `aib ship …` 是同一件事。細節見 `docs/RELEASE-PACKAGES.md`。

## 6.5 在 CI 裡沿用:省 GitHub Actions 的分鐘數(10-07)

GitHub 的機器按分鐘計費,很容易用完。沒有自己的機器(自架 runner)也能省 —— 一行產生工作流程檔:

```bash
aib ship ci            # 寫出 .github/workflows/aib-test.yml(已有就不蓋;--force 重寫、--print 只印)
```

它照樣跑在 GitHub 的機器上,省的方式有三種:

| 省在哪 | 怎麼做 |
|---|---|
| 沒改到的測試不跑 | aib 的紀錄池存進 GitHub 的快取(先找同一分支的、再找預設分支的),下一次沿用上次的綠 |
| 不該跑的那幾輪不跑 | 只在推到預設分支與 PR 時跑;只改 `docs/`、`*.md` 不跑;同一分支新推送取消舊的那輪 |
| 安裝也省 | pip / npm 下載存進快取(小專案的時間多半花在安裝,不是測試) |
| 看得到省了多少 | `aib test --all` 跑完把「沒有 aib → 有 aib」附到每次執行的摘要頁(Actions 網頁上那次執行的 Summary),
  另加三行:**計費分鐘**(GitHub 每個工作進位到整分鐘;小專案省的秒數常常省不到計費分鐘)、「沿用」是什麼意思、這次實跑的是哪幾支 |

**別人送的 PR(不用 aib 的貢獻者)**:
- 貢獻者不必安裝 aib;摘要頁會寫「沿用 = 讀的檔都沒變、上次的綠仍有效;改到的、新加的、上次紅的都會實跑」。
- **PR 只讀快取、不存**:只有推到預設分支與每週全跑會存 ⇒ PR 多了也擠不掉主線那份紀錄。
- **fork 來的 PR 不用 aib、照一般方式全跑**(`python -m pytest` / `npm test` / `go test ./...` / `vendor/bin/phpunit`):
  fork 拿不到密鑰;而且 PR 的測試程式跟紀錄池在同一台機器上,能改寫紀錄檔、偽造自己這一輪的「沿用綠」。
  主線不受影響(PR 寫的快取主線讀不到),但審 PR 的人會看到假綠 ⇒ fork 一律不沿用。
- **每週日一次全部實跑**(`--fresh`):更新秒數、抓「沿用久了藏起來」的偶爾才紅的測試。

10-07 模擬兩次 CI(每次新 clone 到同一路徑、中間只靠快取傳紀錄池;itsdangerous 5 支):
第一次 6.5 秒(全部實跑並建立紀錄)→ 第二次 0.9 秒(5 支全沿用,摘要頁印「少等 3.8 秒,約 81%」);累計跟著快取走。
**還沒在真的 GitHub Actions 上跑過**(只在本機模擬)。

- `aib test --all`:照 aib ship 的找法跑全部測試(pytest testpaths、JS/Go/PHP、`.aib/ship.toml` 宣告);
  **一支都找不到 ⇒ 結束碼 2**(CI 不能在沒測到東西時亮綠)。
- aib 本身固定在一個提交(檔裡的 `AIB_REF`):aib 換版會讓紀錄全部作廢一次,不固定的話每次 aib 更新都全部重跑。
  這份 aib 有還沒推上 GitHub 的提交 ⇒ 不寫(CI 拿不到)。aib 的倉是私有的 ⇒ 在專案 Settings → Secrets 加 `AIB_TOKEN`。
- 安裝那一行(`pip install -e . pytest` 這類)是猜的,照你的專案改。
- **要同一個路徑**:aib 用「git 資料夾的實際路徑」認是不是同一個倉。GitHub Actions、GitLab CI 每次都 checkout 到同一個路徑;
  自己的 CI 若每次換資料夾名,就沿用不到(結果照樣正確,只是不省)。
- **不要讓別人的分支寫的快取給主線用**:紀錄池裡的「上次綠」是拿來跳過測試的。GitHub 的快取照分支隔開
  (PR 寫的快取主線讀不到),產生的檔照這個寫;別的 CI 要自己確認。
- 紅的那一輪也存快取(紅列永遠重跑,綠的那幾支下次就能沿用)。
- 第一次沒有快取 ⇒ 全部實跑;機器環境(python 版本、套件、GitHub 的機器映像更新)變了 ⇒ 全部重跑,跟本機一樣。
- GitHub 的快取每個倉有容量上限、久沒用會被清掉;被清掉就是下一次全部實跑。

## 7. 還不支援 / 要注意

- 測試**只讀時鐘、不讀檔**的那種時間相依(例如「建立超過 30 天算過期」),靠期限兜底,最晚 14 天後才會發現。
- Node:worker_threads、原生擴充(`.node`)一律當量不到;TypeScript 經編譯快取跑的還沒驗。
- **C 寫的函式庫自己讀檔**(不經過 Python 的 open,追蹤器看不到):已處理的有 lxml(parse/iterparse)、OpenCV(imread)、
  pyarrow(parquet/csv/feather/dataset 讀檔)—— 拿路徑讀檔時記下那個檔;h5py、netCDF4、PyTables、GDAL/rasterio/fiona、soundfile
  一載入就當量不到(每次重跑)。
  **名單外的 C 擴充**:Linux 上有 C 編譯器時,aib 會在每個測試行程掛一支小程式庫(安全網,LD_PRELOAD)記下 C 層的開檔,
  跑完比對 ——「C 層讀了倉裡的檔、Python 這邊沒記到」⇒ 那支每次重跑(`aib ship why` 會說是哪個檔)。
  macOS、沒有編譯器、Go 這種不經過 libc 的程式 ⇒ 沒有安全網,名單外的仍是已知限制。關掉:`AIB_NATIVE_GUARD=0`。
  Node 行程也比;但 Node 找模組時讀 `node_modules/**/package.json` 不經過接頭,這部分交給鎖檔(鎖檔變了就全部重跑)——
  **直接手改 node_modules 裡的檔、鎖檔沒變**,aib 不一定看得到(10-07 起已知的限制)。
- Windows 不支援。
- 測試裡叫 git:讀歷史(log/show/cat-file/ls-tree…,版本要是 HEAD 的祖先)、讀工作樹(status/diff/grep)、讀設定、
  指名分支/標籤都量得到;會改倉的(commit/add/checkout…)、對別的倉、`--ignored` 這類讀倉外的,一律當量不到。
- 測試把 PYTHONPATH 整個換掉再起 python 子行程:追蹤器自動接在最後面;要是測試自己帶了 sitecustomize,那支照實記量不到。
- 跨機器共用紀錄要先設定受信機器清單(進階,見 `docs/PLAN-shadow-cross-device-1002.md`)。
