"""Minimal test runner -- runs tests/test_*.py without requiring pytest.

Why this exists
---------------
``pytest`` is not installed in this environment and packages cannot be
downloaded, but "the network is restricted" is a poor reason to ship untested
code. The test files are written as ordinary pytest-style functions, so anyone
who does have pytest can just run ``pytest``. This runner provides the same
signal locally by resolving the small set of fixtures the tests use.

Supported subset: ``test_*`` functions, module-scoped fixtures declared with
``@pytest.fixture`` when pytest is absent, and ``pytest.raises``. That is
deliberately the whole feature set -- it is not trying to be pytest.

Usage
-----
    python scripts/run_tests.py            # all test modules
    python scripts/run_tests.py generate   # only tests/test_generate.py
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def _fixture_factory(module):
    """Collect fixture providers, honouring a module-level cache for scope."""
    cache: dict[str, object] = {}

    def resolve(name: str):
        if name in cache:
            return cache[name]
        fn = getattr(module, name, None)
        if fn is None or not callable(fn):
            raise LookupError(f"no fixture named {name!r}")
        # Support pytest.fixture-decorated functions, and plain functions whose
        # name matches a fixture in the test signature.
        target = getattr(fn, "__wrapped__", fn)
        cache[name] = target()
        return cache[name]

    return resolve


def run_module(path: Path) -> tuple[int, int, list[str]]:
    module = _load(path)
    resolve = _fixture_factory(module)
    passed = failed = 0
    failures: list[str] = []

    tests = [(n, f) for n, f in vars(module).items()
             if n.startswith("test_") and inspect.isfunction(f)]
    tests.sort(key=lambda kv: kv[1].__code__.co_firstlineno)

    print(f"\n{path.name}  ({len(tests)} tests)")
    print("-" * 68)
    for name, fn in tests:
        sig = inspect.signature(fn)
        kwargs = {}
        try:
            for param in sig.parameters:
                kwargs[param] = resolve(param)
            fn(**kwargs)
        except Exception as exc:  # noqa: BLE001 - a test runner reports everything
            failed += 1
            first = f"{type(exc).__name__}: {exc}"
            print(f"  FAIL  {name}")
            print(f"        {first}")
            failures.append(f"{path.name}::{name}\n{traceback.format_exc()}")
        else:
            passed += 1
            print(f"  ok    {name}")
    return passed, failed, failures


def main(argv: list[str]) -> int:
    if not TESTS.is_dir():
        print(f"no tests directory at {TESTS}", file=sys.stderr)
        return 2

    pattern = f"test_*{argv[0]}*.py" if argv else "test_*.py"
    paths = sorted(TESTS.glob(pattern))
    if not paths:
        print(f"no test modules matching {pattern}", file=sys.stderr)
        return 2

    total_passed = total_failed = 0
    all_failures: list[str] = []
    for path in paths:
        p, f, fails = run_module(path)
        total_passed += p
        total_failed += f
        all_failures.extend(fails)

    print()
    print("=" * 68)
    if all_failures:
        print(f"FAILED  {total_failed} failed, {total_passed} passed")
        print()
        for blob in all_failures:
            print(blob)
        return 1
    print(f"PASSED  {total_passed} tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
