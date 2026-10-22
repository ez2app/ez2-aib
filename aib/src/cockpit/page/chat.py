#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""262(c) `aib page chat`:讀你的頁、出區塊改動、寫進**草稿**。

規格 §五。艦長 a seat 09-17 00:0x 三條判準在這支檔裡的落點:
  ① 未綁時只讀不寫 → `assert_writable()`,所有寫入路徑的唯一入口。
  ② 不准有 publish 呼叫 → 本檔與 `api.py` 都沒有 `/publish` 字面;
     「發布要你按」那句的網址來自 `POST /draft` 回應的 `preview.publish_url`。
  ③ 每次寫草稿留 operation key + 席碼 + 時刻 → `operation_key()` 把三者組進同一個字串,
     它會被送到 PAGE 並寫進那邊的留痕(規格 §5.4),而且會印在座艙上。

★ 對話本身不在這支程式裡。「你說改什麼 → 出 diff」是**席位裡那個 AI** 的工作;
  這支 CLI 是傳輸與護欄:讀得到現況、把 ops 送出去、把錯誤翻成人話。
  (我已把這個讀法回報 root2 a seat / 艦長,要互動式 REPL 再加。)
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

import intake
import seat_address
import template_skill
from api import ApiError

# 套版 skill 檔住在產品樹的 docs/skills/;從這支檔往上三層就是產品樹(尺用副本樹時跟著走)。
PRODUCT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            os.pardir, os.pardir, os.pardir))

# 規格 §5.3:operation_key 走 ID_RE;`:` 合法,所以 ISO 時刻可以整段放進去。
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}$")
SEAT_ADDR_RE = re.compile(r"^@?[0-9a-f]{5}$")   # b15 位址:@ + session uuid 尾五碼(seat_address.uuid_tail5 的產物)
OPS_MAX = 50               # 規格 §5.1
OP_KINDS = ("create", "update", "move", "delete", "reorder")


class ChatError(Exception):
    """給人看的一句話。"""


def assert_writable(record):
    """① 的唯一守衛:沒有憑證就不准走任何寫入路徑。

    ★ 刀對準這支函式:把它拿掉,「未綁也能寫」那一格必須紅。
    """
    if not record or not record.get("assistant_token"):
        raise ChatError(
            "這台機器還沒綁這個頁面,所以我只能讀、不能改。"
            "要我改的話先跑 `aib page bind`,你到 ez2.page 後台輸入配對碼就好")
    return record


def operation_key(seat_addr, clock=None, seq=1, tag=""):
    """判準三:`<席碼>-<ISO 時刻>-<序號>`。組不出合法的鍵就停手,不退回一個「看起來像」的。

    `seat_addr` 是席位**位址**(`@` + session uuid 尾五碼,b15 之後艦隊唯一的席身分,seat_address.code_of 的產物;
    不是已退役的第二真相源 seat_id/舊碼)。這裡只把它放進 key,不猜、不頂替。

    `tag` 給用量收據用(規格 v2 §13.2 的例子是 `…-u0003`):草稿與用量各自一個序號空間,
    不共用 —— 兩本帳用同一把 key 會讓「這次寫入」與「這次模型呼叫」在冪等表上互相蓋掉。
    """
    seat = (seat_addr or "").strip()
    if not seat:
        raise ChatError("不知道自己是哪一席(--seat 或 AIB_SEAT_CODE),不編一個假的席碼寫上去")
    # b15 §三:合法位址只從 session_uuid 導出。給整條 uuid 就用全庫唯一的 uuid_tail5 導;給的是位址(@xxxxx)就要
    #   長得像 uuid 尾五碼(五個十六進位);別的字(舊 seat_id、角色名、隨便的字)具名拒,不猜、不退回舊碼。
    if len(seat.replace("-", "")) >= 32 and seat_address.uuid_tail5(seat):
        seat = "@" + seat_address.uuid_tail5(seat)
    if not SEAT_ADDR_RE.match(seat):
        raise ChatError("席位 %r 不是位址(要是 @ + session uuid 尾五碼,或整條 session uuid);b15 之後不收舊碼" % (seat_addr,))
    # ★ 席位位址在艦隊裡寫成 `a seat`,而規格的 ID_RE **不收 `@`**、而且第一個字必須是英數
    #   ⇒ 直接把席碼放進 key 會組出一個永遠非法的字串(尺 C3/C4/C6 09-17 00:2x 實際抓到,
    #     三格同時紅;沒有這幾格的話,錯誤會等到真的寫草稿時才由伺服器回 400)。
    #   規格 §5.3 自己的例子是 `aib-730aa-2026-09-16T15:41:07Z-u0003` —— 前綴 `aib-`、席碼不帶 `@`,
    #   這裡照它的形狀組。
    seat = seat.lstrip("@")
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$", seat):
        raise ChatError(
            "席碼 %r 放不進 operation key:規格只收英數與 _ . : -(去掉開頭的 @ 之後仍不合)"
            % (seat_addr,))
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    key = "aib-%s-%s-%s%04d" % (seat, stamp, tag, int(seq))
    if not ID_RE.match(key):
        raise ChatError("組出來的 operation key 不合規格(%r)" % key)
    return key


def load_ops(text):
    """吃一段 JSON:可以是 `[...]`,也可以是 `{"ops": [...]}`。形狀錯就具名拒收。"""
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise ChatError("ops 不是合法的 JSON:%s" % exc)
    if isinstance(parsed, dict):
        parsed = parsed.get("ops")
    if not isinstance(parsed, list) or not parsed:
        raise ChatError("ops 要是一個非空的陣列")
    if len(parsed) > OPS_MAX:
        raise ChatError("一次最多 %d 個 ops(規格 §5.1),收到 %d 個" % (OPS_MAX, len(parsed)))
    for i, op in enumerate(parsed):
        if not isinstance(op, dict) or op.get("op") not in OP_KINDS:
            raise ChatError("第 %d 個 op 的 `op` 要是 %s 之一" % (i + 1, "/".join(OP_KINDS)))
    return parsed


def load_skill_ops(text):
    """275(b):吃一段 JSON,只收五種 skill 操作名(`template_skill.SKILL_OPS`)。

    ★ 這是 `aib page chat --ops` 現在走的入口;`load_ops()` 是寫入 API 那五種的形狀,
      留給直接對傳輸層說話的人(尺)。第七種操作名在這裡就被拒,連翻譯都不會做(S4)。
    """
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise ChatError("ops 不是合法的 JSON:%s" % exc)
    if isinstance(parsed, dict):
        parsed = parsed.get("ops")
    if not isinstance(parsed, list) or not parsed:
        raise ChatError("ops 要是一個非空的陣列")
    if len(parsed) > OPS_MAX:
        raise ChatError("一次最多 %d 個 ops(規格 §5.1),收到 %d 個" % (OPS_MAX, len(parsed)))
    try:
        return template_skill.validate_skill_ops(parsed)
    except template_skill.SkillError as exc:
        raise ChatError(str(exc))


# RL-045(艦長 09-17 08:4x):這兩種操作原本寫註冊表、立刻生效;262(f)(RL-067,r21 起)改成寫草稿、人在預覽確認頁按發布,
# 所以送出前必須先把「現在 → 改成」的差異給人看並得到確認。
CONFIRM_OPS = ("set_profile", "set_theme")


# ---- PAGE 端讀回應的**真**形狀(root2 09-17 19:4x 端到端對正式站錄下,tests/fixtures/page-api/*.json)------------
# GET /draft:{has_draft, status:{draft_revision_id, draft_block_count, draft_updated_at, draft_profile, draft_appearance, dirty, …},
#             draft:{page, blocks, routes} | null, profile, appearance, review_url, demo_remaining}
# POST /draft、PUT /profile、PUT /appearance:{draft:{draft_revision_id, block_count, updated_at, …} | null, preview:{review_url, …}}
# ★ 2a108360 之前 chat 讀的是 draft.draft_revision_id / draft.block_count / 頂層 blocks——那是寫入回應的摘要形狀,
#   不是 GET /draft 的;尺的假伺服器照 chat 的想像回,所以全綠而正式站上「已有草稿的頁一個字都寫不進」(G3/G5)。
def draft_revision_of(response):
    """從讀回應或寫回應拿草稿 revision;拿不到回 None(呼叫端決定要不要停手,**不得把 None 當「沒草稿」送出去**)。"""
    src = response if isinstance(response, dict) else {}
    status = src.get("status") if isinstance(src.get("status"), dict) else {}
    if status.get("draft_revision_id"):
        return str(status["draft_revision_id"])
    draft = src.get("draft") if isinstance(src.get("draft"), dict) else {}
    page = draft.get("page") if isinstance(draft.get("page"), dict) else {}
    return str(page.get("revision_id") or draft.get("draft_revision_id") or "") or None


def draft_blocks_of(response):
    src = response if isinstance(response, dict) else {}
    draft = src.get("draft") if isinstance(src.get("draft"), dict) else {}
    blocks = draft.get("blocks") if isinstance(draft.get("blocks"), list) else src.get("blocks")
    return blocks if isinstance(blocks, list) else []


def expected_revision_for_write(draft_response):
    """有草稿就一定要拿到它的 revision(拿不到=停手,不送 null 讓伺服器 fork 第二份);沒草稿才是 None。"""
    if not draft_response.get("has_draft"):
        return None
    revision = draft_revision_of(draft_response)
    if not revision:
        raise ChatError("伺服器說有草稿,但讀回應裡找不到草稿版本(status.draft_revision_id);不猜、不送 null,請回報這一行")
    return revision


def profile_diff(skill_ops, state):
    """把 set_profile / set_theme 要改的東西整理成「欄位:現在 → 改成」的行。

    現值從 state 的 `appearance`(套版)與 `profile`(個人檔案)欄讀(262(f) 起 GET /blocks、GET /draft 都帶 profile,
    值=線上 + 草稿);讀不到就寫「現值未知」——不假裝知道。
    """
    src = state if isinstance(state, dict) else {}
    appearance = src.get("appearance") if isinstance(src.get("appearance"), dict) else {}
    profile = src.get("profile") if isinstance(src.get("profile"), dict) else {}
    lines = []
    for op in skill_ops:
        kind = op.get("op")
        if kind == "set_theme":
            lines.append("色系 theme_id:%s → %s" % (appearance.get("theme_id", "現值未知"), op.get("theme_id")))
        elif kind == "set_profile":
            for key in template_skill.PROFILE_SLOTS:
                if key not in op:
                    continue
                # 316 v1.1:avatar / cover 槽位對的是 profile 的 avatar_asset_id / cover_asset_id(GET /draft、/blocks 的 profile 帶);"" 印「(清空)」
                field = dict(template_skill.PROFILE_IMAGE_SLOTS).get(key, key)
                shown = "(清空)" if op[key] == "" else op[key]
                lines.append("%s:%s → %s" % (key, profile.get(field, "現值未知") or "(無)", shown))
    return lines


def apply_skill_ops(api, record, skill_ops, seat_addr, out, template_id=None,
                    expected_draft_revision=None, clock=None, seq=1, root=None, confirm=None,
                    open_browser=False, opener=None):
    """275(b):載入這一頁風格的 skill → 五種操作過白名單 → set_profile/set_theme 走 PUT 端點、其餘翻成 API 三種 → `apply_ops`。

    風格從哪來:`template_id` 明示優先;沒給就讀草稿/已發布的回應體找 theme_id
    (寫入 API v1 若沒有這欄 ⇒ 具名停手,不退回 S1)。

    RL-045:批裡有 set_profile / set_theme 時,先印差異、再問 `confirm(lines)`;
    `confirm` 沒給或回 False ⇒ 整批不送(先印「現在 → 改成」讓人看過再送;送出後進草稿,人在預覽確認頁按發布)。
    ★ 刀對準這一段:把它拿掉,「沒確認就送」那一格必須紅。
    """
    assert_writable(record)                       # ① 仍是唯一入口;這裡先擋一次,少讀一趟
    root = root or PRODUCT_ROOT
    state = None
    if expected_draft_revision is None or template_id is None:
        draft = api.get_draft()
        state = draft
        if expected_draft_revision is None:
            expected_draft_revision = expected_revision_for_write(draft)
            if expected_draft_revision is None and template_id is None:
                state = api.get_blocks()
    try:
        tid, _text = template_skill.load_skill(state, root, template_id=template_id)
        registry_ops, draft_ops = template_skill.split_skill_ops(skill_ops)
        # 兩條 PUT 的本體先組好再問確認:組不出來(avatar 沒端點、socials 形狀不對、讀不到形狀)就在這裡停,
        # 一個請求都不發——不然確認完才發現送不出去。
        profile_fields = template_skill.profile_request(registry_ops)
        if registry_ops and state is None:          # 明示了 --template 與 --expected-revision 時上面沒讀過:現值要真的讀
            state = api.get_draft()
        appearance = (state.get("appearance") if isinstance(state, dict) and isinstance(state.get("appearance"), dict) else {})
        appearance_body = template_skill.appearance_request(registry_ops, appearance.get("shape_id"))
        if registry_ops:
            lines = profile_diff(skill_ops, state)
            out("這些個人檔案/色系的改動會寫進草稿(要你在 ez2.page 預覽確認頁按「確定發布」才上線):")
            for line in lines:
                out("  " + line)
            if not (confirm and confirm(lines)):
                raise ChatError("上面的差異還沒被確認,這一批整批沒送;確認了就帶 --yes 再送一次")
        api_ops = template_skill.to_api_ops(draft_ops) if draft_ops else []
        # 288 / RL-099 第二點:少必填欄的區塊在這裡就停(伺服器會 422 page_block_<type>_invalid;先印清單那一列,一個請求都不發)。
        #   ★ 刀對準這一段:拿掉 ⇒ C8 必紅。
        missing = intake.missing_op_lines(draft_ops, template_skill.BLOCK_REQUIRED_KEYS, root)
        if missing:
            out("這一批有區塊少了必填欄(存不進去,伺服器會回 422):")
            for line in missing:
                out("  " + line)
            raise ChatError("補齊上面列的欄位再送;這一批整批沒送(準備清單:aib page intake)")
    except template_skill.SkillError as exc:
        raise ChatError(str(exc))
    except intake.IntakeError as exc:
        raise ChatError(str(exc))
    out("風格 %s:skill 已載入(%s)" % (tid, os.path.relpath(template_skill.skill_path(tid, root), root)))
    # 262(d) 增補 §二:確認過的 set_profile / set_theme 真的送,各自一把 operation key(tag p / t,與草稿的序號空間分開)。
    #   順序 profile → appearance → 草稿;三條路各自冪等、各自留痕,**不是一個交易**:後面那條失敗,前面已生效的不會回滾
    #   (印出來讓人知道),要回就再送一次反向的。
    # 262(f)(r21 起;RL-067 乙):這兩條 PUT **寫草稿**,回 draft + preview.review_url;draft 為 null = 值與線上相同、沒開草稿。
    #   每一條 PUT 都會把草稿推到新版本 ⇒ 後面的草稿區塊批要接在它回的 revision 上(不然 CAS 撞 409)。
    last_answer = None
    if profile_fields is not None:
        key = operation_key(seat_addr, clock=clock, seq=seq, tag="p")
        try:
            answer = api.put_profile(key, profile_fields)
        except ApiError as exc:
            raise ChatError("個人檔案沒改成(PUT /profile):" + write_error_text(exc))
        last_answer = answer
        announce_quota(answer, out)
        info = answer.get("draft") if isinstance(answer.get("draft"), dict) else None
        if info:
            out("個人檔案已寫進草稿(要在預覽確認頁按「確定發布」才上線):%s%s"
                % (", ".join(sorted(profile_fields)), "(先前送過,伺服器回同一份)" if answer.get("replayed") else ""))
            expected_draft_revision = draft_revision_of(answer) or expected_draft_revision
        else:
            out("個人檔案:%s 與線上相同,沒有變更" % ", ".join(sorted(profile_fields)))
    if appearance_body is not None:
        key = operation_key(seat_addr, clock=clock, seq=seq, tag="t")
        try:
            answer = api.put_appearance(key, appearance_body["theme_id"], appearance_body["shape_id"])
        except ApiError as exc:
            raise ChatError("色系沒改成(PUT /appearance)%s:%s" % (";個人檔案那條已寫進草稿,不會回滾" if profile_fields is not None else "", write_error_text(exc)))
        last_answer = answer
        announce_quota(answer, out)
        got = answer.get("appearance") or {}
        info = answer.get("draft") if isinstance(answer.get("draft"), dict) else None
        if info:
            out("色系已寫進草稿(要在預覽確認頁按「確定發布」才上線):%s(形狀照舊 %s)%s"
                % (got.get("theme_id", appearance_body["theme_id"]), got.get("shape_id", appearance_body["shape_id"]),
                   "(先前送過,伺服器回同一份)" if answer.get("replayed") else ""))
            expected_draft_revision = draft_revision_of(answer) or expected_draft_revision
        else:
            out("色系:%s 與線上相同,沒有變更" % appearance_body["theme_id"])
    if not api_ops:
        out("這一批沒有草稿區塊操作")
        # 278 同一段:PUT 回了預覽確認頁就印、開瀏覽器;draft 為 null(沒變更)時 preview.review_url 也是 null,什麼都不印
        announce_review(last_answer, out, open_browser=open_browser, opener=opener)
        return {"ok": True, "draft": (last_answer or {}).get("draft"), "preview": (last_answer or {}).get("preview"), "registry_only": True}
    try:
        return apply_ops(api, record, api_ops, seat_addr, out,
                         expected_draft_revision=expected_draft_revision, clock=clock, seq=seq, open_browser=open_browser, opener=opener)
    except ChatError as exc:
        if last_answer is not None:
            done = []
            if profile_fields is not None:
                done.append("個人檔案")
            if appearance_body is not None:
                done.append("色系")
            raise ChatError("、".join(done) + "請求已成功,不會回滾;區塊草稿這條失敗:" + str(exc))
        raise


def read_current(api, record, out, public_url=None):
    """讀現況。綁了讀草稿(沒草稿讀已發布);沒綁只讀公開頁 HTML。"""
    if record and record.get("assistant_token"):
        draft = api.get_draft()
        if draft.get("has_draft"):
            out(_draft_summary(draft))
            return draft
        out("目前沒有草稿,下面是已發布的內容(改動會從它 fork 一份草稿出來)")
        blocks = api.get_blocks()
        out(_blocks_summary(blocks.get("blocks") or []))
        return blocks
    if not public_url:
        raise ChatError("還沒綁,而且你沒給 --public-url,我沒有東西可以讀")
    out("(未綁定:只讀得到公開頁,改不了東西)")
    html = api.public_html(public_url)
    out(html)
    return {"public_html": html}


def _draft_summary(draft):
    status = draft.get("status") if isinstance(draft.get("status"), dict) else {}
    blocks = draft_blocks_of(draft)
    lines = ["草稿版本 %s(共 %s 塊,最後更新 %s)"
             % (draft_revision_of(draft) or "?",
                status.get("draft_block_count", len(blocks)), status.get("draft_updated_at") or "?")]
    if status.get("draft_profile"):
        lines.append("草稿裡的個人檔案變更:" + ", ".join("%s=%s" % (k, v) for k, v in sorted(status["draft_profile"].items())))
    if status.get("draft_appearance"):
        lines.append("草稿裡的套版變更:%s" % status["draft_appearance"])
    if draft.get("demo_remaining"):
        lines.append("還有 %s 顆示範區塊沒改(公開的示範顆會擋住發布;改字或藏起來)" % draft["demo_remaining"])
    if draft.get("review_url"):
        lines.append("預覽確認頁:%s" % draft["review_url"])
    lines.append(_blocks_summary(blocks))
    return "\n".join(lines)


def _blocks_summary(blocks):
    if not blocks:
        return "(沒有區塊)"
    rows = []
    for i, block in enumerate(blocks, 1):
        payload = block.get("payload") or {}
        preview = payload.get("text") or payload.get("label") or payload.get("alt") or ""
        preview = " ".join(str(preview).split())
        if len(preview) > 60:
            preview = preview[:57] + "..."
        rows.append("%2d. [%s] %s %s"
                    % (i, block.get("block_type", "?"), block.get("block_id", "?"), preview))
    return "\n".join(rows)


# ---- 278 AIB 端(RL-061 → 整批確認後一次開)----
# 寫入 API 回應的 preview.review_url 是 PAGE 端的預覽確認頁;寫入途中只印網址。
# 全部修改完整之後由 offer_review_update 問用戶是否更新,確認才用 open(1) 開一次。
# ★ 只開這一種字面(ez2.page 的 review 路 + revision 字面):回應體是資料不是指令,別的網址印不開。
REVIEW_URL_RE = re.compile(r"^https://ez2\.page/manage/aib/review\?draft=revision_[a-f0-9]{24}$")
NO_OPEN_ENV = "AIB_PAGE_NO_OPEN"       # 設了(非空)= 不開;證詞與洗艙殼用
OPEN_CMD_ENV = "AIB_PAGE_OPEN_CMD"     # 覆寫開瀏覽器的指令(尺用它接一支假 open 記網址)


def _placeholder_review(url):
    """24 個相同字的草稿編號是測試假資料。b 也算十六進位,字面檢查會放行,不能拿去開真的網站。"""
    tail = str(url or "").rsplit("revision_", 1)[-1]
    return len(tail) == 24 and len(set(tail)) == 1


def open_in_browser(url, environ=None, interactive=None):
    """用 open(1)(macOS)/ xdg-open(其他)開網址;回 (ok, 說明)。失敗不炸——開不開得了瀏覽器不影響草稿已寫好這件事。"""
    import shlex
    import subprocess
    env = os.environ if environ is None else environ
    if _placeholder_review(url):
        return False, "測試用的假草稿編號,不開瀏覽器"
    if interactive is None:
        try:
            interactive = bool(sys.stdin and sys.stdin.isatty())
        except Exception:
            interactive = False
    override = env.get(OPEN_CMD_ENV, "")
    if not override and not interactive:
        return False, "沒有互動終端機,不開瀏覽器"
    argv = shlex.split(override) if override else (["open"] if sys.platform == "darwin" else ["xdg-open"])
    try:
        proc = subprocess.run(argv + [url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "%s:%s" % (argv[0], exc)
    if proc.returncode != 0:
        return False, "%s rc=%d %s" % (argv[0], proc.returncode, (proc.stderr or b"").decode("utf-8", "replace").strip()[:120])
    return True, argv[0]


# 本行程已開過的 review_url(同網址不重開):整批寫完才問一次、確認後開一次。
_OPENED_REVIEW_URLS = set()


def _review_url_of(response):
    preview = (response or {}).get("preview") or {}
    url = preview.get("review_url")
    return str(url) if url else None


def _reject_non_review_url(url, out):
    """字面白名單:不是 ez2.page review 路就印「不開」並回 True(呼叫端應立刻 return)。"""
    if not REVIEW_URL_RE.match(url):
        out("預覽確認頁的網址不是 ez2.page 的 review 路,不開:%r" % url[:120])
        return True
    return False


def announce_review(response, out, open_browser=False, opener=None, environ=None):
    """印預覽確認頁網址。預設只印不開——寫草稿途中可被叫多次,開瀏覽器留給 offer_review_update。

    `open_browser` 為真且沒設 AIB_PAGE_NO_OPEN 才用 opener 開。回 (url 或 None, 有沒有開)。
    """
    env = os.environ if environ is None else environ
    url = _review_url_of(response)
    if not url:
        return None, False
    if _reject_non_review_url(url, out):
        return url, False
    out("預覽確認頁:%s" % url)
    out("看完按「確定發布」就上線,按「退回」就丟掉草稿(未登入會先登入再回到同一頁;LINE 登入後請再點一次這條)")
    if not open_browser or env.get(NO_OPEN_ENV):
        return url, False
    if url in _OPENED_REVIEW_URLS:
        out("這一版預覽確認頁已經開過,不再重開")
        return url, False
    ok, note = (opener or open_in_browser)(url)
    if ok:
        _OPENED_REVIEW_URLS.add(url)
    out("已用瀏覽器打開(%s;帶 --no-open 就不開)" % note if ok else "沒能自動打開瀏覽器(%s),請自己點上面那條" % note)
    return url, bool(ok)


def ask_update_default(url, out=None):
    """整批改完後的預設詢問:stdin 讀一行,y/yes/是 才算同意。非 TTY 或 EOF ⇒ 不同意。"""
    prompt = "草稿已全部改完。要打開預覽確認頁、一次更新嗎？[y/N] "
    try:
        if not sys.stdin or not sys.stdin.isatty():
            if out:
                out("沒有互動輸入,先不開預覽確認頁(要開請回覆 y,或帶 --open)")
            return False
        answer = input(prompt)
    except (EOFError, OSError):
        if out:
            out("沒有互動輸入,先不開預覽確認頁(要開請回覆 y,或帶 --open)")
        return False
    return str(answer or "").strip().lower() in ("y", "yes", "是")


def offer_review_update(response, out, ask=None, opener=None, environ=None, opened=None):
    """全部修改完整之後:印網址 → 問用戶是否更新 → 確認才開一次瀏覽器。

    `ask(url) -> bool`;沒給就用 ask_update_default。`opened` 是可覆寫的 set(尺用);
    沒給就用行程內 `_OPENED_REVIEW_URLS`。AIB_PAGE_NO_OPEN 設了 ⇒ 只印不問不開。
    回 (url 或 None, 有沒有開)。
    """
    env = os.environ if environ is None else environ
    seen = _OPENED_REVIEW_URLS if opened is None else opened
    url = _review_url_of(response)
    if not url:
        return None, False
    # 先印(與 announce_review 同字面白名單);寫入途中若已印過同網址,這裡再印一次也無妨——人要看最後一版。
    if _reject_non_review_url(url, out):
        return url, False
    out("預覽確認頁:%s" % url)
    out("看完按「確定發布」就上線,按「退回」就丟掉草稿(未登入會先登入再回到同一頁;LINE 登入後請再點一次這條)")
    if env.get(NO_OPEN_ENV):
        return url, False
    if url in seen:
        out("這一版預覽確認頁已經開過,不再重開")
        return url, False
    decide = ask if ask is not None else (lambda u: ask_update_default(u, out=out))
    if not decide(url):
        out("先不開預覽確認頁;要更新時再打開上面那條,或帶 --open 重跑")
        return url, False
    ok, note = (opener or open_in_browser)(url)
    if ok:
        seen.add(url)
    out("已用瀏覽器打開(%s;帶 --no-open 就不開)" % note if ok else "沒能自動打開瀏覽器(%s),請自己點上面那條" % note)
    return url, bool(ok)


def apply_ops(api, record, ops, seat_addr, out, expected_draft_revision=None,
              clock=None, seq=1, open_browser=False, opener=None):
    """把 ops 寫進草稿。回 API 的回應體。

    `expected_draft_revision` 沒給就現讀一次草稿拿(規格 §5.1:`null` 只在「真的沒草稿」時用)。
    預設只印預覽確認頁網址、不開瀏覽器——整批改完由 offer_review_update 問過再開一次。
    """
    assert_writable(record)                       # ① 的唯一入口
    key = operation_key(seat_addr, clock=clock, seq=seq)
    if expected_draft_revision is None:
        expected_draft_revision = expected_revision_for_write(api.get_draft())   # 沒草稿才是 None ⇒ 請伺服器 fork 一份
    out("operation key:%s" % key)                 # ③:席碼與時刻都在這一行裡
    try:
        response = api.post_draft(key, expected_draft_revision, ops)
    except ApiError as exc:
        raise ChatError(write_error_text(exc))
    if response.get("replayed"):
        out("(這一批先前送過,伺服器回同一份結果,沒有寫第二次)")
    announce_quota(response, out)
    info = response.get("draft") or {}
    out("草稿已更新:版本 %s,共 %s 塊"
        % (info.get("draft_revision_id", "?"), info.get("block_count", "?")))
    preview = response.get("preview") or {}
    if preview.get("preview_url"):
        out("預覽:%s" % preview["preview_url"])
    # ② 發布永遠是人按的;網址來自這份回應,不是去打 publish
    if preview.get("publish_url"):
        out("發布要你自己到 %s 按(我沒有權限發布,也不會去按)" % preview["publish_url"])
    else:
        out("發布要你自己到 ez2.page 後台按(我沒有權限發布)")
    # 278+:寫入途中只印網址;開瀏覽器留給整批結束後的 offer_review_update
    announce_review(response, out, open_browser=open_browser, opener=opener)
    return response


def announce_quota(response, out):
    quota = response.get("quota") if isinstance(response, dict) else None
    if isinstance(quota, dict):
        out("今日 AIB 額度 %s/%s" % (quota.get("used", "?"), quota.get("limit", "?")))


def _taipei_time(value, fmt):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(ZoneInfo("Asia/Taipei")).strftime(fmt)
    except (ValueError, TypeError):
        return None


def write_error_text(exc):
    """規格 §六 的碼 → 人話。**兩種 409 要講不同的話**(規格 §七 A10 那一格)。"""
    code = exc.code
    if code == "page_api_aib_write_disabled":
        since = _taipei_time(exc.body.get("since"), "%Y-%m-%d %H:%M") or "不知道何時關的"
        return ("這個頁面的主人已關閉「允許 AIB 改頁」(%s),這一批沒送;請他到 ez2.page 後台打開再試" % since)
    if code == "page_api_aib_quota_exceeded":
        quota = exc.body.get("quota") or {}
        reset = _taipei_time(quota.get("resets_at"), "%H:%M") or "未知時刻"
        return "今日 AIB 額度用完(%s/%s),明天 %s 重置,或到 ez2.page 升級" % (quota.get("used", "?"), quota.get("limit", "?"), reset)
    if code == "page_api_asset_not_found":
        # 316 v1.1(root2 複審 F1):形狀對但不在這一頁圖庫的 asset_id ⇒ PAGE 回 400 details:[avatar_asset_id];要說是哪一欄、要先上傳
        return "這張圖不在這一頁的圖庫(%s);先 `aib page upload <圖>` 拿 asset_id 再設" % ("、".join(exc.details) or "avatar / cover")
    if code == "PAGE_WORKSPACE_CONFLICT":
        return ("草稿在我讀到之後又被改過(可能是你在後台改的,或另一席 AI)。"
                "我重新讀一次草稿再把改動套上去,不會覆蓋你剛打的字")
    if code == "PAGE_DRAFT_STALE":
        body = exc.body or {}
        return ("你在後台發布過新版,我手上這份草稿是舊的基礎"
                "(草稿基於 %s,現在已發布的是 %s)。我重讀一次再改"
                % (body.get("base_revision_id", "?"), body.get("published_revision_id", "?")))
    if code == "page_api_draft_exists":
        return ("已經有一份草稿了,我不會不打招呼就蓋掉它。"
                "先告訴我要接著那份改,還是請你到後台把它丟棄")
    if code == "page_api_operation_key_reused":
        return "同一個 operation key 被拿去送不同內容了,這是我這邊的錯,我換一把新的鍵重送"
    if code == "page_api_token_expired":
        return "與 ez2.page 的連線已過期(30 天),跑 `aib page bind` 重新配對就好"
    if code == "page_api_token_revoked":
        import revoked_note   # 0.1.42:伺服器有帶撤的時間 / 誰撤的 ⇒ 講清楚;舊伺服器照舊那句
        return revoked_note.note(exc) or ("你在 ez2.page 後台解除了這個助理,所以我不能再寫。"
                "如果不是你解除的,先去後台的「AI 助理」頁看一下")
    if code == "page_api_publish_requires_human":
        body = exc.body or {}
        return ("發布要你到 %s 按 —— 這是設計成這樣的,AI 永遠發布不了"
                % (body.get("publish_url") or "ez2.page 後台"))
    if code == "page_api_rate_limited":
        return "寫太快被限流了,等一分鐘我再送"
    if code in ("PAGE_WORKSPACE_LOCKED",):
        return "PAGE 那邊正忙(有人也在改這頁),等幾秒我再送"
    if exc.http_status == 422:
        return "這批改動沒通過內容檢查:%s" % ("; ".join(exc.details) or code)
    return "寫草稿失敗:%s" % (code or exc)
