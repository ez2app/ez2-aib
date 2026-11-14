# aib-slice sweep — 介面(S73a,1ced7)

`aib-slice sweep [--repo DIR] [--pending DIR] [--seat 5碼] [--no-fetch] [--gate] [--hours N] [--gh PROGRAM | --no-gh]`

只讀:fetch、讀 git、用 `gh api` GET 讀開著的 PR、讀 pending 夾的審查 / 收據 / ack / 建置中登記。不推、不開 PR、不刪樹。
輸出一個 JSON 物件 `aib-slice-sweep.v1`,正本是 `schema/sweep.v1.json`;下面的欄位表跟它逐欄對照,`tests/iface.rs` 比對兩邊欄位集合,不一致就紅。

## 結束碼
- 0 掃完;2 掃不完整(fetch / gh / 某支分支讀不到),JSON 照印、`complete=false`,`--gate` 也是 2 —— mergewatch 把 2 當不合;3 只在 `--gate`:有類 1 / 2 / 3;64 用法錯。

## 六類(由上往下,先中先算)
5 merged(獨有 patch 全在 main 或開著的 PR)→ 6 ack(sweep-ack.txt)→ 6 可放棄(PR 關掉沒合、關在現在這個頭;s33)→ 3 fix(開著的 PR 衝突 / PR checks FAILURE / 頭上審查 H/M > 0 / 頭上收據封印不符 / 頭上收據紅)→ 1 merge_first(頭上審過 + 收據綠 + 已推 + 不衝突 + PR 開著)→ 2 push_or_pr(頭上有審查或收據,但沒推或沒開 PR)→ 6 stale(超過 --hours 沒 commit 且沒開著的 PR)→ 4 in_progress。`--gate` 擋 1 / 2 / 3。

## pending 夾讀的檔
- `REVIEW-*.md` 第一行:`結論: X 頭: <40> H/M/L/N: a/b/c/d …`。
- `TEST-*.txt`、`RECEIPT-*.txt|.log`:前 5 行的 40 碼,或 `== HEAD=` / `HEAD ` / `HEAD-END ` 行;`xxx_rc=N`、`STEP xxx rc=N`、`RC xxx=N`(`KNIFE` 行、`RC knife_*`、`head_rc` / `base_rc` 不算;後面帶 `(want N)` 的以 N 判、`(want non-0)` 是 0 才紅、`(report only)` 不算紅);`HEAD-END <頭>` 完成記號;最後一行 `RECEIPT-SHA256 <64>`。
- `REVIEW-*.md` 內 `非閘理由: …` 一行(S73j)。
- pending 夾或檔讀不到 ⇒ errors(`pending …`)。
- `sweep-ack.txt`:`分支<TAB>原因`。
- `build-claims.tsv`:`分支<TAB>席<TAB>開始時間<TAB>說明`;時間沒寫時區 = 台北;超過 6 小時不算。

這些都是同帳號可改的檔:讀來分類,不是防線。

## 欄位表
- `schema` — 固定 `aib-slice-sweep.v1`
- `generated_at` — unix 秒
- `main` — origin/main 40 碼
- `complete` — false = 有東西讀不到,看 errors
- `errors` — 讀不到的原因,一則一行(`no_fetch`、`no_gh` …)
- `open_prs` — 開著的 PR
- `open_prs[].number` — PR 號
- `open_prs[].head_ref` — PR 的分支名
- `open_prs[].head` — PR 頭 40 碼
- `branches` — 每支分支一筆(本機 + origin 的 integ-src/ feat/ fix/ integ/,同名算一支)
- `branches[].branch` — 分支名
- `branches[].seat` — 分支尾碼的 5 碼席碼;認不出 = null
- `branches[].head` — 頭 40 碼(本機優先)
- `branches[].local` — 本機有這支
- `branches[].remote` — 同名的 origin 分支;沒有 = null
- `branches[].worktree` — 掛在哪棵工作樹;沒有 = null
- `branches[].dirty` — 工作樹有沒有未提交;沒工作樹或沒獨有 commit = null
- `branches[].last_commit_at` — 頭的 committer 時間(unix 秒)
- `branches[].age_h` — 離現在幾小時
- `branches[].unique` — origin/main..頭 的非合併 commit,扣掉 patch 已在 main 的
- `branches[].unique_not_in_prs` — 再扣掉 patch 已在任一開著 PR 頭的
- `branches[].in_prs` — 收了這支部分 patch 的開著的 PR
- `branches[].pushed_to` — 含這個頭的 origin 分支
- `branches[].pr` — 開著的 PR(head_ref 是這支或它推到的名字),否則最新一張關掉的;沒有 = null
- `branches[].pr.number` — PR 號
- `branches[].pr.state` — open / closed
- `branches[].pr.merged` — 關掉時是不是合了
- `branches[].pr.base` — PR 的 base 分支
- `branches[].pr.author` — PR 作者 gh login
- `branches[].pr.mergeable` — MERGEABLE / CONFLICTING / UNKNOWN;沒讀 = null
- `branches[].pr.merge_state` — GitHub mergeable_state 原字;沒讀 = null
- `branches[].pr.checks` — 頭的 check runs 彙總 SUCCESS / FAILURE / PENDING;沒讀或沒有 = null
- `branches[].pr.red_checks` — 紅的 check run 名;null = 沒讀(不代表沒紅)
- `branches[].review` — 這支 commit 上最近的一份審查;沒有 = null
- `branches[].review.head` — 審查第一行的頭
- `branches[].review.verdict` — 通過 / 不通過 / 要改 …
- `branches[].review.h` — H 數
- `branches[].review.m` — M 數
- `branches[].review.l` — L 數
- `branches[].review.n` — N 數
- `branches[].review.file` — 審查檔名
- `branches[].review.at_head` — 審查頭 == 分支頭
- `branches[].review.unreviewed_after` — 審查頭到分支頭之間的非合併 commit
- `branches[].receipt` — 這支 commit 上最近的一份收據;沒有 = null
- `branches[].receipt.head` — 收據寫的頭
- `branches[].receipt.file` — 收據檔名
- `branches[].receipt.nonzero` — 不是 0 的 rc 鍵
- `branches[].receipt.green` — nonzero 空、封印沒壞、有 rc 行、complete
- `branches[].receipt.at_head` — 收據頭 == 分支頭
- `branches[].receipt.steps` — 收據跑過的 rc 鍵
- `branches[].receipt.sealed` — RECEIPT-SHA256 對得上 = true、對不上 = false、沒這行 = null
- `branches[].receipt.complete` — 有 `HEAD-END <該頭>` 行(跑到底);false 或沒有任何 rc 行 ⇒ green=false、類 3「receipt incomplete」
- `branches[].stacked_on` — 頭落在我獨有 commit 裡的別支分支
- `branches[].stack_top` — 沒有別支疊在我上面
- `branches[].conflicts_with` — 跟哪些開著的 PR 衝突(只算類 1 / 2 / 3 候選)
- `branches[].conflicts_with[].pr` — PR 號
- `branches[].conflicts_with[].files` — 衝突檔
- `branches[].conflicts_with_branches` — 跟哪些類 1 / 2 分支互相衝突
- `branches[].conflicts_with_branches[].branch` — 分支名
- `branches[].conflicts_with_branches[].files` — 衝突檔
- `branches[].conflicts_with_main` — 對 origin/main 試合;沒試 = null
- `branches[].conflicts_with_main.conflict` — 有沒有衝突
- `branches[].conflicts_with_main.files` — 衝突檔
- `branches[].claimed_by` — build-claims.tsv 的建置中登記;沒有或過期 = null
- `branches[].claimed_by.seat` — 登記的席
- `branches[].claimed_by.at` — 登記時間(unix 秒)
- `branches[].new_gate` — gate_files 非空:這支改了檢查,同批排最後(S73j)
- `branches[].not_gate` — S73j 降成非閘(519d0 11-14)
- `branches[].not_gate.label` — PR 有 `not-gate` 標籤
- `branches[].not_gate.reason` — 頭上審查(審查席 ≠ 分支席,由檔名 `REVIEW-…-<席>-<頭8>.md` 認)的 `非閘理由:` 那行;沒有 = null
- `branches[].not_gate.effective` — new_gate 且(label 且 reason 非空,或 by_content)⇒ 排序當非閘
- `branches[].not_gate.by_content` — S73k 看內容判非閘(519d0 / Andy 11-14):閘檔只有一支 workflow,而且只多了一個 step、只跑這支分支自己改的 crate(src/gatecontent.rs 六條全成立);拿不準一律照字面當新閘
- `branches[].not_gate.content` — 判非閘時寫加了什麼 step;不成立時寫哪一條不成立(`rule N: …`);沒判(沒有閘檔,或 git 讀不到)= null
- `branches[].gate_files` — merge base..頭 動到的 `.github/workflows/`、`.github/rulesets/`、`xtask/src/`、`aib/tools/release/docs_only.py`、檔名含 ruleset 的檔
- `branches[].class` — 1–6
- `branches[].class_name` — merge_first / push_or_pr / fix / in_progress / merged / stale
- `branches[].why` — 為什麼是這一類
- `branches[].abandonable` — 可放棄(s33,Andy 11-14):PR 關掉沒合、關的時候就是現在這個頭、還有自己的 commit 不在 main 也不在開著的 PR;類 6,不算「沒驗」也不算「要處理」,留或丟仍由人定。關掉後又有新 commit 的不算
- `branches[].py_files` — merge base..頭 動到的 `.py` 檔(s35,只能 Rust 之後一眼看到還帶 Python 的未合分支)

## 不做(第二輪 / 別片)
報表排版、座艙一行(S73c)、`--gate` 接 mergewatch(S73b)。
