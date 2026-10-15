# aib-languages — 切換 AIB 語言(Claude Code mod,免費,公開版可帶)

`/aib-languages` 開一個小框:四個語言各用自己的寫法(1 繁體中文 · 2 English · 3 日本語 · 4 한국어),`s` 切「這台全部艙 / 只這一艙」,Esc 關。
`/aib-languages en` 直接切,不開框。

切了之後(設計稿 docs/DESIGN-aib-languages-04e0a-1014.md):

- **設定檔**:`aib lang <loc>` 寫 `<狀態根>/locale`(全部艙)或 `$EZ2_AIB_INSTANCE_HOME/locale`(只這艙)。順位:明給 > `EZ2_AIB_LOCALE` > 艙檔 > 人檔 > zh-TW。
- **本席**:mod 立刻 `$.env.set("EZ2_AIB_LOCALE")`(掛勾子行程跟)、狀態列 `🌐 en`;每次請求的系統提示多一段「回答語言」⇒ **下一句就換,不用 /clear**。
- **別席 / 艦長室切的**:mod 每 5 秒看設定檔 mtime,變了就跟(env、狀態列、toast)。
- **其他畫面**:每輪重算的狀態列、底部頁簽、右欄、hub、`aib` 指令輸出都讀設定檔;非 Claude 席收到一封 dm 請改口。
- mod 自己的字走 repo `i18n/<loc>.json` 的 `mod.aib_languages.*` 鍵(`i18n_lib.py --prefix mod.aib_languages --json` 一次取);別的 mod 照 `hooks/i18n.ts`(純函式)+ `register.tsx` 的 `bundle()` 兩段做。

裝法:`/plugin marketplace add <repo>/tools/claude-mods/aib-languages`,再 `/plugin install aib-languages@ez2-aib-languages`。
測試:`tests/test-claude-mods-languages-04e0a-1014.py`(複製到暫存目錄跑 `claude plugin validate` + `claude plugin test`,6 支)。
