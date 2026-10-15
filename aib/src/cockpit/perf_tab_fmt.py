#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""效能優化頁三欄渲染器(the maintainer 2026-08-17 直令:整齊/簡潔)。

stdin=`idle_reaper scan --json`;stdout=只有「名稱 | 閒置 | [X]/狀態詞」
三欄+頂部全關建議。判決/艙齡/自動/細節一概不上畫面——資料層不動,
只收顯示。[X] 目前是顯示(可關標記);點擊交互=dev2 效能面板單(rowmap)。

狀態詞對映(兩三字,不印長原因):
    auto==True                → [X](reaper policy 安全集,唯一判準來源)
    human_attached            → 使用中
    verdict==unknown          → 量不到
    其餘(含 cli_never_auto)  → 手動
閒置:inst=活席最小 idle_s(全量不到→?;無席=死艙→用艙齡);cli=idle_s。
量不到畫 ? 不畫 0m(機況清理席判準:量不到不等於剛動過)。

--spans(dev2 效能面板第一片,the maintainer 08-18 核准):同時把每行可點座標寫成
JSON 落 $EZ2_COCKPIT_STATE_DIR/perf-spans.json(或 --spans 後帶路徑)。
座標從**實際印出的那行字串**量([X] 的 index),不是自己再算一份版面——
格式改了座標自動跟著改,測試靠這一點釘「畫面與落點同源」(與右欄
rowmap 同紀律)。只有 mark==[X] 的列才有 x_span;聚合列帶安全集 id 清單
(全關走 aib idle reap,id 供顯示/稽核,不迴圈 close)。座標 0-based、
end 不含;line 0-based=本輸出行號(pane 端 head/cut 裁切自己另計偏移)。
"""
import json
import os
import sys

from cockpit_lib import cjk_w   # 顯示寬度=cell 數;與全艙版面同一把尺

_TR = {}


def _t(key, params=None):
    """畫面字走 i18n(10-15 多語第二批);一個語系的字典每個行程只載一次。[X] 不進字典(點擊落點靠它量)。"""
    try:
        import i18n_lib
        loc = i18n_lib.resolve_locale()
        tr = _TR.get(loc) or _TR.setdefault(loc, i18n_lib.Translator(locale=loc))
        return tr.t(key, params)
    except Exception:   # 字典讀不到也要畫得出來
        return key


def dur(sec):
    if sec is None:
        return '?'
    sec = int(sec)
    if sec >= 86400:
        return '%.1fd' % (sec / 86400.0)
    if sec >= 3600:
        return '%.1fh' % (sec / 3600.0)
    return '%dm' % (sec // 60)


X_MARK = '[X]'   # qa X2 釘子的 ADAPTER 取用;單一 token,勿改字面


def rows_from(targets, self_instance=None):
    """回 (rows, safe):rows=每列 dict(顯示三欄+id/kind,id 供 --spans 落
    盤給點擊層跑 `aib idle close <id> --yes` 用);safe=安全集 (name, id)。
    顯示輸出與改版前逐字元相同(name/idle/mark 三欄),只是多帶身份。"""
    rows, safe = [], []
    for t in targets:
        tid = t.get('id', '?')
        is_self = (t.get('kind') == 'inst' and self_instance is not None
                   and str(t.get('instance')) == str(self_instance))
        if t.get('kind') == 'inst':
            name = t.get('outer') or tid
            idles = [s.get('idle_s') for s in t.get('seats', [])]
            known = [i for i in idles if i is not None]
            idle = min(known) if known else (t.get('age_s') if not idles else None)
        else:
            prog = (t.get('args') or '').split(' ', 1)[0].rsplit('/', 1)[-1]
            name = ((t.get('tty') or '') + ' ' + prog).strip() or tid
            idle = t.get('idle_s')
        if is_self:
            # 紅線:自己(發起點擊的席)永不列關——就算 scan 給了 auto=True
            # (防守縱深:正常不會,但畫面層不賭資料層)也不亮 [X]。
            mark = _t('aib.perf_tab.mark_self')
        elif t.get('auto'):
            mark = X_MARK
            safe.append((name, tid))
        elif str(t.get('human_attached')) == 'True':
            mark = _t('aib.perf_tab.mark_in_use')
        elif t.get('verdict') == 'unknown':
            mark = _t('aib.perf_tab.mark_unmeasured')
        else:
            mark = _t('aib.perf_tab.mark_manual')
        rows.append({'name': name[:22], 'idle': dur(idle), 'mark': mark,
                     'id': tid, 'kind': t.get('kind')})
    return rows, safe


def render(targets, self_instance=None):
    """回 (lines, spans)。座標一律從組好的字串 rindex('[X]') 量——
    「畫面與落點同源」:任何版面改動,span 跟著實字串走,不會留下
    畫在舊座標上的幽靈點擊區。"""
    rows, safe = rows_from(targets, self_instance=self_instance)
    lines, spans = [], []

    def push(text, meta):
        idx = len(lines)
        lines.append(text)
        if meta is not None:
            # x_span 用**終端 cell 座標**(cjk_w),不是字串 index——聚合列
            # 帶 CJK,index 座標會把 [X] 定在畫面左邊好幾格的地方,點擊層
            # 對不上。同一把尺=cockpit_lib.cjk_w(右欄 rowmap 同源)。
            start = cjk_w(text[:text.rindex('[X]')])
            meta.update({'line': idx, 'x_span': [start, start + 3]})
            spans.append(meta)

    if safe:
        push(' %s [X]' % _t('aib.perf_tab.safe_all', {'n': len(safe), 'names': ','.join(n for n, _ in safe)[:40]}),
             {'kind': 'agg', 'ids': [i for _, i in safe]})
    else:
        push(' ' + _t('aib.perf_tab.none_safe'), None)
    width = max([len(r['name']) for r in rows] + [6])
    for r in rows:
        text = ' %-*s  %6s  %s' % (width, r['name'], r['idle'], r['mark'])
        push(text, ({'kind': r['kind'], 'id': r['id'], 'name': r['name']}
                    if r['mark'] == '[X]' else None))
    return lines, spans


def render_perf_page(targets, policy, self_instance=None):
    """qa X2 釘子治具入口:raw targets(未帶 auto)逐列**現場呼叫**
    idle_reaper.auto_decision 本尊(屬性查找在呼叫時發生,mock 得到)。
    產線路徑由 scan --json 預算同一函式——兩路只有一份判準,這裡不是
    第二份,是同一份的另一個進料口。回整頁文字(與產線逐字元同格式)。"""
    import idle_reaper
    enriched = []
    for t in targets:
        t2 = dict(t)
        t2['auto'], t2['auto_reason'] = idle_reaper.auto_decision(t, policy)
        enriched.append(t2)
    if self_instance is None:
        self_instance = os.environ.get('EZ2_AIB_INSTANCE')
    lines, _spans = render(enriched, self_instance=self_instance)
    return '\n'.join(lines)


def spans_path(argv):
    """--spans [路徑]:旗標不在→None(不落盤);帶路徑用之,否則
    $EZ2_COCKPIT_STATE_DIR/perf-spans.json;旗標在但兩者皆無=用法錯。"""
    if '--spans' not in argv:
        return None
    i = argv.index('--spans')
    if i + 1 < len(argv) and not argv[i + 1].startswith('-'):
        return argv[i + 1]
    state = os.environ.get('EZ2_COCKPIT_STATE_DIR')
    if not state:
        # SystemExit(字串) 的 rc 是 1;用法錯要 rc=2(與 aib-dm 契約同族)
        print('perf_tab_fmt: ' + _t('aib.perf_tab.spans_need_path'), file=sys.stderr)
        raise SystemExit(2)
    return os.path.join(state, 'perf-spans.json')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    out = spans_path(argv)
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = []
    targets = data if isinstance(data, list) else data.get('targets', [])
    lines, spans = render(targets,
                          self_instance=os.environ.get('EZ2_AIB_INSTANCE'))
    for ln in lines:
        print(ln)
    if out is not None:
        # tmp+rename:pane 端每幀讀這檔,不能讓它讀到半寫(cache 同紀律)
        tmp = out + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump({'schema': 'aib-perf-spans/v1', 'lines': lines,
                       'spans': spans}, fh, ensure_ascii=False)
        os.replace(tmp, out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
