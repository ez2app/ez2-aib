# SPDX-License-Identifier: Apache-2.0
# 標題取樣：原本是 title-beat.sh sample() 裡的 heredoc(`"$EZ2_PY3" - <<'PY'`),原文搬出來成檔。
# 2026-11-12 goal2 title-beat:bash 舊路與 Rust 新路(crates/aib-title)都跑這一份，標題字串只有一個來源。
# 輸入只有環境變數 EZ2_COCKPIT_DIR / EZ2_TB_UUID / EZ2_TB_BEAT / EZ2_TB_COLOR / EZ2_TB_PRINT_ONLY / EZ2_TB_WINDOW(+ 下面讀的狀態檔)。
import json, os, re, sys, time

# 介面號(PLAN-aibtitle-install-043dd-1113 r2 包 2):Rust 的 aib-title 帶 AIB_TITLE_PROTO 來;
# 不是這裡的號 ⇒ 什麼都不做、rc 75(aib-title 轉成 126,窗格連 3 幀就退回 title-beat.sh)。
# bash 舊路不帶這個變數 ⇒ 不檢查。改了輸入變數或輸出格式就把號 +1,crates/aib-title/src/lib.rs 的
# SAMPLER_PROTO 一起改(crates/aib-title/tests/title.rs 把兩邊釘在一起)。
TB_PROTO = 1
_want = os.environ.get("AIB_TITLE_PROTO")
if _want is not None and _want != str(TB_PROTO):
    sys.stderr.write("title_sample.py: 介面 %s,aib-title 要 %s\n" % (TB_PROTO, _want))
    sys.exit(75)
sys.path.insert(0, os.path.join(os.environ["EZ2_COCKPIT_DIR"], "title"))
sys.path.insert(0, os.path.join(os.environ["EZ2_COCKPIT_DIR"], "..", "binding"))
sys.path.insert(0, os.environ["EZ2_COCKPIT_DIR"])          # dm_header.local_addr(IP 與 dm 抬頭同源)
from title_beat import (trust_light, title_string, cabin_label,  # noqa: E402
                        seat_record, seat_light, cabin_since, uptime_str,
                        version_segment, version_state, short_version,
                        model_label)

uuid = os.environ.get("EZ2_TB_UUID") or ""

# ★★ 2026-08-21(車道 E,舊載體完全移除):**舊 registry 那條路已整條移除**。
#   移除前的實測(a seat):偽造一筆 sessions/by-id/<uuid>.json 進去,
#   標題的署名段整個被它接管(`NAILSIG@zzzzz/Nail` 蓋掉 `a seat/Claude`)
#   —— 所以它是**活碼不是死碼**,拆掉是行為改變,不是 no-op。
#   ★ 08-16 那句「指到空目錄顯示的字一模一樣」是對的量測、過度推廣的結論:
#     兩端都空當然一樣,那一發沒有「必須紅」的那一端。
#   拆掉仍然安全的理由是**母體**:現存 52 席有 uuid,舊 registry 517 筆,
#   **交集 0**;且該目錄最新一筆停在 08-11、不會再有新的。
#   自此席位檔是唯一來源(上限黃燈,理由見 title_beat.seat_record 的 docstring)。
seat_based = None
# ★ 這四行原本在解釋「舊 registry 那條路走得通時 seat 會 NameError」——**那條路已不存在**,
#   註解跟著改而不是留著:留著會讓下一個人去找一條找不到的分支。
#   保留的結論:seat / seat_based 要先有定義,下面版號段會引用。
seat = None
# EH.2:席位目錄一律走單一真相源 seat_onboard.seats_dir()
#   (順位 HUB 導出 > EZ2_AIB_INSTANCE_HOME > 主艙,理由見該函式的 docstring)。
# ★ 刻意**不在這裡再寫一份**:全庫已經有五份各自實作的 seats_dir(),
#   而 tests/test-seats-dir-resolution.py 的 14 支債就是這樣長出來的。
#   複製一份能讓那支測試轉綠,但那是讓判準過關,不是把問題修好。
# ★ 取不到就讓 seat 留 None:**主艙不是回退來源,是衝突**。
#   顯示「未綁定」比顯示別艙的殘檔誠實 —— 08-21 五席全部印錯 seat id
#   就是回退成功造成的,而**成功不會叫**。
seats = os.environ.get("EZ2_SEATS_DIR")
if not seats:
    try:
        sys.path.insert(0, os.environ["EZ2_COCKPIT_DIR"])
        from seat_onboard import seats_dir as _seats_dir
        seats = _seats_dir()
    except Exception:
        seats = None
wid = os.environ.get("EZ2_TB_WINDOW") or ""
if seats:
    try:
        with open(os.path.join(seats, "w%s.json" % wid.lstrip("@")),
                  encoding="utf-8") as fh:
            seat = json.load(fh)
    except (OSError, ValueError):
        seat = None
if seat:
    # 活性兩個獨立訊號,都在**這裡**量(判定是純函數,不碰系統):
    #   ① pid 還在不在 —— 直接問作業系統,比讀心跳檔可靠
    #   ② transcript 多久沒動 —— 席位在工作的獨立證據
    alive = False
    try:
        pid = int(seat.get("pid") or 0)
        if pid > 0:
            os.kill(pid, 0)          # 不送訊號,只問「這個 pid 在嗎」
            alive = True
    except (OSError, ValueError, TypeError):
        alive = False
    age = None
    model = None
    # ★★ 2026-08-21 修(既有缺陷,不是本次引入):transcript 要用**被顯示
    #   的那一席自己的** session_uuid,不是執行本腳本那個行程的環境變數。
    #   席位是用**窗號**選的(EZ2_TB_WINDOW),而 uuid 來自
    #   EZ2_SESSION_UUID / CLAUDE_CODE_SESSION_ID —— 兩者不同源。
    #   實測:EZ2_TB_WINDOW=@3 顯示 `a seat`,而讀的是
    #   CLAUDE_CODE_SESSION_ID=8864a1c7(root 席)的 transcript,
    #   pm 席位檔登記的是 efc0612e。
    #   後果:標題上任何一席的「transcript 多久沒動」都是**執行者自己的**
    #   活性,不是那一席的。加上模型之後同一條路會把別人的模型畫到這一席
    #   頭上,更明顯。
    #   ★ 今天兩邊剛好都是 claude-opus-5,所以模型看起來是對的——
    #     **一個巧合遮住了缺陷**。取不到席位 uuid 才退回環境變數。
    _tuuid = None
    if isinstance(seat.get("session_uuid"), str):
        _tuuid = seat["session_uuid"]
    uuid_for_transcript = _tuuid or uuid
    if uuid_for_transcript:
        for base in (os.path.expanduser("~/.claude/projects"),):
            try:
                for d in os.listdir(base):
                    p = os.path.join(base, d,
                                     uuid_for_transcript + ".jsonl")
                    if os.path.exists(p):
                        age = time.time() - os.path.getmtime(p)
                        # 模型:讀 transcript **尾端固定一段**,不整檔掃。
                        # ★ 成本是硬約束:標題每 5 秒一拍、十六個 instance
                        #   各一份,而 transcript 會長到幾千行。所以只 seek
                        #   到檔尾 64KB 抓最後一次出現的 model。
                        # ★ 只有 claude 走得到這裡(它的 transcript 用
                        #   session uuid 當檔名,可直接定位)。**codex /
                        #   agy / copilot 今天取不到,那些席會退回兩段
                        #   sig,不是壞掉。**
                        #   codex 的模型在 rollout meta 首行的
                        #   `payload.base_instructions.provenance.model`
                        #   (實查 gpt-5.6-sol),但檔名帶的是 thread id
                        #   不是 session id,配對要掃目錄——每拍掃一次太貴。
                        #   欄位路徑記在這裡,接手的人不必重查。
                        try:
                            sz = os.path.getsize(p)
                            with open(p, "rb") as fh:
                                if sz > 65536:
                                    fh.seek(sz - 65536)
                                tail = fh.read().decode("utf-8", "ignore")
                            hits = re.findall(r'"model"\s*:\s*"([^"]+)"',
                                              tail)
                            if hits:
                                model = hits[-1]
                        except (OSError, ValueError):
                            model = None
                        break
            except OSError:
                pass
    # 人格:走**右欄同一支** seat_persona.persona_of(),不另做取值——
    # 它已經擋掉 `@` 與 `|`(假人格@他人五碼會在畫面上冒充別席身分)。
    # 取不到就傳 None,seat_record 會退回 role,畫面不會空掉。
    persona = None
    try:
        sys.path.insert(0, os.environ["EZ2_COCKPIT_DIR"])
        from seat_persona import persona_of, load_display
        try:
            persona = persona_of(seat, load_display()) or None
        except TypeError:            # 舊簽名相容
            persona = persona_of(seat) or None
    except Exception:
        persona = None
    # 段位圖示:讀右欄**算完的結果**,不在這裡重算一次。
    # ★ 為什麼:👑 的真規則(全域唯一、只給第一艦隊首位、未編制永不
    #   戴冠)住在 pane-right-ai.sh,而本檔看不到別席。在此之前標題自
    #   己判,判出來的是「root 一律 ⚓」——艙裡只有一個艦長時那是錯的
    #   (⚓ 語意=第二位起),the maintainer 08-21 實見。抄一份規則進來只會讓兩
    #   個畫面繼續各說各話,所以改成消費它的產物。
    # ★ 比對優先用 seat_id 不用窗號:窗號會被重用(死窗重生仍是同號),
    #   而五碼綁的是這一席。取不到就傳 None,由純函數退回原本的自判。
    rank_hint = None
    try:
        _rm = os.path.join(os.environ["EZ2_COCKPIT_STATE_DIR"],
                           "ai-rowmap.json")
        _sid = seat.get("seat_id")
        with open(_rm, encoding="utf-8") as fh:
            for _row in json.load(fh):
                _d = (_row or {}).get("detail") or {}
                if _sid and _d.get("seat") == _sid and _d.get("mark"):
                    rank_hint = _d["mark"]
                    break
    except Exception:
        rank_hint = None
    seat_based = seat_record(seat, pid_alive=alive, transcript_age_s=age,
                             now_s=time.time(), persona=persona,
                             rank_hint=rank_hint, model=model)

bind_item = None
try:
    from bind_queue import list_items
    for it in list_items():
        if it.get("requested", {}).get("session_uuid") == uuid:
            bind_item = it
            break
except Exception:
    bind_item = None

now_ms = int(time.time() * 1000)          # 時鐘只在這裡讀,判定本身是純函數
if seat_based:
    # 席位檔這條路的上限是**黃燈**:trust 永遠是 self_reported,
    # 三項證據齊全只證明「活著、身份可讀」,不證明有第二方背書。
    light = seat_light(seat_based, now_ms)
    sig = seat_based.get("sig") or "unbound"
else:
    light = trust_light(None, bind_item, now_ms)
    sig = "unbound"
role = os.environ.get("EZ2_SESSION_ROLE")
# 艙位碼:哪一艙。10-07 the maintainer:`<IP>:[<艙位碼四碼>]`,與 dm 地址同一組碼(以前是 session 名·token 八碼,開艙時間印兩次)。
cabin = cabin_label(os.environ.get("EZ2_AIB_INSTANCE"))
# IP 與 dm 抬頭同一個來源(dm_header.local_addr:AIB_DM_ADDR > Tailscale > 區網 > 127.0.0.1)。
# probe 要跑子行程(tailscale/ipconfig),這支每拍起一個 python,所以把結果快取 10 分鐘在狀態目錄;
# 取不到就 addr 留空 ⇒ 標題只印 `[艙位碼]`,不印假 IP。
addr = ""
_addr_dir = os.environ.get("EZ2_COCKPIT_STATE_DIR") or os.environ.get("EZ2_AIB_INSTANCE_HOME") or ""
_addr_file = os.path.join(_addr_dir, "title-addr.json") if _addr_dir else ""
try:
    if _addr_file:
        with open(_addr_file, encoding="utf-8") as fh:
            _c = json.load(fh)
        if isinstance(_c, dict) and _c.get("addr") and time.time() - float(_c.get("epoch") or 0) < 600:
            addr = str(_c["addr"])
except (OSError, ValueError):
    addr = ""
if not addr:
    try:
        from dm_header import local_addr as _local_addr   # noqa: E402
        addr = _local_addr()
        if addr and _addr_file:
            _tmp = _addr_file + ".tmp.%d" % os.getpid()
            with open(_tmp, "w", encoding="utf-8") as fh:
                json.dump({"addr": addr, "epoch": time.time()}, fh)
            os.replace(_tmp, _addr_file)
    except Exception:  # noqa: BLE001 —— 取不到 IP 不擋標題
        addr = ""
# 10-08 the maintainer:外網 IP 排在區網 IP 前面(`AIB <外網IP> | <區網IP>:[艙位碼]`)。查詢要上網 ⇒ 只讀快取(30 分鐘),
#   過期就背景更新(public_ip.spawn_refresh,不等它),這一拍照用舊值;取不到就不印外網段。AIB_PUBLIC_IP_LOOKUP=0 關。
public = ""
if cabin and cabin != "no-cabin" and _addr_dir and os.environ.get("AIB_PUBLIC_IP_LOOKUP") != "0":   # 沒有艙段就不印外網段,也不查
    try:
        import public_ip as _pub   # noqa: E402
        _pub_file = os.path.join(_addr_dir, "title-public-ip.json")
        public, _stale = _pub.read_cache(_pub_file)
        if _stale and os.environ.get("EZ2_TB_PRINT_ONLY") != "1" and os.environ.get("EZ2_ONESHOT") != "1":
            # --print(測試與除錯)只讀快取、不上網;真正在跑的節拍才背景更新。
            # 10-08:單幀模式(EZ2_ONESHOT=1,治具/截幀;pane-right-tabs.sh 畫完就走)也不起 —— 脫離的查詢行程在面板
            #   結束後才把快取寫進治具目錄,撞上 tearDown 刪目錄(推送前 test-tree-rails-ar-0902 Errno 66),而且測試真的上網
            _pub.spawn_refresh(_pub_file)
    except Exception:  # noqa: BLE001 —— 外網 IP 取不到不擋標題
        public = ""
# 這一艙是**什麼時候啟用的**(the maintainer 08-16:尾段改顯示開艙時間)。
# 來源=instance-token 檔的建立時間:它是艙身份的實體,艙在它才在。
# ★ 不用席位檔的 started_epoch —— 那是「這一**席**開的時間」,不是艙的
#   (實測差了 17 小時:席 08/16 12:41,艙 08/15 19:38)。
# ★ 佐證:tmux session_created 與它只差 1 秒,兩個獨立來源互相對得上。
since = ""
_home = os.environ.get("EZ2_AIB_INSTANCE_HOME")
if _home:
    try:
        # E2.2 純度:時鐘/時區只在這一端(採集端)讀,注入給純函式。
        since = cabin_since(os.path.getctime(os.path.join(_home,
                                                          "instance-token")),
                            localtime=time.localtime)
    except OSError:
        since = ""
# 已執行多久(the maintainer 08-16:尾段再加一格)。同一個來源,不另取時間。
up = ""
if _home:
    try:
        up = uptime_str(os.path.getctime(os.path.join(_home, "instance-token")),
                        time.time())
    except OSError:
        up = ""
# ---- 版號與更新偵測(the maintainer 08-21)-------------------------------------------
# ★★ 硬約束:**標題每秒重跑一次**。所以這一段不准碰網路、不准起 git 子行程
#   ——十六個 instance 每秒各跑一次 `git ls-remote` 是災難。兩個訊號都只做
#   **純檔案讀取**:
#     ↑  本地 main 的 ref 內容 ≠ origin/main 的 ref 內容(有東西還沒合)
#     ⟳  本地 main 的 ref **mtime 晚於這一席開席時間**(這席跑的是舊碼)
#
# ★ `↑` 的誠實邊界:它反映的是**上一次 fetch 之後**的狀態。沒有人 fetch,
#   它就不會亮——這不是 bug 是這個做法的代價,換來的是每秒零網路。要讓它
#   永遠準,得有東西定期 fetch(那是另一條線,不在標題裡做)。
#
# ★ `⟳` 用**席位**的 started_epoch 不用艙的:問題是「這一席載入的碼有沒有
#   過期」,而席與艙可以差很多(本檔上面那段實測差過 17 小時)。
#
# ★★ 三態不是兩態:讀不到判準來源時傳 None(畫 `?`),**不是傳 False**。
#   ref 可能被 pack 進 packed-refs 而讓 refs/heads/main 這個檔不存在;
#   那時把「讀不到」當成「沒有新版本」,這個指示器的失效值就恰好等於它表示
#   安全的那個值——壞掉時長得跟一切正常一模一樣。
version_seg = ""
_repo = os.environ.get("EZ2_AIB_ROOT")
if _repo:
    _ver = None
    try:
        with open(os.path.join(_repo, "VERSION"), encoding="utf-8") as fh:
            _ver = fh.read().strip()
    except OSError:
        _ver = None
    if _ver:
        _behind = None
        _changed = None
        _local = os.path.join(_repo, ".git", "refs", "heads", "main")
        _remote = os.path.join(_repo, ".git", "refs", "remotes", "origin",
                               "main")
        try:
            with open(_local, encoding="utf-8") as fh:
                _lsha = fh.read().strip()
            with open(_remote, encoding="utf-8") as fh:
                _rsha = fh.read().strip()
            if _lsha and _rsha:
                _behind = (_lsha != _rsha)
        except OSError:
            _behind = None                    # packed-refs / 非 git 樹:不明
        try:
            _moved = os.path.getmtime(_local)
            _born = float((seat or {}).get("started_epoch") or 0)
            if _born > 0:
                _changed = (_moved > _born)
        except (OSError, TypeError, ValueError):
            _changed = None
        version_seg = version_segment(_ver, behind=_behind,
                                      code_changed=_changed)
        # ★ 狀態另外落一個小檔給狀態列用。**標題與狀態列是兩個顯示層**:
        #   標題只吃純文字(終端機 OSC 標題通道沒有樣式),狀態列吃得下顏色。
        #   判準只算一次,兩層各自決定畫成形狀還是顏色。
        # ★ 為什麼用檔案而不是多印一行:`--print` 的契約是**零接觸 tmux**
        #   (test_print_mode_never_touches_tmux),而標題字串本身是 stdout
        #   的唯一內容(黃金幀比對逐字)。多印一行會同時弄壞這兩件事。
        try:
            _sd = os.environ.get("EZ2_COCKPIT_STATE_DIR")
            if _sd:
                _tmp = os.path.join(_sd, ".title-version.tmp.%d" % os.getpid())
                with open(_tmp, "w", encoding="utf-8") as fh:
                    json.dump({"ver": short_version(_ver),
                               "state": version_state(_behind, _changed)}, fh)
                os.replace(_tmp, os.path.join(_sd, "title-version.json"))
        except OSError:
            pass

print(title_string(sig, light, int(os.environ.get("EZ2_TB_BEAT") or 0), role,
                   color=os.environ.get("EZ2_TB_COLOR") == "1", cabin=cabin,
                   since=since, uptime=up, version=version_seg, addr=addr, public=public or None))
