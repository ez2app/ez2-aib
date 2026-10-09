# cockpit-ops — 督導與協調工具(自版控外收編)

## 由來

2026-08-10,a seat 第一艦長收編。這五支是本大綱多處引用為交付件、卻從未進入任何版控的工具:
`git -C ~/ez2-cockpit-demo rev-parse --is-inside-work-tree` rc=128 —— 那不是 git repo,無版控、無備份、不進 CI。

大綱引用點:`captain-patrol.sh` 是 E2.10 哨兵 v2 的參考實作;`board-sync.py` 是 E3.2 的先遣件;
`aib-dm.sh` 是 E2.18 的 DM 工具。憲章引用了它們,而它們的實體隨時可能因為一次目錄清理而消失。

本次收編是**逐位元複製**,不是重寫:七件(含 `docs/handbook/` 兩份 E3.6 手冊)全部 sha256 與來源相符。
保全與重構刻意分開——這批東西之所以會不見,就是因為每次有人想動它時都順手改了別的。

## 與 ACB 廢止的關係(the maintainer 08-08 拍板,08-10 重申:只留 AIB)

實查結果:**五支之中沒有任何一支呼叫 ACB CLI**。耦合只剩路徑名,絞殺者要拆的是路徑不是功能。

| 檔案 | ACB 耦合 | 狀態 |
|---|---|---|
| `captain-patrol.sh` | 第 5-6 行預設 sandbox 路徑含 `acb-sandbox` | 待改 AIB 路徑 |
| `board-sync.py` | 第 7 行預設 `~/ez2-cockpit-demo/acb-sandbox` | 待改 AIB 路徑 |
| `aib-dm.sh` | 無 | 可直接沿用 |
| `captain-say.sh` | 無 | 可直接沿用 |
| `root-sentinel-v2.sh` | 無 | 見下方缺陷 |

## 收編時已知的兩個缺陷(原樣保留,不在本次修)

**`captain-patrol.sh:5` 的預設路徑指向一個已死 session 的暫存目錄**
(`/private/tmp/claude-501/-Users-user/bb890cd5-.../scratchpad/acb-sandbox`)。
那個 session 早已不存在,實際靠第 6 行 fallback 到 demo 目錄才活著。這正是本 repo 反覆付學費的同一個病:
狀態住在 session 暫存目錄。改路徑時要一起根治,不要只換一個同樣易失的位置。

**`root-sentinel-v2.sh:5` 寫死告警管道 `/dev/ttys019` 且吞掉寫入失敗**(已修,見下)。
該 tty 是總艦長 a seat 的終端,session 一歿裝置檔即不存在,而原本的 `2>/dev/null`
把寫入失敗一併吞掉——監督者的告警全部寫進虛空,而且它不會告訴任何人。
已改為:不寫死(`AIB_SENTINEL_TTY`)、未設定就明說只進 log、管道失效或寫入失敗回非零並落 log。

**一則已撤回的誤判(留作紀錄)**:收編當下曾記載「第 18 行以裸 `&` 起 patrol=§五 第一條活違規」。
讀全檔後撤回:第 19 行 `patrol_pid=$!`、第 25/35/41 行 `kill`、第 44 行 `kill -0` 探活並在子程序
退出時一併 exit——這是**有監督**的 supervisor 模式,正是 §五 第一條要的相反面。
`tests/test-shell-hygiene.py` 因此設計為只擋 fire-and-forget(無 `$!`、無 kill/wait),
實跑確認未誤殺本檔。看到 `&` 就判違規會誤殺正確的監督者。

## 紀律

改這裡的檔案前先讀 `docs/EZ2-AIB-DEV-OUTLINE.md` 的 E2.10 與 E3.2 兩節。
`~/ez2-cockpit-demo/` 的同名檔自本次起**不是正本**;兩份副本無同步機制,
拿舊副本驗收會量出假紅(2026-08-07 已有實案)。以本目錄為準。
