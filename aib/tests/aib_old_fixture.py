"""Read a pinned old version of a source file from tests/fixtures/old-ref/ (043dd, 11-08, DECISION-18 follow-up).

Tests that compare "old code vs new code" used to read the old one with `git show <sha>:<path>`. That skips on
shallow clones and copies without .git, so the comparison silently stopped running in CI. Now the old file is
committed as a fixture, and its git blob id is pinned in the test: a fixture that was edited, truncated or
re-encoded (CRLF) no longer matches and the test goes red instead of comparing against the wrong file.
"""
import hashlib
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "old-ref"


def git_blob_id(data):
    """Same id `git hash-object` gives, without needing git."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def read_old_fixture(name, blob_id):
    """Text of fixtures/old-ref/<name>. AssertionError when it is missing or its blob id is not `blob_id`."""
    path = FIXTURES / name
    try:
        data = path.read_bytes()
    except OSError as e:
        raise AssertionError("old-version fixture %s missing: %s" % (name, e))
    got = git_blob_id(data)
    if got != blob_id:
        raise AssertionError("old-version fixture %s changed: blob %s, pinned %s" % (name, got, blob_id))
    return data.decode("utf-8")
