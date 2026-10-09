#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# 交接單瘦身閘(2026-08-15,the maintainer 直令「寫成程式 hook,不要只靠 ai 記」)
#
# ## 它擋什麼
#
# 1. **交接單超過行數上限** → 擋下。沒有硬上限的話「保持簡潔」只是願望:
#    2026-08-15 當天早上重寫成 166 行,幾小時後就長到 302 行,因為每一條
#    新規則都「很重要」。上限的作用是逼人當下決定什麼該退場,而不是三個月
#    後再來一次大掃除。
#
# 2. **刪了交接單的內容卻沒有留墓碑** → 擋下。git 把每一版都存著,所以刪
#    掉不等於消失;真正的問題是六週後沒人知道那段東西存在過、也不知道要
#    搜什麼字。`docs/RETIRED.md` 一行索引就解決,但**要有人記得寫**——
#    這支 hook 就是那個「記得」。
#
# ## 安裝(交付進 repo 不等於啟用)
#
# ★ **裝到 `core.hooksPath` 指的那個目錄,不是 `.git/hooks/`。** 本 repo 已設
#   `core.hooksPath=.git/ai-b/hooks`,**設了之後 git 就完全不看 `.git/hooks/`** ——
#   裝在那裡會得到一個永不執行、也永不報錯的 hook(同一個坑的原文在
#   `src/policy/hooks/release-tag-gate.sh:43`;2026-09-27 由 A 包再踩一次,
#   所以現在有 `tests/test-hook-install-path-guard.py` 在守)。
#
#   DIR="$(git config core.hooksPath || printf '%s/hooks' "$(git rev-parse --git-common-dir)")"
#   ln -sf ../../src/policy/hooks/docs-retire-gate.sh "$DIR/pre-commit"
#
# 隨時可退:移除那個目錄裡的 pre-commit 即完全復原。本閘零狀態檔、零線上
# 相依,不存在「壞掉之後沒人能解」的路徑(沿用 trust-git-gate 的同一條
# 紀律:守衛不得把守衛自己關在門外)。
#
# ★ 多個 worktree(工作樹副本)共用同一個 hooks 目錄(本 repo = `core.hooksPath`
#   指的 .git/ai-b/hooks),所以裝一次全部生效。
#
# ## 逃生門
#
#   AIB_DOCS_GATE_REASON="理由" git commit ...
#
# 帶理由就放行,並把理由印到 stderr 留痕。**刻意做成「要打字說明」而不是
# 「加個 --force」**:沒有逃生門的閘會被整個關掉,那比放行更糟;但逃生門
# 要留下你為什麼用它。
set -euo pipefail

HANDOFF="docs/HANDOFF.md"
RETIRED="docs/RETIRED.md"
MAX_LINES="${AIB_HANDOFF_MAX_LINES:-300}"

staged="$(git diff --cached --name-only 2>/dev/null || true)"

# --- 零、正本樹只准讀,要改檔就開自己的工作樹副本 --------------------------
#
# root3 08-15 裁,而且是他自己造成衝突後的直接教訓:問題不是「有例外」,
# 是「例外的範圍沒講清楚」。他當時的理由是「我是發版席,我在正本樹上」——
# 那句話對前半(需要在正本樹跑關卡)成立,對後半(所以可以在這裡改檔)
# **完全不成立**。
#
# 所以切法不是「哪一席例外」,是分清楚讀與寫:
#   正本樹做的是讀——跑測試、跑發版關卡、merge、tag
#   要改任何檔案,一律開自己的工作樹副本,誰都一樣
#
# 例外只有發版動作本身會碰的那幾支(版號三載體與釘選檔)。
# ★★ docs/reports/RELEASE-ESCAPES.md 必須在這裡,否則兩道守衛互相打架:
#    `tools/aib-release.sh` 走逃生門時會把理由寫進那份檔,並且**要求它已提交**
#    (未提交的話,tag 會標在一棵不含本次逃生理由的樹上)。而發版是在**正本樹**
#    跑的,於是本閘會擋掉那次 commit ——**那個流程在正本樹裡結構上走不完。**
#    這是 2026-08-17 dogfood 第一次真跑 --release 時撞到的,兩輪覆審都沒抓到,
#    因為它不在任何一支腳本裡面,它在**兩支腳本的交界**。
#    它本來就屬於這一類:發版動作的產物,與 VERSION/CHANGELOG 同級。
# ★★ 2026-09-05:這一行本來是手抄的清單,現在改成**從版號載體表導出**。
#    舊值逐字保留在這裡當墓碑:
#      RELEASE_FILES="VERSION README.md CHANGELOG.md src/dataguard/pins.py docs/reports/RELEASE-ESCAPES.md"
#    它漏了 `deploy/line/manifest-v1.json` —— 而 `tests/test-version.py` 要求那一支跟上 VERSION。
#    照這份清單做則版本臂紅,照版本臂做則本閘擋下 commit ⇒ **每一次發版都得走逃生口**
#    (a6b2e 發 v0.55 當場撞到,缺陷七)。而常態化的逃生口等於這道閘不存在。
#    ★ 修法不是「把 manifest 補進來」:補進來之後兩份清單還是兩份,只是今天剛好一樣。
#      「都引用同一份」與「各自抄了一份剛好一樣的」在版控上逐字相同,差別只在它們哪一天不一樣了。
#    新值 = 載體表(`tests/test-version.py` 的 VERSION_CARRIERS)+ 下面這份具名的發版產物。
#
# 發版產物:不帶版號,所以不是載體,不會出現在載體表裡,但發版動作會寫它。
# 只有這一支,而且要具名——不具名就等於把「不是載體的東西」偷渡進白名單。
RELEASE_ARTIFACTS="docs/reports/RELEASE-ESCAPES.md"

# 讀載體表。**讀不到就吵著失敗**,不得靜默回空清單:
# 回空的失效形狀是假的嚴格——正本樹連 VERSION 都改不動,而畫面上長得像「閘變嚴了」,
# 沒有一個字會說「我根本沒讀到表」。
read_version_carriers() {
  local top table
  top="$(git rev-parse --show-toplevel)" || return 1
  table="$top/tests/test-version.py"
  [ -f "$table" ] || {
    echo "✖ 發版白名單讀不到版號載體表:$table" >&2
    echo "  白名單是從那張表(VERSION_CARRIERS)導出的,表不在就沒有白名單可用。" >&2
    return 1
  }
  command -v python3 >/dev/null 2>&1 || {
    echo "✖ 找不到 python3,無法讀 $table 的 VERSION_CARRIERS" >&2
    return 1
  }
  python3 - "$table" <<'PYEOF'
import importlib.util
import sys

path = sys.argv[1]
spec = importlib.util.spec_from_file_location("aib_version_carriers", path)
if spec is None or spec.loader is None:
    sys.exit("✖ 載入不了版號載體表:%s" % path)
module = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(module)
except Exception as exc:                      # noqa: BLE001 —— 什麼壞法都要具名
    sys.exit("✖ 讀 %s 時炸了:%r" % (path, exc))
carriers = getattr(module, "VERSION_CARRIERS", None)
if not carriers:
    sys.exit("✖ %s 沒有 VERSION_CARRIERS,或它是空的" % path)
# 白名單在 bash 那邊是用空白切開、再用 glob 比對的。含空白的路徑會被切成兩項,
# 含 glob 字元的路徑會比它自己**多**比到別的檔 —— 兩種都是安靜的放寬。
# 這裡當場擋,不讓它們變成白名單。
unsafe = [c for c in carriers
          if (not c) or any(ch.isspace() for ch in c) or any(ch in c for ch in "*?[")]
if unsafe:
    sys.exit("✖ 載體表裡有不能安全當白名單的路徑(含空白或 glob 字元):%r" % unsafe)
print(" ".join(carriers))
PYEOF
}

is_main_worktree() {
  # 主工作樹的 git-dir 與 git-common-dir 相同;工作樹副本的 git-dir 是
  # .git/worktrees/<名>,兩者不同。這是判斷「我在不在正本樹」最直接的方式。
  local d c
  d="$(cd "$(git rev-parse --git-dir)" && pwd -P)"
  c="$(cd "$(git rev-parse --git-common-dir)" && pwd -P)"
  [ "$d" = "$c" ]
}

# 正本樹的正當工作會一次帶進大量別人的檔,那些不是「在正本樹改檔」:
#   merge(把兩條線的改動併起來)/ cherry-pick(挑單一 commit 過來)/
#   rebase(把改動搬到最新基礎上重放)/ revert(回退某個 commit)
# 這些狀態一律跳過。實測 rebase 本來就不觸發 pre-commit,但寫進來當防禦——
# **這一族的失敗會發生在別人發版的半路上**,不能靠「應該不會」。
in_git_operation() {
  local g; g="$(git rev-parse --git-dir)"
  [ -f "$g/MERGE_HEAD" ] || [ -f "$g/CHERRY_PICK_HEAD" ] \
    || [ -f "$g/REVERT_HEAD" ] || [ -d "$g/rebase-merge" ] \
    || [ -d "$g/rebase-apply" ]
}

if [ -z "${AIB_DOCS_GATE_REASON:-}" ] && [ -n "$staged" ] \
   && is_main_worktree && ! in_git_operation; then
  # 只在真的要用到白名單時才去讀表:工作樹副本的 commit 一次 python 都不會多跑。
  if ! carriers="$(read_version_carriers)"; then
    echo "" >&2
    echo "✖ 發版白名單導不出來,本閘不猜 —— 上面那行說了是哪一格。" >&2
    echo "" >&2
    exit 1
  fi
  RELEASE_FILES="$carriers $RELEASE_ARTIFACTS"
  offenders=""
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    case " $RELEASE_FILES " in
      *" $f "*) continue ;;
    esac
    offenders="$offenders  $f"$'\n'
  done <<EOF
$staged
EOF
  if [ -n "$offenders" ]; then
    echo "" >&2
    echo "✖ 正本樹只准讀,不准改檔" >&2
    echo "" >&2
    echo "  這幾支要改的話,請開自己的工作樹副本:" >&2
    printf '%s' "$offenders" >&2
    echo "" >&2
    echo "  正本樹的正當工作=跑測試、跑發版關卡、merge、tag。" >&2
    echo "  發版動作本身可改的只有:$RELEASE_FILES" >&2
    echo "" >&2
    echo "  開一個工作樹副本:" >&2
    echo "    git worktree add -b <分支名> ~/worktrees/<名> origin/main" >&2
    echo "" >&2
    echo "  真的要放行:AIB_DOCS_GATE_REASON=\"理由\" git commit ...(理由會被記檔)" >&2
    echo "" >&2
    exit 1
  fi
fi

# --- 以下只在交接單有被改到時作用;其他 commit 一個位元組都不碰 ------------
case " $staged " in
  *" $HANDOFF "*) ;;
  *) exit 0 ;;
esac

fail() {
  echo "" >&2
  echo "✖ 交接單瘦身閘擋下這次 commit" >&2
  echo "" >&2
  printf '%s\n' "$@" >&2
  echo "" >&2
  echo "  真的要放行:AIB_DOCS_GATE_REASON=\"理由\" git commit ..." >&2
  echo "" >&2
  exit 1
}

if [ -n "${AIB_DOCS_GATE_REASON:-}" ]; then
  # ★理由必須落在**累積且有人會看到**的檔案,不能只留在 stderr。
  # root3 08-15 裁:擔心「每次都打一個理由」是對的,但加使用次數統計是錯的
  # 解法(又一個要維護的機制,而且門檻該設多少沒人知道)。**正解是讓濫用
  # 自己現形**——連續五筆「趕時間」誰都看得出來,不需要統計。
  # 這與今天的主線教訓一致:靜默的東西沒人管,可見的東西自己會被管。
  ts="$(date '+%Y-%m-%d %H:%M')"
  who="${EZ2_SESSION_SIG:-${USER:-unknown}}"
  printf '| %s | %s | %s |\n' "$ts" "$who" "$AIB_DOCS_GATE_REASON" >> "$RETIRED"
  git add "$RETIRED"
  echo "⚠ 交接單瘦身閘已放行" >&2
  echo "  理由已記入 $RETIRED(不是只印在畫面上):$AIB_DOCS_GATE_REASON" >&2
  exit 0
fi

# --- 一、行數上限 -----------------------------------------------------------
# 讀**暫存區**的版本而不是工作目錄:commit 進去的是前者,兩者可能不同。
lines="$(git show ":$HANDOFF" 2>/dev/null | wc -l | tr -d ' ')"
if [ "$lines" -gt "$MAX_LINES" ]; then
  fail "  $HANDOFF 有 $lines 行,超過上限 $MAX_LINES 行。" \
       "" \
       "  這代表有東西該退場了。做法:" \
       "    1. 找出已經不是「現在的狀態」的段落(歷史、已執行完的裁定、過程)" \
       "    2. 在 $RETIRED 加一行墓碑(用症狀寫,不要用代號)" \
       "    3. 把那段從交接單刪掉" \
       "" \
       "  git 會留著全文,墓碑那一行負責讓人找得回來。"
fi

# --- 二、刪內容必須留墓碑 ---------------------------------------------------
# 判準=交接單有**淨減少的行**。純新增不需要墓碑,改寫(有加有減)也算刪。
removed="$(git diff --cached --numstat -- "$HANDOFF" | awk '{print $2+0}')"
added="$(git diff --cached --numstat -- "$HANDOFF" | awk '{print $1+0}')"
: "${removed:=0}" "${added:=0}"

if [ "$removed" -gt 0 ]; then
  case " $staged " in
    *" $RETIRED "*) ;;
    *)
      fail "  這次改動從 $HANDOFF 刪掉了 $removed 行(新增 $added 行)," \
           "  但同一個 commit 沒有動 ${RETIRED}。" \
           "" \
           "  刪掉的內容 git 還留著,問題是**沒有人知道要去找它**。" \
           "  請在 $RETIRED 加一行:那段講什麼(用症狀寫)、最後活在哪個 commit。" \
           "" \
           "  只是改錯字或調整排版?那也算刪——用逃生門並說明。"
      ;;
  esac
fi

exit 0
