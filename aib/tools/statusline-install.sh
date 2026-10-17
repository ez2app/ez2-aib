#!/usr/bin/env bash
# statusline-install.sh —— 三家量尺寫端安裝器(claude / codex / agy)
#
# 規格正本 SPEC-L10-statusline-vendor-1107e-0905.md §2.3(七條行為)
#         + WO-L10b §六(a6b2e 04:33/04:36 兩裁,依 a seat 實測)
#
# 設計上的兩條紅線:
#   1. 目的地清單一律讀 deploy/statusline/MANIFEST.json,**不得在本檔手抄第二份**。
#      兩份清單只會在某一天不一樣,而那一天沒有人會發現(缺陷七判例)。
#   2. 失敗要吵。靜默跳過的畫面與「已是最新」逐字相同 —— 所以每一種說不出話的情況都具名 rc≠0。
#
# 不做的事(§2.3-6,刻意):不寫 settings.json / config.toml / hooks.json。
#   接線是第二個閘門;混在一起會讓「裝了但沒接上」與「沒裝」在收據上同形。
#
# ★ codex 專屬的靜默失效(dev3 2026-09-05 四跑實測,報告 REPORT-L10b-trusted-hash-87dec-0905.md):
#     · 換掉本安裝器管的**腳本檔** ⇒ hook 照跑新內容,trusted_hash 不變。**安全,不必重新信任。**
#     · 改 **hooks.json** ⇒ 那條 hook 從此安靜地不執行:rc=0、沒有錯誤、沒有提示。
#       codex exec 無法重新信任,只有互動 TUI 會問。
#   ⇒ --check 要抓的是「hooks.json 變了而 trusted_hash 沒變」,見 codex_trust_check()。
#     腳本變而信任不變是**綠**,不是紅(做成紅會教人去重新信任一個沒壞的東西)。
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$REPO_ROOT/deploy/statusline/MANIFEST.json"
# $HOME 一律從環境取,不得寫死(§2.3-7:臂就是靠換 HOME 來跑)
: "${HOME:?HOME 未設,安裝器拒絕猜測目的地}"
WITNESS="$HOME/.ez2/statusline-install-witness.json"
KNOWN="$REPO_ROOT/deploy/statusline/KNOWN-SHAS.json"

MODE="install"
for arg in "$@"; do
  case "$arg" in
    --check)   MODE="check" ;;
    --dry-run) MODE="dry-run" ;;
    --auto)    MODE="auto" ;;
    --help|-h) MODE="help" ;;
    *) printf '錯誤:未知選項 %s(只接受 --check / --dry-run / --auto / --help)\n' "$arg" >&2; exit 2 ;;
  esac
done

if [ "$MODE" = "help" ]; then
  cat <<'USAGE'
用法: tools/statusline-install.sh [--check | --dry-run]

  (無選項)  依 MANIFEST 安裝;內容相同的檔不寫、不備份(冪等)
  --check    只比對不寫,逐支印 same / differs / missing;任一 differs|missing ⇒ rc=1
  --dry-run  印出會做什麼,零寫入,rc 與 --check 同
  --auto     給 aib update / install.sh / aib init 結尾叫的:只換 aib 裝的舊版(sha 在 KNOWN-SHAS.json),
             你改過或不是 aib 的不動(印一行手動換法);沒在用的那家不新建;不印接線說明。
             AIB_STATUSLINE_AUTO=0 ⇒ 什麼都不做

目的地清單唯一來源:deploy/statusline/MANIFEST.json
本工具不改 settings.json / config.toml / hooks.json —— 接線那幾行由它印出、由人自己貼。
USAGE
  exit 0
fi

# 10-17 the maintainer「以後都應該自動更新」(@abb89 交辦):自動模式可以整個關掉;關了就一個位元組都不碰
if [ "$MODE" = "auto" ] && [ "${AIB_STATUSLINE_AUTO:-1}" = "0" ]; then
  printf '狀態列自動更新已關(AIB_STATUSLINE_AUTO=0);要手動換:%s\n' "$REPO_ROOT/tools/statusline-install.sh"
  exit 0
fi

# ── 讀 MANIFEST(讀不到就吵,不得靜默回空清單)──────────────────────────────
# ★ 靜默回空清單的失效形狀是「假的嚴格」:一個字都沒裝,而畫面上長得像「全部已是最新」。
if [ ! -r "$MANIFEST" ]; then
  printf '錯誤:讀不到 MANIFEST:%s\n' "$MANIFEST" >&2
  exit 3
fi

ITEMS="$(python3 - "$MANIFEST" "$MODE" "$KNOWN" "$HOME" "$REPO_ROOT" <<'PY'
import hashlib, json, os, sys
try:
    m = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as e:
    sys.stderr.write("錯誤:MANIFEST 不是合法 JSON:%s\n" % e); sys.exit(3)
items = m.get("items")
if not isinstance(items, list) or not items:
    sys.stderr.write("錯誤:MANIFEST 的 items 是空的或不是陣列 —— 拒絕當成『沒有東西要裝』\n"); sys.exit(3)
for i, it in enumerate(items):
    for f in ("src", "dest", "mode", "engine"):
        if not it.get(f):
            sys.stderr.write("錯誤:MANIFEST items[%d] 缺欄位 %s\n" % (i, f)); sys.exit(3)
    dest = it["dest"]
    # dest 只准 $HOME 一個變數:別的變數會在別人的機器上展開成別的地方,而且不會叫
    rest = dest.replace("$HOME", "", 1)
    if "$" in rest:
        sys.stderr.write("錯誤:items[%d] 的 dest 含 $HOME 以外的變數:%s\n" % (i, dest)); sys.exit(3)
    if any(c in dest for c in " \t*?["):
        sys.stderr.write("錯誤:items[%d] 的 dest 含空白或 glob 字元(安靜的放寬):%s\n" % (i, dest)); sys.exit(3)
# --auto:每一項先分好要不要動(其他模式一律 go)
#   same / known(家目錄那支的 sha 是 aib 發過的某一版)⇒ go
#   unknown(你改過或不是 aib 的)⇒ user;missing 而且那一家沒有任何一支是 aib 的 ⇒ unused(沒在用,不新建)
mode, home, root = sys.argv[2], sys.argv[4], sys.argv[5]
known = {}
if mode == "auto":
    try:
        known = json.load(open(sys.argv[3], encoding="utf-8")).get("shas") or {}
    except Exception as e:
        sys.stderr.write("錯誤:讀不到 KNOWN-SHAS.json(%s)—— 自動模式認不出哪支是 aib 裝的,一支都不換\n" % e); sys.exit(3)
def sha(path):
    try:
        return hashlib.sha256(open(path, "rb").read()).hexdigest()
    except OSError:
        return None
state = []
for it in items:
    dest = it["dest"].replace("$HOME", home, 1)
    if mode != "auto":
        state.append("go"); continue
    have = sha(dest)
    if have is None:
        state.append("missing")
    elif have == sha(os.path.join(root, it["src"])) or have in (known.get(it["src"]) or []):
        state.append("go")
    else:
        state.append("user")
in_use = {it["engine"] for it, st in zip(items, state) if st == "go"}
for it, st in zip(items, state):
    if st == "missing":
        st = "go" if it["engine"] in in_use else "unused"
    print("\t".join((it["src"], it["dest"], str(it["mode"]), it["engine"], st)))
PY
)" || exit 3

# ── 逐項比對 ────────────────────────────────────────────────────────────────
n_same=0; n_write=0; n_missing=0; n_differ=0; n_user=0; rc=0
declare -a NAMED_BAD=()

sha_of() { shasum -a 256 "$1" 2>/dev/null | awk '{print $1}'; }

while IFS=$'\t' read -r src dest mode engine auto; do
  [ -n "${src:-}" ] || continue
  abs_src="$REPO_ROOT/$src"
  abs_dest="${dest/\$HOME/$HOME}"
  if [ "$MODE" = "auto" ]; then
    case "$auto" in
      unused) continue ;;
      user)
        printf '  %-8s %-46s 你改過(或不是 aib 的),沒自動換;要換成這一版:%s\n' "$engine" "${abs_dest#$HOME/}" "$REPO_ROOT/tools/statusline-install.sh"
        n_user=$((n_user+1)); continue ;;
    esac
  fi

  if [ ! -r "$abs_src" ]; then
    printf '  %-52s 來源不存在 ← 具名失敗\n' "$src" >&2
    NAMED_BAD+=("$src(來源不存在)"); rc=3; continue
  fi

  if [ ! -e "$abs_dest" ]; then
    state="missing"
  elif cmp -s "$abs_src" "$abs_dest"; then
    state="same"
  else
    state="differs"
  fi

  cur_mode=""
  [ -e "$abs_dest" ] && cur_mode="$(stat -f%Lp "$abs_dest" 2>/dev/null || stat -c%a "$abs_dest" 2>/dev/null)"
  mode_ok="yes"
  [ "$state" = "same" ] && [ "$cur_mode" != "$mode" ] && mode_ok="no"

  case "$MODE" in
    check|dry-run)
      if [ "$state" = "same" ] && [ "$mode_ok" = "yes" ]; then
        printf '  %-8s %-46s same\n' "$engine" "${abs_dest#$HOME/}"; n_same=$((n_same+1))
      elif [ "$state" = "same" ]; then
        printf '  %-8s %-46s mode 不符(現 %s ≠ MANIFEST %s)\n' "$engine" "${abs_dest#$HOME/}" "$cur_mode" "$mode"
        NAMED_BAD+=("$dest(mode ${cur_mode}≠$mode)"); n_differ=$((n_differ+1)); rc=1
      elif [ "$state" = "missing" ]; then
        printf '  %-8s %-46s missing\n' "$engine" "${abs_dest#$HOME/}"
        NAMED_BAD+=("$dest(missing)"); n_missing=$((n_missing+1)); rc=1
      else
        printf '  %-8s %-46s differs\n' "$engine" "${abs_dest#$HOME/}"
        NAMED_BAD+=("$dest(differs)"); n_differ=$((n_differ+1)); rc=1
      fi
      ;;
    install|auto)
      if [ "$state" = "same" ]; then
        # 內容相同一律不備份、不寫 —— 冪等的判準是「寫入次數」,不是 rc
        if [ "$mode_ok" != "yes" ]; then
          chmod "$mode" "$abs_dest" || { NAMED_BAD+=("$dest(chmod 失敗)"); rc=4; continue; }
          printf '  %-8s %-46s mode 修正 → %s\n' "$engine" "${abs_dest#$HOME/}" "$mode"; n_write=$((n_write+1))
        else
          n_same=$((n_same+1))
        fi
        continue
      fi
      destdir="$(dirname "$abs_dest")"
      if [ ! -d "$destdir" ]; then
        # §三(a) 要求「空的臨時 HOME 裡跑完 rc=0 且每一項 dest 都存在」⇒ 目錄要建。
        # 但**建不起來要吵**:靜默跳過的畫面與「已是最新」逐字相同。
        mkdir -p "$destdir" 2>/dev/null || {
          printf '  %-8s %-46s 目的地目錄建不起來 %s ← 具名失敗\n' "$engine" "${abs_dest#$HOME/}" "$destdir" >&2
          NAMED_BAD+=("$dest(目錄建不起來)"); rc=4; continue; }
      fi
      if [ ! -w "$destdir" ]; then
        printf '  %-8s %-46s 目的地目錄不可寫 ← 具名失敗\n' "$engine" "${abs_dest#$HOME/}" >&2
        NAMED_BAD+=("$dest(不可寫)"); rc=4; continue
      fi
      if [ -e "$abs_dest" ]; then
        # 沿用家目錄現有慣例:<dest>.ez2-backup-2026-07-21T22-36-11-138Z
        stamp="$(python3 -c 'import datetime;print(datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-")+"%03dZ"%(datetime.datetime.now(datetime.timezone.utc).microsecond//1000))')"
        cp -p "$abs_dest" "$abs_dest.ez2-backup-$stamp" || {
          printf '  %-8s 備份失敗,不覆蓋 ← 具名失敗\n' "$engine" >&2
          NAMED_BAD+=("$dest(備份失敗)"); rc=4; continue; }
      fi
      cp "$abs_src" "$abs_dest" && chmod "$mode" "$abs_dest" || {
        NAMED_BAD+=("$dest(寫入失敗)"); rc=4; continue; }
      printf '  %-8s %-46s 已更新\n' "$engine" "${abs_dest#$HOME/}"; n_write=$((n_write+1))
      ;;
  esac
done <<< "$ITEMS"

# ── codex 失信任面(a6b2e 04:36 裁,依 dev3 實測)────────────────────────────
# 抓的狀態:hooks.json 變了**而** trusted_hash 沒跟著變 ⇒ 那條 hook 現在靜默不跑。
# ★ 我不能直接判 trusted_hash 對不對(雜湊函式未知),所以靠安裝時寫下的驗證檔比「兩者是否同時變」。
# ★ 腳本變而信任不變 ⇒ 綠。那是實測過的安全狀態,不是紅。
codex_hooks_json="$HOME/.codex/hooks.json"
codex_config="$HOME/.codex/config.toml"

read_trusted_hashes() {
  [ -r "$codex_config" ] || { echo ""; return; }
  grep -A1 '^\[hooks\.state\.' "$codex_config" 2>/dev/null | grep 'trusted_hash' | sed 's/.*= *//' | tr -d '"' | sort | tr '\n' ','
}

codex_trust_check() {
  # 沒接線就沒有這個問題:hooks.json 不存在 = codex 這一家還沒接上,不是壞掉
  [ -r "$codex_hooks_json" ] || { printf '  codex    hooks.json 不存在 ⇒ 尚未接線(不是錯誤;接線那一行見下方)\n'; return 0; }
  local cur_hooks_sha cur_hashes w_hooks_sha w_hashes
  cur_hooks_sha="$(sha_of "$codex_hooks_json")"
  cur_hashes="$(read_trusted_hashes)"
  if [ ! -r "$WITNESS" ]; then
    printf '  codex    驗證檔還沒建立 ⇒ 這一格無法判(不謊報綠);跑一次安裝器就會建立\n'
    return 0
  fi
  w_hooks_sha="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("codex",{}).get("hooks_json_sha",""))' "$WITNESS" 2>/dev/null)"
  w_hashes="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("codex",{}).get("trusted_hashes",""))' "$WITNESS" 2>/dev/null)"
  if [ "$cur_hooks_sha" != "$w_hooks_sha" ] && [ "$cur_hashes" = "$w_hashes" ]; then
    printf '  codex    ★ hooks.json 變了而 trusted_hash 沒變 ⇒ 那條 hook 現在**靜默不執行**\n' >&2
    printf '           codex 自己不會說一個字(rc=0、無錯誤、無提示);量尺會安靜停寫。\n' >&2
    printf '           修法:在 codex **互動** TUI 跑一次,出現 Hooks need review 時選 Trust all and continue。\n' >&2
    printf '           codex exec 模式無法重新信任(未信任時它連 trusted_hash 都不寫)。\n' >&2
    NAMED_BAD+=("codex hooks.json 已變更但信任未更新"); return 1
  fi
  printf '  codex    hooks.json 與 trusted_hash 一致(或兩者同時更新過)\n'
  return 0
}

write_witness() {
  # ★ 驗證檔自己也算寫入。第一版每跑一次就重寫一次(written_at 每次不同)⇒ 冪等面當場紅:
  #   輸出逐字說「10 支已是最新」,而整棵樹的 mtime+sha 變了。
  #   ——「輸出說沒寫」與「真的沒寫」是兩件事,這支工具自己踩過一次,所以現在先比再寫。
  local hooks_sha hashes
  hooks_sha="$(sha_of "$codex_hooks_json")"
  hashes="$(read_trusted_hashes)"
  mkdir -p "$(dirname "$WITNESS")" 2>/dev/null || return 0
  python3 - "$WITNESS" "${hooks_sha:-}" "${hashes:-}" <<'PY' 2>/dev/null || true
import json, sys, datetime, os
path, hooks_sha, hashes = sys.argv[1], sys.argv[2], sys.argv[3]
codex = {"hooks_json_sha": hooks_sha, "trusted_hashes": hashes,
         "note": "--check 用這兩個值判『hooks.json 變了而 trusted_hash 沒變』。腳本檔的 sha 刻意不記:實測換腳本不影響信任。"}
if os.path.exists(path):
    try:
        old = json.load(open(path, encoding="utf-8"))
        if old.get("codex") == codex:
            sys.exit(0)          # 內容沒變 ⇒ 一個位元組都不寫(冪等)
    except Exception:
        pass                     # 讀不動就重寫一份好的
json.dump({"schema": "ez2-statusline-install-witness-v1",
           "written_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "codex": codex},
          open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
}

if [ "$MODE" = "auto" ]; then
  codex_trust_check >/dev/null || rc=1      # 自動模式只在真的出事時開口(stderr);平常的綠燈不印
else
echo
echo "── codex 信任面 ──────────────────────────────────────────"
codex_trust_check || rc=1
fi

# ── 接線那幾行(只印,不代貼;§2.3-6)────────────────────────────────────────
# ★ 只在 install 模式印。--check / --dry-run 是**診斷**,不是說明書。
#   把「要重新信任」這種指示無條件印在 --check 裡,會讓真正出事那一次的警告
#   與每一次的樣板逐字相同 —— 那正是 DEFECT-13 本身的形狀(永遠亮著的警告不帶資訊)。
#   真的出事時,codex_trust_check() 會在 --check 裡具名開口,那一句才是有資訊的。
if [ "$MODE" = "install" ]; then
echo
echo "── 接線(本工具不代貼,以下由你自己貼)────────────────────"
python3 - "$MANIFEST" <<'PY'
import json, sys
w = json.load(open(sys.argv[1], encoding="utf-8")).get("wiring", {})
for eng in ("claude", "agy", "codex"):
    c = w.get(eng)
    if not c:
        continue
    print("  %-7s %s → %s" % (eng, c["file"], c["key"]))
    print("          %s" % c["line"])
    for x in c.get("extra") or []:
        print("          %s → %s" % (x["key"], x["line"]))
        if x.get("note"):
            print("          (%s)" % x["note"])
    if c.get("retrust_required"):
        print("          ★ 貼完之後必須重新信任,否則那條 hook 會安靜地不執行:")
        print("            在 codex 互動 TUI 跑一次 → Hooks need review → Trust all and continue")
        print("            (codex exec 模式無法重新信任。換掉腳本檔本身不影響信任。)")
PY
fi

# ── 收尾 ────────────────────────────────────────────────────────────────────
[ "$MODE" = "auto" ] || echo
case "$MODE" in
  install)
    [ $rc -eq 0 ] && write_witness
    if [ "$n_write" -eq 0 ]; then
      printf '%d 支已是最新\n' "$n_same"
    else
      printf '%d 支已更新,%d 支已是最新\n' "$n_write" "$n_same"
    fi
    ;;
  auto)
    # 一支 aib 的狀態列都沒有(沒在用)⇒ 驗證檔也不建:自動模式在沒用的機器上一個位元組都不寫
    [ $rc -eq 0 ] && [ $((n_write + n_same)) -gt 0 ] && write_witness
    if [ "$n_write" -gt 0 ] || [ "$n_user" -gt 0 ]; then
      printf '狀態列:%d 支已更新、%d 支已是最新、%d 支你改過沒動\n' "$n_write" "$n_same" "$n_user"
    fi
    ;;
  check|dry-run)
    printf '%s:same %d / differs %d / missing %d\n' "$MODE" "$n_same" "$n_differ" "$n_missing"
    ;;
esac
if [ "${#NAMED_BAD[@]}" -gt 0 ]; then
  printf '具名有問題的項目(%d):\n' "${#NAMED_BAD[@]}" >&2
  for b in "${NAMED_BAD[@]}"; do printf '  · %s\n' "$b" >&2; done
fi
exit $rc
