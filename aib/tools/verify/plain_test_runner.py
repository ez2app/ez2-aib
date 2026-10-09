#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run a test file that has no `if __name__ == "__main__"` guard (pytest style), without needing pytest.

Why (10-04): `aib test` runs each test file directly. A pytest-style file has no main guard, so running it only
defines the functions and exits 0 -- green with zero cells checked. tests/test-ctx-foot-line-0920.py had been
"green" that way since 09-20. When pytest is installed the runner uses pytest; otherwise it uses this file.

What it runs: module-level functions named test_* and unittest.TestCase subclasses, in file order.
A test_* function that takes parameters needs pytest fixtures -> reported as an error (red), never skipped.
Output mimics unittest so the existing parsers read it: "Ran N tests" then "OK" or "FAILED (failures=F, errors=E)".
Exit codes: 0 all passed; 1 any failure/error; 5 no tests found (caller decides; the runner does not call us then).
"""
import importlib.util
import inspect
import sys
import traceback
import unittest
from pathlib import Path


def main(path):
    path = Path(path).resolve()
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    funcs = [(n, f) for n, f in vars(mod).items()
             if n.startswith("test") and inspect.isfunction(f) and f.__module__ == mod.__name__]
    funcs.sort(key=lambda nf: nf[1].__code__.co_firstlineno)
    suite = unittest.TestSuite()
    for _n, obj in vars(mod).items():
        if inspect.isclass(obj) and issubclass(obj, unittest.TestCase) and obj.__module__ == mod.__name__:
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(obj))
    if not funcs and suite.countTestCases() == 0:
        print("Ran 0 tests\n\nNO TESTS RAN")
        return 5
    failures = errors = 0
    for name, f in funcs:
        if inspect.signature(f).parameters:
            errors += 1
            print("ERROR: %s (needs pytest fixtures: %s; install pytest)" % (name, ", ".join(inspect.signature(f).parameters)))
            continue
        try:
            f()
            print("%s ... ok" % name)
        except AssertionError:
            failures += 1
            print("FAIL: %s\n%s" % (name, traceback.format_exc()))
        except Exception:  # noqa: BLE001
            errors += 1
            print("ERROR: %s\n%s" % (name, traceback.format_exc()))
    res = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite) if suite.countTestCases() else None
    if res is not None:
        failures += len(res.failures)
        errors += len(res.errors)
    total = len(funcs) + (res.testsRun if res is not None else 0)
    print("\n" + "-" * 70 + "\nRan %d tests\n" % total)
    if failures or errors:
        print("FAILED (%s)" % ", ".join(s for s in ("failures=%d" % failures if failures else "",
                                                     "errors=%d" % errors if errors else "") if s))
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
