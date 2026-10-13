# SPDX-License-Identifier: Apache-2.0
"""jsonl 的行界只認 "\n"(10-04 雲端實踩:整池作廢兩次的真因)。

json.dumps(ensure_ascii=False) 會把字串裡的 U+2028 / U+2029 / U+0085 原樣寫出;Python 的 str.splitlines()
把它們也當換行 ⇒ 一列被切成兩半 ⇒ 讀的那邊判 bad-jsonl ⇒ 隔離工具認不出是哪支 ⇒ 整池墓碑(test-rotation 那列的
argv 裡就有 U+2028)。兩邊都修:
  dumps():寫的時候把這三個字元寫成 \\uXXXX(JSON 語意完全相同;舊版讀的那邊也不會再被切)
  split_lines():讀的時候只用 "\n" 切(讀得了舊資料裡已經存在的原樣字元)
"""
import json

_LINE_LIKE = ((" ", "\\u2028"), (" ", "\\u2029"), ("\x85", "\\u0085"))


def dumps(obj, **kw):
    kw.setdefault("ensure_ascii", False)
    s = json.dumps(obj, **kw)
    for raw, esc in _LINE_LIKE:
        if raw in s:
            s = s.replace(raw, esc)
    return s


def split_lines(text):
    """與 str.splitlines() 相同,但只認 "\n"(尾端的空字串去掉;行尾的 "\r" 留著,json 視為空白)。"""
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def iter_lines(path):
    """逐行讀一個 jsonl 檔,行界同 split_lines(只認 "\n"、行尾 "\r" 留著、尾端空字串不回)。
    10-05:整檔讀成一個字串再切,大池子(含中文 ⇒ 每字元 4 位元組)要兩倍的檔案大小乘四;逐行讀只佔一行。"""
    with open(path, "r", encoding="utf-8", errors="replace", newline="\n") as f:
        for line in f:
            yield line[:-1] if line.endswith("\n") else line


def append_locked(path, data, tries=5):
    """在池檔尾端整筆追加 data(bytes),跟所有寫池的人用同一把檔鎖(flock)。
    M1 合池(pool_merge --apply)會在鎖裡原子換掉整個池檔:拿到鎖時手上這份若已不是路徑上那份,
    寫進去就等於寫進被換掉的舊檔(那一列不見)⇒ 重開再寫。tries 次都對不上 ⇒ OSError(呼叫端照舊當「這次沒記到」)。"""
    import fcntl
    import os
    for _ in range(tries):
        with open(path, "ab") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                try:
                    same = os.fstat(f.fileno()).st_ino == os.stat(path).st_ino
                except OSError:
                    same = False
                if not same:
                    continue
                f.write(data)
                f.flush()     # 放鎖之前一定要整列寫進檔案(10-04:緩衝留到關檔才寫 ⇒ 一列被切兩半)
                return
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    raise OSError(_T("replaced_too_often", {"path": path, "n": tries}))


def _T(key, params):
    """出錯才用到的字串走四語字典(tests/test-tools-i18n-ratchet.py);字典載不到也照樣 raise,只是退回鍵名。
    放在函式裡才 import:這支是寫列的底層,平常路徑不該多載一份字典。"""
    import os
    import sys
    from pathlib import Path
    cockpit = str(Path(__file__).resolve().parents[2] / "src" / "cockpit")
    if cockpit not in sys.path:
        sys.path.append(cockpit)
    try:
        import i18n_lib
        return i18n_lib.t("aib.verify.jsonl_safe." + key, params, locale=os.environ.get("EZ2_AIB_LOCALE", "zh-TW"))
    except Exception:
        return "aib.verify.jsonl_safe.%s %r" % (key, params)
