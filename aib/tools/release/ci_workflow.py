#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""aib ship ci(10-07 the maintainer:GitHub Actions 分鐘數很容易耗光;我們有自架機器,其他 aib 用戶沒有 ⇒ 要一個他們也能省的方式)。

產生 `.github/workflows/aib-test.yml`:在 GitHub 的機器上照樣跑,但
1. 紀錄池存進 GitHub 的快取(先找同一分支、再找預設分支的)⇒ 沒改到的測試沿用上次的綠,不重跑 —— 省的是「跑測試」的分鐘數;
2. 只在推到預設分支與 PR 時跑、只改文件不跑、同一分支新推送取消舊的那輪 —— 省的是「根本不用跑」的那幾輪;
3. `aib test --all` 跑完把「沒有 aib → 有 aib」附到這次執行的摘要頁,用戶在網頁上看得到省了多少。
安全:GitHub 的快取照分支隔開,PR 寫的快取主線讀不到(不受信的分支偽造不了主線的「上次綠」);紅燈永遠重跑。
存快取用 if: always():紅的那輪也存(紅列不會被沿用,綠的那幾支下次就能沿用)。
aib 本身照固定的提交取出(AIB_REF),取出後搬到專案外($RUNNER_TEMP,免得 aib 自己的測試被當成專案的):追蹤器換版 ⇒ 紀錄全部作廢重跑,不固定的話每次 aib 一更新就全部重跑。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

WORKFLOW = ".github/workflows/aib-test.yml"
DOC_PATHS = ("docs/**", "**/*.md")


def detect(repo):
    """這個倉要哪些語言的安裝步驟:{"python", "node", "go", "php"} 的子集。"""
    r = Path(repo)
    out = set()
    if any((r / f).exists() for f in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "tox.ini")):
        out.add("python")
    if (r / "package.json").exists():
        out.add("node")
    if (r / "go.mod").exists():
        out.add("go")
    if (r / "composer.json").exists():
        out.add("php")
    return out or {"python"}


def default_branch(repo):
    """origin 的預設分支;量不到 ⇒ 目前分支;再不行 ⇒ main。"""
    for args in (["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], ["rev-parse", "--abbrev-ref", "HEAD"]):
        try:
            p = subprocess.run(["git", "-C", str(repo)] + args, capture_output=True, text=True, timeout=10,
                               stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            continue
        name = p.stdout.strip()
        if p.returncode == 0 and name and name != "HEAD":
            return name.split("/", 1)[1] if name.startswith("origin/") else name
    return "main"


def _python_install(repo):
    r = Path(repo)
    if (r / "pyproject.toml").exists() or (r / "setup.py").exists():
        return "pip install -e . pytest"
    if (r / "requirements.txt").exists():
        return "pip install -r requirements.txt pytest"
    return "pip install pytest"


def _pip_dep_file(repo):
    """setup-python 的套件快取要一個相依檔(沒有會報錯)⇒ 有才開。"""
    for f in ("requirements.txt", "pyproject.toml", "setup.cfg", "setup.py"):
        if (Path(repo) / f).exists():
            return f
    return ""


def plain_commands(langs):
    """fork 來的 PR 不用 aib、照一般方式全跑的命令(拿不到密鑰、也不能讓它碰紀錄池)。"""
    out = []
    if "python" in langs:
        out.append("python -m pytest")
    if "node" in langs:
        out.append("npm test")
    if "go" in langs:
        out.append("go test ./...")
    if "php" in langs:
        out.append("vendor/bin/phpunit")
    return out


def render(repo, aib_repo, aib_ref, branch=None, python="3.12"):
    """工作流程檔的內容(純函式)。"""
    langs = detect(repo)
    branch = branch or default_branch(repo)
    ignore = "".join("\n      - '%s'" % p for p in DOC_PATHS)
    setup = []
    dep = _pip_dep_file(repo)
    pip_cache = ("\n          cache: pip\n          cache-dependency-path: %s" % dep) if dep else ""
    if "python" in langs:
        setup.append("      - uses: actions/setup-python@v5\n        with:\n          python-version: '%s'%s\n"
                     "      - run: %s  # your project's own install; edit as needed" % (python, pip_cache, _python_install(repo)))
    else:
        setup.append("      - uses: actions/setup-python@v5   # aib itself runs on Python\n        with:\n"
                     "          python-version: '%s'" % python)
    if "node" in langs:
        npm_cache = "\n          cache: npm" if (Path(repo) / "package-lock.json").exists() else ""
        setup.append("      - uses: actions/setup-node@v4\n        with:\n          node-version: '20'%s\n"
                     "      - run: npm ci --ignore-scripts" % npm_cache)
    if "go" in langs:
        setup.append("      - uses: actions/setup-go@v5\n        with:\n          go-version-file: go.mod")
    if "php" in langs:
        setup.append("      - run: composer install --no-interaction")
    plain = "\n".join("          %s" % c for c in plain_commands(langs))
    return TEMPLATE.format(branch=branch, ignore=ignore, aib_repo=aib_repo, aib_ref=aib_ref, setup="\n".join(setup),
                           plain=plain, py=python)


CONTRIBUTING = """### Tests in CI (aib-test)

The `aib-test / test` check runs the test suite with [aib](https://github.com/{aib_repo}).
A test whose inputs (the files it reads) did not change since its last green run is **reused** instead of run again;
everything you changed or added runs for real, and a red result is never reused. The run's summary page lists which
tests ran. Pull requests from forks run the whole suite the plain way. Once a week everything runs for real.
"""


def contributing(aib_repo):
    """可以貼進 CONTRIBUTING.md 的一段(給不認識 aib 的貢獻者;英文,跟工作流程檔的註解同一個讀者)。"""
    return CONTRIBUTING.format(aib_repo=aib_repo)


TEMPLATE = """# Generated by `aib ship ci`. Saves GitHub Actions minutes:
#  1. tests whose inputs did not change reuse their last green result (the aib record pool is kept in the Actions cache);
#  2. runs only on pushes to {branch} and on pull requests, not for docs-only changes; a newer push cancels the older run;
#  3. pip / npm downloads are cached too (on small projects installing often takes longer than the tests).
# Red is never reused, and anything aib cannot measure always runs.
# Only pushes to {branch} (and the weekly full run) save the record cache; pull requests only read it.
# Pull requests from forks run everything the plain way: no secrets reach them, and their test code must not be able to
# touch the record pool (a test could rewrite it and fake its own "reused green").
name: aib-test

on:
  push:
    branches: [{branch}]
    paths-ignore:{ignore}
  pull_request:
    paths-ignore:{ignore}
  schedule:
    - cron: '17 3 * * 0'   # weekly: run everything for real (refresh timings, catch tests that only fail sometimes)

concurrency:
  group: aib-test-${{{{ github.event_name }}}}-${{{{ github.ref }}}}
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    env:
      AIB_REPO: {aib_repo}
      AIB_REF: {aib_ref}
      FORK: ${{{{ github.event_name == 'pull_request' && github.event.pull_request.head.repo.full_name != github.repository }}}}
    steps:
      - uses: actions/checkout@v4
{setup}
      - name: Fork pull request - run everything the plain way (no aib, no record cache)
        if: env.FORK == 'true'
        run: |
{plain}
      - name: Get aib (pinned; a new aib version invalidates the records once)
        if: env.FORK != 'true'
        uses: actions/checkout@v4
        with:
          repository: ${{{{ env.AIB_REPO }}}}
          ref: ${{{{ env.AIB_REF }}}}
          path: .aib-tool
          token: ${{{{ secrets.AIB_TOKEN || github.token }}}}
      - name: Move aib out of the project tree (so its own tests are not picked up)
        if: env.FORK != 'true'
        run: mv .aib-tool "$RUNNER_TEMP/aib-tool"
      - name: Restore aib records
        if: env.FORK != 'true'
        uses: actions/cache/restore@v4
        with:
          path: ~/.ez2-aib/verify-shadow
          key: aib-pool-${{{{ runner.os }}}}-py{py}-${{{{ github.ref_name }}}}-${{{{ github.sha }}}}
          restore-keys: |
            aib-pool-${{{{ runner.os }}}}-py{py}-${{{{ github.ref_name }}}}-
            aib-pool-${{{{ runner.os }}}}-py{py}-{branch}-
      - name: aib test (reuse unchanged, run the rest)
        if: env.FORK != 'true' && github.event_name != 'schedule'
        run: python3 "$RUNNER_TEMP/aib-tool/tools/verify/test_entry.py" --repo . --all
      - name: aib test - weekly full run
        if: env.FORK != 'true' && github.event_name == 'schedule'
        run: python3 "$RUNNER_TEMP/aib-tool/tools/verify/test_entry.py" --repo . --all --fresh
      - name: Weekly cross-check - one by one vs the whole suite at once (they can differ when tests share state)
        if: env.FORK != 'true' && github.event_name == 'schedule'
        run: |
          # exit 1 = the two disagree (fail the job); exit 4 = this framework cannot be compared (not a failure)
          python3 "$RUNNER_TEMP/aib-tool/tools/release/ship.py" --repo . crosscheck || rc=$?
          test "${{rc:-0}}" != 1
      - name: Record pool size (the Actions cache keeps 10 GB per repo; a large pool can push older caches out)
        if: always() && env.FORK != 'true'
        run: |
          size=$(du -sh ~/.ez2-aib/verify-shadow 2>/dev/null | cut -f1)
          echo "aib record pool: ${{size:-none}} (stored compressed by actions/cache)" >> "$GITHUB_STEP_SUMMARY"
      - name: Save aib records (pushes to {branch} and the weekly run only; pull requests only read)
        if: always() && env.FORK != 'true' && github.event_name != 'pull_request'
        uses: actions/cache/save@v4
        with:
          path: ~/.ez2-aib/verify-shadow
          key: aib-pool-${{{{ runner.os }}}}-py{py}-${{{{ github.ref_name }}}}-${{{{ github.sha }}}}
"""
