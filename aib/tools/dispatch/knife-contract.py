#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""派工宣告刀,交件對得上 —— 把「判準」從只有人會讀的地方搬到會回非零 rc 的地方。

★ 為什麼有這支檔(2026-09-09 的兩次實例,不是理論):
  我把「工具自己壞掉要 fail-open」**明文寫進派工單**,交回來的稿仍然寫成硬擋;
  少的那一把刀是我**逐則讀**才發現的。同一天同一個形狀發生兩次,兩次都靠眼睛接住。
  ⇒ **規則寫在只有人會讀的地方,就只有人在守。**

★ 這支檔守六件事,各回不同 rc,因為它們是不同的假象:

  rc=3  宣告過的刀**沒有出現在交件裡**
        —— 少一把刀的交件,與做滿的交件在文字上長得一樣。
  rc=4  某把刀的紅集合是**空的**
        —— 刀在、跑了、沒有任何一格紅 = 那把刀不帶資訊,而它看起來像通過。
  rc=5  **no-op 的紅集合不是空的**
        —— 產品自己就是紅的,那麼刀下的紅證不了刀。
  rc=6  某把刀的產品**與 no-op 逐位元組相同**
        —— ★ 刀沒落與刀落了但沒事,在收據上同形。這一欄是本庫付過學費的。
  rc=7  `requires` 指到的上游**沒有獨立接受**
        —— 下游自稱接受、或只交了一個抄來的 hash,都不能替上游自己的紀錄。
  rc=8  上游接受紀錄綁的交付物**無法重算或已漂移**
        —— 接受時相同與現在仍相同是兩件事;`check` 每次都從檔案 bytes 重算。

★ 本檔不評價「刀砍得對不對」—— 那要人看。它只保證
  **宣告過的每一把都真的落過、真的有紅、而且產品沒有一開始就是紅的。**
  ⇒ 它是必要條件不是充分條件,這句話寫在這裡免得有人拿它當驗收。
"""

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import sys

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


RC_OK = 0
RC_USAGE = 2
RC_MISSING_KNIFE = 3
RC_EMPTY_RED_SET = 4
RC_NOOP_NOT_CLEAN = 5
RC_KNIFE_NEVER_LANDED = 6
RC_UPSTREAM_NOT_ACCEPTED = 7
RC_UPSTREAM_ARTIFACT_DRIFT = 8

PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def store() -> pathlib.Path:
    root = os.environ.get("EZ2_AIB_HOME") or (pathlib.Path.home() / ".ez2-aib")
    d = pathlib.Path(root) / "dispatch"
    d.mkdir(parents=True, exist_ok=True)
    return d


def contract_path(pkg: str) -> pathlib.Path:
    return store() / ("%s.json" % pkg)


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def cmd_declare(args) -> int:
    knives = []
    for raw in args.knife or []:
        if "=" not in raw:
            sys.stderr.write(T("aib.knife.form", {"got": raw}))
            return RC_USAGE
        name, proves = raw.split("=", 1)
        name, proves = name.strip(), proves.strip()
        if not name or not proves:
            # ★ 沒寫「證明什麼」的刀,事後沒有人分得出它該紅哪一格。
            sys.stderr.write(T("aib.knife.empty", {"got": raw}))
            return RC_USAGE
        knives.append({"name": name, "proves": proves})
    if not knives:
        sys.stderr.write(T("aib.knife.none"))
        return RC_USAGE
    rec = {
        "package": args.package,
        "seat": args.seat,
        "declared_at": now_iso(),
        "by": args.by,
        "knives": knives,
        "notes": args.note,
    }
    contract_path(args.package).write_text(
        json.dumps(rec, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"package": args.package, "knives": [k["name"] for k in knives],
                      "path": str(contract_path(args.package))}, ensure_ascii=False))
    return RC_OK


def load_contract(pkg):
    p = contract_path(pkg)
    if not p.exists():
        sys.stderr.write(T("aib.knife.pkg_none", {"pkg": pkg, "path": p}))
        sys.exit(RC_USAGE)
    return json.loads(p.read_text())


def acceptance_fix(path: pathlib.Path) -> str:
    return (
        "補法=在 %s 的上游宣告加 "
        "acceptance.state=accepted、acceptance.accepted_by、"
        "acceptance.artifact_root、acceptance.artifacts、"
        "acceptance.artifacts_sha256(由主導席重算 path:digest 合成 SHA-256)"
        % path
    )


def acceptance_fingerprint(acceptance):
    """從上游自己的接受紀錄重算交付物指紋。

    算法沿用本庫 `tools/aib-verify.py::artifacts_fingerprint`:依 path 排序,
    每檔先算 sha256,串成 `path:digest` 換行清單後再算一次 sha256。
    這裡刻意不收 delivery 的任何 hash;下游只能指出依賴誰,不能替上游驗自己。
    """
    root_raw = acceptance.get("artifact_root")
    if not isinstance(root_raw, str) or not root_raw.strip():
        raise ValueError("缺 acceptance.artifact_root")
    root = pathlib.Path(root_raw).expanduser()
    if not root.is_absolute():
        raise ValueError("acceptance.artifact_root 必須是絕對路徑:%s" % root_raw)
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("acceptance.artifact_root 不存在或不是目錄:%s" % root)

    artifacts = acceptance.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("acceptance.artifacts 必須是非空陣列")

    rows = []
    seen = set()
    for index, item in enumerate(artifacts):
        if not isinstance(item, dict):
            raise ValueError("acceptance.artifacts[%d] 必須是 object" % index)
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("acceptance.artifacts[%d].path 缺或空" % index)
        rel = pathlib.Path(raw_path)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("acceptance artifact path 必須留在 artifact_root 內:%s" % raw_path)
        normalized = rel.as_posix()
        if normalized in seen:
            raise ValueError("acceptance artifact path 重複:%s" % normalized)
        seen.add(normalized)
        target = (root / rel).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise ValueError("acceptance artifact path 逃出 artifact_root:%s" % raw_path)
        if not target.is_file():
            raise ValueError("acceptance artifact 缺檔或不是一般檔:%s" % raw_path)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        rows.append((normalized, digest))

    joined = "\n".join("%s:%s" % row for row in sorted(rows))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def verify_requirements(rec):
    """只從上游自己的 dispatch 宣告驗 requires,不信下游 delivery 自報。"""
    requirements = rec.get("requires")
    if requirements is None:
        return [], []
    if not isinstance(requirements, list):
        return [(RC_UPSTREAM_NOT_ACCEPTED,
                 "requires 必須是陣列;收到:%s" % type(requirements).__name__)], []

    problems = []
    checked = []
    seen = set()
    for index, requirement in enumerate(requirements):
        if not isinstance(requirement, dict):
            problems.append((RC_UPSTREAM_NOT_ACCEPTED,
                             "requires[%d] 必須是 object" % index))
            continue
        package = requirement.get("package")
        if not isinstance(package, str) or not PACKAGE_RE.fullmatch(package):
            problems.append((RC_UPSTREAM_NOT_ACCEPTED,
                             "requires[%d].package 缺或格式不合法:%r" % (index, package)))
            continue
        if package in seen:
            problems.append((RC_UPSTREAM_NOT_ACCEPTED,
                             "上游 %s 在 requires 重複" % package))
            continue
        seen.add(package)

        upstream_path = contract_path(package)
        if not upstream_path.is_file():
            problems.append((
                RC_UPSTREAM_NOT_ACCEPTED,
                "上游 %s 宣告不存在:%s;%s" %
                (package, upstream_path, acceptance_fix(upstream_path)),
            ))
            continue
        try:
            upstream = json.loads(upstream_path.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            problems.append((RC_UPSTREAM_NOT_ACCEPTED,
                             "上游 %s 宣告讀取失敗:%s" % (package, error)))
            continue
        if not isinstance(upstream, dict) or upstream.get("package") != package:
            problems.append((RC_UPSTREAM_NOT_ACCEPTED,
                             "上游 %s 宣告的 package 不吻合:%s" %
                             (package, upstream.get("package") if isinstance(upstream, dict) else None)))
            continue

        acceptance = upstream.get("acceptance")
        state = acceptance.get("state") if isinstance(acceptance, dict) else None
        if state != "accepted":
            detail = ("缺 acceptance.state" if state is None
                      else "acceptance.state=%s,不是 accepted" % state)
            problems.append((RC_UPSTREAM_NOT_ACCEPTED,
                             "上游 %s 未被獨立接受:%s;%s" %
                             (package, detail, acceptance_fix(upstream_path))))
            continue
        accepted_by = acceptance.get("accepted_by")
        if not isinstance(accepted_by, str) or not accepted_by.strip():
            problems.append((RC_UPSTREAM_NOT_ACCEPTED,
                             "上游 %s acceptance.accepted_by 缺或空;%s" %
                             (package, acceptance_fix(upstream_path))))
            continue

        claimed = acceptance.get("artifacts_sha256")
        if not isinstance(claimed, str) or not SHA256_RE.fullmatch(claimed):
            problems.append((RC_UPSTREAM_ARTIFACT_DRIFT,
                             "上游 %s acceptance.artifacts_sha256 缺或不是 64 位小寫 hex;%s" %
                             (package, acceptance_fix(upstream_path))))
            continue
        try:
            actual = acceptance_fingerprint(acceptance)
        except (OSError, ValueError) as error:
            problems.append((RC_UPSTREAM_ARTIFACT_DRIFT,
                             "上游 %s 交付物無法重算:%s;%s" %
                             (package, error, acceptance_fix(upstream_path))))
            continue
        if claimed != actual:
            artifact_paths = [str(item.get("path")) for item in acceptance.get("artifacts", [])
                              if isinstance(item, dict)]
            problems.append((RC_UPSTREAM_ARTIFACT_DRIFT,
                             "上游 %s 交付物指紋不符:記錄 %s vs 重算 %s;artifacts=%s" %
                             (package, claimed[:12], actual[:12], artifact_paths)))
            continue
        checked.append(package)
    return problems, checked


def cmd_check(args) -> int:
    rec = load_contract(args.package)
    delivery = json.loads(pathlib.Path(args.delivery).read_text())
    results = {r.get("variant"): r for r in delivery.get("results", [])}

    problems, checked_requirements = verify_requirements(rec)

    noop = results.get("noop")
    if noop is None:
        problems.append((RC_MISSING_KNIFE, "交件裡沒有 noop 那一格 —— 沒有它,刀下的紅證不了刀"))
    else:
        if noop.get("red_set"):
            problems.append((RC_NOOP_NOT_CLEAN,
                             "no-op 紅集合不是空的:%s" % noop["red_set"]))
        if noop.get("byte_identical") is False:
            problems.append((RC_NOOP_NOT_CLEAN,
                             "no-op 那一格的產品與正本不同 —— 它不是 no-op"))

    for k in rec["knives"]:
        name = k["name"]
        r = results.get(name)
        if r is None:
            problems.append((RC_MISSING_KNIFE,
                             "宣告過的刀沒有出現在交件裡:%s(它要證明:%s)" % (name, k["proves"])))
            continue
        if not r.get("red_set"):
            problems.append((RC_EMPTY_RED_SET,
                             "刀 %s 的紅集合是空的 —— 它不帶資訊(它本來要證明:%s)"
                             % (name, k["proves"])))
        if r.get("byte_identical") is True:
            problems.append((RC_KNIFE_NEVER_LANDED,
                             "刀 %s 的產品與 no-op 逐位元組相同 —— 這把刀根本沒落" % name))

    extra = [v for v in results if v != "noop" and v not in {k["name"] for k in rec["knives"]}]

    if not problems:
        print(json.dumps({"package": args.package, "ok": True,
                          "knives_checked": [k["name"] for k in rec["knives"]],
                          "requires_checked": checked_requirements,
                          # ★ 多出來的刀不是錯,但要說出來:它沒有被宣告過,所以沒有人審過它該紅什麼。
                          "undeclared_extra_knives": extra}, ensure_ascii=False))
        return RC_OK

    for _, msg in problems:
        sys.stderr.write("knife-contract: %s\n" % msg)
    sys.stderr.write(T("aib.knife.total", {"n": len(problems), "path": contract_path(args.package)}))
    # ★ 多個問題時回**最嚴重**的那一個 rc(數字大的先),不要回一個模糊的 1。
    return max(rc for rc, _ in problems)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="派工宣告刀,交件對得上")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("declare", help="派工時宣告這一包必須有哪幾把具名的刀")
    p.add_argument("--package", required=True)
    p.add_argument("--seat", required=True)
    p.add_argument("--by", default="80ec5")
    p.add_argument("--knife", action="append", help="刀名=它證明什麼,可重複")
    p.add_argument("--note")
    p.set_defaults(func=cmd_declare)

    p = sub.add_parser("check", help="拿交件的矩陣結果對宣告")
    p.add_argument("--package", required=True)
    p.add_argument("--delivery", required=True,
                   help="含 results:[{variant,red_set,byte_identical,...}] 的 JSON")
    p.set_defaults(func=cmd_check)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
