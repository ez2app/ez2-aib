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
