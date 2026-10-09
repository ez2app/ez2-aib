#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""派工單渲染器 —— 派工單文字一律從 manifest 產生,不手抄。

## 它擋什麼

手抄會把 manifest 的錯**複製成第二個受害者**,而兩份一致**看起來像互相佐證**
(實例:大綱 §〇 的座標永遠差自己那一顆,root2 manifest 逐字抄成第二份)。

## 用法

  render                       印出派工單 markdown
  render --out <path>          寫檔
  render --check <path>        比對該檔是否等於現在渲染的結果;不同 -> rc=1

★ `--check` 是這支工具唯一有承重的部分:沒有它,「從 manifest 產生」與
  「手抄了一份剛好很像的」在版控上逐字相同。
"""
import pathlib
import sys
import os
import argparse, io, json, sys

# ★ 字串一律走既有的 i18n 正本(i18n/{zh-TW,en,ja,ko}.json + src/cockpit/i18n_lib.py)。
#   不在本檔內建第二套字串表 —— 大綱 §610 十三條地基逐字列著「i18n 是地基」。
# ★★ 順序是承重的(33f17 抓到):連續兩次 insert(0) 會讓後 insert 的排前面 ⇒ 主庫贏本樹,
#   而 i18n_lib 的字典根綁 __file__ ⇒ 會讀回主庫舊字典。本樹 insert(0)、fallback append。
_here = pathlib.Path(__file__).resolve().parents[2] / "src" / "cockpit"
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
_fallback = pathlib.Path.home() / "ez2-aib" / "src" / "cockpit"
if str(_fallback) not in sys.path:
    sys.path.append(str(_fallback))
import i18n_lib  # noqa: E402

LOC = os.environ.get("EZ2_AIB_LOCALE", "zh-TW")


def T(key, params=None):
    """唯一的字串入口 —— locale 只在這裡決定一次。"""
    return i18n_lib.t(key, params, locale=LOC)


TOP_KEYS = {"project", "title", "owner", "criteria_queue", "source_of_truth",
            "redlines", "receipt_required_fields", "packages",
            "enum_ruling", "rejudge_points", "named_items", "g2_status"}
PKG_KEYS = {"order", "id", "blade", "seat", "code", "branch", "title", "goal",
            "mount_points", "writes", "acceptance", "stop_lines",
            "prerequisite_step", "blast_radius", "idempotence_definition",
            "signoff", "five_steps", "retracted_arm", "criteria",
            "report_split", "write_set_two_columns", "install_timing"}
# ★ 巢狀也要守:頂層有 assert_known,而巢狀沒有 —— 在此之前,往 install_timing /
#   g2_status 裡加一個渲染器不認得的鍵,它會被**靜默丟掉**,而 --check 照樣綠
#   (兩邊都沒有它)。這是「未知欄位守衛」只裝了一層的現場。
INSTALL_KEYS = {"by", "ruling", "why", "reinforced_by", "who_installs", "installed"}
GS_KEYS = {"as_of", "blade2", "blade3", "blade1", "named_item", "o5", "★",
           "ruling_upheld"}


SRC_KEYS = {"path", "ref", "note", "pinned"}


def assert_known(m):
    """manifest 長出渲染器不認得的欄位 -> 停手。

    ★ 沒有這一格,新增一個欄位會**靜默不印**,而派工單看起來仍然完整
      —— 漏印的方向不會有紅燈。
    """
    unknown = sorted(set(m) - TOP_KEYS)
    if unknown:
        raise SystemExit("✖ manifest 有渲染器不認得的頂層欄位:%s" % unknown)
    for p in m["packages"]:
        u = sorted(set(p) - PKG_KEYS)
        if u:
            raise SystemExit("✖ 包 %s 有渲染器不認得的欄位:%s" % (p.get("id"), u))
    for src in m["source_of_truth"]:
        u = sorted(set(src) - SRC_KEYS)
        if u:
            raise SystemExit("✖ 正本項 %s 有渲染器不認得的欄位:%s" % (src.get("path"), u))


def render(m):
    assert_known(m)
    o = []
    w = o.append
    w(u"# 派工單 %s:%s" % (m["project"], m["title"]))
    w(u"")
    w(u"> ★ **本檔由 `tools/dispatch/render-dispatch.py` 從 `docs/dispatch/MANIFEST-0828-G2.json` 產生,不手改。**")
    w(u"> 改內容請改 manifest 再重跑;複驗:`python3 tools/dispatch/render-dispatch.py --check <本檔>` -> `rc=0`。")
    w(u"")
    w(u"- **主管**:`%s@%s`" % (m["owner"]["role"], m["owner"]["code"]))
    w(u"- **判準隊列**:`%s@%s` —— %s" % (m["criteria_queue"]["role"], m["criteria_queue"]["code"], m["criteria_queue"]["note"]))
    w(u"- **執行序**:" + u" → ".join(u"%s(%s)" % (p["blade"], p["seat"]) for p in sorted(m["packages"], key=lambda p: p["order"])))
    w(u"")
    w(u"## 正本(座標一律 `ls-remote` 現查,不抄本檔)")
    w(u"")
    for s in m["source_of_truth"]:
        w(u"- `%s` @ `%s` —— %s" % (s["path"], s["ref"], s["note"]))
        # ★ 雙座標:釘讀的那顆寫值,tip 只寫取法 —— tip 的值寫下即腐爛,
        #   而一個過期的 tip 比沒有 tip 更糟(它看起來像現況)。
        w(u"  - **釘讀** = `%s`;**現查 tip 取法**:`git ls-remote origin %s`"
          % (s.get("pinned", "(未釘)"), s["ref"]))
    w(u"")
    w(u"## 子 AI 三紅線(艦長定,the maintainer 大方向)")
    w(u"")
    for i, r in enumerate(m["redlines"], 1):
        w(u"%d. %s" % (i, r))
    w(u"")
    w(u"## 交件收據必附欄(缺欄 = 交件不完整)")
    w(u"")
    for f in m["receipt_required_fields"]:
        w(u"- %s" % f)
    w(u"")
    for p in sorted(m["packages"], key=lambda p: p["order"]):
        w(u"---")
        w(u"")
        w(u"## %s %s —— `%s@%s`" % (p["blade"], p["title"], p["seat"], p["code"]))
        w(u"")
        w(u"- **目標**:%s" % p["goal"])
        w(u"- **分支**:`%s`(一交付一分支;基底 `origin/main` 現查)" % p["branch"])
        w(u"- **掛載點(逐個已驗存在於 `origin/main`,除註明者外)**:")
        for mp in p["mount_points"]:
            w(u"  - `%s`" % mp)
        w(u"- **寫入集(申報三閘;交件時 `git diff --name-only <base>..<leaf>` 逐行落在此)**:")
        for x in p["writes"]:
            w(u"  - `%s`" % x)
        fs = p.get("five_steps")
        if fs:
            w(u"- **五步序(%s)**:`%s`" % (fs["by"], fs["steps"]))
            w(u"  - %s" % fs["★"])
            w(u"  - **半徑**:%s" % fs["radius"])
        if p.get("criteria"):
            w(u"- **判準**:`%s`" % p["criteria"])
        rs = p.get("report_split")
        if rs:
            w(u"- **分段報法(%s)**:" % rs["by"])
            w(u"  - **交件段(現在判得完)**:%s" % rs["now"])
            w(u"  - **安裝段(候「裝」那一句)**:%s" % rs["later"])
            w(u"  - %s" % rs["★"])
        ra = p.get("retracted_arm")
        if ra:
            w(u"- **已撤回的臂**:~~%s~~" % ra["was"])
            w(u"  - **撤回理由**:%s" % ra["why_retracted"])
            w(u"  - **改用**:%s" % ra["replaced_by"])
            if ra.get("also_retracted"):
                w(u"  - %s" % ra["also_retracted"])
        pre = p.get("prerequisite_step")
        if pre:
            w(u"- **前置步 `%s`(已核准:%s)**:" % (pre["id"], pre["approved_by"]))
            w(u"  - **做什麼**:%s" % pre["what"])
            w(u"  - **為什麼**:%s" % pre["why"])
            w(u"  - **臂**:")
            for x in pre["arms"]:
                w(u"    - %s" % x)
        it = p.get("install_timing")
        if it:
            unk = sorted(set(it) - INSTALL_KEYS)
            if unk:
                raise SystemExit("✖ install_timing 有渲染器不認得的鍵:%s" % unk)
            w(u"- **安裝時點(%s)**:**%s**" % (it["by"], it["ruling"]))
            w(u"  - **理由**:%s" % it["why"])
            w(u"  - %s" % it["reinforced_by"])
            w(u"  - **由誰裝**:%s" % it["who_installs"])
            ins = it.get("installed")
            if ins:
                w(u"  - **✅ 已掛載(%s)**:%s" % (ins["when"], ins["by"]))
                for step in ins["five_steps"]:
                    w(u"    - %s" % step)
                w(u"    - **回退**:%s" % ins["rollback"])
                w(u"    - %s" % ins["honest_note"])
        w2 = p.get("write_set_two_columns")
        if w2:
            w(u"- **寫入集(兩欄)**:產品面 %s / 交件紀錄面 %s / 合計 %s"
              % (w2["product"], w2["receipt"], w2["total"]))
            w(u"  - %s" % w2["★"])
        sg = p.get("signoff")
        if sg:
            w(u"- **簽收(%s)**:**%s**" % (sg["by"], sg["verdict"]))
            w(u"  - **葉**:%s" % sg["leaf"])
            w(u"  - **判準**:%s" % sg["criteria"])
            w(u"  - **複跑**:%s" % sg["runs"])
            w(u"  - **改判**:%s" % sg["N_rejudge"])
            w(u"  - **未結**:%s" % sg["open"])
        idm = p.get("idempotence_definition")
        if idm:
            w(u"- **冪等定義(%s)**:" % idm["ruled_by"])
            w(u"  - **主臂**:%s" % idm["main_arm"])
            w(u"  - **上限臂**:%s" % idm["upper_arm"])
            w(u"  - **下限臂 `H-var`**:%s" % idm["lower_arm_H_var"])
            w(u"  - **必紅臂(三支)**:")
            for x in idm["must_red"]:
                w(u"    - %s" % x)
            w(u"  - **`H-fix`**:%s" % idm["H_fix"])
        if p.get("blast_radius"):
            w(u"- **爆炸半徑(enum 裁定的下游,不是本刀的)**:")
            for x in p["blast_radius"]:
                w(u"  - `%s`" % x)
        w(u"- **驗收臂**:")
        for a in p["acceptance"]:
            w(u"  - %s" % a)
        w(u"- **停止線**:")
        for s in p["stop_lines"]:
            w(u"  - %s" % s)
        w(u"")
    gs = m.get("g2_status")
    if gs:
        unk = sorted(set(gs) - GS_KEYS)
        if unk:
            raise SystemExit("✖ g2_status 有渲染器不認得的鍵:%s" % unk)
        w(u"---")
        w(u"")
        w(u"## G2 現況(%s)" % gs["as_of"])
        w(u"")
        for k, lab in (("blade2", "刀2"), ("blade3", "刀3"), ("blade1", "刀1"),
                       ("named_item", "具名件"), ("o5", "O5")):
            if k in gs:
                w(u"- **%s**:%s" % (lab, gs[k]))
        w(u"- %s" % gs["★"])
        if gs.get("ruling_upheld"):
            w(u"- %s" % gs["ruling_upheld"])
        w(u"")
    er = m.get("enum_ruling")
    if er:
        w(u"---")
        w(u"")
        w(u"## 狀態符 enum 收斂裁定(%s)" % er["by"])
        w(u"")
        for x in er["map"]:
            w(u"- `%s`" % x)
        w(u"")
        w(u"- **unknown**:%s" % er["unknown"])
        w(u"- **規格更正**:%s" % er["spec_correction"])
        w(u"- **載體**:%s" % er["carrier"])
        w(u"")
    for n in m.get("named_items", []):
        w(u"---")
        w(u"")
        w(u"## 具名件 `%s` —— %s" % (n["id"], n["title"]))
        w(u"")
        w(u"- **提出**:%s" % n["raised_by"])
        w(u"- **承接席**:%s" % n["seat"])
        w(u"- **寫入集**:%s" % u"、".join(u"`%s`" % x for x in n["writes"]))
        w(u"- **已量到的事實**:")
        for x in n["facts"]:
            w(u"  - %s" % x)
        w(u"- **臂**:")
        for x in n["arms"]:
            w(u"  - %s" % x)
        w(u"- %s" % n["note"])
        w(u"")
    for r in m.get("rejudge_points", []):
        w(u"---")
        w(u"")
        w(u"## 重判點 `%s`" % r["id"])
        w(u"")
        w(u"- **已裁**:%s" % r["ruling"])
        w(u"- **重判時機**:%s" % r["when"])
        w(u"- **由誰重量**:%s" % u"、".join(u"`%s`" % x for x in r["by"]))
        w(u"- **要量的三件事**:")
        for x in r["measure"]:
            w(u"  - %s" % x)
        w(u"- %s" % r["note"])
        oc = r.get("outcome")
        if oc:
            w(u"- **重判結果**:")
            for k in ("triggered","measured_by","m1","m2","m3","ruling","criterion","answer","inherited_risk"):
                if oc.get(k):
                    w(u"  - `%s`:%s" % (k, oc[k]))
            fo = oc.get("final_order")
            if fo:
                w(u"  - **最終序(%s)**:**%s**" % (fo["by"], fo["ruling"]))
                for k in ("why","correction_to_my_answer","meta"):
                    w(u"    - %s" % fo[k])
        w(u"")
    w(u"---")
    w(u"")
    w(u"## 共同前置(三刀皆適用)")
    w(u"")
    w(u"1. **判準先行**:動工前先向 `qa3@00000` 取該刀判準分支;**判準不外包子 AI**。")
    w(u"2. **共同前置臂**:被測物尚未存在時跑整組,確認**全紅且理由是「缺被測物」那一個**——沒有這一步,「N 綠」證不了任何事。")
    w(u"3. **交件面含全庫臂**:只看自己動過的檔,答案是「我這一支」的函數;閘要回答的是「合進去之後」。")
    w(u"4. **狀態宣稱貼當回合機器輸出**;`push rc=0` 不是落檔證據,要 `ls-remote` 讀回。")
    return u"\n".join(o) + u"\n"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="docs/dispatch/MANIFEST-0828-G2.json")
    ap.add_argument("--out")
    ap.add_argument("--check")
    a = ap.parse_args()
    m = json.load(io.open(a.manifest, encoding="utf-8"))
    text = render(m)
    if a.check:
        cur = io.open(a.check, encoding="utf-8").read()
        if cur != text:
            sys.stderr.write(T("aib.render.diff", {"path": a.check}))
            return 1
        sys.stdout.write(T("aib.render.same", {"path": a.check}))
        return 0
    if a.out:
        io.open(a.out, "w", encoding="utf-8").write(text)
        return 0
    sys.stdout.write(text)
    return 0

if __name__ == "__main__":
    sys.exit(main())
