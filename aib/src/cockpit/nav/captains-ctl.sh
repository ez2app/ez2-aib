#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# captains-ctl v1.0.0 — 艦長對話選擇器(E2.19 第一刀,2026-08-13):中欄下方那條列=內層 aihub tmux 的
# status bar。原本放著預設樣式(session 名+主機+時鐘,即截圖紅框那行),現在
# 改造成:只列**艦長級席位**的 `role@末五` 分頁,點擊即切換該對話。
#
# 為什麼是這裡而不是 pane-border:內層 status bar 本來就存在(零幾何變更,
# E2.16/17 紅線),而 tmux 原生就把 status 上的視窗分頁做成可點擊(mouse on 時
# MouseDown1Status=select-window)——不重造點擊命中,少一整層 spans/rowmap。
#
# 資料源(E2.19 規格更正):原規格要 registry_verified,該狀態隨舊載體拆除
# 永久消失;繼任=席位檔 $seats/w<id>.json,trust=self_reported(絕不冒充
# verified;顯示層 ~ 前綴已依 the maintainer 08-14 拍板移除,誠實語意由資料層承載)。艦長級=EZ2_AIB_CAPTAIN_ROLES(預設 "pm root")。
#
#   captains-ctl.sh --install [--dry-run]   設定內層 status 格式 + 回填窗選項
#   captains-ctl.sh --sync    [--dry-run]   只回填/清理(ensure 或巡邏可重跑,冪等)
#   captains-ctl.sh --assign <wid>          指派該席為艦長(captain_assigned=true)
#   captains-ctl.sh --unassign <wid>        撤指派(刪欄,回原 role 位)
#   captains-ctl.sh --advisor <wid> on|off  標/撤顧問(advisor 欄;顧問永不成現任)
#
# E2.19 四裁(2026-08-14 the maintainer):指派=人類/整合席動作,寫席位檔 optional 欄
# 後跑既有 --sync(冪等);trust 語意不變(self_reported)。任何 role 被指派
# 即以艦長身分顯示與排序(右欄層級樹+本 bar);advisor=true 永不計入現任,
# bar 也不列(顧問是艦長之後的獨立段位,由右欄樹呈現)。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COCKPIT="$(cd "$HERE/.." && pwd)"
# 報錯訊息走 aib/i18n 四語鍵;字典或 python 出事就印鍵名,報錯本身不能因翻譯失敗而消失。
_t() { "${EZ2_PY3:-python3}" -I "$COCKPIT/i18n_lib.py" "$@" 2>/dev/null || printf '%s\n' "$1"; }
# ★ 在 source 之前先記下「哪些是呼叫端**明示**的」。source 完之後兩者一定都有值
#   (cabin_resolve 會補上推導值),那時就再也分不出「他說的」與「我算的」——
#   而底下的艙一致性閘要判的正是這個差別。
_PRE_INSTANCE="${EZ2_AIB_INSTANCE-}"
_PRE_HOME="${EZ2_AIB_INSTANCE_HOME-}"
_PRE_HUB="${EZ2_AIHUB_SOCKET-}"
. "$COCKPIT/cockpit-env.sh"

DRY="${AIB_DRY_RUN:-0}"
MODE=""
TARGET_WID=""
ADVISOR_STATE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --install) MODE=install ;;
    --sync) MODE=sync ;;
    --assign) MODE=assign; TARGET_WID="${2:-}"; shift $(( $# > 1 ? 1 : 0 )) ;;
    --unassign) MODE=unassign; TARGET_WID="${2:-}"; shift $(( $# > 1 ? 1 : 0 )) ;;
    --advisor)
      MODE=advisor; TARGET_WID="${2:-}"; ADVISOR_STATE="${3:-}"
      shift $(( $# > 2 ? 2 : ($# > 1 ? 1 : 0) )) ;;
    --dry-run) DRY=1 ;;
    *) echo "captains-ctl: unknown arg $1" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$MODE" ] || { echo "captains-ctl: need --install or --sync" >&2; exit 2; }
# 缺陷十九(09-05):暫存樹 / 暫存 state 不得寫活的 tmux。只在**非 dry 的真安裝**
# 路徑 source/enforce:dry 不碰 tmux,也就不該硬相依這支守衛檔(否則單獨複製本
# 腳本跑 dry 的治具會 rc=1)。守衛檔缺席時具名 fail-closed,不是靜默 set -e 死在 source。
if [ "$MODE" = "install" ] && [ "$DRY" != "1" ]; then
  if [ -r "$COCKPIT/install-guard.sh" ]; then
    . "$COCKPIT/install-guard.sh"
    aib_refuse_tmp_install captains-ctl.sh "$HERE" || exit $?
  else
    _t aib.install_guard.missing --param script=captains-ctl.sh --param "path=$COCKPIT/install-guard.sh" >&2
    exit 6
  fi
fi

HUB="${EZ2_AIHUB_SOCKET:?}"
# 刀 A 單源(R8 第四項,2026-08-23):狀態根/艙家一律取 cabin_resolve 匯出的值,
# 不在這裡再組一次 env 鏈。手抄的那一份與單源會在「只注入根」時分家,
# 而分家的失效形狀是**刪到別艙的席位檔**,不是紅。
SEATS_DIR="$EZ2_AIB_INSTANCE_HOME/seats"

# ★★ 艙一致性:SEATS_DIR 與窗清單必須來自**同一個艙**。
# 窗清單來自 $HUB(內層 socket),席位檔來自 $EZ2_AIB_INSTANCE_HOME;
# 兩者由不同的 env 決定,所以它們可以合法地指向不同的艙——而那一刻,
# 「用 B 艙的窗清單去判 A 艙的席位檔死活」會把 A 艙每一個席位檔都判成死的。
# ★ 這條比守衛本身重要:守衛沒修好只是沒進度,這條做錯是**刪掉別人的席位檔**。
# ★★ 什麼叫「不一致」(2026-08-23 修正,root 12:23 裁):
#   第一版把「推導值 != 明示值」就算成不一致,於是**只明示了 INSTANCE_HOME** 的呼叫端
#   被自己的明示值擋下來——而刀 A 的優先序本來就是**明示的贏過推導的**。
#   真正該擋的是「**兩邊都明示、而且互相矛盾**」:那才是不知道該信誰。
#     一 只明示 INSTANCE_HOME,沒有任何獨立來源指得出艙 -> 放行,以明示為準
#     二 只明示艙,沒明示 HOME -> 用推導的,放行
#     三 兩邊都明示且矛盾 -> 拒絕並出聲,訊息同時點名兩個值(刀 A 的 fail-closed 形狀)
#   ★ 「艙不一致就不刪、而且出聲」這個保護沒有變弱;變的只有「什麼叫不一致」的定義。
#
# ★★★ 什麼算「一個艙來源」(root 08-23 13:07 補裁,我上一版在這裡錯了):
#   **推導出來的值不算,不論推得出或推不出;只有呼叫端真的設了那個變數才算。**
#   我上一版拿 **source 之後**的 $HUB 去解艙——而呼叫端沒設 socket 時,那個值是
#   cabin_resolve 自己推出來的 `aihub`(主艙)。於是「只設了 INSTANCE_HOME」的呼叫端
#   會被**我自己推導出來的值**判成矛盾,那道閘退回它今天早上才修掉的那個過嚴狀態。
#   ★ 所以這裡一律用 source **之前**的 _PRE_* 三個值。
#   三條規則:
#     一 只設 HOME                     -> 放行,以它為準(推導出來的 main 不算矛盾)
#     二 只設 INSTANCE                 -> 放行,用推導的 HOME
#     三 **任何兩個可解析的艙來源互相矛盾** -> 拒絕並出聲,訊息同時點名兩值
#        (含 INSTANCE vs HOME、以及**呼叫端明示的** socket 解出的艙 vs HOME)
#   ★ 「可解析」是承重的:`test-hub` 這種解不出艙的 socket 不是一個來源,沒有東西可以矛盾。
CABIN_OK=1
CABIN_WHY=""
if [ "$(type -t aib_cabin_home 2>/dev/null)" != "function" ]; then
  # 解析器不在=判不出艙,**不猜**。fail-closed 只擋清理,不擋渲染。
  CABIN_OK=0
  CABIN_WHY="$(_t aib.captains_ctl.cabin_resolver_missing)"
else
  _CABIN_FROM=""
  _CABIN_SRC=""
  if [ -n "$_PRE_INSTANCE" ]; then
    _CABIN_FROM="$_PRE_INSTANCE"
    _CABIN_SRC="EZ2_AIB_INSTANCE=$_PRE_INSTANCE"
  else
    # ★ 用 _PRE_HUB 不是 $HUB:$HUB 在 source 之後一定有值(推導的也算),
    #   拿它當「呼叫端明示的艙」會把我自己算出來的東西當成他說的。
    if [ -n "$_PRE_HUB" ]; then
      _CABIN_FROM="$(aib_cabin_instance_from_hub "$_PRE_HUB" 2>/dev/null || true)"
      [ -z "$_CABIN_FROM" ] || _CABIN_SRC="EZ2_AIHUB_SOCKET=$_PRE_HUB"
    fi
  fi
  if [ -n "$_CABIN_FROM" ] && [ -n "$_PRE_HOME" ]; then
    _CABIN_EXPECT_HOME="$(aib_cabin_home "$_CABIN_FROM" "$(aib_cabin_state_root)")"
    if [ "$_CABIN_EXPECT_HOME" != "$_PRE_HOME" ]; then
      CABIN_OK=0
      CABIN_WHY="$(_t aib.captains_ctl.cabin_conflict --param "src=$_CABIN_SRC" --param "cabin=$_CABIN_FROM" \
        --param "expect=$_CABIN_EXPECT_HOME" --param "home=$_PRE_HOME")"
    fi
  fi
fi
# the maintainer 08-14 拍板:對映後 pm=主管不是艦長,艦長列預設只列 root
# (右欄艦隊列照舊全列,只有這條 bar 收窄)。
CAPTAIN_ROLES="${EZ2_AIB_CAPTAIN_ROLES:-root}"

run() {
  if [ "$DRY" = "1" ]; then printf 'DRY: %s\n' "$*"; else "$@"; fi
}
hub_tmux() { env -u TMUX tmux -L "$HUB" "$@"; }

# --- E2.19 四裁:席位檔 optional 布林欄寫入器(assign/unassign/advisor) ------
# fail-closed:壞 wid/無席位檔/schema 不合=rc2 一 byte 不寫;原子寫(tmp+mv)。
set_seat_flag() {
  local wid="$1" field="$2" action="$3" f
  case "$wid" in
    @*[!0-9]*|@|[!@]*|'')
      echo "captains-ctl: bad window id: ${wid:-<missing>}" >&2; exit 2 ;;
  esac
  f="$SEATS_DIR/w${wid#@}.json"
  [ -f "$f" ] || { echo "captains-ctl: no seat file for $wid ($f)" >&2; exit 2; }
  if [ "$DRY" = "1" ]; then
    printf 'DRY: seat-flag %s %s=%s\n' "$f" "$field" "$action"
    return 0
  fi
  # ★ E2.19 codex 審「高-2」(2026-08-22):原本是無鎖 read-modify-replace——PID 暫存檔只防
  #   半寫,不防 lost update:並行 --assign 與 --advisor on 各讀同一舊版,後寫者蓋掉前者欄位
  #   (最壞掉 advisor=true,顧問重回艦長選擇)。改走 src/cockpit/atomic_json.update_json:
  #   全程持目錄鎖的 RMW,與 aib 種檔/心跳/seat-detect 回填同一把鎖(席位檔本來就是多寫者)。
  #   rc 語意不變:讀不到/schema 不合 → rc2 一 byte 不寫(在鎖內再判一次,不信鎖外那次)。
  python3 - "$f" "$field" "$action" "$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" <<'PY'
import glob, json, os, sys
path, field, action, cockpit = sys.argv[1:5]
sys.path.insert(0, cockpit)
import atomic_json
import seat_address


def _t(key, **params):
    try:
        import i18n_lib
        return i18n_lib.t(key, params)
    except Exception:
        return key


try:
    json.load(open(path, encoding='utf-8'))
except Exception as err:
    print('captains-ctl: seat file unreadable, refuse: %s' % err, file=sys.stderr)
    raise SystemExit(2)


def mutate(rec):
    if not isinstance(rec, dict) or rec.get('schema') != 'aib-seat/v1':
        print('captains-ctl: seat schema mismatch, refuse', file=sys.stderr)
        raise SystemExit(2)          # 鎖內拒絕:不回傳 dict 就不會寫
    # B15:受派艦長是**單席**狀態。唯一性必須在真正的寫入器、而且在
    # atomic_json 已持有 seats 目錄鎖的這一段檢查；只在 appoint 前置檢查
    # 會被直接呼叫本工具繞過，鎖外先掃則會留下兩個寫者同時通過的競態窗。
    # 不自動清舊再寫新：那會跨兩份 JSON，沒有交易就可能只完成半次換手。
    if field == 'captain_assigned' and action == 'true':
        target = os.path.abspath(path)
        for other_path in sorted(glob.glob(os.path.join(
                os.path.dirname(target), 'w*.json'))):
            if os.path.abspath(other_path) == target:
                continue
            try:
                with open(other_path, encoding='utf-8') as handle:
                    other = json.load(handle)
            except Exception as err:
                raise atomic_json.AtomicWriteError(_t(
                    'aib.captains_ctl.unique_unreadable',
                    file=os.path.basename(other_path), err=err.__class__.__name__))
            if not isinstance(other, dict) \
                    or other.get('schema') != 'aib-seat/v1':
                raise atomic_json.AtomicWriteError(_t(
                    'aib.captains_ctl.unique_schema', file=os.path.basename(other_path)))
            if other.get('captain_assigned') is True:
                raw_wid = str(other.get('window_id') or '')
                unmeasured = _t('aib.captains_ctl.unmeasured')
                window = raw_wid[1:] if raw_wid.startswith('@') else unmeasured
                code = seat_address.code_of(other) or unmeasured
                raise atomic_json.AtomicWriteError(_t(
                    'aib.captains_ctl.captain_exists', window=window, code=code))
    if action == 'true':
        rec[field] = True
    else:
        rec.pop(field, None)
    return rec


try:
    atomic_json.update_json(path, mutate)
except atomic_json.AtomicWriteError as err:
    print('captains-ctl: seat file write failed, refuse: %s' % err, file=sys.stderr)
    raise SystemExit(2)
PY
}

# 七輪審高1:拒收/降級共用的窗態清理——不清=曾為艦長的窗殘留 👑 殭屍分頁
clear_window_state() {
  run hub_tmux set-option -w -t "$1" @aib_captain 0
  run hub_tmux set-option -w -t "$1" @aib_seat ''
  run hub_tmux set-option -w -t "$1" @aib_role_display ''
  run hub_tmux set-option -w -t "$1" @aib_persona ''
  run hub_tmux set-option -w -t "$1" @aib_captain_icon ''
}

# --- 回填:窗選項 @aib_captain / @aib_seat 由席位檔而來 ---------------------
# dry-run 時不碰 tmux(治具可只驗純邏輯輸出)。
# SYNC_RC:0=健康;3=degraded(採集失敗/壞行/注入拒收),documented rc,
# 呼叫端據此分流(三輪審中2:降級不得回 0 騙過 ensure)。
SYNC_RC=0

sync_windows() {
  # 三輪審高1:窗名/role 值可含 \n 與 |,單次 list-windows 拼欄位有歧義
  # (role 夾 '\n@2|root' 可替他窗設艦長)。改無歧義兩段式:ids-only 快照
  # (id 是 tmux 產生的 @數字,無注入面)+ 逐窗查 @aib_role,整值驗證。
  local alive="" alive_rc=0 wid role snapshot_ok=1
  LIVE_IDS=""   # 清理用活窗集合:只來自 ids 快照(role 被拒的窗仍是活窗)
  if [ "$DRY" != "1" ]; then
    local ids=""
    ids="$(hub_tmux list-windows -t aihub -F '#{window_id}' 2>/dev/null)" || alive_rc=$?
    if [ "$alive_rc" -ne 0 ]; then
      echo "captains-ctl: window snapshot failed (rc=$alive_rc); skip sync" >&2
      SYNC_RC=3
      return 0
    fi
    while IFS= read -r wid; do
      [ -n "$wid" ] || continue
      case "$wid" in
        @*[!0-9]*|@|[!@]*)
          # 壞 id=ids 快照本身不可信:degraded+封鎖本輪清理
          echo "captains-ctl: malformed window id rejected: $wid" >&2
          SYNC_RC=3
          snapshot_ok=0
          continue
          ;;
      esac
      LIVE_IDS="$LIVE_IDS ${wid#@}"
      role="$(hub_tmux show-options -wqv -t "$wid" @aib_role 2>/dev/null || true)"
      # ★整值驗證必須在取值當下做:多行 role 拼進 alive 會重生
      # 「\n@2|root 替他窗設艦長」的注入面(三輪審高1 指定負例)。
      case "$role" in
        *[!0-9A-Za-z@._-]*)
          echo "captains-ctl: window $wid role rejected (unlisted chars)" >&2
          SYNC_RC=3
          # 七輪審高1:拒收不清舊態=曾為艦長的窗殘留 👑 分頁
          clear_window_state "$wid"
          continue   # 窗仍在 LIVE_IDS(清理不動它),但不回填不標艦長
          ;;
        *[vV][eE][rR][iI][fF][iI][eE][dD]*)
          # 六輪審高2:字元白名單擋不住語意——role 塞 verified 純英數過關,
          # 會打穿「全輸出無 verified 字樣」紅線;含 verified 子串整筆拒。
          echo "captains-ctl: window $wid role rejected (verified denylist)" >&2
          SYNC_RC=3
          clear_window_state "$wid"
          continue
          ;;
      esac
      alive="${alive}${wid}|${role}
"
    done <<< "$ids"
  else
    alive="${AIB_FAKE_WINDOWS:-}"   # 治具注入:'@1|pm\n@2|dev1' 形
  fi

  # 三輪審高1:整筆拒收,不洗字元——刪字元語意讓 'p#m' 洗成 'pm' 可冒充
  # 艦長。wid 非 @純數字/role 含白名單外字元(含 \n、|)或超長=該窗整筆拒
  # (不回填不上 status),fail-visible+degraded。
  local seat_file seat_id seat_read seat_advisor seat_assigned seat_started
  local seat_broken is_captain
  CAPTAIN_ROWS=""
  while IFS='|' read -r wid role; do
    [ -n "$wid" ] || continue
    case "$wid" in
      @*[!0-9]*|@|[!@]*)
        echo "captains-ctl: malformed window id rejected: $wid" >&2
        snapshot_ok=0
        SYNC_RC=3
        continue
        ;;
    esac
    case "$role" in
      *[!0-9A-Za-z@._-]*)
        echo "captains-ctl: window $wid role rejected (unlisted chars)" >&2
        SYNC_RC=3
        clear_window_state "$wid"
        continue   # 窗仍算活(清理不動它),但不回填不標艦長
        ;;
      *[vV][eE][rR][iI][fF][iI][eE][dD]*)
        echo "captains-ctl: window $wid role rejected (verified denylist)" >&2
        SYNC_RC=3
        clear_window_state "$wid"
        continue   # 六輪審高2 語意 denylist(與上段同判準,治具路徑也蓋)
        ;;
    esac
    if [ "${#role}" -gt 32 ]; then
      echo "captains-ctl: window $wid role rejected (too long)" >&2
      SYNC_RC=3
      clear_window_state "$wid"
      continue
    fi
    case "$role" in
      sys.*) continue ;;   # 08-18 系統窗(sys.watch 等)不是席、不是艦長候選;仍是活窗
    esac
    seat_file="$SEATS_DIR/w${wid#@}.json"
    # 三輪審高1:seat_id 含白名單外字元=整筆拒(降級處理);
    # 檔缺席/欄位缺=誠實 '-----'(缺席不是惡意)。
    # E2.19 四裁:同一次讀取一併取 advisor/captain_assigned 兩布林欄
    # (嚴格 is True 才認;缺席=0)——與右欄同源同判準。
    seat_id="-----"; seat_advisor=0; seat_assigned=0; seat_started=9999999999
    if [ -f "$seat_file" ]; then
      seat_read="$(python3 -c "
import json, re, sys
try:
    rec = json.load(open(sys.argv[1]))
except Exception:
    rec = None
# 七輪審中2:schema+window_id 交叉驗與右欄同判準——server 重啟 ID 重用時
# 舊席位檔錯掛新窗會顯示錯 seat;不符=當缺席 -----(非惡意,不 REJECT)。
# 十四輪審2:但**旗標讀不到≠旗標為假**——壞檔的 root 席若原為 advisor,
# 讀成 false 會被重新加冕(fail-open)。BROKEN 標記讓呼叫端把該席排除出
# 現任選擇(fail-closed),顯示照舊 -----;與右欄(壞檔整席跳過)兩面一致。
if not isinstance(rec, dict) or rec.get('schema') != 'aib-seat/v1' \
        or rec.get('window_id') != sys.argv[2]:
    print('BROKEN 0 0')
    raise SystemExit(0)
adv = 1 if rec.get('advisor') is True else 0
asn = 1 if rec.get('captain_assigned') is True else 0
# 十五輪審高3:started_epoch 一併輸出——現任順位以它定,與右欄同源;
# 缺/壞值=9999999999(排最後,不炸排序)
try:
    st = int(float(rec.get('started_epoch')))
    if st <= 0:
        raise ValueError   # 十六輪審1:非正=沉底,與右欄同判準
except (TypeError, ValueError):
    st = 9999999999
# ★ 08-29 the maintainer 直令:顯示碼統一=session uuid 尾五碼(全艦互稱用的那個),
#   席位檔 seat_id 只當 uuid 缺席時的後備——右欄與中格狀態列自此同碼。
#   位址層不動:dm 解析器雙認兩形(qa3 dual-accept 判準),本改動只動顯示。
u = rec.get('session_uuid')
if isinstance(u, str) and re.fullmatch(
        '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', u):
    v = u[-5:]
else:
    v = rec.get('seat_id')
if v is None:
    print('----- %d %d %d' % (adv, asn, st))
elif not isinstance(v, str) or re.search('[^0-9A-Za-z@._-]', v):
    print('REJECT 0 0 9999999999')
elif 'verified' in v.lower():
    print('REJECT 0 0 9999999999')
elif re.fullmatch('[0-9a-z]{4,8}', v):
    print('%s %d %d %d' % (v, adv, asn, st))
else:
    print('----- %d %d %d' % (adv, asn, st))
" "$seat_file" "$wid" 2>/dev/null || echo 'BROKEN 0 0 9999999999')"
      read -r seat_id seat_advisor seat_assigned seat_started <<EOF2
$seat_read
EOF2
    fi
    # 人格名另外取:它可能含中文與空白,塞不進上面那條空白分隔的輸出。
    # 取值與消毒**呼叫右欄用的同一個模組**(src/cockpit/seat_persona.py),
    # 不在這裡重寫一份等值判斷——兩份等值的碼今天相同、改一邊就分岔,而
    # 症狀是「同一席在兩個地方顯示不同名字」,沒有人會去比對。
    seat_persona=""
    if [ -f "$seat_file" ]; then
      seat_persona="$(python3 "$HERE/../seat_persona.py" \
        "$seat_file" "$wid" 2>/dev/null || true)"
    fi
    if [ "$seat_id" = "REJECT" ]; then
      echo "captains-ctl: window $wid seat_id rejected (unlisted chars)" >&2
      SYNC_RC=3
      clear_window_state "$wid"
      continue
    fi
    seat_broken=0
    if [ "$seat_id" = "BROKEN" ]; then
      seat_broken=1
      seat_id="-----"
    fi
    # E2.19 四裁:艦長=role 在 CAPTAIN_ROLES **或** captain_assigned;
    # advisor 永不計入(顧問是獨立段位,bar 不列——bar 是艦長對話選擇器)。
    # 十四輪審2:席位檔壞/schema 不符=旗標不可信,該席**不得參與現任選擇**
    # (fail-closed;原 advisor 席壞檔瞬間被加冕=顧問復辟,此洞在此關)。
    is_captain=0
    case " $CAPTAIN_ROLES " in *" $role "*) is_captain=1 ;; esac
    [ "${seat_assigned:-0}" = "1" ] && is_captain=1
    [ "${seat_advisor:-0}" = "1" ] && is_captain=0
    if [ "$seat_broken" = "1" ] && [ "$is_captain" = "1" ]; then
      echo "captains-ctl: window $wid seat file broken/mismatch;" \
           "excluded from captaincy (fail-closed)" >&2
      is_captain=0
    fi
    case "$is_captain" in
      1)
        # 十五輪審高3:現任順位改與右欄同源=started_epoch,不再依窗序
        # (窗序重排會讓 👑 飄)。本迴圈只收集,發冠移到迴圈後統一排序。
        #
        # ★★ 2026-08-16:排序鍵補上**受派優先**,與右欄 509c407 同判準。
        #   在此之前本 bar 只按 started_epoch 排,於是「指派某席為第一艦長」
        #   這個動作**對 bar 完全無效**——右欄的 👑 換人了,bar 的 👑 還掛在
        #   最資深的那一席身上。the maintainer 08-16 螢幕實見。
        #   ★ 根因是同一條規則只改了一半:509c407 的訊息寫著「受派艦長勝過
        #     資深——否則『指派』這個動作等於沒有」,而它只改了右欄。
        #     **同一句理由對 bar 一字不差地成立,只是沒有人把它搬過來。**
        #   `_asn_rank`:0=受派(排前)、1=未受派。與 started 一起當複合鍵。
        _asn_rank=1
        [ "${seat_assigned:-0}" = "1" ] && _asn_rank=0
        CAPTAIN_ROWS="${CAPTAIN_ROWS}${_asn_rank}|${seat_started}|${wid}|${seat_id}|${role}|${seat_persona}
"
        ;;
      *)
        # 降級要清舊 seat:留著舊值,下次誤標 captain 會顯示殭屍席位。
        run hub_tmux set-option -w -t "$wid" @aib_captain 0
        run hub_tmux set-option -w -t "$wid" @aib_seat ''
        run hub_tmux set-option -w -t "$wid" @aib_role_display ''
        run hub_tmux set-option -w -t "$wid" @aib_persona ''
        run hub_tmux set-option -w -t "$wid" @aib_captain_icon ''
        ;;
    esac
  done <<< "$alive"

  # 十五輪審高3:發冠統一在此——按 started_epoch 升冪(sort -s 平手保輸入
  # 序=無 started 的舊席位檔行為不變),首位=👑 其餘=⚓;先寫 seat 再啟
  # captain(一輪審低1 反閃爍序不變);@aib_role_display=驗證過副本(三輪審高1)。
  if [ -n "$CAPTAIN_ROWS" ]; then
    local _crow_asn _crow_started _crow_wid _crow_seat _crow_role _crow_persona
    local _cap_icon _cap_idx
    _cap_idx=0
    while IFS='|' read -r _crow_asn _crow_started _crow_wid _crow_seat \
                          _crow_role _crow_persona; do
      [ -n "$_crow_wid" ] || continue
      _cap_prov=""
      _sf=""
      if [ -n "${SEATS_DIR:-}" ] && [ -n "${_crow_wid:-}" ]; then
        _sf="$SEATS_DIR/w${_crow_wid#@}.json"
      fi
      if [ -n "$_sf" ] && [ -f "$_sf" ]; then
        _cap_prov="$("$EZ2_PY3" -c "import json,sys;print((json.load(open(sys.argv[1])).get('provider') or '').lower())" "$_sf" 2>/dev/null || true)"
      fi
      case "$_cap_prov" in
        sand|grokbot|codexbot|claudebot|musebot) _cap_icon="🤖" ;;
        # API-key 次位戴 🔑(與 pane-right / list_captains 同裁;providers.json auth=api_key)
        deepseek|nvidia|openrouter|typesafe|openrouterjev)
          if [ "$_cap_idx" -eq 0 ]; then _cap_icon="👑"; else _cap_icon="🔑"; fi ;;
        *) if [ "$_cap_idx" -eq 0 ]; then _cap_icon="👑"; else _cap_icon="⚓"; fi ;;
      esac
      _cap_idx=$((_cap_idx + 1))
      run hub_tmux set-option -w -t "$_crow_wid" @aib_seat "$_crow_seat"
      run hub_tmux set-option -w -t "$_crow_wid" @aib_role_display "$_crow_role"
      # 新欄而不是沿用 @aib_role_display:那個名字的語意是「角色」,把人格塞
      # 進去會讓下一棒讀到名實不符的欄(交接單第三節 (B) 建議)。
      #
      # ★角色副本仍然寫,但要誠實記一句:全庫盤點後**已無任何消費者**
      # (`grep -rn aib_role_display` 只剩本檔的寫入端與兩支測試的清除斷言)。
      # 保留的理由是診斷——`show-options -w` 看得到該窗被 sync 認成什麼角色,
      # 而那是排查「為什麼這一席沒被判成艦長」時第一個要看的值。
      # 若日後要拆,拆的是寫入端與 clear_window_state 兩處,不是只拆這一行。
      run hub_tmux set-option -w -t "$_crow_wid" @aib_persona "$_crow_persona"
      run hub_tmux set-option -w -t "$_crow_wid" @aib_captain_icon "$_cap_icon"
      run hub_tmux set-option -w -t "$_crow_wid" @aib_captain 1
    done <<EOF3
$(printf '%s' "$CAPTAIN_ROWS" | sort -t'|' -k1,1n -k2,2n -s)
EOF3
  fi

  # 清掉死窗的席位檔:席位檔是顯示資料,窗不在=席位不在,留著會顯示殭屍分頁。
  # ★rc=0 且逐行完整才准清理;**成功的空快照是合法清理輸入**(二輪審中2:
  # 最後一窗關閉後死席不得永久殘留)。刪前對該窗補一次存在檢查
  # (二輪審高2 TOCTOU:快照後新開的窗其席位檔不得誤刪)。
  # ★ 艙不一致時**一律不刪**,並且出聲(不是靜默跳過:靜默會讓「守衛沒跑」
  #   與「沒有死席」長得一樣)。rc 維持 0——清理是輔助動作,不得擋住渲染。
  if [ "$CABIN_OK" != "1" ]; then
    _t aib.captains_ctl.cross_cabin_no_cleanup --param "why=$CABIN_WHY" >&2
  fi
  if [ "$DRY" != "1" ] && [ "$snapshot_ok" = "1" ] && [ "$CABIN_OK" = "1" ] && [ -d "$SEATS_DIR" ]; then
    # 活窗集合=ids 快照(LIVE_IDS),不是 alive 行:role 被整筆拒的窗
    # 仍是活窗,它的席位檔不得被當死席清掉。
    local f live_ids base id CURRENT_IDS="" CURRENT_IDS_LOADED="" DM_USABLE=0
    live_ids=" ${LIVE_IDS} "
    for f in "$SEATS_DIR"/w*.json; do
      [ -e "$f" ] || continue
      base="$(basename "$f")"; id="${base#w}"; id="${id%.json}"
      case "$id" in *[!0-9]*|'') continue ;; esac   # 檔名壞形狀不動它
      case "$live_ids" in *" $id "*) continue ;; esac
      # ★ 舊寫法用 `display-message -p -t @<id> ''` 的 rc 判死活,而 tmux 對
      #   **任何**目標都回 0——於是這個守衛對每一個候選都說「還活著」,
      #   `continue` 永遠成立,清理從來沒有發生過。
      #   ★ 失效值就是它表示安全的那個值:回 0 = 不刪 = 看起來很保守。
      #   正解=拿**當下的**窗清單重查一次(快照之後新開的窗會在裡面),
      #   用集合成員判定,不用任何單一目標的 rc。
      if [ -z "$CURRENT_IDS_LOADED" ]; then
        CURRENT_IDS=" $(hub_tmux list-windows -t aihub -F '#{window_id}' 2>/dev/null | tr '\n' ' ') "
        CURRENT_IDS_LOADED=1
        # ★ 逐窗探測器要先校準才准用。實測(拋棄式 tmux server,2026-08-23):
        #     display-message -p -t @0        (存在)   -> rc=0
        #     display-message -p -t @999999   (不存在) -> rc=0   ← 舊守衛的根因
        #   所以這裡先問一個**一定不存在**的窗:它若也回成功,這支探測器沒有分辨力,
        #   一律不採信,只用當下窗清單。它若失敗,表示這個 tmux 分得出來,
        #   那就拿它當比窗清單更即時的第二來源(補快照與刪除之間新開的窗)。
        # ★ 失效方向是安全的:萬一那個 id 剛好存在,校準會判「沒有分辨力」,
        #   於是退回只信窗清單——那正是本來就正確的來源。
        if hub_tmux display-message -p -t "@999999999" '' >/dev/null 2>&1; then
          DM_USABLE=0
        else
          DM_USABLE=1
        fi
      fi
      case "$CURRENT_IDS" in
        *" @$id "*) continue ;;   # 當下窗清單裡有它:席位是活的,跳過
      esac
      # 窗清單裡沒有,但探測器有分辨力時再問它一次:快照與刪除之間新開的窗
      # 只有這條路看得見(TOCTOU)。沒有分辨力就不問——問了只會得到恆真的「還活著」。
      if [ "$DM_USABLE" = "1" ] && hub_tmux display-message -p -t "@${id}" '' >/dev/null 2>&1; then
        continue
      fi
      # ★ 10-02(a seat,f643 事故):這裡原本 `rm -f`。席位檔不只是顯示資料——它存
      #   session_uuid(原地復活要用)與人格/專案綁定。aihub 整個重建(kill-server 後重開)
      #   時窗 id 全換,舊席位檔全數被判死 ⇒ 四席一次被刪、復活要靠人工翻對話檔找 uuid。
      #   改成跟 seat_retire.py 同形狀搬進 retired/wN-<五碼>-<epoch>.json(顯示照樣消失,
      #   資料留著);搬不動就留在原地,不刪(失效方向:多一個殭屍分頁,不是丟資料)。
      _rdir="$SEATS_DIR/retired"
      _code="$(sed -n 's/.*"session_uuid"[[:space:]]*:[[:space:]]*"[^"]*\([0-9a-f]\{5\}\)".*/\1/p' "$f" 2>/dev/null | head -n 1)"
      mkdir -p "$_rdir" 2>/dev/null && mv -f "$f" "$_rdir/w${id}-${_code:-nocode}-$(date +%s).json" 2>/dev/null || true
    done
  fi
}


# --- status 格式(install 才設;sync 不重設樣式,避免蓋掉主題) --------------
install_status() {
  # 非艦長窗渲染為空字串=隱形分頁;艦長窗=" role@末五 ",當前窗反白。
  # 三輪審高1:status 只渲染 sync 驗證過的 @aib_role_display 副本,
  # 絕不渲染未清洗的原始 @aib_role。
  # the maintainer 08-14 拍板:顯示層不加 ~ 前綴(與右欄一致);trust 資料層不變。
  local fmt cur cur_attr
  # 現任標示的屬性隨「無底色」而變(root3 08-15 裁):
  #
  # 有底色時 `#[reverse]` 是 the maintainer 眼驗過的終稿(E2.16 線),不動。但無底色時
  # status-style 是 `bg=default,fg=colour252`,reverse 反轉後變成**淺灰實心塊**
  # ——拿掉背景反而讓那一格更顯眼。08-15 實測:只把 status-style 改成
  # bg=default 而沒動 reverse,the maintainer 回報的就是「艦長底色沒有被取消」。
  #
  # ★通則:**「拿掉顏色」與「拿掉視覺重量」是兩件事**。reverse/bold/underscore
  # 這類屬性不在 `bg=` 那個軸上,strip_background 掃不到;無底色的射程必須
  # 涵蓋 format 字串裡的屬性,不是只有樣式字串裡的 bg。
  if "$COCKPIT/theme-ctl.sh" --is-no-bg 2>/dev/null; then
    cur_attr="bold"
  else
    cur_attr="reverse"
  fi
  # the maintainer 08-14 令:名字欄顯示**綁定的人格**,角色文字移除(與右欄同令)。
  # 未綁人格=該段渲染成空,整列收斂為 " 👑 a seat "(現有 format 的空白
  # 自然吸收,不需要第二套分支)。引擎字樣不加——the maintainer 要的是移除 root,
  # 不是換一個字上去,而艦長列比右欄窄。
  fmt='#{?#{==:#{@aib_captain},1}, #{@aib_captain_icon} #{@aib_persona}@#{@aib_seat} ,}'
  cur='#{?#{==:#{@aib_captain},1},#['"$cur_attr"'] #{@aib_captain_icon} #{@aib_persona}@#{@aib_seat} #[default],}'
  run hub_tmux set-option -t aihub status on
  run hub_tmux set-option -t aihub status-position bottom
  # 10-10(the maintainer 眼驗令):艦長列與上面 Claude 的權限列之間空一行——status 改兩行,第 0 行空、第 1 行放
  #   原本的第 0 行(艦長分頁那一行)。原格式從全域讀,不手抄 tmux 預設字串;讀不到(dry-run 治具沒 tmux)
  #   就只設空行、不猜第 1 行。a seat 10-10 已在 931e/9668 活艙照這組選項套過,the maintainer 眼驗過。
  _fmt0="$(hub_tmux show-option -gv 'status-format[0]' 2>/dev/null || true)"
  run hub_tmux set-option -t aihub status 2
  run hub_tmux set-option -t aihub 'status-format[0]' ''
  if [ -n "$_fmt0" ]; then
    run hub_tmux set-option -t aihub 'status-format[1]' "$_fmt0"
  fi
  run hub_tmux set-option -t aihub status-interval 5
  # 10-10(the maintainer 眼驗令):艦長列與 Claude 輸入框下方那三行(📌 / CTX / 權限列)對齊——
  #   Claude Code 把那三行固定縮 2 格,tmux status 從第 0 欄起畫,所以這裡補 2 格;長度 8 → 10 跟著加。
  #   外層座艙的 status-left(cockpit-full.sh / title-beat.sh)同一條令、同樣補 2 格。
  #   10-14 語系盤查:「艦長:」走四語字典 aib.cockpit.hub_captain_label(開艙 / --install 時取一次;切語系後下次 --install 換);
  #   字典讀不到(印 !!key!! 或空)退回中文字面。
  _cap_label="$("${EZ2_PY3:-python3}" -I "$COCKPIT/i18n_lib.py" aib.cockpit.hub_captain_label 2>/dev/null || true)"
  case "$_cap_label" in ''|'!!'*) _cap_label='艦長:' ;; esac
  #   長度照顯示寬度算(全形算 2 格)再留 2 格,不寫死 10:英文「Captain:」比中文寬,寫死會被截(公開倉 PR #11 帶回)。
  _label="  $_cap_label "
  _label_w="$("${EZ2_PY3:-python3}" -I -c 'import sys, unicodedata as u; print(sum(2 if u.east_asian_width(c) in "WF" else 1 for c in sys.argv[1]))' "$_label" 2>/dev/null || echo 8)"
  run hub_tmux set-option -t aihub status-left "$_label"
  run hub_tmux set-option -t aihub status-left-length "$((_label_w + 2))"
  run hub_tmux set-option -t aihub status-right ''
    run hub_tmux set-option -w -g window-status-format "$fmt"
  run hub_tmux set-option -w -g window-status-current-format "$cur"
  run hub_tmux set-option -w -g window-status-separator ''
  run hub_tmux set-option -t aihub mouse on
}

case "$MODE" in
  assign) set_seat_flag "$TARGET_WID" captain_assigned true ;;
  unassign) set_seat_flag "$TARGET_WID" captain_assigned del ;;
  advisor)
    case "$ADVISOR_STATE" in
      on) set_seat_flag "$TARGET_WID" advisor true ;;
      off) set_seat_flag "$TARGET_WID" advisor del ;;
      *) echo "captains-ctl: usage --advisor <wid> on|off" >&2; exit 2 ;;
    esac ;;
esac
sync_windows
[ "$MODE" != "install" ] || install_status
# 三輪審中2:降級同步以 documented rc=3 回報,健康=0;
# 呼叫端(cockpit-full --ensure/aib --sync)已驗證容忍 rc=3。
exit "$SYNC_RC"
