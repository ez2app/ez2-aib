#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# seats_batch.sh — `aib <N>sits` 的實作(批次開席,2026-08-21 the maintainer 直令)。
#
#   aib 5sits                      # 全走預設名冊
#   aib 4sits pm dev1 design qa:codex
#   aib 5sits qa:codex             # 只覆寫 qa 的引擎,其餘取名冊
#   aib 5sits --dry-run
#
# ★ 本檔**不重寫開席邏輯**:逐席回頭呼叫既有的 `aib <provider> <mode> <role>`。
#   開席是既有那條路的事,本檔只負責「開幾席、開哪些、開完了沒」。多一份開席
#   邏輯就是第二個真相源,而兩份遲早分岔。
#
# ★ 完成定義=**席位檔真的有身份**,不是 `aib` 回 rc=0。
#   理由是量出來的:開席鏈裡 `seat-detect --apply --retry 5` 以前會擋住切畫面
#   約 12 秒(O3 起改背景,aib 不再等它)。codex 的 rollout jsonl / agy 的 presence
#   lock 那時通常還沒生出來,失敗又被吞掉,全庫沒有第二次機會 → 席位檔永遠缺
#   session_uuid → seat_status 判 NO_UUID → 右欄那席戴 ❓ 壓過角色圖示。
#   實測:instance 15 的 codex 席開在 13:45,一小時後探針一發就中
#   (`lsof:codex-rollout-meta`),而席位檔到那時仍是空的。**資料早就有了,
#   只是沒人再看。** 所以本檔開完席會有上限地回頭再看幾次。
#   (根治在另一層:`aib idle watch` 每輪經 seat_uuid_backfill.py 回填,
#    ED.13 2026-08-22 已接線,時間窗 EZ2_AIB_UUID_SETTLE_S 預設 600s。本檔是
#    批次工具該自己負的責任,不是那一層的替代品;本檔回 4 只表示「上限內
#    沒等到」,不表示那席壞了——seats_report 會標 pending/overdue。)
#
# exit code: 0 全數開起且身份落檔 / 2 用法或名冊錯 / 3 有席沒開起來
#            / 4 席開起來了但身份未落檔(誠實降級,不假裝成功)
set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# ★ 預設用同一棵樹的 aib(啟動器與執行期同一份 checkout,是 aib 檔頭自己的
#   紀律)。`AIB_SITS_AIB_BIN` 是**唯一**的例外口,存在的理由很窄:在
#   worktree 裡驗證本工具時,開出來的席不該把 EZ2_AIB_ROOT 指到那棵未審的樹
#   ——席位是長期的,worktree 是暫時的。指定時原樣尊重,不可執行就擋下,
#   不靜默退回預設(「我指定了 A 卻跑到 B」比「跑不起來」難查十倍)。
AIB="${AIB_SITS_AIB_BIN:-$HERE/aib}"
[ -x "$AIB" ] || { echo "aib sits: 開席器不可執行:$AIB" >&2; exit 2; }
PY3="${EZ2_PY3:-python3}"
ROSTER="${AIB_SEATS_ROSTER:-$HERE/rosters/default.json}"

die() { echo "aib sits: $2" >&2; exit "$1"; }

# ---- socket fail-closed ----------------------------------------------------
# ★ 這是本檔唯一一處刻意比既有行為嚴格的地方。`aib` 現行預設會退回 socket
#   名 `aihub`(第一台);在只有 aihub-15/aihub-16 的機器上,那個名字**不存在**,
#   於是 `new` 會靜默生出第三個艙,兩邊 dm 不到,而且不報錯。實測:
#     aib claude dgrs qa --dry-run                → tmux -L aihub
#     EZ2_AIHUB_SOCKET=aihub-16 aib ... --dry-run → tmux -L aihub-16
#   批次開席一次錯五席,所以這裡擋下來要人講清楚,不猜。
HUB="${EZ2_AIHUB_SOCKET:-}"
[ -n "$HUB" ] || die 2 "EZ2_AIHUB_SOCKET 未設。批次開席不猜艙——一次猜錯就是五席開錯地方。
  現有 socket:$(ls "${TMUX_TMPDIR:-/tmp}/tmux-$(id -u)" 2>/dev/null | tr '\n' ' ')
  例:EZ2_AIHUB_SOCKET=aihub-16 aib 5sits"

DRY=0; N=""; SPECS=()
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    -*) die 2 "不認得的旗標:$arg" ;;
    *[0-9]sits|[0-9]*sits) N="${arg%sits}" ;;
    *) SPECS+=("$arg") ;;
  esac
done
[ -n "$N" ] || die 2 "缺席數。用法:aib <N>sits [role[:provider] ...]"
[[ "$N" =~ ^[0-9]+$ ]] && [ "$N" -ge 1 ] || die 2 "席數要是正整數:$N"
MODE_OVERRIDE="${AIB_SITS_MODE_OVERRIDE:-}"
case "$MODE_OVERRIDE" in
  ""|dgrs|safe) ;;
  *) die 2 "不認得的模式覆寫:$MODE_OVERRIDE" ;;
esac

# ---- 名冊解析 --------------------------------------------------------------
PLAN="$("$PY3" - "$ROSTER" "$HERE/providers.json" "$N" "$MODE_OVERRIDE" \
  "${SPECS[@]+"${SPECS[@]}"}" <<'PY'
import json, re, sys
roster_path, providers_path = sys.argv[1:3]
n, mode_override = int(sys.argv[3]), sys.argv[4]
specs = sys.argv[5:]
try:
    roster = json.load(open(roster_path, encoding='utf-8'))
except Exception as e:
    sys.exit('名冊讀不到或不是合法 JSON:%s(%s)' % (roster_path, e))
try:
    registry = json.load(open(providers_path, encoding='utf-8'))
    model_defaults = {
        row['id']: row['default_requested_model']
        for row in registry.get('providers', [])
        if row.get('status') == 'supported'
        and isinstance(row.get('default_requested_model'), str)
    }
except Exception as e:
    sys.exit('provider 登記簿讀不到:%s(%s)' % (providers_path, e))
seats = roster.get('seats') or []
mode = mode_override or roster.get('mode') or 'safe'   # 10-08:名冊沒寫模式 ⇒ 每次先問(要全部放行就在名冊寫 mode: dgrs)
by_role = {s['role']: dict(s) for s in seats}

if specs:
    # 明給的以明給的為準;`role:provider` 覆寫引擎,單寫 `role` 沿用名冊引擎。
    plan = []
    for sp in specs:
        # The third field is the model tail and may itself contain ':' (some
        # provider namespaces use vendor:model), so split at most twice.
        parts = sp.split(':', 2)
        role = parts[0]
        prov = parts[1] if len(parts) > 1 else ''
        explicit_model = parts[2] if len(parts) > 2 else ''
        if not role:
            sys.exit('席位規格缺角色:%r' % sp)
        base = dict(by_role.get(role, {'role': role, 'provider': 'claude'}))
        base['role'] = role
        if prov:
            if prov != base.get('provider'):
                base.pop('requested_model', None)
            base['provider'] = prov
        if explicit_model:
            base['requested_model'] = explicit_model
        plan.append(base)
    # 明給的比 N 少 → 用名冊裡還沒被點到的補足(順序照名冊)
    named = {p['role'] for p in plan}
    for s in seats:
        if len(plan) >= n:
            break
        if s['role'] not in named:
            plan.append(dict(s))
else:
    plan = [dict(s) for s in seats]

# ★ 不靜默截斷也不靜默補齊。少開一席不會亮紅燈,成本是別人以為開了。
if len(plan) != n:
    sys.exit('要開 %d 席,但解出 %d 席(%s)。名冊 %s 只有 %d 席——'
             '改 N、補參數,或改名冊,三選一;本工具不替你截斷也不替你湊數。'
             % (n, len(plan), ' '.join(p['role'] for p in plan),
                roster_path, len(seats)))
seen = {}
for p in plan:
    if p['role'] in seen:
        sys.exit('角色重複:%s。同一艙兩席同名會被 aib 自動改窗名(dev12-claude)'
                 '而你不會知道哪一席是哪一席——要開兩席請明寫 dev1 dev2。'
                 % p['role'])
    seen[p['role']] = 1
for p in plan:
    provider = p.get('provider', 'claude')
    requested_model = p.get('requested_model') or model_defaults.get(provider)
    if not isinstance(requested_model, str) or not re.fullmatch(
            r'[0-9A-Za-z][0-9A-Za-z._:/-]*', requested_model):
        sys.exit('席位 %s 的 requested_model 缺失或格式錯誤(provider=%s)'
                 % (p['role'], provider))
    print('%s\t%s\t%s\t%s\t%s' % (
        p['role'], provider, mode, p.get('icon', ''), requested_model))
PY
)" || die 2 "$PLAN"

PLAN_JSON="$("$PY3" - "$PLAN" "$N" "$DRY" \
  "${AIB_SITS_NEW_INSTANCE:-0}" <<'PYPLAN'
import json, sys

rows, count, dry, new_instance = sys.argv[1:5]
seats = []
for row in rows.splitlines():
    if not row:
        continue
    role, provider, mode, _icon, requested_model = row.split('\t', 4)
    seats.append({
        "kind": "main_ai", "role": role, "provider": provider,
        "mode": mode, "requested_model": requested_model,
    })
plan = {
    "schema": "ez2-aib/main-seat-launch-plan/v1",
    "new_instance": new_instance == "1",
    "dry_run": dry == "1",
    "seat_count": int(count),
    "seats": seats,
}
if dry == "1":
    plan["state_effect_count"] = 0
print(json.dumps(plan, ensure_ascii=False, sort_keys=True,
                 separators=(",", ":")))
PYPLAN
)" || die 2 "啟動計畫無法編碼"
printf 'AIB_FLEET_PLAN %s\n' "$PLAN_JSON"

echo "aib sits: 艙=$HUB 名冊=$ROSTER 要開 $N 席"
printf '%s\n' "$PLAN" | while IFS=$'\t' read -r role prov mode icon requested_model; do
  printf '  %-8s %-7s %-24s %s\n' "$role" "$prov" "$requested_model" "$icon"
done

# ---- 活席不重開,死席要重生 ------------------------------------------------
# ★ 判準是 **pane_dead**,不是「這個角色存在嗎」。理由來自讀 aib 而不是猜:
#     活席撞名 → aib 自己 `die rc=2`,要你加 --again(aib:399-402);
#                new-window 那條路的註解也明寫 "Existing provider windows and
#                conversations are never killed"。
#     死席撞名 → aib 走 respawn-pane -k **就地重生同一個窗**(aib:395-398),
#                窗號與席位檔都留著,不會產生孤兒席位檔。
#   所以本閘只擋活席:擋掉的是那個會讓整批中止的 rc=2(批次要能把剩下幾席
#   開完,不能因為第一席已在就整批停),而不是擋掉重生——重生正是修復路徑。
#
# ★ 我第一版把理由寫成「重開會殺掉它」,那是錯的,而且是我沒讀碼就寫下的。
#   一個正確的閘配一個錯誤的理由,下一個人會照那個理由去改別的地方。
existing_roles() {
  env -u TMUX tmux -L "$HUB" list-windows -t aihub \
      -F '#{@aib_role}|#{@aib_provider}|#{pane_dead}' 2>/dev/null \
    | awk -F'|' 'NF==3 && $2 != "" && $3 == "0" {print $1}'
}
if [ "${AIB_SITS_ASSUME_EMPTY:-0}" = "1" ]; then
  EXISTING=""
else
  EXISTING="$(existing_roles)"
fi

# ---- 席位的工作目錄由名冊宣告,不繼承呼叫者 --------------------------------
# ★ `aib` 把**呼叫端當下的 cwd**當成席位的工作目錄(provider-launch.sh 第四參)。
#   對互動使用是對的(人站在哪就在哪開),對批次工具是錯的:批次常常從腳本、
#   從 worktree、從 CI 被呼叫,而「我剛好站在哪裡」跟「這些席該在哪工作」是
#   兩件事。實測付過一次:五席全開在一棵暫時的 worktree 裡,codex 那席開機
#   畫面直接印著 `directory: ~/worktrees/...`,而那棵樹隨時會被移除。
# ★ `$EZ2_AIB_ROOT` 要展開成**真正啟動席位的那支 aib 所在的樹**,不是本檔
#   繼承到的那個值。理由:aib 自己會 `export EZ2_AIB_ROOT="$AIB_SOURCE_ROOT"`
#   (啟動器與執行期必須同一份 checkout,是它檔頭的紀律),所以席位跑的是
#   $AIB 那棵樹的碼——它的工作目錄就該是同一棵。實測踩到:用 worktree 的
#   seats_batch 去驅動正本樹的 aib 時,繼承來的 EZ2_AIB_ROOT 指向 worktree,
#   於是宣告出來的 cwd 跟席位實際跑的碼分屬兩棵樹。
AIB_TREE="$(cd -P "$(dirname "$AIB")/../.." && pwd)"
SEAT_CWD="$(EZ2_AIB_ROOT="$AIB_TREE" "$PY3" - "$ROSTER" <<'PYC'
import json, os, sys
r = json.load(open(sys.argv[1], encoding='utf-8'))
print(os.path.expandvars(r.get('cwd') or '$EZ2_AIB_ROOT'))
PYC
)"
case "$SEAT_CWD" in
  ''|*'$'*) die 2 "名冊的 cwd 展不開:'$SEAT_CWD'(EZ2_AIB_ROOT 有設嗎?)" ;;
esac
[ -d "$SEAT_CWD" ] || die 2 "名冊宣告的工作目錄不存在:$SEAT_CWD"
echo "aib sits: 席位工作目錄=$SEAT_CWD"

# ---- 逐席開 ---------------------------------------------------------------
# ★ AIB_NO_ATTACH=1 是 aib 給程式呼叫端的正門(aib:560-575 自己記著理由):
#   互動路徑最後會 attach / switch-client 把操作者的畫面切過去,那件事在
#   腳本裡**必定失敗**,而失敗的 rc 會蓋掉「席其實開好了」。實測就踩到:
#   dev1 席開起來了、席位檔寫了、開席單投遞了,`aib` 仍回 rc=1,只因為它
#   切不動我的畫面。**不設這個旗標,rc 回答的是另一個問題。**
# ★ --dry-run 放在這裡而不是更前面:它必須看得到 cwd 與「哪些會被跳過」。
#   放在解析完就印,印出來的是一份**與實際行為不同**的清單——那種 dry-run
#   會讓人以為五席都會開,而實際只開三席。會說謊的預演比沒有預演更糟。
if [ "$DRY" = "1" ]; then
  echo "aib sits: --dry-run,以下不會執行"
  printf '%s\n' "$PLAN" | while IFS=$'\t' read -r role prov mode icon requested_model; do
    if printf '%s\n' "$EXISTING" | grep -qxF "$role"; then
      echo "  跳過 $role(已有活席)"
    else
      echo "  ( cd $SEAT_CWD && EZ2_AIHUB_SOCKET=$HUB AIB_NO_ATTACH=1 $AIB $prov $mode $role --model $requested_model )"
    fi
  done
  exit 0
fi

# ★★ 開席前先記下每個計畫席**現在**的 uuid(基線)。
#   qa 二審判退的那條:完成定義原本是「席位檔有 session_uuid」,而欄位存在
#   不證明它是這次開席產生的——一個十天前留下的 uuid 會讓這次看起來成功。
#   那是恆真判準:缺陷還在時它也是綠的。
#   ★ 基線只用在「這次真的開了」的席上;被跳過的活席本來就不該換 uuid,
#     對它們要求變號是製造假紅。
BASELINE="$("$PY3" - "$HUB" "$HERE" <<'PYB'
import json, os, sys
hub, here = sys.argv[1], sys.argv[2]
sys.path.insert(0, here)
import seats_dir_lib                  # ED.1:席位目錄單源(狀態根可注入),不在這裡再抄一份
d = seats_dir_lib.seats_dir(hub=hub)
out = {}
try:
    for b in os.listdir(d):
        if b.startswith("w") and b.endswith(".json"):
            try:
                r = json.load(open(os.path.join(d, b), encoding="utf-8"))
            except Exception:
                continue
            if isinstance(r.get("role"), str):
                out[r["role"]] = r.get("session_uuid")
except OSError:
    pass
print(json.dumps(out))
PYB
)"

OPENED=(); SKIPPED=(); FAILED=""; FAILED_ROLE=""
while IFS=$'\t' read -r role prov mode icon requested_model; do
  if printf '%s\n' "$EXISTING" | grep -qxF "$role"; then
    echo "aib sits: 跳過 ${role}——該角色有活席在艙內(aib 對活席會 rc=2,跳過才能把其餘幾席開完)"
    SKIPPED+=("$role")
    continue
  fi
  echo "aib sits: 開 $role($prov)…"
  # ★ EZ2_TMUX_SESSION 一併帶上:少了它,aib 會把外層 session 算成 `main`
  #   (instance=main → session aib-main),於是 attach/switch 會把操作者的畫面
  #   切到**另一個艙**。實測付過:畫面被切到主艙外殼,而那個艙的中央格
  #   attach 的是 aihub-15,看起來像「面板顯示了別人的艦隊」。
  #   AIB_NO_ATTACH=1 已經擋掉切換動作,這一行是第二道:算對它,而不是
  #   只靠「反正不會用到」。
  # ★★★ 2026-08-21 第二次修同一個根因,這次修到底。
  #   只帶 socket 的話,aib 會把 instance 算成 `main`,而它**不只影響這一次
  #   執行**——它會把那個錯的值寫進外層 session 的環境並留在那裡:
  #       aib-16 session env: EZ2_AIB_INSTANCE=main
  #                           EZ2_COCKPIT_STATE_DIR=~/.ez2-aib/cockpit
  #   後果實測(the maintainer 15:34 之後兩小時點不動右欄):滑鼠綁定在 run-shell
  #   情境下讀 session 環境 → 拿到**全域** cockpit 目錄 → 讀到的 rowmap 是
  #   **另一支艦隊的席位**,行號對不上這一艙的面板,點擊靜默失效。
  #   ★ 第一次我只補了 EZ2_TMUX_SESSION(那次的症狀是畫面被切到別的艙),
  #     **沒發現同一個錯誤值還污染了 session 環境**。修症狀不修型,就會再來一次。
  #   ★ 三個值一起帶:它們是同一個事實(我在哪一艙)的三種寫法,少帶任何
  #     一個都會讓下游某一層自己去猜,而猜錯不會報錯。
  _inst="${EZ2_AIB_INSTANCE:-${HUB#aihub-}}"
  ( cd "$SEAT_CWD" && EZ2_AIHUB_SOCKET="$HUB" AIB_NO_ATTACH=1 \
      EZ2_TMUX_SESSION="${EZ2_TMUX_SESSION:-aib-${HUB#aihub-}}" \
      EZ2_AIB_INSTANCE="$_inst" \
      EZ2_AIB_INSTANCE_HOME="${EZ2_AIB_INSTANCE_HOME:-$HOME/.ez2-aib/instances/$_inst}" \
      "$AIB" "$prov" "$mode" "$role" --model "$requested_model" )
  rc=$?
  if [ "$rc" -ne 0 ]; then
    FAILED="$role($prov) rc=$rc"; FAILED_ROLE="$role"
    break
  fi
  OPENED+=("$role")
done < <(printf '%s\n' "$PLAN")

[ "${#SKIPPED[@]}" -eq 0 ] || \
  echo "aib sits: 已在艙內而跳過:${SKIPPED[*]}"

if [ -n "$FAILED" ]; then
  # ★ qa 二審判退第二條:收據只列了「已開」與「失敗」,**沒有點名還沒嘗試
  #   的席**。那些席既沒開也沒失敗,而讀收據的人無從知道它們的存在——
  #   一份不完整的清單看起來跟一份完整的清單一模一樣。三類都要點名。
  NOT_TRIED=""
  while IFS=$'\t' read -r role prov mode icon requested_model; do
    case " ${OPENED[*]:-} ${SKIPPED[*]:-} $FAILED_ROLE " in
      *" $role "*) ;;
      *) NOT_TRIED="$NOT_TRIED $role" ;;
    esac
  done < <(printf '%s\n' "$PLAN")
  echo "aib sits: 停在 $FAILED" >&2
  echo "aib sits:   已開 ${#OPENED[@]} 席:${OPENED[*]:-無}" >&2
  echo "aib sits:   失敗:${FAILED_ROLE:-無}" >&2
  echo "aib sits:   已在艙內而跳過:${SKIPPED[*]:-無}" >&2
  echo "aib sits:   ★ 尚未嘗試:${NOT_TRIED:- 無}" >&2
  echo "aib sits: ★ 已開的席**沒有被回收**——回收是人的決定,不是本工具的。" >&2
  exit 3
fi

# ---- 等身份落檔 -----------------------------------------------------------
# 有上限地重試;每一輪之間讓 provider 有時間把自己的 artifact 寫出來。
# ★ 上限到了就誠實回 4,不把「還沒好」講成「好了」。
# 只把「這次真的開了」的席放進基線 —— 跳過的不算(見上面那段的理由)。
OPENED_BASELINE="$("$PY3" - "$BASELINE" "${OPENED[*]:-}" <<'PYO'
import json, sys
base = json.loads(sys.argv[1] or "{}")
opened = (sys.argv[2] or "").split()
print(json.dumps({r: base.get(r) for r in opened}))
PYO
)"

TRIES="${AIB_SITS_SETTLE_TRIES:-10}"
for i in $(seq 1 "$TRIES"); do
  EZ2_AIHUB_SOCKET="$HUB" "$PY3" "$HERE/seat-detect.py" --all --apply --retry 3 \
    >/dev/null 2>&1
  MISSING="$(EZ2_AIHUB_SOCKET="$HUB" "$PY3" "$HERE/seats_report.py" --missing-uuid --require-new "$OPENED_BASELINE" 2>/dev/null)"
  [ -n "$MISSING" ] || break
  echo "aib sits: 第 $i/$TRIES 輪,尚未落檔:$MISSING"
done

EZ2_AIHUB_SOCKET="$HUB" "$PY3" "$HERE/seats_report.py" --table \
  --require-new "$OPENED_BASELINE"
RC=$?
exit "$RC"
