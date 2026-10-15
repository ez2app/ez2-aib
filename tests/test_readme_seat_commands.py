# SPDX-License-Identifier: Apache-2.0
"""README's "personas, projects, messages and hand-offs" commands exist as written, in all four languages.

Every subcommand and option in that section's code block must be one the real `aib` accepts (checked against its
own help), and the four READMEs must show the same commands. Standard library only.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AIB = [sys.executable, str(ROOT / "bin" / "aib")]
SECTIONS = {"README.md": "## Personas, projects, messages and hand-offs", "README.zh-TW.md": "## 人格、專案、傳訊與交接",
            "README.ja.md": "## ペルソナ、プロジェクト、メッセージ、引き継ぎ", "README.ko.md": "## 페르소나, 프로젝트, 메시지, 인수인계"}


def commands(readme, heading):
    text = (ROOT / readme).read_text(encoding="utf-8")
    body = text.split(heading, 1)[1].split("\n## ", 1)[0]
    block = re.search(r"```sh\n(.*?)```", body, re.S).group(1)
    joined = re.sub(r"\\\n\s*", " ", block)
    return [re.sub(r"\s+#.*$", "", line).strip() for line in joined.splitlines() if line.strip()]


class ReadmeSeatCommands(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aib-readme-"))
        (self.td / "home").mkdir()

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def help_of(self, *args):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.td / "home"), "EZ2_AIB_LOCALE": "en"}
        r = subprocess.run(AIB + list(args), cwd=self.td, env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60)
        return r.stdout + r.stderr

    def test_same_commands_in_every_language(self):
        en = commands("README.md", SECTIONS["README.md"])
        self.assertEqual(len(en), 5)
        for readme, heading in SECTIONS.items():
            self.assertEqual(commands(readme, heading), en, readme)

    def test_every_subcommand_and_option_exists(self):
        for cmd in commands("README.md", SECTIONS["README.md"]):
            words = cmd.split()
            self.assertEqual(words[0], "aib")
            sub = words[1]
            text = self.help_of(sub, "--help") if sub != "dm" else self.help_of("dm")
            for word in words[2:]:
                if word.startswith("--"):
                    with self.subTest(command=sub, option=word):
                        # whole option only: "--seat" must not be satisfied by "--new-seat"
                        self.assertRegex(text, r"(?<![\w-])" + re.escape(word) + r"\b")
                elif sub in ("bindme", "handoff") and re.fullmatch(r"[a-z]+", word):
                    with self.subTest(command=sub, subcommand=word):
                        self.assertRegex(text, r"(?<![\w-])" + re.escape(word) + r"\b")
        for sub in ("newrole", "newproj"):
            self.assertIn("--dry-run", self.help_of(sub, "--help"))
        self.assertIn("--dry-run", self.help_of("dm"))
        handoff = self.help_of("handoff", "--help")
        for step in ("collect", "write", "verify"):
            self.assertIn(step, handoff)


if __name__ == "__main__":
    unittest.main()
