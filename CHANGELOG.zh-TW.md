[English](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.md) | [繁體中文](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.zh-TW.md) | [日本語](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.ja.md) | [한국어](https://github.com/ez2app/ez2-aib/blob/main/CHANGELOG.ko.md)

# 版本紀錄

版號跟 `pyproject.toml` 的 `version` 一致;發版 = 維護者打的 `v<版號>` 標籤。

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
