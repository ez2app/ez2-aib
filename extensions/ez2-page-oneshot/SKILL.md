---
name: ez2-page-oneshot
description: >-
  In an Ez2 AIB seat, build or refresh the user's whole ez2.page (Ez2.PAGE) bio page from one
  plain-language sentence: turn it into a v3.109 oneshot intent JSON, run `aib page oneshot --dry-run`,
  show the plan, write the DRAFT only, and hand back the review link — the human publishes.
  Use for「一句話建整頁／幫我做我的 ez2.page／做一頁 bio page／oneshot」. Not for designing
  Ez2.PAGE's own product UI (that is ez2-page-design). Same folder works for Claude, Codex, Grok, Antigravity.
---

# 一句話建整頁 ez2.page（AIB 席用，S6）

正本：ez2-ai `docs/handoff/page-line-0917/aib-oneshot-0926.md`（短 prompt）＋`oneshot-skill-0926.md`（硬規則、速查）＋
`oneshot-opening-checklist-0926.md`（S2 清單），v3.109 / c406e07d。AIB 端指令：`aib page oneshot`（規格 `docs/spec/AIB-PAGE-ONESHOT-v1.md`）。
四家通用：把這個資料夾拷進該家的 skill 目錄（Claude `~/.claude/skills/`、Codex skills、Grok、Antigravity），指令都一樣。

一句話：人說一句 → 你整理成 **intent JSON** → `aib page oneshot` 組成整頁 → **只寫草稿** → 給人預覽確認頁連結 → **人按「確定發布」才上線**。

## 流程（照順序，一步都不跳）

0. **確認能用**：跑 `aib page oneshot --help`。
   - 回「invalid choice: 'oneshot'」＝這台 AIB 還沒併入這個指令 → 跟人說「AIB 還沒併入 oneshot（等 AIB 開發總綱合併發版）」，停。
   - 跑 `aib page status`：「未綁定」→ 請人**自己在終端**跑 `aib page bind --serial <序號>`（序號在 ez2.page 後台），綁好再來；「允許 AIB 改頁：關」→ 請人到後台打開。停。
1. **開場清單（S2）**：跑 `aib page oneshot --checklist`（英文用 `--lang en`）。
   - 把印出的清單**原文**貼給人（不要改寫、不要摘要）；下面那段「目前頁面」是給你看的現況（顯示名、外觀、還能加幾顆）。
   - 人那一句已經講到的項目就別再問；只追問**缺的必填**（顯示名／一句自介／至少一個連結或社群），一次問一格。缺了也不擋：標 missing（待補）照樣做。
   - 「請幫我找」只列候選給人選，不自動寫；AIB oneshot **不上網搜爬**（沒有授權參數），要上網找就先問人同意、由你自己搜、把結果當候選給人挑。
2. **寫 intent**：把人講的整理成一份 JSON，存成檔（例：`$TMPDIR/ez2-oneshot-intent.json`）。規則見下方「intent 規則」。
3. **先演一次**：`aib page oneshot <檔> --dry-run`。把印出的計畫給人看（會改哪些外觀、哪些個人檔案欄、新增哪些區塊、待補必填），問「照這樣寫進草稿嗎？」，**等人說好**。
   - 計畫裡有「注意:這次會改外觀」而人沒要換外觀 → 改 intent（照抄目前的 theme_id／shape_id／avatar_size／avatar_shape）再演一次。
   - 有「clear_existing:true」→ 再跟人確認一次「會刪掉草稿現有全部區塊」。
4. **真的寫**：`aib page oneshot <檔>`（`clear_existing:true` 時要再加 `--confirm-clear`）。成功最後一行是 `REVIEW_URL=https://ez2.page/manage/aib/review?draft=…`。
5. **給連結**：「預覽確認頁：<REVIEW_URL> —— 看完按『確定發布』才會上線；我沒有發布權限。」
   - `REVIEW_URL=` 後面是空的＝沒有任何變更：回「沒有需要變更的內容」，**不要自己拼連結**。
   - 草稿還有示範顆（骨架）時發布會被擋：提醒人把「示範，請改」換成真資料。

## intent 規則（短 prompt ＋ AIB 更嚴的地方）

```
你是 Ez2.PAGE oneshot 填表員。使用者用自然語言描述一頁個人/品牌 bio。
你只輸出一份 JSON intent（不要 markdown 圍欄），欄位如下：
{
  "display_name": "短名",
  "bio": "一句自介",
  "status_text": "可空",
  "theme_id": "nebula|mono|dawn|forest|ocean|pop|rosegold|starlight 擇一",
  "shape_id": "pill|rounded|rectangle 擇一",
  "socials": [{"platform":"ig|line|fb|youtube|threads|x|tiktok|…","url":"https://…"}],
  "links": [{"label":"…","href":"https://…"}],
  "link_groups": [{"title":"作品集","open":false,"items":[{"label":"…","href":"https://…"}]}],
  "faq": {"open_first":false,"items":[{"q":"…","a":"…"}]},
  "texts": ["開場白一段"],
  "clear_existing": false
}
規則：只 https；clear_existing 一律明寫；零 HTML 標籤；link_groups.items 1–12；theme/shape 必須在白名單。
不要發明 CSS、不要輸出 <script>、不要要求直接 publish。
```

AIB 這邊更嚴、一定要照做：

- **`clear_existing` 只寫 JSON 的 `false` 或 `true`（不加引號）**。`"false"`、`null`、`0` 會被 `aib page oneshot` 拒收、什麼都不送。沒寫會補 `false`（保留現有內容）。`true`＝刪掉草稿全部區塊再重建，只在人明確說「清空重做」、看過目前內容後才寫，且要 `--confirm-clear`。
- 白名單：`format_id` = `creator_dark`（預設，nebula）／`creator_light`（mono）／`creator_ocean`／`creator_rosegold`；`theme_id` 八套（light：mono／rosegold／starlight；dark：nebula／dawn／forest／ocean；vivid：pop）；`shape_id` = rounded／rectangle／pill；選填 `avatar_size` md／lg／xl、`avatar_shape` circle／rounded。
- 鍵名照寫：連結 `{label, href}`（不是 url）；FAQ `{items:[{q, a}]}`；社群 `{platform, url}`，platform 用白名單 id（IG＝`ig`，不是 instagram），新增社群還要店主已給該平台社群同意，否則會在寫完外觀後 422——**沒把握就放 `links`**。
- 網址一律完整 `https://`；零 HTML；不自創 theme／block_type。
- `opening_checklist.items.<項>.status` 只收 `have`／`missing`／`find_for_me`，把第 1 步的結果寫進去。
- **已有內容的頁**（清單顯示「區塊：自己的 n」n>0，或「有草稿：是」）每次都再加三件事：① `"clear_existing": false`；② 照抄「目前頁面」的 `theme_id`／`shape_id`／`avatar_size`／`avatar_shape`（只改人要換的那項；換主題就給新的 `theme_id` 或 `format_id`）；③ intent 只剩清單或外觀時，另帶目前的 `display_name`（否則會走示範骨架）。
- 空 intent（什麼內容都沒有）＝示範骨架，只適合全新空白頁。

例：「我是小安，做品牌設計，用星雲紫，放 Behance 跟官網在作品集夾，FAQ：怎麼預約／有開發票嗎」→

```json
{"display_name":"小安","bio":"品牌設計","theme_id":"nebula","shape_id":"rounded",
 "link_groups":[{"title":"作品集","open":false,"items":[{"label":"Behance","href":"https://www.behance.net/xiaoan"},{"label":"官網","href":"https://xiaoan.example"}]}],
 "faq":{"items":[{"q":"怎麼預約？","a":"請點上方預約連結。"},{"q":"有開發票嗎？","a":"有，可開統編。"}]},
 "clear_existing":false}
```

（網址是人給的才寫；沒給就問，或標 `link_or_social` missing，不要編網址。）

## 絕對不要

- **不要叫人把憑證（`ezp_…`）或序號貼進對話**，也不要去讀 AIB 的憑證檔。`aib page oneshot` 自己用這台機器的綁定；綁定是人自己在終端跑 `aib page bind --serial <序號>`。
- **不要在這個流程用 `aib page chat --ops` 的 `set_profile`／`set_theme` 加 `--yes`**：把它們視同**立刻上線**（舊版 PAGE 伺服器上那兩條 PUT 直接改公開頁；AIB `api.py` 的說明也還寫「立刻生效在公開頁」），而且它們不做 oneshot 的「伺服器是草稿語意」預檢。外觀、名字、自介一律寫進 intent，交給 `aib page oneshot`。
- 不要用 `EZ2_PAGE_ASSISTANT_TOKEN=… node tools/ez2-page-oneshot-apply.js`（那要把憑證放進環境變數／指令列）；AIB 席一律 `aib page oneshot`。
- 不要呼叫或要求 publish，不要自己拼預覽連結；發布永遠是人在預覽確認頁按。

## 出錯怎麼講

- `409 PAGE_WORKSPACE_CONFLICT`／`page_api_draft_exists`：有人同時在改草稿。**不要自動重跑**；照錯誤句告訴人哪些已寫進草稿（不會退回），用 `aib page chat` 重讀草稿，跟人確認後再給新的 intent（通常帶 `false`）。
- `PAGE_SOCIAL_*`（社群不合或缺同意）：外觀／名字可能已寫進草稿、區塊沒建；把社群改放 `links` 或請店主先給社群同意，再跑一次（帶 `false`）。
- `PAGE_BLOCKS_LIMIT_REACHED`：區塊超過方案上限；先看清單的「還能加」，請人刪幾顆或少加幾顆。
- `clear_existing 只收 JSON 的 true 或 false`：intent 寫成字串了，改成不加引號的布林。
- 「這台 PAGE 伺服器還不是 v3.109 草稿語意」：一筆都沒寫；回報給人（PAGE 端要先升級），不要改用別的指令硬寫。
