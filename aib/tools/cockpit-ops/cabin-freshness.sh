#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# cabin-freshness.sh — 開艙前證明這棵 checkout 對 origin/main 是新鮮的。
#
# 裁定 docs/criteria/RULING-B14-cabin-open-freshness-0829.md
# 契約 docs/criteria/CONTRACT-B14-cabin-freshness-0829.md(字面與 rc 逐字釘在臂上)
#
# 用法:bash tools/cockpit-ops/cabin-freshness.sh <repo>
# rc:0=新鮮或逃生門放行 / 1=落後,擋 / 3=判不出來,擋
#
# ★★ 「判不出來」與「沒落後」是**兩個不同的結局**,不共用觀測值。
#   ls-remote 問不到、fetch 逾時、遠端答了但物件不在本地 —— 這些都不是安全,
#   是量不到。探測器的失效值不得等於它表示安全的那個值。
# ★★ 三個底層 rc 實測分得開(正常 0 / timeout 逾時 124 / 不可達 128),
#   而 `ls-remote` 對**不存在的 ref 是 rc=0 且輸出空** —— 所以判「拿到了」
#   要**同時**要求 40 位 hex,不能只看 rc。
set -uo pipefail

REPO="${1:-}"
# ★ 逾時刻意**寫死、不做成環境變數**(臂 09 抓到我原本放了一個沒進契約的旋鈕):
#   一、慢網路那個情境**逃生門已經涵蓋了** —— fetch 逾時會走「判不出來」,
#     帶理由就過得去,所以這個旋鈕是多餘的。
#   二、閘上的每一個旋鈕都是一種卡死它的方法(設成 1 就每次都判不出來)。
#     這一個的失效方向雖然是 fail-closed(只能卡死、不能放水),但多餘 + 沒記載
#     = 下一個人讀契約時看不到它,而它會改變閘的行為。
TIMEOUT_S=60
REASON="${AIB_OPEN_FLEET_FRESHNESS_REASON:-}"
TRACE="$HOME/.ez2-aib/state/cabin-open-gate.jsonl"

say(){ printf '%s\n' "$*" >&2; }

# B14-fix:舊逃生門名稱佔用了 cabin_resolve.sh 的保留命名空間。
# 不把舊名當相容別名；舊名單獨出現時大聲拒絕，讓既有操作不會靜默失效。
# ★ 舊名刻意在執行期組出，避免退役字面繼續留在產品與全庫掃描母體裡。
LEGACY_REASON_ENV="AIB_"'CABIN_FRESHNESS_REASON'
if [ "${AIB_OPEN_FLEET_FRESHNESS_REASON+x}" != "x" ] \
   && printenv "$LEGACY_REASON_ENV" >/dev/null 2>&1; then
  say "開艙擋下:新鮮度判不出來(你設的是舊名 $LEGACY_REASON_ENV,現在的名字是 AIB_OPEN_FLEET_FRESHNESS_REASON)"
  exit 3
fi

# ★ 逃生門刻意**不寫進 ~/.ez2-aib/logs/**:I1 收割器的語料來源逐條寫死含
#   `~/.ez2-aib/logs/*.jsonl`,理由寫進那個 glob 就會變成字典的候選語料,
#   而那個污染不會有任何一格叫。
trace_override(){
  local verdict="$1"
  mkdir -p "$(dirname "$TRACE")" 2>/dev/null || true
  AIB_B14_TRACE="$TRACE" AIB_B14_VERDICT="$verdict" AIB_B14_REASON="$REASON" \
  AIB_B14_REPO="$REPO" python3 - <<'PY' 2>/dev/null || true
import datetime, json, os
row = {
    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "gate": "cabin-freshness",
    "verdict": os.environ["AIB_B14_VERDICT"],
    "repo": os.environ["AIB_B14_REPO"],
    "reason": os.environ["AIB_B14_REASON"],
}
with open(os.environ["AIB_B14_TRACE"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
PY
}

# 逃生門:理由必須是**非空白**的字。空、未設、純空白一律擋 —— 不准無聲放行。
override_or_exit(){
  local verdict="$1" rc="$2"
  if [ -n "${REASON//[[:space:]]/}" ]; then
    trace_override "$verdict"
    say "開艙放行:逃生門 AIB_OPEN_FLEET_FRESHNESS_REASON"
    say "  理由 = $REASON"
    say "  留痕 = $TRACE"
    exit 0
  fi
  say "  真的要放行:AIB_OPEN_FLEET_FRESHNESS_REASON=\"理由\" 重跑(理由會被記檔)"
  exit "$rc"
}

# ★ 第一行**不得**沿用「落後」字樣:那會讓人以為量到了,而我們其實沒量到。
unknown(){
  say "開艙擋下:新鮮度判不出來($1)"
  say "  這是**判不出來**,不是沒落後。"
  override_or_exit unknown 3
}

[ -n "$REPO" ] || unknown "沒有給 repo 路徑"
cd "$REPO" 2>/dev/null || unknown "進不去 repo:$REPO"

STATUS_BEFORE="$(git status --porcelain 2>/dev/null)"
HEAD_SHA="$(git rev-parse HEAD 2>/dev/null)"
case "$HEAD_SHA" in
  [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) : ;;
  *) unknown "取不到 HEAD" ;;
esac

REMOTE_OUT="$(timeout "$TIMEOUT_S" git ls-remote origin refs/heads/main 2>/dev/null)"
LS_RC=$?
[ "$LS_RC" = "0" ] || unknown "ls-remote rc=$LS_RC"
REMOTE_SHA="$(printf '%s\n' "$REMOTE_OUT" | awk 'NF{print $1; exit}')"
# ★ rc=0 且輸出空是 ls-remote 對不存在 ref 的正常回法 —— 那是量不到,不是沒落後。
case "$REMOTE_SHA" in
  ????????????????????????????????????????) : ;;
  *) unknown "ls-remote rc=0 但沒有 40 位 sha(輸出 ${#REMOTE_SHA} 字元)" ;;
esac

# fetch 只取最小面:單一 remote 單一分支,不 --all、不動 tag;且必有時間上界。
timeout "$TIMEOUT_S" git fetch --no-tags origin main >/dev/null 2>&1
FETCH_RC=$?
[ "$FETCH_RC" = "0" ] || unknown "fetch rc=$FETCH_RC"

# ★ 證明剛才那一步沒有碰工作樹。這一格在**乾淨樹上恆真**(前後都是空字串),
#   所以它的臂必須跑在有未提交內容的樹上,否則是一支永遠不會紅的臂。
STATUS_AFTER="$(git status --porcelain 2>/dev/null)"
[ "$STATUS_BEFORE" = "$STATUS_AFTER" ] || unknown "fetch 動了工作樹狀態"

# ★ 物件在不在要用 rev-parse --verify(在=0/不在=1)。
#   不要拿 rev-list 的 rc 去猜:它缺物件時回 128,而 ls-remote 不可達也是 128
#   —— 兩種完全不同的處境同一個碼,分不出來。
git rev-parse --verify -q "${REMOTE_SHA}^{commit}" >/dev/null 2>&1 \
  || unknown "遠端 sha 在本地沒有物件"

BEHIND="$(git rev-list --count "HEAD..${REMOTE_SHA}" 2>/dev/null)"
case "$BEHIND" in
  ''|*[!0-9]*) unknown "落後顆數算不出來" ;;
esac

if [ "$BEHIND" = "0" ]; then
  exit 0
fi

say "開艙擋下:checkout 落後 origin/main $BEHIND 顆"
say "  HEAD       = $HEAD_SHA"
say "  origin/main= $REMOTE_SHA"
say "  要過請先跑:git -C $REPO pull --ff-only origin main"
override_or_exit stale 1
