# aib-boards — 每一席的電腦(Claude Code mod,免費)

`/aib-boards`(只有真人在終端機打字送出才會開;外掛 / 排程 / 別席送來的不開)開一個面板,佔畫面 70%(對話 15 : 面板 35)。

| 分頁 | 內容 |
|---|---|
| 1 今天 | 狀態列、應知雷達與燈號、待辦分組(🔴⏳🙋👥📝)與分布圓餅、人格職責 / 紅線、專案 |
| 2 專案 | 綁定專案的計畫樹(總綱 › 大項 › 子項 › Task + 狀態 + 進度,亮的是我的) |
| 3 私訊 | 信件列表(未讀 / 已讀未結 / 完成);新信靠檔案事件從右下彈出,不輪詢 |
| 4 圖表 | 依級別:艦長 13、主管 8、監看 6、工程 6(同示範網頁);每張會動 |
| 5 自訂 | 勾選要放哪些圖,存在 `~/.ez2-aib/state/aib-boards/<seat_id>.json` |

- 資料只讀 `src/cockpit/seat_screen.py`(+ 艦長級 `seat_screen_fleet.py`)一份;打開才算,今天 15 秒、圖表 / 專案 60 秒、私訊 15 秒快取。
- 動畫 0.4 秒一格,打開後動 20 秒自停(一直重畫會把對話拉回底部);按按鈕、重新整理、在面板上滾再動 20 秒;「動畫:省電」全停。
- 要捲對話:關掉面板(滾輪在面板上 = 捲面板,mod 無法轉交給對話)。
- **席位核心**(10-14 從 aib-seat 搬來,aib-seat 已拿掉,設計稿 §24):每回合寫進系統提示的席位身份(艦長 / 整合 / dev / qa 另加摘要)、狀態列一行、85% 交接提醒、守衛擋下提示、新信卡片(輸入框上方,狀態色、可展開、✕ 才關)。`/aib room`(開艦長室網頁)是艦長室外掛 captain-room 那段,拆在 `hooks/room.ts`:這份 aib 沒有艦長室(公開版)就只說開發中。探測程式 `bin/seat-info.py`。
- 裝法(用目錄當外掛來源):`/plugin marketplace add <repo>/tools/claude-mods/aib-boards`,再 `/plugin install aib-boards@ez2-aib-boards`。
- 測試:`tests/test-claude-mods-boards-hosts-86ff0-1014.py`(複製到暫存目錄跑 `claude plugin validate` + `claude plugin test`)。設計稿 docs/DESIGN-seat-screen-86ff0-1013.md §21、§22。

舊的 aib-seat 外掛要從各機移除:`claude plugin uninstall aib-seat@ez2-aib-mods`,再 `claude plugin marketplace remove ez2-aib-mods`。
