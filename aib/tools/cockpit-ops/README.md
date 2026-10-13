# cockpit-ops

2026-08-10 從 ~/ez2-cockpit-demo 收編的督導工具。10-13 刪掉沒在用的第一批(a seat 盤點、the maintainer 01:5x 准):
root-sentinel-v2.sh、captain-patrol.sh、board-sync.py、captain-say.sh、worktree-inventory.sh、FIXFWD-AIB-DM-20260814
(全倉沒有呼叫者、沒有排程、30 天沒人跑;清單與證據在 a seat 的 TOOL-PRUNE-LIST-8f366-1013)。要看舊內容:`git log -- tools/cockpit-ops/<檔名>`。

## 留下來的

| 檔案 | 誰在用 |
|---|---|
| `aib-dm.sh` | `aib dm` 每次送信:dm-ctl.sh → src/messaging/aib-send.sh 執行它。改它等於改所有席的送信路徑。 |

`open-fleet6.sh`、`cabin-freshness.sh` 是第二批待刪(要先改另外 3 支提到它的測試),還沒動。
