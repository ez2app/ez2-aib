#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""`aib page` 的入口:bind / chat / oneshot / list / intake / upload / import。

被 `src/cockpit/aib` 用 `exec python3 .../page/cli.py "$@"` 叫起來(與 dm / dm-queue 同形)。

結束碼:0 成功;1 失敗(訊息是人話,印在 stderr);2 用法錯。
★ 不吃 stdin 以外的互動;stdin 只在 `--ops -` / `oneshot -` 時讀。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_COCKPIT = os.path.dirname(_HERE)
for _p in (_HERE, _COCKPIT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bind as bind_mod          # noqa: E402
import chat as chat_mod          # noqa: E402
import intake as intake_mod      # noqa: E402
import intake_sources            # noqa: E402
import intake_linkinbio          # noqa: E402,F401  (289 包一:import 就把四站轉接器登記進 intake_sources)
import intake_search             # noqa: E402  (289 (h):搜尋回退——只有 --search 才走,不會自動搜)
import intake_website            # noqa: E402,F401  (289 (d):網站爬取轉接器,explicit_only——只認用戶親手給的網址)
import intake_oembed             # noqa: E402,F401  (289 (e):五平台 oEmbed 轉接器 youtube / tiktok / x / threads / instagram)
import intake_images             # noqa: E402  (289 (d):快照裡的圖 → 288 上傳 → asset_id)
import autofill as autofill_mod  # noqa: E402  (289 (c):描述 + 套版 skill 生成只缺的欄)
import template_skill            # noqa: E402
import upload as upload_mod      # noqa: E402
import store                     # noqa: E402
import usage as usage_mod        # noqa: E402
import oneshot as oneshot_mod      # noqa: E402  (the maintainer 09-29:intent → v3.109 組版契約 → 只寫草稿)
from api import ApiError, PageApi, TransportError   # noqa: E402

BASE_ENV = "EZ2_PAGE_API_BASE"
SEAT_ENV = "AIB_SEAT_CODE"


def _out(line):
    print(line, flush=True)


def _m(_name, **params):
    """執行時的訊息走四語鍵 aib.page.*(語系盤查:英文介面不准印中文);讀不到印鍵名,不讓指令崩。"""
    try:
        import i18n_lib
        return i18n_lib.t("aib.page." + _name, params or None, locale=i18n_lib.resolve_locale())
    except Exception:  # noqa: BLE001
        return _name


def _locale():
    try:
        import i18n_lib
        return i18n_lib.resolve_locale()
    except Exception:  # noqa: BLE001
        return "zh-TW"


def _seat_addr(args, environ):
    """席位位址(`@` + session uuid 尾五碼;b15:席身分只有這一個,不讀退役的舊欄)。"""
    return args.seat or environ.get(SEAT_ENV) or ""


def _api_for(record, args, environ):
    base = args.base or environ.get(BASE_ENV)
    token = (record or {}).get("assistant_token")
    return PageApi(base_url=base, token=token)


def _pick_record(args, environ):
    """--tenant/--page 指名;沒指名就用唯一那一份(多份會要你指名,不猜)。"""
    if args.tenant and args.page:
        return store.load_token(args.tenant, args.page, environ)
    if args.tenant or args.page:
        raise store.StoreError(_m("tenant_page_both"))
    return store.only_token(environ)


def cmd_bind(args, environ):
    seat = _seat_addr(args, environ)
    label = args.seat_label or (("%s %s" % (seat, args.role)).strip() if seat else "")
    if args.serial and not label:
        label = "AIB"
    if not label:
        raise SystemExit("aib page bind: " + _m("bind_need_label"))
    api = _api_for(None, args, environ)
    if args.serial:
        record, path = bind_mod.run_serial_bind(api, args.serial, label, _out, environ=environ)
        args.serial = None
    else:
        _out(_m("bind_pair_code"))
        record, path = bind_mod.run_bind(api, label, _out, environ=environ)
    _out(_m("bind_saved", path=path))
    return 0


def cmd_unbind(args, environ):
    import json as _json
    import unbind as unbind_mod
    try:
        record = _pick_record(args, environ)
    except store.StoreError:
        if args.tenant or args.page or store.list_tokens(environ):
            raise
        record = None
    local_only = getattr(args, "local_only", False)
    api = _api_for(record, args, environ) if record and not local_only else None
    res, rc = unbind_mod.unbind(record, api, environ, local_only=local_only)
    if args.json:
        _out(_json.dumps(res, ensure_ascii=False))
    elif res.get("ok"):
        _out(_m("unbind_" + res["state"], slug=res.get("slug") or ""))
        if res.get("hint"):
            _out(res["hint"])
    else:
        _out(res.get("hint") or res["why"])
    return rc


def _offer_review_after_writes(args, environ, response):
    """整批寫完才問要不要更新;確認後開一次。--no-open / AIB_PAGE_NO_OPEN 跳過;--open 不問直接開。"""
    if getattr(args, "no_open", False):
        return
    ask = (lambda url: True) if getattr(args, "open_after", False) else None
    chat_mod.offer_review_update(response, _out, ask=ask, environ=environ)


def cmd_chat(args, environ):
    record = _pick_record(args, environ)
    api = _api_for(record, args, environ)
    if args.autofill is not None:
        return _chat_autofill(args, environ, record, api)
    if args.ops is None:
        chat_mod.read_current(api, record, _out, public_url=args.public_url)
        return 0
    text = sys.stdin.read() if args.ops == "-" else open(args.ops, encoding="utf-8").read()
    ops = chat_mod.load_skill_ops(text)   # 275(b):只收五種 skill 操作名
    chat_mod.assert_writable(record)      # 未綁時在這裡就停,不會送出任何請求
    response = chat_mod.apply_skill_ops(api, record, ops, _seat_addr(args, environ), _out,
                             template_id=args.template,
                             expected_draft_revision=args.expected_revision, seq=args.seq,
                             confirm=(lambda lines: True) if args.yes else None,
                             open_browser=False)   # 寫入途中不開;整批結束再問
    _offer_review_after_writes(args, environ, response)
    return 0


def _chat_autofill(args, environ, record, api):
    """289 (c):描述 + 套版 skill ⇒ 只缺的欄(自介 / 在線狀態 / text 內容 / 示範圖占位)全進草稿,一次 review_url;零 publish。"""
    chat_mod.assert_writable(record)          # 未綁時在這裡就停,零請求
    state = _current_state(api)
    ops, placeholders, notes = autofill_mod.plan(state, args.autofill, template_skill.BLOCK_REQUIRED_KEYS)
    tmp_root = os.path.join(store.state_root(environ)[0], "tmp")
    os.makedirs(tmp_root, exist_ok=True)
    more, more_notes = autofill_mod.materialize_placeholders(placeholders, state, api, _seat_addr(args, environ), _out, environ=environ, tmp_root=tmp_root)
    for note in notes + more_notes:
        _out("  " + note)
    ops = ops + more
    if not ops:
        _out(_m("autofill_nothing"))
        return 0
    response = chat_mod.apply_skill_ops(api, record, ops, _seat_addr(args, environ), _out, template_id=args.template, seq=args.seq,
                             confirm=(lambda lines: True) if args.yes else None, open_browser=False)
    _offer_review_after_writes(args, environ, response)
    return 0


def cmd_oneshot(args, environ):
    """the maintainer 09-29 19:00「AIB 裡一句話建整頁」:intent JSON → 組版 → 只寫草稿 → 印預覽確認頁網址。

    一句話 → intent 是席上 AI 照 skill `ez2-page-oneshot` 做的;這裡只收 intent。
    憑證用這台機器存的綁定(同 `chat --ops`),不問人要、不印出來。
    """
    record = _pick_record(args, environ)
    token = (record or {}).get("assistant_token")
    if args.intent is None:
        text = None
    elif args.intent == "-":
        text = sys.stdin.read()
    else:
        try:
            with open(args.intent, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            raise oneshot_mod.OneshotError(_m("oneshot_read_fail", path=args.intent, error=exc.strerror or exc))
    if args.checklist:
        intent = oneshot_mod.load_intent(text, confirm_clear=True)[0] if text is not None else None
        api = _api_for(record, args, environ) if token else None
        if not token:
            _out(_m("oneshot_checklist_unbound"))
        oneshot_mod.checklist(api, intent, _out, environ, token=token, lang=args.lang)
        return 0
    if text is None:
        raise oneshot_mod.OneshotError(_m("oneshot_need_intent"))
    intent, clear = oneshot_mod.load_intent(text, confirm_clear=args.confirm_clear or args.dry_run)
    if clear and args.dry_run and not args.confirm_clear:
        _out(_m("oneshot_clear_note"))
    if args.dry_run and not token:
        oneshot_mod.compose_only(intent, _out, environ)
        return 0
    chat_mod.assert_writable(record)          # 未綁在這裡就停,零請求
    seat = _seat_addr(args, environ)
    prefix = chat_mod.operation_key(seat, seq=args.seq, tag="o") if (seat or not args.dry_run) else "aib-dryrun"
    api = _api_for(record, args, environ)
    result, broker = oneshot_mod.apply(api, intent, clear, _out, environ, prefix,
                                       dry_run=args.dry_run, token=token)
    if args.dry_run:
        result = dict(result, review_url=None, draft_revision_id=None)   # 組版器用假草稿號拼出的網址不能給人看
    if args.json:
        _out(oneshot_mod.redact(json.dumps(result, ensure_ascii=False, indent=2), token))
    if args.dry_run:
        _out("DRY_RUN_WRITES=%d%s" % (len(broker.writes), _m("oneshot_dry_writes_note")))   # 前綴給機器讀,不翻
        _out(_m("oneshot_dry_rerun"))
        return 0
    if broker.last_write is not None:
        chat_mod.announce_quota(broker.last_write, _out)
    url = result.get("review_url")
    wrote = any(w[3] == 200 for w in broker.writes)
    if not url:
        _out(_m("oneshot_no_change"))
        _out("REVIEW_URL=")
        return 0
    if not chat_mod.REVIEW_URL_RE.match(url):
        raise oneshot_mod.OneshotError(oneshot_mod.redact(
            _m("oneshot_bad_review_url", url=repr(url[:120]),
               tail=_m("oneshot_bad_url_wrote") if wrote else _m("oneshot_bad_url_not_wrote")), token))
    if wrote:
        _out(_m("oneshot_draft_updated"))
    else:
        _out(_m("oneshot_draft_unchanged"))
    _offer_review_after_writes(args, environ, {"preview": {"review_url": url}})
    _out("REVIEW_URL=%s" % url)
    return 0


def cmd_usage(args, environ):
    """一次模型呼叫 = 一筆收據(規格 v2 §13.1)。

    ★ 這是**獨立的子命令**,不掛在 `chat --ops` 上:模型呼叫發生在席位裡的 AI,不在這支 CLI;
      而且分開之後「用量失敗擋住草稿」在結構上就不可能發生(§13.5 第 1 條、U7 那把刀)。
    """
    record = _pick_record(args, environ)
    if not record:
        raise usage_mod.UsageError(_m("usage_unbound"))
    api = _api_for(record, args, environ)
    tenant, page = record.get("tenant_id"), record.get("page_id")
    if args.flush:
        sent, left = usage_mod.flush(api, tenant, page, environ, _out)
        _out(_m("usage_flushed", sent=sent, left=left))
        return 0
    explicit = {"binding_id": args.binding_id, "provider": args.provider,
                "funding": args.funding}
    binding = usage_mod.load_binding(environ, explicit=explicit)
    seat = _seat_addr(args, environ)
    key = chat_mod.operation_key(seat, seq=args.seq, tag="u")
    tools = []
    for raw in args.tool or []:
        kind, _, count = raw.partition(":")
        if not count.isdigit():
            raise usage_mod.UsageError(_m("usage_bad_tool", raw=repr(raw)))
        tools.append({"kind": kind, "count": int(count)})
    receipt = usage_mod.build_receipt(
        operation_key=key, call_id=args.call_id,
        started_at=args.started_at or datetime.now(timezone.utc),
        seat_label=args.seat_label or seat, purpose=args.purpose, binding=binding,
        model_id=args.model, tools=tools, linked_operation_key=args.linked_key,
        tokens={"input_uncached": args.input_uncached, "input_cached": args.input_cached,
                "output": args.output, "output_reasoning_included": args.output_reasoning})
    ok, detail = usage_mod.send(api, receipt, tenant, page, environ, _out)
    _out(_m("usage_receipt", state=_m("usage_sent") if ok else _m("usage_kept"), detail=detail or key))
    return 0


def cmd_intake(args, environ):
    """288:印客戶準備清單(不打網路);--missing 讀已綁的頁逐條印「缺:…」。"""
    if not args.missing:
        _out(intake_mod.checklist_text().rstrip("\n"))
        return 0
    record = _pick_record(args, environ)
    api = _api_for(record, args, environ)
    blocks = api.get_blocks()
    draft = api.get_draft()
    lines = intake_mod.missing_lines(blocks, draft, template_skill.BLOCK_REQUIRED_KEYS)
    if not lines:
        _out(_m("intake_complete"))
        return 0
    for line in lines:
        _out(line)
    # 289 (b) 第二層候選:社群快照(GET /draft 頂層)+ 轉接器快照(--source 明給的網址;--refetch 再加頁上取回顆的 provenance 網址)。
    #   沒帶 --source / --refetch ⇒ 零請求(--missing 原本就不打外網);抓不到的來源印一行不停手。
    profile = intake_mod.profile_of(blocks, draft)
    sources = list(args.source or [])
    known = intake_mod.provenance_urls(blocks, draft)
    if args.refetch:
        sources += [u for u in known if u not in sources]
    elif known:
        _out(_m("intake_known_sources", n=len(known), urls=", ".join(known)[:200]))
    adapter = []
    for url in sources:
        try:
            adapter.append((url, intake_sources.fetch_snapshot(url, explicit=True)))
        except intake_sources.SourceError as exc:
            _out(_m("intake_source_fail", url=url, error=exc))
    fields, socials_gap = intake_mod.missing_profile_fields(profile)
    cands = intake_mod.candidates(profile, intake_mod.social_snapshots_of(blocks, draft), adapter)
    for line in intake_mod.candidate_lines(cands, fields, socials_gap):
        _out(line)
    if not args.accept or not cands:
        return 0
    # 第三層答「要」:只寫缺欄(候選結構上只含缺欄),走 set_profile ⇒ PUT /profile ⇒ 草稿;零 publish
    chat_mod.assert_writable(record)
    ops = intake_mod.accept_ops(cands, profile)
    response = chat_mod.apply_skill_ops(api, record, ops, _seat_addr(args, environ), _out, template_id=None, seq=args.seq,
                             confirm=(lambda lines: True), open_browser=False)
    _offer_review_after_writes(args, environ, response)
    return 0


def cmd_import(args, environ):
    """289:`aib page import <url…>`——每條網址挑轉接器抓快照 → 只填缺欄 → 走 chat 那條草稿路 → 印預覽網址。
    沒有轉接器的網址在這裡就具名停手(一個請求都不發);多條網址按給的順序填缺(RL-089 取回順序由呼叫端排)。
    ★ 這條路上只有 set_profile / set_theme / upsert_block 三種 skill 操作,零 publish(Z2)。"""
    record = _pick_record(args, environ)
    chat_mod.assert_writable(record)          # 未綁時在這裡就停
    api = _api_for(record, args, environ)
    if not args.urls and not args.search:
        raise intake_sources.SourceError(_m("import_need_url"))
    if (args.city or args.domain) and not args.search:
        raise intake_sources.SourceError(_m("import_city_needs_search"))
    snapshots = []
    for url in args.urls:                     # 先把每條都抓齊、驗齊,再動草稿:第 3 條沒轉接器不該讓第 1 條已經寫了一半
        snapshots.append((url, intake_sources.fetch_snapshot(url, explicit=True)))   # 用戶親手給的網址 ⇒ 網站轉接器也認
    total = 0
    for url, snapshot in snapshots:
        state = _current_state(api)           # 每一輪重讀現值:上一輪填進去的欄,這一輪就是「已有」
        image_notes = _materialize(snapshot, api, args, environ)
        ops, notes = intake_sources.merge_into_ops(snapshot, state)
        _out(_m("import_source_ops", kind=snapshot["source"]["kind"], url=url, n=len(ops)))
        for note in image_notes:
            _out("  " + note)
        for note in notes:
            _out("  " + note)
        if not ops:
            _out(_m("import_source_nothing"))
            continue
        response = chat_mod.apply_skill_ops(api, record, ops, _seat_addr(args, environ), _out,
                                 template_id=args.template, seq=args.seq,
                                 confirm=(lambda lines: True) if args.yes else None, open_browser=False)
        _offer_review_after_writes(args, environ, response)
        total += len(ops)
    if args.search:
        total += _import_search(args, environ, record, api)
    if not total:
        _out(_m("import_all_nothing"))
    return 0


def _search_gaps(state):
    """289 (h) §一 1 的「仍有缺欄」:display_name 之外的個人檔案三欄(bio / status_text / socials)或零區塊。"""
    profile = intake_sources._current_profile(state)
    gaps = [key for key in ("bio", "status_text") if not intake_sources._filled(profile.get(key))]
    if not intake_sources._filled(profile.get("socials") or profile.get("social_links")):
        gaps.append("socials")
    if not intake_sources._current_blocks(state):
        gaps.append("blocks")
    return gaps


def _import_search(args, environ, record, api):
    """289 (h) 搜尋回退:給了網址的來源都走完之後才輪到(取回順序第七位)。
    只在頁上**仍有缺欄**時搜(H1:沒缺欄 ⇒ 印「沒有缺欄」零請求;display_name 空 ⇒ 具名停手零請求);候選印出來再抓(告知不是提問);
    每顆 provenance 多 via:"search"、profile 由搜尋帶入時補一顆隱藏文字顆;結尾印那句提醒(與 PAGE i18n 同字面)。回寫入的操作數。"""
    state = _current_state(api)
    gaps = _search_gaps(state)
    if not gaps:
        _out(_m("search_no_gaps"))
        return 0
    name = (intake_sources._current_profile(state).get("display_name") or "").strip()
    _out(_m("search_gaps", gaps=_m("list_sep").join(gaps), name=name or _m("search_empty_name")))
    found, _lines = intake_search.run_search(name, environ, city=args.city, domain=args.domain, out=_out)
    if not found:
        _out(_m("search_none"))
        return 0
    # 候選是機器挑的不是用戶給的:一個抓不到(lnk.bio 對機器人 403 是常態)只跳過那一個,其餘照寫(root2 複審 F1;用戶給網址的那條路仍是「第 3 條沒轉接器不該讓第 1 條寫一半」)。
    #   刀 K7(尺裡把 except 換成 raise):任一候選抓不到就整個中止 ⇒ H4'' 必紅
    snapshots = []
    for item in found:
        try:
            snapshots.append((item["url"], intake_sources.fetch_snapshot(item["url"], explicit=item.get("explicit", False))))
        except intake_sources.SourceError as exc:
            _out(_m("search_skip", url=item["url"], error=str(exc)[:200]))
    if not snapshots:
        _out(_m("search_all_failed"))
        return 0
    total = 0
    for url, snapshot in snapshots:
        ops, notes = intake_sources.merge_into_ops(snapshot, _current_state(api))
        ops, marks = intake_search.mark_search_ops(ops, snapshot)
        _out(_m("search_source_ops", kind=snapshot["source"]["kind"], url=url, n=len(ops)))
        for note in notes + marks:
            _out("  " + note)
        if not ops:
            _out(_m("import_source_nothing"))
            continue
        response = chat_mod.apply_skill_ops(api, record, ops, _seat_addr(args, environ), _out,
                                 template_id=args.template, seq=args.seq,
                                 confirm=(lambda lines: True) if args.yes else None, open_browser=False)
        _offer_review_after_writes(args, environ, response)
        total += len(ops)
    if total:
        _out(intake_search.notice(_locale()))
    return total


def cmd_refresh(args, environ):
    """289 / 292:`aib page refresh <source>`——頁上帶 provenance.source == <source> 的取回顆,回它們的來源網址重抓一次:
    同一顆換成最新內容(update 帶新 provenance ⇒ fetched_at 更新、過期解除),新出現的顆照種,個人檔案照舊只填缺欄。
    頁上沒有這個來源的顆 ⇒ 具名停手零請求;來源沒有網址(snapshot_file 手寫的)⇒ 具名停手。"""
    record = _pick_record(args, environ)
    chat_mod.assert_writable(record)
    api = _api_for(record, args, environ)
    state = _current_state(api)
    urls = []
    for block in intake_sources._current_blocks(state):
        prov = block.get("provenance") if isinstance(block, dict) else None
        if isinstance(prov, dict) and prov.get("source") == args.source and prov.get("url") not in urls:
            urls.append(prov.get("url"))
    if not urls:
        raise intake_sources.SourceError(
            _m("refresh_no_source", source=args.source,
               have=", ".join(sorted({b["provenance"]["source"] for b in intake_sources._current_blocks(state)
                                      if isinstance(b, dict) and isinstance(b.get("provenance"), dict)})) or _m("refresh_none")))
    if any(not isinstance(u, str) or not u for u in urls):
        raise intake_sources.SourceError(_m("refresh_no_url", source=args.source))
    snapshots = [(url, intake_sources.fetch_snapshot(url, explicit=True)) for url in urls]      # 先全抓齊再動草稿(來源網址是用戶當初給的)
    total = 0
    for url, snapshot in snapshots:
        image_notes = _materialize(snapshot, api, args, environ)
        ops, notes = intake_sources.refresh_into_ops(snapshot, _current_state(api), args.source)
        for note in image_notes:
            _out("  " + note)
        _out(_m("refresh_ops", source=args.source, url=url, n=len(ops)))
        for note in notes:
            _out("  " + note)
        if not ops:
            continue
        response = chat_mod.apply_skill_ops(api, record, ops, _seat_addr(args, environ), _out,
                                 template_id=args.template, seq=args.seq,
                                 confirm=(lambda lines: True) if args.yes else None, open_browser=False)
        _offer_review_after_writes(args, environ, response)
        total += len(ops)
    if not total:
        _out(_m("refresh_nothing_new", source=args.source))
    return 0


def _materialize(snapshot, api, args, environ):
    """289 (d):快照裡帶 image_urls 的顆(shop / image)先走 288 上傳拿 asset_id,再合併(合併器只認 payload)。"""
    tmp_root = os.path.join(store.state_root(environ)[0], "tmp")
    os.makedirs(tmp_root, exist_ok=True)
    return intake_images.materialize_images(snapshot, api, _seat_addr(args, environ), _out, environ=environ, tmp_root=tmp_root)


def _current_state(api):
    """現值 = 草稿;沒草稿 ⇒ 線上區塊(GET /blocks)。import 與 refresh 同一條規則。
    289 〇 F2(root2 複審):真的 GET /draft 沒草稿時是 draft:null 且沒有頂層 blocks ⇒ 現值 = 線上,要另讀 GET /blocks
      (fixtures/page-api/get-draft-none.json 錄自真站);不讀就會把已發布的區塊再種一次。刀 = 拿掉這段 ⇒ Z7 紅"""
    state = api.get_draft()
    if not state.get("has_draft"):
        live = api.get_blocks()
        state = dict(state, blocks=live.get("blocks") or [])
    return state


def cmd_upload(args, environ):
    """288:本地縮圖 → webp → POST /assets;一檔一行,一檔失敗不影響其他檔(rc 1)。"""
    record = _pick_record(args, environ)
    api = _api_for(record, args, environ)
    seat = _seat_addr(args, environ)
    tmp_root = os.path.join(store.state_root(environ)[0], "tmp")   # 縮圖落 <狀態根>/tmp/,送完刪
    os.makedirs(tmp_root, exist_ok=True)
    return upload_mod.run_upload(api, args.paths, _out, seat, alt=args.alt or "", environ=environ, tmp_root=tmp_root)


def cmd_list(args, environ):
    records = store.list_tokens(environ)
    if not records:
        _out(_m("list_empty"))
        return 0
    for record in records:
        _out(_m("list_row", tenant=record.get("tenant_id"), page=record.get("page_id"),
                slug=_m("list_slug", slug=record["slug"]) if record.get("slug") else "",
                expires=record.get("expires_at", "?")))
    return 0


def cmd_status(args, environ):
    record = _pick_record(args, environ)
    if not record:
        _out(_m("status_unbound"))
        return 0
    try:
        state = _api_for(record, args, environ).get_draft()
    except ApiError as exc:
        _out(_m("status_unverified"))
        raise chat_mod.ChatError(chat_mod.write_error_text(exc))
    _out(_m("status_ok", page=record.get("slug") or record.get("page_id")))
    enabled = state.get("aib_write_enabled")
    if enabled is False:
        since = chat_mod._taipei_time(state.get("aib_write_disabled_at"), "%Y-%m-%d %H:%M")
        _out(_m("status_write_off", since=since or _m("status_since_unknown")))
    else:
        _out(_m("status_write", state=_m("status_write_on") if enabled is True else _m("status_write_unknown")))
    chat_mod.announce_quota(state, _out)
    return 0


def cmd_stats(args, environ):
    """317 第 16 項:印這一頁的基本統計(瀏覽 / 點擊;今天、近 7 天、近 30 天)。數的是「次」不是「人」(伺服器零 cookie 零腳本不存 IP)。
    區塊標籤要對線上顆:讀一次 /blocks 拿 block_id → 型別 / 字(唯讀,不扣配額)。"""
    record = _pick_record(args, environ)
    if not record:
        raise chat_mod.ChatError(_m("stats_unbound"))
    api = _api_for(record, args, environ)
    try:
        answer = api.get_stats()
    except ApiError as exc:
        if exc.code == "page_api_not_found":
            raise chat_mod.ChatError(_m("stats_not_enabled"))
        raise chat_mod.ChatError(_m("stats_read_fail", error=chat_mod.write_error_text(exc)))
    stats = answer.get("stats") if isinstance(answer, dict) else None
    if not isinstance(stats, dict):
        raise chat_mod.ChatError(_m("stats_bad_shape"))
    labels = {}
    try:
        for block in (api.get_blocks().get("blocks") or []):
            payload = block.get("payload") or {}
            labels[block.get("block_id")] = "%s · %s" % (block.get("block_type"), str(payload.get("label") or payload.get("title") or payload.get("name") or payload.get("text") or "")[:40])
    except ApiError:
        pass
    _out(_m("stats_head", page=record.get("slug") or record.get("page_id"), days=stats.get("retention_days", "?")))
    for key, name in (("today", _m("stats_today")), ("last_7", _m("stats_last_7")), ("last_30", _m("stats_last_30"))):
        row = stats.get(key) or {}
        clicks = sum(sum(slots.values()) for slots in (row.get("clicks") or {}).values() if isinstance(slots, dict))
        _out(_m("stats_row", name=name, views=row.get("views", 0), clicks=clicks))
    per_block = (stats.get("last_30") or {}).get("clicks") or {}
    if per_block:
        _out(_m("stats_per_block"))
        for block_id, slots in per_block.items():
            parts = _m("list_sep").join("%s %d" % (_m("stats_slot_" + slot) if slot in ("link", "nav", "review", "social") else slot, n)
                                        for slot, n in (slots or {}).items())
            _out(_m("stats_block_row", label=labels.get(block_id, block_id), parts=parts))
    else:
        _out(_m("stats_no_clicks"))
    return 0


def _h(key, **params):
    """說明文字走四語鍵 aib.page.help.*(語系盤查:英文介面不准整段中文);讀不到印鍵名,不讓 --help 崩。"""
    try:
        import i18n_lib
        return i18n_lib.t("aib.page.help." + key, params or None, locale=i18n_lib.resolve_locale()).replace("%", "%%")
    except Exception:  # noqa: BLE001
        return key


def build_parser():
    parser = argparse.ArgumentParser(prog="aib page", description=_h("description"))
    parser.add_argument("--base", help=_h("base", env=BASE_ENV))
    parser.add_argument("--seat", help=_h("seat", env=SEAT_ENV))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_bind = sub.add_parser("bind", help=_h("bind"), description=_h("bind"))
    p_bind.add_argument("--seat-label", help=_h("seat_label"))
    p_bind.add_argument("--role", default="", help=_h("role"))
    mode = p_bind.add_mutually_exclusive_group()
    mode.add_argument("--serial", help=_h("serial"))
    mode.add_argument("--pair", action="store_true", help=_h("pair"))
    p_bind.set_defaults(func=cmd_bind)

    p_unbind = sub.add_parser("unbind", help=_h("unbind"), description=_h("unbind"))
    p_unbind.add_argument("--tenant", help=_h("tenant"))
    p_unbind.add_argument("--page", help=_h("page"))
    p_unbind.add_argument("--local-only", action="store_true", help=_h("unbind_local_only"))
    p_unbind.add_argument("--json", action="store_true")
    p_unbind.set_defaults(func=cmd_unbind)

    p_chat = sub.add_parser("chat", help=_h("chat"), description=_h("chat"))
    p_chat.add_argument("--tenant", help=_h("tenant"))
    p_chat.add_argument("--page", help=_h("page"))
    p_chat.add_argument("--ops", help=_h("ops"))
    p_chat.add_argument("--autofill", metavar=_h("autofill_metavar"), help=_h("autofill"))
    p_chat.add_argument("--template", help=_h("template"))
    p_chat.add_argument("--yes", action="store_true", help=_h("chat_yes"))
    p_chat.add_argument("--expected-revision", dest="expected_revision", help=_h("expected_revision"))
    p_chat.add_argument("--seq", type=int, default=1, help=_h("seq"))
    p_chat.add_argument("--no-open", dest="no_open", action="store_true", help=_h("chat_no_open"))
    p_chat.add_argument("--open", dest="open_after", action="store_true", help=_h("chat_open"))
    p_chat.add_argument("--public-url", help=_h("public_url"))
    p_chat.set_defaults(func=cmd_chat)

    p_one = sub.add_parser("oneshot", help=_h("oneshot"), description=_h("oneshot_description"))
    p_one.add_argument("intent", nargs="?", help=_h("intent"))
    p_one.add_argument("--checklist", action="store_true", help=_h("checklist"))
    p_one.add_argument("--lang", choices=("zh", "en"), default="zh", help=_h("lang"))
    p_one.add_argument("--dry-run", dest="dry_run", action="store_true", help=_h("dry_run"))
    p_one.add_argument("--confirm-clear", dest="confirm_clear", action="store_true", help=_h("confirm_clear"))
    p_one.add_argument("--json", action="store_true", help=_h("json"))
    p_one.add_argument("--seq", type=int, default=1, help=_h("seq"))
    p_one.add_argument("--no-open", dest="no_open", action="store_true", help=_h("no_open"))
    p_one.add_argument("--open", dest="open_after", action="store_true", help=_h("open"))
    p_one.add_argument("--tenant", help=_h("tenant"))
    p_one.add_argument("--page", help=_h("page"))
    p_one.set_defaults(func=cmd_oneshot)

    p_usage = sub.add_parser("usage", help=_h("usage"), description=_h("usage"))
    p_usage.add_argument("--tenant", help=_h("tenant"))
    p_usage.add_argument("--page", help=_h("page"))
    p_usage.add_argument("--flush", action="store_true", help=_h("flush"))
    p_usage.add_argument("--call-id", dest="call_id", help=_h("call_id"))
    p_usage.add_argument("--model", help=_h("model"))
    p_usage.add_argument("--purpose", default="page_chat", choices=list(usage_mod.PURPOSES), help=_h("purpose"))
    p_usage.add_argument("--started-at", dest="started_at", help=_h("started_at"))
    p_usage.add_argument("--seat-label", dest="seat_label", help=_h("seat_label"))
    p_usage.add_argument("--linked-key", dest="linked_key", help=_h("linked_key"))
    p_usage.add_argument("--seq", type=int, default=1, help=_h("seq"))
    p_usage.add_argument("--binding-id", dest="binding_id")
    p_usage.add_argument("--provider")
    p_usage.add_argument("--funding", choices=list(usage_mod.FUNDINGS), help=_h("funding"))
    p_usage.add_argument("--tool", action="append", metavar=_h("tool_metavar"), help=_h("tool"))
    for name in ("input-uncached", "input-cached", "output", "output-reasoning"):
        p_usage.add_argument("--" + name, dest=name.replace("-", "_"), type=int, default=0, help=_h("tokens"))
    p_usage.set_defaults(func=cmd_usage)

    p_intake = sub.add_parser("intake", help=_h("intake"), description=_h("intake"))
    p_intake.add_argument("--missing", action="store_true", help=_h("missing"))
    p_intake.add_argument("--source", action="append", metavar=_h("source_metavar"), help=_h("source"))
    p_intake.add_argument("--refetch", action="store_true", help=_h("refetch"))
    p_intake.add_argument("--accept", action="store_true", help=_h("accept"))
    p_intake.add_argument("--seq", type=int, default=1, help=_h("seq"))
    p_intake.add_argument("--no-open", dest="no_open", action="store_true", help=_h("accept_no_open"))
    p_intake.add_argument("--open", dest="open_after", action="store_true", help=_h("accept_open"))
    p_intake.add_argument("--tenant", help=_h("tenant"))
    p_intake.add_argument("--page", help=_h("page"))
    p_intake.set_defaults(func=cmd_intake)

    p_upload = sub.add_parser("upload", help=_h("upload"), description=_h("upload"))
    p_upload.add_argument("paths", nargs="+", help=_h("paths"))
    p_upload.add_argument("--alt", default="", help=_h("alt"))
    p_upload.add_argument("--tenant", help=_h("tenant"))
    p_upload.add_argument("--page", help=_h("page"))
    p_upload.set_defaults(func=cmd_upload)

    p_import = sub.add_parser("import", help=_h("import"), description=_h("import"))
    p_import.add_argument("urls", nargs="*", metavar="url", help=_h("urls"))
    p_import.add_argument("--search", action="store_true", help=_h("search", max=intake_search.SEARCH_DAILY_MAX))
    p_import.add_argument("--city", help=_h("city"))
    p_import.add_argument("--domain", help=_h("domain"))
    p_import.add_argument("--template", help=_h("template"))
    p_import.add_argument("--yes", action="store_true", help=_h("profile_yes"))
    p_import.add_argument("--seq", type=int, default=1, help=_h("seq"))
    p_import.add_argument("--no-open", dest="no_open", action="store_true", help=_h("no_open"))
    p_import.add_argument("--open", dest="open_after", action="store_true", help=_h("open"))
    p_import.add_argument("--tenant", help=_h("tenant"))
    p_import.add_argument("--page", help=_h("page"))
    p_import.set_defaults(func=cmd_import)

    p_refresh = sub.add_parser("refresh", help=_h("refresh"), description=_h("refresh"))
    p_refresh.add_argument("source", help=_h("source_name"))
    p_refresh.add_argument("--template", help=_h("template"))
    p_refresh.add_argument("--yes", action="store_true", help=_h("profile_yes"))
    p_refresh.add_argument("--seq", type=int, default=1, help=_h("seq"))
    p_refresh.add_argument("--no-open", dest="no_open", action="store_true", help=_h("no_open"))
    p_refresh.add_argument("--open", dest="open_after", action="store_true", help=_h("open"))
    p_refresh.add_argument("--tenant", help=_h("tenant"))
    p_refresh.add_argument("--page", help=_h("page"))
    p_refresh.set_defaults(func=cmd_refresh)

    p_list = sub.add_parser("list", help=_h("list"), description=_h("list"))
    p_list.set_defaults(func=cmd_list)
    p_status = sub.add_parser("status", help=_h("status"), description=_h("status"))
    p_status.add_argument("--tenant", help=_h("tenant"))
    p_status.add_argument("--page", help=_h("page"))
    p_status.set_defaults(func=cmd_status)

    p_stats = sub.add_parser("stats", help=_h("stats"), description=_h("stats"))
    p_stats.add_argument("--tenant", help=_h("tenant"))
    p_stats.add_argument("--page", help=_h("page"))
    p_stats.set_defaults(func=cmd_stats)
    return parser


def main(argv=None, environ=None):
    environ = os.environ if environ is None else environ
    args = build_parser().parse_args(argv)
    try:
        return args.func(args, environ)
    except (chat_mod.ChatError, bind_mod.BindError, store.StoreError, intake_mod.IntakeError, upload_mod.UploadError,
            intake_sources.SourceError, oneshot_mod.OneshotError) as exc:
        print("aib page: %s" % oneshot_mod.redact(exc), file=sys.stderr)   # 任何一路的錯誤句都不帶憑證形狀出去
        return 1
    except TransportError as exc:
        print("aib page: %s" % exc, file=sys.stderr)
        return 1
    except ApiError as exc:
        # 走到這裡代表某條路忘了翻成人話 —— 印出原碼,不要假裝看得懂
        print("aib page: " + _m("api_untranslated", code=exc.code or "?", status=exc.http_status), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
