# SPDX-License-Identifier: Apache-2.0
"""維度盤點機器化(C1):把 C0 收斂的維度真相源讀成機器可讀,產覆蓋率報表。

本檔是**讀取器**,不是真相源。維度清單(名字/判準)由 C0 收斂進 `dimensions.json`
(canonical 上游 = ez2-ai `tools/persona-node-merge.js dimensionFor()`),本檔只讀它、
不硬編任何一維——硬編會變成第二份維度真相源,C0 改了這裡不會跟著改,報表就會說謊。

三條硬判準(艦長 04:17 派工;違反=退回):
  1. **覆蓋率一定帶分母,而且分母有兩個不得壓成一個**(dev4 二審抓到的病):
       enumerated_dimensions = 這份文件列了幾維(已知枚舉)
       expected_total        = 總維數(canonical=ez2-ai dimensionFor());**未給=UNRESOLVED,不得預設成枚舉數**
     只有 `enumeration_complete`(總數已知且列滿)時,覆蓋率才可讀成完整。
     ★ 一份只列 3 維的輸入若把 `len(dims)` 當總數,會印出「3/3=100%」——而真相是「這份只列了 3 維」。
     「值不得把必須分辨的狀態壓成一個(criterion_missing≠uncaptured);名字不得把同一個值描述成兩件事
     (已知枚舉≠總數)」——這一刀兩半都要做到。任何「N 維已捕捉」都必須同時帶枚舉數、總數(或 UNRESOLVED)與量測 sha。
  2. **聚合律:三種狀態不得壓成一個值。**
       captured           = 判準在、且量到證據
       uncaptured         = 判準在、但沒量到證據(該補捕捉)
       criterion_missing  = **連判準都查不到**(該補判準,不是補捕捉)
     把 criterion_missing 併進 uncaptured,會叫人去補捕捉——而其實缺的是判準;
     同族於 F 閘的 `contract_unavailable`(看不到規則)≠ `category_uid_is_fabricated`(規則說不合格):
     **「查不到判準」與「判準說沒捕捉」處置相反,壓成一個值診斷方向就會反。**
  3. 突變證明照常(每條不變式都有一刀會讓對應測試紅)。

★ 量測 sha:覆蓋率是「在某個維度真相源狀態下」算出來的。真相源會動(C0 還在收斂),
  所以報表把 `measurement_sha` 釘進去——沒有它,「12 維捕捉 3 維」這句話沒有時態,
  下次真相源長到 15 維時這句話就變成假的而沒人知道(艦長 03:10 狀態會動律)。

★ 「捕捉」口徑聲明(接線 C0 spec `docs/spec/persona-dimensions.md` §3-C1【候裁】,艦長 22:54 令):
  §3-C1 列「捕捉了幾維」有三個口徑——有樣本(9,E5.1:79)/有正則(12,E5.2:76,repo 外已漂移)/有測試釘住(0)。
  **本實作的 `captured` 依「有樣本/有事件證據」那一口徑**:某維若量測輸入 count>0 即判 captured。
  本檔**不裁決**那三個數字之爭:枚舉數來自 dimensions.json(可為 12)、captured 來自量測輸入
  (C0 §2 交叉時餵的是 E5.1:79 的 9=有樣本口徑)、`expected_total` 未給則 UNRESOLVED。
  「有正則」與「有測試釘住」是另兩個口徑,本工具不拿它們當 captured——選擇在此,讓它看得見。
"""

import hashlib
import json

SCHEMA_DIMENSIONS = "aib-persona-dimensions/v1"
SCHEMA_COVERAGE = "aib-persona-coverage/v1"

# 三態常數。★ 別把這三個合併——聚合律的整個重點就是它們是三件事。
CAPTURED = "captured"
UNCAPTURED = "uncaptured"
CRITERION_MISSING = "criterion_missing"
STATUSES = (CAPTURED, UNCAPTURED, CRITERION_MISSING)


class DimensionsError(ValueError):
    """dimensions.json 形狀不對。fail-closed:讀不成就報錯,不回一個看起來合理的空表。"""


def _canonical_bytes(dims_doc):
    """量測 sha 的輸入:維度真相源的規範化位元組(排序鍵、保留非 ASCII)。

    只涵蓋會影響判讀的欄位(source_rev + 每維的 id/name/criterion),不涵蓋註解類欄位,
    這樣『改了一個無關的註解』不會讓 sha 變、把舊報表誤判成過期。"""
    core = {
        "source_rev": dims_doc.get("source_rev"),
        # ★ expected_total 進指紋:總數從 12 改成 15、或從 UNRESOLVED 變成已知,都是不同的一次量測。
        "expected_total": dims_doc.get("expected_total"),
        "dimensions": [
            {"id": d.get("id"), "name": d.get("name"), "criterion": d.get("criterion")}
            for d in dims_doc.get("dimensions", [])
        ],
    }
    return json.dumps(core, sort_keys=True, ensure_ascii=False).encode("utf-8")


def measurement_sha(dims_doc):
    """維度真相源這一刻的內容指紋。內容變 → sha 變(這是它存在的理由)。"""
    return hashlib.sha256(_canonical_bytes(dims_doc)).hexdigest()


def load_dimensions(path):
    """讀 dimensions.json。形狀不對就丟 DimensionsError,不吞。"""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    return validate_dimensions(doc)


def validate_dimensions(doc):
    if not isinstance(doc, dict):
        raise DimensionsError("dimensions 頂層必須是物件")
    if doc.get("schema") != SCHEMA_DIMENSIONS:
        raise DimensionsError("schema 必須是 %s(拿到 %r)" % (SCHEMA_DIMENSIONS, doc.get("schema")))
    dims = doc.get("dimensions")
    if not isinstance(dims, list) or not dims:
        raise DimensionsError("dimensions 必須是非空陣列(空表不是有效真相源,是還沒收斂)")
    seen = set()
    for d in dims:
        if not isinstance(d, dict) or not d.get("id"):
            raise DimensionsError("每一維都要有 id:%r" % (d,))
        if d["id"] in seen:
            raise DimensionsError("維度 id 重複:%s(重複=兩個真相,盤點必假)" % d["id"])
        seen.add(d["id"])
    # expected_total = 這批維度的『總數正本』(canonical=ez2-ai dimensionFor(),C0 從上游抄來)。
    # ★ 可以不給:不給就是『總數 UNRESOLVED』,覆蓋率不得讀成完整——不是預設成 len(dims)。
    #   給了就必須 >= 枚舉數:總數不可能比『已經列出來的維度』還少。
    et = doc.get("expected_total")
    if et is not None:
        if not isinstance(et, int) or isinstance(et, bool) or et <= 0:
            raise DimensionsError("expected_total 若給必須是正整數:%r" % (et,))
        if et < len(dims):
            raise DimensionsError(
                "expected_total(%d) < 枚舉數(%d):總數不可能小於已列出的維度" % (et, len(dims)))
    return doc


def _has_criterion(dim):
    """判準在不在。空字串/None/缺鍵都算『查不到判準』——不是『判準說沒捕捉』。"""
    c = dim.get("criterion")
    return bool(c) and (not isinstance(c, str) or c.strip() != "")


def _captured_count(entry):
    """一筆量測代表幾次捕捉。★ 只有這一份定義——captured 算不算數的判準不得有第二處。"""
    if isinstance(entry, dict):
        return entry.get("count", 0) or 0
    if isinstance(entry, (int, float)):
        return entry
    return 0


def dimension_status(dim, captured):
    """單一維度的三態判定。★ 判準先判:沒判準就到不了 captured/uncaptured 這一層。"""
    if not _has_criterion(dim):
        return CRITERION_MISSING          # 查不到判準:該補判準,不是補捕捉
    entry = captured.get(dim["id"]) if isinstance(captured, dict) else None
    return CAPTURED if _captured_count(entry) > 0 else UNCAPTURED


def coverage(dims_doc, captured=None):
    """覆蓋率報表。★ 一定帶分母(total_dimensions)與量測 sha;三態各自計數不相加壓平。

    captured:{dim_id: {"count": N, "evidence": "..."}} 或 {dim_id: N};量測輸入。
    """
    validate_dimensions(dims_doc)
    captured = captured or {}
    by_dim, counts = [], {s: 0 for s in STATUSES}
    for d in dims_doc["dimensions"]:
        st = dimension_status(d, captured)
        counts[st] += 1
        entry = captured.get(d["id"]) if isinstance(captured, dict) else None
        evidence = entry.get("evidence") if isinstance(entry, dict) else None
        by_dim.append({
            "id": d["id"], "name": d.get("name"), "status": st,
            "criterion_present": _has_criterion(d),
            # ★ 證據位置:未捕捉/查不到判準都可能沒有——如實留 None,不塞佔位字。
            "evidence": evidence,
        })
    enumerated = len(dims_doc["dimensions"])
    # ★ 不變式:三態之和 == 枚舉數(這份文件列了幾維)。任何把兩態壓成一態的突變都會破這條。
    assert counts[CAPTURED] + counts[UNCAPTURED] + counts[CRITERION_MISSING] == enumerated
    # ★★ ghost:量測捕捉了一個『枚舉表上沒有』的維度。不得靜默丟棄(pm2 第 2 型:沒有那一格)——
    #    它正是「這份枚舉不完整」的直接機器證據(有東西落在表外),也是艦長第六種「發生了卻不被寫下」的實體。
    #    列 dim_id 不只計數:計數說有問題,列表說缺哪一維。
    known = {d["id"] for d in dims_doc["dimensions"]}
    captured_for_unknown = sorted(k for k, v in captured.items()
                                  if k not in known and _captured_count(v) > 0) \
        if isinstance(captured, dict) else []
    expected_total = dims_doc.get("expected_total")   # None = 總數 UNRESOLVED
    # ★ 覆蓋率只有在『枚舉完整』時才可讀成完整:總數已知、這份列滿了那麼多維、且沒有落在表外的 ghost。
    #   有 ghost 就一定不完整——否則會出現「總數已知、列滿了、卻有量到的維度不在表上」的自相矛盾綠。
    enumeration_complete = (expected_total is not None and enumerated == expected_total
                            and not captured_for_unknown)
    return {
        "schema": SCHEMA_COVERAGE,
        "measurement_sha": measurement_sha(dims_doc),   # 沒有它這份報表沒有時態
        "source_rev": dims_doc.get("source_rev"),
        "enumerated_dimensions": enumerated,            # 已知枚舉:這份文件列了幾維(≠總數)
        "expected_total": expected_total,               # 總數;None=UNRESOLVED,不得預設成 enumerated
        "enumeration_complete": enumeration_complete,   # 只有 True 覆蓋率才可讀成完整
        "captured_for_unknown": captured_for_unknown,   # 量到卻不在枚舉表上的維度 id(不得靜默丟)
        "counts": counts,                                # captured/uncaptured/criterion_missing 各自一格
        "by_dimension": by_dim,
    }


def total_line(cov):
    """總數那一行。★ 這是 dev4 二審的釘子:枚舉不完整/總數未解時,絕不印出可讀成 100% 的形狀。
    分子(captured/enumerated)在這裡永遠帶著『這只是已列出的維度、不是總數』的限定。"""
    en, et = cov["enumerated_dimensions"], cov["expected_total"]
    if cov["enumeration_complete"]:
        return "已知枚舉 %d 維;總數 %d 維(枚舉完整,覆蓋率可讀成完整)" % (en, et)
    ghost = cov.get("captured_for_unknown") or []
    if ghost:
        # ★ 有 ghost:即使宣告總數==枚舉數,也被推翻——量到的維度落在表外,證明總數至少低估 len(ghost)。
        base = ("宣告總數 %d" % et) if et is not None else "總數 UNRESOLVED"
        return ("已知枚舉 %d 維;%s,但有 %d 個量到的維度落在枚舉表外——"
                "總數至少低估 %d,不得讀成完整" % (en, base, len(ghost), len(ghost)))
    if et is None:
        return ("已知枚舉 %d 維;總數 UNRESOLVED——維度正本在 ez2-ai 側(dimensionFor()),"
                "本刀唯讀不裁;在總數確定前不得把本報表讀成完整覆蓋" % en)
    return ("已知枚舉 %d 維;枚舉不完整:%d/%d(還有 %d 維未進本文件)——"
            "不得讀成已覆蓋" % (en, en, et, et - en))


def format_report(cov):
    """人看的逐維報表。分子永遠和『已知枚舉/總數/sha』綁在一起印,不給人只讀到分子當 100% 的機會。"""
    c = cov["counts"]
    head = ("維度覆蓋率:captured %d / uncaptured %d / criterion_missing %d\n"
            "%s\n量測 sha=%s  source_rev=%s"
            % (c[CAPTURED], c[UNCAPTURED], c[CRITERION_MISSING], total_line(cov),
               cov["measurement_sha"][:16], cov.get("source_rev")))
    mark = {CAPTURED: "✓", UNCAPTURED: "·", CRITERION_MISSING: "?"}
    lines = [head]
    # ★ ghost 一定要進人看的報表,不能只躺在 JSON——列 id 讓人看得到缺哪一維(枚舉不完整的直接證據)。
    ghost = cov.get("captured_for_unknown") or []
    if ghost:
        lines.append("★ 量到卻不在枚舉表上的維度(枚舉不完整的直接證據,expected_total 至少要 +%d):%s"
                     % (len(ghost), "、".join(ghost)))
    lines.append("-" * 60)
    for d in cov["by_dimension"]:
        # ★ 這行的文案必須跟著 status,不是只看有沒有 evidence——captured 但沒附證據位置的維度
        #   若掉進「未捕捉」文案,就是又一次「名字把值描述成它不是的東西」。
        if d["evidence"]:
            where = d["evidence"]
        elif d["status"] == CAPTURED:
            where = "(已捕捉,證據位置未附)"
        elif d["status"] == CRITERION_MISSING:
            where = "(無判準,先補判準)"
        else:
            where = "(未捕捉,先補捕捉)"
        lines.append("  [%s] %-6s %-10s %s" % (mark[d["status"]], d["status"], d.get("name") or d["id"], where))
    return "\n".join(lines)


def _main(argv=None):
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="aib persona dimensions",
                                 description="維度盤點覆蓋率(C1:讀 C0 真相源,帶分母與量測 sha)")
    ap.add_argument("--dimensions", required=True, help="dimensions.json(C0 收斂的維度真相源)")
    ap.add_argument("--captured", help="量測輸入 JSON:{dim_id:{count,evidence}};不給=全部未捕捉")
    ap.add_argument("--json", action="store_true", help="輸出機器格式(預設人看格式)")
    args = ap.parse_args(argv)
    try:
        doc = load_dimensions(args.dimensions)
    except (OSError, DimensionsError, json.JSONDecodeError) as e:
        print("dimensions_error: %s" % e, file=sys.stderr)
        return 2
    captured = {}
    if args.captured:
        with open(args.captured, encoding="utf-8") as fh:
            captured = json.load(fh)
    cov = coverage(doc, captured)
    if args.json:
        print(json.dumps(cov, ensure_ascii=False, indent=2))
    else:
        print(format_report(cov))
    # rc:0=枚舉完整 且 每一維都 captured;1=枚舉未完整/總數未解,或有 uncaptured/criterion_missing。
    # ★ 枚舉不完整即使『已列出的都捕捉了』也不得 rc=0——那正是會被讀成 100% 的假完整。
    clean = cov["counts"][UNCAPTURED] == 0 and cov["counts"][CRITERION_MISSING] == 0
    return 0 if (cov["enumeration_complete"] and clean) else 1


if __name__ == "__main__":
    import sys
    sys.exit(_main())
