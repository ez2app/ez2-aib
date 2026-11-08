# SPDX-License-Identifier: Apache-2.0
"""Exercise the actual tag step against local Git remotes; no public upload."""
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / '.github/workflows/release.yml').read_text()
GUARD = next(line.split('run: ', 1)[1] for line in WORKFLOW.splitlines() if 'run: test "$GITHUB_REF_NAME"' in line)
SCRIPT = GUARD + '\n' + textwrap.dedent(WORKFLOW.split('        run: |\n', 1)[1].split('  precheck:', 1)[0])


class ReleaseSync(unittest.TestCase):
    def run_step(self, event, existing=False, ref='main', main_head=None):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, remote, bindir = root/'repo', root/'remote.git', root/'bin'
            repo.mkdir(); bindir.mkdir()
            def git(*args):
                return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
            git('init', '-b', 'main'); git('config', 'user.name', 'test'); git('config', 'user.email', 'test@example.invalid')
            (repo/'pyproject.toml').write_text('[project]\nversion="0.1.61"\n')
            (repo/'CHANGELOG.md').write_text('## 0.1.61\n\nA release.\n')
            git('add', 'pyproject.toml', 'CHANGELOG.md'); git('commit', '-m', 'release')
            sha = git('rev-parse', 'HEAD')
            subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
            git('remote', 'add', 'origin', str(remote))
            if existing: git('tag', 'v0.1.61')
            # The hosted tag job has Python 3.12; keep shell-flow tests usable on 3.9.
            version_reader = bindir/'python3'
            version_reader.write_text('#!/bin/sh\nprintf \"0.1.61\\n\"\n'); version_reader.chmod(0o755)
            # 假 gh:`gh api …/commits/main` 印 MAIN_HEAD(空 = 讀不到、rc 1),其他呼叫照記
            gh = bindir/'gh'; gh.write_text('#!/bin/sh\nif [ "$1" = api ]; then [ -n "$MAIN_HEAD" ] || exit 1; printf "%s\\n" "$MAIN_HEAD"; exit 0; fi\nprintf "%s\\n" "$*" >> "$GH_CALLS"\n'); gh.chmod(0o755)
            env = dict(os.environ, PATH=str(bindir)+':'+str(Path(os.sys.executable).parent)+':'+os.environ['PATH'],
                       GITHUB_EVENT_NAME=event, GITHUB_REF_NAME=ref, GITHUB_SHA='f'*40,
                       GITHUB_OUTPUT=str(root/'output'), GH_CALLS=str(root/'calls'),
                       MAIN_HEAD=sha if main_head is None else main_head)
            p = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', SCRIPT], cwd=repo, env=env, capture_output=True, text=True)
            output = (root/'output').read_text() if (root/'output').exists() else ''
            calls = (root/'calls').read_text() if (root/'calls').exists() else ''
            q = subprocess.run(['git', 'rev-parse', '-q', '--verify', 'refs/tags/v0.1.61'], cwd=repo, capture_output=True, text=True)
            tag = q.stdout.strip() if p.returncode == 0 and not existing and q.returncode == 0 else None
            return p, output, calls, tag, sha

    def test_new_main_version_publishes_checked_commit(self):
        p, output, calls, tag, sha = self.run_step('workflow_run')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(output, 'tag=v0.1.61\n')
        self.assertIn('release create v0.1.61', calls)
        self.assertEqual(tag, sha)  # GITHUB_SHA deliberately differs from the checked CI commit.

    def test_same_version_skips_without_release_or_upload_target(self):
        p, output, calls, _, _ = self.run_step('workflow_run', existing=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual((output, calls), ('', ''))

    def test_main_moved_on_does_not_release_the_older_run(self):
        # 11-08 DECISION-17 修訂:這輪檢查的 commit 已不是 main 頭碼 ⇒ 不發、rc 0、不打標籤
        p, output, calls, tag, _ = self.run_step('workflow_run', main_head='e' * 40)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual((output, calls), ('', ''))
        self.assertIn('main moved on', p.stdout)
        self.assertIsNone(tag)   # 沒打標籤

    def test_cannot_read_main_head_fails_closed(self):
        p, output, calls, _, _ = self.run_step('workflow_run', main_head='')
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual((output, calls), ('', ''))
        self.assertIn('fail closed', p.stdout)

    def test_manual_duplicate_and_nonmain_refuse(self):
        for existing, ref in [(True, 'main'), (False, 'feature')]:
            p, output, calls, _, _ = self.run_step('workflow_dispatch', existing, ref)
            self.assertNotEqual(p.returncode, 0)
            self.assertEqual((output, calls), ('', ''))

    def test_untrusted_workflows_are_not_release_authority(self):
        tag_job = WORKFLOW.split('  tag:', 1)[1].split('  precheck:', 1)[0]
        for guard in ["conclusion == 'success'", "event == 'push'", "head_branch == 'main'", 'head_repository.full_name == github.repository']:
            self.assertIn(guard, tag_job)
        verify = WORKFLOW.split('  verify:', 1)[1]
        self.assertIn('ref: ${{ needs.tag.outputs.tag || github.ref }}', verify)
        self.assertIn('environment: pypi', WORKFLOW)
        self.assertIn("needs.tag.outputs.tag != ''", WORKFLOW)


if __name__ == '__main__':
    unittest.main()
