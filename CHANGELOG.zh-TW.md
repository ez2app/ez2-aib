[English](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.md) | [繁體中文](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.zh-TW.md) | [日本語](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.ja.md) | [한국어](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.ko.md)

# 版本紀錄

版號跟 `pyproject.toml` 的 `version` 一致;發版 = 維護者打的 `v<版號>` 標籤。

## 0.1.3

- `aib killswitch on|off|status` 一鍵急停:打開後,所有 AI 都不能開新席、不能發 `aib dm`、不能推版;急停檔讀不到也當成急停中。
- 推前檢查改成整套跑完再判:一次列出新的紅、原本就紅的、這台沒驗的;要遇紅就停,明寫 `--fail-fast`。
- 艦長室通訊錄:四個來源合成一份(誰在線、搜尋、最常聯絡的前十名),可設好友、分組、封鎖、靜音;訊息顯示送達、已讀、已處理、回覆中;可群組發送,也有 `aib room contacts`。`aib room say` 對同一間的席位喊話。
- 翻譯改用席位綁定的 AI 來翻。
- `aib bindme`:一次看清各席的人格綁定,缺的補齊。
- 每一席的狀態列顯示正在做什麼(`aib now`):第幾步、進度、彩色狀態。
- 打中文組字時,畫面暫停刷新。
- 不會再自己叫出舊的「main」主艙,只有你明確要 main 才開。
- 連別台主機一律用安全的 ssh 設定:不轉交你的金鑰,也關掉所有轉發。
- 送給外部 AI 審查前,先把金鑰遮掉。
- 艙會自己接回來:Terminal 當掉或視窗不見時自動開新視窗接回;一直噴錯、其實卡住的視窗會被換掉。AI 輸出時輸入區會提醒,Terminal 剛因打中文崩過也會提醒。
- `aib top`:看誰在吃這台機器(CPU 前幾名、每秒新開了哪些程式、是誰開的)。紀錄檔每個最多 8MB,超過就壓縮,只留 3 份。
- 同一席 30 分鐘內換了 3 次 Codex 帳號就先停手;用名稱停程式不會停到 aib 自己;自動清閒置程式前會先記下是什麼。
- `aib handoff` 交接一開始先檢查人格和專案,缺了會擬一份綁定提案;`aib bindme` 先找合適的現有人格卡,沒有才新建。
- `aib test --guard` / `--scan`:推版前一定要過的檢查改成照規則自動算出來。
- NVIDIA 席可每小時排一次最穩的模型,某個模型沒回應時自動換下一個。
- 修正:Grok 復活時帶回原本的模型;部分 Mac 找不到 `lsof`;macOS 27 清殘留程式。
- `aib room`(艦長室網頁)這一版還沒有;`aib room contacts`、`aib room say` 在終端機照常可用。
- 修正:交接收據跨過午夜出錯、`seat-info` 會掛掉、釘選的艙關掉後又自己重開、一支時紅時綠的畫面測試。
- 用 pip 或 pipx 裝的,`aib uninstall` 改成直接說要用哪個指令移除(`pipx uninstall ez2-aib` / `python3 -m pip uninstall ez2-aib`),不再誤說成開發版。

## 0.1.2

- 新增 `aib uninstall`:先列出會刪哪些;`--yes` 刪掉這份 aib,`--all` 連紀錄一起刪(先備份)。同一台電腦上另一個 aib 絕不會被動到。
- 電腦上已經有另一個 aib 時,一行安裝不再回報失敗、也不再叫你把它換掉:原本的 aib 照常可用,公開版放在旁邊。
- 安裝程式的提示改用 GitHub 上連得到的網址,也不再出現只有開發者看得懂的話。
- 在 Linux 上重跑一行安裝,標題改為「更新」而不是「安裝」。
- 一行安裝改裝「最新的正式版」(最新的 `v*` 標籤),跟 PyPI 同一版,不再是最後合併進去的程式;發了新版後重跑就會更新,不會往回退。
- 發版:同一輪流程打標籤、建 GitHub Release(說明就是 CHANGELOG 那一節),維護者核准後上傳 PyPI;同一時間只跑一個發佈。CI 只在 PR 和 main 上跑。

## 0.1.1

- 說明文件四種語言:README、CHANGELOG、CONTRIBUTING、SECURITY 和兩份使用說明,都有 English、繁體中文、日本語、한국어。
- 每份文件最上面的語言切換改指向 GitHub,在 PyPI 上也點得到(0.1.0 在 PyPI 上會 404)。
- `aib --help` 第一行改成說明 aib 的用途:多個 AI 在同一個工作空間協作。
- 一行安裝在 Linux 上也能用:只裝測試安全網(`aib test`、`aib ship`);駕駛艙仍需要 macOS。
- README 補上沒有 pipx 時要先怎麼裝。
- CI 加測 Python 3.14、3.15。

## 0.1.0 —— 第一個公開版

- `aib test`:只跑輸入有變的測試,其餘沿用先前的綠燈結果(Python、bash、Node 測試;pytest 與 unittest)。
  每支測試有自己的暫存資料夾與家目錄;留下的東西、背景程式會被收掉,並在總表點名。
- `aib ship`:推前檢查、打包、推送;擋過推包的測試最先跑。
- 內建駕駛艙(tmux 三欄、滑鼠選單、彈出選單)、艦隊管理、席位技能(交接、建專案、建角色、掛新 AI、掛新 bot)、回報 bug 技能。
- `aib tmp`:列出或清掉 aib 與它的測試留在暫存區的東西,其他一律不碰。
- 一行安裝直接抓本倉裡的 `install.sh`;`aib update` 先試網站那份,不是公開版就改用本倉裡那份。
- **復活功能**:席位或整個艙當掉、斷線後,原地救回來接著做。
  - 帶回原本在用的 AI 模型(先看對話紀錄、再看舊席位檔、最後才用預設)。
  - 復活後第一則回答先給白話標題、約 150 字總結和建議的下一步。
  - `aib restart` 開機選單(macOS):登入後有艙沒回來,才開終端機列出可恢復的艙。
- 打中文還沒送出時,面板暫停刷新,避免 macOS 終端機出錯當掉(要搭配輸入法回報組字狀態;沒有時照常刷新)。
- 右欄:席位確認能打字後,舊的「未確認」不再蓋過它。
- `/aib view bot`:在別席看 bot 席的狀態(在不在跑、綁哪個對話、待收幾封、最後收發時間、錯誤)。
- Claude 席預設模型改為 Opus 5.5。
