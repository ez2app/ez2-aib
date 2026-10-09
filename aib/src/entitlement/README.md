# entitlement（§108.320 + 今晚包 D／DESIGN §4.4）

**for_task: §108.320 / evening-D-0926**

產品有可用的付費資格底（`free`／單家／全開），付費功能有處可查；人格粹煉來源可按 provider 獨立開關，供收集器 C 讀有效開啟集。

## 邊界

- **本軸**：`tier ∈ {free, single, all}`，`single` 帶 `providers[]`。
- **並列不混**：不碰、不擴充 `src/bootstrap/license_plan.py` 的 basic|advanced，也不改 `feature_policy.py`。
- **執行期路徑**：
  - 資格：`~/.ez2-aib/license.json`（`EZ2_AIB_HOME` 覆寫根）
  - 來源開關：`~/.ez2-aib/persona/sources.json`
- **fail-closed**：缺檔／壞 JSON／未知 tier／single 缺 providers／無簽章／過期逾寬限 → `tier=free`（回 `reason`）。
- **免費只 claude 可開**（寫進程式 `FREE_ALLOWED_PROVIDERS`;claudebot 算付費,the maintainer 09-27 裁）；免費態對其他家「開」⇒ 拒並印「付費版可開」，不靜默、不偷開。
- **永不**把憑證原文寫進回傳、log、例外訊息。
- **不動** `src/cockpit/aib`（入口由 a seat 接）、persona-collect 本體、計畫樹。

## 資格三級

| tier | 可開來源 |
|------|----------|
| `free` | 僅 `claude` |
| `single` | `claude` + `license.providers[]` |
| `all` | provider-launch.sh case 清單全部 |

Provider 母體從 `src/cockpit/provider-launch.sh` 的 `case "$PROVIDER"` 解析（備援常數 `KNOWN_PROVIDERS` 測裡對齊）。teamoclaude／bots 各自獨立、算付費。

## 給呼叫端（C／#321）一行用法

```python
from entitlement import get_entitlement, get_enabled_sources, is_source_enabled

e = get_entitlement()  # → {tier, providers, reason, source_path}
# free：只開 claude 來源；無 an internal service；頂行不可展開整樹
# single：claude + e["providers"]；含 an internal service／可展開
# all：全部 provider（含未來新增）；含 an internal service／可展開
paid = e["tier"] in ("single", "all")

# C 只收有效開啟的家；關閉／不可用 ⇒ is_source_enabled False（0 筆）
enabled = get_enabled_sources()          # list[str]
ok = is_source_enabled("codex")          # bool
```

機器可讀資格：

```sh
tools/entitlement/aib-entitlement status
# {"tier":"free","providers":[],"reason":"missing","source_path":"..."}
```

來源開關／狀態（本包交腳本；`aib persona …` 入口待 a seat）：

```sh
tools/entitlement/aib-persona-sources 來源 claude 開
tools/entitlement/aib-persona-sources source codex on   # 免費態 ⇒ 拒、「付費版可開」、rc≠0
tools/entitlement/aib-persona-sources 狀態
# 資格: free（無簽章 = 免費）
#   claude: 開
#   codex: 不可用(免費)
#   …
```

單家態狀態尾行：`已開 N 家,升級全開可再開 M 家`。

## 候 the maintainer（三缺口草稿預設，合入審查用；勿當已裁）

1. **離線寬限**：先 **72h**，時鐘系統 **UTC**。
2. **簽章／發撤**：有 `signature` 欄就驗、無就 `unavailable`→free；`issue`|`revoke` 只開 CLI 草稿殼，不接真金鑰。草稿驗簽僅檢查非空字串。
3. **路徑**：執行期 `~/.ez2-aib/license.json`（如上）。

## 紀律

- 只在草稿分支／worktree 發展；**不推 main**、不 force-push、不改遠端保護分支。
- 狀態：**今晚包 D 交件**，入口接線候 a seat；下一正式動作等審查與 the maintainer「上」。
