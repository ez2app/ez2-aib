# SPDX-License-Identifier: Apache-2.0
"""測試共用小工具。

檔名不帶連字號是刻意的:`test-*.py` 的模組名含 `-`,無法被 `import`,
所以共用的東西只能放在這種可 import 的檔裡。
"""

import ast
import io
import os
import re
import sys
import tokenize

# review r1 m9:測試預設基因**關**。ez2_genes 沒設 AIB_GENES 時讀 $HOME/.ez2-aib/genes.json ——
#   沒換 HOME 的舊測試會讀到操作者真的開關,開著時一堆斷言開場字/系統提示的格就翻紅(或假綠)。
#   只 setdefault:格子自己設了 AIB_GENES(要量「開」的那幾支)照舊贏;子行程繼承 os.environ 一起關。
#   ★ 放在測試共用檔、不改 ez2_genes 的檔案位置:真使用者的開關語意一個字都不動。
os.environ.setdefault("AIB_GENES", "0")
# 10-13 記憶整理第 2 包(a seat 11:36):交接第三步的背景自動綁預設開 ⇒ 測試預設**關**(不在 the maintainer 螢幕跳真視窗、不真建卡)。
#   同樣只 setdefault:要驗自動綁的格自己明確開(AIB_AUTO_BIND=1);子行程繼承 os.environ 一起關。
os.environ.setdefault("AIB_AUTO_BIND", "0")
# 10-17 Touch ID 核准(@570a9):AI 席跑 aib newproj --approve 時 Mac 會跳 Touch ID 視窗 ⇒ 測試預設**關**(不在 the maintainer
#   螢幕跳真視窗)。AIB_TOUCHID=0 只會關掉那條路、退回只剩終端機,不會放行任何東西。要驗的格用替身注入,不開真視窗。
os.environ.setdefault("AIB_TOUCHID", "0")
# 10-17 狀態列自動更新(@570a9):aib init 結尾會跑 statusline-install.sh --auto ⇒ 測試預設**關**(沒換 HOME 的舊測試
#   不會去換操作者真的 ~/.claude 狀態列)。要驗的格自己明確開(AIB_STATUSLINE_AUTO=1)並換 HOME。
os.environ.setdefault("AIB_STATUSLINE_AUTO", "0")


# ★ AR(2026-09-02,the maintainer 眼驗選 A):子 AI 列自帶彎角 `├--⚙`/`╰--⚙`(舊形 `---⚙` 退役)。
#   消費者一律用這把尺,不各存一份字面(test-mouse / test-e219 / test-pane-right-ai 共用)。
SUB_RAIL_RE = re.compile(r"[├╰]--⚙")

def python_code_only(text):
    """剝掉 Python 的 docstring 與 `#` 註解後,**真的會被執行到**的東西。

    回傳的是「字串常數 + 識別字」逐行串起來的文字,不是可執行的 Python
    ——它只給靜態斷言拿去 `in` / `not in`,不給人讀也不給機器跑。

    ★為什麼需要這一支(08-15 第四次發作):`code_only` 剝 `#` 註解但**不剝
    docstring**。拿它去掃 Python 檔時,說明文字會被當成碼:

        assertNotIn 被說明文字絆倒 → **假紅**(碼是乾淨的,卻紅了)
        assertIn    被說明文字餵飽 → **假綠**(碼裡根本沒有,卻綠了)

    兩個方向是同一個病:**判準分不出「碼」與「講碼的話」**。而假綠是危險的
    那一半——假紅會有人來吵,假綠沒有人會發現。
    """
    tree = ast.parse(text)
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                doc_nodes.add(id(body[0].value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in doc_nodes:
            out.append(node.value)
        elif isinstance(node, ast.Attribute):
            out.append(node.attr)
        elif isinstance(node, ast.Name):
            out.append(node.id)
    return "\n".join(out)


def _docstring_lines(tree):
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                doc = body[0].value
                lines.update(range(doc.lineno,
                                   (doc.end_lineno or doc.lineno) + 1))
    return lines


def python_source_without_docs(text):
    """Python 原始碼,**把 docstring 與 `#` 註解挖空、其餘一字不動**。

    ★與 `python_code_only` 的差別是本檔最容易被誤用的一點,寫清楚:

        python_code_only        回「識別字與字串常數」→ 答得了「這個名字有沒有
                                被用到」,**答不了「原始碼裡有沒有這段語法」**
        python_source_without_docs  回原始碼本身 → 兩種都答得了

    08-15 實測差點釀成假綠:`test-approval-writer` 問的是「模組沒有寫死基數」
    (`"== 54" not in src`),那是**語法**問題。餵 `python_code_only` 的話輸出
    裡永遠不會有 `==` 這種運算子,於是那條斷言變成**恆真**——它守的正是寫死
    基數,而它會在寫死基數真的出現時繼續亮綠。**把守衛換成永久假綠,比不修
    更糟**,因為它看起來像修好了。

    挖空而不是刪除(沿用 `env_constructions_missing_isolation` 的既有理由):
    刪除會讓行號位移,回報的位置就指到別的地方。行數與欄位維持不變。
    """
    lines = text.splitlines(True)
    doc_lines = _docstring_lines(ast.parse(text))
    cuts = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                lineno, col = tok.start
                cuts[lineno] = min(cuts.get(lineno, col), col)
    except (tokenize.TokenError, IndentationError):
        pass       # 取不到就只挖 docstring:少挖是誤報方向,誤報看得見
    out = []
    for index, line in enumerate(lines, 1):
        eol = "\n" if line.endswith("\n") else ""
        if index in doc_lines:
            out.append(eol)
        elif index in cuts:
            out.append(line[:cuts[index]].rstrip() + eol)
        else:
            out.append(line)
    return "".join(out)


def shell_code_only(text):
    """剝掉 shell 註解後的可執行碼。`code_only` 的 shell 分支,語意未變。"""
    return "\n".join(ln for ln in text.splitlines()
                     if not ln.strip().startswith("#"))


def _is_python_source(text):
    """這段文字是不是 Python。**決定程序,不是猜**。

    ★不用「有 `def `/`import ` 開頭的行」這種啟發式(指揮席 08-15 指出):
    shell 裡出現那種行不是不可能(heredoc、被註解掉的範例、字串),誤擋一個
    清白的 shell 檔就是製造下一個假紅。改用「能不能被 Python 解析」——實測
    `cockpit-full.sh` 第 35 行、`mouse-ctl.sh` 第 49 行都是 SyntaxError,
    兩個真實消費端都乾淨落在 shell 那一側。

    退化情形:只有註解與空行的檔兩邊都解析得過,但那種檔兩支剝法結果相同,
    分錯也沒有後果。
    """
    try:
        ast.parse(text)
    except (SyntaxError, ValueError):
        return False
    return True


def python_source_without_strings(text):
    """Python 原始碼,**把每一個字串常數與 `#` 註解挖空、其餘一字不動**。

    ★ 本家族的第三面(09-05,缺陷:test-no-dead-tests 假紅)。三面各答一種問題:

        python_code_only            回識別字與字串常數 → 答「這個名字有沒有被用到」
        python_source_without_docs  挖掉 docstring + 註解 → 答「碼裡有沒有這段語法」
        python_source_without_strings(本函式)
                                    再把**所有**字串常數挖掉 → 答「這段語法是**碼**,
                                    還是只是某個字串的內容」

    為什麼第二面不夠:`python_source_without_docs` 只挖 docstring,而
    `GREEN_TEST = '''…if __name__ == "__main__":…'''` 是**模組層賦值**不是 docstring
    ⇒ 它留在輸出裡,文字掃描照樣把治具字串裡的 runner 當成真 runner。
    09-05 實例:`test-witness-always-run.py` 文字掃描找到第 50 行,
    AST 的真 runner 在第 219 行 —— 於是「runner 之後還有測試定義」恆為真,而那是假紅。

    挖空而不是刪除(沿用同家族的既有理由):刪除會讓行號位移,回報的位置就指到
    別的地方;本函式保證**行數與欄位完全不變**,所以呼叫端算出來的行號仍可信。
    ★ 多行字串內部的換行要保留,否則後面每一行的行號都會少算。
    """
    lines = text.splitlines(True)
    grid = [list(line) for line in lines]
    wanted = {tokenize.STRING, tokenize.COMMENT}
    for name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
        if hasattr(tokenize, name):          # py3.12+ 把 f-string 拆成三種 token
            wanted.add(getattr(tokenize, name))
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError):
        return text        # 取不到就原樣回:少挖是**誤報**方向,誤報看得見
    for tok in toks:
        if tok.type not in wanted:
            continue
        (r1, c1), (r2, c2) = tok.start, tok.end
        for row in range(r1, r2 + 1):
            if row - 1 >= len(grid):
                break
            line = grid[row - 1]
            start = c1 if row == r1 else 0
            end = c2 if row == r2 else len(line)
            for col in range(start, min(end, len(line))):
                if line[col] != "\n":       # 換行留著,行號才不會位移
                    line[col] = " "
    return "".join("".join(row) for row in grid)


def python_runner_lineno(text):
    """回模組層 `if __name__ == "__main__":` 的行號(1 起);沒有就回 None。

    ★ 為什麼是 AST 而不是「剝字串後再正則」:剝字串會把 runner **自己那行**的
      `"__main__"` 也挖空(它就是個字串常數),於是 `^if __name__ == ["\']__main__["\']:`
      再也匹配不到 —— 09-05 實測,剝完之後 `test-witness-always-run.py` 回 None。
      「先剝再找」對這個目標是自我矛盾的;要找的東西本身含一個字串常數。
    ★ 只認**模組層**(`tree.body` 直屬):巢狀在函式裡的同款 if 不是 runner。
    ★ 語法壞掉時回 None,與「真的沒有 runner」同值 —— 呼叫端若要分辨,
      自己先 ast.parse 一次;本函式不吞掉例外以外的資訊。
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (isinstance(test, ast.Compare) and len(test.ops) == 1
                and isinstance(test.ops[0], ast.Eq)):
            continue
        left, right = test.left, test.comparators[0]
        names = {n.id for n in (left, right) if isinstance(n, ast.Name)}
        consts = {c.value for c in (left, right)
                  if isinstance(c, ast.Constant) and isinstance(c.value, str)}
        if "__name__" in names and "__main__" in consts:
            return node.lineno
    return None


def code_only(text, path=None):
    """剝掉註解後的可執行碼。**依語言分派**:`.py` 走 Python、其餘走 shell。

    ★靜態斷言「碼中不得出現 X」時**一律先剝註解**——註解裡本來就會寫到
    那個字(記錄根因用),不剝就會把文件誤判成缺陷。

    `path` 給了就照副檔名分派(最可靠);沒給就用 `_is_python_source` 判定,
    **而且判成 Python 時會往 stderr 留一行痕**——那條路是靠推斷走的,推斷
    要看得見。判成 shell 不留痕:那是歷史預設,三個既有呼叫點全部走它。

    ★Python 那條走 `python_source_without_docs`(保留原始碼)而**不是**
    `python_code_only`(只回識別字)。理由是 08-15 差一步就送出去的一個假綠:
    本函式的歷史語意一直是「原始碼,把註解拿掉」,呼叫端因此可以問**語法**
    問題(例:`"== 54" not in src`)。若分派到只回識別字的那支,運算子永遠
    不會出現在輸出裡,那類斷言就變成**恆真**——守衛看起來還在,實際上再也
    不可能紅。**保留原始碼的那支對兩類問題都安全,只回識別字的那支只對一類
    安全**,所以預設走前者;要後者的呼叫端自己指名。

    發作史(同一個病四次):
    - 08-14 `test-mouse` 誤中兩次(`/Users/user` 與 `@aib_pane` 都出現在
      說明根因的註解裡),當時就地抽出這個函式,但**只活在 test-mouse.py 裡**。
    - 08-15 `test-ui-nav` 的 `test_focus_style_is_window_scoped_not_server_global`
      第三次發作:它掃 `cockpit-full.sh` 每一行含 `window-style` 字樣的行,
      把一句**說明「另外兩處讀端在哪」的註解**判成 server-global 樣式設定。
      該判準當時沒有用這個函式——因為函式沒有共用的地方可以放。
    - 08-15 第四次:本函式被拿去掃 **Python** 檔,而它不剝 docstring。

    ★第三次的根因不是「有人忘了剝註解」,是**修法只修在發現它的那一支**。
    ★第四次的根因不同,而且更值得記:**這個檔只收了問題的一半就以為收完了。**
    Python 那一半 `test-golden-frames.py` 與 `test-persona-route.py` 各自
    獨立解過一次,兩份都沒有共用出來——而一個「為了不再重複發明」而建的共用
    檔,只收一半會給人「這件已經有人解決了」的錯覺,**那個錯覺正是讓另外兩個
    人各自重新發明的原因**。收斂時要問的不是「我收了嗎」,是「我收全了嗎」。
    """
    if path is not None:
        suffix = str(path).rsplit(".", 1)[-1].lower() if "." in str(path) else ""
        if suffix == "py":
            return python_source_without_docs(text)
        if suffix:
            return shell_code_only(text)
    if _is_python_source(text):
        # 靜默走上另一條剝法是這件事本身的病:降級可以,無聲不行。
        sys.stderr.write(
            "aib_testlib.code_only: 未給 path,依語法判定為 Python 並改走 "
            "python_source_without_docs(建議呼叫端補上 path=)\n")
        return python_source_without_docs(text)
    return shell_code_only(text)


# 測試從 os.environ 繼承下來就會讀到活艙真實狀態的變數。
# 「不密閉的測試不會在寫它的人手上紅,只會在別人手上紅」——而且它紅的時候
# 碼是對的,於是有人會去改碼(見 docs/reports/
# EVIDENCE-AND-HERMETICITY-LESSONS-20260815.md)。
LIVE_STATE_ENV = ("EZ2_COCKPIT_STATE_DIR",)


def env_constructions_missing_isolation(source, required=LIVE_STATE_ENV):
    """找出 `dict(os.environ, ...)` 之類的環境建構裡漏掉隔離變數的位置。

    回 [(行號, 缺的變數名), ...];空=該檔所有建構都覆蓋了。

    ★判準從碼推導而不是靠人記得:同一支檔可以有五處建構,四處對一處漏,
    而漏掉的那一處在寫它的人手上永遠是綠的(他的活艙剛好是預設值)。
    08-15 `test-theme.py:588` 就是這個形狀——同檔另外四處都有隔離。

    刻意用「往後看 N 行」的樸素掃法而不是 AST:AST 版要處理 `dict()` 的
    各種寫法與變數傳遞,複雜度會讓這支判準自己變成需要被驗的東西。樸素版
    的失敗方向是**誤報**(把有隔離的判成沒有),不是漏報——誤報看得見。

    ★註解是**置空不是刪除**:刪除會讓行號位移,回報的位置就指到別的地方。
    置空同時擋掉兩個方向——註解裡寫到 `dict(os.environ` 不會觸發掃描,
    註解裡寫到變數名也不會被當成「已隔離」(那是假綠)。
    """
    hits = []
    lines = ["" if ln.strip().startswith("#") else ln
             for ln in source.splitlines()]
    for index, line in enumerate(lines):
        if "dict(" not in line:
            continue
        block = "\n".join(lines[index:index + 14])
        if "os.environ" not in block:
            continue
        for name in required:
            if name not in block:
                hits.append((index + 1, name))
    return hits


# ── 缺陷十四:重畫判準改成全稱謂詞,時間退出判準 ──────────────────────────
# 舊判準是「首幀後 3 倍首幀延遲的窗內幀數必須 == 1」。兩個量**都是牆鐘**,
# 而且都隨負載變長:窗變長、第二幀也變晚,誰先到是排程決定的
# ⇒ 失效方式是不穩定,而不穩定的紅與真回歸的紅逐字同形(記憶 time-constant-in-a-criterion)。
#
# ★ 09-05 現查到的關鍵事實,推翻了「第二幀 = 假紅」這個說法:
#   把窗開到 6 秒,右欄**穩定地**畫第二幀,而且兩幀內容不同 ——
#     第一幀:`機器狀態 量測中…`
#     第二幀:`忙碌 49.1  溫度 54°C  降頻 0%  記憶體 50% …`
#   那是非同步的機器狀態量測回來後**合法**的重畫(09-04 才進來的功能)。
#   ⇒ 舊格不是「負載下多畫了一幀」,是它的窗平常剛好在第二幀之前關掉。
#     `count == 1` 從原理上就是錯的判準,不是常數調不對。
#
# 真正要守的性質只有一條:**畫面沒變就不准重畫**(重畫會把使用者的選字清掉)。
# 那是一個全稱謂詞,和時間無關:窗開多久都只影響「證據多寡」,不影響對錯。
def split_frames(stdout):
    """依 cursor-home 切幀。回 list;不含首幀之前的前綴(那不是一幀)。"""
    parts = stdout.split(b"\x1b[H")
    return parts[1:] if len(parts) > 1 else []


def repaint_violations(stdout):
    """回 (違規, 未定) —— 兩個 list,元素是 (前一幀索引, 理由)。

    違規 = 相鄰兩幀**內容沒變卻重畫了**:
      · 逐位元組相同                    ⇒ 一定是違規
      · 後一幀較短且是前一幀的前綴      ⇒ 「到截斷處為止完全相同」
        - 若它不是最後一幀 ⇒ 違規(中間的幀不會被我們截斷)
        - 若它是最後一幀   ⇒ **未定**,因為那可能是我們自己 SIGTERM 砍在寫到一半
                              (未定不算綠也不算紅,要印出來)
    合法 = 內容真的變了。後一幀較長且以前一幀開頭也算合法(內容長出來了),
           不得判成違規 —— 否則「底部多一行狀態」這種正當變化會變成假紅。

    ★★★ 適用前提:**畫面幾何沒變**。這個判準只對「閒置時不得重畫」成立。
      幾何變了的時候,**逐位元組相同的重畫是正確行為**(尺寸變了即使文字相同
      也要清一次),此時本判準會給假紅。
      實測反例(09-05 現量):`test_bottom_repaints_once_when_geometry_changes`
      的兩幀 301/301 位元組、sha 同為 `d6419f22`,而那一格是**對的** ——
      它的判準是 `count == 2`,**不得**改用本函式。
      ⇒ 要把新的格接上來之前,先問「這一格的情境裡幾何會不會變」。
    """
    frames = split_frames(stdout)
    violations, indeterminate = [], []
    for i in range(len(frames) - 1):
        prev, curr = frames[i], frames[i + 1]
        last = (i + 1) == len(frames) - 1
        if prev == curr:
            violations.append((i, "第 %d 幀與第 %d 幀逐位元組相同" % (i, i + 1)))
        elif len(curr) < len(prev) and prev.startswith(curr):
            note = "第 %d 幀是第 %d 幀的前綴(到截斷處為止相同)" % (i + 1, i)
            (indeterminate if last else violations).append((i, note))
    return violations, indeterminate


def run_until_stable_frames(argv, env, expected, first_cap=60.0, cycles_factor=3.0,
                            min_window=1.0, watch_full_window=False):
    """跑一支面板腳本,等到 stdout 累積 `expected` 幀(`\\x1b[H`)——**等事件不等秒數**——
    再觀察一個「相對於首幀延遲」的窗,確認幀數不再增加;回 (stdout, stderr, info)。

    為什麼(BB 熱修,2026-09-02):原本 `time.sleep(0.65)` 再數幀,0.65 是時間常數;
    單次渲染在忙機上要 1–6s(load 100+ 實測),常數只會以不穩定方式紅(HANDOFF 未結案 #4)。
    - `first_cap` 只當**逾時紅**用(info["timeout"]=True),不是判準;判準是幀數。
    - 觀察窗 = max(min_window, cycles_factor × 首幀延遲):一個渲染週期 ≈ 首幀延遲,
      三倍窗內至少再過兩個週期;若產品每個週期都重畫,窗內幀數必增(這是必落空端)。
    - 窗內幀數一增就提早結束(證據已到,不必等滿)。
    """
    import os as _os
    import signal as _signal
    import subprocess as _sp
    import threading as _th
    import time as _time

    proc = _sp.Popen(argv, env=env, stdout=_sp.PIPE, stderr=_sp.PIPE, start_new_session=True)
    buf = bytearray()

    def pump():
        while True:
            chunk = proc.stdout.read1(65536)
            if not chunk:
                return
            buf.extend(chunk)
    _th.Thread(target=pump, daemon=True).start()

    def count():
        return bytes(buf).count(b"\x1b[H")

    info = {"timeout": False, "first_latency": None, "window": None, "after_window": None}
    t0 = _time.monotonic()
    deadline = t0 + first_cap
    while _time.monotonic() < deadline and count() < expected:
        _time.sleep(0.02)
    if count() < expected:
        info["timeout"] = True
    else:
        info["first_latency"] = _time.monotonic() - t0
        window = max(min_window, cycles_factor * info["first_latency"])
        info["window"] = window
        end = _time.monotonic() + window
        # ★ 缺陷十四:`watch_full_window` 決定「幀數一變就收工」還是「窗跑滿」。
        #   舊判準是幀數 == expected,所以一超過就可以提早結束(證據已到)。
        #   全稱謂詞不一樣:它要比**相鄰每一對**幀,第 2 幀就收工的話,
        #   第 3、4 幀有沒有原地重畫**永遠問不到** —— 那是「綠得對而問得太少」。
        #   跑滿窗只花時間,不改變對錯(判準裡沒有時間)。
        while _time.monotonic() < end and (watch_full_window or count() == expected):
            _time.sleep(0.02)
        info["after_window"] = count()
    try:
        _os.killpg(proc.pid, _signal.SIGTERM)
        _, stderr = proc.communicate(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    _time.sleep(0.05)
    return bytes(buf), stderr, info
