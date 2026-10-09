#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 發版 tag 閘(2026-08-15,pm3@662be6 派工:v0.14–v0.19 六個 tag 全部標在紅 CI 上)
#
# ## 它擋什麼
#
# **推一個 tag 上去,而它指到的 commit 沒有「跑滿完整矩陣且全綠」的紀錄。**
#
# ## 為什麼判準不是「那次 CI 綠不綠」
#
# 六次都沒被擋,不是因為有人看錯燈,是因為**看的是真燈,但那盞燈在回答別的
# 問題**。v0.19 實測(2026-08-15 現查):
#
#   引用為綠  run 31871262137  ref=main   → jobs = plan + ubuntu/py3.12         2 格
#   tag 實際  run 31871480107  ref=tag    → jobs = plan + 5 格,macOS 兩格 failure
#
# 兩個都是真 run、兩個都是真結論。差別在 `.github/workflows/test.yml` 的 plan
# 分流:推 main 只跑一格。所以「main 綠」一直只等於「ubuntu+py3.12 綠」,
# 它從來沒有回答過「這版能不能發」。
#
# ★ 因此本閘**不看 run 層的 conclusion**——那正是被騙的那一層。它看的是
#   **job 集合**:完整矩陣的每一格都要在,而且每一格都要 success。
#   一個只跑一格的綠 run,在本閘眼中是「涵蓋不足」,擋。
#
# ## 為什麼是 pre-push 而不是別的地方
#
#   * git **沒有 pre-tag hook**。`git tag` 是純本地動作,無鉤可掛。
#   * tag 在被推上去之前對外不存在,所以 push 是唯一的天然咽喉。
#   * 放 CI 裡擋不住:tag 的 CI 是 tag 推上去**之後**才跑的——那正是這次
#     的結構性成因,再放一次只會再遲到一次。
#   * 放 `~/.claude/skills/` 更不行:那是 AI 照著讀的散文,正是這次失效的
#     那一層(實查 ez2-release/SKILL.md 40 行,全文沒有 CI/tag/run 任何一字,
#     而且它寫的是別的 repo)。
#
# ## 取不到就擋下,不預設通過
#
# CI 還在跑、查不到 run、gh 沒裝、gh 沒登入、矩陣讀不出來——**一律擋**。
# 依據是本 repo 自己的教訓(docs/HANDOFF.md §六):「守衛不見了必須比守衛
# 擋下你更吵,否則守衛可以無聲消失」。fail-open 的守衛等於沒有守衛,而且
# 更糟——它會讓人以為有。
#
# ## 安裝(交付進 repo 不等於啟用)
#
# ★ **裝到 `core.hooksPath` 指的那個目錄,不是 `.git/hooks/`。**
#
#   install -m 755 src/policy/hooks/pre-push-adapter.sh \
#     "$(git rev-parse --git-common-dir)/ai-b/hooks/pre-push"
#
# 本 repo 已設 `core.hooksPath=.git/ai-b/hooks`。**設了之後 git 就完全不看
# `.git/hooks/`**——裝在那裡會得到一個永不執行、也永不報錯的 hook。
#
# 本檔前一版正是這樣寫錯的:第 45 行寫對了「hooksPath 指向 .git/ai-b/hooks」,
# 隔兩行的安裝指令卻寫 `.git/hooks/pre-push`。**同一份檔案裡兩處該同步而沒
# 同步**,照著做會裝出一個假的守衛——正是 pre-push-adapter.sh 檔頭自己警告
# 的那個失效模式(「tag 閘一年只用幾次,它消失了可以很久沒有人知道」)。
#
# 裝的是**轉接器**不是本腳本:轉接器在找不到本腳本時會大聲擋下(見同層
# pre-commit 的同一形狀),直接指過來則會隨著分支切換靜默消失。
#
# 驗它真的裝上了(不接管線取真 rc):
#
#   test -x "$(git rev-parse --git-common-dir)/ai-b/hooks/pre-push"; echo $?
#
# `tests/test-release-gate.py::HooksPathInstallTest` 釘住這件事:安裝指引
# 指到 git 實際不看的目錄時會紅。
#
# ★ 多個 worktree 共用同一份 hooks,裝一次全部生效。
#
# ## 逃生門
#
#   AIB_RELEASE_GATE_REASON="理由" git push origin v0.20.0
#
# 帶理由就放行,理由記進 .git/ai-b/release-gate-bypass.log 並印 stderr。**刻意做成「要打字說明」而不是
# 「加 --force」**:沒有逃生門的閘會被整個關掉,那比放行更糟。
#
# ## 裝上之後會發生什麼:**現在沒有任何 commit 過得了這道閘**
#
# 這是**正確行為,不是閘壞掉**。2026-08-15 現況:完整矩陣要五格,而 macOS
# 那兩格拿不到結果(Actions 額度耗盡——`workflow_dispatch` 兩秒內死、零步驟、
# 無 log,連六分鐘前綠過的 run 在我方零改動下重跑也紅)。**沒有五格全綠的
# run,就沒有任何 commit 夠格。**
#
# 第一個撞到的人會看到「無法證明夠格」而不是「已證明不夠格」——**那個區分
# 就是為了這一刻做的**:去補證據,不要去翻自己的碼。要發版就帶逃生門並寫
# 明理由,那一次會被記進 .git/ai-b/release-gate-bypass.log。
#
# ## ★ 這道閘還沒有在真的 push 到 GitHub 上跑過
#
# **這句在第一次真推之前都該留著,不准軟化。** 38 支測試全程用**假的 `gh`**;
# 真 git 那一組(`RealGitTagTest`)推的是**本機 bare repo**。
# **本機 bare repo 不是 GitHub**——真遠端那一段跟本機治具的差距不是理論:
# 08-15 同一天,push / workflow_dispatch / 推 tag 三種觸發全部失效,而本機
# 治具全綠。治具驗的是判準邏輯,不是「它在真環境會不會被叫到」。
#
# ## 為什麼這道閘要存在:v0.20.0-alpha.1 就是它的實例
#
# 2026-08-15,the maintainer 直令發版,而**那一版沒有跨平台 CI 證據**(同上,拿不到)。
# 發版席把這件寫進 CHANGELOG 第一段與 tag 訊息,沒有寫成「已驗證」。
#
# ★ **如果這道閘早兩小時修好並裝上,它會擋住那一次發版。**
#   那不是假設,是同一天、同一個 repo、同一批人身上發生的事。
#   一個沒有機器背書就發出去的版本,正是這道閘要防的東西——
#   而它發得出去,只因為閘還在分支上沒裝上。
#
# ## 直接使用(不經 hook)
#
#   release-tag-gate.sh --commit <sha>     查某個 commit 夠不夠格被 tag
#   release-tag-gate.sh --audit-tags       盤點現有所有 tag(這次的六個就是這樣查出來的)
set -uo pipefail

TOP="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
WORKFLOW="${AIB_GATE_WORKFLOW_FILE:-$TOP/.github/workflows/test.yml}"

die() { printf '%s\n' "$@" >&2; exit 1; }
# check_commit 內一律用 fail:它必須「回報不合格」而不是「當場結束程式」,
# 否則 pre-push 迴圈第一個不合格的 tag 就會把後面的提示訊息一起帶走。
fail() { printf '%s\n' "$@" >&2; return 1; }

# --- 一、完整矩陣的格子從 workflow 現讀,不在這裡重寫一份 -------------------
#
# 硬寫一份 job 名清單=第二真相源:有人在 workflow 加一格,這裡不會知道,
# 閘門就會用舊的、比較鬆的標準放行。改成現讀,加格子自動變嚴。
# 讀不出來就擋(不是退回寬鬆預設)。
#
# ★ 2026-08-15 `fed5300` 實錯:workflow 把發版矩陣從 `full='{...}'` 改成
#   `release='{...}'` 並補一行 `full="$release"` 相容。**對 workflow 完全等價,
#   對這裡是全盲**——`full=` 後面接的是雙引號變數不是字面 JSON,正規式一個
#   字都比對不到,於是每個 commit 都變成「讀不出完整矩陣」。
#   實測:給它一次四格全綠的 run,仍然 rc=2。
#
#   ★★ 為什麼沒有任何測試轉紅:38 支全部餵 `FIXTURE_WORKFLOW`(檔內常數),
#   **沒有一支讀真正的 .github/workflows/test.yml**。治具是一份不會跟著現況
#   變的副本——跟今天記過的「手抄對照組」「寫死的近似字串」同一族:
#   **一個東西宣稱它在比對,而它比對的是一份不會跟著變的東西。**
#   修法附一支讀真檔的測試(RealWorkflowParseTest),那才是會紅的那一支。
#
#   名字順序=優先序:release 是「發版要跑的那組」的現行名,full 是舊名。
# ★ 第 64 項(the maintainer 09-12 11:4x 裁,大綱第 27 項的落地):矩陣閘改認**自架格**。
#   workflow 的 plan 若有 `selfhosted='{…}'` 字面 ⇒ 那組是**必要格**(a CI machine + a CI machine),全綠即放行;
#   `release`/`full` 那組託管四格降為**參考格**:缺席(帳單擋住沒起跑)不算紅,紅了只印一行不擋
#   (託管四格保留給 release workflow 用,哪天帳單回來它們的燈自然回來)。
#   沒有 `selfhosted` 字面的 workflow(治具、舊版)⇒ 行為逐字不變:仍要求 release/full 全綠。
#   ★ 失效方向:selfhosted 字面存在但解不出/空 ⇒ 擋(讀不出必要格不退回寬鬆預設)。
# $1 = required(必要格)| hosted(參考格)
expected_jobs() {
  python3 - "$WORKFLOW" "${1:-required}" <<'PY'
import json, re, sys
try:
    raw = open(sys.argv[1], encoding="utf-8").read()
except OSError as exc:
    print("READ_FAIL %s" % exc, file=sys.stderr); sys.exit(1)
mode = sys.argv[2]


def code_only(src):
    """剝掉 YAML 註解,只留會被 workflow 真的執行到的字。

    ★ qa @00000 09-12 注入審 H2 實測:本函式以前掃整份原文,於是在真字面**之前**
      插一行 `# 例:selfhosted='{"include":[{"os":"ubuntu-latest","python":"3.12"}]}'`
      ⇒ 必要格塌成一格 ubuntu-latest/py3.12、閘 rc=0,而那一格正是每次 push 的 fast run
      就會產生的燈 ⇒ **只跑過一格的顆就能過發版閘**。同手法對舊版 `release` 字面一樣成立
      (既存病),但本檔與 handbook 的說明文字都在示範這個寫法,踩到的機率是升高的。
    ★ 註解的判準照 YAML:`#` 只有在**行首或前面是空白**時才開始註解,`a#b` 不是註解;
      引號內的 `#` 不算(單引號字面 JSON 跨行,狀態要跨行維持)。
    ★ 失效方向:引號狀態若被古怪的字弄亂,結果是**找不到字面** ⇒ NO_MATRIX_LITERAL ⇒ 擋。
      庫內先例:aib_testlib.code_only(同一個形狀,Python 那一層)。
    """
    out, quote, i, n = [], None, 0, len(src)
    while i < n:
        ch = src[i]
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch; out.append(ch); i += 1; continue
        if ch == "#" and (not out or out[-1] in " \t\n"):
            while i < n and src[i] != "\n":
                i += 1
            continue                      # 換行本身留給下一圈
        out.append(ch); i += 1
    return "".join(out)


text = code_only(raw)

# 抓 plan job 裡的字面 JSON。單引號包住,可跨行。
# ★ 只認**單引號包住的字面值**:`full="$release"` 那種變數轉指不算,
#   因為這裡讀的是檔案不是 shell,展開不了。
HOSTED = ("release", "full")
SELF = ("selfhosted",)
has_selfhosted = re.search(r"selfhosted='(\{.*?\})'", text, re.S)


def cells(names):
    """回第一個命中的名字解出來的格子清單;一個名字都沒命中回 None。格式壞=擋。"""
    for name in names:
        m = re.search(name + r"='(\{.*?\})'", text, re.S)
        if not m:
            continue
        try:
            matrix = json.loads(re.sub(r"\s+", "", m.group(1)))
        except json.JSONDecodeError as exc:
            print("BAD_JSON %s(%s)" % (exc, name), file=sys.stderr); sys.exit(1)
        got = matrix.get("include") or []
        if not got:
            print("EMPTY_MATRIX %s" % name, file=sys.stderr); sys.exit(1)
        out = []
        for c in got:
            if "os" not in c or "python" not in c:
                print("BAD_CELL %r" % (c,), file=sys.stderr); sys.exit(1)
            # 必須與 workflow 的 `name: ${{ matrix.os }} / py${{ matrix.python }}` 一致
            out.append("%s / py%s" % (c["os"], c["python"]))
        return out
    return None


if mode == "required":
    names = SELF if has_selfhosted else HOSTED
    want = cells(names)
    if want is None:
        print("NO_MATRIX_LITERAL 找過這些名字:%s(要的是 <名字>='{...}' 這種"
              "單引號字面 JSON;`full=\"$release\"` 這種變數轉指讀不到;"
              "註解裡的字面不算)" % ", ".join(names), file=sys.stderr)
        sys.exit(1)
    for c in want:
        print(c)
elif mode == "hosted":
    # 沒有自架格 ⇒ 沒有「參考格」這個概念(舊行為:全部都是必要格)
    if not has_selfhosted:
        sys.exit(0)
    # ★ qa H1:參考格要**扣掉必要格**。不扣的話 release 字面裡的 a CI machine/a CI machine 也會被當成
    #   託管格印出來,必要格全綠時就會印「託管參考格綠: a CI machine … a CI machine …」——
    #   把自己的機器講成 GitHub 的機器,而 handbook §1.9.1 寫的是「四個託管格」。
    required = cells(SELF) or []
    for c in (cells(HOSTED) or []):        # 有自架格、沒託管格:參考集合為空,合法
        if c not in required:
            print(c)
else:
    print("BAD_MODE %s" % mode, file=sys.stderr); sys.exit(1)
PY
}

# --- 二、查這個 commit 有沒有一次「跑滿全矩陣且全綠」的 run ----------------
#
# 回傳三態(2026-08-15 加,原本只有 0/1):
#
#   0  夠格          有一次 run 跑滿完整矩陣且全綠
#   1  已證明不夠格  **證據拿到了**,而它顯示這個 commit 沒達標 → 去修碼
#   2  無法證明夠格  **證據拿不到**(gh 沒裝/查詢失敗/查無 run/矩陣讀不出)
#                    → 去等、或換一個證據來源
#
# ★ 為什麼要分:**兩種都該擋,但處置完全相反。** 原本六條失敗路徑訊息各自
# 不同,但**回傳碼一律是 1**,於是呼叫端(尤其 --audit-tags)分不出來,把
# 「我查不到」講成「你不夠格」。
#
# 08-15 實測撞出來的:那天 GitHub Actions 額度用完,`workflow_dispatch` 兩秒
# 內死、零步驟、無 log,連**六分鐘前綠過的那次在我方零改動下重跑也紅**。
# 那時閘門會說「這個 commit 沒有任何 tests run」——**一句真話,回答的卻是
# 另一個問題**,而讀訊息的人會去翻自己的碼。
#
# ★ 這正是本閘檔頭已經記過兩次的同一個病(短 sha 那次、annotated tag 那次):
#   **一個真的結論配一個錯的理由,會讓下一個人往錯的方向修。** 第三次了,
#   所以這次把它釘進回傳碼,不只是訊息措辭——**措辭會被改掉,回傳碼有測試。**
GATE_QUALIFIED=0
GATE_UNQUALIFIED=1
GATE_UNPROVEN=2

check_commit() {
  local sha="$1" label="$2"
  local want ok_cells run_ids

  # ★ 短 sha 要先正規化。`gh run list --commit` 只吃完整 40 碼。
  #
  # 沒有這段時,`--commit 21f530c`(檔頭第 59 行公開的用法)回的是
  # 「這個 commit 沒有任何 tests run」——**一個真的訊息在回答另一個問題**,
  # 正好是這道閘自己要對付的那個病,發生在它自己身上。指揮席審出來的。
  #
  # ★★ **不得用長度當條件。** annotated 標籤物件的 sha 也正好是 40 碼,
  #    只在「長度不是 40」時正規化,等於把標籤物件整類漏掉——那正是 P0-1
  #    的同一個病走第三條路。
  #
  #    這支檔已經記著「同檔兩條路徑不一致」的教訓(pre-push 沒解、
  #    --audit-tags 解了),當時修了兩條,**第三條靠長度條件躲過去**。
  #    實測(真 annotated tag):
  #      --commit 646e3554…(40 碼標籤物件)
  #        → 「commit 646e3554… 沒有任何 tests run」
  #        而它指到的 commit 可能全綠,閘門從頭到尾沒有看過它。
  #
  #    fail-closed 不會放行壞版本,但**它用錯誤的理由擋**,而錯誤的理由會
  #    訓練出「發版一律帶逃生門」的習慣(見檔頭 P0-1 那段)。
  #
  # 一律走 rev-parse ^{commit}:commit 給進去回自己,標籤物件回它指到的
  # commit,短 sha 展開,解不出來就擋。四種情形一條路徑。
  local full
  if full="$(git rev-parse --verify --quiet "${sha}^{commit}" 2>/dev/null)" \
     && [ -n "$full" ]; then
    sha="$full"
  elif [ "${#sha}" -ne 40 ]; then
    fail "" "✖ 發版閘:$label 給的不是完整 40 碼 sha,而且本地解不出來" \
        "  給的是:$sha" \
        "  gh run list --commit 只吃完整 sha;解不出來就無法查證,擋下。" \
        "  分類:無法證明夠格(不是已證明不夠格)——這是「我看不到」不是「你不行」。"
    return "$GATE_UNPROVEN"
  fi
  # 40 碼但本地解不開(例如只存在於遠端)就沿用原值:查無 run → 擋下。

  if ! command -v gh >/dev/null 2>&1; then
    fail "" "✖ 發版閘:找不到 gh,無法查 CI 結論——擋下 $label" \
        "  取不到證據一律擋,不預設通過(HANDOFF §六)。" \
        "  裝: brew install gh && gh auth login" \
        "  分類:無法證明夠格——證據來源不存在,與這個 commit 的品質無關。"
    return "$GATE_UNPROVEN"
  fi

  if ! want="$(expected_jobs required)"; then
    fail "" "✖ 發版閘:讀不出完整矩陣,擋下 $label" \
        "  來源:$WORKFLOW" \
        "  閘門不知道該要求哪些格子時一律擋,不退回寬鬆預設。" \
        "  分類:無法證明夠格——連「該要求什麼」都讀不到,更不可能評估。"
    return "$GATE_UNPROVEN"
  fi

  local runs
  if ! runs="$(gh run list --commit "$sha" --limit 20 \
                 --json databaseId,status,conclusion,workflowName 2>&1)"; then
    fail "" "✖ 發版閘:gh 查詢失敗,擋下 $label" "  $runs" \
        "  (常見:gh 未登入 → gh auth status;或 Actions 額度用完——" \
        "   08-15 實測:額度耗盡時 dispatch 兩秒內死、零步驟、無 log)" \
        "  分類:無法證明夠格——**去等或換來源,不要去翻自己的碼。**"
    return "$GATE_UNPROVEN"
  fi

  run_ids="$(printf '%s' "$runs" | python3 -c '
import json, sys
try:
    runs = json.load(sys.stdin)
except Exception:
    sys.exit(1)
for r in runs:
    if r.get("workflowName") != "tests":
        continue
    print("%s\t%s\t%s" % (r.get("databaseId"), r.get("status"), r.get("conclusion")))
')" || {
    fail "" "✖ 發版閘:gh 回的不是可解析的 JSON,擋下 $label" \
        "  分類:無法證明夠格——拿到的東西不是證據。"
    return "$GATE_UNPROVEN"
  }

  if [ -z "$run_ids" ]; then
    fail "" "✖ 發版閘:$label 指到的 commit ${sha:0:12} 沒有任何 tests run" \
        "  沒跑過 ≠ 沒問題。" "" \
        "  ★ 怎麼生出那個 run(**推 main 不會**——見下):" \
        "      gh workflow run tests --ref $sha" \
        "    或等每日 02:00(CST)的排程那次,它跑的也是完整矩陣。" "" \
        "    自 5d48ed6 起,推 main 只跑一格,**永遠不會**產生完整矩陣的 run。" "" \
        "  分類:無法證明夠格——**沒有 run ≠ 有 run 而它是紅的。**" \
        "    矩陣制之前的舊 tag(v0.2–v0.10)全部落在這一類:那個年代沒有" \
        "    這種 run 可查,說它們「標在不夠格的 commit 上」是講錯了。" \
        "  **去等或換來源,不要去翻自己的碼。**"
    return "$GATE_UNPROVEN"
  fi

  # 逐個 run 找「全矩陣且全綠」的那一個。找到就放行。
  #
  # ★ saw_evidence:有沒有**真的讀到過**某個 run 的 job 集合。
  #   讀到了才有資格說「已證明不夠格」;全部都還在跑、或 job 清單讀不到,
  #   那是「無法證明」——**沒有證據不等於反面的證據**。
  # 第 64 項:參考格(託管四格)只用來印一行,不進 want。讀不出參考格不擋(它不是必要條件)。
  local hosted_want
  hosted_want="$(expected_jobs hosted 2>/dev/null)" || hosted_want=""
  local found_complete=0 saw_evidence=0 detail=""
  while IFS=$'\t' read -r rid status conclusion; do
    [ -n "$rid" ] || continue
    if [ "$status" != "completed" ]; then
      detail="$detail
    run $rid: 還在跑($status)——沒跑完不算綠(這一筆不構成證據)"
      continue
    fi
    local jobs
    jobs="$(gh run view "$rid" --json jobs 2>/dev/null)" || {
      detail="$detail
    run $rid: 讀不到 job 清單"
      continue
    }
    ok_cells="$(printf '%s' "$jobs" | python3 -c '
import json, sys
try:
    jobs = json.load(sys.stdin).get("jobs") or []
except Exception:
    sys.exit(1)
for j in jobs:
    if j.get("conclusion") == "success":
        print(j.get("name", ""))
')" || {
      detail="$detail
    run $rid: job JSON 解析失敗"
      continue
    }

    # ★ 先問「這個 run 到底有沒有跑過矩陣」,再問「跑出來綠不綠」。
    #   指揮席 08-15 實測的形狀:Actions 額度用完時 plan job 兩秒內死、零步驟、
    #   無 log——`run list` 查得到這個 run,`run view` 也回得出 jobs,但**矩陣
    #   一格都沒有出現過**。那種 run 不是「這個 commit 沒達標」的證據,它是
    #   「這次根本沒評估過這個 commit」。**沒跑 ≠ 跑了沒過。**
    local all_cells any_cell=0
    all_cells="$(printf '%s' "$jobs" | python3 -c '
import json, sys
try:
    jobs = json.load(sys.stdin).get("jobs") or []
except Exception:
    sys.exit(1)
for j in jobs:
    print(j.get("name", ""))
')" || all_cells=""
    while IFS= read -r cell; do
      [ -n "$cell" ] || continue
      printf '%s\n' "$all_cells" | grep -qxF "$cell" && any_cell=1
    done <<<"$want"
    if [ "$any_cell" != 1 ]; then
      detail="$detail
    run $rid: 完整矩陣一格都沒出現過——這次沒有評估過這個 commit(不構成證據)"
      continue
    fi

    local missing=""
    while IFS= read -r cell; do
      [ -n "$cell" ] || continue
      printf '%s\n' "$ok_cells" | grep -qxF "$cell" || missing="$missing $cell"
    done <<<"$want"

    saw_evidence=1     # 這個 run 跑完了、job 清單也讀到了 = 拿到證據
    if [ -z "$missing" ]; then
      found_complete=1
      printf '✓ 發版閘:%s 的 commit %s 必要格全綠(run %s)\n' \
             "$label" "${sha:0:12}" "$rid" >&2
      # 第 64 項:託管參考格的狀態**說一句**,不擋。缺席(帳單擋住沒起跑)≠ 紅;紅了也只是紅在託管。
      if [ -n "$hosted_want" ]; then
        local h_red="" h_absent="" h_green=""
        while IFS= read -r cell; do
          [ -n "$cell" ] || continue
          if printf '%s\n' "$ok_cells" | grep -qxF "$cell"; then h_green="$h_green $cell"
          elif printf '%s\n' "$all_cells" | grep -qxF "$cell"; then h_red="$h_red $cell"
          else h_absent="$h_absent $cell"; fi
        done <<<"$hosted_want"
        [ -n "$h_red" ]    && printf '△ 發版閘:託管參考格**紅**(不擋,自架格已全綠):%s\n' "$h_red" >&2
        [ -n "$h_absent" ] && printf '△ 發版閘:託管參考格缺席(不算紅;帳單擋住沒起跑就是這個樣子):%s\n' "$h_absent" >&2
        [ -n "$h_green" ]  && printf '  發版閘:託管參考格綠:%s\n' "$h_green" >&2
      fi
      break
    fi
    detail="$detail
    run $rid: 缺這幾格的綠 →$missing"
  done <<<"$run_ids"

  if [ "$found_complete" != 1 ]; then
    if [ "$saw_evidence" = 1 ]; then
      fail "" "✖ 發版閘:$label 指到的 commit ${sha:0:12} 沒有「跑滿完整矩陣且全綠」的 run" \
          "$detail" "" \
          "  ★ 注意:run 層寫著 success 不代表夠格。推 main 若只跑一格," \
          "    那個綠只回答了「那一格過不過」。本閘看的是 job 集合。" \
          "  分類:**已證明不夠格**——證據拿到了,而它顯示沒達標。去修碼或補跑。" \
          "  要求的格子:"
    else
      fail "" "✖ 發版閘:$label 指到的 commit ${sha:0:12} 查不到可用的證據" \
          "$detail" "" \
          "  有 run,但沒有任何一個「跑完且 job 清單讀得到」——所以本閘" \
          "  **沒有評估過**這個 commit。" \
          "  分類:無法證明夠格——**去等或換來源,不要去翻自己的碼。**" \
          "  參考:要求的格子(等這些跑完再來):"
    fi
    # 逐行印,不用 $(...) 展開——job 名含空白,word splitting 會把
    # 「ubuntu-latest / py3.9」拆成三項。
    printf '%s\n' "$want" | sed 's/^/    /' >&2
    [ "$saw_evidence" = 1 ] && return "$GATE_UNQUALIFIED"
    return "$GATE_UNPROVEN"
  fi
  return "$GATE_QUALIFIED"
}

# --- 三、直接呼叫模式 ------------------------------------------------------
case "${1:-}" in
  --commit)
    [ -n "${2:-}" ] || die "用法:$0 --commit <sha>"
    # ★ 必須把 rc 原樣傳出去,**不可以寫成 `|| exit 1`**。
    #   直接寫 exit 0 會把「不合格」變成「合格」(本閘要擋的病自己犯一次);
    #   而 `|| exit 1` 是同一個病的另一半:它把三態壓回兩態,**訊息說「無法
    #   證明夠格」而 exit code 說「已證明不夠格」,兩個出口互相矛盾**。
    #   2026-08-15 實測抓到:noruns / running / queryfail 三種都印對了分類,
    #   rc 卻一律是 1——分類做在訊息裡而沒做在回傳碼裡,等於沒做。
    check_commit "$2" "${3:-commit $2}"
    exit "$?"
    ;;
  --audit-tags)
    # 盤點現有 tag。這次那六個就是這樣查出來的。
    #
    # ★ 三態,不是兩態(2026-08-15 改)。原本對**每一種**失敗都印
    #   「← 標在不夠格的 commit 上」,於是兩件意義完全不同的事被講成同一件:
    #
    #     紅 已證明不夠格  有 run,而它顯示必要格沒有全綠(v0.14–v0.19)
    #                      ★ 第 64 項後「必要格」= workflow 的 selfhosted 那組;沒有該字面的舊 workflow 仍是完整矩陣
    #     灰 無法證明夠格  根本查不到可用的證據(v0.2–v0.10,矩陣制之前那批)
    #
    #   舊寫法會對著 v0.2 說「標在不夠格的 commit 上」,**而那個年代沒有這種
    #   run 可查**——那是一句真的紅燈配一個假的理由,會讓人去翻一個當時根本
    #   不存在的東西。
    rc=0
    red=0
    grey=0
    green=0
    for tag in $(git tag -l --sort=version:refname); do
      sha="$(git rev-list -n1 "$tag")"
      check_commit "$sha" "tag $tag" >/dev/null 2>&1
      case "$?" in
        "$GATE_QUALIFIED")
          printf '  綠 %s\n' "$tag"; green=$((green + 1)) ;;
        "$GATE_UNQUALIFIED")
          printf '  紅 %s  ← 已證明不夠格(有證據,而證據顯示沒達標)\n' "$tag"
          red=$((red + 1)); rc=1 ;;
        *)
          printf '  灰 %s  ← 無法證明夠格(查不到可用的證據,**不等於它是壞的**)\n' "$tag"
          grey=$((grey + 1)); rc=1 ;;
      esac
    done
    printf '\n  合計:綠 %d / 紅 %d(已證明不夠格) / 灰 %d(無法證明夠格)\n' \
           "$green" "$red" "$grey" >&2
    printf '  ★ 紅與灰都不該發版,但**處置相反**:紅去修碼,灰去補證據。\n' >&2
    exit "$rc"
    ;;
  --help|-h)
    sed -n '2,70p' "$0"; exit 0 ;;
esac

# --- 四、pre-push 模式:stdin 是 <local_ref> <local_sha> <remote_ref> <remote_sha> ---
zero="0000000000000000000000000000000000000000"
blocked=0

while read -r local_ref local_sha remote_ref remote_sha; do
  [ -n "${remote_ref:-}" ] || continue
  case "$remote_ref" in
    refs/tags/*) ;;
    *) continue ;;            # 只管 tag,分支不擋
  esac
  # 刪 tag 放行:刪除不會讓任何人裝到壞版本。
  [ "$local_sha" = "$zero" ] && continue

  tag="${remote_ref#refs/tags/}"

  # ★ annotated tag 送進來的是**標籤物件**的 sha,不是它指到的 commit。
  #
  # 真 git 實測(2026-08-15,拋棄式 repo + 真 push):
  #   annotated   local_sha=354f87d4…  git cat-file -t → tag
  #                                    git rev-list -n1 → 29f98381…(commit)
  #   lightweight local_sha=29f98381…  git cat-file -t → commit
  #
  # 本 repo 六個 tag(v0.14–v0.19)`git cat-file -t` **全部回 tag**,所以
  # 少了這一行,每一次真實發版都會被擋,而且理由是錯的
  # ——`gh run list --commit <標籤物件sha>` 永遠查無 run,閘門會說
  # 「這個 commit 沒跑過 CI」,那句話在回答另一個問題。
  #
  # ★★ 比「擋錯」更該怕的是它訓練出來的習慣:每次發版都被擋 → 每次都帶
  #    AIB_RELEASE_GATE_REASON → 閘門還在、還會印訊息,但沒有任何一次真的
  #    評估過 CI。**一個永遠被繞過的閘,比沒有閘更危險,因為它讓人以為有。**
  #
  # 同一支檔的 --audit-tags 早就用 rev-list 解對了(見上),pre-push 這條沒有
  # ——同檔兩條路徑不一致,是 pm 席用真 git 差分實驗抓到的,不是讀碼推論。
  #
  # 解不出來就沿用原值:那個值查不到 run,於是走 fail-closed 擋下,
  # 不會因為解析失敗而放行。
  local_sha="$(git rev-list -n1 "$local_sha" 2>/dev/null \
               || printf '%s' "$local_sha")"

  if [ -n "${AIB_RELEASE_GATE_REASON:-}" ]; then
    # 純空白不算理由。`AIB_RELEASE_GATE_REASON=" "` 原本能過,那等於一個
    # 不用打字的 --force——逃生門刻意做成「要打字說明」就是為了留下為什麼。
    if [ -z "$(printf '%s' "$AIB_RELEASE_GATE_REASON" | tr -d '[:space:]')" ]; then
      fail "" "✖ 發版閘:逃生門要一個真的理由,不能是空白" \
          "  AIB_RELEASE_GATE_REASON=\"為什麼這次要繞過\" git push ..."
      blocked=1
      continue
    fi
    # ★ 記檔,不只印 stderr。stderr 會被捲掉,而逃生門的**全部價值**就是
    #   事後查得到誰在什麼時候用什麼理由繞過——只印等於沒留。
    #   姊妹閘 docs-retire-gate.sh 是 append 進 RETIRED.md,同一條語意。
    #
    #   落在 git 目錄下而不是 repo 內的追蹤檔:寫追蹤檔會讓 push 完工作樹
    #   立刻變髒,那個副作用比它換來的可見度更容易讓人把整個逃生門關掉。
    #   本 repo 現行假設是單機單人(大綱 EN 節),本機檔足以回答「誰在什麼
    #   時候為什麼繞過」。要跨機共享是治理決定,不在本閘。
    bypass_log="$(git rev-parse --git-common-dir)/ai-b/release-gate-bypass.log"
    mkdir -p "$(dirname "$bypass_log")" 2>/dev/null
    printf '%s\t%s\t%s\t%s\n' \
           "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$tag" "${local_sha:0:12}" \
           "$AIB_RELEASE_GATE_REASON" >> "$bypass_log" 2>/dev/null \
      || printf '⚠ 發版閘:繞過紀錄寫不進 %s(仍放行,該次繞過將查不到)\n' \
                "$bypass_log" >&2
    # ★ 取捨具名(F3,pm2 席問、第一艦長裁):**寫不進日誌仍然放行。**
    #   代價明擺著——那一次繞過事後查不到,只剩一行會被捲掉的 stderr。
    #   刻意選這一邊:因為日誌寫不進(唯讀 fs、權限、磁碟滿)就擋掉 push,
    #   會讓人把整個逃生門關掉,而**沒有逃生門的閘會被整支拔掉**,那比漏記
    #   一次糟。同一條推理見檔頭「沒有逃生門的閘會被整個關掉,那比放行更糟」。
    #
    #   **這不是疏漏,是選過的一邊。** 下一棒若要改成 fail-closed,請連同
    #   上面那個後果一起評估,不要當成順手修一個看起來像 bug 的行為。
    printf '\n⚠ 發版閘被逃生門放行:%s\n  理由:%s\n  已記錄:%s\n\n' \
           "$tag" "$AIB_RELEASE_GATE_REASON" "$bypass_log" >&2
    continue
  fi

  check_commit "$local_sha" "tag $tag"
  case "$?" in
    "$GATE_QUALIFIED")   : ;;
    "$GATE_UNQUALIFIED") blocked=1; had_unqualified=1 ;;
    *)                   blocked=1; had_unproven=1 ;;
  esac
done

if [ "$blocked" != 0 ]; then
  # ★ 收尾也要分類:讀到最後一行才決定下一步的人,不該把「我查不到」讀成
  #   「我的碼不夠好」。兩種都擋,但下一步相反。
  if [ "${had_unqualified:-0}" = 1 ]; then
    printf '\n  下一步(已證明不夠格):修碼,或對修好的 commit 補跑完整矩陣。\n' >&2
  fi
  if [ "${had_unproven:-0}" = 1 ]; then
    printf '\n  下一步(無法證明夠格):**先確認證據拿不拿得到,不要先去改碼。**\n' >&2
    printf '    gh auth status;gh run list --limit 1\n' >&2
    printf '    Actions 額度用完時,dispatch 會兩秒內死、零步驟、無 log(08-15 實測)。\n' >&2
  fi
  printf '\n  真的要推:AIB_RELEASE_GATE_REASON="理由" git push ...(理由會被記檔)\n\n' >&2
  exit 1
fi
exit 0
