# SPDX-License-Identifier: Apache-2.0
"""i18n 載入器(i18n/loader-spec.md §3 的機械落地)。

字典正本=repo 的 `i18n/<locale>.json`,本模組只讀不寫、純函數無全域可變狀態。
行為逐條照規格:
  1. 語系來源(高→低):呼叫端明給 > $EZ2_AIB_LOCALE > 預設 zh-TW
     支援 zh-TW/en/ja/ko 與常見 POSIX locale alias;未知值回預設語系。
  2. 查詢順序:當前語系 -> en fallback -> fail-loud 回 `!!key!!`(絕不回空字串)
  3. 字典檔缺失/JSON 壞掉 = 啟動即 fail(地基破洞不掩蓋)
  4. 插值 `{name}`;缺參數時保留 `{name}` 字面原樣(可見即可抓)

放在 src/cockpit/ 是因為 E2.9 只准動本 lane;它是通用件,第一艦長整合時可
升格成共用模組,介面不必改(t/load_dict 皆無 cockpit 依賴)。

CLI(給 bash 腳本用,避免 shell 自己解析 JSON):
  python3 i18n_lib.py [--locale zh-TW] [--param k=v]... KEY [KEY...]
每個 KEY 印一行;未知 key 印 `!!key!!` 但 rc 仍為 0(fail-loud 靠可見字串,
不靠讓呼叫端崩潰 —— 原理 10:回饋器永不擋)。
"""

import json
import os
import re
import stat
import sys
from pathlib import Path
from collections.abc import Mapping

DEFAULT_LOCALE = "zh-TW"
FALLBACK_LOCALE = "en"
CANONICAL_LOCALES = ("zh-TW", "en", "ja", "ko")
I18N_DIR = Path(__file__).resolve().parents[2] / "i18n"

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_KEY_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
_LOCALE_ALIASES = {
    "zh": "zh-TW",
    "zh-tw": "zh-TW",
    "zh-hant": "zh-TW",
    "zh-hant-tw": "zh-TW",
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "ja": "ja",
    "ja-jp": "ja",
    "ko": "ko",
    "ko-kr": "ko",
}


class I18nError(RuntimeError):
    """字典不可用 —— 地基破洞,啟動即 fail。"""


def normalize_locale(locale):
    """Return an allowlisted locale or None without ever treating input as a path."""
    if not isinstance(locale, str) or not locale.strip():
        return None
    token = locale.strip().split("@", 1)[0].split(".", 1)[0]
    token = token.replace("_", "-").lower()
    return _LOCALE_ALIASES.get(token)


def dict_path(locale, i18n_dir=None):
    canonical = normalize_locale(locale)
    if canonical is None:
        raise I18nError("unsupported i18n locale: %s" % locale)
    return Path(i18n_dir or I18N_DIR) / ("%s.json" % canonical)


def _reject_duplicate_keys(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise I18nError("i18n dictionary has duplicate key: %s" % key)
        data[key] = value
    return data


def load_dict(locale, i18n_dir=None, _keyed=False, _quiet=False):
    """_keyed:Translator 自己載(之後只經 t()/逐鍵取值用)—— 不算「整份都用到」;
    其他呼叫端拿到的是整份 dict ⇒ 記 "@whole"。_quiet:測試沿用的重算端拿來驗字典,不寫任何追蹤紀錄。"""
    path = dict_path(locale, i18n_dir)
    if not _quiet:
        if _keyed:
            _trace_emit({"ch": "i18nload", "path": os.path.abspath(str(path))})
        else:
            _keylog("@whole")
    try:
        mode = path.lstat().st_mode
    except OSError as err:
        raise I18nError("i18n dictionary unreadable: %s (%s)" % (path, err))
    if not stat.S_ISREG(mode):
        raise I18nError("i18n dictionary must be a regular non-symlink file: %s" % path)
    try:
        with open(path, encoding="utf-8") as fh:
            # 10-12 a seat:不經 json.load / json.loads —— 測試沿用以鍵為單位時(AIB_KEYED_FILES,aib 自己的倉自動偵測到
            #   i18n/*.json)那兩個會被換成「會記帳的字典」,下面的整份檢查就會被記成整份用到,Translator 的以鍵沿用整個失效。
            #   JSONDecoder.decode 與 json.load 解出來一模一樣;以鍵的紀錄由本支自己的 i18nload / i18nkey 負責。
            data = json.JSONDecoder(object_pairs_hook=_reject_duplicate_keys).decode(fh.read())
    except OSError as err:
        raise I18nError("i18n dictionary unreadable: %s (%s)" % (path, err))
    except ValueError as err:
        raise I18nError("i18n dictionary is not valid JSON: %s (%s)" % (path, err))
    if not isinstance(data, dict):
        raise I18nError("i18n dictionary must be a flat object: %s" % path)
    bad_keys = [key for key in data if not isinstance(key, str) or not _KEY_RE.match(key)]
    if bad_keys:
        raise I18nError("i18n dictionary has invalid keys: %s (%s)" % (path, bad_keys))
    bad_values = [key for key, value in data.items()
                  if not isinstance(value, str) or not value.strip()]
    if bad_values:
        raise I18nError("i18n dictionary values must be non-empty strings: %s (%s)" %
                        (path, bad_values))
    bad_placeholders = []
    for key, value in data.items():
        remainder = _PLACEHOLDER.sub("", value)
        if "{" in remainder or "}" in remainder:
            bad_placeholders.append(key)
    if bad_placeholders:
        raise I18nError("i18n dictionary has malformed placeholders: %s (%s)" %
                        (path, bad_placeholders))
    return data


def prefixed(prefix, locale, i18n_dir=None):
    """10-07:某語系字典裡「鍵以 prefix 開頭」的全部 {鍵: 值}(像 seat_address 的 aib.sender.origin.*)。
    不做 load_dict 的整份檢查(與呼叫端原本自己 json.load 的行為一致:讀不到 / 不是 JSON / 不是物件 ⇒ {})。
    追蹤:記這份字典被翻譯程式庫打開(i18nload)、用到「@prefix:<prefix>」這一組 ⇒ 測試沿用只看這一組的鍵與值
    (新增、修改、刪掉這一組裡的鍵 ⇒ 重跑;別的鍵改了 ⇒ 沿用)。"""
    path = dict_path(locale, i18n_dir)
    _trace_emit({"ch": "i18nload", "path": os.path.abspath(str(path))})
    _keylog("@prefix:" + prefix)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.JSONDecoder().decode(fh.read())     # 同 load_dict:不經會被換掉的 json.loads
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and k.startswith(prefix)}


def resolve_locale(explicit=None, env=None):
    env = os.environ if env is None else env
    selected = explicit or env.get("EZ2_AIB_LOCALE") or DEFAULT_LOCALE
    return normalize_locale(selected) or DEFAULT_LOCALE


def interpolate(template, params):
    """以 {name} 具名代入;缺的參數保留字面原樣。"""
    if not params:
        return template

    def sub(m):
        name = m.group(1)
        return str(params[name]) if name in params else m.group(0)

    return _PLACEHOLDER.sub(sub, template)


# 10-06(開發檔 §15.3「讓熱門檔變冷,先量」):測試環境帶 AIB_I18N_KEYLOG=<檔> 時,記下這個行程用到哪些翻譯鍵
#   (每個鍵記一次;整份讀的 parity_report 記 "@whole")。正式使用沒有這個變數,行為不變。只用來估算,不影響沿用判斷。
# 10-06 晚(開發檔 §15.3 第二步):測試在追蹤下跑(EZ2_VERIFY_TRACE)時,同一份資訊也寫進追蹤檔 ——
#   i18nload = Translator 自己開了哪份字典;i18nkey = 用到哪個鍵("@whole" = 整份都用到)。
#   追蹤器只在「這份字典的每一次開檔都是 Translator 開的、沒有 @whole」時,才把這份字典改成只看用到的那些鍵。
#   寫法與 tools/verify/cell_runner.py 的格界線相同(一行 JSON 附加)。正式使用兩個變數都沒有,行為不變。
_KEYLOG_SEEN = set()


def _trace_emit(obj):
    path = os.environ.get("EZ2_VERIFY_TRACE")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(obj, pid=os.getpid()), ensure_ascii=True, sort_keys=True) + "\n")
    except OSError:
        pass


def _keylog(key):
    if key in _KEYLOG_SEEN:
        return
    _KEYLOG_SEEN.add(key)
    path = os.environ.get("AIB_I18N_KEYLOG")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(key + "\n")
        except OSError:
            pass
    _trace_emit({"ch": "i18nkey", "key": key})


class _KeyedView(Mapping):
    """唯讀的字典外觀(取代 MappingProxyType):逐鍵取值記那個鍵;列舉、算長度 ⇒ 記 "@whole"(用到整份)。"""

    def __init__(self, data):
        self._data = data

    def __getitem__(self, key):
        _keylog(key)
        return self._data[key]

    def __iter__(self):
        _keylog("@whole")
        return iter(self._data)

    def __len__(self):
        _keylog("@whole")
        return len(self._data)


class Translator:
    def __init__(self, locale=None, i18n_dir=None):
        self.locale = resolve_locale(locale)
        self._i18n_dir = i18n_dir
        # Unknown locale names are normalized to DEFAULT_LOCALE above.  A known
        # locale whose canonical file is missing/corrupt must fail instead of
        # being misclassified as an unknown locale and silently hidden.
        self._p = load_dict(self.locale, i18n_dir, _keyed=True)
        self._f = self._p if self.locale == FALLBACK_LOCALE else load_dict(FALLBACK_LOCALE, i18n_dir, _keyed=True)
        self._primary = _KeyedView(self._p)
        self._fallback = _KeyedView(self._f)

    def parity_report(self, locales=None):
        """runtime 四語 parity 自檢(codex 審 2026-08-10 中級)。

        原本 runtime 只驗「當前語系與 en 各自合法」,字典漂移只能等 CI;
        但部署後的機器不跑 CI。本方法讓任何消費者(哨兵/啟動器/測試)
        都能在執行期取得 key 與 placeholder 的 parity 事實。
        回報而不 raise——原理 10:閘門可擋,回饋永不擋。"""
        import re as _re
        _keylog("@whole")
        names = list(locales or CANONICAL_LOCALES)
        dicts = {}
        errors = []
        for loc in names:
            try:
                dicts[loc] = load_dict(loc, self._i18n_dir)
            except Exception as err:  # 壞字典本身就是 parity 事實
                errors.append({"locale": loc, "code": "unloadable",
                               "detail": str(err)})
        if not dicts:
            return {"ok": False, "errors": errors, "checked": names}
        base = FALLBACK_LOCALE if FALLBACK_LOCALE in dicts else sorted(dicts)[0]
        base_keys = set(dicts[base])
        ph = _re.compile(r"\{([a-zA-Z0-9_]+)\}")
        for loc, table in dicts.items():
            if loc == base:
                continue
            missing = sorted(base_keys - set(table))
            extra = sorted(set(table) - base_keys)
            if missing:
                errors.append({"locale": loc, "code": "missing_keys",
                               "detail": missing[:20]})
            if extra:
                errors.append({"locale": loc, "code": "extra_keys",
                               "detail": extra[:20]})
            for key in base_keys & set(table):
                if sorted(ph.findall(dicts[base][key])) != sorted(ph.findall(table[key])):
                    errors.append({"locale": loc, "code": "placeholder_drift",
                                   "detail": key})
        return {"ok": not errors, "errors": errors, "checked": sorted(dicts),
                "base": base, "key_count": len(base_keys)}

    def t(self, key, params=None):
        _keylog(key)
        value = self._p.get(key)
        if value is None:
            value = self._f.get(key)
        if value is None:
            return "!!%s!!" % key
        return interpolate(str(value), params)


def t(key, params=None, locale=None, i18n_dir=None):
    """一次性查詢(CLI 與測試用;長駐程式請自建 Translator 重用字典)。"""
    return Translator(locale=locale, i18n_dir=i18n_dir).t(key, params)


def _main(argv):
    locale, params, keys = None, {}, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--locale" and i + 1 < len(argv):
            locale = argv[i + 1]
            i += 2
        elif a == "--param" and i + 1 < len(argv):
            k, _, v = argv[i + 1].partition("=")
            params[k] = v
            i += 2
        else:
            keys.append(a)
            i += 1
    if not keys:
        print("usage: i18n_lib.py [--locale L] [--param k=v]... KEY [KEY...]",
              file=sys.stderr)
        return 2
    try:
        tr = Translator(locale=locale)
    except I18nError as err:
        print(str(err), file=sys.stderr)
        return 1
    for key in keys:
        print(tr.t(key, params))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
