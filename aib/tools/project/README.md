# `tools/project`：計畫樹與偏離工具

目的：提供計畫樹 uid、偏離的工具（§108.323／§108.324／§108.330）。欄位契約見 [`docs/specs/PLAN-TREE-SCHEMA-v0.md`](../../docs/specs/PLAN-TREE-SCHEMA-v0.md)。以下命令皆在 repo root 執行。

## 呼叫端用法

- **show**：`python3 tools/project/show.py --nodes <path> --json`
  - stdout 為 JSON **物件**：`{nodes, orphan_count, out_of_writeset_count, detour_unreturned_count, flags}`。
  - 每個 node 含七欄＋`progress: {done, total, ratio}`（只算子孫 `layer=task`；`not_doing` 不計 total）。
  - `--depth N`：只含深度 ≤ N 的節點（**根深度 = 0**）。
  - `--mine`：依 `--owner`／環境 `AIB_SEAT`／`EZ2_AIB_SEAT` 過濾本席 Task 葉子，**帶齊父鏈到根**；父鏈斷裂或缺父 → stderr＋非 0。
  - `--flags-json PATH`：讀事件檔算三旗標計數給席 B；缺檔或未傳 → 計數 0（介面穩定）。
  - 保留 `--nodes`／`--project-uid`。缺檔 → 非 0。
- **dry-run**：`python3 tools/project/import_outline_dry_run.py --repo-root .`
  - 產出**四層** nodes（outline›major›subitem›task），每節點有 `layer`。
  - 預設 stdout／`--pending`；不寫 `projects/`。測試可用 `--write-nodes /tmp/…`。
- **trailer**：`python3 tools/project/check_task_trailer.py -m $'feat: x\n\nTask: t.aib.108.324\n'`
  - 只接受正式 uid `t.…`；`Task: §108.324`／`Task: #324` → 非 0。
  - 無 `Task:` → 非 0；`--warn-only` 永遠 rc=0。
- **hook**：`tools/project/install_task_commit_msg_hook.sh [--repo PATH] [--force] [--dry-run]`
  - **尊重 `core.hooksPath`**：裝到 `git rev-parse --git-path hooks`（有 hooksPath 就裝該目錄並 `mkdir -p`；無則預設 `.git/hooks`）。印出的 `installed:` 必為 git 實際會讀的路徑；hooksPath 空字串／不可寫／無法建立／解析成 repo root → 非 0 並印 hooksPath，**不**印 installed。
  - hook 呼叫 `check_task_trailer`；無 Task → 拒 commit。刀3 多臂：無 hooksPath／自訂 hooksPath 皆拒無 Task；空 hooksPath／不可寫拒裝。
- **drift**：`python3 tools/project/drift_event.py --event-json tests/fixtures/drift_event_min.json`
- **new-project（Wave2 O）**：`python3 tools/project/new_project.py --seat SEAT [--title T] [--cwd PATH] [--state-root DIR] [--persona-uid UID] [--dry-run]`
  - 建專案卡 `aib-project-card/v1`（`$EZ2_AIB_HOME/projects/<uid>/card.json`）+ 倉指紋。
  - 從人格生成**憲法初稿候選**（`…/candidates/constitution-draft.json`）；`approved` 恆 `false`，不核准不生效。
  - 更新席位延續檔／席位檔的 `project_uid`（免費、無 license 閘）。
  - stdout JSON：`{ok, project_uid, card_path, candidate_path, seat, approved:false}`。
  - 選單：brain「新開專案」→ `--bind-menu new-project-do`（`mouse-ctl.sh`）。
- **expand（Wave2 E）**：`AIB_PROJECT_EXPAND=1 python3 tools/project/render_tree.py --nodes <path> --seat <id> [--display] [--tier free|single|all]` → 展開樹；資料層預設零 ANSI，`--display` 本席行底色；免費只一層大項。席位色表見 `src/cockpit/render/seat_colors.py`。
- **flags**（真做，非 stub）：
  - `python3 tools/project/flags.py orphan --for-task-uid ''` → `true`／`false`（無效 uid 亦 true）
  - `out-of-writeset`：目錄前綴（`tools/project/` 涵蓋其下檔）；writeset 缺 → `unknown`
  - `detour-unreturned`：disposition=暫時繞路後回歸 ∧ returned 未歸
  - `count`：`python3 tools/project/flags.py count --events-json PATH` → `{"orphan":N,"out_of_writeset":N,"detour_unreturned":N}`

## 測試

```sh
python3 tests/test-plan-tree-show-323.py
python3 tests/test-plan-drift-324.py
python3 tests/test-plan-evening-A-0926.py
python3 tests/test-project-expand-e.py
python3 tests/test-new-project-o-0927.py
```

## 對帳（席 B）

- **A 正本**：`tests/fixtures/plan_task_uids_A_draft.json`；schema `PLAN-TREE-SCHEMA-v0`；四層 uid：`t.aib.108` → `t.aib.108.3` → `t.aib.108.3.{NNN}` → `t.aib.108.{NNN}`。
- **準則**：欄位契約以 A 的 `PLAN-TREE-SCHEMA-v0` 為準（含必填 `layer`）。B 讀 `--json` 的 `flags`／`*_count` 即可對無主／寫集外／繞路未歸位。
