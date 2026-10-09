[English](CHANGELOG.md) | [繁體中文](CHANGELOG.zh-TW.md)

# 版本紀錄

版號跟 `pyproject.toml` 的 `version` 一致;發版 = 維護者打的 `v<版號>` 標籤。

## 0.1.0 —— 第一個公開版

- `aib test`:只跑輸入有變的測試,其餘沿用先前的綠燈結果(Python、bash、Node 測試;pytest 與 unittest)。
  每支測試有自己的暫存資料夾與家目錄;留下的東西、背景程式會被收掉,並在總表點名。
- `aib ship`:推前檢查、打包、推送;擋過推包的測試最先跑。
- 內建駕駛艙(tmux 三欄、滑鼠選單、彈出選單)、艦隊管理、席位技能(交接、建專案、建角色、掛新 AI、掛新 bot)、回報 bug 技能。
- 付費外掛另外裝(裝到 `~/.ez2-aib/plugins/`);`aib license` 驗 Ed25519 簽章的授權、綁機器、線上啟用。
- `aib tmp`:列出或清掉 aib 與它的測試留在暫存區的東西,其他一律不碰。
- 一行安裝與 `aib update`:先用網站那份,網站連不上就用本倉裡那份。
